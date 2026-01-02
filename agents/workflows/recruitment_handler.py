"""채용 프로세스 워크플로우 핸들러."""

from typing import Dict, Any, Optional
from agents.state import OrchestratorState
from agents.workflows.base import BaseWorkflowHandler


class RecruitmentWorkflowHandler(BaseWorkflowHandler):
    """채용 프로세스 워크플로우 핸들러.
    
    워크플로우 단계:
    0: recruitment - 이력서 검색 및 선택
    1: schedule - 면접 일정 및 면접관 설정
    2: email - 면접 안내 메일 발송
    3: document - 면접 평가표 생성
    """
    
    @property
    def workflow_type(self) -> str:
        return "recruitment"
    
    async def handle_step(
        self,
        step_index: int,
        state: OrchestratorState,
        query: str,
        domain: str
    ) -> Dict[str, Any]:
        """채용 워크플로우 단계를 처리합니다.
        
        Args:
            step_index: 현재 워크플로우 단계 인덱스 (0부터 시작)
            state: 오케스트레이터 상태
            query: 사용자 쿼리
            domain: 현재 도메인
            
        Returns:
            처리 결과 딕셔너리
        """
        # Step 0: recruitment - 이력서 검색 및 선택
        if step_index == 0:
            if domain != "recruitment":
                 return {
                    "final_response": f"❌ 워크플로우 단계 오류: Step 1은 recruitment 도메인에서 처리되어야 합니다. (현재: {domain})",
                    "messages": [f"워크플로우 단계 오류: Step 1은 recruitment 도메인에서 처리되어야 합니다."],
                    "requires_input": False,
                }
            # recruitment/master_graph.py에서 처리됨 (라우팅만 확인)
            return {
                "message": "이력서 검색을 진행합니다.",
                "next_domain": "recruitment"
            }
        
        # Step 1: schedule - 면접 일정 및 면접관 설정
        elif step_index == 1:
             # schedule/master_graph.py에서 처리됨
            return {
                "message": "면접 일정 설정을 진행합니다.",
                "next_domain": "schedule"
            }
        
        # Step 2: email - 면접 안내 메일 발송
        elif step_index == 2:
            # email/master_graph.py에서 처리됨
            # compose 모드로 라우팅
            return {
                "message": "면접 안내 메일 작성을 진행합니다.",
                "next_domain": "email"
            }
            
        # Step 3: document - 면접 평가표 생성
        elif step_index == 3:
            # document/master_graph.py에서 처리됨
            return {
                "message": "면접 평가표 생성을 진행합니다.",
                "next_domain": "document"
            }
        
        else:
            return {
                "final_response": f"❌ 워크플로우 단계 오류: 유효하지 않은 단계 인덱스 {step_index}",
                "messages": [f"워크플로우 단계 오류: 유효하지 않은 단계 인덱스 {step_index}"],
                "requires_input": False,
            }
