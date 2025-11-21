from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .email_search_agent import email_search, has_pending_email_results
from .email_auto_reply_agent import email_auto_reply
from .llm_router import classify_intent_with_llm


@dataclass
class EmailIntent:
	"""이메일 관련 의도 분류 결과."""
	kind: Literal["search", "auto_reply"]
	details: Optional[str] = None


def parse_email_intent(user_text: str, session_id: Optional[str] = None) -> EmailIntent:
	"""LLM 기반 의도 분류: 이메일 검색 vs 이메일 답장문 생성.
	
	Args:
		user_text: 사용자 질의
		session_id: 세션 ID (멀티턴 대화 지원)
		
	Returns:
		EmailIntent: 이메일 검색 또는 답장문 생성 의도
	"""
	t = user_text.strip()
	import re as _re
	
	# 이메일 검색 연속 요청 체크 (이전 검색 결과가 있는 경우)
	# "더 찾아줘", "계속 검색", "더 검색" 등의 요청은 이전 이메일 검색의 연속
	if has_pending_email_results(session_id) and any(k in t.lower() for k in ["더", "계속", "추가", "더 찾", "계속 검색", "더 검색", "더 찾아줘", "계속 찾아줘"]):
		return EmailIntent(kind="search", details=t)
	
	# 이메일 상세 요청 우선 체크
	# 숫자로 시작 + 제목 형식이 있으면 이메일 상세 요청으로 간주
	if has_pending_email_results(session_id):
		# 숫자로 시작 + 제목 형식 (예: "355. 육아휴직 연장 안내의 건")
		# 단, 날짜 표현이나 다른 명확한 의도가 아닐 때만
		if _re.match(r"^\d+\.?\s*[^0-9]", t):
			# 날짜 표현(예: "17일")이나 다른 명확한 의도가 아닐 때만 이메일로
			if not any(k in t for k in ["일", "월", "년", "시", "분", "점심", "저녁", "메뉴", "식단", "구내식당"]):
				return EmailIntent(kind="search", details=t)
		
		# 숫자만 입력된 경우 (예: "355")
		if _re.fullmatch(r"\d+", t):
			return EmailIntent(kind="search", details=t)
		
		# 숫자로 시작 + 상세 요청 키워드 (예: "355번", "355 상세", "355번 상세")
		if any(k in t for k in ["상세", "선택", "번"]) and not any(k in t for k in ["점심", "저녁", "메뉴", "식단", "구내식당"]):
			return EmailIntent(kind="search", details=t)
		
		# 숫자로 시작 + "에 대해 자세히" 패턴 (예: "355. 제목에 대해 자세히 알려줘")
		# 주의: 특허/논문 관련 키워드가 있으면 tech로 라우팅되어야 하므로 제외
		if _re.match(r"^\d+\.?\s*", t) and any(k in t for k in ["에 대해", "에대해", "자세히", "상세히", "알려줘", "알려"]):
			# 다른 명확한 의도 키워드가 없을 때만 이메일로
			# 특허/논문 관련 키워드가 있으면 tech로 라우팅되어야 함
			if not any(k in t for k in ["점심", "저녁", "메뉴", "식단", "구내식당", "식사", "밥",
			                            "특허", "논문", "paper", "patent", "KISTI", "ScienceON", "학술"]):
				return EmailIntent(kind="search", details=t)
	
	# 이메일 검색 의도 키워드 체크
	# "이메일에서 ... 찾아줘", "메일에서 ... 찾아줘" 같은 패턴은 이메일 검색
	if any(phrase in t for phrase in ["메일에서", "이메일에서"]) or \
	   (any(k in t for k in ["메일", "이메일"]) and any(v in t for v in ["찾아", "찾아줘", "검색", "search", "find"])):
		return EmailIntent(kind="search", details=t)
	
	# LLM 기반 라우팅 시도
	llm_intent = classify_intent_with_llm(t, route_type="email", fallback_route=None, session_id=session_id)
	
	if llm_intent:
		if llm_intent == "search":
			return EmailIntent(kind="search", details=t)
		elif llm_intent == "auto_reply":
			return EmailIntent(kind="auto_reply", details=t)
	
	# LLM 실패 시 키워드 기반 fallback
	# 이메일 답장문 생성 의도: "이메일 자동응답", "메일 자동응답", "답장", "응답" 등
	if any(k in t for k in ["이메일 자동응답", "메일 자동응답", "자동응답", "답장", "응답", "회신", "auto-reply", "auto reply"]):
		return EmailIntent(kind="auto_reply", details=t)
	
	# 기본은 이메일 검색 (이메일 관련 키워드가 있으면 검색으로 간주)
	if any(k in t for k in ["메일", "이메일"]):
		return EmailIntent(kind="search", details=t)
	
	# 기본값: 이메일 검색
	return EmailIntent(kind="search", details=t)


def email_master(user_text: str, session_id: Optional[str] = None) -> dict:
	"""이메일 마스터 에이전트: LLM 기반 라우팅으로 이메일 검색 또는 답장문 생성.
	
	Args:
		user_text: 사용자 질의
		session_id: 세션 ID (멀티턴 대화 지원)
		
	Returns:
		에이전트 실행 결과
	"""
	intent = parse_email_intent(user_text, session_id=session_id)
	
	if intent.kind == "search":
		return email_search(intent.details or user_text, session_id=session_id)
	elif intent.kind == "auto_reply":
		return email_auto_reply(intent.details or user_text)
	
	# 기본값: 이메일 검색
	return email_search(user_text, session_id=session_id)



