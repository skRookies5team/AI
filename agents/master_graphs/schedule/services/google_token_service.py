"""Google OAuth 토큰 DB 관리 서비스."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, Any, Optional, List

from sqlalchemy import text

from .token_encryption_service import get_encryption_service
from .database_service import DatabaseService


class GoogleTokenService:
    """Google OAuth 토큰 DB 관리.
    
    사원별 Google OAuth refresh_token을 DB에 안전하게 저장하고 관리합니다.
    refresh_token은 암호화하여 저장하고, access_token은 Redis에 캐시합니다.
    
    Features:
        - refresh_token 암호화 저장 (DB)
        - access_token 캐시 (Redis, 1시간)
        - 토큰 사용 이력 추적
        - 연결 해제 (revoke)
    
    Examples:
        >>> service = GoogleTokenService()
        >>> await service.save_google_credentials(
        ...     employee_id=4001,
        ...     google_email="user@example.com",
        ...     refresh_token="1//abc...",
        ...     scope="https://www.googleapis.com/auth/calendar",
        ... )
    """
    
    def __init__(self) -> None:
        """초기화."""
        self.db = DatabaseService()
        self.encryption = get_encryption_service()
    
    async def save_google_credentials(
        self,
        employee_id: int,
        google_email: str,
        refresh_token: str,
        scope: str,
        google_sub: Optional[str] = None,
    ) -> bool:
        """Google OAuth 토큰을 DB에 저장 (refresh_token 암호화).
        
        Args:
            employee_id: 사원 ID
            google_email: Google 계정 이메일
            refresh_token: Google refresh token (평문)
            scope: OAuth scope (예: "https://www.googleapis.com/auth/calendar")
            google_sub: Google 고유 ID (선택)
            
        Returns:
            저장 성공 여부
            
        Note:
            - refresh_token은 자동으로 암호화되어 저장됩니다.
            - 이미 존재하는 employee_id면 업데이트(UPSERT)됩니다.
        """
        if not refresh_token:
            print(f"⚠️ refresh_token이 비어있습니다 (employee_id={employee_id})")
            return False
        
        # refresh_token 암호화
        try:
            encrypted_token = self.encryption.encrypt(refresh_token)
        except Exception as e:
            print(f"❌ refresh_token 암호화 실패 (employee_id={employee_id}): {e}")
            return False
        
        sql = """
        INSERT INTO google_credentials (
            employee_id,
            google_email,
            google_sub,
            refresh_token,
            scope,
            created_at,
            updated_at,
            last_used_at
        ) VALUES (
            :employee_id,
            :google_email,
            :google_sub,
            :refresh_token,
            :scope,
            NOW(),
            NOW(),
            NOW()
        )
        ON DUPLICATE KEY UPDATE
            google_email = VALUES(google_email),
            google_sub = VALUES(google_sub),
            refresh_token = VALUES(refresh_token),
            scope = VALUES(scope),
            updated_at = NOW(),
            last_used_at = NOW();
        """
        
        try:
            async with self.db._session(transactional=True) as session:
                await session.execute(
                    text(sql),
                    {
                        "employee_id": employee_id,
                        "google_email": google_email,
                        "google_sub": google_sub,
                        "refresh_token": encrypted_token,
                        "scope": scope,
                    },
                )
            print(f"✅ Google 토큰 DB 저장 완료 (employee_id={employee_id}, email={google_email})")
            return True
        except Exception as e:
            print(f"❌ Google 토큰 저장 실패 (employee_id={employee_id}): {e}")
            return False
    
    async def get_google_credentials(self, employee_id: int) -> Optional[Dict[str, Any]]:
        """DB에서 Google OAuth 토큰 조회 (refresh_token 복호화).
        
        Args:
            employee_id: 사원 ID
            
        Returns:
            토큰 정보 딕셔너리 또는 None
            {
                "employee_id": int,
                "google_email": str,
                "google_sub": str,
                "client_id": str,  # OAuth Client ID
                "client_secret": str,  # OAuth Client Secret
                "refresh_token": str,  # 복호화된 평문
                "scope": str,
                "token_version": int,
                "created_at": datetime,
                "updated_at": datetime,
                "last_used_at": datetime,
            }
        """
        sql = """
        SELECT
            employee_id,
            google_email,
            google_sub,
            client_id,
            client_secret,
            refresh_token,
            scope,
            token_version,
            created_at,
            updated_at,
            last_used_at
        FROM google_credentials
        WHERE employee_id = :employee_id
        LIMIT 1;
        """
        
        try:
            async with self.db._session() as session:
                result = await session.execute(
                    text(sql),
                    {"employee_id": employee_id},
                )
                row = result.mappings().first()
            
            if not row:
                return None
            
            data = dict(row)
            
            # refresh_token 복호화
            encrypted_token = data.get("refresh_token")
            if encrypted_token:
                decrypted_token = self.encryption.decrypt(encrypted_token)
                if not decrypted_token:
                    print(f"❌ refresh_token 복호화 실패 (employee_id={employee_id})")
                    return None
                data["refresh_token"] = decrypted_token
            else:
                print(f"⚠️ refresh_token이 없습니다 (employee_id={employee_id})")
                return None
            
            return data
            
        except Exception as e:
            print(f"❌ Google 토큰 조회 실패 (employee_id={employee_id}): {e}")
            return None
    
    async def update_last_used(self, employee_id: int) -> bool:
        """토큰 마지막 사용 시각 업데이트.
        
        Args:
            employee_id: 사원 ID
            
        Returns:
            업데이트 성공 여부
        """
        sql = """
        UPDATE google_credentials
        SET last_used_at = NOW()
        WHERE employee_id = :employee_id;
        """
        
        try:
            async with self.db._session(transactional=True) as session:
                result = await session.execute(text(sql), {"employee_id": employee_id})
            return result.rowcount > 0
        except Exception as e:
            print(f"❌ 토큰 사용 시각 업데이트 실패 (employee_id={employee_id}): {e}")
            return False
    
    async def revoke_google_credentials(self, employee_id: int) -> bool:
        """Google OAuth 토큰 삭제 (연결 해제).
        
        Args:
            employee_id: 사원 ID
            
        Returns:
            삭제 성공 여부
            
        Note:
            DB에서만 삭제합니다. Google에 revoke 요청은 별도로 수행해야 합니다.
        """
        sql = """
        DELETE FROM google_credentials
        WHERE employee_id = :employee_id;
        """
        
        try:
            async with self.db._session(transactional=True) as session:
                result = await session.execute(text(sql), {"employee_id": employee_id})
            
            if result.rowcount > 0:
                print(f"✅ Google 토큰 삭제 완료 (employee_id={employee_id})")
                return True
            else:
                print(f"⚠️ 삭제할 토큰이 없습니다 (employee_id={employee_id})")
                return False
        except Exception as e:
            print(f"❌ Google 토큰 삭제 실패 (employee_id={employee_id}): {e}")
            return False
    
    async def get_all_connected_employees(self) -> List[int]:
        """Google Calendar 연동된 모든 사원 ID 조회.
        
        Returns:
            사원 ID 리스트 (최근 사용 순서)
        """
        sql = """
        SELECT employee_id
        FROM google_credentials
        ORDER BY last_used_at DESC;
        """
        
        try:
            async with self.db._session() as session:
                result = await session.execute(text(sql))
                return [row[0] for row in result.fetchall()]
        except Exception as e:
            print(f"❌ 연동된 사원 목록 조회 실패: {e}")
            return []
    
    async def is_connected(self, employee_id: int) -> bool:
        """사원이 Google Calendar에 연동되어 있는지 확인.
        
        Args:
            employee_id: 사원 ID
            
        Returns:
            연동 여부 (True: 연동됨, False: 연동 안 됨)
        """
        credentials = await self.get_google_credentials(employee_id)
        return credentials is not None and credentials.get("refresh_token") is not None
    
    async def get_oauth_client_config(self) -> Optional[Dict[str, str]]:
        """DB에서 OAuth Client 설정 조회 (client_id, client_secret).
        
        Returns:
            {"client_id": str, "client_secret": str} 또는 None
            
        Note:
            - DB에서 첫 번째 레코드의 client_id/client_secret을 가져옵니다.
            - 없으면 환경 변수에서 fallback합니다.
        """
        sql = """
        SELECT client_id, client_secret
        FROM google_credentials
        WHERE client_id IS NOT NULL 
          AND client_secret IS NOT NULL
          AND client_id != ''
          AND client_secret != ''
        LIMIT 1;
        """
        
        try:
            async with self.db._session() as session:
                result = await session.execute(text(sql))
                row = result.mappings().first()
            
            if row and row.get("client_id") and row.get("client_secret"):
                return {
                    "client_id": row["client_id"],
                    "client_secret": row["client_secret"],
                }
        except Exception as e:
            print(f"❌ OAuth Client 설정 조회 실패: {e}")
        
        # DB에서 가져올 수 없으면 환경 변수에서 fallback
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        if client_id and client_secret:
            print("⚠️ DB에서 OAuth Client 설정을 찾지 못해 환경 변수를 사용합니다.")
            return {
                "client_id": client_id,
                "client_secret": client_secret,
            }
        
        return None


# 싱글톤 인스턴스
_token_service: Optional[GoogleTokenService] = None


def get_token_service() -> GoogleTokenService:
    """토큰 서비스 싱글톤 인스턴스 반환.
    
    Returns:
        GoogleTokenService 인스턴스
    """
    global _token_service
    if _token_service is None:
        _token_service = GoogleTokenService()
    return _token_service
