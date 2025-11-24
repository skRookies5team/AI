"""스케줄 마스터그래프 - 연차/일정/회의 관리 (AI-002~AI-010)."""

from __future__ import annotations

from typing import Any, Dict, Optional
import asyncio

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.schedule.leave_agent import LeaveQuery, handle_leave
from agents.master_graphs.schedule.schedule_api import create_schedule, cancel_schedule
from agents.master_graphs.schedule.schedule_data import ScheduleFilters, fetch_schedule_rows, get_session, _resolve_employee_ids_by_names
from agents.master_graphs.schedule.leave_data import _run


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
        workflow.add_node("handle_meeting", self.handle_meeting)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅: route_intent의 결과에 따라 분기
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "leave": "handle_leave",
                "schedule": "handle_schedule",
                "meeting": "handle_meeting",
            }
        )
        
        # 모든 하위 에이전트는 END로
        workflow.add_edge("handle_leave", END)
        workflow.add_edge("handle_schedule", END)
        workflow.add_edge("handle_meeting", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """스케줄 도메인 내부 의도 분류."""
        print("--- [Schedule Master] Routing Intent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="schedule",
            fallback_route="schedule",
            session_id=session_id
        )
        
        # llm_router는 "leave", "meeting", "schedule"을 반환
        if intent not in ["leave", "meeting", "schedule"]:
            # 키워드 기반 fallback
            if any(k in query for k in ["연차", "반차", "병가", "경조사", "휴가", "신청", "잔여"]):
                intent = "leave"
            elif any(k in query for k in ["회의실", "회의", "예약"]):
                intent = "meeting"
            else:
                intent = "schedule"  # 기본값
        
        print(f"✅ Schedule 의도 결정: {intent}")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "schedule")
        return intent
    
    async def handle_leave(self, state: OrchestratorState) -> Dict[str, Any]:
        """연차/반차 관리 (AI-003, AI-004)."""
        print("--- [Schedule Master] Handling Leave ---")
        query = state.get("query", "")
        employee_id = state.get("employee_id")
        
        # 간단한 쿼리 파싱 (키워드 기반)
        query_lower = query.lower()
        
        # "오늘 연차인 사람", "연차인 사람" 등의 패턴 감지
        if any(k in query for k in ["연차인", "연차인 사람", "연차 사용", "연차 신청한", "연차 내역"]):
            # 연차 내역 조회 (특정 날짜의 연차 사용자 조회는 schedule_langgraph 사용)
            from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
            result = run_schedule_agent(query)
            return {
                "final_response": result.get("message", "연차 정보를 조회할 수 없습니다."),
                "messages": [result.get("message", "")],
            }
        
        # employee_id가 필요한 작업들
        if not employee_id:
            # employee_id가 없어도 조회 가능한 경우는 schedule_langgraph 사용
            if any(k in query for k in ["조회", "알려줘", "보여줘", "확인"]):
                from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
                result = run_schedule_agent(query)
                return {
                    "final_response": result.get("message", "연차 정보를 조회할 수 없습니다."),
                    "messages": [result.get("message", "")],
                }
            return {
                "final_response": "연차 관리를 위해서는 사번(employee_id)이 필요합니다.",
                "messages": ["employee_id is required"],
            }
        
        # 키워드 기반 action 결정
        if any(k in query for k in ["잔여", "남은", "남았"]):
            action = "조회"
        elif any(k in query for k in ["신청", "등록", "추가"]):
            action = "신청"
        elif any(k in query for k in ["내역", "목록", "리스트", "조회"]):
            action = "내역"
        elif any(k in query for k in ["추천"]):
            action = "추천"
        else:
            # 기본값: 조회
            action = "조회"
        
        # leave_type 결정
        leave_type = "연차"
        if "반차" in query:
            leave_type = "반차"
        elif "병가" in query:
            leave_type = "병가"
        elif "경조사" in query:
            leave_type = "경조사"
        
        # LeaveQuery 생성 및 처리
        if action == "조회":
            q = LeaveQuery(
                employee_id=employee_id,
                action="조회",
                leave_type=leave_type
            )
            result = handle_leave(q)
        elif action == "신청":
            # 신청의 경우 날짜 정보가 필요하므로 schedule_langgraph 사용
            from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
            result = run_schedule_agent(query)
        elif action == "내역":
            q = LeaveQuery(
                employee_id=employee_id,
                action="내역",
                leave_type=leave_type
            )
            result = handle_leave(q)
        elif action == "추천":
            q = LeaveQuery(
                employee_id=employee_id,
                action="추천"
            )
            result = handle_leave(q)
        else:
            # 기본적으로 schedule_langgraph 사용
            from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
            result = run_schedule_agent(query)
        
        return {
            "final_response": result.get("message", "연차 처리가 완료되었습니다."),
            "messages": [result.get("message", "")],
        }
    
    async def handle_schedule(self, state: OrchestratorState) -> Dict[str, Any]:
        """일정 관리 (AI-005, AI-006)."""
        print("--- [Schedule Master] Handling Schedule ---")
        query = state.get("query", "")
        
        # schedule_langgraph 사용
        from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
        result = run_schedule_agent(query)
        
        return {
            "final_response": result.get("message", "일정 처리가 완료되었습니다."),
            "messages": [result.get("message", "")],
        }
    
    async def handle_meeting(self, state: OrchestratorState) -> Dict[str, Any]:
        """회의실 예약 및 회의 관리 (AI-007~AI-010)."""
        print("--- [Schedule Master] Handling Meeting ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        try:
            # meeting_room_agent 사용
            from agents.master_graphs.schedule.meeting_agent import meeting_room_query
            
            # context 구성
            context = {}
            if session_id:
                context["session_id"] = session_id
            
            # 동기 함수를 async에서 호출
            import asyncio
            result = await asyncio.to_thread(meeting_room_query, query, context)
            
            if result.get("ok"):
                message = result.get("message", "회의 처리가 완료되었습니다.")
                # 추천된 회의실이 있으면 추가 정보 포함
                recommended_rooms = result.get("recommended_rooms", [])
                if recommended_rooms:
                    room_list = "\n".join([f"- {room.get('name', '알 수 없음')}" for room in recommended_rooms])
                    message = f"{message}\n\n추천된 회의실:\n{room_list}"
                
                return {
                    "final_response": message,
                    "messages": [message],
                }
            else:
                error_msg = result.get("error", "회의실 처리를 완료하지 못했습니다.")
                return {
                    "final_response": f"❌ {error_msg}",
                    "messages": [error_msg],
                }
        except Exception as e:
            import traceback
            error_msg = f"회의실 처리 중 오류가 발생했습니다: {str(e)}"
            print(f"❌ 회의실 처리 오류: {traceback.format_exc()}")
            return {
                "final_response": f"❌ {error_msg}",
                "messages": [error_msg],
            }


def get_schedule_master_graph() -> StateGraph:
    """스케줄 마스터그래프 인스턴스 반환."""
    master = ScheduleMasterGraph()
    return master.get_graph()

