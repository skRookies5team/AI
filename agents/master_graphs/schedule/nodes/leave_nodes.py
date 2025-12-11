"""연차/반차 관리 노드 - HybridScheduler + LLM 파싱."""

from __future__ import annotations

from typing import Any, Dict, Optional, Literal
from dataclasses import dataclass
from datetime import datetime

from agents.state import OrchestratorState
from agents.master_graphs.schedule.services.hybrid_scheduler import HybridScheduler
from agents.master_graphs.schedule.utils.date_parser import parse_korean_date_with_llm
from agents.master_graphs.schedule.utils.entity_parser import extract_leave_entities


# HybridScheduler 싱글톤 인스턴스
_scheduler = None


def _get_scheduler() -> HybridScheduler:
    """HybridScheduler 싱글톤 인스턴스 반환."""
    global _scheduler
    if _scheduler is None:
        _scheduler = HybridScheduler()
    return _scheduler


@dataclass
class ParsedLeaveIntent:
    """파싱된 연차 의도."""
    action: Literal["조회", "신청", "내역", "추천"]
    leave_type: Literal["연차", "반차", "병가", "경조사"]
    employee_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    total_days: Optional[float] = None
    reason: str = "개인 사정"


async def process_leave_request(state: OrchestratorState) -> Dict[str, Any]:
    """연차/반차 요청 처리.
    
    Args:
        state: 오케스트레이터 상태
        
    Returns:
        처리 결과 딕셔너리
    """
    query = state.get("query", "")
    employee_id = state.get("employee_id")
    user_id = state.get("user_id")
    
    # Redis 기반 컨텍스트 초기화
    if state.get("schedule_context") is None:
        state["schedule_context"] = {}
    if state["schedule_context"].get("leave") is None:
        state["schedule_context"]["leave"] = {}
    
    leave_context = state["schedule_context"]["leave"]
    
    print(f"[Leave Node] 쿼리: {query}, employee_id: {employee_id}")
    
    # 1. employee_id 검증
    if not employee_id:
        return {
            "final_response": "연차 관리를 위해서는 사번(employee_id)이 필요합니다.",
            "messages": ["연차 관리를 위해서는 사번이 필요합니다."],
            "schedule_context": state["schedule_context"],
        }
    
    # 2. 액션 결정 (키워드 기반)
    action = _determine_leave_action(query)
    
    # 3. LLM 기반 엔티티 추출
    entities = await extract_leave_entities(query)
    
    # 4. 날짜 파싱 (신청인 경우만 LLM 사용)
    start_date_obj = None
    end_date_obj = None
    start_date_str = None
    end_date_str = None
    total_days = entities.get("total_days")
    
    if action == "신청":
        start_date_obj, end_date_obj = await parse_korean_date_with_llm(query)
        
        if start_date_obj:
            start_date_str = start_date_obj.strftime("%Y-%m-%d")
            end_date_str = end_date_obj.strftime("%Y-%m-%d") if end_date_obj else start_date_str
            
            # total_days 계산
            if not total_days:
                if entities.get("leave_type") == "반차":
                    total_days = 0.5
                else:
                    days_diff = (end_date_obj - start_date_obj).days + 1 if end_date_obj else 1
                    total_days = float(days_diff)
        else:
            # 날짜를 파싱하지 못한 경우
            return {
                "final_response": "연차 신청을 위해서는 날짜를 명시해주세요.\n예: '내일 연차 신청', '12월 25일 연차'",
                "messages": ["날짜 정보가 필요합니다."],
                "schedule_context": state["schedule_context"],
            }
    
    # 5. ParsedLeaveIntent 생성
    parsed = ParsedLeaveIntent(
        action=action,
        leave_type=entities.get("leave_type", "연차"),
        employee_id=employee_id,
        start_date=start_date_str,
        end_date=end_date_str,
        total_days=total_days,
        reason=entities.get("reason") or "개인 사정",
    )
    
    # 6. 액션별 처리
    scheduler = _get_scheduler()
    
    if parsed.action == "조회":
        # 연차 잔여 조회
        result = await _query_leave_balance(parsed, scheduler)
    elif parsed.action == "신청":
        # 연차 신청
        result = await _apply_leave(parsed, user_id or str(employee_id), scheduler)
    elif parsed.action == "내역":
        # 연차 사용 내역
        result = await _query_leave_history(parsed, scheduler)
    elif parsed.action == "추천":
        # 연차 추천
        result = await _recommend_leave(parsed, scheduler)
    else:
        result = {"ok": False, "message": "알 수 없는 액션입니다."}
    
    # 7. Redis 컨텍스트 업데이트
    leave_context["last_query"] = query
    leave_context["last_action"] = parsed.action
    
    if result.get("ok"):
        if parsed.action == "조회":
            leave_context["last_balance"] = result.get("balance", {})
        elif parsed.action == "신청":
            leave_context["pending_approval"] = {
                "leave_type": parsed.leave_type,
                "start_date": parsed.start_date,
                "end_date": parsed.end_date,
                "total_days": parsed.total_days,
            }
    
    # 8. 응답 반환
    message = result.get("message", "연차 처리가 완료되었습니다.")
    
    state["last_domain"] = "schedule"
    state["domain"] = "schedule"

    return {
        "final_response": message,
        "messages": [message],
        "schedule_context": state["schedule_context"],
    }


