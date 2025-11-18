"""모든 JOIN 상태를 확인하고 문제를 진단하는 스크립트."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_data import fetch_schedule_rows, ScheduleFilters, get_session
from agents.leave_data import get_balance
from agents.schedule_langgraph import run_schedule_agent
from sqlalchemy import text


async def verify_all_joins() -> None:
    """모든 JOIN 상태를 확인하고 문제를 진단."""
    print("=" * 70)
    print("JOIN 상태 종합 확인")
    print("=" * 70)
    
    # 1. schedule JOIN 확인
    print("\n[1] schedule 테이블 JOIN 확인...")
    try:
        filters = ScheduleFilters(employee_id=4001, limit=3)
        rows = await fetch_schedule_rows(filters)
        if rows:
            print(f"  조회된 일정: {len(rows)}개")
            first = rows[0]
            if "employee_name" in first:
                print(f"  [OK] employee_name 포함: {first['employee_name']}")
            else:
                print(f"  [ERROR] employee_name이 없습니다!")
                print(f"  컬럼 목록: {list(first.keys())}")
        else:
            print(f"  [WARNING] 조회된 일정이 없습니다.")
    except Exception as e:
        print(f"  [ERROR] 오류: {e}")
        import traceback
        traceback.print_exc()
    
    # 2. leave_balance_v2 JOIN 확인
    print("\n[2] leave_balance_v2 테이블 JOIN 확인...")
    try:
        balance = get_balance(4001)
        if balance:
            if "employee_name" in balance:
                print(f"  [OK] employee_name 포함: {balance['employee_name']}")
            else:
                print(f"  [ERROR] employee_name이 없습니다!")
                print(f"  컬럼 목록: {list(balance.keys())}")
        else:
            print(f"  [WARNING] balance 데이터가 없습니다.")
    except Exception as e:
        print(f"  [ERROR] 오류: {e}")
        import traceback
        traceback.print_exc()
    
    # 3. 실제 SQL JOIN 쿼리 테스트
    print("\n[3] 직접 SQL JOIN 쿼리 테스트...")
    async with get_session() as session:
        # schedule JOIN
        try:
            result = await session.execute(text("""
                SELECT schedule.schedule_id, schedule.employee_id, employee.name AS employee_name
                FROM schedule
                LEFT JOIN employee ON schedule.employee_id = employee.employee_id
                WHERE schedule.employee_id = 4001
                LIMIT 1
            """))
            row = result.mappings().first()
            if row:
                row_dict = dict(row)
                if "employee_name" in row_dict:
                    print(f"  schedule JOIN: [OK] employee_name = {row_dict['employee_name']}")
                else:
                    print(f"  schedule JOIN: [ERROR] employee_name 없음")
            else:
                print(f"  schedule JOIN: [WARNING] 데이터 없음")
        except Exception as e:
            print(f"  schedule JOIN: [ERROR] {e}")
        
        # leave_balance_v2 JOIN
        try:
            result = await session.execute(text("""
                SELECT leave_balance_v2.employee_id, leave_balance_v2.remaining_days, employee.name AS employee_name
                FROM leave_balance_v2
                LEFT JOIN employee ON leave_balance_v2.employee_id = employee.employee_id
                WHERE leave_balance_v2.employee_id = 4001
                LIMIT 1
            """))
            row = result.mappings().first()
            if row:
                row_dict = dict(row)
                if "employee_name" in row_dict:
                    print(f"  leave_balance_v2 JOIN: [OK] employee_name = {row_dict['employee_name']}")
                else:
                    print(f"  leave_balance_v2 JOIN: [ERROR] employee_name 없음")
            else:
                print(f"  leave_balance_v2 JOIN: [WARNING] 데이터 없음")
        except Exception as e:
            print(f"  leave_balance_v2 JOIN: [ERROR] {e}")
    
    # 4. 챗봇 응답 확인
    print("\n[4] 챗봇 응답 확인...")
    try:
        result = run_schedule_agent("사번 4001 일정 알려줘")
        print(f"  성공: {result.get('ok')}")
        print(f"  메시지: {result.get('message', '')[:100]}")
        if result.get('rows'):
            first_row = result['rows'][0]
            if "employee_name" in first_row:
                print(f"  [OK] 응답에 employee_name 포함: {first_row['employee_name']}")
            else:
                print(f"  [ERROR] 응답에 employee_name 없음")
    except Exception as e:
        print(f"  [ERROR] 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(verify_all_joins())

