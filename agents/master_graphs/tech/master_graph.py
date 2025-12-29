"""기술 마스터그래프 - 특허/논문 검색 (AI-028~AI-032)."""

from __future__ import annotations

from typing import Any, Dict
import logging
import asyncio
import re
from pathlib import Path
from datetime import datetime

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.tech.patent.patent_agent import patent_search
from agents.master_graphs.tech.tech_article_agent import article_master


def _setup_tech_logger() -> logging.Logger:
    """Tech 도메인 로거 설정 (콘솔 + 파일)."""
    logger = logging.getLogger(__name__)
    
    # 이미 핸들러가 설정되어 있으면 재설정하지 않음
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.INFO)
    
    # 콘솔 핸들러 추가 (FastAPI에서도 보이도록)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # 파일 핸들러 추가 (선택적)
    try:
        # logs 디렉토리 생성
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent.parent
        log_dir = project_root / "logs"
        log_dir.mkdir(exist_ok=True)
        
        # 로그 파일 경로 (날짜별로 생성)
        log_file = log_dir / f"tech_master_{datetime.now().strftime('%Y%m%d')}.log"
        
        file_handler = logging.FileHandler(log_file, encoding="utf-8", mode='a')
        file_handler.setLevel(logging.INFO)
        file_formatter = logging.Formatter(
            '%(asctime)s | %(pathname)s:%(lineno)d | %(funcName)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # 파일 핸들러 설정 실패해도 콘솔 핸들러는 유지
        import sys
        print(f"⚠️ Tech 로거 파일 핸들러 설정 실패: {e}", file=sys.stderr)
    
    logger.propagate = False  # 상위 로거로 전파하지 않음 (중복 방지)
    
    return logger


LOGGER = _setup_tech_logger()


class TechMasterGraph(BaseMasterGraph):
    """기술 도메인 마스터그래프.
    
    요구사항:
    - AI-028: 기술 라우팅
    - AI-029: 특허 검색
    - AI-030: 특허 벡터화
    - AI-031: 논문 검색
    - AI-032: 논문 벡터화
    """
    
    def get_domain_name(self) -> str:
        return "tech"
    
    def get_graph(self) -> StateGraph:
        """기술 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("ask_source_and_keyword", self.ask_source_and_keyword)
        workflow.add_node("search_patent", self.search_patent)
        workflow.add_node("vectorize_patent", self.vectorize_patent)
        workflow.add_node("search_article", self.search_article)
        workflow.add_node("vectorize_article", self.vectorize_article)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "ask_source_and_keyword": "ask_source_and_keyword",
                "patent": "search_patent",
                "article": "search_article",
            }
        )

        # ask_source_and_keyword -> END (사용자 응답 대기)
        workflow.add_edge("ask_source_and_keyword", END)
        
        # 특허 검색 후 벡터화 (선택적)
        workflow.add_conditional_edges(
            "search_patent",
            self.should_vectorize,
            {
                "yes": "vectorize_patent",
                "no": END,
            }
        )
        
        # 논문 검색 후 벡터화 (선택적)
        workflow.add_conditional_edges(
            "search_article",
            self.should_vectorize,
            {
                "yes": "vectorize_article",
                "no": END,
            }
        )
        
        workflow.add_edge("vectorize_patent", END)
        workflow.add_edge("vectorize_article", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """기술 도메인 내부 의도 분류."""
        LOGGER.info("--- [Tech Master] Routing Intent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")

        if "tech_search_context" not in state or state["tech_search_context"] is None:
            state["tech_search_context"] = {}
        tech_search_context = state.get("tech_search_context", {})
        
        # 특수 상태 확인 (사용자가 선택을 기다리는 상태)
        patent_ctx = None
        article_ctx = None
        
        if tech_search_context:
            if "patent" in tech_search_context:
                patent_ctx = tech_search_context.get("patent")
                if not isinstance(patent_ctx, dict):
                    patent_ctx = None
            
            if "article" in tech_search_context:
                article_ctx = tech_search_context.get("article")
                if not isinstance(article_ctx, dict):
                    article_ctx = None
        
        # ask_source_and_keyword 상태 확인 (출처 선택 대기 중)
        if tech_search_context and tech_search_context.get("awaiting_source_selection"):
            # 사용자가 출처를 선택했는지 확인
            query_lower = query.lower()
            stored_keyword = tech_search_context.get("pending_keyword", "")

            if any(k in query_lower for k in ["특허", "patent", "1", "1번"]):
                LOGGER.info(f"✅ 사용자가 특허 선택 - 키워드: '{stored_keyword}'")
                tech_search_context["awaiting_source_selection"] = False
                tech_search_context.pop("pending_keyword", None)
                final_query = stored_keyword if stored_keyword else query
                return {"intent": "patent", "query": final_query, "tech_search_context": tech_search_context}
            elif any(k in query_lower for k in ["논문", "paper", "학술", "article", "2", "2번"]):
                LOGGER.info(f"✅ 사용자가 논문 선택 - 키워드: '{stored_keyword}'")
                tech_search_context["awaiting_source_selection"] = False
                tech_search_context.pop("pending_keyword", None)
                final_query = stored_keyword if stored_keyword else query
                if final_query and "찾아줘" not in final_query and "찾아" not in final_query:
                    final_query = f"{final_query} 찾아줘"
                return {"intent": "article", "query": final_query, "tech_search_context": tech_search_context}
            else:
                # 명확하지 않은 답변 - 다시 물어봄
                LOGGER.info("⚠️ 명확하지 않은 답변, 다시 질문")
                return {"intent": "ask_source_and_keyword", "tech_search_context": tech_search_context}
        
        # ask_source_and_keyword 상태 확인 (키워드 입력 대기 중)
        if tech_search_context and tech_search_context.get("awaiting_keyword_input"):
            LOGGER.info(f"✅ 사용자가 키워드 입력: '{query}'")
            tech_search_context["awaiting_keyword_input"] = False
            
            requested_source = tech_search_context.get("requested_source")
            if requested_source in ["patent", "article"]:
                # 출처가 이미 명확하므로 바로 검색
                LOGGER.info(f"✅ 출처 명확({requested_source}), 바로 검색 진행")
                tech_search_context.pop("requested_source", None)
                
                # 논문일 때 "찾아줘" 추가
                final_query = query
                if requested_source == "article":
                    if final_query and "찾아줘" not in final_query and "찾아" not in final_query:
                        final_query = f"{final_query} 찾아줘"
                
                return {
                    "intent": requested_source,
                    "query": final_query,
                    "tech_search_context": tech_search_context
                }
            else:
                # 출처가 불명확하면 선택 요청
                tech_search_context["awaiting_source_selection"] = True
                tech_search_context["pending_keyword"] = query
                return {
                    "intent": "ask_source_and_keyword",
                    "tech_search_context": tech_search_context
                }
    
        last_active_agent = None
        if tech_search_context:
            last_active_subagent = tech_search_context.get("last_active_subagent")
            if last_active_subagent in ["patent", "article"]:
                last_active_agent = last_active_subagent
        
        patent_has_results = patent_ctx and bool(patent_ctx.get("results") or patent_ctx.get("vector_db_results"))
        article_has_results = article_ctx and bool(article_ctx.get("results"))
        
        # 논문: ask_source (최근 활성 agent가 논문이면 우선 확인)
        if article_ctx:
            article_intent = article_ctx.get("intent")
            # 논문 관련 키워드가 없는 경우에도 이전 컨텍스트 유지
            has_article_keywords = any(k in query.lower() for k in ["논문", "paper", "학술", "article", "scienceon"])
            if article_intent == "ask_source" and not has_article_keywords:
                LOGGER.info(f"✅ 이전 컨텍스트(ask_source, 논문 키워드 없음)에 따라 라우팅 유지: article")
                return {"intent": "article"}
        
        # 특허: ask_search_mode, ask_web_search (최근 활성 agent가 특허이면 우선 확인)
        if patent_ctx:
            patent_intent = patent_ctx.get("intent")
            waiting_for_web_search = patent_ctx.get("waiting_for_web_search", False)
            has_patent_keywords = any(k in query.lower() for k in ["특허", "patent"])
            if (patent_intent == "ask_search_mode" or patent_intent == "ask_web_search" or waiting_for_web_search) and not has_patent_keywords:
                LOGGER.info(f"✅ 이전 컨텍스트({patent_intent}, waiting_for_web_search={waiting_for_web_search}, 특허 키워드 없음)에 따라 라우팅 유지: patent")
                return {"intent": "patent"}
        
        if (patent_has_results) and (article_has_results):
            # 둘 다 results가 있으면 last_active_subagent 우선
            pass
        elif article_has_results:
            # 논문 results 또는 vector_db_results가 있으면 논문으로 라우팅
            last_active_agent = "article"
            LOGGER.info(f"✅ 논문 results/vector_db_results 있음, 논문으로 라우팅")
        elif patent_has_results:
            # 특허 results 또는 vector_db_results가 있으면 특허로 라우팅
            last_active_agent = "patent"
            LOGGER.info(f"✅ 특허 results/vector_db_results 있음, 특허로 라우팅")
        
        # 번호 선택 요청 확인 (연속 대화)
        selection_keywords = ["번", "자세히", "상세", "선택", "알려", "보여", "알려줘", "보여줘"]
        has_selection_keyword = any(k in query for k in selection_keywords)
        has_number_pattern = bool(re.match(r'^\d+', query.strip()))
        
        if last_active_agent and (has_number_pattern or has_selection_keyword):
            LOGGER.info(f"✅ 번호 선택 요청 감지 (숫자: {has_number_pattern}, 키워드: {has_selection_keyword}), 이전 컨텍스트 유지: {last_active_agent}")
            return {"intent": last_active_agent}
        
        # LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="tech",
            fallback_route="patent",
            session_id=session_id
        )
        
        LOGGER.info(f"🔍 LLM 분류 결과: {intent}")
        
        # query에서 출처 추출 (공통 로직)
        query_lower = query.lower()
        detected_source = None
        if any(k in query_lower for k in ["특허", "patent"]):
            detected_source = "patent"
        elif any(k in query_lower for k in ["논문", "paper", "학술", "article", "scienceon"]):
            detected_source = "article"

        if intent == "missing_keyword":
            LOGGER.info(f"⚠️ LLM: 출처 명확({detected_source}), 키워드 없음")
            state["tech_search_context"]["awaiting_keyword_input"] = True
            state["tech_search_context"]["requested_source"] = detected_source
            return {"intent": "ask_source_and_keyword", "tech_search_context": state["tech_search_context"]} 
        elif intent == "missing_source":
            LOGGER.info(f"⚠️ LLM: 키워드 있음, 출처 불명확")
            state["tech_search_context"]["awaiting_source_selection"] = True
            state["tech_search_context"]["pending_keyword"] = query
            return {"intent": "ask_source_and_keyword", "tech_search_context": state["tech_search_context"]}
        elif intent == "ambiguous":
            LOGGER.info(f"⚠️ LLM: 출처와 키워드 모두 불명확")
            state["tech_search_context"]["awaiting_source_selection"] = True
            state["tech_search_context"]["pending_keyword"] = None
            return {"intent": "ask_source_and_keyword", "tech_search_context": state["tech_search_context"]} 
        elif intent in ["patent", "article"]:
            LOGGER.info(f"✅ Tech 의도 결정: {intent}")
            return {"intent": intent}

        # 여기까지 도달하면 예상치 못한 intent
        LOGGER.warning(f"⚠️ 예상치 못한 intent: {intent}, fallback to ask_source_and_keyword")
        return {"intent": "ask_source_and_keyword", "tech_search_context": state.get("tech_search_context", {})}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "patent")
        
        # ask_source_and_keyword인 경우 특별 처리
        if intent == "ask_source_and_keyword":
            return "ask_source_and_keyword"
        
        return intent
    
    async def search_patent(self, state: OrchestratorState) -> Dict[str, Any]:
        """특허 검색 (AI-029)."""
        LOGGER.info("--- [Tech Master] Searching Patent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # orchestrator_state에서 search_mode 확인
        search_mode = None
        tech_search_context = state.get("tech_search_context", {})
        if tech_search_context:
            patent_context = tech_search_context.get("patent", {})
            if isinstance(patent_context, dict):
                # patent_context에서 search_mode 확인
                search_mode = patent_context.get("search_mode")
        
        # search_mode가 없으면 질문에서 직접 파싱
        if not search_mode:
            query_lower = query.lower()
            web_keywords = ["웹에서", "인터넷에서", "온라인에서", "kipris", "웹 검색", "인터넷 검색", "웹으로", "인터넷으로"]
            server_keywords = ["서버에서", "내부에서", "벡터db", "벡터 db", "내부 데이터", "사내", "서버로", "내부로"]
            
            if any(kw in query_lower for kw in web_keywords):
                search_mode = "web"
                LOGGER.info(f"[Tech Master] 질문에서 '웹' 검색 모드 감지: '{query}' → search_mode=web")
            elif any(kw in query_lower for kw in server_keywords):
                search_mode = "server"
                LOGGER.info(f"[Tech Master] 질문에서 '서버' 검색 모드 감지: '{query}' → search_mode=server")
        
        # search_mode를 patent_search에 전달
        result = await patent_search(
            query, 
            session_id=session_id, 
            search_mode=search_mode,
            orchestrator_state=state
        )
        
        if "tech_search_context" not in state or state["tech_search_context"] is None:
            state["tech_search_context"] = {}
        state["tech_search_context"]["last_active_subagent"] = "patent"
        
        message = result.get("message") or result.get("error") or "특허 검색 결과를 찾을 수 없습니다."
        return {
            "final_response": message,
            "search_results": result.get("items", []),
            "messages": [message],
            "tech_search_context": state.get("tech_search_context"),
        }
    
    def should_vectorize(self, state: OrchestratorState) -> str:
        """벡터화가 필요한지 결정."""
        # 간단한 로직: 검색 결과가 있고 사용자가 요청하면 벡터화
        # 실제로는 사용자 의도나 설정에 따라 결정
        if state.get("search_results"):
            return "no"  # 기본적으로 자동 벡터화하지 않음
        return "no"
    
    async def vectorize_patent(self, state: OrchestratorState) -> Dict[str, Any]:
        """특허 벡터화 (AI-030)."""
        LOGGER.info("--- [Tech Master] Vectorizing Patent ---")
        search_results = state.get("search_results", [])
        
        # 특허 벡터화 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "특허가 벡터화되어 저장되었습니다.",
            "messages": ["특허 벡터화가 완료되었습니다."],
        }
    
    async def search_article(self, state: OrchestratorState) -> Dict[str, Any]:
        """논문 검색 (AI-031)."""
        LOGGER.info("--- [Tech Master] Searching Article ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        result = await article_master(query, session_id=session_id, orchestrator_state=state)
        
        if "tech_search_context" not in state or state["tech_search_context"] is None:
            state["tech_search_context"] = {}
        state["tech_search_context"]["last_active_subagent"] = "article"
        
        return {
            "final_response": result.get("message", "논문 검색 결과를 찾을 수 없습니다."),
            "search_results": result.get("items", []),
            "messages": [result.get("message", "")],
        }
    
    async def vectorize_article(self, state: OrchestratorState) -> Dict[str, Any]:
        """논문 벡터화 (AI-032)."""
        LOGGER.info("--- [Tech Master] Vectorizing Article ---")
        search_results = state.get("search_results", [])
        
        # 논문 벡터화 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "논문이 벡터화되어 저장되었습니다.",
            "messages": ["논문 벡터화가 완료되었습니다."],
        }

    async def ask_source_and_keyword(self, state: OrchestratorState) -> Dict[str, Any]:
        """출처/키워드 질문 (Human-in-the-loop)."""
        LOGGER.info("--- [Tech Master] Asking Source and Keyword ---")
        tech_search_context = state.get("tech_search_context", {})
        
        # 상태에 따라 다른 메시지 생성
        awaiting_keyword = tech_search_context.get("awaiting_keyword_input", False)
        awaiting_source = tech_search_context.get("awaiting_source_selection", False)
        requested_source = tech_search_context.get("requested_source")
        pending_keyword = tech_search_context.get("pending_keyword")
        
        if awaiting_keyword and requested_source:
            # 출처는 명확하지만 키워드가 필요한 경우
            source_name = "특허" if requested_source == "patent" else "논문"
            message = f"어떤 주제의 {source_name}을 검색하시겠습니까?\n\n예) 인공지능, 머신러닝, 블록체인 등"
        elif awaiting_source and pending_keyword:
            # 키워드는 있지만 출처가 필요한 경우
            message = f"어디에서 검색하시겠습니까?\n\n1️⃣ 특허 검색\n2️⃣ 논문 검색\n\n'특허', '1번' 또는 '논문', '2번'으로 답변해주세요."
        else:
            # 출처와 키워드 모두 필요한 경우 (한 번에 물어봄)
            message = "어떤 주제를 어디에서 검색하시겠습니까?\n\n예시)\n- 특허에서 인공지능 검색\n- 논문에서 머신러닝 검색\n\n"
        
        return {
            "final_response": message,
            "messages": [message],
            "tech_search_context": tech_search_context,
        }


def get_tech_master_graph() -> StateGraph:
    """기술 마스터그래프 인스턴스 반환."""
    master = TechMasterGraph()
    return master.get_graph()
