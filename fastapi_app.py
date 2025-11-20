from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from agents.schedule_master import schedule_master
from agents.supervisor import run_supervisor


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
    version="0.2.0",
    description="LangGraph 기반 멀티 에이전트 매니저먼트 챗봇 API",
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


class ChatResponse(BaseModel):
    """통합 챗봇 응답 모델."""

    ok: bool = Field(..., description="요청 처리 성공 여부")
    message: str = Field(..., description="에이전트 응답 메시지")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="조회된 데이터 (있는 경우)")
    error: Optional[str] = Field(default=None, description="오류 메시지")
    session_id: Optional[str] = Field(default=None, description="세션 ID")


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(payload: ChatRequest) -> ChatResponse:
    """통합 에이전트 매니저먼트 챗봇 엔드포인트.
    
    LangGraph 기반 멀티 에이전트 시스템으로 사용자 질의를 처리합니다.
    - 일정/연차: schedule 에이전트
    - 문서/이메일: document 에이전트
    - Q&A: qa 에이전트
    - 기술: tech 에이전트
    """
    normalized = payload.query.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="query 값이 필요합니다.")

    try:
        result = await run_in_threadpool(run_supervisor, normalized, payload.session_id)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"챗봇 처리 실패: {exc}") from exc

    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="챗봇 결과가 비정상입니다.")

    return ChatResponse(
        ok=bool(result.get("ok", False)),
        message=str(result.get("message", "")),
        rows=result.get("rows") or [],
        error=result.get("error"),
        session_id=result.get("session_id") or payload.session_id,
    )

