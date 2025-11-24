from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .leave_data import (
    get_balance,
    update_balance_from_requests,
    create_leave_request,
    ensure_balance_row,
    get_leave_requests,
)


@dataclass
class LeaveQuery:
    employee_id: int
    action: Literal["조회", "신청", "내역", "추천"]
    leave_type: Literal["연차", "반차", "병가", "경조사"] = "연차"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_days: Optional[float] = None
    reason: str = "개인 사정"


def check_balance(employee_id: int, leave_type: Optional[Literal["연차", "반차", "병가", "경조사"]] = None) -> dict:
    """휴가 잔여일을 조회한다.

    Args:
        employee_id: 직원 ID
        leave_type: 휴가 유형 (연차, 반차, 병가, 경조사). None이면 전체 연차 조회.
    """
    ensure_balance_row(employee_id)
    # 최신 요청 합산 반영
    bal = update_balance_from_requests(employee_id)

    # 메시지 생성
    employee_name = bal.get("employee_name", "직원")
    total = bal.get("total_entitlement_days", 0)
    used = bal.get("used_days", 0)
    pending = bal.get("pending_days", 0)
    remaining = bal.get("remaining_days", 0)

    # leave_type에 따라 메시지 변경
    if leave_type:
        leave_type_name = leave_type
        if leave_type == "반차":
            leave_type_name = "반차"
        elif leave_type == "병가":
            leave_type_name = "병가"
        elif leave_type == "경조사":
            leave_type_name = "경조사"
        else:
            leave_type_name = "연차"

        message = (
            f"{employee_name}님의 {leave_type_name} 현황:\n"
            f"- 총 연차: {total}일\n"
            f"- 사용: {used}일\n"
            f"- 대기: {pending}일\n"
            f"- 잔여: {remaining}일"
        )
    else:
        message = (
            f"{employee_name}님의 연차 현황:\n"
            f"- 총 연차: {total}일\n"
            f"- 사용: {used}일\n"
            f"- 대기: {pending}일\n"
            f"- 잔여: {remaining}일"
        )

    return {"ok": True, "message": message, "balance": bal}


def can_apply(employee_id: int, total_days: float) -> dict:
    ensure_balance_row(employee_id)
    bal = update_balance_from_requests(employee_id)
    can = float(bal["remaining_days"]) >= float(total_days)
    return {"ok": True, "can_apply": can, "balance": bal}


def apply_leave(q: LeaveQuery) -> dict:
    # 검증
    if q.total_days is None or q.start_date is None or q.end_date is None:
        return {"ok": False, "error": "신청에는 start_date, end_date, total_days가 필요합니다."}

    # 가능 여부 확인
    pre = can_apply(q.employee_id, float(q.total_days))
    if not pre["can_apply"]:
        return {
            "ok": False,
            "error": "잔여 연차 부족으로 신청할 수 없습니다.",
            "balance": pre["balance"],
        }

    result = create_leave_request(
        employee_id=q.employee_id,
        leave_type=q.leave_type,
        start_date=q.start_date,
        end_date=q.end_date,
        total_days=float(q.total_days),
        reason=q.reason,
        status="대기",
    )
    return {"ok": True, **result}


