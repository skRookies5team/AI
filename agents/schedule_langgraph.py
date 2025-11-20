from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Any, Optional

from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .llm_router import _get_env
from .schedule_data import (
    ScheduleDatabaseError,
    ScheduleFilters,
    fetch_schedule_rows,
    parse_iso_datetime,
)

try:
    from langchain_openai import ChatOpenAI

    LLM_AVAILABLE = True
except ImportError:  # pragma: no cover
    LLM_AVAILABLE = False


class ScheduleState(BaseModel):
    """LangGraph 실행 상태."""

    query: str = Field(description="사용자 질의")
    filters: Optional[ScheduleFilters] = Field(default=None, description="DB 조회 필터")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="조회 결과")
    response: Optional[str] = Field(default=None, description="LLM 응답")
    error: Optional[str] = Field(default=None, description="오류 메시지")
    name_search_failed: Optional[list[str]] = Field(
        default=None, description="이름 검색에 실패한 키워드 목록"
    )


@dataclass(slots=True)
class ScheduleQueryInsights:
    """자연어 질의에서 추출한 정보."""

    employee_id: Optional[int] = None
    employee_names: Optional[list[str]] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    keyword: Optional[str] = None
    schedule_types: Optional[list[str]] = None


def _extract_employee_id(text: str) -> Optional[int]:
    match = re.search(r"(사번|employee_id)\s*[:= ]*\s*(\d{3,})", text)
    if match:
        return int(match.group(2))
    return None


def _extract_keyword(text: str) -> Optional[str]:
    """질의에서 키워드를 추출한다. 날짜 표현과 이름은 제외한다."""
    # 날짜 표현 키워드 (제외할 단어들)
    date_keywords = {"오늘", "내일", "이번", "다음", "주", "달", "일정", "스케줄", "캘린더"}
    
    if "일정" in text or "스케줄" in text:
        tokens = re.split(r"\s+", text)
        try:
            idx = tokens.index("일정")
        except ValueError:
            idx = tokens.index("스케줄") if "스케줄" in tokens else -1
        if idx > 0:
            candidate = tokens[idx - 1]
            # 숫자가 아니고, 날짜 키워드가 아니고, 한글 이름 패턴이 아닌 경우만 키워드로 사용
            if (not candidate.isdigit() 
                and candidate not in date_keywords
                and not re.match(r"^[가-힣]{2,4}$", candidate)):  # 한글 이름 패턴 제외
                return candidate
    return None


def _infer_schedule_types(text: str) -> list[str]:
    mapping = {
        "출장": ["출장", "출장일정"],
        "휴가": ["휴가"],
        "반차": ["반차"],
        "병가": ["병가"],
        "회의실": ["회의", "회의실", "미팅"],
        "회의": ["회의", "미팅"],
        "할 일": ["업무", "할일", "할 일"],
        "업무": ["업무"],
        "TODO": ["업무", "할일"],
    }
    lower_text = text.lower()
    collected: list[str] = []
    for keyword, schedule_types in mapping.items():
        if keyword in text or keyword.lower() in lower_text:
            for value in schedule_types:
                if value not in collected:
                    collected.append(value)
    return collected


