"""하이브리드 스케줄러 - Google Calendar (Primary) + MariaDB (Secondary)."""

from __future__ import annotations

from typing import Optional, List, Dict, Any
from datetime import datetime

from agents.master_graphs.schedule.services.google_calendar_service import GoogleCalendarService
from agents.master_graphs.schedule.services.database_service import DatabaseService


class HybridScheduler:
    """Google Calendar와 MariaDB를 함께 사용하는 하이브리드 스케줄러."""
    
    def __init__(self):
        """초기화."""
        self.gcal = GoogleCalendarService()
        self.db = DatabaseService()
    
    # ==================== 일정 조회 ====================
    
    async def get_events(
        self,
        user_id: str,
        start_date: str,
        end_date: str
    ) -> List[Dict[str, Any]]:
        """일정 조회 (Google Calendar Primary).
        
        Args:
            user_id: 사용자 ID
            start_date: 시작일 (ISO 8601)
            end_date: 종료일 (ISO 8601)
            
        Returns:
            병합된 일정 목록
        """
        # 1. Google Calendar에서 일정 조회
        gcal_events = await self.gcal.get_events(
            user_id=user_id,
            time_min=start_date,
            time_max=end_date
        )
        
        # 2. DB에서 메타데이터 조회
        event_ids = [e.get('id') for e in gcal_events if e.get('id')]
        metadata_map = await self.db.get_event_metadata(event_ids)
        
        # 3. 데이터 병합
        enriched_events = []
        for event in gcal_events:
            event_id = event.get('id')
            metadata = metadata_map.get(event_id, {})
            
            enriched_events.append({
                'gcal_event_id': event_id,
                'title': event.get('summary', '제목 없음'),
                'description': event.get('description', ''),
                'start_time': event['start'].get('dateTime', event['start'].get('date')),
                'end_time': event['end'].get('dateTime', event['end'].get('date')),
                'location': event.get('location', ''),
                'attendees': [a.get('email') for a in event.get('attendees', [])],
                # DB 메타데이터 추가
                'meeting_room_name': metadata.get('meeting_room_name'),
                'meeting_room_id': metadata.get('meeting_room_id'),
                'approval_status': metadata.get('approval_status'),
            })
        
        return enriched_events
    
    # ==================== 일정 생성 ====================
    
    async def create_event(
        self,
        user_id: str,
        event_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """일정 생성 (Google Calendar + DB).
        
        Args:
            user_id: 사용자 ID
            event_data: 일정 데이터
                {
                    'title': '팀 회의',
                    'description': '주간 업무 회의',
                    'start_time': '2024-12-10T14:00:00',
                    'end_time': '2024-12-10T15:00:00',
                    'attendees': ['user1@example.com'],
                    'meeting_room_id': 1  # 선택사항
                }
                
        Returns:
            생성된 이벤트 정보
        """
        async with self.db.transaction():
            gcal_event = await self.gcal.create_event(user_id=user_id, event_data=event_data)

            if not gcal_event:
                raise Exception("Google Calendar 이벤트 생성 실패")  # noqa: TRY002

            await self.db.save_event_metadata({
                "gcal_event_id": gcal_event["id"],
                "user_id": user_id,
                "meeting_room_id": event_data.get("meeting_room_id"),
                "created_at": datetime.now(),
            })

            reservation_id: Optional[int] = None
            if event_data.get("meeting_room_id"):
                reservation_id = await self.db.create_room_reservation({
                    "room_id": event_data["meeting_room_id"],
                    "employee_id": int(user_id),
                    "schedule_id": event_data.get("schedule_id"),
                    "gcal_event_id": gcal_event["id"],
                    "title": event_data.get("title"),
                    "description": event_data.get("description"),
                    "start_time": datetime.fromisoformat(event_data["start_time"]),
                    "end_time": datetime.fromisoformat(event_data["end_time"]),
                    "participants": event_data.get("participant_count"),
                    "status": "CONFIRMED",
                })

            return {
                "gcal_event": gcal_event,
                "reservation_id": reservation_id,
            }
    
    # ==================== 일정 수정 ====================
    
    async def update_event(
        self,
        user_id: str,
        event_id: str,
        event_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """일정 수정 (Google Calendar + DB).
        
        Args:
            user_id: 사용자 ID
            event_id: Google Calendar 이벤트 ID
            event_data: 수정할 데이터
            
        Returns:
            수정된 이벤트 정보
        """
        updated_event = await self.gcal.update_event(
            user_id=user_id,
            event_id=event_id,
            event_data=event_data
        )
        
        updates: Dict[str, Any] = {}
        if event_data.get("meeting_room_id") is not None:
            updates["meeting_room_id"] = event_data["meeting_room_id"]
        if event_data.get("start_time"):
            updates["start_time"] = datetime.fromisoformat(event_data["start_time"])
        if event_data.get("end_time"):
            updates["end_time"] = datetime.fromisoformat(event_data["end_time"])
        if event_data.get("participant_count") is not None:
            updates["participants"] = event_data["participant_count"]
        if event_data.get("title"):
            updates["title"] = event_data["title"]
        if event_data.get("description") is not None:
            updates["description"] = event_data["description"]

        if updates:
            await self.db.update_room_reservation_by_event(event_id, updates)
        
        return updated_event
    
    # ==================== 일정 삭제 ====================
    
    async def delete_event(
        self,
        user_id: str,
        event_id: str
    ) -> bool:
        """일정 삭제 (Google Calendar + DB).
        
        Args:
            user_id: 사용자 ID
            event_id: Google Calendar 이벤트 ID
            
        Returns:
            삭제 성공 여부
        """
        success = await self.gcal.delete_event(
            user_id=user_id,
            event_id=event_id
        )
        
        if success:
            await self.db.delete_room_reservation_by_event(event_id)
        
        return success
    
    # ==================== 연차 관련 ====================
    
    async def create_leave_request(
        self,
        user_id: str,
        leave_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """연차 신청 (DB Primary, 승인 후 GCal 등록).
        
        Args:
            user_id: 사용자 ID
            leave_data: 연차 신청 데이터
            
        Returns:
            신청 결과
        """
        # 1. DB에 연차 신청 저장
        request_id = await self.db.create_leave(leave_data)
        
        # 2. 승인 대기 상태로 반환
        # (승인 후 webhook이나 배치 작업으로 Google Calendar 등록)
        return {
            'request_id': request_id,
            'status': 'pending_approval',
            'message': '연차 신청이 제출되었습니다. 승인 후 캘린더에 자동 등록됩니다.'
        }
    
    async def approve_leave_request(
        self,
        request_id: int,
        user_id: str
    ) -> bool:
        """연차 승인 후 Google Calendar 등록.
        
        Args:
            request_id: 연차 신청 ID
            user_id: 사용자 ID
            
        Returns:
            승인 성공 여부
        """
        # TODO: 
        # 1. DB에서 연차 신청 조회
        # 2. 상태를 '승인'으로 변경
        # 3. Google Calendar에 일정 등록
        
        return True