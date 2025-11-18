"""잔여 연차 조회 상세 테스트."""

from __future__ import annotations

from agents.leave_data import get_balance, _run
from agents.schedule_master import schedule_master


def test_leave_balance():
    """잔여 연차 조회 상세 테스트."""
    print("=" * 70)
    print("잔여 연차 조회 상세 테스트")
    print("=" * 70)
    
    # 직접 get_balance 호출 테스트
    print("\n[1] 직접 get_balance 호출 테스트")
    test_employee_ids = [4001, 4002, 4000]
    for emp_id in test_employee_ids:
        try:
            balance = _run(get_balance(emp_id))
            print(f"\n  사번 {emp_id}:")
            if balance:
                print(f"    총 연차: {balance.get('total_entitlement_days', 'N/A')}")
                print(f"    사용: {balance.get('used_days', 'N/A')}")
                print(f"    대기: {balance.get('pending_days', 'N/A')}")
                print(f"    잔여: {balance.get('remaining_days', 'N/A')}")
                print(f"    이름: {balance.get('employee_name', 'N/A')}")
            else:
                print(f"    조회 결과 없음")
        except Exception as e:
            print(f"    오류: {e}")
    
    # schedule_master를 통한 테스트
    print("\n[2] schedule_master를 통한 테스트")
    test_queries = [
        "사번 4001 연차 잔여 조회",
        "강하은 연차 잔여 조회",
    ]
    
    for query in test_queries:
        print(f"\n  질의: '{query}'")
        try:
            result = schedule_master(query)
            print(f"    ok: {result.get('ok')}")
            message = result.get('message', '')
            if message:
                print(f"    message: {message[:150]}")
            else:
                print(f"    message: (비어있음)")
            error = result.get('error')
            if error:
                print(f"    error: {error}")
        except Exception as e:
            print(f"    오류: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    test_leave_balance()

