"""schedule과 employee 테이블 JOIN 테스트."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_data import fetch_schedule_rows, ScheduleFilters


async def test_join() -> None:
    """JOIN이 제대로 작동하는지 테스트."""
    print("=" * 70)
    print("JOIN 테스트: schedule + employee")
    print("=" * 70)
    
    # 사번 4001의 일정 조회 (employee 테이블과 JOIN되어 이름이 포함되어야 함)
    filters = ScheduleFilters(employee_id=4001, limit=5)
    
    try:
        rows = await fetch_schedule_rows(filters)
        print(f"\n조회된 일정 수: {len(rows)}")
        
        if rows:
            print("\n첫 번째 일정 데이터:")
            first_row = rows[0]
            print(json.dumps(first_row, ensure_ascii=False, indent=2, default=str))
            
            # employee_name이 포함되어 있는지 확인
            if "employee_name" in first_row:
                print(f"\n[SUCCESS] JOIN 성공! employee_name: {first_row['employee_name']}")
            else:
                print("\n[WARNING] JOIN 실패: employee_name이 결과에 없습니다.")
                print("가능한 원인:")
                print("  1. employee 테이블에 해당 employee_id가 없음")
                print("  2. JOIN 조건이 맞지 않음")
                print("  3. 컬럼명이 예상과 다름")
        else:
            print("\n[WARNING] 조회된 일정이 없습니다.")
            
    except Exception as e:
        print(f"\n[ERROR] 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_join())

