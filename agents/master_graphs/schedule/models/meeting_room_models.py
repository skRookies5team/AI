"""회의실 관련 데이터 모델."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, List, Literal, Dict, Any

from pydantic import BaseModel, Field


class ReservationStatus(str, Enum):
    """예약 상태."""
    PENDING = "대기"
    CONFIRMED = "확정"
    CANCELLED = "취소"


class MeetingRoom(BaseModel):
    """회의실 정보."""
    room_id: int = Field(..., description="회의실 ID")
    room_name: str = Field(..., description="회의실 이름")
    location: str = Field(..., description="위치")
    capacity: int = Field(..., description="수용 인원")
    floor: Optional[int] = Field(None, description="층")
    has_projector: bool = Field(False, description="프로젝터 유무")
    has_whiteboard: bool = Field(False, description="화이트보드 유무")
    has_video_conf: bool = Field(False, description="화상회의 장비 유무")
    description: Optional[str] = Field(None, description="설명")
    
    class Config:
        json_schema_extra = {
            "example": {
                "room_id": 1,
                "room_name": "회의실 A",
                "location": "본관 3층",
                "capacity": 10,
                "floor": 3,
                "has_projector": True,
                "has_whiteboard": True,
                "has_video_conf": True
            }
        }


class MeetingRoomReservation(BaseModel):
    """회의실 예약 정보."""
    reservation_id: Optional[int] = Field(None, description="예약 ID")
    room_id: int = Field(..., description="회의실 ID")
    employee_id: int = Field(..., description="예약자 ID")
    event_title: str = Field(..., description="일정 제목")
    start_time: datetime = Field(..., description="시작 시간")
    end_time: datetime = Field(..., description="종료 시간")
    attendees_count: int = Field(1, description="참석 인원", ge=1)
    purpose: Optional[str] = Field(None, description="사용 목적")
    status: ReservationStatus = Field(ReservationStatus.PENDING, description="예약 상태")
    gcal_event_id: Optional[str] = Field(None, description="Google Calendar 이벤트 ID")
    created_at: Optional[datetime] = Field(None, description="생성 시간")
    
    class Config:
        json_schema_extra = {
            "example": {
                "room_id": 1,
                "employee_id": 4001,
                "event_title": "팀 회의",
                "start_time": "2024-12-10T14:00:00",
                "end_time": "2024-12-10T15:00:00",
                "attendees_count": 5,
                "purpose": "주간 업무 회의",
                "status": "확정"
            }
        }


class MeetingRoomSearchCriteria(BaseModel):
    """회의실 검색 조건."""
    start_time: datetime = Field(..., description="희망 시작 시간")
    end_time: datetime = Field(..., description="희망 종료 시간")
    capacity: Optional[int] = Field(None, description="필요 인원")
    floor: Optional[int] = Field(None, description="희망 층")
    has_projector: Optional[bool] = Field(None, description="프로젝터 필요 여부")
    has_whiteboard: Optional[bool] = Field(None, description="화이트보드 필요 여부")
    has_video_conf: Optional[bool] = Field(None, description="화상회의 장비 필요 여부")
    
    class Config:
        json_schema_extra = {
            "example": {
                "start_time": "2024-12-10T14:00:00",
                "end_time": "2024-12-10T15:00:00",
                "capacity": 8,
                "has_projector": True
            }
        }


class MeetingSlotDecision(BaseModel):
    """회의실 슬롯 매니저가 반환하는 구조화된 판단."""

    action: Literal["예약", "취소", "조회", "추천", "유지"] = Field(
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
                "action": "예약",
                "filled_slots": {
                    "start_time": "2025-12-10T14:00:00",
                    "participants": 5
                },
                "missing_slots": ["end_time", "room"],
                "followup_question": "회의 종료 시간을 알려주세요.",
                "should_ask": True
            }
        }


class MeetingSlotState(BaseModel):
    """슬롯 매니저가 추적하는 전체 회의실 상태."""

    intent: Literal["예약", "취소", "조회", "추천"] = Field(
        ..., description="현재 사용자의 의도"
    )
    slots: Dict[str, Any] = Field(
        default_factory=dict,
        description="수집된 슬롯 값들을 저장하는 컨테이너"
    )
    recommended_rooms: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="최근 추천된 회의실 리스트"
    )
    pending_reservation: Optional[Dict[str, Any]] = Field(
        default=None,
        description="예약 확정 전 임시 저장된 데이터"
    )
    awaiting_slots: List[str] = Field(
        default_factory=list,
        description="다음 턴에 질문해야 할 슬롯 목록"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "intent": "예약",
                "slots": {
                    "start_time": "2025-12-10T14:00:00",
                    "participants": 5
                },
                "recommended_rooms": [],
                "pending_reservation": None,
                "awaiting_slots": ["end_time"]
            }
        }