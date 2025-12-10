"""LLM 기반 엔티티 추출 (의도는 이미 마스터그래프에서 분류됨)."""

from __future__ import annotations

import os
from typing import Dict, Any, List, Optional

try:
    from langchain_openai import ChatOpenAI  # type: ignore[import]
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False


async def extract_schedule_entities(query: str) -> Dict[str, Any]:
    """일정 생성/수정에 필요한 정보 추출.
    
    Args:
        query: 사용자 쿼리 (예: "내일 오후 2시 팀 회의 등록")
        
    Returns:
        {
            'title': '팀 회의',
            'description': None,
            'location': None
        }
    """
    if not LLM_AVAILABLE:
        return _fallback_schedule_extraction(query)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_schedule_extraction(query)
    
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key)
        
        prompt = f"""사용자 질문: "{query}"

위 질문에서 일정 정보를 추출하여 JSON으로 반환하세요.

**출력 형식 (JSON만):**
{{
    "title": "일정 제목" or null,
    "description": "상세 설명" or null,
    "location": "장소" or null
}}

**예시:**
- "내일 팀 회의 등록" → {{"title": "팀 회의", "description": null, "location": null}}
- "다음주 금요일 프로젝트 킥오프 미팅 추가" → {{"title": "프로젝트 킥오프 미팅", "description": null, "location": null}}
- "회의실 A에서 분기 보고회" → {{"title": "분기 보고회", "description": null, "location": null}}

JSON만 출력하세요."""

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
        
        prompt = f"""사용자 질문: "{query}"

위 질문에서 연차 정보를 추출하여 JSON으로 반환하세요.

**휴가 종류:**
- 연차: 일반 연차
- 반차: 반일 휴가 (0.5일)
- 병가: 병가
- 경조사: 경조사 휴가

**출력 형식 (JSON만):**
{{
    "leave_type": "연차" | "반차" | "병가" | "경조사",
    "total_days": number or null,
    "reason": "사유" or null
}}

**예시:**
- "내일 연차 신청" → {{"leave_type": "연차", "total_days": 1.0, "reason": null}}
- "다음주 월요일 반차" → {{"leave_type": "반차", "total_days": 0.5, "reason": null}}
- "개인 사정으로 연차" → {{"leave_type": "연차", "total_days": null, "reason": "개인 사정"}}

JSON만 출력하세요."""

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
        "location": None
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
        prompt = f"""
사용자 입력: "{query}"

회의실 예약/조회에 필요한 정보를 JSON으로 추출하세요.

출력 형식(JSON만):
{{
  "title": "회의 제목" or null,
  "purpose": "회의 목적" or null,
  "participants": number or null,
  "capacity": number or null,
  "duration_minutes": number or null,
  "room_code": "회의실 코드" or null,
  "room_name": "회의실 이름" or null,
  "floor": number or null,
  "requires_video": true | false | null,
  "requires_projector": true | false | null,
  "requires_whiteboard": true | false | null,
  "attendees": ["이메일", ...] or [],
  "start_time_str": "시작 시간 표현" or null,
  "end_time_str": "종료 시간 표현" or null
}}

주의사항:
- 참가 인원이 명시되면 participants와 capacity에 모두 반영하세요.
- 화상/프로젝터/화이트보드 요구사항을 인식하세요.
- start_time_str과 end_time_str은 원문 그대로 추출하세요 (예: "내일 오후 2시", "12월 10일 14시")
- 정보가 없다면 null을 사용하세요.
JSON 외 다른 텍스트는 출력하지 마세요.
"""
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