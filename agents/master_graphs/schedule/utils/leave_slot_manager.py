"""연차 Slot Manager - LLM 기반 슬롯 판단 유틸."""

from __future__ import annotations

from typing import Dict, Any

from agents.master_graphs.schedule.models.leave_models import (
    LeaveSlotDecision,
)
from agents.structured_output import extract_structured_output
from agents.master_graphs.schedule.utils.prompt_loader import get_sub_agent_prompt


def _get_leave_slot_manager_prompt() -> str:
    """연차 Slot Manager 프롬프트를 YAML에서 로드합니다."""
    return get_sub_agent_prompt("leave", "slot_manager")


async def analyze_leave_slots(
    query: str,
    slot_state: Dict[str, Any],
    *,
    temperature: float = 0.0,
    model_name: str = "gpt-4o-mini",
) -> LeaveSlotDecision:
    """LLM을 호출하여 연차 슬롯 상태를 분석한다."""
    payload = {
        "query": query,
        "slot_state": slot_state,
    }
    return await extract_structured_output(
        query=str(payload),
        model_class=LeaveSlotDecision,
        system_prompt=_get_leave_slot_manager_prompt(),
        temperature=temperature,
        model_name=model_name,
    )