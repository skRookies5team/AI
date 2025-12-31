"""FastAPI 애플리케이션 - AI Agent 시스템 UI 및 API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, List

from fastapi import FastAPI, HTTPException, UploadFile, File as FastAPIFile, Form, Query, Response, Request, Depends, status
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, RedirectResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import json
from pydantic import BaseModel, Field
from typing import Dict, Any

from agents.orchestrator_v3 import get_orchestrator_graph
from agents.session_manager import get_session_manager
from agents.notifications import get_notification_queue
import asyncio
import threading
from datetime import datetime, timedelta
import time
from typing import Dict, Tuple

from google_auth_oauthlib.flow import Flow

# Orchestrator 그래프 캐시 (앱 시작 시 한 번만 생성)
_orchestrator_graph = None
_oauth_state_store: Dict[str, Tuple[str, float]] = {}
_OAUTH_STATE_TTL_SECONDS = 600

def get_cached_orchestrator_graph():
    """캐시된 오케스트레이터 그래프 반환 (앱 시작 시 한 번만 생성).
    
    세션 관리자가 대화 히스토리를 관리하므로 체크포인터 없이 실행합니다.
    """
    global _orchestrator_graph
    if _orchestrator_graph is None:
        print("🔄 Orchestrator 그래프 초기화 중... (첫 요청 시 한 번만)")
        # 체크포인터 없이 실행 (세션 관리자가 대화 히스토리 관리)
        _orchestrator_graph = get_orchestrator_graph(checkpointer=None)
        print("✅ Orchestrator 그래프 초기화 완료 (이제 재사용됩니다)")
    return _orchestrator_graph


def _get_app_base_url() -> str:
    """애플리케이션 기본 URL을 반환."""
    return os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")


async def _create_google_oauth_flow(employee_id: Optional[int] = None) -> Flow:
    """Google OAuth Flow 인스턴스를 생성.
    
    Args:
        employee_id: 사원 ID (선택). 지정하면 해당 사원의 client_id/client_secret을 우선 사용.
    
    DB에서 client_id/client_secret을 가져오고, 없으면 환경 변수를 사용합니다.
    """
    # DB에서 OAuth Client 설정 가져오기
    from agents.master_graphs.schedule.services.google_token_service import get_token_service
    token_service = get_token_service()
    
    # async 함수이므로 await 사용 (employee_id 전달)
    client_config = await token_service.get_oauth_client_config(employee_id=employee_id)
    
    if not client_config:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth 클라이언트 정보를 DB 또는 환경 변수에서 찾을 수 없습니다. DB에 client_id/client_secret을 저장하거나 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 환경 변수를 설정하세요.",
        )
    
    client_id = client_config["client_id"]
    client_secret = client_config["client_secret"]

    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI")
    if not redirect_uri:
        redirect_uri = f"{_get_app_base_url()}/auth/google/callback"

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "project_id": os.environ.get("GOOGLE_PROJECT_ID", "gaagoo-local"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": client_secret,
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=[
            "https://www.googleapis.com/auth/calendar",
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
    )
    flow.redirect_uri = redirect_uri
    return flow


def _cleanup_expired_oauth_states() -> None:
    """만료된 OAuth state를 정리."""
    now = time.time()
    expired_keys = [
        state for state, (_, ts) in _oauth_state_store.items()
        if now - ts > _OAUTH_STATE_TTL_SECONDS
    ]
    for state in expired_keys:
        _oauth_state_store.pop(state, None)

# FastAPI 앱 생성
app = FastAPI(
    title="깐부 AI Agent 시스템",
    description="AI 기반 사내 도우미 시스템",
    version="1.0.0"
)

# 인증 관련
security = HTTPBearer()

def cleanup_old_redis_sessions():
    """백그라운드에서 오래된 Redis 세션 데이터를 정리하는 함수."""
    try:
        from redis import Redis
        

        redis_host = os.environ.get("HOST")
        redis_port_str = os.environ.get("REDIS_PORT")
        redis_password = os.environ.get("REDIS_PASSWORD")
        redis_db_str = os.environ.get("REDIS_DB") or "0"
        
        # 포트 번호 필수 체크
        if not redis_port_str:
            print("[WARNING] REDIS_PORT 환경 변수가 설정되지 않아 백그라운드 세션 정리를 건너뜁니다.")
            return
        
        try:
            redis_port = int(redis_port_str.strip())
        except (ValueError, AttributeError) as e:
            print(f"[WARNING] REDIS_PORT 값이 유효하지 않아 백그라운드 세션 정리를 건너뜁니다: {redis_port_str}")
            return
        
        try:
            redis_db = int(redis_db_str.strip())
        except (ValueError, AttributeError):
            redis_db = 0
        
        # 빈 문자열이거나 None인 경우 None으로 설정
        if not redis_password or redis_password.strip() == "":
            redis_password = None
        
        client = Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            db=redis_db,
            decode_responses=False,
            socket_connect_timeout=5,
        )
        
        # 연결 테스트
        client.ping()
        
        # 만료된 키 삭제 (TTL이 -2인 키)
        # 체크포인터 관련 패턴은 더 이상 사용하지 않으므로 제거
        # 세션 관련 패턴만 정리
        patterns = [
            "session:*",  # SessionManager가 사용하는 세션 키
        ]
        
        deleted_count = 0
        for pattern in patterns:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
                for key in keys:
                    ttl = client.ttl(key)
                    if ttl == -2:  # 이미 만료된 키
                        client.delete(key)
                        deleted_count += 1
                if cursor == 0:
                    break
        
        if deleted_count > 0:
            print(f"🧹 [백그라운드 정리] {deleted_count}개의 만료된 키 삭제 완료")
        
        client.close()
        
    except Exception as e:
        # 백그라운드 작업이므로 오류가 발생해도 앱 실행에는 영향 없음
        print(f"⚠️ [백그라운드 정리] 오류 발생 (무시됨): {e}")


@app.on_event("startup")
async def startup_event():
    """앱 시작 시 세션 관리자 초기화, 그래프 초기화 및 백그라운드 정리 작업 시작."""
    global _orchestrator_graph
    try:
        print("🚀 FastAPI 앱 시작 - 세션 관리자 초기화 중...")
        
        # 세션 관리자 초기화
        session_manager = await get_session_manager()
        if session_manager:
            print("✅ 세션 관리자 초기화 완료")
        else:
            print("⚠️ 세션 관리자 초기화 실패 (Redis 연결 확인 필요)")
        
        # Orchestrator 그래프도 미리 초기화하여 첫 요청 시 지연 방지
        print("🔄 Orchestrator 그래프 사전 초기화 중...")
        _orchestrator_graph = get_orchestrator_graph(checkpointer=None)
        print("✅ Orchestrator 그래프 사전 초기화 완료 (첫 요청 시 지연 방지)")
        
        # 백그라운드에서 오래된 세션 정리 (비동기로 실행)
        print("🧹 백그라운드 세션 정리 작업 시작...")
        cleanup_thread = threading.Thread(target=cleanup_old_redis_sessions, daemon=True)
        cleanup_thread.start()
        
        # 주기적으로 정리하는 백그라운드 작업 (30분마다)
        def periodic_cleanup():
            import time
            while True:
                time.sleep(1800)  # 30분 대기
                cleanup_old_redis_sessions()
        
        periodic_thread = threading.Thread(target=periodic_cleanup, daemon=True)
        periodic_thread.start()
        print("✅ 주기적 세션 정리 작업 시작 (30분마다 실행)")
        
    except Exception as e:
        print(f"⚠️ 초기화 중 오류 발생 (계속 진행): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 백그라운드 태스크 정리."""
    try:
        from agents.master_graphs.email.email_search_agent import stop_all_email_checkers
        await stop_all_email_checkers()
        print("🛑 서버 종료: 이메일 체커 태스크 cancel 완료")
    except Exception as e:
        print(f"🛑 서버 종료: 이메일 체커 태스크 정리 실패(무시): {e}")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 환경에서는 모든 origin 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== 요청/응답 모델 =====

class ChatRequest(BaseModel):
    """챗봇 질의 모델."""

    query: str = Field(..., description="사용자 입력 질의 문장")
    session_id: Optional[str] = Field(default=None, description="세션 ID (멀티턴 대화 지원)")
    # employee_id는 인증된 사용자에서 자동으로 가져옴 (더 이상 필요 없음)


class ChatResponse(BaseModel):
    """통합 챗봇 응답 모델."""

    ok: bool = Field(..., description="요청 처리 성공 여부")
    message: str = Field(..., description="에이전트 응답 메시지")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="조회된 데이터 (있는 경우)")
    email_ids: list[str] = Field(default_factory=list, description="이메일 검색 결과 UID 리스트 (있는 경우)")
    email_draft: Optional[dict[str, Any]] = Field(default=None, description="이메일 초안 (있는 경우)")
    error: Optional[str] = Field(default=None, description="오류 메시지")
    session_id: Optional[str] = Field(default=None, description="세션 ID")
    document_path: Optional[str] = Field(default=None, description="생성된 문서 파일 경로 (있는 경우)")
    requires_input: Optional[bool] = Field(default=False, description="사용자 입력 필요 여부 (예시문 표시 시)")
    last_domain: Optional[str] = Field(default=None, description="LLM 라우팅 시 결정된 도메인 (schedule, document, qa, email, tech)")
    workflow_type: Optional[str] = Field(default=None, description="워크플로우 타입 (business_trip 등)")
    workflow_in_progress: Optional[bool] = Field(default=False, description="워크플로우 진행 중 여부")
    workflow_step_index: Optional[int] = Field(default=None, description="현재 워크플로우 단계 인덱스")


class EmailReplyRequest(BaseModel):
    """이메일 UID 기반 답장 요청 모델."""

    uid: str = Field(..., description="답장 대상 메일 UID")
    mode: str = Field(..., description="auto(자동) 또는 manual(수동)")
    message: str = Field(..., description="auto: 지시문/요청, manual: 답장 본문(그대로 전송)")
    session_id: Optional[str] = Field(default=None, description="세션 ID (선택)")


class EmailComposeModifyRequest(BaseModel):
    """작성중인 이메일 초안 직접 수정(저장) 요청."""

    session_id: str = Field(..., description="세션 ID")
    subject: str = Field(..., description="수정된 제목")
    body_text: str = Field(..., description="수정된 본문(텍스트)")

class EmailComposeSendRequest(BaseModel):
    """작성중인 이메일 초안 전송 요청."""

    session_id: str = Field(..., description="세션 ID")
    subject: str = Field(..., description="최종 제목")
    body_text: str = Field(..., description="최종 본문(텍스트)")


class EmailComposeCancelRequest(BaseModel):
    """작성중인 이메일 초안 취소(컨텍스트 초기화) 요청."""

    session_id: str = Field(..., description="세션 ID")


class NotificationReadRequest(BaseModel):
    """알림 읽음 처리 요청."""

    ids: list[str] = Field(default_factory=list, description="읽음 처리할 알림 id 리스트")


class NotificationTestRequest(BaseModel):
    """프론트/스트림 테스트용 알림 발행 요청(개발용).

    주의: 기본적으로 비활성화되어 있고, ENABLE_NOTIFICATION_TEST_ENDPOINT=true 일 때만 동작합니다.
    """

    kind: str = Field(default="email_new", description="알림 종류 (email_new 등)")


class SessionRequest(BaseModel):
    """세션 변경 요청 모델."""
    session_id: Optional[str] = Field(default=None, description="세션 ID (None이면 새 세션 생성)")
    # employee_id는 인증된 사용자에서 자동으로 가져옴 (더 이상 필요 없음)


