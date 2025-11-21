from typing import TypedDict, Annotated, Optional, List, Dict, Any
import operator

class OrchestratorState(TypedDict):
    """
    Shared state for the LangGraph Orchestrator.
    """
    # Input
    query: str
    user_id: Optional[str]
    session_id: Optional[str]
    
    # Workflow Context
    intent: Optional[str]  # 'business_trip', 'research', 'meeting', etc.
    current_step: str
    
    # Extracted Data (Entities)
    employee_id: Optional[int]
    employee_name: Optional[str]
    start_date: Optional[str]
    end_date: Optional[str]
    destination: Optional[str]
    topic: Optional[str] # For research or meeting
    participants: List[str]
    
    # Intermediate Artifacts
    schedule_conflicts: List[Dict[str, Any]]
    document_content: Optional[str]
    document_path: Optional[str]
    email_draft: Optional[str]
    search_results: List[Dict[str, Any]] # For patents/papers
    summary_text: Optional[str]
    
    # Final Output
    final_response: Optional[str]
    messages: Annotated[List[str], operator.add]