def _extract_employee_names(text: str) -> list[str]:
    """질의에서 한글 이름 후보를 추출한다.
    
    다양한 패턴을 지원:
    - "홍길동 일정 알려줘"
    - "홍길동님 일정"
    - "홍길동의 일정"
    - "홍길동 일정 있어?"
    """
    stopwords = {
        "오늘", "내일", "이번주", "일정", "휴가", "누구", "예약", "회의", "회의실",
        "출장", "할일", "할", "일", "있어", "없어", "사번", "알려줘", "알려",
        "보여줘", "보여", "찾아줘", "찾아", "조회", "신청", "반차", "병가",
        "경조사", "연차", "잔여", "업무", "스케줄", "캘린더", "의", "님", "이", "가",
        "얼마", "몇", "개", "일", "남았", "남은", "있나", "있어", "없어",
    }
    
    names: list[str] = []
    
    # 패턴 1: "이름 일정", "이름님 일정", "이름의 일정" 등
    # "님", "의"는 제외하고 이름만 추출
    patterns = [
        r"([가-힣]{2,4})(?:님|의)?\s*(?:일정|스케줄|캘린더|휴가|출장|회의)",
        r"(?:일정|스케줄|캘린더|휴가|출장|회의)\s*(?:을|를|이|가)?\s*([가-힣]{2,4})(?:님|의)?",
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            name_candidate = match.group(1).strip()
            # "님", "의"가 포함된 경우 제거
            name_candidate = name_candidate.rstrip("님의")
            if name_candidate and len(name_candidate) >= 2 and name_candidate not in stopwords:
                # 숫자와 함께 나오는 경우 제외 (예: "사번 4001")
                context_start = max(0, match.start() - 5)
                context_end = min(len(text), match.end() + 5)
                context = text[context_start:context_end]
                if not re.search(r"\d", context):
                    if name_candidate not in names:
                        names.append(name_candidate)
    
    # 패턴 2: 일반적인 한글 이름 추출 (패턴 1에서 찾지 못한 경우)
    if not names:
        candidates = re.findall(r"[가-힣]{2,4}", text)
        for cand in candidates:
            if cand in stopwords:
                continue
            if cand.endswith(("일", "주", "월", "년", "번", "줘", "요", "의", "님")):
                continue
            # 숫자와 함께 나오는 경우 제외
            cand_pos = text.find(cand)
            if cand_pos >= 0:
                context = text[max(0, cand_pos - 5):min(len(text), cand_pos + len(cand) + 5)]
                if re.search(r"\d", context):
                    continue
            if cand not in names:
                names.append(cand)
    
    return names


def _parse_relative_date(text: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """상대적인 날짜 표현을 파싱하여 시작일과 종료일을 반환한다.
    
    지원하는 표현:
    - 오늘: 오늘 00:00:00 ~ 오늘 23:59:59
    - 내일: 내일 00:00:00 ~ 내일 23:59:59
    - 이번 주: 이번 주 월요일 00:00:00 ~ 일요일 23:59:59
    - 다음 주: 다음 주 월요일 00:00:00 ~ 일요일 23:59:59
    - 이번 달: 이번 달 1일 00:00:00 ~ 마지막 날 23:59:59
    - 다음 달: 다음 달 1일 00:00:00 ~ 마지막 날 23:59:59
    """
    from datetime import timedelta
    
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0)
    today_end = datetime(now.year, now.month, now.day, 23, 59, 59)
    
    # 오늘
    if "오늘" in text:
        return today_start, today_end
    
    # 내일
    if "내일" in text:
        tomorrow = today_start + timedelta(days=1)
        tomorrow_end = tomorrow.replace(hour=23, minute=59, second=59)
        return tomorrow, tomorrow_end
    
    # 이번 주 (월요일 ~ 일요일)
    if "이번 주" in text or "이번주" in text:
        # 오늘의 요일 (0=월요일, 6=일요일)
        days_since_monday = now.weekday()
        week_start = today_start - timedelta(days=days_since_monday)
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return week_start, week_end
    
    # 다음 주
    if "다음 주" in text or "다음주" in text:
        days_since_monday = now.weekday()
        next_week_start = today_start - timedelta(days=days_since_monday) + timedelta(days=7)
        next_week_end = next_week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return next_week_start, next_week_end
    
    # 이번 달
    if "이번 달" in text or "이번달" in text:
        month_start = datetime(now.year, now.month, 1, 0, 0, 0)
        # 다음 달 1일에서 1일 빼면 이번 달 마지막 날
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1)
        else:
            next_month = datetime(now.year, now.month + 1, 1)
        month_end = next_month - timedelta(days=1)
        month_end = month_end.replace(hour=23, minute=59, second=59)
        return month_start, month_end
    
    # 다음 달
    if "다음 달" in text or "다음달" in text:
        if now.month == 12:
            next_month_start = datetime(now.year + 1, 1, 1, 0, 0, 0)
            next_month_end = datetime(now.year + 1, 2, 1) - timedelta(days=1)
        else:
            next_month_start = datetime(now.year, now.month + 1, 1, 0, 0, 0)
            next_month_end = datetime(now.year, now.month + 2, 1) - timedelta(days=1)
        next_month_end = next_month_end.replace(hour=23, minute=59, second=59)
        return next_month_start, next_month_end
    
    return None, None


