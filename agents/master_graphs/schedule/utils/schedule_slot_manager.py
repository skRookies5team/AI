"""일정 Slot Manager - LLM 기반 슬롯 판단 유틸."""

from __future__ import annotations

from typing import Dict, Any

from agents.master_graphs.schedule.models.schedule_models import (
    ScheduleSlotDecision,
)
from agents.structured_output import extract_structured_output

SCHEDULE_SLOT_MANAGER_PROMPT = """당신은 일정 관리 도우미입니다.

사용자 입력과 현재까지 수집된 슬롯 정보를 기반으로 다음을 판단하세요:
1. 필요한 슬롯 중 아직 채워지지 않은 항목을 missing_slots에 명시
2. 추가 질문이 필요하면 should_ask=True로 설정하고 followup_question에 자연스러운 질문 작성
3. followup_question은 한 번에 하나의 슬롯만 묻습니다. (예: "일정 제목을 알려주세요.")
4. slot_state.awaiting_slots와 slot_state.last_question을 참고하여, 사용자가 직전에 받은 질문이 무엇인지 확인하고 그 질문에 대한 답변이라면 해당 슬롯을 우선 채웁니다.
5. 시간 표현(예: "내일 오후 2시", "12월 25일 14:00")이 있으면 start_time으로 파싱하세요.
6. "~까지", "1시간" 같은 표현이 있으면 end_time도 계산합니다.
7. 제목이 명시되지 않았지만 일정 내용이 있으면 title로 채웁니다.
8. 모든 필수 슬롯이 채워졌다면 should_ask=False로 설정하고 action에 수행해야 할 작업을 지정합니다.
9. filled_slots에는 현재까지의 슬롯 값을 JSON 형태로 포함합니다.

예시:
- awaiting_slots=['title'], query="팀 미팅"
  → filled_slots={"title": "팀 미팅"}
- awaiting_slots=['delete_selection'], query="2"
  → filled_slots={"selection_index": 2}
- query="내일 오후 2시에 회의", intent="생성"
  → filled_slots={"title": "회의", "start_time": "2024-12-13T14:00:00"}
- query="12월 25일 오후 2시부터 3시까지 브리핑", intent="생성"
  → filled_slots={"title": "브리핑", "start_time": "2024-12-25T14:00:00", "end_time": "2024-12-25T15:00:00"}

슬롯 스키마:
- title: 문자열 (일정 제목)
- description: 문자열 (일정 설명)
- location: 문자열 (장소)
- start_time: ISO-8601 datetime (예: 2024-12-25T14:00:00)
- end_time: ISO-8601 datetime
- selection_index: 정수 (삭제/수정 시 선택 번호)
- update_content: 문자열 (수정할 내용)

다음 형식의 JSON만 출력하세요."""


async def analyze_schedule_slots(
    query: str,
    slot_state: Dict[str, Any],
    *,
    temperature: float = 0.0,
    model_name: str = "gpt-4o-mini",
) -> ScheduleSlotDecision:
    """LLM을 호출하여 일정 슬롯 상태를 분석한다."""
    payload = {
        "query": query,
        "slot_state": slot_state,
    }
    return await extract_structured_output(
        query=str(payload),
        model_class=ScheduleSlotDecision,
        system_prompt=SCHEDULE_SLOT_MANAGER_PROMPT,
        temperature=temperature,
        model_name=model_name,
    )