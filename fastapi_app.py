from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File as FastAPIFile, Form
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.schedule_master import schedule_master
from agents.orchestrator_v3 import get_orchestrator_graph
from agents.master_graphs.document.document_upload_service import process_uploaded_file
from agents.redis_checkpoint import get_redis_checkpointer

# Redis 체크포인터 초기화 (앱 시작 시 한 번만)
_redis_checkpointer = get_redis_checkpointer()


class ScheduleChatRequest(BaseModel):
    """스케줄 챗봇 질의 모델."""

    query: str = Field(..., description="사용자 입력 질의 문장")


class ScheduleChatResponse(BaseModel):
    """스케줄 챗봇 응답 모델."""

    ok: bool = Field(..., description="요청 처리 성공 여부")
    message: str = Field(..., description="LLM이 생성한 응답 메시지")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="조회된 일정 행 목록")


app = FastAPI(
    title="AI Agent Management Chatbot API",
    version="0.3.0",
    description="LangGraph Orchestrator 기반 멀티 에이전트 챗봇 API",
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 환경에서는 모든 origin 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/schedule/chat", response_model=ScheduleChatResponse)
async def schedule_chat_endpoint(payload: ScheduleChatRequest) -> ScheduleChatResponse:
    """스케줄 챗봇 질의 엔드포인트."""
    try:
        result = await run_in_threadpool(schedule_master, payload.query)
    except Exception as exc:  # pragma: no cover - FastAPI가 처리
        raise HTTPException(status_code=500, detail=f"일정 챗봇 처리 실패: {exc}") from exc

    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="일정 챗봇 결과가 비정상입니다.")

    return ScheduleChatResponse(
        ok=bool(result.get("ok", False)),
        message=str(result.get("message", "")),
        rows=result.get("rows") or [],
    )


@app.get("/api/schedule", response_model=ScheduleChatResponse)
async def schedule_get_endpoint(query: str = Query("", description="사용자 일정 질의")) -> ScheduleChatResponse:
    """GET 방식 일정 조회 엔드포인트."""
    normalized = query.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="query parameter must not be empty.")

    try:
        result = await run_in_threadpool(schedule_master, normalized)
    except Exception as exc:  # pragma: no cover - FastAPI가 처리
        raise HTTPException(status_code=500, detail=f"일정 챗봇 처리 실패: {exc}") from exc

    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="일정 챗봇 결과가 비정상입니다.")

    return ScheduleChatResponse(
        ok=bool(result.get("ok", False)),
        message=str(result.get("message", "")),
        rows=result.get("rows") or [],
    )


