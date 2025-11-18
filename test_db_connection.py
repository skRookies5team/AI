"""DB 연결 및 데이터 조회 진단 스크립트."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_data import (
    fetch_schedule_rows,
    ScheduleFilters,
    get_session,
    _get_schedule_columns,
    _get_employee_columns,
)
from sqlalchemy import text


async def test_db_connection() -> None:
    """DB 연결 및 기본 조회 테스트."""
    print("=" * 70)
    print("DB 연결 및 데이터 조회 진단")
    print("=" * 70)
    
    try:
        # 1. 세션 연결 테스트
        print("\n[1] DB 세션 연결 테스트...")
        async with get_session() as session:
            print("[SUCCESS] DB 세션 연결 성공!")
            
            # 2. schedule 테이블 존재 확인
            print("\n[2] schedule 테이블 확인...")
            try:
                result = await session.execute(text("SHOW TABLES LIKE 'schedule'"))
                tables = result.fetchall()
                if tables:
                    print(f"[SUCCESS] schedule 테이블 존재 확인")
                else:
                    print("[ERROR] schedule 테이블이 존재하지 않습니다!")
                    return
            except Exception as e:
                print(f"[ERROR] 테이블 확인 실패: {e}")
                return
            
            # 3. schedule 테이블 컬럼 확인
            print("\n[3] schedule 테이블 컬럼 확인...")
            columns = await _get_schedule_columns(session)
            print(f"[SUCCESS] 컬럼 수: {len(columns)}")
            print(f"주요 컬럼: {', '.join(columns[:10])}")
            
            # 4. employee 테이블 확인
            print("\n[4] employee 테이블 확인...")
            try:
                result = await session.execute(text("SHOW TABLES LIKE 'employee'"))
                tables = result.fetchall()
                if tables:
                    print("[SUCCESS] employee 테이블 존재 확인")
                    employee_columns = await _get_employee_columns(session)
                    print(f"employee 컬럼 수: {len(employee_columns)}")
                    print(f"주요 컬럼: {', '.join(employee_columns[:10])}")
                else:
                    print("[WARNING] employee 테이블이 존재하지 않습니다 (JOIN 불가)")
            except Exception as e:
                print(f"[WARNING] employee 테이블 확인 실패: {e}")
            
            # 5. 전체 일정 개수 확인
            print("\n[5] schedule 테이블 전체 데이터 개수 확인...")
            try:
                result = await session.execute(text("SELECT COUNT(*) as cnt FROM schedule"))
                count = result.scalar()
                print(f"[SUCCESS] 전체 일정 개수: {count}")
                if count == 0:
                    print("[WARNING] schedule 테이블에 데이터가 없습니다!")
                    return
            except Exception as e:
                print(f"[ERROR] 개수 조회 실패: {e}")
                return
            
            # 6. 사번 4001의 일정 개수 확인
            print("\n[6] 사번 4001의 일정 개수 확인...")
            try:
                result = await session.execute(
                    text("SELECT COUNT(*) as cnt FROM schedule WHERE employee_id = :emp_id"),
                    {"emp_id": 4001}
                )
                count = result.scalar()
                print(f"[SUCCESS] 사번 4001의 일정 개수: {count}")
                if count == 0:
                    print("[WARNING] 사번 4001의 일정이 없습니다!")
            except Exception as e:
                print(f"[ERROR] 사번별 조회 실패: {e}")
            
            # 7. 실제 데이터 샘플 조회 (JOIN 없이)
            print("\n[7] 실제 데이터 샘플 조회 (JOIN 없이)...")
            try:
                result = await session.execute(
                    text("SELECT * FROM schedule WHERE employee_id = :emp_id LIMIT 3"),
                    {"emp_id": 4001}
                )
                rows = [dict(row) for row in result.mappings().all()]
                if rows:
                    print(f"[SUCCESS] 샘플 데이터 {len(rows)}개 조회 성공")
                    print("첫 번째 행:")
                    print(json.dumps(rows[0], ensure_ascii=False, indent=2, default=str))
                else:
                    print("[WARNING] 사번 4001의 일정 데이터가 없습니다!")
            except Exception as e:
                print(f"[ERROR] 샘플 조회 실패: {e}")
                import traceback
                traceback.print_exc()
            
            # 8. JOIN 테스트
            print("\n[8] JOIN 테스트...")
            try:
                query = text("""
                    SELECT schedule.*, employee.employee_name AS employee_name
                    FROM schedule
                    LEFT JOIN employee ON schedule.employee_id = employee.employee_id
                    WHERE schedule.employee_id = :emp_id
                    LIMIT 3
                """)
                result = await session.execute(query, {"emp_id": 4001})
                rows = [dict(row) for row in result.mappings().all()]
                if rows:
                    print(f"[SUCCESS] JOIN 조회 성공: {len(rows)}개")
                    first = rows[0]
                    if "employee_name" in first:
                        print(f"employee_name 포함: {first['employee_name']}")
                    else:
                        print("[WARNING] employee_name이 결과에 없습니다")
                else:
                    print("[WARNING] JOIN 결과가 없습니다")
            except Exception as e:
                print(f"[ERROR] JOIN 실패: {e}")
                import traceback
                traceback.print_exc()
            
            # 9. fetch_schedule_rows 함수 테스트
            print("\n[9] fetch_schedule_rows 함수 테스트...")
            try:
                filters = ScheduleFilters(employee_id=4001, limit=5)
                rows = await fetch_schedule_rows(filters)
                print(f"[SUCCESS] fetch_schedule_rows 결과: {len(rows)}개")
                if rows:
                    print("첫 번째 행:")
                    print(json.dumps(rows[0], ensure_ascii=False, indent=2, default=str))
                else:
                    print("[WARNING] fetch_schedule_rows가 빈 결과를 반환했습니다")
            except Exception as e:
                print(f"[ERROR] fetch_schedule_rows 실패: {e}")
                import traceback
                traceback.print_exc()
                
    except Exception as e:
        print(f"\n[ERROR] 전체 테스트 실패: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_db_connection())

