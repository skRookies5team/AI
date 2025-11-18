from langgraph.graph import StateGraph, START, END
import imaplib, email, os
from email.header import decode_header
from typing import TypedDict, List, Any, Tuple

# ====================================
# 🧩 안전한 인코딩 처리 유틸 함수
# ====================================
FALLBACK_ENCODINGS = ["utf-8", "cp949", "euc-kr", "iso-2022-kr", "latin-1"]

def safe_decode_bytes(b: bytes | None, enc: str | None) -> str:
    """비표준 인코딩 포함 모든 상황에서 안전하게 디코딩"""
    if b is None:
        return ""
    if not isinstance(b, (bytes, bytearray)):
        return str(b)
    if enc and "unknown" not in enc.lower():
        try:
            return b.decode(enc, errors="replace")
        except Exception:
            pass
    for e in FALLBACK_ENCODINGS:
        try:
            return b.decode(e, errors="replace")
        except Exception:
            continue
    return b.decode("latin-1", errors="replace")

def decode_mime_header(header_value: str | None) -> str:
    """헤더 (Subject, From 등) 디코딩"""
    if not header_value:
        return "(없음)"
    try:
        parts = decode_header(header_value)
    except Exception:
        return str(header_value)
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += safe_decode_bytes(part, enc)
        else:
            result += str(part)
    return result.strip()

def extract_text_from_msg(msg: email.message.EmailMessage) -> str:
    """메일 본문 안전 추출 (text/plain 기준)"""
    body = []
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "")
                if ctype == "text/plain" and "attachment" not in disp.lower():
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset()
                        body.append(safe_decode_bytes(payload, charset))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset()
                body.append(safe_decode_bytes(payload, charset))
            elif isinstance(msg.get_payload(), str):
                body.append(msg.get_payload())
    except Exception:
        return ""
    return "\n".join(body)


# ====================================
# 📦 상태 정의
# ====================================
class MailState(TypedDict):
    connection: Any
    mail_ids: List[bytes]
    found_count: int
    batch_num: int
    current_range: Tuple[int, int]
    keyword: str
    user_response: str | None
    matched_ids: List[bytes]
    matched_titles: List[str]
    imap_host: str
    imap_port: int
    imap_user: str
    imap_pass: str


# ====================================
# 🧠 노드 정의
# ====================================
def connect_imap(state: MailState):
    """IMAP 서버 연결"""
    print("📬 네이버 IMAP 서버 연결 중...")
    host = "imap.naver.com"
    port = 993
    user = os.getenv("NAVER_EMAIL") or "shin2923@naver.com"
    pwd = os.getenv("NAVER_APP_PASSWORD") or "XKMZKRTB5W5U"  # ⚠️ 실제 비밀번호는 환경변수로 처리 권장
    mail = imaplib.IMAP4_SSL(host, port)
    mail.login(user, pwd)
    mail.select("INBOX")
    print("✅ 로그인 완료")

    status, data = mail.search(None, "ALL")
    if status != "OK" or not data or not data[0]:
        print("❌ 메일 목록을 가져올 수 없습니다.")
        return {**state, "connection": mail, "mail_ids": [], "current_range": (0, 0)}

    mail_ids = data[0].split()
    total = len(mail_ids)
    print(f"📨 총 {total}개의 메일 중 검색 준비 완료")

    # 최근 1000건만 검색: (max(1, total-999) ~ total)
    # IMAP 시퀀스: 1=가장 오래된, total=가장 최신
    end = total
    start = max(1, total - 1000 + 1)
    return {
        **state,
        "connection": mail,
        "mail_ids": mail_ids,
        "found_count": 0,
        "batch_num": 1,
        "current_range": (start, end),
        "user_response": None,
        "matched_ids": [],
        "matched_titles": [],
        "imap_host": host,
        "imap_port": port,
        "imap_user": user,
        "imap_pass": pwd,
    }


