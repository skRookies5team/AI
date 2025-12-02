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
	"""이메일 검색 상태 (mail_test.py의 MailState 참고).
	
	주의: 이 State는 내부 LangGraph용이며 Redis에 저장되지 않습니다.
	직렬화 가능한 타입만 사용하여 상위 그래프의 checkpointer와 충돌하지 않도록 함.
	connection 객체는 상태에서 제거하고 전역 변수로 관리합니다.
	"""
	# connection: Any  # IMAP4_SSL 연결 객체는 상태에서 제거 (직렬화 불가능)
	mail_ids: List[str]  # bytes를 문자열로 변환하여 직렬화 가능하게 함
	keyword: str
	matched_ids: List[str]  # bytes를 문자열로 변환하여 직렬화 가능하게 함
	matched_items: List[Dict[str, Any]]
	found_count: int
	limit: int
	batch_size: int  # 배치 크기 (기본 100)
	batch_offset: int  # 현재 배치 오프셋
	max_batches: int  # 최대 배치 수
	selection_index: int
	session_id: Optional[str]  # 세션 ID (멀티턴 대화 지원)
	result: Optional[Dict[str, Any]]
	has_more: bool  # 더 검색할 메일이 있는지 여부
	# orchestrator_state는 EmailSearchState에 포함하지 않음 (직렬화 오류 방지)
	# 모든 상태는 OrchestratorState.email_search_context를 통해 Redis에 저장됨


#
# IMAP 구현
#
# 세션별 이메일 검색 결과 캐시 (멀티턴 대화 지원)
# DEPRECATED: 이제 Redis 숏텀 메모리(OrchestratorState.email_search_context)를 사용합니다.
# 하위 호환성을 위해 유지되지만, 새 코드에서는 사용하지 마세요.
# {session_id: {"ids": [], "items": [], "index_map": {}, "batch_offset": 0, "last_keyword": "", "keyword_offsets": {}}}
# keyword_offsets: {extracted_keyword: batch_offset} - 키워드별 마지막 검색 위치 저장
_EMAIL_SEARCH_CACHE: Dict[str, Dict[str, Any]] = {}
_EMAIL_SESSION_ID: Optional[str] = None

# IMAP 연결 정보 캐시 (세션별이 아닌 전역)
_IMAP_CONNECTION_INFO: Dict[str, Any] = {"host": "", "port": 993, "user": ""}


def _set_email_session_id(session_id: Optional[str] = None) -> str:
	"""이메일 검색 세션 ID 설정."""
	global _EMAIL_SESSION_ID
	if session_id:
		_EMAIL_SESSION_ID = session_id
	elif not _EMAIL_SESSION_ID:
		import uuid
		_EMAIL_SESSION_ID = str(uuid.uuid4())
	return _EMAIL_SESSION_ID


