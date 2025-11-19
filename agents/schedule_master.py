from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from .leave_agent import LeaveQuery, handle_leave
from .llm_router import classify_intent_with_llm
from .meeting_agent import meeting_room_query


LeaveType = Literal["연차", "반차", "병가", "경조사"]


@dataclass
class ScheduleIntent:
    kind: Literal["leave", "calendar", "meeting"]
    # leave
    action: Optional[Literal["조회", "신청"]] = None
    employee_id: Optional[int] = None
    leave_type: Optional[LeaveType] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_days: Optional[float] = None
    reason: Optional[str] = None
    # stubs
    details: Optional[str] = None


def parse_schedule_intent(user_text: str) -> ScheduleIntent:
    """LLM 기반 의도 분류 (키워드 fallback 포함)."""
    t = user_text.strip()
    
    # LLM 기반 라우팅 시도
    llm_intent = classify_intent_with_llm(t, route_type="schedule", fallback_route=None)
    
    if llm_intent == "leave":
        # LLM이 leave로 분류한 경우, 키워드 파싱 진행
        pass
    elif llm_intent == "meeting":
        return ScheduleIntent(kind="meeting", details=t)
    elif llm_intent == "calendar":
        return ScheduleIntent(kind="calendar", details=t)
    
    # 키워드 기반 fallback (leave 파싱)
    if any(k in t for k in ["연차", "반차", "병가", "경조사", "휴가"]):
        leave_type: LeaveType = "연차"
        for k in ["연차", "반차", "병가", "경조사"]:
            if k in t:
                leave_type = k  # type: ignore[assignment]
                break
        action = "조회" if ("조회" in t or "얼마" in t or "잔여" in t) else "신청"
        # 직원번호 추출
        m_emp = re.search(r"(사번|직원번호|employee_id)\s*[:= ]\s*(\d+)", t)
        emp = int(m_emp.group(2)) if m_emp else None
        # 일수/반차 추출
        total_days = None
        if "반차" in t:
            total_days = 0.5
        m_days = re.search(r"(\d+(\.\d+)?)\s*일", t)
        if m_days:
            total_days = float(m_days.group(1))
        # 날짜 추출 (YYYY-MM-DD)
        m_start = re.search(r"시작[:= ]?(\d{4}-\d{2}-\d{2})", t)
        m_end = re.search(r"종료[:= ]?(\d{4}-\d{2}-\d{2})", t)
        reason = None
        m_reason = re.search(r"(사유|reason)[:= ](.+)$", t)
        if m_reason:
            reason = m_reason.group(2).strip()
        return ScheduleIntent(
            kind="leave",
            action=action,  # type: ignore[arg-type]
            employee_id=emp,
            leave_type=leave_type,
            start_date=m_start.group(1) if m_start else None,
            end_date=m_end.group(1) if m_end else None,
            total_days=total_days,
            reason=reason,
        )

    if any(k in t for k in ["회의실", "예약", "회의", "미팅룸", "meeting"]):
        return ScheduleIntent(kind="meeting", details=t)

    if any(k in t for k in ["일정", "캘린더", "스케줄"]):
        return ScheduleIntent(kind="calendar", details=t)

    # 기본은 일정(스케줄)로 보냄
    return ScheduleIntent(kind="calendar", details=t)


def schedule_master(user_text: str, context: Optional[Dict[str, Any]] = None) -> dict:
    ctx = context or {}
    has_reservation_context = bool(
        ctx.get("reservation")
        or ctx.get("reservation_candidate")
        or ctx.get("pending_intent") in {"예약", "수정", "취소", "조회"}
    )
    if has_reservation_context:
        intent = ScheduleIntent(kind="meeting", details=user_text)
    else:
        intent = parse_schedule_intent(user_text)

    if intent.kind == "leave":
        if intent.employee_id is None:
            return {"ok": False, "error": "사번(employee_id)을 제공해 주세요."}
        if intent.action == "조회":
            q = LeaveQuery(employee_id=intent.employee_id, action="조회")
            return handle_leave(q)
        # 신청
        q = LeaveQuery(
            employee_id=intent.employee_id,
            action="신청",
            leave_type=intent.leave_type or "연차",
            start_date=intent.start_date,
            end_date=intent.end_date,
            total_days=intent.total_days,
            reason=intent.reason or "개인 사정",
        )
        return handle_leave(q)

    if intent.kind == "meeting":
        request_context: Dict[str, Any] = dict(context or {})
        return meeting_room_query(intent.details or user_text, context=request_context)

    # calendar
    return {
        "ok": True,
        "message": "일정 관리 요청이 접수되었습니다. 캘린더 연동 후에 동기화합니다.",
        "echo": intent.details,
    }





