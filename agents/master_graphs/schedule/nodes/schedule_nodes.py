"""일정 관리 노드 - HybridScheduler + LLM 파싱."""

from __future__ import annotations

from typing import Any, Dict, Optional, Literal
from dataclasses import dataclass
from datetime import datetime, timedelta

from agents.state import OrchestratorState
from agents.master_graphs.schedule.services.hybrid_scheduler import HybridScheduler
from agents.master_graphs.schedule.utils.date_parser import parse_korean_date_with_llm
from agents.master_graphs.schedule.utils.entity_parser import extract_schedule_entities
from agents.master_graphs.schedule.services.google_calendar_service import GoogleAuthRequired  # [OAuth] 인증 유도 예외
# HybridScheduler 싱글톤 인스턴스
_scheduler = None


def _get_scheduler() -> HybridScheduler:
    """HybridScheduler 싱글톤 인스턴스 반환."""
    global _scheduler
    if _scheduler is None:
        _scheduler = HybridScheduler()
    return _scheduler


@dataclass
class ParsedScheduleIntent:
    """파싱된 일정 의도 (leave_nodes와 동일 패턴)."""
    action: Literal["조회", "생성", "삭제", "수정"]
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    user_id: Optional[str] = None


async def process_schedule_request(state: OrchestratorState) -> Dict[str, Any]:
    """일정 요청 처리.
    
    Args:
        state: 오케스트레이터 상태
        
    Returns:
        처리 결과 딕셔너리
    """
    query = state.get("query", "")
    user_id = state.get("user_id") or state.get("employee_id")
    
    # Redis 기반 컨텍스트 초기화
    if state.get("schedule_context") is None:
        state["schedule_context"] = {}
    if state["schedule_context"].get("schedule") is None:
        state["schedule_context"]["schedule"] = {}
    
    schedule_context = state["schedule_context"]["schedule"]
    
    print(f"[Schedule Node] 쿼리: {query}, user_id: {user_id}")
    
    # 1. 액션 결정 (키워드 기반)
    action = _determine_action(query)
    
    # 2. 특수 케이스: "오늘 일정" 같은 전체 조회 (user_id 불필요)
    if action == "조회" and not user_id:
        schedule_context["last_query"] = query
        schedule_context["last_action"] = action
        message = "사용자 인증이 되지 않아 개인 일정을 조회할 수 없습니다. 로그인 후 다시 시도해주세요."
        return {
            "final_response": message,
            "messages": [message],
            "schedule_context": state["schedule_context"],
        }
    
    # 3. LLM 기반 파싱
    parsed = await _parse_schedule_intent(query, user_id, action)
    
    # 4. user_id 검증 (생성/삭제/수정은 필수)
    if not parsed.user_id and action in ["생성", "삭제", "수정"]:
        return {
            "final_response": "일정 관리를 위해서는 사용자 인증(user_id)이 필요합니다.",
            "messages": ["일정 관리를 위해서는 사용자 인증이 필요합니다."],
            "schedule_context": state["schedule_context"],
        }
    
    # 5. 액션별 처리
    if parsed.action == "조회":
        result = await _query_schedule(parsed, state)
    elif parsed.action == "생성":
        result = await _create_schedule(parsed, state)
    elif parsed.action == "삭제":
        result = await _delete_schedule(parsed, state)
    elif parsed.action == "수정":
        result = await _update_schedule(parsed, state)
    else:
        # fallback
        result = await _query_schedule(parsed, state)
    
    # 6. Redis 컨텍스트 업데이트
    schedule_context["last_query"] = query
    schedule_context["last_action"] = parsed.action

    state["last_domain"] = "schedule"
    state["domain"] = "schedule"
    
    return {
        "final_response": result.get("message", "일정 처리가 완료되었습니다."),
        "messages": [result.get("message", "")],
        "schedule_context": state["schedule_context"],
    }


async def _parse_schedule_intent(
    query: str,
    user_id: Optional[str],
    action: str
) -> ParsedScheduleIntent:
    """쿼리에서 일정 의도를 파싱.
    
    Args:
        query: 사용자 쿼리
        user_id: 사용자 ID
        action: 결정된 액션
        
    Returns:
        파싱된 의도
    """
    # 1. 날짜 파싱 (LLM)
    start_date, end_date = await parse_korean_date_with_llm(query)
    
    # 2. 엔티티 추출 (LLM)
    if action == "생성":
        entities = await extract_schedule_entities(query)
    else:
        entities = {}
    
    return ParsedScheduleIntent(
        action=action,
        title=entities.get("title"),
        description=entities.get("description"),
        location=entities.get("location"),
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
    )


