"""이름으로 질의하는 다양한 패턴 테스트."""

from __future__ import annotations

import asyncio
from agents.schedule_langgraph import analyze_query, ScheduleState, run_schedule_agent
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_name_patterns() -> None:
    """이름으로 질의하는 다양한 패턴 테스트."""
    print("=" * 70)
    print("이름으로 질의하는 패턴 테스트")
    print("=" * 70)
    
    # 실제 DB에 존재하는 이름 가져오기
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name, employee_id FROM employee WHERE employee_id IN (4001, 4002) LIMIT 2")
        )
        employees = result.fetchall()
        
        if not employees:
            print("[오류] 테스트할 직원 데이터가 없습니다.")
            return
        
        test_name1 = employees[0][0]  # employee_id 4001의 이름
        test_name2 = employees[1][0] if len(employees) > 1 else employees[0][0]  # employee_id 4002의 이름
        
        print(f"\n테스트 이름: {test_name1} (사번: {employees[0][1]})")
        if len(employees) > 1:
            print(f"테스트 이름: {test_name2} (사번: {employees[1][1]})")
        
        # 다양한 패턴 테스트
        test_patterns = [
            f"{test_name1} 일정 알려줘",
            f"{test_name1}님 일정",
            f"{test_name1}의 일정",
            f"{test_name1} 일정 있어?",
            f"{test_name1} 스케줄",
            f"{test_name1}님의 일정 알려줘",
        ]
        
        print("\n" + "=" * 70)
        print("패턴별 테스트")
        print("=" * 70)
        
        for pattern in test_patterns:
            print(f"\n[질의] {pattern}")
            print("-" * 70)
            
            # 1. 질의 파싱
            state = ScheduleState(query=pattern)
            state = analyze_query(state)
            
            if state.filters:
                print(f"  employee_id: {state.filters.employee_id}")
                print(f"  employee_name_keywords: {state.filters.employee_name_keywords}")
                
                # 2. 실제 조회 테스트 (간단히)
                if state.filters.employee_name_keywords:
                    from agents.schedule_data import _resolve_employee_ids_by_names
                    resolved_ids = await _resolve_employee_ids_by_names(
                        session, state.filters.employee_name_keywords
                    )
                    if resolved_ids:
                        print(f"  [성공] 찾은 employee_ids: {resolved_ids}")
                    else:
                        print(f"  [실패] 이름으로 employee_id를 찾지 못했습니다.")


if __name__ == "__main__":
    asyncio.run(test_name_patterns())

