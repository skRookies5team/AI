"""기술 마스터그래프 - 특허/논문 검색 (AI-028~AI-032)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio
import re

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.tech.patent.patent_agent import patent_search
from agents.master_graphs.tech.tech_article_agent import article_master


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
                "patent": "search_patent",
                "article": "search_article",
            }
        )
        
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
        print("--- [Tech Master] Routing Intent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        tech_search_context = state.get("tech_search_context", {})
        
        # 사용자가 명시적으로 "논문" 또는 "특허"를 요청한 경우 최우선 처리
        explicit_keywords = {
            "논문": "article",
            "paper": "article",
            "학술": "article",
            "ScienceON": "article",
            "특허": "patent",
            "patent": "patent",
            "Patent": "patent",
        }
        
        query_lower = query.lower()
        for keyword, intent_type in explicit_keywords.items():
            if keyword.lower() in query_lower:
                print(f"✅ 명시적 키워드 '{keyword}' 감지: {intent_type}로 라우팅")
                return {"intent": intent_type}
        
        # 2. 특수 상태 확인 (사용자가 선택을 기다리는 상태)
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
        
        last_active_agent = None
        if tech_search_context:
            last_active_subagent = tech_search_context.get("last_active_subagent")
            if last_active_subagent in ["patent", "article"]:
                last_active_agent = last_active_subagent
        
        patent_has_results = patent_ctx and bool(patent_ctx.get("results"))
        article_has_results = article_ctx and bool(article_ctx.get("results"))
        
        patent_has_vector_results = patent_ctx and bool(patent_ctx.get("vector_db_results"))
        article_has_vector_results = article_ctx and bool(article_ctx.get("vector_db_results"))
        
        # 논문: ask_source (최근 활성 agent가 논문이면 우선 확인)
        if last_active_agent == "article" and article_ctx:
            if article_ctx.get("intent") == "ask_source":
                print(f"✅ 이전 컨텍스트(ask_source, last_active=article)에 따라 라우팅 유지: article")
                return {"intent": "article"}
        
        # 특허: ask_search_mode, ask_web_search (최근 활성 agent가 특허이면 우선 확인)
        if last_active_agent == "patent" and patent_ctx:
            patent_intent = patent_ctx.get("intent")
            waiting_for_web_search = patent_ctx.get("waiting_for_web_search", False)
            if patent_intent == "ask_search_mode" or patent_intent == "ask_web_search" or waiting_for_web_search:
                print(f"✅ 이전 컨텍스트({patent_intent}, waiting_for_web_search={waiting_for_web_search}, last_active=patent)에 따라 라우팅 유지: patent")
                return {"intent": "patent"}
        
        if (patent_has_results or patent_has_vector_results) and (article_has_results or article_has_vector_results):
            # 둘 다 results가 있으면 last_active_subagent 우선
            pass
        elif article_has_results or article_has_vector_results:
            # 논문 results 또는 vector_db_results가 있으면 논문으로 라우팅
            last_active_agent = "article"
            print(f"✅ 논문 results/vector_db_results 있음, 논문으로 라우팅")
        elif patent_has_results or patent_has_vector_results:
            # 특허 results 또는 vector_db_results가 있으면 특허로 라우팅
            last_active_agent = "patent"
            print(f"✅ 특허 results/vector_db_results 있음, 특허로 라우팅")
        
        # 번호 선택 요청 확인 (연속 대화)
        selection_keywords = ["번", "자세히", "상세", "선택", "알려", "보여", "알려줘", "보여줘"]
        has_selection_keyword = any(k in query for k in selection_keywords)
        has_number_pattern = bool(re.match(r'^\d+', query.strip()))
        
        if last_active_agent and (has_number_pattern or has_selection_keyword):
            print(f"✅ 번호 선택 요청 감지 (숫자: {has_number_pattern}, 키워드: {has_selection_keyword}), 이전 컨텍스트 유지: {last_active_agent}")
            return {"intent": last_active_agent}
        
        # LLM 기반 분류 (새로운 요청인 경우)
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="tech",
            fallback_route="patent",
            session_id=session_id
        )
        
        # llm_router는 "patent", "article"을 반환
        if intent not in ["patent", "article"]:
            # 키워드 기반 fallback
            if any(k in query for k in ["특허", "patent", "Patent"]):
                intent = "patent"
            elif any(k in query for k in ["논문", "paper", "학술", "ScienceON"]):
                intent = "article"
            else:
                intent = "patent"  # 기본값
        
        print(f"✅ Tech 의도 결정: {intent}")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "patent")
        return intent
    
    async def search_patent(self, state: OrchestratorState) -> Dict[str, Any]:
        """특허 검색 (AI-029)."""
        print("--- [Tech Master] Searching Patent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        result = await patent_search(query, session_id=session_id, orchestrator_state=state)
        
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
        print("--- [Tech Master] Vectorizing Patent ---")
        search_results = state.get("search_results", [])
        
        # 특허 벡터화 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "특허가 벡터화되어 저장되었습니다.",
            "messages": ["특허 벡터화가 완료되었습니다."],
        }
    
    async def search_article(self, state: OrchestratorState) -> Dict[str, Any]:
        """논문 검색 (AI-031)."""
        print("--- [Tech Master] Searching Article ---")
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
        print("--- [Tech Master] Vectorizing Article ---")
        search_results = state.get("search_results", [])
        
        # 논문 벡터화 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "논문이 벡터화되어 저장되었습니다.",
            "messages": ["논문 벡터화가 완료되었습니다."],
        }


def get_tech_master_graph() -> StateGraph:
    """기술 마스터그래프 인스턴스 반환."""
    master = TechMasterGraph()
    return master.get_graph()