def _extract_dates(text: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """질의에서 날짜 정보를 추출한다.
    
    ISO 포맷 날짜와 상대적인 날짜 표현을 모두 지원한다.
    """
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    
    # 먼저 상대적인 날짜 표현 확인
    rel_start, rel_end = _parse_relative_date(text)
    if rel_start and rel_end:
        return rel_start, rel_end
    
    # ISO 포맷 날짜 패턴
    patterns = [
        r"(\d{4}-\d{2}-\d{2})\s*(부터|이후)",
        r"(\d{4}-\d{2}-\d{2})\s*(까지|이전)",
        r"(\d{4}-\d{2}-\d{2})\s*(~|-|~)\s*(\d{4}-\d{2}-\d{2})",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 2:
            date_str, token = groups
            if token in {"부터", "이후"}:
                start = parse_iso_datetime(date_str)
            elif token in {"까지", "이전"}:
                end = parse_iso_datetime(date_str)
        elif len(groups) == 3:
            start = parse_iso_datetime(groups[0])
            end = parse_iso_datetime(groups[2])
    
    return start, end


def analyze_query(state: ScheduleState) -> ScheduleState:
    """사용자 질의를 구조화된 필터로 변환한다."""
    text = state.query.strip()
    insights = ScheduleQueryInsights(
        employee_id=_extract_employee_id(text),
        keyword=None,  # 일단 None으로 초기화
    )
    start, end = _extract_dates(text)
    insights.start_at = start
    insights.end_at = end
    schedule_types = _infer_schedule_types(text)
    if schedule_types:
        insights.schedule_types = schedule_types
    employee_names = _extract_employee_names(text)
    if employee_names:
        insights.employee_names = employee_names

    # employee_id가 있으면 employee_name_keywords 무시 (중복 필터 방지)
    # employee_id가 없을 때만 이름으로 검색
    name_keywords = None
    if not insights.employee_id and insights.employee_names:
        name_keywords = insights.employee_names
    
    # 키워드 추출: 날짜나 이름이 추출된 경우 키워드 필터를 사용하지 않음
    # (날짜는 start_at/end_at으로, 이름은 employee_id로 필터링하므로 키워드 검색이 불필요)
    use_keyword = True
    if start is not None or end is not None:
        # 날짜 필터가 있으면 키워드 필터 사용 안 함
        use_keyword = False
    if name_keywords:
        # 이름 필터가 있으면 키워드 필터 사용 안 함 (이름이 키워드로 오인될 수 있음)
        use_keyword = False
    
    if use_keyword:
        extracted_keyword = _extract_keyword(text)
        if name_keywords and extracted_keyword:
            # 추출된 키워드가 이름과 겹치면 제외
            keyword_words = extracted_keyword.split()
            filtered_keywords = [
                kw for kw in keyword_words 
                if kw not in name_keywords and not any(name in kw or kw in name for name in name_keywords)
            ]
            if filtered_keywords:
                insights.keyword = " ".join(filtered_keywords)
            else:
                insights.keyword = None
        else:
            insights.keyword = extracted_keyword
    else:
        insights.keyword = None
    
    state.filters = ScheduleFilters(
        employee_id=insights.employee_id,
        start_at=insights.start_at,
        end_at=insights.end_at,
        keyword=insights.keyword,
        schedule_types=insights.schedule_types,
        employee_name_keywords=name_keywords,
    )
    return state


async def load_schedule(state: ScheduleState) -> ScheduleState:
    """MariaDB에서 일정 데이터를 조회한다."""
    if state.filters is None:
        state.filters = ScheduleFilters()
    
    # 이름 검색: employee_name_keywords가 있으면 먼저 employee_ids로 변환
    if (
        state.filters.employee_name_keywords
        and not state.filters.employee_id
        and not state.filters.employee_ids
    ):
        from agents.schedule_data import get_session, _resolve_employee_ids_by_names
        try:
            async with get_session() as session:
                resolved_ids = await _resolve_employee_ids_by_names(
                    session, state.filters.employee_name_keywords
                )
                if resolved_ids:
                    state.filters.employee_ids = resolved_ids
                else:
                    # 이름 검색 실패
                    state.name_search_failed = state.filters.employee_name_keywords
        except Exception as exc:
            state.error = f"이름 검색 중 오류 발생: {exc}"
            return state
    
    # 일정 조회
    try:
        state.rows = await fetch_schedule_rows(state.filters)
    except ScheduleDatabaseError as exc:
        state.error = str(exc)
    return state


def _format_rows_for_prompt(rows: list[dict[str, Any]]) -> str:
    """일정 데이터를 LLM 프롬프트용 텍스트로 포맷팅. JOIN된 employee_name을 우선 사용."""
    if not rows:
        return "조회 결과가 없습니다."
    lines: list[str] = []
    for row in rows:
        title = row.get("title") or row.get("subject") or "제목 미지정"
        start = row.get("start_time") or row.get("start_at") or row.get("start_date")
        end = row.get("end_time") or row.get("end_at") or row.get("end_date")
        # JOIN된 employee_name을 우선 사용 (없으면 다른 필드 시도)
        owner = (
            row.get("employee_name")  # JOIN으로 가져온 이름 (우선)
            or row.get("owner")
            or row.get("created_by")
            or "미확인"
        )
        location = row.get("location") or row.get("place") or "미지정"
        schedule_type = row.get("schedule_type") or row.get("type") or row.get("category")
        employee_id = row.get("employee_id")
        
        # 더 상세한 정보 포함
        info_parts = [f"제목: {title}"]
        if schedule_type:
            info_parts.append(f"유형: {schedule_type}")
        info_parts.append(f"시작: {start}")
        info_parts.append(f"종료: {end}")
        info_parts.append(f"담당자: {owner}")
        if employee_id:
            info_parts.append(f"(사번: {employee_id})")
        info_parts.append(f"장소: {location}")
        
        lines.append("- " + ", ".join(info_parts))
    return "\n".join(lines)


def respond(state: ScheduleState) -> ScheduleState:
    """LLM 또는 단순 포맷터로 최종 응답을 만든다."""
    if state.error:
        state.response = state.error
        return state
    
    # 이름 검색 실패 시 더 명확한 메시지 제공
    if not state.rows and state.name_search_failed:
        failed_names = ", ".join(state.name_search_failed)
        state.response = (
            f"'{failed_names}' 이름의 직원을 찾을 수 없습니다. "
            f"사번으로 검색하시거나 정확한 이름을 입력해 주세요. "
            f"(예: '사번 4001 일정 알려줘')"
        )
        return state
    
    if not state.rows:
        # 일반적인 경우: 필터 조건이 너무 엄격하거나 데이터가 없음
        filter_info = []
        if state.filters:
            if state.filters.employee_id:
                filter_info.append(f"사번 {state.filters.employee_id}")
            if state.filters.start_at:
                filter_info.append(f"시작일 {state.filters.start_at}")
            if state.filters.end_at:
                filter_info.append(f"종료일 {state.filters.end_at}")
            if state.filters.schedule_types:
                filter_info.append(f"유형 {', '.join(state.filters.schedule_types)}")
        
        if filter_info:
            state.response = f"조회된 일정이 없습니다. (조건: {', '.join(filter_info)})"
        else:
            state.response = "조회된 일정이 없습니다."
        return state

    if not LLM_AVAILABLE:
        state.response = _format_rows_for_prompt(state.rows)
        return state

    api_key = _get_env("OPENAI_API_KEY")
    if not api_key:
        state.response = _format_rows_for_prompt(state.rows)
        return state

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "너는 사내 일정 관리 도우미야. 주어진 일정 목록을 한국어로 이해하기 쉽게 요약해줘. "
                "각 일정에는 담당자 이름(employee_name)과 사번이 포함되어 있으니, 이를 활용해서 "
                "사용자가 누구의 일정을 찾는지 명확하게 알 수 있도록 응답해줘.",
            ),
            ("human", "사용자 질의: {query}\n일정 목록:\n{rows}"),
        ]
    )
    rendered_rows = _format_rows_for_prompt(state.rows)
    try:
        if not LLM_AVAILABLE:
            state.response = rendered_rows
            return state
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, api_key=api_key)
        chain = prompt | llm
        result = chain.invoke({"query": state.query, "rows": rendered_rows})
        state.response = result.content if hasattr(result, "content") else str(result)
    except (ImportError, NameError) as exc:
        # langchain_openai import 오류 처리
        if "langchain_openai" in str(exc) or "ChatOpenAI" in str(exc):
            state.response = rendered_rows
        else:
            state.response = f"LLM 응답 생성 실패: {exc}"
    except Exception as exc:  # pragma: no cover
        state.response = f"LLM 응답 생성 실패: {exc}"
    return state


