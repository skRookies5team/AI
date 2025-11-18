"""schedule_master 테스트."""

from __future__ import annotations

from agents.schedule_master import schedule_master


def test_schedule_master() -> None:
    """schedule_master 테스트."""
    print("=" * 70)
    print("schedule_master 테스트")
    print("=" * 70)
    
    test_queries = [
        "강하은 일정 알려줘",
        "윤서준 일정 알려줘",
        "한지민 일정 알려줘",
        "사번 4001 일정 알려줘",
    ]
    
    for query in test_queries:
        print(f"\n질의: '{query}'")
        print("-" * 70)
        try:
            result = schedule_master(query)
            print(f"성공: {result.get('ok')}")
            print(f"메시지: {result.get('message', '')[:150]}")
            if result.get('rows'):
                print(f"조회된 일정: {len(result['rows'])}개")
            if result.get('error'):
                print(f"오류: {result.get('error')}")
        except Exception as e:
            print(f"[ERROR] 예외 발생: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    test_schedule_master()

