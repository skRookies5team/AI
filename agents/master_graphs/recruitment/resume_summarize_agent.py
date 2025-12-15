"""이력서 핵심 요약 에이전트 - LLM 기반 요약."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, Optional
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)

# YAML 파일 로드
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None


def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    try:
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent.parent.parent
        env_path = project_root / ".env"

        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass


_load_env_file()


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 안전 조회."""
    return os.environ.get(name, default)


def _load_summary_prompt() -> str:
    """agent_prompts.yaml에서 요약 프롬프트를 로드합니다.
    
    Returns:
        요약 프롬프트 문자열 (로드 실패 시 기본 프롬프트 반환)
    """
    if not YAML_AVAILABLE:
        logger.warning("YAML 패키지가 설치되지 않았습니다. 기본 프롬프트를 사용합니다.")
        return _get_default_summary_prompt()
    
    try:
        # llm_router의 통합 로더 사용
        from agents.llm_router import load_agent_prompts
        all_prompts = load_agent_prompts()
        
        recruitment_prompts = all_prompts.get("recruitment", {})
        if not recruitment_prompts:
            logger.warning("YAML 파일에 'recruitment' 섹션이 없습니다. 기본 프롬프트를 사용합니다.")
            return _get_default_summary_prompt()
        
        summarize_prompts = recruitment_prompts.get("sub_agents", {}).get("summarize", {})
        if not summarize_prompts:
            logger.warning("YAML 파일에 'recruitment.sub_agents.summarize' 섹션이 없습니다. 기본 프롬프트를 사용합니다.")
            return _get_default_summary_prompt()
        
        summary_prompt = summarize_prompts.get("summary_prompt")
        if summary_prompt:
            logger.info("✅ 요약 프롬프트를 YAML에서 로드했습니다.")
            return summary_prompt
        else:
            logger.warning("YAML 파일에 'summary_prompt'가 없습니다. 기본 프롬프트를 사용합니다.")
            return _get_default_summary_prompt()
            
    except Exception as e:
        logger.warning(f"YAML 파일 로드 실패: {e}. 기본 프롬프트를 사용합니다.")
        return _get_default_summary_prompt()


def _get_default_summary_prompt() -> str:
    """기본 요약 프롬프트를 반환합니다."""
    return """당신은 채용 전문가입니다. 주어진 이력서를 분석하여 채용 담당자가 빠르게 판단할 수 있도록 핵심 정보를 요약해주세요.

다음 형식으로 요약하세요:

## 기본 정보
- 이름: [이름]
- 포지션: [지원 포지션]
- 경력: [경력년수 및 레벨]

## 핵심 경력 요약
[주요 회사 및 역할을 2-3줄로 요약]
- 회사명, 기간, 주요 역할

## 주요 성과 (구체적 수치 포함)
[이력서에 명시된 구체적 성과를 나열]
- 예: "트래픽 50% 증가", "응답속도 40% 개선", "배포 장애 50% 감소" 등
- 수치가 없는 경우 구체적인 업무 성과를 기술

## 핵심 기술 스택
[실제 사용 경험이 있는 기술만 나열, 숙련도 표시 가능]
- 주요 기술: [기술명]
- 보조 기술: [기술명]

## 주요 강점 (3가지)
1. [기술적 강점]
2. [비즈니스 임팩트]
3. [협업/리더십 또는 기타 강점]

## 채용 추천 포인트
[이 포지션에 적합한 이유를 구체적으로 설명]
- 검색 조건과의 매칭도
- 특별히 주목할 만한 역량

## 주의사항/고려사항
[채용 시 확인이 필요한 사항이나 잠재적 리스크]
- 없으면 "특별한 주의사항 없음"으로 표시

**작성 원칙:**
- 간결하고 명확하게 작성
- 이력서에 없는 내용은 추측하지 마세요
- 구체적 수치와 성과를 최대한 포함
- 검색 조건과 관련된 강점을 특히 강조"""


def _parse_summary_to_structured(summary: str) -> Dict[str, Any]:
    """요약 텍스트를 구조화된 데이터로 파싱합니다.
    
    Args:
        summary: LLM이 생성한 요약 텍스트
        
    Returns:
        구조화된 요약 데이터
    """
    structured = {
        "basic_info": {},
        "career_summary": "",
        "key_achievements": [],
        "core_skills": [],
        "strengths": [],
        "recommendation_points": "",
        "considerations": ""
    }
    
    # 섹션별로 파싱
    sections = {
        "기본 정보": "basic_info",
        "핵심 경력 요약": "career_summary",
        "주요 성과": "key_achievements",
        "핵심 기술 스택": "core_skills",
        "주요 강점": "strengths",
        "채용 추천 포인트": "recommendation_points",
        "주의사항": "considerations"
    }
    
    # 기본 정보 파싱
    name_match = re.search(r'이름:\s*(.+)', summary)
    position_match = re.search(r'포지션:\s*(.+)', summary)
    experience_match = re.search(r'경력:\s*(.+)', summary)
    
    if name_match:
        structured["basic_info"]["name"] = name_match.group(1).strip()
    if position_match:
        structured["basic_info"]["position"] = position_match.group(1).strip()
    if experience_match:
        structured["basic_info"]["experience"] = experience_match.group(1).strip()
    
    # 섹션별 내용 추출
    for section_name, key in sections.items():
        pattern = rf'##\s*{re.escape(section_name)}.*?\n(.*?)(?=\n##|$)'
        match = re.search(pattern, summary, re.DOTALL)
        if match:
            content = match.group(1).strip()
            if key == "key_achievements" or key == "core_skills" or key == "strengths":
                # 리스트 형식으로 파싱
                items = [item.strip() for item in content.split('\n') if item.strip() and item.strip().startswith(('-', '•', '1.', '2.', '3.'))]
                structured[key] = items if items else [content]
            else:
                structured[key] = content
    
    return structured


