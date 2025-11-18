"""모든 테이블의 JOIN 상태 확인."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_data import get_session
from sqlalchemy import text


async def test_all_joins() -> None:
    """schedule, leave_balance_v2, leave_request와 employee 테이블의 JOIN 확인."""
    print("=" * 70)
    print("모든 테이블 JOIN 상태 확인")
    print("=" * 70)
    
    async with get_session() as session:
        # 1. 테이블 구조 확인
        print("\n[1] 테이블 구조 확인...")
        tables = ["schedule", "leave_balance_v2", "leave_request", "employee"]
        for table in tables:
            try:
                result = await session.execute(text(f"SHOW COLUMNS FROM {table}"))
                columns = [row[0] for row in result.fetchall()]
                print(f"  {table}: {len(columns)}개 컬럼")
                if "employee_id" in columns:
                    print(f"    ✓ employee_id 컬럼 존재")
                else:
                    print(f"    ✗ employee_id 컬럼 없음")
            except Exception as e:
                print(f"  {table}: 오류 - {e}")
        
        # 2. schedule 테이블과 employee JOIN 확인
        print("\n[2] schedule ↔ employee JOIN 테스트...")
        try:
            query = text("""
                SELECT 
                    schedule.schedule_id,
                    schedule.employee_id,
                    employee.name AS employee_name
                FROM schedule
                LEFT JOIN employee ON schedule.employee_id = employee.employee_id
                WHERE schedule.employee_id = 4001
                LIMIT 3
            """)
            result = await session.execute(query)
            rows = [dict(row) for row in result.mappings().all()]
            if rows:
                print(f"  [성공] {len(rows)}개 일정 조회")
                print(f"  첫 번째 일정:")
                print(json.dumps(rows[0], ensure_ascii=False, indent=2, default=str))
                if "employee_name" in rows[0] and rows[0]["employee_name"]:
                    print(f"    ✓ employee_name 포함됨: {rows[0]['employee_name']}")
                else:
                    print(f"    ✗ employee_name 없음")
            else:
                print(f"  [경고] 사번 4001의 일정이 없습니다.")
        except Exception as e:
            print(f"  [오류] JOIN 실패: {e}")
        
        # 3. leave_balance_v2 테이블과 employee JOIN 확인
        print("\n[3] leave_balance_v2 ↔ employee JOIN 테스트...")
        try:
            query = text("""
                SELECT 
                    leave_balance_v2.employee_id,
                    leave_balance_v2.remaining_days,
                    employee.name AS employee_name
                FROM leave_balance_v2
                LEFT JOIN employee ON leave_balance_v2.employee_id = employee.employee_id
                WHERE leave_balance_v2.employee_id = 4001
                LIMIT 1
            """)
            result = await session.execute(query)
            row = result.mappings().first()
            if row:
                row_dict = dict(row)
                print(f"  [성공] 조회됨")
                print(json.dumps(row_dict, ensure_ascii=False, indent=2, default=str))
                if "employee_name" in row_dict and row_dict["employee_name"]:
                    print(f"    ✓ employee_name 포함됨: {row_dict['employee_name']}")
                else:
                    print(f"    ✗ employee_name 없음")
            else:
                print(f"  [경고] 사번 4001의 leave_balance_v2 데이터가 없습니다.")
        except Exception as e:
            print(f"  [오류] JOIN 실패: {e}")
        
        # 4. leave_request 테이블과 employee JOIN 확인
        print("\n[4] leave_request ↔ employee JOIN 테스트...")
        try:
            query = text("""
                SELECT 
                    leave_request.employee_id,
                    leave_request.total_days,
                    employee.name AS employee_name
                FROM leave_request
                LEFT JOIN employee ON leave_request.employee_id = employee.employee_id
                WHERE leave_request.employee_id = 4001
                LIMIT 3
            """)
            result = await session.execute(query)
            rows = [dict(row) for row in result.mappings().all()]
            if rows:
                print(f"  [성공] {len(rows)}개 요청 조회")
                print(f"  첫 번째 요청:")
                print(json.dumps(rows[0], ensure_ascii=False, indent=2, default=str))
                if "employee_name" in rows[0] and rows[0]["employee_name"]:
                    print(f"    ✓ employee_name 포함됨: {rows[0]['employee_name']}")
                else:
                    print(f"    ✗ employee_name 없음")
            else:
                print(f"  [경고] 사번 4001의 leave_request 데이터가 없습니다.")
        except Exception as e:
            print(f"  [오류] JOIN 실패: {e}")
        
        # 5. employee_id 연결성 확인
        print("\n[5] employee_id 연결성 확인...")
        try:
            # schedule에 있는 employee_id가 employee에 존재하는지
            query = text("""
                SELECT COUNT(DISTINCT s.employee_id) as schedule_emp_count,
                       COUNT(DISTINCT e.employee_id) as employee_count,
                       COUNT(DISTINCT CASE WHEN e.employee_id IS NOT NULL THEN s.employee_id END) as matched_count
                FROM schedule s
                LEFT JOIN employee e ON s.employee_id = e.employee_id
            """)
            result = await session.execute(query)
            row = result.first()
            if row:
                print(f"  schedule의 고유 employee_id: {row.schedule_emp_count}")
                print(f"  employee 테이블의 고유 employee_id: {row.employee_count}")
                print(f"  매칭된 employee_id: {row.matched_count}")
                if row.matched_count == row.schedule_emp_count:
                    print(f"    ✓ 모든 schedule employee_id가 employee에 존재")
                else:
                    print(f"    ⚠ 일부 schedule employee_id가 employee에 없음")
            
            # leave_balance_v2에 있는 employee_id가 employee에 존재하는지
            query = text("""
                SELECT COUNT(DISTINCT l.employee_id) as leave_emp_count,
                       COUNT(DISTINCT CASE WHEN e.employee_id IS NOT NULL THEN l.employee_id END) as matched_count
                FROM leave_balance_v2 l
                LEFT JOIN employee e ON l.employee_id = e.employee_id
            """)
            result = await session.execute(query)
            row = result.first()
            if row:
                print(f"  leave_balance_v2의 고유 employee_id: {row.leave_emp_count}")
                print(f"  매칭된 employee_id: {row.matched_count}")
                if row.matched_count == row.leave_emp_count:
                    print(f"    ✓ 모든 leave_balance_v2 employee_id가 employee에 존재")
                else:
                    print(f"    ⚠ 일부 leave_balance_v2 employee_id가 employee에 없음")
        except Exception as e:
            print(f"  [오류] 연결성 확인 실패: {e}")


if __name__ == "__main__":
    asyncio.run(test_all_joins())

