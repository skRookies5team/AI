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
from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

import urllib.parse
import requests
from bs4 import BeautifulSoup

from agents.master_graphs.tech.article_agent_module.state import (
    ScienceONAgentState,
    OPENAI_TIMEOUT,
    HTTP_TIMEOUT,
    _get_int_env,
)
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

load_dotenv()

LOGGER = logging.getLogger(__name__)

# ============================================================================
# 공통 유틸리티 함수들 (분리된 모듈에서 import됨)
# ============================================================================

def _should_continue(state: ScienceONAgentState) -> Literal["rag_answer", "ask_search_source_with_db", "search_papers", "generate_response"]:
    """check_db 이후 다음 액션 결정."""
    next_action = state.get("next_action", "generate_response")
    if next_action == "rag_answer":
        return "rag_answer"
    elif next_action == "ask_search_source_with_db":
        return "ask_search_source_with_db"
    elif next_action == "search_papers":
        return "search_papers"
    else:
        return "generate_response"


def route_after_ask_source_with_db(state: ScienceONAgentState) -> Literal["rag_answer", "search_papers", "ask_search_source_with_db", "generate_response"]:
    """ask_search_source_with_db 이후 다음 액션 결정."""
    next_action = state.get("next_action", "generate_response")
    # 디버그: 라우팅 결정 시 state 상태 로깅
    LOGGER.debug(
        f"[라우팅 디버그] route_after_ask_source_with_db - 라우팅 결정: "
        f"next_action={next_action}, "
        f"search_source={state.get('search_source')}, "
        f"intent={state.get('intent')}, "
        f"user_input={state.get('user_input', '')[:50]}"
    )
    if next_action == "rag_answer":
        LOGGER.debug("[라우팅 디버그] route_after_ask_source_with_db - 라우팅 결정: rag_answer")
        return "rag_answer"
    elif next_action == "search_papers":
        LOGGER.debug("[라우팅 디버그] route_after_ask_source_with_db - 라우팅 결정: search_papers")
        return "search_papers"
    elif next_action == "ask_search_source_with_db":
        LOGGER.debug("[라우팅 디버그] route_after_ask_source_with_db - 라우팅 결정: ask_search_source_with_db")
        return "ask_search_source_with_db"
    else:
        LOGGER.debug("[라우팅 디버그] route_after_ask_source_with_db - 라우팅 결정: generate_response (기본값)")
        return "generate_response"


