"""LLM 기반 엔티티 추출 (의도는 이미 마스터그래프에서 분류됨)."""

from __future__ import annotations

import os
from typing import Dict, Any, List, Optional

try:
    from langchain_openai import ChatOpenAI  # type: ignore[import]
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from agents.master_graphs.schedule.utils.prompt_loader import (
    get_sub_agent_prompt,
    format_prompt_template
)


async def extract_schedule_entities(query: str) -> Dict[str, Any]:
    """일정 생성/수정에 필요한 정보 추출.
    
    Args:
        query: 사용자 쿼리 (예: "내일 오후 2시 팀 회의 등록")
        
    Returns:
        {
            'title': '팀 회의',
            'description': None,
            'destination': None
        }
    """
    if not LLM_AVAILABLE:
        return _fallback_schedule_extraction(query)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_schedule_extraction(query)
    
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key)
        
        # YAML에서 프롬프트 로드
        prompt_template = get_sub_agent_prompt("schedule", "entity_extraction")
        prompt = format_prompt_template(prompt_template, query=query)

        response = llm.invoke(prompt)
        content = response.content.strip()
        
        import json, re
        json_match = re.search(r'\{[^{}]*"title"[^{}]*\}', content)
        if json_match:
            data = json.loads(json_match.group(0))
            print(f"✅ LLM 일정 엔티티 추출: {data}")
            return data
        
        return _fallback_schedule_extraction(query)
    
    except Exception as e:
        print(f"⚠️ LLM 엔티티 추출 실패: {e}")
        return _fallback_schedule_extraction(query)


async def extract_leave_entities(query: str) -> Dict[str, Any]:
    """연차 신청에 필요한 정보 추출.
    
    Args:
        query: 사용자 쿼리 (예: "내일 반차 신청")
        
    Returns:
        {
            'leave_type': '연차' | '반차' | '병가' | '경조사',
            'total_days': float or None,
            'reason': str or None
        }
    """
    if not LLM_AVAILABLE:
        return _fallback_leave_extraction(query)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_leave_extraction(query)
    
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key)
        
        # YAML에서 프롬프트 로드
        prompt_template = get_sub_agent_prompt("leave", "entity_extraction")
        prompt = format_prompt_template(prompt_template, query=query)

        response = llm.invoke(prompt)
        content = response.content.strip()
        
        import json, re
        json_match = re.search(r'\{[^{}]*"leave_type"[^{}]*\}', content)
        if json_match:
            data = json.loads(json_match.group(0))
            print(f"✅ LLM 연차 엔티티 추출: {data}")
            return data
        
        return _fallback_leave_extraction(query)
    
    except Exception as e:
        print(f"⚠️ LLM 엔티티 추출 실패: {e}")
        return _fallback_leave_extraction(query)


async def extract_interviewer_entities(query: str) -> Dict[str, Any]:
    """면접관 정보(이름, 부서, 직급) 추출.
    
    Args:
        query: 사용자 쿼리 (예: "인사팀 김철수 과장, 개발팀 이영희 대리")
        
    Returns:
        {
            'interviewers': [
                {'name': '김철수', 'dept': '인사팀', 'position': '과장'},
                {'name': '이영희', 'dept': '개발팀', 'position': '대리'}
            ]
        }
    """
    if not LLM_AVAILABLE:
        return {"interviewers": []}
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"interviewers": []}
    
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI
        import json, re
        
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key)
        
        system_prompt = """사용자 입력에서 면접관들의 정보를 추출하여 JSON 형식으로 반환하세요.
        각 면접관에 대해 이름(name), 부서(dept), 직급(position)을 추출해야 합니다.
        정보가 명시되지 않은 필드는 null로 설정하세요.
        
        결과는 반드시 아래와 같은 키를 가진 JSON 객체여야 합니다:
        - interviewers: 면접관 정보 리스트 (객체 필드: name, dept, position)
        """
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("user", "{query}")
        ])
        
        chain = prompt | llm
        response = await chain.ainvoke({"query": query})
        content = response.content.strip()
        
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            print(f"✅ LLM 면접관 엔티티 추출: {data}")
            return data
        
        return {"interviewers": []}
    
    except Exception as e:
        print(f"⚠️ LLM 면접관 엔티티 추출 실패: {e}")
        return {"interviewers": []}


