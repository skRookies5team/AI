"""스케줄 마스터그래프 - 연차/일정/회의 관리 (AI-002~AI-010)."""

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.master_graphs.base import BaseMasterGraph
from .router import determine_schedule_intent


class ScheduleMasterGraph(BaseMasterGraph):
    """스케줄 도메인 마스터그래프.
    
    요구사항:
    - AI-002: 스케줄 라우팅 (연차/일정/회의 분류)
    - AI-003: 연차/반차 조회
    - AI-004: 연차/반차 신청
    - AI-005: 일정 조회
    - AI-006: 일정 등록/취소
    - AI-007: 일정 알림
    - AI-008: 회의실 조회
    - AI-009: 회의실 추천
    - AI-010: 예약/취소
    """
    
    def get_domain_name(self) -> str:
        return "schedule"
    
    def get_graph(self) -> StateGraph:
        """스케줄 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("handle_leave", self.handle_leave)
        workflow.add_node("handle_schedule", self.handle_schedule)
        workflow.add_node("handle_meeting_room", self.handle_meeting_room)
        workflow.add_node("handle_business_trip", self.handle_business_trip)
        workflow.add_node("handle_interview", self.handle_interview)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "leave": "handle_leave",
                "schedule": "handle_schedule",
                "meeting": "handle_meeting_room",
                "business_trip": "handle_business_trip",
                "interview": "handle_interview",
            }
        )
        
        # 모든 하위 에이전트는 END로
        workflow.add_edge("handle_leave", END)
        workflow.add_edge("handle_schedule", END)
        workflow.add_edge("handle_meeting_room", END)
        workflow.add_edge("handle_business_trip", END)
        workflow.add_edge("handle_interview", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """스케줄 도메인 내부 의도 분류."""
        print("--- [Schedule Master] Routing Intent ---")
        intent = await determine_schedule_intent(state)
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "schedule")
        return intent
    
    async def handle_leave(self, state: OrchestratorState) -> Dict[str, Any]:
        """연차/반차 관리 (AI-003, AI-004).
        
        Redis 기반 숏텀메모리를 사용하여 이전 대화 컨텍스트를 유지합니다.
        """
        print("--- [Schedule Master] Handling Leave ---")
        
        # nodes/leave_nodes.py에서 처리
        from agents.master_graphs.schedule.nodes.leave_nodes import process_leave_request
        
        result = await process_leave_request(state)
        return result
    
    async def handle_schedule(self, state: OrchestratorState) -> Dict[str, Any]:
        """일정 관리 (AI-005, AI-006, AI-007).
        
        Redis 기반 숏텀메모리를 사용하여 이전 대화 컨텍스트를 유지합니다.
        """
        print("--- [Schedule Master] Handling Schedule ---")
        
        # nodes/schedule_nodes.py에서 처리
        from agents.master_graphs.schedule.nodes.schedule_nodes import process_schedule_request
        
        result = await process_schedule_request(state)
        return result
    
    async def handle_meeting_room(self, state: OrchestratorState) -> Dict[str, Any]:
        """회의실 관리 (AI-008, AI-009, AI-010).
        
        Redis 기반 숏텀메모리를 사용하여 이전 대화 컨텍스트를 유지하고,
        Human-in-the-loop을 통해 회의실 예약 시 승인을 받습니다.
        """
        print("--- [Schedule Master] Handling Meeting Room ---")
        
        # nodes/meeting_room/core.py에서 처리
        from .nodes.meeting_room.core import process_meeting_room_request
        
        result = await process_meeting_room_request(state)
        return result
    
    async def handle_business_trip(self, state: OrchestratorState) -> Dict[str, Any]:
        """출장 관리.
        
        Redis 기반 숏텀메모리를 사용하여 이전 대화 컨텍스트를 유지합니다.
        일정 조회, 출장 신청, 캘린더 등록, 팀원 알림을 처리합니다.
        """
        print("--- [Schedule Master] Handling Business Trip ---")
        
        # nodes/business_trip_nodes.py에서 처리
        from agents.master_graphs.schedule.nodes.business_trip_nodes import process_business_trip_request
        
        result = await process_business_trip_request(state)
        return result

    async def handle_interview(self, state: OrchestratorState) -> Dict[str, Any]:
        """면접 일정 관리."""
        from .nodes.interview_nodes import process_interview_request

        result = await process_interview_request(state)
        return result


def get_schedule_master_graph() -> StateGraph:
    """스케줄 마스터그래프 인스턴스 반환."""
    master = ScheduleMasterGraph()
    return master.get_graph()