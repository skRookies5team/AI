"""출장 관리 노드 - Slot Manager Pattern."""

from __future__ import annotations

from typing import Any, Dict, Optional, Literal, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

from agents.state import OrchestratorState
from agents.master_graphs.schedule.services.hybrid_scheduler import HybridScheduler
from agents.master_graphs.schedule.utils.date_parser import parse_korean_date_with_llm
from agents.master_graphs.schedule.services.google_calendar_service import GoogleAuthRequired
from agents.master_graphs.schedule.models.business_trip_models import BusinessTripSlotDecision
from agents.structured_output import extract_structured_output

_scheduler = None

# 출장 신청 필수 정보 정의
APPLICATION_SLOT_ORDER = ["start_time", "end_time", "destination", "trip_purpose", "trip_title"]
APPLICATION_SLOT_DESCRIPTIONS = {
    "start_time": "출장 시작 일시 (예: 2025-01-15 09:00, 다음주 월요일 오전 9시)",
    "end_time": "출장 종료 일시 (예: 2025-01-17 18:00, 다음주 수요일 오후 6시)",
    "destination": "출장 목적지 (예: 제주도, 부산)",
    "trip_purpose": "출장 목적 (예: 워크샵, 고객 미팅)",
    "trip_title": "출장 제목 (선택, 없으면 자동 생성)",
}

# Slot Manager 프롬프트는 YAML에서 로드
from agents.master_graphs.schedule.utils.prompt_loader import get_sub_agent_prompt


def _get_business_trip_slot_manager_prompt() -> str:
    """출장 Slot Manager 프롬프트를 YAML에서 로드합니다."""
    return get_sub_agent_prompt("business_trip", "slot_manager")


def _get_scheduler() -> HybridScheduler:
    """HybridScheduler 싱글톤 인스턴스 반환."""
    global _scheduler
    if _scheduler is None:
        _scheduler = HybridScheduler()
    return _scheduler


@dataclass
class ParsedBusinessTripIntent:
    """파싱된 출장 의도."""
    action: Literal["조회", "신청", "내역", "취소", "수정"]
    employee_id: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    destination: Optional[str] = None
    trip_purpose: Optional[str] = None
    trip_title: Optional[str] = None
    trip_id: Optional[int] = None


async def process_business_trip_request(state: OrchestratorState) -> Dict[str, Any]:
    """출장 요청 처리 - Slot Manager Pattern + 워크플로우 모드 지원."""
    query = state.get("query", "")
    employee_id = state.get("employee_id")
    user_id = state.get("user_id") or str(employee_id) if employee_id else None
    
    # 워크플로우 진행 중인지 확인
    workflow_in_progress = state.get("workflow_in_progress", False)
    workflow_type = state.get("workflow_type")
    current_workflow_step_index = state.get("current_workflow_step_index", 0)
    
    # 워크플로우 모드이고 business_trip 워크플로우인 경우
    if workflow_in_progress and workflow_type == "business_trip":
        # 워크플로우 핸들러를 통한 단계 처리
        from agents.workflows import get_workflow_handler
        workflow_handler = get_workflow_handler(workflow_type)
        workflow_steps = state.get("workflow_steps", [])
        
        if workflow_handler:
            # 워크플로우 핸들러가 해당 단계를 처리할 수 있는지 확인
            # Step 0과 Step 3만 schedule 도메인에서 처리
            if current_workflow_step_index == 0:
                # Step 1: 일정 조회 및 날짜 확인
                return await _handle_workflow_step1_check_conflicts(state, query, employee_id, user_id)
            elif current_workflow_step_index == 3:
                # Step 4: 캘린더 등록 및 RDB 저장
                return await _handle_workflow_step4_register_calendar(state, query, employee_id, user_id)
            else:
                # Step 2 (document) 또는 Step 3 (email)는 다른 도메인에서 처리
                # 여기서는 오류 메시지 반환
                expected_domain = workflow_steps[current_workflow_step_index] if current_workflow_step_index < len(workflow_steps) else 'unknown'
                return {
                    "final_response": f"❌ 워크플로우 단계 오류: Step {current_workflow_step_index + 1}는 {expected_domain} 도메인에서 처리되어야 합니다.",
                    "messages": [f"워크플로우 단계 오류: Step {current_workflow_step_index + 1}"],
                    "schedule_context": state.get("schedule_context", {}),
                }
    
    # 일반 모드 (기존 로직)
    # Redis 기반 컨텍스트 초기화
    if state.get("schedule_context") is None:
        state["schedule_context"] = {}
    if state["schedule_context"].get("business_trip") is None:
        state["schedule_context"]["business_trip"] = {}
    
    business_trip_context = state["schedule_context"]["business_trip"]
    
    print(f"[Business Trip Node] 쿼리: {query}, employee_id: {employee_id}")
    
    # 1. employee_id 검증
    if not employee_id:
        return {
            "final_response": "출장 관리를 위해서는 사번(employee_id)이 필요합니다.",
            "messages": ["출장 관리를 위해서는 사번이 필요합니다."],
            "schedule_context": state["schedule_context"],
        }
    
    # 2. slot_state 및 awaiting_slots 추출
    slot_state_ctx = business_trip_context.get("slot_state") or {}
    awaiting_slots_ctx = business_trip_context.get("awaiting_slots") or []
    
    # 3. 액션 결정 (출장은 주로 신청)
    inferred_action = _determine_business_trip_action(query)
    if awaiting_slots_ctx and slot_state_ctx.get("intent"):
        # 새로운 액션이 이전 액션과 다르면 컨텍스트 리셋
        if inferred_action != slot_state_ctx.get("intent"):
            action = inferred_action
            should_reset_context = True
        else:
            action = slot_state_ctx["intent"]
            should_reset_context = False
    else:
        action = inferred_action
        should_reset_context = True
    
    # 4. 컨텍스트 리셋 여부 판단
    if should_reset_context:
        _maybe_reset_business_trip_context(business_trip_context, action, query)
    
    # 5. slot_state_payload 구성
    slot_state_payload = _build_slot_state_payload(action, business_trip_context)
    
    # 6. LLM 슬롯 분석
    slot_decision = await _analyze_business_trip_slots(query, slot_state_payload)
    
    # 7. action 정규화
    normalized_action = _map_decision_action(slot_decision.action, action) if slot_decision else action
    
    # 8. ParsedIntent 생성
    parsed = await _parse_business_trip_intent(
        query, employee_id, user_id, normalized_action,
        business_trip_context, awaiting_slots_ctx, slot_decision, slot_state_payload,
        state=state  # state 전달 추가
    )
    
    # 9. slot_decision 적용
    followup_question = _apply_slot_decision(
        parsed, slot_decision, business_trip_context, slot_state_payload, query
    )
    
    # 10. followup이 있으면 즉시 반환
    if followup_question:
        business_trip_context["last_question"] = followup_question
        state["last_domain"] = "schedule"
        state["domain"] = "schedule"
        
        return {
            "final_response": followup_question,
            "messages": [followup_question],
            "schedule_context": state["schedule_context"],
        }
    
    # 11. 액션 처리
    scheduler = _get_scheduler()
    
    if parsed.action == "조회":
        result = await _handle_query_conflicts(parsed, scheduler, user_id)
    elif parsed.action == "신청":
        result = await _handle_create_trip(parsed, user_id, scheduler)
    elif parsed.action == "내역":
        result = await _handle_query_history(parsed, scheduler)
    elif parsed.action == "취소":
        result = await _handle_cancel_trip(parsed, user_id, scheduler)
    else:
        result = {"ok": False, "message": "알 수 없는 액션입니다."}
    
    # 12. slot_state 저장
    _persist_slot_state(business_trip_context, parsed)
    
    # 13. 응답 반환
    message = result.get("message", "출장 처리가 완료되었습니다.")
    
    state["last_domain"] = "schedule"
    state["domain"] = "schedule"

    return {
        "final_response": message,
        "messages": [message],
        "schedule_context": state["schedule_context"],
    }


