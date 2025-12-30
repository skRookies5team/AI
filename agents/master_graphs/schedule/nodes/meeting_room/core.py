"""회의실 관리 엔트리 모듈.

`meeting_room_nodes.py`의 기존 구현을 래핑하여 점진적으로 기능을
세분화/이전할 수 있는 구조를 제공합니다.
"""

from __future__ import annotations

from typing import Any, Dict

from agents.master_graphs.schedule.nodes import meeting_room_nodes
from agents.state import OrchestratorState

__all__ = ["process_meeting_room_request"]


async def process_meeting_room_request(state: OrchestratorState) -> Dict[str, Any]:
    """회의실 관련 요청을 처리한다.

    Args:
        state: 오케스트레이터 상태.

    Returns:
        회의실 처리 결과 딕셔너리.
    """
    return await meeting_room_nodes.process_meeting_room_request(state)

