from __future__ import annotations

from agents.supervisor import run_supervisor
from agents.tech_patent_agent import set_progress_callback
import argparse
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from agents.supervisor import run_supervisor


def _fmt_num(n):
    try:
        f = float(n)
        if f.is_integer():
            return str(int(f))
        return str(f)
    except Exception:
        return str(n)


def format_result(result) -> str:
    """에이전트 결과를 채팅에 표시하기 좋은 형식으로 포맷팅."""
    if isinstance(result, dict):
        if result.get("ok") is False and result.get("error"):
            error_msg = result['error']
            # 에러 메시지를 더 친절하게
            if "컬렉션" in error_msg or "Collection" in error_msg:
                return f"❌ 데이터베이스 연결에 문제가 있습니다.\n\n{error_msg}\n\n💡 인프라 팀에 문의해주세요."
            if "IMAP" in error_msg or "메일" in error_msg:
                return f"❌ 이메일 검색 중 문제가 발생했습니다.\n\n{error_msg}\n\n💡 이메일 설정을 확인해주세요."
            return f"❌ {error_msg}"

        if "message" in result and isinstance(result["message"], str):
            # items가 있으면 메시지와 함께 나열 (검색 결과)
            message_text = result["message"]
            lines = [message_text]
            items = result.get("items", [])

            if isinstance(items, list) and items:
                # 특허 검색 결과 형식
                if items and "index" in items[0] and "application_number" in items[0]:
                    lines.append("")  # 빈 줄 추가
                    for it in items:
                        idx = it.get("index", "")
                        app_no = it.get("application_number", "")
                        title = it.get("title", "제목 없음")
                        abstract = it.get("abstract", "")
                        lines.append(f"{idx}. [{app_no}] {title}")
                        if abstract:
                            # 초록이 너무 길면 자르기
                            abstract_preview = abstract[:150] + "..." if len(abstract) > 150 else abstract
                            lines.append(f"   📄 {abstract_preview}")

                    # 특허 상세 보기 안내 (더 자연스럽게)
                    lines.append("")
                    lines.append("💡 자세히 보려면 번호를 입력하세요. 예: '1번' 또는 '1번 상세'")

                # 이메일 검색 결과 형식
                elif items and ("subject" in items[0] or "title" in items[0]):
                    lines.append("")  # 빈 줄 추가
                    for it in items:
                        idx = it.get("index", "")
                        title = it.get("title") or it.get("subject") or "제목 없음"
                        from_addr = it.get("from_addr") or it.get("from") or ""
                        date = it.get("date") or ""

                        lines.append(f"{idx}. {title}")
                        if from_addr:
                            lines.append(f"   📧 {from_addr}")
                        if date:
                            lines.append(f"   📅 {date}")

                    # 이메일 상세 보기 안내 (더 자연스럽게)
                    lines.append("")
                    lines.append("💡 자세히 보려면 번호나 제목을 말씀해주세요. 예: '1번' 또는 '1번에 대해 알려줘'")

                # 기타 형식 (title, link 등)
                else:
                    for it in items:
                        title = it.get("title") or it.get("name") or "제목 없음"
                        link = it.get("link") or it.get("url") or ""
                        idx = it.get("index", "")
                        if link:
                            lines.append(f"{idx}. {title} - {link}" if idx else f"- {title} - {link}")
                        else:
                            lines.append(f"{idx}. {title}" if idx else f"- {title}")

            # 참고 링크 추가
            if isinstance(result.get("link"), str) and result["link"]:
                lines.append("")
                lines.append(f"🔗 참고 링크: {result['link']}")

            # benefit_rag 결과 처리 (contexts가 있는 경우)
            # GPT 답변에 이미 "[참고 문서]"가 포함되어 있지 않은 경우에만 추가
            message_text = result["message"]
            if isinstance(result.get("contexts"), list) and result["contexts"]:
                # GPT 답변에 "[참고 문서]"가 이미 포함되어 있는지 확인
                if "[참고 문서]" not in message_text and "[참고" not in message_text:
                    lines.append("")
                    lines.append("📚 참고 문서:")
                    for i, ctx in enumerate(result["contexts"][:3], 1):
                        preview = ctx[:150] + "..." if len(ctx) > 150 else ctx
                        lines.append(f"  {i}. {preview}")

            return "\n".join(lines)
        if "balance" in result and isinstance(result["balance"], dict):
            b = result["balance"]
            emp = b.get("employee_id", "?")
            total = _fmt_num(b.get("total_entitlement_days", 0))
            used = _fmt_num(b.get("used_days", 0))
            pending = _fmt_num(b.get("pending_days", 0))
            remain = _fmt_num(b.get("remaining_days", 0))
            return (
                f"📅 사번 {emp}의 연차 현황\n\n"
                f"• 잔여 연차: {remain}일\n"
                f"• 사용한 연차: {used}일\n"
                f"• 대기 중인 연차: {pending}일\n"
                f"• 총 연차: {total}일"
            )
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
            status_emoji = "✅" if s == "승인" else "⏳" if s == "대기" else "❌"
            return (
                f"{status_emoji} 연차 신청이 접수되었습니다.\n\n"
                f"• 사번: {emp}\n"
                f"• 종류: {lt}\n"
                f"• 일수: {days}일\n"
                f"• 기간: {start} ~ {end}\n"
                f"• 상태: {s}\n\n"
                f"📊 현재 잔여 연차: {remain}일 (대기 중: {pending}일)"
            )
    return str(result)


