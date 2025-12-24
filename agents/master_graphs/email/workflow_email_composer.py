"""워크플로우 모드 이메일 초안 생성 모듈.

워크플로우 모드에서 팀원들에게 출장 알림 이메일을 생성하는 기능을 제공합니다.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from datetime import datetime

from agents.master_graphs.email.email_compose_agent import generate_email_draft
from agents.master_graphs.email.email_search_agent import _extract_recipient_info_with_llm


async def generate_workflow_email_draft(
    workflow_temp_data: Dict[str, Any],
    employee_id: Optional[int],
    user_dept: Optional[str] = None,
) -> Dict[str, Any]:
    """워크플로우 모드에서 팀원들에게 보낼 출장 알림 이메일 초안을 생성합니다.
    
    Args:
        workflow_temp_data: 워크플로우 임시 데이터
            - start_time: 출장 시작 일시 (ISO 형식 문자열)
            - end_time: 출장 종료 일시 (ISO 형식 문자열)
            - destination: 출장 목적지
            - description: 출장 사유/목적
        employee_id: 현재 사원 ID
        user_dept: 현재 사원의 부서명 (없으면 자동 조회)
        
    Returns:
        {
            "to_email": str,  # 팀원 이메일 주소 (쉼표로 구분)
            "subject": str,    # 이메일 제목
            "body": str,       # 이메일 본문
            "error": Optional[str]  # 에러 메시지 (있는 경우)
        }
    """
    # workflow_temp_data에서 출장 정보 가져오기
    start_time_str = workflow_temp_data.get("start_time")
    end_time_str = workflow_temp_data.get("end_time")
    destination = workflow_temp_data.get("destination", "")
    description = workflow_temp_data.get("description", "출장")
    
    if not start_time_str or not end_time_str:
        return {
            "to_email": "",
            "subject": "",
            "body": "",
            "error": "출장 정보가 없습니다. 워크플로우를 처음부터 다시 시작해주세요."
        }
    
    # 날짜 파싱
    try:
        start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
    except Exception as e:
        return {
            "to_email": "",
            "subject": "",
            "body": "",
            "error": f"날짜 정보를 파싱할 수 없습니다: {e}"
        }
    
    # employee_id로 팀원 정보 조회
    if not user_dept and employee_id:
        try:
            from agents.master_graphs.schedule.services.database_service import DatabaseService
            from sqlalchemy import text
            
            db = DatabaseService()
            async with db._session() as session:
                sql = "SELECT dept FROM ai_agent.employee WHERE employee_id = :employee_id"
                result = await session.execute(text(sql), {"employee_id": employee_id})
                row = result.fetchone()
                if row:
                    user_dept = row.dept
                    print(f"[DEBUG] [Workflow Email] user_dept 조회 성공: {user_dept}")
        except Exception as e:
            print(f"⚠️ 팀원 정보 조회 실패: {e}")
    
    # 팀원 이메일 주소 추출 (일반 요청과 동일한 방식)
    recipient_info = await _extract_recipient_info_with_llm(
        query="우리팀한테",  # 워크플로우에서는 팀원들에게 자동 발송
        employee_id=employee_id,
        user_dept=user_dept
    )
    
    to_emails = recipient_info.get("to_emails", [])
    to_email = ", ".join(to_emails) if to_emails else ""
    
    if not to_email:
        return {
            "to_email": "",
            "subject": "",
            "body": "",
            "error": "팀원 이메일 주소를 찾을 수 없습니다."
        }
    
    # 이메일 초안 생성 (LLM 사용)
    try:
        # 사용자 메시지 구성
        user_message = f"""다음 주 3일간 {destination}로 출장 다녀올게요.
        
출장 기간: {start_time.strftime('%Y년 %m월 %d일')}부터 {end_time.strftime('%Y년 %m월 %d일')}까지
출장 목적지: {destination}
출장 사유: {description}

팀원들에게 출장으로 인해 부재 중임을 알리는 이메일을 작성해주세요."""
        
        # generate_email_draft 함수 사용 (LLM 기반)
        subject, body = await generate_email_draft(
            to_email=to_email,
            user_message=user_message,
            original_subject=None,
            original_sender=None,
            original_body=None,
            is_revision=False,
            feedback=None,
            current_draft_subject=None,
            current_draft_body=None,
        )
        
        return {
            "to_email": to_email,
            "subject": subject,
            "body": body,
            "error": None
        }
        
    except Exception as e:
        print(f"⚠️ 이메일 초안 생성 실패: {e}")
        # LLM 실패 시 fallback: 직접 문자열 생성
        fallback_subject = f"출장 알림: {start_time.strftime('%Y년 %m월 %d일')} ~ {end_time.strftime('%Y년 %m월 %d일')}"
        fallback_body = f"""안녕하세요, 팀 여러분.

저는 {start_time.strftime('%Y년 %m월 %d일')}부터 {end_time.strftime('%Y년 %m월 %d일')}까지 {destination} 출장으로 인해 부재 중입니다.

{description}

필요한 사항이 있으시면 이메일로 연락 주시면 감사하겠습니다.

감사합니다.
[당신의 이름]"""
        
        return {
            "to_email": to_email,
            "subject": fallback_subject,
            "body": fallback_body,
            "error": None
        }

