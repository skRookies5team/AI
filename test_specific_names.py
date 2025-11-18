"""특정 이름(강하은, 윤서준, 한지민)으로 테스트."""

from __future__ import annotations

import asyncio
from agents.supervisor import run_supervisor
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_specific_names() -> None:
    """특정 이름으로 테스트."""
    print("=" * 70)
    print("특정 이름으로 테스트 (강하은, 윤서준, 한지민)")
    print("=" * 70)
    
    # DB에서 해당 이름들의 employee_id 확인
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name, employee_id FROM employee WHERE name IN ('강하은', '윤서준', '한지민')")
        )
        employees = result.fetchall()
        
        if not employees:
            print("[경고] 해당 이름의 직원이 DB에 없습니다. 전체 직원 목록을 확인합니다...")
            result = await session.execute(
                text("SELECT name, employee_id FROM employee LIMIT 10")
            )
            all_employees = result.fetchall()
            print("\nDB에 있는 직원 목록 (최대 10명):")
            for name, emp_id in all_employees:
                print(f"  - {name} (사번: {emp_id})")
            return
        
        print("\n테스트할 직원 목록:")
        for name, emp_id in employees:
            print(f"  - {name} (사번: {emp_id})")
        
        # 각 직원에 대해 테스트
        for name, emp_id in employees:
            print(f"\n{'='*70}")
            print(f"[테스트] {name} (사번: {emp_id})")
            print(f"{'='*70}")
            
            # 1. 이름으로 일정 조회
            query1 = f"{name} 일정 알려줘"
            print(f"\n[1] 일정 조회: '{query1}'")
            try:
                result = run_supervisor(query1)
                print(f"  성공: {result.get('ok')}")
                if result.get('ok'):
                    if result.get('rows'):
                        print(f"  조회된 일정: {len(result['rows'])}개")
                        first = result['rows'][0]
                        if "employee_name" in first:
                            print(f"  [OK] employee_name: {first['employee_name']}")
                    print(f"  메시지: {result.get('message', '')[:150]}")
                else:
                    print(f"  실패: {result.get('message', '')[:150]}")
            except Exception as e:
                print(f"  [ERROR] {e}")
                import traceback
                traceback.print_exc()
            
            # 2. 이름으로 연차 잔여 조회
            query2 = f"{name} 연차 잔여 조회"
            print(f"\n[2] 연차 잔여 조회: '{query2}'")
            try:
                result = run_supervisor(query2)
                print(f"  성공: {result.get('ok')}")
                if result.get('ok'):
                    if result.get('balance'):
                        balance = result['balance']
                        print(f"  잔여 연차: {balance.get('remaining_days')}")
                        if "employee_name" in balance:
                            print(f"  [OK] employee_name: {balance['employee_name']}")
                    print(f"  메시지: {result.get('message', '')[:150]}")
                else:
                    print(f"  실패: {result.get('message', '')[:150]}")
            except Exception as e:
                print(f"  [ERROR] {e}")
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_specific_names())

