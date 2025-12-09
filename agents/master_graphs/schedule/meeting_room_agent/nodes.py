"""LangGraph 회의실 에이전트 노드 정의."""

from __future__ import annotations

import json
import logging
from datetime import datetime
import re
from typing import Any, Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .prompt_config import SYSTEM_PROMPT, PARSE_PROMPT
from .schemas import AgentState, Reservation
from .services import (
    cancel_reservation,
    fetch_reservations,
    get_reservation,
    is_room_available,
    recommend_meeting_rooms,
    reserve_room,
    update_reservation,  # ✅ 추가!
)

__all__: list[str] = [
    "llm",
    "intent_node",
    "reserve_node",
    "cancel_node",
    "update_node",  # ✅ 추가!
    "recommend_node",
    "lookup_node",
]

LOGGER = logging.getLogger(__name__)

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)


def _coerce_datetime(value: Any) -> datetime | None:
    """문자열 또는 datetime 값을 datetime으로 변환한다."""

    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            LOGGER.warning("ISO8601 형식이 아닌 시간 값: %s", value)
            return None
    return None


async def _parse_with_llm(user_input: str) -> dict[str, Any]:
    """
    LLM을 사용하여 자연어에서 회의실 예약 정보를 추출
    
    Args:
        user_input: 사용자의 자연어 입력
        
    Returns:
        파싱된 정보를 담은 딕셔너리
        {
            "start_time": datetime | None,
            "end_time": datetime | None,
            "participants": int | None,
            "building": str | None,
            "require_video": bool
        }
    
    Examples:
        "오늘 10시에 5명" → {"start_time": "2025-01-15T10:00:00", ...}
        "다음주 화요일 오후 회의실" → {"start_time": "2025-01-21T14:00:00", ...}
    """
    now = datetime.now()
    
    prompt = PARSE_PROMPT.format(current_time=now.isoformat())
    
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=f"사용자 입력: {user_input}")
    ]
    
    try:
        response = await llm.ainvoke(messages)
        content = (response.content or "").strip()
        
        # JSON 코드 블록 제거 (```json ... ``` 형태)
        content = re.sub(r"```json\s*", "", content)
        content = re.sub(r"```\s*", "", content)
        content = content.strip()
        
        data = json.loads(content)
        
        # datetime 변환
        result = {
            "start_time": _coerce_datetime(data.get("start_time")),
            "end_time": _coerce_datetime(data.get("end_time")),
            "participants": data.get("participants"),
            "building": data.get("building"),
            "require_video": bool(data.get("require_video", False)),
            "meeting_room_id": data.get("meeting_room_id"),
            "title": data.get("title"),
        }
        
        LOGGER.info("LLM 파싱 결과: %s", result)
        return result
        
    except json.JSONDecodeError as exc:
        LOGGER.error("LLM 응답 JSON 파싱 실패: %s, content: %s", exc, content)
        return {
            "start_time": None,
            "end_time": None,
            "participants": None,
            "building": None,
            "require_video": False,
            "meeting_room_id": None,
            "title": None,
        }
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("LLM 파싱 중 오류 발생", exc_info=exc)
        return {
            "start_time": None,
            "end_time": None,
            "participants": None,
            "building": None,
            "require_video": False,
            "meeting_room_id": None,
            "title": None,
        }


RESERVATION_REQUIRED_FIELDS: list[str] = [
    "meeting_room_id",
    "employee_id",
    "title",
    "start_time",
    "end_time",
    "participants",
]

UPDATE_OPTIONAL_FIELDS: list[str] = [
    "meeting_room_id",
    "title",
    "start_time",
    "end_time",
    "participants",
    "description",
    "status",
]

FIELD_QUESTIONS_RESERVE: dict[str, str] = {
    "meeting_room_id": "사용할 회의실 ID를 숫자로 입력해 주세요.",
    "employee_id": "예약을 요청하는 사번(직원 ID)을 숫자로 입력해 주세요.",
    "title": "회의 제목을 알려주세요.",
    "start_time": "회의 시작 시간을 YYYY-MM-DD HH:MM 형식(예: 2025-11-19 13:00)으로 알려주세요.",
    "end_time": "회의 종료 시간을 YYYY-MM-DD HH:MM 형식(예: 2025-11-19 14:00)으로 알려주세요.",
    "participants": "참석 인원 수를 숫자로 알려주세요.",
}

