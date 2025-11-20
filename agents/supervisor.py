from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

from .schedule_master import schedule_master
from .document_master import document_master
from .qa_master import qa_master
from .tech_master import tech_master
from .email_master import email_master
from .llm_router import classify_intent_with_llm

# 전역 대화 메모리: 세션별로 이전 검색 결과 저장
_CONVERSATION_MEMORY: dict[str, dict] = {}


class ConversationState(BaseModel):
	query: str = Field(default="")
	route: Optional[Literal["schedule", "document", "qa", "tech", "email", "unknown"]] = Field(default=None)
	result: Optional[dict] = Field(default=None)
	session_id: Optional[str] = Field(default=None)
	context: Dict[str, Any] = Field(default_factory=dict)


def classify(state: ConversationState) -> ConversationState:
	"""LLM 기반 의도 분류 (키워드 fallback 포함)."""
	t = state.query.strip()
	import re
	session_id = state.session_id if hasattr(state, 'session_id') else None

	if session_id:
		memory = _CONVERSATION_MEMORY.get(session_id, {})
		pending_field = memory.get("pending_field")
		pending_intent = memory.get("pending_intent")
		if pending_field and pending_intent in {"예약", "수정", "취소", "조회"}:
			state.context.update(memory)
			state.route = "schedule"
			return state

	# 예약 컨텍스트가 명시적으로 제공된 경우 회의실 워크플로로 전달
	if state.context.get("reservation"):
		state.route = "schedule"
		return state

	update_keywords = ["수정", "변경", "바꿔", "업데이트", "재조정", "modify", "update", "reschedule", "adjust"]
	if any(keyword in t for keyword in update_keywords):
		state.context["action"] = "update"
		state.route = "schedule"
		return state

	cancel_keywords = ["취소", "cancel", "취소해", "취소해줘", "취소해주세요"]
	if any(keyword in t for keyword in cancel_keywords):
		state.context["action"] = "cancel"
		state.route = "schedule"
		return state

	# 빈 입력 처리 (최우선 체크)
	if not t:
		# 특허 검색 결과가 있으면 tech로, 그렇지 않으면 unknown으로
		try:
			from .tech_patent_agent import has_pending_patent_results
			if has_pending_patent_results(session_id):
				state.route = "tech"
				return state
		except Exception:
			pass
		state.route = "unknown"
		return state

	# 검색 모드 선택 대기 중이면 tech로 라우팅 (최우선 체크)
	try:
		from .tech_patent_agent import is_waiting_for_search_mode, get_session_search_mode, _WAITING_FOR_WEB_SEARCH, _set_session_id
		# 웹 검색 여부 대기 중이면 tech로 라우팅
		if session_id:
			sid = _set_session_id(session_id)
			if _WAITING_FOR_WEB_SEARCH.get(sid, False):
				state.route = "tech"
				return state

		if is_waiting_for_search_mode(session_id):
			state.route = "tech"
			return state
		# 검색 모드가 설정되어 있고, 특허 관련 후속 질문이면 tech로 라우팅
		saved_mode = get_session_search_mode(session_id)
		if saved_mode and any(k in t.lower() for k in ["그다음", "다음", "db", "임베딩", "벡터", "서버", "정보", "보여줘", "보여주", "찾아줘"]):
			state.route = "tech"
			return state
	except Exception:
		pass

	# 특허 번호 패턴 감지 (10자리 이상 숫자 또는 특허 번호 + "에 대해 자세히" 등) - 우선 체크
	# 특허 번호는 보통 10자리 이상의 숫자입니다
	patent_number_match = re.search(r'\b(\d{10,})\b', t)
	if patent_number_match or (re.search(r'\d{10,}', t) and any(k in t for k in ["자세히", "상세", "알고싶어", "알려줘", "에 대해"])):
		# 이전 특허 검색 결과가 있거나, 특허 번호 패턴이 명확하면 tech로 라우팅
		from .tech_patent_agent import has_pending_patent_results
		if has_pending_patent_results(session_id) or patent_number_match:
			state.route = "tech"
			return state

	# 명확한 다른 의도 키워드가 있으면 우선 처리 (이메일/특허 검색 결과가 있어도)
	# 식단/메뉴 관련 키워드 우선 체크
	if any(k in t for k in ["식단", "메뉴", "구내식당", "점심", "저녁", "아침", "식사", "밥"]):
		state.route = "qa"  # cafeteria는 qa 마스터에서 처리
		return state

	# 일정 관련 키워드 우선 체크
	if any(k in t for k in ["일정", "스케줄", "회의", "약속", "일정표"]):
		state.route = "schedule"
		return state

	# 문서 관련 키워드 우선 체크
	if any(k in t for k in ["문서", "서류", "자료", "파일", "생성", "작성"]):
		state.route = "document"
		return state

	# 특허 관련 키워드 우선 체크
	if any(k in t for k in ["특허", "특허권", "특허번호", "KIPRIS", "특허검색"]):
		state.route = "tech"
		return state

	# 쉼표로 구분된 숫자들 (예: "1,2,5", "1, 3, 5, 7, 10") - 특허 선택 패턴
	# 이전 특허 검색 결과가 있으면 tech로 라우팅
	if re.match(r"^[\d\s,]+$", t) and "," in t:
		# 숫자, 쉼표, 공백만 포함된 경우
		numbers = re.findall(r"\d+", t)
		if len(numbers) > 1:  # 여러 숫자가 있으면
			from .tech_patent_agent import has_pending_patent_results
			if has_pending_patent_results(session_id):
				state.route = "tech"
				return state

	# 공백으로 구분된 숫자들 (예: "1 2 5", "1 2 4 8 10") - 특허 선택 패턴
	# 이전 특허 검색 결과가 있으면 tech로 라우팅
	if re.match(r"^[\d\s]+$", t) and re.search(r"\d+\s+\d+", t):
		# 숫자와 공백만 포함되고, 공백으로 구분된 숫자가 있는 경우
		numbers = re.findall(r"\d+", t)
		if len(numbers) > 1:  # 여러 숫자가 있으면
			from .tech_patent_agent import has_pending_patent_results
			# 특허 검색 결과가 있으면 무조건 tech로 라우팅
			if has_pending_patent_results(session_id):
				state.route = "tech"
				return state
			# 특허 검색 결과가 없어도 숫자만 있으면 특허 선택으로 간주 (tech로 라우팅)
			# 논문 검색 결과가 있는지 확인
			try:
				from .tech_master import has_pending_article_results
				if has_pending_article_results(session_id):
					state.route = "tech"
					return state
			except Exception:
				pass
			# 특허나 논문 검색 결과가 없어도 숫자 선택 패턴이면 tech로 라우팅 (기본값)
			state.route = "tech"
			return state

	# 단일 단어 입력 체크 (예, 아니오 등) - 웹 검색 여부 대기 상태 우선 확인
	if len(t.split()) == 1:
		try:
			from .tech_patent_agent import _WAITING_FOR_WEB_SEARCH, _set_session_id
			if session_id:
				sid = _set_session_id(session_id)
				if _WAITING_FOR_WEB_SEARCH.get(sid, False):
					# "예", "아니오" 같은 단어는 특허 검색으로 라우팅
					if any(k in t.lower() for k in ["네", "예", "yes", "y", "아니오", "아니", "no", "n"]):
						state.route = "tech"
						return state
		except Exception:
			pass

	# 숫자만 입력된 경우: 이전 검색 결과 확인 (특허 우선, 그 다음 이메일)
	# 단, 명확한 다른 의도가 없을 때만
	# 단일 숫자(1 또는 2)는 검색 모드 선택일 수 있으므로 먼저 확인
	if re.fullmatch(r"\d+", t):
		# 검색 모드 선택 대기 중이면 tech로 (1 또는 2 입력)
		try:
			from .tech_patent_agent import is_waiting_for_search_mode
			if is_waiting_for_search_mode(session_id):
				state.route = "tech"
				return state
		except Exception:
			pass

		# 특허 검색 결과가 있으면 tech로
		from .tech_patent_agent import has_pending_patent_results
		if has_pending_patent_results(session_id):
			state.route = "tech"
			return state
		# 논문 검색 결과가 있으면 tech로
		try:
			from .tech_master import has_pending_article_results
			if has_pending_article_results(session_id):
				state.route = "tech"
				return state
		except Exception:
			pass
		# 이메일 검색 결과가 있으면 email로 (이메일 상세 요청)
		from .email_search_agent import has_pending_email_results
		if has_pending_email_results(session_id):
			state.route = "email"
			return state
	
	# 이메일 검색 연속 요청 체크 (이전 검색 결과가 있는 경우) - 우선 처리
	# "더 찾아줘", "계속 검색", "더 검색" 등의 요청은 이전 이메일 검색의 연속
	from .email_search_agent import has_pending_email_results
	if has_pending_email_results(session_id) and any(k in t.lower() for k in ["더", "계속", "추가", "더 찾", "계속 검색", "더 검색", "더 찾아줘", "계속 찾아줘"]):
		state.route = "email"
		return state

	# 숫자로 시작 + 제목 + "에 대해 자세히" 패턴 체크 (이메일 상세 요청)
	# 예: "355. 육아휴직 연장 안내의 건에 대해 자세히 알려줘"
	# 단, 다른 명확한 의도 키워드가 없을 때만
	# 주의: 특허/논문 관련 키워드가 있으면 tech로 라우팅되어야 하므로 제외
	if re.match(r"^\d+\.?\s*", t) and any(k in t for k in ["에 대해", "에대해", "자세히", "상세히"]):
		# 다른 명확한 의도가 없을 때만 이메일로
		# 특허/논문 관련 키워드가 있으면 tech로 라우팅되어야 함
		if not any(k in t for k in ["점심", "저녁", "메뉴", "식단", "구내식당", "일", "월", "년",
		                              "특허", "논문", "paper", "patent", "KISTI", "ScienceON", "학술"]):
			if has_pending_email_results(session_id):
				state.route = "email"
				return state

	# 숫자 + "번", "상세", "선택", "자세히", "에 대해" 등이 포함된 경우도 확인 (특허 우선, 그 다음 이메일)
	# 패턴: "355. 제목에 대해 자세히 알려줘", "355번 상세", "355번에 대해" 등
	# 단, 다른 명확한 의도가 없을 때만
	if re.match(r"^\d+", t) and any(k in t for k in ["번", "상세", "선택", "자세히", "에 대해", "에대해"]):
		# 다른 명확한 의도가 없을 때만
		# 특허/논문 관련 키워드가 있으면 tech로 라우팅되어야 함
		if not any(k in t for k in ["점심", "저녁", "메뉴", "식단", "구내식당", "일", "월", "년",
		                              "특허", "논문", "paper", "patent", "KISTI", "ScienceON", "학술"]):
			# 특허 검색 결과가 있으면 tech로
			from .tech_patent_agent import has_pending_patent_results
			if has_pending_patent_results(session_id):
				state.route = "tech"
				return state
			# 논문 검색 결과가 있으면 tech로
			try:
				from .tech_master import has_pending_article_results
				if has_pending_article_results(session_id):
					state.route = "tech"
					return state
			except Exception:
				pass
			# 이메일 검색 결과가 있으면 email로 (이메일 상세 요청)
			if has_pending_email_results(session_id):
				state.route = "email"
				return state
	
	# 이메일 관련 의도 우선 체크 (email_master로 라우팅)
	# "이메일에서 ... 찾아줘", "메일에서 ... 찾아줘" 같은 패턴은 이메일 검색
	# "이메일 자동응답", "메일 자동응답" 같은 패턴은 이메일 답장문 생성
	if any(phrase in t for phrase in ["메일에서", "이메일에서"]) or \
	   (any(k in t for k in ["메일", "이메일"]) and any(v in t for v in ["찾아", "찾아줘", "검색", "search", "find"])) or \
	   any(k in t for k in ["이메일 자동응답", "메일 자동응답", "자동응답", "답장", "응답", "회신"]):
		state.route = "email"
		return state
	
	# 이메일 상세 요청 우선 체크 (숫자로 시작하는 패턴이 있으면 이메일 상세 요청 우선)
	# 숫자로 시작 + 제목 형식이 있으면 이메일 상세 요청으로 간주
	# 복리후생 키워드보다 우선 처리 (예: "938. [고용보험]육아휴직...에 대해 알려줘")
	# 단, 날짜 표현이나 다른 명확한 의도가 아닐 때만
	if re.match(r"^\d+\.?\s*[^0-9]", t):
		# 날짜 표현(예: "17일")이나 다른 명확한 의도가 아닐 때만 이메일로
		if not any(k in t for k in ["일", "월", "년", "시", "분", "점심", "저녁", "메뉴", "식단", "구내식당"]):
			from .email_search_agent import has_pending_email_results
			if has_pending_email_results(session_id):
				state.route = "email"
				return state
	
	# 복리후생 관련 질문 체크 (QA로 라우팅)
	# 주의: 숫자로 시작하는 패턴은 이미 위에서 처리되었으므로 여기서는 제외
	# "휴가·근무제도", "근무제도", "급여 외 혜택", "회사 복지" 등은 복리후생 정책 질문
	# 단, 숫자로 시작하는 패턴이 있으면 이메일 상세 요청으로 간주하므로 이전에 처리됨
	benefit_keywords = [
		"복리후생", "근무제도", "급여 외 혜택", "회사 복지", "복지 정책", "복지 제도",
		"휴가규정", "근무 규정", "근무 제도", "휴가 제도", "휴가 규정",
		"보험", "수당", "퇴직연금", "건강보험", "연금", "보상", "복지 혜택"
	]
	# 복리후생 관련 질문인지 확인 (정책/제도에 대한 질문인지)
	# 단, 숫자로 시작하는 패턴이 있으면 이메일 상세 요청이므로 제외
	# "에 대해 알려줘" 같은 패턴이 있으면 이메일 상세 요청일 가능성이 높음
	if not re.match(r"^\d+", t) and not any(k in t for k in ["에 대해", "에대해", "자세히", "상세히", "알려줘"]) and \
	   (any(k in t for k in benefit_keywords) or \
	   ("휴가" in t and ("제도" in t or "규정" in t or "정책" in t or "알려" in t or "알려줘" in t)) or \
	   ("근무" in t and ("제도" in t or "규정" in t or "정책" in t or "알려" in t or "알려줘" in t))):
		state.route = "qa"
		return state
	
	# LLM 기반 라우팅 시도
	llm_route = classify_intent_with_llm(t, route_type="supervisor", fallback_route=None, session_id=session_id)

	if llm_route and llm_route != "unknown":
		# LLM이 "qa"로 분류했지만 이메일 관련이면 "email"로 변경
		if llm_route == "qa":
			# 이메일 검색 또는 답장문 생성 의도인지 확인
			if any(phrase in t for phrase in ["메일에서", "이메일에서"]) or \
			   (any(k in t for k in ["메일", "이메일"]) and any(v in t for v in ["찾아", "찾아줘", "검색", "search", "find"])) or \
			   any(k in t for k in ["이메일 자동응답", "메일 자동응답", "자동응답", "답장", "응답", "회신"]):
				state.route = "email"
			else:
				state.route = llm_route  # type: ignore
		else:
			state.route = llm_route  # type: ignore
		return state
	
	# LLM 실패 시 키워드 기반 fallback
	# 주의: "휴가"는 복리후생 질문일 수도 있고 연차 신청일 수도 있으므로, 
	# "연차 신청", "반차 신청" 같은 명확한 액션 키워드가 있을 때만 schedule로 라우팅
	if any(k in t for k in ["연차 신청", "반차 신청", "병가 신청", "경조사 신청", "회의실", "예약", "일정", "스케줄", "캘린더"]):
		state.route = "schedule"
	# 이메일 자동응답: "이메일 자동응답", "메일 자동응답" 같은 명확한 키워드만 document로
	elif any(k in t for k in ["이메일 자동응답", "메일 자동응답", "자동응답"]) or \
	     (any(k in t for k in ["문서", "보고서", "작성", "템플릿"]) and not any(v in t for v in ["찾아", "찾아줘", "검색"])):
		state.route = "document"
	elif any(k in t for k in ["이메일", "메일"]) and not any(v in t for v in ["찾아", "찾아줘", "검색", "자동응답", "답장", "응답", "회신"]):
		# 이메일 관련이지만 명확한 의도가 없으면 email로 (기본은 검색)
		state.route = "email"
	elif any(k in t for k in ["Q&A", "질문", "문의", "메뉴얼", "복리후생", "보험", "수당", "식단", "메뉴", "구내식당",
	                           "회사", "역사", "설립", "창립", "기업", "회사소개", "회사정보", "급여", "출근", "근무", "지급", "봉급", "연봉", "월급"]):
		state.route = "qa"
	elif any(k in t for k in ["특허", "논문", "KISTI", "ScienceON"]):
		state.route = "tech"
	else:
		state.route = "unknown"
	return state


