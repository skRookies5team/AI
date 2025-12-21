"""회의실 관리 노드 - HybridScheduler + LLM 파싱."""

from __future__ import annotations

import re
from dataclasses import dataclass
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

from agents.master_graphs.schedule.services.hybrid_scheduler import HybridScheduler
from agents.master_graphs.schedule.services.google_calendar_service import GoogleAuthRequired  # [OAuth] 인증 안내 예외
from agents.master_graphs.schedule.utils.date_parser import parse_korean_date_with_llm
from agents.master_graphs.schedule.utils.entity_parser import extract_meeting_room_entities
from agents.state import OrchestratorState
from agents.master_graphs.schedule.models.meeting_room_models import MeetingSlotDecision
from agents.master_graphs.schedule.utils.meeting_slot_manager import analyze_meeting_slots
from agents.session_manager import get_session_manager
from agents.master_graphs.schedule.utils.prompt_loader import (
    get_sub_agent_prompt,
    format_prompt_template
)


_scheduler: Optional[HybridScheduler] = None
DEFAULT_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))

RESERVATION_SLOT_ORDER = ["title", "description", "start_time", "end_time", "participants", "room"]
RESERVATION_SLOT_DESCRIPTIONS = {
    "title": "회의 제목",
    "description": "회의 설명",
    "start_time": "시작 일시 (예: 2025-12-13 14:00)",
    "end_time": "종료 일시(또는 지속 시간)",
    "participants": "참석 인원 수",
    "room": "예약할 회의실 (추천 목록 번호 또는 회의실 이름)",
}


def _get_scheduler() -> HybridScheduler:
    """HybridScheduler 싱글톤 인스턴스를 반환한다."""
    global _scheduler
    if _scheduler is None:
        _scheduler = HybridScheduler()
    return _scheduler


@dataclass
class ParsedMeetingIntent:
    """파싱된 회의실 요청 정보."""

    action: Literal["조회", "예약", "취소", "수정"]
    raw_query: str
    user_id: str
    employee_id: Optional[int]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    duration_minutes: Optional[int]
    capacity: Optional[int]
    participants: Optional[int]
    floor: Optional[int]
    room_index: Optional[int]
    selection_index: Optional[int]
    room_code: Optional[str]
    room_name: Optional[str]
    requires_video: Optional[bool]
    requires_projector: Optional[bool]
    requires_whiteboard: Optional[bool]
    title: str
    description: Optional[str]
    attendees: List[str]
    selection_index: Optional[int]


async def process_meeting_room_request(state: OrchestratorState) -> Dict[str, Any]:
    """회의실 관련 요청을 처리한다."""
    query = state.get("query", "")
    employee_id = state.get("employee_id")
    user_id_value = state.get("user_id") or state.get("employee_id")
    user_id = str(user_id_value) if user_id_value is not None else None

    if state.get("schedule_context") is None:
        state["schedule_context"] = {}
    if state["schedule_context"].get("meeting") is None:
        state["schedule_context"]["meeting"] = {}
    meeting_context = state["schedule_context"]["meeting"]

    print(f"[Meeting Room Node] query={query}, user_id={user_id}")

    if not user_id:
        return {
            "final_response": "회의실 기능을 이용하려면 사용자 인증이 필요합니다.",
            "messages": ["회의실 기능을 이용하려면 사용자 인증이 필요합니다."],
            "schedule_context": state["schedule_context"],
        }

    slot_state_ctx = meeting_context.get("slot_state") or {}
    awaiting_slots_ctx = meeting_context.get("awaiting_slots") or []

    # 예약 진행 중일 때는 라우팅 방지 플래그 설정
    if awaiting_slots_ctx:
        state["prevent_routing"] = True
    
    # 추천 후 회의실 선택 조기 감지 및 파싱
    room_selection_detected = False
    recommended_rooms = meeting_context.get("recommended_rooms") or []
    
    print(f"[조기 감지 시작] query='{query}', recommended_rooms 개수={len(recommended_rooms)}")
    
    if recommended_rooms:
        # slot_state 초기화
        if meeting_context.get("slot_state") is None:
            meeting_context["slot_state"] = {"intent": "조회", "slots": {}, "missing_slots": []}
        if meeting_context["slot_state"].get("slots") is None:
            meeting_context["slot_state"]["slots"] = {}
        
        query_normalized = query.strip()
        
        # 패턴 1 (우선): "C동 중회의실 2", "A동 대회의실 1" - 회의실 이름 전체 매칭
        # 이걸 먼저 시도해야 "중회의실 2"의 "2"를 숫자로 오인하지 않음
        room_name_pattern = r"([A-Z가-힣]\s*동\s*[가-힣\s]+\d+)"
        room_name_match = re.search(room_name_pattern, query_normalized)
        if room_name_match:
            room_name_input = room_name_match.group(1).strip()
            # 공백 제거 후 비교
            normalized_input = re.sub(r"\s+", "", room_name_input)
            print(f"[조기 감지] 회의실 이름 패턴 매칭: '{room_name_input}' → '{normalized_input}'")
            for idx, room in enumerate(recommended_rooms, 1):
                room_name = room.get("name") or room.get("room_name") or ""
                normalized_room = re.sub(r"\s+", "", room_name)
                if normalized_input in normalized_room or normalized_room in normalized_input:
                    meeting_context["slot_state"]["slots"]["room_index"] = idx
                    meeting_context["slot_state"]["slots"]["room_name"] = room_name
                    meeting_context["slot_state"]["slots"]["room_code"] = room.get("room_code")
                    room_selection_detected = True
                    print(f"✅ [조기 감지 성공] 회의실 이름 선택: {idx}번 {room_name}")
                    break
        
        # 패턴 2 (후순위): "10번", "10번 회의실", "10" - 순수 숫자만
        # 회의실 이름 패턴이 실패한 경우에만 시도
        if not room_selection_detected:
            # 순수 숫자 패턴: 앞뒤에 한글이 없어야 함 (예: "중회의실 2"는 제외)
            # ^로 시작하거나 공백 후 + 숫자 + 번/번째/회의실 또는 끝
            pure_number_pattern = r"^(\d+)\s*(?:번|번째)?(?:\s*회의실)?$"
            number_match = re.search(pure_number_pattern, query_normalized)
            if number_match:
                index = int(number_match.group(1))
                print(f"[조기 감지] 숫자 패턴 매칭: index={index}, recommended_rooms 개수={len(recommended_rooms)}")
                if 1 <= index <= len(recommended_rooms):
                    room = recommended_rooms[index - 1]
                    meeting_context["slot_state"]["slots"]["room_index"] = index
                    meeting_context["slot_state"]["slots"]["room_name"] = room.get("name") or room.get("room_name")
                    meeting_context["slot_state"]["slots"]["room_code"] = room.get("room_code")
                    room_selection_detected = True
                    print(f"✅ [조기 감지 성공] {index}번 회의실 선택: {room.get('name')}")
                else:
                    print(f"⚠️ [조기 감지 실패] index={index}가 범위 벗어남 (1~{len(recommended_rooms)})")
            else:
                print(f"[조기 감지] 순수 숫자 패턴 매칭 실패: '{query_normalized}'")
    else:
        print(f"[조기 감지 스킵] recommended_rooms 없음")
    
    inferred_action = _determine_meeting_action(query)
    
    # 추천 후 회의실 선택 시 예약으로 전환
    if room_selection_detected:
        action = "예약"
        should_reset_context = False
        meeting_context["recommendation_ready"] = False
        print(f"[Meeting Room] 추천→예약 자동 전환 (회의실 선택 감지)")
    elif awaiting_slots_ctx and slot_state_ctx.get("intent"):
        # 슬롯 채우기 진행 중이면 기존 intent 유지
        action = slot_state_ctx["intent"]
        should_reset_context = False
        print(f"[Meeting Room] 슬롯 진행 중 - intent 유지: {action}")
    else:
        action = inferred_action
        should_reset_context = True
        print(f"[Meeting Room] 새 요청 - inferred_action: {action}")

    scheduler = _get_scheduler()
    if should_reset_context:
        _maybe_reset_meeting_context(meeting_context, action, query)
    slot_state_payload = _build_slot_state_payload(
        current_action=action,
        meeting_context=meeting_context,
    )
    slot_decision = await analyze_meeting_slots(
        query=query,
        slot_state=slot_state_payload,
    )
    
    # Slot Manager가 회의실을 채웠는지 확인
    slot_filled_room = False
    if slot_decision and slot_decision.filled_slots:
        filled = slot_decision.filled_slots
        if filled.get("room_name") or filled.get("room_code") or filled.get("room_index"):
            slot_filled_room = True
            print(f"[Slot Manager가 회의실 채움] room_name={filled.get('room_name')}, room_code={filled.get('room_code')}, room_index={filled.get('room_index')}")
    
    # normalized_action 결정: 조기 감지나 추천 컨텍스트에서 예약으로 전환된 경우 slot_decision 무시
    if room_selection_detected:
        normalized_action = "예약"
        print(f"[Meeting Room] normalized_action 강제 설정: 예약 (회의실 선택 감지)")
    elif slot_filled_room and meeting_context.get("recommended_rooms"):
        # Slot Manager가 회의실을 채웠고 추천 목록이 있으면 예약으로 전환
        normalized_action = "예약"
        print(f"[Meeting Room] normalized_action 강제 설정: 예약 (Slot Manager가 회의실 채움)")
    elif meeting_context.get("recommended_rooms") and action == "예약":
        normalized_action = "예약"
        print(f"[Meeting Room] normalized_action 강제 설정: 예약 (추천 컨텍스트)")
    else:
        normalized_action = _map_decision_action(slot_decision.action, action) if slot_decision else action
    
    print(f"[Meeting Room] action={action}, normalized_action={normalized_action}")
    
    parsed = await _parse_meeting_intent(
        query=query,
        user_id=user_id,
        employee_id=employee_id,
        action=normalized_action,
        context=meeting_context,
        awaiting_slots=awaiting_slots_ctx,
        slot_decision=slot_decision,
        slot_state_payload=slot_state_payload,
    )
    
    print(f"[Meeting Room] parsed.action={parsed.action}")
    followup_question = _apply_slot_decision(
        parsed=parsed,
        slot_decision=slot_decision,
        meeting_context=meeting_context,
        slot_state_payload=slot_state_payload,
        raw_query=query,
    )
    if parsed.action == "예약":
        initial_missing = _collect_reservation_missing(parsed, meeting_context)
        slot_state_ctx = meeting_context.get("slot_state") or {}
        slot_values = slot_state_ctx.get("slots") or {}
        previous_awaiting = slot_state_payload.get("awaiting_slots") or []
        previous_slots = slot_state_payload.get("slots") or {}
        
        # 추천에서 넘어온 경우도 기존 슬롯 진행으로 간주
        from_recommendation = bool(meeting_context.get("recommended_rooms"))
        
        has_existing_slot_progress = bool(
            previous_awaiting
            or from_recommendation
            or any(
                value not in (None, "", [], {})
                for value in previous_slots.values()
            )
            or any(
                value not in (None, "", [], {})
                for value in slot_values.values()
            )
        )
        if initial_missing and not has_existing_slot_progress:
            initial_prompt = _build_initial_reservation_prompt(initial_missing)
            slot_state_ctx.setdefault("intent", parsed.action)
            slot_state_ctx.setdefault("slots", {})
            slot_state_ctx["missing_slots"] = initial_missing
            meeting_context["slot_state"] = slot_state_ctx
            meeting_context["awaiting_slots"] = initial_missing
            meeting_context["last_question"] = initial_prompt
            meeting_context["last_query"] = query
            meeting_context["last_action"] = parsed.action
            state["last_domain"] = "schedule"
            state["domain"] = "schedule"
            return {
                "final_response": initial_prompt,
                "messages": [initial_prompt],
                "schedule_context": state["schedule_context"],
            }
    # 조회(추천)는 필수 정보가 아니면 followup 생성하지 않음
    if parsed.action == "조회":
        followup_question = None
        meeting_context.pop("awaiting_slots", None)
        meeting_context.pop("last_question", None)

    if followup_question:
        meeting_context["last_query"] = query
        meeting_context["last_action"] = parsed.action
        meeting_context["last_question"] = followup_question
        state["last_domain"] = "schedule"
        state["domain"] = "schedule"
        return {
            "final_response": followup_question,
            "messages": [followup_question],
            "schedule_context": state["schedule_context"],
        }

    if parsed.action == "조회":
        result = await _handle_query(parsed, scheduler, meeting_context)
    elif parsed.action == "예약":
        result = await _handle_reservation(parsed, scheduler, meeting_context, slot_decision)
    elif parsed.action == "취소":
        result = await _handle_cancel(parsed, scheduler, meeting_context)
    else:  # 수정
        result = await _handle_update(parsed, scheduler, meeting_context)

    meeting_context["last_query"] = query
    meeting_context["last_action"] = parsed.action
    meeting_context.pop("last_question", None)
    _persist_slot_state(meeting_context, parsed)
    
    # 예약이 성공하면 추천 컨텍스트 정리
    if parsed.action == "예약" and result.get("ok"):
        meeting_context.pop("recommended_rooms", None)
        meeting_context.pop("recommendation_ready", None)
        meeting_context.pop("query_filters", None)

    message = result.get("message", "회의실 처리가 완료되었습니다.")
    state["last_domain"] = "schedule"
    state["domain"] = "schedule"

    return {
        "final_response": message,
        "messages": [message],
        "schedule_context": state["schedule_context"],
    }


