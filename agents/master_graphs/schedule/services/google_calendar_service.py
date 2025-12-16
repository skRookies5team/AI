"""Google Calendar API 서비스."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional, List, Dict, Any

from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from agents.session_manager import get_session_manager  # [OAuth] 사용자별 토큰 조회용


class GoogleAuthRequired(Exception):
    """[OAuth] Google 계정 연동이 필요함을 나타내는 예외."""

    def __init__(self, user_id: str, auth_url: str) -> None:
        super().__init__(f"Google authentication required for user {user_id}")
        self.user_id = user_id
        self.auth_url = auth_url


class GoogleCalendarService:
    """Google Calendar API 래퍼 클래스."""
    
    SCOPES = ['https://www.googleapis.com/auth/calendar']
    
    def __init__(self):
        """초기화."""
        self.credentials = None
        self.service = None
        
    async def _get_credentials(self, user_id: Optional[str] = None) -> Credentials:
        """사용자별 OAuth 자격증명 생성 (DB + Redis 통합).
        
        플로우:
        1. Redis에서 access_token 캐시 확인 (1시간)
        2. 없으면 DB에서 refresh_token 조회
        3. refresh_token으로 access_token 갱신
        4. 갱신된 access_token을 Redis에 캐시
        """
        if not user_id:
            # user_id가 없으면 service account 사용 (기존 로직)
            service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
            if service_account_file and os.path.exists(service_account_file):
                return service_account.Credentials.from_service_account_file(
                    service_account_file,
                    scopes=self.SCOPES
                )
            raise ValueError("Google Calendar 인증 정보가 없습니다.")
        
        # user_id를 employee_id로 변환
        try:
            employee_id = int(user_id)
        except ValueError:
            # user_id가 숫자가 아니면 그대로 사용 (email 등)
            print(f"⚠️ user_id가 숫자가 아닙니다. 그대로 사용: {user_id}")
            employee_id = None
        
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise ValueError("Google OAuth 클라이언트 정보가 설정되지 않았습니다.")
        
        # 1️⃣ Redis에서 access_token 캐시 확인
        session_manager = await get_session_manager()
        if session_manager:
            cached_tokens = await session_manager.get_google_tokens(user_id)
            if cached_tokens and cached_tokens.get("access_token"):
                # Redis에 캐시된 access_token 사용
                return Credentials(
                    token=cached_tokens["access_token"],
                    refresh_token=cached_tokens.get("refresh_token"),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=self.SCOPES,
                )
        
        # 2️⃣ DB에서 refresh_token 조회
        if employee_id is not None:
            from .google_token_service import get_token_service
            token_service = get_token_service()
            credentials_data = await token_service.get_google_credentials(employee_id)
            
            if credentials_data and credentials_data.get("refresh_token"):
                refresh_token = credentials_data["refresh_token"]
                
                # 3️⃣ refresh_token으로 access_token 갱신
                credentials = Credentials(
                    token=None,  # 만료됨
                    refresh_token=refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=self.SCOPES,
                )
                
                # 토큰 갱신
                from google.auth.transport.requests import Request
                try:
                    credentials.refresh(Request())
                except Exception as e:
                    print(f"❌ 토큰 갱신 실패 (employee_id={employee_id}): {e}")
                    # 토큰 갱신 실패 시 재인증 필요
                    raise GoogleAuthRequired(user_id, self._build_auth_url(user_id))
                
                # 4️⃣ 갱신된 access_token을 Redis에 캐시 (1시간)
                if session_manager and credentials.token:
                    await session_manager.save_google_tokens(
                        user_id,
                        {
                            "access_token": credentials.token,
                            "refresh_token": refresh_token,
                            "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
                        },
                        ttl=3600,  # 1시간
                    )
                
                # 5️⃣ DB의 last_used_at 업데이트
                await token_service.update_last_used(employee_id)
                
                return credentials
        
        # 토큰이 없으면 OAuth 플로우 시작
        raise GoogleAuthRequired(user_id, self._build_auth_url(user_id))
    
    async def _get_service(self, user_id: Optional[str] = None):
        """Google Calendar API 서비스 객체 반환.
        
        Args:
            user_id: 사용자 ID
            
        Returns:
            Google Calendar API 서비스
        """
        credentials = await self._get_credentials(user_id)
        return build('calendar', 'v3', credentials=credentials)

    def _build_auth_url(self, user_id: str) -> str:
        base_url = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
        return f"{base_url}/auth/google?user_id={user_id}"
    
    async def get_events(
        self,
        user_id: str,
        time_min: str,
        time_max: str,
        calendar_id: str = 'primary',
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """일정 목록 조회.
        
        Args:
            user_id: 사용자 ID
            time_min: 시작 시간 (ISO 8601 형식, 예: "2024-12-01T00:00:00Z")
            time_max: 종료 시간 (ISO 8601 형식)
            calendar_id: 캘린더 ID (기본값: 'primary')
            max_results: 최대 결과 수
            
        Returns:
            일정 목록
        """
        service = await self._get_service(user_id)
        try:
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            return events_result.get('items', [])
        except HttpError as error:
            print(f"Google Calendar API 오류: {error}")
            return []
    
    async def create_event(
        self,
        user_id: str,
        event_data: Dict[str, Any],
        calendar_id: str = 'primary',
        send_notifications: bool = True
    ) -> Optional[Dict[str, Any]]:
        """일정 생성.
        
        Args:
            user_id: 사용자 ID
            event_data: 일정 데이터
                {
                    'title': '일정 제목',
                    'description': '설명',
                    'start_time': '2024-12-10T10:00:00',
                    'end_time': '2024-12-10T11:00:00',
                    'attendees': ['user1@example.com', 'user2@example.com'],
                    'location': '회의실 A'
                }
            calendar_id: 캘린더 ID
            send_notifications: 참석자에게 알림 전송 여부
            
        Returns:
            생성된 이벤트 정보
        """
        service = await self._get_service(user_id)
        try:
            event = {
                'summary': event_data.get('title', '제목 없음'),
                'description': event_data.get('description', ''),
                'start': {
                    'dateTime': event_data['start_time'],
                    'timeZone': 'Asia/Seoul',
                },
                'end': {
                    'dateTime': event_data['end_time'],
                    'timeZone': 'Asia/Seoul',
                },
            }
            if event_data.get('location'):
                event['location'] = event_data['location']
            if event_data.get('attendees'):
                event['attendees'] = [{'email': email} for email in event_data['attendees']]

            created_event = service.events().insert(
                calendarId=calendar_id,
                body=event,
                sendNotifications=send_notifications
            ).execute()
            print(f"✅ Google Calendar 이벤트 생성: {created_event.get('id')}")
            return created_event
        except HttpError as error:
            print(f"❌ Google Calendar 이벤트 생성 실패: {error}")
            return None
    
    async def update_event(
        self,
        user_id: str,
        event_id: str,
        event_data: Dict[str, Any],
        calendar_id: str = 'primary',
        send_notifications: bool = True
    ) -> Optional[Dict[str, Any]]:
        """일정 수정.
        
        Args:
            user_id: 사용자 ID
            event_id: Google Calendar 이벤트 ID
            event_data: 수정할 데이터
            calendar_id: 캘린더 ID
            send_notifications: 참석자에게 알림 전송 여부
            
        Returns:
            수정된 이벤트 정보
        """
        service = await self._get_service(user_id)
        try:
            event = service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            if 'title' in event_data:
                event['summary'] = event_data['title']
            if 'description' in event_data:
                event['description'] = event_data['description']
            if 'start_time' in event_data:
                event['start'] = {
                    'dateTime': event_data['start_time'],
                    'timeZone': 'Asia/Seoul',
                }
            if 'end_time' in event_data:
                event['end'] = {
                    'dateTime': event_data['end_time'],
                    'timeZone': 'Asia/Seoul',
                }
            if 'location' in event_data:
                event['location'] = event_data['location']
            if 'attendees' in event_data:
                event['attendees'] = [{'email': email} for email in event_data['attendees']]

            updated_event = service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event,
                sendNotifications=send_notifications
            ).execute()
            print(f"✅ Google Calendar 이벤트 수정: {event_id}")
            return updated_event
        except HttpError as error:
            print(f"❌ Google Calendar 이벤트 수정 실패: {error}")
            return None
    
    async def delete_event(
        self,
        user_id: str,
        event_id: str,
        calendar_id: str = 'primary',
        send_notifications: bool = True
    ) -> bool:
        """일정 삭제.
        
        Args:
            user_id: 사용자 ID
            event_id: Google Calendar 이벤트 ID
            calendar_id: 캘린더 ID
            send_notifications: 참석자에게 알림 전송 여부
            
        Returns:
            삭제 성공 여부
        """
        service = await self._get_service(user_id)
        try:
            service.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
                sendNotifications=send_notifications
            ).execute()
            print(f"✅ Google Calendar 이벤트 삭제: {event_id}")
            return True
        except HttpError as error:
            print(f"❌ Google Calendar 이벤트 삭제 실패: {error}")
            return False
    
    async def get_event(
        self,
        user_id: str,
        event_id: str,
        calendar_id: str = 'primary'
    ) -> Optional[Dict[str, Any]]:
        """단일 일정 조회.
        
        Args:
            user_id: 사용자 ID
            event_id: Google Calendar 이벤트 ID
            calendar_id: 캘린더 ID
            
        Returns:
            이벤트 정보
        """
        service = await self._get_service(user_id)
        try:
            event = service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            return event
        except HttpError as error:
            print(f"❌ Google Calendar 이벤트 조회 실패: {error}")
            return None