class SessionResponse(BaseModel):
    """세션 정보 응답 모델."""
    ok: bool = Field(..., description="요청 처리 성공 여부")
    session_id: str = Field(..., description="세션 ID")
    message_count: int = Field(default=0, description="대화 히스토리 메시지 개수")
    message: Optional[str] = Field(default=None, description="응답 메시지")
    conversation_history: Optional[List[Dict[str, Any]]] = Field(default=None, description="대화 히스토리 (메시지 리스트)")
    last_domain: Optional[str] = Field(default=None, description="마지막 도메인 (schedule, document, qa, email, tech)")


class SessionMetadata(BaseModel):
    """세션 메타데이터 모델."""
    session_id: str = Field(..., description="세션 ID")
    title: str = Field(..., description="세션 제목")
    employee_id: int = Field(..., description="사원 번호")
    message_count: int = Field(default=0, description="대화 메시지 개수")
    created_at: str = Field(..., description="생성일시 (ISO 형식)")
    updated_at: str = Field(..., description="최종 수정일시 (ISO 형식)")
    last_domain: Optional[str] = Field(default=None, description="마지막 도메인")
    ttl: Optional[int] = Field(default=None, description="TTL (초)")


class SessionListResponse(BaseModel):
    """세션 목록 응답 모델."""
    ok: bool = Field(..., description="요청 처리 성공 여부")
    sessions: List[SessionMetadata] = Field(default_factory=list, description="세션 목록")
    message: Optional[str] = Field(default=None, description="응답 메시지")


class SessionCreateRequest(BaseModel):
    """세션 생성 요청 모델."""
    # employee_id는 인증된 사용자에서 자동으로 가져옴 (더 이상 필요 없음)
    title: Optional[str] = Field(default=None, description="세션 제목")


class SessionUpdateRequest(BaseModel):
    """세션 업데이트 요청 모델."""
    title: Optional[str] = Field(default=None, description="새 세션 제목")


class MessagePayload(BaseModel):
    """단일 메시지 페이로드."""
    role: str = Field(..., description="메시지 역할 (user 또는 assistant)")
    content: str = Field(..., description="메시지 내용")
    timestamp: Optional[str] = Field(default=None, description="타임스탬프 (ISO 8601)")


class AddMessagesRequest(BaseModel):
    """메시지 추가 요청 모델."""
    messages: List[MessagePayload] = Field(..., description="추가할 메시지 리스트")


class DocumentUploadResponse(BaseModel):
    """문서 업로드 응답 모델."""

    ok: bool = Field(..., description="업로드 성공 여부")
    message: str = Field(..., description="응답 메시지")
    document_title: Optional[str] = Field(default=None, description="업로드된 문서 제목")
    error: Optional[str] = Field(default=None, description="오류 메시지")
    last_domain: Optional[str] = Field(default="document", description="LLM 라우팅 시 결정된 도메인 (문서 업로드는 항상 'document')")


# ===== 엔드포인트 =====

@app.get("/login", response_class=HTMLResponse)
async def serve_login():
    """로그인 페이지."""
    html_file = Path(__file__).parent / "templates" / "login.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="login.html을 찾을 수 없습니다.")
    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """메인 UI 페이지 (index.html)."""
    html_file = Path(__file__).parent / "templates" / "index.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="index.html을 찾을 수 없습니다.")
    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """현재 로그인한 사용자 조회 (의존성 주입).
    
    Args:
        credentials: HTTP Bearer 토큰
        
    Returns:
        사용자 정보 (employee_id 포함)
        
    Raises:
        HTTPException: 인증 실패 시
    """
    from agents.auth_service import get_auth_service
    
    session_token = credentials.credentials
    
    auth_service = await get_auth_service()
    session_data = await auth_service.get_auth_session(session_token)
    
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 세션이 만료되었거나 유효하지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return session_data


