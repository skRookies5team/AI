"""실제 DB에 존재하는 이름으로 테스트."""

from __future__ import annotations

import asyncio
import json
from agents.supervisor import run_supervisor
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_real_names() -> None:
    """실제 DB에 존재하는 이름으로 테스트."""
    print("=" * 70)
    print("실제 이름으로 조회 테스트")
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
            LIMIT 5
        """))
        employees = result.fetchall()
        
        if not employees:
            print("[오류] 일정이 있는 직원이 없습니다.")
            return
        
        print("\n테스트할 직원 목록 (일정이 있는 직원):")
        for name, emp_id, count in employees:
            print(f"  - {name} (사번: {emp_id}, 일정: {count}개)")
        
        # 각 직원에 대해 이름으로 테스트
        for name, emp_id, count in employees[:3]:  # 상위 3명만 테스트
            print("\n" + "=" * 70)
            print(f"테스트 직원: {name} (사번: {emp_id}, 일정: {count}개)")
            print("=" * 70)
            
            # 1. 일정 조회 (이름)
            query = f"{name} 일정 알려줘"
            print(f"\n[1] '{query}'")
            try:
                result = run_supervisor(query)
                print(f"  성공: {result.get('ok')}")
                if result.get('rows'):
                    print(f"  조회된 일정: {len(result['rows'])}개")
                    first = result['rows'][0]
                    if "employee_name" in first:
                        print(f"  [OK] employee_name: {first['employee_name']}")
                        if first['employee_name'] == name:
                            print(f"  [OK] 이름 일치 확인!")
                    else:
                        print(f"  [ERROR] employee_name 없음")
                elif result.get('message'):
                    print(f"  메시지: {result.get('message', '')[:150]}")
                else:
                    print(f"  [WARNING] 조회된 일정 없음")
            except Exception as e:
                print(f"  [ERROR] 예외 발생: {e}")
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_real_names())

