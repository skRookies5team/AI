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

from agents.master_graphs.schedule.utils.prompt_loader import (
    get_utils_prompt,
    format_prompt_template
)


LOCAL_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Seoul"))


async def parse_korean_date_with_llm(
    query: str, 
    current_datetime: Optional[datetime] = None,
    allow_past_dates: bool = False
) -> Tuple[Optional[datetime], Optional[datetime], bool]:
    """한국어 날짜 표현을 파싱하여 datetime 객체와 정밀도 여부 반환.
    
    Args:
        query: 사용자 쿼리 (예: "내일 오후 2시", "다음주 월요일", "이번 달")
        current_datetime: 현재 시간 (orchestrator에서 전달, 없으면 datetime.now() 사용)
        allow_past_dates: 과거 날짜 허용 여부 (조회는 True, 생성/예약/취소는 False)
        
    Returns:
        (시작 datetime, 종료 datetime, is_precise) 튜플
        날짜가 없으면 (None, None, False) 반환
    """
    # orchestrator에서 전달받은 현재 시간 사용, 없으면 기본값 사용
    if current_datetime is None:
        now = datetime.now(LOCAL_TIMEZONE)
    else:
        # 문자열인 경우 datetime으로 변환 (state에서 문자열로 전달될 수 있음)
        if isinstance(current_datetime, str):
            try:
                current_datetime = datetime.fromisoformat(current_datetime.replace('Z', '+00:00'))
            except Exception as e:
                print(f"⚠️ current_datetime 문자열 파싱 실패: {e}, datetime.now() 사용")
                now = datetime.now(LOCAL_TIMEZONE)
            else:
                # timezone 정보가 없으면 추가
                if current_datetime.tzinfo is None:
                    now = current_datetime.replace(tzinfo=LOCAL_TIMEZONE)
                else:
                    now = current_datetime.astimezone(LOCAL_TIMEZONE)
        elif isinstance(current_datetime, datetime):
            # datetime 객체인 경우
            # timezone 정보가 없으면 추가
            if current_datetime.tzinfo is None:
                now = current_datetime.replace(tzinfo=LOCAL_TIMEZONE)
            else:
                now = current_datetime.astimezone(LOCAL_TIMEZONE)
        else:
            # 예상치 못한 타입
            print(f"⚠️ current_datetime 타입 오류: {type(current_datetime)}, datetime.now() 사용")
            now = datetime.now(LOCAL_TIMEZONE)
    
    if not LLM_AVAILABLE:
        return _fallback_date_parsing(query, current_datetime=now)
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback_date_parsing(query, current_datetime=now)
    
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key)
        today_str = now.strftime("%Y-%m-%d %H:%M")
        today_weekday = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일'][now.weekday()]
        
        # 이번주 월요일 계산 (이번주 시작일)
        days_since_monday = now.weekday()  # 0=월요일, 6=일요일
        this_week_monday = now - timedelta(days=days_since_monday)
        this_week_monday_str = this_week_monday.strftime("%Y-%m-%d")
        
        # 이번주 각 요일 날짜 계산 (월요일부터 일요일까지)
        weekday_names = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
        this_week_dates = {}
        for i, weekday_name in enumerate(weekday_names):
            date = this_week_monday + timedelta(days=i)
            this_week_dates[weekday_name] = date.strftime("%Y-%m-%d")
        
        # 다음주 월요일 계산 (예시용)
        days_until_next_monday = (7 - now.weekday()) % 7
        if days_until_next_monday == 0:
            days_until_next_monday = 7  # 오늘이 월요일이면 다음주 월요일
        next_monday = now + timedelta(days=days_until_next_monday)
        next_monday_str = next_monday.strftime("%Y-%m-%d")
        
        # 다음주 일요일 계산 (전체 주용)
        next_sunday = next_monday + timedelta(days=6)  # 월요일 + 6일 = 일요일
        next_sunday_str = next_sunday.strftime("%Y-%m-%d")
        
        # 다음주 수요일 계산 (3일간 예시용)
        next_wednesday = next_monday + timedelta(days=2)  # 월요일 + 2일 = 수요일
        next_wednesday_str = next_wednesday.strftime("%Y-%m-%d")
        
        # YAML에서 프롬프트 템플릿 로드
        prompt_template = get_utils_prompt("date_parser", "korean_date_parsing")
        
        # 템플릿 변수 준비
        today_date = today_str.split()[0]  # 날짜 부분만 추출
        
        # 프롬프트 포맷팅 (예외 처리 추가)
        try:
            prompt = format_prompt_template(
                prompt_template,
                today_str=today_str,
                today_weekday=today_weekday,
                now_year=now.year,
                next_year=now.year + 1,  # ✅ 다음 연도 계산
                now_month=now.month,
                next_monday_str=next_monday_str,
                next_sunday_str=next_sunday_str,  # ✅ 다음주 일요일 추가
                next_wednesday_str=next_wednesday_str,
                this_week_monday_str=this_week_monday_str,
                this_week_monday=this_week_dates['월요일'],
                this_week_tuesday=this_week_dates['화요일'],
                this_week_wednesday=this_week_dates['수요일'],
                this_week_thursday=this_week_dates['목요일'],
                this_week_friday=this_week_dates['금요일'],
                this_week_saturday=this_week_dates['토요일'],
                this_week_sunday=this_week_dates['일요일'],
                query=query,
                today_date=today_date
            )
        except KeyError as e:
            print(f"❌ [date_parser] 프롬프트 변수 누락: {e}")
            print(f"❌ [date_parser] 누락된 변수로 인해 프롬프트 포맷팅 실패, Fallback 사용")
            # 프롬프트 템플릿에 누락된 변수 정보 출력
            import re
            missing_vars = re.findall(r'\{(\w+)\}', prompt_template)
            provided_vars = set(['today_str', 'today_weekday', 'now_year', 'now_month', 
                                'next_monday_str', 'next_sunday_str', 'next_wednesday_str', 'this_week_monday_str',
                                'this_week_monday', 'this_week_tuesday', 'this_week_wednesday',
                                'this_week_thursday', 'this_week_friday', 'this_week_saturday',
                                'this_week_sunday', 'query', 'today_date'])
            missing = set(missing_vars) - provided_vars
            if missing:
                print(f"❌ [date_parser] 누락된 변수: {missing}")
            return _fallback_date_parsing(query, current_datetime=now)
        except Exception as e:
            print(f"❌ [date_parser] 프롬프트 포맷팅 실패: {e}")
            print(f"❌ [date_parser] 예외 타입: {type(e).__name__}")
            import traceback
            print(f"❌ [date_parser] 트레이스백: {traceback.format_exc()}")
            return _fallback_date_parsing(query, current_datetime=now)

        # 디버깅: 프롬프트에 변수가 제대로 치환되었는지 확인
        if "{this_week_tuesday}" in prompt or "{this_week_monday}" in prompt:
            print(f"⚠️ [date_parser] 프롬프트에 치환되지 않은 변수 발견!")
            print(f"⚠️ [date_parser] 프롬프트에서 발견된 미치환 변수:")
            import re
            unsubstituted = re.findall(r'\{(\w+)\}', prompt)
            print(f"⚠️ [date_parser] 미치환 변수 목록: {unsubstituted}")
            # Fallback 사용
            return _fallback_date_parsing(query, current_datetime=now)

        # 디버깅: 실제 전달되는 프롬프트의 일부 확인 (너무 길면 잘라서)
        print(f"🔍 [date_parser] 실제 전달되는 프롬프트 (처음 800자):")
        print(prompt[:800])
        print(f"🔍 [date_parser] 실제 전달되는 프롬프트 (마지막 800자):")
        print(prompt[-800:])
        
        # 디버깅: LLM에 전달되는 프롬프트의 주요 날짜 정보 확인
        print(f"🔍 [date_parser] LLM 프롬프트 날짜 정보:")
        print(f"  - 현재 날짜/시간: {today_str} ({today_weekday})")
        print(f"  - 현재 연도: {now.year}년")
        print(f"  - 이번주 화요일: {this_week_dates['화요일']}")
        print(f"  - 이번주 목요일: {this_week_dates['목요일']}")
        print(f"  - 사용자 쿼리: {query}")

        response = llm.invoke(prompt)
        content = response.content.strip()
        
        # 디버깅: LLM 원본 응답 확인
        print(f"🔍 [date_parser] LLM 원본 응답: {content[:300]}...")
        
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
                is_precise = data.get("is_precise", False)
                
                if start_str:
                    start_date = datetime.strptime(start_str, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TIMEZONE)
                    end_date = datetime.strptime(end_str, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TIMEZONE) if end_str else start_date
                    if end_date < start_date:
                        end_date = start_date
                    
                    # 파싱된 날짜가 과거인지 검증 (연도 변경 허용)
                    # now는 이미 위에서 정의됨 (orchestrator의 현재 시간 또는 datetime.now())
                    days_diff = (start_date.date() - now.date()).days
                    
                    # 디버깅: LLM 반환값 확인
                    print(f"🔍 [date_parser] LLM 반환 날짜: {start_date.date()}, 오늘: {now.date()}, 차이: {days_diff}일, 반환 연도: {start_date.year}, 현재 연도: {now.year}, 정밀도: {is_precise}")
                    
                    # 과거 날짜만 거부 (연도 변경은 허용 - 예: 12월 말에 "다음주"는 다음 해 1월)
                    # 연도가 바뀌는 경우는 정상 (예: 2025-12-29에 "다음주" → 2026-01-05)
                    
                    if days_diff < 0:
                        # allow_past_dates가 True면 과거 날짜 허용 (조회용)
                        if allow_past_dates:
                            print(f"ℹ️ 과거 날짜 허용 (조회 요청): {start_date.date()}")
                        else:
                            # 생성/예약/취소용: 주 단위 표현이어도 과거 날짜는 절대 허용하지 않음
                            print(f"⚠️ LLM이 과거 날짜를 반환함: {start_date.date()} (오늘: {now.date()}, 차이: {days_diff}일)")
                            print(f"⚠️ 날짜 정보가 불확실하므로 None 반환하여 재요청")
                            return None, None, False  # 재요청을 위해 None 반환
                    
                    # 연도가 바뀌는 경우는 허용하되, 너무 먼 미래는 경고만 출력
                    if start_date.year != now.year:
                        year_diff = start_date.year - now.year
                        if year_diff > 1:  # 1년 이상 차이나는 경우만 경고
                            print(f"⚠️ 연도 차이가 큼: {start_date.year}년 (현재: {now.year}년, 차이: {year_diff}년)")
                        else:
                            print(f"ℹ️ 연도 변경 감지: {start_date.year}년 (현재: {now.year}년) - 정상적인 경우입니다 (예: 12월 말에 '다음주')")
                    
                    print(f"✅ LLM 날짜 파싱: {start_date} ~ {end_date} (정밀도: {is_precise})")
                    return start_date, end_date, is_precise
            except (ValueError, json.JSONDecodeError) as e:
                print(f"⚠️ LLM 응답 파싱 실패: {e}, Fallback 사용")
                return _fallback_date_parsing(query, current_datetime=now)
        
        # JSON을 찾지 못했거나 파싱 실패 시 fallback 사용
        print(f"⚠️ LLM 응답에서 날짜를 찾을 수 없음, Fallback 사용")
        return _fallback_date_parsing(query, current_datetime=now)
    
    except Exception as e:
        print(f"⚠️ LLM 날짜 파싱 실패: {e}")
        return _fallback_date_parsing(query, current_datetime=now)


