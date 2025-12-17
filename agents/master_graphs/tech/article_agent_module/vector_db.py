"""논문 전용 벡터DB 함수들.

공통 벡터DB 유틸리티 함수와 논문 전용 함수들을 포함합니다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from .state import _get_collection_lock, _collection_cache, OPENAI_MODEL
from .utils import extract_text_from_pdf
from .crawler import download_pdf_to_memory
from .keyword import normalize_and_translate_keyword

load_dotenv()

LOGGER = logging.getLogger(__name__)

# 벡터DB 관련 임포트 (선택적)
try:
    from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    VECTOR_DB_AVAILABLE = True
except ImportError:
    VECTOR_DB_AVAILABLE = False
    Collection = None
    RecursiveCharacterTextSplitter = None

# 전역 변수 (벡터DB 관련)
_splitter: Optional[Any] = None
_async_openai_client: Optional[Any] = None

# Milvus 연결 초기화용 Lock 및 상태 플래그
_milvus_lock: Optional[asyncio.Lock] = None
_milvus_initialized = False


def _get_milvus_lock() -> asyncio.Lock:
    """Milvus 초기화용 asyncio.Lock을 가져온다 (lazy initialization)."""
    global _milvus_lock
    if _milvus_lock is None:
        _milvus_lock = asyncio.Lock()
    return _milvus_lock


def initialize_models() -> None:
    """텍스트 스플리터 초기화."""
    global _splitter
    
    if not VECTOR_DB_AVAILABLE:
        LOGGER.warning("벡터DB 라이브러리가 설치되지 않았습니다.")
        return
    
    if _splitter is None:
        try:
            _splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        except Exception as e:
            LOGGER.warning(f"텍스트 스플리터 초기화 실패: {e}")


async def initialize_milvus() -> None:
    """Milvus 연결 초기화 (async-safe, 중복 연결 방지)."""
    global _milvus_initialized

    if not VECTOR_DB_AVAILABLE:
        LOGGER.warning("벡터DB 라이브러리가 설치되지 않았습니다.")
        return

    # 이미 초기화된 경우 빠른 경로
    if _milvus_initialized:
        try:
            if connections.has_connection("default"):
                return
        except Exception:
            _milvus_initialized = False

    lock = _get_milvus_lock()
    async with lock:
        # Double-check (다른 코루틴이 먼저 초기화했을 수 있음)
        if _milvus_initialized:
            try:
                if connections.has_connection("default"):
                    return
            except Exception:
                _milvus_initialized = False

        milvus_host = os.getenv("HOST")
        milvus_port = os.getenv("MILVUS_PORT")
        
        # 환경변수 안전 변환
        def _get_int_env(key: str, default: int) -> int:
            try:
                value = os.getenv(key, str(default))
                return int(value)
            except (ValueError, TypeError) as e:
                LOGGER.warning(f"환경변수 {key}='{os.getenv(key)}'가 유효하지 않아 기본값 {default} 사용: {e}")
                return default
        
        milvus_timeout = _get_int_env("MILVUS_TIMEOUT", 20)  # 기본 20초

        if not milvus_host or not milvus_port:
            LOGGER.warning("HOST 또는 MILVUS_PORT가 설정되지 않았습니다.")
            return

        try:
            # 이미 연결되어 있지 않은 경우만 연결 시도
            if not connections.has_connection("default"):
                # pymilvus의 connections.connect는 timeout 파라미터를 직접 지원하지 않으므로
                # asyncio.wait_for로 래핑
                await asyncio.wait_for(
                    asyncio.to_thread(
                        connections.connect,
                        "default",
                        host=milvus_host,
                        port=int(milvus_port),
                    ),
                    timeout=milvus_timeout,
                )
            _milvus_initialized = True
        except asyncio.TimeoutError:
            LOGGER.warning(f"Milvus 연결 타임아웃 (timeout={milvus_timeout}s)")
            _milvus_initialized = False
        except Exception as e:
            LOGGER.warning(f"Milvus 연결 실패 (timeout={milvus_timeout}s): {e}")
            _milvus_initialized = False


def extract_keyword_from_text(text: str) -> str:
    """텍스트에서 주요 키워드를 추출한다.
    
    Args:
        text: 분석할 텍스트
        
    Returns:
        추출된 키워드
    """
    keywords = [
        "디스플레이", "display", "인공지능", "ai", "머신러닝", "딥러닝",
        "배터리", "battery", "반도체", "semiconductor", "센서", "sensor",
    ]
    
    text_lower = text.lower()
    for keyword in keywords:
        if keyword.lower() in text_lower:
            return keyword
    
    return "기타"


async def sanitize_collection_name(keyword: str, use_llm: bool = True) -> str:
    """키워드를 Milvus 컬렉션 이름으로 변환 (LLM + Fallback + 캐싱).
    
    Args:
        keyword: 변환할 키워드 (오타 가능)
        use_llm: LLM 사용 여부 (기본값: True)
    
    Milvus 컬렉션 이름은 영문자, 숫자, 언더스코어만 허용합니다.
    한글 키워드는 LLM을 통해 오타를 수정하고 영문으로 변환하거나,
    fallback keyword_map 또는 해시 기반으로 변환합니다.
    """
    return await normalize_and_translate_keyword(keyword, use_llm=use_llm, use_cache=True)


async def get_collection_name(keyword: str, prefix: str = "embeddings") -> str:
    """키워드로 컬렉션 이름을 생성한다.
    
    Args:
        keyword: 검색 키워드 (한글 가능)
        prefix: 컬렉션 이름 접두사 (기본값: "embeddings")
        
    Returns:
        Milvus 컬렉션 이름 (영문자, 숫자, 언더스코어만 포함)
    """
    collection_suffix = await sanitize_collection_name(keyword)
    collection_name = f"{prefix}_{collection_suffix}"
    
    # 길이 제한 (Milvus 컬렉션 이름은 최대 255자)
    if len(collection_name) > 255:
        hash_value = hashlib.md5(keyword.encode('utf-8')).hexdigest()[:12]
        collection_name = f"{prefix}_kw_{hash_value}"
    
    return collection_name


async def encode_texts(texts: List[str]) -> Optional[Any]:
    """텍스트 리스트를 OpenAI 임베딩 벡터로 변환한다 (비동기).
    
    AsyncOpenAI 클라이언트는 싱글톤 패턴으로 관리되어 재사용됩니다.
    
    Args:
        texts: 임베딩할 텍스트 리스트 (단일 문자열도 가능)
        
    Returns:
        numpy array 형태의 임베딩 벡터 (실패 시 None)
    """
    global _async_openai_client
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        LOGGER.warning("OPENAI_API_KEY가 설정되지 않았습니다.")
        return None
    
    try:
        from openai import AsyncOpenAI
        import numpy as np
        
        # 환경변수 안전 변환
        def _get_int_env(key: str, default: int) -> int:
            try:
                value = os.getenv(key, str(default))
                return int(value)
            except (ValueError, TypeError) as e:
                LOGGER.warning(f"환경변수 {key}='{os.getenv(key)}'가 유효하지 않아 기본값 {default} 사용: {e}")
                return default
        
        embedding_timeout = _get_int_env("EMBEDDING_TIMEOUT", 60)  # 기본 60초
        
        if _async_openai_client is None:
            _async_openai_client = AsyncOpenAI(
                api_key=api_key,
                timeout=embedding_timeout,
                max_retries=2,
            )
        
        client = _async_openai_client
        model = os.getenv("EMBEDDING_MODEL")
        
        # 단일 문자열인 경우 리스트로 변환
        if isinstance(texts, str):
            texts = [texts]
        
        # 명시적 타임아웃 적용
        response = await asyncio.wait_for(
            client.embeddings.create(
                model=model,
                input=texts,
            ),
            timeout=embedding_timeout,
        )
        embeddings = [item.embedding for item in response.data]
        return np.array(embeddings)
    except ImportError:
        LOGGER.warning("openai 라이브러리가 설치되지 않았습니다.")
        return None
    except asyncio.TimeoutError:
        LOGGER.warning(f"임베딩 생성 타임아웃 (timeout={embedding_timeout}s)")
        return None
    except Exception as e:
        LOGGER.warning(f"임베딩 생성 실패: {e}")
        return None


def get_text_splitter() -> Optional[Any]:
    """텍스트 스플리터를 반환한다."""
    initialize_models()
    return _splitter


# ============================================================================
# 논문 전용 벡터DB 함수들
# ============================================================================

async def get_or_create_paper_collection(collection_name: str) -> Optional[Any]:
    """논문 컬렉션 가져오기 또는 생성 (비동기 버전, asyncio.Lock 사용)."""
    if not VECTOR_DB_AVAILABLE:
        return None
    
    # 멀티톤 패턴: 캐시에 있으면 재사용 (async-safe)
    lock = _get_collection_lock()
    async with lock:
        cached = _collection_cache.get(collection_name)
    if cached is not None:
        return cached
    
    try:
        await initialize_milvus()
        
        # 기존 컬렉션이 있으면 로드
        if collection_name in utility.list_collections():
            collection = Collection(collection_name)
            collection.load()
            # 캐시에 저장 (Lock으로 보호)
            async with lock:
                _collection_cache[collection_name] = collection
            return collection
        
        # 새 컬렉션 생성
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="identifier", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
            FieldSchema(name="paper_keyword", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="pdf_link", dtype=DataType.VARCHAR, max_length=512),  # 🆕 추가
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1536),
        ]
        schema = CollectionSchema(fields, description=f"ScienceON 논문 임베딩 데이터 - {collection_name}")
        
        collection = Collection(name=collection_name, schema=schema)
        collection.create_index(
            field_name="embedding",
            index_params={
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 1024}
            }
        )
        collection.load()
        # 새 컬렉션도 캐시에 저장 (Lock으로 보호)
        async with lock:
            _collection_cache[collection_name] = collection
        return collection
    except Exception as e:
        LOGGER.warning(f"컬렉션 생성/로드 실패: {e}")
        return None


async def get_paper_collection(collection_name: str) -> Optional[Any]:
    """기존 논문 컬렉션만 가져오기 (생성하지 않음)."""
    if not VECTOR_DB_AVAILABLE:
        return None
    
    # 멀티톤 패턴: 캐시에 있으면 재사용 (async-safe)
    lock = _get_collection_lock()
    async with lock:
        cached = _collection_cache.get(collection_name)
    if cached is not None:
        return cached
    
    try:
        await initialize_milvus()
        
        # 기존 컬렉션이 있으면 로드
        if collection_name in utility.list_collections():
            collection = Collection(collection_name)
            collection.load()
            # 캐시에 저장 (Lock으로 보호)
            async with lock:
                _collection_cache[collection_name] = collection
            return collection
        
        # 컬렉션이 없으면 None 반환 (생성하지 않음)
        return None
    except Exception as e:
        LOGGER.warning(f"컬렉션 로드 실패: {e}")
        return None


async def check_vector_db(query: str, limit: int = 5, max_distance: float = 1.5, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """벡터DB에서 유사 논문 검색."""
    if not VECTOR_DB_AVAILABLE:
        return []
    
    try:
        initialize_models()
        await initialize_milvus()
        
        if not keyword:
            keyword = extract_keyword_from_text(query)
        
        # 🔧 개선: 다중 컬렉션 검색 지원
        collections = await _load_paper_collections_for_query(keyword)
        if not collections:
            return []
        
        embeddings_result = await encode_texts([query])
        if embeddings_result is None or len(embeddings_result) == 0:
            return []
        query_vec = embeddings_result[0].tolist()
        
        query_lower = query.lower()
        query_words = re.findall(r'[\w가-힣]+', query_lower)
        
        # 여러 컬렉션의 결과를 집계
        aggregated: List[Dict[str, Any]] = []
        seen_identifiers: set[str] = set()
        
        for collection_name in collections:
            try:
                collection = await get_paper_collection(collection_name)
                if not collection:
                    continue
                
                num_entities = collection.num_entities
                if num_entities == 0:
                    continue
            except Exception:
                continue
            
            # 스키마 확인하여 pdf_link 필드 존재 여부 체크
            schema = collection.schema
            field_names = [field.name for field in schema.fields]
            has_pdf_link = "pdf_link" in field_names
            
            output_fields = ["identifier", "title", "content"]
            if has_pdf_link:
                output_fields.append("pdf_link")
            
            try:
                results = await asyncio.to_thread(
                    collection.search,
                    data=[query_vec],
                    anns_field="embedding",
                    param={"metric_type": "L2", "params": {"nprobe": 10}},
                    limit=limit * 5,
                    expr=None,
                    output_fields=output_fields
                )
            except Exception:
                continue
            
            if not results or len(results) == 0 or len(results[0]) == 0:
                continue
            
            # 각 컬렉션의 결과 처리
            for hit in results[0]:
                identifier = hit.entity.get('identifier')
                if not identifier:
                    continue
                
                # identifier 기반 중복 제거 (여러 컬렉션에 있을 수 있음)
                if identifier in seen_identifiers:
                    continue
                
                title = hit.entity.get('title', '').lower()
                content = hit.entity.get('content', '').lower()
                
                # 키워드 매칭 확인
                full_match = (query_lower in title or query_lower in content)
                word_match = any(word in title or word in content for word in query_words if len(word) > 1)
                keyword_match = full_match or word_match
                
                # 키워드 매칭이 있으면 거리 임계값 완화
                effective_max_distance = 2.0 if keyword_match else max_distance
                
                if hit.distance > effective_max_distance:
                    continue
                
                seen_identifiers.add(identifier)
                similarity = 1.0 / (1.0 + hit.distance)
                
                paper_data = {
                    "identifier": identifier,
                    "title": hit.entity.get('title'),
                    "content": hit.entity.get('content', ''),
                    "score": hit.distance,
                    "similarity": similarity,
                }
                # pdf_link가 있으면 추가
                if has_pdf_link:
                    paper_data["pdf_link"] = hit.entity.get("pdf_link", "")
                
                aggregated.append(paper_data)
        
        aggregated.sort(key=lambda x: x["score"])
        return aggregated[:limit]
    except Exception as e:
        LOGGER.warning(f"벡터DB 검색 오류: {e}")
        return []


async def _load_paper_collections_for_query(keyword: Optional[str]) -> List[str]:
    """질의 키워드 기준으로 탐색할 논문 컬렉션 목록을 반환."""
    try:
        await initialize_milvus()
        if keyword:
            cleaned_keyword = keyword.strip()
            if cleaned_keyword.startswith("논문_"):
                cleaned_keyword = cleaned_keyword[2:]
            
            keywords = [kw.strip() for kw in cleaned_keyword.split(",") if kw.strip()]
            
            if not keywords:
                return []
                
            collection_names = []
            for kw in keywords:
                collection_name = await get_collection_name(kw, prefix="papers_embeddings")
                if collection_name and collection_name not in collection_names:
                    collection_names.append(collection_name)
            
            return collection_names if collection_names else []
        
        return [
            name
            for name in utility.list_collections()
            if name.startswith("papers_embeddings_")
        ]
    except Exception:
        return []


async def retrieve_paper_chunks_from_milvus(
    query: str,
    *,
    identifier: Optional[str] = None,
    keyword: Optional[str] = None,
    top_k: int = 8,
    score_floor: float = 0.25,
) -> List[Dict[str, Any]]:
    """Manual QA agent 방식으로 Milvus에서 논문 청크를 검색."""
    if not VECTOR_DB_AVAILABLE or not query.strip():
        return []

    embeddings = await encode_texts([query])
    if embeddings is None or len(embeddings) == 0:
        return []

    query_vec = embeddings[0].tolist()
        
    if identifier:
        collections = await _load_paper_collections_for_query(None)  # 모든 컬렉션
    else:
        collections = await _load_paper_collections_for_query(keyword)
    
    if not collections:
        return []

    aggregated: List[Dict[str, Any]] = []
    for collection_name in collections:
        try:
            collection = await get_paper_collection(collection_name)
            if not collection:
                continue
        except Exception:
            continue

        expr = f'identifier == "{identifier}"' if identifier else None
        
        # 스키마 확인하여 pdf_link 필드 존재 여부 체크
        schema = collection.schema
        field_names = [field.name for field in schema.fields]
        has_pdf_link = "pdf_link" in field_names
        
        output_fields = ["identifier", "title", "content"]
        if has_pdf_link:
            output_fields.append("pdf_link")
        
        try:
            hits = await asyncio.to_thread(
                collection.search,
                data=[query_vec],
                anns_field="embedding",
                param={"metric_type": "L2", "params": {"nprobe": 10}},
                limit=top_k * 3,
                expr=expr,
                output_fields=output_fields,
            )
        except Exception:
            continue

        if not hits or not hits[0]:
            continue

        for hit in hits[0]:
            score = 1 / (1 + hit.distance)
            if score < score_floor:
                continue

            chunk_data = {
                "chunk_id": f"{collection_name}:{hit.id}",
                "identifier": hit.entity.get("identifier"),
                "title": hit.entity.get("title", ""),
                "content": hit.entity.get("content", ""),
                "score": score,
            }
            # pdf_link가 있으면 추가
            if has_pdf_link:
                chunk_data["pdf_link"] = hit.entity.get("pdf_link", "")
            aggregated.append(chunk_data)

    if not aggregated:
        return []

    aggregated.sort(key=lambda x: x["score"], reverse=True)

    deduped: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in aggregated:
        chunk_id = item.get("chunk_id")
        if not chunk_id:
            continue
        if chunk_id in seen_ids:
            continue
        seen_ids.add(chunk_id)
        deduped.append(item)
        if len(deduped) >= top_k:
            break

    return deduped


def convert_vector_db_results_to_paper_results(vector_db_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """벡터DB 검색 결과를 논문 목록 형식으로 변환."""
    paper_results = []
    for vdb_result in vector_db_results:
        paper_results.append({
            "title": vdb_result.get("title", "제목 없음"),
            "identifier": vdb_result.get("identifier", ""),
            "pdf_link": vdb_result.get("pdf_link", ""),  # 🆕 벡터DB에서 가져온 값 사용
            "detail_link": "",
            "content": vdb_result.get("content", ""),
            "score": vdb_result.get("score"),
            "similarity": vdb_result.get("similarity"),
        })
    return paper_results


async def check_paper_exists(identifier: str, keyword: Optional[str] = None) -> bool:
    """벡터DB에 해당 identifier를 가진 논문이 이미 존재하는지 확인한다."""
    if not VECTOR_DB_AVAILABLE:
        LOGGER.debug(f"[중복 체크] 벡터DB가 사용 불가능하여 체크 건너뜀: {identifier}")
        return False
    
    try:
        initialize_models()
        await initialize_milvus()
        
        if keyword:
            cleaned_keyword = keyword.strip()
            if cleaned_keyword.startswith("논문_"):
                cleaned_keyword = cleaned_keyword[2:]
            
            keywords = [kw.strip() for kw in cleaned_keyword.split(",") if kw.strip()]
            
            if keywords:
                for kw in keywords:
                    collection_name = await get_collection_name(kw, prefix="papers_embeddings")
                    if not collection_name:
                        continue
                    
                    try:
                        collection = await get_paper_collection(collection_name)
                        if not collection:
                            continue
                        
                        expr = f'identifier == "{identifier}"'
                        results = await asyncio.to_thread(
                            collection.query,
                            expr=expr,
                            output_fields=["identifier"],
                            limit=1
                        )
                        
                        if results and len(results) > 0:
                            LOGGER.info(f"[중복 체크] identifier '{identifier}'가 컬렉션 '{collection_name}'에 존재함")
                            return True
                    except Exception as e:
                        LOGGER.debug(f"[중복 체크] 컬렉션 '{collection_name}' 확인 중 오류: {e}")
                        continue
                
                LOGGER.debug(f"[중복 체크] identifier '{identifier}'가 키워드 컬렉션에서 발견되지 않음")
                return False
        
        all_collections = utility.list_collections()
        paper_collections = [col for col in all_collections if col.startswith("papers_embeddings_")]
        
        if not paper_collections:
            LOGGER.debug(f"[중복 체크] 논문 컬렉션이 없어 중복 없음으로 판단: {identifier}")
            return False
        
        for collection_name in paper_collections:
            try:
                collection = await get_paper_collection(collection_name)
                if not collection:
                    continue
                
                expr = f'identifier == "{identifier}"'
                results = await asyncio.to_thread(
                    collection.query,
                    expr=expr,
                    output_fields=["identifier"],
                    limit=1
                )
                
                if results and len(results) > 0:
                    LOGGER.info(f"[중복 체크] identifier '{identifier}'가 이미 컬렉션 '{collection_name}'에 존재함")
                    return True
            except Exception as e:
                LOGGER.debug(f"[중복 체크] 컬렉션 '{collection_name}' 확인 중 오류: {e}")
                continue
        
        LOGGER.debug(f"[중복 체크] identifier '{identifier}'가 모든 컬렉션에서 발견되지 않음 (중복 없음)")
        return False
    except Exception as e:
        LOGGER.warning(f"[중복 체크] identifier '{identifier}' 확인 중 오류: {e}")
        return False


async def download_and_vectorize_paper(
    result: Dict[str, Any],
    keyword: Optional[str] = None,
) -> bool:
    """논문을 PDF 다운로드하고 벡터DB에 저장한다 (메모리 처리 방식)."""
    if not VECTOR_DB_AVAILABLE:
        return False
    
    pdf_link = result.get("pdf_link", "")
    title = result.get("title", "")
    identifier = result.get("identifier", "")
    
    if not pdf_link or pdf_link == "원문보기 링크 없음":
        LOGGER.warning(f"[벡터화 건너뜀] PDF 링크 없음: {title[:50]}...")
        return False
    
    if not identifier:
        LOGGER.warning(f"[벡터화 건너뜀] 식별자(ID) 없음: {title[:50]}...")
        return False
    
    if await check_paper_exists(identifier, keyword=keyword):
        LOGGER.info(f"[벡터화 건너뜀] 이미 벡터DB에 존재함 (중복 방지): {identifier}")
        return True
    
    try:
        LOGGER.info(f"[벡터화 시작] PDF 메모리 다운로드 시도: {title[:50]}..., ID: {identifier}")
        pdf_bytes = await download_pdf_to_memory(pdf_link, use_playwright=True)
        
        if not pdf_bytes:
            LOGGER.warning(f"[벡터화 실패] PDF 다운로드 실패: {title[:50]}...")
            return False
        
        LOGGER.info(f"[벡터화] PDF 메모리 다운로드 성공: {len(pdf_bytes)} bytes")
        
        LOGGER.debug(f"[벡터화] 텍스트 추출 시도 (메모리): {identifier}")
        full_text = await extract_text_from_pdf(pdf_bytes)
        
        if not full_text:
            LOGGER.warning(f"[벡터화 실패] 텍스트 추출 실패 또는 빈 텍스트: {title[:50]}...")
            return False
        
        LOGGER.info(f"[벡터화] 텍스트 추출 성공: {len(full_text)}자")
        
        paper_keyword = f"논문_{keyword}" if keyword else None
        
        collection_keyword = None
        if keyword:
            keywords_list = [kw.strip() for kw in keyword.split(",") if kw.strip()]
            
            if keywords_list:
                title_lower = title.lower()
                
                matched_keywords = []
                for kw in keywords_list:
                    kw_lower = kw.lower()
                    if kw_lower in title_lower:
                        matched_keywords.append(kw)
                
                if matched_keywords:
                    collection_keyword = matched_keywords[0]
                    LOGGER.info(
                        f"[벡터화] 논문 제목에 포함된 키워드 선택: '{collection_keyword}' "
                        f"(논문: {title[:50]}...)"
                    )
                else:
                    collection_keyword = keywords_list[0]
                    LOGGER.debug(f"[벡터화] 논문 제목에 포함된 키워드 없음, 첫 번째 키워드 사용: {collection_keyword}")
            else:
                collection_keyword = keyword
        
        LOGGER.debug(f"[벡터화] 벡터DB 저장 시도 (키워드: {paper_keyword}, 컬렉션 키워드: {collection_keyword})")
        
        chunks_count = await embed_and_store_paper(
            identifier=identifier,
            title=title,
            text=full_text,
            paper_keyword=paper_keyword,
            collection_keyword=collection_keyword,
            pdf_link=pdf_link
        )
        
        if chunks_count > 0:
            LOGGER.info(f"[벡터화 완료] ID: {identifier}, 제목: {title[:50]}..., 청크: {chunks_count}개")
            return True
        else:
            LOGGER.warning(f"[벡터화 실패] 벡터DB 저장 실패 (청크 0개): {title[:50]}...")
            return False
            
    except Exception as exc:
        LOGGER.error(f"[벡터화 실패] 예외 발생: {exc}", exc_info=True)
        return False


async def embed_and_store_paper(
    identifier: str,
    title: str,
    text: Optional[str] = None,
    paper_keyword: Optional[str] = None,
    collection_keyword: Optional[str] = None,
    pdf_link: Optional[str] = None
) -> int:
    """논문을 벡터DB에 임베딩하여 저장한다."""
    if not VECTOR_DB_AVAILABLE:
        return 0
    
    try:
        initialize_models()
        await initialize_milvus()
        
        if text:
            text_content = text.strip()
        else:
            LOGGER.error("text 파라미터는 필수입니다.")
            return 0
        
        if collection_keyword:
            cleaned_keyword = collection_keyword.strip()
            if cleaned_keyword.startswith("논문_"):
                cleaned_keyword = cleaned_keyword[2:]
            
            keywords = [kw.strip() for kw in cleaned_keyword.split(",") if kw.strip()]
        else:
            extracted_keyword = extract_keyword_from_text(title)
            keywords = [extracted_keyword] if extracted_keyword else []
        
        if not keywords:
            LOGGER.warning(f"컬렉션 키워드가 없어 저장할 수 없습니다: {title[:50]}...")
            return 0
        
        splitter = get_text_splitter()
        if splitter is None:
            return 0
        chunks = splitter.split_text(text_content)
        
        embeddings_result = await encode_texts(chunks)
        if embeddings_result is None:
            return 0
        
        if len(embeddings_result) != len(chunks):
            LOGGER.warning(
                f"임베딩 길이({len(embeddings_result)})와 청크 길이({len(chunks)})가 불일치합니다. "
                f"논문 ID: {identifier}, 제목: {title[:50]}..."
            )
            return 0
        
        embeddings = embeddings_result
        identifiers = [identifier] * len(chunks)
        titles = [title] * len(chunks)
        pdf_links = [pdf_link or ""] * len(chunks)
        
        total_stored = 0
        
        for kw in keywords:
            normalized_kw = kw.strip()
            
            collection_name = await get_collection_name(normalized_kw, prefix="papers_embeddings")
            collection = await get_or_create_paper_collection(collection_name)
            
            if not collection:
                LOGGER.warning(f"컬렉션 생성/로드 실패: {collection_name}")
                continue
            
            try:
                collection.load()
                existing = await asyncio.to_thread(
                    collection.query,
                    expr=f'identifier == "{identifier}"',
                    output_fields=["identifier"]
                )
                if existing and len(existing) > 0:
                    LOGGER.info(f"[벡터화 건너뜀] 이미 컬렉션 '{collection_name}'에 존재: {identifier}")
                    continue
            except Exception:
                pass
            
            if paper_keyword is None:
                current_paper_keyword = f"논문_{normalized_kw}"
            else:
                current_paper_keyword = paper_keyword
            
            schema = collection.schema
            field_names = [field.name for field in schema.fields]
            has_paper_keyword = "paper_keyword" in field_names
            has_pdf_link = "pdf_link" in field_names
            
            # 필드 존재 여부에 따라 데이터 구성
            if has_paper_keyword and has_pdf_link:
                paper_keywords = [current_paper_keyword] * len(chunks)
                data = [identifiers, titles, chunks, paper_keywords, pdf_links, embeddings.tolist()]
            elif has_paper_keyword:
                paper_keywords = [current_paper_keyword] * len(chunks)
                data = [identifiers, titles, chunks, paper_keywords, embeddings.tolist()]
            elif has_pdf_link:
                data = [identifiers, titles, chunks, pdf_links, embeddings.tolist()]
            else:
                data = [identifiers, titles, chunks, embeddings.tolist()]
            
            try:
                collection.insert(data)
                collection.flush()
                total_stored += len(chunks)
                LOGGER.info(f"[벡터화 저장] 컬렉션 '{collection_name}': {len(chunks)}개 청크 저장")
            except Exception as e:
                LOGGER.warning(f"[벡터화 저장 실패] 컬렉션 '{collection_name}': {e}")
                continue
        
        return total_stored
        
    except Exception as e:
        LOGGER.warning(f"논문 벡터DB 저장 오류: {e}")
        return 0