def build_scienceon_agent_graph() -> Any:
    """ScienceON 서브 에이전트 그래프를 생성한다.

    Returns:
        CompiledGraph: 컴파일된 LangGraph 그래프.
        통합 마스터 에이전트의 서브 그래프로 사용 가능합니다.
    """
    workflow = StateGraph(ScienceONAgentState)

    # 노드 추가
    workflow.add_node("analyze_input", analyze_input_node)
    workflow.add_node("check_db", check_db_node)
    workflow.add_node("ask_search_source_with_db", ask_search_source_with_db_result)
    workflow.add_node("rag_answer", rag_answer_node)
    workflow.add_node("search_papers", search_papers)
    workflow.add_node("generate_response", generate_response)

    # 엣지 설정
    workflow.set_entry_point("analyze_input")
    
    # analyze_input -> check_db, search_papers, ask_search_source_with_db, rag_answer, generate_response
    def route_after_analyze(state: ScienceONAgentState) -> Literal["check_db", "search_papers", "ask_search_source_with_db", "rag_answer", "generate_response"]:
        next_action = state.get("next_action", "check_db")
        intent = state.get("intent", "unknown")
        LOGGER.debug(
            f"[라우팅 디버그] route_after_analyze - "
            f"next_action={next_action}, intent={intent}, "
            f"search_source={state.get('search_source')}, "
            f"results_count={len(state.get('results', []))}, "
            f"user_input={state.get('user_input', '')[:50]}"
        )
        if next_action == "rag_answer":
            LOGGER.debug(f"[라우팅 디버그] 라우팅 결정: rag_answer")
            return "rag_answer"
        elif next_action == "search_papers":
            LOGGER.debug(f"[라우팅 디버그] 라우팅 결정: search_papers")
            return "search_papers"
        elif next_action == "ask_search_source_with_db":
            LOGGER.debug(f"[라우팅 디버그] 라우팅 결정: ask_search_source_with_db")
            return "ask_search_source_with_db"
        elif next_action == "generate_response":
            LOGGER.debug(f"[라우팅 디버그] 라우팅 결정: generate_response")
            return "generate_response"
        else:
            LOGGER.debug(f"[라우팅 디버그] 라우팅 결정: check_db (기본값)")
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
    state: Optional[ScienceONAgentState] = None,
) -> ScienceONAgentState:
    """ScienceON 서브 에이전트를 실행한다.

    통합 마스터 에이전트의 노드에서 호출하거나 독립적으로 실행 가능합니다.
    사용자 채팅 입력을 기반으로 논문 검색 및 PDF 다운로드를 수행합니다.

    Args:
        user_input (str): 사용자의 자연어 입력.
            - 검색 예시: "인공지능 논문 찾아줘", "디스플레이 관련 논문 검색"
            - 다운로드 예시: "2번째 논문 pdf 다운받아줘", "첫 번째 논문 다운로드"
        state (Optional[ScienceONAgentState]): 이전 대화 상태 (세션 메모리).

    Returns:
        ScienceONAgentState: 업데이트된 상태.
            - intent: "search" 또는 "download"
            - results: 검색 결과 목록 (각 항목에 title, pdf_link, detail_link 포함)
            - response: 사용자에게 보여줄 응답 메시지
            - summary: AI 요약 (검색 시)
            - error: 오류 메시지 (있을 경우)

    Example:
        >>> # 독립 실행
        >>> result = run_scienceon_agent("인공지능 논문 찾아줘")
        >>> print(result["response"])
        >>> print(f"검색 결과: {len(result['results'])}건")

        >>> # 세션 상태 유지하며 멀티턴 대화
        >>> session_state = None
        >>> session_state = run_scienceon_agent("대규모 언어모델 논문 찾아줘", session_state)
        >>> session_state = run_scienceon_agent("2", session_state)  # 두 번째 논문 다운로드

        >>> # 통합 마스터 에이전트의 노드에서 사용
        >>> def scienceon_node(master_state):
        ...     user_input = master_state.get("user_input", "")
        ...     scienceon_state = extract_scienceon_state(master_state)
        ...     result = run_scienceon_agent(user_input, scienceon_state)
        ...     return merge_to_master_state(master_state, result)
    """
    # 입력 검증 및 전처리
    if not user_input or not isinstance(user_input, str):
        LOGGER.warning("빈 입력 또는 잘못된 입력 타입: %s", user_input)
        return {
            "messages": state.get("messages", []) if state else [],
            "user_input": str(user_input) if user_input else "",
            "intent": "unknown",
            "query": "",
            "download_index": [],
            "keyword": "",
            "results": state.get("results", []) if state else [],
            "summary": "",
            "response": "검색어를 입력해 주세요.",
            "error": "입력이 없습니다.",
            "search_source": state.get("search_source", "unknown") if state else "unknown",
            "displayed_count": state.get("displayed_count", 0) if state else 0,
        }

    user_input = user_input.strip()
    if not user_input:
        return {
            "messages": state.get("messages", []) if state else [],
            "user_input": "",
            "intent": "unknown",
            "query": "",
            "download_index": [],
            "keyword": "",
            "results": state.get("results", []) if state else [],
            "summary": "",
            "response": "검색어를 입력해 주세요.",
            "error": "입력이 비어있습니다.",
            "search_source": state.get("search_source", "unknown") if state else "unknown",
            "displayed_count": state.get("displayed_count", 0) if state else 0,
        }

    # 초기 상태 설정 (이전 상태가 있으면 복사, 없으면 새로 생성)
    initial_state: ScienceONAgentState = copy.deepcopy(state) if state else {
        "messages": [],
        "user_input": "",
        "intent": "unknown",
        "query": "",
        "download_index": [],
        "keyword": "",
        "results": [],
        "summary": "",
        "response": "",
        "error": None,
        "search_source": "unknown",
        "displayed_count": 0,
        "vector_db_results": [],
        "next_action": "check_db",
        "ask_source_retry_count": 0,
        "previous_query": "",
        "last_selected_paper_index": -1,
    }

    # 기존 state에서 새 필드가 없으면 기본값 설정
    if "search_source" not in initial_state:
        initial_state["search_source"] = "unknown"
    if "displayed_count" not in initial_state:
        initial_state["displayed_count"] = 0
    if "vector_db_results" not in initial_state:
        initial_state["vector_db_results"] = []
    if "next_action" not in initial_state:
        initial_state["next_action"] = "check_db"
    if "ask_source_retry_count" not in initial_state:
        initial_state["ask_source_retry_count"] = 0
    if "previous_query" not in initial_state:
        initial_state["previous_query"] = ""
    if "last_selected_paper_index" not in initial_state:
        initial_state["last_selected_paper_index"] = -1

    # 사용자 입력을 메시지에 추가
    messages = initial_state.get("messages", [])
    messages.append(HumanMessage(content=user_input))
    initial_state["messages"] = messages
    initial_state["user_input"] = user_input

    # 에이전트 실행 (LangGraph 기반)
    try:
        LOGGER.info("ScienceON 에이전트 실행 시작: user_input=%s", user_input[:100])
        # recursion_limit 설정 (무한 루프 방지)
        # 재시도 카운터(MAX_ASK_SOURCE_RETRIES=3)가 있어 정상 플로우는 최대 10-15단계
        # 30으로 설정하여 충분한 여유를 두되 무한 루프 시 빠르게 실패하도록 함
        final_state = await SCIENCEON_AGENT_GRAPH.ainvoke(
            initial_state,
            config={"recursion_limit": 30}
        )
        LOGGER.info(
            "ScienceON 에이전트 실행 완료: intent=%s, results_count=%d",
            final_state.get("intent"),
            len(final_state.get("results", [])),
        )
        return final_state
    except Exception as exc:
        LOGGER.exception("ScienceON 에이전트 실행 중 오류 발생")
        # 오류 발생 시 이전 상태 유지하면서 오류 정보 추가
        error_state: ScienceONAgentState = {
            "messages": initial_state.get("messages", []),
            "user_input": user_input,
            "intent": "unknown",
            "query": "",
            "download_index": [],
            "keyword": "",
            "results": initial_state.get("results", []),  # 이전 검색 결과 유지
            "summary": "",
            "response": _get_user_friendly_error_message(exc),
            "error": str(exc),  # 기술적 에러는 error 필드에만 저장
            "search_source": initial_state.get("search_source", "unknown"),
            "displayed_count": initial_state.get("displayed_count", 0),
            "vector_db_results": initial_state.get("vector_db_results", []),
            "next_action": "generate_response",
            "ask_source_retry_count": initial_state.get("ask_source_retry_count", 0),
            "previous_query": initial_state.get("previous_query", ""),
            "last_selected_paper_index": initial_state.get("last_selected_paper_index", -1),
        }
        return error_state


