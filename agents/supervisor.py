from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Optional

from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

from .schedule_master import schedule_master
from .document_master import document_master
from .qa_master import qa_master
from .llm_router import classify_intent_with_llm

# tech_master는 선택적 import (의존성 문제 가능)
try:
    from .tech_master import tech_master
    TECH_AVAILABLE = True
except ImportError:
    TECH_AVAILABLE = False
    tech_master = None  # type: ignore

# 전역 대화 메모리: 세션별로 이전 검색 결과 저장
_CONVERSATION_MEMORY: dict[str, dict] = {}


class ConversationState(BaseModel):
	"""에이전트 매니저먼트 챗봇의 대화 상태."""
	query: str = Field(default="", description="사용자 질의")
	route: Optional[Literal["schedule", "document", "qa", "tech", "unknown"]] = Field(
		default=None, description="라우팅된 에이전트 타입"
	)
	result: Optional[dict[str, Any]] = Field(default=None, description="에이전트 실행 결과")
	error: Optional[str] = Field(default=None, description="에러 메시지")
	session_id: Optional[str] = Field(default=None, description="대화 세션 ID")
	context: dict[str, Any] = Field(default_factory=dict, description="추가 컨텍스트 정보")


def classify(state: ConversationState) -> ConversationState:
	"""LLM 기반 의도 분류 (키워드 fallback 포함)."""
	t = state.query.strip()
	import re
	
	# 숫자만 입력된 경우: 이전 이메일 검색 결과가 있으면 QA로 라우팅 (이메일 상세 보기)
	if re.fullmatch(r"\d+", t):
		from .email_search_agent import has_pending_email_results
		if has_pending_email_results():
			state.route = "qa"
			return state
	
	# 숫자 + "번", "상세", "선택" 등이 포함된 경우도 QA로 라우팅
	if re.match(r"^\d+", t) and any(k in t for k in ["번", "상세", "선택"]):
		from .email_search_agent import has_pending_email_results
		if has_pending_email_results():
			state.route = "qa"
			return state
	
	# 이메일 검색 의도 우선 체크 (QA로 라우팅)
	# "이메일에서 ... 찾아줘", "메일에서 ... 찾아줘" 같은 패턴은 이메일 검색
	if any(phrase in t for phrase in ["메일에서", "이메일에서"]) or \
	   (any(k in t for k in ["메일", "이메일"]) and any(v in t for v in ["찾아", "찾아줘", "검색", "search", "find"])):
		state.route = "qa"
		return state
	
	# 잔여 연차 조회 우선 체크 (schedule로 라우팅)
	# "연차 얼마나", "연차 남았", "잔여 연차" 등은 schedule로 라우팅
	balance_keywords = ["잔여", "남았", "남은", "얼마", "몇 개", "몇 일", "현황"]
	leave_keywords = ["연차", "반차", "병가", "경조사", "휴가"]
	has_balance = any(k in t for k in balance_keywords)
	has_leave = any(k in t for k in leave_keywords)
	
	# 잔여 연차 조회인 경우 schedule로 라우팅
	if has_balance and has_leave:
		state.route = "schedule"
		return state
	
	# 이름 + 연차/잔여 패턴도 schedule로 라우팅 (예: "강하은 연차 얼마나 남았어?")
	if re.search(r"[가-힣]{2,4}\s*(?:님|의)?\s*(?:연차|잔여|남았|남은|얼마)", t):
		state.route = "schedule"
		return state
	
	# 복리후생 관련 질문 우선 체크 (QA로 라우팅)
	# "휴가·근무제도", "근무제도", "급여 외 혜택", "회사 복지" 등은 복리후생 정책 질문
	benefit_keywords = [
		"복리후생", "근무제도", "급여 외 혜택", "회사 복지", "복지 정책", "복지 제도",
		"휴가규정", "근무 규정", "근무 제도", "휴가 제도", "휴가 규정",
		"보험", "수당", "퇴직연금", "건강보험", "연금", "보상", "복지 혜택"
	]
	# 복리후생 관련 질문인지 확인 (정책/제도에 대한 질문인지)
	# 단, "연차 얼마나", "연차 남았" 같은 잔여 조회는 제외
	if (any(k in t for k in benefit_keywords) or \
	   ("휴가" in t and ("제도" in t or "규정" in t or "정책" in t or "알려" in t or "알려줘" in t)) or \
	   ("근무" in t and ("제도" in t or "규정" in t or "정책" in t or "알려" in t or "알려줘" in t))) and \
	   not (has_balance and has_leave):  # 잔여 연차 조회는 제외
		state.route = "qa"
		return state
	
	# LLM 기반 라우팅 시도
	llm_route = classify_intent_with_llm(t, route_type="supervisor", fallback_route=None)
	
	if llm_route and llm_route != "unknown":
		state.route = llm_route  # type: ignore
		return state
	
	# LLM 실패 시 키워드 기반 fallback
	# 주의: "휴가"는 복리후생 질문일 수도 있고 연차 신청일 수도 있으므로, 
	# "연차 신청", "반차 신청" 같은 명확한 액션 키워드가 있을 때만 schedule로 라우팅
	# "연차 잔여", "연차 조회", "연차 얼마나", "연차 남았" 같은 조회 의도도 schedule로 라우팅
	# "일정", "스케줄", "캘린더" 키워드가 있으면 schedule로 라우팅 (이름 + 일정 패턴 포함)
	schedule_keywords = [
		"연차 신청", "반차 신청", "병가 신청", "경조사 신청",
		"연차 잔여", "연차 조회", "반차 조회", "병가 조회",
		"연차 얼마나", "연차 남았", "연차 남은", "연차 몇 개", "연차 몇 일",
		"잔여 연차", "남은 연차", "연차 현황",
		"회의실", "예약", "일정", "스케줄", "캘린더"
	]
	if any(k in t for k in schedule_keywords):
		state.route = "schedule"
	# 이름 + 일정 패턴도 schedule로 라우팅 (예: "홍길동 일정 알려줘")
	elif re.search(r"[가-힣]{2,4}\s*(?:님|의)?\s*(?:일정|스케줄|캘린더)", t):
		state.route = "schedule"
	# 이름 + 연차/잔여 패턴도 schedule로 라우팅 (예: "강하은 연차 얼마나 남았어?")
	elif re.search(r"[가-힣]{2,4}\s*(?:님|의)?\s*(?:연차|잔여|남았|남은|얼마)", t):
		state.route = "schedule"
	# 이메일 자동응답: "이메일 자동응답", "메일 자동응답" 같은 명확한 키워드만 document로
	elif any(k in t for k in ["이메일 자동응답", "메일 자동응답", "자동응답"]) or \
	     (any(k in t for k in ["문서", "보고서", "작성", "템플릿"]) and not any(v in t for v in ["찾아", "찾아줘", "검색"])):
		state.route = "document"
	elif any(k in t for k in ["Q&A", "질문", "문의", "메뉴얼", "복리후생", "보험", "수당", "식단", "메뉴", "구내식당"]):
		state.route = "qa"
	elif any(k in t for k in ["특허", "논문", "KISTI", "ScienceON"]):
		state.route = "tech"
	else:
		state.route = "unknown"
	return state