def get_leave_history(
    employee_id: int,
    leave_type: Optional[Literal["연차", "반차", "병가", "경조사"]] = None,
    leave_types: Optional[list[Literal["연차", "반차", "병가", "경조사"]]] = None,
    include_all: bool = False,
) -> dict:
    """연차 신청 내역을 조회한다.
    
    Args:
        employee_id: 직원 ID
        leave_type: 휴가 유형 필터 (None이면 전체, 단일 타입)
        leave_types: 여러 휴가 유형 필터 (None이면 전체, 여러 타입)
        include_all: 전체 신청 내역도 포함할지 여부
        
    Returns:
        신청 내역 딕셔너리
    """
    all_requests = []
    type_requests_map = {}
    
    # 여러 타입이 있는 경우 각 타입별로 조회
    if leave_types and len(leave_types) > 1:
        for lt in leave_types:
            type_requests = get_leave_requests(employee_id, leave_type=lt)
            type_requests_map[lt] = type_requests
            all_requests.extend(type_requests)
        
        # 전체 조회도 포함
        if include_all:
            all_requests = get_leave_requests(employee_id, leave_type=None)
    elif leave_type:
        # 단일 타입 조회
        all_requests = get_leave_requests(employee_id, leave_type=leave_type)
    else:
        # 전체 조회
        all_requests = get_leave_requests(employee_id, leave_type=None)
    
    # employee_name 가져오기 (requests가 비어있을 수도 있으므로 별도 조회)
    employee_name = "직원"
    if all_requests:
        employee_name = all_requests[0].get("employee_name", "직원")
    else:
        # requests가 비어있으면 employee 테이블에서 직접 조회
        from .leave_data import _run
        from .schedule_data import get_session
        from sqlalchemy import text
        async def get_employee_name():
            async with get_session() as session:
                result = await session.execute(
                    text("SELECT name FROM employee WHERE employee_id = :employee_id LIMIT 1"),
                    {"employee_id": employee_id}
                )
                row = result.first()
                return row[0] if row else "직원"
        try:
            employee_name = _run(get_employee_name())
        except Exception:
            pass
    
    # 중복 제거 (같은 request_id가 여러 타입에 포함될 수 있음)
    seen_ids = set()
    unique_requests = []
    for req in all_requests:
        req_id = req.get("request_id")
        if req_id and req_id not in seen_ids:
            seen_ids.add(req_id)
            unique_requests.append(req)
    all_requests = unique_requests
    
    # 날짜순 정렬 (최신순)
    all_requests.sort(key=lambda x: (
        x.get("request_date", ""),
        x.get("start_date", "")
    ), reverse=True)
    
    if not all_requests:
        if leave_types and len(leave_types) > 1:
            leave_type_name = ", ".join(leave_types)
        else:
            leave_type_name = leave_type if leave_type else "전체"
        return {
            "ok": True,
            "message": f"{employee_name}님의 {leave_type_name} 신청 내역이 없습니다.",
            "requests": [],
        }
    
    # 메시지 생성
    if leave_types and len(leave_types) > 1:
        # 여러 타입별로 섹션 나누기
        message_lines = [f"{employee_name}님의 신청 내역:"]
        
        # 각 타입별 조회 결과 표시
        for lt in leave_types:
            type_requests = type_requests_map.get(lt, [])
            if type_requests:
                message_lines.append(f"\n[{lt}] ({len(type_requests)}건):")
                for idx, req in enumerate(type_requests[:10], 1):  # 최대 10개만 표시
                    status = req.get("status", "알 수 없음")
                    start_date = req.get("start_date", "")
                    end_date = req.get("end_date", "")
                    total_days = req.get("total_days", 0)
                    request_date = req.get("request_date", "")
                    reason = req.get("reason", "")
                    
                    message_lines.append(
                        f"  {idx}. [{status}] {start_date} ~ {end_date} ({total_days}일)"
                    )
                    if request_date:
                        message_lines.append(f"     신청일: {request_date}")
                    if reason:
                        message_lines.append(f"     사유: {reason}")
        
        # 전체 조회 결과 표시 (include_all이 true인 경우)
        if include_all and len(all_requests) > 0:
            message_lines.append(f"\n[전체] ({len(all_requests)}건):")
            for idx, req in enumerate(all_requests[:10], 1):  # 최대 10개만 표시
                status = req.get("status", "알 수 없음")
                leave_type_val = req.get("leave_type", "알 수 없음")
                start_date = req.get("start_date", "")
                end_date = req.get("end_date", "")
                total_days = req.get("total_days", 0)
                request_date = req.get("request_date", "")
                reason = req.get("reason", "")
                
                message_lines.append(
                    f"  {idx}. [{status}] {leave_type_val} {start_date} ~ {end_date} ({total_days}일)"
                )
                if request_date:
                    message_lines.append(f"     신청일: {request_date}")
                if reason:
                    message_lines.append(f"     사유: {reason}")
    else:
        # 단일 타입 또는 전체 조회
        leave_type_name = leave_type if leave_type else "전체"
        message_lines = [f"{employee_name}님의 {leave_type_name} 신청 내역 ({len(all_requests)}건):"]
        for idx, req in enumerate(all_requests, 1):
            status = req.get("status", "알 수 없음")
            leave_type_val = req.get("leave_type", "알 수 없음")
            start_date = req.get("start_date", "")
            end_date = req.get("end_date", "")
            total_days = req.get("total_days", 0)
            request_date = req.get("request_date", "")
            reason = req.get("reason", "")
            
            message_lines.append(
                f"\n{idx}. [{status}] {leave_type_val} "
                f"({start_date} ~ {end_date}, {total_days}일)"
            )
            if request_date:
                message_lines.append(f"   신청일: {request_date}")
            if reason:
                message_lines.append(f"   사유: {reason}")
    
    message = "\n".join(message_lines)
    
    return {
        "ok": True,
        "message": message,
        "requests": all_requests,
    }


