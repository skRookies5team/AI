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
from agents.structured_output import extract_structured_output, ScheduleParams


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
        # LLM 결과를 키워드로 재검증 (특히 meeting은 명확해야 함)
        query_lower = query.lower()
        
        # 회의실 관련 키워드가 있으면 무조건 meeting으로 분류 (우선순위 높음)
        meeting_keywords = ["회의실", "회의", "사용가능한", "사용 가능한", "빈 회의실", "회의실 추천", "회의실 찾", "회의실 예약"]
        if any(kw in query for kw in meeting_keywords):
            intent = "meeting"
        elif intent not in ["leave", "meeting", "schedule"]:
            # 키워드 기반 fallback
            if any(k in query for k in ["연차", "반차", "병가", "경조사", "휴가", "신청", "잔여"]):
                intent = "leave"
            else:
                intent = "schedule"  # 기본값
        elif intent == "schedule":
            # schedule로 분류되었지만 회의실 키워드가 있으면 meeting으로 재분류
            if any(kw in query for kw in meeting_keywords):
                intent = "meeting"
        
        print(f"✅ Schedule 의도 결정: {intent}")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "schedule")
        return intent
    
    async def handle_leave(self, state: OrchestratorState) -> Dict[str, Any]:
        """연차/반차 관리 (AI-003, AI-004).
        
        Redis 기반 숏텀메모리를 사용하여 이전 대화 컨텍스트를 유지하고,
        Structured Output Parser를 사용하여 파라미터를 추출합니다.
        """
        print("--- [Schedule Master] Handling Leave ---")
        query = state.get("query", "")
        employee_id = state.get("employee_id")
        orchestrator_state = state  # orchestrator_state는 state와 동일
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("schedule_context") is None:
            orchestrator_state["schedule_context"] = {}
        if orchestrator_state["schedule_context"].get("leave") is None:
            orchestrator_state["schedule_context"]["leave"] = {}
        
        leave_context = orchestrator_state["schedule_context"]["leave"]
        conversation_history = state.get("conversation_history", [])
        
        # 이전 대화 컨텍스트 확인
        previous_action = leave_context.get("last_action")
        pending_approval = leave_context.get("pending_approval")
        
        # 승인 대기 중인 경우 사용자 응답 처리
        if pending_approval and any(k in query.lower() for k in ["승인", "확인", "네", "예", "yes", "ok", "취소", "아니오", "no"]):
            if any(k in query.lower() for k in ["승인", "확인", "네", "예", "yes", "ok"]):
                # 승인 처리
                from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
                result = run_schedule_agent(pending_approval.get("original_query", query))
                orchestrator_state["schedule_context"]["leave"]["pending_approval"] = None
                orchestrator_state["schedule_context"]["leave"]["last_action"] = "신청"
                return {
                    "final_response": f"✅ 연차 신청이 완료되었습니다.\n\n{result.get('message', '')}",
                    "messages": [result.get("message", "")],
                    "schedule_context": orchestrator_state["schedule_context"],
                }
            else:
                # 취소 처리
                orchestrator_state["schedule_context"]["leave"]["pending_approval"] = None
                return {
                    "final_response": "연차 신청이 취소되었습니다.",
                    "messages": ["연차 신청이 취소되었습니다."],
                    "schedule_context": orchestrator_state["schedule_context"],
                }
        
        # Structured Output Parser를 사용하여 파라미터 추출
        try:
            # 대화 히스토리 컨텍스트 구성
            context = ""
            if conversation_history:
                recent_history = conversation_history[-3:]
                context = "\n\n이전 대화 내용:\n"
                for msg in recent_history:
                    context += f"{msg.get('role', 'unknown')}: {msg.get('content', '')}\n"
            
            system_prompt = f"""사용자의 연차 관련 요청을 분석하여 다음 정보를 추출하세요:
- action: 요청 액션 (조회, 신청, 내역, 추천)
- leave_type: 휴가 타입 (연차, 반차, 병가, 경조사)
- start_date: 시작 날짜 (YYYY-MM-DD 형식)
- end_date: 종료 날짜 (YYYY-MM-DD 형식)
- total_days: 총 일수 (소수점 가능, 반차는 0.5)
- reason: 사유 (신청인 경우)

{context}"""
            
            params = await extract_structured_output(
                query=query,
                model_class=ScheduleParams,
                system_prompt=system_prompt
            )
            
            print(f"✅ 구조화된 파라미터 추출 완료:")
            print(f"   - 액션: {getattr(params, 'action', 'N/A')}")
            print(f"   - 휴가 타입: {params.leave_type}")
            print(f"   - 시작 날짜: {params.start_date}")
            print(f"   - 종료 날짜: {params.end_date}")
            
            # employee_id 우선순위: params > state > None
            final_employee_id = params.employee_id or employee_id
            
            # action 결정 (params에 action이 없으면 키워드 기반)
            if hasattr(params, 'action') and params.action:
                action = params.action
            elif any(k in query for k in ["잔여", "남은", "남았"]):
                action = "조회"
            elif any(k in query for k in ["신청", "등록", "추가"]):
                action = "신청"
            elif any(k in query for k in ["내역", "목록", "리스트"]):
                action = "내역"
            elif any(k in query for k in ["추천"]):
                action = "추천"
            else:
                action = "조회"
            
            # leave_type 결정
            leave_type = params.leave_type or "연차"
            if not leave_type:
                if "반차" in query:
                    leave_type = "반차"
                elif "병가" in query:
                    leave_type = "병가"
                elif "경조사" in query:
                    leave_type = "경조사"
                else:
                    leave_type = "연차"
            
        except Exception as e:
            print(f"⚠️ Structured Output 파싱 실패: {e}, 키워드 기반 파싱 사용")
            # Fallback: 키워드 기반 파싱
            final_employee_id = employee_id
            if any(k in query for k in ["잔여", "남은", "남았"]):
                action = "조회"
            elif any(k in query for k in ["신청", "등록", "추가"]):
                action = "신청"
            elif any(k in query for k in ["내역", "목록", "리스트"]):
                action = "내역"
            elif any(k in query for k in ["추천"]):
                action = "추천"
            else:
                action = "조회"
            
            leave_type = "연차"
            if "반차" in query:
                leave_type = "반차"
            elif "병가" in query:
                leave_type = "병가"
            elif "경조사" in query:
                leave_type = "경조사"
            
            params = None
        
        # "오늘 연차인 사람", "연차인 사람" 등의 패턴 감지
        if any(k in query for k in ["연차인", "연차인 사람", "연차 사용", "연차 신청한", "연차 내역"]):
            from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
            result = run_schedule_agent(query)
            orchestrator_state["schedule_context"]["leave"]["last_query"] = query
            orchestrator_state["schedule_context"]["leave"]["last_action"] = "조회"
            return {
                "final_response": result.get("message", "연차 정보를 조회할 수 없습니다."),
                "messages": [result.get("message", "")],
                "schedule_context": orchestrator_state["schedule_context"],
            }
        
        # employee_id가 필요한 작업들
        if not final_employee_id:
            if any(k in query for k in ["조회", "알려줘", "보여줘", "확인"]):
                from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
                result = run_schedule_agent(query)
                orchestrator_state["schedule_context"]["leave"]["last_query"] = query
                orchestrator_state["schedule_context"]["leave"]["last_action"] = "조회"
                return {
                    "final_response": result.get("message", "연차 정보를 조회할 수 없습니다."),
                    "messages": [result.get("message", "")],
                    "schedule_context": orchestrator_state["schedule_context"],
                }
            return {
                "final_response": "연차 관리를 위해서는 사번(employee_id)이 필요합니다.",
                "messages": ["employee_id is required"],
                "schedule_context": orchestrator_state["schedule_context"],
            }
        
        # LeaveQuery 생성 및 처리
        if action == "조회":
            q = LeaveQuery(
                employee_id=final_employee_id,
                action="조회",
                leave_type=leave_type
            )
            result = handle_leave(q)
            orchestrator_state["schedule_context"]["leave"]["last_query"] = query
            orchestrator_state["schedule_context"]["leave"]["last_action"] = "조회"
            orchestrator_state["schedule_context"]["leave"]["last_balance"] = result.get("balance")
        elif action == "신청":
            # 신청의 경우 Human-in-the-loop: 승인 요청
            if params and params.start_date and params.end_date:
                # 날짜 정보가 있으면 승인 요청
                total_days = params.total_days
                if not total_days:
                    from datetime import datetime
                    try:
                        start = datetime.strptime(params.start_date, "%Y-%m-%d")
                        end = datetime.strptime(params.end_date, "%Y-%m-%d")
                        total_days = (end - start).days + 1
                    except:
                        total_days = 1.0
                
                approval_data = {
                    "original_query": query,
                    "employee_id": final_employee_id,
                    "leave_type": leave_type,
                    "start_date": params.start_date,
                    "end_date": params.end_date,
                    "total_days": total_days,
                    "reason": getattr(params, 'reason', '개인 사정') if params else '개인 사정',
                }
                
                orchestrator_state["schedule_context"]["leave"]["pending_approval"] = approval_data
                orchestrator_state["waiting_for_approval"] = {
                    "type": "leave_application",
                    "data": approval_data,
                    "message": f"다음 연차 신청을 승인하시겠습니까?\n\n"
                              f"휴가 타입: {leave_type}\n"
                              f"기간: {params.start_date} ~ {params.end_date} ({total_days}일)\n"
                              f"사유: {getattr(params, 'reason', '개인 사정') if params else '개인 사정'}\n\n"
                              f"승인하시려면 '승인', '확인', '네'라고 말씀해주세요.\n"
                              f"취소하시려면 '취소', '아니오'라고 말씀해주세요."
                }
                
                return {
                    "final_response": orchestrator_state["waiting_for_approval"]["message"],
                    "messages": [orchestrator_state["waiting_for_approval"]["message"]],
                    "schedule_context": orchestrator_state["schedule_context"],
                    "waiting_for_approval": orchestrator_state["waiting_for_approval"],
                }
            else:
                # 날짜 정보가 없으면 schedule_langgraph 사용 (LLM이 날짜 추출)
                from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
                result = run_schedule_agent(query)
                orchestrator_state["schedule_context"]["leave"]["last_query"] = query
                orchestrator_state["schedule_context"]["leave"]["last_action"] = "신청"
        elif action == "내역":
            q = LeaveQuery(
                employee_id=final_employee_id,
                action="내역",
                leave_type=leave_type
            )
            result = handle_leave(q)
            orchestrator_state["schedule_context"]["leave"]["last_query"] = query
            orchestrator_state["schedule_context"]["leave"]["last_action"] = "내역"
        elif action == "추천":
            q = LeaveQuery(
                employee_id=final_employee_id,
                action="추천"
            )
            result = handle_leave(q)
            orchestrator_state["schedule_context"]["leave"]["last_query"] = query
            orchestrator_state["schedule_context"]["leave"]["last_action"] = "추천"
        else:
            from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
            result = run_schedule_agent(query)
            orchestrator_state["schedule_context"]["leave"]["last_query"] = query
        
        return {
            "final_response": result.get("message", "연차 처리가 완료되었습니다."),
            "messages": [result.get("message", "")],
            "schedule_context": orchestrator_state["schedule_context"],
        }
    
    async def handle_schedule(self, state: OrchestratorState) -> Dict[str, Any]:
        """일정 관리 (AI-005, AI-006).
        
        Redis 기반 숏텀메모리를 사용하여 이전 대화 컨텍스트를 유지합니다.
        """
        print("--- [Schedule Master] Handling Schedule ---")
        query = state.get("query", "")
        orchestrator_state = state
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("schedule_context") is None:
            orchestrator_state["schedule_context"] = {}
        if orchestrator_state["schedule_context"].get("schedule") is None:
            orchestrator_state["schedule_context"]["schedule"] = {}
        
        schedule_context = orchestrator_state["schedule_context"]["schedule"]
        
        # schedule_langgraph 사용
        from agents.master_graphs.schedule.schedule_langgraph import run_schedule_agent
        result = run_schedule_agent(query)
        
        # Redis 컨텍스트에 저장
        schedule_context["last_query"] = query
        schedule_context["last_results"] = result.get("rows", [])
        
        return {
            "final_response": result.get("message", "일정 처리가 완료되었습니다."),
            "messages": [result.get("message", "")],
            "schedule_context": orchestrator_state["schedule_context"],
        }
    
    async def handle_meeting(self, state: OrchestratorState) -> Dict[str, Any]:
        """회의실 예약 및 회의 관리 (AI-007~AI-010).
        
        Redis 기반 숏텀메모리를 사용하여 이전 대화 컨텍스트를 유지하고,
        Human-in-the-loop를 통해 회의실 예약 시 승인을 받습니다.
        """
        print("--- [Schedule Master] Handling Meeting ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        orchestrator_state = state
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("schedule_context") is None:
            orchestrator_state["schedule_context"] = {}
        if orchestrator_state["schedule_context"].get("meeting") is None:
            orchestrator_state["schedule_context"]["meeting"] = {}
        
        meeting_context = orchestrator_state["schedule_context"]["meeting"]
        
        # 승인 대기 중인 경우 사용자 응답 처리
        pending_reservation = meeting_context.get("pending_reservation")
        if pending_reservation and any(k in query.lower() for k in ["승인", "확인", "네", "예", "yes", "ok", "취소", "아니오", "no"]):
            if any(k in query.lower() for k in ["승인", "확인", "네", "예", "yes", "ok"]):
                # 승인 처리: 실제 예약 진행
                from agents.master_graphs.schedule.meeting_agent import meeting_room_query
                import asyncio
                context = {"session_id": session_id} if session_id else {}
                result = await asyncio.to_thread(meeting_room_query, pending_reservation.get("original_query", query), context)
                meeting_context["pending_reservation"] = None
                orchestrator_state["waiting_for_approval"] = None
                return {
                    "final_response": f"✅ 회의실 예약이 완료되었습니다.\n\n{result.get('message', '')}",
                    "messages": [result.get("message", "")],
                    "schedule_context": orchestrator_state["schedule_context"],
                }
            else:
                # 취소 처리
                meeting_context["pending_reservation"] = None
                orchestrator_state["waiting_for_approval"] = None
                return {
                    "final_response": "회의실 예약이 취소되었습니다.",
                    "messages": ["회의실 예약이 취소되었습니다."],
                    "schedule_context": orchestrator_state["schedule_context"],
                }
        
        try:
            # meeting_room_agent 사용
            from agents.master_graphs.schedule.meeting_agent import meeting_room_query
            
            # context 구성 (날짜/시간 정보 포함)
            context = {}
            if session_id:
                context["session_id"] = session_id
            
            # orchestrator_state에서 날짜/시간 정보 가져오기
            if orchestrator_state:
                context["current_date"] = orchestrator_state.get("current_date")
                context["current_time"] = orchestrator_state.get("current_time")
                context["current_datetime"] = orchestrator_state.get("current_datetime")
                context["current_weekday"] = orchestrator_state.get("current_weekday")
            
            # 동기 함수를 async에서 호출
            import asyncio
            result = await asyncio.to_thread(meeting_room_query, query, context)
            
            if result.get("ok"):
                message = result.get("message", "회의 처리가 완료되었습니다.")
                # 추천된 회의실이 있으면 추가 정보 포함
                recommended_rooms = result.get("recommended_rooms", [])
                
                # Redis 컨텍스트에 저장
                meeting_context["last_query"] = query
                if recommended_rooms:
                    meeting_context["recommended_rooms"] = recommended_rooms
                    room_list = "\n".join([f"- {room.get('name', '알 수 없음')}" for room in recommended_rooms])
                    message = f"{message}\n\n추천된 회의실:\n{room_list}"
                    
                    # 예약 요청이 있는 경우 Human-in-the-loop: 승인 요청
                    if any(k in query.lower() for k in ["예약", "예약해", "예약해줘", "예약하", "reserve", "book"]):
                        reservation_data = {
                            "original_query": query,
                            "recommended_rooms": recommended_rooms,
                        }
                        meeting_context["pending_reservation"] = reservation_data
                        orchestrator_state["waiting_for_approval"] = {
                            "type": "meeting_reservation",
                            "data": reservation_data,
                            "message": f"다음 회의실을 예약하시겠습니까?\n\n"
                                      f"추천된 회의실:\n{room_list}\n\n"
                                      f"승인하시려면 '승인', '확인', '네'라고 말씀해주세요.\n"
                                      f"취소하시려면 '취소', '아니오'라고 말씀해주세요."
                        }
                        return {
                            "final_response": orchestrator_state["waiting_for_approval"]["message"],
                            "messages": [orchestrator_state["waiting_for_approval"]["message"]],
                            "schedule_context": orchestrator_state["schedule_context"],
                            "waiting_for_approval": orchestrator_state["waiting_for_approval"],
                        }
                
                return {
                    "final_response": message,
                    "messages": [message],
                    "schedule_context": orchestrator_state["schedule_context"],
                }
            else:
                error_msg = result.get("error", "회의실 처리를 완료하지 못했습니다.")
                return {
                    "final_response": f"❌ {error_msg}",
                    "messages": [error_msg],
                    "schedule_context": orchestrator_state["schedule_context"],
                }
        except Exception as e:
            import traceback
            error_msg = f"회의실 처리 중 오류가 발생했습니다: {str(e)}"
            print(f"❌ 회의실 처리 오류: {traceback.format_exc()}")
            return {
                "final_response": f"❌ {error_msg}",
                "messages": [error_msg],
                "schedule_context": orchestrator_state["schedule_context"],
            }


def get_schedule_master_graph() -> StateGraph:
    """스케줄 마스터그래프 인스턴스 반환."""
    master = ScheduleMasterGraph()
    return master.get_graph()

