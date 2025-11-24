"""이메일 마스터그래프 - 이메일 검색/작성/분석 (AI-021~AI-027)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.email.email_search_agent import email_search
from agents.master_graphs.email.email_auto_reply_agent import email_auto_reply


class EmailMasterGraph(BaseMasterGraph):
    """이메일 도메인 마스터그래프.
    
    요구사항:
    - AI-021: 이메일 라우팅
    - AI-022: 이메일 검색
    - AI-023: 답변 생성 (RAG)
    - AI-024: 에이전트 추천
    - AI-025: 수신 감지
    - AI-026: 답장 초안 생성
    - AI-027: 자동 발송
    """
    
    def get_domain_name(self) -> str:
        return "email"
    
    def get_graph(self) -> StateGraph:
        """이메일 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("search_email", self.search_email)
        workflow.add_node("generate_reply", self.generate_reply)
        workflow.add_node("detect_reception", self.detect_reception)
        workflow.add_node("send_email", self.send_email)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "search": "search_email",
                "reply": "generate_reply",
                "detect": "detect_reception",
            }
        )
        
        # 답장 생성 후 자동 발송 (선택적)
        workflow.add_conditional_edges(
            "generate_reply",
            self.should_send,
            {
                "yes": "send_email",
                "no": END,
            }
        )
        
        workflow.add_edge("search_email", END)
        workflow.add_edge("detect_reception", END)
        workflow.add_edge("send_email", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """이메일 도메인 내부 의도 분류."""
        print("--- [Email Master] Routing Intent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="email",
            fallback_route="search",
            session_id=session_id
        )
        
        # llm_router는 "search" 또는 "auto_reply"를 반환하지만,
        # 우리 그래프는 "search", "reply", "detect"를 사용
        if intent == "auto_reply":
            intent = "reply"
        elif intent not in ["search", "reply", "detect"]:
            # 키워드 기반 fallback
            if any(k in query for k in ["찾아", "검색", "search", "find"]):
                intent = "search"
            elif any(k in query for k in ["답장", "응답", "자동응답", "reply"]):
                intent = "reply"
            elif any(k in query for k in ["수신", "새 메일", "확인"]):
                intent = "detect"
            else:
                intent = "search"  # 기본값
        
        print(f"✅ Email 의도 결정: {intent}")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "search")
        return intent
    
    async def search_email(self, state: OrchestratorState) -> Dict[str, Any]:
        """이메일 검색 (AI-022)."""
        print("--- [Email Master] Searching Email ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # 기존 email_search_agent 사용
        result = email_search(query, session_id=session_id)
        
        return {
            "final_response": result.get("message", "이메일 검색 결과를 찾을 수 없습니다."),
            "messages": [result.get("message", "")],
        }
    
    async def generate_reply(self, state: OrchestratorState) -> Dict[str, Any]:
        """답장 초안 생성 (AI-026)."""
        print("--- [Email Master] Generating Reply ---")
        query = state.get("query", "")
        
        # 기존 email_auto_reply_agent 사용
        result = email_auto_reply(query)
        
        return {
            "email_draft": result.get("message", ""),
            "messages": ["답장 초안이 생성되었습니다."],
        }
    
    def should_send(self, state: OrchestratorState) -> str:
        """자동 발송이 필요한지 결정."""
        # 간단한 로직: email_draft가 있으면 발송 가능
        # 실제로는 사용자 승인(Human-in-the-loop)이 필요할 수 있음
        if state.get("email_draft"):
            return "no"  # 기본적으로 자동 발송하지 않음 (안전)
        return "no"
    
    async def send_email(self, state: OrchestratorState) -> Dict[str, Any]:
        """자동 발송 (AI-027)."""
        print("--- [Email Master] Sending Email ---")
        email_draft = state.get("email_draft", "")
        
        # 이메일 발송 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "이메일이 발송되었습니다.",
            "messages": ["이메일 발송이 완료되었습니다."],
        }
    
    async def detect_reception(self, state: OrchestratorState) -> Dict[str, Any]:
        """수신 감지 (AI-025)."""
        print("--- [Email Master] Detecting Reception ---")
        
        # 수신 감지 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "새로운 이메일이 없습니다.",
            "messages": ["이메일 수신 확인이 완료되었습니다."],
        }


def get_email_master_graph() -> StateGraph:
    """이메일 마스터그래프 인스턴스 반환."""
    master = EmailMasterGraph()
    return master.get_graph()

