"""Recruitment 도메인 프롬프트 로더 - YAML 파일에서 프롬프트 로드."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional
import yaml

# 프롬프트 캐시 (모듈 레벨에서 한 번만 로드)
_prompts_cache: Optional[Dict[str, Any]] = None


def load_recruitment_prompts() -> Dict[str, Any]:
    """Recruitment 도메인 프롬프트를 YAML 파일에서 로드합니다.
    
    캐시를 사용하여 첫 호출 시에만 YAML 파일을 읽고, 이후에는 캐시된 값을 반환합니다.
    
    Returns:
        Recruitment 도메인 프롬프트 딕셔너리
    """
    global _prompts_cache
    
    # 캐시가 있으면 즉시 반환
    if _prompts_cache is not None:
        return _prompts_cache
    
    # YAML 파일 경로 찾기
    current_file = Path(__file__).resolve()
    yaml_path = current_file.parent.parent / "prompts.yaml"
    
    if not yaml_path.exists():
        # llm_router에서 사용하는 통합 프롬프트 로더 시도
        try:
            from agents.llm_router import load_agent_prompts
            all_prompts = load_agent_prompts()
            _prompts_cache = all_prompts.get("recruitment", {})
            if _prompts_cache:
                return _prompts_cache
        except Exception:
            pass
        
        raise FileNotFoundError(
            f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}\n"
            "agents/master_graphs/recruitment/prompts.yaml 파일이 존재하는지 확인해주세요."
        )
    
    # YAML 파일 읽기
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            _prompts_cache = yaml.safe_load(f)
    except Exception as e:
        raise ValueError(
            f"YAML 파일 파싱 실패 ({yaml_path}): {str(e)}\n"
            "파일 형식이 올바른지 확인해주세요."
        ) from e
    
    if not _prompts_cache:
        raise ValueError(f"YAML 파일이 비어있습니다: {yaml_path}")
    
    print(f"✅ [Recruitment Prompts] 프롬프트 로드 완료: {yaml_path}")
    
    return _prompts_cache


def get_recruitment_prompt(sub_agent: str, prompt_key: str) -> str:
    """서브 에이전트의 특정 프롬프트를 가져옵니다.
    
    Args:
        sub_agent: 서브 에이전트 이름 (search, summarize)
        prompt_key: 프롬프트 키 (extract_position, extract_experience, extract_tech_stack 등)
        
    Returns:
        프롬프트 문자열
        
    Raises:
        ValueError: 프롬프트를 찾을 수 없는 경우
    """
    prompts = load_recruitment_prompts()
    sub_agents = prompts.get("sub_agents", {})
    
    if sub_agent not in sub_agents:
        raise ValueError(f"서브 에이전트 '{sub_agent}'를 찾을 수 없습니다.")
    
    sub_agent_config = sub_agents[sub_agent]
    sub_prompts = sub_agent_config.get("prompts", {})
    
    if prompt_key not in sub_prompts:
        raise ValueError(
            f"프롬프트 '{prompt_key}'를 찾을 수 없습니다. "
            f"서브 에이전트 '{sub_agent}'의 prompts 섹션을 확인해주세요."
        )
    
    return sub_prompts[prompt_key]


def clear_prompts_cache():
    """프롬프트 캐시를 초기화합니다. (디버깅 및 개발용)"""
    global _prompts_cache
    _prompts_cache = None
    print("✅ [Recruitment Prompts] 프롬프트 캐시 초기화 완료")

