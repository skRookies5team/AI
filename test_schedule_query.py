"""특정 사원의 schedule 데이터를 직접 조회하는 테스트 스크립트."""

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
    print("  python test_schedule_query.py")
    print("\n[방법 2] uv 사용:")
    print("  uv run python test_schedule_query.py")
    print("\n" + "=" * 70)
    sys.exit(1)

from sqlalchemy import text
from agents.schedule_data import _get_schedule_columns, _match_column, _DEFAULT_START_CANDIDATES

async def test_schedule_query():
    """특정 사원의 schedule 데이터를 조회한다."""
    async with get_session() as session:
        # schedule 테이블의 컬럼 확인
        columns = await _get_schedule_columns(session)
        start_column = _match_column(columns, _DEFAULT_START_CANDIDATES)
        
        # ORDER BY 절 생성 (start_column이 있으면 사용, 없으면 생략)
        order_by_clause = f"ORDER BY schedule.{start_column} ASC" if start_column else ""
        
        # 사번 4001, 4003의 모든 schedule 데이터 조회
        for emp_id in [4001, 4003]:
            print(f"\n{'='*70}")
            print(f"사번 {emp_id}의 모든 schedule 데이터:")
            print(f"{'='*70}")
            
            query = f"""
                SELECT schedule.*, employee.name AS employee_name 
                FROM schedule 
                LEFT JOIN employee ON schedule.employee_id = employee.employee_id
                WHERE schedule.employee_id = :emp_id
                {order_by_clause}
                LIMIT 20
            """
            
            result = await session.execute(
                text(query),
                {"emp_id": emp_id}
            )
            rows = result.mappings().all()
            
            if rows:
                print(f"\n총 {len(rows)}건의 일정이 있습니다:\n")
                for idx, row in enumerate(rows, 1):
                    schedule_id = row.get("schedule_id") or row.get("id")
                    title = row.get("title", "제목 없음")
                    schedule_type = row.get("schedule_type") or row.get("type", "N/A")
                    start_at = row.get("start_at") or row.get("start_time") or row.get("start_datetime")
                    employee_name = row.get("employee_name", "N/A")
                    
                    print(f"{idx}. [ID: {schedule_id}] {title}")
                    print(f"   - schedule_type: '{schedule_type}'")
                    print(f"   - employee_name: {employee_name}")
                    if start_at:
                        print(f"   - start_at: {start_at}")
                    print()
            else:
                print(f"\n사번 {emp_id}의 일정이 없습니다.")
        
        # 반차/병가 관련 모든 데이터 확인
        print(f"\n{'='*70}")
        print("반차/병가 관련 모든 schedule 데이터:")
        print(f"{'='*70}")
        
        # ORDER BY 절 생성
        order_by_clause = f"ORDER BY schedule.employee_id, schedule.{start_column} ASC" if start_column else "ORDER BY schedule.employee_id ASC"
        
        result = await session.execute(
            text(f"""
                SELECT schedule.*, employee.name AS employee_name 
                FROM schedule 
                LEFT JOIN employee ON schedule.employee_id = employee.employee_id
                WHERE schedule.schedule_type LIKE '%반차%' 
                   OR schedule.schedule_type LIKE '%병가%'
                {order_by_clause}
                LIMIT 50
            """)
        )
        rows = result.mappings().all()
        
        if rows:
            print(f"\n총 {len(rows)}건의 반차/병가 일정이 있습니다:\n")
            for idx, row in enumerate(rows, 1):
                schedule_id = row.get("schedule_id") or row.get("id")
                title = row.get("title", "제목 없음")
                schedule_type = row.get("schedule_type") or row.get("type", "N/A")
                employee_id = row.get("employee_id")
                employee_name = row.get("employee_name", "N/A")
                start_at = row.get("start_at") or row.get("start_time") or row.get("start_datetime")
                
                print(f"{idx}. [사번: {employee_id}, 이름: {employee_name}] {title}")
                print(f"   - schedule_type: '{schedule_type}'")
                if start_at:
                    print(f"   - start_at: {start_at}")
                print()
        else:
            print("\n반차/병가 관련 일정이 없습니다.")

if __name__ == "__main__":
    try:
        _run(test_schedule_query())
    except Exception as e:
        print(f"\n오류 발생: {e}")
        import traceback
        traceback.print_exc()