# ============================================================================
# 통합 마스터 에이전트를 위한 헬퍼 함수들
# ============================================================================


def create_scienceon_subgraph() -> Any:
    """통합 마스터 에이전트의 서브 그래프로 사용할 수 있는 그래프를 반환한다.

    Returns:
        CompiledGraph: 서브 그래프로 사용 가능한 컴파일된 그래프.

    Example:
        >>> from langgraph.graph import StateGraph
        >>> from agent import create_scienceon_subgraph
        >>>
        >>> # 통합 마스터 에이전트
        >>> master_graph = StateGraph(MasterState)
        >>>
        >>> # ScienceON 서브 그래프 추가
        >>> scienceon_subgraph = create_scienceon_subgraph()
        >>> master_graph.add_node("scienceon_agent", scienceon_subgraph)
    """
    return build_scienceon_agent_graph()


def extract_scienceon_state(master_state: Dict[str, Any]) -> Optional[ScienceONAgentState]:
    """통합 마스터 에이전트의 상태에서 ScienceON 에이전트 상태를 추출한다.

    Args:
        master_state: 통합 마스터 에이전트의 상태 딕셔너리.

    Returns:
        Optional[ScienceONAgentState]: 추출된 ScienceON 상태, 없으면 None.
    """
    # master_state에 scienceon 필드가 있는 경우
    if "scienceon" in master_state and isinstance(master_state["scienceon"], dict):
        return master_state["scienceon"]

    # master_state가 직접 ScienceON 상태를 포함하는 경우
    if all(key in master_state for key in ["user_input", "intent", "query"]):
        return {
            "messages": master_state.get("messages", []),
            "user_input": master_state.get("user_input", ""),
            "intent": master_state.get("intent", "unknown"),
            "query": master_state.get("query", ""),
            "download_index": master_state.get("download_index", []),
            "keyword": master_state.get("keyword", ""),
            "results": master_state.get("results", []),
            "summary": master_state.get("summary", ""),
            "response": master_state.get("response", ""),
            "error": master_state.get("error"),
        }

    return None