def process_batch(state: MailState):
    """최근 1000개 범위 내 메일 검색(단일 배치)"""
    mail = state["connection"]
    mail_ids = state["mail_ids"]
    start, end = state["current_range"]
    keyword = state["keyword"]
    batch_num = state["batch_num"]
    found_count = 0
    matched_ids: List[bytes] = []
    matched_titles: List[str] = []

    print(f"\n🔍 [배치 {batch_num}] {start} ~ {end}번 메일 검색 중...")

    if not mail_ids or start <= 0 or end <= 0 or start > end:
        print("📭 검색할 메일이 없습니다.")
        return state

    # IMAP 시퀀스 기준 슬라이싱: start~end (포함) → 파이썬 인덱스는 0-based
    for num in mail_ids[start - 1 : end]:
        try:
            status, msg_data = mail.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_mime_header(msg.get("Subject"))
            sender = decode_mime_header(msg.get("From"))
            date = msg.get("Date", "(날짜 없음)")
            body = extract_text_from_msg(msg)

            if keyword in subject or keyword in body:
                found_count += 1
                print(f"\n✅ [메일 발견 {found_count}]")
                print(f"제목: {subject}")
                print(f"보낸 사람: {sender}")
                print(f"날짜: {date}")
                print("-" * 60)
                matched_ids.append(num)
                matched_titles.append(subject)

        except Exception as e:
            num_str = num.decode(errors="ignore") if isinstance(num, bytes) else str(num)
            print(f"⚠️ 메일 {num_str} 처리 중 오류: {e}")

    if found_count == 0:
        print(f"\n❌ 이번 배치({batch_num})에서는 '{keyword}' 관련 메일을 찾지 못했습니다.")
    else:
        print(f"\n🎯 이번 배치({batch_num})에서 {found_count}개의 '{keyword}' 관련 메일을 찾았습니다.")

    # 단일 배치로 종료되도록 current_range 제거
    new_state = {**state, "found_count": found_count, "matched_ids": matched_ids, "matched_titles": matched_titles}
    new_state.pop("current_range", None)
    return new_state


def ask_user_feedback(state: MailState):
    """단일 배치 모드: 사용자가 상세 보기 대상을 고르면 전체 본문을 출력하고 종료"""
    mail = state["connection"]
    matched_ids = state.get("matched_ids", [])
    matched_titles = state.get("matched_titles", [])

    if not matched_ids:
        # 상세볼 대상 없음 → 종료
        return {**state, "user_response": "네"}

    print("\n🔎 상세 내용을 볼 메일을 선택하세요.")
    for i, title in enumerate(matched_titles, 1):
        print(f"{i}. {title}")
    try:
        sel = input("번호를 입력하거나(예: 1), 그냥 엔터로 종료: ").strip()
    except Exception:
        sel = ""
    if not sel:
        return {**state, "user_response": "네"}

    chosen_id: bytes | None = None
    if sel.isdigit():
        idx = int(sel)
        if 1 <= idx <= len(matched_ids):
            chosen_id = matched_ids[idx - 1]
    else:
        # 제목으로 선택(부분일치)
        for mid, title in zip(matched_ids, matched_titles):
            if sel in title:
                chosen_id = mid
                break

    if not chosen_id:
        print("⚠️ 유효한 선택이 아닙니다. 종료합니다.")
        return {**state, "user_response": "네"}

    # 전체 본문 출력
    def _ensure_connection(st: MailState) -> MailState:
        try:
            # 연결 유지 확인
            mail = st["connection"]
            mail.noop()
            return st
        except Exception:
            # 재연결
            try:
                host = st.get("imap_host", "imap.naver.com")
                port = int(st.get("imap_port", 993))
                user = st.get("imap_user", "")
                pwd = st.get("imap_pass", "")
                new_mail = imaplib.IMAP4_SSL(host, port)
                new_mail.login(user, pwd)
                new_mail.select("INBOX")
                return {**st, "connection": new_mail}
            except Exception as e:
                print(f"⚠️ IMAP 재연결 실패: {e}")
                return st

    try:
        # 연결 보장
        state = _ensure_connection(state)
        mail = state["connection"]
        status, msg_data = mail.fetch(chosen_id, "(RFC822)")
        if status == "OK" and msg_data and msg_data[0]:
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_mime_header(msg.get("Subject"))
            sender = decode_mime_header(msg.get("From"))
            date = msg.get("Date", "(날짜 없음)")
            body = extract_text_from_msg(msg)
            print("\n" + "=" * 80)
            print(f"제목: {subject}")
            print(f"보낸 사람: {sender}")
            print(f"날짜: {date}")
            print("-" * 80)
            print(body if body.strip() else "(본문 없음)")
            print("=" * 80 + "\n")
        else:
            # 한 번 재연결 후 재시도
            state = _ensure_connection(state)
            mail = state["connection"]
            status2, msg_data2 = mail.fetch(chosen_id, "(RFC822)")
            if status2 == "OK" and msg_data2 and msg_data2[0]:
                msg = email.message_from_bytes(msg_data2[0][1])
                subject = decode_mime_header(msg.get("Subject"))
                sender = decode_mime_header(msg.get("From"))
                date = msg.get("Date", "(날짜 없음)")
                body = extract_text_from_msg(msg)
                print("\n" + "=" * 80)
                print(f"제목: {subject}")
                print(f"보낸 사람: {sender}")
                print(f"날짜: {date}")
                print("-" * 80)
                print(body if body.strip() else "(본문 없음)")
                print("=" * 80 + "\n")
            else:
                print("⚠️ 선택한 메일을 가져올 수 없습니다.")
    except Exception as e:
        print(f"⚠️ 메일 본문을 가져오는 중 오류: {e}")

    # 종료
    return {**state, "user_response": "네"}


