"""FastAPI 애플리케이션 - AI Agent 시스템 UI 및 API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, List

from fastapi import FastAPI, HTTPException, UploadFile, File as FastAPIFile, Form, Query, Response
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, RedirectResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
import json
from pydantic import BaseModel, Field

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


def _create_google_oauth_flow() -> Flow:
    """Google OAuth Flow 인스턴스를 생성."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Google OAuth 클라이언트 정보가 설정되지 않았습니다. GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET를 확인하세요.",
        )

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
        scopes=["https://www.googleapis.com/auth/calendar"],
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
    employee_id: Optional[int] = Field(default=4000, description="사원 번호")


class ChatResponse(BaseModel):
    """통합 챗봇 응답 모델."""

    ok: bool = Field(..., description="요청 처리 성공 여부")
    message: str = Field(..., description="에이전트 응답 메시지")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="조회된 데이터 (있는 경우)")
    error: Optional[str] = Field(default=None, description="오류 메시지")
    session_id: Optional[str] = Field(default=None, description="세션 ID")
    document_path: Optional[str] = Field(default=None, description="생성된 문서 파일 경로 (있는 경우)")
    requires_input: Optional[bool] = Field(default=False, description="사용자 입력 필요 여부 (예시문 표시 시)")
    last_domain: Optional[str] = Field(default=None, description="LLM 라우팅 시 결정된 도메인 (schedule, document, qa, email, tech)")


class SessionRequest(BaseModel):
    """세션 변경 요청 모델."""
    session_id: Optional[str] = Field(default=None, description="세션 ID (None이면 새 세션 생성)")
    employee_id: Optional[int] = Field(default=4000, description="사원 번호")


class SessionResponse(BaseModel):
    """세션 정보 응답 모델."""
    ok: bool = Field(..., description="요청 처리 성공 여부")
    session_id: str = Field(..., description="세션 ID")
    message_count: int = Field(default=0, description="대화 히스토리 메시지 개수")
    message: Optional[str] = Field(default=None, description="응답 메시지")
    conversation_history: Optional[List[Dict[str, Any]]] = Field(default=None, description="대화 히스토리 (메시지 리스트)")


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
    employee_id: int = Field(default=4000, description="사원 번호")
    title: Optional[str] = Field(default=None, description="세션 제목")


class SessionUpdateRequest(BaseModel):
    """세션 업데이트 요청 모델."""
    title: Optional[str] = Field(default=None, description="새 세션 제목")


class DocumentUploadResponse(BaseModel):
    """문서 업로드 응답 모델."""

    ok: bool = Field(..., description="업로드 성공 여부")
    message: str = Field(..., description="응답 메시지")
    document_title: Optional[str] = Field(default=None, description="업로드된 문서 제목")
    error: Optional[str] = Field(default=None, description="오류 메시지")
    last_domain: Optional[str] = Field(default="document", description="LLM 라우팅 시 결정된 도메인 (문서 업로드는 항상 'document')")


# ===== 엔드포인트 =====

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """메인 UI 페이지 (index.html)."""
    html_file = Path(__file__).parent / "templates" / "index.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="index.html을 찾을 수 없습니다.")
    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


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


@app.get("/api/notifications/stream")
async def notifications_stream(session_id: str):
    """세션별 알림 스트림(SSE). 새 메일 수신 등 백그라운드 이벤트를 프론트로 푸시합니다."""

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


