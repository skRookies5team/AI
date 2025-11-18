"""schedule 테이블의 schedule_type 값들을 확인하는 스크립트."""

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
    print("\n다음 중 하나의 방법으로 실행하세요:")
    print("\n[방법 1] 가상환경 활성화 후 실행:")
    print("  .venv\\Scripts\\activate")
    print("  python check_schedule_types.py")
    print("\n[방법 2] uv 사용:")
    print("  uv run python check_schedule_types.py")
    print("\n" + "=" * 70)
    sys.exit(1)

from sqlalchemy import text

async def check_schedule_types():
    """schedule 테이블의 schedule_type 값들을 확인한다."""
    async with get_session() as session:
        # schedule_type 컬럼의 고유 값들 조회
        result = await session.execute(
            text("SELECT DISTINCT schedule_type, COUNT(*) as count FROM schedule GROUP BY schedule_type ORDER BY count DESC")
        )
        rows = result.fetchall()
        
        print("=" * 70)
        print("schedule 테이블의 schedule_type 값들:")
        print("=" * 70)
        print(f"\n총 {len(rows)}개의 고유한 schedule_type이 있습니다:\n")
        
        for row in rows:
            schedule_type = row[0]
            count = row[1]
            print(f"  - '{schedule_type}': {count}건")
        
        # 특정 employee_id의 schedule_type 확인
        print("\n" + "=" * 70)
        print("특정 사원의 schedule_type 확인:")
        print("=" * 70)
        
        # employee_id 4001, 4003의 schedule_type 확인
        for emp_id in [4001, 4003]:
            result = await session.execute(
                text("""
                    SELECT schedule_type, COUNT(*) as count 
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
        
        # 반차, 병가 관련 모든 값 확인
        print("\n" + "=" * 70)
        print("반차/병가 관련 schedule_type 확인:")
        print("=" * 70)
        
        result = await session.execute(
            text("""
                SELECT DISTINCT schedule_type, COUNT(*) as count 
                FROM schedule 
                WHERE schedule_type LIKE '%반차%' OR schedule_type LIKE '%병가%'
                GROUP BY schedule_type
                ORDER BY count DESC
            """)
        )
        rows = result.fetchall()
        
        if rows:
            for row in rows:
                schedule_type = row[0]
                count = row[1]
                print(f"  - '{schedule_type}': {count}건")
        else:
            print("  - 반차/병가 관련 일정이 없습니다.")

if __name__ == "__main__":
    try:
        _run(check_schedule_types())
    except Exception as e:
        print(f"\n오류 발생: {e}")
        import traceback
        traceback.print_exc()

