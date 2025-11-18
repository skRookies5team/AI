"""이름으로 연차 조회 테스트."""

from __future__ import annotations

import asyncio
from agents.schedule_master import schedule_master
from agents.schedule_data import get_session, _resolve_employee_ids_by_names
from agents.schedule_langgraph import _extract_employee_names
from sqlalchemy import text


async def test_name_leave() -> None:
    """이름으로 연차 조회 테스트."""
    print("=" * 70)
    print("이름으로 연차 조회 테스트")
    print("=" * 70)
    
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name, employee_id FROM employee WHERE employee_id = 4001 LIMIT 1")
        )
        row = result.first()
        if not row:
            print("[오류] 테스트할 직원이 없습니다.")
            return
        
        test_name = row[0]
        test_id = row[1]
        print(f"\n테스트 이름: {test_name} (사번: {test_id})")
        
        # 1. 이름 추출 테스트
        query = f"{test_name} 연차 잔여 조회"
        print(f"\n[1] 질의 파싱: '{query}'")
        employee_names = _extract_employee_names(query)
        print(f"  추출된 이름: {employee_names}")
        
        # 2. 이름으로 employee_id 찾기
        if employee_names:
            print(f"\n[2] 이름으로 employee_id 찾기...")
            resolved_ids = await _resolve_employee_ids_by_names(session, employee_names)
            print(f"  찾은 employee_ids: {resolved_ids}")
        
        # 3. schedule_master로 테스트
        print(f"\n[3] schedule_master로 테스트: '{query}'")
        try:
            result = schedule_master(query)
            print(f"  성공: {result.get('ok')}")
            print(f"  메시지: {result.get('message', '')[:150]}")
            if result.get('balance'):
                balance = result['balance']
                print(f"  잔여 연차: {balance.get('remaining_days')}")
                if "employee_name" in balance:
                    print(f"  [OK] employee_name: {balance['employee_name']}")
        except Exception as e:
            print(f"  [ERROR] {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_name_leave())

