"""워크플로우 핸들러 모듈.

각 워크플로우 타입별로 단계 관리 및 처리 로직을 담당합니다.
"""

from typing import Dict, Optional, Type
from agents.workflows.base import BaseWorkflowHandler
from agents.workflows.generic_handler import GenericWorkflowHandler

def get_workflow_handler(workflow_type: str) -> Optional[BaseWorkflowHandler]:
    """워크플로우 타입에 맞는 범용 핸들러를 반환합니다.
    
    Args:
        workflow_type: 워크플로우 타입 (예: "business_trip", "recruitment")
        
    Returns:
        워크플로우 핸들러 인스턴스 또는 None
    """
    if workflow_type == "recruitment":
        from agents.workflows.recruitment_handler import RecruitmentWorkflowHandler
        return RecruitmentWorkflowHandler()
        
    if workflow_type == "business_trip":
        from agents.workflows.business_trip_handler import BusinessTripWorkflowHandler
        return BusinessTripWorkflowHandler()
        
    handler = GenericWorkflowHandler(workflow_type)
    # 설정이 로드된 경우만 핸들러 반환
    if handler._config:
        return handler
    return None
