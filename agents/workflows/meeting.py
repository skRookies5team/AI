import json
from datetime import datetime
from typing import Any, Dict

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.schedule_api import create_schedule
from agents.qa_master import qa_master
from agents.email_auto_reply_agent import email_auto_reply

# --- Nodes ---

async def extract_meeting_info(state: OrchestratorState) -> Dict[str, Any]:
    """Extracts meeting details."""
    print("--- [Meeting] Extracting Info ---")
    query = state["query"]
    
    prompt = f"""
    Extract meeting details from: "{query}"
    
    Return JSON:
    - topic
    - start_time (YYYY-MM-DD HH:MM)
    - duration_minutes (int)
    - participants (list of names)
    
    Assume today is {datetime.now().strftime('%Y-%m-%d')}.
    """
    
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    response = await llm.ainvoke(prompt)
    
    try:
        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].strip()
        data = json.loads(content)
        return {
            "topic": data.get("topic"),
            "start_date": data.get("start_time"), # Using start_date field for timestamp
            "participants": data.get("participants", [])
        }
    except Exception:
        return {"messages": ["Failed to extract meeting info."]}

async def book_room(state: OrchestratorState) -> Dict[str, Any]:
    """Books a meeting room."""
    print("--- [Meeting] Booking Room ---")
    
    emp_id = state.get("employee_id") or 1001
    topic = state.get("topic", "회의")
    start_str = state.get("start_date")
    
    if not start_str:
        return {"messages": ["Start time missing."]}
        
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        # Assume 1 hour duration
        from datetime import timedelta
        end_dt = start_dt + timedelta(hours=1)
        
        # Mock logic: Create schedule for Room A
        # In real app, use meeting_room_agent
        result = create_schedule(
            employee_id=emp_id, # Use injected employee_id
            title=f"[회의] {topic}",
            description="회의실 예약",
            start_time=start_dt,
            end_time=end_dt,
            schedule_type="회의",
            location="회의실 A"
        )
        msg = result.get("message")
    except Exception as e:
        msg = f"Booking failed: {e}"

    return {"messages": [msg]}

async def prepare_materials(state: OrchestratorState) -> Dict[str, Any]:
    """Searches for previous project docs."""
    print("--- [Meeting] Preparing Materials ---")
    
    topic = state.get("topic", "")
    # Use QA agent to find related docs
    result = qa_master(f"{topic} 관련 이전 프로젝트 문서나 자료 찾아줘")
    
    return {
        "summary_text": result.get("message"),
        "messages": ["Materials prepared."]
    }

async def draft_invite(state: OrchestratorState) -> Dict[str, Any]:
    """Drafts an invitation email."""
    print("--- [Meeting] Drafting Invite ---")
    
    topic = state.get("topic")
    start = state.get("start_date")
    materials = state.get("summary_text", "")[:100] + "..." # Truncate for prompt
    
    prompt = f"회의 초대장 메일 써줘. 주제: {topic}, 시간: {start}, 참고자료 요약: {materials}"
    result = email_auto_reply(prompt)
    
    return {
        "email_draft": result.get("message"),
        "messages": ["Invite drafted."]
    }

async def recommend_snacks(state: OrchestratorState) -> Dict[str, Any]:
    """Recommends snacks based on time."""
    print("--- [Meeting] Recommending Snacks ---")
    
    start_str = state.get("start_date", "")
    # Simple logic or QA call
    if "14:00" in start_str or "2 PM" in start_str:
        recommendation = "점심 직후이니 가벼운 아메리카노와 쿠키를 추천합니다."
    else:
        recommendation = "회의 간식으로 샌드위치와 주스를 추천합니다."
        
    return {
        "final_response": f"회의 준비가 완료되었습니다. (회의실 예약, 자료 준비, 초대장 초안). \n간식 추천: {recommendation}",
        "messages": ["Snacks recommended."]
    }

# --- Graph Construction ---

try:
    from agents.workflows import workflow as workflow_decorator
    
    @workflow_decorator(
        name="meeting_flow",
        category="meeting",
        description="스마트 회의 준비 비서 (회의실 예약 + 자료 준비 + 초대장 발송 + 간식 추천)",
        priority=5,  # YAML에서 10으로 오버라이드됨
        enabled=True,
        examples=["다음 주 월요일 오후 2시에 신규 프로젝트 킥오프 회의 잡아줘"],
    )
    def get_meeting_graph():
        """회의 준비 워크플로우 그래프 생성."""
        workflow = StateGraph(OrchestratorState)
except ImportError:
    def get_meeting_graph():
        """회의 준비 워크플로우 그래프 생성."""
        workflow = StateGraph(OrchestratorState)
    
    workflow.add_node("extract_meeting_info", extract_meeting_info)
    workflow.add_node("book_room", book_room)
    workflow.add_node("prepare_materials", prepare_materials)
    workflow.add_node("draft_invite", draft_invite)
    workflow.add_node("recommend_snacks", recommend_snacks)
    
    workflow.set_entry_point("extract_meeting_info")
    workflow.add_edge("extract_meeting_info", "book_room")
    workflow.add_edge("book_room", "prepare_materials")
    workflow.add_edge("prepare_materials", "draft_invite")
    workflow.add_edge("draft_invite", "recommend_snacks")
    workflow.add_edge("recommend_snacks", END)
    
    return workflow.compile()
