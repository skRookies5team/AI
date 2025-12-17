"""논문 에이전트 상태 정의 및 전역 변수."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

# .env 파일 로드 (여러 경로에서 시도)
def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    import sys
    from pathlib import Path
    
    # 가능한 .env 파일 경로들
    possible_paths = [
        os.path.join(os.getcwd(), ".env"),  # 현재 작업 디렉토리
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),  # 프로젝트 루트
        os.path.join(os.path.dirname(__file__), ".env"),  # 현재 파일 디렉토리
    ]
    
    # 스크립트 파일이 있는 경우 그 디렉토리도 확인
    if hasattr(sys, 'argv') and sys.argv:
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        possible_paths.append(os.path.join(script_dir, ".env"))
        possible_paths.append(os.path.join(script_dir, "..", ".env"))
    
    env_path = None
    for path in possible_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            env_path = abs_path
            break
    
    if env_path:
        load_dotenv(env_path, override=False)  # 기존 환경 변수는 덮어쓰지 않음
    else:
        # dotenv의 기본 동작 시도
        load_dotenv(override=False)

_load_env_file()

LOGGER = logging.getLogger(__name__)

# OPENAI_MODEL 환경 변수 가져오기 (기본값 제공)
OPENAI_MODEL = os.getenv("OPENAI_MODEL") or os.getenv("OPENAI_MODEL_NAME") or "gpt-4o-mini"


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
    last_selected_paper_index: int  # 마지막으로 선택한 논문 인덱스 (순번 없을 때 사용)

