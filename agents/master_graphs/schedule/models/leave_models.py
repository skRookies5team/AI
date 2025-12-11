"""연차/반차 관련 데이터 모델."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class LeaveType(str, Enum):
    """휴가 유형."""
    ANNUAL = "연차"
    HALF_DAY = "반차"
    SICK = "병가"
    FAMILY_EVENT = "경조사"


class LeaveStatus(str, Enum):
    """휴가 신청 상태."""
    PENDING = "대기"
    APPROVED = "승인"
    REJECTED = "반려"
    CANCELLED = "취소"


class LeaveRequest(BaseModel):
    """휴가 신청 정보."""
    request_id: Optional[int] = Field(None, description="신청 ID")
    employee_id: int = Field(..., description="직원 ID")
    leave_type: LeaveType = Field(..., description="휴가 유형")
    start_date: date = Field(..., description="시작일")
    end_date: date = Field(..., description="종료일")
    total_days: float = Field(..., description="총 일수", gt=0)
    reason: str = Field("개인 사정", description="사유")
    status: LeaveStatus = Field(LeaveStatus.PENDING, description="상태")
    request_date: Optional[date] = Field(None, description="신청일")
    
    class Config:
        json_schema_extra = {
            "example": {
                "employee_id": 4001,
                "leave_type": "연차",
                "start_date": "2024-12-25",
                "end_date": "2024-12-26",
                "total_days": 2.0,
                "reason": "연말 휴가",
                "status": "대기"
            }
        }


class LeaveBalance(BaseModel):
    """연차 잔여 정보."""
    employee_id: int = Field(..., description="직원 ID")
    employee_name: Optional[str] = Field(None, description="직원 이름")
    total_entitlement_days: float = Field(..., description="총 연차 일수")
    used_days: float = Field(0.0, description="사용 일수")
    pending_days: float = Field(0.0, description="대기 일수")
    remaining_days: float = Field(..., description="잔여 일수")
    year: int = Field(..., description="연도")
    
    class Config:
        json_schema_extra = {
            "example": {
                "employee_id": 4001,
                "employee_name": "홍길동",
                "total_entitlement_days": 15.0,
                "used_days": 5.0,
                "pending_days": 2.0,
                "remaining_days": 8.0,
                "year": 2024
            }
        }