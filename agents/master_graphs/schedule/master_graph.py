"""스케줄 마스터그래프 - 연차/일정/회의 관리 (AI-002~AI-010)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio
import time

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph


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
            }
        )
        
        # 모든 하위 에이전트는 END로
        workflow.add_edge("handle_leave", END)
        workflow.add_edge("handle_schedule", END)
        workflow.add_edge("handle_meeting_room", END)
        workflow.add_edge("handle_business_trip", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """스케줄 도메인 내부 의도 분류."""
        print("--- [Schedule Master] Routing Intent ---")
        step_start = time.time()
        query = state.get("query", "")
        session_id = state.get("session_id")

        # ✅ 워크플로우 모드 확인 (최우선 처리)
        workflow_in_progress = state.get("workflow_in_progress", False)
        workflow_type = state.get("workflow_type")
        current_step_index = state.get("current_workflow_step_index", 0)
        workflow_steps = state.get("workflow_steps", [])

        # 출장 워크플로우 진행 중인 경우 의도를 business_trip으로 강제
        if workflow_in_progress and workflow_type == "business_trip":
            # 현재 단계가 schedule 도메인이라면 business_trip으로 라우팅 (Step 1 또는 Step 3)
            if current_step_index < len(workflow_steps) and workflow_steps[current_step_index] == "schedule":
                step_elapsed = (time.time() - step_start) * 1000
                print(f"✅ 워크플로우 모드 감지: {workflow_type} (Step {current_step_index + 1}) → business_trip으로 의도 강제 ({step_elapsed/1000:.2f}초)")
                return {"intent": "business_trip"}

        schedule_context = state.get("schedule_context") or {}
        
        # schedule의 awaiting_slots 체크 (일정 삭제 선택 대기 중)
        schedule_ctx = schedule_context.get("schedule") or {}
        schedule_awaiting_slots = schedule_ctx.get("awaiting_slots") or []
        if schedule_awaiting_slots:
            # delete_selection이나 update_selection이 있으면 schedule로 강제 라우팅
            if "delete_selection" in schedule_awaiting_slots or "update_selection" in schedule_awaiting_slots:
                step_elapsed = (time.time() - step_start) * 1000
                print(f"✅ 일정 삭제/수정 선택 대기 중 - schedule으로 강제 라우팅 ({step_elapsed/1000:.2f}초)")
                return {"intent": "schedule"}
        
        # delete_candidates가 있으면 연속 삭제 가능하므로 schedule로 강제 라우팅
        if schedule_ctx.get("delete_candidates"):
            step_elapsed = (time.time() - step_start) * 1000
            print(f"✅ 일정 삭제 후보가 남아있음 - schedule으로 강제 라우팅 ({step_elapsed/1000:.2f}초)")
            return {"intent": "schedule"}
        
        # meeting의 awaiting_slots 체크
        meeting_context = schedule_context.get("meeting") or {}
        meeting_slot_state = meeting_context.get("slot_state") or {}
        awaiting_slots = meeting_context.get("awaiting_slots") or []
        missing_slots = meeting_slot_state.get("missing_slots") or []
        
        # 추천 후 회의실 선택 중이면 무조건 meeting으로 라우팅
        if meeting_context.get("recommended_rooms"):
            step_elapsed = (time.time() - step_start) * 1000
            print(f"✅ 회의실 추천 후 선택 진행 중 - meeting으로 강제 라우팅 ({step_elapsed/1000:.2f}초)")
            return {"intent": "meeting"}
        
        if awaiting_slots:
            step_elapsed = (time.time() - step_start) * 1000
            print(f"✅ 회의실 슬롯 질의 응답 진행 중 - meeting으로 강제 라우팅 ({step_elapsed/1000:.2f}초)")
            return {"intent": "meeting"}
        if missing_slots:
            step_elapsed = (time.time() - step_start) * 1000
            print(f"✅ 회의실 슬롯이 아직 남아 있음 - meeting으로 강제 라우팅 ({step_elapsed/1000:.2f}초)")
            return {"intent": "meeting"}
        pending_reservation = meeting_context.get("pending_reservation")
        awaiting_field = meeting_context.get("awaiting_field")
        if pending_reservation and awaiting_field:
            step_elapsed = (time.time() - step_start) * 1000
            print(f"✅ 회의실 예약 슬롯 채우기 진행 중 - meeting으로 강제 라우팅 ({step_elapsed/1000:.2f}초)")
            return {"intent": "meeting"}
        
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="schedule",
            fallback_route="schedule",
            session_id=session_id
        )
        
        # intent 검증: "leave", "schedule", "meeting", "business_trip" 중 하나
        if intent not in ["leave", "schedule", "meeting", "business_trip"]:
            intent = "schedule"  # 기본값
        
        step_elapsed = (time.time() - step_start) * 1000
        print(f"✅ Schedule 의도 결정: {intent} ({step_elapsed/1000:.2f}초)")
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
        
        # nodes/meeting_room_nodes.py에서 처리
        from agents.master_graphs.schedule.nodes.meeting_room_nodes import process_meeting_room_request
        
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


def get_schedule_master_graph() -> StateGraph:
    """스케줄 마스터그래프 인스턴스 반환."""
    master = ScheduleMasterGraph()
    return master.get_graph()