FIELD_QUESTION_CANCEL_ID = "취소할 예약 ID를 숫자로 알려주세요."
FIELD_QUESTION_UPDATE_ID = "수정할 예약 ID를 숫자로 알려주세요."
FIELD_QUESTION_UPDATE_DETAIL = (
    "변경할 내용을 `필드=값` 형식으로 알려주세요. "
    "예: start_time=2025-11-19T13:00:00, end_time=2025-11-19T14:00:00. "
    "변경이 더 없다면 '없음'이라고 답해주세요."
)

SKIP_RESPONSES = {
    "없음",
    "없어요",
    "없다",
    "없습니다",
    "건너뛰기",
    "skip",
    "no",
    "아니오",
    "아니요",
}

LOOKUP_REQUIRED_FIELDS: list[str] = ["start_time", "end_time"]
LOOKUP_OPTIONAL_FIELDS: list[str] = ["meeting_room_id", "building", "status"]
FIELD_QUESTIONS_LOOKUP: dict[str, str] = {
    "start_time": "조회 시작 시간을 YYYY-MM-DD HH:MM 형식(예: 2025-11-21 09:00)으로 알려주세요.",
    "end_time": "조회 종료 시간을 YYYY-MM-DD HH:MM 형식(예: 2025-11-21 10:00)으로 알려주세요.",
}

RESERVATION_KEY_MAPPING = {
    "meetingRoomId": "meeting_room_id",
    "employeeId": "employee_id",
    "scheduleId": "schedule_id",
    "startTime": "start_time",
    "endTime": "end_time",
}


def _normalize_reservation_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        normalized[RESERVATION_KEY_MAPPING.get(key, key)] = value
    return normalized


def _normalize_lookup_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        lower = key.lower()
        if lower in {"meetingroomid", "meeting_room_id", "room_id", "room"}:
            normalized["meeting_room_id"] = value
        elif lower in {"building"}:
            normalized["building"] = value
        elif lower in {"start", "start_time", "starttime"}:
            normalized["start_time"] = value
        elif lower in {"end", "end_time", "endtime"}:
            normalized["end_time"] = value
        elif lower in {"status"}:
            normalized["status"] = value
        else:
            normalized[key] = value
    return normalized


def _is_skip_response(text: str) -> bool:
    return text.strip().lower() in SKIP_RESPONSES


