"""YAML 기반 워크플로우 레지스트리 (하이브리드 방식 지원)."""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

from agents.workflows.registry import WorkflowRegistry, WorkflowMetadata


class YAMLWorkflowRegistry(WorkflowRegistry):
    """YAML 기반 워크플로우 레지스트리 (하이브리드 방식 - Python 기본값 + YAML 오버라이드)."""
    
    _instance: Optional['YAMLWorkflowRegistry'] = None
    _base_registry: Optional[WorkflowRegistry] = None
    
    def __new__(cls, yaml_path: Optional[Path] = None, base_registry: Optional[WorkflowRegistry] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._base_registry = base_registry
            cls._instance.yaml_path = yaml_path or Path(__file__).parent / "workflows.yaml"
            cls._instance._yaml_config: Dict[str, Any] = {}
            cls._instance._load_yaml()
        return cls._instance
    
    def _load_yaml(self) -> None:
        """YAML 파일 로드."""
        if not self.yaml_path.exists():
            print(f"ℹ️ YAML 설정 파일이 없습니다: {self.yaml_path} (Python 기본값 사용)")
            return
        
        try:
            with open(self.yaml_path, 'r', encoding='utf-8') as f:
                self._yaml_config = yaml.safe_load(f) or {}
            wf_count = len(self._yaml_config.get('workflows', {}))
            if wf_count > 0:
                print(f"✅ YAML 설정 로드 완료: {wf_count}개 워크플로우 메타데이터")
        except Exception as e:
            print(f"⚠️ YAML 파일 로드 실패: {e} (Python 기본값 사용)")
            self._yaml_config = {}
    
    def register(
        self,
        name: str,
        category: str,
        description: str,
        get_graph_func: Callable,
        run_func: Optional[Callable] = None,
        priority: int = 0,
        enabled: bool = True,
        examples: Optional[List[str]] = None,
    ) -> None:
        """워크플로우 등록 (YAML 오버라이드 적용 - 하이브리드 방식)."""
        # YAML에서 설정 확인 (있으면 오버라이드)
        yaml_wf = self._yaml_config.get('workflows', {}).get(name, {})
        
        # YAML 설정이 있으면 오버라이드, 없으면 Python 기본값 사용
        final_priority = yaml_wf.get('priority', priority)
        final_enabled = yaml_wf.get('enabled', enabled)
        final_category = yaml_wf.get('category', category)
        final_description = yaml_wf.get('description', description)
        final_examples = yaml_wf.get('examples', examples or [])
        
        # 부모 클래스의 register 호출
        super().register(
            name=name,
            category=final_category,
            description=final_description,
            get_graph_func=get_graph_func,
            run_func=run_func,
            priority=final_priority,
            enabled=final_enabled,
            examples=final_examples,
        )
    
    def reload_yaml(self) -> None:
        """YAML 파일을 다시 로드하고 모든 워크플로우 재등록."""
        old_config = self._yaml_config.copy()
        self._load_yaml()
        
        # 변경된 워크플로우만 업데이트
        if self._yaml_config != old_config:
            print("🔄 YAML 설정 변경 감지 - 워크플로우 메타데이터 업데이트 필요")
            # 실제로는 각 워크플로우를 다시 등록해야 하지만,
            # 이미 등록된 워크플로우의 메타데이터만 업데이트
            for name, wf_meta in self._workflows.items():
                yaml_wf = self._yaml_config.get('workflows', {}).get(name, {})
                if yaml_wf:
                    if 'enabled' in yaml_wf:
                        wf_meta.enabled = yaml_wf['enabled']
                    if 'priority' in yaml_wf:
                        wf_meta.priority = yaml_wf['priority']
                    if 'category' in yaml_wf:
                        wf_meta.category = yaml_wf['category']
                    if 'description' in yaml_wf:
                        wf_meta.description = yaml_wf['description']
                    if 'examples' in yaml_wf:
                        wf_meta.examples = yaml_wf['examples']
    
    def save_to_yaml(self, output_path: Optional[Path] = None) -> None:
        """현재 등록된 워크플로우를 YAML 파일로 저장."""
        output_path = output_path or self.yaml_path
        
        workflows_dict = {}
        for name, wf_meta in self._workflows.items():
            workflows_dict[name] = {
                'category': wf_meta.category,
                'description': wf_meta.description,
                'priority': wf_meta.priority,
                'enabled': wf_meta.enabled,
                'examples': wf_meta.examples,
            }
        
        yaml_data = {
            'workflows': workflows_dict,
            'version': '1.0',
            'comment': '워크플로우 메타데이터 설정 파일'
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            yaml.dump(yaml_data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        
        print(f"💾 YAML 파일 저장 완료: {output_path}")


def get_yaml_registry() -> YAMLWorkflowRegistry:
    """YAML 기반 레지스트리 인스턴스 반환."""
    return YAMLWorkflowRegistry()

