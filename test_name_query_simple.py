"""이름으로 질의하는 기능 간단 테스트."""

from __future__ import annotations

import asyncio
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_name_query() -> None:
    """이름으로 질의 테스트."""
    print("=" * 70)
    print("이름으로 질의 테스트")
    print("=" * 70)
    
    async with get_session() as session:
        # 실제 DB에 존재하는 이름 가져오기
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
        
        # 이름으로 employee_id 찾기
        from agents.schedule_data import _resolve_employee_ids_by_names
        resolved_ids = await _resolve_employee_ids_by_names(session, [test_name])
        
        if resolved_ids:
            print(f"[성공] 이름 '{test_name}'으로 찾은 employee_ids: {resolved_ids}")
            if test_id in resolved_ids:
                print(f"[성공] 올바른 employee_id 포함됨!")
            else:
                print(f"[경고] 예상한 employee_id {test_id}가 포함되지 않음")
        else:
            print(f"[실패] 이름 '{test_name}'으로 employee_id를 찾지 못했습니다.")


if __name__ == "__main__":
    asyncio.run(test_name_query())

