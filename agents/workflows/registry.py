"""워크플로우 자동 등록 및 관리 레지스트리."""

from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Type, Any
from dataclasses import dataclass, field

from agents.state import OrchestratorState


@dataclass
class WorkflowMetadata:
    """워크플로우 메타데이터."""
    name: str  # 워크플로우 이름 (예: "business_trip_flow")
    category: str  # 카테고리 (예: "travel", "research", "meeting")
    description: str  # 설명
    get_graph_func: Callable  # get_xxx_graph() 함수
    run_func: Optional[Callable] = None  # 실행 함수 (있으면 사용, 없으면 자동 생성)
    priority: int = 0  # 우선순위 (높을수록 우선)
    enabled: bool = True  # 활성화 여부
    examples: List[str] = field(default_factory=list)  # 예시 질문들


class WorkflowRegistry:
    """워크플로우 자동 등록 및 관리 레지스트리."""
    
    _instance: Optional['WorkflowRegistry'] = None
    _workflows: Dict[str, WorkflowMetadata] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
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
        """워크플로우를 레지스트리에 등록.
        
        Args:
            name: 워크플로우 이름 (고유 식별자)
            category: 카테고리
            description: 설명
            get_graph_func: get_xxx_graph() 함수
            run_func: 실행 함수 (선택적)
            priority: 우선순위
            enabled: 활성화 여부
            examples: 예시 질문들
        """
        metadata = WorkflowMetadata(
            name=name,
            category=category,
            description=description,
            get_graph_func=get_graph_func,
            run_func=run_func,
            priority=priority,
            enabled=enabled,
            examples=examples or [],
        )
        self._workflows[name] = metadata
        print(f"✅ 워크플로우 등록: {name} ({category})")
    
    def get_all(self, enabled_only: bool = True) -> List[WorkflowMetadata]:
        """등록된 모든 워크플로우 반환.
        
        Args:
            enabled_only: 활성화된 것만 반환할지 여부
            
        Returns:
            워크플로우 메타데이터 리스트 (우선순위 내림차순 정렬)
        """
        workflows = list(self._workflows.values())
        if enabled_only:
            workflows = [w for w in workflows if w.enabled]
        return sorted(workflows, key=lambda x: x.priority, reverse=True)
    
    def get_by_name(self, name: str) -> Optional[WorkflowMetadata]:
        """이름으로 워크플로우 조회."""
        return self._workflows.get(name)
    
    def get_by_category(self, category: str) -> List[WorkflowMetadata]:
        """카테고리로 워크플로우 조회."""
        return [w for w in self._workflows.values() if w.category == category and w.enabled]
    
    def enable(self, name: str) -> None:
        """워크플로우 활성화."""
        if name in self._workflows:
            self._workflows[name].enabled = True
    
    def disable(self, name: str) -> None:
        """워크플로우 비활성화."""
        if name in self._workflows:
            self._workflows[name].enabled = False
    
    def get_routing_prompt(self) -> str:
        """라우팅 프롬프트 생성."""
        workflows = self.get_all(enabled_only=True)
        
        lines = ["다음 카테고리 중 하나를 선택하세요:\n"]
        
        # 카테고리별로 그룹화
        by_category: Dict[str, List[WorkflowMetadata]] = {}
        for wf in workflows:
            if wf.category not in by_category:
                by_category[wf.category] = []
            by_category[wf.category].append(wf)
        
        # 카테고리별로 설명 생성
        for category, wfs in by_category.items():
            lines.append(f"\n{category.upper()} 카테고리:")
            for wf in sorted(wfs, key=lambda x: x.priority, reverse=True):
                lines.append(f"  - '{wf.name}': {wf.description}")
                if wf.examples:
                    examples_str = ", ".join([f'"{ex}"' for ex in wf.examples[:2]])
                    lines.append(f"    예시: {examples_str}")
        
        return "\n".join(lines)


# 전역 레지스트리 인스턴스 (하이브리드 방식에서는 YAML 레지스트리로 교체됨)
# __init__.py에서 YAML 레지스트리로 교체되므로 여기서는 기본 인스턴스만 생성
registry = WorkflowRegistry()


def workflow(
    name: str,
    category: str,
    description: str,
    priority: int = 0,
    enabled: bool = True,
    examples: Optional[List[str]] = None,
):
    """워크플로우 등록 데코레이터.
    
    사용 예:
        @workflow(
            name="business_trip_flow",
            category="travel",
            description="출장/휴가 관리 (일정 + 문서 + 이메일)",
            examples=["다음주 제주 출장 등록", "휴가 신청해줘"]
        )
        def get_business_trip_graph():
            ...
    """
    def decorator(func: Callable):
        # 함수가 get_xxx_graph 형식인지 확인
        if not func.__name__.startswith('get_') or not func.__name__.endswith('_graph'):
            raise ValueError(f"워크플로우 함수는 'get_xxx_graph' 형식이어야 합니다: {func.__name__}")
        
        # 자동으로 run 함수 생성
        async def auto_run_func(state: OrchestratorState) -> Dict[str, Any]:
            """자동 생성된 실행 함수."""
            graph = func()
            result = await graph.ainvoke(state)
            return result
        
        registry.register(
            name=name,
            category=category,
            description=description,
            get_graph_func=func,
            run_func=auto_run_func,
            priority=priority,
            enabled=enabled,
            examples=examples or [],
        )
        return func
    return decorator


def auto_discover_workflows(workflows_dir: Optional[Path] = None) -> None:
    """워크플로우 디렉토리에서 자동으로 워크플로우를 찾아 등록.
    
    Args:
        workflows_dir: 워크플로우 디렉토리 경로 (None이면 자동 탐색)
    """
    if workflows_dir is None:
        # 현재 파일 기준으로 workflows 디렉토리 찾기
        current_file = Path(__file__)
        workflows_dir = current_file.parent
    
    # workflows 디렉토리의 모든 .py 파일 검색
    for py_file in workflows_dir.glob("*.py"):
        # __init__, registry 등은 제외
        if py_file.stem.startswith("_") or py_file.stem == "registry":
            continue
        
        try:
            # 모듈 동적 import
            module_name = f"agents.workflows.{py_file.stem}"
            module = importlib.import_module(module_name)
            
            # get_xxx_graph 함수 찾기
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                if name.startswith("get_") and name.endswith("_graph"):
                    # 이미 등록되어 있으면 스킵 (데코레이터로 등록된 경우)
                    workflow_name = name.replace("get_", "").replace("_graph", "")
                    if workflow_name not in registry._workflows:
                        # 수동 등록 (기본 메타데이터 사용)
                        # run_func는 자동으로 생성됨 (registry.register에서)
                        async def auto_run_func(state: OrchestratorState) -> Dict[str, Any]:
                            """자동 생성된 실행 함수."""
                            graph = obj()
                            result = await graph.ainvoke(state)
                            return result
                        
                        registry.register(
                            name=workflow_name,
                            category="general",  # 기본 카테고리
                            description=f"워크플로우: {workflow_name}",
                            get_graph_func=obj,
                            run_func=auto_run_func,
                            priority=0,
                            enabled=True,
                        )
                        print(f"✅ 자동 등록: {workflow_name} from {py_file.name}")
        
        except Exception as e:
            print(f"⚠️ 워크플로우 자동 등록 실패 ({py_file.name}): {e}")

