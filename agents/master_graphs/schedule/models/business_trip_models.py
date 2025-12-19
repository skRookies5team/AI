"""출장 관련 데이터 모델."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Literal, Dict, Any

from pydantic import BaseModel, Field


class BusinessTripRequest(BaseModel):
    """출장 신청 정보."""
    trip_id: Optional[int] = Field(None, description="출장 ID")
    employee_id: int = Field(..., description="사원 ID")
    trip_title: Optional[str] = Field(None, description="출장 제목")
    trip_purpose: Optional[str] = Field(None, description="출장 목적")
    destination: Optional[str] = Field(None, description="출장 목적지")
    start_time: datetime = Field(..., description="출장 시작 일시")
    end_time: datetime = Field(..., description="출장 종료 일시")
    document_id: Optional[int] = Field(None, description="연결된 문서 ID")
    alert_time: Optional[datetime] = Field(None, description="알림 시간")
    
    class Config:
        json_schema_extra = {
            "example": {
                "employee_id": 4003,
                "trip_title": "제주도 워크샵",
                "trip_purpose": "팀 워크샵 참석",
                "destination": "제주도",
                "start_time": "2025-01-15T09:00:00",
                "end_time": "2025-01-17T18:00:00",
            }
        }


class BusinessTripSlotDecision(BaseModel):
    """출장 슬롯 매니저가 반환하는 구조화된 판단."""

    action: Literal["조회", "신청", "내역", "취소", "수정", "유지"] = Field(
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
                    "start_time": "2025-01-15T09:00:00",
                    "end_time": "2025-01-17T18:00:00",
                    "destination": "제주도"
                },
                "missing_slots": ["trip_purpose"],
                "followup_question": "출장 목적을 알려주세요.",
                "should_ask": True
            }
        }