def merge_scienceon_state(
    master_state: Dict[str, Any],
    scienceon_state: ScienceONAgentState,
) -> Dict[str, Any]:
    """ScienceON 에이전트 상태를 통합 마스터 에이전트 상태에 병합한다.

    Args:
        master_state: 통합 마스터 에이전트의 상태 딕셔너리.
        scienceon_state: ScienceON 에이전트의 상태.

    Returns:
        Dict[str, Any]: 병합된 마스터 상태.
    """
    # scienceon 필드에 저장
    master_state["scienceon"] = scienceon_state

    # 또는 직접 필드로 병합 (선택사항)
    master_state.update({
        "scienceon_response": scienceon_state.get("response", ""),
        "scienceon_results": scienceon_state.get("results", []),
        "scienceon_summary": scienceon_state.get("summary", ""),
    })

    return master_state


def create_scienceon_node():
    """통합 마스터 에이전트의 노드로 사용할 수 있는 함수를 반환한다.

    Returns:
        Callable: 마스터 상태를 받아서 업데이트된 상태를 반환하는 함수.

    Example:
        >>> from langgraph.graph import StateGraph
        >>> from agent import create_scienceon_node
        >>>
        >>> master_graph = StateGraph(MasterState)
        >>> scienceon_node = create_scienceon_node()
        >>> master_graph.add_node("scienceon", scienceon_node)
    """
    async def scienceon_node(master_state: Dict[str, Any]) -> Dict[str, Any]:
        """ScienceON 서브 에이전트를 실행하는 노드 함수."""
        # 상태 추출
        scienceon_state = extract_scienceon_state(master_state)
        user_input = master_state.get("user_input", master_state.get("message", ""))

        # 에이전트 실행
        result_state = await run_scienceon_agent(user_input, scienceon_state)

        # 결과 병합
        return merge_scienceon_state(master_state, result_state)

    return scienceon_node


# ============================================================================
# 내보내기 (통합 마스터 에이전트에서 사용)
# ============================================================================

__all__ = [
    # 상태 타입
    "ScienceONAgentState",

    # 그래프 생성
    "build_scienceon_agent_graph",
    "create_scienceon_subgraph",

    # 실행 함수
    "run_scienceon_agent",

    # 통합 헬퍼
    "extract_scienceon_state",
    "merge_scienceon_state",
    "create_scienceon_node",

    # 노드 함수들 (직접 사용 가능)
    "search_papers",
    "generate_response",
]


