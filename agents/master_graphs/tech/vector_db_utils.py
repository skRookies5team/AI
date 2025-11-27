"""벡터DB 공통 유틸리티 모듈.

특허 및 논문 에이전트에서 공통으로 사용하는 벡터DB 관련 함수들을 제공합니다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, List, Optional

from dotenv import load_dotenv

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


def initialize_milvus() -> None:
    """Milvus 연결 초기화."""
    if not VECTOR_DB_AVAILABLE:
        LOGGER.warning("벡터DB 라이브러리가 설치되지 않았습니다.")
        return
    
    milvus_host = os.getenv("HOST")
    milvus_port = os.getenv("MILVUS_PORT")
    
    if not milvus_host or not milvus_port:
        LOGGER.warning("HOST 또는 MILVUS_PORT가 설정되지 않았습니다.")
        return
    
    try:
        connections.connect("default", host=milvus_host, port=int(milvus_port))
    except Exception as e:
        LOGGER.warning(f"Milvus 연결 실패: {e}")


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


def sanitize_collection_name(keyword: str) -> str:
    """키워드를 Milvus 컬렉션 이름으로 변환 (한글을 영문으로 변환).
    
    Milvus 컬렉션 이름은 영문자, 숫자, 언더스코어만 허용합니다.
    한글 키워드는 영문으로 변환하거나 해시 기반으로 변환합니다.
    """
    # 키워드 맵
    keyword_map = {
        "디스플레이": "display",
        "인공지능": "ai",
        "배터리": "battery",
        "반도체": "semiconductor",
        "센서": "sensor",
        "전자": "electronics",
        "통신": "communication",
        "소프트웨어": "software",
        "하드웨어": "hardware",
        "네트워크": "network",
        "보안": "security",
        "데이터": "data",
        "알고리즘": "algorithm",
        "머신러닝": "machine_learning",
        "딥러닝": "deep_learning",
        "인터넷": "internet",
        "컴퓨터": "computer",
        "프로그램": "program",
        "애플리케이션": "application",
        "시스템": "system",
        "장치": "device",
        "방법": "method",
        "제어": "control",
        "처리": "process",
        "저장": "storage",
        "전송": "transmission",
        "수신": "reception",
        "디지털": "digital",
        "아날로그": "analog",
        "신호": "signal",
        "파일": "file",
        "이미지": "image",
        "비디오": "video",
        "오디오": "audio",
        "텍스트": "text",
        "음성": "voice",
        "화면": "screen",
        "터치": "touch",
        "입력": "input",
        "출력": "output",
        "전기": "electricity",
    }
    
    normalized = keyword.strip()
    # 공백과 언더스코어를 제거한 버전으로도 매칭 시도 (keyword_map 매칭을 위해)
    normalized_no_space = normalized.replace(' ', '').replace('_', '')
    
    # 먼저 원본으로 매칭 시도
    if normalized in keyword_map:
        collection_suffix = keyword_map[normalized]
    # 공백 제거 버전으로 매칭 시도
    elif normalized_no_space in keyword_map:
        collection_suffix = keyword_map[normalized_no_space]
    else:
        # 해시 기반 변환
        hash_value = hashlib.md5(normalized.encode('utf-8')).hexdigest()[:12]
        english_only = re.sub(r'[^a-zA-Z0-9_]', '_', normalized)
        english_only = re.sub(r'_+', '_', english_only).strip('_')
        collection_suffix = english_only[:50] if english_only else f"kw_{hash_value}"
    
    return collection_suffix


def get_collection_name(keyword: str, prefix: str = "embeddings") -> str:
    """키워드로 컬렉션 이름을 생성한다.
    
    Args:
        keyword: 검색 키워드 (한글 가능)
        prefix: 컬렉션 이름 접두사 (기본값: "embeddings")
        
    Returns:
        Milvus 컬렉션 이름 (영문자, 숫자, 언더스코어만 포함)
    """
    collection_suffix = sanitize_collection_name(keyword)
    collection_name = f"{prefix}_{collection_suffix}"
    
    # 길이 제한 (Milvus 컬렉션 이름은 최대 255자)
    if len(collection_name) > 255:
        hash_value = hashlib.md5(keyword.encode('utf-8')).hexdigest()[:12]
        collection_name = f"{prefix}_kw_{hash_value}"
    
    return collection_name


def encode_texts(texts: List[str]) -> Optional[Any]:
    """텍스트 리스트를 OpenAI 임베딩 벡터로 변환한다.
    
    Args:
        texts: 임베딩할 텍스트 리스트 (단일 문자열도 가능)
        
    Returns:
        numpy array 형태의 임베딩 벡터 (실패 시 None)
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        LOGGER.warning("OPENAI_API_KEY가 설정되지 않았습니다.")
        return None
    
    try:
        from openai import OpenAI
        import numpy as np
        
        client = OpenAI(api_key=api_key)
        model = os.getenv("EMBEDDING_MODEL")
        
        # 단일 문자열인 경우 리스트로 변환
        if isinstance(texts, str):
            texts = [texts]
        
        response = client.embeddings.create(
            model=model,
            input=texts,
        )
        embeddings = [item.embedding for item in response.data]
        return np.array(embeddings)
    except ImportError:
        LOGGER.warning("openai 라이브러리가 설치되지 않았습니다.")
        return None
    except Exception as e:
        LOGGER.warning(f"임베딩 생성 실패: {e}")
        return None


def get_text_splitter() -> Optional[Any]:
    """텍스트 스플리터를 반환한다."""
    initialize_models()
    return _splitter

