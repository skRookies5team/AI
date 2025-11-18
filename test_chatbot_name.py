"""챗봇 인터페이스에서 이름으로 테스트."""

from __future__ import annotations

import asyncio
from agents.supervisor import run_supervisor
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_chatbot_with_names() -> None:
    """챗봇에서 이름으로 질의 테스트."""
    print("=" * 70)
    print("챗봇에서 이름으로 질의 테스트")
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
            
            # 1. 이름으로 일정 조회
            query1 = f"{name} 일정 알려줘"
            print(f"\n질의: '{query1}'")
            print("-" * 70)
            try:
                result = run_supervisor(query1)
                print(f"성공: {result.get('ok')}")
                if result.get('ok'):
                    if result.get('rows'):
                        print(f"조회된 일정: {len(result['rows'])}개")
                        first = result['rows'][0]
                        if "employee_name" in first:
                            print(f"[OK] employee_name: {first['employee_name']}")
                    print(f"메시지: {result.get('message', '')[:150]}")
                else:
                    print(f"실패: {result.get('message', '')[:150]}")
            except Exception as e:
                print(f"[ERROR] {e}")
            
            # 2. 이름으로 연차 잔여 조회 (이름으로는 안될 수 있음)
            query2 = f"{name} 연차 잔여 조회"
            print(f"\n질의: '{query2}'")
            print("-" * 70)
            try:
                result = run_supervisor(query2)
                print(f"성공: {result.get('ok')}")
                if result.get('ok'):
                    if result.get('balance'):
                        balance = result['balance']
                        print(f"잔여 연차: {balance.get('remaining_days')}")
                        if "employee_name" in balance:
                            print(f"[OK] employee_name: {balance['employee_name']}")
                    print(f"메시지: {result.get('message', '')[:150]}")
                else:
                    print(f"실패: {result.get('message', '')[:150]}")
            except Exception as e:
                print(f"[ERROR] {e}")


if __name__ == "__main__":
    asyncio.run(test_chatbot_with_names())

