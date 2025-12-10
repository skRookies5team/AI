"""LLM 기반 한국어 날짜 파싱."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional, Tuple

try:
    from langchain_openai import ChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False


async def parse_korean_date_with_llm(query: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """한국어 날짜 표현을 파싱하여 datetime 객체로 반환.
    
    Args:
        query: 사용자 쿼리 (예: "내일 오후 2시", "다음주 월요일", "이번 달")
        
    Returns:
        (시작 datetime, 종료 datetime) 튜플
        날짜가 없으면 (None, None) 반환
    """
    if not LLM_AVAILABLE:
        return _fallback_date_parsing(query)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_date_parsing(query)
    
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key)
        
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d %H:%M")
        
        prompt = f"""오늘 날짜/시간: {today_str}

사용자 질문: "{query}"

위 질문에서 날짜 정보를 추출하여 JSON으로 반환하세요.

**날짜 표현 예시:**
- "오늘" → 오늘 00:00 ~ 23:59
- "내일" → 내일 00:00 ~ 23:59
- "내일 오후 2시" → 내일 14:00 ~ 15:00 (1시간)
- "다음주 월요일" → 다음주 월요일 00:00 ~ 23:59
- "이번 주" → 이번주 월요일 00:00 ~ 일요일 23:59
- "이번 달" → 이번달 1일 00:00 ~ 말일 23:59
- "12월 25일" → 해당 날짜 00:00 ~ 23:59
- "12월 25일 10시" → 해당 날짜 10:00 ~ 11:00

**출력 형식 (JSON만):**
{{
    "start_date": "YYYY-MM-DD HH:MM" or null,
    "end_date": "YYYY-MM-DD HH:MM" or null
}}

**주의사항:**
- 날짜 정보가 없으면 null 반환
- 시간이 명시되지 않으면 00:00 사용 (종일 일정)
- 종료일이 명시되지 않으면 시작일과 동일

JSON만 출력하세요."""

        response = llm.invoke(prompt)
        content = response.content.strip()
        
        import json, re
        json_match = re.search(r'\{[^{}]*"start_date"[^{}]*\}', content)
        if json_match:
            data = json.loads(json_match.group(0))
            
            start_str = data.get("start_date")
            end_str = data.get("end_date")
            
            if start_str:
                start_date = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
                end_date = datetime.strptime(end_str, "%Y-%m-%d %H:%M") if end_str else start_date
                
                print(f"✅ LLM 날짜 파싱: {start_date} ~ {end_date}")
                return start_date, end_date
        
        return _fallback_date_parsing(query)
    
    except Exception as e:
        print(f"⚠️ LLM 날짜 파싱 실패: {e}")
        return _fallback_date_parsing(query)


def _fallback_date_parsing(query: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """키워드 기반 날짜 파싱 (fallback).
    
    Args:
        query: 사용자 쿼리
        
    Returns:
        (시작 datetime, 종료 datetime) 튜플
    """
    now = datetime.now()
    
    # 오늘
    if "오늘" in query:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start, end
    
    # 내일
    elif "내일" in query:
        tomorrow = now + timedelta(days=1)
        start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        end = tomorrow.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start, end
    
    # 이번 주
    elif "이번 주" in query or "이번주" in query:
        days_since_monday = now.weekday()
        week_start = now - timedelta(days=days_since_monday)
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return week_start, week_end
    
    # 이번 달
    elif "이번 달" in query or "이번달" in query:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            month_end = now.replace(month=12, day=31, hour=23, minute=59, second=59)
        else:
            next_month = now.replace(month=now.month + 1, day=1)
            month_end = next_month - timedelta(seconds=1)
        return month_start, month_end
    
    # 날짜 정보 없음
    else:
        return None, None