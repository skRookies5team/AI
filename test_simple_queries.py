"""간단한 조회 기능 테스트 (이벤트 루프 문제 없이)."""

from __future__ import annotations

import json
from agents.schedule_master import schedule_master
from agents.leave_agent import check_balance, LeaveQuery


def test_simple_queries() -> None:
    """간단한 조회 기능 테스트."""
    print("=" * 70)
    print("간단한 조회 기능 테스트")
    print("=" * 70)
    
    # 1. 잔여 연차 조회
    print("\n[1] 잔여 연차 조회: '사번 4001 연차 잔여 조회'")
    try:
        result = schedule_master("사번 4001 연차 잔여 조회")
        print(f"  성공: {result.get('ok')}")
        print(f"  메시지: {result.get('message', '')[:150]}")
        if result.get('balance'):
            balance = result['balance']
            print(f"  잔여 연차: {balance.get('remaining_days')}")
            if "employee_name" in balance:
                print(f"  [OK] employee_name: {balance['employee_name']}")
            else:
                print(f"  [ERROR] employee_name 없음")
    except Exception as e:
        print(f"  [ERROR] {e}")
        import traceback
        traceback.print_exc()
    
    # 2. 일정 조회 (사번)
    print("\n[2] 일정 조회 (사번): '사번 4001 일정 알려줘'")
    try:
        result = schedule_master("사번 4001 일정 알려줘")
        print(f"  성공: {result.get('ok')}")
        print(f"  메시지: {result.get('message', '')[:150]}")
        if result.get('rows'):
            print(f"  조회된 일정: {len(result['rows'])}개")
            first = result['rows'][0]
            if "employee_name" in first:
                print(f"  [OK] employee_name: {first['employee_name']}")
            else:
                print(f"  [ERROR] employee_name 없음")
        else:
            print(f"  [WARNING] 조회된 일정 없음")
    except Exception as e:
        print(f"  [ERROR] {e}")
        import traceback
        traceback.print_exc()
    
    # 3. 회의실 조회
    print("\n[3] 회의실 조회: '회의실 일정 알려줘'")
    try:
        result = schedule_master("회의실 일정 알려줘")
        print(f"  성공: {result.get('ok')}")
        print(f"  메시지: {result.get('message', '')[:150]}")
        if result.get('rows'):
            print(f"  조회된 일정: {len(result['rows'])}개")
        else:
            print(f"  [WARNING] 조회된 일정 없음")
    except Exception as e:
        print(f"  [ERROR] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_simple_queries()

