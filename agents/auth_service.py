"""사용자 인증 및 세션 관리 서비스."""

from __future__ import annotations

import secrets
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json

from agents.session_manager import get_session_manager
from agents.master_graphs.schedule.services.database_service import DatabaseService
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입.
    
    여러 경로에서 .env 파일을 찾습니다:
    1. 현재 작업 디렉토리
    2. 스크립트 파일이 있는 디렉토리
    3. 프로젝트 루트 디렉토리
    """
    import sys
    from pathlib import Path
    
    # 가능한 .env 파일 경로들
    possible_paths = [
        os.path.join(os.getcwd(), ".env"),  # 현재 작업 디렉토리
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),  # 프로젝트 루트
        os.path.join(os.path.dirname(__file__), ".env"),  # 현재 파일 디렉토리
    ]
    
    # 스크립트 파일이 있는 경우 그 디렉토리도 확인
    if hasattr(sys, 'argv') and sys.argv:
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        possible_paths.append(os.path.join(script_dir, ".env"))
        possible_paths.append(os.path.join(script_dir, "..", ".env"))
    
    env_path = None
    for path in possible_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            env_path = abs_path
            break
    
    if not env_path:
        logger.warning(".env 파일을 찾을 수 없습니다.")
        return
    
    try:
        loaded_vars = []
        with open(env_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                original_line = line
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                # 따옴표 제거 (단, 값이 비어있지 않은 경우만)
                if v:
                    v = v.strip("'").strip('"')
                
                # 기존 환경 변수가 있으면 덮어쓰지 않음 (환경 변수 우선)
                if k:
                    if k not in os.environ:
                        os.environ[k] = v
                        loaded_vars.append(k)
                    else:
                        logger.debug(f"환경 변수 {k}는 이미 설정되어 있어 덮어쓰지 않음")
        
        # 로드된 변수 확인
        db_password = os.getenv("DB_PASSWORD", "")
        db_username = os.getenv("DB_USERNAME", "")
        logger.info(f".env 파일 로드 완료: {env_path}")
        if loaded_vars:
            logger.debug(f"로드된 환경 변수: {', '.join(loaded_vars)}")
        logger.debug(f"DB_PASSWORD 존재 여부: {'있음' if db_password else '없음'}, 길이: {len(db_password)}")
        logger.debug(f"DB_USERNAME: {db_username}")
    except Exception as e:
        logger.warning(f".env 파일 로드 실패 ({env_path}): {e}")
        import traceback
        traceback.print_exc()


# 모듈 로드 시 .env 파일 로드
_load_env_file()


class AuthService:
    """사용자 인증 및 세션 관리.
    
    Features:
        - employee 테이블의 login_id와 password로 인증
        - Redis 기반 인증 세션 관리
        - employee_id 기반 세션 관리
    """
    
    def __init__(self, session_ttl: int = 3600 * 24):  # 24시간
        """초기화.
        
        Args:
            session_ttl: 인증 세션 TTL (초 단위)
        """
        # .env 파일이 로드되었는지 확인하고, 없으면 다시 로드 시도
        if not os.getenv("DB_PASSWORD") and not os.getenv("DB_USERNAME"):
            _load_env_file()
        
        self.session_ttl = session_ttl
        self.db = DatabaseService()
        
        # DB 연결 정보 디버깅 (비밀번호는 마스킹)
        db_password = os.getenv("DB_PASSWORD", "")
        db_username = os.getenv("DB_USERNAME", "root")
        db_host = os.getenv("DB_HOST") or os.getenv("HOST", "127.0.0.1")
        db_port = os.getenv("DB_PORT", "3306")
        db_name = os.getenv("DB_NAME", "ai_agent")
        
        password_display = "***" if db_password else "(없음)"
        logger.info(f"AuthService DB 연결 정보: host={db_host}, port={db_port}, db={db_name}, user={db_username}, password={password_display}")
    
    async def verify_password(self, password: str, stored_password: str) -> bool:
        """비밀번호 검증.
        
        Args:
            password: 입력된 평문 비밀번호
            stored_password: DB에 저장된 비밀번호 (해시 또는 평문)
            
        Returns:
            검증 성공 여부
            
        Note:
            DB의 password가 해시인지 평문인지에 따라 검증 방식이 달라질 수 있습니다.
            필요시 bcrypt 등으로 해시 검증을 구현하세요.
        """
        # DB에 해시로 저장되어 있다면 bcrypt 등으로 검증
        # 평문으로 저장되어 있다면 직접 비교
        # 여기서는 기본적으로 직접 비교 (실제 환경에 맞게 수정 필요)
        return password == stored_password
    
    async def authenticate_employee(
        self,
        login_id: str,
        password: str
    ) -> Optional[Dict[str, Any]]:
        """employee 테이블에서 login_id와 password로 인증.
        
        Args:
            login_id: 로그인 ID
            password: 비밀번호
            
        Returns:
            인증 성공 시 employee 정보 (employee_id 포함) 또는 None
        """
        # 먼저 login_id와 password만으로 조회 (상태 조건 없이)
        sql = """
        SELECT 
            employee_id,
            login_id,
            name,
            dept,
            position,
            email,
            employment_status
        FROM employee
        WHERE login_id = :login_id
          AND password = :password
        LIMIT 1;
        """
        
        try:
            logger.debug(f"로그인 시도: login_id={login_id}, password_length={len(password)}")
            
            async with self.db._session() as session:
                result = await session.execute(
                    text(sql),
                    {
                        "login_id": login_id,
                        "password": password  # 실제로는 해시 검증 필요
                    }
                )
                row = result.mappings().first()
            
            if not row:
                logger.debug(f"로그인 실패: login_id={login_id}에 해당하는 사용자를 찾을 수 없거나 비밀번호가 일치하지 않습니다.")
                # 디버깅: login_id만으로 조회해서 사용자가 존재하는지 확인
                try:
                    check_sql = """
                    SELECT employee_id, login_id, name, employment_status, password
                    FROM employee
                    WHERE login_id = :login_id
                    LIMIT 1;
                    """
                    async with self.db._session() as session:
                        check_result = await session.execute(
                            text(check_sql),
                            {"login_id": login_id}
                        )
                        check_row = check_result.mappings().first()
                        if check_row:
                            check_data = dict(check_row)
                            stored_password = check_data.get('password', '')
                            password_match = (password == stored_password)
                            logger.debug(f"사용자는 존재합니다: employee_id={check_data.get('employee_id')}, name={check_data.get('name')}")
                            logger.debug(f"employment_status={check_data.get('employment_status')}")
                            logger.debug(f"비밀번호 일치 여부: {password_match}")
                            logger.debug(f"입력 비밀번호 길이: {len(password)}, 저장 비밀번호 길이: {len(stored_password)}")
                            
                            # 비밀번호가 일치하는데도 실패했다면 상태 조건 문제
                            if password_match:
                                logger.warning(f"비밀번호는 일치하지만 상태 조건으로 인해 로그인 실패할 수 있습니다.")
                                logger.warning(f"employment_status 값: {repr(check_data.get('employment_status'))}")
                        else:
                            logger.debug(f"login_id={login_id}에 해당하는 사용자가 존재하지 않습니다.")
                except Exception as check_e:
                    logger.debug(f"사용자 존재 여부 확인 중 오류: {check_e}")
                    import traceback
                    traceback.print_exc()
                
                return None
            
            employee_data = dict(row)
            logger.info(f"로그인 성공: employee_id={employee_data.get('employee_id')}, name={employee_data.get('name')}")
            
            # 마지막 로그인 시간 업데이트 (선택사항)
            await self.update_last_login(employee_data["employee_id"])
            
            return employee_data
            
        except Exception as e:
            logger.error(f"사용자 인증 실패 (login_id={login_id}): {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def update_last_login(self, employee_id: int) -> bool:
        """마지막 로그인 시간 업데이트 (선택사항).
        
        Args:
            employee_id: 사원 번호
            
        Returns:
            업데이트 성공 여부
        """
        # employee 테이블에 last_login_at 컬럼이 있다면 업데이트
        # 없으면 이 메서드는 무시
        try:
            sql = """
            UPDATE employee
            SET last_login_at = NOW()
            WHERE employee_id = :employee_id;
            """
            async with self.db._session(transactional=True) as session:
                await session.execute(text(sql), {"employee_id": employee_id})
            return True
        except Exception as e:
            # 컬럼이 없어도 오류로 처리하지 않음
            logger.debug(f"마지막 로그인 시간 업데이트 건너뜀: {e}")
            return False
    
    async def create_session_token(self) -> str:
        """세션 토큰 생성 (암호학적으로 안전한 랜덤 문자열).
        
        Returns:
            세션 토큰
        """
        return secrets.token_urlsafe(32)
    
    async def create_auth_session(
        self,
        employee_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Dict[str, Any]:
        """인증 세션 생성 및 Redis에 저장.
        
        Args:
            employee_id: 사원 번호
            ip_address: 클라이언트 IP 주소
            user_agent: 사용자 에이전트
            
        Returns:
            세션 정보 (session_token 포함)
        """
        session_manager = await get_session_manager()
        if not session_manager:
            raise RuntimeError("세션 관리자를 사용할 수 없습니다.")
        
        # 세션 토큰 생성
        session_token = await self.create_session_token()
        
        # 세션 데이터 구성
        session_data = {
            "employee_id": employee_id,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(seconds=self.session_ttl)).isoformat()
        }
        
        # Redis에 저장: auth:session:{session_token}
        auth_key = f"auth:session:{session_token}"
        await session_manager.redis.setex(
            auth_key,
            self.session_ttl,
            json.dumps(session_data, ensure_ascii=False)
        )
        
        # 사용자별 세션 목록에 추가: auth:user:{employee_id}
        user_sessions_key = f"auth:user:{employee_id}"
        await session_manager.redis.sadd(user_sessions_key, session_token)
        await session_manager.redis.expire(user_sessions_key, self.session_ttl)
        
        return {
            "session_token": session_token,
            "employee_id": employee_id,
            "expires_at": session_data["expires_at"]
        }
    
    async def get_auth_session(self, session_token: str) -> Optional[Dict[str, Any]]:
        """인증 세션 조회.
        
        Args:
            session_token: 세션 토큰
            
        Returns:
            세션 정보 (employee_id 포함) 또는 None
        """
        session_manager = await get_session_manager()
        if not session_manager:
            return None
        
        auth_key = f"auth:session:{session_token}"
        value = await session_manager.redis.get(auth_key)
        
        if not value:
            return None
        
        return json.loads(value)
    
    async def revoke_session(self, session_token: str) -> bool:
        """세션 무효화 (로그아웃).
        
        Args:
            session_token: 세션 토큰
            
        Returns:
            성공 여부
        """
        session_manager = await get_session_manager()
        if not session_manager:
            return False
        
        # 세션 데이터 조회
        session_data = await self.get_auth_session(session_token)
        if not session_data:
            return False
        
        employee_id = session_data.get("employee_id")
        
        # 세션 삭제
        auth_key = f"auth:session:{session_token}"
        await session_manager.redis.delete(auth_key)
        
        # 사용자별 세션 목록에서 제거
        if employee_id:
            user_sessions_key = f"auth:user:{employee_id}"
            await session_manager.redis.srem(user_sessions_key, session_token)
        
        return True
    
    async def revoke_all_user_sessions(self, employee_id: int) -> int:
        """사용자의 모든 세션 무효화.
        
        Args:
            employee_id: 사원 번호
            
        Returns:
            무효화된 세션 개수
        """
        session_manager = await get_session_manager()
        if not session_manager:
            return 0
        
        user_sessions_key = f"auth:user:{employee_id}"
        session_tokens = await session_manager.redis.smembers(user_sessions_key)
        
        count = 0
        for token in session_tokens:
            auth_key = f"auth:session:{token}"
            await session_manager.redis.delete(auth_key)
            count += 1
        
        # 사용자 세션 목록 삭제
        await session_manager.redis.delete(user_sessions_key)
        
        return count


# 싱글톤 인스턴스
_auth_service: Optional[AuthService] = None


async def get_auth_service() -> AuthService:
    """인증 서비스 싱글톤 인스턴스 반환.
    
    Returns:
        AuthService 인스턴스
    """
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service

