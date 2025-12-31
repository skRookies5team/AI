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
        """일정 조회 (Google Calendar + DB schedule 테이블 병합, 중복 제거).
        
        Args:
            user_id: 사용자 ID
            start_date: 시작일 (ISO 8601)
            end_date: 종료일 (ISO 8601)
            
        Returns:
            병합된 일정 목록 (중복 제거됨)
        """
        # user_id를 employee_id로 변환 시도
        try:
            employee_id = int(user_id)
        except (ValueError, TypeError):
            employee_id = None
        
        # 1. Google Calendar에서 일정 조회 (연동된 경우)
        gcal_events = []
        gcal_event_ids = set()  # 중복 제거용
        
        try:
            gcal_events = await self.gcal.get_events(
                user_id=user_id,
                time_min=start_date,
                time_max=end_date
            )
            # Google Calendar 이벤트 ID 집합 생성
            gcal_event_ids = {e.get('id') for e in gcal_events if e.get('id')}
        except Exception as e:
            # Google Calendar 연동이 안 되어 있거나 오류 발생 시 무시하고 계속 진행
            print(f"⚠️ Google Calendar 조회 실패 (DB 조회 계속 진행): {e}")
        
        # 2. DB schedule 테이블에서 일정 조회
        db_events = []
        if employee_id:
            try:
                start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                
                db_schedules = await self.db.get_schedules(
                    employee_id=employee_id,
                    start_date=start_dt,
                    end_date=end_dt
                )
                
                # DB 일정을 Google Calendar 형식으로 변환
                for schedule in db_schedules:
                    gcal_event_id = schedule.get('gcal_event_id')
                    
                    # 중복 제거: gcal_event_id가 있고 Google Calendar에도 있으면 제외
                    if gcal_event_id and gcal_event_id in gcal_event_ids:
                        continue  # Google Calendar에서 이미 조회했으므로 제외
                    
                    # DB에만 있는 일정만 추가
                    db_events.append({
                        'id': gcal_event_id,  # gcal_event_id가 있으면 사용, 없으면 None
                        'schedule_id': schedule.get('schedule_id'),  # DB 일정 ID
                        'summary': schedule.get('title', '제목 없음'),
                        'description': schedule.get('description', ''),
                        'start': {
                            'dateTime': schedule['start_time'].isoformat() if isinstance(schedule['start_time'], datetime) else schedule['start_time'],
                            'date': None
                        },
                        'end': {
                            'dateTime': schedule['end_time'].isoformat() if isinstance(schedule['end_time'], datetime) else schedule['end_time'],
                            'date': None
                        },
                        'location': schedule.get('location', ''),
                        'attendees': [],
                        'from_db': True,  # DB에서 온 일정임을 표시
                        'schedule_type': schedule.get('schedule_type'),
                        'status': schedule.get('status'),
                    })
            except Exception as e:
                print(f"⚠️ DB 일정 조회 실패: {e}")
        
        # 3. Google Calendar 이벤트 메타데이터 조회
        event_ids = [e.get('id') for e in gcal_events if e.get('id')]
        metadata_map = await self.db.get_event_metadata(event_ids) if event_ids else {}
        
        # 4. 데이터 병합
        enriched_events = []
        
        # 4-1. Google Calendar 이벤트 추가
        for event in gcal_events:
            event_id = event.get('id')
            metadata = metadata_map.get(event_id, {})
            
            enriched_events.append({
                'gcal_event_id': event_id,
                'id': event_id,
                'title': event.get('summary', '제목 없음'),
                'summary': event.get('summary', '제목 없음'),
                'description': event.get('description', ''),
                'start_time': event['start'].get('dateTime', event['start'].get('date')),
                'start': event.get('start'),
                'end_time': event['end'].get('dateTime', event['end'].get('date')),
                'end': event.get('end'),
                'location': event.get('location', ''),
                'attendees': [a.get('email') for a in event.get('attendees', [])],
                'meeting_room_name': metadata.get('meeting_room_name'),
                'meeting_room_id': metadata.get('meeting_room_id'),
                'schedule_type': metadata.get('schedule_type'),
                'approval_status': metadata.get('approval_status'),
                'from_gcal': True,  # Google Calendar에서 온 일정임을 표시
            })
        
        # 4-2. DB 일정 추가 (Google Calendar에 없는 것만)
        for db_event in db_events:
            event_id = db_event.get('id')
            enriched_events.append({
                'gcal_event_id': event_id,  # None일 수 있음 (DB에만 있는 일정)
                'schedule_id': db_event.get('schedule_id'),
                'id': event_id or f"db_{db_event.get('schedule_id')}",  # DB 일정은 임시 ID 사용
                'title': db_event.get('summary', '제목 없음'),
                'summary': db_event.get('summary', '제목 없음'),
                'description': db_event.get('description', ''),
                'start_time': db_event['start'].get('dateTime'),
                'start': db_event.get('start'),
                'end_time': db_event['end'].get('dateTime'),
                'end': db_event.get('end'),
                'location': db_event.get('location', ''),
                'attendees': [],
                'schedule_type': db_event.get('schedule_type'),
                'status': db_event.get('status'),
                'from_db': True,
            })
        
        # 5. 시작 시간 기준으로 정렬
        enriched_events.sort(key=lambda x: x.get('start_time', ''))
        
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
                    'meeting_room_id': 1,  # 선택사항
                    'schedule_type': 'general',  # 'meeting_room', 'general', 'business_trip', 'leave'
                    'employee_id': 4003,  # schedule 테이블 저장용
                }
                
        Returns:
            생성된 이벤트 정보
        """
        async with self.db.transaction():
            # 1. Google Calendar에 이벤트 생성
            gcal_event = await self.gcal.create_event(user_id=user_id, event_data=event_data)

            if not gcal_event:
                raise Exception("Google Calendar 이벤트 생성 실패")  # noqa: TRY002

            gcal_event_id = gcal_event["id"]
            
            # 2. event_metadata 저장
            schedule_type = event_data.get("schedule_type", "general")
            await self.db.save_event_metadata({
                "gcal_event_id": gcal_event_id,
                "user_id": user_id,
                "meeting_room_id": event_data.get("meeting_room_id"),
                "schedule_type": schedule_type,
                "created_at": datetime.now(),
            })

            employee_id = event_data.get("employee_id")
            schedule_id: Optional[int] = None
            reservation_id: Optional[int] = None

            # 3. schedule_type에 따라 각 테이블에만 저장
            if schedule_type == "meeting_room":
                # 회의실 예약 → meeting_room_reservation 테이블만 저장
                if event_data.get("meeting_room_id"):
                    reservation_id = await self.db.create_room_reservation({
                        "room_id": event_data["meeting_room_id"],
                        "employee_id": employee_id or int(user_id),
                        "schedule_id": None,  # schedule 테이블에 저장하지 않으므로 None
                        "gcal_event_id": gcal_event_id,
                        "title": event_data.get("title"),
                        "description": event_data.get("description"),
                        "start_time": datetime.fromisoformat(event_data["start_time"]),
                        "end_time": datetime.fromisoformat(event_data["end_time"]),
                        "participants": event_data.get("participant_count"),
                        "status": "CONFIRMED",
                    })
            elif schedule_type == "business_trip":
                # 출장 → business_trip 테이블만 저장 (이미 business_trip_nodes.py에서 별도로 저장됨)
                # 여기서는 저장하지 않음
                pass
            elif schedule_type == "leave":
                # 연차/반차 → leave_request 테이블만 저장 (이미 leave_nodes.py에서 별도로 저장됨)
                # 여기서는 저장하지 않음
                pass
            else:  # general
                # 일반 개인 스케줄 → schedule 테이블만 저장
                if employee_id:
                    try:
                        schedule_id = await self.db.create_schedule({
                            "employee_id": employee_id,
                            "title": event_data.get("title", ""),
                            "description": event_data.get("description"),
                            "start_time": datetime.fromisoformat(event_data["start_time"]),
                            "end_time": datetime.fromisoformat(event_data["end_time"]),
                            "schedule_type": schedule_type,
                            "location": event_data.get("location"),
                            "gcal_event_id": gcal_event_id,
                            "alert_time": None,
                            "status": "active",
                        })
                    except Exception as e:
                        print(f"⚠️ schedule 테이블 저장 실패 (계속 진행): {e}")

            return {
                "gcal_event": gcal_event,
                "schedule_id": schedule_id,
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
            # meeting_room_reservation 삭제
            await self.db.delete_room_reservation_by_event(event_id)
            # schedule 테이블에서 일정 삭제
            await self.db.delete_schedule_by_gcal_event_id(event_id)
            # event_metadata 삭제 (선택사항, 나중에 추가 가능)
        
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