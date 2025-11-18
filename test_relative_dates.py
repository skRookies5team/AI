"""상대 날짜 표현 테스트."""

from __future__ import annotations

import os
os.environ["DEBUG_SCHEDULE"] = "true"

from agents.schedule_langgraph import run_schedule_agent


def test_relative_dates():
    """상대 날짜 표현 테스트."""
    print("=" * 70)
    print("상대 날짜 표현 테스트")
    print("=" * 70)
    
    test_queries = [
        "오늘 일정 알려줘",
        "내일 일정 알려줘",
        "이번 주 일정 알려줘",
        "다음 주 일정 알려줘",
        "이번 달 일정 알려줘",
        "강하은 오늘 일정 알려줘",
        "사번 4001 이번 주 일정 알려줘",
    ]
    
    for query in test_queries:
        print(f"\n{'='*70}")
        print(f"질의: '{query}'")
        print(f"{'='*70}")
        try:
            result = run_schedule_agent(query)
            print(f"\n결과:")
            print(f"  ok: {result.get('ok')}")
            message = result.get('message', '')
            if message:
                print(f"  message: {message[:200]}")
            print(f"  rows: {len(result.get('rows', []))}개")
        except Exception as e:
            print(f"\n[오류] {e}")
            import traceback
            traceback.print_exc()
        print()


if __name__ == "__main__":
    test_relative_dates()

