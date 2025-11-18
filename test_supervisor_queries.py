"""Supervisor를 통한 모든 조회 기능 테스트."""

from __future__ import annotations

import json
from agents.supervisor import run_supervisor


def test_supervisor_queries() -> None:
    """Supervisor를 통한 모든 조회 기능 테스트."""
    print("=" * 70)
    print("Supervisor 조회 기능 테스트")
    print("=" * 70)
    
    test_queries = [
        ("사번 4001 일정 알려줘", "일정 조회"),
        ("사번 4001 연차 잔여 조회", "연차 잔여 조회"),
        ("회의실 일정 알려줘", "회의실 조회"),
    ]
    
    for query, description in test_queries:
        print(f"\n[{description}] 질의: '{query}'")
        print("-" * 70)
        try:
            result = run_supervisor(query)
            print(f"  성공: {result.get('ok')}")
            print(f"  메시지: {result.get('message', '')[:200]}")
            
            if result.get('rows'):
                print(f"  조회된 데이터: {len(result['rows'])}개")
                if result['rows']:
                    first = result['rows'][0]
                    if "employee_name" in first:
                        print(f"  [OK] employee_name 포함: {first['employee_name']}")
            
            if result.get('balance'):
                balance = result['balance']
                print(f"  잔여 연차: {balance.get('remaining_days')}")
                if "employee_name" in balance:
                    print(f"  [OK] employee_name 포함: {balance['employee_name']}")
            
            if not result.get('ok'):
                print(f"  [ERROR] 실패: {result.get('error', result.get('message', ''))}")
        except Exception as e:
            print(f"  [ERROR] 예외 발생: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    test_supervisor_queries()