async def summarize_resume(
    resume_index: int,
    session_id: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    선택된 이력서를 핵심 요약합니다.
    
    Args:
        resume_index: 선택한 인재 번호 (1, 2, 3...)
        session_id: 세션 ID
        state: OrchestratorState
        
    Returns:
        {
            "message": str,  # 요약 메시지
            "summary_data": Dict,  # 구조화된 요약 정보
            "ok": bool
        }
    """
    try:
        # recruitment_context에서 이전 검색 결과 가져오기
        recruitment_context = state.get("recruitment_context", {}) if state else {}
        resume_search = recruitment_context.get("resume_search", {})
        last_results = resume_search.get("last_results", [])
        
        if not last_results:
            return {
                "message": "검색 결과를 찾을 수 없습니다. 먼저 인재를 검색해주세요.",
                "ok": False,
            }
        
        # 선택된 인재 찾기
        selected_resume = None
        for resume in last_results:
            if int(resume.get("index", 0)) == resume_index:
                selected_resume = resume
                break
        
        if not selected_resume:
            return {
                "message": f"{resume_index}번 인재를 찾을 수 없습니다. 검색 결과를 확인해주세요.",
                "ok": False,
            }
        
        # 이력서 내용 가져오기
        resume_content = selected_resume.get("content", "")
        if not resume_content:
            # content가 없으면 다른 정보로 요약 생성
            resume_content = f"""
이름: {selected_resume.get('name', '알 수 없음')}
포지션: {selected_resume.get('position', '알 수 없음')}
경력: {selected_resume.get('experience', '알 수 없음')}
기술 스택: {', '.join(selected_resume.get('skills', []))}
"""
        
        # 검색 쿼리 컨텍스트 가져오기
        search_query = resume_search.get("last_query", "")
        search_context = ""
        if search_query:
            search_context = f"\n\n**검색 조건:** 사용자가 '{search_query}' 조건으로 검색했습니다. 이 조건에 맞는 강점과 역량을 특히 강조해주세요."
        
        # LLM으로 이력서 요약
        api_key = _get_env("OPENAI_API_KEY")
        if not api_key:
            # API 키가 없으면 간단한 요약만 제공
            summary = f"""
**{selected_resume.get('name', '알 수 없음')}** ({selected_resume.get('position', '알 수 없음')})

**기본 정보:**
- 경력: {selected_resume.get('experience', '알 수 없음')}
- 주요 기술: {', '.join(selected_resume.get('skills', [])[:10])}

**이력서 내용:**
{resume_content[:500]}...
"""
            return {
                "message": summary,
                "ok": True,
                "summary_data": {
                    "resume_id": selected_resume.get("resume_id"),
                    "name": selected_resume.get("name"),
                    "position": selected_resume.get("position"),
                    "summary": summary,
                    "skills": selected_resume.get("skills", []),
                }
            }
        
        # YAML에서 프롬프트 로드
        system_prompt = _load_summary_prompt()
        
        # 검색 쿼리 컨텍스트를 프롬프트에 추가
        if search_context:
            system_prompt += search_context
        
        llm = ChatOpenAI(
            model=_get_env("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0,
            api_key=api_key
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("user", "다음 이력서를 요약해주세요:\n\n{resume_content}")
        ])
        
        chain = prompt | llm
        response = await chain.ainvoke({"resume_content": resume_content})
        summary = response.content.strip()
        
        # 구조화된 요약 정보 파싱
        structured_summary = _parse_summary_to_structured(summary)
        
        # 구조화된 요약 정보 생성
        summary_data = {
            "resume_id": selected_resume.get("resume_id"),
            "name": selected_resume.get("name"),
            "position": selected_resume.get("position"),
            "experience": selected_resume.get("experience"),
            "summary": summary,  # 원본 텍스트 요약
            "structured_summary": structured_summary,  # 구조화된 데이터
            "skills": selected_resume.get("skills", []),
            "search_query": search_query,  # 검색 쿼리 컨텍스트
        }
        
        # 사용자 메시지 생성
        message = f"**{selected_resume.get('name', '알 수 없음')}** ({selected_resume.get('position', '알 수 없음')})\n\n{summary}"
        
        return {
            "message": message,
            "ok": True,
            "summary_data": summary_data,
        }
        
    except Exception as e:
        logger.error(f"❌ 이력서 요약 실패: {e}", exc_info=True)
        return {
            "message": f"이력서 요약 중 오류가 발생했습니다: {str(e)}",
            "ok": False,
        }


