"""통합 AI 에이전트 FastAPI 서버이다."""
from __future__ import annotations

import uuid
from typing import Dict, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents.supervisor import run_supervisor
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI 앱 생성
app = FastAPI(
    title="통합 AI 에이전트 API",
    description="회의실 + 연차 + QA + 문서 + 특허/논문 통합 에이전트",
    version="1.0.0",
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# DTO 정의
class ChatRequest(BaseModel):
    """채팅 요청 모델"""
    message: str = Field(..., description="사용자 질의")
    
    # 🆕 reservation 추가
    reservation: Dict[str, Any] | None = Field(
        default=None, 
        description="예약 정보 (예약/취소 시 사용)"
    )
    
    # 기존 context
    context: Dict[str, Any] = Field(
        default_factory=dict, 
        description="추가 컨텍스트"
    )


class ChatResponse(BaseModel):
    """채팅 응답 모델"""
    intent: str | None = Field(default=None, description="추론된 의도")
    response: str = Field(description="응답 메시지")
    state: Dict[str, Any] | None = Field(default=None, description="상태 정보")


# 엔드포인트 정의
@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "message": "통합 AI 에이전트 서버",
        "status": "running",
        "version": "1.0.0",
        "agents": ["schedule", "document", "qa", "tech"],
    }


@app.get("/health")
async def health():
    """헬스 체크"""
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """통합 AI 에이전트 채팅 엔드포인트"""
    logger.info(f"📥 요청 수신: message={request.message}, reservation={request.reservation is not None}")
    
    # 세션 ID 생성 (멀티턴 대화 지원)
    session_id = str(uuid.uuid4())
    
    # 🆕 context에 reservation 병합
    full_context = request.context.copy()
    if request.reservation:
        full_context["reservation"] = request.reservation
        logger.info(f"📋 예약 정보 추가: {request.reservation}")
    
    logger.info(f"🎯 최종 context: {full_context}")
    
    # Supervisor 실행
    result = run_supervisor(
        request.message, 
        session_id=session_id,
        context=full_context  # 🆕 context 전달
    )
    
    # 응답 포맷팅
    if isinstance(result, dict):
        intent = result.get("intent")
        message = result.get("message", str(result))
        
        logger.info(f"✅ 응답 생성: intent={intent}")
        
        return ChatResponse(
            intent=intent,
            response=message,
            state=result,
        )
    
    # 결과가 dict가 아닌 경우
    return ChatResponse(
        intent=None,
        response=str(result),
        state={"raw": result},
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )