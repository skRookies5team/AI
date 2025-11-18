from __future__ import annotations

import sys
import os

# 가상환경 체크 및 안내
def check_environment():
    """가상환경이 활성화되어 있는지 확인하고, 필요한 모듈이 설치되어 있는지 체크."""
    venv_path = os.path.join(os.path.dirname(__file__), ".venv")
    is_venv = (
        hasattr(sys, "real_prefix") 
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
        or ".venv" in sys.executable
    )
    
    # asyncmy 모듈 체크
    try:
        import asyncmy
    except ImportError:
        if not is_venv:
            print("=" * 70)
            print("⚠️  가상환경이 활성화되지 않았습니다!")
            print("=" * 70)
            print("\n다음 중 하나의 방법으로 실행하세요:")
            print("\n[방법 1] 가상환경 활성화 후 실행:")
            print("  .venv\\Scripts\\activate")
            print("  python main_agents.py")
            print("\n[방법 2] 배치 파일 사용:")
            print("  run_chatbot.bat")
            print("\n[방법 3] uv 사용:")
            print("  uv run python main_agents.py")
            print("\n" + "=" * 70)
            sys.exit(1)
        else:
            print("=" * 70)
            print("⚠️  필요한 패키지가 설치되지 않았습니다!")
            print("=" * 70)
            print("\n다음 명령어로 패키지를 설치하세요:")
            print("  uv sync")
            print("또는")
            print("  pip install -r requirements.txt")
            print("\n" + "=" * 70)
            sys.exit(1)

# 환경 체크 실행
check_environment()

from agents.supervisor import run_supervisor
import uuid


def _fmt_num(n):
    try:
        f = float(n)
        if f.is_integer():
            return str(int(f))
        return str(f)
    except Exception:
        return str(n)


