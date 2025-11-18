"""Supervisor 에이전트 테스트."""

from __future__ import annotations

import json
from agents.supervisor import run_supervisor


def test_supervisor() -> None:
    """Supervisor 에이전트 테스트."""
    print("=" * 70)
    print("Supervisor 에이전트 테스트")
    print("=" * 70)
    
    test_queries = [
        "사번 4001 일정 알려줘",
        "사번 4001 연차 잔여 조회",
    ]
    
    for query in test_queries:
        print(f"\n[테스트] 질의: {query}")
        print("-" * 70)
        try:
            result = run_supervisor(query)
            print(f"[결과] 성공: {result.get('ok', False)}")
            print(f"[메시지] {result.get('message', '')[:200]}")
            if result.get('rows'):
                print(f"[데이터] {len(result['rows'])}개 행 조회됨")
            if result.get('error'):
                print(f"[오류] {result['error']}")
        except Exception as e:
            print(f"[오류] 실행 실패: {e}")
            import traceback
            traceback.print_exc()
        print("-" * 70)


if __name__ == "__main__":
    test_supervisor()