@app.post("/api/auth/login")
async def login(
    login_id: str = Form(...),
    password: str = Form(...),
    request: Request = None
) -> Dict[str, Any]:
    """사용자 로그인 (employee 테이블의 login_id와 password 사용).
    
    Args:
        login_id: 로그인 ID
        password: 비밀번호
        request: FastAPI Request 객체 (IP 주소 추출용)
        
    Returns:
        세션 토큰 및 사용자 정보
    """
    from agents.auth_service import get_auth_service
    
    auth_service = await get_auth_service()
    
    # employee 테이블에서 인증
    employee = await auth_service.authenticate_employee(login_id, password)
    
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인 ID 또는 비밀번호가 올바르지 않습니다.",
        )
    
    # 인증 세션 생성
    ip_address = request.client.host if request else None
    user_agent = request.headers.get("user-agent") if request else None
    
    session_info = await auth_service.create_auth_session(
        employee_id=employee["employee_id"],
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    return {
        "ok": True,
        "session_token": session_info["session_token"],
        "employee_id": employee["employee_id"],
        "name": employee.get("name"),
        "dept": employee.get("dept"),
        "position": employee.get("position"),
        "expires_at": session_info["expires_at"]
    }


@app.get("/api/auth/me")
async def get_current_user_info(
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    """현재 로그인한 사용자 정보 조회."""
    return {
        "ok": True,
        "employee_id": current_user["employee_id"],
        "expires_at": current_user.get("expires_at")
    }


@app.post("/api/auth/logout")
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """사용자 로그아웃."""
    from agents.auth_service import get_auth_service
    
    session_token = credentials.credentials
    auth_service = await get_auth_service()
    success = await auth_service.revoke_session(session_token)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="로그아웃에 실패했습니다.",
        )
    
    return {"ok": True, "message": "로그아웃되었습니다."}


@app.get("/api/documents/{filename:path}")
async def serve_document(filename: str):
    """생성된 문서 파일을 서빙합니다."""
    # 보안: 경로 탐색 공격 방지
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    document_path = Path(__file__).parent / "output" / "documents" / filename
    if not document_path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    
    return FileResponse(
        path=document_path,
        media_type="text/html",
        filename=filename
    )


@app.get("/api/notifications/stream/session")
async def notifications_stream_session(session_id: str):
    """세션별 알림 스트림(SSE) - 레거시.

    기존 구현은 session_id 기반 큐를 사용합니다.
    """

    async def event_generator():
        q = get_notification_queue(session_id)
        while True:
            try:
                # 15초마다 ping으로 연결 유지
                payload = await asyncio.wait_for(q.get(), timeout=15.0)
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield "event: ping\ndata: {}\n\n"
            except asyncio.CancelledError:
                break
            except Exception as e:
                err = {"type": "error", "message": str(e)}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/notifications/stream")
async def notifications_stream_employee(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """employee_id 단위 알림 스트림(SSE).

    - Authorization(Bearer) 기반으로 employee_id를 식별합니다.
    - employee_id 큐 + Redis 영속 저장과 함께 사용됩니다.
    """
    from agents.notifications import get_employee_notification_queue

    employee_id = current_user["employee_id"]

    async def event_generator():
        q = get_employee_notification_queue(employee_id)
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15.0)
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield "event: ping\ndata: {}\n\n"
            except asyncio.CancelledError:
                break
            except Exception as e:
                err = {"type": "error", "message": str(e)}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/notifications")
async def list_notifications(
    limit: int = 50,
    unread_only: bool = False,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """employee_id 알림 목록 조회 (Redis 영속 저장분)."""
    from agents.notifications import list_employee_notifications

    employee_id = current_user["employee_id"]
    items = await list_employee_notifications(employee_id, limit=limit, unread_only=unread_only)
    return {"ok": True, "items": items}


@app.post("/api/notifications/read")
async def read_notifications(
    payload: NotificationReadRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """employee_id 알림 읽음 처리."""
    from agents.notifications import mark_employee_notifications_read

    employee_id = current_user["employee_id"]
    updated = await mark_employee_notifications_read(employee_id, payload.ids or [])
    return {"ok": True, "updated": updated}


@app.post("/api/notifications/clear")
async def clear_notifications(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """employee_id 알림 전체 삭제."""
    from agents.notifications import clear_employee_notifications

    employee_id = current_user["employee_id"]
    await clear_employee_notifications(employee_id)
    return {"ok": True}


@app.post("/api/notifications/test")
async def notifications_test_publish(
    payload: NotificationTestRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """개발/디버그용: 현재 로그인한 employee에게 테스트 알림을 1건 발행합니다.

    - 기본적으로는 404로 막습니다.
    - ENABLE_NOTIFICATION_TEST_ENDPOINT=true 일 때만 활성화됩니다.
    """

    if os.environ.get("ENABLE_NOTIFICATION_TEST_ENDPOINT", "").lower() not in ("1", "true", "yes", "y"):
        raise HTTPException(status_code=404, detail="Not Found")

    from agents.notifications import publish_employee_notification

    employee_id = current_user["employee_id"]

    if payload.kind == "email_new":
        body = {
            "type": "email_new",
            "count": 1,
            "items": [
                {
                    "index": "1",
                    "uid": "TEST-UID",
                    "subject": "테스트 메일 알림",
                    "from": "test@example.com",
                    "date": datetime.now().isoformat(),
                }
            ],
        }
    else:
        body = {"type": payload.kind, "message": "test"}

    item = await publish_employee_notification(int(employee_id), body)
    return {"ok": True, "item": item}


@app.post("/api/chat/stream")
async def chat_stream_endpoint(
    payload: ChatRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """스트리밍 챗봇 엔드포인트 - 실시간 작업 상태 표시."""
    async def generate():
        try:
            # 인증된 사용자의 employee_id 사용
            employee_id = current_user["employee_id"]
            
            # 세션 ID 설정 (employee_id 기반으로 고정 세션 사용)
            # session_id가 없으면 새 세션 생성 (첫 메시지를 타이틀로 사용)
            if not payload.session_id:
                session_manager = await get_session_manager()
                if session_manager:
                    # 첫 메시지를 타이틀로 사용하여 새 세션 생성
                    title = payload.query[:30] if payload.query else "새 대화"  # 첫 30자만 타이틀로 사용
                    session_id = await session_manager.create_session(
                        employee_id=employee_id,
                        title=title
                    )
                else:
                    import uuid
                    session_id = f"session_{employee_id}_{uuid.uuid4().hex[:8]}"
            else:
                session_id = payload.session_id

            # ✅ 첫 채팅 요청에서 현재 세션을 알 수 있으므로, 해당 세션을 수신감지 대상에 등록 + supervisor(프로세스당 1개) 시작
            try:
                from agents.master_graphs.email.email_search_agent import (
                    register_email_check_session,
                    start_email_checker_supervisor,
                )
                register_email_check_session(session_id, employee_id)
                start_email_checker_supervisor(check_interval=int(os.environ.get("EMAIL_CHECK_INTERVAL", "30")))
            except Exception as e:
                print(f"⚠️ 이메일 체커 시작 실패 (session_id={session_id}): {e}")
            
            # 세션 관리자로 이전 세션 데이터 전체 로드 (conversation_history + 컨텍스트들)
            session_manager = await get_session_manager()
            session_data = {
                "conversation_history": [],
                "email_search_context": {},
                "email_compose_context": {},
                "qa_search_context": {},
                "tech_search_context": {},
                "schedule_context": {},
                "document_context": {},
                "recruitment_context": {}
            }
            if session_manager:
                session_data = await session_manager.load_session_data(session_id, employee_id)
            
            # 그래프 가져오기 (체크포인터 없이 실행)
            graph = get_cached_orchestrator_graph()
            
            # 그래프가 None이면 초기화 (startup 실패 시 fallback)
            if graph is None:
                _orchestrator_graph = get_orchestrator_graph(checkpointer=None)
                graph = _orchestrator_graph
            
            input_state = {
                "query": payload.query,
                "user_id": employee_id,
                "session_id": session_id,
                "employee_id": employee_id,
                "conversation_history": session_data.get("conversation_history", []),
                "email_search_context": session_data.get("email_search_context", {}),
                "email_compose_context": session_data.get("email_compose_context") or {},
                "qa_search_context": session_data.get("qa_search_context", {}),
                "tech_search_context": session_data.get("tech_search_context", {}),
                "schedule_context": session_data.get("schedule_context", {}),
                "document_context": session_data.get("document_context", {}),
                "recruitment_context": session_data.get("recruitment_context", {}),
                # ⚠️ 워크플로우 관련 필드 추가
                "workflow_temp_data": session_data.get("workflow_temp_data", {}),
                "workflow_type": session_data.get("workflow_type"),
                "workflow_steps": session_data.get("workflow_steps", []),
                "current_workflow_step_index": session_data.get("current_workflow_step_index", 0),
                "workflow_completed_steps": session_data.get("workflow_completed_steps", []),
                "workflow_in_progress": session_data.get("workflow_in_progress", False),
                "next_domain": session_data.get("next_domain"),
                "last_domain": session_data.get("last_domain"),
            }
            
            config = {"configurable": {"thread_id": session_id}}
            
            # 노드 이름과 상태 메시지 매핑
            node_status_messages = {
                "route_to_domain": "🔍 의도 분석 중...",
                "schedule": "📅 스케줄 에이전트 작업 중...",
                "document": "📝 문서 에이전트 작업 중...",
                "qa": "❓ Q&A 에이전트 작업 중...",
                "email": "📧 이메일 에이전트 작업 중...",
                "tech": "🔬 기술 에이전트 작업 중...",
            }
            
            # 즉시 첫 번째 상태 메시지 전송 (사용자에게 빠른 피드백 제공)
            initial_status_msg = {
                "type": "status",
                "message": "🔍 요청 처리 중...",
                "node": "initializing"
            }
            yield f"data: {json.dumps(initial_status_msg, ensure_ascii=False)}\n\n"
            
            final_state = None
            last_status = None
            
            async for chunk in graph.astream(input_state, config=config):
                # 각 노드 실행 시 상태 메시지 전송
                for node_name, node_state in chunk.items():
                    # 마지막 상태 저장
                    final_state = node_state
                    
                    # 상태 메시지 전송 (중복 방지)
                    if node_name in node_status_messages and last_status != node_name:
                        status_msg = {
                            "type": "status",
                            "message": node_status_messages[node_name],
                            "node": node_name
                        }
                        yield f"data: {json.dumps(status_msg, ensure_ascii=False)}\n\n"
                        last_status = node_name
            
            # 최종 결과 전송
            if final_state:
                final_msg = final_state.get("final_response")
                conversation_history = final_state.get("conversation_history", [])
                
                if not final_msg and conversation_history:
                    recent_assistant_msgs = [
                        msg for msg in conversation_history 
                        if msg.get("role") == "assistant"
                    ]
                    if recent_assistant_msgs:
                        final_msg = recent_assistant_msgs[-1].get("content", None)
                
                if not final_msg:
                    final_msg = "요청하신 작업을 완료했습니다."
                
                current_domain = final_state.get("domain")
                workflow_in_progress = final_state.get("workflow_in_progress", False)
                
                # document_path 추출 (워크플로우 진행 중이면 도메인 상관없이 반환)
                document_path = None
                if workflow_in_progress:
                    # 워크플로우 진행 중이면 document_path 유지 (도메인 전환 후에도 문서 미리보기 유지)
                    document_path = final_state.get("document_path")
                elif current_domain == "document":
                    # 일반 문서 요청인 경우에만 document_path 사용
                    document_path = final_state.get("document_path")
                
                requires_input = final_state.get("requires_input", False)
                
                # 도메인 정보 추출 (LLM 라우팅 시 결정된 도메인)
                # domain이 없으면 last_domain을 fallback으로 사용
                last_domain = final_state.get("domain") or final_state.get("last_domain")
                
                # 도메인별 rows/email_ids 추출
                rows = []
                email_ids: list[str] = []
                email_draft = None
                if last_domain == "recruitment":
                    search_results = final_state.get("search_results", [])
                    if search_results:
                        rows = search_results
                elif last_domain == "tech":
                    # Tech 도메인(논문/특허)인 경우 검색 결과를 rows에 포함
                    search_results = final_state.get("search_results", [])
                    if search_results:
                        rows = search_results
                    rows = final_state.get("search_results", []) or []
                elif last_domain == "email":
                    rows = final_state.get("search_results", []) or []
                    email_ids = final_state.get("email_ids", []) or []
                    email_draft = final_state.get("email_draft")
                    if isinstance(email_draft, dict):
                        ctx = final_state.get("email_compose_context") or {}
                        if isinstance(ctx, dict):
                            to_email = ctx.get("to_email") or ", ".join(ctx.get("to_emails", []) or [])
                            if to_email:
                                email_draft.setdefault("to", to_email)
                            if ctx.get("cc"):
                                email_draft.setdefault("cc", ctx.get("cc"))
                            if ctx.get("bcc"):
                                email_draft.setdefault("bcc", ctx.get("bcc"))

                # 문서 업데이트 UI 표시 플래그 확인
                show_document_update_ui = final_state.get("show_document_update_ui", False)
                
                # 면접 일정 조정 관련 플래그 확인
                # 1순위: final_state 최상위 레벨 (schedule_nodes.py에서 직접 반환)
                # 2순위: schedule_context.interview (하위 호환성)
                schedule_context = final_state.get("schedule_context") or {}  # ✅ None 체크 추가
                interview_context = schedule_context.get("interview", {}) if schedule_context else {}  # ✅ 안전하게 처리

                # 최상위 레벨 우선, 없으면 interview_context에서 읽기
                requires_interviewer_selection = final_state.get("requires_interviewer_selection",
                                                                 interview_context.get("requires_interviewer_selection", False))
                requires_date_range = final_state.get("requires_date_range_input",
                                                     interview_context.get("requires_date_range", False))
                requires_duration_selection = final_state.get("requires_duration_selection",
                                                               interview_context.get("requires_duration_selection", False))  # ✅ 추가
                requires_slot_selection = final_state.get("requires_slot_selection",
                                                         interview_context.get("requires_slot_selection", False))
                interviewer_selection_config = final_state.get("interviewer_selection_config",
                                                              interview_context.get("interviewer_selection_config"))
                available_slots = final_state.get("available_slots",
                                                 interview_context.get("available_slots")) or []  # ✅ None일 경우 빈 배열로 처리 or []  # ✅ None일 경우 빈 배열로 처리

                # 디버깅: 문서 업데이트 UI 플래그 확인
                print(f"🔍 [FastAPI] 문서 업데이트 UI 플래그: {show_document_update_ui}")
                print(f"🔍 [FastAPI] 면접관 선택 플래그: {requires_interviewer_selection}")
                print(f"🔍 [FastAPI] 기간 입력 플래그: {requires_date_range}")
                print(f"🔍 [FastAPI] 지속 시간 선택 플래그: {requires_duration_selection}")  # ✅ 추가
                print(f"🔍 [FastAPI] 시간대 선택 플래그: {requires_slot_selection}")
                print(f"🔍 [FastAPI] interview_context: {interview_context}")
                print(f"🔍 [FastAPI] final_state keys: {list(final_state.keys())}")
                
                result = {
                    "type": "complete",
                    "ok": True,
                    "message": final_msg,
                    "rows": rows,  # Recruitment 검색 결과 또는 빈 배열
                    "email_ids": email_ids,  # ✅ 이메일 검색 UID 리스트
                    "email_draft": email_draft,  # ✅ 이메일 초안 (작성 흐름)
                    "session_id": session_id,
                    "document_path": document_path,
                    "requires_input": requires_input,
                    "last_domain": last_domain,  # LLM 라우팅 시 결정된 도메인
                    "show_document_update_ui": show_document_update_ui,  # 문서 업데이트 UI 표시 플래그
                # 면접 일정 조정 관련 플래그
                "requires_interviewer_selection": requires_interviewer_selection,
                "requires_date_range": requires_date_range,
                "requires_duration_selection": requires_duration_selection,  # ✅ 수정: 계산된 변수 사용
                "requires_slot_selection": requires_slot_selection,
                "interviewer_selection_config": interviewer_selection_config,
                "available_slots": available_slots,
                }
                
                print(f"🔍 [FastAPI] 응답에 포함된 show_document_update_ui: {result.get('show_document_update_ui')}")
                print(f"🔍 [FastAPI] 응답에 포함된 requires_interviewer_selection: {result.get('requires_interviewer_selection')}")
                yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
                
                # 노드 실행 후 세션 데이터 저장 (conversation_history + 컨텍스트들)
                if session_manager:
                    # 사용자 메시지 추가
                    user_message = {
                        "role": "user",
                        "content": payload.query,
                        "timestamp": datetime.now().isoformat()
                    }
                    
                    # conversation_history 업데이트
                    # 기존 세션 데이터의 conversation_history를 가져와서 병합
                    existing_history = session_data.get("conversation_history", [])
                    new_history = list(final_state.get("conversation_history", []))
                    
                    # 기존 히스토리와 새 히스토리를 병합 (중복 제거)
                    # final_state의 conversation_history는 operator.add로 추가된 것이므로
                    # 기존 히스토리 + 새 메시지들로 구성됨
                    # 하지만 이전 대화 확인 질문의 경우 새로 생성될 수 있으므로 병합 필요
                    conversation_history = existing_history.copy()
                    
                    # 새 히스토리에서 기존에 없는 메시지만 추가
                    existing_content_set = {(msg.get("role"), msg.get("content", "")[:100]) for msg in existing_history}
                    for msg in new_history:
                        msg_key = (msg.get("role"), msg.get("content", "")[:100])
                        if msg_key not in existing_content_set:
                            conversation_history.append(msg)
                            existing_content_set.add(msg_key)
                    
                    # 사용자 메시지가 없으면 추가
                    if not conversation_history or conversation_history[-1].get("role") != "user":
                        conversation_history.append(user_message)
                    
                    # Assistant 메시지 추가 (없으면 final_response로 생성)
                    if not conversation_history or conversation_history[-1].get("role") != "assistant":
                        if final_msg:
                            # 이메일 검색 결과인 경우 HTML 대신 요약 메시지와 UID 리스트만 저장
                            # email_ids는 master_graph에서 반환된 result에서 가져옴
                            email_ids = final_state.get("email_ids", [])
                            last_keyword = final_state.get("last_search_keyword")
                            
                            # HTML이 포함되어 있거나 email_ids가 있으면 이메일 검색 결과로 판단
                            is_email_search_result = (
                                email_ids or 
                                "<div" in final_msg or 
                                "📧" in final_msg or 
                                ("메일" in final_msg and ("찾았" in final_msg or "검색" in final_msg))
                            )
                            
                            if is_email_search_result and email_ids:
                                # 이메일 검색 결과: HTML 대신 요약 메시지와 UID 리스트만 저장
                                email_count = len(email_ids)
                                search_keyword = last_keyword or "검색"
                                conversation_summary = f"📧 '{search_keyword}' 관련 메일 {email_count}개를 찾았습니다."
                                
                                assistant_message = {
                                    "role": "assistant",
                                    "content": conversation_summary,
                                    "timestamp": datetime.now().isoformat(),
                                    "email_ids": email_ids  # UID 리스트만 저장 (HTML 제외)
                                }
                            else:
                                # 일반 메시지 (HTML이 있어도 email_ids가 없으면 그대로 저장)
                                # 단, HTML이 너무 길면 요약
                                if len(final_msg) > 10000 and "<div" in final_msg:
                                    # HTML이 너무 길면 요약 메시지로 저장
                                    conversation_summary = "📧 이메일 검색 결과를 표시했습니다."
                                    assistant_message = {
                                        "role": "assistant",
                                        "content": conversation_summary,
                                        "timestamp": datetime.now().isoformat()
                                    }
                                else:
                                    assistant_message = {
                                        "role": "assistant",
                                        "content": final_msg,
                                        "timestamp": datetime.now().isoformat()
                                    }
                            conversation_history.append(assistant_message)
                    
                    # 세션 state 전체 저장 (conversation_history + 모든 컨텍스트)
                    # ⚠️ 디버깅: final_state에서 workflow_temp_data 확인
                    workflow_temp_data_from_final = final_state.get("workflow_temp_data", {})
                    print(f"🔍 [FastAPI] final_state에서 가져온 workflow_temp_data: {list(workflow_temp_data_from_final.keys()) if workflow_temp_data_from_final else '비어있음'}")
                    
                    session_state = {
                        "conversation_history": conversation_history,
                        "email_search_context": final_state.get("email_search_context", {}),
                        # Redis/세션에 null로 저장되지 않도록 정규화
                        "email_compose_context": final_state.get("email_compose_context") or {},
                        "qa_search_context": final_state.get("qa_search_context", {}),
                        "tech_search_context": final_state.get("tech_search_context", {}),
                        "schedule_context": final_state.get("schedule_context", {}),
                        "document_context": final_state.get("document_context", {}),
                        "recruitment_context": final_state.get("recruitment_context", {}),
                        "last_domain": final_state.get("last_domain"),
                        "employee_id": employee_id,
                        # ⚠️ 워크플로우 관련 필드 추가
                        "workflow_temp_data": final_state.get("workflow_temp_data", {}),
                        "workflow_type": final_state.get("workflow_type"),
                        "workflow_steps": final_state.get("workflow_steps", []),
                        "current_workflow_step_index": final_state.get("current_workflow_step_index", 0),
                        "workflow_completed_steps": final_state.get("workflow_completed_steps", []),
                        "workflow_in_progress": final_state.get("workflow_in_progress", False),
                        "next_domain": final_state.get("next_domain"),
                        "title": final_state.get("title"),
                    }
                    
                    # ⚠️ 디버깅: session_state 확인
                    print(f"🔍 [FastAPI] session_state에 포함된 workflow_temp_data: {list(session_state.get('workflow_temp_data', {}).keys()) if session_state.get('workflow_temp_data') else '비어있음'}")
                    
                    await session_manager.save_session_state(session_id, session_state)
        
        except Exception as exc:
            import traceback
            traceback.print_exc()
            error_msg = {
                "type": "error",
                "message": f"처리 중 오류가 발생했습니다: {str(exc)}"
            }
            yield f"data: {json.dumps(error_msg, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(
    payload: ChatRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> ChatResponse:
    """LangGraph Orchestrator 기반 통합 챗봇 엔드포인트.
    
    지원 시나리오:
    - 스케줄 관리 (연차/반차 신청, 회의실 예약)
    - 문서 작성 (제품개발 계획서, 발주서 등)
    - Q&A (메뉴얼 검색, 복리후생, 회사식단)
    - 이메일 검색 및 자동 응답
    - 기술 검색 (특허, 논문)
    """
    try:
        # 인증된 사용자의 employee_id 사용
        employee_id = current_user["employee_id"]
        
        # session_id가 없으면 새 세션 생성
        if not payload.session_id:
            session_manager = await get_session_manager()
            if session_manager:
                session_id = await session_manager.create_session(employee_id=employee_id)
            else:
                import uuid
                session_id = f"session_{employee_id}_{uuid.uuid4().hex[:8]}"
        else:
            session_id = payload.session_id

        # ✅ 첫 채팅 요청에서 현재 세션을 알 수 있으므로, 해당 세션을 수신감지 대상에 등록 + supervisor(프로세스당 1개) 시작
        try:
            from agents.master_graphs.email.email_search_agent import (
                register_email_check_session,
                start_email_checker_supervisor,
            )
            employee_id = current_user["employee_id"]
            register_email_check_session(session_id, employee_id)
            start_email_checker_supervisor(check_interval=int(os.environ.get("EMAIL_CHECK_INTERVAL", "30")))
        except Exception as e:
            print(f"⚠️ 이메일 체커 시작 실패 (session_id={session_id}): {e}")
        
        # 세션 관리자로 이전 세션 데이터 전체 로드 (conversation_history + 컨텍스트들)
        session_manager = await get_session_manager()
        session_data = {
            "conversation_history": [],
            "email_search_context": {},
            "qa_search_context": {},
            "tech_search_context": {},
            "schedule_context": {},
            "document_context": {},
            "recruitment_context": {}
        }
        if session_manager:
            session_data = await session_manager.load_session_data(session_id, employee_id)
        
        # 캐시된 Orchestrator 그래프 가져오기 (체크포인터 없이 실행)
        graph = get_cached_orchestrator_graph()
        
        # 입력 state 구성 (이전 세션 데이터 포함)
        input_state = {
            "query": payload.query,
            "user_id": employee_id,
            "session_id": session_id,
            "employee_id": employee_id,
            "conversation_history": session_data.get("conversation_history", []),
            "email_search_context": session_data.get("email_search_context", {}),
            "email_compose_context": session_data.get("email_compose_context") or {},
            "qa_search_context": session_data.get("qa_search_context", {}),
            "tech_search_context": session_data.get("tech_search_context", {}),
            "schedule_context": session_data.get("schedule_context", {}),
            "document_context": session_data.get("document_context", {}),
            "recruitment_context": session_data.get("recruitment_context", {}),
            # ⚠️ 워크플로우 관련 필드 추가
            "workflow_temp_data": session_data.get("workflow_temp_data", {}),
            "workflow_type": session_data.get("workflow_type"),
            "workflow_steps": session_data.get("workflow_steps", []),
            "current_workflow_step_index": session_data.get("current_workflow_step_index", 0),
            "workflow_completed_steps": session_data.get("workflow_completed_steps", []),
            "workflow_in_progress": session_data.get("workflow_in_progress", False),
            "next_domain": session_data.get("next_domain"),
            "last_domain": session_data.get("last_domain"),
        }
        # 디버그 메시지 제거 (프로덕션 환경에서는 불필요)
        
        # LangGraph 체크포인터 없이 실행 (세션 관리자가 대화 히스토리 관리)
        config = {"configurable": {"thread_id": session_id}}
        
        # 그래프 실행
        final_state = await graph.ainvoke(input_state, config=config)
        
        # 노드 실행 후 세션 데이터 저장 (conversation_history + 컨텍스트들)
        if session_manager:
            # 사용자 메시지 추가
            user_message = {
                "role": "user",
                "content": payload.query,
                "timestamp": datetime.now().isoformat()
            }
            
            # conversation_history 업데이트
            # 기존 세션 데이터의 conversation_history를 가져와서 병합
            existing_history = session_data.get("conversation_history", [])
            new_history = list(final_state.get("conversation_history", []))
            
            # 기존 히스토리와 새 히스토리를 병합 (중복 제거)
            conversation_history = existing_history.copy()
            
            # 새 히스토리에서 기존에 없는 메시지만 추가
            existing_content_set = {(msg.get("role"), msg.get("content", "")[:100]) for msg in existing_history}
            for msg in new_history:
                msg_key = (msg.get("role"), msg.get("content", "")[:100])
                if msg_key not in existing_content_set:
                    conversation_history.append(msg)
                    existing_content_set.add(msg_key)
            
            # 사용자 메시지가 없으면 추가
            if not conversation_history or conversation_history[-1].get("role") != "user":
                conversation_history.append(user_message)
            
            # Assistant 메시지 추가 (없으면 final_response로 생성)
            if not conversation_history or conversation_history[-1].get("role") != "assistant":
                final_msg = final_state.get("final_response")
                if final_msg:
                    # 이메일 검색 결과인 경우 HTML 대신 요약 메시지와 UID 리스트만 저장
                    # email_ids는 master_graph에서 반환된 result에서 가져옴
                    email_ids = final_state.get("email_ids", [])
                    last_keyword = final_state.get("last_search_keyword")
                    
                    # HTML이 포함되어 있거나 email_ids가 있으면 이메일 검색 결과로 판단
                    is_email_search_result = (
                        email_ids or 
                        "<div" in final_msg or 
                        "📧" in final_msg or 
                        ("메일" in final_msg and ("찾았" in final_msg or "검색" in final_msg))
                    )
                    
                    if is_email_search_result and email_ids:
                        # 이메일 검색 결과: HTML 대신 요약 메시지와 UID 리스트만 저장
                        email_count = len(email_ids)
                        search_keyword = last_keyword or "검색"
                        conversation_summary = f"📧 '{search_keyword}' 관련 메일 {email_count}개를 찾았습니다."
                        
                        assistant_message = {
                            "role": "assistant",
                            "content": conversation_summary,
                            "timestamp": datetime.now().isoformat(),
                            "email_ids": email_ids  # UID 리스트만 저장 (HTML 제외)
                        }
                    else:
                        # 일반 메시지 (HTML이 있어도 email_ids가 없으면 그대로 저장)
                        # 단, HTML이 너무 길면 요약
                        if len(final_msg) > 10000 and "<div" in final_msg:
                            # HTML이 너무 길면 요약 메시지로 저장
                            conversation_summary = "📧 이메일 검색 결과를 표시했습니다."
                            assistant_message = {
                                "role": "assistant",
                                "content": conversation_summary,
                                "timestamp": datetime.now().isoformat()
                            }
                        else:
                            assistant_message = {
                                "role": "assistant",
                                "content": final_msg,
                                "timestamp": datetime.now().isoformat()
                            }
                    conversation_history.append(assistant_message)
            
            # 세션 state 전체 저장 (conversation_history + 모든 컨텍스트)
            # ⚠️ 디버깅: final_state에서 workflow_temp_data 확인
            workflow_temp_data_from_final = final_state.get("workflow_temp_data", {})
            print(f"🔍 [FastAPI] final_state에서 가져온 workflow_temp_data: {list(workflow_temp_data_from_final.keys()) if workflow_temp_data_from_final else '비어있음'}")
            
            session_state = {
                "conversation_history": conversation_history,
                "email_search_context": final_state.get("email_search_context", {}),
                # Redis/세션에 null로 저장되지 않도록 정규화
                "email_compose_context": final_state.get("email_compose_context") or {},
                "qa_search_context": final_state.get("qa_search_context", {}),
                "tech_search_context": final_state.get("tech_search_context", {}),
                "schedule_context": final_state.get("schedule_context", {}),
                "document_context": final_state.get("document_context", {}),
                "recruitment_context": final_state.get("recruitment_context", {}),
                "last_domain": final_state.get("last_domain"),
                "employee_id": employee_id,
                # ⚠️ 워크플로우 관련 필드 추가
                "workflow_temp_data": final_state.get("workflow_temp_data", {}),
                "workflow_type": final_state.get("workflow_type"),
                "workflow_steps": final_state.get("workflow_steps", []),
                "current_workflow_step_index": final_state.get("current_workflow_step_index", 0),
                "workflow_completed_steps": final_state.get("workflow_completed_steps", []),
                "workflow_in_progress": final_state.get("workflow_in_progress", False),
                "next_domain": final_state.get("next_domain"),
                "title": final_state.get("title"),
            }
            
            # ⚠️ 디버깅: session_state 확인
            print(f"�� [FastAPI] session_state에 포함된 workflow_temp_data: {list(session_state.get('workflow_temp_data', {}).keys()) if session_state.get('workflow_temp_data') else '비어있음'}")
            
            await session_manager.save_session_state(session_id, session_state)
        
        # Process Output
        messages = final_state.get("messages", [])
        final_msg = final_state.get("final_response")
        conversation_history = final_state.get("conversation_history", [])
        
        # final_response가 있으면 우선 사용 (문서 생성 등에서 설정된 경우)
        if final_msg:
            # final_response가 이미 설정되어 있으면 그대로 사용
            pass
        elif conversation_history:
            # final_response가 없으면 conversation_history에서 최근 assistant 메시지 찾기
            recent_assistant_msgs = [
                msg for msg in conversation_history 
                if msg.get("role") == "assistant"
            ]
            if recent_assistant_msgs:
                final_msg = recent_assistant_msgs[-1].get("content", None)
        
        # 여전히 final_msg가 없으면 messages에서 가져오기
        if not final_msg:
            final_msg = "\n".join(messages) if messages else "요청하신 작업을 완료했습니다."

        # 현재 도메인 확인
        current_domain = final_state.get("domain")
        
        # document_path 추출 (워크플로우 진행 중이면 도메인 상관없이 반환)
        document_path = None
        workflow_in_progress = final_state.get("workflow_in_progress", False)
        
        if workflow_in_progress:
            # 워크플로우 진행 중이면 document_path 유지 (도메인 전환 후에도 문서 미리보기 유지)
            document_path = final_state.get("document_path")
        elif current_domain == "document":
            # 일반 문서 요청인 경우에만 document_path 사용
            document_path = final_state.get("document_path")
        else:
            # 다른 도메인으로 전환된 경우 (워크플로우가 아닌 경우에만) 이전 document_path 제거
            document_path = None
        
        # requires_input 플래그 추출 (예시문 표시 시)
        requires_input = final_state.get("requires_input", False)
        
        # 도메인 정보 추출 (LLM 라우팅 시 결정된 도메인)
        # domain이 없으면 last_domain을 fallback으로 사용
        last_domain = final_state.get("domain") or final_state.get("last_domain")
        
        # 도메인별 rows/email_ids 추출
        rows: list[dict[str, Any]] = []
        email_ids: list[str] = []
        email_draft = None
        if last_domain == "recruitment":
            search_results = final_state.get("search_results", [])
            if search_results:
                # 카드 형식으로 변환
                rows = search_results
                print(f"✅ search_results에서 {len(rows)}개 인재 결과 추출")
            else:
                # search_results가 없으면 recruitment_context의 last_results에서 가져오기
                recruitment_context = final_state.get("recruitment_context", {})
                if recruitment_context and isinstance(recruitment_context, dict):
                    resume_search = recruitment_context.get("resume_search", {})
                    last_results = resume_search.get("last_results", [])
                    if last_results:
                        rows = last_results
                        print(f"✅ recruitment_context에서 {len(rows)}개 인재 결과 추출")
        elif last_domain == "email":
            rows = final_state.get("search_results", []) or []
            email_ids = final_state.get("email_ids", []) or []
            email_draft = final_state.get("email_draft")
            if isinstance(email_draft, dict):
                ctx = final_state.get("email_compose_context") or {}
                if isinstance(ctx, dict):
                    to_email = ctx.get("to_email") or ", ".join(ctx.get("to_emails", []) or [])
                    if to_email:
                        email_draft.setdefault("to", to_email)
                    if ctx.get("cc"):
                        email_draft.setdefault("cc", ctx.get("cc"))
                    if ctx.get("bcc"):
                        email_draft.setdefault("bcc", ctx.get("bcc"))
        elif last_domain == "tech":
            # Tech 도메인(논문/특허)인 경우 검색 결과를 rows에 포함
            search_results = final_state.get("search_results", [])
            if search_results:
                rows = search_results

        # 워크플로우 관련 필드 추출
        workflow_type = final_state.get("workflow_type")
        workflow_in_progress = final_state.get("workflow_in_progress", False)
        workflow_step_index = final_state.get("current_workflow_step_index")

        return ChatResponse(
            ok=True,
            message=final_msg,
            rows=rows,  # Recruitment 검색 결과 또는 빈 배열
            email_ids=email_ids,  # ✅ 이메일 검색 UID 리스트
            email_draft=email_draft,  # ✅ 이메일 초안 (작성 흐름)
            session_id=session_id,
            document_path=document_path,  # 생성된 문서 파일 경로 (문서 도메인에서만)
            requires_input=requires_input,  # 사용자 입력 필요 여부
            last_domain=last_domain,  # LLM 라우팅 시 결정된 도메인
            workflow_type=workflow_type,  # 워크플로우 타입
            workflow_in_progress=workflow_in_progress,  # 워크플로우 진행 중 여부
            workflow_step_index=workflow_step_index,  # 현재 워크플로우 단계 인덱스
        )

    except Exception as exc:  # pragma: no cover
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"챗봇 처리 실패: {exc}") from exc


@app.post("/api/email/reply")
async def email_reply_endpoint(
    payload: EmailReplyRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """메일 UID 기반 답장 전송 API.

    - manual: 사용자가 입력한 message를 그대로 답장 본문으로 전송
    - auto: 사용자 message(지시문)를 기반으로 AI가 답장 초안을 생성하여 전송
    """
    import re
    import html as html_module
    import email as email_pkg

    employee_id = current_user["employee_id"]
    uid = (payload.uid or "").strip()
    mode = (payload.mode or "").strip().lower()
    user_text = (payload.message or "").strip()
    if not uid:
        raise HTTPException(status_code=400, detail="uid가 필요합니다.")
    if mode not in {"auto", "manual"}:
        raise HTTPException(status_code=400, detail="mode는 auto 또는 manual 이어야 합니다.")
    if not user_text:
        raise HTTPException(status_code=400, detail="message가 비어있습니다.")

    # IMAP에서 원문을 가져온 뒤, Reply-To/From으로 답장 대상 추출
    from agents.master_graphs.email.email_search_agent import (
        _imap_connect,
        _decode_mime_header,
        _decode_header_value,
        _get_email_body_and_attachments,
    )
    from agents.master_graphs.email.email_compose_agent import (
        generate_email_draft,
        send_email_via_smtp,
        add_company_signature,
    )

    EMAIL_ADDR_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

    mail = None
    try:
        mail = _imap_connect()
        st2, msg_data = mail.uid("fetch", uid, "(RFC822)")
        if st2 != "OK" or not msg_data or not msg_data[0]:
            raise HTTPException(status_code=404, detail=f"메일을 가져오지 못했습니다. (uid={uid})")

        msg = email_pkg.message_from_bytes(msg_data[0][1])
        original_subject = _decode_mime_header(msg.get("Subject", "")) or "(제목 없음)"
        original_from = _decode_mime_header(msg.get("From", "")) or "(발신자 없음)"
        original_date = _decode_header_value(msg.get("Date", "")) or ""
        reply_to_header = _decode_mime_header(msg.get("Reply-To", "")) or ""

        # 답장 대상(to): Reply-To 우선, 없으면 From
        to_source = reply_to_header or original_from
        m = EMAIL_ADDR_RE.search(to_source)
        if not m:
            raise HTTPException(status_code=400, detail=f"답장 대상 이메일 주소를 추출할 수 없습니다: {to_source}")
        to_email = m.group(0)

        # 원문 본문
        text_body, html_body, _atts = _get_email_body_and_attachments(msg)
        original_body = html_body or text_body or ""

        # 스레딩 헤더 (가능하면 유지)
        original_message_id = (msg.get("Message-ID") or "").strip()
        original_references = (msg.get("References") or "").strip()
        extra_headers: dict[str, str] = {}
        if original_message_id:
            extra_headers["In-Reply-To"] = original_message_id
            if original_references:
                extra_headers["References"] = f"{original_references} {original_message_id}".strip()
            else:
                extra_headers["References"] = original_message_id

        # auto: LLM로 답장 생성
        if mode == "auto":
            subject, body = await generate_email_draft(
                to_email=to_email,
                user_message=user_text,
                original_subject=original_subject,
                original_sender=original_from,
                original_body=original_body,
                employee_id=employee_id,
            )
        # manual: 사용자 입력 그대로 전송(LLM 없이)
        else:
            subject = original_subject
            if not re.match(r"(?i)^\s*re\s*:", subject or ""):
                subject = f"Re: {subject}"

            safe = html_module.escape(user_text).replace("\n", "<br>")
            body = (
                "<div style=\"font-family:'Malgun Gothic','맑은 고딕',Arial,sans-serif;"
                "font-size:14px;color:#333;line-height:1.8;padding:20px;\">"
                f"{safe}"
                "</div>"
            )
            body = await add_company_signature(body, employee_id=employee_id)

        ok, err = send_email_via_smtp(
            to=to_email,
            subject=subject,
            body=body,
            is_html=True,
            extra_headers=extra_headers or None,
        )
        if not ok:
            raise HTTPException(status_code=500, detail=f"답장 전송 실패: {err or 'unknown error'}")

        return {
            "ok": True,
            "message": "✅ 답장을 전송했습니다.",
            "uid": uid,
            "to": to_email,
            "original_subject": original_subject,
            "original_from": original_from,
            "original_date": original_date,
            "mode": mode,
        }
    finally:
        try:
            if mail:
                mail.logout()
        except Exception:
            pass


@app.post("/api/email/compose/modify")
async def email_compose_modify_endpoint(
    payload: EmailComposeModifyRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """이메일 작성 초안을 사용자가 직접 수정한 내용으로 저장."""
    import html as html_module

    employee_id = current_user["employee_id"]
    session_id = payload.session_id

    session_manager = await get_session_manager()
    if not session_manager:
        raise HTTPException(status_code=500, detail="세션 매니저를 초기화할 수 없습니다.")

    session_data = await session_manager.load_session_data(session_id, employee_id)
    ctx = session_data.get("email_compose_context") or {}
    if not isinstance(ctx, dict):
        ctx = {}

    to_email = ctx.get("to_email") or ", ".join(ctx.get("to_emails", []) or [])
    if not to_email:
        raise HTTPException(status_code=400, detail="수신자(to_email)가 없습니다. 먼저 이메일 초안을 생성해주세요.")

    subject = (payload.subject or "").strip() or "(제목 없음)"
    body_text = payload.body_text or ""

    from agents.master_graphs.email.email_compose_agent import add_company_signature

    safe = html_module.escape(body_text).replace("\n", "<br>")
    body_html = (
        "<div style=\"font-family:'Malgun Gothic','맑은 고딕',Arial,sans-serif;"
        "font-size:14px;color:#333;line-height:1.8;padding:20px;\">"
        f"{safe}"
        "</div>"
    )
    body_html = await add_company_signature(body_html, employee_id=employee_id)

    ctx["email_draft_subject"] = subject
    ctx["email_draft_body"] = body_html
    ctx["to_email"] = to_email

    # 저장
    session_data["email_compose_context"] = ctx
    session_data["employee_id"] = employee_id
    await session_manager.save_session_state(session_id, session_data)

    return {"ok": True, "message": "✅ 초안을 저장했습니다.", "draft": {"to": to_email, "subject": subject, "body": body_html}}


@app.post("/api/email/compose/send")
async def email_compose_send_endpoint(
    payload: EmailComposeSendRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """작성중인 이메일 초안을 전송하고 컨텍스트를 초기화."""
    import html as html_module

    employee_id = current_user["employee_id"]
    session_id = payload.session_id

    session_manager = await get_session_manager()
    if not session_manager:
        raise HTTPException(status_code=500, detail="세션 매니저를 초기화할 수 없습니다.")

    session_data = await session_manager.load_session_data(session_id, employee_id)
    ctx = session_data.get("email_compose_context") or {}
    if not isinstance(ctx, dict):
        ctx = {}

    to_email = ctx.get("to_email") or ", ".join(ctx.get("to_emails", []) or [])
    if not to_email:
        raise HTTPException(status_code=400, detail="수신자(to_email)가 없습니다. 먼저 이메일 초안을 생성해주세요.")

    subject = (payload.subject or "").strip() or "(제목 없음)"
    body_text = payload.body_text or ""

    from agents.master_graphs.email.email_compose_agent import send_email_via_smtp, add_company_signature

    safe = html_module.escape(body_text).replace("\n", "<br>")
    body_html = (
        "<div style=\"font-family:'Malgun Gothic','맑은 고딕',Arial,sans-serif;"
        "font-size:14px;color:#333;line-height:1.8;padding:20px;\">"
        f"{safe}"
        "</div>"
    )
    body_html = await add_company_signature(body_html, employee_id=employee_id)

    ok, err = send_email_via_smtp(
        to=to_email,
        subject=subject,
        body=body_html,
        cc=ctx.get("cc"),
        bcc=ctx.get("bcc"),
        is_html=True,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=f"이메일 전송 실패: {err or 'unknown error'}")

    # 전송 성공 → 컨텍스트 초기화
    session_data["email_compose_context"] = {}
    session_data["employee_id"] = employee_id
    await session_manager.save_session_state(session_id, session_data)

    return {"ok": True, "message": "✅ 이메일을 전송했습니다.", "to": to_email}


@app.post("/api/email/compose/cancel")
async def email_compose_cancel_endpoint(
    payload: EmailComposeCancelRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """작성중인 이메일 초안 취소: email_compose_context를 비웁니다."""
    employee_id = current_user["employee_id"]
    session_id = payload.session_id

    session_manager = await get_session_manager()
    if not session_manager:
        raise HTTPException(status_code=500, detail="세션 매니저를 초기화할 수 없습니다.")

    session_data = await session_manager.load_session_data(session_id, employee_id)
    session_data["email_compose_context"] = {}
    session_data["employee_id"] = employee_id
    await session_manager.save_session_state(session_id, session_data)
    return {"ok": True, "message": "✅ 이메일 작성이 취소되었습니다."}


@app.post("/api/upload-document", response_model=DocumentUploadResponse)
async def upload_document_endpoint(
    file: UploadFile = FastAPIFile(...),
    document_title: str = Form(...),
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> DocumentUploadResponse:
    """문서 업로드 엔드포인트 (Milvus Vector DB 저장).
    
    PDF 또는 DOCX 파일을 업로드하여 Milvus Vector DB에 저장합니다.
    """
    try:
        # 파일 확장자 확인
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in [".pdf", ".docx"]:
            raise HTTPException(
                status_code=400,
                detail="PDF (.pdf) 또는 Word (.docx) 파일만 업로드 가능합니다."
            )
        
        # 파일 내용 읽기
        content = await file.read()
        
        # 임시 파일로 저장 (master_graph의 update_document는 파일 경로를 받음)
        import tempfile
        import os
        temp_file = None
        try:
            # 임시 파일 생성
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
                tmp.write(content)
                temp_file = tmp.name
            
            # 세션 관리자로 이전 세션 데이터 로드
            employee_id = current_user["employee_id"]
            session_manager = await get_session_manager()
            # 문서 업데이트용 새 세션 생성
            if session_manager:
                session_id = await session_manager.create_session(employee_id=employee_id)
            else:
                import uuid
                session_id = f"session_{uuid.uuid4().hex[:8]}"
            session_data = {
                "conversation_history": [],
                "email_search_context": {},
                "qa_search_context": {},
                "tech_search_context": {},
                "schedule_context": {},
                "document_context": {}
            }
            if session_manager:
                session_data = await session_manager.load_session_data(session_id, employee_id)
            
            # master_graph의 update_document를 직접 호출하여 conversation_history를 포함한 결과 받기
            from agents.master_graphs.document.master_graph import DocumentMasterGraph
            from agents.state import OrchestratorState
            
            # input_state 구성
            input_state: OrchestratorState = {
                "query": f"문서 '{document_title}' 업데이트 요청",
                "user_id": employee_id,
                "session_id": session_id,
                "employee_id": employee_id,
                "conversation_history": session_data.get("conversation_history", []),
                "email_search_context": session_data.get("email_search_context", {}),
                "qa_search_context": session_data.get("qa_search_context", {}),
                "tech_search_context": session_data.get("tech_search_context", {}),
                "schedule_context": session_data.get("schedule_context", {}),
                "document_context": session_data.get("document_context", {}),
                "document_path": temp_file,  # 임시 파일 경로 전달
                "document_title": document_title,  # 문서 제목 전달
            }
            
            # DocumentMasterGraph 인스턴스 생성 및 update_document 호출
            document_master = DocumentMasterGraph()
            result_state = await document_master.update_document(input_state)
            
            # 결과 추출
            result = {
                "ok": result_state.get("final_response") and "실패" not in result_state.get("final_response", ""),
                "message": result_state.get("final_response", "문서 업데이트 완료"),
                "chunks_count": result_state.get("document_context", {}).get("last_update", {}).get("chunks_count", 0)
            }
            
            # Redis에 세션 데이터 저장 (다른 agent들과 동일한 방식)
            if session_manager:
                # 세션 state 전체 저장 (conversation_history + 모든 컨텍스트)
                session_state = {
                    "conversation_history": result_state.get("conversation_history", session_data.get("conversation_history", [])),
                    "email_search_context": result_state.get("email_search_context", session_data.get("email_search_context", {})),
                    "qa_search_context": result_state.get("qa_search_context", session_data.get("qa_search_context", {})),
                    "tech_search_context": result_state.get("tech_search_context", session_data.get("tech_search_context", {})),
                    "schedule_context": result_state.get("schedule_context", session_data.get("schedule_context", {})),
                    "document_context": result_state.get("document_context", session_data.get("document_context", {})),
                    "recruitment_context": result_state.get("recruitment_context", session_data.get("recruitment_context", {}))
                }
                await session_manager.save_session_state(session_id, session_state)
                print(f"✅ [Document Update] Redis에 문서 업데이트 정보 및 대화 내역 저장 완료 (session_id: {session_id})")
            else:
                print(f"⚠️ [Document Update] 세션 관리자가 없어 Redis 저장을 건너뜁니다.")
            
        finally:
            # 임시 파일 삭제
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except Exception as e:
                    print(f"⚠️ 임시 파일 삭제 실패: {e}")
        
        if result.get("ok"):
            
            return DocumentUploadResponse(
                ok=True,
                message=result.get("message", "문서가 성공적으로 업로드되었습니다."),
                document_title=document_title,
                last_domain="document"  # 문서 업로드는 항상 document 도메인
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "문서 업로드에 실패했습니다.")
            )
                
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"문서 업로드 실패: {str(exc)}"
        ) from exc


@app.get("/auth/google")
async def google_oauth_start(user_id: str = Query(..., description="Google 캘린더와 연동할 사용자 ID")) -> RedirectResponse:
    """Google OAuth 인증 페이지로 리다이렉트."""
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id가 필요합니다.")

    _cleanup_expired_oauth_states()

    # user_id를 employee_id로 변환
    try:
        employee_id = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"user_id는 employee_id(숫자)여야 합니다: {user_id}")

    try:
        # 해당 employee_id의 client_id/client_secret을 사용하도록 전달
        flow = await _create_google_oauth_flow(employee_id=employee_id)
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )

        _oauth_state_store[state] = (user_id, time.time())
        return RedirectResponse(authorization_url)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google OAuth 시작 중 오류가 발생했습니다: {exc}") from exc


@app.get("/auth/google/callback")
async def google_oauth_callback(code: str = Query(...), state: str = Query(...)) -> Response:
    """Google OAuth 인증 후 토큰을 DB + Redis에 저장.
    
    플로우:
    1. OAuth 코드로 토큰 교환
    2. Google 계정 정보 추출 (email, sub)
    3. refresh_token을 DB에 저장 (암호화)
    4. access_token을 Redis에 캐시 (1시간)
    """
    _cleanup_expired_oauth_states()

    state_entry = _oauth_state_store.pop(state, None)
    if not state_entry:
        raise HTTPException(status_code=400, detail="유효하지 않은 OAuth state입니다. 다시 시도해주세요.")

    user_id, _ = state_entry
    
    # user_id를 employee_id로 변환
    try:
        employee_id = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"user_id는 employee_id(숫자)여야 합니다: {user_id}")

    try:
        # 해당 employee_id의 client_id/client_secret을 사용하도록 전달
        flow = await _create_google_oauth_flow(employee_id=employee_id)
        flow.fetch_token(code=code)
        credentials = flow.credentials
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google OAuth 토큰 교환 실패: {exc}") from exc

    # refresh_token 확인
    if not credentials.refresh_token:
        raise HTTPException(
            status_code=500, 
            detail="refresh_token을 받지 못했습니다. OAuth 플로우에서 access_type=offline, prompt=consent가 설정되어야 합니다."
        )
    
    # Google 계정 정보 추출 (email, sub)
    google_email = None
    google_sub = None
    
    try:
        # UserInfo API로 사용자 정보 조회
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {credentials.token}"}
            )
            if response.status_code == 200:
                user_info = response.json()
                google_email = user_info.get("email")
                google_sub = user_info.get("id")
            else:
                print(f"⚠️ UserInfo API 호출 실패: {response.status_code}")
    except Exception as e:
        print(f"⚠️ Google 계정 정보 조회 실패: {e}")
    
    if not google_email:
        raise HTTPException(status_code=500, detail="Google 계정 정보를 가져올 수 없습니다.")

    # ✅ 1. DB에 refresh_token 저장 (암호화)
    from agents.master_graphs.schedule.services.google_token_service import get_token_service
    token_service = get_token_service()
    
    db_success = await token_service.save_google_credentials(
        employee_id=employee_id,
        google_email=google_email,
        refresh_token=credentials.refresh_token,
        scope=" ".join(credentials.scopes) if credentials.scopes else "",
        google_sub=google_sub,
    )
    
    if not db_success:
        raise HTTPException(status_code=500, detail="토큰 DB 저장에 실패했습니다.")
    
    # ✅ 2. Redis에 access_token 캐시 (1시간) - refresh_token은 DB에만 저장
    session_manager = await get_session_manager()
    if session_manager:
        await session_manager.save_google_tokens(
            user_id,
            {
                "access_token": credentials.token,
                # refresh_token은 DB에만 저장 (보안)
                "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
            },
            ttl=3600,  # 1시간
        )
        print(f"✅ Redis에 access_token 캐시 저장 완료 (user_id={user_id}, ttl=3600초)")

    success_html = """
    <html>
        <head>
            <title>Google 인증 완료</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    background-color: #f5f5f5;
                }
                .container {
                    background: white;
                    padding: 40px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    text-align: center;
                }
                h2 { color: #4CAF50; }
                p { color: #666; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>✅ Google Calendar 연동이 완료되었습니다!</h2>
                <p>DB에 안전하게 저장되었으며, 서버 재시작 후에도 유지됩니다.</p>
                <p>이 창을 닫고 다시 일정 관련 요청을 진행해주세요.</p>
                <script>
                    setTimeout(() => {
                        window.close();
                    }, 3000);
                </script>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(content=success_html)

@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> SessionListResponse:
    """사원별 세션 목록 조회 (최신순).
    
    Args:
        current_user: 인증된 사용자 정보
        
    Returns:
        세션 목록 (최신순 정렬)
    """
    try:
        employee_id = current_user["employee_id"]
        session_manager = await get_session_manager()
        if not session_manager:
            return SessionListResponse(
                ok=False,
                sessions=[],
                message="세션 관리자를 사용할 수 없습니다."
            )
        
        sessions_metadata = await session_manager.list_employee_sessions(employee_id)
        
        sessions = [
            SessionMetadata(
                session_id=meta["session_id"],
                title=meta["title"],
                employee_id=meta["employee_id"],
                message_count=meta["message_count"],
                created_at=meta["created_at"],
                updated_at=meta["updated_at"],
                last_domain=meta.get("last_domain"),
                ttl=meta.get("ttl")
            )
            for meta in sessions_metadata
        ]
        
        return SessionListResponse(
            ok=True,
            sessions=sessions,
            message=f"세션 {len(sessions)}개 조회 완료"
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return SessionListResponse(
            ok=False,
            sessions=[],
            message=f"세션 목록 조회 실패: {str(exc)}"
        )


@app.post("/api/sessions", response_model=SessionResponse)
async def create_session(
    payload: SessionCreateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> SessionResponse:
    """새 세션 생성.
    
    Args:
        payload: 세션 생성 요청 (title)
        current_user: 인증된 사용자 정보
        
    Returns:
        생성된 세션 정보
    """
    try:
        employee_id = current_user["employee_id"]
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")
        
        session_id = await session_manager.create_session(
            employee_id=employee_id,
            title=payload.title
        )
        
        return SessionResponse(
            ok=True,
            session_id=session_id,
            message_count=0,
            message="새 세션이 생성되었습니다."
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"세션 생성 실패: {str(exc)}") from exc


@app.delete("/api/sessions/{session_id}", response_model=SessionResponse)
async def delete_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> SessionResponse:
    """세션 삭제.
    
    Args:
        session_id: 삭제할 세션 ID
        current_user: 인증된 사용자 정보
        
    Returns:
        삭제 결과
    """
    employee_id = current_user["employee_id"]
    try:
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")
        
        success = await session_manager.delete_session(session_id, employee_id)
        
        if success:
            return SessionResponse(
                ok=True,
                session_id=session_id,
                message_count=0,
                message="세션이 삭제되었습니다."
            )
        else:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"세션 삭제 실패: {str(exc)}") from exc


@app.patch("/api/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str, 
    payload: SessionUpdateRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> SessionResponse:
    """세션 메타데이터 업데이트 (제목 변경 등).
    
    Args:
        session_id: 업데이트할 세션 ID
        payload: 업데이트 요청 (title 등)
        current_user: 인증된 사용자 정보
        
    Returns:
        업데이트된 세션 정보
    """
    try:
        employee_id = current_user["employee_id"]
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")
        
        success = await session_manager.update_session_metadata(
            session_id=session_id,
            title=payload.title
        )
        
        if success:
            # 업데이트된 세션 정보 조회
            session_data = await session_manager.load_session_data(session_id, employee_id)
            message_count = len(session_data.get("conversation_history", []))
            
            return SessionResponse(
                ok=True,
                session_id=session_id,
                message_count=message_count,
                message="세션이 업데이트되었습니다."
            )
        else:
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"세션 업데이트 실패: {str(exc)}") from exc


@app.post("/api/sessions/{session_id}/messages")
async def add_messages_to_session(
    session_id: str,
    payload: 'AddMessagesRequest',
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    """프론트엔드 UI 상호작용 메시지를 conversation_history에 추가.

    AI 그래프 실행 없이 메시지만 저장합니다.
    카테고리 선택, FAQ 클릭 등 프론트엔드에서 처리되는 상호작용에 사용.

    Args:
        session_id: 세션 ID
        payload: 추가할 메시지 리스트
        current_user: 인증된 사용자

    Returns:
        저장 결과
    """
    try:
        employee_id = current_user["employee_id"]
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")

        # 기존 세션 데이터 로드 (없으면 새로 생성)
        try:
            session_data = await session_manager.load_session_data(session_id, employee_id)
        except Exception:
            # 세션이 없으면 새로 생성 (session_id를 지정하여 생성)
            await session_manager.create_session(
                employee_id=employee_id,
                session_id=session_id,
                title="새 대화"
            )
            session_data = await session_manager.load_session_data(session_id, employee_id)

        conversation_history = session_data.get("conversation_history", [])

        # 새 메시지 추가
        for msg in payload.messages:
            conversation_history.append({
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp or datetime.now().isoformat()
            })

        # conversation_history만 업데이트하여 저장
        session_data["conversation_history"] = conversation_history

        # ✅ employee_id 추가 (save_session_state에서 필수로 요구)
        if "employee_id" not in session_data:
            session_data["employee_id"] = employee_id

        await session_manager.save_session_state(session_id, session_data)

        return {
            "ok": True,
            "message": f"{len(payload.messages)}개 메시지가 저장되었습니다.",
            "message_count": len(conversation_history)
        }
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"메시지 저장 실패: {str(exc)}") from exc


@app.post("/api/session/new", response_model=SessionResponse)
async def create_new_session(employee_id: int = 4003) -> SessionResponse:
    """새 세션 생성 (하위 호환성 유지용 - /api/sessions 사용 권장).
    
    Args:
        employee_id: 사원 번호
        
    Returns:
        새로 생성된 세션 ID
    """
    try:
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")
        
        session_id = await session_manager.create_session(employee_id=employee_id)
        
        return SessionResponse(
            ok=True,
            session_id=session_id,
            message_count=0,
            message="새 세션이 생성되었습니다."
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"세션 생성 실패: {str(exc)}") from exc


@app.post("/api/session/change", response_model=SessionResponse)
async def change_session(
    payload: SessionRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> SessionResponse:
    """세션 변경 또는 생성.
    
    Args:
        payload: 세션 변경 요청 (session_id가 None이면 새 세션 생성)
        
    Returns:
        세션 정보
    """
    try:
        employee_id = current_user["employee_id"]
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")
        
        if payload.session_id:
            # 기존 세션으로 변경
            session_id = payload.session_id
            session_data = await session_manager.load_session_data(session_id, employee_id)
            message_count = len(session_data.get("conversation_history", []))
            
            return SessionResponse(
                ok=True,
                session_id=session_id,
                message_count=message_count,
                message=f"세션이 변경되었습니다. (대화 {message_count}개)"
            )
        else:
            # 새 세션 생성 (인증된 사용자의 employee_id 사용)
            session_id = await session_manager.create_session(employee_id=employee_id)
            
            return SessionResponse(
                ok=True,
                session_id=session_id,
                message_count=0,
                message="새 세션이 생성되었습니다."
            )
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"세션 변경 실패: {str(exc)}") from exc


@app.get("/api/session/current/{session_id}", response_model=SessionResponse)
async def get_session_info(
    session_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> SessionResponse:
    """현재 세션 정보 조회.
    
    Args:
        session_id: 세션 ID
        current_user: 인증된 사용자 정보
        
    Returns:
        세션 정보 (대화 히스토리 개수, last_domain 포함)
    """
    employee_id = current_user["employee_id"]
    session_manager = await get_session_manager()
    message_count = 0
    conversation_history = []
    last_domain = None
    
    if session_manager:
        session_data = await session_manager.load_session_data(session_id, employee_id)
        conversation_history = session_data.get("conversation_history", [])
        message_count = len(conversation_history)
        last_domain = session_data.get("last_domain")  # last_domain 추출
    
    return SessionResponse(
        ok=True,
        session_id=session_id,
        message_count=message_count,
        message=f"현재 세션: {session_id} (대화 {message_count}개)",
        conversation_history=conversation_history,
        last_domain=last_domain  # last_domain 반환
    )


@app.get("/healthz")
async def health_check():
    """헬스 체크 엔드포인트."""
    return {"status": "ok", "service": "깐부 AI Agent 시스템"}


# ===== 회의실 및 일정 API 엔드포인트 =====

@app.get("/api/my-reservations")
async def get_my_reservations(employee_id: int = Query(..., description="사원 번호")):
    """내 예약 목록 조회 (AI 없이 직접 DB 조회).
    
    Args:
        employee_id: 사원 번호
        
    Returns:
        예약 목록 (회의실 정보 포함)
    """
    try:
        from agents.master_graphs.schedule.services.database_service import DatabaseService
        
        db_service = DatabaseService()
        reservations = await db_service.get_room_reservations_by_user(
            employee_id=employee_id,
            include_past=False,
            limit=50
        )
        
        # 회의실 정보도 함께 조회 (효율적으로 한 번만 조회)
        room_map = {}
        if reservations:
            rooms = await db_service.get_meeting_rooms()
            room_map = {room.get("id"): room.get("name", f"회의실 {room.get('id')}") for room in rooms}
        
        return {
            "ok": True,
            "reservations": [
                {
                    "id": res.get("id"),
                    "reservation_id": res.get("id"),
                    "event_id": res.get("gcal_event_id"),
                    "meeting_room_id": res.get("meeting_room_id"),
                    "meeting_room_name": room_map.get(res.get("meeting_room_id"), f"회의실 {res.get('meeting_room_id')}"),
                    "title": res.get("title", "회의"),
                    "description": res.get("description"),
                    "start_time": res.get("start_time").isoformat() if res.get("start_time") else None,
                    "end_time": res.get("end_time").isoformat() if res.get("end_time") else None,
                    "participants": res.get("participants"),
                    "status": res.get("status"),
                }
                for res in reservations
            ],
            "count": len(reservations)
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "reservations": [],
            "count": 0,
            "message": f"예약 목록 조회 실패: {str(e)}"
        }


@app.delete("/api/reservations/{reservation_id}")
async def delete_reservation(reservation_id: int):
    """예약 취소 (AI 없이 직접 DB 삭제).
    
    Args:
        reservation_id: 예약 ID
        
    Returns:
        삭제 결과
    """
    try:
        from agents.master_graphs.schedule.services.database_service import DatabaseService
        
        db_service = DatabaseService()
        success = await db_service.delete_room_reservation(reservation_id)
        
        if success:
            return {
                "ok": True,
                "message": "예약이 취소되었습니다."
            }
        else:
            return {
                "ok": False,
                "message": "예약을 찾을 수 없습니다."
            }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "message": f"예약 취소 실패: {str(e)}"
        }


@app.get("/api/my-events")
async def get_my_events(
    employee_id: int = Query(..., description="사원 번호"),
    start_date: str = Query(..., description="시작일 (ISO 8601, 예: 2024-12-01T00:00:00Z)"),
    end_date: str = Query(..., description="종료일 (ISO 8601)")
):
    """내 일정 조회 (Google Calendar + DB 메타데이터 병합).
    
    Args:
        employee_id: 사원 번호
        start_date: 시작일 (ISO 8601)
        end_date: 종료일 (ISO 8601)
        
    Returns:
        병합된 일정 목록
    """
    try:
        from agents.master_graphs.schedule.services.hybrid_scheduler import HybridScheduler
        
        scheduler = HybridScheduler()
        user_id = str(employee_id)
        
        # HybridScheduler.get_events 사용 (Google Calendar + DB 병합)
        events = await scheduler.get_events(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date
        )
        
        return {
            "ok": True,
            "events": events,
            "count": len(events)
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "events": [],
            "count": 0,
            "message": f"일정 조회 실패: {str(e)}"
        }


# ===== 테스트용 엔드포인트 (연차/반차 & 일정 관리) =====

class LeaveTestRequest(BaseModel):
    """연차 테스트 요청 모델."""
    query: str = Field(..., description="연차 관련 질의")
    # employee_id는 인증된 사용자에서 자동으로 가져옴
    session_id: Optional[str] = Field(default=None, description="세션 ID (멀티턴 대화)")


class LeaveTestResponse(BaseModel):
    """연차 테스트 응답 모델."""
    ok: bool = Field(..., description="성공 여부")
    message: str = Field(..., description="응답 메시지")
    slot_state: Optional[dict] = Field(default=None, description="슬롯 상태")
    awaiting_slots: Optional[list] = Field(default=None, description="대기 중인 슬롯")
    action: Optional[str] = Field(default=None, description="실행된 액션")


@app.post("/api/test/leave", response_model=LeaveTestResponse)
async def test_leave_endpoint(
    payload: LeaveTestRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> LeaveTestResponse:
    """연차/반차 기능 테스트 엔드포인트.
    
    테스트 시나리오:
    1. 연차 조회: {"query": "내 연차 잔여일수 알려줘"}
    2. 연차 신청: {"query": "내일 연차 신청"}
    3. 반차 신청: {"query": "내일 오후 반차"}
    4. 연차 취소 (멀티턴):
       - 1단계: {"query": "연차 취소하고 싶어"}
       - 2단계: {"query": "1번", "session_id": "<1단계에서 받은 session_id>"}
    5. 연차 수정 (멀티턴):
       - 1단계: {"query": "연차 수정하고 싶어"}
       - 2단계: {"query": "1번", "session_id": "<1단계에서 받은 session_id>"}
       - 3단계: {"query": "12월 20일로 변경", "session_id": "<동일 session_id>"}
    """
    try:
        from agents.master_graphs.schedule.nodes.leave_nodes import process_leave_request
        from agents.state import OrchestratorState
        
        # 인증된 사용자의 employee_id 사용
        employee_id = current_user["employee_id"]
        
        # 세션 ID 설정
        session_id = payload.session_id or f"test_leave_{employee_id}"
        
        # 세션 관리자로 이전 컨텍스트 로드
        session_manager = await get_session_manager()
        session_data = {"schedule_context": {}}
        if session_manager:
            loaded_data = await session_manager.load_session_data(session_id, employee_id)
            session_data = {"schedule_context": loaded_data.get("schedule_context", {})}
        
        # State 구성
        state = OrchestratorState(
            query=payload.query,
            employee_id=employee_id,
            user_id=str(employee_id),
            messages=[],
            schedule_context=session_data.get("schedule_context", {}),
        )
        
        # Leave 노드 실행
        result = await process_leave_request(state)
        
        # 세션 저장 (멀티턴 대화 지원)
        if session_manager:
            session_state = {
                "schedule_context": result.get("schedule_context", {})
            }
            await session_manager.save_session_state(session_id, session_state)
        
        # 응답 구성
        leave_context = result.get("schedule_context", {}).get("leave", {})
        
        return LeaveTestResponse(
            ok=True,
            message=result.get("final_response", "처리 완료"),
            slot_state=leave_context.get("slot_state"),
            awaiting_slots=leave_context.get("awaiting_slots"),
            action=leave_context.get("slot_state", {}).get("intent")
        )
        
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"연차 테스트 실패: {exc}") from exc


class ScheduleTestRequest(BaseModel):
    """일정 테스트 요청 모델."""
    query: str = Field(..., description="일정 관련 질의")
    # employee_id는 인증된 사용자에서 자동으로 가져옴
    user_id: Optional[str] = Field(default=None, description="사용자 ID (Google Calendar)")
    session_id: Optional[str] = Field(default=None, description="세션 ID (멀티턴 대화)")


class ScheduleTestResponse(BaseModel):
    """일정 테스트 응답 모델."""
    ok: bool = Field(..., description="성공 여부")
    message: str = Field(..., description="응답 메시지")
    slot_state: Optional[dict] = Field(default=None, description="슬롯 상태")
    awaiting_slots: Optional[list] = Field(default=None, description="대기 중인 슬롯")
    action: Optional[str] = Field(default=None, description="실행된 액션")
    auth_required: Optional[bool] = Field(default=False, description="Google 인증 필요 여부")


@app.post("/api/test/schedule", response_model=ScheduleTestResponse)
async def test_schedule_endpoint(
    payload: ScheduleTestRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> ScheduleTestResponse:
    """일정 관리 기능 테스트 엔드포인트 (Google Calendar 연동).
    
    테스트 시나리오:
    1. 일정 조회: {"query": "오늘 일정 알려줘"}
    2. 일정 생성: {"query": "내일 오후 2시 팀 미팅"}
    3. 일정 삭제 (멀티턴):
       - 1단계: {"query": "일정 삭제"}
       - 2단계: {"query": "1번", "session_id": "<1단계에서 받은 session_id>"}
    4. 일정 수정 (멀티턴):
       - 1단계: {"query": "일정 수정"}
       - 2단계: {"query": "1번", "session_id": "<동일 session_id>"}
       - 3단계: {"query": "내일 3시로 변경", "session_id": "<동일 session_id>"}
       
    Note: Google Calendar 연동이 필요한 경우 auth_required=true 반환
    """
    try:
        from agents.master_graphs.schedule.nodes.schedule_nodes import process_schedule_request
        from agents.state import OrchestratorState
        
        # 인증된 사용자의 employee_id 사용
        employee_id = current_user["employee_id"]
        
        # 세션 ID 설정
        user_id = payload.user_id or str(employee_id)
        session_id = payload.session_id or f"test_schedule_{user_id}"
        
        # 세션 관리자로 이전 컨텍스트 로드
        session_manager = await get_session_manager()
        session_data = {"schedule_context": {}}
        if session_manager:
            loaded_data = await session_manager.load_session_data(session_id, employee_id)
            session_data = {"schedule_context": loaded_data.get("schedule_context", {})}
        
        # State 구성
        state = OrchestratorState(
            query=payload.query,
            employee_id=employee_id,
            user_id=user_id,
            messages=[],
            schedule_context=session_data.get("schedule_context", {}),
        )
        
        # Schedule 노드 실행
        result = await process_schedule_request(state)
        
        # 세션 저장 (멀티턴 대화 지원)
        if session_manager:
            session_state = {
                "schedule_context": result.get("schedule_context", {})
            }
            await session_manager.save_session_state(session_id, session_state)
        
        # 응답 구성
        schedule_context = result.get("schedule_context", {}).get("schedule", {})
        message = result.get("final_response", "처리 완료")
        
        # Google 인증 필요 여부 확인
        auth_required = "Google 캘린더 연동이 필요합니다" in message or "인증이 필요합니다" in message
        
        return ScheduleTestResponse(
            ok=True,
            message=message,
            slot_state=schedule_context.get("slot_state"),
            awaiting_slots=schedule_context.get("awaiting_slots"),
            action=schedule_context.get("slot_state", {}).get("intent"),
            auth_required=auth_required
        )
        
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"일정 테스트 실패: {exc}") from exc


# ===== 회의실 조회 REST API 엔드포인트 =====

@app.get("/api/meeting-rooms")
async def get_meeting_rooms(
    floor: Optional[int] = Query(None, description="층수 (선택사항)"),
    capacity: Optional[int] = Query(None, description="최소 수용 인원 (선택사항)"),
    has_video: Optional[bool] = Query(None, description="화상회의 장비 필요 여부 (선택사항)"),
    has_projector: Optional[bool] = Query(None, description="프로젝터 필요 여부 (선택사항)"),
    has_whiteboard: Optional[bool] = Query(None, description="화이트보드 필요 여부 (선택사항)"),
    start_date: Optional[str] = Query(None, description="시작 날짜 (YYYY-MM-DD)"),
    start_time: Optional[str] = Query(None, description="시작 시간 (HH:MM)"),
    end_date: Optional[str] = Query(None, description="종료 날짜 (YYYY-MM-DD)"),
    end_time: Optional[str] = Query(None, description="종료 시간 (HH:MM)"),
) -> Dict[str, Any]:
    """회의실 목록 조회 (조건별 필터링 가능, AI 없이 직접 DB 조회).
    
    Args:
        floor: 층수
        capacity: 최소 수용 인원
        has_video: 화상회의 장비 필요 여부
        has_projector: 프로젝터 필요 여부
        has_whiteboard: 화이트보드 필요 여부
        start_date: 시작 날짜
        start_time: 시작 시간
        end_date: 종료 날짜
        end_time: 종료 시간
        
    Returns:
        회의실 목록
    """
    try:
        from agents.master_graphs.schedule.services.database_service import DatabaseService
        
        db_service = DatabaseService()
        rooms = await db_service.get_meeting_rooms(
            capacity=capacity,
            floor=floor,
            has_video_conf=has_video,
            has_projector=has_projector,
            has_whiteboard=has_whiteboard,
        )
        
        # 날짜/시간 기반 예약 가능 여부 필터링 (선택사항)
        available_rooms = rooms
        if start_date and start_time and end_date and end_time:
            from datetime import datetime
            try:
                start_datetime = datetime.fromisoformat(f"{start_date}T{start_time}")
                end_datetime = datetime.fromisoformat(f"{end_date}T{end_time}")
                
                # 각 회의실의 예약 확인
                filtered_rooms = []
                for room in rooms:
                    room_id = room.get("id")
                    if room_id:
                        # 해당 시간대에 예약이 있는지 확인 (겹치는 예약 찾기)
                        conflicts = await db_service.find_room_conflicts(
                            meeting_room_id=room_id,
                            start_time=start_datetime,
                            end_time=end_datetime
                        )
                        # 예약이 없으면 사용 가능
                        if not conflicts or len(conflicts) == 0:
                            filtered_rooms.append(room)
                    else:
                        filtered_rooms.append(room)
                
                available_rooms = filtered_rooms
            except Exception as e:
                print(f"날짜/시간 필터링 오류: {e}")
                # 필터링 실패 시 모든 회의실 반환
        
        return {
            "ok": True,
            "rooms": [
                {
                    "id": room.get("id"),
                    "room_code": room.get("room_code"),
                    "name": room.get("name"),
                    "building": room.get("building"),
                    "floor": room.get("floor"),
                    "capacity": room.get("capacity"),
                    "has_video": bool(room.get("has_video", 0)),
                    "has_projector": bool(room.get("has_projector", 0)) if "has_projector" in room else None,
                    "has_whiteboard": bool(room.get("has_whiteboard", 0)) if "has_whiteboard" in room else None,
                }
                for room in available_rooms
            ],
            "count": len(available_rooms)
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "rooms": [],
            "count": 0,
            "message": f"회의실 목록 조회 실패: {str(e)}"
        }


@app.get("/api/employees", response_model=List[Dict[str, Any]])
async def get_employees(
    dept: Optional[str] = Query(None, description="부서 필터"),
    position: Optional[str] = Query(None, description="직급 필터"),
    status: Optional[str] = Query(None, description="직원 상태 (Active, 재직 중, etc.)"),
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> List[Dict[str, Any]]:
    """사원 목록 조회 (부서, 직급 필터링 지원).

    면접관 선택 등에 사용됩니다.

    Args:
        dept: 부서 필터 (예: "개발팀")
        position: 직급 필터 (예: "과장")
        status: 직원 상태 (기본값: "Active")

    Returns:
        사원 목록 [{"employee_id": int, "name": str, "dept": str, ...}, ...]
    """
    try:
        from agents.master_graphs.schedule.services.database_service import DatabaseService
        from sqlalchemy import text

        db = DatabaseService()

        clauses = []
        params: Dict[str, Any] = {}

        if dept:
            clauses.append("dept = :dept")
            params["dept"] = dept

        if position:
            clauses.append("(position = :position OR job_title = :position)")
            params["position"] = position

        # ✅ status 필터 수정: 'Active' 또는 '재직 중' 둘 다 매칭
        if status and status.strip():
            if status.lower() == 'active':
                # 'Active' 또는 '재직 중' 둘 다 포함
                clauses.append("(employment_status = 'Active' OR employment_status = '재직 중')")
            else:
                clauses.append("employment_status = :status")
                params["status"] = status
        # status가 None이면 필터 적용 안 함 (전체 조회)

        where_sql = " AND ".join(clauses) if clauses else "1=1"

        sql = f"""
        SELECT
            employee_id,
            name,
            dept,
            position,
            job_title,
            email,
            employment_status
        FROM employee
        WHERE {where_sql}
        ORDER BY dept, position, name
        """

        async with db._session() as session:
            result = await session.execute(text(sql), params)
            employees = [dict(row) for row in result.mappings().all()]

        # ✅ 로그 추가
        print(f"✅ [API] /api/employees 조회 결과: {len(employees)}명")
        if employees:
            print(f"✅ [API] 첫 번째 사원 예시: {employees[0]}")
        else:
            print(f"⚠️ [API] 사원 데이터가 없습니다. SQL: {sql}, params: {params}")

        return employees
    except Exception as e:
        import traceback
        traceback.print_exc()
        return []