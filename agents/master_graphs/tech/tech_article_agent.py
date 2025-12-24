"""LangGraph 기반 ScienceON 서브 에이전트.

이 모듈은 통합 마스터 에이전트의 서브 에이전트로 사용될 수 있도록 설계되었습니다.
독립적으로 실행하거나, 상위 마스터 에이전트의 노드/서브 그래프로 통합 가능합니다.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import END, StateGraph

import urllib.parse
import re
import requests
from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from agents.master_graphs.tech.article_agent_module.state import (
    # ScienceONAgentState,
    OPENAI_TIMEOUT,
    HTTP_TIMEOUT,
    _get_int_env,
)
from agents.master_graphs.tech.article_agent_module.crawler import USER_AGENT
from agents.master_graphs.tech.article_agent_module.utils import (
    get_user_friendly_error_message as _get_user_friendly_error_message,
)
from agents.master_graphs.tech.article_agent_module.nodes import (
    analyze_input_node,
    check_db_node,
    ask_search_source_with_db_result,
    rag_answer_node,
    search_papers,
    generate_response,
)
from agents.state import OrchestratorState

load_dotenv()

LOGGER = logging.getLogger(__name__)

# ============================================================================
# 공통 유틸리티 함수들 (분리된 모듈에서 import됨)
# ============================================================================

def _should_continue(state: OrchestratorState) -> Literal["rag_answer", "ask_search_source_with_db", "search_papers", "generate_response"]:
    """check_db 이후 다음 액션 결정."""
    article_ctx = state.get("tech_search_context", {}).get("article", {})
    next_action = article_ctx.get("next_action", "generate_response")
    if next_action == "rag_answer":
        return "rag_answer"
    elif next_action == "ask_search_source_with_db":
        return "ask_search_source_with_db"
    elif next_action == "search_papers":
        return "search_papers"
    else:
        return "generate_response"


def route_after_ask_source_with_db(state: OrchestratorState) -> Literal["rag_answer", "search_papers", "ask_search_source_with_db", "generate_response"]:
    """ask_search_source_with_db 이후 다음 액션 결정."""
    article_ctx = state.get("tech_search_context", {}).get("article", {})
    next_action = article_ctx.get("next_action", "generate_response")
    LOGGER.debug(
        f"[라우팅 디버그] route_after_ask_source_with_db - 라우팅 결정: "
        f"next_action={next_action}, "
        f"search_source={article_ctx.get('search_source')}, "
        f"intent={article_ctx.get('intent')}, "
        f"query={state.get('query', '')[:50]}"
    )
    if next_action == "rag_answer":
        return "rag_answer"
    elif next_action == "search_papers":
        return "search_papers"
    elif next_action == "ask_search_source_with_db":
        return "ask_search_source_with_db"
    else:
        return "generate_response"


def build_scienceon_agent_graph() -> Any:
    """ScienceON 서브 에이전트 그래프를 생성한다."""
    workflow = StateGraph(OrchestratorState)
    
    # 노드 추가
    workflow.add_node("analyze_input", analyze_input_node)
    workflow.add_node("check_db", check_db_node)
    workflow.add_node("ask_search_source_with_db", ask_search_source_with_db_result)
    workflow.add_node("rag_answer", rag_answer_node)
    workflow.add_node("search_papers", search_papers)
    workflow.add_node("generate_response", generate_response)
    
    # 엣지 설정
    workflow.set_entry_point("analyze_input")
    
    def route_after_analyze(state: OrchestratorState) -> Literal["check_db", "search_papers", "ask_search_source_with_db", "rag_answer", "generate_response"]:
        article_ctx = state.get("tech_search_context", {}).get("article", {})
        next_action = article_ctx.get("next_action", "check_db")
        intent = article_ctx.get("intent", "unknown")
        LOGGER.debug(
            f"[라우팅 디버그] route_after_analyze - "
            f"next_action={next_action}, intent={intent}, "
            f"search_source={article_ctx.get('search_source')}, "
            f"results_count={len(article_ctx.get('results', []))}, "
            f"query={state.get('query', '')[:50]}"
        )
        if next_action == "rag_answer":
            return "rag_answer"
        elif next_action == "search_papers":
            return "search_papers"
        elif next_action == "ask_search_source_with_db":
            return "ask_search_source_with_db"
        elif next_action == "generate_response":
            return "generate_response"
        else:
            return "check_db"
    
    workflow.add_conditional_edges(
        "analyze_input",
        route_after_analyze,
        {
            "check_db": "check_db",
            "search_papers": "search_papers",
            "ask_search_source_with_db": "ask_search_source_with_db",
            "rag_answer": "rag_answer",
            "generate_response": "generate_response",
        },
    )
    
    # check_db -> rag_answer, ask_search_source_with_db, search_papers
    workflow.add_conditional_edges(
        "check_db",
        _should_continue,
        {
            "rag_answer": "rag_answer",
            "ask_search_source_with_db": "ask_search_source_with_db",
            "search_papers": "search_papers",
            "generate_response": "generate_response",
        },
    )
    
    # ask_search_source_with_db -> rag_answer, search_papers
    workflow.add_conditional_edges(
        "ask_search_source_with_db",
        route_after_ask_source_with_db,
        {
            "rag_answer": "rag_answer",
            "search_papers": "search_papers",
            "ask_search_source_with_db": "ask_search_source_with_db",
            "generate_response": "generate_response",
        },
    )
    
    # rag_answer -> generate_response
    workflow.add_edge("rag_answer", "generate_response")
    
    workflow.add_edge("search_papers", "generate_response")
    workflow.add_edge("generate_response", END)

    return workflow.compile()


# 전역 에이전트 그래프 인스턴스
SCIENCEON_AGENT_GRAPH = build_scienceon_agent_graph()


async def run_scienceon_agent(
    user_input: str,
    orchestrator_state: Optional[OrchestratorState] = None,
) -> OrchestratorState:
    """ScienceON 서브 에이전트를 실행한다.

    Args:
        user_input: 사용자 입력 문자열
        orchestrator_state: OrchestratorState 딕셔너리 (이전 대화 상태)

    Returns:
        OrchestratorState: 업데이트된 상태
    """
    # 입력 검증 및 전처리
    if not user_input or not isinstance(user_input, str):
        LOGGER.warning("빈 입력 또는 잘못된 입력 타입: %s", user_input)
        # 에러 상태 반환
        if orchestrator_state is None:
            orchestrator_state = {}
        orchestrator_state["query"] = str(user_input) if user_input else ""
        if "tech_search_context" not in orchestrator_state:
            orchestrator_state["tech_search_context"] = {}
        if "article" not in orchestrator_state["tech_search_context"]:
            orchestrator_state["tech_search_context"]["article"] = {}
        article_ctx = orchestrator_state["tech_search_context"]["article"]
        article_ctx["error"] = "입력이 없습니다."
        article_ctx["response"] = "검색어를 입력해 주세요."
        article_ctx["intent"] = "unknown"
        return orchestrator_state

    user_input = user_input.strip()
    if not user_input:
        if orchestrator_state is None:
            orchestrator_state = {}
        orchestrator_state["query"] = ""
        if "tech_search_context" not in orchestrator_state:
            orchestrator_state["tech_search_context"] = {}
        if "article" not in orchestrator_state["tech_search_context"]:
            orchestrator_state["tech_search_context"]["article"] = {}
        article_ctx = orchestrator_state["tech_search_context"]["article"]
        article_ctx["error"] = "입력이 비어있습니다."
        article_ctx["response"] = "검색어를 입력해 주세요."
        article_ctx["intent"] = "unknown"
        return orchestrator_state

    # 초기 상태 설정 (다른 에이전트들과 일관성 유지)
    if orchestrator_state is None:
        orchestrator_state = {}
    
    # user_input을 query에 설정
    orchestrator_state["query"] = user_input
    
    # tech_search_context.article 초기화
    if "tech_search_context" not in orchestrator_state:
        orchestrator_state["tech_search_context"] = {}
    if "article" not in orchestrator_state["tech_search_context"]:
        orchestrator_state["tech_search_context"]["article"] = {}
    
    # conversation_history 초기화
    if "conversation_history" not in orchestrator_state:
        orchestrator_state["conversation_history"] = []
    elif not isinstance(orchestrator_state.get("conversation_history"), list):
        orchestrator_state["conversation_history"] = []
    
    # messages 초기화
    if "messages" not in orchestrator_state:
        orchestrator_state["messages"] = []
    elif not isinstance(orchestrator_state.get("messages"), list):
        orchestrator_state["messages"] = []

    # 에이전트 실행 (LangGraph 기반)
    try:
        LOGGER.info("ScienceON 에이전트 실행 시작: user_input=%s", user_input[:100])
        # recursion_limit 설정 (무한 루프 방지)
        # 재시도 카운터(MAX_ASK_SOURCE_RETRIES=3)가 있어 정상 플로우는 최대 10-15단계
        # 30으로 설정하여 충분한 여유를 두되 무한 루프 시 빠르게 실패하도록 함
        final_state = await SCIENCEON_AGENT_GRAPH.ainvoke(
            orchestrator_state,
            config={"recursion_limit": 30}
        )
        article_ctx = final_state.get("tech_search_context", {}).get("article", {})
        LOGGER.info(
            "ScienceON 에이전트 실행 완료: intent=%s, results_count=%d",
            article_ctx.get("intent", "unknown"),
            len(article_ctx.get("results", [])),
        )
        return final_state
    except Exception as exc:
        LOGGER.exception("ScienceON 에이전트 실행 중 오류 발생")
        # 오류 발생 시 이전 상태 유지하면서 오류 정보 추가
        article_ctx = orchestrator_state.get("tech_search_context", {}).get("article", {})
        article_ctx["error"] = str(exc)
        article_ctx["response"] = _get_user_friendly_error_message(exc)
        article_ctx["intent"] = "unknown"
        article_ctx["next_action"] = "generate_response"
        return orchestrator_state


# ============================================================================
# 내보내기 (통합 마스터 에이전트에서 사용)
# ============================================================================

__all__ = [

    # 그래프 생성
    "build_scienceon_agent_graph",

    # 실행 함수
    "run_scienceon_agent",

    # 노드 함수들 (직접 사용 가능)
    "search_papers",
    "generate_response",
]


# 세션 별 LangGraph 상태 메모리 (TTL 캐시)
# 세션 데이터 구조: {session_id: (state, last_access_time)}
_ARTICLE_MEMORY: Dict[str, tuple[OrchestratorState, datetime]] = {}
_SESSION_TTL = timedelta(hours=_get_int_env("SESSION_TTL_HOURS", 24))  # 기본 24시간
_MAX_SESSIONS = _get_int_env("MAX_SESSIONS", 1000)  # 기본 최대 1000개 세션

# 세션 정리 성능 최적화를 위한 변수
_last_cleanup_time: Optional[float] = None
_cleanup_interval = 300  # 5분마다 정리 (초)
_session_memory_lock: Optional[asyncio.Lock] = None


def _get_session_memory_lock() -> asyncio.Lock:
    """세션 메모리 접근용 asyncio.Lock을 가져온다."""
    global _session_memory_lock
    if _session_memory_lock is None:
        _session_memory_lock = asyncio.Lock()
    return _session_memory_lock


def _should_cleanup() -> bool:
    """정리가 필요한지 확인 (주기적 정리)."""
    global _last_cleanup_time
    if _last_cleanup_time is None:
        return True
    return time.time() - _last_cleanup_time > _cleanup_interval


def _cleanup_expired_sessions() -> None:
    """만료된 세션을 정리한다 (주기적 정리로 최적화)."""
    global _ARTICLE_MEMORY, _last_cleanup_time
    
    # 주기적 정리 체크
    if not _should_cleanup():
        return
    
    now = datetime.now()
    expired_sessions = [
        session_id for session_id, (_, last_access) in _ARTICLE_MEMORY.items()
        if now - last_access > _SESSION_TTL
    ]
    
    if expired_sessions:
        for session_id in expired_sessions:
            del _ARTICLE_MEMORY[session_id]
            LOGGER.debug(f"만료된 세션 삭제: {session_id}")
    
    _last_cleanup_time = time.time()


def _enforce_max_sessions() -> None:
    """최대 세션 수를 초과하면 가장 오래된 세션부터 삭제 (LRU 방식, 최적화)."""
    global _ARTICLE_MEMORY
    if len(_ARTICLE_MEMORY) <= _MAX_SESSIONS:
        return
    
    # 정렬 최적화: 큰 데이터셋의 경우 heapq 사용 고려
    # 현재는 작은 데이터셋이므로 sorted 유지
    sorted_sessions = sorted(
        _ARTICLE_MEMORY.items(),
        key=lambda x: x[1][1]  # (state, last_access_time)의 last_access_time
    )
    
    # 초과분만큼 삭제
    excess_count = len(_ARTICLE_MEMORY) - _MAX_SESSIONS
    for session_id, _ in sorted_sessions[:excess_count]:
        del _ARTICLE_MEMORY[session_id]
        LOGGER.debug(f"최대 세션 수 초과로 세션 삭제: {session_id}")


async def _get_session_state(session_id: Optional[str]) -> Optional[OrchestratorState]:
    """세션 상태를 가져온다 (접근 시간 업데이트, 동시성 안전)."""
    if not session_id:
        return None
    
    lock = _get_session_memory_lock()
    async with lock:
        _cleanup_expired_sessions()
        
        if session_id in _ARTICLE_MEMORY:
            data = _ARTICLE_MEMORY[session_id]
            # 호환성 처리: 기존 형식(state)과 새 형식((state, datetime)) 모두 처리
            if isinstance(data, tuple) and len(data) == 2:
                state, _ = data
            else:
                # 기존 형식인 경우 새 형식으로 변환
                state = data
                _ARTICLE_MEMORY[session_id] = (state, datetime.now())
                LOGGER.debug(f"기존 세션 데이터 형식 변환: {session_id}")
            
            # 접근 시간 업데이트
            _ARTICLE_MEMORY[session_id] = (state, datetime.now())
            return state
        return None


async def _set_session_state(session_id: str, state: OrchestratorState) -> None:
    """세션 상태를 저장한다 (동시성 안전)."""
    lock = _get_session_memory_lock()
    async with lock:
        _cleanup_expired_sessions()
        _enforce_max_sessions()
        
        _ARTICLE_MEMORY[session_id] = (state, datetime.now())
        LOGGER.debug(f"세션 상태 저장: {session_id}")


def _search_url(keyword: str) -> str:
    base_url = "https://scienceon.kisti.re.kr/srch/selectPORSrchArticleList.do"
    q = urllib.parse.quote(keyword, safe="")
    return f"{base_url}?queryText={q}"

def _normalize_items_from_results(results: List[Dict[str, Any]], displayed_count: Optional[int] = None) -> List[Dict[str, Any]]:
    """검색 결과를 정규화하여 반환합니다."""
    items: List[Dict[str, Any]] = []
    
    for row in results:
        title = str(row.get("title") or "").strip()
        pdf_link = (row.get("pdf_link") or "").strip()
        detail_link = (row.get("detail_link") or row.get("link") or "").strip()
        if title:
            items.append({
                "title": title,
                "pdf_link": pdf_link or None,
                "detail_link": detail_link or None,
            })
    return items

def has_pending_article_results(
    session_id: Optional[str] = None,
    orchestrator_state: Optional[Dict[str, Any]] = None
) -> bool:
    """세션에 이전 논문 검색 결과가 저장되어 있는지 확인 (Redis 우선, 메모리 폴백).
    
    동기 함수로 구현 (llm_router.py에서 사용).
    
    Args:
        session_id: 세션 ID
        orchestrator_state: OrchestratorState 딕셔너리 (Redis 숏텀 메모리, 선택적)
    
    Returns:
        이전 검색 결과가 있으면 True, 없으면 False
    """
    # 1. Redis 숏텀 메모리에서 확인 (우선)
    if orchestrator_state and "tech_search_context" in orchestrator_state:
        tech_context = orchestrator_state.get("tech_search_context", {})
        article_context = tech_context.get("article", {})
        results = article_context.get("results", [])
        if results:
            return True
    
    # 2. 메모리 기반 세션 관리에서 확인 (하위 호환성)
    if session_id:
        import asyncio
        try:
            # asyncio.to_thread로 실행되는 스레드에서는 asyncio.run() 사용 가능
            st = asyncio.run(_get_session_state(session_id))
            if st and isinstance(st, dict):
                results = st.get("results", [])
                if results and len(results) > 0:
                    return True
        except RuntimeError:
            # 이미 실행 중인 이벤트 루프가 있는 경우 (드물지만 안전을 위해)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    lambda: asyncio.run(_get_session_state(session_id))
                )
                st = future.result()
                if st and isinstance(st, dict):
                    results = st.get("results", [])
                    if results and len(results) > 0:
                        return True
    
    return False

async def article_master(
    query: str, 
    session_id: Optional[str] = None,
    orchestrator_state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """LangGraph 기반 논문 검색/다운로드 마스터 엔트리.

    - 세션 상태를 복원/저장한다 (Redis 숏텀 메모리 사용).
    - 숫자만 입력되었고, 이전 결과가 있으면 다운로드 명령으로 해석한다.
    - 에이전트 사용 불가 시 폴백으로 검색만 수행한다.
    
    Args:
        query: 사용자 검색 쿼리
        session_id: 세션 ID (멀티턴 대화 지원, 참고용)
        orchestrator_state: OrchestratorState 딕셔너리 (Redis 숏텀 메모리 통합용, 필수)
    
    Returns:
        검색 결과 딕셔너리
    
    Note:
        메모리 기반 세션 관리(_ARTICLE_MEMORY)는 제거되었습니다.
        Redis 기반 세션 관리(tech_search_context)만 사용합니다.
        독립 실행 시나리오는 TechArticleSession 클래스를 사용하세요.
    """
    t = (query or "").strip()
    
    try:
        # orchestrator_state 초기화
        if orchestrator_state is None:
            orchestrator_state = {}
        
        # query 설정
        orchestrator_state["query"] = t
        orchestrator_state["session_id"] = session_id
        
        # tech_search_context.article 초기화
        if "tech_search_context" not in orchestrator_state:
            orchestrator_state["tech_search_context"] = {}
        if not isinstance(orchestrator_state.get("tech_search_context"), dict):
            orchestrator_state["tech_search_context"] = {}
        
        if "article" not in orchestrator_state["tech_search_context"]:
            orchestrator_state["tech_search_context"]["article"] = {}
        elif not isinstance(orchestrator_state["tech_search_context"].get("article"), dict):
            orchestrator_state["tech_search_context"]["article"] = {}
        
        # conversation_history 초기화
        if "conversation_history" not in orchestrator_state:
            orchestrator_state["conversation_history"] = []
        elif not isinstance(orchestrator_state.get("conversation_history"), list):
            orchestrator_state["conversation_history"] = []
        
        # messages 초기화
        if "messages" not in orchestrator_state:
            orchestrator_state["messages"] = []
        elif not isinstance(orchestrator_state.get("messages"), list):
            orchestrator_state["messages"] = []
        
        # 에이전트 실행 (OrchestratorState 직접 사용)
        final_state = await SCIENCEON_AGENT_GRAPH.ainvoke(
            orchestrator_state,
            config={"recursion_limit": 30}
        )
        
        # 결과 추출
        article_ctx = final_state.get("tech_search_context", {}).get("article", {})
        results = article_ctx.get("results", [])
        response = article_ctx.get("response", "")
        summary = article_ctx.get("summary", "")
        intent = article_ctx.get("intent", "unknown")
        error = article_ctx.get("error")

        # generate_response에서 만든 구조화된 search_results 가져오기 (우선 사용)
        structured_search_results = final_state.get("search_results")

        # None 체크 추가
        if structured_search_results is None:
            structured_search_results = []

        # 정규화된 응답 구성 (하위 호환성을 위해 유지)
        items = _normalize_items_from_results(results, displayed_count=len(results))

        LOGGER.debug(
            f"[article_master] 실행 완료: intent={intent}, results_count={len(results)}, "
            f"response_length={len(response)}, error={error is not None}, "
            f"structured_results_count={len(structured_search_results) if structured_search_results else 0}"
        )
        
        return {
            "ok": error is None,
            "intent": intent,
            "message": response if response else (summary or ""),
            "summary": summary,
            "link": _search_url(query),
            "items": structured_search_results if structured_search_results else items,  # ← 구조화된 결과 우선 사용
            "results": results,
        }
    except Exception as exc:
        LOGGER.exception(f"article_master 실행 중 오류: {exc}")
        # 폴백: 검색만 수행
        refined_keyword = query
        try:
            # final_state가 이미 얻어진 경우 정제된 키워드 추출 시도
            if 'final_state' in locals():
                article_ctx = final_state.get("tech_search_context", {}).get("article", {})
                refined_keyword = article_ctx.get("keyword", query)
        except:
            pass
        fb = await article_search(refined_keyword)
        fb["message"] = f"{fb.get('message', '')} (agent 연동 실패로 폴백: {exc})"
        return fb

async def article_search(keyword: str) -> Dict:
    """ScienceON 논문 검색(에이전트 연동, 실패 시 HTML 폴백).

    우선 `agent.py`의 `run_scienceon_agent`를 호출하여
    외부 API/크롤러 기반의 정확한 검색을 수행한다.
    만약 런타임 환경 또는 의존성 문제로 에이전트 호출이 불가하면,
    기존 HTML 스크래핑 기반의 간이 검색으로 폴백한다.

    Args:
        keyword: 검색 키워드(자연어 쿼리 가능)

    Returns:
        Dict: 다음 형태의 결과 딕셔너리
            - ok (bool): 성공 여부
            - message (str): 사용자 메시지
            - link (str): 검색 페이지 링크(참고용)
            - items (List[Dict]): 간략 결과 목록
                - title (str): 제목
                - link (str): 원문/상세 또는 PDF 링크(가능 시)
                - pdf_link (Optional[str]): PDF 링크(가능 시)
    """
    # 1) 에이전트 경로 (정확/풍부한 결과)
    agent_error: Optional[str] = None
    try:
        orchestrator_state: Optional[OrchestratorState] = None
        # 타임아웃 설정 추가 (더 긴 타임아웃)
        result_state = await asyncio.wait_for(
            run_scienceon_agent(user_input=keyword, orchestrator_state=orchestrator_state),
            timeout=OPENAI_TIMEOUT * 3  # 에이전트 전체 실행 타임아웃 증가: 2배 -> 3배
        )
        article_ctx = result_state.get("tech_search_context", {}).get("article", {})
        results: List[Dict[str, Any]] = article_ctx.get("results", [])
        summary: str = article_ctx.get("summary", "") or ""
        displayed_count = len(results)

        # items 정규화: agent.py 결과 필드를 그대로 보존한다
        items = _normalize_items_from_results(results, displayed_count=displayed_count)

        # 결과가 있으면 성공 반환
        if items:
            return {
                "ok": True,
                "message": summary
                or f"논문 검색 결과(키워드: {keyword})입니다. 상세는 검색 페이지를 참고하세요.",
                "link": _search_url(keyword),
                "items": items,
            }
        else:
            # 결과가 없으면 폴백으로 진행
            agent_error = "검색 결과가 없습니다"
            LOGGER.warning(f"에이전트 실행 완료했지만 검색 결과가 없음 (키워드: {keyword})")
    except asyncio.TimeoutError:
        agent_error = "검색 시간이 초과되었습니다"
        LOGGER.warning(f"에이전트 실행 타임아웃 (키워드: {keyword})")
        # 폴백으로 이어감
    except PlaywrightTimeoutError:
        agent_error = "ScienceON 페이지 로딩 시간이 초과되었습니다"
        LOGGER.warning(f"Playwright 타임아웃 (키워드: {keyword})")
        # 폴백으로 이어감
    except Exception as e:
        agent_error = f"검색 중 오류가 발생했습니다: {str(e)[:100]}"
        LOGGER.warning(f"에이전트 실행 실패: {e}")
        # 폴백으로 이어감

    # 2) 폴백: 간단 HTML 스크래핑(기존 동작 유지)
    url = _search_url(keyword)
    items: List[Dict] = []
    fallback_success = False
    try:
        # 타임아웃 증가: 10초 -> 20초
        resp = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 더 정확한 셀렉터 사용
        # 검색 결과 링크 찾기
        result_links = soup.find_all("a", href=re.compile(r"/srch/selectPORSrchArticleList\.do|/srch/selectPORSrchArticle\.do"))
        for link in result_links:
            title = link.get_text(strip=True)
            href = link.get("href") or ""
            if not title or len(title) < 5:  # 너무 짧은 제목 제외
                continue
            
            # 상대 경로를 절대 경로로 변환
            if href.startswith("/"):
                full_url = urllib.parse.urljoin("https://scienceon.kisti.re.kr", href)
            else:
                full_url = urllib.parse.urljoin(url, href)
            
            items.append({
                "title": title,
                "pdf_link": None,
                "detail_link": full_url,
            })
            if len(items) >= 10:  # 결과 개수 증가: 5개 -> 10개
                break
        
        # PDF 링크도 별도로 찾기
        pdf_links = soup.find_all("a", href=re.compile(r"\.pdf", re.IGNORECASE))
        for pdf_link in pdf_links[:5]:
            title = pdf_link.get_text(strip=True) or "PDF 문서"
            href = pdf_link.get("href") or ""
            if href:
                if href.startswith("/"):
                    full_url = urllib.parse.urljoin("https://scienceon.kisti.re.kr", href)
                else:
                    full_url = urllib.parse.urljoin(url, href)
                
                items.append({
                    "title": title,
                    "pdf_link": full_url,
                    "detail_link": None,
                })
        
        fallback_success = len(items) > 0
    except requests.exceptions.Timeout:
        LOGGER.warning(f"HTML 스크래핑 타임아웃 (키워드: {keyword})")
    except Exception as e:
        LOGGER.warning(f"HTML 스크래핑 실패: {e}")

    # 결과 메시지 구성
    if fallback_success:
        message = f"논문 검색 결과(키워드: {keyword})입니다. "
        if agent_error:
            message += f"(고급 검색 실패, 기본 검색 결과 제공) "
        message += "상세는 검색 페이지를 참고하세요."
    else:
        message = f"논문 검색 중 문제가 발생했습니다. "
        if agent_error:
            message += f"{agent_error} "
        message += f"다음 링크에서 직접 검색해 주세요: {url}"

    return {
        "ok": fallback_success or len(items) > 0,
        "message": message,
        "link": url,
        "items": items,
    }


class TechArticleSession:
    """(독립 실행용) 에이전트 상태를 유지하며 검색/다운로드를 보조하는 세션 래퍼.

    - 가능한 경우 `agent.py`를 활용하여 고품질 검색/다운로드를 수행한다.
    - 환경 제약으로 사용할 수 없을 경우 검색은 HTML 폴백을 제공하고,
      다운로드는 사용 불가 메시지를 반환한다.
    """

    def __init__(self, session_id: Optional[str] = None) -> None:
        self._state: Optional[OrchestratorState] = None
        self._session_id = session_id

    async def search(self, query: str) -> Dict[str, Any]:
        """검색 수행. 내부 상태가 최신 결과로 갱신된다."""
        try:
            # 세션 메모리와 동기화
            if self._session_id and self._state is None:
                self._state = await _get_session_state(self._session_id)
            # 파라미터 이름 수정
            self._state = await run_scienceon_agent(user_input=query, orchestrator_state=self._state)
            if self._session_id:
                await _set_session_state(self._session_id, self._state)
            # OrchestratorState에서 결과 추출
            article_ctx = self._state.get("tech_search_context", {}).get("article", {})
            results = article_ctx.get("results", [])
            items = _normalize_items_from_results(results, displayed_count=len(results))
            return {
                "ok": True,
                "message": article_ctx.get("summary") or f"논문 검색 결과(키워드: {query})입니다.",
                "link": _search_url(query),
                "items": items,
            }
        except Exception as exc:
            # 에이전트 실패 시 폴백
            refined_keyword = query
            try:
                if self._state:
                    article_ctx = self._state.get("tech_search_context", {}).get("article", {})
                    refined_keyword = article_ctx.get("keyword", query)
            except:
                pass
            fb = await article_search(refined_keyword)
            fb["message"] = f"{fb.get('message', '')} (agent 연동 실패로 폴백: {exc})"
            return fb
    
    async def download(self, ordinal: int) -> Dict[str, Any]:
        """검색 결과의 N번째(1-base) 논문 PDF 다운로드 시도."""
        if not isinstance(ordinal, int) or ordinal <= 0:
            return {"ok": False, "message": "순번은 1 이상의 정수여야 합니다."}
        try:
            command = f"{ordinal}번째 논문 pdf 다운받아줘"
            if self._session_id and self._state is None:
                self._state = await _get_session_state(self._session_id)
            # 파라미터 이름 수정
            self._state = await run_scienceon_agent(user_input=command, orchestrator_state=self._state)
            if self._session_id:
                await _set_session_state(self._session_id, self._state)
            article_ctx = self._state.get("tech_search_context", {}).get("article", {})
            response = article_ctx.get("response", "")
            error = article_ctx.get("error")
            ok = error is None
            results = article_ctx.get("results", [])
            items = _normalize_items_from_results(results, displayed_count=len(results))
            return {
                "ok": ok,
                "message": response if ok else error,
                "items": items,
            }
        except Exception as exc:
            return {"ok": False, "message": f"다운로드 중 오류가 발생했습니다: {exc}"}
    
    def get_state(self) -> Optional[OrchestratorState]:
        """내부 에이전트 상태를 반환한다."""
        return self._state








