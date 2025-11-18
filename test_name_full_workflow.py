"""이름으로 질의하는 전체 워크플로우 테스트."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_langgraph import run_schedule_agent
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_name_full_workflow() -> None:
    """이름으로 질의하는 전체 워크플로우 테스트."""
    print("=" * 70)
    print("이름으로 질의하는 전체 워크플로우 테스트")
    print("=" * 70)
    
    # 실제 DB에 존재하는 이름 가져오기
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name, employee_id FROM employee WHERE employee_id IN (4001, 4002) LIMIT 2")
        )
        employees = result.fetchall()
        
        if not employees:
            print("[오류] 테스트할 직원이 없습니다.")
            return
        
        for name, emp_id in employees:
            print(f"\n{'='*70}")
            print(f"[테스트] {name} (사번: {emp_id})")
            print(f"{'='*70}")
            
            # 전체 워크플로우 테스트
            query = f"{name} 일정 알려줘"
            print(f"\n질의: '{query}'")
            print("-" * 70)
            
            try:
                result = run_schedule_agent(query)
                print(f"성공: {result.get('ok')}")
                print(f"메시지: {result.get('message', '')[:200]}")
                
                if result.get('rows'):
                    print(f"조회된 일정: {len(result['rows'])}개")
                    first = result['rows'][0]
                    print(f"첫 번째 일정:")
                    print(f"  employee_id: {first.get('employee_id')}")
                    print(f"  employee_name: {first.get('employee_name')}")
                    print(f"  title: {first.get('title')}")
                    
                    if "employee_name" in first:
                        if first['employee_name'] == name:
                            print(f"  [OK] 이름 일치 확인!")
                        else:
                            print(f"  [WARNING] 이름 불일치: 예상={name}, 실제={first['employee_name']}")
                    else:
                        print(f"  [ERROR] employee_name 없음")
                        print(f"  컬럼: {list(first.keys())}")
                else:
                    print(f"[WARNING] 조회된 일정 없음")
                    if result.get('error'):
                        print(f"오류: {result['error']}")
            except Exception as e:
                print(f"[ERROR] 예외 발생: {e}")
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_name_full_workflow())

