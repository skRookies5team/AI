"""Q&A 마스터그래프 - 질문 답변 및 조회 (AI-016~AI-020)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.qa.qa_manual_agent import manual_search
from agents.master_graphs.qa.qa_benefit_agent import benefit_rag
from agents.master_graphs.qa.qa_cafeteria_agent import cafeteria_menu


class QAMasterGraph(BaseMasterGraph):
    """Q&A 도메인 마스터그래프.
    
    요구사항:
    - AI-016: QA 라우팅
    - AI-017: 매뉴얼 검색 (RAG)
    - AI-018: 복리후생 조회
    - AI-019: 식단 조회
    - AI-020: 메뉴 추천
    """
    
    def get_domain_name(self) -> str:
        return "qa"
    
    def get_graph(self) -> StateGraph:
        """Q&A 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("search_manual", self.search_manual)
        workflow.add_node("query_benefit", self.query_benefit)
        workflow.add_node("query_menu", self.query_menu)
        workflow.add_node("recommend_menu", self.recommend_menu)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "manual": "search_manual",
                "benefit": "query_benefit",
                "menu": "query_menu",
                "recommend": "recommend_menu",
            }
        )
        
        # 모든 하위 에이전트는 END로
        workflow.add_edge("search_manual", END)
        workflow.add_edge("query_benefit", END)
        workflow.add_edge("query_menu", END)
        workflow.add_edge("recommend_menu", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """Q&A 도메인 내부 의도 분류."""
        print("--- [QA Master] Routing Intent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="qa",
            fallback_route="manual",
            session_id=session_id
        )
        
        # llm_router는 "email", "benefit", "cafeteria", "manual"을 반환하지만,
        # 우리 그래프는 "manual", "benefit", "menu", "recommend"를 사용
        if intent == "cafeteria":
            # 식단 조회 vs 추천 구분
            if any(k in query for k in ["추천", "다이어트", "어떻게", "뭐 먹", "먹을까"]):
                intent = "recommend"
            else:
                intent = "menu"
        elif intent == "email":
            # 이메일 검색은 이 도메인에서 처리하지 않음 (상위 Orchestrator에서 처리)
            # 하지만 혹시 모를 경우를 위해 manual로 fallback
            intent = "manual"
        elif intent not in ["manual", "benefit", "menu", "recommend"]:
            intent = "manual"  # 기본값
        
        print(f"✅ QA 의도 결정: {intent}")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "manual")
        return intent
    
    async def search_manual(self, state: OrchestratorState) -> Dict[str, Any]:
        """매뉴얼 검색 (RAG) (AI-017)."""
        print("--- [QA Master] Searching Manual ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # 기존 qa_manual_agent 사용 (비동기 호출, state 전달)
        result = await manual_search(query, orchestrator_state=state)
        
        # 검색 성공 여부 확인
        is_success = result.get("ok", False)
        message = result.get("message", "")
        error = result.get("error", "")
        
        # 명확한 manual 관련 키워드가 있으면 fallback하지 않음
        manual_keywords = ["메뉴얼", "manual", "매뉴얼", "연차", "휴가", "병가", "반차", "경조사", "출장", 
                          "근무", "근태", "규정", "정책", "지침", "가이드", "안내"]
        has_manual_keyword = any(kw in query.lower() for kw in manual_keywords)
        
        # 검색 결과가 없거나 부적절한 경우, 다른 에이전트로 fallback 시도
        # 단, manual 관련 키워드가 있으면 fallback하지 않음
        should_fallback = (
            not is_success or 
            not message or 
            "찾을 수 없습니다" in message or 
            "포함되어 있지 않습니다" in message or
            "오류가 발생했습니다" in message or
            "검색 중 오류" in message
        )
        
        if should_fallback and not has_manual_keyword:
            # 식단/메뉴 관련 키워드가 있으면 menu로 재라우팅
            if any(k in query for k in ["식단", "메뉴", "구내식당", "점심", "저녁", "다이어트", "먹어야", "먹을까"]):
                print("⚠️ Manual 검색 실패, menu로 재라우팅")
                return await self.query_menu(state)
            # 복리후생 키워드가 있으면 benefit으로 재라우팅
            elif any(k in query for k in ["복리후생", "휴가규정", "보험", "수당"]):
                print("⚠️ Manual 검색 실패, benefit으로 재라우팅")
                return await self.query_benefit(state)
        
        # manual 관련 키워드가 있거나 성공한 경우, 결과 반환
        return {
            "final_response": message if message else "검색 결과를 찾을 수 없습니다.",
            "messages": [message] if message else ["검색 결과를 찾을 수 없습니다."],
            "qa_search_context": state.get("qa_search_context", {}),  # Redis에 저장된 컨텍스트 반환
        }
    
    async def query_benefit(self, state: OrchestratorState) -> Dict[str, Any]:
        """복리후생 조회 (AI-018)."""
        print("--- [QA Master] Querying Benefit ---")
        query = state.get("query", "")
        
        # 기존 qa_benefit_agent 사용 (비동기 호출, state 전달)
        result = await benefit_rag(query, orchestrator_state=state)
        
        return {
            "final_response": result.get("message", "복리후생 정보를 찾을 수 없습니다."),
            "messages": [result.get("message", "")],
            "qa_search_context": state.get("qa_search_context", {}),  # Redis에 저장된 컨텍스트 반환
        }
    
    async def query_menu(self, state: OrchestratorState) -> Dict[str, Any]:
        """식단 조회 (AI-019)."""
        print("--- [QA Master] Querying Menu ---")
        query = state.get("query", "")
        
        # 기존 qa_cafeteria_agent 사용 (비동기 호출, state 전달)
        result = await cafeteria_menu(query, orchestrator_state=state)
        
        return {
            "final_response": result.get("message", "식단 정보를 찾을 수 없습니다."),
            "messages": [result.get("message", "")],
            "qa_search_context": state.get("qa_search_context", {}),  # Redis에 저장된 컨텍스트 반환
        }
    
    async def recommend_menu(self, state: OrchestratorState) -> Dict[str, Any]:
        """메뉴 추천 (AI-020)."""
        print("--- [QA Master] Recommending Menu ---")
        query = state.get("query", "")
        employee_id = state.get("employee_id")
        
        # 메뉴 추천: 식단 조회 결과를 바탕으로 추천 메시지 추가 (state 전달)
        result = await cafeteria_menu(query, orchestrator_state=state)
        
        # 식단 조회 결과가 있으면 추천 메시지 추가
        message = result.get("message", "")
        if message and "찾을 수 없습니다" not in message:
            # 다이어트 관련 질문인 경우 추가 안내
            if "다이어트" in query.lower():
                message += "\n\n💡 다이어트를 위한 팁:\n- 칼로리가 낮은 메뉴를 선택하세요.\n- 단백질이 풍부한 메뉴를 권장합니다.\n- 탄수화물 섭취를 줄이는 것이 도움이 됩니다."
            else:
                message += "\n\n💡 추천 메뉴를 확인하시려면 식단표를 참고하세요."
        
        return {
            "final_response": message if message else "메뉴 추천을 제공할 수 없습니다. 식단 조회를 먼저 해주세요.",
            "messages": [message] if message else ["메뉴 추천을 제공할 수 없습니다."],
            "qa_search_context": state.get("qa_search_context", {}),  # Redis에 저장된 컨텍스트 반환
        }


def get_qa_master_graph() -> StateGraph:
    """Q&A 마스터그래프 인스턴스 반환."""
    master = QAMasterGraph()
    return master.get_graph()

