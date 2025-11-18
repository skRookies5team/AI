"""이름 기반 일정 조회 디버깅 테스트."""

from __future__ import annotations

import os
os.environ["DEBUG_SCHEDULE"] = "true"

from agents.schedule_langgraph import run_schedule_agent


def test_name_queries():
    """이름 기반 일정 조회 테스트."""
    print("=" * 70)
    print("이름 기반 일정 조회 디버깅 테스트")
    print("=" * 70)
    
    test_queries = [
        "강하은 일정 알려줘",
        "윤서준 일정 알려줘",
        "한지민 일정 알려줘",
    ]
    
    for query in test_queries:
        print(f"\n{'='*70}")
        print(f"질의: '{query}'")
        print(f"{'='*70}")
        result = run_schedule_agent(query)
        print(f"\n결과:")
        print(f"  ok: {result.get('ok')}")
        print(f"  message: {result.get('message', '')[:200]}")
        print(f"  rows: {len(result.get('rows', []))}개")
        if result.get('rows'):
            print(f"  첫 번째 일정:")
            first_row = result.get('rows', [])[0]
            print(f"    - 이름: {first_row.get('employee_name', 'N/A')}")
            print(f"    - 사번: {first_row.get('employee_id', 'N/A')}")
            print(f"    - 제목: {first_row.get('title', 'N/A')}")
        print()


if __name__ == "__main__":
    test_name_queries()

