"""워크플로우 핸들러 기본 클래스."""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from agents.state import OrchestratorState


class BaseWorkflowHandler(ABC):
    """워크플로우 핸들러 기본 클래스.
    
    각 워크플로우는 이 클래스를 상속받아 구현합니다.
    """
    
    @property
    @abstractmethod
    def workflow_type(self) -> str:
        """워크플로우 타입을 반환합니다."""
        pass
    
    @abstractmethod
    async def handle_step(
        self,
        step_index: int,
        state: OrchestratorState,
        query: str,
        domain: str
    ) -> Dict[str, Any]:
        """워크플로우 단계를 처리합니다.
        
        Args:
            step_index: 현재 워크플로우 단계 인덱스 (0부터 시작)
            state: 오케스트레이터 상태
            query: 사용자 쿼리
            domain: 현재 도메인
            
        Returns:
            처리 결과 딕셔너리:
            - final_response: 최종 응답 메시지
            - messages: 메시지 리스트
            - requires_input: 사용자 입력 필요 여부
            - next_domain: 다음 단계 도메인 (없으면 None)
            - workflow_temp_data: 임시 데이터 업데이트 (선택적)
        """
        pass
    
    def validate_step_transition(
        self,
        current_index: int,
        next_index: int,
        workflow_steps: list
    ) -> bool:
        """워크플로우 단계 전환이 유효한지 검증합니다.
        
        Args:
            current_index: 현재 단계 인덱스
            next_index: 다음 단계 인덱스
            workflow_steps: 워크플로우 단계 리스트
            
        Returns:
            유효하면 True, 아니면 False
        """
        if next_index < 0:
            return False
        if next_index >= len(workflow_steps):
            return False
        if next_index <= current_index:
            # 순차적 증가가 아니면 경고 (특수 케이스는 허용할 수 있음)
            print(f"⚠️ 워크플로우 인덱스 비순차적 증가: {current_index} → {next_index}")
            # 기본적으로는 순차적 증가만 허용
            return False
        return True
    
    def get_step_domain(self, step_index: int, workflow_steps: list) -> Optional[str]:
        """단계 인덱스에 해당하는 도메인을 반환합니다.
        
        Args:
            step_index: 단계 인덱스
            workflow_steps: 워크플로우 단계 리스트
            
        Returns:
            도메인 이름 또는 None
        """
        if 0 <= step_index < len(workflow_steps):
            return workflow_steps[step_index]
        return None


