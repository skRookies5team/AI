import json
from typing import Any, Dict

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.tech_master import tech_master
from agents.doc_generate_agent import doc_generate
from agents.email_auto_reply_agent import email_auto_reply

# --- Nodes ---

async def search_tech(state: OrchestratorState) -> Dict[str, Any]:
    """Searches for patents and papers."""
    print("--- [Research] Searching Tech ---")
    query = state["query"]
    
    # tech_master handles routing to patent or paper agents
    # It returns a dict with results.
    result = tech_master(query)
    
    # Store results for the report
    # Assuming result['message'] contains the summary or list
    return {
        "search_results": [result], # Store raw result
        "summary_text": result.get("message", ""),
        "messages": ["Tech search completed."]
    }

async def summarize_and_vectorize(state: OrchestratorState) -> Dict[str, Any]:
    """Summarizes the search results (Mocking vectorization if not explicit)."""
    print("--- [Research] Summarizing ---")
    
    # In the real app, tech_master might have already done RAG.
    # Here we ensure we have a clean summary for the report.
    
    context = state.get("summary_text", "")
    if not context:
        return {"messages": ["No search results to summarize."]}
        
    prompt = f"""
    Summarize the following technical search results into a key insight report format:
    
    {context}
    """
    
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    response = await llm.ainvoke(prompt)
    
    return {
        "summary_text": response.content,
        "messages": ["Summary generated."]
    }

async def create_report(state: OrchestratorState) -> Dict[str, Any]:
    """Generates a technical trend report."""
    print("--- [Research] Creating Report ---")
    
    summary = state.get("summary_text")
    prompt = f"기술 동향 보고서 작성해줘. 내용은 다음과 같아: {summary}"
    
    result = doc_generate(prompt)
    
    return {
        "document_content": result.get("message"),
        "messages": ["Report generated."]
    }

async def share_result(state: OrchestratorState) -> Dict[str, Any]:
    """Drafts an email to share the report."""
    print("--- [Research] Sharing Result ---")
    
    prompt = "팀장님께 기술 동향 보고서 공유하는 메일 초안 작성해줘."
    result = email_auto_reply(prompt)
    
    return {
        "email_draft": result.get("message"),
        "final_response": "기술 동향 조사 및 보고서 작성이 완료되었습니다. 이메일 초안을 확인해주세요.",
        "messages": ["Email draft for report sharing created."]
    }

# --- Graph Construction ---

try:
    from agents.workflows import workflow as workflow_decorator
    
    @workflow_decorator(
        name="research_flow",
        category="research",
        description="R&D 인사이트 및 리포트 자동화 (특허/논문 검색 + 벡터 DB 저장 + 리포트 생성 + 공유)",
        priority=5,  # YAML에서 10으로 오버라이드됨
        enabled=True,
        examples=["생성형 AI 최신 특허 동향 조사해줘", "R&D 리포트 작성"],
    )
    def get_research_graph():
        """R&D 리서치 워크플로우 그래프 생성."""
        workflow = StateGraph(OrchestratorState)
except ImportError:
    def get_research_graph():
        """R&D 리서치 워크플로우 그래프 생성."""
        workflow = StateGraph(OrchestratorState)
    
    workflow.add_node("search_tech", search_tech)
    workflow.add_node("summarize_and_vectorize", summarize_and_vectorize)
    workflow.add_node("create_report", create_report)
    workflow.add_node("share_result", share_result)
    
    workflow.set_entry_point("search_tech")
    workflow.add_edge("search_tech", "summarize_and_vectorize")
    workflow.add_edge("summarize_and_vectorize", "create_report")
    workflow.add_edge("create_report", "share_result")
    workflow.add_edge("share_result", END)
    
    return workflow.compile()