def _get_email_cache(session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	"""세션별 이메일 검색 결과 캐시 가져오기.
	
	Redis 숏텀 메모리(OrchestratorState)를 우선 사용하고,
	없으면 로컬 캐시를 사용합니다 (하위 호환성).
	
	Args:
		session_id: 세션 ID
		state: OrchestratorState (선택적, Redis에서 가져온 state)
	
	Returns:
		이메일 검색 캐시 딕셔너리
	"""
	# Redis 숏텀 메모리(OrchestratorState)에서 가져오기 (우선)
	if state and "email_search_context" in state and state["email_search_context"]:
		return state["email_search_context"]
	
	# 하위 호환성: 로컬 캐시 사용 (deprecated)
	sid = _set_email_session_id(session_id)
	if sid not in _EMAIL_SEARCH_CACHE:
		_EMAIL_SEARCH_CACHE[sid] = {
			"ids": [],
			"items": [],
			"index_map": {},
			"batch_offset": 0,
			"last_keyword": "",
			"keyword_offsets": {}  # 키워드별 마지막 검색 위치: {extracted_keyword: batch_offset}
		}
	return _EMAIL_SEARCH_CACHE[sid]


def _save_email_cache(cache_data: Dict[str, Any], session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	"""세션별 이메일 검색 결과 캐시 저장.
	
	Redis 숏텀 메모리(OrchestratorState)에 저장할 수 있도록
	업데이트된 state를 반환합니다.
	
	Args:
		cache_data: 저장할 캐시 데이터
		session_id: 세션 ID
		state: OrchestratorState (선택적, Redis에 저장할 state)
	
	Returns:
		업데이트된 state (state가 전달된 경우) 또는 None
	"""
	# Redis 숏텀 메모리(OrchestratorState)에 저장 (우선)
	if state is not None:
		updated_state = state.copy()
		updated_state["email_search_context"] = cache_data
		return updated_state
	
	# 하위 호환성: 로컬 캐시 사용 (deprecated)
	sid = _set_email_session_id(session_id)
	_EMAIL_SEARCH_CACHE[sid] = cache_data
	return None

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
	_IMAP_CONNECTION_INFO["host"] = host
	_IMAP_CONNECTION_INFO["port"] = port
	_IMAP_CONNECTION_INFO["user"] = user
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
	"""IMAP 서버 연결 (mail_test.py의 connect_imap 참고).
	
	주의: connection 객체는 상태에 저장하지 않고, 노드 함수 내에서만 사용합니다.
	IMAP 연결은 search_emails_node에서도 필요하므로, 여기서는 mail_ids만 가져옵니다.
	실제 검색은 search_emails_node에서 새로운 연결을 생성하여 수행합니다.
	"""
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
	
	# IMAP 연결 생성 (mail_ids만 가져오기 위해)
	m = imaplib.IMAP4_SSL(host, port)
	try:
		m.login(user, pwd)
		m.select("INBOX")
		
		status, data = m.search(None, "ALL")
		if status != "OK" or not data or not data[0]:
			session_id = state.get("session_id")
			return {**state, "mail_ids": [], "session_id": session_id, "result": {"ok": False, "error": "메일 목록을 가져올 수 없습니다."}}

		mail_ids_bytes = data[0].split()
		# bytes를 문자열로 변환하여 직렬화 가능하게 함
		mail_ids = [msg_id.decode('utf-8') if isinstance(msg_id, bytes) else str(msg_id) for msg_id in mail_ids_bytes]
		_IMAP_CONNECTION_INFO["host"] = host
		_IMAP_CONNECTION_INFO["port"] = port
		_IMAP_CONNECTION_INFO["user"] = user
		session_id = state.get("session_id")
		return {**state, "mail_ids": mail_ids, "session_id": session_id}
	finally:
		# 연결 종료 (mail_ids만 가져왔으므로)
		try:
			m.logout()
		except Exception:
			pass


def search_emails_node(state: EmailSearchState) -> EmailSearchState:
	"""메일 검색 - 100개씩 배치로 점진적 검색.
	
	주의: IMAP 연결은 이 노드 함수 내에서 생성하고 사용 후 즉시 종료합니다.
	모든 상태는 OrchestratorState.email_search_context를 통해 Redis에 저장됩니다.
	"""
	# orchestrator_state는 _imap_search_emails에서 설정한 전역 변수에서 가져오기
	global _current_orchestrator_state_for_node
	orchestrator_state = _current_orchestrator_state_for_node
	
	mail_ids = state["mail_ids"]
	keyword = state["keyword"]
	batch_size = state.get("batch_size", 100)
	batch_offset = state.get("batch_offset", 0)
	max_batches = state.get("max_batches", 10)  # 최대 10배치 (1000개)
	session_id = state.get("session_id")

	# 핵심 키워드 추출
	search_keyword = _extract_keywords(keyword)
	if not search_keyword:
		return {
			**state,
			"result": {"ok": True, "message": "검색 키워드를 찾을 수 없습니다.", "items": []},
		}
	
	# 이전 배치에서 찾은 결과 가져오기 (첫 번째 배치인 경우 빈 리스트)
	if batch_offset == 0:
		previous_items = []
		previous_ids = []
		previous_found = 0
	else:
		# 이전 배치 결과는 Redis 숏텀 메모리에서 가져오기
		# orchestrator_state를 전달하여 Redis에서 가져오기
		cache = _get_email_cache(session_id, orchestrator_state)
		previous_items = cache.get("items", [])
		previous_ids = cache.get("ids", [])
		previous_found = len(previous_items)

	# 현재 배치 범위 계산 (최신 메일부터 역순)
	total_mails = len(mail_ids)
	start_idx = total_mails - (batch_offset + 1) * batch_size
	end_idx = total_mails - batch_offset * batch_size

	if start_idx < 0:
		start_idx = 0

	# 현재 배치의 메일 ID 추출 (최신순)
	batch_ids = mail_ids[start_idx:end_idx] if start_idx < end_idx else []

	if not batch_ids:
		# 더 이상 검색할 메일이 없음
		if previous_found == 0:
			result = {
				"ok": True,
				"message": f"📭 관련 메일을 찾지 못했습니다. (총 {total_mails}개 메일 확인)\n\n💡 다른 키워드로 검색해보시거나, 검색 범위를 넓혀보세요.",
				"items": [],
			}
		else:
			# 메일 목록 포맷팅 (번호와 제목 표시)
			mail_list = []
			for item in previous_items:
				mail_index = item.get("index", "")
				mail_title = item.get("title", "(제목 없음)")
				mail_list.append(f"{mail_index}. {mail_title}")
			
			mail_list_text = "\n".join(mail_list)
			result = {
				"ok": True,
				"message": f"📧 관련 메일 {previous_found}개를 찾았습니다. (총 {total_mails}개 메일 확인)\n\n{mail_list_text}\n\n💡 특정 메일의 상세 내용을 보려면 번호를 말씀해주세요. (예: '355번에 대해 알려줘', '355번 상세')",
				"items": previous_items,
			}

		return {**state, "matched_ids": previous_ids, "matched_items": previous_items, "found_count": previous_found, "session_id": session_id, "result": result, "has_more": False}

	# IMAP 연결 생성
	host = _get_env("IMAP_HOST") or "imap.naver.com"
	port = int(_get_env("IMAP_PORT") or "993")
	user = _get_env("NAVER_EMAIL") or ""
	if not user:
		nid = _get_env("NAVER_ID") or ""
		if nid:
			user = f"{nid}@naver.com"
	pwd = _get_env("NAVER_APP_PASSWORD") or ""
	
	if not user or not pwd:
		return {
			**state,
			"result": {"ok": False, "error": "IMAP 접속 정보가 필요합니다."},
		}
	
	mail = imaplib.IMAP4_SSL(host, port)
	try:
		mail.login(user, pwd)
		mail.select("INBOX")

		# 현재 배치 검색
		items: List[Dict[str, Any]] = []
		matched_ids: List[str] = []  # bytes 대신 문자열 사용
		found_in_batch = 0

		# 배치 내에서 역순으로 처리 (최신 메일부터)
		for idx, msg_id in enumerate(reversed(batch_ids)):
			try:
				# msg_id가 문자열이면 bytes로 변환
				msg_id_bytes = msg_id.encode('utf-8') if isinstance(msg_id, str) else msg_id
				msg = _imap_fetch_msg(mail, msg_id_bytes)
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
					# 전체 메일 목록에서의 순서번호 계산
					global_index = total_mails - (batch_offset * batch_size + idx)
					s = _extract_summary_from_msg(global_index, msg)
					# 검색 결과에서의 실제 인덱스 (이전 결과 포함)
					actual_index = previous_found + found_in_batch
					display_index = global_index  # 표시되는 번호 (전체 메일 목록의 순서)

					items.append(
						{
							"title": s.subject,
							"from": s.from_addr,
							"date": s.date,
							"snippet": s.snippet,
							"index": str(display_index),  # 표시용 번호 (문자열)
							"actual_index": actual_index,  # 실제 인덱스 (숫자)
						}
					)
					# msg_id를 문자열로 저장 (직렬화 가능하게 함)
					msg_id_str = msg_id if isinstance(msg_id, str) else msg_id.decode('utf-8')
					matched_ids.append(msg_id_str)
					found_in_batch += 1
			except Exception as e:
				logger.debug("메일 처리 중 오류: %s", repr(e))
				continue
		
		# 이전 결과와 병합
		all_items = previous_items + items
		all_matched_ids = previous_ids + matched_ids
		total_found = previous_found + found_in_batch

		# 다음 배치가 있는지 확인 (먼저 계산)
		has_more = (batch_offset + 1) < max_batches and (batch_offset + 1) * batch_size < total_mails

		# 세션별 캐시에 저장 (Redis 숏텀 메모리 사용)
		# all_matched_ids는 이미 문자열 리스트이므로 변환 불필요
		all_matched_ids_str = all_matched_ids
		
		# orchestrator_state를 전달하여 Redis에서 가져오기
		cache = _get_email_cache(session_id, orchestrator_state)
		cache["ids"] = all_matched_ids_str  # 문자열로 변환된 ID 저장
		cache["items"] = all_items
		# batch_offset 업데이트: 다음 검색을 위해 증가시킴
		# has_more가 True이면 다음 배치가 있으므로 batch_offset을 증가시킴
		# has_more가 False이면 더 이상 검색할 메일이 없으므로 현재 batch_offset 유지
		if has_more:
			cache["batch_offset"] = batch_offset + 1
		else:
			cache["batch_offset"] = batch_offset  # 더 이상 검색할 메일이 없으면 현재 위치 유지
		cache["last_keyword"] = keyword

		# 키워드별 마지막 검색 위치 저장 (추출된 키워드 기준)
		extracted_keyword = _extract_keywords(keyword)
		if extracted_keyword:
			if "keyword_offsets" not in cache:
				cache["keyword_offsets"] = {}
			# has_more가 True이면 다음 배치 위치 저장, False이면 현재 위치 저장
			cache["keyword_offsets"][extracted_keyword] = batch_offset + 1 if has_more else batch_offset

		# 번호 매핑 저장: {표시번호: 실제인덱스}
		index_map = {}
		for item in all_items:
			display_num = int(item.get("index", 0))
			actual_idx = item.get("actual_index", 0)
			index_map[display_num] = actual_idx
		cache["index_map"] = index_map
		
		# Redis 숏텀 메모리에 저장
		# orchestrator_state를 전달하여 Redis에 저장
		updated_state = _save_email_cache(cache, session_id, orchestrator_state)
		if updated_state and orchestrator_state:
			orchestrator_state.update(updated_state)
	finally:
		# IMAP 연결 종료
		try:
			mail.logout()
		except Exception:
			pass
	
	# 결과가 충분히 찾았거나 (5개 이상), 더 이상 검색할 메일이 없으면 종료
	if total_found >= 5 or not has_more:

		if total_found == 0:
			result = {
				"ok": True,
				"message": f"📭 관련 메일을 찾지 못했습니다. (최근 {(batch_offset + 1) * batch_size}개 메일 확인)\n\n💡 다른 키워드로 검색해보시거나, 검색 범위를 넓혀보세요.",
				"items": [],
			}
		else:
			# 메일 목록 포맷팅 (번호와 제목 표시)
			mail_list = []
			for item in all_items:
				mail_index = item.get("index", "")
				mail_title = item.get("title", "(제목 없음)")
				mail_list.append(f"{mail_index}. {mail_title}")
			
			mail_list_text = "\n".join(mail_list)
			result = {
				"ok": True,
				"message": f"📧 관련 메일 {total_found}개를 찾았습니다. (최근 {(batch_offset + 1) * batch_size}개 메일 확인)\n\n{mail_list_text}\n\n💡 특정 메일의 상세 내용을 보려면 번호를 말씀해주세요. (예: '355번에 대해 알려줘', '355번 상세')",
				"items": all_items,
			}

		return {**state, "matched_ids": all_matched_ids, "matched_items": all_items, "found_count": total_found, "session_id": session_id, "result": result, "has_more": False}

	# 결과가 적지만 더 검색할 수 있는 경우
	# 메일 목록 포맷팅 (번호와 제목 표시)
	mail_list = []
	for item in all_items:
		mail_index = item.get("index", "")
		mail_title = item.get("title", "(제목 없음)")
		mail_list.append(f"{mail_index}. {mail_title}")
	
	mail_list_text = "\n".join(mail_list)
	result = {
		"ok": True,
		"message": f"📧 관련 메일 {total_found}개를 찾았습니다. (최근 {(batch_offset + 1) * batch_size}개 메일 확인)\n\n{mail_list_text}\n\n💡 더 많은 메일을 검색하려면 '더 찾아줘' 또는 '계속 검색'이라고 말씀해주세요.\n💡 특정 메일의 상세 내용을 보려면 번호를 말씀해주세요. (예: '355번에 대해 알려줘', '355번 상세')",
		"items": all_items,
		"has_more": True,
	}
	
	return {**state, "matched_ids": all_matched_ids, "matched_items": all_items, "found_count": total_found, "batch_offset": batch_offset + 1, "session_id": session_id, "result": result, "has_more": has_more}


def fetch_detail_node(state: EmailSearchState) -> EmailSearchState:
	"""선택한 메일의 상세 내용 가져오기."""
	matched_ids = state.get("matched_ids", [])
	selection_index = state.get("selection_index", 0)
	session_id = state.get("session_id")

	if not matched_ids or selection_index <= 0 or selection_index > len(matched_ids):
		return {
			**state,
			"result": {"ok": False, "error": "유효하지 않은 번호입니다."},
		}
	
	msg_id = matched_ids[selection_index - 1]
	# matched_ids가 문자열이므로 bytes로 변환 필요
	msg_id_bytes = msg_id.encode('utf-8') if isinstance(msg_id, str) else msg_id
	m = _imap_connect()
	try:
		msg = _imap_fetch_msg(m, msg_id_bytes)
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
	
	return {**state, "result": result, "session_id": session_id}


#
# LangGraph 구성
#
# 전역 변수 제거: 모든 상태는 OrchestratorState를 통해 Redis에 저장됨
# IMAP 연결은 각 노드 함수 내에서 생성하고 사용 후 즉시 종료

# orchestrator_state를 노드 함수에 전달하기 위한 임시 저장소
# 주의: 이는 단일 요청당 단일 그래프 실행이므로 안전함
_current_orchestrator_state_for_node: Optional[Dict[str, Any]] = None

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
# 주의: 이 그래프는 checkpointer 없이 컴파일합니다.
# EmailSearchState에는 IMAP4_SSL 연결 객체와 bytes 타입이 포함되어 있어
# Redis에 직렬화할 수 없습니다. 이 그래프는 내부 구현 세부사항이므로
# Redis에 저장할 필요가 없습니다. 실제 상태는 OrchestratorState에 저장됩니다.
_search_graph = _build_search_graph().compile(checkpointer=None)
_detail_graph = _build_detail_graph().compile(checkpointer=None)


#
# 기존 함수 인터페이스 유지 (LangGraph 사용)
#
async def _imap_search_emails(keyword: str, limit: int = 1000, session_id: Optional[str] = None, batch_offset: int = 0, orchestrator_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	"""IMAP을 통해 최근 메일에서 키워드로 검색 (LangGraph 사용, 100개씩 배치 검색).

	Args:
		keyword: 검색 키워드
		limit: 검색할 최대 메일 개수 (참고용, 실제로는 배치 단위로 검색)
		session_id: 세션 ID (멀티턴 대화 지원)
		batch_offset: 배치 오프셋 (0부터 시작)
		orchestrator_state: OrchestratorState (Redis 숏텀 메모리)
	
	Note:
		AsyncRedisSaver를 사용하는 경우 비동기 인터페이스(ainvoke)를 사용해야 합니다.
	"""
	batch_size = 100
	max_batches = (limit + batch_size - 1) // batch_size  # 올림 계산

	# initial_state에는 직렬화 불가능한 객체를 포함하지 않음
	# orchestrator_state는 노드 함수에서 직접 사용 (클로저 또는 전역 변수로 전달)
	# connection은 전역 변수로 관리하므로 상태에서 제거
	initial_state: EmailSearchState = {
		"mail_ids": [],  # connect_imap_node에서 생성됨
		"keyword": keyword,
		"matched_ids": [],
		"matched_items": [],
		"found_count": 0,
		"limit": limit,
		"batch_size": batch_size,
		"batch_offset": batch_offset,
		"max_batches": max_batches,
		"session_id": session_id,
		"result": None,
		"has_more": False,
		# orchestrator_state는 포함하지 않음 (직렬화 오류 방지)
		# 대신 search_emails_node에서 session_id를 통해 접근
	}
	# AsyncRedisSaver를 사용하는 경우 비동기 인터페이스 사용
	# 주의: _search_graph는 checkpointer=None으로 컴파일되어 내부 상태는 Redis에 저장되지 않음
	# 하지만 검색 결과는 orchestrator_state를 통해 OrchestratorState.email_search_context에 저장됨
	
	# orchestrator_state를 노드 함수에 전달하기 위해 전역 변수에 저장
	# 주의: 이는 단일 요청당 단일 그래프 실행이므로 안전함
	global _current_orchestrator_state_for_node
	_current_orchestrator_state_for_node = orchestrator_state
	
	try:
		# _search_graph 실행 (checkpointer 없이 독립 실행)
		final_state = await _search_graph.ainvoke(initial_state, config={"configurable": {}})
		result = final_state.get("result")
		
		# search_emails_node에서 이미 _save_email_cache를 통해 OrchestratorState.email_search_context에 저장됨
		# orchestrator_state는 상위 그래프에서 자동으로 Redis에 저장됨
		
		if result:
			return result
		return {"ok": False, "error": "검색 결과를 가져올 수 없습니다."}
	finally:
		# 전역 변수 정리
		_current_orchestrator_state_for_node = None

async def _imap_fetch_full_by_index(sel_index: int, session_id: Optional[str] = None, by_display_number: bool = True, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	"""선택한 메일의 상세 내용 가져오기 (LangGraph 사용).

	Args:
		sel_index: 선택한 번호
		session_id: 세션 ID (멀티턴 대화 지원)
		by_display_number: True면 표시 번호로 검색, False면 실제 인덱스로 검색
		state: OrchestratorState (선택적, Redis에서 가져온 state)
	"""
	cache = _get_email_cache(session_id, state)

	if not cache.get("ids"):
		return {"ok": False, "error": "선택 가능한 메일 목록이 없습니다. 먼저 검색을 실행해 주세요."}
	
	# 표시 번호로 검색하는 경우 매핑에서 실제 인덱스 찾기
	if by_display_number:
		index_map = cache.get("index_map", {})
		# Redis에서 로드된 JSON은 키가 문자열일 수 있으므로 처리
		actual_index = index_map.get(sel_index)
		if actual_index is None:
			# 문자열 키로도 시도
			actual_index = index_map.get(str(sel_index))
		
		if actual_index is None:
			# 매핑에서 찾지 못하면, 표시 번호가 있는 아이템에서 직접 찾기
			items = cache.get("items", [])
			for idx, item in enumerate(items):
				if int(item.get("index", 0)) == sel_index:
					actual_index = idx
					break

			if actual_index is None:
				return {"ok": False, "error": f"번호 {sel_index}에 해당하는 메일을 찾을 수 없습니다."}

		# 실제 인덱스는 1-based로 변환 (사용자 친화적)
		sel_index = actual_index + 1

	initial_state: EmailSearchState = {
		# connection은 전역 변수로 관리하므로 상태에서 제거
		"mail_ids": [],
		"keyword": "",
		"matched_ids": cache["ids"],
		"matched_items": cache.get("items", []),
		"found_count": 0,
		"limit": 1000,
		"selection_index": sel_index,
		"session_id": session_id,
		"result": None,
	}
	# AsyncRedisSaver를 사용하는 경우 비동기 인터페이스 사용
	final_state = await _detail_graph.ainvoke(initial_state)
	result = final_state.get("result")
	if result:
		return result
	return {"ok": False, "error": "메일 상세 내용을 가져올 수 없습니다."}

def has_pending_email_results(session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> bool:
	"""최근 검색 결과가 캐시에 남아 있는지 여부 (세션별).

	Args:
		session_id: 세션 ID (없으면 현재 세션 사용)
		state: OrchestratorState (선택적, Redis에서 가져온 state)
	"""
	cache = _get_email_cache(session_id, state)
	# 결과(ids)가 있거나, 이전 검색 키워드(last_keyword)가 있으면 연속 검색 가능
	return bool(cache.get("ids") or cache.get("last_keyword"))


async def email_search(user_text: str, session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	print("사용자 질의로 메일함을 검색하여 관련 메일 요약/상세을 준비중입니다.")
	"""사용자 질의로 IMAP 메일함을 검색하여 관련 메일 요약/상세를 반환합니다.

	Args:
		user_text: 사용자 질의 (예: "육아휴직 신청 관련 메일을 찾아줘")
		session_id: 세션 ID (멀티턴 대화 지원)
		state: OrchestratorState (선택적, Redis 숏텀 메모리에서 가져온 state)

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

	# IMAP: 선택 요청인지 확인
	# 1. 이전 검색 결과가 있고, 숫자로 시작 + "에 대해", "자세히", "상세히" 패턴 → 상세 요청 (메일 키워드 불필요)
	# 2. "메일" 또는 "이메일" 키워드가 포함된 경우 상세 요청
	# 3. 숫자로 시작 + 제목 형식 (예: "355번 메일", "355. 육아휴직 연장 안내의 건 메일")
	selection_index: Optional[int] = None
	selection_by_title: Optional[str] = None

	try:
		# 이전 검색 결과가 있는 경우에만 상세 요청 처리 (세션별)
		if has_pending_email_results(session_id, state):
			# 패턴 1: 숫자로 시작 + "에 대해", "자세히", "상세히" 패턴 → 상세 요청 (메일 키워드 불필요)
			# 예: "626. 육아휴직 연장 안내의 건에 대해 알려줘", "600. 육아휴직 연장 안내의 건 상세"
			# 주의: 다른 agent(특허, 논문)와 충돌 방지를 위해 이전 검색 결과 확인 필수
			if re.match(r"^\d+\.?\s*", query) and any(k in query for k in ["에 대해", "에대해", "자세히", "상세히", "상세", "알려줘", "알려"]):
				# 다른 명확한 의도 키워드가 없을 때만 (식단, 일정, 특허, 논문 등 제외)
				# 특허/논문 관련 키워드가 있으면 이메일이 아닐 가능성이 높음
				if not any(k in query for k in ["점심", "저녁", "메뉴", "식단", "구내식당", "일정", "스케줄",
				                                 "특허", "논문", "paper", "patent", "KISTI", "ScienceON", "학술"]):
					match_with_dot = re.match(r"^(\d+)\.\s*(.+)", query)
					if match_with_dot:
						selection_index = int(match_with_dot.group(1))
						# 제목에서 "에 대해", "에대해", "자세히", "상세히", "상세", "알려줘" 등 제거
						title = match_with_dot.group(2).strip()
						# "에 대해 알려줘", "에 대해 자세히 알려줘" 등의 패턴 제거
						title = re.sub(r"\s*에\s*대해\s*(자세히\s*)?(알려줘|알려)?\s*$", "", title, flags=re.IGNORECASE)
						title = re.sub(r"\s*자세히\s*(알려줘|알려)?\s*$", "", title, flags=re.IGNORECASE)
						title = re.sub(r"\s*상세히\s*(알려줘|알려)?\s*$", "", title, flags=re.IGNORECASE)
						title = re.sub(r"\s*상세\s*$", "", title, flags=re.IGNORECASE)
						title = re.sub(r"\s*알려줘\s*$", "", title, flags=re.IGNORECASE)
						selection_by_title = title.strip()
					else:
						# 숫자만 있는 경우 (예: "626에 대해 알려줘")
						digits = re.findall(r"\d+", query)
						if digits:
							selection_index = int(digits[0])

			# 패턴 2: "메일" 또는 "이메일" 키워드가 포함된 경우
			has_email_keyword = "메일" in query or "이메일" in query
			if has_email_keyword and selection_index is None:
				# 패턴 2-1: "355. 제목" 형식 (메일 키워드 포함)
				match_with_dot = re.match(r"^(\d+)\.\s*(.+)", query)
				if match_with_dot:
					selection_index = int(match_with_dot.group(1))
					selection_by_title = match_with_dot.group(2).strip()
				# 패턴 2-2: 숫자 + "번" + "메일"/"이메일" 패턴
				elif re.search(r"(\d+)\s*번\s*(메일|이메일)", query):
					digits = re.findall(r"\d+", query)
					if digits:
						selection_index = int(digits[0])
				# 패턴 2-3: 숫자 + "번" + "상세" 패턴 (예: "600번 상세", "600. 제목 상세")
				elif re.search(r"(\d+)\s*번\s*상세", query) or (re.match(r"^\d+\.?\s*", query) and "상세" in query):
					digits = re.findall(r"\d+", query)
					if digits:
						selection_index = int(digits[0])
						# 제목 추출 시도
						match_with_dot = re.match(r"^(\d+)\.\s*(.+)", query)
						if match_with_dot:
							title = match_with_dot.group(2).strip()
							title = re.sub(r"\s*상세\s*$", "", title, flags=re.IGNORECASE)
							if title:
								selection_by_title = title.strip()
				# 패턴 2-4: 숫자 + "메일"/"이메일" 패턴 (예: "355 메일", "355번 메일 상세")
				elif re.search(r"(\d+)\s*(번\s*)?(메일|이메일)", query):
					digits = re.findall(r"\d+", query)
					if digits:
						selection_index = int(digits[0])
				# 패턴 2-4: 숫자로 시작 + "메일"/"이메일" 포함 (제목 포함 가능)
				elif re.match(r"^\d+", query) and ("메일" in query or "이메일" in query):
					digits = re.findall(r"\d+", query)
					if digits:
						num_str = digits[0]
						if len(query) > len(num_str) and query[len(num_str):].strip():
							selection_index = int(num_str)
							# 제목 추출 시도
							title_part = query[len(num_str):].strip().lstrip(". ")
							if title_part and title_part not in ["메일", "이메일", "번 메일", "번 이메일"]:
								selection_by_title = title_part
	except Exception:
		selection_index = None
		selection_by_title = None

	# 선택 처리: 숫자가 있고 이전 검색 결과가 있으면 상세 보기 (세션별)
	try:
		if selection_index is not None and has_pending_email_results(session_id, state):
			# 제목으로도 검색 시도 (번호가 매칭되지 않는 경우)
			if selection_by_title:
				cache = _get_email_cache(session_id, state)
				items = cache.get("items", [])
				# 제목으로 아이템 찾기
				for idx, item in enumerate(items):
					item_title = item.get("title", "").strip()
					display_num = int(item.get("index", 0))
					# 번호가 일치하거나 제목이 유사하면
					if display_num == selection_index or selection_by_title.lower() in item_title.lower():
						actual_index = item.get("actual_index", idx)
						return await _imap_fetch_full_by_index(actual_index + 1, session_id=session_id, by_display_number=False, state=state)

			# 번호로만 검색
			return await _imap_fetch_full_by_index(selection_index, session_id=session_id, by_display_number=True, state=state)

		# "더 찾아줘", "계속 검색" 등의 요청 처리
		# Redis 숏텀 메모리(OrchestratorState)에서 이전 검색 정보 가져오기
		batch_offset = 0
		search_keyword = query  # 기본값은 현재 쿼리
		cache = _get_email_cache(session_id, state)

		# 현재 쿼리에서 키워드 추출
		current_extracted = _extract_keywords(query)

		# 이전 검색 결과가 있고, 명시적으로 "더 찾아줘" 등의 요청인 경우
		if has_pending_email_results(session_id, state):
			last_keyword = cache.get("last_keyword", "")
			keyword_offsets = cache.get("keyword_offsets", {})

			# "더 찾아줘", "계속 검색" 등의 연속 검색 요청인 경우
			if any(k in query.lower() for k in ["더", "계속", "추가", "더 찾", "계속 검색", "더 검색", "더 찾아줘", "계속 찾아줘", "더 찾아", "계속 찾아"]):
				# 이전 키워드를 사용하여 계속 검색 (Redis 숏텀 메모리에서 가져옴)
				if last_keyword:
					search_keyword = last_keyword
					# 저장된 배치 오프셋 사용 (Redis 숏텀 메모리에서 가져옴)
					# 이전 검색에서 저장된 batch_offset은 이미 다음 배치를 가리키고 있음
					batch_offset = cache.get("batch_offset", 0)
					print(f"✅ 연속 검색: 이전 키워드='{last_keyword}', batch_offset={batch_offset}")
				else:
					# 이전 키워드가 없으면 현재 쿼리 사용 (fallback)
					search_keyword = query
					batch_offset = 0
					print(f"⚠️ 이전 키워드 없음, 현재 쿼리 사용: '{query}'")
			# 같은 키워드로 계속 검색하는 경우
			elif query == last_keyword:
				# 저장된 배치 오프셋 사용 (Redis 숏텀 메모리에서 가져옴)
				batch_offset = cache.get("batch_offset", 0)
				search_keyword = query
			# 새로운 키워드로 검색하는 경우
			else:
				# 추출된 키워드가 이전에 검색한 키워드인지 확인
				if current_extracted and current_extracted in keyword_offsets:
					# 같은 키워드로 이전에 검색한 적이 있으면, 그 위치부터 계속 검색
					batch_offset = keyword_offsets[current_extracted]
					search_keyword = query
				else:
					# 완전히 새로운 키워드인 경우 처음부터 검색
					batch_offset = 0
					search_keyword = query

				# Redis 숏텀 메모리에 캐시 업데이트
				cache["batch_offset"] = batch_offset
				cache["last_keyword"] = query
				updated_state = _save_email_cache(cache, session_id, state)
				if updated_state:
					state.update(updated_state)
		else:
			# 새로운 검색인 경우
			batch_offset = 0
			search_keyword = query
			cache["batch_offset"] = 0
			cache["last_keyword"] = query
			if "keyword_offsets" not in cache:
				cache["keyword_offsets"] = {}
			# Redis 숏텀 메모리에 캐시 저장
			updated_state = _save_email_cache(cache, session_id, state)
			if updated_state:
				state.update(updated_state)

		# 검색 실행(IMAP) - 배치 단위로 검색 (이전 키워드 사용)
		# Redis 숏텀 메모리 state를 전달하여 이전 검색 정보 활용
		imap_limit = int(_get_env("IMAP_MAX_FETCH", "1000") or "1000")
		# _imap_search_emails는 비동기 함수이므로 await 사용
		return await _imap_search_emails(
			keyword=search_keyword, 
			limit=imap_limit, 
			session_id=session_id, 
			batch_offset=batch_offset,
			orchestrator_state=state
		)
	except Exception as e:
		import traceback
		traceback.print_exc()
		return {
			"ok": False,
			"error": f"IMAP 검색 실패: {str(e)}",
		}
	# 이 지점까지 오면 정상 반환됨(검색/상세)


async def email_search_async(user_text: str, session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	"""비동기 래퍼: email_search는 이미 비동기 함수이므로 직접 호출."""
	return await email_search(user_text, session_id=session_id, state=state)