async def _query_schedule(
    parsed: ParsedScheduleIntent,
    state: OrchestratorState
) -> Dict[str, Any]:
    """일정 조회 (Google Calendar + DB 하이브리드).
    
    Args:
        parsed: 파싱된 일정 정보
        state: 상태
        
    Returns:
        조회 결과
    """
    scheduler = _get_scheduler()
    schedule_context = state["schedule_context"]["schedule"]
    
    try:
        # 1. 날짜 범위 기본값 설정
        start_date = parsed.start_date
        end_date = parsed.end_date
        
        if not start_date:
            start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=7)
        
        # 2. Google Calendar + DB에서 일정 조회
        if parsed.user_id:
            # HybridScheduler 사용 (Google Calendar Primary)
            try:
                events = await scheduler.get_events(
                    user_id=str(parsed.user_id),
                    start_date=start_date.isoformat() + "Z",
                    end_date=end_date.isoformat() + "Z"
                )
            except GoogleAuthRequired as auth_error:
                message = (
                    "🔐 Google 캘린더 연동이 필요합니다.\n"
                    f"아래 링크에서 계정을 연결한 뒤 다시 시도해주세요:\n{auth_error.auth_url}"
                )
                return {
                    "ok": False,
                    "message": message,
                    "events": [],
                }
            
            # 컨텍스트 저장
            schedule_context["last_results"] = events
            schedule_context["last_filters"] = {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }
            
            # 응답 메시지 생성
            if not events:
                message = f"{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')} 기간에 일정이 없습니다."
            else:
                message = f"📅 {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')} 일정 ({len(events)}건):\n\n"
                
                for idx, event in enumerate(events[:10], 1):
                    title = event.get('title', '제목 없음')
                    start = event.get('start_time', '')
                    location = event.get('location', '')
                    room = event.get('meeting_room_name', '')
                    
                    message += f"{idx}. {title}\n"
                    message += f"   📍 시간: {start}\n"
                    if location:
                        message += f"   🏢 장소: {location}\n"
                    if room:
                        message += f"   🚪 회의실: {room}\n"
                    message += "\n"
                
                if len(events) > 10:
                    message += f"... 외 {len(events) - 10}건"
            
            return {"ok": True, "message": message, "events": events}
        
        else:
            message = "사용자 인증이 되지 않아 개인 일정을 조회할 수 없습니다. 로그인 후 다시 시도해주세요."
            return {
                "ok": False,
                "message": message,
                "events": [],
            }
    
    except Exception as e:
        print(f"❌ 일정 조회 실패: {e}")
        
        return {
            "ok": False,
            "message": f"일정 조회 중 오류가 발생했습니다: {str(e)}",
        }


async def _create_schedule(
    parsed: ParsedScheduleIntent,
    state: OrchestratorState
) -> Dict[str, Any]:
    """일정 생성 (LLM 파싱 적용).
    
    Args:
        parsed: 파싱된 일정 정보
        state: 상태
        
    Returns:
        생성 결과
    """
    scheduler = _get_scheduler()
    
    # 날짜 기본값 설정
    start_date = parsed.start_date
    end_date = parsed.end_date
    
    if not start_date:
        start_date = (datetime.now() + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        end_date = start_date.replace(hour=15, minute=0)
    
    # 이벤트 데이터 준비
    event_data = {
        'title': parsed.title or '새 일정',
        'description': parsed.description or '',
        'start_time': start_date.isoformat(),
        'end_time': end_date.isoformat(),
        'location': parsed.location or '',
        'attendees': [],
    }
    
    try:
        try:
            created_event_response = await scheduler.create_event(
                user_id=str(parsed.user_id),
                event_data=event_data
            )
        except GoogleAuthRequired as auth_error:
            message = (
                "🔐 Google 캘린더 연동이 필요합니다.\n"
                f"아래 링크에서 계정을 연결한 뒤 다시 시도해주세요:\n{auth_error.auth_url}"
            )
            return {"ok": False, "message": message}
        gcal_event = created_event_response
        if isinstance(created_event_response, dict):
            gcal_event = created_event_response.get("gcal_event")
        
        if gcal_event:
            return {
                "ok": True,
                "message": f"✅ 일정이 생성되었습니다!\n\n📌 제목: {event_data['title']}\n📍 시간: {start_date.strftime('%Y-%m-%d %H:%M')}"
            }
        else:
            return {"ok": False, "message": "일정 생성에 실패했습니다."}
    
    except Exception as e:
        print(f"❌ 일정 생성 실패: {e}")
        return {"ok": False, "message": f"일정 생성 중 오류: {str(e)}"}


async def _delete_schedule(
    parsed: ParsedScheduleIntent,
    state: OrchestratorState
) -> Dict[str, Any]:
    """일정 삭제.
    
    Args:
        parsed: 파싱된 일정 정보
        state: 상태
        
    Returns:
        삭제 결과
    """
    # TODO: 이전 조회 결과에서 이벤트 선택 후 삭제
    # schedule_context["last_results"]에서 번호로 선택
    
    return {
        "ok": False,
        "message": "일정 삭제 기능은 아직 구현 중입니다.\n먼저 '일정 조회'로 확인한 후 번호를 알려주세요."
    }


async def _update_schedule(
    parsed: ParsedScheduleIntent,
    state: OrchestratorState
) -> Dict[str, Any]:
    """일정 수정.
    
    Args:
        parsed: 파싱된 일정 정보
        state: 상태
        
    Returns:
        수정 결과
    """
    # TODO: 이전 조회 결과에서 이벤트 선택 후 수정
    
    return {
        "ok": False,
        "message": "일정 수정 기능은 아직 구현 중입니다.\n먼저 '일정 조회'로 확인한 후 번호를 알려주세요."
    }


def _determine_action(query: str) -> str:
    """간단한 키워드 기반 액션 결정 (한글 통일).
    
    Args:
        query: 사용자 쿼리
        
    Returns:
        액션 ("조회", "생성", "삭제", "수정")
    """
    # 생성
    if any(k in query for k in ["등록", "생성", "추가", "만들", "예약", "넣어"]):
        return "생성"
    # 삭제
    elif any(k in query for k in ["삭제", "취소", "지워", "제거"]):
        return "삭제"
    # 수정
    elif any(k in query for k in ["수정", "변경", "바꿔", "고쳐"]):
        return "수정"
    # 조회 (기본값)
    else:
        return "조회"