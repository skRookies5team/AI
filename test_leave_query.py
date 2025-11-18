"""잔여 연차 조회 테스트."""

from __future__ import annotations

import os
os.environ["DEBUG_SCHEDULE"] = "true"

from agents.schedule_master import schedule_master


def test_leave_queries():
    """잔여 연차 조회 테스트."""
    print("=" * 70)
    print("잔여 연차 조회 테스트")
    print("=" * 70)
    
    test_queries = [
        "사번 4001 연차 잔여 조회",
        "강하은 연차 잔여 조회",
        "윤서준 연차 잔여 조회",
    ]
    
    for query in test_queries:
        print(f"\n{'='*70}")
        print(f"질의: '{query}'")
        print(f"{'='*70}")
        try:
            result = schedule_master(query)
            print(f"\n결과:")
            print(f"  ok: {result.get('ok')}")
            print(f"  message: {result.get('message', '')[:200]}")
            print(f"  error: {result.get('error', 'None')}")
        except Exception as e:
            print(f"\n[오류] {e}")
            import traceback
            traceback.print_exc()
        print()


if __name__ == "__main__":
    test_leave_queries()

