"""마스터그래프 베이스 클래스 및 공통 유틸리티."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState


class BaseMasterGraph(ABC):
    """모든 마스터그래프가 상속받는 베이스 클래스.
    
    각 도메인별 마스터그래프는 이 클래스를 상속받아 구현합니다.
    LangGraph의 강점을 살려서 도구 선택, 다음 노드 결정, Human-in-the-loop 등을 처리합니다.
    """
    
    @abstractmethod
    def get_graph(self) -> StateGraph:
        """LangGraph StateGraph 인스턴스를 반환.
        
        Returns:
            컴파일된 StateGraph
        """
        pass
    
    @abstractmethod
    def get_domain_name(self) -> str:
        """도메인 이름 반환 (예: 'schedule', 'document', 'qa')."""
        pass
    
    def create_graph(self) -> StateGraph:
        """기본 그래프 생성 (서브클래스에서 오버라이드 가능)."""
        return StateGraph(OrchestratorState)
    
    async def route_intent(
        self, 
        state: OrchestratorState
    ) -> Dict[str, Any]:
        """도메인 내부 의도 분류 (서브그래프 라우팅).
        
        각 마스터그래프 내에서 여러 하위 에이전트 중 어떤 것을 호출할지 결정합니다.
        
        Args:
            state: 오케스트레이터 상태
            
        Returns:
            intent: 하위 에이전트 식별자
        """
        # 기본 구현: query를 기반으로 LLM으로 분류
        from langchain_openai import ChatOpenAI
        
        query = state.get("query", "")
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        
        prompt = f"""
        사용자 질의를 분석하여 {self.get_domain_name()} 도메인 내에서 적절한 하위 작업을 선택하세요.
        
        질의: "{query}"
        
        가능한 작업 목록은 각 마스터그래프 구현에서 정의됩니다.
        """
        
        response = await llm.ainvoke(prompt)
        intent = response.content.strip().lower()
        
        return {"intent": intent}


def create_master_graph_template(
    domain_name: str,
    nodes: Dict[str, Any],
    edges: list[tuple[str, str]],
    entry_point: str = "router"
) -> StateGraph:
    """마스터그래프 템플릿 생성 함수.
    
    Args:
        domain_name: 도메인 이름
        nodes: 노드 딕셔너리 {node_name: node_function}
        edges: 엣지 리스트 [(from_node, to_node), ...]
        entry_point: 진입점 노드 이름
        
    Returns:
        컴파일된 StateGraph
    """
    workflow = StateGraph(OrchestratorState)
    
    # 노드 추가
    for node_name, node_func in nodes.items():
        workflow.add_node(node_name, node_func)
    
    # 엣지 추가
    workflow.set_entry_point(entry_point)
    for from_node, to_node in edges:
        if to_node == "END":
            workflow.add_edge(from_node, END)
        else:
            workflow.add_edge(from_node, to_node)
    
    return workflow.compile()

