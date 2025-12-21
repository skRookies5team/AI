"""Schedule 도메인 프롬프트 로더 - YAML 파일에서 프롬프트 로드."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional
import yaml

# 프롬프트 캐시 (모듈 레벨에서 한 번만 로드)
_prompts_cache: Optional[Dict[str, Any]] = None


def load_schedule_prompts() -> Dict[str, Any]:
    """Schedule 도메인 프롬프트를 YAML 파일에서 로드합니다.
    
    캐시를 사용하여 첫 호출 시에만 YAML 파일을 읽고, 이후에는 캐시된 값을 반환합니다.
    
    Returns:
        Schedule 도메인 프롬프트 딕셔너리
        
    Raises:
        FileNotFoundError: prompts.yaml 파일을 찾을 수 없는 경우
        ValueError: YAML 파일 파싱 실패 시
    """
    global _prompts_cache
    
    # 캐시가 있으면 즉시 반환
    if _prompts_cache is not None:
        return _prompts_cache
    
    # YAML 파일 경로 찾기
    current_file = Path(__file__).resolve()
    yaml_path = current_file.parent.parent / "prompts.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}\n"
            "agents/master_graphs/schedule/prompts.yaml 파일이 존재하는지 확인해주세요."
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
    
    print(f"✅ [Schedule Prompts] 프롬프트 로드 완료: {yaml_path}")
    
    return _prompts_cache


def get_sub_agent_prompt(sub_agent: str, prompt_key: str) -> str:
    """서브 에이전트의 특정 프롬프트를 가져옵니다.
    
    Args:
        sub_agent: 서브 에이전트 이름 (leave, schedule, meeting, business_trip)
        prompt_key: 프롬프트 키 (slot_manager, entity_extraction, initial_application_template 등)
        
    Returns:
        프롬프트 문자열
        
    Raises:
        ValueError: 프롬프트를 찾을 수 없는 경우
    """
    prompts = load_schedule_prompts()
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


def get_utils_prompt(util_name: str, prompt_key: str) -> str:
    """유틸리티의 특정 프롬프트를 가져옵니다.
    
    Args:
        util_name: 유틸리티 이름 (date_parser 등)
        prompt_key: 프롬프트 키 (korean_date_parsing 등)
        
    Returns:
        프롬프트 문자열
        
    Raises:
        ValueError: 프롬프트를 찾을 수 없는 경우
    """
    prompts = load_schedule_prompts()
    utils = prompts.get("utils", {})
    
    if util_name not in utils:
        raise ValueError(f"유틸리티 '{util_name}'를 찾을 수 없습니다.")
    
    util_config = utils[util_name]
    
    if prompt_key not in util_config:
        raise ValueError(
            f"프롬프트 '{prompt_key}'를 찾을 수 없습니다. "
            f"유틸리티 '{util_name}'의 prompts 섹션을 확인해주세요."
        )
    
    return util_config[prompt_key]


def format_prompt_template(template: str, **kwargs) -> str:
    """프롬프트 템플릿을 포맷팅합니다.
    
    Args:
        template: 프롬프트 템플릿 문자열
        **kwargs: 템플릿에 삽입할 변수들
        
    Returns:
        포맷팅된 프롬프트 문자열
    """
    return template.format(**kwargs)

