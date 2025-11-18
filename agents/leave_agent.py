from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .leave_data import (
    get_balance,
    update_balance_from_requests,
    create_leave_request,
    ensure_balance_row,
)


@dataclass
class LeaveQuery:
    employee_id: int
    action: Literal["조회", "신청"]
    leave_type: Literal["연차", "반차", "병가", "경조사"] = "연차"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_days: Optional[float] = None
    reason: str = "개인 사정"


def check_balance(employee_id: int) -> dict:
    ensure_balance_row(employee_id)
    # 최신 요청 합산 반영
    bal = update_balance_from_requests(employee_id)
    return {"ok": True, "balance": bal}


def can_apply(employee_id: int, total_days: float) -> dict:
    ensure_balance_row(employee_id)
    bal = update_balance_from_requests(employee_id)
    can = float(bal["remaining_days"]) >= float(total_days)
    return {"ok": True, "can_apply": can, "balance": bal}


def apply_leave(q: LeaveQuery) -> dict:
    # 검증
    if q.total_days is None or q.start_date is None or q.end_date is None:
        return {"ok": False, "error": "신청에는 start_date, end_date, total_days가 필요합니다."}

    # 가능 여부 확인
    pre = can_apply(q.employee_id, float(q.total_days))
    if not pre["can_apply"]:
        return {
            "ok": False,
            "error": "잔여 연차 부족으로 신청할 수 없습니다.",
            "balance": pre["balance"],
        }

    result = create_leave_request(
        employee_id=q.employee_id,
        leave_type=q.leave_type,
        start_date=q.start_date,
        end_date=q.end_date,
        total_days=float(q.total_days),
        reason=q.reason,
        status="대기",
    )
    return {"ok": True, **result}


def handle_leave(query: LeaveQuery) -> dict:
    if query.action == "조회":
        return check_balance(query.employee_id)
    if query.action == "신청":
        return apply_leave(query)
    return {"ok": False, "error": "지원하지 않는 action 입니다. (조회|신청)"}








