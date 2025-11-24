"""LangGraph 기반 회의실 에이전트 워크플로 정의."""

from __future__ import annotations

from typing import Any, Final

from langgraph.graph import END, StateGraph

from .nodes import (
    cancel_node,
    intent_node,
    lookup_node,
    recommend_node,
    reserve_node,
    update_node,
)
from .schemas import AgentState

__all__: list[str] = ["build_meeting_room_graph"]

INTENT_TO_NODE: Final[dict[str, str]] = {
    "예약": "reserve",
    "취소": "cancel",
    "수정": "update",
    "추천": "recommend",
    "조회": "lookup",
}


def _route_intent(state: AgentState) -> str:
    """의도에 따라 다음 노드를 결정한다."""

    intent = (state.get("intent") or "").strip()
    return INTENT_TO_NODE.get(intent, "end")


def build_meeting_room_graph() -> Any:
    """회의실 에이전트용 LangGraph 워크플로를 생성한다."""

    graph = StateGraph(AgentState)
    graph.add_node("intent", intent_node)
    graph.add_node("reserve", reserve_node)
    graph.add_node("cancel", cancel_node)
    graph.add_node("update", update_node)
    graph.add_node("recommend", recommend_node)
    graph.add_node("lookup", lookup_node)

    graph.set_entry_point("intent")

    graph.add_conditional_edges(
        "intent",
        _route_intent,
        {
            "reserve": "reserve",
            "cancel": "cancel",
            "update": "update",
            "recommend": "recommend",
            "lookup": "lookup",
            "end": END,
        },
    )

    graph.add_edge("reserve", END)
    graph.add_edge("cancel", END)
    graph.add_edge("update", END)
    graph.add_edge("recommend", END)
    graph.add_edge("lookup", END)

    return graph.compile()