async def _query_leave_balance(
    parsed: ParsedLeaveIntent,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """연차 잔여 조회."""
    try:
        balance = await scheduler.db.get_leave_balance(parsed.employee_id)
        
        message = f"""📊 연차 잔여 현황 (사번: {parsed.employee_id})

총 연차: {balance.get('total_entitlement_days', 0)}일
사용: {balance.get('used_days', 0)}일
대기: {balance.get('pending_days', 0)}일
잔여: {balance.get('remaining_days', 0)}일"""
        
        return {"ok": True, "message": message, "balance": balance}
    
    except Exception as e:
        print(f"❌ 연차 조회 실패: {e}")
        return {"ok": False, "message": f"연차 조회 중 오류: {str(e)}"}


async def _apply_leave(
    parsed: ParsedLeaveIntent,
    user_id: str,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """연차 신청."""
    leave_data = {
        'employee_id': parsed.employee_id,
        'leave_type': parsed.leave_type,
        'start_date': parsed.start_date,
        'end_date': parsed.end_date,
        'total_days': parsed.total_days,
        'reason': parsed.reason,
        'status': '대기',
    }
    
    try:
        result = await scheduler.create_leave_request(
            user_id=user_id,
            leave_data=leave_data
        )
        
        return {
            "ok": True,
            "message": result.get('message', '✅ 연차 신청이 완료되었습니다.'),
            "request_id": result.get('request_id'),
        }
    
    except Exception as e:
        print(f"❌ 연차 신청 실패: {e}")
        return {"ok": False, "message": f"연차 신청 중 오류: {str(e)}"}


async def _query_leave_history(
    parsed: ParsedLeaveIntent,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """연차 사용 내역."""
    try:
        history = await scheduler.db.get_leave_list(
            employee_id=parsed.employee_id,
            leave_type=parsed.leave_type if parsed.leave_type != "연차" else None
        )
        
        if not history:
            message = "연차 사용 내역이 없습니다."
        else:
            message = f"📋 연차 사용 내역 (총 {len(history)}건):\n\n"
            for idx, item in enumerate(history[:10], 1):
                message += f"{idx}. {item.get('leave_type')} {item.get('total_days')}일\n"
                message += f"   기간: {item.get('start_date')} ~ {item.get('end_date')}\n"
                message += f"   상태: {item.get('status')}\n\n"
            
            if len(history) > 10:
                message += f"... 외 {len(history) - 10}건"
        
        return {"ok": True, "message": message, "history": history}
    
    except Exception as e:
        print(f"❌ 연차 내역 조회 실패: {e}")
        return {"ok": False, "message": f"연차 내역 조회 중 오류: {str(e)}"}


async def _recommend_leave(
    parsed: ParsedLeaveIntent,
    scheduler: HybridScheduler
) -> Dict[str, Any]:
    """연차 추천 (간단 구현)."""
    # TODO: 실제 추천 로직 구현
    return {
        "ok": True,
        "message": "연차 추천 기능은 준비 중입니다.\n현재 잔여 연차를 확인하려면 '연차 조회'를 사용해주세요."
    }


def _determine_leave_action(query: str) -> str:
    """간단한 키워드 기반 액션 결정."""
    if any(k in query for k in ["잔여", "남은", "남았", "몇일", "얼마"]):
        return "조회"
    elif any(k in query for k in ["신청", "등록", "추가"]):
        return "신청"
    elif any(k in query for k in ["내역", "목록", "리스트", "사용 내역"]):
        return "내역"
    elif any(k in query for k in ["추천"]):
        return "추천"
    else:
        return "조회"