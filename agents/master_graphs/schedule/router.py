"""스케줄 도메인 라우팅 로직 분리 모듈."""

from __future__ import annotations

import asyncio
import json
import time

from agents.llm_router import classify_intent_with_llm
from agents.state import OrchestratorState

ALLOWED_INTENTS = {
    "leave",
    "schedule",
    "meeting",
    "business_trip",
    "interview",
}


async def determine_schedule_intent(state: OrchestratorState) -> str:
    """스케줄 마스터그래프의 하위 의도를 결정한다.

    Args:
        state: 오케스트레이터 상태.

    Returns:
        결정된 intent 문자열.
    """
    step_start = time.time()
    query = state.get("query", "")
    session_id = state.get("session_id")

    workflow_in_progress = state.get("workflow_in_progress", False)
    workflow_type = state.get("workflow_type")
    current_step_index = state.get("current_workflow_step_index", 0)
    workflow_steps = state.get("workflow_steps", [])

    if workflow_in_progress and workflow_type == "business_trip":
        if current_step_index < len(workflow_steps) and workflow_steps[current_step_index] == "schedule":
            print(
                f"✅ 워크플로우 모드 감지: {workflow_type} (Step {current_step_index + 1})"
                " → business_trip으로 의도 강제"
            )
            return "business_trip"

    schedule_context = state.get("schedule_context") or {}

    # 면접 우선 라우팅
    interview_keywords = ["면접", "면접관", "면접 일정", "면접 일정 조정", "면접일정", "면접일정잡아줘"]
    if any(keyword in query for keyword in interview_keywords):
        print("✅ 면접 일정 조정 감지 - interview로 라우팅")
        return "interview"

    interview_context = schedule_context.get("interview") or {}
    if interview_context:
        try:
            if query.strip().startswith("{"):
                query_json = json.loads(query)
                if isinstance(query_json, dict) and any(
                    key in query_json for key in ["selectedInterviewers", "duration_minutes", "slot_index", "start", "end"]
                ):
                    print("✅ 면접 일정 진행 중 감지 (JSON) - interview로 라우팅")
                    return "interview"
        except (json.JSONDecodeError, AttributeError):
            pass

        if (
            interview_context.get("selected_interviewers")
            or interview_context.get("duration_minutes")
            or interview_context.get("requires_slot_selection")
            or interview_context.get("available_slots")
        ):
            print("✅ 면접 일정 진행 중 감지 (컨텍스트) - interview로 라우팅")
            return "interview"

    # 일정 삭제/수정 흐름 유지
    schedule_ctx = schedule_context.get("schedule") or {}
    schedule_awaiting_slots = schedule_ctx.get("awaiting_slots") or []
    if schedule_awaiting_slots and (
        "delete_selection" in schedule_awaiting_slots or "update_selection" in schedule_awaiting_slots
    ):
        print("✅ 일정 삭제/수정 선택 대기 중 - schedule로 라우팅")
        return "schedule"

    if schedule_ctx.get("delete_candidates"):
        print("✅ 일정 삭제 후보가 남아있음 - schedule로 라우팅")
        return "schedule"

    # 회의실 흐름 유지
    meeting_context = schedule_context.get("meeting") or {}
    meeting_slot_state = meeting_context.get("slot_state") or {}
    awaiting_slots = meeting_context.get("awaiting_slots") or []
    missing_slots = meeting_slot_state.get("missing_slots") or []

    if meeting_context.get("recommended_rooms"):
        print("✅ 회의실 추천 후 선택 진행 중 - meeting으로 라우팅")
        return "meeting"

    if awaiting_slots:
        print("✅ 회의실 슬롯 질의 응답 진행 중 - meeting으로 라우팅")
        return "meeting"

    if missing_slots:
        print("✅ 회의실 슬롯이 아직 남아 있음 - meeting으로 라우팅")
        return "meeting"

    pending_reservation = meeting_context.get("pending_reservation")
    awaiting_field = meeting_context.get("awaiting_field")
    if pending_reservation and awaiting_field:
        print("✅ 회의실 예약 슬롯 채우기 진행 중 - meeting으로 라우팅")
        return "meeting"

    intent = await asyncio.to_thread(
        classify_intent_with_llm,
        query=query,
        route_type="schedule",
        fallback_route="schedule",
        session_id=session_id,
    )

    if intent not in ALLOWED_INTENTS:
        intent = "schedule"

    print(f"✅ Schedule 의도 결정: {intent} ({(time.time() - step_start):.2f}s)")
    return intent