def build_schedule_workflow() -> StateGraph:
    """LangGraph 워크플로우를 구성한다."""
    graph = StateGraph(ScheduleState)
    graph.add_node("analyze", analyze_query)
    graph.add_node("load", load_schedule)
    graph.add_node("respond", respond)

    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "load")
    graph.add_edge("load", "respond")
    graph.add_edge("respond", END)
    return graph


async def _run_workflow(query: str) -> Any:
    try:
        workflow = build_schedule_workflow().compile()
        state = {"query": query}
        return await workflow.ainvoke(state)
    except (ImportError, NameError) as e:
        # langchain_openai 등 import 오류 처리
        error_msg = str(e)
        if "langchain_openai" in error_msg or "ChatOpenAI" in error_msg:
            # LLM 없이 기본 조회 시도
            filters = ScheduleFilters(limit=20)
            rows = await fetch_schedule_rows(filters)
            if rows:
                formatted = _format_rows_for_prompt(rows)
                return {"response": formatted, "rows": rows, "error": None}
            else:
                return {"response": "조회된 일정이 없습니다.", "rows": [], "error": None}
        raise


def _normalize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        response = result.get("response")
        rows = result.get("rows", [])
        error = result.get("error")
    else:
        response = getattr(result, "response", None)
        rows = getattr(result, "rows", [])
        error = getattr(result, "error", None)

    ok = bool(response) and not error
    message = error or response or "응답을 생성하지 못했습니다."
    return {"ok": ok, "message": message, "rows": rows, "error": error}


