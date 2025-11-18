"""최종 이름 조회 테스트 - 모든 단계 통합."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_langgraph import run_schedule_agent


def test_final_name_query() -> None:
    """최종 이름 조회 테스트."""
    print("=" * 70)
    print("최종 이름 조회 테스트")
    print("=" * 70)
    
    # 실제 DB에 존재하는 이름으로 테스트
    test_queries = [
        "김철수 일정 알려줘",
        "이영희 일정 알려줘",
        "박민수 일정 알려줘",
    ]
    
    for query in test_queries:
        print(f"\n[테스트] '{query}'")
        print("-" * 70)
        try:
            result = run_schedule_agent(query)
            print(f"  성공: {result.get('ok')}")
            print(f"  메시지: {result.get('message', '')[:200]}")
            if result.get('rows'):
                print(f"  조회된 일정: {len(result['rows'])}개")
                if result['rows'][0].get('employee_name'):
                    print(f"  [OK] employee_name: {result['rows'][0]['employee_name']}")
            elif result.get('error'):
                print(f"  오류: {result['error']}")
        except Exception as e:
            print(f"  [ERROR] 예외 발생: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    test_final_name_query()

