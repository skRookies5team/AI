"""이벤트 루프 오류 재현 테스트."""

from __future__ import annotations

from agents.supervisor import run_supervisor


def test_event_loop_error() -> None:
    """이벤트 루프 오류 재현."""
    print("=" * 70)
    print("이벤트 루프 오류 재현 테스트")
    print("=" * 70)
    
    test_queries = [
        "강하은 일정 알려줘",
        "윤서준 일정 알려줘",
        "한지민 일정 알려줘",
    ]
    
    for query in test_queries:
        print(f"\n질의: '{query}'")
        print("-" * 70)
        try:
            result = run_supervisor(query)
            print(f"성공: {result.get('ok')}")
            print(f"메시지: {result.get('message', '')[:100]}")
            if result.get('error'):
                print(f"오류: {result.get('error')}")
        except Exception as e:
            print(f"[ERROR] 예외 발생: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    test_event_loop_error()

