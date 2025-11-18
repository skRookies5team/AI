"""schedule_master 직접 테스트."""

from __future__ import annotations

import json
from agents.schedule_master import schedule_master, parse_schedule_intent


def test_schedule_master_direct() -> None:
    """schedule_master 직접 테스트."""
    print("=" * 70)
    print("schedule_master 직접 테스트")
    print("=" * 70)
    
    # 실제 이름으로 테스트
    test_queries = [
        "김철수 일정 알려줘",
        "이영희 일정 알려줘",
        "박민수 일정 알려줘",
    ]
    
    for query in test_queries:
        print(f"\n[테스트] '{query}'")
        print("-" * 70)
        
        # 1. 의도 파싱 확인
        intent = parse_schedule_intent(query)
        print(f"  의도 종류: {intent.kind}")
        print(f"  details: {intent.details}")
        
        # 2. schedule_master 호출
        try:
            result = schedule_master(query)
            print(f"  성공: {result.get('ok')}")
            print(f"  메시지: {result.get('message', '')[:150]}")
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
    test_schedule_master_direct()