def _parse_datetime_string(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    for candidate in (text, text.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _extract_first_int(text: str) -> int | None:
    matches = re.findall(r"\d+", text)
    if matches:
        return int(matches[0])
    return None


def _coerce_candidate_types(candidate: dict[str, Any]) -> None:
    for key in list(candidate.keys()):
        if key.startswith("_"):
            continue
        value = candidate.get(key)
        if key in {"meeting_room_id", "employee_id", "participants", "id"}:
            if isinstance(value, str):
                number = _extract_first_int(value)
                if number is not None:
                    candidate[key] = number
            elif isinstance(value, float):
                candidate[key] = int(value)
        if key in {"start_time", "end_time"}:
            if isinstance(value, str):
                dt = _parse_datetime_string(value)
                if dt:
                    candidate[key] = dt


def _field_filled(candidate: dict[str, Any], field: str) -> bool:
    value = candidate.get(field)
    if value is None:
        return False
    if field in {"start_time", "end_time"}:
        return isinstance(value, datetime)
    if field in {"meeting_room_id", "employee_id", "participants", "id"}:
        return isinstance(value, int)
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _parse_field_value(field: str, answer: str) -> tuple[Any | None, str | None]:
    text = answer.strip()
    if not text:
        return None, "값을 입력해 주세요."
    if field in {"meeting_room_id", "employee_id", "participants", "id"}:
        number = _extract_first_int(text)
        if number is None:
            return None, "숫자 형태로 입력해 주세요."
        return number, None
    if field in {"start_time", "end_time"}:
        dt = _parse_datetime_string(text)
        if dt is None:
            return None, "시간은 YYYY-MM-DD HH:MM 형식으로 입력해 주세요."
        return dt, None
    return text, None


def _set_pending_question(state: AgentState, field: str, intent: str, question: str) -> AgentState:
    state["pending_field"] = field
    state["pending_intent"] = intent
    state["response"] = question
    state["intent"] = intent
    return state


def _clear_pending(state: AgentState) -> None:
    state["pending_field"] = None
    state["pending_intent"] = None


def _build_reservation_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "meeting_room_id": candidate["meeting_room_id"],
        "employee_id": candidate["employee_id"],
        "title": candidate["title"],
        "description": candidate.get("description"),
        "start_time": candidate["start_time"],
        "end_time": candidate["end_time"],
        "participants": candidate["participants"],
    }


def _parse_manual_update_detail(answer: str) -> tuple[dict[str, Any], str | None]:
    if _is_skip_response(answer):
        return {}, None
    updates: dict[str, Any] = {}
    parts = [p.strip() for p in answer.split(",") if p.strip()]
    if not parts and "=" not in answer:
        return {}, "변경할 내용을 `필드=값` 형식으로 입력해 주세요."
    if not parts:
        parts = [answer.strip()]
    field_map = {
        "meeting_room_id": "meeting_room_id",
        "room": "meeting_room_id",
        "room_id": "meeting_room_id",
        "title": "title",
        "제목": "title",
        "start": "start_time",
        "start_time": "start_time",
        "시작": "start_time",
        "end": "end_time",
        "end_time": "end_time",
        "종료": "end_time",
        "participants": "participants",
        "인원": "participants",
        "description": "description",
        "내용": "description",
    }
    for part in parts:
        if "=" not in part:
            return {}, "변경할 내용을 `필드=값` 형식으로 입력해 주세요."
        key, value = part.split("=", 1)
        canonical = field_map.get(key.strip().lower())
        if not canonical:
            return {}, f"'{key.strip()}' 항목은 수정할 수 없습니다. 사용 가능한 필드: meeting_room_id, start_time, end_time, title, participants, description"
        parsed, error = _parse_field_value(canonical if canonical != "description" else "description", value)
        if error:
            return {}, error
        updates[canonical] = parsed
    if not updates:
        return {}, "변경할 내용을 입력해 주세요."
    return updates, None


async def _parse_update_answer(answer: str, existing: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
    if _is_skip_response(answer):
        return {}, None

    parsed = await _parse_with_llm(answer)
    updates: dict[str, Any] = {}

    start_dt = parsed["start_time"]
    end_dt = parsed["end_time"]
    if start_dt and existing and isinstance(existing.get("start_time"), datetime):
        base = existing["start_time"]
        start_dt = start_dt.replace(year=base.year, month=base.month, day=base.day)
    if end_dt and existing and isinstance(existing.get("end_time"), datetime):
        base = existing["end_time"]
        end_dt = end_dt.replace(year=base.year, month=base.month, day=base.day)
    if start_dt:
        updates["start_time"] = start_dt
    if end_dt:
        updates["end_time"] = end_dt
    if parsed["participants"] is not None:
        updates["participants"] = parsed["participants"]
    if parsed["meeting_room_id"] is not None:
        updates["meeting_room_id"] = parsed["meeting_room_id"]
    title = parsed.get("title")
    if isinstance(title, str) and title.strip():
        updates["title"] = title.strip()

    if updates:
        return updates, None

    if "=" in answer:
        return _parse_manual_update_detail(answer)

    return {}, "변경할 내용을 이해하지 못했습니다. 예: 시작 시간을 오후 3시로, 참석자를 4명으로 변경해줘."


async def intent_node(state: AgentState) -> AgentState:
    """사용자 입력에서 의도를 분류한다.

    Args:
        state: LangGraph 상태 딕셔너리.

    Returns:
        AgentState: 분류된 intent가 추가된 상태.
    """

    query = state.get("user_input", "").strip()
    if not query:
        state["intent"] = "unknown"
        state["response"] = "요청 내용을 이해하지 못했습니다. 다시 입력해 주세요."
        return state

    pending_field = state.get("pending_field")
    pending_intent = state.get("pending_intent")
    if pending_field and pending_intent:
        state["intent"] = pending_intent
        LOGGER.debug("의도 분류: pending 상태 %s → %s", pending_field, pending_intent)
        return state

    reservation_exists = state.get("reservation") is not None or bool(state.get("reservation_candidate"))
    has_pending_candidate = bool(state.get("reservation_candidate"))
    query_lower = query.lower()

    # 컨텍스트에서 명시적으로 action이 주어진 경우 우선 사용
    action = state.get("action")
    if isinstance(action, str):
        normalized = action.strip().lower()
        if normalized in {"update", "modify", "edit"}:
            state["intent"] = "수정"
            LOGGER.debug("의도 분류: context action으로 '수정' 선택")
            return state
        if normalized in {"cancel", "취소"}:
            state["intent"] = "취소"
            LOGGER.debug("의도 분류: context action으로 '취소' 선택")
            return state

    if reservation_exists:
        update_keywords = ["수정", "변경", "바꿔", "업데이트", "재조정", "move", "change", "update", "modify", "reschedule", "adjust"]
        cancel_keywords = ["취소", "cancel", "취소해", "취소해줘", "취소해주세요"]

        if not has_pending_candidate and any(keyword in query for keyword in update_keywords):
            state["intent"] = "수정"
            LOGGER.debug("의도 분류: 예약 context + 키워드로 '수정' 선택")
            return state
        if any(keyword in query for keyword in cancel_keywords) or "cancel" in query_lower:
            state["intent"] = "취소"
            LOGGER.debug("의도 분류: 예약 context + 키워드로 '취소' 선택")
            return state

        if any(keyword in query for keyword in update_keywords) or any(keyword in query_lower for keyword in ["change", "update", "modify", "reschedule", "adjust"]):
            state["intent"] = "수정"
            LOGGER.debug("의도 분류: 예약 context + 키워드로 '수정' 선택")
            return state

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                "다음 사용자 요청의 의도를 `조회`, `추천`, `예약`, `취소` 중 하나로 답변하세요.\n"
                f"요청: {query}"
            )
        ),
    ]
    try:
        response = await llm.ainvoke(messages)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("의도 분류 중 오류 발생", exc_info=exc)
        state["intent"] = "error"
        state["response"] = "의도 분류 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        return state

    intent_raw = (response.content or "").strip()
    match = re.search(r"(조회|추천|예약|취소)", intent_raw)
    intent = match.group(1) if match else "unknown"
    state["intent"] = intent
    LOGGER.debug("의도 분류 결과: raw=%s, parsed=%s", intent_raw, intent)
    return state


