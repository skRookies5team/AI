"""schedule 테이블의 실제 데이터를 간단하게 확인하는 스크립트."""

from __future__ import annotations

import os
import sys

# 가상환경 체크
try:
    from agents.schedule_data import get_session
    from agents.leave_data import _run
except ImportError:
    print("=" * 70)
    print("⚠️  가상환경이 활성화되지 않았습니다!")
    print("=" * 70)
    sys.exit(1)

from sqlalchemy import text

async def check_schedule_simple():
    """schedule 테이블의 실제 데이터를 확인한다."""
    async with get_session() as session:
        # 1. schedule_type의 모든 고유 값 확인
        print("=" * 70)
        print("1. schedule 테이블의 모든 schedule_type 값:")
        print("=" * 70)
        result = await session.execute(
            text("SELECT DISTINCT schedule_type FROM schedule WHERE schedule_type IS NOT NULL ORDER BY schedule_type")
        )
        types = [row[0] for row in result.fetchall()]
        print(f"\n총 {len(types)}개의 고유한 schedule_type:\n")
        for stype in types:
            print(f"  - '{stype}'")
        
        # 2. 반차/병가 관련 모든 데이터 (employee_id 조건 없이)
        print("\n" + "=" * 70)
        print("2. 반차/병가 관련 모든 데이터 (employee_id 조건 없이):")
        print("=" * 70)
        result = await session.execute(
            text("""
                SELECT schedule.schedule_id, schedule.employee_id, schedule.schedule_type, 
                       schedule.title, employee.name AS employee_name
                FROM schedule 
                LEFT JOIN employee ON schedule.employee_id = employee.employee_id
                WHERE schedule.schedule_type LIKE '%반차%' 
                   OR schedule.schedule_type LIKE '%병가%'
                LIMIT 20
            """)
        )
        rows = result.mappings().all()
        
        if rows:
            print(f"\n총 {len(rows)}건의 반차/병가 일정:\n")
            for idx, row in enumerate(rows, 1):
                schedule_id = row.get("schedule_id")
                employee_id = row.get("employee_id")
                schedule_type = row.get("schedule_type")
                title = row.get("title", "제목 없음")
                employee_name = row.get("employee_name", "N/A")
                
                print(f"{idx}. [ID: {schedule_id}, 사번: {employee_id}, 이름: {employee_name}]")
                print(f"   - schedule_type: '{schedule_type}'")
                print(f"   - title: {title}")
                print()
        else:
            print("\n반차/병가 관련 일정이 없습니다.")
        
        # 3. 사번 4001, 4003의 모든 schedule_type 확인
        print("=" * 70)
        print("3. 사번 4001, 4003의 schedule_type 확인:")
        print("=" * 70)
        for emp_id in [4001, 4003]:
            result = await session.execute(
                text("""
                    SELECT DISTINCT schedule_type, COUNT(*) as count
                    FROM schedule 
                    WHERE employee_id = :emp_id
                    GROUP BY schedule_type
                """),
                {"emp_id": emp_id}
            )
            rows = result.fetchall()
            
            print(f"\n사번 {emp_id}:")
            if rows:
                for row in rows:
                    schedule_type = row[0]
                    count = row[1]
                    print(f"  - '{schedule_type}': {count}건")
            else:
                print("  - 일정이 없습니다.")

if __name__ == "__main__":
    try:
        _run(check_schedule_simple())
    except Exception as e:
        print(f"\n오류 발생: {e}")
        import traceback
        traceback.print_exc()