def run_schedule(state: ConversationState) -> ConversationState:
	context: Dict[str, Any] = dict(getattr(state, "context", {}) or {})
	if state.session_id:
		context["session_id"] = state.session_id
	state.result = schedule_master(state.query, context=context)
	return state


def run_document(state: ConversationState) -> ConversationState:
	state.result = document_master(state.query)
	return state


def run_email(state: ConversationState) -> ConversationState:
	"""이메일 마스터 실행 (세션 ID 전달)."""
	session_id = state.session_id if hasattr(state, 'session_id') else None
	state.result = email_master(state.query, session_id=session_id)
	return state


def run_qa(state: ConversationState) -> ConversationState:
	"""QA 마스터 실행 (세션 ID 전달)."""
	session_id = state.session_id if hasattr(state, 'session_id') else None
	state.result = qa_master(state.query, session_id=session_id)
	return state


def run_tech(state: ConversationState) -> ConversationState:
	# state에서 session_id 가져오기
	session_id = state.session_id if hasattr(state, 'session_id') else None
	state.result = tech_master(state.query, session_id=session_id)
	return state


def handle_unknown(state: ConversationState) -> ConversationState:
	"""알 수 없는 요청에 대한 친절한 안내."""
	query = state.query.strip()

	# 회사 정보, 역사, 일반 질문은 qa로 재시도
	if any(k in query for k in ["회사", "역사", "설립", "창립", "기업", "회사소개", "회사정보",
	                             "급여", "출근", "근무", "지급", "봉급", "연봉", "월급",
	                             "어떻게", "뭐야", "알려줘", "알려", "궁금"]):
		# qa로 재라우팅 시도
		state.route = "qa"
		return run_qa(state)

	# 사용자 질문에 대한 더 구체적인 안내
	suggestions = []
	if any(k in query for k in ["휴가", "연차", "반차", "병가"]):
		suggestions.append("• 일정 관련: '사번 1001 연차 조회' 또는 '연차 신청'")
	if any(k in query for k in ["메일", "이메일"]):
		suggestions.append("• 이메일 검색: '육아휴직 관련 메일 찾아줘'")
	if any(k in query for k in ["복리후생", "복지", "보험", "수당"]):
		suggestions.append("• 복리후생: '휴가규정 알려줘' 또는 '급여 외 혜택'")
	if any(k in query for k in ["식단", "메뉴", "구내식당"]):
		suggestions.append("• 식단표: '오늘 점심 메뉴 뭐야?'")
	if any(k in query for k in ["특허", "논문", "기술"]):
		suggestions.append("• 기술 검색: '디스플레이 관련 특허 찾아줘'")

	message = "🤔 요청을 명확히 이해하지 못했습니다."
	if suggestions:
		message += "\n\n다음과 같이 질문해보세요:\n" + "\n".join(suggestions)
	else:
		message += "\n\n💡 다음 중 하나로 질문해보세요:"
		message += "\n• 일정: '연차 조회', '회의실 예약'"
		message += "\n• 이메일: '육아휴직 관련 메일 찾아줘'"
		message += "\n• 복리후생: '휴가규정 알려줘'"
		message += "\n• 식단표: '오늘 점심 메뉴 뭐야?'"
		message += "\n• 기술 검색: '디스플레이 관련 특허 찾아줘'"

	state.result = {
		"ok": False,
		"message": message,
	}
	return state


