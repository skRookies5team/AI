"""FastAPI 애플리케이션 - AI Agent 시스템 UI 및 API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File as FastAPIFile, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import json
from pydantic import BaseModel, Field

from agents.orchestrator_v3 import get_orchestrator_graph
from agents.master_graphs.document.document_upload_service import process_uploaded_file
from agents.session_manager import get_session_manager
import asyncio
import threading
from datetime import datetime, timedelta

# Orchestrator 그래프 캐시 (앱 시작 시 한 번만 생성)
_orchestrator_graph = None

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
        
        redis_host = os.environ.get("HOST") or os.environ.get("REDIS_HOST") or "localhost"
        redis_port_str = os.environ.get("REDIS_PORT") or "6379"
        redis_password = os.environ.get("REDIS_PASSWORD")
        redis_db_str = os.environ.get("REDIS_DB") or "0"
        
        try:
            redis_port = int(redis_port_str.strip())
        except (ValueError, AttributeError):
            redis_port = 6379
        
        try:
            redis_db = int(redis_db_str.strip())
        except (ValueError, AttributeError):
            redis_db = 0
        
        if redis_password == "":
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
    employee_id: Optional[int] = Field(default=1001, description="사원 번호")


class ChatResponse(BaseModel):
    """통합 챗봇 응답 모델."""

    ok: bool = Field(..., description="요청 처리 성공 여부")
    message: str = Field(..., description="에이전트 응답 메시지")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="조회된 데이터 (있는 경우)")
    error: Optional[str] = Field(default=None, description="오류 메시지")
    session_id: Optional[str] = Field(default=None, description="세션 ID")
    document_path: Optional[str] = Field(default=None, description="생성된 문서 파일 경로 (있는 경우)")
    requires_input: Optional[bool] = Field(default=False, description="사용자 입력 필요 여부 (예시문 표시 시)")


class SessionRequest(BaseModel):
    """세션 변경 요청 모델."""
    session_id: Optional[str] = Field(default=None, description="세션 ID (None이면 새 세션 생성)")
    employee_id: Optional[int] = Field(default=1001, description="사원 번호")


class SessionResponse(BaseModel):
    """세션 정보 응답 모델."""
    ok: bool = Field(..., description="요청 처리 성공 여부")
    session_id: str = Field(..., description="세션 ID")
    message_count: int = Field(default=0, description="대화 히스토리 메시지 개수")
    message: Optional[str] = Field(default=None, description="응답 메시지")


class DocumentUploadResponse(BaseModel):
    """문서 업로드 응답 모델."""

    ok: bool = Field(..., description="업로드 성공 여부")
    message: str = Field(..., description="응답 메시지")
    document_title: Optional[str] = Field(default=None, description="업로드된 문서 제목")
    error: Optional[str] = Field(default=None, description="오류 메시지")


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


@app.post("/api/chat/stream")
async def chat_stream_endpoint(payload: ChatRequest):
    """스트리밍 챗봇 엔드포인트 - 실시간 작업 상태 표시."""
    async def generate():
        try:
            # 세션 ID 설정 (employee_id 기반으로 고정 세션 사용)
            session_id = payload.session_id or f"session_{payload.employee_id}"
            
            # 세션 관리자로 이전 세션 데이터 전체 로드 (conversation_history + 컨텍스트들)
            session_manager = await get_session_manager()
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
                "qa_search_context": session_data.get("qa_search_context", {}),
                "tech_search_context": session_data.get("tech_search_context", {}),
                "schedule_context": session_data.get("schedule_context", {}),
                "document_context": session_data.get("document_context", {})
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
                
                result = {
                    "type": "complete",
                    "ok": True,
                    "message": final_msg,
                    "session_id": session_id,
                    "document_path": document_path,
                    "requires_input": requires_input,
                }
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
                    conversation_history = list(final_state.get("conversation_history", []))
                    if not conversation_history or conversation_history[-1].get("role") != "user":
                        conversation_history.append(user_message)
                    
                    # Assistant 메시지 추가 (없으면 final_response로 생성)
                    if not conversation_history or conversation_history[-1].get("role") != "assistant":
                        if final_msg:
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
                        "qa_search_context": final_state.get("qa_search_context", {}),
                        "tech_search_context": final_state.get("tech_search_context", {}),
                        "schedule_context": final_state.get("schedule_context", {}),
                        "document_context": final_state.get("document_context", {})
                    }
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
        # 세션 ID 설정 (employee_id 기반으로 고정 세션 사용)
        session_id = payload.session_id or f"session_{payload.employee_id}"
        
        # 세션 관리자로 이전 세션 데이터 전체 로드 (conversation_history + 컨텍스트들)
        session_manager = await get_session_manager()
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
            "qa_search_context": session_data.get("qa_search_context", {}),
            "tech_search_context": session_data.get("tech_search_context", {}),
            "schedule_context": session_data.get("schedule_context", {}),
            "document_context": session_data.get("document_context", {})
        }
        
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
            conversation_history = list(final_state.get("conversation_history", []))
            if not conversation_history or conversation_history[-1].get("role") != "user":
                conversation_history.append(user_message)
            
            # Assistant 메시지 추가 (없으면 final_response로 생성)
            if not conversation_history or conversation_history[-1].get("role") != "assistant":
                if final_state.get("final_response"):
                    assistant_message = {
                        "role": "assistant",
                        "content": final_state.get("final_response"),
                        "timestamp": datetime.now().isoformat()
                    }
                    conversation_history.append(assistant_message)
            
            # 세션 state 전체 저장 (conversation_history + 모든 컨텍스트)
            session_state = {
                "conversation_history": conversation_history,
                "email_search_context": final_state.get("email_search_context", {}),
                "qa_search_context": final_state.get("qa_search_context", {}),
                "tech_search_context": final_state.get("tech_search_context", {}),
                "schedule_context": final_state.get("schedule_context", {}),
                "document_context": final_state.get("document_context", {})
            }
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

        return ChatResponse(
            ok=True,
            message=final_msg,
            rows=[],  # 오케스트레이터는 주로 텍스트 응답을 반환하므로 rows는 비움
            session_id=session_id,
            document_path=document_path,  # 생성된 문서 파일 경로 (문서 도메인에서만)
            requires_input=requires_input,  # 사용자 입력 필요 여부
        )

    except Exception as exc:  # pragma: no cover
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"챗봇 처리 실패: {exc}") from exc


@app.post("/api/upload-document", response_model=DocumentUploadResponse)
async def upload_document_endpoint(
    file: UploadFile = FastAPIFile(...),
    document_title: str = Form(...),
    employee_id: int = Form(1001)
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
        
        # 문서 업로드 서비스 호출 (동기 함수를 비동기로 실행)
        from fastapi.concurrency import run_in_threadpool
        result = await run_in_threadpool(
            process_uploaded_file,
            file_content=content,
            filename=file.filename,
            document_title=document_title
        )
        
        if result.get("ok"):
            return DocumentUploadResponse(
                ok=True,
                message=result.get("message", "문서가 성공적으로 업로드되었습니다."),
                document_title=document_title
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


@app.post("/api/session/new", response_model=SessionResponse)
async def create_new_session(employee_id: int = 1001) -> SessionResponse:
    """새 세션 생성.
    
    Args:
        employee_id: 사원 번호
        
    Returns:
        새로 생성된 세션 ID
    """
    import uuid
    session_id = f"session_{employee_id}_{uuid.uuid4().hex[:8]}"
    return SessionResponse(
        ok=True,
        session_id=session_id,
        message_count=0,
        message="새 세션이 생성되었습니다."
    )


@app.post("/api/session/change", response_model=SessionResponse)
async def change_session(payload: SessionRequest) -> SessionResponse:
    """세션 변경 또는 생성.
    
    Args:
        payload: 세션 변경 요청 (session_id가 None이면 새 세션 생성)
        
    Returns:
        세션 정보
    """
    import uuid
    
    if payload.session_id:
        # 기존 세션으로 변경
        session_id = payload.session_id
        session_manager = await get_session_manager()
        message_count = 0
        
        if session_manager:
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
        employee_id = payload.employee_id or 1001
        session_id = f"session_{employee_id}_{uuid.uuid4().hex[:8]}"
        return SessionResponse(
            ok=True,
            session_id=session_id,
            message_count=0,
            message="새 세션이 생성되었습니다."
        )


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
    
    if session_manager:
        session_data = await session_manager.load_session_data(session_id)
        message_count = len(session_data.get("conversation_history", []))
    
    return SessionResponse(
        ok=True,
        session_id=session_id,
        message_count=message_count,
        message=f"현재 세션: {session_id} (대화 {message_count}개)"
    )


@app.get("/healthz")
async def health_check():
    """헬스 체크 엔드포인트."""
    return {"status": "ok", "service": "깐부 AI Agent 시스템"}


# ===== 서버 시작 =====

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*60)
    print("🚀 FastAPI 서버 시작 중...")
    print("="*60)
    print(f"📝 API 문서: http://localhost:8000/docs")
    print(f"🌐 UI 메인 페이지: http://localhost:8000/")
    print(f"📄 문서 작성 UI: http://localhost:8000/")
    print("="*60 + "\n")
    
    # uvicorn 서버 실행
    uvicorn.run(
        "fastapi_app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # 개발 환경에서 자동 리로드
        log_level="info"
    )