def chat_loop() -> None:
	"""대화 루프: 메모리 기능을 통해 멀티턴 대화 지원."""
	print("Super Supervisor 대화 시작. 종료하려면 'exit' 입력.")

	# 진행 상황 콜백 설정 (실시간 진행 상황 표시)
	def progress_handler(message: str) -> None:
		"""진행 상황 메시지를 실시간으로 표시."""
		print(f"⏳ {message}", flush=True)

	set_progress_callback(progress_handler)

	# 세션 ID 생성 (대화 세션별 메모리 관리)
	session_id = str(uuid.uuid4())
	
	while True:
		user = input("\n사용자> ").strip()
		if user.lower() in {"exit", "quit"}:
			print("종료합니다.")
			break
		result = run_supervisor(user, session_id=session_id)
		print("에이전트>", format_result(result))


logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., description="사용자 질의")
    reservation: Dict[str, Any] | None = Field(
        default=None,
        description="예약 정보 (예약/취소/수정 시 사용)",
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="추가 컨텍스트",
    )


class ChatResponse(BaseModel):
    intent: Optional[str] = Field(default=None, description="추론된 의도")
    response: str = Field(description="응답 메시지")
    state: Dict[str, Any] | None = Field(default=None, description="상태 정보")


app = FastAPI(
    title="통합 AI 에이전트 API",
    description="회의실 + 연차 + QA + 문서 + 특허/논문 통합 에이전트",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> Dict[str, Any]:
    return {
        "message": "통합 AI 에이전트 서버",
        "status": "running",
        "version": "1.0.0",
        "agents": ["schedule", "document", "qa", "tech"],
    }


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    logger.info(
        "📥 요청 수신: message=%s, reservation=%s",
        request.message,
        request.reservation is not None,
    )

    session_id = str(uuid.uuid4())

    full_context = request.context.copy()
    if request.reservation:
        full_context["reservation"] = request.reservation
        logger.info("📋 예약 정보 추가: %s", request.reservation)

    result = run_supervisor(
        request.message,
        session_id=session_id,
        context=full_context,
    )

    if isinstance(result, dict):
        intent = result.get("intent")
        message = result.get("message", str(result))

        logger.info("✅ 응답 생성: intent=%s", intent)

        return ChatResponse(
            intent=intent,
            response=message,
            state=result,
        )

    return ChatResponse(
        intent=None,
        response=str(result),
        state={"raw": result},
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="통합 AI 에이전트 실행기")
    parser.add_argument(
        "--mode",
        choices=["cli", "api"],
        default="cli",
        help="cli: 터미널 대화 모드 / api: FastAPI 서버 모드",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="API 서버 호스트 (mode=api일 때만 사용)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="API 서버 포트 (mode=api일 때만 사용)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="uvicorn reload 옵션 활성화 (mode=api일 때만)",
    )
    args = parser.parse_args()

    if args.mode == "cli":
        chat_loop()
        return

    import uvicorn

    uvicorn.run(
        "main_agents:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
