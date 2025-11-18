"""이름으로 질의 테스트."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_langgraph import analyze_query, ScheduleState
from agents.schedule_data import get_session, _resolve_employee_ids_by_names


async def test_name_query() -> None:
    """이름으로 질의하는 기능 테스트."""
    print("=" * 70)
    print("이름으로 질의 테스트")
    print("=" * 70)
    
    # 1. 실제 DB에 존재하는 이름 확인
    print("\n[1] 실제 DB에 존재하는 이름 확인...")
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name, employee_id FROM employee LIMIT 10")
        )
        employees = result.fetchall()
        print("직원 목록 (샘플):")
        for name, emp_id in employees:
            print(f"  - {name} (사번: {emp_id})")
        
        if employees:
            test_name = employees[0][0]  # 첫 번째 직원 이름 사용
            print(f"\n[2] 테스트 이름: '{test_name}'")
            
            # 2. 이름으로 질의 파싱 테스트
            print(f"\n[3] '{test_name} 일정 알려줘' 질의 파싱...")
            query = f"{test_name} 일정 알려줘"
            state = ScheduleState(query=query)
            state = analyze_query(state)
            
            print(f"  employee_id: {state.filters.employee_id if state.filters else None}")
            print(f"  employee_name_keywords: {state.filters.employee_name_keywords if state.filters else None}")
            
            # 3. 이름으로 employee_id 찾기
            if state.filters and state.filters.employee_name_keywords:
                print(f"\n[4] 이름으로 employee_id 찾기...")
                resolved_ids = await _resolve_employee_ids_by_names(
                    session, state.filters.employee_name_keywords
                )
                if resolved_ids:
                    print(f"  찾은 employee_ids: {resolved_ids}")
                else:
                    print(f"  [경고] 이름으로 employee_id를 찾지 못했습니다.")


if __name__ == "__main__":
    from sqlalchemy import text
    from agents.schedule_data import get_session
    asyncio.run(test_name_query())

