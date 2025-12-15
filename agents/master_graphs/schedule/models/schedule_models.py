"""일정 관련 데이터 모델."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Literal, Dict, Any

from pydantic import BaseModel, Field


class ScheduleEvent(BaseModel):
    """일정 이벤트 정보."""
    event_id: Optional[str] = Field(None, description="이벤트 ID")
    title: str = Field(..., description="일정 제목")
    start_time: datetime = Field(..., description="시작 시간")
    end_time: datetime = Field(..., description="종료 시간")
    description: Optional[str] = Field(None, description="일정 설명")
    location: Optional[str] = Field(None, description="장소")
    attendees: List[str] = Field(default_factory=list, description="참석자 이메일 목록")
    
    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "evt_123",
                "title": "팀 미팅",
                "start_time": "2024-12-25T14:00:00",
                "end_time": "2024-12-25T15:00:00",
                "description": "주간 회의",
                "location": "회의실 A",
                "attendees": ["user@example.com"]
            }
        }


class ScheduleSlotDecision(BaseModel):
    """일정 슬롯 매니저가 반환하는 구조화된 판단."""

    action: Literal["조회", "생성", "삭제", "수정", "유지"] = Field(
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
                "action": "생성",
                "filled_slots": {
                    "title": "팀 미팅",
                    "start_time": "2024-12-25T14:00:00"
                },
                "missing_slots": ["end_time"],
                "followup_question": "일정 종료 시간을 알려주세요.",
                "should_ask": True
            }
        }


class ScheduleSlotState(BaseModel):
    """슬롯 매니저가 추적하는 전체 일정 상태."""

    intent: Literal["조회", "생성", "삭제", "수정"] = Field(
        ..., description="현재 사용자의 의도"
    )
    slots: Dict[str, Any] = Field(
        default_factory=dict,
        description="수집된 슬롯 값들을 저장하는 컨테이너"
    )
    delete_candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="삭제 가능한 일정 목록"
    )
    update_candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="수정 가능한 일정 목록"
    )
    awaiting_slots: List[str] = Field(
        default_factory=list,
        description="다음 턴에 질문해야 할 슬롯 목록"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "intent": "생성",
                "slots": {
                    "title": "팀 미팅",
                    "start_time": "2024-12-25T14:00:00"
                },
                "delete_candidates": [],
                "update_candidates": [],
                "awaiting_slots": ["end_time"]
            }
        }