def recommend_leave_usage(employee_id: int) -> dict:
    """연차 사용 패턴을 분석하여 추천 기간을 제안한다.
    
    Args:
        employee_id: 직원 ID
        
    Returns:
        추천 정보 딕셔너리
    """
    from datetime import datetime, timedelta
    from collections import defaultdict
    
    # 잔여 연차 확인
    ensure_balance_row(employee_id)
    bal = update_balance_from_requests(employee_id)
    remaining_days = bal.get("remaining_days", 0)
    employee_name = bal.get("employee_name", "직원")
    
    if remaining_days <= 0:
        return {
            "ok": True,
            "message": f"{employee_name}님의 잔여 연차가 없어 추천할 수 없습니다.",
            "recommendations": [],
        }
    
    # 과거 연차 사용 내역 조회 (최근 2년)
    current_year = datetime.now().year
    all_requests = get_leave_requests(employee_id, leave_type="연차", limit=100)
    
    # 승인된 연차만 분석
    approved_requests = [
        req for req in all_requests
        if req.get("status") == "승인" and req.get("leave_type") == "연차"
    ]
    
    if not approved_requests:
        # 과거 사용 내역이 없으면 일반적인 추천
        return {
            "ok": True,
            "message": f"{employee_name}님의 과거 연차 사용 내역이 없어 일반적인 추천을 드립니다:\n\n"
                      f"💡 추천:\n"
                      f"- 봄(3-5월): 신학기/개학 시즌, 벚꽃 시즌\n"
                      f"- 여름(7-8월): 여름휴가 시즌, 가족 여행\n"
                      f"- 가을(9-11월): 단풍 시즌, 가을 여행\n"
                      f"- 연말(12월): 연말 정리 및 휴식\n\n"
                      f"현재 잔여 연차: {remaining_days}일",
            "recommendations": [],
            "remaining_days": remaining_days,
        }
    
    # 월별 사용 패턴 분석
    monthly_usage = defaultdict(float)
    seasonal_usage = {"봄": 0.0, "여름": 0.0, "가을": 0.0, "겨울": 0.0}
    
    for req in approved_requests:
        start_date_str = req.get("start_date")
        if not start_date_str:
            continue
        
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            month = start_date.month
            total_days = float(req.get("total_days", 0))
            
            monthly_usage[month] += total_days
            
            # 계절 분류
            if month in [3, 4, 5]:
                seasonal_usage["봄"] += total_days
            elif month in [6, 7, 8]:
                seasonal_usage["여름"] += total_days
            elif month in [9, 10, 11]:
                seasonal_usage["가을"] += total_days
            else:
                seasonal_usage["겨울"] += total_days
        except Exception:
            continue
    
    # 추천 로직
    recommendations = []
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    # 1. 사용량이 적은 월 추천
    if monthly_usage:
        min_usage_month = min(monthly_usage.items(), key=lambda x: x[1])[0]
        if min_usage_month >= current_month:
            month_names = ["", "1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"]
            recommendations.append({
                "period": f"{current_year}년 {month_names[min_usage_month]}",
                "reason": f"과거에 {month_names[min_usage_month]}에 연차를 적게 사용하셨습니다. 이번 기회에 활용해보세요.",
            })
    
    # 2. 계절별 추천
    if seasonal_usage:
        min_season = min(seasonal_usage.items(), key=lambda x: x[1])[0]
        season_periods = {
            "봄": ("3월", "5월"),
            "여름": ("7월", "8월"),
            "가을": ("9월", "11월"),
            "겨울": ("12월", "2월"),
        }
        if min_season in season_periods:
            start_month, end_month = season_periods[min_season]
            recommendations.append({
                "period": f"{current_year}년 {start_month}~{end_month}",
                "reason": f"과거에 {min_season}에 연차를 적게 사용하셨습니다. {min_season} 휴가를 계획해보세요.",
            })
    
    # 3. 연말/연초 추천
    year_end_usage = monthly_usage.get(12, 0) + monthly_usage.get(1, 0)
    if year_end_usage < remaining_days * 0.3:  # 연말/연초 사용이 적으면
        recommendations.append({
            "period": f"{current_year}년 12월 또는 {current_year + 1}년 1월",
            "reason": "연말/연초에 연차를 사용하면 연말 정리와 새해 계획을 세우기에 좋습니다.",
        })
    
    # 4. 일반적인 추천 (과거 패턴이 없거나 부족한 경우)
    if not recommendations:
        recommendations.append({
            "period": f"{current_year}년 하반기",
            "reason": "하반기에 연차를 사용하면 업무 부담이 적은 시기에 휴식을 취할 수 있습니다.",
        })
    
    # LLM으로 자연어 추천 메시지 생성
    import os
    try:
        from langchain_openai import ChatOpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            prompt = f"""사용자의 연차 사용 패턴을 분석하여 친절하고 실용적인 추천 메시지를 작성하세요.

직원 이름: {employee_name}
잔여 연차: {remaining_days}일

과거 사용 패턴:
- 월별 사용량: {dict(monthly_usage)}
- 계절별 사용량: {dict(seasonal_usage)}

추천 기간:
{chr(10).join([f"- {rec['period']}: {rec['reason']}" for rec in recommendations])}

다음 형식으로 추천 메시지를 작성하세요:
1. 잔여 연차 현황 요약
2. 과거 사용 패턴 분석 (간단히)
3. 추천 기간과 이유
4. 추가 팁 (선택사항)

친절하고 실용적인 톤으로 작성하세요."""
            
            llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.7,
                api_key=api_key,
            )
            response = llm.invoke(prompt)
            message = response.content.strip()
        else:
            # LLM이 없으면 기본 메시지
            message = f"{employee_name}님의 연차 사용 추천:\n\n"
            message += f"현재 잔여 연차: {remaining_days}일\n\n"
            message += "💡 추천 기간:\n"
            for rec in recommendations:
                message += f"- {rec['period']}: {rec['reason']}\n"
    except Exception:
        # LLM 실패 시 기본 메시지
        message = f"{employee_name}님의 연차 사용 추천:\n\n"
        message += f"현재 잔여 연차: {remaining_days}일\n\n"
        message += "💡 추천 기간:\n"
        for rec in recommendations:
            message += f"- {rec['period']}: {rec['reason']}\n"
    
    return {
        "ok": True,
        "message": message,
        "recommendations": recommendations,
        "remaining_days": remaining_days,
        "monthly_usage": dict(monthly_usage),
        "seasonal_usage": dict(seasonal_usage),
    }


def handle_leave(query: LeaveQuery, leave_types: Optional[list[Literal["연차", "반차", "병가", "경조사"]]] = None, include_all: bool = False) -> dict:
    if query.action == "조회":
        # leave_type을 전달하여 특정 휴가 유형 조회 지원
        return check_balance(query.employee_id, leave_type=query.leave_type)
    if query.action == "신청":
        return apply_leave(query)
    if query.action == "내역":
        return get_leave_history(
            query.employee_id,
            leave_type=query.leave_type,
            leave_types=leave_types,
            include_all=include_all
        )
    if query.action == "추천":
        return recommend_leave_usage(query.employee_id)
    return {"ok": False, "error": "지원하지 않는 action 입니다. (조회|신청|내역|추천)"}








