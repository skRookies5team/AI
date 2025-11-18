"""이름으로 직접 조회 테스트 (이벤트 루프 문제 없이)."""

from __future__ import annotations

import asyncio
from agents.schedule_data import get_session, _resolve_employee_ids_by_names, fetch_schedule_rows, ScheduleFilters
from agents.leave_data import _fetch_balance_row, _normalize_balance_row
from sqlalchemy import text


async def test_name_direct() -> None:
    """이름으로 직접 조회 테스트."""
    print("=" * 70)
    print("이름으로 직접 조회 테스트")
    print("=" * 70)
    
    async with get_session() as session:
        # 실제 DB에 존재하는 이름 가져오기
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
            
            # 1. 이름으로 employee_id 찾기
            print(f"\n[1] 이름으로 employee_id 찾기: '{name}'")
            resolved_ids = await _resolve_employee_ids_by_names(session, [name])
            if resolved_ids:
                print(f"  [성공] 찾은 employee_ids: {resolved_ids}")
                if emp_id in resolved_ids:
                    print(f"  [OK] 예상한 employee_id {emp_id} 포함됨!")
                else:
                    print(f"  [WARNING] 예상한 employee_id {emp_id} 없음")
            else:
                print(f"  [ERROR] 이름으로 employee_id를 찾지 못했습니다!")
                continue
            
            # 2. 찾은 employee_id로 일정 조회
            print(f"\n[2] 일정 조회 (employee_ids: {resolved_ids})")
            filters = ScheduleFilters(employee_ids=resolved_ids, limit=5)
            rows = await fetch_schedule_rows(filters)
            print(f"  조회된 일정: {len(rows)}개")
            if rows:
                first = rows[0]
                print(f"  첫 번째 일정:")
                print(f"    employee_id: {first.get('employee_id')}")
                print(f"    employee_name: {first.get('employee_name')}")
                print(f"    title: {first.get('title')}")
                if "employee_name" in first:
                    if first['employee_name'] == name:
                        print(f"  [OK] 이름 일치 확인!")
                    else:
                        print(f"  [WARNING] 이름 불일치: 예상={name}, 실제={first['employee_name']}")
                else:
                    print(f"  [ERROR] employee_name 없음")
            else:
                print(f"  [WARNING] 조회된 일정 없음")
            
            # 3. 잔여 연차 조회
            print(f"\n[3] 잔여 연차 조회 (employee_id: {emp_id})")
            try:
                balance_row = await _fetch_balance_row(session, emp_id)
                if balance_row:
                    balance = _normalize_balance_row(balance_row)
                    print(f"  잔여 연차: {balance.get('remaining_days')}")
                    if "employee_name" in balance:
                        print(f"  [OK] employee_name 포함: {balance['employee_name']}")
                        if balance['employee_name'] == name:
                            print(f"  [OK] 이름 일치 확인!")
                    else:
                        print(f"  [ERROR] employee_name 없음")
                else:
                    print(f"  [WARNING] 잔여 연차 데이터 없음")
            except Exception as e:
                print(f"  [ERROR] {e}")


if __name__ == "__main__":
    asyncio.run(test_name_direct())
