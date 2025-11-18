"""챗봇 기능을 직접 테스트하는 스크립트."""

from __future__ import annotations

import json
from agents.schedule_master import schedule_master


def test_schedule_chat() -> None:
    """일정 조회 테스트."""
    print("=" * 60)
    print("일정 조회 테스트")
    print("=" * 60)
    
    queries = [
        "사번 4001 일정 알려줘",
        "오늘 회의실 일정 있어?",
        "사번 4002 일정 알려줘",
    ]
    
    for query in queries:
        print(f"\n질의: {query}")
        result = schedule_master(query)
        print(f"결과: {json.dumps(result, ensure_ascii=False, indent=2, default=str)}")
        print("-" * 60)


def test_leave_chat() -> None:
    """연차 잔여 조회 테스트."""
    print("\n" + "=" * 60)
    print("연차 잔여 조회 테스트")
    print("=" * 60)
    
    queries = [
        "사번 4001 연차 잔여 조회",
        "사번 4001 연차 얼마나 남았어?",
    ]
    
    for query in queries:
        print(f"\n질의: {query}")
        result = schedule_master(query)
        print(f"결과: {json.dumps(result, ensure_ascii=False, indent=2, default=str)}")
        print("-" * 60)


if __name__ == "__main__":
    try:
        test_schedule_chat()
        test_leave_chat()
        print("\n✅ 모든 테스트 완료!")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

