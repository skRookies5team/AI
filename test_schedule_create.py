"""일정 등록 테스트 스크립트."""

from __future__ import annotations

import os
import sys

# 가상환경 체크
try:
    import httpx
except ImportError:
    print("=" * 70)
    print("⚠️  가상환경이 활성화되지 않았습니다!")
    print("=" * 70)
    print("\n다음 중 하나의 방법으로 실행하세요:")
    print("\n[방법 1] 가상환경 활성화 후 실행:")
    print("  .venv\\Scripts\\activate")
    print("  python test_schedule_create.py")
    print("\n[방법 2] uv 사용:")
    print("  uv run python test_schedule_create.py")
    print("\n" + "=" * 70)
    sys.exit(1)

from agents.schedule_master import schedule_master

# 디버깅 모드 활성화
os.environ["DEBUG_SCHEDULE"] = "true"

if __name__ == "__main__":
    test_query = "일정 추가해줘 사번 4001 제목: 출장 시작: 2025-12-05 종료: 2025-12-07"
    print("=" * 70)
    print("일정 등록 테스트")
    print("=" * 70)
    print(f"\n[테스트 질의] {test_query}\n")
    
    result = schedule_master(test_query)
    
    print("\n[결과]")
    print(f"  ok: {result.get('ok')}")
    print(f"  message: {result.get('message')}")
    if result.get('error'):
        print(f"  error: {result.get('error')}")
    if result.get('schedule'):
        print(f"  schedule: {result.get('schedule')}")

