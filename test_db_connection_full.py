"""DB 연결 및 테이블 데이터 확인 스크립트."""

from __future__ import annotations

import asyncio
import os
from agents.schedule_data import get_session, _resolve_employee_ids_by_names
from sqlalchemy import text


async def check_db_connection():
    """DB 연결 및 테이블 데이터 확인."""
    print("=" * 70)
    print("DB 연결 및 테이블 데이터 확인")
    print("=" * 70)
    
    try:
        async with get_session() as session:
            # 1. employee 테이블 확인
            print("\n[1] employee 테이블 확인")
            result = await session.execute(text("SELECT COUNT(*) as cnt FROM employee"))
            count = result.scalar()
            print(f"  - 총 직원 수: {count}명")
            
            # 샘플 데이터 조회
            result = await session.execute(
                text("SELECT employee_id, name FROM employee LIMIT 10")
            )
            rows = result.fetchall()
            print(f"  - 샘플 데이터 (최대 10개):")
            for row in rows:
                print(f"    사번: {row[0]}, 이름: {row[1]}")
            
            # 2. schedule 테이블 확인
            print("\n[2] schedule 테이블 확인")
            result = await session.execute(text("SELECT COUNT(*) as cnt FROM schedule"))
            count = result.scalar()
            print(f"  - 총 일정 수: {count}개")
            
            # 샘플 데이터 조회 (JOIN 포함)
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
            print(f"  - 샘플 일정 데이터 (JOIN 포함, 최대 10개):")
            for row in rows:
                print(f"    일정ID: {row[0]}, 사번: {row[1]}, 이름: {row[5]}, 제목: {row[2]}, 시작: {row[3]}, 종료: {row[4]}")
            
            # 3. 이름 검색 테스트
            print("\n[3] 이름 검색 테스트")
            test_names = ["강하은", "윤서준", "한지민", "누구누구"]
            for name in test_names:
                resolved_ids = await _resolve_employee_ids_by_names(session, [name])
                if resolved_ids:
                    print(f"  - '{name}' -> 사번: {resolved_ids}")
                else:
                    print(f"  - '{name}' -> 검색 결과 없음")
            
            # 4. 일정 조회 테스트 (이름으로)
            print("\n[4] 일정 조회 테스트 (이름으로)")
            for name in ["강하은", "윤서준", "한지민"]:
                resolved_ids = await _resolve_employee_ids_by_names(session, [name])
                if resolved_ids:
                    result = await session.execute(
                        text("""
                            SELECT s.schedule_id, s.title, s.start_time, s.end_time, e.name
                            FROM schedule s
                            LEFT JOIN employee e ON s.employee_id = e.employee_id
                            WHERE s.employee_id = :emp_id
                            LIMIT 5
                        """),
                        {"emp_id": resolved_ids[0]}
                    )
                    rows = result.fetchall()
                    print(f"  - '{name}' (사번: {resolved_ids[0]}) 일정: {len(rows)}개")
                    for row in rows:
                        print(f"    제목: {row[1]}, 시작: {row[2]}, 종료: {row[3]}, 이름: {row[4]}")
                else:
                    print(f"  - '{name}' -> 사번을 찾을 수 없음")
            
    except Exception as e:
        print(f"\n[오류] DB 연결 실패: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(check_db_connection())

