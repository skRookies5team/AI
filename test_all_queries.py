"""모든 조회 기능 종합 테스트."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_master import schedule_master
from agents.leave_agent import check_balance
from agents.schedule_data import fetch_schedule_rows, ScheduleFilters, get_session
from sqlalchemy import text


async def test_all_queries() -> None:
    """모든 조회 기능을 테스트하고 문제를 진단."""
    print("=" * 70)
    print("모든 조회 기능 종합 테스트")
    print("=" * 70)
    
    # 1. 잔여 연차 조회 테스트
    print("\n[1] 잔여 연차 조회 테스트...")
    print("  질의: '사번 4001 연차 잔여 조회'")
    try:
        result = schedule_master("사번 4001 연차 잔여 조회")
        print(f"  성공: {result.get('ok')}")
        print(f"  메시지: {result.get('message', '')[:100]}")
        if result.get('balance'):
            balance = result['balance']
            print(f"  잔여 연차: {balance.get('remaining_days')}")
            if "employee_name" in balance:
                print(f"  [OK] employee_name 포함: {balance['employee_name']}")
            else:
                print(f"  [ERROR] employee_name 없음")
                print(f"  컬럼: {list(balance.keys())}")
    except Exception as e:
        print(f"  [ERROR] 오류: {e}")
        import traceback
        traceback.print_exc()
    
    # 2. 사원 일정 조회 테스트 (사번)
    print("\n[2] 사원 일정 조회 테스트 (사번)...")
    print("  질의: '사번 4001 일정 알려줘'")
    try:
        result = schedule_master("사번 4001 일정 알려줘")
        print(f"  성공: {result.get('ok')}")
        print(f"  메시지: {result.get('message', '')[:100]}")
        if result.get('rows'):
            print(f"  조회된 일정: {len(result['rows'])}개")
            first = result['rows'][0]
            if "employee_name" in first:
                print(f"  [OK] employee_name 포함: {first['employee_name']}")
            else:
                print(f"  [ERROR] employee_name 없음")
                print(f"  컬럼: {list(first.keys())}")
        else:
            print(f"  [WARNING] 조회된 일정이 없습니다.")
    except Exception as e:
        print(f"  [ERROR] 오류: {e}")
        import traceback
        traceback.print_exc()
    
    # 3. 사원 일정 조회 테스트 (이름)
    print("\n[3] 사원 일정 조회 테스트 (이름)...")
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name FROM employee WHERE employee_id = 4001 LIMIT 1")
        )
        name_row = result.first()
        if name_row:
            test_name = name_row[0]
            print(f"  질의: '{test_name} 일정 알려줘'")
            try:
                result = schedule_master(f"{test_name} 일정 알려줘")
                print(f"  성공: {result.get('ok')}")
                print(f"  메시지: {result.get('message', '')[:100]}")
                if result.get('rows'):
                    print(f"  조회된 일정: {len(result['rows'])}개")
                    first = result['rows'][0]
                    if "employee_name" in first:
                        print(f"  [OK] employee_name 포함: {first['employee_name']}")
                    else:
                        print(f"  [ERROR] employee_name 없음")
                else:
                    print(f"  [WARNING] 조회된 일정이 없습니다.")
            except Exception as e:
                print(f"  [ERROR] 오류: {e}")
                import traceback
                traceback.print_exc()
    
    # 4. 회의실 조회 테스트
    print("\n[4] 회의실 조회 테스트...")
    print("  질의: '회의실 일정 알려줘'")
    try:
        result = schedule_master("회의실 일정 알려줘")
        print(f"  성공: {result.get('ok')}")
        print(f"  메시지: {result.get('message', '')[:100]}")
        if result.get('rows'):
            print(f"  조회된 일정: {len(result['rows'])}개")
            # 회의실 관련 일정인지 확인
            first = result['rows'][0]
            schedule_type = first.get('schedule_type') or first.get('type')
            if schedule_type and ('회의' in str(schedule_type) or '미팅' in str(schedule_type)):
                print(f"  [OK] 회의실 관련 일정 조회됨")
            else:
                print(f"  [WARNING] 회의실 관련 일정이 아닐 수 있음")
        else:
            print(f"  [WARNING] 조회된 일정이 없습니다.")
    except Exception as e:
        print(f"  [ERROR] 오류: {e}")
        import traceback
        traceback.print_exc()
    
    # 5. 직접 DB 조회 테스트
    print("\n[5] 직접 DB 조회 테스트...")
    async with get_session() as session:
        # schedule 조회
        try:
            filters = ScheduleFilters(employee_id=4001, limit=3)
            rows = await fetch_schedule_rows(filters)
            print(f"  schedule 조회: {len(rows)}개")
            if rows:
                if "employee_name" in rows[0]:
                    print(f"  [OK] employee_name 포함: {rows[0]['employee_name']}")
                else:
                    print(f"  [ERROR] employee_name 없음")
        except Exception as e:
            print(f"  [ERROR] schedule 조회 실패: {e}")
        
        # leave_balance_v2 조회
        try:
            result = await session.execute(text("""
                SELECT leave_balance_v2.*, employee.name AS employee_name
                FROM leave_balance_v2
                LEFT JOIN employee ON leave_balance_v2.employee_id = employee.employee_id
                WHERE leave_balance_v2.employee_id = 4001
                LIMIT 1
            """))
            row = result.mappings().first()
            if row:
                row_dict = dict(row)
                print(f"  leave_balance_v2 조회: 성공")
                if "employee_name" in row_dict:
                    print(f"  [OK] employee_name 포함: {row_dict['employee_name']}")
                else:
                    print(f"  [ERROR] employee_name 없음")
            else:
                print(f"  [WARNING] leave_balance_v2 데이터 없음")
        except Exception as e:
            print(f"  [ERROR] leave_balance_v2 조회 실패: {e}")


if __name__ == "__main__":
    asyncio.run(test_all_queries())

