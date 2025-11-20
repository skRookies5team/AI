from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

from .leave_agent import LeaveQuery, handle_leave
from .llm_router import classify_intent_with_llm
from .schedule_api import cancel_schedule, create_schedule
from .schedule_langgraph import run_schedule_agent, _extract_employee_names
from .schedule_data import ScheduleFilters, _resolve_employee_ids_by_names, fetch_schedule_rows, get_session


LeaveType = Literal["연차", "반차", "병가", "경조사", "휴가"]


def _extract_schedule_info_with_llm(user_text: str) -> Optional[dict[str, Any]]:
    """LLM을 사용하여 자연어에서 일정 관련 정보를 추출한다.

    Args:
        user_text: 사용자 입력 텍스트

    Returns:
        추출된 정보 딕셔너리 (action, employee_name, schedule_type, title, start_time, end_time, location 등) 또는 None
    """
    import os
    from langchain_openai import ChatOpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        prompt = f"""사용자의 질의를 분석하여 일정 관련 정보를 추출하세요.

질의: {user_text}

다음 정보를 추출하세요:
1. action: "등록", "조회", "취소" 중 하나 (없으면 "조회")
2. employee_name: 직원 이름 (한글 이름, 없으면 null)
3. employee_id: 사번 (숫자, 없으면 null)
4. schedule_type: 일정 유형 ("출장", "업무", "회의", "휴가", "연차" 등, 없으면 null)
5. title: 일정 제목 (없으면 null)
6. start_time: 시작 시간 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM 형식, 없으면 null)
7. end_time: 종료 시간 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM 형식, 없으면 null)
8. location: 장소 (없으면 null)
9. description: 설명 (없으면 null)
10. schedule_id: 일정 ID (숫자, 없으면 null)

예시:
- "강하은 출장 일정 알려줘" -> {{"action": "조회", "employee_name": "강하은", "schedule_type": "출장", ...}}
- "사번 4001 일정 등록해줘 제목: 출장 시작: 2025-12-05 종료: 2025-12-07" -> {{"action": "등록", "employee_id": 4001, "title": "출장", "start_time": "2025-12-05", "end_time": "2025-12-07", ...}}
- "일정 추가해줘" -> {{"action": "등록", ...}}
- "오늘 일정 보여줘" -> {{"action": "조회", ...}}
- "회의실 예약해줘" -> {{"action": "등록", "schedule_type": "회의", ...}}
- "윤서준 회의 일정 있어?" -> {{"action": "조회", "employee_name": "윤서준", "schedule_type": "회의", ...}}

JSON 형식으로 응답하세요: {{"action": "...", "employee_name": "..." 또는 null, "employee_id": 숫자 또는 null, "schedule_type": "..." 또는 null, "title": "..." 또는 null, "start_time": "..." 또는 null, "end_time": "..." 또는 null, "location": "..." 또는 null, "description": "..." 또는 null, "schedule_id": 숫자 또는 null}}"""

        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=api_key,
        )

        response = llm.invoke(prompt)
        content = response.content.strip()

        # JSON 파싱
        import json
        import re

        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
        if json_match:
            try:
                result = json.loads(json_match.group())

                action = result.get("action", "조회")
                if action not in ["등록", "조회", "취소"]:
                    action = "조회"

                employee_name = result.get("employee_name")
                if employee_name:
                    employee_name = str(employee_name).strip()
                    if not employee_name or employee_name.lower() == "null":
                        employee_name = None

                employee_id = result.get("employee_id")
                if employee_id is not None:
                    try:
                        employee_id = int(employee_id)
                    except (TypeError, ValueError):
                        employee_id = None

                schedule_type = result.get("schedule_type")
                if schedule_type:
                    schedule_type = str(schedule_type).strip()
                    if not schedule_type or schedule_type.lower() == "null":
                        schedule_type = None

                title = result.get("title")
                if title:
                    title = str(title).strip()
                    if not title or title.lower() == "null":
                        title = None

                start_time = result.get("start_time")
                if start_time:
                    start_time = str(start_time).strip()
                    if not start_time or start_time.lower() == "null":
                        start_time = None

                end_time = result.get("end_time")
                if end_time:
                    end_time = str(end_time).strip()
                    if not end_time or end_time.lower() == "null":
                        end_time = None

                location = result.get("location")
                if location:
                    location = str(location).strip()
                    if not location or location.lower() == "null":
                        location = None

                description = result.get("description")
                if description:
                    description = str(description).strip()
                    if not description or description.lower() == "null":
                        description = None

                schedule_id = result.get("schedule_id")
                if schedule_id is not None:
                    try:
                        schedule_id = int(schedule_id)
                    except (TypeError, ValueError):
                        schedule_id = None

                return {
                    "action": action,
                    "employee_name": employee_name,
                    "employee_id": employee_id,
                    "schedule_type": schedule_type,
                    "title": title,
                    "start_time": start_time,
                    "end_time": end_time,
                    "location": location,
                    "description": description,
                    "schedule_id": schedule_id,
                }
            except json.JSONDecodeError:
                pass

        # JSON 파싱 실패 시 기본값 반환
        return {
            "action": "조회",
            "employee_name": None,
            "employee_id": None,
            "schedule_type": None,
            "title": None,
            "start_time": None,
            "end_time": None,
            "location": None,
            "description": None,
            "schedule_id": None,
        }
    except Exception:
        return None


