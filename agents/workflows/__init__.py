"""워크플로우 패키지 초기화 (하이브리드 방식: Python 기본값 + YAML 오버라이드)."""

from __future__ import annotations

from agents.workflows.registry import WorkflowRegistry, workflow as workflow_decorator, auto_discover_workflows
from agents.workflows.registry_yaml import YAMLWorkflowRegistry

# YAML 레지스트리 생성 (하이브리드 방식)
yaml_registry = YAMLWorkflowRegistry()

# 전역 registry를 YAML 레지스트리로 설정 (auto_discover_workflows가 이 registry를 사용)
# 이렇게 하면 자동 등록된 워크플로우도 YAML 오버라이드가 적용됨
import agents.workflows.registry as registry_module
registry_module.registry = yaml_registry

# 워크플로우 자동 등록 (YAML 레지스트리에 등록되므로 YAML 오버라이드 적용됨)
auto_discover_workflows()

# 전역 registry를 YAML 레지스트리로 사용
registry = yaml_registry
workflow = workflow_decorator  # 데코레이터는 그대로 사용

__all__ = ["registry", "workflow", "auto_discover_workflows", "yaml_registry"]