async def _handle_query(
    parsed: ParsedMeetingIntent,
    scheduler: HybridScheduler,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """회의실 조회 처리."""
    # 시작/종료 시간이 있으면 해당 시간대에 빈 회의실만 조회
    if parsed.start_time and parsed.end_time:
        start_local = _ensure_aware(parsed.start_time)
        end_local = _ensure_aware(parsed.end_time)
        rooms = await scheduler.db.find_available_rooms(
            start_time=_to_local_naive(start_local),
            end_time=_to_local_naive(end_local),
            capacity=parsed.capacity or parsed.participants,
            floor=parsed.floor,
            has_video_conf=parsed.requires_video,
        )
        time_range = f"{start_local.strftime('%Y-%m-%d %H:%M')} ~ {end_local.strftime('%H:%M')}"
    else:
        # 시간 정보가 없으면 모든 회의실 조회
        rooms = await scheduler.db.get_meeting_rooms(
            capacity=parsed.capacity or parsed.participants,
            floor=parsed.floor,
            has_video_conf=parsed.requires_video,
        )
        time_range = None

    if not rooms:
        return {
            "ok": True,
            "message": "조건에 맞는 회의실을 찾지 못했습니다. 다른 조건으로 다시 요청해 보세요.",
            "recommended_rooms": [],
        }

    limited_rooms = rooms[:10]
    message = _build_room_list_message(rooms, time_range, parsed)
    context["recommended_rooms"] = limited_rooms
    context["query_filters"] = {
        "capacity": parsed.capacity or parsed.participants,
        "floor": parsed.floor,
        "requires_video": parsed.requires_video,
    }
    context["recommendation_ready"] = True
    
    # 추천 시 입력받은 시간, 인원 정보를 slot_state에 저장 (예약 전환 시 재사용)
    if parsed.start_time or parsed.end_time or parsed.participants:
        slot_state = context.get("slot_state") or {}
        slots = slot_state.get("slots") or {}
        if parsed.start_time:
            slots["start_time"] = _normalize_iso(parsed.start_time)
        if parsed.end_time:
            slots["end_time"] = _normalize_iso(parsed.end_time)
        if parsed.participants:
            slots["participants"] = parsed.participants
        slot_state["slots"] = slots
        context["slot_state"] = slot_state
    
    context.pop("last_selected_room", None)

    return {
        "ok": True,
        "message": message,
        "recommended_rooms": limited_rooms,
    }


async def _handle_reservation(
    parsed: ParsedMeetingIntent,
    scheduler: HybridScheduler,
    context: Dict[str, Any],
    slot_decision: Optional[MeetingSlotDecision] = None,
) -> Dict[str, Any]:
    """회의실 예약 처리."""
    if parsed.end_time is None and parsed.duration_minutes is not None and parsed.start_time is not None:
        parsed.end_time = parsed.start_time + timedelta(minutes=parsed.duration_minutes)

    required_missing = _collect_reservation_missing(parsed, context)
    if required_missing:
        prompt = _build_missing_reservation_prompt(required_missing, slot_decision)
        slot_state = context.get("slot_state") or {}
        slot_state.setdefault("intent", parsed.action)
        slot_state.setdefault("slots", {})
        slot_state["missing_slots"] = required_missing
        context["slot_state"] = slot_state
        context["awaiting_slots"] = required_missing
        context["last_question"] = prompt
        return {"ok": False, "message": prompt}

    room = await _resolve_room_selection(parsed, scheduler, context)
    if not room:
        return {
            "ok": False,
            "message": "예약할 회의실을 찾지 못했습니다. 먼저 '회의실 추천'으로 목록을 확인한 뒤 번호로 지정해주세요.",
        }

    if parsed.start_time is None or parsed.end_time is None:
        return {
            "ok": False,
            "message": "회의 시작/종료 시각을 이해하지 못했습니다. 다시 한번 정확한 시간을 알려주세요.",
        }

    start_local = _ensure_aware(parsed.start_time)
    end_local = _ensure_aware(parsed.end_time)
    now_local = datetime.now(DEFAULT_TIMEZONE)
    if end_local < now_local:
        return {
            "ok": False,
            "message": "이미 지난 시각에는 예약을 생성할 수 없습니다. 미래 시간을 알려주세요.",
        }

    last_reservation = context.get("last_reservation") or {}
    last_room = context.get("last_selected_room") or {}
    last_gcal_event = last_reservation.get("gcal_event") or {}
    last_start_iso = (
        (last_gcal_event.get("start") or {}).get("dateTime")
        or (last_gcal_event.get("start") or {}).get("date")
    )
    last_room_id = last_room.get("id")
    if (
        last_start_iso
        and last_room_id
        and last_room_id == room.get("id")
        and last_start_iso == parsed.start_time.isoformat()
    ):
        start_str = start_local.strftime("%Y-%m-%d %H:%M")
        end_str = end_local.strftime("%H:%M")
        room_name = room.get("name") or room.get("room_code", "회의실")
        return {
            "ok": True,
            "message": (
                f"✅ {room_name} 예약이 이미 완료되어 있습니다.\n"
                f"📅 일시: {start_str} ~ {end_str}\n"
                f"👥 인원: {(parsed.participants or parsed.capacity) or '-'}명"
            ),
            "event": last_reservation,
        }

    conflicts = await scheduler.db.find_room_conflicts(
        meeting_room_id=room["id"],
        start_time=_to_local_naive(start_local),
        end_time=_to_local_naive(end_local),
    )
    if conflicts:
        return {
            "ok": False,
            "message": "해당 시간에는 이미 회의가 예약되어 있습니다. 다른 시간이나 회의실을 요청해주세요.",
        }

    session_manager = await get_session_manager()
    if not session_manager:
        return {
            "ok": False,
            "message": "세션 관리자가 초기화되지 않았습니다. Redis 설정을 확인한 뒤 다시 시도해주세요.",
        }

    tokens = await session_manager.get_google_tokens(parsed.user_id)
    if not tokens or not tokens.get("access_token"):
        base_url = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")
        auth_url = f"{base_url}/auth/google?user_id={parsed.user_id}"
        return {
            "ok": False,
            "message": f"Google Calendar 인증이 필요합니다: {auth_url}",
            "auth_required": True,
            "auth_url": auth_url,
        }

    participant_count = parsed.participants or parsed.capacity
    event_data = {
        "title": parsed.title or f"{room.get('name', '회의실')} 예약",
        "description": parsed.description or parsed.purpose or parsed.raw_query,
        "start_time": start_local.isoformat(),
        "end_time": end_local.isoformat(),
        "meeting_room_id": room["id"],
        "participant_count": participant_count,
        "attendees": parsed.attendees,
        "schedule_type": "meeting_room",
        "employee_id": parsed.employee_id,
    }

    try:
        created_event = await scheduler.create_event(user_id=parsed.user_id, event_data=event_data)
        context["last_selected_room"] = room
        context["last_reservation"] = created_event

        start_str = start_local.strftime("%Y-%m-%d %H:%M")
        end_str = end_local.strftime("%H:%M")
        room_name = room.get("name") or room.get("room_code", "회의실")

        return {
            "ok": True,
            "message": (
                f"✅ {room_name} 예약이 완료되었습니다!\n"
                f"📅 일시: {start_str} ~ {end_str}\n"
                f"👥 인원: {participant_count or '-'}명"
            ),
            "event": created_event,
        }
    except GoogleAuthRequired as e:
        return {
            "ok": False,
            "message": f"Google Calendar 인증이 필요합니다: {e.auth_url}",
            "auth_required": True,
            "auth_url": e.auth_url,
        }
    except Exception as e:
        print(f"[Meeting Room] 예약 실패: {e}")
        return {
            "ok": False,
            "message": f"회의실 예약 중 오류가 발생했습니다: {str(e)}",
        }


async def _handle_cancel(
    parsed: ParsedMeetingIntent,
    scheduler: HybridScheduler,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """회의실 예약 취소 처리."""
    slot_state = context.get("slot_state") or {}
    slots = slot_state.get("slots", {})

    event_queue: List[str] = context.get("cancel_selection_queue") or []
    pending_invalid: Optional[List[int]] = context.pop("cancel_selection_error", None)
    messages: List[str] = []
    room_code = slots.get("room_code") or parsed.room_code
    room_name = slots.get("room_name") or parsed.room_name

    now = datetime.now(DEFAULT_TIMEZONE)
    start_dt = (
        _parse_iso_datetime(slots.get("start_time"))
        or parsed.start_time
        or now - timedelta(days=7)
    )
    end_dt = (
        _parse_iso_datetime(slots.get("end_time"))
        or parsed.end_time
        or now + timedelta(days=30)
    )
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(days=1)

    session_manager = await get_session_manager()
    if not session_manager:
        return {
            "ok": False,
            "message": "세션 관리자가 초기화되지 않았습니다. Redis 설정을 확인한 뒤 다시 시도해주세요.",
        }

    tokens = await session_manager.get_google_tokens(parsed.user_id)
    if not tokens or not tokens.get("access_token"):
        base_url = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")
        auth_url = f"{base_url}/auth/google?user_id={parsed.user_id}"
        return {
            "ok": False,
            "message": f"Google Calendar 인증이 필요합니다. 인증 후 다시 시도해 주세요: {auth_url}",
            "auth_required": True,
            "auth_url": auth_url,
        }

    def resolve_candidate_by_event(event_identifier: Optional[str]) -> Optional[Dict[str, Any]]:
        if not event_identifier:
            return None
        for candidate in context.get("cancel_candidates") or []:
            if candidate.get("event_id") == str(event_identifier):
                return candidate
        return None

    def resolve_candidate_by_index(index: int) -> Optional[Dict[str, Any]]:
        candidates_local = context.get("cancel_candidates") or []
        if 1 <= index <= len(candidates_local):
            return candidates_local[index - 1]
        return None

    while True:
        event_id = slots.get("event_id")
        selection_index = _safe_int(slots.get("selection_index"))

        if event_queue and not event_id:
            next_event_id = event_queue.pop(0)
            slots["event_id"] = next_event_id
            slots.pop("selection_index", None)
            event_id = next_event_id
            context["cancel_selection_queue"] = event_queue

        selected_candidate: Optional[Dict[str, Any]] = None

        if event_id:
            selected_candidate = resolve_candidate_by_event(event_id)

        if selection_index and not event_id:
            selected_candidate = resolve_candidate_by_index(selection_index)
            if selected_candidate:
                event_id = selected_candidate.get("event_id")
                slots["event_id"] = event_id
            else:
                context["awaiting_slots"] = ["cancel_selection"]
                return {
                    "ok": False,
                    "message": "선택한 번호에 해당하는 회의를 찾지 못했습니다. 후보 목록의 번호를 확인해 주세요.",
                }

        if not event_id:
            try:
                gcal_events = await scheduler.get_events(
                    user_id=parsed.user_id,
                    start_date=_to_rfc3339(start_dt),
                    end_date=_to_rfc3339(end_dt),
                )
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[Meeting Room] Google Calendar 조회 실패: {exc}")
                gcal_events = []

            db_reservations = await scheduler.db.get_room_reservations_by_user(
                employee_id=int(parsed.user_id),
                include_past=False,
                limit=50,
            )

            candidates = _collect_cancel_candidates(
                gcal_events=gcal_events,
                db_reservations=db_reservations,
                last_reservation=context.get("last_reservation") or {},
            )

            filtered_candidates = _filter_cancel_candidates(
                candidates=candidates,
                room_name=room_name,
                room_code=room_code,
            )

            if filtered_candidates:
                candidates = filtered_candidates

            if not candidates:
                context["cancel_selection_queue"] = []
                return {
                    "ok": False,
                    "message": "취소 가능한 회의 예약을 찾지 못했습니다. 기간이나 회의 제목을 조금 더 알려주세요.",
                }

            context["cancel_candidates"] = candidates[:10]
            context["awaiting_slots"] = ["cancel_selection"]
            slot_state.setdefault("slots", {})
            slot_state["slots"].pop("event_id", None)
            slot_state["slots"].pop("selection_index", None)
            prompt = _format_cancel_prompt(context["cancel_candidates"])
            if pending_invalid:
                invalid_text = ", ".join(str(idx) for idx in pending_invalid)
                prompt = f"선택한 번호 {invalid_text}는 목록에서 찾지 못했습니다.\n{prompt}"
            return {
                "ok": False,
                "message": prompt,
            }

        if not selected_candidate:
            selected_candidate = resolve_candidate_by_event(event_id)

        try:
            success = await scheduler.delete_event(user_id=parsed.user_id, event_id=str(event_id))
        except GoogleAuthRequired as exc:
            return {
                "ok": False,
                "message": f"Google Calendar 인증이 필요합니다. 인증 후 다시 시도해 주세요: {exc.auth_url}",
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {
                "ok": False,
                "message": f"회의 취소 중 오류가 발생했습니다: {str(exc)}",
            }

        if not success:
            return {
                "ok": False,
                "message": "회의 취소 요청이 실패했습니다. 다시 시도해 주세요.",
            }

        candidates_list = context.get("cancel_candidates") or []
        context["cancel_candidates"] = [
            candidate for candidate in candidates_list if candidate.get("event_id") != str(event_id)
        ]
        slot_state.setdefault("slots", {})
        slot_state["slots"].pop("event_id", None)
        slot_state["slots"].pop("selection_index", None)

        if not selected_candidate:
            selected_candidate = {
                "event_id": event_id,
                "title": slots.get("title") or parsed.title,
                "start_time": slots.get("start_time"),
                "end_time": slots.get("end_time"),
                "meeting_room_name": room_name or room_code,
            }

        messages.append(_format_cancel_success(selected_candidate))

        if event_queue:
            continue

        context.pop("awaiting_slots", None)
        context.pop("cancel_selection_queue", None)
        break

    if pending_invalid:
        invalid_text = ", ".join(str(idx) for idx in pending_invalid)
        messages.append(f"선택한 번호 {invalid_text}는 후보 목록에서 찾지 못해 건너뛰었습니다.")

    if messages:
        return {
            "ok": True,
            "message": "\n\n".join(messages),
        }

    return {
        "ok": False,
        "message": "취소 가능한 회의 예약을 찾지 못했습니다. 기간이나 회의 제목을 조금 더 알려주세요.",
    }


async def _handle_update(
    parsed: ParsedMeetingIntent,
    scheduler: HybridScheduler,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """회의실 예약 수정 처리."""
    return {
        "ok": False,
        "message": "회의 수정 기능은 아직 지원하지 않습니다. 취소 후 다시 예약해 주세요.",
    }


def _determine_meeting_action(query: str) -> Literal["조회", "예약", "취소", "수정"]:
    """사용자 의도를 파악한다."""
    query_lower = query.lower()

    cancel_keywords = ["취소", "삭제", "없애", "지워", "cancel", "delete"]
    if any(kw in query_lower for kw in cancel_keywords):
        return "취소"

    update_keywords = ["변경", "수정", "옮기", "바꿔", "update", "modify", "change"]
    if any(kw in query_lower for kw in update_keywords):
        return "수정"

    reservation_keywords = ["예약", "잡아", "잡고", "빌려", "사용", "reserve", "book"]
    if any(kw in query_lower for kw in reservation_keywords):
        return "예약"

    query_keywords = ["조회", "추천", "찾아", "알려", "보여", "list", "show", "find"]
    if any(kw in query_lower for kw in query_keywords):
        return "조회"

    return "조회"


async def _parse_meeting_intent(
    query: str,
    user_id: str,
    employee_id: Optional[int],
    action: str,
    context: Dict[str, Any],
    awaiting_slots: Optional[List[str]] = None,
    slot_decision: Optional[MeetingSlotDecision] = None,
    slot_state_payload: Optional[Dict[str, Any]] = None,
) -> ParsedMeetingIntent:
    """LLM을 사용해 회의실 관련 의도를 파싱한다."""
    awaiting_list = awaiting_slots or []
    awaiting_set = set(awaiting_list)

    parse_cache: Dict[str, Tuple[Optional[datetime], Optional[datetime]]] = {}

    async def cached_parse(text: Optional[str]) -> Tuple[Optional[datetime], Optional[datetime]]:
        if not text:
            return None, None
        if text not in parse_cache:
            parse_cache[text] = await parse_korean_date_with_llm(text)
        return parse_cache[text]

    entities = await extract_meeting_room_entities(query)
    manual_title = False
    manual_room = False

    # 쉼표로 구분된 입력 처리 (예: "절거운 미팅, 대표님과의 점심 식사")
    if ("title" in awaiting_set or "description" in awaiting_set) and "," in query:
        parts = [part.strip() for part in query.split(",", 1)]
        if len(parts) == 2:
            if "title" in awaiting_set:
                entities["title"] = parts[0]
                print(f"[쉼표 파싱] title: {parts[0]}")
            if "description" in awaiting_set:
                entities["description"] = parts[1]
                print(f"[쉼표 파싱] description: {parts[1]}")
            entities.pop("start_time_str", None)
            entities.pop("end_time_str", None)
            manual_title = True
    elif "title" in awaiting_set and len(awaiting_set) == 1:
        manual_title = True
        stripped_title = query.strip()
        entities["title"] = stripped_title
        entities.pop("start_time_str", None)
        entities.pop("end_time_str", None)

    if "description" in awaiting_set and len(awaiting_set) == 1 and not entities.get("description"):
        cleaned_desc = query.strip()
        if cleaned_desc:
            entities["description"] = cleaned_desc
            entities.pop("start_time_str", None)
            entities.pop("end_time_str", None)

    if "cancel_selection" in awaiting_set and len(awaiting_set) == 1:
        selection = _extract_selection_index(query)
        if selection is not None:
            entities["selection_index"] = selection

    if "room" in awaiting_set and len(awaiting_set) == 1:
        manual_room = True
        cleaned_room = query.strip()
        if cleaned_room:
            entities["room_name"] = cleaned_room
            entities.setdefault("room_code", cleaned_room)
    
    # 추천 후 회의실 번호 입력 처리 (예: "10번 회의실", "1번")
    if action == "예약" and context.get("recommended_rooms"):
        import re
        # 숫자 추출
        number_match = re.search(r'(\d+)', query)
        if number_match:
            room_index = int(number_match.group(1))
            entities["room_index"] = room_index

    decision_slots = slot_decision.filled_slots if slot_decision else {}
    merged_entities: Dict[str, Any] = dict(entities or {})
    for key, value in (decision_slots or {}).items():
        if value is not None:
            merged_entities[key] = value

    base_start: Optional[datetime] = None
    base_end: Optional[datetime] = None
    need_full_time_parse = True
    if need_full_time_parse:
        base_start, base_end = await cached_parse(query)

    now_local = datetime.now(DEFAULT_TIMEZONE)
    
    start_time = None
    if base_start:
        start_time = _ensure_aware(base_start)
    elif merged_entities.get("start_time"):
        candidate_start = _ensure_aware(_parse_iso_datetime(merged_entities.get("start_time")))
        if candidate_start and candidate_start > now_local:
            start_time = candidate_start

    end_time = None
    if base_end:
        end_time = _ensure_aware(base_end)
    elif merged_entities.get("end_time"):
        candidate_end = _ensure_aware(_parse_iso_datetime(merged_entities.get("end_time")))
        if candidate_end and candidate_end > now_local:
            end_time = candidate_end

    duration_minutes: Optional[int] = None
    start_time_str = merged_entities.get("start_time_str")
    end_time_str = merged_entities.get("end_time_str")

    previous_slots = (slot_state_payload or {}).get("slots") or {}
    fallback_start = _parse_iso_datetime(previous_slots.get("start_time"))
    fallback_end = _parse_iso_datetime(previous_slots.get("end_time"))

    if start_time is None and fallback_start and "start_time" not in awaiting_set:
        if fallback_start > now_local:
            start_time = fallback_start
    if end_time is None and fallback_end and "end_time" not in awaiting_set:
        if fallback_end > now_local:
            end_time = fallback_end

    if start_time is None and start_time_str:
        parsed_start, parsed_end = await cached_parse(start_time_str)
        if parsed_start:
            start_time = _ensure_aware(parsed_start)
        if parsed_end and end_time is None:
            end_time = _ensure_aware(parsed_end)
        if (
            parsed_start
            and parsed_end
            and parsed_end - parsed_start == timedelta(hours=1)
            and not _query_has_explicit_duration_or_end(start_time_str)
            and not end_time_str
        ):
            end_time = None

    if end_time is None and end_time_str:
        parsed_start_hint, parsed_end = await cached_parse(end_time_str)
        heuristic_end = parsed_end
        if (
            parsed_start_hint
            and parsed_end
            and parsed_end - parsed_start_hint == timedelta(hours=1)
            and "까지" in end_time_str
        ):
            heuristic_end = parsed_start_hint
        if heuristic_end:
            end_time = _ensure_aware(heuristic_end)

    if (
        start_time
        and end_time
        and end_time <= start_time
        and not end_time_str
    ):
        end_time = None

    if "end_time" in awaiting_set and end_time is None:
        reference_start = start_time or fallback_start
        resolved_end = await _resolve_end_time_from_response(
            start_time=reference_start,
            end_time=end_time,
            start_time_str=start_time_str,
            end_time_str=end_time_str,
            fallback_start=reference_start,
            original_query=query,
            parse_cache=parse_cache,
        )
        if resolved_end and reference_start:
            duration = resolved_end - reference_start
            if duration.total_seconds() > 0:
                duration_minutes = int(duration.total_seconds() // 60)
            end_time = resolved_end
            start_time = reference_start
        elif resolved_end:
            end_time = resolved_end

    if duration_minutes is None:
        duration_minutes = _safe_int(merged_entities.get("duration_minutes"))

    if duration_minutes is None:
        duration_minutes = _safe_int(previous_slots.get("duration_minutes"))

    if duration_minutes is None and start_time and end_time and end_time > start_time:
        duration_minutes = int((end_time - start_time).total_seconds() // 60)

    participants_val = _safe_int(merged_entities.get("participants"))
    if participants_val is None:
        participants_val = _safe_int(previous_slots.get("participants"))

    capacity_val = _safe_int(merged_entities.get("capacity"))
    if capacity_val is None:
        capacity_val = _safe_int(previous_slots.get("capacity"))

    title_value = merged_entities.get("title")
    if isinstance(title_value, str):
        title_value = title_value.strip()
    if (not title_value) and manual_title:
        title_value = query.strip()
    if not title_value:
        prev_title = previous_slots.get("title")
        if isinstance(prev_title, str):
            title_value = prev_title.strip()
        else:
            title_value = prev_title or ""

    room_name_value = (
        merged_entities.get("room_name")
        or merged_entities.get("room")
        or (previous_slots.get("room_name") if not manual_room else None)
    )
    room_code_value = merged_entities.get("room_code")
    if room_code_value is None and not manual_room:
        room_code_value = previous_slots.get("room_code")
    if manual_room:
        cleaned_room = query.strip()
        if cleaned_room:
            room_name_value = cleaned_room
            room_code_value = room_code_value or cleaned_room

    requires_video_val = merged_entities.get("requires_video")
    if requires_video_val is None:
        requires_video_val = previous_slots.get("requires_video")

    requires_projector_val = merged_entities.get("requires_projector")
    if requires_projector_val is None:
        requires_projector_val = previous_slots.get("requires_projector")

    requires_whiteboard_val = merged_entities.get("requires_whiteboard")
    if requires_whiteboard_val is None:
        requires_whiteboard_val = previous_slots.get("requires_whiteboard")

    description_val = merged_entities.get("description")
    if description_val is None:
        description_val = previous_slots.get("description")

    selection_index_val = merged_entities.get("selection_index")
    if selection_index_val is None:
        selection_index_val = previous_slots.get("selection_index")
    selection_index_val = _safe_int(selection_index_val)

    attendees_val = merged_entities.get("attendees")
    if not attendees_val:
        attendees_val = previous_slots.get("attendees") or []

    return ParsedMeetingIntent(
        action=action,
        raw_query=query,
        user_id=user_id,
        employee_id=employee_id,
        start_time=start_time,
        end_time=end_time,
        duration_minutes=duration_minutes,
        capacity=capacity_val,
        participants=participants_val,
        floor=_safe_int(merged_entities.get("floor")) or _safe_int(previous_slots.get("floor")),
        room_index=_safe_int(merged_entities.get("room_index")) or _safe_int(previous_slots.get("room_index")),
        room_code=room_code_value,
        room_name=room_name_value,
        requires_video=requires_video_val,
        requires_projector=requires_projector_val,
        requires_whiteboard=requires_whiteboard_val,
        title=title_value or "",
        description=description_val,
        attendees=attendees_val,
        selection_index=selection_index_val,
    )


def _safe_int(value: Any) -> Optional[int]:
    """값을 안전하게 int로 변환한다."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _resolve_room_selection(
    parsed: ParsedMeetingIntent,
    scheduler: HybridScheduler,
    context: Dict[str, Any],
    default_room_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """사용자의 요청에서 실제 회의실 정보를 결정한다."""
    recommended = context.get("recommended_rooms") or []
    if parsed.room_index:
        idx = parsed.room_index - 1
        if 0 <= idx < len(recommended):
            return recommended[idx]

    if parsed.room_code or parsed.room_name:
        rooms = await scheduler.db.get_meeting_rooms()
        
        # 1단계: 정확한 매칭 우선 (완전 일치)
        for room in rooms:
            if parsed.room_code and room.get("room_code"):
                if parsed.room_code.lower() == room["room_code"].lower():
                    return room
            if parsed.room_name and room.get("name"):
                if parsed.room_name.lower() == room["name"].lower():
                    return room
        
        # 2단계: 부분 매칭 (정확한 매칭 실패 시)
        for room in rooms:
            if parsed.room_code and room.get("room_code"):
                if parsed.room_code.lower() in room["room_code"].lower():
                    return room
            if parsed.room_name and room.get("name"):
                if parsed.room_name.lower() in room["name"].lower():
                    return room

    if default_room_id:
        rooms = await scheduler.db.get_meeting_rooms()
        for room in rooms:
            if room.get("id") == default_room_id:
                return room

    last_room = context.get("last_selected_room")
    if last_room:
        return last_room

    return recommended[0] if recommended else None


def _format_reservation_list(reservations: List[Dict[str, Any]]) -> str:
    """예약 목록을 사용자 메시지 형태로 정리한다."""
    if not reservations:
        return "예약된 회의가 없습니다."

    lines = ["📅 현재 예약된 회의:"]
    for idx, item in enumerate(reservations[:10], start=1):
        start_dt = _parse_iso_datetime(item.get("start_time"))
        end_dt = _parse_iso_datetime(item.get("end_time"))
        start_str = start_dt.strftime("%Y-%m-%d %H:%M") if start_dt else item.get("start_time")
        end_str = end_dt.strftime("%H:%M") if end_dt else item.get("end_time")
        title = item.get("title") or "회의 예약"
        lines.append(f"{idx}. {title} ({start_str} ~ {end_str})")
    if len(reservations) > 10:
        lines.append(f"... 외 {len(reservations) - 10}건")
    return "\n".join(lines)


def _build_room_list_message(
    rooms: List[Dict[str, Any]],
    time_range: Optional[str] = None,
    parsed: Optional[Any] = None,
) -> str:
    """회의실 추천 메시지를 생성한다."""
    if not rooms:
        return "조건에 맞는 회의실을 찾지 못했습니다. 다른 조건으로 다시 요청해 보세요."

    limited_rooms = rooms[:10]
    lines = []
    
    # 시간 정보가 있으면 표시
    if time_range:
        lines.append(f"🕐 요청 시간: {time_range}")
        lines.append("")
    
    lines.append(f"📋 추천 회의실 목록 ({len(limited_rooms)}개):")
    for idx, room in enumerate(limited_rooms, start=1):
        capacity = room.get("capacity") or "-"
        floor = room.get("floor") or "-"
        has_video = "✅" if room.get("has_video") else "❌"
        lines.append(
            f"{idx}. {room.get('name') or room.get('room_code', '회의실')}"
            f" (수용 {capacity}명, {floor}층, 화상 {has_video})"
        )
    if len(rooms) > len(limited_rooms):
        lines.append(f"... 외 {len(rooms) - len(limited_rooms)}개 회의실")
    
    # 예약 가능 여부에 따라 안내 메시지 추가
    if parsed and parsed.start_time and parsed.end_time:
        lines.append("")
        lines.append("💡 원하시는 회의실을 예약해드릴까요?")
        lines.append("예약하실 회의실 번호나 이름을 말씀해 주세요.")
    
    return "\n".join(lines)


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """ISO 문자열을 datetime으로 안전하게 변환한다."""
    if not value:
        return None
    try:
        return _ensure_aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def _normalize_slot_value(slot_name: str, value: Any) -> Any:
    """Slot 값 유형을 정규화한다."""
    if value is None:
        return None

    if slot_name in {"start_time", "end_time"}:
        if isinstance(value, datetime):
            return _normalize_iso(value)
        if isinstance(value, str):
            dt_value = _parse_iso_datetime(value)
            if dt_value:
                return _normalize_iso(dt_value)
            return value.strip() or None
        return _normalize_iso(value)

    if slot_name in {"duration_minutes", "participants", "capacity", "room_index"}:
        return _coerce_int(value)

    if slot_name in {"requires_video", "requires_projector", "requires_whiteboard"}:
        return _coerce_bool(value)

    return value


def _export_parsed_slots(parsed: ParsedMeetingIntent) -> Dict[str, Any]:
    """ParsedMeetingIntent를 직렬화 가능한 슬롯 딕셔너리로 변환한다."""
    return {
        "start_time": _normalize_iso(parsed.start_time),
        "end_time": _normalize_iso(parsed.end_time),
        "duration_minutes": parsed.duration_minutes,
        "participants": parsed.participants,
        "capacity": parsed.capacity,
        "room_index": parsed.room_index,
        "room_code": parsed.room_code,
        "room_name": parsed.room_name,
        "requires_video": parsed.requires_video,
        "requires_projector": parsed.requires_projector,
        "requires_whiteboard": parsed.requires_whiteboard,
        "title": parsed.title,
        "description": parsed.description,
        "selection_index": parsed.selection_index,
    }


def _map_decision_action(decision_action: Optional[str], fallback: str) -> str:
    """Slot Manager가 제시한 액션을 내부 액션으로 매핑한다."""
    if not decision_action:
        return fallback

    mapping = {
        "예약": "예약",
        "취소": "취소",
        "조회": "조회",
        "추천": "조회",
        "유지": fallback,
    }
    return mapping.get(decision_action, fallback)


def _coerce_int(value: Any) -> Optional[int]:
    """정수 변환 유틸."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> Optional[bool]:
    """불린 변환 유틸."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "t"}:
            return True
        if lowered in {"false", "0", "no", "n", "f"}:
            return False
    return None


def _has_room_selection(parsed: ParsedMeetingIntent, context: Dict[str, Any]) -> bool:
    """회의실 선택 정보가 있는지 확인한다."""
    if parsed.room_index or parsed.room_code or parsed.room_name:
        return True
    slot_state = context.get("slot_state") or {}
    slots = slot_state.get("slots") or {}
    if slots.get("room_index") or slots.get("room_code") or slots.get("room_name"):
        return True
    if context.get("last_selected_room"):
        return True
    return False


def _collect_reservation_missing(parsed: ParsedMeetingIntent, context: Dict[str, Any]) -> List[str]:
    """예약에 필요한 필수 슬롯 중 비어 있는 항목을 수집한다."""
    slot_state = context.get("slot_state") or {}
    slots = slot_state.get("slots") or {}

    missing: List[str] = []

    title_candidate = (parsed.title or "").strip()
    if not title_candidate:
        title_candidate = (slots.get("title") or "").strip()
    if not title_candidate:
        missing.append("title")

    description_candidate = (parsed.description or "").strip()
    if not description_candidate:
        description_candidate = (slots.get("description") or "").strip()
    if not description_candidate:
        missing.append("description")

    start_candidate = parsed.start_time or _parse_iso_datetime(slots.get("start_time"))
    if start_candidate is None:
        missing.append("start_time")

    end_candidate = parsed.end_time or _parse_iso_datetime(slots.get("end_time"))
    duration_candidate: Optional[int]
    if parsed.duration_minutes is not None:
        duration_candidate = parsed.duration_minutes
    else:
        raw_duration = slots.get("duration_minutes")
        if isinstance(raw_duration, str):
            duration_candidate = _coerce_int(raw_duration)
        else:
            duration_candidate = raw_duration
    if end_candidate is None and not duration_candidate:
        missing.append("end_time")

    participant_candidate = (
        parsed.participants
        or parsed.capacity
        or slots.get("participants")
        or slots.get("capacity")
    )
    if isinstance(participant_candidate, str):
        participant_candidate = participant_candidate.strip() or None
    if participant_candidate is None:
        missing.append("participants")

    if not _has_room_selection(parsed, context):
        missing.append("room")

    return missing


def _build_initial_reservation_prompt(missing_slots: List[str]) -> str:
    """예약 시작 시 필수 정보를 안내하는 메시지를 생성한다."""
    # YAML에서 템플릿 로드
    template = get_sub_agent_prompt("meeting", "initial_reservation_template")
    
    # 누락된 슬롯 목록 생성
    missing_slots_list = "\n".join([
        f"- {RESERVATION_SLOT_DESCRIPTIONS[slot]}"
        for slot in RESERVATION_SLOT_ORDER
    ])
    
    # 템플릿 포맷팅
    return format_prompt_template(template, missing_slots_list=missing_slots_list)


def _build_missing_reservation_prompt(
    missing_slots: List[str],
    slot_decision: Optional[MeetingSlotDecision],
) -> str:
    """누락된 슬롯에 대해 사용자에게 안내 메시지를 생성한다."""
    if slot_decision and slot_decision.followup_question:
        return slot_decision.followup_question
    if not missing_slots:
        return "필요한 정보가 모두 준비되었습니다."
    
    # YAML에서 템플릿 로드
    template = get_sub_agent_prompt("meeting", "missing_reservation_template")
    
    # 누락된 슬롯 목록 생성
    missing_slots_list = "\n".join([
        f"- {RESERVATION_SLOT_DESCRIPTIONS[slot]}"
        for slot in missing_slots
    ])
    
    # 템플릿 포맷팅
    return format_prompt_template(template, missing_slots_list=missing_slots_list)


def _maybe_reset_meeting_context(
    context: Dict[str, Any],
    action: Literal["조회", "예약", "취소", "수정"],
    query: str,
) -> None:
    """사용자가 명시적으로 다른 의도를 말했을 때 슬롯 상태를 초기화한다."""
    if not context.get("slot_state"):
        return

    # 사용자에게 추가 정보를 기다리는 중이라면 컨텍스트를 유지한다.
    awaiting_slots = context.get("awaiting_slots") or []
    if awaiting_slots:
        return

    action_keywords = {
        "예약": ["예약", "잡아", "잡고", "reserve", "book"],
        "조회": ["추천", "조회", "찾아", "list", "find", "show"],
        "취소": ["취소", "삭제", "cancel", "없애", "지워"],
        "수정": ["수정", "변경", "옮겨", "update", "modify"],
    }

    normalized_query = query.lower()
    keywords = action_keywords.get(action, [])
    if keywords and any(keyword in normalized_query for keyword in keywords):
        for key in [
            "slot_state",
            "awaiting_slots",
            "last_question",
            "cancel_candidates",
            "last_action",
            "last_query",
        ]:
            context.pop(key, None)


def _contains_explicit_date(text: Optional[str]) -> bool:
    """문자열이 날짜 정보를 명시하는지 판별한다."""
    if not text:
        return False
    lowered = text.lower()
    date_keywords = [
        "오늘",
        "내일",
        "모레",
        "이번",
        "다음",
        "차주",
        "주말",
        "월요일",
        "화요일",
        "수요일",
        "목요일",
        "금요일",
        "토요일",
        "일요일",
        "today",
        "tomorrow",
        "next",
    ]
    if any(keyword in lowered for keyword in date_keywords):
        return True
    if re.search(r"\d{1,2}\s*월", text):
        return True
    if re.search(r"\d{1,2}\s*일", text):
        return True
    if re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", text):
        return True
    return False


def _align_with_reference(
    candidate: datetime,
    reference: Optional[datetime],
    has_explicit_date: bool,
) -> datetime:
    """종료 시간을 기준 날짜와 맞춘다."""
    if reference is None or has_explicit_date:
        return _ensure_aware(candidate)

    reference = _ensure_aware(reference)
    aligned = datetime.combine(reference.date(), candidate.time(), tzinfo=reference.tzinfo)
    if aligned <= reference:
        aligned += timedelta(days=1)
    return aligned


async def _resolve_end_time_from_response(
    *,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    start_time_str: Optional[str],
    end_time_str: Optional[str],
    fallback_start: Optional[datetime] = None,
    original_query: Optional[str] = None,
    parse_cache: Optional[Dict[str, Tuple[Optional[datetime], Optional[datetime]]]] = None,
) -> Optional[datetime]:
    """종료 시간을 추론한다."""
    parse_cache = parse_cache or {}

    async def cached_parse(text: Optional[str]) -> Tuple[Optional[datetime], Optional[datetime]]:
        if not text:
            return None, None
        if text not in parse_cache:
            parse_cache[text] = await parse_korean_date_with_llm(text)
        return parse_cache[text]

    start_time = _ensure_aware(start_time) if start_time else None
    end_time = _ensure_aware(end_time) if end_time else None
    fallback_start = _ensure_aware(fallback_start) if fallback_start else None
    base_start = fallback_start or start_time
    candidate_texts: List[Tuple[str, bool]] = []
    if end_time_str:
        candidate_texts.append((end_time_str, _contains_explicit_date(end_time_str)))
    if start_time_str:
        candidate_texts.append((start_time_str, _contains_explicit_date(start_time_str)))
    if original_query:
        candidate_texts.append((original_query, _contains_explicit_date(original_query)))

    seen_texts: set[str] = set()
    candidates: List[datetime] = []
    for text, has_explicit_date in candidate_texts:
        if text in seen_texts:
            continue
        seen_texts.add(text)
        parsed_start, parsed_end = await cached_parse(text)
        candidate: Optional[datetime] = None
        if parsed_end and parsed_start:
            diff = parsed_end - parsed_start
            if diff == timedelta(hours=1) and not _query_has_explicit_duration_or_end(text):
                candidate = parsed_start
            else:
                candidate = parsed_end
        else:
            candidate = parsed_end or parsed_start
        if candidate:
            aligned = _align_with_reference(candidate, fallback_start or start_time, has_explicit_date)
            candidates.append(aligned)

    if end_time and base_start:
        aligned_end = _align_with_reference(end_time, base_start, False)
        if aligned_end <= base_start:
            aligned_end = base_start + timedelta(hours=1)
        candidates.append(aligned_end)
    elif end_time:
        candidates.append(end_time)
    if not end_time and start_time:
        candidates.append(_align_with_reference(start_time, base_start or start_time, False))

    if not candidates:
        return None

    best = candidates[0]
    if base_start:
        # 가장 가까운 미래 시간을 선택한다.
        best = min(
            candidates,
            key=lambda dt: (
                base_start is not None and dt < base_start,
                abs((dt - base_start).total_seconds()) if base_start else 0,
            ),
        )
        if base_start and best <= base_start:
            best = base_start + timedelta(hours=1)
    return _ensure_aware(best)


def _persist_slot_state(context: Dict[str, Any], parsed: ParsedMeetingIntent) -> None:
    """slot_state 컨텍스트를 최신 파싱 결과로 갱신한다."""
    slot_state = context.get("slot_state") or {}
    slot_state["intent"] = parsed.action
    slot_state["slots"] = _export_parsed_slots(parsed)
    if "missing_slots" not in slot_state:
        slot_state["missing_slots"] = []
    context["slot_state"] = slot_state


def _normalize_iso(value: Any) -> Optional[str]:
    """값을 ISO 문자열로 정규화한다."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value).isoformat()
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _ensure_aware(dt: datetime) -> datetime:
    """datetime을 기본 타임존 기준 timezone-aware로 변환한다."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=DEFAULT_TIMEZONE)
    return dt.astimezone(DEFAULT_TIMEZONE)


def _to_rfc3339(dt: datetime) -> str:
    """Google Calendar API에서 요구하는 RFC3339 문자열로 변환한다."""
    aware = _ensure_aware(dt).astimezone(timezone.utc)
    return aware.isoformat().replace("+00:00", "Z")


def _to_local_naive(dt: datetime) -> datetime:
    """DB 저장/비교용 naive 로컬 시간을 생성한다."""
    return _ensure_aware(dt).replace(tzinfo=None)


def _query_has_explicit_duration_or_end(query: str) -> bool:
    """문장 안에 종료 시간 또는 지속 시간을 명시했는지 판별한다."""
    lowered = query.lower()
    explicit_keywords = [
        "까지",
        "부터",
        "~",
        " to ",
        " until",
        " for ",
        " ending",
        " end ",
        "마칠",
        "종료",
    ]
    if any(keyword in lowered for keyword in explicit_keywords):
        return True

    # 숫자와 함께 시간 단위를 언급한 경우 (예: 2시간, 30분)
    duration_pattern = re.compile(r"\d+\s*(시간|분)\s*(동안|간|정도|예정|필요)?")
    if duration_pattern.search(query):
        return True

    # 특정 종료 시각을 명시한 패턴 (예: 3시까지, 4:30 ~ 5:00)
    end_pattern = re.compile(
        r"(\d{1,2}\s*시\s*까지)|(\d{1,2}:\d{2}\s*(까지|부터|~|-))|(\d+\s*(시간|분)\s*(후|뒤))"
    )
    return bool(end_pattern.search(query))


def _extract_selection_index(query: str) -> Optional[int]:
    """사용자 입력에서 선택 번호를 추출한다."""
    import re
    
    # "1번", "첫번째", "1", "첫 번째" 등 다양한 패턴 인식
    patterns = [
        r'(\d+)\s*번',
        r'(\d+)\s*번째',
        r'^(\d+)$',
        r'번호\s*(\d+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue
    
    # 한글 숫자 처리
    korean_numbers = {
        '첫': 1, '첫번째': 1, '하나': 1,
        '둘': 2, '두번째': 2,
        '셋': 3, '세번째': 3,
        '넷': 4, '네번째': 4,
        '다섯': 5, '다섯번째': 5,
    }
    
    query_lower = query.lower()
    for word, num in korean_numbers.items():
        if word in query_lower:
            return num
    
    return None


def _extract_selection_indices(query: str) -> List[int]:
    """사용자 입력에서 다중 선택 번호를 추출한다."""
    import re

    indices: List[int] = []
    seen: set[int] = set()

    for match in re.findall(r'(\d+)\s*번|\b(\d+)\b', query):
        number = next((m for m in match if m), None)
        if number is None:
            continue
        try:
            value = int(number)
        except ValueError:
            continue
        if value not in seen:
            indices.append(value)
            seen.add(value)

    korean_numbers = {
        '첫': 1, '첫번째': 1, '하나': 1,
        '둘': 2, '두번째': 2,
        '셋': 3, '세번째': 3,
        '넷': 4, '네번째': 4,
        '다섯': 5, '다섯번째': 5,
    }
    lowered = query.lower()
    for word, value in korean_numbers.items():
        if word in lowered and value not in seen:
            indices.append(value)
            seen.add(value)

    return indices


def _collect_cancel_candidates(
    *,
    gcal_events: List[Dict[str, Any]],
    db_reservations: List[Dict[str, Any]],
    last_reservation: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """취소 후보 목록을 정규화한다."""
    candidates: Dict[str, Dict[str, Any]] = {}

    def add_candidate(candidate: Optional[Dict[str, Any]]) -> None:
        if not candidate:
            return
        event_id = candidate.get("event_id")
        if not event_id:
            return
        existing = candidates.get(event_id)
        if existing is None or candidate.get("source") == "gcal":
            candidates[event_id] = candidate

    add_candidate(_candidate_from_last_reservation(last_reservation))

    for event in gcal_events:
        add_candidate(_candidate_from_gcal_event(event))

    for record in db_reservations:
        add_candidate(_candidate_from_db_reservation(record))

    return sorted(
        candidates.values(),
        key=lambda item: (
            _parse_iso_datetime(item.get("start_time")) or datetime.max.replace(tzinfo=timezone.utc)
        ),
    )


def _filter_cancel_candidates(
    *,
    candidates: List[Dict[str, Any]],
    room_name: Optional[str],
    room_code: Optional[str],
) -> List[Dict[str, Any]]:
    """회의실 조건으로 후보를 필터링."""
    if not room_name and not room_code:
        return []

    filtered: List[Dict[str, Any]] = []
    room_name_lower = room_name.lower() if isinstance(room_name, str) else None
    room_code_lower = room_code.lower() if isinstance(room_code, str) else None

    for candidate in candidates:
        cand_room = (candidate.get("meeting_room_name") or "").lower()
        if room_name_lower and room_name_lower in cand_room:
            filtered.append(candidate)
            continue
        if room_code_lower and room_code_lower in cand_room:
            filtered.append(candidate)

    return filtered


def _candidate_from_last_reservation(reservation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """마지막 예약 컨텍스트에서 후보 생성."""
    if not reservation:
        return None
    event = reservation.get("gcal_event") or {}
    event_id = event.get("id") or reservation.get("gcal_event_id")
    if not event_id:
        return None
    start_raw = (
        event.get("start", {}).get("dateTime")
        or event.get("start", {}).get("date")
        or reservation.get("start_time")
    )
    end_raw = (
        event.get("end", {}).get("dateTime")
        or event.get("end", {}).get("date")
        or reservation.get("end_time")
    )
    return {
        "event_id": str(event_id),
        "title": event.get("summary") or reservation.get("title") or "회의 예약",
        "start_time": _normalize_iso(start_raw),
        "end_time": _normalize_iso(end_raw),
        "meeting_room_name": reservation.get("meeting_room_name"),
        "source": "context",
    }


def _candidate_from_gcal_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Google Calendar 이벤트에서 후보 생성."""
    event_id = _extract_event_id(event)
    if not event_id:
        return None
    start_raw = event.get("start_time") or event.get("start")
    end_raw = event.get("end_time") or event.get("end")
    meeting_room = event.get("meeting_room_name") or event.get("location")
    return {
        "event_id": str(event_id),
        "title": event.get("title") or event.get("summary") or "회의 예약",
        "start_time": _normalize_iso(start_raw),
        "end_time": _normalize_iso(end_raw),
        "meeting_room_name": meeting_room,
        "source": "gcal",
    }


def _candidate_from_db_reservation(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """DB 예약 레코드에서 후보 생성."""
    event_id = record.get("gcal_event_id")
    if not event_id:
        return None
    return {
        "event_id": str(event_id),
        "title": record.get("title") or "회의 예약",
        "start_time": _normalize_iso(record.get("start_time")),
        "end_time": _normalize_iso(record.get("end_time")),
        "meeting_room_name": record.get("meeting_room_name"),
        "source": "db",
    }


def _format_cancel_prompt(candidates: List[Dict[str, Any]]) -> str:
    """취소 후보 목록을 사용자 메시지로 변환한다."""
    # YAML에서 템플릿 로드
    template = get_sub_agent_prompt("meeting", "cancel_prompt_template")
    
    # 후보 목록 생성
    lines = []
    for idx, candidate in enumerate(candidates, start=1):
        start_dt = _parse_iso_datetime(candidate.get("start_time"))
        end_dt = _parse_iso_datetime(candidate.get("end_time"))
        start_str = start_dt.strftime("%Y-%m-%d %H:%M") if start_dt else (candidate.get("start_time") or "-")
        end_str = end_dt.strftime("%H:%M") if end_dt else (candidate.get("end_time") or "-")
        room_name = candidate.get("meeting_room_name")
        room_text = f" | 회의실: {room_name}" if room_name else ""
        lines.append(f"{idx}. {candidate.get('title', '회의 예약')} ({start_str} ~ {end_str}){room_text}")
    
    candidates_list = "\n".join(lines)
    
    # 템플릿 포맷팅
    return format_prompt_template(template, candidates_list=candidates_list)


def _format_cancel_success(candidate: Dict[str, Any]) -> str:
    """취소 성공 메시지 생성."""
    start_dt = _parse_iso_datetime(candidate.get("start_time"))
    end_dt = _parse_iso_datetime(candidate.get("end_time"))
    start_str = start_dt.strftime("%Y-%m-%d %H:%M") if start_dt else (candidate.get("start_time") or "-")
    end_str = end_dt.strftime("%H:%M") if end_dt else (candidate.get("end_time") or "-")
    room_name = candidate.get("meeting_room_name")
    room_text = f"\n🏢 회의실: {room_name}" if room_name else ""
    return (
        f"✅ '{candidate.get('title', '회의 예약')}' 예약을 취소했습니다.\n"
        f"📅 일시: {start_str} ~ {end_str}{room_text}"
    )


def _extract_event_id(event: Dict[str, Any]) -> Optional[str]:
    """Google Calendar 이벤트 또는 DB 레코드에서 이벤트 ID 추출."""
    event_id = event.get("id") or event.get("event_id") or event.get("gcal_event_id")
    return str(event_id) if event_id else None


def _apply_slot_decision(
    *,
    parsed: ParsedMeetingIntent,
    slot_decision: Optional[MeetingSlotDecision],
    meeting_context: Dict[str, Any],
    slot_state_payload: Dict[str, Any],
    raw_query: str,
) -> Optional[str]:
    """Slot Manager 결정 사항을 컨텍스트에 반영한다."""
    print(f"[Slot Manager INPUT] query={raw_query}")
    print(f"[Slot Manager INPUT] slot_state={slot_state_payload}")
    if slot_decision is None:
        meeting_context.pop("awaiting_slots", None)
        meeting_context.pop("last_question", None)
        return None

    print(f"[Slot Manager OUTPUT] filled_slots={slot_decision.filled_slots}")
    print(f"[Slot Manager OUTPUT] missing_slots={slot_decision.missing_slots}")
    print(f"[Slot Manager OUTPUT] followup={slot_decision.followup_question} should_ask={slot_decision.should_ask}")

    merged_slots: Dict[str, Any] = dict(slot_state_payload.get("slots") or {})
    for key, value in (slot_decision.filled_slots or {}).items():
        normalized = _normalize_slot_value(key, value)
        if normalized is not None:
            merged_slots[key] = normalized

    parsed_slots = _export_parsed_slots(parsed)
    for key, value in parsed_slots.items():
        if value is not None:
            merged_slots[key] = value

    # 슬롯이 채워진 경우 missing_slots 재계산
    recalculated_missing = []
    for slot_name in (slot_decision.missing_slots or []):
        if slot_name not in merged_slots or merged_slots[slot_name] is None:
            recalculated_missing.append(slot_name)
        elif slot_name in ["title", "description"] and not str(merged_slots[slot_name]).strip():
            recalculated_missing.append(slot_name)
    
    meeting_context["slot_state"] = {
        "intent": parsed.action,
        "slots": merged_slots,
        "missing_slots": recalculated_missing,
    }

    if parsed.action == "취소":
        awaiting_slots = meeting_context.get("awaiting_slots") or []
        if awaiting_slots == ["cancel_selection"]:
            indices = _extract_selection_indices(raw_query)
            if indices:
                candidates = meeting_context.get("cancel_candidates") or []
                queue = meeting_context.get("cancel_selection_queue") or []
                invalid_indices: List[int] = []
                for idx in indices:
                    if 1 <= idx <= len(candidates):
                        event_id = candidates[idx - 1].get("event_id")
                        if event_id:
                            queue.append(str(event_id))
                    else:
                        invalid_indices.append(idx)

                if queue:
                    meeting_context["cancel_selection_queue"] = queue
                    meeting_context["awaiting_slots"] = []
                    meeting_context["last_question"] = None
                    meeting_context["slot_state"]["missing_slots"] = []
                    meeting_context["slot_state"]["slots"].pop("selection_index", None)
                    meeting_context["slot_state"]["slots"].pop("event_id", None)
                    if invalid_indices:
                        meeting_context["cancel_selection_error"] = invalid_indices
                    else:
                        meeting_context.pop("cancel_selection_error", None)
                    return None

                if invalid_indices:
                    meeting_context["cancel_selection_error"] = invalid_indices

    # recalculated_missing이 비어있으면 추가 질문 없음
    if recalculated_missing and slot_decision.should_ask:
        # 여러 슬롯이 누락되었으면 한 번에 물어봄
        if len(recalculated_missing) > 1:
            slot_descriptions = [
                RESERVATION_SLOT_DESCRIPTIONS.get(slot, slot)
                for slot in recalculated_missing
            ]
            combined_question = "다음 정보를 알려주세요:\n" + "\n".join(
                f"- {desc}" for desc in slot_descriptions
            )
            meeting_context["awaiting_slots"] = recalculated_missing
            meeting_context["last_question"] = combined_question
            print(f"[복합 질문 생성] missing={recalculated_missing}, question={combined_question}")
            return combined_question
        elif slot_decision.followup_question:
            meeting_context["awaiting_slots"] = recalculated_missing
            meeting_context["last_question"] = slot_decision.followup_question
            return slot_decision.followup_question

    meeting_context.pop("awaiting_slots", None)
    meeting_context.pop("last_question", None)
    return None


def _build_slot_state_payload(
    *,
    current_action: str,
    meeting_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Slot Manager 호출에 사용할 컨텍스트 기반 상태를 구성한다."""
    existing_state = meeting_context.get("slot_state") or {}
    payload = {
        "intent": existing_state.get("intent") or current_action,
        "slots": existing_state.get("slots") or {},
        "recommended_rooms": meeting_context.get("recommended_rooms") or [],
        "awaiting_slots": meeting_context.get("awaiting_slots") or [],
        "last_question": meeting_context.get("last_question"),
    }
    return payload