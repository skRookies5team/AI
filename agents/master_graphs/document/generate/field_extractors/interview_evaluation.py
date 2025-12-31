"""면접 평가표 템플릿 필드 추출 모듈."""

import re
from datetime import datetime
from typing import Any, Dict, Optional, List
from sqlalchemy import text
from agents.master_graphs.schedule.services.database_service import DatabaseService


async def extract_interview_evaluation_data(state: Dict[str, Any]) -> Dict[str, Any]:
    """면접 평가표용 데이터를 추출합니다.

    Args:
        state: OrchestratorState

    Returns:
        면접 평가표에 사용할 데이터 딕셔너리
    """
    print("🎯 [면접 평가표] 데이터 추출 시작")

    # workflow_temp_data에서 정보 추출
    workflow_temp_data = state.get("workflow_temp_data", {}) or {}

    # 1. 후보자 정보 추출
    candidate = workflow_temp_data.get("selected_candidate", {})
    candidate_name = candidate.get("name", "")
    candidate_position = candidate.get("position", "")
    phone = candidate.get("phone", "")
    email = candidate.get("email", "")
    experience = candidate.get("experience", "")
    education = candidate.get("education", "")

    print(f"✅ 후보자 정보 추출: {candidate_name} ({candidate_position})")

    # 2. 면접관 정보 추출
    interviewers = workflow_temp_data.get("interviewers", [])
    interviewer_data = await _fetch_interviewer_info(interviewers)

    # 면접관 정보 가공 (모든 면접관 정보 사용)
    print(f"✅ 면접관 정보 추출: 총 {len(interviewer_data)}명")
    for i, interviewer in enumerate(interviewer_data, 1):
        name = interviewer.get("name", "")
        dept = interviewer.get("dept", "")
        interviewer_pos = interviewer.get("position", "")
        print(f"  - 면접관 {i}: {name} ({dept} {interviewer_pos})")

    # 3. 면접 일정 정보 추출
    interview_date = workflow_temp_data.get("interview_date", "")
    interview_duration = workflow_temp_data.get("interview_duration", "")

    # HTML 템플릿에 맞는 형식으로 데이터 반환
    return {
        "candidate_name": candidate_name,
        "position": candidate_position,
        "phone": phone,
        "email": email,
        "experience": experience,
        "education": education,
        "interview_date": interview_date,
        "interview_duration": interview_duration,
        "interview_type": "1차면접",  # 기본값
        "interviewer_data": interviewer_data,  # 전체 면접관 리스트
    }


async def _fetch_interviewer_info(interviewer_ids: List[int]) -> List[Dict[str, Any]]:
    """면접관 ID 리스트로 DB에서 면접관 정보를 조회합니다.

    Args:
        interviewer_ids: 면접관 사원 번호 리스트

    Returns:
        면접관 정보 리스트
    """
    if not interviewer_ids:
        print("⚠️ 면접관 ID가 없습니다.")
        return []

    try:
        db = DatabaseService()
        interviewers = []

        async with db._session() as session:
            for emp_id in interviewer_ids:
                sql = text("""
                    SELECT employee_id, name, dept, position
                    FROM employee
                    WHERE employee_id = :employee_id
                """)
                result = await session.execute(sql, {"employee_id": emp_id})
                row = result.fetchone()

                if row:
                    interviewers.append({
                        "employee_id": row.employee_id,
                        "name": row.name,
                        "dept": row.dept,
                        "position": row.position
                    })
                    print(f"✅ 면접관 조회 성공: {row.name} (사번: {row.employee_id})")
                else:
                    print(f"⚠️ 면접관 조회 실패: 사번 {emp_id}에 해당하는 정보가 없습니다.")

        return interviewers

    except Exception as e:
        print(f"❌ 면접관 정보 조회 실패: {e}")
        import traceback
        traceback.print_exc()
        return []


def populate_interview_evaluation_form(html_content: str, data: Dict[str, Any]) -> str:
    """면접 평가표 HTML에 데이터를 채워 넣습니다.

    Args:
        html_content: 원본 HTML 템플릿
        data: 채워 넣을 데이터

    Returns:
        데이터가 채워진 HTML
    """
    import json
    import re

    # 지원자 정보 치환 (placeholder 유지하면서 value 추가)
    replacements = {
        'placeholder="지원자 성명"': f'placeholder="지원자 성명" value="{data.get("candidate_name", "")}"',
        'placeholder="지원 직무"': f'placeholder="지원 직무" value="{data.get("position", "")}"',
        'placeholder="연락처"': f'placeholder="연락처" value="{data.get("phone", "")}"',
        'placeholder="이메일"': f'placeholder="이메일" value="{data.get("email", "")}"',
        'placeholder="경력"': f'placeholder="경력" value="{data.get("experience", "")}"',
        'placeholder="학력"': f'placeholder="학력" value="{data.get("education", "")}"',
    }

    result_html = html_content
    for placeholder, replacement in replacements.items():
        result_html = result_html.replace(placeholder, replacement)

    # 면접관 데이터를 JavaScript 변수로 추가 (강화된 로직)
    interviewer_data = data.get("interviewer_data", [])
    if interviewer_data:
        # JavaScript 변수 추가 (HTML head 태그 끝나기 전에 삽입)
        js_script = f"\n    <script>\n        window.interviewerData = {json.dumps(interviewer_data, ensure_ascii=False)};\n    </script>\n"
        
        # 대소문자 무관하게 </head> 태그 찾기
        head_tag_pattern = re.compile(r'</\s*head\s*>', re.IGNORECASE)
        if head_tag_pattern.search(result_html):
            result_html = head_tag_pattern.sub(f"{js_script}</head>", result_html)
            print("✅ [HTML 주입] </head> 태그 앞에 스크립트 주입 완료")
        else:
            # </head> 태그가 없으면 맨 뒤에라도 추가
            result_html += js_script
            print("⚠️ [HTML 주입] </head> 태그를 찾지 못해 문서 끝에 스크립트 추가")

    return result_html
