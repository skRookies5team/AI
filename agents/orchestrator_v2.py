"""확장 가능한 LangGraph Orchestrator - 워크플로우 레지스트리 기반."""

from __future__ import annotations

from typing import Dict, Any, Optional

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.workflows import registry

# Import legacy simple agents for fallback
from agents.schedule_master import schedule_master
from agents.document_master import document_master
from agents.email_master import email_master
from agents.qa_master import qa_master
from agents.tech_master import tech_master

# Simple agents 등록 (레거시 호환성)
SIMPLE_AGENTS = {
    "simple_schedule": schedule_master,
    "simple_document": document_master,
    "simple_email": email_master,
    "simple_qa": qa_master,
    "simple_tech": tech_master,
}

# --- Router ---

async def route_request(state: OrchestratorState) -> Dict[str, Any]:
    """워크플로우 레지스트리를 기반으로 사용자 의도를 분류."""
    print("--- [Orchestrator] Routing Request ---")
    query = state["query"]
    
    # 등록된 모든 워크플로우 가져오기 (활성화된 것만)
    workflows = registry.get_all(enabled_only=True)
    
    if not workflows:
        print("⚠️ 등록된 워크플로우가 없습니다. simple_qa로 fallback")
        return {"intent": "simple_qa"}
    
    # 라우팅 프롬프트 동적 생성
    routing_prompt = registry.get_routing_prompt()
    
    # Simple agents 추가
    simple_agents_text = "\n".join([
        f"  - '{name}': Simple {name.replace('simple_', '')} agent"
        for name in SIMPLE_AGENTS.keys()
    ])
    
    # 등록된 워크플로우 이름 목록 추가 (명확성을 위해)
    workflow_names = [wf.name for wf in workflows[:20]]  # 최대 20개만 표시
    
    prompt = f"""
    다음 카테고리 중 하나를 선택하세요:
    
{routing_prompt}
    
    Simple Agents (단순 요청용):
{simple_agents_text}
    
    사용자 질의: "{query}"
    
    위 카테고리 중 하나의 이름만 반환하세요 (따옴표 없이).
    사용 가능한 워크플로우: {', '.join(workflow_names[:10])}...
    """
    
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    response = await llm.ainvoke(prompt)
    intent = response.content.strip().lower().strip('"\'')
    
    print(f"Detected Intent: {intent}")
    return {"intent": intent}

# --- Subgraph Wrappers (동적 생성) ---

def create_workflow_run_func(workflow_metadata):
    """워크플로우 메타데이터로부터 실행 함수 생성."""
    async def run_func(state: OrchestratorState) -> Dict[str, Any]:
        if workflow_metadata.run_func:
            return await workflow_metadata.run_func(state)
        else:
            # 기본 실행 함수
            app = workflow_metadata.get_graph_func()
            result = await app.ainvoke(state)
            return result
    return run_func

# --- Fallback Wrappers ---

def run_simple_agent(agent_func):
    """레거시 에이전트를 래핑하는 함수."""
    def wrapper(state: OrchestratorState) -> Dict[str, Any]:
        # Legacy agents are sync, so we wrap them
        res = agent_func(state["query"])
        return {
            "final_response": res.get("message"),
            "messages": ["Simple agent executed."]
        }
    return wrapper

# --- Main Graph ---

def get_orchestrator_graph():
    """레지스트리 기반 동적 오케스트레이터 그래프 생성."""
    workflow = StateGraph(OrchestratorState)
    
    # Router 노드 추가
    workflow.add_node("router", route_request)
    
    # 등록된 모든 워크플로우를 노드로 추가
    workflows = registry.get_all(enabled_only=True)
    workflow_names = []
    workflow_name_set = set()  # 빠른 조회를 위한 set
    
    for wf_meta in workflows:
        node_name = wf_meta.name
        workflow_names.append(node_name)
        workflow_name_set.add(node_name)
        
        # 실행 함수 생성
        run_func = create_workflow_run_func(wf_meta)
        workflow.add_node(node_name, run_func)
        print(f"✅ 노드 등록: {node_name} ({wf_meta.category}, priority={wf_meta.priority})")
    
    # Simple agents 노드 추가
    simple_agent_names = list(SIMPLE_AGENTS.keys())
    for name in simple_agent_names:
        workflow_names.append(name)
        workflow_name_set.add(name)
        workflow.add_node(name, run_simple_agent(SIMPLE_AGENTS[name]))
    
    print(f"📊 총 {len(workflows)}개 워크플로우 + {len(simple_agent_names)}개 Simple Agent 등록 완료")
    
    # Edges
    workflow.set_entry_point("router")
    
    def route_edge(state: OrchestratorState) -> str:
        """라우팅 엣지 함수 - 등록된 모든 워크플로우를 확인."""
        intent = state.get("intent", "simple_qa")
        
        # 등록된 워크플로우 이름 목록과 매칭 (클로저 사용)
        if intent in workflow_name_set:
            return intent
        return "simple_qa"  # 기본 fallback

    workflow.add_conditional_edges(
        "router",
        route_edge
    )
    
    # 모든 워크플로우와 simple agents가 END로 연결
    for node_name in workflow_names:
        workflow.add_edge(node_name, END)
        
    return workflow.compile()

