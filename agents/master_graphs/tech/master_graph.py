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
        
        # 1. 이전 대화 컨텍스트 확인 (Redis 숏텀 메모리)
        # "웹 검색", "사내 검색", "1", "2" 등 모호한 답변이 들어올 경우
        # 이전에 활성화된 서브 에이전트(patent 또는 article)로 라우팅
        
        last_active_agent = None
        last_timestamp = 0
        
        # 특허 검색 컨텍스트 확인
        if tech_search_context and "patent" in tech_search_context:
            patent_ctx = tech_search_context.get("patent")
            if patent_ctx and isinstance(patent_ctx, dict):
                patent_intent = patent_ctx.get("intent")
                waiting_for_web_search = patent_ctx.get("waiting_for_web_search", False)
                
                # ask_search_mode 또는 ask_web_search 상태라면 무조건 patent로 라우팅 (우선순위 최상)
                if patent_intent == "ask_search_mode" or patent_intent == "ask_web_search" or waiting_for_web_search:
                    print(f"✅ 이전 컨텍스트({patent_intent}, waiting_for_web_search={waiting_for_web_search})에 따라 라우팅 유지: patent")
                    return {"intent": "patent"}
                
                # 최근 검색 결과나 진행 중인 상태가 있는지 확인
                if patent_ctx.get("results"):
                    last_active_agent = "patent"
                    # 타임스탬프가 있다면 비교 로직 추가 가능 (현재는 단순 우선순위나 플래그로 처리)
        
        # 논문 검색 컨텍스트 확인 (특허보다 우선순위가 낮음 - 특허가 이미 설정되었으면 무시)
        # 특허 검색이 활성화되어 있으면 논문으로 덮어쓰지 않음
        if tech_search_context and "article" in tech_search_context and not last_active_agent:
            article_ctx = tech_search_context.get("article")
            if article_ctx and isinstance(article_ctx, dict):
                if article_ctx.get("intent") == "ask_source":
                    last_active_agent = "article"
                elif article_ctx.get("results"):
                    last_active_agent = "article"
                
        # 2. 사용자가 명시적으로 "논문" 또는 "특허"를 요청한 경우 이전 컨텍스트보다 우선
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
        
        # 3. 선택지 답변이거나 연속된 대화인 경우 이전 에이전트 유지
        # 단, "찾아"는 제외 (명시적 요청일 수 있음)
        
        # 번호 선택 관련 키워드 (예: "10번 자세히 알려줘", "1번 상세", "2번 선택")
        selection_keywords = ["번", "자세히", "상세", "선택", "알려", "보여", "알려줘", "보여줘"]
        has_selection_keyword = any(k in query for k in selection_keywords)
        
        # 숫자 패턴 감지 (예: "10번", "1번", "2번 자세히", "10")
        has_number_pattern = bool(re.match(r'^\d+', query.strip()))
        
        # 이전 검색 결과가 있고, 번호 선택 요청인 경우 우선 처리
        if last_active_agent and (has_number_pattern or has_selection_keyword):
            # 숫자로 시작하거나 번호 선택 키워드가 있으면 연속 대화로 판단
            print(f"✅ 번호 선택 요청 감지 (숫자: {has_number_pattern}, 키워드: {has_selection_keyword}), 이전 컨텍스트 유지: {last_active_agent}")
            return {"intent": last_active_agent}
        
        # 기존 continuation 로직 (웹 검색 선택 등)
        is_continuation = any(k in query for k in ["웹", "사내", "1", "2", "3", "더"]) or query.isdigit()
        
        if is_continuation and last_active_agent:
            print(f"✅ 이전 컨텍스트에 따라 라우팅 유지: {last_active_agent}")
            return {"intent": last_active_agent}

        # 4. llm_router를 사용한 LLM 기반 분류 (새로운 요청인 경우)
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
        
        # patent 서브 에이전트 사용 (Redis 숏텀 메모리 state 전달)
        # patent_search 함수는 비동기 함수이므로 await 사용
        result = await patent_search(query, session_id=session_id, orchestrator_state=state)
        
        # patent_search에서 업데이트된 state를 반영하여 Redis에 저장
        # message가 비어있으면 error 필드 확인
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
        
        # 기존 tech_article_agent 사용 (Redis 숏텀 메모리 state 전달)
        # article_master 함수는 이제 비동기 함수이므로 await 사용
        result = await article_master(query, session_id=session_id, orchestrator_state=state)
        
        # article_master에서 이미 orchestrator_state에 tech_search_context를 저장했으므로
        # 불필요한 딕셔너리 복사 제거 (성능 최적화)
        return {
            "final_response": result.get("message", "논문 검색 결과를 찾을 수 없습니다."),
            "search_results": result.get("items", []),
            "messages": [result.get("message", "")],
            # tech_search_context는 이미 state에 저장되어 있으므로 명시적으로 반환하지 않아도 됨
            # LangGraph가 state를 자동으로 병합하므로 불필요한 복사 제거
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