@app.post("/api/chat/stream")
async def chat_stream_endpoint(payload: ChatRequest):
    """스트리밍 챗봇 엔드포인트 - 실시간 작업 상태 표시."""
    async def generate():
        try:
            # 세션 ID 설정 (employee_id 기반으로 고정 세션 사용)
            # session_id가 없으면 새 세션 생성 (첫 메시지를 타이틀로 사용)
            if not payload.session_id:
                session_manager = await get_session_manager()
                if session_manager:
                    # 첫 메시지를 타이틀로 사용하여 새 세션 생성
                    title = payload.query[:30] if payload.query else "새 대화"  # 첫 30자만 타이틀로 사용
                    session_id = await session_manager.create_session(
                        employee_id=payload.employee_id,
                        title=title
                    )
                else:
                    import uuid
                    session_id = f"session_{uuid.uuid4().hex[:8]}"
            else:
                session_id = payload.session_id

            # ✅ 첫 채팅 요청에서 현재 세션을 알 수 있으므로, 해당 세션을 수신감지 대상에 등록 + supervisor(프로세스당 1개) 시작
            try:
                from agents.master_graphs.email.email_search_agent import (
                    register_email_check_session,
                    start_email_checker_supervisor,
                )
                register_email_check_session(session_id)
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
                session_data = await session_manager.load_session_data(session_id)
            
            # 그래프 가져오기 (체크포인터 없이 실행)
            graph = get_cached_orchestrator_graph()
            
            # 그래프가 None이면 초기화 (startup 실패 시 fallback)
            if graph is None:
                _orchestrator_graph = get_orchestrator_graph(checkpointer=None)
                graph = _orchestrator_graph
            
            input_state = {
                "query": payload.query,
                "user_id": payload.employee_id,
                "session_id": session_id,
                "employee_id": payload.employee_id,
                "conversation_history": session_data.get("conversation_history", []),
                "email_search_context": session_data.get("email_search_context", {}),
                # Redis/세션에서 null(None)로 들어오는 경우 방어
                "email_compose_context": session_data.get("email_compose_context") or {},
                "qa_search_context": session_data.get("qa_search_context", {}),
                "tech_search_context": session_data.get("tech_search_context", {}),
                "schedule_context": session_data.get("schedule_context", {}),
                "document_context": session_data.get("document_context", {}),
                "recruitment_context": session_data.get("recruitment_context", {})
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
                document_path = None
                if current_domain == "document":
                    document_path = final_state.get("document_path")
                
                requires_input = final_state.get("requires_input", False)
                
                # 도메인 정보 추출 (LLM 라우팅 시 결정된 도메인)
                # domain이 없으면 last_domain을 fallback으로 사용
                last_domain = final_state.get("domain") or final_state.get("last_domain")
                
                # Recruitment 도메인인 경우 검색 결과를 rows에 포함
                rows = []
                if last_domain == "recruitment":
                    search_results = final_state.get("search_results", [])
                    if search_results:
                        rows = search_results
                
                # 문서 업데이트 UI 표시 플래그 확인
                show_document_update_ui = final_state.get("show_document_update_ui", False)
                
                # 디버깅: 문서 업데이트 UI 플래그 확인
                print(f"🔍 [FastAPI] 문서 업데이트 UI 플래그: {show_document_update_ui}")
                print(f"🔍 [FastAPI] final_state keys: {list(final_state.keys())}")
                if "show_document_update_ui" in final_state:
                    print(f"🔍 [FastAPI] show_document_update_ui 값: {final_state['show_document_update_ui']}")
                
                result = {
                    "type": "complete",
                    "ok": True,
                    "message": final_msg,
                    "rows": rows,  # Recruitment 검색 결과 또는 빈 배열
                    "session_id": session_id,
                    "document_path": document_path,
                    "requires_input": requires_input,
                    "last_domain": last_domain,  # LLM 라우팅 시 결정된 도메인
                    "show_document_update_ui": show_document_update_ui,  # 문서 업데이트 UI 표시 플래그
                }
                
                print(f"🔍 [FastAPI] 응답에 포함된 show_document_update_ui: {result.get('show_document_update_ui')}")
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
                        "last_domain": final_state.get("last_domain")  # last_domain 저장
                    }
                    # 디버그 메시지 제거 (프로덕션 환경에서는 불필요)
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
async def chat_endpoint(payload: ChatRequest) -> ChatResponse:
    """LangGraph Orchestrator 기반 통합 챗봇 엔드포인트.
    
    지원 시나리오:
    - 스케줄 관리 (연차/반차 신청, 회의실 예약)
    - 문서 작성 (제품개발 계획서, 발주서 등)
    - Q&A (메뉴얼 검색, 복리후생, 회사식단)
    - 이메일 검색 및 자동 응답
    - 기술 검색 (특허, 논문)
    """
    try:
        # session_id가 없으면 새 세션 생성
        if not payload.session_id:
            session_manager = await get_session_manager()
            if session_manager:
                session_id = await session_manager.create_session(employee_id=payload.employee_id)
            else:
                import uuid
                session_id = f"session_{payload.employee_id}_{uuid.uuid4().hex[:8]}"
        else:
            session_id = payload.session_id

        # ✅ 첫 채팅 요청에서 현재 세션을 알 수 있으므로, 해당 세션을 수신감지 대상에 등록 + supervisor(프로세스당 1개) 시작
        try:
            from agents.master_graphs.email.email_search_agent import (
                register_email_check_session,
                start_email_checker_supervisor,
            )
            register_email_check_session(session_id)
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
            session_data = await session_manager.load_session_data(session_id)
        
        # 캐시된 Orchestrator 그래프 가져오기 (체크포인터 없이 실행)
        graph = get_cached_orchestrator_graph()
        
        # 입력 state 구성 (이전 세션 데이터 포함)
        input_state = {
            "query": payload.query,
            "user_id": payload.employee_id,
            "session_id": session_id,
            "employee_id": payload.employee_id,
            "conversation_history": session_data.get("conversation_history", []),
            "email_search_context": session_data.get("email_search_context", {}),
            # Redis/세션에서 null(None)로 들어오는 경우 방어
            "email_compose_context": session_data.get("email_compose_context") or {},
            "qa_search_context": session_data.get("qa_search_context", {}),
            "tech_search_context": session_data.get("tech_search_context", {}),
            "schedule_context": session_data.get("schedule_context", {}),
            "document_context": session_data.get("document_context", {}),
            "recruitment_context": session_data.get("recruitment_context", {})
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
                "last_domain": final_state.get("last_domain")  # last_domain 저장
            }
            # 디버그 메시지 제거 (프로덕션 환경에서는 불필요)
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
        
        # document_path 추출 (문서 도메인에서만 의미가 있음)
        document_path = None
        if current_domain == "document":
            # 문서 도메인에서만 document_path 사용
            document_path = final_state.get("document_path")
        else:
            # 다른 도메인으로 전환된 경우 이전 document_path 제거
            document_path = None
        
        # requires_input 플래그 추출 (예시문 표시 시)
        requires_input = final_state.get("requires_input", False)
        
        # 도메인 정보 추출 (LLM 라우팅 시 결정된 도메인)
        # domain이 없으면 last_domain을 fallback으로 사용
        last_domain = final_state.get("domain") or final_state.get("last_domain")
        
        # Recruitment 도메인인 경우 검색 결과를 rows에 포함
        rows = []
        if last_domain == "recruitment":
            search_results = final_state.get("search_results", [])
            if search_results:
                # 카드 형식으로 변환
                rows = search_results

        return ChatResponse(
            ok=True,
            message=final_msg,
            rows=rows,  # Recruitment 검색 결과 또는 빈 배열
            session_id=session_id,
            document_path=document_path,  # 생성된 문서 파일 경로 (문서 도메인에서만)
            requires_input=requires_input,  # 사용자 입력 필요 여부
            last_domain=last_domain,  # LLM 라우팅 시 결정된 도메인
        )

    except Exception as exc:  # pragma: no cover
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"챗봇 처리 실패: {exc}") from exc