def _extract_schedule_cancel_info_with_llm(user_text: str) -> Optional[dict[str, Any]]:
    """LLM을 사용하여 자연어에서 일정 취소 관련 정보를 추출한다.

    Args:
        user_text: 사용자 입력 텍스트

    Returns:
        추출된 정보 딕셔너리 (schedule_id, employee_name, schedule_type, reason) 또는 None
    """
    import os
    from langchain_openai import ChatOpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        prompt = f"""사용자의 질의를 분석하여 일정 취소 관련 정보를 추출하세요.

질의: {user_text}

다음 정보를 추출하세요:
1. is_cancel: 일정 취소 요청인지 여부 (true/false)
2. schedule_id: 일정 ID (숫자, 없으면 null)
3. employee_name: 직원 이름 (한글 이름, 없으면 null)
4. schedule_type: 일정 유형 ("출장", "업무", "회의", "휴가", "연차" 등, 없으면 null)
5. reason: 취소 사유 (없으면 null)

예시:
- "강하은 출장 업무 취소해줘" -> {{"is_cancel": true, "schedule_id": null, "employee_name": "강하은", "schedule_type": "출장", "reason": null}}
- "사번 4001 출장 일정 취소해줘" -> {{"is_cancel": true, "schedule_id": null, "employee_name": null, "schedule_type": "출장", "reason": null}}
- "일정 2212 취소해줘" -> {{"is_cancel": true, "schedule_id": 2212, "employee_name": null, "schedule_type": null, "reason": null}}
- "윤서준 회의 일정 삭제해줘" -> {{"is_cancel": true, "schedule_id": null, "employee_name": "윤서준", "schedule_type": "회의", "reason": null}}
- "강하은 일정 취소" -> {{"is_cancel": true, "schedule_id": null, "employee_name": "강하은", "schedule_type": null, "reason": null}}
- "일정 조회해줘" -> {{"is_cancel": false, "schedule_id": null, "employee_name": null, "schedule_type": null, "reason": null}}

JSON 형식으로 응답하세요: {{"is_cancel": true/false, "schedule_id": 숫자 또는 null, "employee_name": "이름" 또는 null, "schedule_type": "유형" 또는 null, "reason": "사유" 또는 null}}"""

        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=api_key,
        )

        response = llm.invoke(prompt)
        content = response.content.strip()

        # JSON 파싱
        import json
        import re

        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
        if json_match:
            try:
                result = json.loads(json_match.group())
                is_cancel = result.get("is_cancel", False)

                if not is_cancel:
                    return None

                schedule_id = result.get("schedule_id")
                if schedule_id is not None:
                    try:
                        schedule_id = int(schedule_id)
                    except (TypeError, ValueError):
                        schedule_id = None

                employee_name = result.get("employee_name")
                if employee_name:
                    employee_name = str(employee_name).strip()
                    if not employee_name or employee_name.lower() == "null":
                        employee_name = None

                schedule_type = result.get("schedule_type")
                if schedule_type:
                    schedule_type = str(schedule_type).strip()
                    if not schedule_type or schedule_type.lower() == "null":
                        schedule_type = None

                reason = result.get("reason")
                if reason:
                    reason = str(reason).strip()
                    if not reason or reason.lower() == "null":
                        reason = None

                return {
                    "is_cancel": True,
                    "schedule_id": schedule_id,
                    "employee_name": employee_name,
                    "schedule_type": schedule_type,
                    "reason": reason,
                }
            except json.JSONDecodeError:
                pass

        # JSON 파싱 실패 시 텍스트에서 직접 추출 시도
        cancel_keywords = ["취소", "삭제", "취소해", "삭제해", "취소할", "삭제할", "취소해줘", "삭제해줘"]
        is_cancel = any(k in user_text for k in cancel_keywords)
        if not is_cancel:
            return None

        # schedule_id 추출
        schedule_id = None
        m_id = re.search(r"(일정\s*ID|schedule\s*id|일정\s*번호|일정)\s*[:= ]?\s*(\d{3,})", user_text, re.IGNORECASE)
        if m_id:
            schedule_id = int(m_id.group(2))
        else:
            m_id2 = re.search(r"\b(\d{3,})\b", user_text)
            if m_id2:
                schedule_id = int(m_id2.group(1))

        # employee_name 추출
        employee_name = None
        employee_names = _extract_employee_names(user_text)
        if employee_names:
            employee_name = employee_names[0]

        # schedule_type 추출
        schedule_type = None
        for stype in ["출장", "업무", "회의", "휴가", "연차"]:
            if stype in user_text:
                schedule_type = stype
                break

        # reason 추출
        reason = None
        m_reason = re.search(r"(사유|reason)\s*[:=]\s*['\"]?([^'\"]+)['\"]?", user_text, re.IGNORECASE)
        if m_reason:
            reason = m_reason.group(2).strip()

        return {
            "is_cancel": True,
            "schedule_id": schedule_id,
            "employee_name": employee_name,
            "schedule_type": schedule_type,
            "reason": reason,
        }
    except Exception:
        return None


