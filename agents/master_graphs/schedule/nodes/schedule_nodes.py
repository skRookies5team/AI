"""일정 관리 노드 - Slot Manager Pattern."""

from __future__ import annotations

from typing import Any, Dict, Optional, Literal, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import re
import json
from sqlalchemy import text

from agents.state import OrchestratorState
from agents.master_graphs.schedule.services.hybrid_scheduler import HybridScheduler
from agents.master_graphs.schedule.services.database_service import DatabaseService
from agents.master_graphs.schedule.utils.date_parser import parse_korean_date_with_llm
from agents.master_graphs.schedule.utils.entity_parser import (
    extract_schedule_entities,
    extract_interviewer_entities
)
from agents.master_graphs.schedule.services.google_calendar_service import GoogleAuthRequired
from agents.master_graphs.schedule.models.schedule_models import ScheduleSlotDecision
from agents.master_graphs.schedule.utils.schedule_slot_manager import analyze_schedule_slots
from agents.master_graphs.schedule.utils.prompt_loader import (
    get_sub_agent_prompt,
    format_prompt_template
)


_scheduler = None

# 일정 등록 필수 정보 정의
CREATION_SLOT_ORDER = ["title", "start_time", "end_time", "description", "destination"]
CREATION_SLOT_DESCRIPTIONS = {
    "title": "일정 제목",
    "start_time": "시작 일시 (예: 2025-12-13 14:00)",
    "end_time": "종료 일시 (예: 2025-12-13 15:00)",
    "description": "일정 설명",
    "destination": "장소 (선택사항)",
}


def _get_scheduler() -> HybridScheduler:
    """HybridScheduler 싱글톤 인스턴스 반환."""
    global _scheduler
    if _scheduler is None:
        _scheduler = HybridScheduler()
    return _scheduler


@dataclass
class ParsedScheduleIntent:
    """파싱된 일정 의도."""
    action: Literal["조회", "생성", "삭제", "수정"]
    title: Optional[str] = None
    description: Optional[str] = None
    destination: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    user_id: Optional[str] = None
    event_id: Optional[str] = None


async def process_schedule_request(state: OrchestratorState) -> Dict[str, Any]:
    """일정 요청 처리 - Slot Manager Pattern."""
    query = state.get("query", "")
    user_id = state.get("user_id") or state.get("employee_id")
    
    # 면접 일정 조정인지 확인 (기존 기능과 분리)
    # 1. 쿼리에 면접 키워드가 있는지 확인
    interview_keywords = ["면접", "면접관", "면접 일정", "면접 일정 조정", "면접일정", "면접일정잡아줘"]
    if any(keyword in query for keyword in interview_keywords):
        print(f"[Schedule Node] 면접 일정 조정 감지: query='{query}'")
        result = await process_interview_scheduling(state)
        print(f"[Schedule Node] 면접 일정 처리 결과: {list(result.keys()) if result else 'None'}")
        return result
    
    # 2. 면접 컨텍스트가 있는지 확인 (이전 대화에서 면접 일정 조정이 시작된 경우)
    # Redis 기반 컨텍스트 초기화
    if state.get("schedule_context") is None:
        state["schedule_context"] = {}
    schedule_context = state.get("schedule_context") or {}
    interview_context = schedule_context.get("interview") or {}
    
    # 면접 컨텍스트 확인 (더 강화된 체크)
    has_interview_context = bool(interview_context)
    if has_interview_context:
        # 면접 컨텍스트가 있으면 면접 처리로 이어가기
        print(f"[Schedule Node] 면접 컨텍스트 감지: 기간 입력 또는 면접관 선택 단계, query='{query}'")
        print(f"[Schedule Node] 면접 컨텍스트 내용: {interview_context}")
        result = await process_interview_scheduling(state)
        print(f"[Schedule Node] 면접 일정 처리 결과: {list(result.keys()) if result else 'None'}")
        return result
    
    # 3. 면접 관련 키워드가 없지만, 이전 대화에서 면접 일정 조정이 시작되었을 수 있음
    # conversation_history에서 확인
    conversation_history = state.get("conversation_history", [])
    if conversation_history:
        # 최근 대화에서 면접 관련 키워드 확인
        recent_messages = conversation_history[-5:]  # 최근 5개 메시지 확인
        for msg in recent_messages:
            msg_content = (msg.get("content") or msg.get("message") or "").lower()
            if any(keyword in msg_content for keyword in interview_keywords):
                print(f"[Schedule Node] 최근 대화에서 면접 키워드 발견: {msg_content[:50]}")
                # 면접 컨텍스트 초기화 후 면접 처리
                if state["schedule_context"].get("interview") is None:
                    state["schedule_context"]["interview"] = {}
                result = await process_interview_scheduling(state)
                print(f"[Schedule Node] 면접 일정 처리 결과: {list(result.keys()) if result else 'None'}")
                return result
    
    # 일반 일정 처리
    if state["schedule_context"].get("schedule") is None:
        state["schedule_context"]["schedule"] = {}
    if state.get("schedule_context") is None:
        state["schedule_context"] = {}
    if state["schedule_context"].get("schedule") is None:
        state["schedule_context"]["schedule"] = {}
    
    schedule_context = state["schedule_context"]["schedule"]
    
    print(f"[Schedule Node] 쿼리: {query}, user_id: {user_id}")
    
    # 1. slot_state 및 awaiting_slots 추출
    slot_state_ctx = schedule_context.get("slot_state") or {}
    awaiting_slots_ctx = schedule_context.get("awaiting_slots") or []
    
    # 2. 액션 결정
    inferred_action = _determine_action(query)
    if awaiting_slots_ctx and slot_state_ctx.get("intent"):
        action = slot_state_ctx["intent"]
        should_reset_context = False
    else:
        action = inferred_action
        should_reset_context = True
    
    # 3. 컨텍스트 리셋 여부 판단
    if should_reset_context:
        _maybe_reset_schedule_context(schedule_context, action, query)
    
    # 4. slot_state_payload 구성
    slot_state_payload = _build_slot_state_payload(action, schedule_context)
    
    # 5. LLM 슬롯 분석
    slot_decision = await analyze_schedule_slots(query, slot_state_payload)
    
    # 6. action 정규화
    normalized_action = _map_decision_action(slot_decision.action, action) if slot_decision else action
    
    # 7. ParsedIntent 생성
    parsed = await _parse_schedule_intent(
        query, user_id, normalized_action,
        schedule_context, awaiting_slots_ctx, slot_decision, slot_state_payload, state
    )
    
    # 8. slot_decision 적용
    followup_question = _apply_slot_decision(
        parsed, slot_decision, schedule_context, slot_state_payload, query
    )
    
    # 8.5. 일정 생성 시 초기 프롬프트 표시 (회의실 패턴)
    if parsed.action == "생성":
        initial_missing = _collect_creation_missing(parsed, schedule_context)
        slot_state_ctx = schedule_context.get("slot_state") or {}
        slot_values = slot_state_ctx.get("slots") or {}
        previous_awaiting = slot_state_payload.get("awaiting_slots") or []
        previous_slots = slot_state_payload.get("slots") or {}
        
        has_existing_slot_progress = bool(
            previous_awaiting
            or any(value not in (None, "", [], {}) for value in previous_slots.values())
            or any(value not in (None, "", [], {}) for value in slot_values.values())
        )
        
        if initial_missing and not has_existing_slot_progress:
            initial_prompt = _build_initial_creation_prompt(initial_missing)
            slot_state_ctx.setdefault("intent", parsed.action)
            slot_state_ctx.setdefault("slots", {})
            slot_state_ctx["missing_slots"] = initial_missing
            schedule_context["slot_state"] = slot_state_ctx
            schedule_context["awaiting_slots"] = initial_missing
            schedule_context["last_question"] = initial_prompt
            state["last_domain"] = "schedule"
            state["domain"] = "schedule"
            
            return {
                "final_response": initial_prompt,
                "messages": [initial_prompt],
                "schedule_context": state["schedule_context"],
            }
    
    # 8.6. 일정 삭제 시 기간 입력 확인 (초기)
    if parsed.action == "삭제":
        slot_state_ctx = schedule_context.get("slot_state") or {}
        slots = slot_state_ctx.get("slots") or {}
        previous_awaiting = slot_state_payload.get("awaiting_slots") or []
        
        # 삭제 선택 대기 중이 아니고, 기간 정보가 없으면 기간 입력 요청
        if "delete_selection" not in previous_awaiting:
            start_date = parsed.start_time or _parse_iso_datetime(slots.get("start_time"))
            end_date = parsed.end_time or _parse_iso_datetime(slots.get("end_time"))
            
            if not start_date or not end_date:
                schedule_context["awaiting_slots"] = ["start_time", "end_time"]
                schedule_context["last_question"] = "삭제할 일정의 기간을 알려주세요.\n예: 오늘부터 다음주까지, 12월 1일부터 12월 31일까지"
                state["last_domain"] = "schedule"
                state["domain"] = "schedule"
                
                return {
                    "final_response": "삭제할 일정의 기간을 알려주세요.\n예: 오늘부터 다음주까지, 12월 1일부터 12월 31일까지",
                    "messages": ["삭제할 일정의 기간을 알려주세요.\n예: 오늘부터 다음주까지, 12월 1일부터 12월 31일까지"],
                    "schedule_context": state["schedule_context"],
                }
    
    # 8.7. 일정 조회 시 기간 입력 확인 (초기)
    if parsed.action == "조회":
        slot_state_ctx = schedule_context.get("slot_state") or {}
        slots = slot_state_ctx.get("slots") or {}
        previous_awaiting = slot_state_payload.get("awaiting_slots") or []
        
        # 기간 정보가 없으면 기간 입력 요청
        start_date = parsed.start_time or _parse_iso_datetime(slots.get("start_time"))
        end_date = parsed.end_time or _parse_iso_datetime(slots.get("end_time"))
        
        if not start_date or not end_date:
            schedule_context["awaiting_slots"] = ["start_time", "end_time"]
            schedule_context["last_question"] = "조회할 일정의 기간을 알려주세요.\n예: 이번주, 다음주, 12월 1일부터 12월 31일까지"
            state["last_domain"] = "schedule"
            state["domain"] = "schedule"
            
            return {
                "final_response": "조회할 일정의 기간을 알려주세요.\n예: 이번주, 다음주, 12월 1일부터 12월 31일까지",
                "messages": ["조회할 일정의 기간을 알려주세요.\n예: 이번주, 다음주, 12월 1일부터 12월 31일까지"],
                "schedule_context": state["schedule_context"],
            }
    
    # 9. followup이 있으면 즉시 반환
    if followup_question:
        schedule_context["last_question"] = followup_question
        state["last_domain"] = "schedule"
        state["domain"] = "schedule"
        
        return {
            "final_response": followup_question,
            "messages": [followup_question],
            "schedule_context": state["schedule_context"],
        }
    
    # 10. 액션 처리
    if parsed.action == "조회":
        result = await _handle_query(parsed, state)
    elif parsed.action == "생성":
        result = await _handle_create(parsed, state)
    elif parsed.action == "삭제":
        result = await _handle_delete(parsed, state, schedule_context)
    elif parsed.action == "수정":
        result = await _handle_update(parsed, state, schedule_context)
    else:
        result = await _handle_query(parsed, state)
    
    # 11. slot_state 저장
    _persist_slot_state(schedule_context, parsed)
    
    # 12. 응답 반환
    state["last_domain"] = "schedule"
    state["domain"] = "schedule"
    
    return {
        "final_response": result.get("message", "일정 처리가 완료되었습니다."),
        "messages": [result.get("message", "")],
        "schedule_context": state["schedule_context"],
    }