def _fallback_date_parsing(
    query: str, 
    current_datetime: Optional[datetime] = None
) -> Tuple[Optional[datetime], Optional[datetime], bool]:
    """키워드 기반 날짜 파싱 (fallback).
    
    Args:
        query: 사용자 쿼리
        current_datetime: 현재 시간 (orchestrator에서 전달, 없으면 datetime.now() 사용)
        
    Returns:
        (시작 datetime, 종료 datetime, is_precise) 튜플
    """
    # orchestrator에서 전달받은 현재 시간 사용, 없으면 기본값 사용
    if current_datetime is None:
        now = datetime.now(LOCAL_TIMEZONE)
    else:
        # 문자열인 경우 datetime으로 변환 (state에서 문자열로 전달될 수 있음)
        if isinstance(current_datetime, str):
            try:
                current_datetime = datetime.fromisoformat(current_datetime.replace('Z', '+00:00'))
            except Exception as e:
                print(f"⚠️ current_datetime 문자열 파싱 실패: {e}, datetime.now() 사용")
                now = datetime.now(LOCAL_TIMEZONE)
            else:
                # timezone 정보가 없으면 추가
                if current_datetime.tzinfo is None:
                    now = current_datetime.replace(tzinfo=LOCAL_TIMEZONE)
                else:
                    now = current_datetime.astimezone(LOCAL_TIMEZONE)
        elif isinstance(current_datetime, datetime):
            # datetime 객체인 경우
            # timezone 정보가 없으면 추가
            if current_datetime.tzinfo is None:
                now = current_datetime.replace(tzinfo=LOCAL_TIMEZONE)
            else:
                now = current_datetime.astimezone(LOCAL_TIMEZONE)
        else:
            # 예상치 못한 타입
            print(f"⚠️ current_datetime 타입 오류: {type(current_datetime)}, datetime.now() 사용")
            now = datetime.now(LOCAL_TIMEZONE)
    
    # 오늘
    if "오늘" in query:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start, end, True
    
    # 내일
    elif "내일" in query:
        tomorrow = now + timedelta(days=1)
        start = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        end = tomorrow.replace(hour=23, minute=59, second=59, microsecond=999999)
        return start, end, True
    
    # 이번 주
    elif "이번 주" in query or "이번주" in query:
        days_since_monday = now.weekday()
        week_start = now - timedelta(days=days_since_monday)
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return week_start, week_end, False
    
    # 이번 달
    elif "이번 달" in query or "이번달" in query:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            month_end = now.replace(month=12, day=31, hour=23, minute=59, second=59)
        else:
            next_month = now.replace(month=now.month + 1, day=1)
            month_end = next_month - timedelta(seconds=1)
        return month_start, month_end, False
    
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
            return None, None, False
        
        print(f"✅ Fallback 날짜 파싱 (다음 주): {next_monday.date()} ~ {end_date.date()}")
        return next_monday, end_date, False
    
    # 날짜 정보 없음
    else:
        return None, None, False