# ========== Utility Functions ==========

def _build_slot_state_payload(current_action: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Slot Manager에 전달할 payload 구성."""
    existing_state = context.get("slot_state") or {}
    return {
        "intent": existing_state.get("intent") or current_action,
        "slots": existing_state.get("slots") or {},
        "awaiting_slots": context.get("awaiting_slots") or [],
        "last_question": context.get("last_question"),
    }


async def _analyze_business_trip_slots(
    query: str,
    slot_state: Dict[str, Any]
) -> BusinessTripSlotDecision:
    """LLM을 사용하여 출장 슬롯을 분석."""
    try:
        # slot_state 정보를 system_prompt에 포함
        enhanced_prompt = f"""{_get_business_trip_slot_manager_prompt()}

현재 슬롯 상태:
{slot_state}"""
        
        result = await extract_structured_output(
            query=query,
            model_class=BusinessTripSlotDecision,
            system_prompt=enhanced_prompt,
            temperature=0.0,
        )
        return result
    except Exception as e:
        print(f"⚠️ 출장 슬롯 분석 실패: {e}")
        # 기본값 반환
        return BusinessTripSlotDecision(
            action="신청",
            filled_slots={},
            missing_slots=["start_time", "end_time"],
            should_ask=True
        )


def _apply_slot_decision(
    parsed: ParsedBusinessTripIntent,
    slot_decision: Optional[BusinessTripSlotDecision],
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
        elif slot_name in ["destination", "trip_purpose", "trip_title"] and not str(merged_slots[slot_name]).strip():
            recalculated_missing.append(slot_name)
    
    context["slot_state"] = {
        "intent": parsed.action,
        "slots": merged_slots,
        "missing_slots": recalculated_missing,
    }
    
    # 여러 슬롯이 누락되었으면 한 번에 물어봄
    if recalculated_missing and slot_decision.should_ask:
        # 필수 필드가 없으면 반드시 물어봄
        required_missing = [s for s in recalculated_missing if s in ["start_time", "end_time"]]
        if required_missing:
            slot_descriptions = [
                APPLICATION_SLOT_DESCRIPTIONS.get(slot, slot)
                for slot in required_missing
            ]
            if slot_descriptions:
                combined_question = "다음 정보를 알려주세요:\n" + "\n".join(
                    f"- {desc}" for desc in slot_descriptions
                )
                context["awaiting_slots"] = required_missing
                context["last_question"] = combined_question
                return combined_question
        
        # 권장 필드가 없으면 한 번 물어보고, 없어도 진행 가능
        recommended_missing = [s for s in recalculated_missing if s not in ["start_time", "end_time"]]
        if recommended_missing and len(recalculated_missing) > 1:
            slot_descriptions = [
                APPLICATION_SLOT_DESCRIPTIONS.get(slot, slot)
                for slot in recommended_missing
            ]
            if slot_descriptions:
                combined_question = "다음 정보를 알려주세요 (선택사항):\n" + "\n".join(
                    f"- {desc}" for desc in slot_descriptions
                )
                context["awaiting_slots"] = recommended_missing
                context["last_question"] = combined_question
                return combined_question
        
        if slot_decision.followup_question:
            context["awaiting_slots"] = recalculated_missing
            context["last_question"] = slot_decision.followup_question
            return slot_decision.followup_question
    
    context.pop("awaiting_slots", None)
    return None


def _persist_slot_state(context: Dict[str, Any], parsed: ParsedBusinessTripIntent) -> None:
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
        # 시간은 datetime 객체로 변환
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        elif isinstance(value, datetime):
            return value
    
    return value


def _export_parsed_slots(parsed: ParsedBusinessTripIntent) -> Dict[str, Any]:
    """ParsedIntent를 직렬화 가능한 dict로 변환."""
    return {
        "start_time": parsed.start_time.isoformat() if parsed.start_time else None,
        "end_time": parsed.end_time.isoformat() if parsed.end_time else None,
        "destination": parsed.destination,
        "trip_purpose": parsed.trip_purpose,
        "trip_title": parsed.trip_title,
    }


def _map_decision_action(decision_action: str, fallback: str) -> str:
    """LLM 액션을 내부 액션으로 매핑."""
    mapping = {
        "조회": "조회",
        "신청": "신청",
        "내역": "내역",
        "취소": "취소",
        "수정": "수정",
        "유지": fallback,
    }
    return mapping.get(decision_action, fallback)


def _maybe_reset_business_trip_context(context: Dict[str, Any], action: str, query: str) -> None:
    """명시적 의도 변경 시 컨텍스트 리셋."""
    action_keywords = {
        "조회": ["조회", "확인", "체크"],
        "신청": ["신청", "등록", "다녀올", "갈"],
        "내역": ["내역", "목록", "리스트"],
        "취소": ["취소", "삭제", "취소해", "삭제해"],
    }
    
    current_intent = context.get("slot_state", {}).get("intent")
    if current_intent and current_intent != action:
        # 명시적 키워드가 있으면 리셋
        for kw in action_keywords.get(action, []):
            if kw in query:
                context.pop("slot_state", None)
                context.pop("awaiting_slots", None)
                break


# ========== Action Handlers ==========

async def _parse_business_trip_intent(
    query: str,
    employee_id: int,
    user_id: Optional[str],
    action: str,
    context: Dict[str, Any],
    awaiting_slots: Optional[List[str]],
    slot_decision: Optional[BusinessTripSlotDecision],
    slot_state_payload: Dict[str, Any],
    state: Optional[Dict[str, Any]] = None  # state 파라미터 추가
) -> ParsedBusinessTripIntent:
    """쿼리에서 출장 의도 파싱."""
    # orchestrator의 현재 시간 가져오기
    from zoneinfo import ZoneInfo
    import os
    
    current_datetime = None
    if state:
        current_datetime_str = state.get("current_datetime")
        if current_datetime_str:
            try:
                current_datetime = datetime.fromisoformat(current_datetime_str)
                if current_datetime.tzinfo is None:
                    local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
                    current_datetime = current_datetime.replace(tzinfo=local_tz)
            except Exception as e:
                print(f"⚠️ current_datetime 파싱 실패: {e}, datetime.now() 사용")
                local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
                current_datetime = datetime.now(local_tz)
    else:
        local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
        current_datetime = datetime.now(local_tz)
    
    # 1. 기존 slots에서 정보 추출
    existing_slots = slot_state_payload.get("slots", {})
    
    # 2. 날짜 파싱
    start_time = existing_slots.get("start_time")
    end_time = existing_slots.get("end_time")
    
    if action == "신청" and not awaiting_slots:
        # LLM으로 날짜 파싱 (orchestrator의 현재 시간 전달)
        start_date_obj, end_date_obj = await parse_korean_date_with_llm(
            query,
            current_datetime=current_datetime  # orchestrator의 현재 시간 전달
        )
        
        if start_date_obj:
            # 시간이 없으면 기본값 설정 (09:00)
            if start_date_obj.hour == 0 and start_date_obj.minute == 0:
                start_date_obj = start_date_obj.replace(hour=9, minute=0)
            start_time = start_date_obj
            
            if end_date_obj:
                # 시간이 없으면 기본값 설정 (18:00)
                if end_date_obj.hour == 0 and end_date_obj.minute == 0:
                    end_date_obj = end_date_obj.replace(hour=18, minute=0)
                end_time = end_date_obj
            else:
                # 종료일이 없으면 시작일과 동일하게 설정
                end_time = start_date_obj.replace(hour=18, minute=0)
    elif action == "취소":
        # 취소 액션의 경우 trip_id 추출 시도
        import re
        trip_id_match = re.search(r'(\d+)\s*번', query)
        if trip_id_match:
            try:
                existing_slots["trip_id"] = int(trip_id_match.group(1))
            except ValueError:
                pass
        
        # trip_id가 없으면 날짜로 찾기 위해 날짜 파싱 (orchestrator의 현재 시간 전달)
        if not existing_slots.get("trip_id") and not start_time:
            start_date_obj, end_date_obj = await parse_korean_date_with_llm(
                query,
                current_datetime=current_datetime  # orchestrator의 현재 시간 전달
            )
            if start_date_obj:
                start_time = start_date_obj
            if end_date_obj:
                end_time = end_date_obj
    
    # 3. 문자열을 datetime으로 변환
    if isinstance(start_time, str):
        try:
            start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            start_time = None
    
    if isinstance(end_time, str):
        try:
            end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            end_time = None
    
    return ParsedBusinessTripIntent(
        action=action,
        employee_id=employee_id,
        start_time=start_time,
        end_time=end_time,
        destination=existing_slots.get("destination"),
        trip_purpose=existing_slots.get("trip_purpose"),
        trip_title=existing_slots.get("trip_title"),
        trip_id=existing_slots.get("trip_id"),
    )


async def _handle_query_conflicts(
    parsed: ParsedBusinessTripIntent,
    scheduler: HybridScheduler,
    user_id: Optional[str]
) -> Dict[str, Any]:
    """일정 충돌 조회."""
    if not user_id or not parsed.start_time or not parsed.end_time:
        return {"ok": False, "message": "일정 조회를 위해서는 사용자 ID와 출장 기간이 필요합니다."}
    
    try:
        local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
        start_aware = parsed.start_time.replace(tzinfo=local_tz) if parsed.start_time.tzinfo is None else parsed.start_time
        end_aware = parsed.end_time.replace(tzinfo=local_tz) if parsed.end_time.tzinfo is None else parsed.end_time
        
        # Google Calendar에서 일정 조회
        events = await scheduler.get_events(
            user_id=user_id,
            start_date=start_aware.astimezone(ZoneInfo("UTC")).strftime('%Y-%m-%dT%H:%M:%S') + "Z",
            end_date=end_aware.astimezone(ZoneInfo("UTC")).strftime('%Y-%m-%dT%H:%M:%S') + "Z"
        )
        
        if events:
            conflict_titles = [e.get('title', '제목 없음') for e in events[:5]]
            conflict_msg = "\n".join(f"- {title}" for title in conflict_titles)
            return {
                "ok": True,
                "message": f"⚠️ 해당 기간에 {len(events)}개의 일정이 있습니다:\n{conflict_msg}\n\n출장 일정을 등록하시겠습니까?",
                "conflicts": events
            }
        else:
            return {
                "ok": True,
                "message": "✅ 해당 기간에 일정 충돌이 없습니다. 출장 일정을 등록할 수 있습니다.",
                "conflicts": []
            }
    except GoogleAuthRequired as auth_error:
        return {
            "ok": False,
            "message": f"Google Calendar 인증이 필요합니다. {auth_error.auth_url}"
        }
    except Exception as e:
        print(f"❌ 일정 조회 실패: {e}")
        return {"ok": False, "message": f"일정 조회 중 오류: {str(e)}"}


async def _handle_create_trip(
    parsed: ParsedBusinessTripIntent,
    user_id: str,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """출장 신청 및 캘린더 등록."""
    if not parsed.start_time or not parsed.end_time:
        return {"ok": False, "message": "출장 신청을 위해서는 시작일과 종료일이 필요합니다."}
    
    try:
        # 1. 일정 충돌 체크 (실제 시간 겹침 확인)
        try:
            local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
            start_aware = parsed.start_time.replace(tzinfo=local_tz) if parsed.start_time.tzinfo is None else parsed.start_time
            end_aware = parsed.end_time.replace(tzinfo=local_tz) if parsed.end_time.tzinfo is None else parsed.end_time
            
            # UTC 변환
            start_utc = start_aware.astimezone(ZoneInfo("UTC"))
            end_utc = end_aware.astimezone(ZoneInfo("UTC"))
            start_date_str = start_utc.strftime('%Y-%m-%dT%H:%M:%S') + "Z"
            end_date_str = end_utc.strftime('%Y-%m-%dT%H:%M:%S') + "Z"
            
            # 디버깅: 요청 파라미터 출력
            print(f"🔍 [충돌 체크] 출장 기간:")
            print(f"  - 로컬: {start_aware.strftime('%Y-%m-%d %H:%M:%S %Z')} ~ {end_aware.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            print(f"  - UTC: {start_date_str} ~ {end_date_str}")
            
            events = await scheduler.get_events(
                user_id=user_id,
                start_date=start_date_str,
                end_date=end_date_str
            )
            
            # 디버깅: 반환된 일정 정보 출력
            print(f"🔍 [충돌 체크] Google Calendar에서 반환된 일정 {len(events)}개:")
            for idx, e in enumerate(events, 1):
                event_start = e.get('start_time', 'N/A')
                event_end = e.get('end_time', 'N/A')
                print(f"  {idx}. {e.get('title', '제목 없음')}")
                print(f"     시작: {event_start}")
                print(f"     종료: {event_end}")
                print(f"     위치: {e.get('location', 'N/A')}")
                print(f"     타입: {e.get('schedule_type', 'N/A')}")
            
            # 실제 시간 겹침 확인 (출장 일정은 제외)
            conflicting_events = []
            for event in events:
                # 출장 일정은 충돌에서 제외 (자신의 출장은 중복 체크 불필요)
                if event.get('schedule_type') == 'business_trip':
                    print(f"  ⏭️  출장 일정 제외: {event.get('title')}")
                    continue
                
                event_start_str = event.get('start_time')
                event_end_str = event.get('end_time')
                
                if not event_start_str or not event_end_str:
                    continue
                
                # 날짜 문자열을 datetime으로 변환
                try:
                    # dateTime 형식 (예: "2025-12-20T09:00:00+09:00" 또는 "2025-12-20T09:00:00Z")
                    if 'T' in event_start_str:
                        if event_start_str.endswith('Z'):
                            event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                        else:
                            event_start = datetime.fromisoformat(event_start_str)
                    else:
                        # date 형식 (하루 종일 이벤트, 예: "2025-12-20")
                        event_start = datetime.fromisoformat(event_start_str).replace(tzinfo=ZoneInfo("UTC"))
                    
                    if 'T' in event_end_str:
                        if event_end_str.endswith('Z'):
                            event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))
                        else:
                            event_end = datetime.fromisoformat(event_end_str)
                    else:
                        # date 형식 (하루 종일 이벤트, 다음 날 00:00으로 처리)
                        event_end = datetime.fromisoformat(event_end_str).replace(tzinfo=ZoneInfo("UTC")) + timedelta(days=1)
                    
                    # 시간대 변환 (로컬 시간으로)
                    if event_start.tzinfo is None:
                        event_start = event_start.replace(tzinfo=ZoneInfo("UTC"))
                    if event_end.tzinfo is None:
                        event_end = event_end.replace(tzinfo=ZoneInfo("UTC"))
                    
                    event_start_local = event_start.astimezone(local_tz)
                    event_end_local = event_end.astimezone(local_tz)
                    
                    # 실제 겹침 확인: (start < event_end) and (end > event_start)
                    is_overlapping = start_aware < event_end_local and end_aware > event_start_local
                    
                    print(f"  🔍 겹침 확인: {event.get('title')}")
                    print(f"     일정: {event_start_local.strftime('%Y-%m-%d %H:%M')} ~ {event_end_local.strftime('%Y-%m-%d %H:%M')}")
                    print(f"     출장: {start_aware.strftime('%Y-%m-%d %H:%M')} ~ {end_aware.strftime('%Y-%m-%d %H:%M')}")
                    print(f"     겹침: {'✅ 예' if is_overlapping else '❌ 아니오'}")
                    
                    if is_overlapping:
                        conflicting_events.append(event)
                except Exception as parse_err:
                    print(f"  ⚠️ 일정 시간 파싱 실패 ({event.get('title')}): {parse_err}")
                    # 파싱 실패 시 안전하게 충돌로 간주하지 않음
                    continue
            
            if conflicting_events:
                conflict_titles = [e.get('title', '제목 없음') for e in conflicting_events[:3]]
                conflict_msg = "\n".join(f"- {title}" for title in conflict_titles)
                print(f"❌ [충돌 감지] 실제 겹치는 일정 {len(conflicting_events)}개 발견")
                return {
                    "ok": False,
                    "message": f"❌ 해당 기간에 일정이 있습니다:\n{conflict_msg}\n\n일정이 있는 날에는 출장을 신청할 수 없습니다."
                }
            else:
                print(f"✅ [충돌 체크] 겹치는 일정 없음 (반환된 일정 {len(events)}개 중 모두 제외 또는 겹치지 않음)")
        except GoogleAuthRequired:
            # 인증 필요 시 경고 후 진행
            print("⚠️ Google Calendar 인증 필요 (일정 충돌 체크 생략)")
        except Exception as date_err:
            print(f"⚠️ 일정 충돌 체크 실패 (진행): {date_err}")
        
        # 2. 출장 제목 자동 생성
        trip_title = parsed.trip_title
        if not trip_title:
            if parsed.destination:
                trip_title = f"{parsed.destination} 출장"
            elif parsed.trip_purpose:
                trip_title = f"{parsed.trip_purpose} 출장"
            else:
                trip_title = "출장"
        
        # 3. DB에 출장 저장
        trip_data = {
            "employee_id": parsed.employee_id,
            "trip_title": trip_title,
            "trip_purpose": parsed.trip_purpose,
            "destination": parsed.destination,
            "start_time": parsed.start_time,
            "end_time": parsed.end_time,
        }
        
        trip_id = await scheduler.db.create_business_trip_request(trip_data)
        
        # 4. Google Calendar에 이벤트 생성 (본인 캘린더에만 등록)
        event_description = f"출장 목적: {parsed.trip_purpose or '출장'}\n"
        if parsed.destination:
            event_description += f"목적지: {parsed.destination}\n"
        
        event_data = {
            "title": trip_title,
            "description": event_description.strip(),
            "start_time": parsed.start_time.isoformat(),
            "end_time": parsed.end_time.isoformat(),
            "location": parsed.destination or "",
            "attendees": [],  # 본인 캘린더에만 등록 (팀원 초대 없음)
            "schedule_type": "business_trip",
            "employee_id": parsed.employee_id,
        }
        
        try:
            created_event = await scheduler.create_event(
                user_id=user_id,
                event_data=event_data
            )
            
            if created_event:
                gcal_event_id = created_event.get("gcal_event", {}).get("id")
                message = f"✅ 출장 신청이 완료되었습니다!\n\n"
                message += f"기간: {parsed.start_time.strftime('%Y-%m-%d %H:%M')} ~ {parsed.end_time.strftime('%Y-%m-%d %H:%M')}\n"
                if parsed.destination:
                    message += f"목적지: {parsed.destination}\n"
                
                return {
                    "ok": True,
                    "message": message,
                    "trip_id": trip_id,
                    "gcal_event_id": gcal_event_id,
                }
        except GoogleAuthRequired as auth_error:
            # 캘린더 등록 실패해도 DB 저장은 완료
            return {
                "ok": True,
                "message": f"✅ 출장 신청이 완료되었습니다 (캘린더 등록은 Google 인증 후 가능합니다).\n\n기간: {parsed.start_time.strftime('%Y-%m-%d %H:%M')} ~ {parsed.end_time.strftime('%Y-%m-%d %H:%M')}",
                "trip_id": trip_id,
            }
        except Exception as e:
            print(f"⚠️ Google Calendar 이벤트 생성 실패: {e}")
            return {
                "ok": True,
                "message": f"✅ 출장 신청이 완료되었습니다 (캘린더 등록 실패).\n\n기간: {parsed.start_time.strftime('%Y-%m-%d %H:%M')} ~ {parsed.end_time.strftime('%Y-%m-%d %H:%M')}",
                "trip_id": trip_id,
            }
    
    except Exception as e:
        print(f"❌ 출장 신청 실패: {e}")
        return {"ok": False, "message": f"출장 신청 중 오류: {str(e)}"}


async def _handle_query_history(
    parsed: ParsedBusinessTripIntent,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """출장 내역 조회."""
    try:
        history = await scheduler.db.get_business_trip_list(
            employee_id=parsed.employee_id,
            include_past=True
        )
        
        if not history:
            message = "출장 내역이 없습니다."
        else:
            message = f"📋 출장 내역 (총 {len(history)}건):\n\n"
            for idx, item in enumerate(history[:10], 1):
                start_time = item.get('start_time')
                end_time = item.get('end_time')
                start_str = start_time.strftime('%Y-%m-%d %H:%M') if isinstance(start_time, datetime) else str(start_time)
                end_str = end_time.strftime('%Y-%m-%d %H:%M') if isinstance(end_time, datetime) else str(end_time)
                
                message += f"{idx}. {item.get('trip_title', '출장')}\n"
                message += f"   기간: {start_str} ~ {end_str}\n"
                if item.get('destination'):
                    message += f"   목적지: {item.get('destination')}\n"
                message += "\n"
            
            if len(history) > 10:
                message += f"... 외 {len(history) - 10}건"
        
        return {"ok": True, "message": message, "history": history}
    
    except Exception as e:
        print(f"❌ 출장 내역 조회 실패: {e}")
        return {"ok": False, "message": f"출장 내역 조회 중 오류: {str(e)}"}


async def _handle_cancel_trip(
    parsed: ParsedBusinessTripIntent,
    user_id: str,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """출장 취소 처리."""
    if not parsed.employee_id:
        return {"ok": False, "message": "출장 취소를 위해서는 사번이 필요합니다."}
    
    try:
        # 1. 출장 찾기
        trip = None
        
        if parsed.trip_id:
            # trip_id로 직접 조회
            trip = await scheduler.db.get_business_trip_by_id(parsed.trip_id, parsed.employee_id)
        elif parsed.start_time and parsed.end_time:
            # 날짜 범위로 찾기
            trip = await scheduler.db.find_business_trip_by_date(
                employee_id=parsed.employee_id,
                start_time=parsed.start_time,
                end_time=parsed.end_time,
                destination=parsed.destination
            )
        else:
            # 최근 출장 목록에서 선택하도록 안내
            history = await scheduler.db.get_business_trip_list(
                employee_id=parsed.employee_id,
                include_past=False  # 미래 출장만
            )
            
            if not history:
                return {"ok": False, "message": "취소할 출장이 없습니다."}
            
            # 가장 최근 출장을 자동 선택하거나, 사용자에게 선택 요청
            if len(history) == 1:
                trip = history[0]
            else:
                # 여러 개면 목록을 보여주고 선택 요청
                message = "취소할 출장을 선택해주세요:\n\n"
                for idx, item in enumerate(history[:5], 1):
                    start_time = item.get('start_time')
                    end_time = item.get('end_time')
                    start_str = start_time.strftime('%Y-%m-%d %H:%M') if isinstance(start_time, datetime) else str(start_time)
                    end_str = end_time.strftime('%Y-%m-%d %H:%M') if isinstance(end_time, datetime) else str(end_time)
                    
                    message += f"{idx}. {item.get('trip_title', '출장')} ({start_str} ~ {end_str})\n"
                
                return {"ok": False, "message": message}
        
        if not trip:
            return {"ok": False, "message": "취소할 출장을 찾을 수 없습니다."}
        
        trip_id = trip["trip_id"]
        
        # 2. Google Calendar 이벤트 ID 조회
        gcal_event_id = await scheduler.db.get_gcal_event_id_by_trip_id(trip_id)
        
        # 3. Google Calendar에서 이벤트 삭제 (delete_event가 schedule 테이블도 삭제함)
        if gcal_event_id:
            try:
                success = await scheduler.delete_event(user_id=user_id, event_id=gcal_event_id)
                if success:
                    print(f"✅ Google Calendar 이벤트 및 schedule 테이블 삭제 완료: {gcal_event_id}")
                else:
                    print(f"⚠️ Google Calendar 이벤트 삭제 실패: {gcal_event_id}")
            except GoogleAuthRequired as auth_error:
                print(f"⚠️ Google Calendar 인증 필요 (계속 진행): {auth_error}")
                # 인증 실패 시 schedule 테이블만 삭제
                await scheduler.db.delete_schedule_by_gcal_event_id(gcal_event_id)
            except Exception as e:
                print(f"⚠️ Google Calendar 이벤트 삭제 실패 (계속 진행): {e}")
                # 삭제 실패 시 schedule 테이블만 삭제
                await scheduler.db.delete_schedule_by_gcal_event_id(gcal_event_id)
        
        # 5. business_trip 테이블에서 출장 삭제
        cancel_success = await scheduler.db.cancel_business_trip(trip_id, parsed.employee_id)
        
        if cancel_success:
            trip_title = trip.get('trip_title', '출장')
            start_time = trip.get('start_time')
            start_str = start_time.strftime('%Y-%m-%d %H:%M') if isinstance(start_time, datetime) else str(start_time)
            
            return {
                "ok": True,
                "message": f"✅ 출장이 취소되었습니다.\n\n취소된 출장: {trip_title}\n기간: {start_str}",
                "trip_id": trip_id,
            }
        else:
            return {"ok": False, "message": "출장 취소에 실패했습니다."}
    
    except Exception as e:
        print(f"❌ 출장 취소 실패: {e}")
        import traceback
        traceback.print_exc()
        return {"ok": False, "message": f"출장 취소 중 오류: {str(e)}"}


def _determine_business_trip_action(query: str) -> str:
    """키워드 기반 액션 결정 (fallback용)."""
    if any(k in query for k in ["취소", "삭제", "취소해", "삭제해"]):
        return "취소"
    elif any(k in query for k in ["내역", "목록", "리스트", "사용 내역"]):
        return "내역"
    elif any(k in query for k in ["조회", "확인", "체크", "일정"]):
        return "조회"
    elif any(k in query for k in ["신청", "등록", "다녀올", "갈", "출장"]):
        return "신청"
    else:
        return "신청"  # 기본값은 신청


# ========== Workflow Step Handlers ==========

async def _handle_workflow_step1_check_conflicts(
    state: OrchestratorState,
    query: str,
    employee_id: Optional[int],
    user_id: Optional[str]
) -> Dict[str, Any]:
    """워크플로우 1단계: 일정 조회 및 날짜 확인."""
    print("--- [Business Trip Workflow] Step 1: 일정 조회 및 날짜 확인 ---")
    
    # orchestrator에서 전달받은 현재 시간 사용
    current_datetime_str = state.get("current_datetime")
    if current_datetime_str:
        try:
            current_datetime = datetime.fromisoformat(current_datetime_str)
            if current_datetime.tzinfo is None:
                local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
                current_datetime = current_datetime.replace(tzinfo=local_tz)
        except Exception as e:
            print(f"⚠️ current_datetime 파싱 실패: {e}, datetime.now() 사용")
            local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
            current_datetime = datetime.now(local_tz)
    else:
        local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
        current_datetime = datetime.now(local_tz)
    
    # workflow_temp_data 초기화
    workflow_temp_data = state.get("workflow_temp_data") or {}
    
    # 날짜 정보 확인 (workflow_temp_data 또는 state에서)
    start_time_str = workflow_temp_data.get("start_time") or state.get("start_date")
    end_time_str = workflow_temp_data.get("end_time") or state.get("end_date")
    
    # 날짜 파싱 시도
    start_time = None
    end_time = None
    
    if start_time_str:
        try:
            if isinstance(start_time_str, str):
                start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            elif isinstance(start_time_str, datetime):
                start_time = start_time_str
        except Exception as e:
            print(f"⚠️ start_time 파싱 실패: {e}")
    
    if end_time_str:
        try:
            if isinstance(end_time_str, str):
                end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
            elif isinstance(end_time_str, datetime):
                end_time = end_time_str
        except Exception as e:
            print(f"⚠️ end_time 파싱 실패: {e}")
    
    # 날짜가 없으면 사용자 입력에서 파싱 시도
    if not start_time or not end_time:
        print("🔍 날짜 정보 부족 - 사용자 입력에서 파싱 시도")
        start_date_obj, end_date_obj = await parse_korean_date_with_llm(
            query, 
            current_datetime=current_datetime  # orchestrator의 현재 시간 전달
        )
        
        if start_date_obj:
            # 파싱된 날짜가 유효한지 즉시 검증
            # orchestrator의 현재 시간 사용
            now = current_datetime
            
            if start_date_obj.tzinfo is None:
                start_date_obj = start_date_obj.replace(tzinfo=local_tz)
            
            # 과거 날짜이거나 너무 오래된 날짜면 무시
            days_diff = (start_date_obj.date() - now.date()).days
            if days_diff < 0 or start_date_obj.year < 2024:
                print(f"⚠️ 파싱된 날짜가 유효하지 않음: {start_date_obj.date()} (오늘: {now.date()})")
                start_date_obj = None
                end_date_obj = None
            else:
                if start_date_obj.hour == 0 and start_date_obj.minute == 0:
                    start_date_obj = start_date_obj.replace(hour=9, minute=0)
                start_time = start_date_obj
                
                if end_date_obj:
                    if end_date_obj.tzinfo is None:
                        end_date_obj = end_date_obj.replace(tzinfo=local_tz)
                    if end_date_obj.hour == 0 and end_date_obj.minute == 0:
                        end_date_obj = end_date_obj.replace(hour=18, minute=0)
                    end_time = end_date_obj
                else:
                    # 종료일이 없으면 시작일과 동일하게 설정
                    end_time = start_date_obj.replace(hour=18, minute=0)
    
    # 날짜가 여전히 없거나 정확하지 않으면 재요청 (일정 조회 전에)
    # 정확한 날짜 확인: datetime 객체가 있고, 미래 날짜인지 확인
    if not start_time or not end_time:
        buttons_html = """
<div style="margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap;">
    <button onclick="sendMessageFromButton('종료')" style="padding: 10px 20px; background: #dc3545; color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(220, 53, 69, 0.3);">
        ❌ 종료
    </button>
</div>
"""
        question = "출장 기간을 알려주세요.\n예: 12월 3일부터 5일까지, 다음주 월요일부터 수요일까지" + buttons_html
        return {
            "final_response": question,
            "messages": [question],
            "workflow_temp_data": workflow_temp_data,
            "schedule_context": state.get("schedule_context", {}),
            "requires_input": True,  # 사용자 입력 대기
        }
    
    # 날짜가 정확한지 확인 (미래 날짜인지, 시작일이 종료일보다 이전인지)
    # orchestrator의 현재 시간 사용
    now = current_datetime
    local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
    
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=local_tz)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=local_tz)
    
    # 날짜 유효성 검사
    # 1. 시작일이 종료일보다 이전이어야 함
    # 2. 시작일이 과거 날짜가 아니어야 함 (현재 날짜보다 1일 이상 과거면 재요청)
    # 3. 날짜가 너무 오래된 경우 (예: 2023년) 재요청
    buttons_html = """
<div style="margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap;">
    <button onclick="sendMessageFromButton('종료')" style="padding: 10px 20px; background: #dc3545; color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(220, 53, 69, 0.3);">
        ❌ 종료
    </button>
</div>
"""
    
    # 시작일이 종료일보다 이전이어야 함
    if start_time >= end_time:
        question = f"출장 종료일({end_time.strftime('%Y-%m-%d')})이 시작일({start_time.strftime('%Y-%m-%d')})보다 이전이거나 같습니다.\n\n정확한 출장 기간을 알려주세요.\n예: 12월 3일부터 5일까지" + buttons_html
        return {
            "final_response": question,
            "messages": [question],
            "workflow_temp_data": workflow_temp_data,
            "schedule_context": state.get("schedule_context", {}),
            "requires_input": True,
        }
    
    # 시작일이 과거 날짜인지 확인 (현재 날짜보다 1일 이상 과거면 재요청)
    days_diff = (start_time.date() - now.date()).days
    if days_diff < -1:
        question = f"출장 시작일({start_time.strftime('%Y-%m-%d')})이 과거 날짜입니다.\n\n정확한 출장 기간을 알려주세요.\n예: 12월 3일부터 5일까지, 다음주 월요일부터 수요일까지" + buttons_html
        return {
            "final_response": question,
            "messages": [question],
            "workflow_temp_data": workflow_temp_data,
            "schedule_context": state.get("schedule_context", {}),
            "requires_input": True,
        }
    
    # 날짜가 너무 오래된 경우 (2024년 이전이면 재요청)
    if start_time.year < 2024:
        question = f"출장 날짜({start_time.strftime('%Y-%m-%d')})가 너무 오래되었습니다.\n\n정확한 출장 기간을 알려주세요.\n예: 12월 3일부터 5일까지, 다음주 월요일부터 수요일까지" + buttons_html
        return {
            "final_response": question,
            "messages": [question],
            "workflow_temp_data": workflow_temp_data,
            "schedule_context": state.get("schedule_context", {}),
            "requires_input": True,
        }
    
    # workflow_temp_data에 저장
    workflow_temp_data["start_time"] = start_time.isoformat()
    workflow_temp_data["end_time"] = end_time.isoformat()
    
    # 디버깅: 저장된 데이터 확인
    print(f"🔍 [Step 1] workflow_temp_data 저장 직후: {workflow_temp_data}")
    
    # 목적지 및 제목 추출
    destination = workflow_temp_data.get("destination") or state.get("destination")
    if not destination:
        # 쿼리에서 목적지 추출 시도
        if "제주" in query or "제주도" in query:
            destination = "제주도"
        elif "부산" in query:
            destination = "부산"
        elif "서울" in query:
            destination = "서울"
        elif "울산" in query:
            destination = "울산"
        if destination:
            workflow_temp_data["destination"] = destination
    
    # 제목 추출
    title = workflow_temp_data.get("title")
    if not title:
        if destination:
            title = f"{destination} 출장"
        elif "워크샵" in query:
            title = "워크샵 출장"
        else:
            title = "출장"
        workflow_temp_data["title"] = title
    
    # 디버깅: 최종 저장된 데이터 확인
    print(f"🔍 [Step 1] workflow_temp_data 최종 저장: {workflow_temp_data}")
    print(f"🔍 [Step 1] return에 포함될 workflow_temp_data keys: {list(workflow_temp_data.keys())}")
    
    # 일정 충돌 체크 (날짜가 정확할 때만 수행)
    scheduler = _get_scheduler()
    try:
        start_aware = start_time
        end_aware = end_time
        
        start_utc = start_aware.astimezone(ZoneInfo("UTC"))
        end_utc = end_aware.astimezone(ZoneInfo("UTC"))
        start_date_str = start_utc.strftime('%Y-%m-%dT%H:%M:%S') + "Z"
        end_date_str = end_utc.strftime('%Y-%m-%dT%H:%M:%S') + "Z"
        
        events = await scheduler.get_events(
            user_id=user_id,
            start_date=start_date_str,
            end_date=end_date_str
        )
        
        # 출장 일정 제외
        conflicting_events = []
        for event in events:
            if event.get('schedule_type') == 'business_trip':
                continue
            
            event_start_str = event.get('start_time')
            event_end_str = event.get('end_time')
            
            if not event_start_str or not event_end_str:
                continue
            
            try:
                if 'T' in event_start_str:
                    if event_start_str.endswith('Z'):
                        event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                    else:
                        event_start = datetime.fromisoformat(event_start_str)
                else:
                    event_start = datetime.fromisoformat(event_start_str).replace(tzinfo=ZoneInfo("UTC"))
                
                if 'T' in event_end_str:
                    if event_end_str.endswith('Z'):
                        event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))
                    else:
                        event_end = datetime.fromisoformat(event_end_str)
                else:
                    event_end = datetime.fromisoformat(event_end_str).replace(tzinfo=ZoneInfo("UTC")) + timedelta(days=1)
                
                if event_start.tzinfo is None:
                    event_start = event_start.replace(tzinfo=ZoneInfo("UTC"))
                if event_end.tzinfo is None:
                    event_end = event_end.replace(tzinfo=ZoneInfo("UTC"))
                
                event_start_local = event_start.astimezone(local_tz)
                event_end_local = event_end.astimezone(local_tz)
                
                is_overlapping = start_aware < event_end_local and end_aware > event_start_local
                
                if is_overlapping:
                    conflicting_events.append(event)
            except Exception:
                continue
        
        # Step 1 완료 메시지 먼저 추가
        step1_complete_msg = "✅ [Step 1 완료] 일정 조회가 완료되었습니다.\n\n"
        
        # 휴먼 인 더 루프: 출장 신청서 작성 진행 여부 확인
        # 버튼 형태로 표시하기 위해 HTML 형식 사용
        buttons_html = """
<div style="margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap;">
    <button onclick="sendMessageFromButton('진행')" style="padding: 10px 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(102, 126, 234, 0.3);">
        ✅ 진행
    </button>
    <button onclick="sendMessageFromButton('취소')" style="padding: 10px 20px; background: #e0e0e0; color: #333; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);">
        ❌ 취소
    </button>
</div>
"""
        
        if conflicting_events:
            conflict_titles = [e.get('title', '제목 없음') for e in conflicting_events[:3]]
            conflict_msg = "\n".join(f"- {title}" for title in conflict_titles)
            message = step1_complete_msg + f"⚠️ 해당 기간에 일정이 있습니다:\n{conflict_msg}\n\n출장 신청서 작성을 계속하시겠습니까?" + buttons_html
        else:
            message = step1_complete_msg + f"✅ 해당 기간에 일정 충돌이 없습니다.\n\n출장 기간: {start_aware.strftime('%Y-%m-%d %H:%M')} ~ {end_aware.strftime('%Y-%m-%d %H:%M')}\n\n출장 신청서 작성을 진행하시겠습니까?" + buttons_html
        
        return {
            "final_response": message,
            "messages": [message],
            "workflow_temp_data": workflow_temp_data,
            "schedule_context": state.get("schedule_context", {}),
            "requires_input": True,  # 버튼 표시를 위한 플래그
        }
    except GoogleAuthRequired as auth_error:
        # 인증 필요 시 경고 후 진행
        message = f"⚠️ Google Calendar 인증이 필요합니다. (일정 충돌 체크 생략)\n\n출장 기간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}\n\n출장 신청서 작성을 진행하시겠습니까?\n\n[진행] 또는 [취소]를 선택해주세요."
        return {
            "final_response": message,
            "messages": [message],
            "workflow_temp_data": workflow_temp_data,
            "schedule_context": state.get("schedule_context", {}),
            "requires_input": True,
        }
    except Exception as e:
        print(f"⚠️ 일정 조회 실패: {e}")
        message = f"⚠️ 일정 조회 중 오류가 발생했습니다.\n\n출장 기간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}\n\n출장 신청서 작성을 진행하시겠습니까?\n\n[진행] 또는 [취소]를 선택해주세요."
        return {
            "final_response": message,
            "messages": [message],
            "workflow_temp_data": workflow_temp_data,
            "schedule_context": state.get("schedule_context", {}),
            "requires_input": True,
        }


async def _handle_workflow_step4_register_calendar(
    state: OrchestratorState,
    query: str,
    employee_id: Optional[int],
    user_id: Optional[str]
) -> Dict[str, Any]:
    """워크플로우 4단계: 캘린더 등록 및 RDB 저장."""
    print("--- [Business Trip Workflow] Step 4: 캘린더 등록 및 RDB 저장 ---")
    
    workflow_temp_data = state.get("workflow_temp_data") or {}
    
    if not workflow_temp_data.get("start_time") or not workflow_temp_data.get("end_time"):
        return {
            "final_response": "❌ 출장 정보가 없습니다. 워크플로우를 처음부터 다시 시작해주세요.",
            "messages": ["출장 정보가 없습니다."],
            "schedule_context": state.get("schedule_context", {}),
        }
    
    try:
        start_time = datetime.fromisoformat(workflow_temp_data["start_time"].replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(workflow_temp_data["end_time"].replace("Z", "+00:00"))
    except Exception as e:
        print(f"❌ 날짜 파싱 실패: {e}")
        return {
            "final_response": f"❌ 날짜 정보를 파싱할 수 없습니다: {e}",
            "messages": ["날짜 정보를 파싱할 수 없습니다."],
            "schedule_context": state.get("schedule_context", {}),
        }
    
    title = workflow_temp_data.get("title", "출장")
    description = workflow_temp_data.get("description", workflow_temp_data.get("trip_purpose", "출장"))
    destination = workflow_temp_data.get("destination", "")
    
    scheduler = _get_scheduler()
    
    # 출장 제목 자동 생성
    trip_title = title
    if not trip_title:
        if destination:
            trip_title = f"{destination} 출장"
        elif description:
            trip_title = f"{description} 출장"
        else:
            trip_title = "출장"
    
    # DB에 출장 저장
    trip_data = {
        "employee_id": employee_id,
        "trip_title": trip_title,
        "trip_purpose": description,
        "destination": destination,
        "start_time": start_time,
        "end_time": end_time,
    }
    
    trip_id = await scheduler.db.create_business_trip_request(trip_data)
    
    # Google Calendar에 이벤트 생성
    event_description = f"출장 목적: {description}\n"
    if destination:
        event_description += f"목적지: {destination}\n"
    
    event_data = {
        "title": trip_title,
        "description": event_description.strip(),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "location": destination or "",
        "attendees": [],  # 본인 캘린더에만 등록
        "schedule_type": "business_trip",
        "employee_id": employee_id,
    }
    
    try:
        created_event = await scheduler.create_event(
            user_id=user_id,
            event_data=event_data
        )
        
        if created_event:
            gcal_event_id = created_event.get("id")
            # Step 4 완료 메시지 추가
            step4_complete_msg = "✅ [Step 4 완료] 캘린더 등록이 완료되었습니다.\n\n"
            message = f"✅ 출장 신청이 완료되었습니다!\n\n"
            message += f"기간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}\n"
            if destination:
                message += f"목적지: {destination}\n"
            message += f"제목: {trip_title}\n"
            message += "\n구글 캘린더에 일정이 등록되었습니다."
            message = step4_complete_msg + message
            
            # 워크플로우 완료 - workflow_temp_data 초기화
            return {
                "ok": True,
                "final_response": message,
                "messages": [message],
                "trip_id": trip_id,
                "gcal_event_id": gcal_event_id,
                "workflow_temp_data": {},  # 초기화
                "workflow_in_progress": False,  # 워크플로우 완료
                "schedule_context": state.get("schedule_context", {}),
                "next_domain": None,  # 워크플로우 완료
            }
    except GoogleAuthRequired as auth_error:
        # 캘린더 등록 실패해도 DB 저장은 완료
        message = f"✅ 출장 신청이 완료되었습니다 (캘린더 등록은 Google 인증 후 가능합니다).\n\n기간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}"
        return {
            "ok": True,
            "final_response": message,
            "messages": [message],
            "trip_id": trip_id,
            "workflow_temp_data": {},  # 초기화
            "workflow_in_progress": False,  # 워크플로우 완료
            "schedule_context": state.get("schedule_context", {}),
            "next_domain": None,
        }
    except Exception as e:
        print(f"⚠️ Google Calendar 이벤트 생성 실패: {e}")
        message = f"✅ 출장 신청이 완료되었습니다 (캘린더 등록 실패).\n\n기간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}"
        return {
            "ok": True,
            "final_response": message,
            "messages": [message],
            "trip_id": trip_id,
            "workflow_temp_data": {},  # 초기화
            "workflow_in_progress": False,  # 워크플로우 완료
            "schedule_context": state.get("schedule_context", {}),
            "next_domain": None,
        }

