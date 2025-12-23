"""워크플로우 핸들러 모듈.

각 워크플로우 타입별로 단계 관리 및 처리 로직을 담당합니다.
"""

from typing import Dict, Optional, Type
from agents.workflows.base import BaseWorkflowHandler
from agents.workflows.business_trip_handler import BusinessTripWorkflowHandler

# 워크플로우 핸들러 등록
WORKFLOW_HANDLERS: Dict[str, Type[BaseWorkflowHandler]] = {
    "business_trip": BusinessTripWorkflowHandler,
    # 향후 추가 워크플로우
    # "recruitment": RecruitmentWorkflowHandler,
    # "meeting_preparation": MeetingPreparationWorkflowHandler,
}


def get_workflow_handler(workflow_type: str) -> Optional[BaseWorkflowHandler]:
    """워크플로우 타입에 맞는 핸들러 인스턴스를 반환합니다.
    
    Args:
        workflow_type: 워크플로우 타입 (예: "business_trip")
        
    Returns:
        워크플로우 핸들러 인스턴스 또는 None
    """
    handler_class = WORKFLOW_HANDLERS.get(workflow_type)
    if handler_class:
        return handler_class()
    return None


def register_workflow_handler(workflow_type: str, handler_class: Type[BaseWorkflowHandler]):
    """새로운 워크플로우 핸들러를 등록합니다.
    
    Args:
        workflow_type: 워크플로우 타입
        handler_class: 워크플로우 핸들러 클래스
    """
    WORKFLOW_HANDLERS[workflow_type] = handler_class


