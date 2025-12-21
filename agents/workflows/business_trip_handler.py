"""출장 워크플로우 핸들러."""

from typing import Dict, Any, Optional
from agents.state import OrchestratorState
from agents.workflows.base import BaseWorkflowHandler


class BusinessTripWorkflowHandler(BaseWorkflowHandler):
    """출장 워크플로우 핸들러.
    
    워크플로우 단계:
    0: schedule - 일정 조회 및 날짜 확인
    1: document - 출장 신청서 작성
    2: email - 부재중 자동 응답 초안 생성
    3: schedule - 캘린더 등록 및 RDB 저장
    """
    
    @property
    def workflow_type(self) -> str:
        return "business_trip"
    
    async def handle_step(
        self,
        step_index: int,
        state: OrchestratorState,
        query: str,
        domain: str
    ) -> Dict[str, Any]:
        """출장 워크플로우 단계를 처리합니다.
        
        Args:
            step_index: 현재 워크플로우 단계 인덱스 (0부터 시작)
            state: 오케스트레이터 상태
            query: 사용자 쿼리
            domain: 현재 도메인
            
        Returns:
            처리 결과 딕셔너리
        """
        employee_id = state.get("employee_id")
        user_id = state.get("user_id") or str(employee_id) if employee_id else None
        
        # Step 0: schedule - 일정 조회 및 날짜 확인
        if step_index == 0:
            if domain != "schedule":
                return {
                    "final_response": f"❌ 워크플로우 단계 오류: Step 1은 schedule 도메인에서 처리되어야 합니다. (현재: {domain})",
                    "messages": [f"워크플로우 단계 오류: Step 1은 schedule 도메인에서 처리되어야 합니다."],
                    "requires_input": False,
                }
            # 순환 import 방지를 위해 lazy import
            from agents.master_graphs.schedule.nodes.business_trip_nodes import (
                _handle_workflow_step1_check_conflicts,
            )
            return await _handle_workflow_step1_check_conflicts(
                state, query, employee_id, user_id
            )
        
        # Step 1: document - 출장 신청서 작성
        elif step_index == 1:
            # document 도메인으로 라우팅 (여기서는 처리하지 않음)
            # document/master_graph.py에서 처리됨
            return {
                "final_response": "출장 신청서 작성을 진행합니다.",
                "messages": ["출장 신청서 작성을 진행합니다."],
                "next_domain": "document",
                "requires_input": False,
            }
        
        # Step 2: email - 부재중 자동 응답 초안 생성
        elif step_index == 2:
            # email 도메인으로 라우팅 (여기서는 처리하지 않음)
            # email/master_graph.py에서 처리됨
            return {
                "final_response": "부재중 자동 응답 초안을 생성합니다.",
                "messages": ["부재중 자동 응답 초안을 생성합니다."],
                "next_domain": "email",
                "requires_input": False,
            }
        
        # Step 3: schedule - 캘린더 등록 및 RDB 저장
        elif step_index == 3:
            if domain != "schedule":
                return {
                    "final_response": f"❌ 워크플로우 단계 오류: Step 4는 schedule 도메인에서 처리되어야 합니다. (현재: {domain})",
                    "messages": [f"워크플로우 단계 오류: Step 4는 schedule 도메인에서 처리되어야 합니다."],
                    "requires_input": False,
                }
            # 순환 import 방지를 위해 lazy import
            from agents.master_graphs.schedule.nodes.business_trip_nodes import (
                _handle_workflow_step4_register_calendar,
            )
            return await _handle_workflow_step4_register_calendar(
                state, query, employee_id, user_id
            )
        
        else:
            return {
                "final_response": f"❌ 워크플로우 단계 오류: 유효하지 않은 단계 인덱스 {step_index}",
                "messages": [f"워크플로우 단계 오류: 유효하지 않은 단계 인덱스 {step_index}"],
                "requires_input": False,
            }