def build_graph() -> StateGraph:
	graph = StateGraph(ConversationState)
	graph.add_node("classify", classify)
	graph.add_node("schedule", run_schedule)
	graph.add_node("document", run_document)
	graph.add_node("qa", run_qa)
	graph.add_node("email", run_email)
	graph.add_node("unknown", handle_unknown)
	graph.add_node("tech", run_tech)

	graph.add_edge(START, "classify")
	# 라우팅
	def router(state: ConversationState) -> str:
		return state.route or "unknown"

	graph.add_conditional_edges(
		"classify",
		router,
		{"schedule": "schedule", "document": "document", "qa": "qa", "email": "email", "tech": "tech", "unknown": "unknown"},
	)
	graph.add_edge("schedule", END)
	graph.add_edge("document", END)
	graph.add_edge("qa", END)
	graph.add_edge("email", END)
	graph.add_edge("tech", END)
	graph.add_edge("unknown", END)
	return graph


def run_supervisor(
	query: str,
	session_id: Optional[str] = None,
	context: Optional[Dict[str, Any]] = None,
) -> dict:
	"""Supervisor 실행: 메모리 기능을 통한 멀티턴 대화 지원.

	Args:
		query: 사용자 질의
		session_id: 대화 세션 ID (메모리 관리를 위해 사용)
		context: 추가 컨텍스트 (예: 예약 정보)

	Returns:
		에이전트 실행 결과
	"""
	workflow = build_graph().compile()
	state = {
		"query": query,
		"session_id": session_id,
		"context": context or {},
	}
	out = workflow.invoke(state)
	# LangGraph invoke는 dict 상태를 반환할 수 있음
	if isinstance(out, dict):
		result = out.get("result") or {}
		if session_id:
			memory_payload = {
				"reservation_candidate": result.get("reservation_candidate"),
				"lookup_candidate": result.get("lookup_candidate"),
				"pending_field": result.get("pending_field"),
				"pending_intent": result.get("pending_intent"),
			}
			if any(memory_payload.values()):
				_CONVERSATION_MEMORY[session_id] = memory_payload
			elif session_id in _CONVERSATION_MEMORY:
				_CONVERSATION_MEMORY.pop(session_id, None)
		for key in ("reservation_candidate", "lookup_candidate", "pending_field", "pending_intent"):
			result.pop(key, None)
		return result
	# 혹시 모델 객체가 온 경우 대비
	if hasattr(out, "result"):
		return getattr(out, "result") or {}
	return {}
