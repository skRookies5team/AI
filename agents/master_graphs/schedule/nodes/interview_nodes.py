"""면접 일정 전용 노드."""

from __future__ import annotations

from typing import Any, Dict

from agents.master_graphs.schedule.nodes.schedule_nodes import process_interview_scheduling
from agents.state import OrchestratorState


async def process_interview_request(state: OrchestratorState) -> Dict[str, Any]:
    """면접 일정 관련 요청을 처리한다.

    Args:
        state: 오케스트레이터 상태.

    Returns:
        면접 처리 결과 딕셔너리.
    """
    return await process_interview_scheduling(state)







