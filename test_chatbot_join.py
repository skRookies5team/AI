"""챗봇에서 JOIN된 employee_name이 제대로 표시되는지 테스트."""

from __future__ import annotations

import json
from agents.schedule_master import schedule_master


def test_chatbot_with_join() -> None:
    """JOIN된 이름이 챗봇 응답에 포함되는지 확인."""
    print("=" * 70)
    print("챗봇 JOIN 테스트")
    print("=" * 70)
    
    queries = [
        "사번 4001 일정 알려줘",
        "사번 4002 일정 있어?",
    ]
    
    for query in queries:
        print(f"\n질의: {query}")
        print("-" * 70)
        try:
            result = schedule_master(query)
            print(f"성공 여부: {result.get('ok', False)}")
            print(f"응답 메시지: {result.get('message', '')[:200]}...")  # 처음 200자만
            rows = result.get('rows', [])
            if rows:
                first_row = rows[0]
                if 'employee_name' in first_row:
                    print(f"[SUCCESS] employee_name 포함: {first_row['employee_name']}")
                else:
                    print("[WARNING] employee_name이 결과에 없습니다.")
            print("-" * 70)
        except Exception as e:
            print(f"[ERROR] 오류: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    test_chatbot_with_join()