async def reserve_node(state: AgentState) -> AgentState:
    """자연어 기반 예약 생성."""

    candidate: dict[str, Any] = dict(state.get("reservation_candidate") or {})

    reservation_raw = state.get("reservation")
    if isinstance(reservation_raw, Mapping):
        candidate.update(_normalize_reservation_dict(reservation_raw))

    _coerce_candidate_types(candidate)

    if state.get("pending_field") and state.get("pending_intent") == "예약":
        field = state["pending_field"]  # type: ignore[index]
        answer = state.get("user_input", "")
        value, error = _parse_field_value(field, answer)
        if error:
            state["intent"] = "예약"
            state["response"] = error
            state["reservation_candidate"] = candidate
            return state
        candidate[field] = value
        _clear_pending(state)

    state["reservation_candidate"] = candidate

    missing_fields = [
        field for field in RESERVATION_REQUIRED_FIELDS if not _field_filled(candidate, field)
    ]
    if missing_fields:
        field = missing_fields[0]
        question = FIELD_QUESTIONS_RESERVE[field]
        return _set_pending_question(state, field, "예약", question)

    try:
        reservation_data = _build_reservation_payload(candidate)
        reservation = Reservation.model_validate(reservation_data)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("예약 데이터 검증 실패", exc_info=exc)
        state["intent"] = "예약"
        state["response"] = "예약 정보가 올바르지 않습니다. 다시 입력해 주세요."
        return state

    available = await is_room_available(
        room_id=reservation.meeting_room_id,
        start_time=reservation.start_time,
        end_time=reservation.end_time,
    )
    if not available:
        state["intent"] = "예약"
        state["response"] = "❌ 이미 해당 시간대에 예약이 존재합니다. 다른 시간대를 알려주세요."
        return state

    payload = reservation.model_dump()
    try:
        reservation_id = await reserve_room(payload)
    except ValueError as exc:
        state["intent"] = "예약"
        state["response"] = f"예약을 생성하지 못했습니다: {exc}"
        return state
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("예약 생성 중 예상치 못한 오류", exc_info=exc)
        state["intent"] = "예약"
        state["response"] = "예약 처리 중 오류가 발생했습니다."
        return state

    _clear_pending(state)
    state["reservation_candidate"] = {}
    state["intent"] = "예약"
    state["response"] = (
        f"✅ 회의실 {reservation.meeting_room_id} 예약이 완료되었습니다.\n"
        f"제목: {reservation.title}\n"
        f"시간: {reservation.start_time:%Y-%m-%d %H:%M}~{reservation.end_time:%H:%M}\n"
        f"예약 ID: {reservation_id}"
    )
    return state