def _extract_leave_info_with_llm(user_text: str) -> Optional[dict[str, Any]]:
    """LLM을 사용하여 자연어에서 휴가 관련 정보를 추출한다.

    Args:
        user_text: 사용자 입력 텍스트

    Returns:
        추출된 정보 딕셔너리 (leave_type, action, is_balance) 또는 None
    """
    import os
    from langchain_openai import ChatOpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        prompt = f"""사용자의 질의를 분석하여 휴가 관련 정보를 추출하세요.

질의: {user_text}

다음 정보를 추출하세요:
1. leave_type: "연차", "반차", "병가", "경조사", "휴가" 중 하나 (없으면 "연차")
2. action: "조회" 또는 "신청" (없으면 "조회")
3. is_balance: 잔여 연차 조회인지 여부 (true/false)
   - "잔여", "남았", "남은", "얼마", "몇 개", "몇 일", "현황" 등의 키워드가 있으면 true
   - "신청", "사용", "쓴", "내역", "목록" 등의 키워드가 있으면 false

예시:
- "반차 신청했어?" -> {{"leave_type": "반차", "action": "조회", "is_balance": false}}
- "병가 사용했나?" -> {{"leave_type": "병가", "action": "조회", "is_balance": false}}
- "연차 잔여 얼마나 남았어?" -> {{"leave_type": "연차", "action": "조회", "is_balance": true}}
- "잔여 연차 조회" -> {{"leave_type": "연차", "action": "조회", "is_balance": true}}
- "연차 잔여" -> {{"leave_type": "연차", "action": "조회", "is_balance": true}}
- "연차 현황" -> {{"leave_type": "연차", "action": "조회", "is_balance": true}}
- "연차 몇 개 남았어?" -> {{"leave_type": "연차", "action": "조회", "is_balance": true}}
- "반차 현황 알려줘" -> {{"leave_type": "반차", "action": "조회", "is_balance": false}}
- "병가 신청 내역 보여줘" -> {{"leave_type": "병가", "action": "조회", "is_balance": false}}
- "휴가 쓴 적 있어?" -> {{"leave_type": "휴가", "action": "조회", "is_balance": false}}

JSON 형식으로 응답하세요: {{"leave_type": "...", "action": "...", "is_balance": true/false}}"""

        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=api_key,
        )

        response = llm.invoke(prompt)
        content = response.content.strip()

        # JSON 파싱
        import json
        import re

        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
        if json_match:
            try:
                result = json.loads(json_match.group())
                leave_type = result.get("leave_type", "연차")
                action = result.get("action", "조회")
                is_balance = result.get("is_balance", False)

                # 유효성 검사
                if leave_type not in ["연차", "반차", "병가", "경조사", "휴가"]:
                    leave_type = "연차"
                if action not in ["조회", "신청"]:
                    action = "조회"

                return {
                    "leave_type": leave_type,
                    "action": action,
                    "is_balance": bool(is_balance),
                }
            except json.JSONDecodeError:
                pass

        # JSON 파싱 실패 시 텍스트에서 직접 추출 시도
        leave_type = "연차"
        for lt in ["반차", "병가", "경조사", "휴가", "연차"]:
            if lt in content or lt in user_text:
                leave_type = lt
                break

        action = "조회"
        if any(k in user_text for k in ["신청", "신청해", "신청할", "신청하고", "신청하려"]):
            action = "신청"

        # 잔여 연차 조회 키워드 확인 (더 정확한 판단)
        balance_keywords = ["잔여", "남았", "남은", "얼마", "몇 개", "몇 일"]
        non_balance_keywords = ["신청", "사용", "쓴", "내역", "목록", "보여줘", "알려줘"]

        has_balance_keyword = any(k in user_text for k in balance_keywords)
        has_non_balance_keyword = any(k in user_text for k in non_balance_keywords)

        # "잔여" 또는 "남았" 등의 키워드가 있고, "내역"이나 "목록" 같은 키워드가 없으면 잔여 조회
        is_balance = has_balance_keyword and not has_non_balance_keyword

        return {
            "leave_type": leave_type,
            "action": action,
            "is_balance": is_balance,
        }
    except Exception:
        return None


