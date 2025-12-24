"""연차/반차 관리 노드 - Slot Manager Pattern."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Literal, List
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from sqlalchemy import text

from agents.state import OrchestratorState
from agents.master_graphs.schedule.services.hybrid_scheduler import HybridScheduler
from agents.master_graphs.schedule.utils.date_parser import parse_korean_date_with_llm
from agents.master_graphs.schedule.utils.entity_parser import extract_leave_entities
from agents.master_graphs.schedule.models.leave_models import LeaveSlotDecision
from agents.master_graphs.schedule.utils.leave_slot_manager import analyze_leave_slots
from agents.master_graphs.schedule.utils.prompt_loader import (
    get_sub_agent_prompt,
    format_prompt_template
)


_scheduler = None

# 연차/반차 신청 필수 정보 정의
APPLICATION_SLOT_ORDER = ["leave_type", "start_date", "end_date", "reason"]
APPLICATION_SLOT_DESCRIPTIONS = {
    "leave_type": "휴가 종류 (연차/반차/병가/경조사)",
    "start_date": "시작 날짜 (예: 12월 20일, 내일)",
    "end_date": "종료 날짜 (예: 12월 22일, 모레)",
    "reason": "휴가 사유",
}


def _get_scheduler() -> HybridScheduler:
    """HybridScheduler 싱글톤 인스턴스 반환."""
    global _scheduler
    if _scheduler is None:
        _scheduler = HybridScheduler()
    return _scheduler


@dataclass
class ParsedLeaveIntent:
    """파싱된 연차 의도."""
    action: Literal["조회", "신청", "내역", "추천", "취소", "수정"]
    leave_type: Literal["연차", "반차", "병가", "경조사"]
    employee_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_days: Optional[float] = None
    reason: str = "개인 사정"
    request_id: Optional[int] = None


async def process_leave_request(state: OrchestratorState) -> Dict[str, Any]:
    """연차/반차 요청 처리 - Slot Manager Pattern."""
    query = state.get("query", "")
    employee_id = state.get("employee_id")
    user_id = state.get("user_id")
    
    # Redis 기반 컨텍스트 초기화
    if state.get("schedule_context") is None:
        state["schedule_context"] = {}
    if state["schedule_context"].get("leave") is None:
        state["schedule_context"]["leave"] = {}
    
    leave_context = state["schedule_context"]["leave"]
    
    print(f"[Leave Node] 쿼리: {query}, employee_id: {employee_id}")
    
    # 1. employee_id 검증
    if not employee_id:
        return {
            "final_response": "연차 관리를 위해서는 사번(employee_id)이 필요합니다.",
            "messages": ["연차 관리를 위해서는 사번이 필요합니다."],
            "schedule_context": state["schedule_context"],
        }
    
    # 2. slot_state 및 awaiting_slots 추출
    slot_state_ctx = leave_context.get("slot_state") or {}
    awaiting_slots_ctx = leave_context.get("awaiting_slots") or []
    
    # 3. 액션 결정
    inferred_action = _determine_leave_action(query)
    if awaiting_slots_ctx and slot_state_ctx.get("intent"):
        action = slot_state_ctx["intent"]
        should_reset_context = False
    else:
        action = inferred_action
        should_reset_context = True
    
    # 4. 컨텍스트 리셋 여부 판단
    if should_reset_context:
        _maybe_reset_leave_context(leave_context, action, query)
    
    # 5. slot_state_payload 구성
    slot_state_payload = _build_slot_state_payload(action, leave_context)
    
    # 6. LLM 슬롯 분석
    slot_decision = await analyze_leave_slots(query, slot_state_payload)
    
    # 7. action 정규화
    normalized_action = _map_decision_action(slot_decision.action, action) if slot_decision else action
    
    # 8. ParsedIntent 생성
    parsed = await _parse_leave_intent(
        query, employee_id, user_id, normalized_action, 
        leave_context, awaiting_slots_ctx, slot_decision, slot_state_payload
    )
    
    # 9. slot_decision 적용
    followup_question = _apply_slot_decision(
        parsed, slot_decision, leave_context, slot_state_payload, query
    )
    
    # 9.5. 연차/반차 신청 시 초기 프롬프트 표시
    if parsed.action == "신청":
        initial_missing = _collect_application_missing(parsed, leave_context)
        slot_state_ctx = leave_context.get("slot_state") or {}
        slot_values = slot_state_ctx.get("slots") or {}
        previous_awaiting = slot_state_payload.get("awaiting_slots") or []
        previous_slots = slot_state_payload.get("slots") or {}
        
        # 핵심 필드(날짜)가 없으면 초기 프롬프트 표시
        has_date_info = bool(
            parsed.start_date 
            or parsed.end_date
            or previous_slots.get("start_date")
            or previous_slots.get("end_date")
            or slot_values.get("start_date")
            or slot_values.get("end_date")
        )
        
        # 날짜 정보가 없고 initial_missing이 있으면 초기 프롬프트 표시
        if initial_missing and not has_date_info:
            initial_prompt = _build_initial_application_prompt(initial_missing)
            slot_state_ctx.setdefault("intent", parsed.action)
            slot_state_ctx.setdefault("slots", {})
            slot_state_ctx["missing_slots"] = initial_missing
            leave_context["slot_state"] = slot_state_ctx
            leave_context["awaiting_slots"] = initial_missing
            leave_context["last_question"] = initial_prompt
            state["last_domain"] = "schedule"
            state["domain"] = "schedule"
            
            return {
                "final_response": initial_prompt,
                "messages": [initial_prompt],
                "schedule_context": state["schedule_context"],
            }
    
    # 10. followup이 있으면 즉시 반환
    if followup_question:
        leave_context["last_question"] = followup_question
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
        result = await _handle_query_balance(parsed, scheduler)
    elif parsed.action == "신청":
        result = await _handle_apply(parsed, user_id or str(employee_id), scheduler)
    elif parsed.action == "내역":
        result = await _handle_query_history(parsed, scheduler)
    elif parsed.action == "추천":
        result = await _handle_recommend(parsed, scheduler)
    elif parsed.action == "취소":
        result = await _handle_cancel(parsed, scheduler, leave_context, user_id)
    elif parsed.action == "수정":
        result = await _handle_update(parsed, scheduler, leave_context)
    else:
        result = {"ok": False, "message": "알 수 없는 액션입니다."}
    
    # 12. slot_state 저장
    _persist_slot_state(leave_context, parsed)
    
    # 13. 응답 반환
    message = result.get("message", "연차 처리가 완료되었습니다.")
    
    state["last_domain"] = "schedule"
    state["domain"] = "schedule"

    return {
        "final_response": message,
        "messages": [message],
        "schedule_context": state["schedule_context"],
    }


# ========== Utility Functions (Meeting Room Pattern) ==========

def _build_slot_state_payload(current_action: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Slot Manager에 전달할 payload 구성."""
    existing_state = context.get("slot_state") or {}
    return {
        "intent": existing_state.get("intent") or current_action,
        "slots": existing_state.get("slots") or {},
        "cancel_candidates": context.get("cancel_candidates") or [],
        "update_candidates": context.get("update_candidates") or [],
        "awaiting_slots": context.get("awaiting_slots") or [],
        "last_question": context.get("last_question"),
    }