async def cancel_node(state: AgentState) -> AgentState:
    """예약 취소 처리를 수행한다."""

    candidate: dict[str, Any] = dict(state.get("reservation_candidate") or {})
    reservation_raw = state.get("reservation")
    if isinstance(reservation_raw, Mapping):
        candidate.update(_normalize_reservation_dict(reservation_raw))

    _coerce_candidate_types(candidate)

    if state.get("pending_field") == "id" and state.get("pending_intent") == "취소":
        answer = state.get("user_input", "")
        value, error = _parse_field_value("id", answer)
        if error:
            state["intent"] = "취소"
            state["response"] = error
            state["reservation_candidate"] = candidate
            return state
        candidate["id"] = value
        _clear_pending(state)

    state["reservation_candidate"] = candidate

    if not _field_filled(candidate, "id"):
        return _set_pending_question(state, "id", "취소", FIELD_QUESTION_CANCEL_ID)

    reservation_id = candidate.get("id")
    reason = candidate.get("description")

    affected = await cancel_reservation(reservation_id, reason)
    state["intent"] = "취소"
    if affected == 0:
        state["response"] = "취소할 예약이 존재하지 않거나 이미 취소되었습니다."
    else:
        state["response"] = f"✅ 예약 {reservation_id} 취소가 완료되었습니다."

    state["reservation_candidate"] = {}
    _clear_pending(state)
    return state


async def update_node(state: AgentState) -> AgentState:
    """예약 정보를 수정한다."""

    candidate: dict[str, Any] = dict(state.get("reservation_candidate") or {})
    updates_tracker: set[str] = set(candidate.get("_updates", []))
    base_data: dict[str, Any] | None = candidate.get("_base") if isinstance(candidate.get("_base"), dict) else None

    reservation_raw = state.get("reservation")
    if isinstance(reservation_raw, Mapping):
        candidate.update(_normalize_reservation_dict(reservation_raw))

    _coerce_candidate_types(candidate)

    if state.get("pending_intent") == "수정":
        pending_field = state.get("pending_field")
        answer = state.get("user_input", "")
        if pending_field == "id":
            value, error = _parse_field_value("id", answer)
            if error:
                state["intent"] = "수정"
                state["response"] = error
                state["reservation_candidate"] = candidate
                return state
            candidate["id"] = value
            try:
                detail = await get_reservation(value)
            except ValueError as exc:
                state["intent"] = "수정"
                state["response"] = f"예약 정보를 조회하지 못했습니다: {exc}"
                candidate.pop("id", None)
                state["reservation_candidate"] = candidate
                return state

            normalized = _normalize_reservation_dict(detail)
            base_data = {
                "start_time": _coerce_datetime(normalized.get("start_time")),
                "end_time": _coerce_datetime(normalized.get("end_time")),
                "meeting_room_id": normalized.get("meeting_room_id"),
                "title": normalized.get("title"),
            }
            candidate["_base"] = base_data
            candidate["_updates"] = list(updates_tracker)
            state["reservation_candidate"] = candidate
            return _set_pending_question(state, "update_detail", "수정", FIELD_QUESTION_UPDATE_DETAIL)

        if pending_field == "update_detail":
            updates, error = await _parse_update_answer(answer, existing=base_data)
            if error:
                state["intent"] = "수정"
                state["response"] = error
                state["reservation_candidate"] = candidate
                return state
            if updates:
                candidate.update(updates)
                updates_tracker.update(updates.keys())
                if isinstance(base_data, dict):
                    for key, value in updates.items():
                        if key in {"start_time", "end_time", "meeting_room_id", "title"}:
                            base_data[key] = value
                    candidate["_base"] = base_data
                candidate["_updates"] = list(updates_tracker)
                state["reservation_candidate"] = candidate
                state["intent"] = "수정"
                state["response"] = "다른 변경 사항이 있으면 자연어로 말씀해 주세요. 없다면 '없음'이라고 답해주세요."
                state["pending_field"] = "update_detail"
                state["pending_intent"] = "수정"
                return state

            if not updates_tracker:
                state["intent"] = "수정"
                state["response"] = "최소 한 개 이상의 변경 항목이 필요합니다. 예: 시작 시간을 오후 3시로, 참석자를 4명으로 변경해줘"
                state["reservation_candidate"] = candidate
                return state

            _clear_pending(state)

    candidate["_updates"] = list(updates_tracker)
    state["reservation_candidate"] = candidate

    if not candidate.get("id"):
        return _set_pending_question(state, "id", "수정", FIELD_QUESTION_UPDATE_ID)

    if not updates_tracker:
        return _set_pending_question(state, "update_detail", "수정", FIELD_QUESTION_UPDATE_DETAIL)

    payload = {
        field: candidate[field]
        for field in updates_tracker
        if field in candidate and _field_filled(candidate, field)
    }

    try:
        result = await update_reservation(candidate["id"], payload)
    except ValueError as exc:
        state["intent"] = "수정"
        state["response"] = f"예약을 수정하지 못했습니다: {exc}"
        return state
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("예약 수정 중 예상치 못한 오류", exc_info=exc)
        state["intent"] = "수정"
        state["response"] = "예약 수정 중 오류가 발생했습니다."
        return state

    _clear_pending(state)
    state["reservation_candidate"] = {}
    state["intent"] = "수정"
    state["response"] = f"✅ 예약 {candidate['id']} 수정이 완료되었습니다."
    state["reservations"] = [result]
    return state