def run_schedule(state: ConversationState) -> ConversationState:
	"""Schedule 에이전트 실행 (LangGraph 기반)."""
	try:
		state.result = schedule_master(state.query)
		if not state.result:
			state.result = {"ok": False, "message": "일정 조회에 실패했습니다."}
	except Exception as e:
		state.error = f"일정 에이전트 실행 오류: {e}"
		state.result = {"ok": False, "error": state.error}
	return state


def run_document(state: ConversationState) -> ConversationState:
	"""Document 에이전트 실행."""
	try:
		state.result = document_master(state.query)
		if not state.result:
			state.result = {"ok": False, "message": "문서 처리에 실패했습니다."}
	except Exception as e:
		state.error = f"문서 에이전트 실행 오류: {e}"
		state.result = {"ok": False, "error": state.error}
	return state


def run_qa(state: ConversationState) -> ConversationState:
	"""QA 에이전트 실행."""
	try:
		state.result = qa_master(state.query)
		if not state.result:
			state.result = {"ok": False, "message": "질문 처리에 실패했습니다."}
	except Exception as e:
		state.error = f"QA 에이전트 실행 오류: {e}"
		state.result = {"ok": False, "error": state.error}
	return state


def run_tech(state: ConversationState) -> ConversationState:
	"""Tech 에이전트 실행."""
	if not TECH_AVAILABLE or tech_master is None:
		state.result = {
			"ok": False,
			"message": "기술 에이전트를 사용할 수 없습니다. (의존성 누락: xmltodict 등)",
			"error": "Tech agent not available",
		}
		return state
	
	try:
		# session_id는 state에서 가져오거나 전역 메모리에서 가져옴
		session_id = state.session_id or None
		state.result = tech_master(state.query, session_id=session_id)
		if not state.result:
			state.result = {"ok": False, "message": "기술 검색에 실패했습니다."}
	except Exception as e:
		state.error = f"기술 에이전트 실행 오류: {e}"
		state.result = {"ok": False, "error": state.error}
	return state


