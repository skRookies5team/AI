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
        ...     employee_id=4003,
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
            
        Raises:
            ValueError: employee.email과 google_email이 일치하지 않거나, 
                       같은 google_email이 다른 employee_id에 이미 연결되어 있는 경우
        
        Note:
            - refresh_token은 자동으로 암호화되어 저장됩니다.
            - 이미 존재하는 employee_id면 업데이트(UPSERT)됩니다.
            - employee.email과 google_email이 일치해야 합니다.
            - client_id/client_secret도 함께 저장됩니다.
        """
        if not refresh_token:
            print(f"⚠️ refresh_token이 비어있습니다 (employee_id={employee_id})")
            return False
        
        # ✅ 현재 사용 중인 client_id/client_secret 가져오기 (해당 employee_id의 설정 우선 조회)
        client_config = await self.get_oauth_client_config(employee_id=employee_id)
        if not client_config:
            print(f"⚠️ OAuth Client 설정을 찾을 수 없습니다. 환경 변수를 확인하세요.")
            # client_id/client_secret이 없어도 계속 진행 (기존 동작 유지)
            client_id = None
            client_secret = None
        else:
            client_id = client_config.get("client_id")
            client_secret = client_config.get("client_secret")
        
        # ✅ 1. employee 테이블에서 employee_id의 email 조회
        employee_sql = """
        SELECT email
        FROM employee
        WHERE employee_id = :employee_id
        LIMIT 1;
        """
        
        employee_email = None
        try:
            async with self.db._session() as session:
                result = await session.execute(
                    text(employee_sql),
                    {"employee_id": employee_id}
                )
                row = result.mappings().first()
                if row:
                    employee_email = row.get("email")
        except Exception as e:
            print(f"⚠️ employee 테이블 조회 실패 (employee_id={employee_id}): {e}")
            # employee 테이블 조회 실패해도 계속 진행 (옵션)
        
        # ✅ 2. employee.email과 google_email 일치 여부 확인
        if employee_email:
            # 대소문자 무시하고 비교
            if employee_email.lower().strip() != google_email.lower().strip():
                error_msg = (
                    f"❌ Google 계정 이메일({google_email})이 사원 이메일({employee_email})과 일치하지 않습니다. "
                    f"employee_id={employee_id}의 이메일({employee_email})로 Google OAuth 인증을 해주세요."
                )
                print(error_msg)
                raise ValueError(error_msg)
        
        # ✅ 3. 같은 google_email이 다른 employee_id에 연결되어 있는지 확인
        check_sql = """
        SELECT employee_id, google_email
        FROM google_credentials
        WHERE google_email = :google_email
          AND employee_id != :employee_id
        LIMIT 1;
        """
        
        try:
            async with self.db._session(transactional=True) as session:
                # 기존 연결 확인
                result = await session.execute(
                    text(check_sql),
                    {
                        "google_email": google_email,
                        "employee_id": employee_id,
                    }
                )
                existing_row = result.mappings().first()
                
                if existing_row:
                    existing_employee_id = existing_row["employee_id"]
                    
                    # ✅ 4. 기존 연결된 employee_id의 employee.email 확인
                    existing_employee_sql = """
                    SELECT email
                    FROM employee
                    WHERE employee_id = :existing_employee_id
                    LIMIT 1;
                    """
                    
                    existing_result = await session.execute(
                        text(existing_employee_sql),
                        {"existing_employee_id": existing_employee_id}
                    )
                    existing_employee_row = existing_result.mappings().first()
                    existing_employee_email = existing_employee_row.get("email") if existing_employee_row else None
                    
                    if existing_employee_email:
                        # 기존 연결된 employee.email과 google_email이 일치하는지 확인
                        if existing_employee_email.lower().strip() == google_email.lower().strip():
                            error_msg = (
                                f"❌ {google_email}은(는) 이미 employee_id={existing_employee_id} "
                                f"(이메일: {existing_employee_email})에 연결되어 있습니다. "
                                f"다른 사원의 Google 계정으로는 연동할 수 없습니다. "
                                f"올바른 Google 계정({employee_email or '사원 이메일'})으로 연동해주세요."
                            )
                            print(error_msg)
                            raise ValueError(error_msg)
                        else:
                            # 기존 연결의 employee.email과 google_email이 일치하지 않으면 기존 연결 삭제
                            print(f"⚠️ {google_email}은(는) employee_id={existing_employee_id}에 연결되어 있지만, "
                                  f"해당 사원의 이메일({existing_employee_email})과 일치하지 않습니다.")
                            print(f"⚠️ 기존 연결을 해제하고 employee_id={employee_id}로 재연결합니다.")
                            
                            delete_sql = """
                            DELETE FROM google_credentials
                            WHERE employee_id = :existing_employee_id;
                            """
                            await session.execute(
                                text(delete_sql),
                                {"existing_employee_id": existing_employee_id}
                            )
                            print(f"✅ 기존 연결 삭제 완료 (employee_id={existing_employee_id})")
                    else:
                        # 기존 연결된 employee_id의 email을 찾을 수 없으면 기존 연결 삭제
                        print(f"⚠️ employee_id={existing_employee_id}의 이메일을 찾을 수 없습니다. "
                              f"기존 연결을 해제하고 employee_id={employee_id}로 재연결합니다.")
                        
                        delete_sql = """
                        DELETE FROM google_credentials
                        WHERE employee_id = :existing_employee_id;
                        """
                        await session.execute(
                            text(delete_sql),
                            {"existing_employee_id": existing_employee_id}
                        )
                        print(f"✅ 기존 연결 삭제 완료 (employee_id={existing_employee_id})")
                
                # refresh_token 암호화
                try:
                    encrypted_token = self.encryption.encrypt(refresh_token)
                except Exception as e:
                    print(f"❌ refresh_token 암호화 실패 (employee_id={employee_id}): {e}")
                    return False
                
                # 새 연결 저장 (client_id/client_secret 포함)
                sql = """
                INSERT INTO google_credentials (
                    employee_id,
                    google_email,
                    google_sub,
                    refresh_token,
                    scope,
                    client_id,
                    client_secret,
                    created_at,
                    updated_at,
                    last_used_at
                ) VALUES (
                    :employee_id,
                    :google_email,
                    :google_sub,
                    :refresh_token,
                    :scope,
                    :client_id,
                    :client_secret,
                    NOW(),
                    NOW(),
                    NOW()
                )
                ON DUPLICATE KEY UPDATE
                    google_email = VALUES(google_email),
                    google_sub = VALUES(google_sub),
                    refresh_token = VALUES(refresh_token),
                    scope = VALUES(scope),
                    client_id = VALUES(client_id),
                    client_secret = VALUES(client_secret),
                    updated_at = NOW(),
                    last_used_at = NOW();
                """
                
                await session.execute(
                    text(sql),
                    {
                        "employee_id": employee_id,
                        "google_email": google_email,
                        "google_sub": google_sub,
                        "refresh_token": encrypted_token,
                        "scope": scope,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
                
            print(f"✅ Google 토큰 DB 저장 완료 (employee_id={employee_id}, email={google_email}, client_id={client_id})")
            return True
        except ValueError:
            raise  # ValueError는 그대로 전파
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
    
    async def get_oauth_client_config(self, employee_id: Optional[int] = None) -> Optional[Dict[str, str]]:
        """DB에서 OAuth Client 설정 조회 (client_id, client_secret).
        
        Args:
            employee_id: 사원 ID (선택). 지정하면 해당 사원의 client_id/client_secret을 우선 조회.
        
        Returns:
            {"client_id": str, "client_secret": str} 또는 None
            
        Note:
            - employee_id가 지정되면 해당 사원의 client_id/client_secret을 우선 조회합니다.
            - 해당 사원의 설정이 없으면 환경 변수의 공통 설정을 사용합니다.
            - 다른 사원의 client_id/client_secret은 사용하지 않습니다 (보안상 위험).
            - employee_id가 없으면 환경 변수에서만 조회합니다.
        """
        # ✅ 1. employee_id가 지정되면 해당 사원의 client_id/client_secret 우선 조회
        if employee_id is not None:
            sql = """
            SELECT client_id, client_secret
            FROM google_credentials
            WHERE employee_id = :employee_id
              AND client_id IS NOT NULL 
              AND client_secret IS NOT NULL
              AND client_id != ''
              AND client_secret != ''
            LIMIT 1;
            """
            
            try:
                async with self.db._session() as session:
                    result = await session.execute(text(sql), {"employee_id": employee_id})
                    row = result.mappings().first()
                
                if row and row.get("client_id") and row.get("client_secret"):
                    print(f"✅ employee_id={employee_id}의 OAuth Client 설정을 DB에서 조회했습니다.")
                    return {
                        "client_id": row["client_id"],
                        "client_secret": row["client_secret"],
                    }
                else:
                    print(f"⚠️ employee_id={employee_id}의 OAuth Client 설정이 DB에 없습니다. 환경 변수를 사용합니다.")
            except Exception as e:
                print(f"⚠️ employee_id={employee_id}의 OAuth Client 설정 조회 실패: {e}")
        
        # ✅ 2. employee_id가 없거나 해당 사원의 설정이 없으면 환경 변수에서만 조회
        # 다른 사원의 client_id/client_secret을 사용하지 않음 (보안상 위험)
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        
        if client_id and client_secret:
            if employee_id is not None:
                print(f"⚠️ employee_id={employee_id}의 OAuth Client 설정이 없어 환경 변수를 사용합니다.")
            else:
                print("ℹ️ 환경 변수에서 OAuth Client 설정을 사용합니다.")
            return {
                "client_id": client_id,
                "client_secret": client_secret,
            }
        
        # ✅ 3. 환경 변수도 없으면 None 반환
        if employee_id is not None:
            print(f"❌ employee_id={employee_id}의 OAuth Client 설정을 찾을 수 없습니다. DB 또는 환경 변수를 확인하세요.")
        else:
            print("❌ OAuth Client 설정을 찾을 수 없습니다. 환경 변수를 확인하세요.")
        
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
