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
    
    def _get_key(self, session_id: str, employee_id: Optional[int] = None) -> str:
        """세션별 Redis 키 생성.
        
        Args:
            session_id: 세션 ID (형식: session_{uuid} 또는 session_{employee_id}_{uuid})
            employee_id: 사원 번호 (필수, None이면 오류 발생)
            
        Returns:
            Redis 키 (형식: session:{employee_id}:{session_id_part})
            
        Raises:
            ValueError: employee_id가 None인 경우
        """
        # employee_id가 None이면 오류 발생
        if employee_id is None:
            raise ValueError("employee_id는 필수입니다. 세션 키를 생성하려면 employee_id가 필요합니다.")
        
        # session_id에서 실제 세션 ID 부분만 추출
        session_id_part = session_id
        if session_id.startswith("session_"):
            parts = session_id.split("_")
            if len(parts) >= 3:
                # session_4003_xxx 형식인 경우, employee_id 부분 제외
                session_id_part = "_".join(parts[2:])
            elif len(parts) == 2:
                # session_xxx 형식
                session_id_part = parts[1]
            else:
                # session_ 만 있는 경우
                session_id_part = session_id.replace("session_", "", 1)
        else:
            # session_ 접두사가 없음
            session_id_part = session_id
        
        # 세션 ID에서 'session_' 접두사 제거 (중복 방지)
        if session_id_part.startswith("session_"):
            session_id_part = session_id_part.replace("session_", "", 1)
        
        # 키 생성: session:{employee_id}:{session_id_part}
        return f"session:{employee_id}:{session_id_part}"
    
    async def load_history(self, session_id: str, employee_id: Optional[int] = None) -> List[Message]:
        """세션의 대화 히스토리 로드.
        
        Args:
            session_id: 세션 ID
            employee_id: 사원 번호 (필수, None이면 오류 발생)
            
        Returns:
            대화 히스토리 리스트
        """
        if not session_id:
            return []
        
        if employee_id is None:
            print(f"[WARNING] 세션 히스토리 로드 실패: employee_id가 필요합니다 (session_id={session_id})")
            return []
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id, employee_id)
            try:
                value = await self.redis.get(key)
                
                if value is None:
                    return []
                
                data = json.loads(value)
                return data.get("conversation_history", [])
            except Exception as e:
                print(f"[WARNING] 세션 히스토리 로드 실패 (session_id={session_id}): {e}")
                return []
    
    async def load_session_data(self, session_id: str, employee_id: Optional[int] = None) -> Dict[str, Any]:
        """세션 데이터 전체 로드 (conversation_history + 컨텍스트들).
        
        Args:
            session_id: 세션 ID
            employee_id: 사원 번호 (필수, None이면 오류 발생)
            
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
        
        if employee_id is None:
            raise ValueError("employee_id는 필수입니다.")
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id, employee_id)
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
                    "recruitment_context": data.get("recruitment_context") or {},
                    "last_domain": data.get("last_domain")
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
                    "recruitment_context": {},
                    "last_domain": None
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
            state: OrchestratorState 딕셔너리 (employee_id 포함 필요)
        """
        if not session_id:
            return
        
        # state에서 employee_id 추출 (필수)
        employee_id = state.get("employee_id")
        if not employee_id:
            print(f"[WARNING] 세션 state에 employee_id가 없습니다 (session_id={session_id})")
            return
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id, employee_id)
            
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
                
                # 세션 제목 생성 (첫 번째 사용자 메시지 기반)
                title = existing_data.get("title", "새 대화")
                if conversation_history:
                    for msg in conversation_history:
                        if msg.get("role") == "user":
                            content = msg.get("content", "").strip()
                            if content:
                                title = content[:30]  # 첫 30자만 제목으로 사용
                                break
                # 세션 데이터 구성 (메타데이터 포함)
                now = datetime.now().isoformat()
                data = {
                    "conversation_history": conversation_history,
                    "email_search_context": state.get("email_search_context", existing_data.get("email_search_context", {})),
                    "email_compose_context": state.get("email_compose_context", existing_data.get("email_compose_context", {})),
                    "qa_search_context": state.get("qa_search_context", existing_data.get("qa_search_context", {})),
                    "tech_search_context": state.get("tech_search_context", existing_data.get("tech_search_context", {})),
                    "schedule_context": state.get("schedule_context", existing_data.get("schedule_context", {})),
                    "document_context": state.get("document_context", existing_data.get("document_context", {})),
                    "recruitment_context": state.get("recruitment_context", existing_data.get("recruitment_context", {})),
                    "last_domain": state.get("last_domain", existing_data.get("last_domain")),  # last_domain 저장
                    "title": title,
                    "updated_at": now,
                    "last_updated": now  # 호환성을 위해 유지
                }
                
                # created_at은 기존 값 유지, 없으면 현재 시간
                if "created_at" in existing_data:
                    data["created_at"] = existing_data["created_at"]
                else:
                    data["created_at"] = now
                
                # employee_id 저장
                data["employee_id"] = employee_id
                
                # 저장 시도 (타임아웃 설정)
                await asyncio.wait_for(
                    self.redis.setex(key, self.ttl, json.dumps(data, ensure_ascii=False, default=str)),
                    timeout=5.0
                )
                
                # 사원 세션 목록에 추가/업데이트 (최신순 정렬을 위해 score 업데이트)
                sessions_key = self._get_employee_sessions_key(employee_id)
                timestamp = datetime.now().timestamp()
                await self.redis.zadd(sessions_key, {session_id: timestamp})
                await self.redis.expire(sessions_key, self.ttl)
            except asyncio.TimeoutError:
                print(f"[WARNING] 세션 state 저장 타임아웃 (session_id={session_id}): Redis 서버 응답 없음 (5초 초과)")
            except Exception as e:
                print(f"[WARNING] 세션 state 저장 실패 (session_id={session_id}): {e}")

    async def get_google_tokens(self, user_id: str) -> Optional[Dict[str, Any]]:
        """사용자별 Google OAuth 토큰 조회.
        
        Args:
            user_id: 토큰을 조회할 사용자 ID
        
        Returns:
            저장된 토큰 딕셔너리. 토큰이 없거나 오류가 발생하면 None.
        """
        if not user_id:
            return None
        
        try:
            key = f"google_tokens:{user_id}"
            value = await self.redis.get(key)
            if value is None:
                return None
            return json.loads(value)
        except Exception as exc:  # pragma: no cover - Redis 오류 로그
            print(f"[WARNING] Google 토큰 조회 실패 (user_id={user_id}): {exc}")
            return None

    async def save_google_tokens(self, user_id: str, tokens: Dict[str, Any], *, ttl: Optional[int] = None) -> None:
        """사용자별 Google OAuth 토큰 저장.
        
        Args:
            user_id: 토큰을 저장할 사용자 ID
            tokens: access_token, refresh_token 등을 포함한 토큰 정보
            ttl: 토큰 만료 시간(초). 지정하지 않으면 세션 기본 TTL 사용.
        """
        if not user_id or not tokens:
            return
        
        try:
            key = f"google_tokens:{user_id}"
            existing = await self.get_google_tokens(user_id) or {}
            merged_tokens = {**existing, **tokens}
            payload = json.dumps(merged_tokens, ensure_ascii=False, default=str)
            expiry = ttl if ttl is not None else self.ttl
            await self.redis.setex(key, expiry, payload)
        except Exception as exc:  # pragma: no cover - Redis 오류 로그
            print(f"[WARNING] Google 토큰 저장 실패 (user_id={user_id}): {exc}")
    
    def _get_employee_sessions_key(self, employee_id: int) -> str:
        """사원별 세션 목록 Redis 키."""
        return f"employee:{employee_id}:sessions"
    
    async def list_employee_sessions(self, employee_id: int) -> List[Dict[str, Any]]:
        """사원별 세션 목록 조회 (최신순).
        
        Args:
            employee_id: 사원 번호
            
        Returns:
            세션 메타데이터 리스트 (최신순 정렬)
        """
        try:
            sessions_key = self._get_employee_sessions_key(employee_id)
            # Sorted Set에서 세션 ID 목록 조회 (최신순, updated_at 기준 내림차순)
            session_ids = await self.redis.zrevrange(sessions_key, 0, -1)
            
            if not session_ids:
                return []
            
            sessions = []
            for session_id_bytes in session_ids:
                session_id = session_id_bytes.decode('utf-8') if isinstance(session_id_bytes, bytes) else session_id_bytes
                
                # 세션 데이터 로드
                session_data = await self.load_session_data(session_id, employee_id)
                if not session_data:
                    continue
                
                # 메타데이터 추출
                session_key = self._get_key(session_id, employee_id)
                ttl = await self.redis.ttl(session_key)
                
                # 세션 제목 생성 (첫 번째 사용자 메시지 또는 기본값)
                title = "새 대화"
                conversation_history = session_data.get("conversation_history", [])
                if conversation_history:
                    for msg in conversation_history:
                        if msg.get("role") == "user":
                            content = msg.get("content", "").strip()
                            # 첫 30자만 제목으로 사용
                            title = content[:30] if content else "새 대화"
                            break
                
                # 세션 메타데이터 구성
                # updated_at 우선순위: updated_at → last_updated → conversation_history의 마지막 메시지 timestamp → created_at
                updated_at = session_data.get("updated_at") or session_data.get("last_updated")
                if not updated_at and conversation_history:
                    # conversation_history의 마지막 메시지 timestamp 사용
                    last_msg = conversation_history[-1]
                    updated_at = last_msg.get("timestamp") or last_msg.get("created_at")
                if not updated_at:
                    # 최후의 수단으로 created_at 사용
                    updated_at = session_data.get("created_at")
                
                # 디버깅 로그 (프로덕션에서는 제거 가능)
                print(f"[DEBUG] session_id={session_id}, updated_at={updated_at}, created_at={session_data.get('created_at')}")
                
                metadata = {
                    "session_id": session_id,
                    "title": session_data.get("title", title),
                    "employee_id": employee_id,
                    "message_count": len(conversation_history),
                    "created_at": session_data.get("created_at", datetime.now().isoformat()),
                    "updated_at": updated_at or datetime.now().isoformat(),  # 최후의 fallback만 현재 시간
                    "last_domain": session_data.get("last_domain"),
                    "ttl": ttl
                }
                sessions.append(metadata)
            
            return sessions
        except Exception as e:
            print(f"[WARNING] 사원 세션 목록 조회 실패 (employee_id={employee_id}): {e}")
            return []
    
    async def create_session(self, employee_id: int, session_id: Optional[str] = None, title: Optional[str] = None) -> str:
        """새 세션 생성 및 사원 세션 목록에 추가.
        
        Args:
            employee_id: 사원 번호
            session_id: 세션 ID (None이면 자동 생성)
            title: 세션 제목 (None이면 "새 대화")
            
        Returns:
            생성된 세션 ID
        """
        import uuid
        
        # 세션 ID 자동 생성 (employee_id 제외)
        if not session_id:
            session_id = f"session_{uuid.uuid4().hex[:8]}"
        
        try:
            now = datetime.now().isoformat()
            
            # 세션 데이터 초기화 (메타데이터 포함)
            session_data = {
                "conversation_history": [],
                "email_search_context": {},
                "email_compose_context": {},
                "qa_search_context": {},
                "tech_search_context": {},
                "schedule_context": {},
                "document_context": {},
                "recruitment_context": {},
                "title": title or "새 대화",
                "employee_id": employee_id,
                "created_at": now,
                "updated_at": now
            }
            
            # 세션 저장 (employee_id 포함하여 키 생성)
            key = self._get_key(session_id, employee_id)
            await self.redis.setex(
                key,
                self.ttl,
                json.dumps(session_data, ensure_ascii=False, default=str)
            )
            
            # 사원 세션 목록에 추가 (Sorted Set, score는 현재 timestamp)
            sessions_key = self._get_employee_sessions_key(employee_id)
            timestamp = datetime.now().timestamp()
            await self.redis.zadd(sessions_key, {session_id: timestamp})
            # 사원 세션 목록도 TTL 설정 (세션 TTL과 동일)
            await self.redis.expire(sessions_key, self.ttl)
            
            return session_id
        except Exception as e:
            print(f"[ERROR] 세션 생성 실패 (employee_id={employee_id}, session_id={session_id}): {e}")
            raise
    
    async def delete_session(self, session_id: str, employee_id: Optional[int] = None) -> bool:
        """세션 삭제 (완전 삭제).
        
        세션 데이터, 대화 히스토리, 모든 컨텍스트를 Redis에서 완전히 삭제합니다.
        
        Args:
            session_id: 삭제할 세션 ID
            employee_id: 사원 번호 (필수, None이면 오류 발생)
            
        Returns:
            삭제 성공 여부
        """
        if not session_id:
            return False
        
        if employee_id is None:
            print(f"[WARNING] 세션 삭제 실패: employee_id가 필요합니다 (session_id={session_id})")
            return False
        
        try:
            # 세션 데이터 키 생성
            key = self._get_key(session_id, employee_id)
            
            # 세션 데이터 존재 여부 확인
            exists = await self.redis.exists(key)
            if exists:
                # 세션 데이터 완전 삭제 (conversation_history, 모든 컨텍스트 포함)
                deleted = await self.redis.delete(key)
                print(f"[INFO] 세션 데이터 삭제 완료 (session_id={session_id}, employee_id={employee_id}, key={key}, deleted={deleted})")
            else:
                print(f"[INFO] 세션 데이터가 이미 없음 (session_id={session_id}, key={key})")
            
            # 사원 세션 목록에서 제거
            sessions_key = self._get_employee_sessions_key(employee_id)
            removed = await self.redis.zrem(sessions_key, session_id)
            print(f"[INFO] 세션 목록에서 제거 완료 (session_id={session_id}, sessions_key={sessions_key}, removed={removed})")
            
            # 삭제 확인: 세션 데이터가 정말 삭제되었는지 확인
            final_check = await self.redis.exists(key)
            if final_check:
                print(f"[WARNING] 세션 데이터가 여전히 존재함 (session_id={session_id}, key={key}) - 재시도")
                await self.redis.delete(key)
            
            return True
        except Exception as e:
            print(f"[ERROR] 세션 삭제 실패 (session_id={session_id}, employee_id={employee_id}): {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def update_session_metadata(self, session_id: str, employee_id: int, title: Optional[str] = None) -> bool:
        """세션 메타데이터 업데이트 (제목 변경 등).
        
        Args:
            session_id: 세션 ID
            employee_id: 사원 번호 (필수)
            title: 새로운 제목 (None이면 업데이트 안 함)
            
        Returns:
            업데이트 성공 여부
        """
        if not session_id:
            return False
        
        if employee_id is None:
            print(f"[WARNING] 세션 메타데이터 업데이트 실패: employee_id가 필요합니다 (session_id={session_id})")
            return False
        
        lock = await self._get_lock(session_id)
        
        async with lock:
            key = self._get_key(session_id, employee_id)
            
            try:
                value = await self.redis.get(key)
                if not value:
                    return False
                
                data = json.loads(value)
                
                # 제목 업데이트
                if title is not None:
                    data["title"] = title
                
                # updated_at 업데이트
                data["updated_at"] = datetime.now().isoformat()
                
                # employee_id 저장
                data["employee_id"] = employee_id
                
                # 저장
                ttl = await self.redis.ttl(key)
                if ttl > 0:
                    await self.redis.setex(key, ttl, json.dumps(data, ensure_ascii=False, default=str))
                else:
                    await self.redis.setex(key, self.ttl, json.dumps(data, ensure_ascii=False, default=str))
                
                # 사원 세션 목록의 score 업데이트 (최신순 정렬을 위해)
                sessions_key = self._get_employee_sessions_key(employee_id)
                timestamp = datetime.now().timestamp()
                await self.redis.zadd(sessions_key, {session_id: timestamp})
                await self.redis.expire(sessions_key, self.ttl)
                
                return True
            except Exception as e:
                print(f"[WARNING] 세션 메타데이터 업데이트 실패 (session_id={session_id}): {e}")
                return False


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

            # 현재 실행 중인 이벤트 루프 가져오기 (Windows 호환성)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # 이벤트 루프가 실행 중이 아닌 경우 (일반적으로 발생하지 않음)
                loop = None
            
            # Redis 연결 풀 설정으로 타임아웃 및 재시도 설정
            # Windows에서 이벤트 루프 문제를 방지하기 위해 명시적으로 설정
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