def handle_unknown(state: ConversationState) -> ConversationState:
	"""알 수 없는 요청 처리."""
	state.result = {
		"ok": False,
		"message": "요청을 이해하지 못했습니다. 다음 중 하나로 요청해 주세요:\n"
		"- 일정/연차: '사번 4001 일정 알려줘', '연차 신청'\n"
		"- 문서/이메일: '이메일 자동응답 생성', '보고서 작성'\n"
		"- Q&A: '복리후생 정책 알려줘', '식단 메뉴 알려줘'\n"
		"- 기술: '특허 검색', '논문 찾아줘'",
	}
	return state


def format_response(state: ConversationState) -> ConversationState:
	"""최종 응답 포맷팅 및 정리."""
	if state.result:
		# 에러가 있으면 메시지에 포함
		if state.error and "error" not in state.result:
			state.result["error"] = state.error
		# 세션 컨텍스트 저장 (필요시)
		if state.session_id:
			_CONVERSATION_MEMORY[state.session_id] = {
				"last_route": state.route,
				"last_result": state.result,
			}
	return state


def build_graph() -> StateGraph:
	"""에이전트 매니저먼트 챗봇의 LangGraph 워크플로우 구성."""
	graph = StateGraph(ConversationState)
	
	# 노드 추가
	graph.add_node("classify", classify)  # 의도 분류
	graph.add_node("schedule", run_schedule)  # 일정 에이전트
	graph.add_node("document", run_document)  # 문서 에이전트
	graph.add_node("qa", run_qa)  # QA 에이전트
	graph.add_node("tech", run_tech)  # 기술 에이전트
	graph.add_node("unknown", handle_unknown)  # 알 수 없는 요청 처리
	graph.add_node("format", format_response)  # 응답 포맷팅

	# 엣지 구성
	graph.add_edge(START, "classify")
	
	# 조건부 라우팅: classify 후 적절한 에이전트로 분기
	def router(state: ConversationState) -> str:
		"""라우팅 함수: 분류된 route에 따라 적절한 에이전트로 전달."""
		return state.route or "unknown"

	graph.add_conditional_edges(
		"classify",
		router,
		{
			"schedule": "schedule",
			"document": "document",
			"qa": "qa",
			"tech": "tech",
			"unknown": "unknown",
		},
	)
	
	# 모든 에이전트 실행 후 포맷팅 노드로 이동
	graph.add_edge("schedule", "format")
	graph.add_edge("document", "format")
	graph.add_edge("qa", "format")
	graph.add_edge("tech", "format")
	graph.add_edge("unknown", "format")
	
	# 포맷팅 후 종료
	graph.add_edge("format", END)
	
	return graph


def run_supervisor(query: str, session_id: Optional[str] = None) -> dict[str, Any]:
	"""에이전트 매니저먼트 챗봇 실행: LangGraph 기반 멀티 에이전트 오케스트레이션.
	
	이 함수는 사용자 질의를 받아 적절한 에이전트로 라우팅하고 실행합니다.
	각 에이전트는 독립적으로 작동하며, LangGraph를 통해 워크플로우가 관리됩니다.
	
	Args:
		query: 사용자 질의 문장
		session_id: 대화 세션 ID (멀티턴 대화 및 메모리 관리를 위해 사용)
		
	Returns:
		에이전트 실행 결과 딕셔너리:
		- ok: 성공 여부 (bool)
		- message: 응답 메시지 (str)
		- rows: 조회된 데이터 (list, optional)
		- error: 오류 메시지 (str, optional)
		
	Examples:
		>>> result = run_supervisor("사번 4001 일정 알려줘")
		>>> print(result["message"])
	"""
	try:
		workflow = build_graph().compile()
		state = ConversationState(query=query, session_id=session_id)
		out = workflow.invoke(state.model_dump())
		
		# 결과 추출
		if isinstance(out, dict):
			result = out.get("result") or {}
			# 상태 정보도 포함 (디버깅용)
			if session_id:
				result["session_id"] = session_id
			return result
		
		# Pydantic 모델 객체인 경우
		if hasattr(out, "result"):
			result = getattr(out, "result") or {}
			if session_id:
				result["session_id"] = session_id
			return result
		
		# 예외 상황
		return {
			"ok": False,
			"message": "에이전트 실행 결과를 가져올 수 없습니다.",
			"error": "Invalid workflow output",
		}
	except Exception as e:
		return {
			"ok": False,
			"message": f"에이전트 실행 중 오류가 발생했습니다: {e}",
			"error": str(e),
		}
