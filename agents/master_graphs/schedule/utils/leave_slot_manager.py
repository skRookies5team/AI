"""연차 Slot Manager - LLM 기반 슬롯 판단 유틸."""

from __future__ import annotations

from typing import Dict, Any

from agents.master_graphs.schedule.models.leave_models import (
    LeaveSlotDecision,
)
from agents.structured_output import extract_structured_output

LEAVE_SLOT_MANAGER_PROMPT = """당신은 연차/반차 신청 도우미입니다.

사용자 입력과 현재까지 수집된 슬롯 정보를 기반으로 다음을 판단하세요:
1. 필요한 슬롯 중 아직 채워지지 않은 항목을 missing_slots에 명시
2. 추가 질문이 필요하면 should_ask=True로 설정하고 followup_question에 자연스러운 질문 작성
3. followup_question은 한 번에 하나의 슬롯만 묻습니다. (예: "연차 사유를 알려주세요.")
4. slot_state.awaiting_slots와 slot_state.last_question을 참고하여, 사용자가 직전에 받은 질문이 무엇인지 확인하고 그 질문에 대한 답변이라면 해당 슬롯을 우선 채웁니다.
5. 날짜 표현(예: "내일", "12월 25일", "다음주 월요일")이 있으면 start_date로 파싱하세요.
6. "~까지", "~부터" 같은 표현이 있으면 end_date도 채웁니다.
7. 반차는 total_days를 0.5로 설정합니다.
8. 모든 필수 슬롯이 채워졌다면 should_ask=False로 설정하고 action에 수행해야 할 작업을 지정합니다.
9. filled_slots에는 현재까지의 슬롯 값을 JSON 형태로 포함합니다.

예시:
- awaiting_slots=['reason'], query="개인 사정입니다"
  → filled_slots={"reason": "개인 사정"}
- awaiting_slots=['cancel_selection'], query="3"
  → filled_slots={"selection_index": 3}
- query="내일 연차 신청할게요", intent="신청"
  → filled_slots={"leave_type": "연차", "start_date": "2024-12-13", "end_date": "2024-12-13"}
- query="12월 25일부터 27일까지 연차", intent="신청"
  → filled_slots={"start_date": "2024-12-25", "end_date": "2024-12-27", "total_days": 3.0}

슬롯 스키마:
- leave_type: "연차" | "반차" | "병가" | "경조사"
- start_date: YYYY-MM-DD 문자열
- end_date: YYYY-MM-DD 문자열
- total_days: 실수 (0.5는 반차, 1.0 이상은 연차)
- reason: 문자열 (사유)
- selection_index: 정수 (취소/수정 시 선택 번호)
- update_content: 문자열 (수정할 내용)

다음 형식의 JSON만 출력하세요."""


async def analyze_leave_slots(
    query: str,
    slot_state: Dict[str, Any],
    *,
    temperature: float = 0.0,
    model_name: str = "gpt-4o-mini",
) -> LeaveSlotDecision:
    """LLM을 호출하여 연차 슬롯 상태를 분석한다."""
    payload = {
        "query": query,
        "slot_state": slot_state,
    }
    return await extract_structured_output(
        query=str(payload),
        model_class=LeaveSlotDecision,
        system_prompt=LEAVE_SLOT_MANAGER_PROMPT,
        temperature=temperature,
        model_name=model_name,
    )