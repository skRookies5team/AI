"""기술 마스터그래프 - 특허/논문 검색 (AI-028~AI-032)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.tech.tech_patent_agent import patent_search
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
        
        # llm_router를 사용한 LLM 기반 분류
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
        
        # 기존 tech_patent_agent 사용
        result = patent_search(query, session_id=session_id)
        
        return {
            "final_response": result.get("message", "특허 검색 결과를 찾을 수 없습니다."),
            "search_results": result.get("items", []),
            "messages": [result.get("message", "")],
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
        
        # 기존 tech_article_agent 사용
        # article_master 함수 사용
        result = article_master(query, session_id=session_id)
        
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

