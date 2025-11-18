from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
import html as html_module
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict
import imaplib
import email as email_pkg
from email.header import decode_header as _mime_decode_header
from langgraph.graph import StateGraph, START, END

logger = logging.getLogger(__name__)


def _load_env_file() -> None:
	"""모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
	try:
		env_path = os.path.join(os.getcwd(), ".env")
		if os.path.exists(env_path):
			with open(env_path, "r", encoding="utf-8") as f:
				for line in f:
					line = line.strip()
					if not line or line.startswith("#"):
						continue
					if "=" not in line:
						continue
					k, v = line.split("=", 1)
					k = k.strip()
					v = v.strip().strip("'").strip('"')
					# 이미 설정된 환경변수는 덮어쓰지 않음
					if k and k not in os.environ:
						os.environ[k] = v
	except Exception:
		# .env 읽기 실패는 치명적이지 않음
		pass


# 모듈 로드 시 .env 자동 로드
_load_env_file()


@dataclass
class EmailSummary:
	"""단일 이메일 요약 정보.

	Attributes:
		index: 메일 순번(1-based, 검색 결과 내 순서).
		subject: 메일 제목.
		from_addr: 발신자 표기.
		date: 원본 Date 헤더 값.
		snippet: 본문 일부 요약(최대 몇 줄).
	"""

	index: int
	subject: str
	from_addr: str
	date: str
	snippet: str


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
	"""환경 변수 안전 조회."""
	val = os.environ.get(name, default)
	return val


def _extract_keywords(query: str) -> str:
	"""사용자 질의에서 핵심 키워드만 추출 (mail_test.py 방식 참고).
	
	Args:
		query: 사용자 질의 전체 문자열. 예) "육아휴직 신청 관련 메일 찾아줘"
		
	Returns:
		핵심 키워드 문자열. 예) "육아휴직"
	"""
	q = query.strip()
	if not q:
		return ""
	# 불용어 셋
	stopwords = {
		"메일", "이메일", "메일에서", "이메일에서", "관련", "에서", "으로", "로", "을", "를", "은", "는", "이", "가", "에",
		"에게", "의", "도", "및", "그리고", "또는", "search", "find", "찾아", "찾아줘", "찾기", "검색", "해줘", "주세요",
		"관련된", "내용", "안내해줘", "알려줘", "관련해", "관련한", "신청", "안내", "공지",
	}
	# 토큰화 및 정제
	try:
		raw_tokens = [t for t in re.split(r"[\s\W]+", q) if t]
	except Exception:
		raw_tokens = q.split()
	# 불용어 제거 후 2글자 이상인 토큰만 추출
	keywords = [t for t in (tok.strip() for tok in raw_tokens) if t and t not in stopwords and len(t) >= 2]
	if not keywords:
		# 의미 토큰이 없으면 원본 반환 (전체 검색)
		return q
	# 핵심 키워드들을 공백으로 결합 (mail_test.py처럼 단순 매칭을 위해)
	return " ".join(keywords)


def _decode_header_value(value: Optional[str]) -> str:
	"""헤더 값 디코드 및 정리."""
	if not value:
		return ""
	return value.strip()

def _decode_mime_header(value: Optional[str]) -> str:
	"""MIME 인코딩된 헤더(Subject, From 등)를 안전하게 디코드."""
	if not value:
		return ""
	try:
		parts = _mime_decode_header(value)
	except Exception:
		return value
	result = ""
	for part, enc in parts:
		try:
			if isinstance(part, (bytes, bytearray)):
				if enc:
					result += part.decode(enc, errors="replace")
				else:
					result += part.decode("utf-8", errors="replace")
			else:
				result += str(part)
		except Exception:
			try:
				result += str(part)
			except Exception:
				pass
	return result.strip()

def _html_to_text(html: str) -> str:
	"""아주 단순한 HTML → 텍스트 변환."""
	try:
		# 엔티티 복원
		text = html_module.unescape(html)
		# 스크립트/스타일 제거
		text = re.sub(r"<(script|style)[\\s\\S]*?>[\\s\\S]*?</\\1>", " ", text, flags=re.IGNORECASE)
		# 태그 제거
		text = re.sub(r"<[^>]+>", " ", text)
		# 공백 정리
		text = re.sub(r"[ \\t\\r\\f\\v]+", " ", text)
		return text.strip()
	except Exception:
		return html


def _message_to_text(msg, max_lines: Optional[int] = 5) -> str:
	"""이메일 메시지에서 text/plain 본문을 추출 (mail_test.py의 extract_text_from_msg 참고).
	
	중복을 방지하기 위해 text/plain을 우선하고, 없으면 text/html을 텍스트로 변환합니다.
	"""
	body_parts: List[str] = []
	
	if msg.is_multipart():
		# multipart 메일: text/plain 우선, 없으면 text/html 사용
		plain_text = None
		html_text = None
		
		for part in msg.walk():
			ct = part.get_content_type()
			disp = str(part.get("Content-Disposition") or "")
			
			# 첨부 파일은 제외
			if "attachment" in disp.lower():
				continue
			
			# text/plain 우선 처리
			if ct == "text/plain" and plain_text is None:
				try:
					payload = part.get_payload(decode=True)
					if payload:
						charset = part.get_content_charset()
						try:
							if charset:
								text = payload.decode(charset, errors="replace")
							else:
								text = payload.decode("utf-8", errors="replace")
							plain_text = text
						except Exception:
							try:
								text = payload.decode("utf-8", errors="replace")
								plain_text = text
							except Exception:
								pass
				except Exception:
					pass
			
			# text/html은 plain이 없을 때만 사용
			elif ct == "text/html" and html_text is None and plain_text is None:
				try:
					payload = part.get_payload(decode=True)
					if payload:
						charset = part.get_content_charset()
						try:
							if charset:
								html = payload.decode(charset, errors="replace")
							else:
								html = payload.decode("utf-8", errors="replace")
							html_text = _html_to_text(html)
						except Exception:
							try:
								html = payload.decode("utf-8", errors="replace")
								html_text = _html_to_text(html)
							except Exception:
								pass
				except Exception:
					pass
		
		# 우선순위: plain_text > html_text
		if plain_text:
			body_parts.append(plain_text)
		elif html_text:
			body_parts.append(html_text)
	else:
		# 단일 파트 메일
		try:
			payload = msg.get_payload(decode=True)
			if payload:
				charset = msg.get_content_charset()
				try:
					if charset:
						text = payload.decode(charset, errors="replace")
					else:
						text = payload.decode("utf-8", errors="replace")
				except Exception:
					text = payload.decode("utf-8", errors="replace")
				
				ct = msg.get_content_type()
				if ct == "text/html":
					body_parts.append(_html_to_text(text))
				else:
					body_parts.append(text)
		except Exception:
			# 페이로드가 문자열인 경우
			try:
				payload = msg.get_payload()
				if isinstance(payload, str):
					ct = msg.get_content_type()
					if ct == "text/html":
						body_parts.append(_html_to_text(payload))
					else:
						body_parts.append(payload)
			except Exception:
				pass
	
	full_text = "\n".join(body_parts).strip()
	if max_lines is None:
		return full_text
	return "\n".join(full_text.splitlines()[: max_lines]).strip()


#
# LangGraph State 정의 (mail_test.py 참고)
#
class EmailSearchState(TypedDict, total=False):
	"""이메일 검색 상태 (mail_test.py의 MailState 참고)."""
	connection: Any
	mail_ids: List[bytes]
	keyword: str
	matched_ids: List[bytes]
	matched_items: List[Dict[str, Any]]
	found_count: int
	limit: int
	selection_index: int
	result: Optional[Dict[str, Any]]


#
# IMAP 구현
#
_IMAP_CACHE: Dict[str, Any] = {"ids": [], "items": [], "host": "", "port": 993, "user": ""}

def _imap_connect() -> imaplib.IMAP4_SSL:
	"""IMAP 서버에 연결 (mail_test.py 방식 참고)."""
	# mail_test.py: host = "imap.naver.com", port = 993
	host = _get_env("IMAP_HOST") or "imap.naver.com"
	port = int(_get_env("IMAP_PORT") or "993")
	user = _get_env("NAVER_EMAIL") or ""
	if not user:
		nid = _get_env("NAVER_ID") or ""
		if nid:
			user = f"{nid}@naver.com"
	pwd = _get_env("NAVER_APP_PASSWORD") or ""
	if not user or not pwd:
		raise RuntimeError(
			"IMAP 접속 정보가 필요합니다. .env 파일에 NAVER_EMAIL, NAVER_APP_PASSWORD를 설정해 주세요."
		)
	m = imaplib.IMAP4_SSL(host, port)
	m.login(user, pwd)
	m.select("INBOX")
	_IMAP_CACHE["host"] = host
	_IMAP_CACHE["port"] = port
	_IMAP_CACHE["user"] = user
	return m

def _imap_fetch_msg(m: imaplib.IMAP4_SSL, msg_id: bytes) -> email_pkg.message.Message:
	st, msg_data = m.fetch(msg_id, "(RFC822)")
	if st != "OK" or not msg_data or not msg_data[0]:
		raise RuntimeError("메일을 가져오지 못했습니다.")
	return email_pkg.message_from_bytes(msg_data[0][1])

def _extract_summary_from_msg(idx: int, msg: email_pkg.message.Message) -> EmailSummary:
	subject = _decode_mime_header(msg.get("Subject"))
	from_addr = _decode_mime_header(msg.get("From"))
	date = _decode_header_value(msg.get("Date"))
	snippet = _message_to_text(msg, max_lines=5)
	return EmailSummary(index=idx, subject=subject or "(제목 없음)", from_addr=from_addr, date=date, snippet=snippet)

#
# LangGraph 노드 정의 (mail_test.py 참고)
#
def connect_imap_node(state: EmailSearchState) -> EmailSearchState:
	"""IMAP 서버 연결 (mail_test.py의 connect_imap 참고)."""
	host = _get_env("IMAP_HOST") or "imap.naver.com"
	port = int(_get_env("IMAP_PORT") or "993")
	user = _get_env("NAVER_EMAIL") or ""
	if not user:
		nid = _get_env("NAVER_ID") or ""
		if nid:
			user = f"{nid}@naver.com"
	pwd = _get_env("NAVER_APP_PASSWORD") or ""
	if not user or not pwd:
		raise RuntimeError(
			"IMAP 접속 정보가 필요합니다. .env 파일에 NAVER_EMAIL, NAVER_APP_PASSWORD를 설정해 주세요."
		)
	m = imaplib.IMAP4_SSL(host, port)
	m.login(user, pwd)
	m.select("INBOX")
	
	status, data = m.search(None, "ALL")
	if status != "OK" or not data or not data[0]:
		return {**state, "connection": m, "mail_ids": [], "result": {"ok": False, "error": "메일 목록을 가져올 수 없습니다."}}
	
	mail_ids = data[0].split()
	_IMAP_CACHE["host"] = host
	_IMAP_CACHE["port"] = port
	_IMAP_CACHE["user"] = user
	return {**state, "connection": m, "mail_ids": mail_ids}


def search_emails_node(state: EmailSearchState) -> EmailSearchState:
	"""메일 검색 (mail_test.py의 process_batch 참고)."""
	mail = state["connection"]
	mail_ids = state["mail_ids"]
	keyword = state["keyword"]
	limit = state.get("limit", 1000)
	
	# 핵심 키워드 추출
	search_keyword = _extract_keywords(keyword)
	if not search_keyword:
		return {
			**state,
			"result": {"ok": True, "message": "검색 키워드를 찾을 수 없습니다.", "items": []},
		}
	
	# 최근 limit개만 사용
	ids = mail_ids[-limit:] if len(mail_ids) > limit else mail_ids
	items: List[Dict[str, Any]] = []
	matched_ids: List[bytes] = []
	found = 0
	
	for i, msg_id in enumerate(reversed(ids), 1):
		try:
			msg = _imap_fetch_msg(mail, msg_id)
			subject = _decode_mime_header(msg.get("Subject"))
			body_text = _message_to_text(msg, max_lines=None)
			# mail_test.py 방식: keyword in subject or keyword in body
			keywords_list = search_keyword.split()
			matched = False
			if keywords_list:
				subject_lower = subject.lower()
				body_lower = body_text.lower()
				for kw in keywords_list:
					if kw in subject_lower or kw in body_lower:
						matched = True
						break
			
			if matched:
				s = _extract_summary_from_msg(i, msg)
				items.append(
					{
						"title": s.subject,
						"from": s.from_addr,
						"date": s.date,
						"snippet": s.snippet,
						"index": i,
					}
				)
				matched_ids.append(msg_id)
				found += 1
		except Exception as e:
			logger.debug("메일 처리 중 오류: %s", repr(e))
			continue
	
	# 캐시에 저장
	_IMAP_CACHE["ids"] = matched_ids
	_IMAP_CACHE["items"] = items
	
	if found == 0:
		result = {
			"ok": True,
			"message": f"관련 메일을 찾지 못했습니다. (최근 {len(ids)}개 검사)",
			"items": [],
		}
	else:
		result = {
			"ok": True,
			"message": f"다음 메일을 찾았습니다. (최근 {len(ids)}개 중 {found}개 일치)\n원하는 메일 번호를 선택해 상세를 볼 수 있습니다. 예) '메일 1번 상세'",
			"items": items,
		}
	
	# 연결 종료
	try:
		mail.logout()
	except Exception:
		pass
	
	return {**state, "matched_ids": matched_ids, "matched_items": items, "found_count": found, "result": result}


def fetch_detail_node(state: EmailSearchState) -> EmailSearchState:
	"""선택한 메일의 상세 내용 가져오기."""
	matched_ids = state.get("matched_ids", [])
	selection_index = state.get("selection_index", 0)
	
	if not matched_ids or selection_index <= 0 or selection_index > len(matched_ids):
		return {
			**state,
			"result": {"ok": False, "error": "유효하지 않은 번호입니다."},
		}
	
	msg_id = matched_ids[selection_index - 1]
	m = _imap_connect()
	try:
		msg = _imap_fetch_msg(m, msg_id)
		subject = _decode_mime_header(msg.get("Subject"))
		sender = _decode_mime_header(msg.get("From"))
		date = _decode_header_value(msg.get("Date"))
		body = _message_to_text(msg, max_lines=None)
		content = (
			f"제목: {subject}\n보낸 사람: {sender}\n날짜: {date}\n"
			f"----------------------------------------\n{body or '(본문 없음)'}"
		)
		result = {"ok": True, "message": content}
	finally:
		try:
			m.logout()
		except Exception:
			pass
	
	return {**state, "result": result}


#
# LangGraph 구성
#
def _build_search_graph() -> StateGraph:
	"""검색 그래프 구성 (mail_test.py 참고)."""
	graph = StateGraph(EmailSearchState)
	graph.add_node("connect_imap", connect_imap_node)
	graph.add_node("search_emails", search_emails_node)
	graph.add_edge(START, "connect_imap")
	graph.add_edge("connect_imap", "search_emails")
	graph.add_edge("search_emails", END)
	return graph


def _build_detail_graph() -> StateGraph:
	"""상세 보기 그래프 구성."""
	graph = StateGraph(EmailSearchState)
	graph.add_node("fetch_detail", fetch_detail_node)
	graph.add_edge(START, "fetch_detail")
	graph.add_edge("fetch_detail", END)
	return graph


# 그래프 인스턴스 (재사용)
_search_graph = _build_search_graph().compile()
_detail_graph = _build_detail_graph().compile()


#
# 기존 함수 인터페이스 유지 (LangGraph 사용)
#
def _imap_search_emails(keyword: str, limit: int = 1000) -> Dict[str, Any]:
	"""IMAP을 통해 최근 메일에서 키워드로 검색 (LangGraph 사용)."""
	initial_state: EmailSearchState = {
		"connection": None,
		"mail_ids": [],
		"keyword": keyword,
		"matched_ids": [],
		"matched_items": [],
		"found_count": 0,
		"limit": limit,
		"result": None,
	}
	final_state = _search_graph.invoke(initial_state)
	result = final_state.get("result")
	if result:
		return result
	return {"ok": False, "error": "검색 결과를 가져올 수 없습니다."}

def _imap_fetch_full_by_index(sel_index: int) -> Dict[str, Any]:
	"""선택한 메일의 상세 내용 가져오기 (LangGraph 사용)."""
	if not _IMAP_CACHE.get("ids"):
		return {"ok": False, "error": "선택 가능한 메일 목록이 없습니다. 먼저 검색을 실행해 주세요."}
	
	initial_state: EmailSearchState = {
		"connection": None,
		"mail_ids": [],
		"keyword": "",
		"matched_ids": _IMAP_CACHE["ids"],
		"matched_items": _IMAP_CACHE.get("items", []),
		"found_count": 0,
		"limit": 1000,
		"selection_index": sel_index,
		"result": None,
	}
	final_state = _detail_graph.invoke(initial_state)
	result = final_state.get("result")
	if result:
		return result
	return {"ok": False, "error": "메일 상세 내용을 가져올 수 없습니다."}

def has_pending_email_results() -> bool:
	"""최근 검색 결과가 캐시에 남아 있는지 여부."""
	return bool(_IMAP_CACHE.get("ids"))


def email_search(user_text: str) -> Dict[str, Any]:
	"""사용자 질의로 IMAP 메일함을 검색하여 관련 메일 요약/상세를 반환합니다.

	.env 지원:
		현재 작업 디렉터리의 .env 파일을 자동으로 읽어 환경변수에 주입하려 시도합니다(존재 시).
		이미 설정된 환경변수는 .env로 덮어쓰지 않습니다.

	환경 변수:
		IMAP_HOST (기본 imap.naver.com), IMAP_PORT (기본 993)
		EMAIL_USERNAME 또는 NAVER_EMAIL
		EMAIL_PASSWORD 또는 NAVER_APP_PASSWORD
		IMAP_MAX_FETCH: 최근 몇 개 메시지 범위를 검색할지 (기본 1000)

	Args:
		user_text: 예) "육아휴직 신청 관련 메일을 찾아줘"

	Returns:
		표준화된 dict 결과. format_result와 호환되는 구조.
	
	Note:
		.env 파일의 NAVER_EMAIL, NAVER_APP_PASSWORD는 모듈 로드 시 자동으로 환경변수에 주입됩니다.
	"""
	# 로깅 설정 (EMAIL_DEBUG=true 시 활성화)
	def _mask_user(u: str) -> str:
		if not u:
			return ""
		if "@" in u:
			local, domain = u.split("@", 1)
			if len(local) <= 2:
				local_mask = local[:1] + "*" * max(0, len(local) - 1)
			else:
				local_mask = local[:2] + "*" * (len(local) - 2)
			return f"{local_mask}@{domain}"
		return u[:2] + "*" * max(0, len(u) - 2)

	email_debug = (_get_env("EMAIL_DEBUG", "false") or "false").lower() in {"1", "true", "yes", "y"}
	if email_debug:
		if not logger.handlers:
			handler = logging.StreamHandler()
			formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
			handler.setFormatter(formatter)
			logger.addHandler(handler)
		logger.setLevel(logging.DEBUG)

	query = user_text.strip()

	if email_debug:
		# 질의 토큰 간단 추출(로그용)
		try:
			q_tokens = [t for t in re.split(r"[\\s\\W]+", query) if t]
		except Exception:
			q_tokens = query.split()
		logger.info(
			"email_search(IMAP) start: tokens=%s imap_limit=%s",
			q_tokens,
			_get_env("IMAP_MAX_FETCH", "1000") or "1000",
		)

	# IMAP: 선택 요청인지 확인 (숫자만 입력 또는 숫자 + "번"/"상세"/"선택" 패턴)
	selection_index: Optional[int] = None
	try:
		# 숫자만 입력된 경우 (예: "7")
		if re.fullmatch(r"\d+", query):
			selection_index = int(query)
		# 숫자 + 키워드 패턴 (예: "7번", "7 상세", "메일 7번 선택")
		elif any(k in query for k in ["상세", "번", "선택"]):
			digits = re.findall(r"\d+", query)
			if digits:
				selection_index = int(digits[0])
	except Exception:
		selection_index = None
	
	# 선택 처리: 숫자가 있고 이전 검색 결과가 있으면 상세 보기
	try:
		if selection_index is not None and has_pending_email_results():
			return _imap_fetch_full_by_index(selection_index)
		# 검색 실행(IMAP)
		imap_limit = int(_get_env("IMAP_MAX_FETCH", "1000") or "1000")
		return _imap_search_emails(keyword=query, limit=imap_limit)
	except Exception as e:
		return {
			"ok": False,
			"error": f"IMAP 검색 실패: {str(e)}",
		}
	# 이 지점까지 오면 정상 반환됨(검색/상세)


async def email_search_async(user_text: str) -> Dict[str, Any]:
	"""비동기 래퍼: IMAP 블로킹 작업을 스레드로 오프로딩."""
	return await asyncio.to_thread(email_search, user_text)