# ============================================================================
# 터미널 실행 인터페이스
# ============================================================================

def _print_results(results: List[Dict[str, Any]]) -> None:
    """검색 결과를 터미널에 출력한다."""
    if not results:
        print("검색 결과가 없습니다.")
        return

    print(f"\n📄 검색 결과 ({len(results)}건):")
    print("=" * 80)
    for idx, result in enumerate(results, start=1):
        title = result.get("title", "제목 없음")
        pdf_link = result.get("pdf_link", "")
        pdf_path = result.get("pdf_path")

        print(f"\n{idx}. {title}")
        if pdf_link:
            print(f"   🔗 PDF 링크: {pdf_link}")
        if pdf_path:
            print(f"   💾 저장 위치: {pdf_path}")
    print("=" * 80)


def _print_response(state: ScienceONAgentState) -> None:
    """에이전트 응답을 터미널에 출력한다."""
    intent = state.get("intent", "unknown")
    response = state.get("response", "")
    summary = state.get("summary", "")
    results = state.get("results", [])
    error = state.get("error")
    keyword = state.get("keyword", "")
    query = state.get("query", "")

    print("\n" + "=" * 80)

    if error:
        print(f"❌ 오류: {error}")
    else:
        if intent == "search":
            if query:
                print(f"\n🔍 검색 질의: {query}")
            if keyword:
                print(f"🔑 검색 키워드: {keyword}")
            if summary:
                print(f"\n📝 AI 요약:\n{summary}")
            _print_results(results)
        else:
            print(f"\n{response or summary}")

    print("=" * 80 + "\n")


def main() -> None:
    """터미널에서 대화형으로 ScienceON 논문 검색 AI를 실행한다."""
    print("=" * 80)
    print("🔬 ScienceON 논문 검색 AI")
    print("=" * 80)
    print("사용법:")
    print("  - 검색: '인공지능 논문 찾아줘', '디스플레이 관련 논문 검색'")
    print("  - 다운로드: '2번째 논문 pdf 다운받아줘', '첫 번째 논문 다운로드'")
    print("  - 종료: 'quit', 'exit', '종료' 또는 Ctrl+C")
    print("=" * 80 + "\n")

    session_state: Optional[ScienceONAgentState] = None

    try:
        while True:
            try:
                user_input = input("💬 질문: ").strip()

                if not user_input:
                    continue

                # 종료 명령어
                if user_input.lower() in {"quit", "exit", "종료", "q"}:
                    print("\n👋 종료합니다.")
                    break

                # 에이전트 실행
                print("\n⏳ 처리 중...")
                session_state = asyncio.run(run_scienceon_agent(user_input, session_state))

                # 결과 출력
                _print_response(session_state)

            except KeyboardInterrupt:
                print("\n\n👋 종료합니다.")
                break
            except Exception as e:
                LOGGER.exception("오류 발생")
                print(f"\n❌ 오류: {e}\n")

    except KeyboardInterrupt:
        print("\n\n👋 종료합니다.")


if __name__ == "__main__":
    main()


# 현재 파일 내에 LangGraph 에이전트 구현과 실행 함수가 포함되어 있으므로,
# 별도 임포트 없이 사용 가능함을 표시

# 세션 별 LangGraph 상태 메모리 (TTL 캐시)
# 세션 데이터 구조: {session_id: (state, last_access_time)}
_ARTICLE_MEMORY: Dict[str, tuple[ScienceONAgentState, datetime]] = {}
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


async def _get_session_state(session_id: Optional[str]) -> Optional[ScienceONAgentState]:
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


async def _set_session_state(session_id: str, state: ScienceONAgentState) -> None:
    """세션 상태를 저장한다 (동시성 안전)."""
    lock = _get_session_memory_lock()
    async with lock:
        _cleanup_expired_sessions()
        _enforce_max_sessions()
        
        _ARTICLE_MEMORY[session_id] = (state, datetime.now())
        LOGGER.debug(f"세션 상태 저장: {session_id}")


