"""면접 평가표 데이터 추출기."""

from typing import Any, Dict, Optional
from agents.state import OrchestratorState

async def extract_interview_evaluation_data(state: OrchestratorState) -> Dict[str, Any]:
    """워크플로우 임시 데이터에서 면접 평가표에 필요한 데이터를 추출합니다."""
    workflow_temp_data = state.get("workflow_temp_data", {}) or {}
    candidate = workflow_temp_data.get("selected_candidate", {})
    interview_date = workflow_temp_data.get("interview_date", "")
    interviewers = workflow_temp_data.get("interviewers", [])
    
    # 면접관 이름 목록 생성 (사원 ID만 있는 경우 fallback 처리)
    interviewer_names = []
    if interviewers:
        # 실제 환경에서는 DB에서 이름을 가져와야 하지만, 
        # 일단은 저장된 정보를 활용하거나 기본값 설정
        interviewer_names = [str(i) for i in interviewers]
    
    return {
        "candidate_name": candidate.get("name", ""),
        "applied_position": candidate.get("position", ""),
        "interview_date": interview_date,
        "interviewer_names": ", ".join(interviewer_names),
        "candidate_experience": candidate.get("experience", ""),
        "candidate_skills": ", ".join(candidate.get("skills", [])),
        "education": candidate.get("education", ""),
        "candidate_email": candidate.get("email", ""),
        "candidate_phone": candidate.get("phone", "")
    }