async def recommend_node(state: AgentState) -> AgentState:
    """요건에 맞는 회의실 후보를 추천한다."""

    # ===== 🆕 LLM 기반 자연어 파싱 =====
    user_input = state.get("user_input", "")
    
    # 1. LLM으로 자연어에서 정보 추출
    parsed = await _parse_with_llm(user_input)
    
    # 2. 상태에서 기본값 가져오기 (LLM 파싱 결과 우선)
    start_time = _coerce_datetime(state.get("start_time")) or parsed["start_time"]
    end_time = _coerce_datetime(state.get("end_time")) or parsed["end_time"]
    capacity = state.get("participants") or state.get("capacity") or parsed["participants"] or 2
    require_video = bool(state.get("require_video")) or parsed["require_video"]
    building = state.get("building") or parsed["building"]

    # 3. reservation 객체 확인 (최우선)
    request = state.get("reservation")
    if isinstance(request, Reservation):
        start_time = request.start_time or start_time
        end_time = request.end_time or end_time
        capacity = request.participants or capacity
        require_video = require_video or bool(request.description and "화상" in request.description)
    elif isinstance(request, dict):
        start_time = _coerce_datetime(request.get("start_time")) or start_time
        end_time = _coerce_datetime(request.get("end_time")) or end_time
        capacity = request.get("participants") or capacity
        require_video = bool(request.get("require_video")) or require_video
        building = request.get("building") or building

    # 4. 검증
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        state["response"] = (
            "추천을 위해 시작/종료 시각이 필요합니다.\n"
            "(예: 오늘 10시, 내일 오후 2시, 다음주 월요일 오전)"
        )
        return state

    # 5. 회의실 추천
    try:
        rooms = await recommend_meeting_rooms(
            start_time=start_time,
            end_time=end_time,
            capacity=capacity,
            require_video=require_video,
            building=building,
        )
        
        if not rooms:
            state["response"] = (
                f"조건에 맞는 회의실을 찾지 못했습니다.\n"
                f"검색 조건: {start_time:%Y-%m-%d %H:%M}~{end_time:%H:%M}, "
                f"{capacity}명, 화상: {'필요' if require_video else '불필요'}"
            )
        else:
            lines = [
                f"📅 {start_time:%Y-%m-%d %H:%M}~{end_time:%H:%M} 추천 회의실:",
                *[
                    f"- {room['name']} (인원: {room['capacity']}명, 화상: {'가능' if room['has_video'] else '불가'})"
                    for room in rooms
                ],
            ]
            state["response"] = "\n".join(lines)
            state["recommended_rooms"] = rooms
    except Exception as e:
        # 데이터베이스 연결 오류 등 예외 처리
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"회의실 추천 중 오류 발생: {e}", exc_info=True)
        
        # 사용자에게 친화적인 메시지 제공
        state["response"] = (
            f"⚠️ 데이터베이스 연결 오류로 인해 회의실 정보를 조회할 수 없습니다.\n"
            f"요청하신 조건: {start_time:%Y-%m-%d %H:%M}~{end_time:%H:%M}, "
            f"{capacity}명, 화상: {'필요' if require_video else '불필요'}\n\n"
            f"시스템 관리자에게 문의해주세요."
        )
        state["recommended_rooms"] = []
    
    return state


