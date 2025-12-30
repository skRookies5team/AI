"""면접 평가표 데이터 추출기."""

from typing import Any, Dict, Optional
from agents.state import OrchestratorState

async def extract_interview_evaluation_data(state: OrchestratorState) -> Dict[str, Any]:
    """워크플로우 임시 데이터에서 면접 평가표에 필요한 데이터를 추출합니다."""
    workflow_temp_data = state.get("workflow_temp_data", {}) or {}
    candidate = workflow_temp_data.get("selected_candidate", {})
    interview_date = workflow_temp_data.get("interview_date", "")
    interviewers = workflow_temp_data.get("interviewers", [])
    
    # 면접관 이름 목록 생성
    interviewer_names = []
    
    # 1. 상세 정보가 있는 경우 (이름, 부서, 직급 포함)
    interviewers_detail = workflow_temp_data.get("interviewers_detail", [])
    if interviewers_detail:
        for inv in interviewers_detail:
            name = inv.get("name")
            dept = inv.get("dept")
            pos = inv.get("position")
            if name:
                info = name
                if dept or pos:
                    info += f"({dept or ''} {pos or ''})".replace("  ", " ")
                interviewer_names.append(info)
    
    # 2. 상세 정보가 없고 ID만 있는 경우
    elif interviewers:
        interviewer_names = [f"면접관(ID: {i})" for i in interviewers]
    
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

