"""연차/반차 관련 데이터 모델."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional, List, Literal, Dict, Any

from pydantic import BaseModel, Field


class LeaveStatus(str, Enum):
    """연차 상태."""
    PENDING = "대기"
    APPROVED = "승인"
    REJECTED = "반려"
    CANCELLED = "취소"


class LeaveType(str, Enum):
    """연차 유형."""
    ANNUAL = "연차"
    HALF_DAY = "반차"
    SICK = "병가"
    FAMILY = "경조사"


class LeaveBalance(BaseModel):
    """연차 잔여 현황."""
    employee_id: int = Field(..., description="사원 ID")
    total_leave: float = Field(..., description="총 연차 일수")
    used_leave: float = Field(0.0, description="사용한 연차 일수")
    pending_leave: float = Field(0.0, description="대기 중인 연차 일수")
    remaining_leave: float = Field(..., description="잔여 연차 일수")
    
    class Config:
        json_schema_extra = {
            "example": {
                "employee_id": 4003,
                "total_leave": 15.0,
                "used_leave": 3.0,
                "pending_leave": 1.0,
                "remaining_leave": 11.0
            }
        }


class LeaveRequest(BaseModel):
    """연차 신청 정보."""
    request_id: Optional[int] = Field(None, description="신청 ID")
    employee_id: int = Field(..., description="사원 ID")
    leave_type: LeaveType = Field(..., description="연차 유형")
    start_date: date = Field(..., description="시작일")
    end_date: date = Field(..., description="종료일")
    total_days: float = Field(..., description="총 일수", ge=0.5)
    reason: str = Field(..., description="사유")
    status: LeaveStatus = Field(LeaveStatus.PENDING, description="신청 상태")
    
    class Config:
        json_schema_extra = {
            "example": {
                "employee_id": 4003,
                "leave_type": "연차",
                "start_date": "2024-12-25",
                "end_date": "2024-12-26",
                "total_days": 2.0,
                "reason": "개인 사정",
                "status": "대기"
            }
        }


class LeaveSlotDecision(BaseModel):
    """연차 슬롯 매니저가 반환하는 구조화된 판단."""

    action: Literal["조회", "신청", "내역", "추천", "취소", "수정", "유지"] = Field(
        ..., description="다음에 수행해야 할 기본 액션"
    )
    filled_slots: Dict[str, Any] = Field(
        default_factory=dict,
        description="현재까지 수집된 슬롯 값"
    )
    missing_slots: List[str] = Field(
        default_factory=list,
        description="추가로 수집이 필요한 슬롯 이름 목록"
    )
    followup_question: Optional[str] = Field(
        default=None,
        description="사용자에게 추가로 물어볼 질문"
    )
    should_ask: bool = Field(
        False,
        description="followup_question을 사용자에게 바로 전달해야 하는지 여부"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "action": "신청",
                "filled_slots": {
                    "leave_type": "연차",
                    "start_date": "2024-12-25"
                },
                "missing_slots": ["end_date", "reason"],
                "followup_question": "연차 사유를 알려주세요.",
                "should_ask": True
            }
        }


class LeaveSlotState(BaseModel):
    """슬롯 매니저가 추적하는 전체 연차 상태."""

    intent: Literal["조회", "신청", "내역", "추천", "취소", "수정"] = Field(
        ..., description="현재 사용자의 의도"
    )
    slots: Dict[str, Any] = Field(
        default_factory=dict,
        description="수집된 슬롯 값들을 저장하는 컨테이너"
    )
    cancel_candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="취소 가능한 연차 목록"
    )
    update_candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="수정 가능한 연차 목록"
    )
    awaiting_slots: List[str] = Field(
        default_factory=list,
        description="다음 턴에 질문해야 할 슬롯 목록"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "intent": "신청",
                "slots": {
                    "leave_type": "연차",
                    "start_date": "2024-12-25"
                },
                "cancel_candidates": [],
                "update_candidates": [],
                "awaiting_slots": ["reason"]
            }
        }