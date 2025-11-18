"""최종 이름 조회 테스트 - 실제 DB 이름 사용."""

from __future__ import annotations

import asyncio
from agents.schedule_langgraph import _run_workflow, _normalize_result
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_name_final() -> None:
    """최종 이름 조회 테스트."""
    print("=" * 70)
    print("최종 이름 조회 테스트 (실제 DB 이름 사용)")
    print("=" * 70)
    
    async with get_session() as session:
        # 실제 DB에 존재하는 이름과 일정이 있는 직원 찾기
        result = await session.execute(text("""
            SELECT DISTINCT e.name, e.employee_id, COUNT(s.schedule_id) as schedule_count
            FROM employee e
            INNER JOIN schedule s ON e.employee_id = s.employee_id
            GROUP BY e.employee_id, e.name
            HAVING schedule_count > 0
            ORDER BY schedule_count DESC
            LIMIT 3
        """))
        employees = result.fetchall()
        
        print("\n테스트할 직원 목록:")
        for name, emp_id, count in employees:
            print(f"  - {name} (사번: {emp_id}, 일정: {count}개)")
        
        # 각 직원에 대해 이름으로 테스트
        for name, emp_id, count in employees:
            print(f"\n{'='*70}")
            print(f"테스트: '{name} 일정 알려줘' (사번: {emp_id})")
            print("=" * 70)
            try:
                state_result = await _run_workflow(f"{name} 일정 알려줘")
                result = _normalize_result(state_result)
                print(f"  성공: {result.get('ok')}")
                if result.get('rows'):
                    print(f"  조회된 일정: {len(result['rows'])}개")
                    first = result['rows'][0]
                    if "employee_name" in first:
                        print(f"  [OK] employee_name: {first['employee_name']}")
                        if first['employee_name'] == name:
                            print(f"  [OK] 이름 일치 확인!")
                elif result.get('message'):
                    print(f"  메시지: {result.get('message', '')[:200]}")
                else:
                    print(f"  [WARNING] 조회된 일정 없음")
            except Exception as e:
                print(f"  [ERROR] 예외 발생: {e}")
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_name_final())

