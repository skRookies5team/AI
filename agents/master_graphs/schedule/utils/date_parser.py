"""LLM 기반 한국어 날짜 파싱."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

try:
    from langchain_openai import ChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False


LOCAL_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))


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

        now = datetime.now(LOCAL_TIMEZONE)
        today_str = now.strftime("%Y-%m-%d %H:%M")
        today_weekday = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일'][now.weekday()]
        
        # 다음주 월요일 계산 (예시용)
        days_until_next_monday = (7 - now.weekday()) % 7
        if days_until_next_monday == 0:
            days_until_next_monday = 7  # 오늘이 월요일이면 다음주 월요일
        next_monday = now + timedelta(days=days_until_next_monday)
        next_monday_str = next_monday.strftime("%Y-%m-%d")
        
        prompt = f"""오늘 날짜/시간: {today_str}
오늘 요일: {today_weekday}
다음주 월요일: {next_monday_str}

사용자 질문: "{query}"

위 질문에서 날짜와 시간 정보를 정확히 추출하여 JSON으로 반환하세요.

**중요: 시간 해석 규칙**
- "오후 2시" = 14:00 (14시)
- "오후 5시" = 17:00 (17시)
- "오후 7시" = 19:00 (19시)
- "7시" (단독) = 07:00 또는 19:00 (문맥상 판단, "부터~까지" 패턴에서는 오후로 해석)
- "~부터 ~까지" 패턴이 있으면 정확히 그 시간 범위를 사용

**날짜 표현 예시:**
- "오늘" → 오늘 00:00 ~ 23:59
- "내일" → 내일 00:00 ~ 23:59
- "내일 오후 2시" → 내일 14:00 ~ 15:00
- "내일 오후 2시부터 4시까지" → 내일 14:00 ~ 16:00
- "내일 오후 5시부터 7시까지" → 내일 17:00 ~ 19:00
- "내일 오후 5시부터 오후 9시까지" → 내일 17:00 ~ 21:00
- "다음주 월요일" → 다음주 월요일 00:00 ~ 23:59
- "다음 주 3일간" → 다음주 월요일 00:00 ~ 다음주 수요일 23:59 (월, 화, 수 3일)
- "다음 주 3일" → 다음주 월요일 00:00 ~ 다음주 수요일 23:59 (월, 화, 수 3일)
- "다음 주 5일간" → 다음주 월요일 00:00 ~ 다음주 금요일 23:59 (월~금 5일)
- "이번 주" → 이번주 월요일 00:00 ~ 일요일 23:59
- "이번 달" → 이번달 1일 00:00 ~ 말일 23:59
- "12월 25일" → 해당 날짜 00:00 ~ 23:59
- "12월 25일 오후 2시부터 3시까지" → 12월 25일 14:00 ~ 15:00

**중요: "다음 주 N일간" 패턴 처리**
- "다음 주 3일간" = 다음주 월요일부터 3일 (월, 화, 수)
- "다음 주 5일간" = 다음주 월요일부터 5일 (월~금)
- 항상 다음주 월요일을 시작일로 계산
- 오늘 날짜({today_str})를 기준으로 정확히 계산하세요

**출력 형식 (JSON만):**
{{
    "start_date": "YYYY-MM-DD HH:MM" or null,
    "end_date": "YYYY-MM-DD HH:MM" or null
}}

**주의사항:**
- 반드시 오늘 날짜({today_str})를 기준으로 계산하세요
- 과거 날짜를 반환하지 마세요 (오늘보다 이전 날짜는 절대 반환 금지)
- "~부터 ~까지" 패턴이 있으면 반드시 시작/종료 시간을 모두 정확히 추출
- 종료 시간이 명시되지 않은 경우에만 시작 시간 + 1시간으로 설정
- 오후/오전을 정확히 해석 (오후 5시 = 17시, 오후 7시 = 19시)
- 날짜 정보가 없거나 불확실하면 null 반환

JSON만 출력하세요."""

        response = llm.invoke(prompt)
        content = response.content.strip()
        
        import json, re
        # JSON 추출 시 더 넓은 범위로 검색 (중첩된 JSON도 처리)
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*"start_date"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
        if not json_match:
            # 단순 JSON 패턴으로 재시도
            json_match = re.search(r'\{[^{}]*"start_date"[^{}]*\}', content)
        
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                
                start_str = data.get("start_date")
                end_str = data.get("end_date")
                
                if start_str:
                    start_date = datetime.strptime(start_str, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TIMEZONE)
                    end_date = datetime.strptime(end_str, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TIMEZONE) if end_str else start_date
                    if end_date < start_date:
                        end_date = start_date
                    
                    # 파싱된 날짜가 과거인지 검증 (더 엄격하게)
                    now = datetime.now(LOCAL_TIMEZONE)
                    days_diff = (start_date.date() - now.date()).days
                    
                    # 과거 날짜이거나 너무 오래된 날짜(2024년 이전)면 fallback 사용
                    if days_diff < 0 or start_date.year < 2024:
                        print(f"⚠️ LLM이 잘못된 날짜를 반환함: {start_date.date()} (오늘: {now.date()}, 차이: {days_diff}일)")
                        print(f"⚠️ Fallback 파싱으로 전환")
                        return _fallback_date_parsing(query)
                    
                    print(f"✅ LLM 날짜 파싱: {start_date} ~ {end_date}")
                    return start_date, end_date
            except (ValueError, json.JSONDecodeError) as e:
                print(f"⚠️ LLM 응답 파싱 실패: {e}, Fallback 사용")
                return _fallback_date_parsing(query)
        
        # JSON을 찾지 못했거나 파싱 실패 시 fallback 사용
        print(f"⚠️ LLM 응답에서 날짜를 찾을 수 없음, Fallback 사용")
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
    now = datetime.now(LOCAL_TIMEZONE)
    
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
    
    # 다음 주
    elif "다음 주" in query or "다음주" in query:
        # 다음주 월요일 계산
        days_until_next_monday = (7 - now.weekday()) % 7
        if days_until_next_monday == 0:
            days_until_next_monday = 7  # 오늘이 월요일이면 다음주 월요일
        next_monday = now + timedelta(days=days_until_next_monday)
        next_monday = next_monday.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # "3일간" 같은 패턴 추출
        import re
        days_match = re.search(r'(\d+)일간?', query)
        if days_match:
            days = int(days_match.group(1))
            end_date = next_monday + timedelta(days=days - 1, hours=23, minutes=59, seconds=59)
        else:
            # 기본값: 다음주 월요일~일요일
            end_date = next_monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
        
        # 검증: 파싱된 날짜가 미래인지 확인
        if next_monday.date() < now.date():
            print(f"⚠️ Fallback 파싱 결과가 과거 날짜: {next_monday.date()} (오늘: {now.date()})")
            return None, None
        
        print(f"✅ Fallback 날짜 파싱 (다음 주): {next_monday.date()} ~ {end_date.date()}")
        return next_monday, end_date
    
    # 날짜 정보 없음
    else:
        return None, None