def run_schedule_agent(query: str) -> dict[str, Any]:
    """LangGraph 기반 스케줄 조회 싱크 헬퍼."""
    import concurrent.futures
    import warnings
    import logging
    
    # SQLAlchemy 연결 종료 시 발생하는 경고/오류 억제
    # CRITICAL 레벨로 설정하여 모든 로그 억제
    sqlalchemy_loggers = [
        "sqlalchemy.pool",
        "sqlalchemy.engine",
        "sqlalchemy.dialects",
        "sqlalchemy.connectors",
        "sqlalchemy.util",
        "sqlalchemy",
    ]
    for logger_name in sqlalchemy_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.CRITICAL)
        # 핸들러 제거하여 로그 출력 방지
        logger.handlers = []
        logger.propagate = False
    
    # asyncmy 관련 로거도 억제
    asyncmy_logger = logging.getLogger("asyncmy")
    asyncmy_logger.setLevel(logging.CRITICAL)
    asyncmy_logger.handlers = []
    asyncmy_logger.propagate = False
    
    # 이벤트 루프 실행 헬퍼 함수
    def run_in_thread():
        """별도 스레드에서 이벤트 루프 실행."""
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        old_stderr = sys.stderr
        old_stdout = sys.stdout
        old_excepthook = sys.excepthook
        
        # 예외 핸들러를 오버라이드하여 SQLAlchemy 연결 종료 오류 무시
        def custom_excepthook(exc_type, exc_value, exc_traceback):
            """SQLAlchemy 연결 종료 관련 오류는 무시."""
            if exc_type is AttributeError and "'NoneType' object has no attribute 'send'" in str(exc_value):
                return  # 무시
            if exc_type is RuntimeError and "no running event loop" in str(exc_value):
                return  # 무시
            # 다른 예외는 기본 핸들러 사용
            if old_excepthook:
                old_excepthook(exc_type, exc_value, exc_traceback)
        
        try:
            sys.excepthook = custom_excepthook
            # stderr와 stdout 모두 리다이렉트하여 오류 메시지 억제
            sys.stderr = StringIO()
            sys.stdout = StringIO()
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore")
                result = new_loop.run_until_complete(_run_workflow(query))
                return result
        finally:
            sys.excepthook = old_excepthook
            sys.stderr = old_stderr
            sys.stdout = old_stdout
            try:
                # 모든 pending tasks 취소
                pending = asyncio.all_tasks(new_loop)
                for task in pending:
                    task.cancel()
                # 루프 종료 전에 잠시 대기
                if pending:
                    new_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                new_loop.close()
            except Exception:
                pass  # 연결 종료 오류 무시
            asyncio.set_event_loop(None)
    
    # 현재 실행 중인 이벤트 루프 확인
    try:
        loop = asyncio.get_running_loop()
        # 실행 중인 루프가 있으면 별도 스레드에서 실행
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            result = future.result(timeout=30)  # 30초 타임아웃
    except RuntimeError as e:
        # "no running event loop"는 정상적인 경우이므로 asyncio.run 사용
        if "no running event loop" in str(e).lower():
            try:
                # SQLAlchemy 연결 종료 시 발생하는 경고 억제
                old_stderr = sys.stderr
                old_stdout = sys.stdout
                old_excepthook = sys.excepthook
                
                # 예외 핸들러를 오버라이드하여 SQLAlchemy 연결 종료 오류 무시
                def custom_excepthook(exc_type, exc_value, exc_traceback):
                    """SQLAlchemy 연결 종료 관련 오류는 무시."""
                    if exc_type is AttributeError and "'NoneType' object has no attribute 'send'" in str(exc_value):
                        return  # 무시
                    if exc_type is RuntimeError and "no running event loop" in str(exc_value):
                        return  # 무시
                    # 다른 예외는 기본 핸들러 사용
                    if old_excepthook:
                        old_excepthook(exc_type, exc_value, exc_traceback)
                
                try:
                    sys.excepthook = custom_excepthook
                    sys.stderr = StringIO()
                    sys.stdout = StringIO()
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore")
                        result = asyncio.run(_run_workflow(query))
                finally:
                    sys.excepthook = old_excepthook
                    sys.stderr = old_stderr
                    sys.stdout = old_stdout
            except RuntimeError as exc:
                # asyncio.run도 실패하면 별도 스레드에서 실행
                if "event loop" in str(exc).lower() or "Cannot run" in str(exc):
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(run_in_thread)
                        result = future.result(timeout=30)
                else:
                    raise
        else:
            # 다른 RuntimeError는 재발생
            raise
    except ImportError as e:
        # langchain_openai 등 import 오류 처리
        error_msg = str(e)
        if "langchain_openai" in error_msg or "ChatOpenAI" in error_msg:
            # LLM 없이 기본 조회 시도
            try:
                from .leave_data import _run
                async def simple_query():
                    filters = ScheduleFilters(limit=20)
                    return await fetch_schedule_rows(filters)
                
                rows = _run(simple_query())
                if rows:
                    formatted = _format_rows_for_prompt(rows)
                    return {"ok": True, "message": formatted, "rows": rows}
                else:
                    return {"ok": True, "message": "조회된 일정이 없습니다.", "rows": []}
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"일정 조회 중 오류가 발생했습니다: {exc}",
                    "error": str(exc),
                    "rows": [],
                }
        raise
    except Exception as e:
        # 기타 예외 처리
        error_msg = str(e)
        if "langchain_openai" in error_msg or "ChatOpenAI" in error_msg:
            # LLM 없이 기본 조회 시도
            try:
                from .leave_data import _run
                async def simple_query():
                    filters = ScheduleFilters(limit=20)
                    return await fetch_schedule_rows(filters)
                
                rows = _run(simple_query())
                if rows:
                    formatted = _format_rows_for_prompt(rows)
                    return {"ok": True, "message": formatted, "rows": rows}
                else:
                    return {"ok": True, "message": "조회된 일정이 없습니다.", "rows": []}
            except Exception as exc:
                return {
                    "ok": False,
                    "message": f"일정 조회 중 오류가 발생했습니다: {exc}",
                    "error": str(exc),
                    "rows": [],
                }
        # 다른 예외는 재발생
        raise
    
    return _normalize_result(result)

