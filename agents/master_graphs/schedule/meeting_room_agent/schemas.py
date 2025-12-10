"""회의실 에이전트 데이터 스키마 정의."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, NotRequired, TypedDict

from pydantic import BaseModel, Field

__all__: list[str] = ["Room", "Reservation", "ReservationStatus", "AgentState"]


class Room(BaseModel):
    """회의실 기본 정보를 나타내는 모델."""

    id: int
    room_code: str | None = Field(default=None, max_length=50)
    name: str
    building: str | None = Field(default=None, max_length=100)
    floor: str | None = Field(default=None, max_length=50)
    capacity: int = Field(ge=1, description="수용 가능 인원 수")
    has_video: bool = Field(description="화상 회의 장비 보유 여부")
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReservationStatus(str, Enum):
    """예약 상태 열거형."""

    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    PENDING = "PENDING"


class Reservation(BaseModel):
    """회의실 예약 정보를 나타내는 모델."""

    id: int | None = None
    meeting_room_id: int
    employee_id: int
    schedule_id: int | None = None
    title: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=500)
    start_time: datetime
    end_time: datetime
    participants: int | None = Field(default=None, ge=1)
    status: ReservationStatus = ReservationStatus.CONFIRMED

    model_config = {"from_attributes": True}


class AgentState(TypedDict, total=False):
    """LangGraph 상태 컨테이너."""

    user_input: str
    intent: NotRequired[str]
    session_id: NotRequired[str]
    action: NotRequired[str]
    room: NotRequired[Room | dict[str, Any]]
    reservation: NotRequired[Reservation | dict[str, Any]]
    reservation_candidate: NotRequired[dict[str, Any]]
    lookup_candidate: NotRequired[dict[str, Any]]
    pending_field: NotRequired[str]
    pending_intent: NotRequired[str]
    response: NotRequired[str]
    recommended_rooms: NotRequired[list[dict[str, Any]]]
    start_time: NotRequired[datetime | str]
    end_time: NotRequired[datetime | str]
    participants: NotRequired[int]
    capacity: NotRequired[int]
    require_video: NotRequired[bool]
    building: NotRequired[str]
    meeting_room_id: NotRequired[int]
    # 날짜/시간 정보 (orchestrator_state에서 전달)
    current_date: NotRequired[str]
    current_time: NotRequired[str]
    current_datetime: NotRequired[str]
    current_weekday: NotRequired[str]