def check_next_batch(state: MailState):
    """다음 배치 진행 여부 결정"""
    if state["user_response"] == "네":
        print("\n💌 검색을 종료합니다. 원하는 메일을 찾으셨네요!")
        # 종료: 'current_range' 키를 제거하여 라우터가 END로 보내도록 함
        new_state = {**state}
        new_state.pop("current_range", None)
        return new_state

    if state["user_response"] == "아니요":
        try:
            more = input("🤖 더 검색해볼까요? (네/아니요): ").strip()
        except Exception:
            more = "아니요"

        if more == "네":
            # 다음 배치: (end + 1) ~ (end + 1000)
            total = len(state["mail_ids"])
            start, end = state["current_range"]
            next_start = end + 1
            next_end = min(total, end + 1000)

            if next_start > total:
                print("📭 더 이상 검색할 메일이 없습니다.")
                new_state = {**state}
                new_state.pop("current_range", None)
                return new_state

            print(f"🔁 다음({next_start}~{next_end}) 메일로 이동합니다.")
            return {
                **state,
                "batch_num": state["batch_num"] + 1,
                "current_range": (next_start, next_end),
                "found_count": 0,
            }
        else:
            print("🛑 검색을 종료합니다.")
            new_state = {**state}
            new_state.pop("current_range", None)
            return new_state

    # 기본: 종료 처리
    new_state = {**state}
    new_state.pop("current_range", None)
    return new_state


# ====================================
# 🔗 그래프 구성
# ====================================
graph = StateGraph(MailState)
graph.add_node("connect_imap", connect_imap)
graph.add_node("process_batch", process_batch)
graph.add_node("ask_user_feedback", ask_user_feedback)

graph.add_edge(START, "connect_imap")
graph.add_edge("connect_imap", "process_batch")
graph.add_edge("process_batch", "ask_user_feedback")
# 단일 배치 → 상세 보기 → 종료
graph.add_edge("ask_user_feedback", END)

app = graph.compile()


# ====================================
# 🚀 실행
# ====================================
if __name__ == "__main__":
    keyword = input("🔎 검색할 키워드를 입력하세요: ").strip()
    app.invoke({"keyword": keyword})