@dataclass
class ScheduleIntent:
    kind: Literal["leave", "schedule", "meeting"]
    # leave
    action: Optional[Literal["조회", "신청"]] = None
    employee_id: Optional[int] = None
    leave_type: Optional[LeaveType] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_days: Optional[float] = None
    reason: Optional[str] = None
    # schedule actions
    schedule_action: Optional[Literal["조회", "등록", "취소"]] = None
    schedule_id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    schedule_type: Optional[str] = None
    location: Optional[str] = None
    # stubs
    details: Optional[str] = None


def parse_schedule_intent(user_text: str) -> ScheduleIntent:
    """LLM 기반 의도 분류."""
    t = user_text.strip()
    
    # 잔여 연차 조회 키워드 우선 체크 (leave로 분류)
    balance_keywords = ["잔여", "남았", "남은", "얼마", "몇 개", "몇 일"]
    leave_keywords = ["연차", "반차", "병가", "경조사", "휴가"]
    has_balance = any(k in t for k in balance_keywords)
    has_leave = any(k in t for k in leave_keywords)

    if has_balance and has_leave:
        # 잔여 연차 조회로 분류
        leave_info = _extract_leave_info_with_llm(t)
        if leave_info:
            leave_type = leave_info.get("leave_type", "연차")
            action = leave_info.get("action", "조회")
            is_balance = leave_info.get("is_balance", True)  # 잔여 키워드가 있으면 기본값 True

            # employee_id 추출 (이름 또는 사번)
            emp = None
            m_emp = re.search(r"(사번|직원번호|employee_id)\s*[:= ]\s*(\d+)", t)
            if m_emp:
                emp = int(m_emp.group(2))
            else:
                employee_names = _extract_employee_names(t)
                if employee_names:
                    from .leave_data import _run
                    async def find_employee_id():
                        async with get_session() as session:
                            return await _resolve_employee_ids_by_names(session, employee_names)
                    try:
                        resolved_ids = _run(find_employee_id())
                        if resolved_ids and len(resolved_ids) > 0:
                            emp = resolved_ids[0]
                    except Exception:
                        pass

            return ScheduleIntent(
                kind="leave",
                action=action,  # type: ignore[arg-type]
                employee_id=emp,
                leave_type=leave_type,  # type: ignore[arg-type]
                details="잔여" if is_balance else None,
            )

    # LLM 기반 라우팅 시도
    llm_intent = classify_intent_with_llm(t, route_type="schedule", fallback_route=None)
    
    # 일정 취소 정보 추출 (LLM 기반, 우선순위 높음)
    cancel_info = _extract_schedule_cancel_info_with_llm(t)
    if cancel_info and cancel_info.get("is_cancel"):
        schedule_action = "취소"
        schedule_id = cancel_info.get("schedule_id")
        reason = cancel_info.get("reason")
        employee_id = None
        schedule_type = cancel_info.get("schedule_type")

        # 사번 추출 (우선순위)
        m_emp = re.search(r"(사번|직원번호|employee_id)\s*[:= ]\s*(\d+)", t)
        if m_emp:
            employee_id = int(m_emp.group(2))

        # 이름으로 employee_id 추출 (schedule_id가 없고 사번도 없는 경우)
        if schedule_id is None and employee_id is None:
            employee_name = cancel_info.get("employee_name")
            if employee_name:
                from .leave_data import _run
                async def find_employee_id():
                    async with get_session() as session:
                        return await _resolve_employee_ids_by_names(session, [employee_name])
                try:
                    resolved_ids = _run(find_employee_id())
                    if resolved_ids and len(resolved_ids) > 0:
                        employee_id = resolved_ids[0]
                except Exception:
                    pass

        return ScheduleIntent(
            kind="schedule",
            schedule_action=schedule_action,
            schedule_id=schedule_id,
            employee_id=employee_id,
            schedule_type=schedule_type,
            reason=reason,
        )
    
    # 일정 등록/조회 정보 추출 (LLM 기반)
    schedule_info = _extract_schedule_info_with_llm(t)
    if schedule_info:
        action = schedule_info.get("action", "조회")
        employee_id = schedule_info.get("employee_id")
        employee_name = schedule_info.get("employee_name")
        schedule_type = schedule_info.get("schedule_type")
        title = schedule_info.get("title")
        start_time = schedule_info.get("start_time")
        end_time = schedule_info.get("end_time")
        location = schedule_info.get("location")
        description = schedule_info.get("description")
        schedule_id = schedule_info.get("schedule_id")

        # 이름으로 employee_id 추출 (employee_id가 없는 경우)
        if employee_id is None and employee_name:
            from .leave_data import _run
            async def find_employee_id():
                async with get_session() as session:
                    return await _resolve_employee_ids_by_names(session, [employee_name])
            try:
                resolved_ids = _run(find_employee_id())
                if resolved_ids and len(resolved_ids) > 0:
                    employee_id = resolved_ids[0]
            except Exception:
                pass

        # 사번 추출 (우선순위)
        m_emp = re.search(r"(사번|직원번호|employee_id)\s*[:= ]\s*(\d+)", t)
        if m_emp:
            employee_id = int(m_emp.group(2))

        # 날짜 추출 (LLM이 추출하지 못한 경우 정규식으로 시도)
        if not start_time:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", t)
            if dates:
                start_time = dates[0]
                if len(dates) >= 2:
                    end_time = dates[1]

        if action == "등록":
            return ScheduleIntent(
                kind="schedule",
                schedule_action="등록",
                employee_id=employee_id,
                title=title,
                description=description,
                start_time=start_time,
                end_time=end_time,
                schedule_type=schedule_type,
                location=location,
            )
        elif action == "조회":
            # 일정 조회는 run_schedule_agent로 처리
            return ScheduleIntent(kind="schedule", details=t)

    if llm_intent == "leave":
        # LLM이 leave로 분류한 경우, LLM으로 상세 정보 추출 시도
        leave_info = _extract_leave_info_with_llm(t)
        if leave_info:
            leave_type = leave_info.get("leave_type", "연차")
            action = leave_info.get("action", "조회")
            is_balance = leave_info.get("is_balance", False)

            # employee_id 추출 (이름 또는 사번)
            emp = None
            m_emp = re.search(r"(사번|직원번호|employee_id)\s*[:= ]\s*(\d+)", t)
            if m_emp:
                emp = int(m_emp.group(2))
            else:
                employee_names = _extract_employee_names(t)
                if employee_names:
                    from .leave_data import _run
                    async def find_employee_id():
                        async with get_session() as session:
                            return await _resolve_employee_ids_by_names(session, employee_names)
                    try:
                        resolved_ids = _run(find_employee_id())
                        if resolved_ids and len(resolved_ids) > 0:
                            emp = resolved_ids[0]
                    except Exception:
                        pass

            # 날짜 추출
            m_start = re.search(r"시작[:= ]?(\d{4}-\d{2}-\d{2})", t)
            m_end = re.search(r"종료[:= ]?(\d{4}-\d{2}-\d{2})", t)
            m_reason = re.search(r"(사유|reason)[:= ](.+)$", t)

            # 일수 추출
            total_days = None
            if "반차" in leave_type or "반차" in t:
                total_days = 0.5
            m_days = re.search(r"(\d+(\.\d+)?)\s*일", t)
            if m_days:
                total_days = float(m_days.group(1))

            return ScheduleIntent(
                kind="leave",
                action=action,  # type: ignore[arg-type]
                employee_id=emp,
                leave_type=leave_type,  # type: ignore[arg-type]
                start_date=m_start.group(1) if m_start else None,
                end_date=m_end.group(1) if m_end else None,
                total_days=total_days,
                reason=m_reason.group(2).strip() if m_reason else None,
                details="잔여" if is_balance else None,
            )
    elif llm_intent == "meeting":
        return ScheduleIntent(kind="meeting", details=t)
    elif llm_intent == "calendar" or llm_intent == "schedule":
        return ScheduleIntent(kind="schedule", details=t)

    # 기본은 일정(스케줄)로 보냄
    return ScheduleIntent(kind="schedule", details=t)


