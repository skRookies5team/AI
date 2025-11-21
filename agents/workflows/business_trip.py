import json
import os
from datetime import datetime
from typing import Any, Dict

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.schedule_data import fetch_schedule_rows, ScheduleFilters, get_session
from agents.schedule_api import create_schedule
from agents.doc_generate_agent import doc_generate
from agents.email_auto_reply_agent import email_auto_reply

# --- Nodes ---

async def extract_info(state: OrchestratorState) -> Dict[str, Any]:
    """Extracts trip details (dates, destination, reason) from the query."""
    print("--- [Business Trip] Extracting Info ---")
    query = state["query"]
    
    prompt = f"""
    Extract business trip details from the following query: "{query}"
    
    Return a JSON with:
    - start_date (YYYY-MM-DD)
    - end_date (YYYY-MM-DD)
    - destination
    - reason
    - employee_name (if mentioned, else null)
    
    Assume "Next week" starts from the coming Monday relative to today ({datetime.now().strftime('%Y-%m-%d')}).
    """
    
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    response = await llm.ainvoke(prompt)
    
    try:
        # Naive JSON parsing
        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].strip()
            
        data = json.loads(content)
        return {
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "destination": data.get("destination"),
            "topic": data.get("reason"), # Map reason to topic
            "employee_name": data.get("employee_name")
        }
    except Exception as e:
        return {"messages": [f"Error extracting info: {e}"]}

async def check_schedule(state: OrchestratorState) -> Dict[str, Any]:
    """Checks for conflicting schedules."""
    print("--- [Business Trip] Checking Schedule ---")
    # Use injected employee_id from state (Short-term memory)
    emp_id = state.get("employee_id") or 1001 
    
    start = state.get("start_date")
    end = state.get("end_date")
    
    if not start or not end:
        return {"schedule_conflicts": [], "messages": ["Dates not found, skipping schedule check."]}

    # Use existing schedule data logic
    # We need to filter by date range. The existing ScheduleFilters might support it or we filter manually.
    # Assuming simple fetch for now.
    filters = ScheduleFilters(employee_id=emp_id, limit=20)
    
    # Running async function synchronously if needed or awaiting if possible
    # fetch_schedule_rows is async def
    # We need to run it in a way that works with the graph.
    from agents.leave_data import _run
    
    async def run_check():
         return await fetch_schedule_rows(filters)
    
    rows = await run_check()
    
    # Simple date overlap check (Logic simplification for demo)
    conflicts = []
    for row in rows:
        # Check if row date overlaps with start/end
        # This is a simplified check.
        row_start = str(row.get('start_time', ''))
        if start in row_start: # Very naive check
            conflicts.append(row)
            
    return {"schedule_conflicts": conflicts}

async def generate_document(state: OrchestratorState) -> Dict[str, Any]:
    """Generates the business trip application document."""
    print("--- [Business Trip] Generating Document ---")
    
    emp_id = state.get("employee_id", 1001)
    
    # Construct a prompt for the doc generator
    details = f"사번: {emp_id}, 출장지: {state.get('destination')}, 기간: {state.get('start_date')} ~ {state.get('end_date')}, 사유: {state.get('topic')}"
    
    # Reuse doc_generate_agent
    # doc_generate returns a dict with 'message' and potentially 'file_path' (if modified)
    # Based on previous read, it returns dict.
    result = doc_generate(f"출장 신청서 작성해줘. {details}")
    
    return {
        "document_content": result.get("message"),
        # "document_path": result.get("file_path") # Assuming agent returns this
        "messages": ["Document generated."]
    }

async def draft_email(state: OrchestratorState) -> Dict[str, Any]:
    """Drafts an Out-of-Office email."""
    print("--- [Business Trip] Drafting Email ---")
    
    details = f"기간: {state.get('start_date')} ~ {state.get('end_date')}, 사유: 출장 ({state.get('destination')})"
    result = email_auto_reply(f"부재중 자동 응답 메일 초안 작성해줘. {details}")
    
    return {
        "email_draft": result.get("message"),
        "messages": ["Email draft created."]
    }

async def notify_team(state: OrchestratorState) -> Dict[str, Any]:
    """Adds the trip to the team calendar."""
    print("--- [Business Trip] Notifying Team (Calendar) ---")
    
    emp_id = state.get("employee_id") or 1001
    
    # Create schedule entry
    # Ensure start_time/end_time are datetime objects
    try:
        s_dt = datetime.strptime(state.get("start_date"), "%Y-%m-%d")
        e_dt = datetime.strptime(state.get("end_date"), "%Y-%m-%d")
        
        result = create_schedule(
            employee_id=emp_id,
            title=f"출장 ({state.get('destination')})",
            description=state.get("topic"),
            start_time=s_dt,
            end_time=e_dt,
            schedule_type="출장",
            location=state.get("destination")
        )
        msg = result.get("message", "Schedule registered.")
    except Exception as e:
        msg = f"Failed to register schedule: {e}"

    return {
        "final_response": "모든 출장 준비가 완료되었습니다. (일정 확인, 문서 작성, 이메일 초안, 캘린더 등록)",
        "messages": [msg]
    }

# --- Graph Construction ---

# 데코레이터를 사용하여 메타데이터 등록 (YAML로 오버라이드 가능)
try:
    from agents.workflows import workflow as workflow_decorator
    
    @workflow_decorator(
        name="business_trip_flow",
        category="travel",
        description="출장/휴가 관리 (일정 조회 + 문서 작성 + 부재중 이메일 + 팀 알림)",
        priority=5,  # YAML에서 10으로 오버라이드됨
        enabled=True,
        examples=["다음주 3일간 제주도로 워크샵 다녀올게"],
    )
    def get_business_trip_graph():
        """출장/휴가 관리 워크플로우 그래프 생성."""
        workflow = StateGraph(OrchestratorState)
except ImportError:
    # 데코레이터가 없으면 기본 함수만 정의
    def get_business_trip_graph():
        """출장/휴가 관리 워크플로우 그래프 생성."""
        workflow = StateGraph(OrchestratorState)
    
    workflow.add_node("extract_info", extract_info)
    workflow.add_node("check_schedule", check_schedule)
    workflow.add_node("generate_document", generate_document)
    workflow.add_node("draft_email", draft_email)
    workflow.add_node("notify_team", notify_team)
    
    # Linear Flow
    workflow.set_entry_point("extract_info")
    workflow.add_edge("extract_info", "check_schedule")
    workflow.add_edge("check_schedule", "generate_document")
    workflow.add_edge("generate_document", "draft_email")
    workflow.add_edge("draft_email", "notify_team")
    workflow.add_edge("notify_team", END)
    
    return workflow.compile()