# ========== Utility Functions (Meeting Room Pattern) ==========

def _build_slot_state_payload(current_action: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Slot Manager에 전달할 payload 구성."""
    existing_state = context.get("slot_state") or {}
    return {
        "intent": existing_state.get("intent") or current_action,
        "slots": existing_state.get("slots") or {},
        "delete_candidates": context.get("delete_candidates") or [],
        "update_candidates": context.get("update_candidates") or [],
        "awaiting_slots": context.get("awaiting_slots") or [],
        "last_question": context.get("last_question"),
    }


def _apply_slot_decision(
    parsed: ParsedScheduleIntent,
    slot_decision: Optional[ScheduleSlotDecision],
    context: Dict[str, Any],
    slot_state_payload: Dict[str, Any],
    raw_query: str
) -> Optional[str]:
    """LLM 슬롯 결정을 컨텍스트에 적용."""
    if slot_decision is None:
        context.pop("awaiting_slots", None)
        return None
    
    # slots 병합
    merged_slots = dict(slot_state_payload.get("slots") or {})
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
        elif slot_name in ["title", "description", "destination"] and not str(merged_slots[slot_name]).strip():
            recalculated_missing.append(slot_name)
    
    context["slot_state"] = {
        "intent": parsed.action,
        "slots": merged_slots,
        "missing_slots": recalculated_missing,
    }
    
    # 여러 슬롯이 누락되었으면 한 번에 물어봄 (회의실 패턴)
    if recalculated_missing and slot_decision.should_ask:
        # 삭제 액션일 때는 기간만 물어봄 (title 등은 불필요)
        if parsed.action == "삭제":
            if "start_time" in recalculated_missing or "end_time" in recalculated_missing:
                context["awaiting_slots"] = ["start_time", "end_time"]
                context["last_question"] = "삭제할 일정의 기간을 알려주세요.\n예: 오늘부터 다음주까지, 12월 1일부터 12월 31일까지"
                return "삭제할 일정의 기간을 알려주세요.\n예: 오늘부터 다음주까지, 12월 1일부터 12월 31일까지"
            elif "delete_selection" in recalculated_missing:
                # 삭제 선택 대기 중
                return None
        # 조회 액션일 때도 기간만 물어봄
        elif parsed.action == "조회":
            if "start_time" in recalculated_missing or "end_time" in recalculated_missing:
                context["awaiting_slots"] = ["start_time", "end_time"]
                context["last_question"] = "조회할 일정의 기간을 알려주세요.\n예: 이번주, 다음주, 12월 1일부터 12월 31일까지"
                return "조회할 일정의 기간을 알려주세요.\n예: 이번주, 다음주, 12월 1일부터 12월 31일까지"
        # 생성 액션일 때는 복합 질문
        elif len(recalculated_missing) > 1 and parsed.action == "생성":
            slot_descriptions = [
                CREATION_SLOT_DESCRIPTIONS.get(slot, slot)
                for slot in recalculated_missing
                if slot != "destination"  # destination은 선택사항
            ]
            if slot_descriptions:
                combined_question = "다음 정보를 알려주세요:\n" + "\n".join(
                    f"- {desc}" for desc in slot_descriptions
                )
                context["awaiting_slots"] = recalculated_missing
                context["last_question"] = combined_question
                print(f"[복합 질문 생성] missing={recalculated_missing}")
                return combined_question
        elif slot_decision.followup_question:
            context["awaiting_slots"] = recalculated_missing
            context["last_question"] = slot_decision.followup_question
            return slot_decision.followup_question
    
    context.pop("awaiting_slots", None)
    return None


def _persist_slot_state(context: Dict[str, Any], parsed: ParsedScheduleIntent) -> None:
    """액션 완료 후 slot_state 저장."""
    slot_state = context.get("slot_state") or {}
    slot_state["intent"] = parsed.action
    slot_state["slots"] = _export_parsed_slots(parsed)
    if "missing_slots" not in slot_state:
        slot_state["missing_slots"] = []
    context["slot_state"] = slot_state


def _normalize_slot_value(slot_name: str, value: Any) -> Any:
    """슬롯 값 타입 정규화."""
    if value is None:
        return None
    
    if slot_name in ["start_time", "end_time"]:
        # 시간은 ISO 문자열로 유지
        if isinstance(value, str):
            return value
        elif isinstance(value, datetime):
            return value.isoformat()
    
    elif slot_name == "selection_index":
        return int(value)
    
    return value


def _export_parsed_slots(parsed: ParsedScheduleIntent) -> Dict[str, Any]:
    """ParsedIntent를 직렬화 가능한 dict로 변환."""
    return {
        "title": parsed.title,
        "description": parsed.description,
        "destination": parsed.destination,
        "start_time": parsed.start_time.isoformat() if parsed.start_time else None,
        "end_time": parsed.end_time.isoformat() if parsed.end_time else None,
        "event_id": parsed.event_id,
    }


def _map_decision_action(decision_action: str, fallback: str) -> str:
    """LLM 액션을 내부 액션으로 매핑."""
    mapping = {
        "조회": "조회",
        "생성": "생성",
        "삭제": "삭제",
        "수정": "수정",
        "유지": fallback,
    }
    return mapping.get(decision_action, fallback)


def _maybe_reset_schedule_context(context: Dict[str, Any], action: str, query: str) -> None:
    """명시적 의도 변경 시 컨텍스트 리셋."""
    action_keywords = {
        "조회": ["조회", "찾아", "보여"],
        "생성": ["생성", "등록", "추가"],
        "삭제": ["삭제", "취소"],
        "수정": ["수정", "변경"],
    }
    
    current_intent = context.get("slot_state", {}).get("intent")
    if current_intent and current_intent != action:
        for kw in action_keywords.get(action, []):
            if kw in query:
                # 명시적으로 다른 액션을 요청하면 모든 컨텍스트 정리
                context.pop("slot_state", None)
                context.pop("awaiting_slots", None)
                context.pop("delete_candidates", None)  # 삭제 후보도 정리
                context.pop("update_candidates", None)  # 수정 후보도 정리
                break
    # 현재 액션이 "삭제"가 아니고 delete_candidates가 있으면 정리 (다른 작업으로 전환)
    elif action != "삭제" and context.get("delete_candidates"):
        context.pop("delete_candidates", None)
        context.pop("awaiting_slots", None)


def _extract_selection_index(query: str) -> Optional[int]:
    """사용자 입력에서 선택 번호 추출."""
    patterns = [
        r'(\d+)\s*번',
        r'(\d+)\s*번째',
        r'^(\d+)$',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, query.strip())
        if match:
            return int(match.group(1))
    
    korean_numbers = {
        "첫": 1, "하나": 1,
        "둘": 2, "이": 2,
        "셋": 3, "삼": 3,
    }
    
    for korean, num in korean_numbers.items():
        if korean in query:
            return num
    
    return None


def _collect_creation_missing(parsed: ParsedScheduleIntent, context: Dict[str, Any]) -> List[str]:
    """일정 생성에 필요한 필수 슬롯 중 비어 있는 항목을 수집한다."""
    slot_state = context.get("slot_state") or {}
    slots = slot_state.get("slots") or {}
    
    missing: List[str] = []
    
    # 제목 확인
    title_candidate = (parsed.title or "").strip()
    if not title_candidate:
        title_candidate = (slots.get("title") or "").strip()
    if not title_candidate:
        missing.append("title")
    
    # 시작 시간 확인
    if not parsed.start_time and not slots.get("start_time"):
        missing.append("start_time")
    
    # 종료 시간 확인
    if not parsed.end_time and not slots.get("end_time"):
        missing.append("end_time")
    
    # 설명 확인 (선택사항이지만 권장)
    description_candidate = (parsed.description or "").strip()
    if not description_candidate:
        description_candidate = (slots.get("description") or "").strip()
    if not description_candidate:
        missing.append("description")
    
    return missing


def _build_initial_creation_prompt(missing_slots: List[str]) -> str:
    """일정 생성을 위한 초기 프롬프트를 생성한다."""
    # YAML에서 템플릿 로드
    template = get_sub_agent_prompt("schedule", "initial_creation_template")
    
    # 누락된 슬롯 목록 생성
    missing_slots_list = "\n".join([
        f"- {CREATION_SLOT_DESCRIPTIONS.get(slot_name, slot_name)}"
        for slot_name in CREATION_SLOT_ORDER
        if slot_name in missing_slots
    ])
    
    # 템플릿 포맷팅
    return format_prompt_template(template, missing_slots_list=missing_slots_list)


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """ISO 문자열을 datetime으로 안전하게 변환한다."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def _to_rfc3339(dt: datetime) -> str:
    """datetime을 RFC3339 형식(Google Calendar API 호환)으로 변환한다."""
    if dt.tzinfo is None:
        # naive datetime은 로컬 시간으로 간주
        local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
        dt = dt.replace(tzinfo=local_tz)
    
    # UTC로 변환
    utc_dt = dt.astimezone(ZoneInfo("UTC"))
    # Z 포맷으로 변환 (타임존 정보 제거하고 Z 추가)
    return utc_dt.strftime('%Y-%m-%dT%H:%M:%S') + "Z"


# ========== Action Handlers ==========

async def _parse_schedule_intent(
    query: str,
    user_id: Optional[str],
    action: str,
    context: Dict[str, Any],
    awaiting_slots: Optional[List[str]],
    slot_decision: Optional[ScheduleSlotDecision],
    slot_state_payload: Dict[str, Any],
    state: Optional[OrchestratorState] = None
) -> ParsedScheduleIntent:
    """쿼리에서 일정 의도 파싱."""
    existing_slots = slot_state_payload.get("slots", {})
    awaiting_set = set(awaiting_slots or [])
    
    # 엔티티 추출
    if action == "생성":
        entities = await extract_schedule_entities(query)
        
        # 쉼표로 구분된 입력 처리 (예: "제목, 설명, 장소")
        if ("title" in awaiting_set or "description" in awaiting_set or "destination" in awaiting_set) and "," in query:
            parts = [part.strip() for part in query.split(",")]
            idx = 0
            if "title" in awaiting_set and idx < len(parts):
                entities["title"] = parts[idx]
                print(f"[쉼표 파싱] title: {parts[idx]}")
                idx += 1
            if "description" in awaiting_set and idx < len(parts):
                entities["description"] = parts[idx]
                print(f"[쉼표 파싱] description: {parts[idx]}")
                idx += 1
            if "destination" in awaiting_set and idx < len(parts):
                entities["destination"] = parts[idx]
                print(f"[쉼표 파싱] destination: {parts[idx]}")
                idx += 1
    else:
        entities = {}
    
    # 날짜 파싱
    start_time = None
    end_time = None
    
    if existing_slots.get("start_time"):
        start_time = datetime.fromisoformat(existing_slots["start_time"].replace("Z", "+00:00"))
    if existing_slots.get("end_time"):
        end_time = datetime.fromisoformat(existing_slots["end_time"].replace("Z", "+00:00"))
    
    # 날짜 정보가 있는 쿼리면 파싱 (생성, 삭제, 조회 모두)
    if not awaiting_slots or action in ["생성", "삭제", "조회"]:
        # 기간 입력 대기 중이거나 아직 기간이 없으면 파싱 시도
        if action == "삭제" and awaiting_set and ("start_time" in awaiting_set or "end_time" in awaiting_set):
            start_date_obj, end_date_obj, _ = await parse_korean_date_with_llm(query)
            if start_date_obj:
                start_time = start_date_obj
                # 삭제는 기간이 필요하므로 end_time 기본값 설정
                if end_date_obj:
                    end_time = end_date_obj
                elif not end_time:
                    end_time = start_date_obj + timedelta(days=7)  # 기본 7일
        elif action == "조회" and awaiting_set and ("start_time" in awaiting_set or "end_time" in awaiting_set):
            start_date_obj, end_date_obj, _ = await parse_korean_date_with_llm(query)
            if start_date_obj:
                start_time = start_date_obj
                # 조회도 기간이 필요하므로 end_time 기본값 설정
                if end_date_obj:
                    end_time = end_date_obj
                elif not end_time:
                    end_time = start_date_obj + timedelta(days=7)  # 기본 7일
        elif action == "생성" or not awaiting_slots:
            start_date_obj, end_date_obj, _ = await parse_korean_date_with_llm(query)
            if start_date_obj:
                start_time = start_date_obj
                end_time = end_date_obj or (start_date_obj + timedelta(hours=1))
    
    # 선택 인덱스
    selection_index = None
    if awaiting_slots and ("delete_selection" in awaiting_slots or "update_selection" in awaiting_slots):
        selection_index = _extract_selection_index(query)
    # 삭제 액션이고 delete_candidates가 있으면 선택 인덱스 추출 시도
    elif action == "삭제" and context.get("delete_candidates"):
        selection_index = _extract_selection_index(query)
        print(f"[삭제 선택 인덱스 추출] query='{query}', index={selection_index}")
    
    # destination 우선순위: entities > workflow_temp_data.destination > existing_slots
    destination_value = entities.get("destination")
    if not destination_value and state:
        workflow_in_progress = state.get("workflow_in_progress", False)
        workflow_type = state.get("workflow_type")
        workflow_temp_data = state.get("workflow_temp_data") or {}
        
        if workflow_in_progress and workflow_type == "business_trip":
            destination_value = workflow_temp_data.get("destination")
            if destination_value:
                print(f"✅ [워크플로우 모드] workflow_temp_data에서 destination 사용: {destination_value}")
    
    if not destination_value:
        destination_value = existing_slots.get("destination")
    
    return ParsedScheduleIntent(
        action=action,
        title=entities.get("title") or existing_slots.get("title"),
        description=entities.get("description") or existing_slots.get("description"),
        destination=destination_value,
        start_time=start_time,
        end_time=end_time,
        user_id=str(user_id) if user_id else None,
        event_id=str(selection_index) if selection_index else existing_slots.get("event_id"),
    )


async def _handle_query(
    parsed: ParsedScheduleIntent,
    state: OrchestratorState
) -> Dict[str, Any]:
    """일정 조회."""
    scheduler = _get_scheduler()
    schedule_context = state["schedule_context"]["schedule"]
    
    # 기간 확인
    start_date = parsed.start_time
    end_date = parsed.end_time
    
    # 기간이 없으면 이전 단계에서 이미 요청했으므로 에러
    if not start_date or not end_date:
        return {
            "ok": False,
            "message": "일정 조회를 위한 기간 정보가 필요합니다."
        }
    
    try:
        if parsed.user_id:
            try:
                events = await scheduler.get_events(
                    user_id=str(parsed.user_id),
                    start_date=_to_rfc3339(start_date),
                    end_date=_to_rfc3339(end_date)
                )
            except GoogleAuthRequired as auth_error:
                return {
                    "ok": False,
                    "message": f"🔐 Google 캘린더 연동이 필요합니다.\n{auth_error.auth_url}"
                }
            
            schedule_context["last_results"] = events
            
            if not events:
                message = f"{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')} 기간에 일정이 없습니다."
            else:
                message = f"📅 {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')} 일정 ({len(events)}건):\n\n"
                
                for idx, event in enumerate(events[:10], 1):
                    title = event.get('title', '제목 없음')
                    start = event.get('start_time', '')
                    destination = event.get('location', '') or event.get('destination', '')  # DB에서는 location으로 저장됨
                    
                    message += f"{idx}. {title}\n   🕐 {start}\n"
                    if destination:
                        message += f"   🏢 {destination}\n"
                    message += "\n"
                
                if len(events) > 10:
                    message += f"... 외 {len(events) - 10}건"
            
            return {"ok": True, "message": message}
        else:
            return {"ok": False, "message": "사용자 인증이 필요합니다."}
    
    except Exception as e:
        print(f"❌ 일정 조회 실패: {e}")
        return {"ok": False, "message": f"일정 조회 중 오류: {str(e)}"}


async def _handle_create(
    parsed: ParsedScheduleIntent,
    state: OrchestratorState
) -> Dict[str, Any]:
    """일정 생성."""
    scheduler = _get_scheduler()
    
    start_time = parsed.start_time or (datetime.now() + timedelta(days=1)).replace(hour=14, minute=0)
    end_time = parsed.end_time or (start_time + timedelta(hours=1))
    
    # employee_id 추출 (state에서 가져오거나 user_id에서 변환)
    employee_id = state.get("employee_id")
    if not employee_id and parsed.user_id:
        try:
            employee_id = int(parsed.user_id)
        except (ValueError, TypeError):
            employee_id = None
    
    event_data = {
        'title': parsed.title or '새 일정',
        'description': parsed.description or '',
        'start_time': start_time.isoformat(),
        'end_time': end_time.isoformat(),
        'location': parsed.destination or '',  # 외부 API는 location 사용
        'attendees': [],
        'schedule_type': 'general',
        'employee_id': employee_id,
    }
    
    try:
        try:
            created_event = await scheduler.create_event(
                user_id=str(parsed.user_id),
                event_data=event_data
            )
        except GoogleAuthRequired as auth_error:
            return {"ok": False, "message": f"🔐 Google 캘린더 인증이 필요합니다: {auth_error.auth_url}"}
        
        if created_event:
            return {
                "ok": True,
                "message": f"✅ 일정이 생성되었습니다!\n\n📌 {event_data['title']}\n🕐 {start_time.strftime('%Y-%m-%d %H:%M')}"
            }
        
        return {"ok": False, "message": "일정 생성에 실패했습니다."}
    
    except Exception as e:
        print(f"❌ 일정 생성 실패: {e}")
        return {"ok": False, "message": f"일정 생성 중 오류: {str(e)}"}


async def _handle_delete(
    parsed: ParsedScheduleIntent,
    state: OrchestratorState,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """일정 삭제."""
    scheduler = _get_scheduler()
    slot_state = context.get("slot_state") or {}
    slots = slot_state.get("slots") or {}
    
    # event_id가 숫자면 선택 인덱스
    if parsed.event_id and parsed.event_id.isdigit():
        selection = int(parsed.event_id)
        candidates = context.get("delete_candidates", [])
        
        if 1 <= selection <= len(candidates):
            selected = candidates[selection - 1]
            event_id = selected.get('gcal_event_id')
            
            try:
                success = await scheduler.delete_event(
                    user_id=str(parsed.user_id),
                    event_id=event_id
                )
            except GoogleAuthRequired as auth_error:
                return {"ok": False, "message": f"🔐 Google 캘린더 인증 필요: {auth_error.auth_url}"}
            
            if success:
                # 삭제된 항목만 제거하고 나머지는 유지 (연속 삭제 지원)
                context["delete_candidates"] = [
                    c for c in candidates if c.get('gcal_event_id') != event_id
                ]
                # delete_candidates가 비어있으면 완전히 정리
                if not context["delete_candidates"]:
                    context.pop("delete_candidates", None)
                    context.pop("awaiting_slots", None)
                    context.pop("slot_state", None)
                # 아직 삭제할 항목이 있으면 awaiting_slots는 유지 (연속 삭제 가능)
                # 사용자가 다른 작업을 명시적으로 요청하면 _maybe_reset_schedule_context에서 정리됨
                
                return {
                    "ok": True,
                    "message": f"✅ 일정이 삭제되었습니다.\n\n- {selected.get('title')}\n- {selected.get('start_time')}"
                }
        
        return {"ok": False, "message": "올바른 번호를 선택해주세요."}
    
    # 기간 확인 - 슬롯에서 가져오거나 파싱된 값 사용
    start_date = parsed.start_time or _parse_iso_datetime(slots.get("start_time"))
    end_date = parsed.end_time or _parse_iso_datetime(slots.get("end_time"))
    
    # 기간이 없으면 이전 단계에서 이미 요청했으므로 에러
    if not start_date or not end_date:
        return {
            "ok": False,
            "message": "일정 삭제를 위한 기간 정보가 필요합니다."
        }
    
    # 후보 목록 조회
    try:
        try:
            events = await scheduler.get_events(
                user_id=str(parsed.user_id),
                start_date=_to_rfc3339(start_date),
                end_date=_to_rfc3339(end_date)
            )
        except GoogleAuthRequired as auth_error:
            return {"ok": False, "message": f"🔐 Google 캘린더 인증 필요: {auth_error.auth_url}"}
        
        if not events:
            return {"ok": False, "message": f"{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')} 기간에 삭제 가능한 일정이 없습니다."}
        
        # 제목으로 필터링 시도 (사용자가 특정 일정을 언급한 경우)
        filtered_events = events
        if parsed.title:
            title_lower = parsed.title.lower()
            filtered_events = [
                e for e in events
                if title_lower in (e.get('title', '') or '').lower()
            ]
            # 필터링 결과가 1개면 바로 삭제 확인
            if len(filtered_events) == 1:
                selected = filtered_events[0]
                event_id = selected.get('gcal_event_id')
                
                try:
                    success = await scheduler.delete_event(
                        user_id=str(parsed.user_id),
                        event_id=event_id
                    )
                except GoogleAuthRequired as auth_error:
                    return {"ok": False, "message": f"🔐 Google 캘린더 인증 필요: {auth_error.auth_url}"}
                
                if success:
                    context.pop("delete_candidates", None)
                    context.pop("awaiting_slots", None)
                    context.pop("slot_state", None)
                    return {
                        "ok": True,
                        "message": f"✅ '{selected.get('title')}' 일정이 삭제되었습니다.\n\n📅 {selected.get('start_time')}"
                    }
            # 필터링 결과가 여러 개면 선택 요청
            elif len(filtered_events) > 1:
                context["delete_candidates"] = filtered_events[:10]
                context["awaiting_slots"] = ["delete_selection"]
                
                message = f"📋 '{parsed.title}' 일정이 여러 개 있습니다. 삭제할 일정을 선택해주세요:\n\n"
                for idx, event in enumerate(filtered_events[:10], 1):
                    message += f"{idx}. {event.get('title', '제목 없음')}\n   🕐 {event.get('start_time')}\n\n"
                
                if len(filtered_events) > 10:
                    message += f"... 외 {len(filtered_events) - 10}건\n\n"
                
                message += "❗ 삭제할 일정의 번호만 입력해주세요. (예: 2)"
                
                return {"ok": False, "message": message}
        
        # 필터링 결과가 없거나 제목이 없으면 전체 목록 표시
        if not filtered_events:
            filtered_events = events
        
        context["delete_candidates"] = filtered_events[:10]
        context["awaiting_slots"] = ["delete_selection"]
        
        message = f"📋 삭제 가능한 일정 목록 ({start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}):\n\n"
        for idx, event in enumerate(filtered_events[:10], 1):
            message += f"{idx}. {event.get('title', '제목 없음')}\n   🕐 {event.get('start_time')}\n\n"
        
        if len(filtered_events) > 10:
            message += f"... 외 {len(filtered_events) - 10}건\n\n"
        
        message += "❗ 삭제할 일정의 번호만 입력해주세요. (예: 2)"
        
        return {"ok": False, "message": message}
    
    except Exception as e:
        print(f"❌ 일정 삭제 목록 조회 실패: {e}")
        return {"ok": False, "message": f"일정 삭제 처리 중 오류: {str(e)}"}


async def _handle_update(
    parsed: ParsedScheduleIntent,
    state: OrchestratorState,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """일정 수정."""
    scheduler = _get_scheduler()
    
    # 선택된 항목이 있으면 수정 실행
    if "selected_event_for_update" in context:
        selected = context["selected_event_for_update"]
        event_id = selected.get('gcal_event_id')
        
        entities = await extract_schedule_entities(context.get("last_update_query", ""))
        start_date, end_date, _ = await parse_korean_date_with_llm(context.get("last_update_query", ""))
        
        update_data = {}
        
        if entities.get('title'):
            update_data['title'] = entities['title']
        if entities.get('destination'):
            update_data['location'] = entities['destination']  # 외부 API는 location 사용
        if start_date:
            update_data['start_time'] = start_date.isoformat()
            update_data['end_time'] = (end_date or (start_date + timedelta(hours=1))).isoformat()
        
        if not update_data:
            return {"ok": False, "message": "수정할 내용을 찾을 수 없습니다."}
        
        try:
            updated_event = await scheduler.update_event(
                user_id=str(parsed.user_id),
                event_id=event_id,
                event_data=update_data
            )
        except GoogleAuthRequired as auth_error:
            return {"ok": False, "message": f"🔐 Google 캘린더 인증 필요: {auth_error.auth_url}"}
        
        if updated_event:
            context.pop("update_candidates", None)
            context.pop("selected_event_for_update", None)
            context.pop("awaiting_slots", None)
            
            return {
                "ok": True,
                "message": f"✅ 일정이 수정되었습니다.\n\n수정된 내용:\n" +
                          (f"- 제목: {update_data.get('title')}\n" if 'title' in update_data else "") +
                          (f"- 시간: {update_data.get('start_time')}\n" if 'start_time' in update_data else "")
            }
        
        return {"ok": False, "message": "일정 수정에 실패했습니다."}
    
    # 첫 단계: 선택 요청
    if parsed.event_id and parsed.event_id.isdigit():
        selection = int(parsed.event_id)
        candidates = context.get("update_candidates", [])
        
        if 1 <= selection <= len(candidates):
            selected = candidates[selection - 1]
            context["selected_event_for_update"] = selected
            
            return {
                "ok": False,
                "message": f"선택된 일정:\n- {selected.get('title')}\n- {selected.get('start_time')}\n\n변경할 내용을 입력해주세요."
            }
    
    # 후보 목록 조회
    try:
        start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=30)
        
        try:
            events = await scheduler.get_events(
                user_id=str(parsed.user_id),
                start_date=_to_rfc3339(start_date),
                end_date=_to_rfc3339(end_date)
            )
        except GoogleAuthRequired as auth_error:
            return {"ok": False, "message": f"🔐 Google 캘린더 인증 필요: {auth_error.auth_url}"}
        
        if not events:
            return {"ok": False, "message": "수정할 수 있는 일정이 없습니다."}
        
        context["update_candidates"] = events[:10]
        context["awaiting_slots"] = ["update_selection"]
        
        message = "📋 수정 가능한 일정 목록:\n\n"
        for idx, event in enumerate(events[:10], 1):
            message += f"{idx}. {event.get('title', '제목 없음')}\n   🕐 {event.get('start_time')}\n\n"
        
        message += "\n수정할 일정 번호를 입력해주세요. (예: 1)"
        
        return {"ok": False, "message": message}
    
    except Exception as e:
        print(f"❌ 일정 수정 목록 조회 실패: {e}")
        return {"ok": False, "message": f"일정 수정 처리 중 오류: {str(e)}"}


def _determine_action(query: str) -> str:
    """키워드 기반 액션 결정 (fallback용)."""
    if any(k in query for k in ["등록", "생성", "추가", "만들", "예약"]):
        return "생성"
    elif any(k in query for k in ["삭제", "취소", "지워", "제거"]):
        return "삭제"
    elif any(k in query for k in ["수정", "변경", "바꿔", "고쳐"]):
        return "수정"
    else:
        return "조회"


async def find_available_interview_slots(
    interviewer_ids: List[int],
    start_date: datetime,
    end_date: datetime,
    duration_minutes: int = 60,
    working_hours: tuple = (9, 18)  # 오전 9시 ~ 오후 6시
) -> List[Dict[str, Any]]:
    """면접관들이 모두 가능한 시간대를 찾습니다.
    
    Args:
        interviewer_ids: 면접관 employee_id 리스트
        start_date: 조회 시작일
        end_date: 조회 종료일
        duration_minutes: 면접 소요 시간 (분)
        working_hours: 근무 시간 (시작 시간, 종료 시간)
        
    Returns:
        가능한 시간대 리스트 [{"start": datetime, "end": datetime, "date": str, "time": str}, ...]
    """
    from agents.master_graphs.schedule.services.database_service import DatabaseService
    
    db = DatabaseService()
    
    # ✅ 1. 면접관들의 일정 조회 (DB만 사용, 구글 캘린더 제외)
    schedules_by_interviewer = await db.get_interviewer_schedules(
        interviewer_ids=interviewer_ids,
        start_date=start_date,
        end_date=end_date
    )
    
    # ✅ 2. 구글 캘린더 조회 부분 제거 (면접 일정은 DB만 사용)
    
    # 3. 각 면접관의 일정을 시간대 리스트로 변환
    busy_slots_by_interviewer: Dict[int, List[tuple]] = {}
    for emp_id, schedules in schedules_by_interviewer.items():
        busy_slots = []
        for schedule in schedules:
            start = schedule.get("start_time")
            end = schedule.get("end_time")
            if start and end:
                if isinstance(start, str):
                    try:
                        start = datetime.fromisoformat(start.replace('Z', '+00:00'))
                    except:
                        continue
                if isinstance(end, str):
                    try:
                        end = datetime.fromisoformat(end.replace('Z', '+00:00'))
                    except:
                        continue
                # timezone 제거
                if start.tzinfo:
                    start = start.replace(tzinfo=None)
                if end.tzinfo:
                    end = end.replace(tzinfo=None)
                busy_slots.append((start, end))
        busy_slots_by_interviewer[emp_id] = sorted(busy_slots)
    
    # 4. 가능한 시간대 찾기
    available_slots = []
    current_date = start_date.date() if isinstance(start_date, datetime) else start_date
    end_date_only = end_date.date() if isinstance(end_date, datetime) else end_date
    
    # start_date와 end_date가 datetime이면 date로 변환
    if isinstance(start_date, datetime):
        current_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date_only = end_date.date()
    
    while current_date <= end_date_only:
        # 근무 시간 내에서 30분 단위로 체크
        start_hour, end_hour = working_hours
        current_time = datetime.combine(current_date, datetime.min.time().replace(hour=start_hour))
        day_end = datetime.combine(current_date, datetime.min.time().replace(hour=end_hour))
        
        while current_time + timedelta(minutes=duration_minutes) <= day_end:
            slot_start = current_time
            slot_end = current_time + timedelta(minutes=duration_minutes)
            
            # 모든 면접관이 이 시간대에 가능한지 확인
            all_available = True
            for emp_id in interviewer_ids:
                busy_slots = busy_slots_by_interviewer.get(emp_id, [])
                for busy_start, busy_end in busy_slots:
                    # 시간대가 겹치는지 확인
                    if slot_start < busy_end and slot_end > busy_start:
                        all_available = False
                        break
                if not all_available:
                    break
            
            if all_available:
                available_slots.append({
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                    "date": current_date.isoformat(),
                    "time": slot_start.strftime("%H:%M")
                })
            
            # 30분씩 증가
            current_time += timedelta(minutes=30)
        
        current_date += timedelta(days=1)
    
    return available_slots


async def process_interview_scheduling(state: OrchestratorState) -> Dict[str, Any]:
    """면접 일정 조정 처리."""
    try:
        query = state.get("query", "").strip()

        # ⚠️ 취소/종료 요청 처리 (최우선)
        cancel_keywords = ["취소", "종료", "그만", "stop", "cancel"]
        if any(k == query.lower() for k in cancel_keywords):
            print(f"🛑 [Interview] 워크플로우 취소 요청 감지: {query}")
            return {
                "final_response": "워크플로우가 취소되었습니다. 모든 임시 데이터가 초기화됩니다.",
                "messages": ["워크플로우 취소"],
                "workflow_in_progress": False,
                "workflow_type": None,
                "workflow_steps": None,
                "current_workflow_step_index": None,
                "workflow_temp_data": {},  # 임시 데이터 초기화
                "schedule_context": {"interview": {}}, # 컨텍스트 초기화
                "requires_input": False
            }

        user_id = state.get("user_id") or state.get("employee_id")
        employee_id = state.get("employee_id")
        
        # 면접 컨텍스트 초기화 (반드시 생성)
        if state.get("schedule_context") is None:
            state["schedule_context"] = {}
        if state["schedule_context"].get("interview") is None:
            state["schedule_context"]["interview"] = {}
        
        interview_context = state["schedule_context"]["interview"]
        print(f"[면접 일정] process_interview_scheduling 호출: query='{query}', interview_context={interview_context}")
        
        # 🔥 워크플로우 모드 확인 및 데이터 주입
        workflow_in_progress = state.get("workflow_in_progress", False)
        workflow_type = state.get("workflow_type")
        workflow_temp_data = state.get("workflow_temp_data", {}) or {}
        selected_candidate = workflow_temp_data.get("selected_candidate")
        
        # ✅ [추가] Recruitment -> Schedule 전환 시 쿼리 정규화 처리
        # Orchestrator에서 query를 "면접 일정"으로 바꿔서 넘겨준 경우, 컨텍스트를 초기화하여 처음부터 시작
        is_transition_query = query == "면접 일정"
        if workflow_in_progress and workflow_type == "recruitment" and is_transition_query:
            print(f"[면접 일정] 워크플로우 전환 감지 - 컨텍스트 초기화 후 새로 시작")
            interview_context = {}
            state["schedule_context"]["interview"] = {}

        if workflow_in_progress and workflow_type == "recruitment" and selected_candidate:
            if not interview_context.get("candidate_id"):
                print(f"[면접 일정] 워크플로우 데이터 감지 - 후보자 정보 주입: {selected_candidate.get('name')}")
                interview_context["candidate_id"] = selected_candidate.get("resume_id")
                interview_context["candidate_name"] = selected_candidate.get("name")
                # 워크플로우 시작 시 기간 입력을 유도하기 위해 requires_date_range 설정
                if not interview_context.get("date_range"):
                    interview_context["requires_date_range"] = True
                state["schedule_context"]["interview"] = interview_context
        
        # 🔥 새로운 면접 일정 요청인지 확인 (면접 키워드가 있고 기간이 없으면 초기화)
        interview_keywords = ["면접", "면접관", "면접 일정", "면접 일정 조정", "면접일정", "면접일정잡아줘"]
        is_new_request = any(keyword in query.lower() for keyword in interview_keywords)
        date_range = interview_context.get("date_range", {})
        has_date_range = bool(date_range.get("start") and date_range.get("end"))
        
        # 새로운 요청이고 기간이 없으면 컨텍스트 초기화
        if is_new_request and not has_date_range:
            print(f"[면접 일정] 새로운 면접 일정 요청 감지 - 컨텍스트 초기화")
            interview_context = {}  # 기간과 면접관 정보 초기화
            state["schedule_context"]["interview"] = interview_context
        
        # 1단계: 정보 추출 (면접관 및 기간)
        selected_interviewers = interview_context.get("selected_interviewers", [])
        date_range = interview_context.get("date_range", {})
        start_date_str = date_range.get("start")
        end_date_str = date_range.get("end")
        
        print(f"[면접 일정] 초기 상태 확인: selected_interviewers={selected_interviewers}, start_date={start_date_str}")

        # 쿼리에서 면접관 정보 추출 시도 (JSON 또는 자연어)
        if not selected_interviewers and query:
            # 1. JSON 형식(프론트엔드 UI)으로 전달된 경우 파싱 시도 (Regex로 더 견고하게 추출)
            json_match = re.search(r'(\[.*\]|\{.*\})', query, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).strip()
                try:
                    parsed_data = json.loads(json_str)
                    print(f"[면접 일정] JSON 추출 성공: {json_str[:50]}...")
                    
                    if isinstance(parsed_data, list):
                        selected_interviewers = [item.get("employee_id") if isinstance(item, dict) else item for item in parsed_data]
                    elif isinstance(parsed_data, dict):
                        if "employee_id" in parsed_data:
                            selected_interviewers = [parsed_data["employee_id"]]
                        elif "interviewers" in parsed_data:
                            ivs = parsed_data["interviewers"]
                            if isinstance(ivs, list):
                                selected_interviewers = [item.get("employee_id") if isinstance(item, dict) else item for item in ivs]
                    
                    if selected_interviewers:
                        interview_context["selected_interviewers"] = selected_interviewers
                        state["schedule_context"]["interview"] = interview_context
                        print(f"[면접 일정] 면접관 ID 파싱 성공 (JSON): {selected_interviewers}")
                    else:
                        print(f"[면접 일정] JSON에서 면접관 ID를 찾지 못함: {parsed_data}")
                except Exception as e:
                    print(f"[면접 일정] JSON 파싱 중 오류 발생: {e}, text='{json_str[:50]}...'")
            
            # 2. 자연어 입력인 경우 (NLP 추출) - JSON 파싱 실패 시에만 수행
            if not selected_interviewers:
                print(f"[면접 일정] 자연어 엔티티 추출 시도 (query='{query[:50]}...')")
                interviewer_info = await extract_interviewer_entities(query)
                
                if interviewer_info and interviewer_info.get("interviewers"):
                    print(f"✅ [면접 일정] 자연어에서 면접관 정보 추출 완료: {interviewer_info['interviewers']}")
                    
                    # 사용자 요구사항: 이름, 부서, 직급을 임시 state에 업로드
                    workflow_temp_data = state.get("workflow_temp_data", {}) or {}
                    workflow_temp_data["interviewers_detail"] = interviewer_info["interviewers"]
                    state["workflow_temp_data"] = workflow_temp_data
                    
                    # 이름 기반으로 employee_id 조회 시도
                    db = DatabaseService()
                    found_ids = []
                    
                    async with db._session() as session:
                        for inv in interviewer_info["interviewers"]:
                            name = inv.get("name")
                            if not name: continue
                            sql = text("SELECT employee_id FROM employee WHERE name = :name LIMIT 1")
                            result = await session.execute(sql, {"name": name})
                            row = result.fetchone()
                            if row: found_ids.append(row[0])
                    
                    if found_ids:
                        selected_interviewers = found_ids
                        interview_context["selected_interviewers"] = selected_interviewers
                        state["schedule_context"]["interview"] = interview_context
                        print(f"✅ [면접 일정] DB에서 면접관 ID 매칭 성공: {selected_interviewers}")
        
        # 쿼리에서 기간 추출 시도
        if (not start_date_str or not end_date_str) and query:
            # 쿼리가 JSON인 경우(면접관 선택 데이터)는 날짜 파싱 제외
            # Regex로 다시 확인 (안전장치)
            is_json = bool(re.search(r'(\[.*\]|\{.*\})', query, re.DOTALL))
            if not is_json:
                print(f"[면접 일정] 기간 파싱 시도: query='{query}'")
                current_datetime = state.get("current_datetime")
                if current_datetime:
                    try:
                        current_dt = datetime.fromisoformat(current_datetime.replace('Z', '+00:00'))
                    except:
                        current_dt = None
                else:
                    current_dt = None
                
                parsed_start, parsed_end, _ = await parse_korean_date_with_llm(query, current_datetime=current_dt)
                if parsed_start and parsed_end:
                    start_date_str = parsed_start.isoformat()
                    end_date_str = parsed_end.isoformat()
                    interview_context["date_range"] = {
                        "start": start_date_str,
                        "end": end_date_str
                    }
                    state["schedule_context"]["interview"] = interview_context
                    print(f"[면접 일정] 기간 파싱 성공: {start_date_str} ~ {end_date_str}")
            else:
                print(f"[면접 일정] JSON 데이터 감지 - 기간 파싱 건너뜀")

        # 2단계: 필수 정보 누락 확인 및 요청
        
        # (A) 면접관 정보가 없는 경우
        if not selected_interviewers:
            if workflow_in_progress and workflow_type == "recruitment":
                message = (
                    "**[면접 일정 설정]**\n"
                    "먼저 면접에 참여할 **면접관 정보**(이름, 부서, 직급)를 입력해주세요. \n"
                    "아래 **[면접관 선택]** 버튼을 눌러 직접 검색하거나, 채팅창에 정보를 입력해 주셔도 됩니다.\n\n"
                    "동시에 면접을 진행할 **희망 기간**(예: 다음 주, 1월 5일~10일)도 함께 말씀해 주세요."
                )
                interview_context["requires_interviewer_selection"] = True
                interview_context["interviewer_selection_config"] = {
                    "api_endpoint": "/api/employees",
                    "filter_fields": ["dept", "position"],
                    "multiple": True
                }
            else:
                message = "면접관을 선택하거나 이름을 입력해주세요."
            
            interview_context["requires_date_range"] = True
            state["schedule_context"]["interview"] = interview_context
            
            return {
                "final_response": message,
                "messages": [message],
                "schedule_context": state["schedule_context"],
                "requires_date_range_input": True,
                "requires_interviewer_selection": True,
                "interviewer_selection_config": interview_context.get("interviewer_selection_config"),
                "requires_slot_selection": False,
                "workflow_in_progress": workflow_in_progress,
                "workflow_type": workflow_type,
                "requires_input": True
            }

        # (B) 면접관은 있는데 기간 정보가 없는 경우
        if not start_date_str or not end_date_str:
            if workflow_in_progress and workflow_type == "recruitment":
                message = (
                    f"✅ 면접관({len(selected_interviewers)}명) 설정이 완료되었습니다.\n"
                    "이제 면접을 진행할 **희망 기간**(예: 다음 주, 1월 5일~10일)을 입력해주세요."
                )
            else:
                message = "면접 가능한 기간을 알려주세요. (예: 다음 주, 12월 20일부터 25일까지)"
            
            interview_context["requires_date_range"] = True
            state["schedule_context"]["interview"] = interview_context
            
            return {
                "final_response": message,
                "messages": [message],
                "schedule_context": state["schedule_context"],
                "requires_date_range_input": True,
                "requires_interviewer_selection": False, # ✅ 면접관 선택 완료되었으므로 UI 끔
                "requires_slot_selection": False,
                "workflow_in_progress": workflow_in_progress,
                "workflow_type": workflow_type,
                "requires_input": True
            }
        
        # 3단계: 지속 시간 확인 (기존 4단계)
        duration_minutes = interview_context.get("duration_minutes")
        print(f"[면접 일정] 지속 시간 확인: duration_minutes={duration_minutes}, query='{query}'")
        
        # 쿼리에서 지속 시간 파싱 시도 (1시간, 2시간, 60분, 120분 등)
        if not duration_minutes and query:
            # "1시간", "2시간", "1.5시간", "60분", "120분" 등 파싱
            hour_match = re.search(r'(\d+(?:\.\d+)?)\s*시간', query)
            minute_match = re.search(r'(\d+)\s*분', query)
            
            if hour_match:
                hours = float(hour_match.group(1))
                duration_minutes = int(hours * 60)
                print(f"[면접 일정] 지속 시간 파싱 성공 (시간): {hours}시간 = {duration_minutes}분")
            elif minute_match:
                duration_minutes = int(minute_match.group(1))
                print(f"[면접 일정] 지속 시간 파싱 성공 (분): {duration_minutes}분")
            else:
                # JSON 형식으로 전달된 경우 (프론트엔드에서 선택한 지속 시간)
                try:
                    if query.startswith("{") and "duration" in query.lower():
                        parsed_data = json.loads(query)
                        if isinstance(parsed_data, dict) and "duration_minutes" in parsed_data:
                            duration_minutes = int(parsed_data["duration_minutes"])
                            print(f"[면접 일정] 지속 시간 파싱 성공 (JSON): {duration_minutes}분")
                except:
                    pass
            
            # 파싱 성공 시 컨텍스트에 저장
            if duration_minutes:
                interview_context["duration_minutes"] = duration_minutes
                interview_context.pop("requires_duration_selection", None)  # ✅ 지속 시간 선택 플래그 제거
                state["schedule_context"]["interview"] = interview_context
                print(f"[면접 일정] 지속 시간 저장: {duration_minutes}분")
        
        # 지속 시간이 없으면 선택 UI 요청
        if not duration_minutes:
            message = "면접 소요 시간을 선택해주세요."
            interview_context["requires_duration_selection"] = True
            # ✅ 면접관 선택 플래그 명시적으로 제거 (이미 선택됨)
            interview_context.pop("requires_interviewer_selection", None)
            state["schedule_context"]["interview"] = interview_context
            print(f"[면접 일정] 지속 시간 선택 UI 요청, 면접 컨텍스트 저장: {interview_context}")
            return {
                "final_response": message,
                "messages": [message],
                "schedule_context": state["schedule_context"],
                "workflow_temp_data": state.get("workflow_temp_data", {}),  # ✅ 추가
                "requires_date_range_input": True,  # ✅ 추가 (기간은 이미 입력됨)
                "requires_interviewer_selection": False,  # ✅ 추가 (면접관은 이미 선택됨)
                "requires_duration_selection": True,  # ✅ 추가 - 지속 시간 선택!
                "requires_slot_selection": False,  # ✅ 추가
                "workflow_in_progress": workflow_in_progress,
                "workflow_type": workflow_type,
                "requires_input": True  # ✅ 추가
            }
        
        # 5단계: 면접관들의 일정 조회 및 가능한 시간대 찾기
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')) if isinstance(start_date_str, str) else start_date_str
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')) if isinstance(end_date_str, str) else end_date_str
        
        # timezone 정보 제거 (DB 조회용)
        if start_date.tzinfo:
            start_date = start_date.replace(tzinfo=None)
        if end_date.tzinfo:
            end_date = end_date.replace(tzinfo=None)
        
        try:
            available_slots = await find_available_interview_slots(
                interviewer_ids=selected_interviewers,
                start_date=start_date,
                end_date=end_date,
                duration_minutes=duration_minutes  # ✅ 사용자가 선택한 지속 시간 사용
            )
        # ✅ GoogleAuthRequired 예외 처리 제거 (면접은 구글 캘린더 사용 안 함)
        except Exception as e:
            # 기타 오류도 진행 불가
            message = f"면접 일정 조회 중 오류가 발생했습니다: {str(e)}"
            return {
                "final_response": message,
                "messages": [message],
                "schedule_context": state["schedule_context"],
                "workflow_temp_data": state.get("workflow_temp_data", {}),  # ✅ 추가
                "requires_date_range_input": True,
                "requires_interviewer_selection": False,
                "requires_duration_selection": False,
                "requires_slot_selection": False,
                "workflow_in_progress": workflow_in_progress,
                "workflow_type": workflow_type,
            }
        
        if not available_slots:
            message = "선택하신 면접관들의 일정이 모두 겹쳐서 가능한 시간대를 찾을 수 없습니다. 다른 기간을 선택해주세요."
            # 🔥 여기도 수정! - UI 플래그 추가
            return {
                "final_response": message,
                "messages": [message],
                "schedule_context": state["schedule_context"],
                "workflow_temp_data": state.get("workflow_temp_data", {}),  # ✅ 추가
                "requires_date_range_input": True,  # ✅ 추가
                "requires_interviewer_selection": False,  # ✅ 추가 (면접관은 이미 선택됨)
                "requires_slot_selection": False,  # ✅ 추가
                "workflow_in_progress": workflow_in_progress,
                "workflow_type": workflow_type,
            }
        
        # 6단계: 선택된 시간대 확인
        selected_slot = interview_context.get("selected_slot")
        print(f"[면접 일정] 선택된 시간대 확인: selected_slot={selected_slot}, query='{query}'")
        
        # 쿼리에서 시간대 선택 정보 파싱 (프론트엔드에서 전달된 경우)
        if not selected_slot and query:
            try:
                if query.startswith("{") and ("slot" in query.lower() or "start" in query.lower()):
                    parsed_data = json.loads(query)
                    if isinstance(parsed_data, dict):
                        # slot_index가 있는 경우 available_slots에서 찾기
                        if "slot_index" in parsed_data:
                            slot_index = int(parsed_data["slot_index"])
                            # available_slots는 컨텍스트에 저장되어 있음
                            context_slots = interview_context.get("available_slots", available_slots)
                            if 0 <= slot_index < len(context_slots):
                                selected_slot = context_slots[slot_index]
                                print(f"[면접 일정] 시간대 선택 파싱 성공 (인덱스): {slot_index}, slot={selected_slot}")
                        elif "start" in parsed_data and "end" in parsed_data:
                            selected_slot = {
                                "start": parsed_data["start"],
                                "end": parsed_data["end"]
                            }
                            print(f"[면접 일정] 시간대 선택 파싱 성공 (직접): {selected_slot}")
            except Exception as e:
                print(f"[면접 일정] 시간대 파싱 오류: {str(e)}")
                pass
        
        # 시간대가 선택되었으면 일정 생성
        if selected_slot:
            print(f"[면접 일정] 시간대 선택됨, 일정 생성 시작: {selected_slot}")
            try:
                db = DatabaseService()
                
                # 선택된 시간대 정보
                slot_start = datetime.fromisoformat(selected_slot["start"].replace('Z', '+00:00'))
                slot_end = datetime.fromisoformat(selected_slot["end"].replace('Z', '+00:00'))
                
                # timezone 제거
                if slot_start.tzinfo:
                    slot_start = slot_start.replace(tzinfo=None)
                if slot_end.tzinfo:
                    slot_end = slot_end.replace(tzinfo=None)
                
                # ✅ 면접 일정 생성 (DB만 사용, 구글 캘린더 제외)
                # 다른 기능(회의실, 출장 등)은 hybrid_scheduler.create_event 사용
                created_events = []
                async with db._session() as session:
                    try:
                        # ✅ 하나의 행만 생성 (면접관 리스트를 interviewer_ids에 저장)
                        # 면접관 ID 리스트를 JSON 배열로 변환
                        interviewer_ids_json = json.dumps(selected_interviewers)  # [4000, 4001]
                        
                        # DB에 직접 저장 (구글 캘린더 제외)
                        insert_sql = text("""
                            INSERT INTO schedule (
                                employee_id, title, description, 
                                start_time, end_time, schedule_type,
                                interviewer_ids, status,
                                created_at, updated_at
                            ) VALUES (
                                :employee_id, :title, :description,
                                :start_time, :end_time, :schedule_type,
                                :interviewer_ids, :status,
                                NOW(), NOW()
                            )
                        """)
                        await session.execute(insert_sql, {
                            "employee_id": employee_id,  # ✅ 변경: 현재 채팅 중인 사용자 ID (작성자)
                            "title": "면접",
                            "description": f"면접 일정 (소요 시간: {duration_minutes}분)",
                            "start_time": slot_start,
                            "end_time": slot_end,
                            "schedule_type": "interview",  # ✅ 면접 일정으로 명시
                            "interviewer_ids": interviewer_ids_json,  # ✅ 면접관 리스트 (JSON)
                            "status": "active"
                        })
                        await session.commit()
                        
                        created_events.append({
                            "employee_id": employee_id,  # ✅ 변경: 현재 채팅 중인 사용자 ID
                            "start_time": slot_start,
                            "end_time": slot_end,
                            "interviewer_ids": selected_interviewers
                        })
                        print(f"[면접 일정] 일정 생성 성공 (면접관 {len(selected_interviewers)}명): DB에 저장됨")
                    except Exception as e:
                        print(f"[면접 일정] 일정 생성 실패: {str(e)}")
                        import traceback
                        traceback.print_exc()
                        await session.rollback()
                
                if created_events:
                    slot_date = slot_start.strftime("%Y년 %m월 %d일")
                    slot_time = slot_start.strftime("%H:%M")
                    message = f"면접 일정이 생성되었습니다.\n\n📅 일시: {slot_date} {slot_time}\n⏱️ 소요 시간: {duration_minutes}분\n👥 면접관: {len(selected_interviewers)}명"
                    
                    # 🔥 워크플로우 연동 처리
                    workflow_in_progress = state.get("workflow_in_progress", False)
                    workflow_type = state.get("workflow_type")
                    
                    if workflow_in_progress and workflow_type == "recruitment":
                        # 기존 workflow_temp_data를 안전하게 가져옴
                        workflow_temp_data = state.get("workflow_temp_data", {}) or {}
                        # ⚠️ 새로운 딕셔너리를 생성하여 기존 데이터가 유실되지 않도록 보장
                        workflow_temp_data = {**workflow_temp_data}
                        
                        # 면접관 상세 정보 추출
                        workflow_temp_data["interviewers"] = selected_interviewers
                        workflow_temp_data["interview_date"] = f"{slot_date} {slot_time}"
                        workflow_temp_data["interview_duration"] = duration_minutes
                        
                        message += "\n\n**[워크플로우]** 일정이 확정되었습니다. 이제 지원자에게 보낼 안내 메일을 작성하겠습니다."
                        
                        # 컨텍스트 초기화 (완료)
                        state["schedule_context"]["interview"] = {}
                        print(f"✅ [면접 일정] 워크플로우 임시 데이터 업데이트 완료: {list(workflow_temp_data.keys())}")
                        
                        return {
                            "final_response": message,
                            "messages": [message],
                            "schedule_context": state["schedule_context"],
                            "workflow_temp_data": workflow_temp_data,
                            "workflow_in_progress": True,
                            "workflow_type": workflow_type,
                            "current_workflow_step_index": 2, # Email 단계로
                            "next_domain": "email",
                            "requires_input": False # 메일 초안 생성을 위해 바로 진행
                        }
                    else:
                        # 일반 면접 일정 생성 완료 (워크플로우가 아닌 경우)
                        state["schedule_context"]["interview"] = {}
                        
                        return {
                            "final_response": message,
                            "messages": [message],
                            "schedule_context": state["schedule_context"],
                            "workflow_temp_data": state.get("workflow_temp_data", {}),
                            "requires_date_range_input": False,
                            "requires_interviewer_selection": False,
                            "requires_duration_selection": False,
                            "requires_slot_selection": False,
                        }
                else:
                    message = "면접 일정 생성에 실패했습니다. 다시 시도해주세요."
                    return {
                        "final_response": message,
                        "messages": [message],
                        "schedule_context": state["schedule_context"],
                        "workflow_temp_data": state.get("workflow_temp_data", {}),
                        "requires_date_range_input": True,
                        "requires_interviewer_selection": False,
                        "requires_duration_selection": False,
                        "requires_slot_selection": True,  # 다시 시간대 선택
                        "available_slots": available_slots[:20],
                    }
            except Exception as e:
                print(f"[면접 일정] 일정 생성 중 오류: {str(e)}")
                import traceback
                traceback.print_exc()
                message = f"면접 일정 생성 중 오류가 발생했습니다: {str(e)}"
                return {
                    "final_response": message,
                    "messages": [message],
                    "schedule_context": state["schedule_context"],
                    "workflow_temp_data": state.get("workflow_temp_data", {}),  # ✅ 추가
                    "requires_date_range_input": True,
                    "requires_interviewer_selection": False,
                    "requires_duration_selection": False,
                    "requires_slot_selection": True,
                    "available_slots": available_slots[:20],
                }
        
        # 7단계: 가능한 시간 후보군 반환
        interview_context["available_slots"] = available_slots
        interview_context["requires_slot_selection"] = True
        interview_context.pop("requires_duration_selection", None)  # ✅ 지속 시간 선택 플래그 제거 (이미 선택됨)
        interview_context["interviewers"] = selected_interviewers
        interview_context["duration_minutes"] = duration_minutes
        state["schedule_context"]["interview"] = interview_context
        
        # 후보군을 사용자 친화적인 형식으로 포맷팅
        slots_display = []
        for i, slot in enumerate(available_slots[:20], 1):  # 최대 20개만 표시
            slot_start = datetime.fromisoformat(slot["start"].replace('Z', '+00:00'))
            slot_date = slot_start.strftime("%Y년 %m월 %d일")
            slot_time = slot_start.strftime("%H:%M")
            slots_display.append(f"{i}. {slot_date} {slot_time}")
        
        message = f"면접 가능한 시간대 {len(available_slots)}개를 찾았습니다 (소요 시간: {duration_minutes}분):\n\n" + "\n".join(slots_display)
        if len(available_slots) > 20:
            message += f"\n\n(총 {len(available_slots)}개 중 20개만 표시됩니다)"
        
        # 🔥 여기도 수정! - UI 플래그 추가
        return {
            "final_response": message,
            "messages": [message],
            "schedule_context": state["schedule_context"],
            "workflow_temp_data": state.get("workflow_temp_data", {}),  # ✅ 추가
            "requires_date_range_input": True,  # ✅ 추가
            "requires_interviewer_selection": False,  # ✅ 추가 (면접관은 이미 선택됨)
            "requires_duration_selection": False,  # ✅ 추가 (지속 시간은 이미 선택됨)
            "requires_slot_selection": True,  # ✅ 추가 - 이제 시간대 선택!
            "available_slots": available_slots[:20],  # ✅ 추가 - 프론트엔드에서 사용할 슬롯 데이터
            "requires_input": True  # ✅ 추가
        }
    except Exception as e:
        print(f"[면접 일정] 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
        # 🔥 에러 시에도 UI 플래그 추가 및 HITL 유지
        return {
            "final_response": f"면접 일정 조정 중 오류가 발생했습니다: {str(e)}",
            "messages": [f"면접 일정 조정 중 오류가 발생했습니다: {str(e)}"],
            "ok": False,
            "schedule_context": state.get("schedule_context", {}),
            "workflow_temp_data": state.get("workflow_temp_data", {}),  # ✅ 추가
            "requires_input": True, # 🔥 오류 발생 시 다음 단계로 넘어가지 않고 대기
            "requires_interviewer_selection": True, # 면접관 선택 UI 다시 노출 유도
            "workflow_in_progress": state.get("workflow_in_progress", False),
            "workflow_type": state.get("workflow_type")
        }
