"""대화형 에이전트 매니저먼트 챗봇 인터페이스."""

from __future__ import annotations

import json
import uuid
from agents.supervisor import run_supervisor


def format_response(result: dict) -> str:
    """응답 결과를 읽기 쉬운 형식으로 포맷팅."""
    lines = []
    
    if result.get("ok"):
        lines.append("✅ 성공")
    else:
        lines.append("❌ 실패")
    
    message = result.get("message", "")
    if message:
        lines.append(f"\n💬 응답: {message}")
    
    error = result.get("error")
    if error:
        lines.append(f"\n⚠️ 오류: {error}")
    
    rows = result.get("rows", [])
    if rows:
        lines.append(f"\n📋 조회된 데이터 ({len(rows)}개):")
        for i, row in enumerate(rows[:5], 1):  # 최대 5개만 표시
            lines.append(f"  {i}. {json.dumps(row, ensure_ascii=False, default=str)}")
        if len(rows) > 5:
            lines.append(f"  ... 외 {len(rows) - 5}개 더")
    
    return "\n".join(lines)


def main() -> None:
    """대화형 에이전트 매니저먼트 챗봇 메인 루프."""
    print("=" * 70)
    print("🤖 AI 에이전트 매니저먼트 챗봇")
    print("=" * 70)
    print("LangGraph 기반 멀티 에이전트 시스템")
    print("-" * 70)
    print("질의를 입력하세요. (종료: 'quit', 'exit', 'q')")
    print("\n[지원 기능]")
    print("  📅 일정/연차:")
    print("    - 사번 4001 일정 알려줘")
    print("    - 홍길동 일정 알려줘 (이름으로 검색)")
    print("    - 홍길동님 일정 (이름 + 님)")
    print("    - 홍길동의 일정 (이름 + 의)")
    print("    - 사번 4001 연차 잔여 조회")
    print("    - 오늘 회의실 일정 알려줘")
    print("  📄 문서/이메일:")
    print("    - 이메일 자동응답 생성")
    print("    - 보고서 작성")
    print("  ❓ Q&A:")
    print("    - 복리후생 정책 알려줘")
    print("    - 식단 메뉴 알려줘")
    print("  🔬 기술:")
    print("    - 특허 검색")
    print("    - 논문 찾아줘")
    print("-" * 70)
    
    # 세션 ID 생성 (멀티턴 대화 지원)
    session_id = str(uuid.uuid4())
    print(f"\n[세션 ID: {session_id[:8]}...]")
    print("-" * 70)
    
    while True:
        try:
            query = input("\n[질의] ").strip()
            
            if not query:
                continue
            
            if query.lower() in ["quit", "exit", "q", "종료"]:
                print("\n[종료] 챗봇을 종료합니다.")
                break
            
            print("\n[처리 중...]")
            try:
                result = run_supervisor(query, session_id=session_id)
                print("\n" + format_response(result))
            except Exception as inner_e:
                print(f"\n[오류] 처리 중 오류: {inner_e}")
                import traceback
                traceback.print_exc()
            print("-" * 70)
            
        except KeyboardInterrupt:
            print("\n\n[종료] 챗봇을 종료합니다.")
            break
        except Exception as e:
            print(f"\n[오류] 오류 발생: {e}")
            import traceback
            traceback.print_exc()
            print("-" * 70)


if __name__ == "__main__":
    main()