def format_result(result) -> str:
    if isinstance(result, dict):
        if result.get("ok") is False and result.get("error"):
            return f"실패: {result['error']}"
        
        # 일정 조회 결과 처리 (rows가 있는 경우)
        if isinstance(result.get("rows"), list) and result.get("rows"):
            lines = []
            if result.get("message"):
                lines.append(result["message"])
            else:
                lines.append(f"조회된 일정 {len(result['rows'])}개:")
            
            for i, row in enumerate(result["rows"][:10], 1):  # 최대 10개만 표시
                title = row.get("title") or row.get("subject") or "제목 없음"
                start = row.get("start_time") or row.get("start_at") or row.get("start_date", "")
                end = row.get("end_time") or row.get("end_at") or row.get("end_date", "")
                employee_name = row.get("employee_name", "")
                employee_id = row.get("employee_id", "")
                location = row.get("location", "")
                schedule_type = row.get("schedule_type") or row.get("type", "")
                
                info_parts = [f"{i}. {title}"]
                if schedule_type:
                    info_parts.append(f"[{schedule_type}]")
                if start:
                    info_parts.append(f"시작: {start}")
                if end:
                    info_parts.append(f"종료: {end}")
                if employee_name:
                    info_parts.append(f"담당자: {employee_name}")
                if employee_id:
                    info_parts.append(f"(사번: {employee_id})")
                if location:
                    info_parts.append(f"장소: {location}")
                
                lines.append(" - ".join(info_parts))
            
            if len(result["rows"]) > 10:
                lines.append(f"\n... 외 {len(result['rows']) - 10}개 더")
            
            return "\n".join(lines)
        
        if "message" in result and isinstance(result["message"], str):
            # items가 있으면 메시지와 함께 나열
            lines = [result["message"]]
            if isinstance(result.get("items"), list) and result["items"]:
                for it in result["items"]:
                    # 특허 검색 결과 형식 (index, application_number, title, abstract)
                    if "index" in it and "application_number" in it:
                        idx = it.get("index", "")
                        app_no = it.get("application_number", "")
                        title = it.get("title", "제목 없음")
                        abstract = it.get("abstract", "")
                        lines.append(f"\n{idx}. [{app_no}] {title}")
                        if abstract:
                            lines.append(f"   초록: {abstract}")
                    else:
                        # 기존 형식 (title, link 등)
                        title = it.get("title") or it.get("name") or "제목 없음"
                        link = it.get("link") or it.get("url") or ""
                        idx = it.get("index", "")
                        if link:
                            lines.append(f"{idx}. {title} - {link}" if idx else f"- {title} - {link}")
                        else:
                            lines.append(f"{idx}. {title}" if idx else f"- {title}")
            if isinstance(result.get("link"), str):
                lines.append(f"참고 링크: {result['link']}")
            # benefit_rag 결과 처리 (contexts가 있는 경우)
            # GPT 답변에 이미 "[참고 문서]"가 포함되어 있지 않은 경우에만 추가
            message_text = result["message"]
            if isinstance(result.get("contexts"), list) and result["contexts"]:
                # GPT 답변에 "[참고 문서]"가 이미 포함되어 있는지 확인
                if "[참고 문서]" not in message_text and "[참고" not in message_text:
                    lines.append("\n[참고 문서]")
                    for i, ctx in enumerate(result["contexts"][:3], 1):  # 상위 3개만 표시
                        preview = ctx[:100] + "..." if len(ctx) > 100 else ctx
                        lines.append(f"  {i}. {preview}")
            return "\n".join(lines)
        if "balance" in result and isinstance(result["balance"], dict):
            b = result["balance"]
            emp = b.get("employee_id", "?")
            total = _fmt_num(b.get("total_entitlement_days", 0))
            used = _fmt_num(b.get("used_days", 0))
            pending = _fmt_num(b.get("pending_days", 0))
            remain = _fmt_num(b.get("remaining_days", 0))
            return f"사번 {emp}의 잔여 연차는 {remain}일입니다. (사용 {used}일, 대기 {pending}일, 총 {total}일)"
        if "request" in result and isinstance(result["request"], dict):
            r = result["request"]
            b = result.get("balance", {})
            remain = _fmt_num(b.get("remaining_days", 0))
            pending = _fmt_num(b.get("pending_days", 0))
            days = _fmt_num(r.get("total_days", 0))
            lt = r.get("leave_type", "연차")
            s = r.get("status", "대기")
            start = r.get("start_date", "?")
            end = r.get("end_date", "?")
            emp = r.get("employee_id", "?")
            return (
                f"사번 {emp} {lt} {days}일 신청을 접수했습니다. "
                f"기간 {start} ~ {end}, 상태 {s}. "
                f"현재 대기 {pending}일, 잔여 {remain}일입니다."
            )
    return str(result)


def chat_loop() -> None:
	"""대화 루프: 메모리 기능을 통해 멀티턴 대화 지원."""
	print("=" * 70)
	print("🤖 AI 에이전트 매니저먼트 챗봇")
	print("=" * 70)
	print("LangGraph 기반 멀티 에이전트 시스템")
	print("-" * 70)
	print("질의를 입력하세요. (종료: 'quit', 'exit', 'q')")
	print("\n[지원 기능]")
	print("  📅 일정/연차:")
	print("    - 오늘 일정 알려줘")
	print("    - 내일 일정 알려줘")
	print("    - 이번 주 일정 알려줘")
	print("    - 다음 주 일정 알려줘")
	print("    - 이번 달 일정 알려줘")
	print("    - 강하은 일정 알려줘 (이름으로 검색)")
	print("    - 사번 4001 일정 알려줘")
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
	
	# 세션 ID 생성 (대화 세션별 메모리 관리)
	session_id = str(uuid.uuid4())
	print(f"\n[세션 ID: {session_id[:8]}...]")
	print("-" * 70)
	
	while True:
		try:
			user = input("\n[질의] ").strip()
			if not user:
				continue
			
			if user.lower() in {"exit", "quit", "q", "종료"}:
				print("\n[종료] 챗봇을 종료합니다.")
				break
			
			print("\n[처리 중...]")
			try:
				result = run_supervisor(user, session_id=session_id)
				formatted = format_result(result)
				print(f"\n[응답]")
				print(formatted)
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
    chat_loop()