async def lookup_node(state: AgentState) -> AgentState:
    """자연어 기반 예약 조회."""

    candidate: dict[str, Any] = dict(state.get("lookup_candidate") or {})

    for field in ("start_time", "end_time"):
        if field not in candidate:
            value = state.get(field)
            if value:
                dt = _coerce_datetime(value)
                if dt:
                    candidate[field] = dt
    if "meeting_room_id" not in candidate and state.get("meeting_room_id") is not None:
        candidate["meeting_room_id"] = state.get("meeting_room_id")
    if "building" not in candidate and state.get("building"):
        candidate["building"] = state.get("building")

    lookup_raw = state.get("reservation")
    if isinstance(lookup_raw, Mapping):
        candidate.update(_normalize_lookup_dict(lookup_raw))

    _coerce_candidate_types(candidate)

    if not (state.get("pending_field") and state.get("pending_intent") == "조회"):
        parsed = await _parse_with_llm(state.get("user_input", ""))
        if parsed["start_time"] and "start_time" not in candidate:
            candidate["start_time"] = parsed["start_time"]
        if parsed["end_time"] and "end_time" not in candidate:
            candidate["end_time"] = parsed["end_time"]

    if state.get("pending_intent") == "조회":
        pending_field = state.get("pending_field")
        answer = state.get("user_input", "")
        if pending_field:
            value, error = _parse_field_value(pending_field, answer)
            if error:
                state["intent"] = "조회"
                state["response"] = error
                state["lookup_candidate"] = candidate
                return state
            candidate[pending_field] = value
            _clear_pending(state)

    state["lookup_candidate"] = candidate

    missing = [field for field in LOOKUP_REQUIRED_FIELDS if not _field_filled(candidate, field)]
    if missing:
        field = missing[0]
        question = FIELD_QUESTIONS_LOOKUP[field]
        return _set_pending_question(state, field, "조회", question)

    start_time = candidate["start_time"]
    end_time = candidate["end_time"]
    meeting_room_id = candidate.get("meeting_room_id")
    building = candidate.get("building")
    status_filter = candidate.get("status")

    reservations = await fetch_reservations(
        meeting_room_id=meeting_room_id,
        building=building,
        start_time=start_time,
        end_time=end_time,
    )
    if status_filter:
        target = status_filter.strip().upper()
        reservations = [r for r in reservations if str(r.get("status", "")).upper() == target]

    state["intent"] = "조회"
    if not reservations:
        state["response"] = (
            f"{start_time:%Y-%m-%d %H:%M}부터 {end_time:%H:%M}까지 예약이 없습니다."
        )
        state["lookup_candidate"] = {}
        return state

    lines = [
        f"📅 {start_time:%Y-%m-%d %H:%M} ~ {end_time:%H:%M} 예약 현황"
    ]
    for entry in reservations:
        lines.append(
            f"- ID {entry['id']}: {entry['title']} / {entry['start_time']}~{entry['end_time']} / "
            f"{entry['name']} (건물: {entry['building']})"
        )
    lines.append("💡 예약 ID로 수정이나 취소를 요청할 수 있습니다.")

    state["response"] = "\n".join(lines)
    state["reservations"] = reservations
    state["lookup_candidate"] = {}
    _clear_pending(state)
    return state