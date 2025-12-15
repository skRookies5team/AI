"""회의실 Slot Manager - LLM 기반 슬롯 판단 유틸."""

from __future__ import annotations

from typing import Dict, Any

from agents.master_graphs.schedule.models.meeting_room_models import (
    MeetingSlotDecision,
)
from agents.structured_output import extract_structured_output

SLOT_MANAGER_PROMPT = """당신은 회의실 예약 도우미입니다.

사용자 입력과 현재까지 수집된 슬롯 정보를 기반으로 다음을 판단하세요.:
1. 필요한 슬롯 중 아직 채워지지 않은 항목을 missing_slots에 명시
2. 추가 질문이 필요하면 should_ask=True로 설정하고 followup_question에 자연스러운 질문 작성
3. followup_question은 한 번에 하나의 슬롯만 묻습니다. (예: "회의 제목을 알려주세요.")
4. slot_state.awaiting_slots와 slot_state.last_question을 참고하여, 사용자가 직전에 받은 질문이 무엇인지 확인하고 그 질문에 대한 답변이라면 해당 슬롯을 우선 채웁니다.
   - 예: last_question이 "회의 제목을 알려주세요."이고 사용자가 "오후 미팅"이라고 답했다면, 숫자가 없어도 제목으로 기록합니다.
5. awaiting_slots에 "description"이 포함되고 last_question이 회의 설명을 묻고 있다면, 사용자가 말한 문장을 description 슬롯에 그대로 저장하세요. 시간 표현이 함께 있더라도 description을 우선 채우고, 명시적인 시간 표현은 start_time/end_time 파싱에도 활용합니다.
6. 시간(start_time/end_time)을 채울 때는 반드시 명시적인 시간 표현(예: "오후 2시", "14시", "2시간", "~까지", "부터")이 있는지 확인하세요. 숫자나 시간 관련 표현이 전혀 없는 명사구는 제목이나 목적(description/purpose) 후보로 우선 판단합니다.
7. awaiting_slots에 "room"이 포함되고 last_question이 회의실을 묻고 있다면, 사용자가 말한 문장을 그대로 room/room_name 슬롯에 저장하고 followup을 반복하지 마세요.
8. 모든 필수 슬롯이 채워졌다면 should_ask=False로 설정하고 action에 수행해야 할 작업을 지정합니다.
9. filled_slots에는 현재까지의 슬롯 값을 JSON 형태로 포함합니다.

예시:
- awaiting_slots=['title'], query="오후 미팅"
  → filled_slots={"title": "오후 미팅"}
- awaiting_slots=['end_time'], query="내일 오후 4시", 기존 start_time="2025-12-10T14:00:00"
  → filled_slots={"end_time": "2025-12-10T16:00:00"} (start_time은 유지)
- awaiting_slots=['room'], query="B동 대회의실 1"
  → filled_slots={"room_name": "B동 대회의실 1", "room_code": "B동 대회의실 1"}
- last_question: "회의 시작 시간을 알려주세요." / user_input: "내일 오후 2시"
  → filled_slots={"start_time": "2025-12-10T14:00:00"}
- 명시적인 시간 표현이 없고 단순 명사구(예: "브리핑", "디자인 회의")라면 title 또는 purpose 슬롯을 채웁니다.

슬롯 스키마:
- title: 문자열
- description: 문자열 (회의 목적/안건 등)
- start_time: ISO-8601 datetime (예: 2025-12-10T14:00:00)
- end_time: ISO-8601 datetime
- duration_minutes: 정수 (분 단위)
- participants: 정수 (참석 인원)
- room: 문자열 (회의실 이름 또는 코드)
- room_index: 정수 (추천 목록 중 선택 번호)
- purpose: 문자열 (선택 사항)
- requires_video / requires_projector / requires_whiteboard: 불린

다음 형식의 JSON만 출력하세요."""


async def analyze_meeting_slots(
    query: str,
    slot_state: Dict[str, Any],
    *,
    temperature: float = 0.0,
    model_name: str = "gpt-4o-mini",
) -> MeetingSlotDecision:
    """LLM을 호출하여 슬롯 상태를 분석한다."""
    payload = {
        "query": query,
        "slot_state": slot_state,
    }
    return await extract_structured_output(
        query=str(payload),
        model_class=MeetingSlotDecision,
        system_prompt=SLOT_MANAGER_PROMPT,
        temperature=temperature,
        model_name=model_name,
    )
