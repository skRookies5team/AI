"""LangServe 라우트 등록 모듈.

FastAPI 애플리케이션에 LangServe 표준 엔드포인트(/invoke, /stream 등)를
등록하여 오케스트레이터 그래프를 일관성 있게 노출한다.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from langchain_core.runnables import RunnableLambda
from langserve import add_routes
from pydantic import BaseModel, ConfigDict, Field

from agents.orchestrator_v3 import get_orchestrator_graph
from agents.session_manager import get_session_manager
from agents.state import OrchestratorState


class LangServeChatInput(BaseModel):
    """LangServe 오케스트레이터 입력 모델."""

    query: str = Field(..., description="사용자 입력 문장")
    # Optional -> 기본값으로 null 타입 노출을 방지
    session_id: str = Field(
        default="", description="세션 ID (없으면 자동 생성)"
    )
    employee_id: int = Field(
        default=0, description="사원 번호 (없으면 기본값 사용)"
    )

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )


class LangServeChatOutput(BaseModel):
    """LangServe 오케스트레이터 출력 모델."""

    ok: bool
    message: str
    session_id: str
    last_domain: Optional[str] = ""
    document_path: Optional[str] = ""
    requires_input: bool = False
    state: Dict[str, Any] = Field(default_factory=dict)


async def _prepare_state(payload: LangServeChatInput) -> OrchestratorState:
    """세션 정보를 불러와 LangGraph가 기대하는 상태로 변환한다.

    Args:
        payload: LangServe 요청 페이로드.

    Returns:
        오케스트레이터 실행에 필요한 상태 딕셔너리.
    """
    employee_id = payload.employee_id or int(os.environ.get("DEFAULT_EMPLOYEE_ID", "4001"))
    session_id = payload.session_id or f"session_{employee_id}_{uuid.uuid4().hex[:8]}"

    session_manager = await get_session_manager()
    base_state: OrchestratorState = {
        "query": payload.query,
        "session_id": session_id,
        "employee_id": employee_id,
        "user_id": employee_id,
    }

    # Redis에 저장된 기존 세션 상태를 병합하여 멀티턴 컨텍스트를 복원.
    if session_manager:
        try:
            stored_state = await session_manager.load_session_data(session_id, employee_id)
            base_state.update(stored_state)
        except Exception:
            # 세션이 없을 수 있으므로 조용히 무시.
            pass

    # 사용자가 추가로 전달한 필드를 상태에 병합.
    base_state.update(payload.model_dump(exclude_none=True))
    return base_state


async def _persist_state(final_state: OrchestratorState) -> Dict[str, Any]:
    """그래프 실행 결과를 세션 스토리지에 저장하고 응답을 정리한다.

    Args:
        final_state: LangGraph 실행 결과 상태.

    Returns:
        최종 메시지와 부가 정보를 포함한 응답.
    """
    session_id = final_state.get("session_id")
    employee_id = final_state.get("employee_id")
    session_manager = await get_session_manager()

    if session_manager and session_id and employee_id:
        session_state = {
            "conversation_history": final_state.get("conversation_history", []),
            "email_search_context": final_state.get("email_search_context", {}),
            "email_compose_context": final_state.get("email_compose_context", {}),
            "qa_search_context": final_state.get("qa_search_context", {}),
            "tech_search_context": final_state.get("tech_search_context", {}),
            "schedule_context": final_state.get("schedule_context", {}),
            "document_context": final_state.get("document_context", {}),
            "recruitment_context": final_state.get("recruitment_context", {}),
            "workflow_temp_data": final_state.get("workflow_temp_data", {}),
            "workflow_type": final_state.get("workflow_type"),
            "workflow_steps": final_state.get("workflow_steps", []),
            "current_workflow_step_index": final_state.get("current_workflow_step_index", 0),
            "workflow_completed_steps": final_state.get("workflow_completed_steps", []),
            "workflow_in_progress": final_state.get("workflow_in_progress", False),
            "next_domain": final_state.get("next_domain"),
            "last_domain": final_state.get("last_domain"),
            "title": final_state.get("title"),
            "employee_id": employee_id,
        }

        await session_manager.save_session_state(session_id, session_state)

    final_msg = final_state.get("final_response")
    if not final_msg:
        conversation_history: List[Dict[str, Any]] = final_state.get("conversation_history", [])
        for message in reversed(conversation_history):
            if message.get("role") == "assistant":
                final_msg = message.get("content")
                break

    if not final_msg:
        final_msg = "요청하신 작업을 완료했습니다."

    return {
        "ok": True,
        "message": final_msg,
        "session_id": session_id,
        "last_domain": final_state.get("domain") or final_state.get("last_domain"),
        "document_path": final_state.get("document_path"),
        "requires_input": final_state.get("requires_input", False),
        "state": final_state,
    }


def register_langserve_routes(
    app: FastAPI, auth_dependency: Optional[Any] = None
) -> None:
    """Orchestrator 그래프를 LangServe 엔드포인트로 등록한다.

    Args:
        app: FastAPI 애플리케이션 인스턴스.
        auth_dependency: 보안 강화를 위한 인증 의존성(HTTP Bearer 등).
    """
    graph = get_orchestrator_graph(checkpointer=None)
    orchestrator_runnable = (
        RunnableLambda(_prepare_state).with_types(input_type=LangServeChatInput)
        | graph
        | RunnableLambda(_persist_state).with_types(output_type=LangServeChatOutput)
    ).with_config({"run_name": "OrchestratorAgent"})

    dependencies = [Depends(auth_dependency)] if auth_dependency else None

    add_routes(
        app,
        orchestrator_runnable,
        path="/api/langserve/orchestrator",
        input_type=LangServeChatInput,
        output_type=LangServeChatOutput,
        dependencies=dependencies,
        config_keys=["session_id", "employee_id"],
    )