def schedule_master(user_text: str) -> dict:
    intent = parse_schedule_intent(user_text)

    # 일정 등록 처리
    if intent.kind == "schedule" and intent.schedule_action == "등록":
        if intent.employee_id is None:
            return {
                "ok": False,
                "message": "일정 등록을 위해서는 사번(employee_id)이 필요합니다.",
                "error": "employee_id is required",
            }

        if not intent.title:
            return {
                "ok": False,
                "message": "일정 등록을 위해서는 제목(title)이 필요합니다.",
                "error": "title is required",
            }

        # 날짜/시간 파싱
        start_time = None
        end_time = None
        if intent.start_time:
            try:
                from datetime import datetime
                # "YYYY-MM-DD" 또는 "YYYY-MM-DD HH:MM" 형식 파싱
                start_time_str = intent.start_time.strip()
                if len(start_time_str) == 10:
                    # 날짜만 있는 경우 00:00:00으로 설정
                    start_time = datetime.strptime(start_time_str, "%Y-%m-%d")
                elif " " in start_time_str:
                    # "YYYY-MM-DD HH:MM" 형식
                    start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
                else:
                    # "YYYY-MM-DDTHH:MM" 형식도 지원
                    start_time = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M")
            except Exception as e:
                import os
                if os.getenv("DEBUG_SCHEDULE") == "true":
                    print(f"[DEBUG] 시작 시간 파싱 실패: {intent.start_time}, 오류: {e}")

        if intent.end_time:
            try:
                from datetime import datetime
                end_time_str = intent.end_time.strip()
                if len(end_time_str) == 10:
                    # 날짜만 있는 경우 23:59:59로 설정 (하루 종일 일정)
                    end_time = datetime.strptime(end_time_str, "%Y-%m-%d")
                elif " " in end_time_str:
                    # "YYYY-MM-DD HH:MM" 형식
                    end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")
                else:
                    # "YYYY-MM-DDTHH:MM" 형식도 지원
                    end_time = datetime.strptime(end_time_str, "%Y-%m-%dT%H:%M")
            except Exception as e:
                import os
                if os.getenv("DEBUG_SCHEDULE") == "true":
                    print(f"[DEBUG] 종료 시간 파싱 실패: {intent.end_time}, 오류: {e}")

        return create_schedule(
            employee_id=intent.employee_id,
            title=intent.title,
            description=intent.description,
            start_time=start_time,
            end_time=end_time,
            schedule_type=intent.schedule_type,
            location=intent.location,
        )

    # 일정 취소 처리
    if intent.kind == "schedule" and intent.schedule_action == "취소":
        # schedule_id가 없으면 이름과 일정 유형으로 일정을 찾아야 함
        if intent.schedule_id is None:
            if intent.employee_id is None:
                return {
                    "ok": False,
                    "message": "일정 취소를 위해서는 일정 ID 또는 직원 정보가 필요합니다.",
                    "error": "schedule_id or employee_id is required",
                }

            # 이름과 일정 유형으로 일정 조회
            from .leave_data import _run
            async def find_schedule_to_cancel():
                schedule_types = []
                if intent.schedule_type:
                    schedule_types = [intent.schedule_type]
                else:
                    # 일정 유형이 없으면 모든 유형 조회
                    schedule_types = None

                filters = ScheduleFilters(
                    employee_id=intent.employee_id,
                    schedule_types=schedule_types,
                    limit=10  # 최근 10개만 조회
                )
                return await fetch_schedule_rows(filters)

            try:
                rows = _run(find_schedule_to_cancel())

                if not rows:
                    return {
                        "ok": False,
                        "message": f"취소할 일정을 찾을 수 없습니다. (사번: {intent.employee_id}, 유형: {intent.schedule_type or '전체'})",
                        "error": "schedule not found",
                    }

                # 첫 번째 일정을 취소 대상으로 선택 (또는 가장 최근 일정)
                # schedule_id 컬럼 찾기
                schedule_id = None
                for row in rows:
                    # schedule_id, id, scheduleId 등 다양한 컬럼명 시도
                    schedule_id = (
                        row.get("schedule_id")
                        or row.get("id")
                        or row.get("scheduleId")
                        or row.get("scheduleId")
                    )
                    if schedule_id:
                        break

                if not schedule_id:
                    return {
                        "ok": False,
                        "message": "일정 ID를 찾을 수 없습니다.",
                        "error": "schedule_id not found in result",
                    }

                # 일정 정보를 메시지에 포함
                title = rows[0].get("title", "제목 없음")
                employee_name = rows[0].get("employee_name", "직원")

                result = cancel_schedule(
                    schedule_id=int(schedule_id),
                    reason=intent.reason,
                )

                # 성공 시 더 자세한 메시지 제공
                if result.get("ok"):
                    result["message"] = f"{employee_name}님의 '{title}' 일정이 취소되었습니다."

                return result
            except Exception as e:
                return {
                    "ok": False,
                    "message": f"일정 조회 중 오류가 발생했습니다: {e}",
                    "error": str(e),
                }

        # schedule_id가 있는 경우 직접 취소
        return cancel_schedule(
            schedule_id=intent.schedule_id,
            reason=intent.reason,
        )
    
    if intent.kind == "leave":
        if intent.employee_id is None:
            message = "해당 요청을 처리하려면 사번(employee_id)을 제공해 주세요."
            return {"ok": False, "message": message, "error": message}

        # 잔여 연차 조회 (leave_balance_v2 테이블 사용)
        # details가 "잔여"이거나, "잔여", "남았", "얼마" 등의 키워드가 있으면 잔여 조회
        is_balance_query = (
            intent.details == "잔여" or
            any(k in user_text for k in ["잔여", "남았", "남은", "얼마", "몇 개", "몇 일"])
        )

        if intent.action == "조회" and is_balance_query:
            # leave_balance_v2 테이블에서 잔여 연차 조회
            q = LeaveQuery(
                employee_id=intent.employee_id,
                action="조회",
                leave_type=intent.leave_type or "연차"
            )
            return handle_leave(q)

        if intent.action == "조회":
            # schedule 테이블에서 연차/반차/병가/휴가 조회
            leave_type = intent.leave_type or "연차"

            # 휴가 조회의 경우 모든 휴가 유형 포함 (연차, 반차, 병가, 경조사)
            if leave_type == "휴가" or (not intent.leave_type and "휴가" in user_text):
                schedule_types = [
                    "연차", "반차", "병가", "경조사",
                    "반차(오전)", "반차(오후)", "반차 오전", "반차 오후"
                ]
            # 반차의 경우 "반차(오전)", "반차(오후)" 등도 포함
            elif leave_type == "반차":
                schedule_types = ["반차", "반차(오전)", "반차(오후)", "반차 오전", "반차 오후"]
            else:
                schedule_types = [leave_type]

            # schedule 테이블에서 조회
            from .leave_data import _run
            async def query_schedule_leave():
                filters = ScheduleFilters(
                    employee_id=intent.employee_id,
                    schedule_types=schedule_types,  # 연차, 반차, 병가 등 (반차는 여러 변형 포함)
                    limit=100  # 충분한 수량 조회
                )
                return await fetch_schedule_rows(filters)

            try:
                rows = _run(query_schedule_leave())

                if not rows:
                    # employee_name을 별도로 조회하거나 기본값 사용
                    return {
                        "ok": True,
                        "message": f"사번 {intent.employee_id}의 {leave_type} 일정이 없습니다.",
                        "rows": [],
                    }

                # 조회된 일정 정보를 메시지로 구성
                employee_name = rows[0].get("employee_name", "직원") if rows else "직원"
                count = len(rows)

                # 일정 목록 생성
                schedule_list = []
                for row in rows:
                    title = row.get("title", "제목 없음")
                    start_at = row.get("start_at") or row.get("start_time") or row.get("start_datetime")
                    end_at = row.get("end_at") or row.get("end_time") or row.get("end_datetime")

                    if start_at:
                        start_str = str(start_at)[:10] if len(str(start_at)) > 10 else str(start_at)
                    else:
                        start_str = "날짜 없음"

                    if end_at:
                        end_str = str(end_at)[:10] if len(str(end_at)) > 10 else str(end_at)
                    else:
                        end_str = "날짜 없음"

                    schedule_list.append(f"  - {title} ({start_str} ~ {end_str})")

                message = (
                    f"{employee_name}님의 {leave_type} 일정 ({count}건):\n" +
                    "\n".join(schedule_list[:10])  # 최대 10개만 표시
                )

                if count > 10:
                    message += f"\n... 외 {count - 10}건 더 있습니다."

                return {
                    "ok": True,
                    "message": message,
                    "rows": rows,
                }
            except Exception as e:
                import os
                if os.getenv("DEBUG_SCHEDULE") == "true":
                    import traceback
                    print(f"[DEBUG] schedule 테이블 조회 오류: {e}")
                    traceback.print_exc()
                return {
                    "ok": False,
                    "message": f"{leave_type} 조회 중 오류가 발생했습니다: {e}",
                    "error": str(e),
                }
        # 신청
        q = LeaveQuery(
            employee_id=intent.employee_id,
            action="신청",
            leave_type=intent.leave_type or "연차",
            start_date=intent.start_date,
            end_date=intent.end_date,
            total_days=intent.total_days,
            reason=intent.reason or "개인 사정",
        )
        return handle_leave(q)

    if intent.kind == "meeting":
        return run_schedule_agent(intent.details or user_text)

    # schedule (조회)
    return run_schedule_agent(intent.details or user_text)





