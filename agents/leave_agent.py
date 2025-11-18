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


def check_balance(employee_id: int, leave_type: Optional[Literal["연차", "반차", "병가", "경조사"]] = None) -> dict:
    """휴가 잔여일을 조회한다.
    
    Args:
        employee_id: 직원 ID
        leave_type: 휴가 유형 (연차, 반차, 병가, 경조사). None이면 전체 연차 조회.
    """
    ensure_balance_row(employee_id)
    # 최신 요청 합산 반영
    bal = update_balance_from_requests(employee_id)
    
    # 메시지 생성
    employee_name = bal.get("employee_name", "직원")
    total = bal.get("total_entitlement_days", 0)
    used = bal.get("used_days", 0)
    pending = bal.get("pending_days", 0)
    remaining = bal.get("remaining_days", 0)
    
    # leave_type에 따라 메시지 변경
    if leave_type:
        leave_type_name = leave_type
        if leave_type == "반차":
            leave_type_name = "반차"
        elif leave_type == "병가":
            leave_type_name = "병가"
        elif leave_type == "경조사":
            leave_type_name = "경조사"
        else:
            leave_type_name = "연차"
        
        message = (
            f"{employee_name}님의 {leave_type_name} 현황:\n"
            f"- 총 연차: {total}일\n"
            f"- 사용: {used}일\n"
            f"- 대기: {pending}일\n"
            f"- 잔여: {remaining}일"
        )
    else:
        message = (
            f"{employee_name}님의 연차 현황:\n"
            f"- 총 연차: {total}일\n"
            f"- 사용: {used}일\n"
            f"- 대기: {pending}일\n"
            f"- 잔여: {remaining}일"
        )
    
    return {"ok": True, "message": message, "balance": bal}


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
        # leave_type을 전달하여 특정 휴가 유형 조회 지원
        return check_balance(query.employee_id, leave_type=query.leave_type)
    if query.action == "신청":
        return apply_leave(query)
    return {"ok": False, "error": "지원하지 않는 action 입니다. (조회|신청)"}






