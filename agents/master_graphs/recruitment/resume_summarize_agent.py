"""이력서 핵심 요약 에이전트 - LLM 기반 요약."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)


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
        
        llm = ChatOpenAI(
            model=_get_env("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0,
            api_key=api_key
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", """당신은 채용 전문가입니다. 주어진 이력서를 핵심적으로 요약해주세요.

다음 형식으로 요약하세요:

## 기본 정보
- 이름: [이름]
- 포지션: [지원 포지션]

## 경력 요약
[주요 경력 사항을 3-5줄로 요약]

## 핵심 기술
[주요 기술 스택 및 역량을 나열]

## 주요 강점
[이 지원자의 주요 강점 2-3개]

## 채용 추천 포인트
[채용 시 주목할 점과 추천 이유]

간결하고 명확하게 작성해주세요. 이력서 내용을 바탕으로 정확한 정보만 제공하세요."""),
            ("user", "다음 이력서를 요약해주세요:\n\n{resume_content}")
        ])
        
        chain = prompt | llm
        response = await chain.ainvoke({"resume_content": resume_content})
        summary = response.content.strip()
        
        # 구조화된 요약 정보 생성
        summary_data = {
            "resume_id": selected_resume.get("resume_id"),
            "name": selected_resume.get("name"),
            "position": selected_resume.get("position"),
            "experience": selected_resume.get("experience"),
            "summary": summary,
            "skills": selected_resume.get("skills", []),
            "score": selected_resume.get("score"),
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

