"""일정 관련 데이터 모델."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """일정 유형."""
    MEETING = "회의"
    BUSINESS_TRIP = "출장"
    TASK = "업무"
    VACATION = "휴가"
    HALF_DAY = "반차"
    SICK_LEAVE = "병가"
    OTHER = "기타"


class ScheduleEvent(BaseModel):
    """일정 이벤트."""
    event_id: Optional[str] = Field(None, description="이벤트 ID (Google Calendar ID)")
    schedule_id: Optional[int] = Field(None, description="DB 스케줄 ID")
    employee_id: int = Field(..., description="직원 ID")
    title: str = Field(..., description="일정 제목")
    description: Optional[str] = Field(None, description="상세 설명")
    start_time: datetime = Field(..., description="시작 시간")
    end_time: datetime = Field(..., description="종료 시간")
    event_type: EventType = Field(EventType.OTHER, description="일정 유형")
    location: Optional[str] = Field(None, description="장소")
    meeting_room_id: Optional[int] = Field(None, description="회의실 ID")
    attendees: List[str] = Field(default_factory=list, description="참석자 이메일 목록")
    is_all_day: bool = Field(False, description="종일 일정 여부")
    
    class Config:
        json_schema_extra = {
            "example": {
                "employee_id": 4001,
                "title": "프로젝트 킥오프 미팅",
                "description": "Q1 프로젝트 계획 수립",
                "start_time": "2024-12-10T10:00:00",
                "end_time": "2024-12-10T11:30:00",
                "event_type": "회의",
                "location": "회의실 A",
                "meeting_room_id": 1,
                "attendees": ["user1@company.com", "user2@company.com"]
            }
        }


class ScheduleFilter(BaseModel):
    """일정 조회 필터."""
    employee_id: Optional[int] = Field(None, description="직원 ID")
    employee_names: Optional[List[str]] = Field(None, description="직원 이름 목록")
    start_date: Optional[datetime] = Field(None, description="시작일")
    end_date: Optional[datetime] = Field(None, description="종료일")
    event_types: Optional[List[EventType]] = Field(None, description="일정 유형 필터")
    keyword: Optional[str] = Field(None, description="키워드 검색")
    meeting_room_id: Optional[int] = Field(None, description="회의실 ID")
    
    class Config:
        json_schema_extra = {
            "example": {
                "employee_id": 4001,
                "start_date": "2024-12-01T00:00:00",
                "end_date": "2024-12-31T23:59:59",
                "event_types": ["회의", "출장"],
                "keyword": "프로젝트"
            }
        }