async def is_waiting_for_article_search_source(session_id: Optional[str]) -> bool:
    """논문 검색에서 웹/사내 서버 선택 질문 대기 중인지 확인."""
    if not session_id:
        return False
    st = await _get_session_state(session_id)
    if not st or not isinstance(st, dict):
        return False
    # intent가 ask_source이거나, next_action이 generate_response이고 intent가 ask_source인 경우
    intent = st.get("intent", "")
    next_action = st.get("next_action", "")
    return intent == "ask_source" or (next_action == "generate_response" and intent == "ask_source")


def _search_url(keyword: str) -> str:
    base_url = "https://scienceon.kisti.re.kr/srch/selectPORSrchArticleList.do"
    q = urllib.parse.quote(keyword, safe="")
    return f"{base_url}?queryText={q}"

def _normalize_items_from_results(results: List[Dict[str, Any]], displayed_count: Optional[int] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    
    # displayed_count가 있으면 그만큼만 출력, 없으면 최대 10개
    limit = displayed_count if displayed_count is not None and displayed_count > 0 else min(10, len(results))
    
    for row in results[:limit]:
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

async def has_pending_article_results(session_id: Optional[str]) -> bool:
    """세션에 이전 논문 검색 결과가 저장되어 있는지 확인한다."""
    if not session_id:
        return False
    st = await _get_session_state(session_id)
    return bool(st and isinstance(st, dict) and st.get("results") and len(st.get("results")) > 0)

async def article_master(query: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """LangGraph 기반 논문 검색/다운로드 마스터 엔트리.

    - 세션 상태를 복원/저장한다.
    - 숫자만 입력되었고, 이전 결과가 있으면 다운로드 명령으로 해석한다.
    - 에이전트 사용 불가 시 폴백으로 검색만 수행한다.
    """
    t = (query or "").strip()
    
    # 항상 run_scienceon_agent 호출 (일반 검색 쿼리도 포함)
    try:
        # 세션 상태 복원
        st: Optional[ScienceONAgentState] = await _get_session_state(session_id) if session_id else None
        st = await run_scienceon_agent(user_input=t, state=st)
        # 세션 상태 저장
        if session_id:
            await _set_session_state(session_id, st)
        # 정규화된 응답 구성
        results: List[Dict[str, Any]] = st.get("results", [])
        displayed_count = st.get("displayed_count")
        items = _normalize_items_from_results(results, displayed_count=displayed_count)
        response = st.get("response") or ""
        summary = st.get("summary") or ""
        intent = st.get("intent") or "unknown"
        error = st.get("error")
        ok = error is None
        return {
            "ok": ok,
            "intent": intent,
            "message": response if response else (summary or ""),
            "summary": summary,
            "link": _search_url(query),
            "items": items,
            "results": results,
        }
    except Exception as exc:
        # 폴백: 검색만 수행
        fb = await asyncio.to_thread(article_search, query)
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
    try:
        state: Optional[ScienceONAgentState] = None
        # 타임아웃 설정 추가
        result_state = await asyncio.wait_for(
            run_scienceon_agent(user_input=keyword, state=state),
            timeout=OPENAI_TIMEOUT * 2  # 에이전트 전체 실행 타임아웃
        )
        results: List[Dict[str, Any]] = result_state.get("results", [])
        summary: str = result_state.get("summary", "") or ""
        displayed_count = result_state.get("displayed_count")

        # items 정규화: agent.py 결과 필드를 그대로 보존한다
        items = _normalize_items_from_results(results, displayed_count=displayed_count)

        return {
            "ok": True,
            "message": summary
            or f"논문 검색 결과(키워드: {keyword})입니다. 상세는 검색 페이지를 참고하세요.",
            "link": _search_url(keyword),
            "items": items,
        }
    except asyncio.TimeoutError:
        LOGGER.warning(f"에이전트 실행 타임아웃 (키워드: {keyword})")
        # 폴백으로 이어감
    except Exception as e:
        LOGGER.warning(f"에이전트 실행 실패: {e}")
        # 폴백으로 이어감

    # 2) 폴백: 간단 HTML 스크래핑(기존 동작 유지)
    url = _search_url(keyword)
    items: List[Dict] = []
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a"):
            title = a.get_text(strip=True)
            href = a.get("href") or ""
            if not title or not href:
                continue
            if ".pdf" in href.lower():
                base_url = "https://scienceon.kisti.re.kr/srch/selectPORSrchArticleList.do"
                items.append({
                    "title": title,
                    "pdf_link": urllib.parse.urljoin(base_url, href),
                    "detail_link": None,
                })
            if len(items) >= 5:
                break
    except Exception:
        pass

    return {
        "ok": True,
        "message": f"논문 검색 결과(키워드: {keyword})입니다. 상세는 검색 페이지를 참고하세요.",
        "link": url,
        "items": items,
    }


class TechArticleSession:
    """에이전트 상태를 유지하며 검색/다운로드를 보조하는 세션 래퍼.

    - 가능한 경우 `agent.py`를 활용하여 고품질 검색/다운로드를 수행한다.
    - 환경 제약으로 사용할 수 없을 경우 검색은 HTML 폴백을 제공하고,
      다운로드는 사용 불가 메시지를 반환한다.
    """

    def __init__(self, session_id: Optional[str] = None) -> None:
        self._state: Optional[ScienceONAgentState] = None
        self._session_id = session_id

    async def search(self, query: str) -> Dict[str, Any]:
        """검색 수행. 내부 상태가 최신 결과로 갱신된다."""
        try:
            # 세션 메모리와 동기화
            if self._session_id and self._state is None:
                self._state = await _get_session_state(self._session_id)
            self._state = await run_scienceon_agent(user_input=query, state=self._state)
            if self._session_id:
                await _set_session_state(self._session_id, self._state)
            results: List[Dict[str, Any]] = self._state.get("results", [])
            displayed_count = self._state.get("displayed_count")
            items = _normalize_items_from_results(results, displayed_count=displayed_count)
            return {
                "ok": True,
                "message": self._state.get("summary")
                or f"논문 검색 결과(키워드: {query})입니다.",
                "link": _search_url(query),
                "items": items,
            }
        except Exception as exc:
                # 에이전트 실패 시 폴백
                fb = await article_search(query)
                fb["message"] = f"{fb.get('message', '')} (agent 연동 실패로 폴백: {exc})"
                return fb

    async def download(self, ordinal: int) -> Dict[str, Any]:
        """검색 결과의 N번째(1-base) 논문 PDF 다운로드 시도."""
        if not isinstance(ordinal, int) or ordinal <= 0:
            return {"ok": False, "message": "순번은 1 이상의 정수여야 합니다."}
        try:
            # 한국어 지시로 agent의 다운로드 경로를 트리거
            command = f"{ordinal}번째 논문 pdf 다운받아줘"
            if self._session_id and self._state is None:
                self._state = await _get_session_state(self._session_id)
            self._state = await run_scienceon_agent(user_input=command, state=self._state)
            if self._session_id:
                await _set_session_state(self._session_id, self._state)
            response = self._state.get("response") or ""
            error = self._state.get("error")
            ok = error is None
            # 최신 결과를 그대로 반환하여 pdf_path가 반영되도록 함
            results: List[Dict[str, Any]] = self._state.get("results", []) if self._state else []
            displayed_count = self._state.get("displayed_count") if self._state else None
            items = _normalize_items_from_results(results, displayed_count=displayed_count)
            return {
                "ok": ok,
                "message": response if ok else error,
                "items": items,
            }
        except Exception as exc:
            return {"ok": False, "message": f"다운로드 중 오류가 발생했습니다: {exc}"}

    def get_state(self) -> Optional[ScienceONAgentState]:
        """내부 에이전트 상태를 반환한다."""
        return self._state