def _fallback_schedule_extraction(query: str) -> Dict[str, Any]:
    """키워드 기반 일정 추출 (fallback)."""
    # 간단하게 쿼리를 제목으로
    keywords = ["등록", "생성", "추가", "만들", "일정", "회의", "미팅", "예약"]
    title = query
    for kw in keywords:
        title = title.replace(kw, "").strip()
    
    return {
        "title": title if title else "새 일정",
        "description": None,
        "destination": None
    }


def _fallback_leave_extraction(query: str) -> Dict[str, Any]:
    """키워드 기반 연차 추출 (fallback)."""
    leave_type = "연차"
    total_days = None
    
    if "반차" in query:
        leave_type = "반차"
        total_days = 0.5
    elif "병가" in query:
        leave_type = "병가"
    elif "경조사" in query:
        leave_type = "경조사"
    
    return {
        "leave_type": leave_type,
        "total_days": total_days,
        "reason": None
    }


async def extract_meeting_room_entities(query: str) -> Dict[str, Any]:
    """회의실 관련 정보를 추출한다."""
    if not LLM_AVAILABLE:
        return _fallback_meeting_extraction(query)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_meeting_extraction(query)
    
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key)
        
        # YAML에서 프롬프트 로드
        prompt_template = get_sub_agent_prompt("meeting", "entity_extraction")
        prompt = format_prompt_template(prompt_template, query=query)
        response = llm.invoke(prompt)
        content = response.content.strip()
        import json, re  # pylint: disable=import-outside-toplevel
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            print(f"✅ LLM 회의실 엔티티 추출: {data}")
            return data
        return _fallback_meeting_extraction(query)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"⚠️ LLM 회의실 엔티티 추출 실패: {exc}")
        return _fallback_meeting_extraction(query)


def _fallback_meeting_extraction(query: str) -> Dict[str, Any]:
    """회의실 정보 추출 fallback."""
    import re  # pylint: disable=import-outside-toplevel

    def _extract_int(patterns: List[str]) -> Optional[int]:
        for pattern in patterns:
            match = re.search(pattern, query)
            if match:
                try:
                    return int(match.group(1))
                except (IndexError, ValueError):
                    continue
        return None

    participants = _extract_int([r"(\d+)\s*(?:명|인)", r"(\d+)\s*(?:people)"])
    capacity = _extract_int([r"(\d+)\s*(?:인용|seater)", r"(\d+)\s*(?:명 규모)"])
    floor = _extract_int([r"(\d+)\s*층"])
    duration_minutes: Optional[int] = None

    hour_match = re.search(r"(\d+)\s*(?:시간|hour)", query)
    minute_match = re.search(r"(\d+)\s*(?:분|minute)", query)
    if hour_match:
        try:
            duration_minutes = int(hour_match.group(1)) * 60
        except ValueError:
            duration_minutes = None
    if minute_match:
        try:
            duration_value = int(minute_match.group(1))
            duration_minutes = duration_value if duration_minutes is None else duration_minutes + duration_value
        except ValueError:
            pass

    requires_video = any(keyword in query for keyword in ["화상", "video", "줌", "zoom"])
    requires_projector = any(keyword in query for keyword in ["프로젝터", "projector", "빔"])
    requires_whiteboard = any(keyword in query for keyword in ["화이트보드", "whiteboard"])

    title = "회의"
    if "면접" in query:
        title = "면접"
    elif "발표" in query:
        title = "발표"
    elif "워크샵" in query:
        title = "워크샵"

    return {
        "title": title,
        "purpose": None,
        "participants": participants,
        "capacity": capacity or participants,
        "duration_minutes": duration_minutes,
        "room_code": None,
        "room_name": None,
        "floor": floor,
        "requires_video": requires_video if any([requires_video, requires_projector, requires_whiteboard]) else None,
        "requires_projector": requires_projector if requires_projector else None,
        "requires_whiteboard": requires_whiteboard if requires_whiteboard else None,
        "attendees": [],
        "start_time_str": None,
        "end_time_str": None,
    }