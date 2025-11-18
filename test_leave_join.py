"""leave_balance_v2와 employee JOIN 테스트."""

from __future__ import annotations

import json
from agents.leave_agent import check_balance


def test_leave_join() -> None:
    """leave_balance_v2 조회 시 employee_name이 포함되는지 확인."""
    print("=" * 70)
    print("Leave Balance JOIN 테스트")
    print("=" * 70)
    
    # 사번 4001의 잔여 연차 조회
    print("\n[테스트] 사번 4001 잔여 연차 조회...")
    try:
        result = check_balance(4001)
        print(f"성공 여부: {result.get('ok', False)}")
        
        balance = result.get('balance', {})
        if balance:
            print("\n잔여 연차 정보:")
            print(json.dumps(balance, ensure_ascii=False, indent=2, default=str))
            
            if "employee_name" in balance:
                print(f"\n[SUCCESS] employee_name 포함됨: {balance['employee_name']}")
            else:
                print("\n[WARNING] employee_name이 결과에 없습니다.")
        else:
            print("\n[WARNING] balance 정보가 없습니다.")
    except Exception as e:
        print(f"\n[ERROR] 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_leave_join()

