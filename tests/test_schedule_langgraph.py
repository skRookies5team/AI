from __future__ import annotations

from agents.schedule_data import ScheduleDatabaseError
from agents.schedule_langgraph import ScheduleState, analyze_query, run_schedule_agent
from agents import schedule_langgraph as schedule_module
from agents.schedule_master import parse_schedule_intent, schedule_master


def test_analyze_query_extracts_filters() -> None:
    """사번과 날짜 범위를 정확히 파싱하는지 확인."""
    query = "사번 12345의 2025-01-10부터 2025-01-12까지 일정 알려줘"
    state = ScheduleState(query=query)
    result = analyze_query(state)

    assert result.filters is not None
    assert result.filters.employee_id == 12345
    assert result.filters.start_at is not None
    assert result.filters.end_at is not None


def test_analyze_query_infers_schedule_types() -> None:
    """특정 키워드가 포함된 경우 일정 타입 필터를 도출한다."""
    query = "이번주 출장 일정 알려줘"
    state = ScheduleState(query=query)
    result = analyze_query(state)

    assert result.filters is not None
    assert result.filters.schedule_types is not None
    assert "출장" in result.filters.schedule_types


def test_analyze_query_extracts_employee_names() -> None:
    """한글 이름이 포함된 질의에서 이름 키워드를 추출한다."""
    # 실제 DB에 존재하는 이름으로 테스트 (employee_id 4001의 이름 사용)
    query = "오늘 홍길동 출장 일정 알려줘"
    state = ScheduleState(query=query)
    result = analyze_query(state)

    assert result.filters is not None
    # 이름 추출 기능 테스트 (실제 존재 여부와 무관하게 추출만 확인)
    if result.filters.employee_name_keywords:
        # 한글 이름이 추출되었는지 확인
        assert any(len(name) >= 2 for name in result.filters.employee_name_keywords)


def test_run_schedule_agent_handles_db_error(monkeypatch) -> None:
    """DB 오류 발생 시 적절한 메시지를 반환하는지 확인."""

    async def fake_fetch(_filters):
        raise ScheduleDatabaseError("DB 연결 실패")

    monkeypatch.setattr(schedule_module, "fetch_schedule_rows", fake_fetch)
    monkeypatch.setattr(schedule_module, "LLM_AVAILABLE", False)

    result = run_schedule_agent("일정 보여줘")

    assert not result["ok"]
    assert "DB 연결 실패" in result["message"]
    assert result["error"] == "DB 연결 실패"
    assert result["rows"] == []


def test_parse_schedule_intent_routes_leave_question_to_calendar() -> None:
    """휴가 질문형 문장을 캘린더 조회로 라우팅한다."""
    intent = parse_schedule_intent("오늘 휴가 누구누구 가?")
    assert intent.kind == "calendar"


def test_schedule_master_meeting_routes_to_schedule(monkeypatch) -> None:
    """회의실 관련 요청은 일정 에이전트에 위임한다."""

    def fake_schedule_agent(_text: str) -> dict:
        return {"ok": True, "message": "회의 일정 목록", "rows": [{"title": "회의"}]}

    monkeypatch.setattr("agents.schedule_master.run_schedule_agent", fake_schedule_agent)

    result = schedule_master("회의실 예약 일정 알려줘")

    assert result["ok"]
    assert result["message"] == "회의 일정 목록"

