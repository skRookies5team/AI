"""범용 워크플로우 핸들러 - 설정(YAML) 기반 단계 관리."""

from typing import Dict, Any, Optional, List
from agents.state import OrchestratorState
from agents.workflows.base import BaseWorkflowHandler
import yaml
from pathlib import Path

class GenericWorkflowHandler(BaseWorkflowHandler):
    """설정 파일을 기반으로 모든 워크플로우를 처리하는 범용 핸들러."""

    def __init__(self, workflow_type: str):
        self._workflow_type = workflow_type
        self._config = self._load_config()

    @property
    def workflow_type(self) -> str:
        return self._workflow_type

    def _load_config(self) -> Dict[str, Any]:
        yaml_path = Path(__file__).parent / "workflows.yaml"
        if not yaml_path.exists():
            print(f"⚠️ 워크플로우 설정 파일을 찾을 수 없습니다: {yaml_path}")
            return {}
        with open(yaml_path, "r", encoding="utf-8") as f:
            all_configs = yaml.safe_load(f)
        return all_configs.get("workflows", {}).get(self._workflow_type, {})

    async def handle_step(
        self,
        step_index: int,
        state: OrchestratorState,
        query: str,
        domain: str
    ) -> Dict[str, Any]:
        """YAML 설정에 따라 현재 단계의 도메인으로 라우팅하거나 도메인 노드 호출."""
        steps = self._config.get("steps", [])
        if not steps:
            return {"final_response": f"'{self._workflow_type}' 워크플로우 설정을 찾을 수 없습니다.", "workflow_in_progress": False}
            
        if step_index >= len(steps):
            return {"final_response": "워크플로우 모든 단계가 완료되었습니다.", "workflow_in_progress": False}

        current_step_cfg = steps[step_index]
        target_domain = current_step_cfg.get("domain")
        
        # 1. 현재 도메인이 목표 도메인과 다르면 라우팅만 수행 (Orchestrator 레벨에서의 이동)
        if domain != target_domain:
            return {
                "final_response": f"[{current_step_cfg.get('step_name')}] 단계를 진행합니다.",
                "next_domain": target_domain,
                "requires_input": False
            }

        # 2. 현재 도메인이 목표 도메인이면, 해당 도메인의 마스터 그래프가 처리하도록 위임
        # 실제 처리는 각 도메인 마스터 그래프(ScheduleMaster, RecruitmentMaster 등)가 담당
        return {
            "intent": current_step_cfg.get("intent"),
            "action": current_step_cfg.get("action"),
            "workflow_step_config": current_step_cfg
        }