@app.post("/api/upload-document", response_model=DocumentUploadResponse)
async def upload_document_endpoint(
    file: UploadFile = FastAPIFile(...),
    document_title: str = Form(...),
    employee_id: int = Form(4000)
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
                session_data = await session_manager.load_session_data(session_id)
            
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

    try:
        flow = _create_google_oauth_flow()
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
    """Google OAuth 인증 후 토큰을 저장."""
    _cleanup_expired_oauth_states()

    state_entry = _oauth_state_store.pop(state, None)
    if not state_entry:
        raise HTTPException(status_code=400, detail="유효하지 않은 OAuth state입니다. 다시 시도해주세요.")

    user_id, _ = state_entry

    try:
        flow = _create_google_oauth_flow()
        flow.fetch_token(code=code)
        credentials = flow.credentials
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google OAuth 토큰 교환 실패: {exc}") from exc

    session_manager = await get_session_manager()
    if not session_manager:
        raise HTTPException(status_code=500, detail="세션 관리자가 초기화되지 않았습니다. Redis 설정을 확인하세요.")

    existing_tokens = await session_manager.get_google_tokens(user_id) or {}
    tokens = {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token or existing_tokens.get("refresh_token"),
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        "scopes": list(credentials.scopes) if credentials.scopes else existing_tokens.get("scopes", []),
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
    }

    ttl_seconds = None
    if tokens.get("refresh_token"):
        ttl_seconds = int(os.environ.get("GOOGLE_TOKEN_TTL", 60 * 60 * 24 * 30))
    elif credentials.expiry:
        ttl_seconds = max(int(credentials.expiry.timestamp() - time.time()), 60)

    await session_manager.save_google_tokens(user_id, tokens, ttl=ttl_seconds)

    success_html = """
    <html>
        <head>
            <title>Google 인증 완료</title>
        </head>
        <body>
            <h2>✅ Google 계정 연동이 완료되었습니다.</h2>
            <p>이 창을 닫고 다시 일정 관련 요청을 진행해주세요.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=success_html)


@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(employee_id: int = Query(4000, description="사원 번호")) -> SessionListResponse:
    """사원별 세션 목록 조회 (최신순).
    
    Args:
        employee_id: 사원 번호
        
    Returns:
        세션 목록 (최신순 정렬)
    """
    try:
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
async def create_session(payload: SessionCreateRequest) -> SessionResponse:
    """새 세션 생성.
    
    Args:
        payload: 세션 생성 요청 (employee_id, title)
        
    Returns:
        생성된 세션 정보
    """
    try:
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")
        
        session_id = await session_manager.create_session(
            employee_id=payload.employee_id,
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
async def delete_session(session_id: str, employee_id: int = Query(4000, description="사원 번호")) -> SessionResponse:
    """세션 삭제.
    
    Args:
        session_id: 삭제할 세션 ID
        employee_id: 사원 번호
        
    Returns:
        삭제 결과
    """
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
async def update_session(session_id: str, payload: SessionUpdateRequest) -> SessionResponse:
    """세션 메타데이터 업데이트 (제목 변경 등).
    
    Args:
        session_id: 업데이트할 세션 ID
        payload: 업데이트 요청 (title 등)
        
    Returns:
        업데이트된 세션 정보
    """
    try:
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")
        
        success = await session_manager.update_session_metadata(
            session_id=session_id,
            title=payload.title
        )
        
        if success:
            # 업데이트된 세션 정보 조회
            session_data = await session_manager.load_session_data(session_id)
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


@app.post("/api/session/new", response_model=SessionResponse)
async def create_new_session(employee_id: int = 4000) -> SessionResponse:
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
async def change_session(payload: SessionRequest) -> SessionResponse:
    """세션 변경 또는 생성.
    
    Args:
        payload: 세션 변경 요청 (session_id가 None이면 새 세션 생성)
        
    Returns:
        세션 정보
    """
    try:
        session_manager = await get_session_manager()
        if not session_manager:
            raise HTTPException(status_code=500, detail="세션 관리자를 사용할 수 없습니다.")
        
        if payload.session_id:
            # 기존 세션으로 변경
            session_id = payload.session_id
            session_data = await session_manager.load_session_data(session_id)
            message_count = len(session_data.get("conversation_history", []))
            
            return SessionResponse(
                ok=True,
                session_id=session_id,
                message_count=message_count,
                message=f"세션이 변경되었습니다. (대화 {message_count}개)"
            )
        else:
            # 새 세션 생성
            employee_id = payload.employee_id or 4000
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
async def get_session_info(session_id: str) -> SessionResponse:
    """현재 세션 정보 조회.
    
    Args:
        session_id: 세션 ID
        
    Returns:
        세션 정보 (대화 히스토리 개수 등)
    """
    session_manager = await get_session_manager()
    message_count = 0
    conversation_history = []
    
    if session_manager:
        session_data = await session_manager.load_session_data(session_id)
        conversation_history = session_data.get("conversation_history", [])
        message_count = len(conversation_history)
    
    return SessionResponse(
        ok=True,
        session_id=session_id,
        message_count=message_count,
        message=f"현재 세션: {session_id} (대화 {message_count}개)",
        conversation_history=conversation_history
    )


@app.get("/healthz")
async def health_check():
    """헬스 체크 엔드포인트."""
    return {"status": "ok", "service": "깐부 AI Agent 시스템"}


# ===== 테스트용 엔드포인트 (연차/반차 & 일정 관리) =====

class LeaveTestRequest(BaseModel):
    """연차 테스트 요청 모델."""
    query: str = Field(..., description="연차 관련 질의")
    employee_id: int = Field(default=12345, description="사원 번호")
    session_id: Optional[str] = Field(default=None, description="세션 ID (멀티턴 대화)")


class LeaveTestResponse(BaseModel):
    """연차 테스트 응답 모델."""
    ok: bool = Field(..., description="성공 여부")
    message: str = Field(..., description="응답 메시지")
    slot_state: Optional[dict] = Field(default=None, description="슬롯 상태")
    awaiting_slots: Optional[list] = Field(default=None, description="대기 중인 슬롯")
    action: Optional[str] = Field(default=None, description="실행된 액션")


@app.post("/api/test/leave", response_model=LeaveTestResponse)
async def test_leave_endpoint(payload: LeaveTestRequest) -> LeaveTestResponse:
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
        
        # 세션 ID 설정
        session_id = payload.session_id or f"test_leave_{payload.employee_id}"
        
        # 세션 관리자로 이전 컨텍스트 로드
        session_manager = await get_session_manager()
        session_data = {"schedule_context": {}}
        if session_manager:
            loaded_data = await session_manager.load_session_data(session_id)
            session_data = {"schedule_context": loaded_data.get("schedule_context", {})}
        
        # State 구성
        state = OrchestratorState(
            query=payload.query,
            employee_id=payload.employee_id,
            user_id=str(payload.employee_id),
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
    employee_id: int = Field(default=12345, description="사원 번호")
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
async def test_schedule_endpoint(payload: ScheduleTestRequest) -> ScheduleTestResponse:
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
        
        # 세션 ID 설정
        user_id = payload.user_id or str(payload.employee_id)
        session_id = payload.session_id or f"test_schedule_{user_id}"
        
        # 세션 관리자로 이전 컨텍스트 로드
        session_manager = await get_session_manager()
        session_data = {"schedule_context": {}}
        if session_manager:
            loaded_data = await session_manager.load_session_data(session_id)
            session_data = {"schedule_context": loaded_data.get("schedule_context", {})}
        
        # State 구성
        state = OrchestratorState(
            query=payload.query,
            employee_id=payload.employee_id,
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