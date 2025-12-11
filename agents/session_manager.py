"""세션별 대화 히스토리 관리 - Redis 단일 키 방식.

요청 방식:
- 세션당 하나의 Redis 키: session:{session_id}
- 노드 실행 후 사용자 질문 + 답변을 Redis에 저장
- 다음 질문 시 이전 대화 히스토리를 로드하여 함께 전달
"""

from __future__ import annotations

import json
import asyncio
import os
from typing import List, Optional, Dict, Any
from datetime import datetime
from pathlib import Path

try:
    from redis.asyncio import Redis as AsyncRedis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    AsyncRedis = None

from agents.state import Message


def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    try:
        env_path = os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception as e:
        print(f"[WARNING] .env 파일 로드 실패: {e}")


_load_env_file()


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 안전 조회."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    if not value:
        return default
    return value


class SessionManager:
    """세션별 대화 히스토리 관리자 - Redis 단일 키 방식.
    
    특징:
    - 세션당 하나의 Redis 키: session:{session_id}
    - 대화 히스토리만 저장 (conversation_history)
    - TTL로 자동 만료
    - 노드 실행 후 사용자 질문 + 답변 저장
    """
    
    def __init__(self, redis_client: AsyncRedis, ttl: int = 1800, key_prefix: str = "session"):
        """세션 관리자 초기화.
        
        Args:
            redis_client: Redis 비동기 클라이언트
            ttl: 세션 TTL (초 단위, 기본값: 1800초 = 30분)
            key_prefix: Redis 키 접두사
        """
        if not REDIS_AVAILABLE:
            raise ImportError("redis.asyncio를 import할 수 없습니다.")
        
        self.redis = redis_client
        self.ttl = ttl
        self.key_prefix = key_prefix
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock_lock = asyncio.Lock()
    
    async def _get_lock(self, session_id: str) -> asyncio.Lock:
        """세션별 락 가져오기."""
        async with self._lock_lock:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]
    
    def _get_key(self, session_id: str) -> str:
        """세션별 Redis 키."""
        return f"{self.key_prefix}:{session_id}"
    
    async def load_history(self, session_id: str) -> List[Message]:
        """세션의 대화 히스토리 로드.
        
        Args:
            session_id: 세션 ID
            
        Returns:
            대화 히스토리 리스트
        """
        if not session_id:
            return []
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id)
            try:
                value = await self.redis.get(key)
                
                if value is None:
                    return []
                
                data = json.loads(value)
                return data.get("conversation_history", [])
            except Exception as e:
                print(f"[WARNING] 세션 히스토리 로드 실패 (session_id={session_id}): {e}")
                return []
    
    async def load_session_data(self, session_id: str) -> Dict[str, Any]:
        """세션 데이터 전체 로드 (conversation_history + 컨텍스트들).
        
        Args:
            session_id: 세션 ID
            
        Returns:
            세션 데이터 딕셔너리 (conversation_history, email_search_context 등)
        """
        if not session_id:
            return {
                "conversation_history": [],
                "email_search_context": {},
                "email_compose_context": {},
                "qa_search_context": {},
                "tech_search_context": {},
                "schedule_context": {},
                "document_context": {}
            }
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id)
            try:
                # 타임아웃 설정으로 Redis 조회 (5초)
                value = await asyncio.wait_for(self.redis.get(key), timeout=5.0)
                
                if value is None:
                    return {
                        "conversation_history": [],
                        "email_search_context": {},
                        "email_compose_context": {},
                        "qa_search_context": {},
                        "tech_search_context": {},
                        "schedule_context": {},
                        "document_context": {},
                        "recruitment_context": {}
                    }
                
                data = json.loads(value)
                return {
                    "conversation_history": data.get("conversation_history", []),
                    "email_search_context": data.get("email_search_context") or {},
                    "email_compose_context": data.get("email_compose_context", {}),
                    "qa_search_context": data.get("qa_search_context") or {},
                    "tech_search_context": data.get("tech_search_context") or {},
                    "schedule_context": data.get("schedule_context") or {},
                    "document_context": data.get("document_context") or {},
                    "recruitment_context": data.get("recruitment_context") or {}
                }
            except asyncio.TimeoutError:
                print(f"[WARNING] 세션 데이터 로드 타임아웃 (session_id={session_id}): Redis 서버 응답 없음 (5초 초과)")
                return {
                    "conversation_history": [],
                    "email_search_context": {},
                    "email_compose_context": {},
                    "qa_search_context": {},
                    "tech_search_context": {},
                    "schedule_context": {},
                    "document_context": {},
                    "recruitment_context": {}
                }
            except Exception as e:
                print(f"[WARNING] 세션 데이터 로드 실패 (session_id={session_id}): {e}")
                return {
                    "conversation_history": [],
                    "email_search_context": {},
                    "email_compose_context": {},
                    "qa_search_context": {},
                    "tech_search_context": {},
                    "schedule_context": {},
                    "document_context": {}
                }
    
    async def save_message(self, session_id: str, message: Message) -> None:
        """대화 메시지를 세션에 추가 저장.
        
        Args:
            session_id: 세션 ID
            message: 대화 메시지 (user 또는 assistant)
        """
        if not session_id:
            return
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id)
            
            try:
                # 기존 히스토리 로드
                value = await self.redis.get(key)
                if value:
                    data = json.loads(value)
                    history = data.get("conversation_history", [])
                else:
                    history = []
                
                # 새 메시지 추가
                history.append(message)
                
                # 최대 개수 제한 (최근 N개만 유지)
                MAX_HISTORY = 20
                if len(history) > MAX_HISTORY:
                    history = history[-MAX_HISTORY:]
                
                # 기존 컨텍스트 유지
                existing_contexts = {}
                if value:
                    existing_data = json.loads(value)
                    existing_contexts = {
                        "email_search_context": existing_data.get("email_search_context", {}),
                        "qa_search_context": existing_data.get("qa_search_context", {}),
                        "tech_search_context": existing_data.get("tech_search_context", {}),
                        "schedule_context": existing_data.get("schedule_context", {}),
                        "document_context": existing_data.get("document_context", {})
                    }
                
                # Redis에 저장 (TTL 설정, 컨텍스트 유지)
                data = {
                    "conversation_history": history,
                    **existing_contexts,
                    "last_updated": datetime.now().isoformat()
                }
                await self.redis.setex(key, self.ttl, json.dumps(data, ensure_ascii=False, default=str))
            except Exception as e:
                print(f"[WARNING] 세션 메시지 저장 실패 (session_id={session_id}): {e}")
    
    async def save_history(self, session_id: str, history: List[Message]) -> None:
        """전체 대화 히스토리를 세션에 저장.
        
        Args:
            session_id: 세션 ID
            history: 대화 히스토리 리스트
        """
        if not session_id:
            return
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id)
            
            try:
                # 기존 데이터 로드 (컨텍스트 유지)
                value = await self.redis.get(key)
                if value:
                    existing_data = json.loads(value)
                else:
                    existing_data = {}
                
                # 최대 개수 제한
                MAX_HISTORY = 20
                if len(history) > MAX_HISTORY:
                    history = history[-MAX_HISTORY:]
                
                data = {
                    "conversation_history": history,
                    "email_search_context": existing_data.get("email_search_context", {}),
                    "qa_search_context": existing_data.get("qa_search_context", {}),
                    "tech_search_context": existing_data.get("tech_search_context", {}),
                    "schedule_context": existing_data.get("schedule_context", {}),
                    "document_context": existing_data.get("document_context", {}),
                    "last_updated": datetime.now().isoformat()
                }
                await self.redis.setex(key, self.ttl, json.dumps(data, ensure_ascii=False, default=str))
            except Exception as e:
                print(f"[WARNING] 세션 히스토리 저장 실패 (session_id={session_id}): {e}")
    
    async def save_context(self, session_id: str, context_name: str, context_data: Dict[str, Any]) -> None:
        """특정 컨텍스트를 세션에 저장.
        
        Args:
            session_id: 세션 ID
            context_name: 컨텍스트 이름 (예: "email_search_context")
            context_data: 컨텍스트 데이터
        """
        if not session_id or not context_data:
            return
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id)
            
            try:
                # 기존 데이터 로드
                value = await self.redis.get(key)
                if value:
                    data = json.loads(value)
                else:
                    data = {
                        "conversation_history": [],
                        "email_search_context": {},
                        "qa_search_context": {},
                        "tech_search_context": {},
                        "schedule_context": {},
                        "document_context": {}
                    }
                
                # 컨텍스트 업데이트
                data[context_name] = context_data
                data["last_updated"] = datetime.now().isoformat()
                
                await self.redis.setex(key, self.ttl, json.dumps(data, ensure_ascii=False, default=str))
            except Exception as e:
                print(f"[WARNING] 세션 컨텍스트 저장 실패 (session_id={session_id}, context={context_name}): {e}")
    
    async def save_answer(self, session_id: str, user_query: str, answer: str) -> None:
        """사용자 질문과 답변을 세션에 저장.
        
        Args:
            session_id: 세션 ID
            user_query: 사용자 질문
            answer: AI 답변
        """
        if not session_id or not answer:
            return
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id)
            
            try:
                # 기존 데이터 로드
                value = await self.redis.get(key)
                if value:
                    data = json.loads(value)
                    history = data.get("conversation_history", [])
                else:
                    data = {}
                    history = []
                
                # 사용자 메시지 추가 (없는 경우)
                if not history or history[-1].get("role") != "user":
                    history.append({
                        "role": "user",
                        "content": user_query,
                        "timestamp": datetime.now().isoformat()
                    })
                
                # Assistant 답변 추가
                history.append({
                    "role": "assistant",
                    "content": answer,
                    "timestamp": datetime.now().isoformat()
                })
                
                # 최대 개수 제한
                MAX_HISTORY = 20
                if len(history) > MAX_HISTORY:
                    history = history[-MAX_HISTORY:]
                
                # 기존 컨텍스트 유지
                data["conversation_history"] = history
                data["last_updated"] = datetime.now().isoformat()
                
                await self.redis.setex(key, self.ttl, json.dumps(data, ensure_ascii=False, default=str))
            except Exception as e:
                print(f"[WARNING] 답변 저장 실패 (session_id={session_id}): {e}")
    
    async def save_session_state(self, session_id: str, state: Dict[str, Any]) -> None:
        """세션 state 전체 저장 (conversation_history + 모든 컨텍스트).
        
        Args:
            session_id: 세션 ID
            state: OrchestratorState 딕셔너리
        """
        if not session_id:
            return
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id)
            
            try:
                # 기존 데이터 로드 (타임아웃 설정)
                try:
                    value = await asyncio.wait_for(self.redis.get(key), timeout=5.0)
                except asyncio.TimeoutError:
                    print(f"[WARNING] 세션 데이터 로드 타임아웃 (session_id={session_id}): Redis 서버 응답 없음, 새 데이터로 저장 시도")
                    value = None

                if value:
                    existing_data = json.loads(value)
                else:
                    existing_data = {}
                
                # conversation_history 처리
                # state의 conversation_history와 기존 데이터의 conversation_history를 병합
                existing_history = existing_data.get("conversation_history", [])
                new_history = state.get("conversation_history", [])
                
                # 기존 히스토리와 새 히스토리를 병합 (중복 제거)
                conversation_history = existing_history.copy()
                
                # 새 히스토리에서 기존에 없는 메시지만 추가
                existing_content_set = {(msg.get("role"), msg.get("content", "")[:100]) for msg in existing_history}
                for msg in new_history:
                    msg_key = (msg.get("role"), msg.get("content", "")[:100])
                    if msg_key not in existing_content_set:
                        conversation_history.append(msg)
                        existing_content_set.add(msg_key)
                
                MAX_HISTORY = 20
                if len(conversation_history) > MAX_HISTORY:
                    conversation_history = conversation_history[-MAX_HISTORY:]
                
                # 세션 데이터 구성
                data = {
                    "conversation_history": conversation_history,
                    "email_search_context": state.get("email_search_context", existing_data.get("email_search_context", {})),
                    "email_compose_context": state.get("email_compose_context", existing_data.get("email_compose_context", {})),
                    "qa_search_context": state.get("qa_search_context", existing_data.get("qa_search_context", {})),
                    "tech_search_context": state.get("tech_search_context", existing_data.get("tech_search_context", {})),
                    "schedule_context": state.get("schedule_context", existing_data.get("schedule_context", {})),
                    "document_context": state.get("document_context", existing_data.get("document_context", {})),
                    "recruitment_context": state.get("recruitment_context", existing_data.get("recruitment_context", {})),
                    "last_updated": datetime.now().isoformat()
                }
                
                # 저장 시도 (타임아웃 설정)
                await asyncio.wait_for(
                    self.redis.setex(key, self.ttl, json.dumps(data, ensure_ascii=False, default=str)),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                print(f"[WARNING] 세션 state 저장 타임아웃 (session_id={session_id}): Redis 서버 응답 없음 (5초 초과)")
            except Exception as e:
                print(f"[WARNING] 세션 state 저장 실패 (session_id={session_id}): {e}")


# 전역 세션 관리자 인스턴스
_session_manager: Optional[SessionManager] = None


async def get_session_manager() -> Optional[SessionManager]:
    """세션 관리자 인스턴스 가져오기 (비동기).
    
    Returns:
        SessionManager 인스턴스 또는 None
    """
    global _session_manager
    
    if _session_manager is None:
        if not REDIS_AVAILABLE:
            print("[WARNING] redis.asyncio를 사용할 수 없습니다.")
            return None
        
        try:

            redis_host = _get_env("HOST")
            redis_port_str = _get_env("REDIS_PORT")
            redis_password = _get_env("REDIS_PASSWORD")
            redis_db_str = _get_env("REDIS_DB") or "0"
            
            # 포트 번호 필수 체크
            if not redis_port_str:
                raise ValueError("REDIS_PORT 환경 변수가 설정되지 않았습니다. .env 파일에 REDIS_PORT를 설정해주세요.")

            try:
                redis_port = int(redis_port_str.strip())
            except (ValueError, AttributeError) as e:
                raise ValueError(f"REDIS_PORT 값이 유효하지 않습니다: {redis_port_str}") from e
            
            try:
                redis_db = int(redis_db_str.strip())
            except (ValueError, AttributeError):
                redis_db = 0
            
            # 빈 문자열이거나 None인 경우 None으로 설정
            if not redis_password or redis_password.strip() == "":
                redis_password = None
            
            # 디버깅: 연결 정보 로그 (패스워드 제외)
            print(f"[INFO] Redis 연결 설정: host={redis_host}, port={redis_port}, db={redis_db}, password={'설정됨' if redis_password else 'None'}")

            if redis_password:
                redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}"
            else:
                redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
            
            # TTL 설정 (환경 변수에서 읽기)
            redis_ttl_str = _get_env("REDIS_TTL")
            try:
                redis_ttl = int(redis_ttl_str.strip()) if redis_ttl_str else 1800
            except (ValueError, AttributeError):
                redis_ttl = 1800  # 기본값: 30분
            

            # Redis 연결 타임아웃 설정 (환경 변수에서 읽기, 기본값: 10초)
            redis_timeout_str = _get_env("REDIS_TIMEOUT")
            try:
                redis_timeout = int(redis_timeout_str.strip()) if redis_timeout_str else 10
            except (ValueError, AttributeError):
                redis_timeout = 10  # 기본값: 10초

            # Redis 연결 풀 설정으로 타임아웃 및 재시도 설정
            redis_client = AsyncRedis.from_url(
                redis_url,
                decode_responses=False,
                socket_connect_timeout=redis_timeout,
                socket_timeout=redis_timeout,
                retry_on_timeout=True,
                health_check_interval=30,  # 30초마다 연결 상태 확인
            )
            _session_manager = SessionManager(redis_client, ttl=redis_ttl)
            print(f"[INFO] 세션 관리자 초기화 완료 (호스트: {redis_host}:{redis_port}, TTL: {redis_ttl}초, 타임아웃: {redis_timeout}초)")
        except Exception as e:
            print(f"[ERROR] 세션 관리자 초기화 실패: {e}")
            if 'redis_host' in locals() and 'redis_port' in locals():
                print(f"[ERROR] Redis 연결 시도 정보: host={redis_host}, port={redis_port}")
            import traceback
            traceback.print_exc()
            return None
    
    return _session_manager