@app.get("/healthz")
async def health_check() -> dict[str, str]:
    """간단한 헬스 체크 엔드포인트."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def serve_ui() -> HTMLResponse:
    """전자결재 시스템 UI 메인 페이지."""
    templates_dir = Path(__file__).parent / "templates"
    html_file = templates_dir / "index.html"
    
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="UI template not found")
    
    with open(html_file, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    return HTMLResponse(content=html_content)


@app.get("/document-writer", response_class=HTMLResponse)
async def serve_document_writer_ui() -> HTMLResponse:
    """AI 문서 작성 전용 UI 페이지."""
    templates_dir = Path(__file__).parent / "templates"
    html_file = templates_dir / "document_writer.html"
    
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Document writer UI template not found")
    
    with open(html_file, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    return HTMLResponse(content=html_content)


class LeaveChatRequest(BaseModel):
    """휴가(leave) 챗봇 질의 모델."""

    query: str = Field(..., description="휴가/연차 관련 질의 문장")


class LeaveChatResponse(BaseModel):
    """휴가(leave) 챗봇 응답 모델."""

    ok: bool = Field(..., description="요청 처리 성공 여부")
    message: str = Field(..., description="휴가 챗봇 메시지")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="추가 데이터(필요 시)")
    error: Optional[str] = Field(default=None, description="오류 메시지")


@app.post("/api/leave/chat", response_model=LeaveChatResponse)
async def leave_chat_endpoint(payload: LeaveChatRequest) -> LeaveChatResponse:
    """휴가 전용 챗봇 엔드포인트."""
    normalized = payload.query.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="query 값이 필요합니다.")

    try:
        result = await run_in_threadpool(schedule_master, normalized)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"휴가 챗봇 처리 실패: {exc}") from exc

    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="휴가 챗봇 결과가 비정상입니다.")

    return LeaveChatResponse(
        ok=bool(result.get("ok", False)),
        message=str(result.get("message", "")),
        rows=result.get("rows") or [],
        error=result.get("error"),
    )


class ChatRequest(BaseModel):
    """통합 챗봇 질의 모델."""

    query: str = Field(..., description="사용자 입력 질의 문장")
    session_id: Optional[str] = Field(default=None, description="대화 세션 ID (멀티턴 대화 지원)")
    # Short-term memory injection: 사용자 ID를 요청 시 주입 (로그인 기능 부재로 인한 임시 조치)
    employee_id: Optional[int] = Field(default=1001, description="사원 번호 (기본값 1001)")


class ChatResponse(BaseModel):
    """통합 챗봇 응답 모델."""

    ok: bool = Field(..., description="요청 처리 성공 여부")
    message: str = Field(..., description="에이전트 응답 메시지")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="조회된 데이터 (있는 경우)")
    error: Optional[str] = Field(default=None, description="오류 메시지")
    session_id: Optional[str] = Field(default=None, description="세션 ID")


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(payload: ChatRequest) -> ChatResponse:
    """LangGraph Orchestrator 기반 통합 챗봇 엔드포인트.
    
    지원 시나리오:
    1. 원스톱 출장/휴가 관리 (Schedule -> Document -> Email)
    2. R&D 리포트 자동화 (Search -> RAG -> Report -> Email)
    3. 스마트 회의 준비 (Room -> Materials -> Invite -> Snacks)
    4. 기타 일반 조회 및 검색 (Legacy Agents Fallback)
    """
    normalized = payload.query.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="query 값이 필요합니다.")

    try:
        # 그래프 생성 (Redis 체크포인터는 자동으로 생성됨)
        # checkpointer를 전달하지 않으면 orchestrator_v3에서 자동으로 Redis 체크포인터 생성 시도
        graph = get_orchestrator_graph(checkpointer=_redis_checkpointer)
        
        # 세션 ID 생성 (없으면 자동 생성)
        session_id = payload.session_id
        if not session_id:
            import uuid
            session_id = str(uuid.uuid4())
        
        # Initial State with injected employee_id
        # Redis 체크포인터를 사용하면 이전 state가 자동으로 복원됨
        input_state = {
            "query": normalized,
            "session_id": session_id,
            "user_id": str(payload.employee_id),
            "employee_id": payload.employee_id, # Inject employee_id for short-term memory
            "messages": [],
            "conversation_history": []  # 멀티턴 대화 히스토리
        }
        
        # Redis 체크포인터를 사용하여 state 복원 및 실행
        # session_id를 thread_id로 사용하여 세션별 독립적인 메모리 관리
        config = {"configurable": {"thread_id": session_id}}
        
        # 그래프 실행 (Redis 체크포인터가 있으면 state가 자동으로 저장되고 복원됨)
        final_state = await graph.ainvoke(input_state, config=config)
        
        # Process Output
        messages = final_state.get("messages", [])
        final_msg = final_state.get("final_response")
        conversation_history = final_state.get("conversation_history", [])
        
        if not final_msg:
            # Join messages if no explicit final response
            final_msg = "\n".join(messages) if messages else "요청하신 작업을 완료했습니다."
        
        # 최근 응답 메시지 찾기
        if conversation_history:
            recent_assistant_msgs = [
                msg for msg in conversation_history 
                if msg.get("role") == "assistant"
            ]
            if recent_assistant_msgs:
                final_msg = recent_assistant_msgs[-1].get("content", final_msg)

        return ChatResponse(
            ok=True,
            message=final_msg,
            rows=[], # 오케스트레이터는 주로 텍스트 응답을 반환하므로 rows는 비움
            session_id=session_id,
        )

    except Exception as exc:  # pragma: no cover
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"챗봇 처리 실패: {exc}") from exc


class DocumentUploadResponse(BaseModel):
    """문서 업로드 응답 모델."""

    ok: bool = Field(..., description="요청 처리 성공 여부")
    message: str = Field(..., description="처리 결과 메시지")
    document_title: Optional[str] = Field(default=None, description="문서 제목")
    chunks_count: Optional[int] = Field(default=None, description="생성된 청크 수")
    text_length: Optional[int] = Field(default=None, description="추출된 텍스트 길이")


@app.post("/api/upload-document", response_model=DocumentUploadResponse)
async def upload_document_endpoint(
    file: UploadFile = FastAPIFile(..., description="업로드할 문서 파일 (PDF/DOCX)"),
    document_title: str = Form(..., description="문서 제목"),
    employee_id: Optional[int] = Form(default=1001, description="사원 번호"),
) -> DocumentUploadResponse:
    """문서 업로드 및 Milvus DB 저장 엔드포인트."""
    try:
        # 파일 형식 검증
        file_extension = Path(file.filename).suffix.lower() if file.filename else ""
        if file_extension not in [".pdf", ".docx"]:
            raise HTTPException(
                status_code=400,
                detail="지원하지 않는 파일 형식입니다. PDF (.pdf) 또는 Word (.docx) 파일만 업로드 가능합니다.",
            )

        if not document_title or not document_title.strip():
            raise HTTPException(status_code=400, detail="문서 제목을 입력해주세요.")

        # 파일 내용 읽기
        file_content = await file.read()
        
        if not file_content:
            raise HTTPException(status_code=400, detail="파일이 비어있습니다.")

        # 문서 처리 (동기 함수를 비동기로 실행)
        result = await run_in_threadpool(
            process_uploaded_file,
            file_content,
            file.filename or "unknown",
            document_title.strip(),
        )

        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("message", "문서 처리 실패"))

        return DocumentUploadResponse(
            ok=True,
            message=result.get("message", "문서가 성공적으로 저장되었습니다."),
            document_title=result.get("document_title"),
            chunks_count=result.get("chunks_count"),
            text_length=result.get("text_length"),
        )

    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"문서 업로드 실패: {exc}") from exc
