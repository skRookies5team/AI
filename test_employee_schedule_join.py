"""employee와 schedule 테이블 JOIN 확인 및 이름 기반 조회 테스트."""

from __future__ import annotations

import asyncio
from agents.schedule_data import get_session, _resolve_employee_ids_by_names, fetch_schedule_rows, ScheduleFilters


async def test_employee_schedule_join():
    """employee와 schedule 테이블 JOIN 확인 및 이름 기반 조회 테스트."""
    print("=" * 70)
    print("employee와 schedule 테이블 JOIN 확인")
    print("=" * 70)
    
    try:
        async with get_session() as session:
            # 1. employee 테이블 구조 및 샘플 데이터 확인
            print("\n[1] employee 테이블 확인")
            result = await session.execute(
                text("SHOW COLUMNS FROM employee")
            )
            columns = [row[0] for row in result.fetchall()]
            print(f"  - 컬럼: {', '.join(columns)}")
            
            result = await session.execute(
                text("SELECT employee_id, name FROM employee LIMIT 10")
            )
            employees = result.fetchall()
            print(f"  - 샘플 직원 데이터 (최대 10개):")
            for emp in employees:
                print(f"    사번: {emp[0]}, 이름: {emp[1]}")
            
            # 2. schedule 테이블 구조 확인
            print("\n[2] schedule 테이블 확인")
            result = await session.execute(
                text("SHOW COLUMNS FROM schedule")
            )
            columns = [row[0] for row in result.fetchall()]
            print(f"  - 컬럼: {', '.join(columns)}")
            
            # 3. JOIN 테스트 (직접 SQL)
            print("\n[3] JOIN 테스트 (직접 SQL)")
            result = await session.execute(
                text("""
                    SELECT s.schedule_id, s.employee_id, s.title, s.start_time, s.end_time,
                           e.name as employee_name
                    FROM schedule s
                    LEFT JOIN employee e ON s.employee_id = e.employee_id
                    LIMIT 10
                """)
            )
            rows = result.fetchall()
            print(f"  - JOIN 결과 (최대 10개):")
            for row in rows:
                print(f"    일정ID: {row[0]}, 사번: {row[1]}, 이름: {row[5]}, 제목: {row[2]}, 시작: {row[3]}, 종료: {row[4]}")
            
            # 4. 이름으로 employee_id 찾기 테스트
            print("\n[4] 이름으로 employee_id 찾기 테스트")
            test_names = ["강하은", "윤서준", "한지민"]
            for name in test_names:
                resolved_ids = await _resolve_employee_ids_by_names(session, [name])
                if resolved_ids:
                    print(f"  - '{name}' -> 사번: {resolved_ids}")
                else:
                    print(f"  - '{name}' -> 검색 결과 없음 (DB에 해당 이름이 없을 수 있음)")
            
            # 5. 이름으로 일정 조회 테스트 (fetch_schedule_rows 사용)
            print("\n[5] 이름으로 일정 조회 테스트 (fetch_schedule_rows 사용)")
            for name in test_names:
                print(f"\n  [{name} 일정 조회]")
                filters = ScheduleFilters(employee_name_keywords=[name])
                rows = await fetch_schedule_rows(filters)
                print(f"    조회된 일정: {len(rows)}개")
                if rows:
                    for i, row in enumerate(rows[:3], 1):  # 최대 3개만 표시
                        employee_name = row.get("employee_name", "미확인")
                        employee_id = row.get("employee_id", "미확인")
                        title = row.get("title", "제목 없음")
                        start = row.get("start_time") or row.get("start_at", "미지정")
                        print(f"    {i}. 이름: {employee_name} (사번: {employee_id}), 제목: {title}, 시작: {start}")
                else:
                    print(f"    조회된 일정이 없습니다.")
                    # 해당 이름의 employee_id가 있는지 다시 확인
                    resolved_ids = await _resolve_employee_ids_by_names(session, [name])
                    if resolved_ids:
                        print(f"    (참고: '{name}'의 사번은 {resolved_ids[0]}이지만 일정이 없습니다)")
                    else:
                        print(f"    (참고: '{name}'라는 이름의 직원을 찾을 수 없습니다)")
            
            # 6. 실제 DB에 있는 이름으로 테스트
            print("\n[6] 실제 DB에 있는 이름으로 테스트")
            if employees:
                # 첫 번째 직원 이름으로 테스트
                test_name = employees[0][1]  # name 컬럼
                test_id = employees[0][0]    # employee_id
                print(f"  - 테스트 이름: '{test_name}' (사번: {test_id})")
                
                # 이름으로 일정 조회
                filters = ScheduleFilters(employee_name_keywords=[test_name])
                rows = await fetch_schedule_rows(filters)
                print(f"    조회된 일정: {len(rows)}개")
                if rows:
                    for i, row in enumerate(rows[:3], 1):
                        employee_name = row.get("employee_name", "미확인")
                        employee_id = row.get("employee_id", "미확인")
                        title = row.get("title", "제목 없음")
                        print(f"    {i}. 이름: {employee_name} (사번: {employee_id}), 제목: {title}")
                else:
                    print(f"    조회된 일정이 없습니다.")
                
                # 사번으로 직접 조회해서 비교
                result = await session.execute(
                    text("""
                        SELECT COUNT(*) as cnt
                        FROM schedule
                        WHERE employee_id = :emp_id
                    """),
                    {"emp_id": test_id}
                )
                count = result.scalar()
                print(f"    (참고: 사번 {test_id}로 직접 조회한 일정 수: {count}개)")
            
    except Exception as e:
        print(f"\n[오류] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    from sqlalchemy import text
    asyncio.run(test_employee_schedule_join())