def _apply_slot_decision(
    parsed: ParsedLeaveIntent,
    slot_decision: Optional[LeaveSlotDecision],
    context: Dict[str, Any],
    slot_state_payload: Dict[str, Any],
    raw_query: str
) -> Optional[str]:
    """LLM 슬롯 결정을 컨텍스트에 적용."""
    if slot_decision is None:
        context.pop("awaiting_slots", None)
        return None
    
    # slots 병합: slot_state + LLM filled_slots + parsed slots
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
        elif slot_name in ["reason"] and not str(merged_slots[slot_name]).strip():
            recalculated_missing.append(slot_name)
    
    context["slot_state"] = {
        "intent": parsed.action,
        "slots": merged_slots,
        "missing_slots": recalculated_missing,
    }
    
    # 여러 슬롯이 누락되었으면 한 번에 물어봄
    if recalculated_missing and slot_decision.should_ask:
        if len(recalculated_missing) > 1 and parsed.action == "신청":
            slot_descriptions = [
                APPLICATION_SLOT_DESCRIPTIONS.get(slot, slot)
                for slot in recalculated_missing
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


def _persist_slot_state(context: Dict[str, Any], parsed: ParsedLeaveIntent) -> None:
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
    
    if slot_name in ["start_date", "end_date"]:
        # 날짜는 문자열로 유지
        if isinstance(value, str):
            return value
        elif isinstance(value, (datetime, date)):
            return value.strftime("%Y-%m-%d")
    
    elif slot_name == "total_days":
        # 일수는 float
        return float(value)
    
    elif slot_name == "selection_index":
        # 선택 인덱스는 int
        return int(value)
    
    return value


def _export_parsed_slots(parsed: ParsedLeaveIntent) -> Dict[str, Any]:
    """ParsedIntent를 직렬화 가능한 dict로 변환."""
    return {
        "leave_type": parsed.leave_type,
        "start_date": parsed.start_date,
        "end_date": parsed.end_date,
        "total_days": parsed.total_days,
        "reason": parsed.reason,
        "request_id": parsed.request_id,
    }


def _map_decision_action(decision_action: str, fallback: str) -> str:
    """LLM 액션을 내부 액션으로 매핑."""
    mapping = {
        "조회": "조회",
        "신청": "신청",
        "내역": "내역",
        "추천": "추천",
        "취소": "취소",
        "수정": "수정",
        "유지": fallback,
    }
    return mapping.get(decision_action, fallback)


def _maybe_reset_leave_context(context: Dict[str, Any], action: str, query: str) -> None:
    """명시적 의도 변경 시 컨텍스트 리셋."""
    action_keywords = {
        "조회": ["조회", "잔여", "남은"],
        "신청": ["신청", "등록"],
        "취소": ["취소", "삭제"],
        "수정": ["수정", "변경"],
    }
    
    current_intent = context.get("slot_state", {}).get("intent")
    if current_intent and current_intent != action:
        # 명시적 키워드가 있으면 리셋
        for kw in action_keywords.get(action, []):
            if kw in query:
                context.pop("slot_state", None)
                context.pop("awaiting_slots", None)
                break


def _extract_selection_index(query: str) -> Optional[int]:
    """사용자 입력에서 선택 번호 추출."""
    import re
    
    # "1번", "첫번째", "^1$" 패턴
    patterns = [
        r'(\d+)\s*번',
        r'(\d+)\s*번째',
        r'^(\d+)$',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, query.strip())
        if match:
            return int(match.group(1))
    
    # 한글 숫자
    korean_numbers = {
        "첫": 1, "하나": 1, "일": 1,
        "둘": 2, "이": 2,
        "셋": 3, "삼": 3,
        "넷": 4, "사": 4,
        "다섯": 5, "오": 5,
    }
    
    for korean, num in korean_numbers.items():
        if korean in query:
            return num
    
    return None


# ========== Action Handlers ==========

async def _parse_leave_intent(
    query: str,
    employee_id: int,
    user_id: Optional[str],
    action: str,
    context: Dict[str, Any],
    awaiting_slots: Optional[List[str]],
    slot_decision: Optional[LeaveSlotDecision],
    slot_state_payload: Dict[str, Any]
) -> ParsedLeaveIntent:
    """쿼리에서 연차 의도 파싱."""
    # 1. 기존 slots에서 정보 추출
    existing_slots = slot_state_payload.get("slots", {})
    
    # 2. 엔티티 추출
    entities = await extract_leave_entities(query)
    
    # 3. 날짜 파싱
    start_date_str = existing_slots.get("start_date")
    end_date_str = existing_slots.get("end_date")
    total_days = existing_slots.get("total_days")
    
    if action == "신청" and not awaiting_slots:
        start_date_obj, end_date_obj = await parse_korean_date_with_llm(query)
        
        if start_date_obj:
            start_date_str = start_date_obj.strftime("%Y-%m-%d")
            # 반차가 아닌 경우 end_date가 없으면 None으로 설정 (missing에 추가되도록)
            leave_type = entities.get("leave_type") or existing_slots.get("leave_type", "연차")
            if leave_type == "반차":
                # 반차는 end_date를 start_date와 같게 설정
                end_date_str = start_date_str
                total_days = 0.5
            else:
                # 연차는 end_date가 없으면 None으로 설정하여 missing에 추가
                if end_date_obj:
                    end_date_str = end_date_obj.strftime("%Y-%m-%d")
                    days_diff = (end_date_obj - start_date_obj).days + 1
                    total_days = float(days_diff)
                else:
                    # end_date가 없으면 None으로 설정하여 missing에 추가
                    end_date_str = None
                    total_days = None
    
    # 4. 선택 인덱스 추출 (취소/수정 시)
    selection_index = None
    if awaiting_slots and ("cancel_selection" in awaiting_slots or "update_selection" in awaiting_slots):
        selection_index = _extract_selection_index(query)
    
    return ParsedLeaveIntent(
        action=action,
        leave_type=entities.get("leave_type", existing_slots.get("leave_type", "연차")),
        employee_id=employee_id,
        start_date=start_date_str,
        end_date=end_date_str,
        total_days=total_days or entities.get("total_days"),
        reason=entities.get("reason") or existing_slots.get("reason", "개인 사정"),
        request_id=selection_index,
    )


async def _handle_query_balance(
    parsed: ParsedLeaveIntent,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """연차 잔여 조회."""
    try:
        balance = await scheduler.db.get_leave_balance(parsed.employee_id)
        
        message = f"""📊 연차 잔여 현황 (사번: {parsed.employee_id})

총 연차: {balance.get('total_entitlement_days', 0)}일
사용: {balance.get('used_days', 0)}일
대기: {balance.get('pending_days', 0)}일
잔여: {balance.get('remaining_days', 0)}일"""
        
        return {"ok": True, "message": message, "balance": balance}
    
    except Exception as e:
        print(f"❌ 연차 조회 실패: {e}")
        return {"ok": False, "message": f"연차 조회 중 오류: {str(e)}"}


async def _handle_apply(
    parsed: ParsedLeaveIntent,
    user_id: str,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """연차 신청."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    import os
    
    try:
        # 1. 잔여 연차 확인
        balance = await scheduler.db.get_leave_balance(parsed.employee_id)
        if not balance:
            return {"ok": False, "message": "연차 잔여 정보를 찾을 수 없습니다."}
        
        # 연차 종류에 따른 필요 일수 계산
        total_days = parsed.total_days or 1.0
        if parsed.leave_type == "반차":
            total_days = 0.5
        
        # 잔여 연차 확인 (데이터베이스 컬럼명: remaining_days)
        remaining = balance.get('remaining_days', 0)
        # Decimal 타입을 float로 변환
        from decimal import Decimal
        if isinstance(remaining, Decimal):
            remaining = float(remaining)
        else:
            remaining = float(remaining) if remaining else 0.0
        print(f"[연차 잔여 확인] employee_id={parsed.employee_id}, remaining={remaining}, need={total_days}")
        
        if parsed.leave_type in ["연차", "반차"]:
            if remaining < total_days:
                return {
                    "ok": False,
                    "message": f"❌ 잔여 연차가 부족합니다.\n현재 잔여: {remaining}일, 필요: {total_days}일"
                }
        
        # 2. Google Calendar 일정 충돌 체크
        try:
            # 날짜 문자열을 datetime으로 변환
            start_date_obj = datetime.strptime(parsed.start_date, "%Y-%m-%d")
            end_date_obj = datetime.strptime(parsed.end_date, "%Y-%m-%d")
            
            # 하루 종일 이벤트로 체크하기 위해 종료일에 +1일
            check_end = end_date_obj + timedelta(days=1)
            
            local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
            start_aware = start_date_obj.replace(tzinfo=local_tz)
            end_aware = check_end.replace(tzinfo=local_tz)
            
            # Google Calendar에서 해당 기간의 일정 조회
            from agents.master_graphs.schedule.services.google_calendar_service import GoogleAuthRequired
            try:
                events = await scheduler.get_events(
                    user_id=user_id,
                    start_date=start_aware.astimezone(ZoneInfo("UTC")).strftime('%Y-%m-%dT%H:%M:%S') + "Z",
                    end_date=end_aware.astimezone(ZoneInfo("UTC")).strftime('%Y-%m-%dT%H:%M:%S') + "Z"
                )
                
                if events:
                    # 일정이 있으면 경고 메시지
                    conflict_titles = [e.get('title', '제목 없음') for e in events[:3]]
                    conflict_msg = "\n".join(f"- {title}" for title in conflict_titles)
                    return {
                        "ok": False,
                        "message": f"❌ 해당 기간에 일정이 있습니다:\n{conflict_msg}\n\n일정이 있는 날에는 연차를 신청할 수 없습니다."
                    }
            except GoogleAuthRequired as auth_error:
                # 인증 필요 시 경고 후 진행
                print(f"⚠️ Google Calendar 인증 필요 (일정 충돌 체크 생략): {auth_error}")
        except Exception as date_err:
            print(f"⚠️ 일정 충돌 체크 실패 (진행): {date_err}")
        
        # 3. 연차 신청
        leave_data = {
            'employee_id': parsed.employee_id,
            'leave_type': parsed.leave_type,
            'start_date': parsed.start_date,
            'end_date': parsed.end_date,
            'total_days': total_days,
            'reason': parsed.reason,
            'status': '대기',
        }
        
        result = await scheduler.create_leave_request(
            user_id=user_id,
            leave_data=leave_data
        )
        
        # 4. Google Calendar에 연차 일정 등록
        try:
            from zoneinfo import ZoneInfo
            from agents.master_graphs.schedule.services.google_calendar_service import GoogleAuthRequired
            
            start_date_obj = datetime.strptime(parsed.start_date, "%Y-%m-%d")
            end_date_obj = datetime.strptime(parsed.end_date, "%Y-%m-%d")
            
            local_tz = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))
            # 종료일 포함 (하루 종일 이벤트)
            start_aware = start_date_obj.replace(tzinfo=local_tz)
            end_aware = (end_date_obj + timedelta(days=1)).replace(tzinfo=local_tz)
            
            event_data = {
                "title": f"{parsed.leave_type}",
                "description": f"사유: {parsed.reason}",
                "start_time": start_aware.isoformat(),
                "end_time": end_aware.isoformat(),
                "schedule_type": "leave",
                "employee_id": parsed.employee_id,
            }
            
            created_event = await scheduler.create_event(
                user_id=user_id,
                event_data=event_data
            )
            
            if created_event:
                print(f"✅ 연차 일정이 Google Calendar에 등록되었습니다: {created_event.get('gcal_event', {}).get('id')}")
        except GoogleAuthRequired as auth_error:
            print(f"⚠️ Google Calendar 인증 필요 (연차는 DB에 저장됨): {auth_error}")
        except Exception as e:
            print(f"⚠️ Google Calendar 등록 실패 (연차는 DB에 저장됨): {e}")
        
        # 5. 잔여 연차 차감 (승인된 것으로 간주)
        if parsed.leave_type in ["연차", "반차"]:
            new_remaining = remaining - total_days
            current_used = balance.get('used_days', 0)
            # current_used도 Decimal일 수 있으므로 float로 변환
            if isinstance(current_used, Decimal):
                current_used = float(current_used)
            else:
                current_used = float(current_used) if current_used else 0.0
            new_used = current_used + total_days
            
            # 직접 SQL로 업데이트
            update_sql = """
            UPDATE leave_balance
            SET used_days = :used_days,
                remaining_days = :remaining_days,
                updated_at = NOW()
            WHERE employee_id = :employee_id AND year = :year
            """
            
            async with scheduler.db._session() as session:
                await session.execute(
                    text(update_sql),
                    {
                        "employee_id": parsed.employee_id,
                        "year": balance.get('year', datetime.now().year),
                        "used_days": new_used,
                        "remaining_days": new_remaining,
                    }
                )
                await session.commit()
            
            print(f"[연차 차감 완료] employee_id={parsed.employee_id}, used={new_used}, remaining={new_remaining}")
            
            return {
                "ok": True,
                "message": f"✅ {parsed.leave_type} 신청이 완료되었습니다!\n\n기간: {parsed.start_date} ~ {parsed.end_date}\n사용 일수: {total_days}일\n잔여 연차: {new_remaining}일",
                "request_id": result.get('request_id'),
            }
        else:
            return {
                "ok": True,
                "message": result.get('message', f'✅ {parsed.leave_type} 신청이 완료되었습니다.'),
                "request_id": result.get('request_id'),
            }
    
    except Exception as e:
        print(f"❌ 연차 신청 실패: {e}")
        return {"ok": False, "message": f"연차 신청 중 오류: {str(e)}"}


async def _handle_query_history(
    parsed: ParsedLeaveIntent,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """연차 사용 내역."""
    try:
        history = await scheduler.db.get_leave_list(
            employee_id=parsed.employee_id,
            leave_type=parsed.leave_type if parsed.leave_type != "연차" else None
        )
        
        if not history:
            message = "연차 사용 내역이 없습니다."
        else:
            message = f"📋 연차 사용 내역 (총 {len(history)}건):\n\n"
            for idx, item in enumerate(history[:10], 1):
                message += f"{idx}. {item.get('leave_type')} {item.get('total_days')}일\n"
                message += f"   기간: {item.get('start_date')} ~ {item.get('end_date')}\n"
                message += f"   상태: {item.get('status')}\n\n"
            
            if len(history) > 10:
                message += f"... 외 {len(history) - 10}건"
        
        return {"ok": True, "message": message, "history": history}
    
    except Exception as e:
        print(f"❌ 연차 내역 조회 실패: {e}")
        return {"ok": False, "message": f"연차 내역 조회 중 오류: {str(e)}"}


async def _handle_recommend(
    parsed: ParsedLeaveIntent,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """연차 추천."""
    return {
        "ok": True,
        "message": "연차 추천 기능은 준비 중입니다.\n현재 잔여 연차를 확인하려면 '연차 조회'를 사용해주세요."
    }


async def _handle_cancel(
    parsed: ParsedLeaveIntent,
    scheduler: HybridScheduler,
    context: Dict[str, Any],
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """연차 취소 처리."""
    # selection_index가 있으면 즉시 취소
    if parsed.request_id:
        candidates = context.get("cancel_candidates", [])
        if 1 <= parsed.request_id <= len(candidates):
            selected = candidates[parsed.request_id - 1]
            success = await scheduler.db.cancel_leave_request(selected['id'])
            
            if success:
                # Google Calendar 이벤트 삭제
                gcal_event_id = selected.get('gcal_event_id')
                if gcal_event_id:
                    try:
                        from agents.master_graphs.schedule.services.google_calendar_service import GoogleAuthRequired
                        # user_id가 없으면 employee_id를 user_id로 사용
                        event_user_id = user_id or str(parsed.employee_id)
                        delete_success = await scheduler.delete_event(event_user_id, gcal_event_id)
                        if delete_success:
                            print(f"✅ Google Calendar 이벤트 삭제 완료: {gcal_event_id}")
                        else:
                            print(f"⚠️ Google Calendar 이벤트 삭제 실패: {gcal_event_id}")
                    except GoogleAuthRequired as auth_error:
                        print(f"⚠️ Google Calendar 인증 필요 (이벤트 삭제 생략): {auth_error}")
                    except Exception as e:
                        print(f"⚠️ Google Calendar 이벤트 삭제 실패: {e}")
                
                context.pop("cancel_candidates", None)
                context.pop("awaiting_slots", None)
                return {
                    "ok": True,
                    "message": f"✅ 연차 신청이 취소되었습니다.\n\n"
                              f"취소된 내용:\n"
                              f"- 유형: {selected.get('leave_type')}\n"
                              f"- 기간: {selected.get('start_date')} ~ {selected.get('end_date')}\n"
                              f"- 일수: {selected.get('total_days')}일"
                }
        
        return {"ok": False, "message": "올바른 번호를 선택해주세요."}
    
    # 후보 목록 조회 (대기 + 승인된 연차 모두 조회)
    try:
        # 대기 중인 연차 조회
        pending_leaves = await scheduler.db.get_leave_list(
            employee_id=parsed.employee_id,
            status='대기',
            include_past=False
        )
        
        # 승인된 연차도 조회 (취소 가능)
        approved_leaves = await scheduler.db.get_leave_list(
            employee_id=parsed.employee_id,
            status='승인',
            include_past=False
        )
        
        # 두 목록 합치기
        all_leaves = pending_leaves + approved_leaves
        
        if not all_leaves:
            return {"ok": False, "message": "취소 가능한 연차 신청이 없습니다."}
        
        # 최근 순으로 정렬 (request_date 기준)
        all_leaves.sort(key=lambda x: x.get('request_date', ''), reverse=True)
        
        context["cancel_candidates"] = all_leaves[:10]
        context["awaiting_slots"] = ["cancel_selection"]
        
        message = "📋 취소 가능한 연차 신청 목록:\n\n"
        for idx, item in enumerate(all_leaves[:10], 1):
            status_text = "대기" if item.get('status') == '대기' else "승인"
            message += f"{idx}. {item.get('leave_type')} {item.get('total_days')}일 ({status_text})\n"
            message += f"   기간: {item.get('start_date')} ~ {item.get('end_date')}\n"
            message += f"   사유: {item.get('reason', '-')}\n\n"
        
        message += "\n취소할 연차 번호를 입력해주세요. (예: 1)"
        
        return {"ok": False, "message": message}
    
    except Exception as e:
        print(f"❌ 연차 취소 목록 조회 실패: {e}")
        return {"ok": False, "message": f"연차 취소 처리 중 오류: {str(e)}"}


async def _handle_update(
    parsed: ParsedLeaveIntent,
    scheduler: HybridScheduler,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """연차 수정 처리."""
    # 선택된 항목이 있으면 수정 실행
    if "selected_leave_for_update" in context:
        selected = context["selected_leave_for_update"]
        request_id = selected.get('id')
        
        # LLM으로 수정 내용 파싱
        entities = await extract_leave_entities(parsed.reason)  # reason에 query가 들어있음
        start_date_obj, end_date_obj = await parse_korean_date_with_llm(context.get("last_update_query", ""))
        
        update_data = {}
        
        if start_date_obj:
            update_data['start_date'] = start_date_obj.strftime("%Y-%m-%d")
            if end_date_obj:
                update_data['end_date'] = end_date_obj.strftime("%Y-%m-%d")
                days_diff = (end_date_obj - start_date_obj).days + 1
                update_data['total_days'] = float(days_diff)
            else:
                update_data['end_date'] = update_data['start_date']
                update_data['total_days'] = 0.5 if selected.get('leave_type') == '반차' else 1.0
        
        if entities.get('reason'):
            update_data['reason'] = entities['reason']
        
        if not update_data:
            return {"ok": False, "message": "수정할 내용을 찾을 수 없습니다. 날짜나 사유를 명확히 말씀해주세요."}
        
        success = await scheduler.db.update_leave_request(request_id, update_data)
        
        if success:
            context.pop("update_candidates", None)
            context.pop("selected_leave_for_update", None)
            context.pop("awaiting_slots", None)
            
            return {
                "ok": True,
                "message": f"✅ 연차 신청이 수정되었습니다.\n\n수정된 내용:\n" +
                          (f"- 기간: {update_data.get('start_date')} ~ {update_data.get('end_date')}\n" if 'start_date' in update_data else "") +
                          (f"- 일수: {update_data.get('total_days')}일\n" if 'total_days' in update_data else "") +
                          (f"- 사유: {update_data.get('reason')}\n" if 'reason' in update_data else "")
            }
        
        return {"ok": False, "message": "연차 수정에 실패했습니다."}
    
    # 첫 단계: 선택 요청
    if parsed.request_id:
        candidates = context.get("update_candidates", [])
        if 1 <= parsed.request_id <= len(candidates):
            selected = candidates[parsed.request_id - 1]
            context["selected_leave_for_update"] = selected
            
            return {
                "ok": False,
                "message": f"선택된 연차:\n"
                          f"- 유형: {selected.get('leave_type')}\n"
                          f"- 기간: {selected.get('start_date')} ~ {selected.get('end_date')}\n"
                          f"- 일수: {selected.get('total_days')}일\n"
                          f"- 사유: {selected.get('reason', '-')}\n\n"
                          f"변경할 내용을 입력해주세요.\n"
                          f"예: '12월 20일로 변경', '사유를 가족 행사로 변경'"
            }
    
    # 후보 목록 조회
    try:
        pending_leaves = await scheduler.db.get_leave_list(
            employee_id=parsed.employee_id,
            status='대기',
            include_past=False
        )
        
        if not pending_leaves:
            return {"ok": False, "message": "수정 가능한 연차 신청이 없습니다."}
        
        context["update_candidates"] = pending_leaves[:10]
        context["awaiting_slots"] = ["update_selection"]
        
        message = "📋 수정 가능한 연차 신청 목록:\n\n"
        for idx, item in enumerate(pending_leaves[:10], 1):
            message += f"{idx}. {item.get('leave_type')} {item.get('total_days')}일\n"
            message += f"   기간: {item.get('start_date')} ~ {item.get('end_date')}\n"
            message += f"   사유: {item.get('reason', '-')}\n\n"
        
        message += "\n수정할 연차 번호를 입력해주세요. (예: 1)"
        
        return {"ok": False, "message": message}
    
    except Exception as e:
        print(f"❌ 연차 수정 목록 조회 실패: {e}")
        return {"ok": False, "message": f"연차 수정 처리 중 오류: {str(e)}"}


def _determine_leave_action(query: str) -> str:
    """키워드 기반 액션 결정 (fallback용)."""
    if any(k in query for k in ["취소", "삭제", "없애", "지워"]):
        return "취소"
    elif any(k in query for k in ["수정", "변경", "바꿔", "고쳐"]):
        return "수정"
    elif any(k in query for k in ["잔여", "남은", "남았", "몇일", "얼마"]):
        return "조회"
    elif any(k in query for k in ["신청", "등록", "추가"]):
        return "신청"
    elif any(k in query for k in ["내역", "목록", "리스트", "사용 내역"]):
        return "내역"
    elif any(k in query for k in ["추천"]):
        return "추천"
    else:
        return "조회"


def _collect_application_missing(parsed: ParsedLeaveIntent, context: Dict[str, Any]) -> List[str]:
    """연차/반차 신청에 필요한 필수 슬롯 중 비어 있는 항목을 수집한다."""
    slot_state = context.get("slot_state") or {}
    slots = slot_state.get("slots") or {}
    
    missing: List[str] = []
    
    # 휴가 종류 확인
    if not parsed.leave_type and not slots.get("leave_type"):
        missing.append("leave_type")
    
    # 시작 날짜 확인
    if not parsed.start_date and not slots.get("start_date"):
        missing.append("start_date")
    
    # 종료 날짜 확인 (반차가 아닌 경우만 필수)
    leave_type = parsed.leave_type or slots.get("leave_type", "연차")
    if leave_type != "반차":
        # 반차가 아니면 end_date 필수
        if not parsed.end_date and not slots.get("end_date"):
            missing.append("end_date")
    # 반차는 end_date가 없으면 start_date와 같게 설정되므로 missing에 추가하지 않음
    
    # 사유 확인
    reason_candidate = (parsed.reason or "").strip()
    if not reason_candidate or reason_candidate == "개인 사정":
        reason_candidate = (slots.get("reason") or "").strip()
    if not reason_candidate or reason_candidate == "개인 사정":
        missing.append("reason")
    
    return missing


def _build_initial_application_prompt(missing_slots: List[str]) -> str:
    """연차/반차 신청을 위한 초기 프롬프트를 생성한다."""
    # YAML에서 템플릿 로드
    template = get_sub_agent_prompt("leave", "initial_application_template")
    
    # 누락된 슬롯 목록 생성
    missing_slots_list = "\n".join([
        f"- {APPLICATION_SLOT_DESCRIPTIONS.get(slot_name, slot_name)}"
        for slot_name in APPLICATION_SLOT_ORDER
        if slot_name in missing_slots
    ])
    
    # 템플릿 포맷팅
    return format_prompt_template(template, missing_slots_list=missing_slots_list)