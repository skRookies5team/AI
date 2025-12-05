"""논문 에이전트 상태 정의 및 전역 변수."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

load_dotenv()

LOGGER = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL")


def _get_int_env(key: str, default: int) -> int:
    """환경변수를 안전하게 정수로 변환한다."""
    try:
        value = os.getenv(key, str(default))
        return int(value)
    except (ValueError, TypeError) as e:
        LOGGER.warning(f"환경변수 {key}='{os.getenv(key)}'가 유효하지 않아 기본값 {default} 사용: {e}")
        return default


# 타임아웃 설정 (초)
OPENAI_TIMEOUT = _get_int_env("OPENAI_TIMEOUT", 120)  # 기본 120초
HTTP_TIMEOUT = _get_int_env("HTTP_TIMEOUT", 30)  # 기본 30초

# 멀티톤 패턴: Collection 인스턴스 캐시 (키: collection_name)
_collection_cache: Dict[str, Any] = {}

# LangServe/asyncio 환경에 맞게 asyncio.Lock() 사용 (lazy initialization)
_collection_cache_lock: Optional[asyncio.Lock] = None
_rag_llm_lock: Optional[asyncio.Lock] = None

# RAG 답변용 LLM 싱글톤 (전역 INTENT LLM과 별도의 설정 사용)
RAG_LLM: Optional[ChatOpenAI] = None


def _get_collection_lock() -> asyncio.Lock:
    """Collection 캐시용 asyncio.Lock을 가져온다 (lazy initialization)."""
    global _collection_cache_lock
    if _collection_cache_lock is None:
        _collection_cache_lock = asyncio.Lock()
    return _collection_cache_lock


def _get_rag_llm_lock() -> asyncio.Lock:
    """RAG LLM용 asyncio.Lock을 가져온다 (lazy initialization)."""
    global _rag_llm_lock
    if _rag_llm_lock is None:
        _rag_llm_lock = asyncio.Lock()
    return _rag_llm_lock


async def get_rag_llm() -> Optional[ChatOpenAI]:
    """RAG 답변용 ChatOpenAI 싱글톤 인스턴스 (비동기 버전)."""
    global RAG_LLM

    if RAG_LLM is not None:
        return RAG_LLM

    if not OPENAI_MODEL:
        return None

    lock = _get_rag_llm_lock()
    async with lock:
        if RAG_LLM is not None:
            return RAG_LLM

        RAG_LLM = ChatOpenAI(
            model=OPENAI_MODEL,
            temperature=0.3,
            max_tokens=1500,
            timeout=OPENAI_TIMEOUT,
            max_retries=2,
        )
        return RAG_LLM


class ScienceONAgentState(TypedDict):
    """ScienceON 서브 에이전트의 상태.

    통합 마스터 에이전트의 상태에 포함되거나 독립적으로 사용 가능합니다.
    """

    messages: Annotated[List[BaseMessage], "대화 이력"]
    user_input: str
    intent: Literal["search", "detail", "download", "ask_source", "unknown"]
    query: str
    download_index: List[int]  # 단일/여러 논문 모두 처리 (통일)
    keyword: str
    results: List[Dict[str, Any]]
    summary: str
    response: str
    error: Optional[str]
    search_source: Literal["web", "internal", "unknown"]  # 검색 소스: 웹 또는 사내 서버
    displayed_count: int  # 현재까지 사용자에게 보여준 결과 개수 (페이지네이션용)
    vector_db_results: List[Dict[str, Any]]  # 벡터DB 검색 결과
    next_action: Literal["check_db", "ask_search_source_with_db", "search_papers", "rag_answer", "generate_response", "end"]  # 다음 액션
    ask_source_retry_count: int  # ask_search_source 질문 재시도 횟수 (무한 루프 방지)
    previous_query: str  # 이전 검색어 (새로운 검색 요청 감지용)
    last_selected_paper_index: int  # 마지막으로 선택한 논문 인덱스 (순번 없을 때 사용)

