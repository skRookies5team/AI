"""이름으로 질의하는 기능 테스트."""

from __future__ import annotations

import asyncio
import json
from agents.supervisor import run_supervisor
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_name_queries() -> None:
    """이름으로 질의하는 기능 테스트."""
    print("=" * 70)
    print("이름으로 질의하는 기능 테스트")
    print("=" * 70)
    
    # 실제 DB에 존재하는 이름 가져오기
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name, employee_id FROM employee WHERE employee_id IN (4001, 4002, 4003) LIMIT 3")
        )
        employees = result.fetchall()
        
        if not employees:
            print("[오류] 테스트할 직원이 없습니다.")
            return
        
        print("\n테스트할 직원 목록:")
        for name, emp_id in employees:
            print(f"  - {name} (사번: {emp_id})")
        
        # 각 직원에 대해 이름으로 질의 테스트
        for name, emp_id in employees:
            print(f"\n{'='*70}")
            print(f"[테스트] {name} (사번: {emp_id})")
            print(f"{'='*70}")
            
            # 1. 이름으로 일정 조회
            print(f"\n[1] 일정 조회: '{name} 일정 알려줘'")
            try:
                result = run_supervisor(f"{name} 일정 알려줘")
                print(f"  성공: {result.get('ok')}")
                print(f"  메시지: {result.get('message', '')[:150]}")
                if result.get('rows'):
                    print(f"  조회된 일정: {len(result['rows'])}개")
                    first = result['rows'][0]
                    if "employee_name" in first:
                        print(f"  [OK] employee_name 포함: {first['employee_name']}")
                        if first['employee_name'] == name:
                            print(f"  [OK] 이름 일치 확인!")
                        else:
                            print(f"  [WARNING] 이름 불일치: 예상={name}, 실제={first['employee_name']}")
                    else:
                        print(f"  [ERROR] employee_name 없음")
                        print(f"  컬럼: {list(first.keys())}")
                else:
                    print(f"  [WARNING] 조회된 일정 없음")
            except Exception as e:
                print(f"  [ERROR] {e}")
                import traceback
                traceback.print_exc()
            
            # 2. 이름으로 연차 잔여 조회 (이름으로는 연차 조회가 안될 수 있으므로 확인)
            print(f"\n[2] 연차 잔여 조회: '{name} 연차 잔여 조회'")
            try:
                result = run_supervisor(f"{name} 연차 잔여 조회")
                print(f"  성공: {result.get('ok')}")
                print(f"  메시지: {result.get('message', '')[:150]}")
                if result.get('balance'):
                    balance = result['balance']
                    print(f"  잔여 연차: {balance.get('remaining_days')}")
                    if "employee_name" in balance:
                        print(f"  [OK] employee_name 포함: {balance['employee_name']}")
                    else:
                        print(f"  [ERROR] employee_name 없음")
            except Exception as e:
                print(f"  [ERROR] {e}")
            
            # 3. 다양한 패턴 테스트
            patterns = [
                f"{name}님 일정",
                f"{name}의 일정",
                f"{name} 일정 있어?",
            ]
            
            for pattern in patterns:
                print(f"\n[3] 패턴 테스트: '{pattern}'")
                try:
                    result = run_supervisor(pattern)
                    print(f"  성공: {result.get('ok')}")
                    if result.get('rows'):
                        print(f"  조회된 일정: {len(result['rows'])}개")
                        if result['rows'] and "employee_name" in result['rows'][0]:
                            print(f"  [OK] employee_name: {result['rows'][0]['employee_name']}")
                    else:
                        print(f"  메시지: {result.get('message', '')[:100]}")
                except Exception as e:
                    print(f"  [ERROR] {e}")


if __name__ == "__main__":
    asyncio.run(test_name_queries())
