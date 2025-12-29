"""구내식당 식단표 RAG 에이전트 (하이브리드 검색 기반)."""
from __future__ import annotations

import os
import re
import time
from typing import Dict, List, TypedDict, Optional, Any
from collections import Counter
from dotenv import load_dotenv
from pymilvus import connections, Collection
from sentence_transformers import SentenceTransformer
from agents.embedding_utils import get_embedding_generator
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from pathlib import Path
import yaml

# =========================
# YAML 설정 로드
# =========================
_qa_cafeteria_config = None

def _load_qa_cafeteria_config() -> Dict[str, Any]:
    """QA Cafeteria Agent 설정을 YAML 파일에서 로드합니다."""
    global _qa_cafeteria_config
    
    if _qa_cafeteria_config is not None:
        return _qa_cafeteria_config
    
    try:
        # llm_router의 통합 로더 사용
        from agents.llm_router import load_agent_prompts
        all_config = load_agent_prompts()
        # qa 도메인 아래의 qa_cafeteria 설정 로드
        qa_config = all_config.get("qa", {})
        _qa_cafeteria_config = qa_config.get("qa_cafeteria", {})
    except Exception as e:
        print(f"[WARNING] QA Cafeteria 설정 로드 실패: {e}, 기본값 사용")
        _qa_cafeteria_config = {}
    
    return _qa_cafeteria_config

def load_qa_cafeteria_config() -> Dict[str, Any]:
    """QA Cafeteria Agent 설정을 반환합니다."""
    return _load_qa_cafeteria_config()

# =========================
# 환경 변수 로드
# =========================
load_dotenv()

# =========================
# 환경 변수 유틸리티 함수
# =========================
def _get_env_trimmed(key: str, default: str = "") -> str:
    """환경 변수 조회 (공백 제거)."""
    value = os.getenv(key, default)
    return value.strip() if value else default

def _get_env_int(key: str, default: Optional[int] = None) -> Optional[int]:
    """환경 변수를 정수로 조회."""
    value = _get_env_trimmed(key)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default

def _get_env_float(key: str, default: Optional[float] = None) -> Optional[float]:
    """환경 변수를 실수로 조회."""
    value = _get_env_trimmed(key)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default

def _get_env_bool(key: str, default: bool = False) -> bool:
    """환경 변수를 불린으로 조회."""
    value = _get_env_trimmed(key)
    if not value:
        return default
    return value.lower() in ("true", "1", "yes", "on")

# =========================
# Milvus 연결 설정 (.env 우선, YAML fallback)
# =========================
def _get_milvus_config() -> Dict[str, Any]:
    """Milvus 설정을 .env에서 우선 로드, 없으면 YAML에서 로드."""
    config = load_qa_cafeteria_config()
    
    # .env 우선, 없으면 YAML 기본값
    milvus_config = config.get("milvus", {})
    
    host = _get_env_trimmed("HOST") or _get_env_trimmed("MILVUS_HOST") or milvus_config.get("host", "")
    port_env = _get_env_int("MILVUS_PORT")
    port = port_env if port_env is not None else milvus_config.get("port", 19530)
    collection = (
        _get_env_trimmed("CAFETERIA_COLLECTION") or 
        _get_env_trimmed("MILVUS_COLLECTION_NAME_CAFETERIA") or
        milvus_config.get("collection_name", "")
    )
    
    return {
        "host": host,
        "port": port,
        "collection_name": collection
    }

_milvus_config = _get_milvus_config()
MILVUS_HOST = _milvus_config["host"]
MILVUS_PORT = _milvus_config["port"]
COLLECTION_NAME_MENU = _milvus_config["collection_name"]

# =========================
# 싱글톤 패턴으로 초기화 (한 번만 실행)
# =========================
_collection: Optional[Collection] = None
_embedding_model = None  # OpenAIEmbeddingGenerator 인스턴스
_rag_app = None  # LangGraph 앱 캐시


def _initialize_connections() -> tuple[Collection, SentenceTransformer]:
    """Milvus 연결 및 모델 초기화 (싱글톤)."""
    global _collection, _embedding_model

    if _collection is not None and _embedding_model is not None:
        return _collection, _embedding_model

    try:
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
        _collection = Collection(COLLECTION_NAME_MENU)
        _collection.load()
    except Exception as e:
        raise RuntimeError(f"Milvus 연결 실패: {e}")

    try:
        # 임베딩 모델은 get_embedding_generator()가 .env의 EMBEDDING_MODEL을 자동으로 읽음
        _embedding_model = get_embedding_generator()
    except Exception as e:
        raise RuntimeError(f"임베딩 모델 로딩 실패: {e}")

    return _collection, _embedding_model


# =========================
# LangGraph State 정의
# =========================
class GraphState(TypedDict):
    """RAG 그래프 상태."""
    question: str
    query_embedding: List[float]
    query_keywords: List[str]
    vector_search_results: List[Dict]
    keyword_search_results: List[Dict]
    retrieved_contexts: List[str]
    answer: str
    timings: Dict[str, float]
    conversation_history: Optional[List[Dict[str, str]]]  # 이전 대화 히스토리 (Redis 숏텀 메모리)
    current_date: Optional[str]  # 현재 날짜 (예: "2024년 12월 10일") - orchestrator에서 설정됨
    current_time: Optional[str]  # 현재 시간 (예: "14시 30분") - orchestrator에서 설정됨
    current_datetime: Optional[str]  # ISO 형식 (예: "2024-12-10T14:30:00") - orchestrator에서 설정됨
    current_weekday: Optional[str]  # 현재 요일 (예: "월요일") - orchestrator에서 설정됨
    calculated_date: Optional[str]  # 계산된 상대적 날짜 (예: "2025년 12월 15일") - analyze_query에서 설정됨


# =========================
# 키워드 추출 함수
# =========================
def extract_keywords(text: str, min_length: Optional[int] = None, top_count: Optional[int] = None) -> List[str]:
    """텍스트에서 키워드 추출 (한글, 영문, 숫자).

    Args:
        text: 추출할 텍스트
        min_length: 최소 키워드 길이 (None이면 YAML에서 로드)
        top_count: 상위 키워드 개수 (None이면 YAML에서 로드)

    Returns:
        추출된 키워드 리스트
    """
    # 키워드 추출 파라미터 (.env 우선, YAML fallback)
    if min_length is None or top_count is None:
        config = load_qa_cafeteria_config()
        search_params = config.get("search_params", {})
        if min_length is None:
            min_length_env = _get_env_int("KEYWORD_MIN_LENGTH")
            min_length = min_length_env if min_length_env is not None else search_params.get("keyword_min_length", 2)
        if top_count is None:
            top_count_env = _get_env_int("KEYWORD_TOP_COUNT")
            top_count = top_count_env if top_count_env is not None else search_params.get("keyword_top_count", 10)
    
    pattern = r'[가-힣a-zA-Z0-9]+'
    keywords = re.findall(pattern, text.lower())
    keywords = [kw for kw in keywords if len(kw) >= min_length]
    keyword_counts = Counter(keywords)
    return [kw for kw, _ in keyword_counts.most_common(top_count)]


# =========================
# 키워드 점수 계산 함수
# =========================
def calculate_keyword_score(text: str, keywords: List[str]) -> float:
    """문서에서 키워드 매칭 점수 계산.

    Args:
        text: 문서 텍스트
        keywords: 검색 키워드 리스트

    Returns:
        키워드 매칭 점수
    """
    # 키워드 점수 가중치 (.env 우선, YAML fallback)
    config = load_qa_cafeteria_config()
    search_params = config.get("search_params", {})
    exact_match_score_env = _get_env_float("KEYWORD_EXACT_MATCH_SCORE")
    exact_match_score = exact_match_score_env if exact_match_score_env is not None else search_params.get("keyword_exact_match_score", 2.0)
    
    count_multiplier_env = _get_env_float("KEYWORD_COUNT_MULTIPLIER")
    count_multiplier = count_multiplier_env if count_multiplier_env is not None else search_params.get("keyword_count_multiplier", 0.1)
    
    substring_score_env = _get_env_float("KEYWORD_SUBSTRING_SCORE")
    substring_score = substring_score_env if substring_score_env is not None else search_params.get("keyword_substring_score", 0.1)
    
    text_lower = text.lower()
    score = 0.0

    for keyword in keywords:
        if keyword in text_lower:
            score += exact_match_score
            score += text_lower.count(keyword) * count_multiplier

        if len(keyword) > 3:
            for i in range(len(keyword) - 2):
                substring = keyword[i:i+3]
                if substring in text_lower:
                    score += substring_score

    return score


# =========================
# LangGraph 노드 함수들
# =========================
def _get_date_config() -> tuple[str, str, List[str]]:
    """날짜 설정을 YAML에서 로드합니다. (공통 유틸리티)
    
    Returns:
        (date_format, time_format, weekday_names) 튜플
    """
    config = load_qa_cafeteria_config()
    date_time_config = config.get("date_time", {})
    weekday_names = date_time_config.get("weekday_names", ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"])
    date_format = date_time_config.get("date_format", "%Y년 %m월 %d일")
    time_format = date_time_config.get("time_format", "%H시 %M분")
    return date_format, time_format, weekday_names


def analyze_query(state: GraphState, collection: Collection, embedding_model) -> GraphState:
    """사용자 질문을 임베딩 벡터로 변환하고 키워드 추출.
    
    상대적 날짜 표현이 있으면 계산된 날짜를 검색 쿼리에 포함시킵니다.
    orchestrator에서 이미 설정된 날짜 정보를 사용합니다.
    """
    start_time = time.time()
    question = state["question"]
    
    # orchestrator에서 이미 설정된 날짜 정보 사용 (fallback 없음 - orchestrator가 항상 설정함)
    current_date = state.get("current_date")
    if not current_date:
        # 매우 드문 경우를 위한 fallback (orchestrator가 날짜를 설정하지 않은 경우)
        from datetime import datetime
        date_format, _, weekday_names = _get_date_config()
        now = datetime.now()
        current_date = now.strftime(date_format)
        print(f"⚠️ orchestrator에서 날짜 정보를 받지 못함, 직접 계산: {current_date}")
    else:
        date_format, _, weekday_names = _get_date_config()
    
    # 질문에서 상대적 날짜 계산
    calculated_date = _calculate_relative_date(question, current_date, weekday_names, date_format)
    
    # 검색 쿼리 생성: 계산된 날짜가 있으면 검색 쿼리에 명시적으로 포함
    search_query = question
    if calculated_date:
        # 날짜를 검색 쿼리에 추가 (예: "15일 식단")
        try:
            from datetime import datetime
            parsed_date = datetime.strptime(calculated_date, date_format)
            day_only = f"{parsed_date.day}일"  # "15일"
            search_query = f"{day_only} {question}"
            print(f"✅ 검색 쿼리에 날짜 추가: '{question}' → '{search_query}' (계산된 날짜: {calculated_date})")
        except Exception as e:
            print(f"⚠️ 날짜 파싱 실패, 원본 질문 사용: {e}")
            search_query = question

    query_embedding = embedding_model.embed(search_query)
    query_keywords = extract_keywords(search_query)

    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["analyze_query"] = elapsed_time

    return {
        "query_embedding": query_embedding,
        "query_keywords": query_keywords,
        "calculated_date": calculated_date,  # state에 저장하여 generate_answer에서 재사용
        "timings": timings
    }


def vector_search(state: GraphState, collection: Collection) -> GraphState:
    """Milvus에서 벡터 기반 유사 문서 검색."""
    start_time = time.time()
    query_embedding = state["query_embedding"]

    # 검색 파라미터 (.env 우선, YAML fallback)
    config = load_qa_cafeteria_config()
    search_params = config.get("search_params", {})
    vector_search_limit_env = _get_env_int("VECTOR_SEARCH_LIMIT")
    vector_search_limit = vector_search_limit_env if vector_search_limit_env is not None else search_params.get("vector_search_limit", 15)
    
    milvus_metric_type = (
        _get_env_trimmed("MILVUS_METRIC_TYPE") or 
        search_params.get("milvus_metric_type", "L2")
    )
    
    milvus_nprobe_env = _get_env_int("MILVUS_NPROBE")
    milvus_nprobe = milvus_nprobe_env if milvus_nprobe_env is not None else search_params.get("milvus_nprobe", 10)
    
    search_results = collection.search(
        data=[query_embedding],
        anns_field="embedding",
        param={"metric_type": milvus_metric_type, "params": {"nprobe": milvus_nprobe}},
        limit=vector_search_limit,
        output_fields=["content"]
    )

    vector_results = []
    for hit in search_results[0]:
        content = hit.entity.get('content')
        distance = hit.distance
        score = 1.0 / (1.0 + distance)
        vector_results.append({
            "content": content,
            "score": score,
            "distance": distance,
            "source": "vector"
        })

    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["vector_search"] = elapsed_time

    return {
        "vector_search_results": vector_results,
        "timings": timings
    }


def keyword_search(state: GraphState) -> GraphState:
    """키워드 기반 문서 검색."""
    start_time = time.time()
    query_keywords = state["query_keywords"]
    vector_results = state.get("vector_search_results", [])

    if not query_keywords or not vector_results:
        elapsed_time = time.time() - start_time
        timings = state.get("timings", {})
        timings["keyword_search"] = elapsed_time
        return {
            "keyword_search_results": [],
            "timings": timings
        }

    keyword_results = []
    for result in vector_results:
        content = result["content"]
        keyword_score = calculate_keyword_score(content, query_keywords)
        keyword_results.append({
            "content": content,
            "score": keyword_score,
            "source": "keyword"
        })

    keyword_results.sort(key=lambda x: x["score"], reverse=True)

    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["keyword_search"] = elapsed_time

    return {
        "keyword_search_results": keyword_results,
        "timings": timings
    }


def hybrid_merge(state: GraphState) -> GraphState:
    """벡터 검색과 키워드 검색 결과를 병합하여 최종 결과 생성."""
    start_time = time.time()
    vector_results = state.get("vector_search_results", [])
    keyword_results = state.get("keyword_search_results", [])

    merged_results: Dict[str, Dict] = {}

    for result in vector_results:
        content = result["content"]
        if content not in merged_results:
            merged_results[content] = {
                "content": content,
                "vector_score": result["score"],
                "keyword_score": 0.0,
                "source": "vector"
            }
        else:
            merged_results[content]["vector_score"] = result["score"]

    for result in keyword_results:
        content = result["content"]
        if content not in merged_results:
            merged_results[content] = {
                "content": content,
                "vector_score": 0.0,
                "keyword_score": result["score"],
                "source": "keyword"
            }
        else:
            merged_results[content]["keyword_score"] = result["score"]
            merged_results[content]["source"] = "hybrid"

    # 하이브리드 검색 가중치 (.env 우선, YAML fallback)
    config = load_qa_cafeteria_config()
    hybrid_search = config.get("hybrid_search", {})
    vector_weight_env = _get_env_float("HYBRID_SEARCH_VECTOR_WEIGHT")
    VECTOR_WEIGHT = vector_weight_env if vector_weight_env is not None else hybrid_search.get("vector_weight", 0.6)
    
    keyword_weight_env = _get_env_float("HYBRID_SEARCH_KEYWORD_WEIGHT")
    KEYWORD_WEIGHT = keyword_weight_env if keyword_weight_env is not None else hybrid_search.get("keyword_weight", 0.4)

    if vector_results:
        max_vector_score = max(r["score"] for r in vector_results)
        min_vector_score = min(r["score"] for r in vector_results)
        vector_range = max_vector_score - min_vector_score if max_vector_score > min_vector_score else 1.0
    else:
        vector_range = 1.0

    if keyword_results:
        max_keyword_score = max(r["score"] for r in keyword_results)
        min_keyword_score = min(r["score"] for r in keyword_results)
        keyword_range = max_keyword_score - min_keyword_score if max_keyword_score > min_keyword_score else 1.0
    else:
        keyword_range = 1.0

    final_results = []
    for content, result in merged_results.items():
        normalized_vector = (result["vector_score"] - (min_vector_score if vector_results else 0)) / vector_range if vector_range > 0 else 0
        normalized_keyword = (result["keyword_score"] - (min_keyword_score if keyword_results else 0)) / keyword_range if keyword_range > 0 else 0

        hybrid_score = (normalized_vector * VECTOR_WEIGHT) + (normalized_keyword * KEYWORD_WEIGHT)

        final_results.append({
            "content": content,
            "hybrid_score": hybrid_score,
            "vector_score": normalized_vector,
            "keyword_score": normalized_keyword,
            "source": result["source"]
        })

    final_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
    # 상위 결과 개수 (.env 우선, YAML fallback)
    config = load_qa_cafeteria_config()
    search_params = config.get("search_params", {})
    top_results_count_env = _get_env_int("TOP_RESULTS_COUNT")
    top_results_count = top_results_count_env if top_results_count_env is not None else search_params.get("top_results_count", 7)
    top_results = final_results[:top_results_count]
    retrieved_contexts = [r["content"] for r in top_results]

    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["hybrid_merge"] = elapsed_time

    return {
        "retrieved_contexts": retrieved_contexts,
        "timings": timings
    }


def _calculate_relative_date(question: str, current_date_str: str, weekday_names: List[str], date_format: str) -> Optional[str]:
    """질문에서 상대적 날짜 표현을 감지하고 정확한 날짜를 계산합니다.
    
    Args:
        question: 사용자 질문
        current_date_str: 현재 날짜 문자열 (예: "2025년 12월 12일")
        weekday_names: 요일 이름 리스트
        date_format: 날짜 형식 문자열
        
    Returns:
        계산된 날짜 문자열 또는 None
    """
    from datetime import datetime, timedelta
    
    try:
        # 현재 날짜 파싱 및 기준점(이번 주 월요일) 설정
        current_date = datetime.strptime(current_date_str, date_format)
        current_weekday = current_date.weekday()  # 0=월요일, 6=일요일
        
        # 이번 주 월요일 계산
        this_week_monday = current_date - timedelta(days=current_weekday)
        
        # 질문 전처리 (띄어쓰기 정규화)
        question_clean = question.replace(" ", "").lower()
        
        # -------------------------------------------------------
        # Case A: 단순 상대어 (오늘, 내일, 모레)
        # -------------------------------------------------------
        if "오늘" in question_clean:
            return current_date_str
        
        if "내일모레" in question_clean or "내일모래" in question_clean:
            return (current_date + timedelta(days=2)).strftime(date_format)
        
        if "내일" in question_clean and "모레" not in question_clean and "모래" not in question_clean:
            return (current_date + timedelta(days=1)).strftime(date_format)
        
        if ("모레" in question_clean or "모래" in question_clean) and "내일모레" not in question_clean and "내일모래" not in question_clean:
            return (current_date + timedelta(days=2)).strftime(date_format)
        
        # -------------------------------------------------------
        # Case B: 특정 주 + 요일 (예: "다음 주 금요일")
        # -------------------------------------------------------
        target_weekday_idx = -1
        for idx, weekday_name in enumerate(weekday_names):
            weekday_short = weekday_name.replace("요일", "")  # "월", "화", "수" 등
            if weekday_short in question_clean or weekday_name in question_clean:
                target_weekday_idx = idx
                break
        
        # "다음주" + [요일]
        if "다음주" in question_clean and target_weekday_idx != -1:
            target_date = this_week_monday + timedelta(weeks=1) + timedelta(days=target_weekday_idx)
            return target_date.strftime(date_format)
        
        # "이번주" + [요일]
        if "이번주" in question_clean and target_weekday_idx != -1:
            target_date = this_week_monday + timedelta(days=target_weekday_idx)
            return target_date.strftime(date_format)
        
        # -------------------------------------------------------
        # Case C: 요일 없이 범위만 물어볼 때 (예: "이번 주 일정 어때?")
        # -------------------------------------------------------
        # "다음주" 범위
        if "다음주" in question_clean:
            start_date = this_week_monday + timedelta(weeks=1)
            end_date = start_date + timedelta(days=6)  # 일요일까지
            return f"{start_date.strftime(date_format)}부터 {end_date.strftime(date_format)}까지"
        
        # "이번주" 범위
        if "이번주" in question_clean:
            start_date = this_week_monday
            end_date = start_date + timedelta(days=6)  # 일요일까지
            return f"{start_date.strftime(date_format)}부터 {end_date.strftime(date_format)}까지"
        
        return None
    except Exception as e:
        print(f"⚠️ 날짜 계산 오류: {e}")
        return None


def generate_answer(state: GraphState) -> GraphState:
    """검색된 컨텍스트를 바탕으로 GPT가 답변 생성.
    
    orchestrator에서 이미 설정된 날짜 정보와 analyze_query에서 계산된 날짜를 사용합니다.
    """
    start_time = time.time()
    question = state["question"]
    contexts = state["retrieved_contexts"]

    context_text = "\n\n".join([f"[문서 {i+1}]\n{ctx}" for i, ctx in enumerate(contexts)])
    conversation_history = state.get("conversation_history")
    
    # orchestrator에서 이미 설정된 날짜/시간 정보 사용 (fallback 없음)
    current_date = state.get("current_date", "")
    current_time = state.get("current_time", "")
    current_weekday = state.get("current_weekday", "")
    
    # analyze_query에서 이미 계산된 날짜 사용 (재계산 불필요)
    calculated_date = state.get("calculated_date")
    
    # 날짜 정보가 없으면 매우 드문 경우를 위한 fallback
    if not current_date:
        from datetime import datetime
        date_format, time_format, weekday_names = _get_date_config()
        now = datetime.now()
        current_date = now.strftime(date_format)
        current_time = now.strftime(time_format)
        current_weekday = weekday_names[now.weekday()]
        print(f"⚠️ orchestrator에서 날짜 정보를 받지 못함, 직접 계산: {current_date}")
    
    # calculated_date가 없으면 (analyze_query에서 계산하지 않은 경우) 여기서 계산
    if not calculated_date:
        date_format, _, weekday_names = _get_date_config()
        calculated_date = _calculate_relative_date(question, current_date, weekday_names, date_format)
    
    date_info_text = f"**현재 날짜와 시간: {current_date} ({current_weekday}) {current_time}**"
    
    if calculated_date:
        # 범위 문자열인지 확인 ("~부터 ~까지" 형식)
        if "부터" in calculated_date and "까지" in calculated_date:
            # 범위 문자열이면 그대로 사용
            date_info_text += f"\n\n**⚠️ 매우 중요: 사용자가 요청한 날짜 범위는 {calculated_date}입니다.**"
            date_info_text += f"\n**반드시 식단표에서 {calculated_date}의 식단만 찾아 답변하세요.**"
            date_info_text += f"\n**다른 날짜의 식단을 반환하지 마세요. 오직 {calculated_date}의 식단만 찾아주세요.**"
            date_info_text += f"\n**⚠️ 검색된 컨텍스트에 {calculated_date} 범위 내의 날짜가 일부 없으면, 식단이 있는 날짜만 답변하세요.**"
            date_info_text += f"\n**⚠️ 검색된 컨텍스트에 {calculated_date} 범위 내의 날짜가 모두 없으면, 절대 답변을 생성하지 말고 '해당 날짜의 식단 정보는 없습니다.'라고 답변하세요.**"
            print(f"✅ 프롬프트에 날짜 범위 포함: {calculated_date}")
        else:
            # 단일 날짜인 경우 기존 로직 사용
            from datetime import datetime
            date_format, _, weekday_names = _get_date_config()
            try:
                calculated_datetime = datetime.strptime(calculated_date, date_format)
                calculated_weekday = weekday_names[calculated_datetime.weekday()]
                # 계산된 날짜를 더 강조하여 프롬프트에 포함 (요일 포함)
                date_info_text += f"\n\n**⚠️ 매우 중요: 사용자가 요청한 정확한 날짜는 {calculated_date} ({calculated_weekday})입니다.**"
                date_info_text += f"\n**반드시 식단표에서 {calculated_date} ({calculated_weekday})의 식단만 찾아 답변하세요.**"
                date_info_text += f"\n**다른 날짜(예: 18일, 19일 등)나 다른 요일의 식단을 반환하지 마세요. 오직 {calculated_date} ({calculated_weekday})의 식단만 찾아주세요.**"
                date_info_text += f"\n**⚠️ 검색된 컨텍스트에 {calculated_date} ({calculated_weekday})의 식단이 없으면, 절대 답변을 생성하지 말고 '해당 날짜의 식단 정보는 없습니다.' 또는 '요청하신 날짜의 식단 정보가 식단표에 등록되어 있지 않습니다.'라고만 답변하세요.**"
                print(f"✅ 프롬프트에 날짜 및 요일 포함: {calculated_date} ({calculated_weekday})")
            except Exception as e:
                # 날짜 파싱 실패 시 요일 없이 날짜만 포함
                date_info_text += f"\n\n**⚠️ 매우 중요: 사용자가 요청한 정확한 날짜는 {calculated_date}입니다.**"
                date_info_text += f"\n**반드시 식단표에서 {calculated_date}의 식단만 찾아 답변하세요.**"
                date_info_text += f"\n**다른 날짜(예: 18일, 19일 등)의 식단을 반환하지 마세요. 오직 {calculated_date}의 식단만 찾아주세요.**"
                date_info_text += f"\n**⚠️ 검색된 컨텍스트에 {calculated_date}의 식단이 없으면, 절대 답변을 생성하지 말고 '해당 날짜의 식단 정보는 없습니다.' 또는 '요청하신 날짜의 식단 정보가 식단표에 등록되어 있지 않습니다.'라고만 답변하세요.**"
                print(f"⚠️ 날짜 파싱 실패, 요일 없이 날짜만 포함: {e}")

    # 프롬프트 및 설정 (YAML에서 로드)
    config = load_qa_cafeteria_config()
    prompts = config.get("prompts", {})
    
    # YAML에서 system_prompt 로드 (YAML에 정의되어 있어야 함)
    system_prompt_template = prompts.get("system_prompt")
    if not system_prompt_template:
        raise ValueError(
            "system_prompt가 YAML 설정 파일에 정의되어 있지 않습니다. "
            "agents/master_graphs/qa/prompts.yaml 파일의 qa_cafeteria.prompts.system_prompt를 확인하세요."
        )
    
    # 시스템 프롬프트에 날짜/시간 정보 치환
    system_prompt = system_prompt_template.replace("{date_info}", date_info_text)
    
    # 남은 중괄호를 이스케이프 처리 (ChatPromptTemplate 파싱 오류 방지)
    # 단, {context}와 {question}은 user_prompt에서 사용되므로 그대로 유지
    system_prompt = system_prompt.replace("{", "{{").replace("}", "}}")
    # 하지만 {context}와 {question}은 다시 원래대로 (user_prompt에서 사용)
    # system_prompt에는 {context}와 {question}이 없으므로 문제없음
    
    # 이전 대화 히스토리 구성
    messages = [
        ("system", system_prompt),
    ]
    
    # 이전 대화 히스토리 추가 (YAML 설정 사용)
    # conversation_history의 메시지 내용에 중괄호가 있을 수 있으므로 이스케이프 처리
    search_params = config.get("search_params", {})
    conversation_history_max = search_params.get("conversation_history_max_messages", 5)
    if conversation_history:
        # 질문에서 관련 키워드 추출 (식단 관련 질문인지 확인)
        question_lower = question.lower()
        
        # YAML에서 키워드 로드
        conversation_filtering = config.get("conversation_filtering", {})
        cafeteria_keywords = conversation_filtering.get("cafeteria_keywords", ["식단", "메뉴", "식사", "밥", "점심", "저녁", "아침", "식당", "구내식당", "카페테리아"])
        history_check_keywords = conversation_filtering.get("history_check_keywords", ["요청한적", "요청한 적", "요청했어", "한적", "한 적", "했어", "했나", "이전에", "전에", "아까", "요청"])
        
        is_cafeteria_question = any(keyword in question_lower for keyword in cafeteria_keywords)
        
        # 이전 대화 확인 질문인지 확인
        is_history_check = any(keyword in question_lower for keyword in history_check_keywords)
        
        # 필터링된 대화 히스토리
        filtered_history = []
        
        if is_history_check and is_cafeteria_question:
            # 이전 대화 확인 질문이고 식단 관련이면, 식단 관련 대화만 필터링
            # 더 넓은 범위에서 검색 (최근 10개 메시지)
            for msg in conversation_history[-conversation_history_max * 2:]:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role and content:
                    content_lower = content.lower()
                    # 식단 관련 키워드가 있는 대화만 포함
                    if any(keyword in content_lower for keyword in cafeteria_keywords):
                        filtered_history.append(msg)
                        if len(filtered_history) >= conversation_history_max:
                            break
        else:
            # 일반 질문이면 최근 대화만 사용
            filtered_history = conversation_history[-conversation_history_max:]
        
        # 필터링된 대화 히스토리를 messages에 추가
        for msg in filtered_history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                # content의 중괄호를 이스케이프 처리 (템플릿 변수로 인식되지 않도록)
                # 단, 이미 이스케이프된 경우는 제외
                escaped_content = content.replace("{", "{{").replace("}", "}}")
                messages.append((role, escaped_content))
    
    # YAML에서 user_prompt 로드 (YAML에 정의되어 있어야 함)
    user_prompt_template = prompts.get("user_prompt")
    if not user_prompt_template:
        raise ValueError(
            "user_prompt가 YAML 설정 파일에 정의되어 있지 않습니다. "
            "agents/master_graphs/qa/prompts.yaml 파일의 qa_cafeteria.prompts.user_prompt를 확인하세요."
        )
    
    # 계산된 날짜가 있으면 user_prompt에도 명시적으로 포함
    if calculated_date:
        # 범위 문자열인지 확인
        if "부터" in calculated_date and "까지" in calculated_date:
            # 범위 문자열이면 그대로 사용
            user_prompt_template += f"\n\n**중요: 반드시 {calculated_date}의 식단만 찾아 답변하세요. 다른 날짜의 식단을 반환하지 마세요.**"
        else:
            # 단일 날짜인 경우 요일도 포함
            from datetime import datetime
            date_format, _, weekday_names = _get_date_config()
            try:
                calculated_datetime = datetime.strptime(calculated_date, date_format)
                calculated_weekday = weekday_names[calculated_datetime.weekday()]
                user_prompt_template += f"\n\n**중요: 반드시 {calculated_date} ({calculated_weekday})의 식단만 찾아 답변하세요. 다른 날짜나 다른 요일의 식단을 반환하지 마세요.**"
            except Exception as e:
                user_prompt_template += f"\n\n**중요: 반드시 {calculated_date}의 식단만 찾아 답변하세요. 다른 날짜의 식단을 반환하지 마세요.**"
    
    messages.append(("user", user_prompt_template))
    
    prompt_template = ChatPromptTemplate.from_messages(messages)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OpenAI API 키가 없습니다. .env 파일에 OPENAI_API_KEY를 설정하세요.")

    # LLM 설정 (.env 우선, 없으면 기본값 사용)
    llm_model = (
        _get_env_trimmed("OPENAI_MODEL")
    )

    llm = ChatOpenAI(
        model=llm_model,
        temperature=0,
        api_key=api_key
    )

    chain = prompt_template | llm
    response = chain.invoke({
        "context": context_text,
        "question": question
    })

    answer = response.content
    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["generate_answer"] = elapsed_time

    return {
        "answer": answer,
        "timings": timings
    }


# =========================
# LangGraph 그래프 생성
# =========================
def _create_rag_graph(collection: Collection, embedding_model):
    """하이브리드 RAG 그래프 생성."""
    workflow = StateGraph(GraphState)

    # 클로저를 사용하여 collection과 embedding_model을 노드 함수에 전달
    def analyze_with_deps(state: GraphState) -> GraphState:
        return analyze_query(state, collection, embedding_model)

    def vector_search_with_deps(state: GraphState) -> GraphState:
        return vector_search(state, collection)

    workflow.add_node("analyze", analyze_with_deps)
    workflow.add_node("vector_search", vector_search_with_deps)
    workflow.add_node("keyword_search", keyword_search)
    workflow.add_node("hybrid_merge", hybrid_merge)
    workflow.add_node("generate", generate_answer)

    workflow.set_entry_point("analyze")
    workflow.add_edge("analyze", "vector_search")
    workflow.add_edge("vector_search", "keyword_search")
    workflow.add_edge("keyword_search", "hybrid_merge")
    workflow.add_edge("hybrid_merge", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile()


# =========================
# 메인 함수: cafeteria_menu
# =========================
async def cafeteria_menu(
    user_text: str,
    orchestrator_state: Optional[Dict[str, Any]] = None,
) -> Dict:
    """구내식당 식단표 질문에 대한 RAG 기반 답변 생성.

    Args:
        user_text: 사용자 질문
        orchestrator_state: OrchestratorState 딕셔너리 (Redis 숏텀 메모리용)

    Returns:
        {
            "ok": bool,
            "message": str,  # AI 답변
            "retrieved_contexts": List[str],  # 검색된 문서 (선택적)
            "timings": Dict[str, float],  # 처리 시간 정보 (선택적)
            "echo": str  # 사용자 입력 반복
        }
    """
    global _rag_app

    try:
        # 초기화
        collection, embedding_model = _initialize_connections()

        # LangGraph 앱 캐시
        if _rag_app is None:
            _rag_app = _create_rag_graph(collection, embedding_model)

        # conversation_history 추출
        conversation_history = None
        if orchestrator_state:
            conv_history = orchestrator_state.get("conversation_history", [])
            if conv_history:
                # Message 객체를 dict로 변환
                conversation_history = [
                    {
                        "role": msg.get("role", ""),
                        "content": msg.get("content", ""),
                    }
                    for msg in conv_history
                    if isinstance(msg, dict) and msg.get("role") and msg.get("content")
                ]

        # OrchestratorState에서 날짜/시간 정보 가져오기 (orchestrator가 이미 설정함)
        current_date = orchestrator_state.get("current_date") if orchestrator_state else None
        current_time = orchestrator_state.get("current_time") if orchestrator_state else None
        current_datetime = orchestrator_state.get("current_datetime") if orchestrator_state else None
        current_weekday = orchestrator_state.get("current_weekday") if orchestrator_state else None
        
        # 매우 드문 경우를 위한 fallback (orchestrator가 날짜를 설정하지 않은 경우)
        if not current_date:
            from datetime import datetime
            date_format, time_format, weekday_names = _get_date_config()
            now = datetime.now()
            current_date = now.strftime(date_format)
            current_time = now.strftime(time_format)
            current_datetime = now.isoformat()
            current_weekday = weekday_names[now.weekday()]
            print(f"⚠️ orchestrator에서 날짜 정보를 받지 못함, 직접 계산: {current_date}")

        # 초기 상태 설정
        initial_state: GraphState = {
            "question": user_text,
            "query_embedding": [],
            "query_keywords": [],
            "vector_search_results": [],
            "keyword_search_results": [],
            "retrieved_contexts": [],
            "answer": "",
            "timings": {},
            "conversation_history": conversation_history,
            "current_date": current_date,
            "current_time": current_time,
            "current_datetime": current_datetime,
            "current_weekday": current_weekday,
        }

        # 그래프 실행 (비동기)
        result = await _rag_app.ainvoke(initial_state)

        # 답변 텍스트 추출
        answer_text = result.get("answer", "답변을 생성할 수 없습니다.")
        
        # Redis에 검색 컨텍스트 및 답변 저장
        if orchestrator_state is not None:
            # qa_search_context 초기화 (None 체크 포함)
            if "qa_search_context" not in orchestrator_state or orchestrator_state["qa_search_context"] is None:
                orchestrator_state["qa_search_context"] = {}
            if not isinstance(orchestrator_state.get("qa_search_context"), dict):
                orchestrator_state["qa_search_context"] = {}
            
            # 검색 컨텍스트 저장 (YAML 설정 사용)
            config = load_qa_cafeteria_config()
            search_params = config.get("search_params", {})
            retrieved_contexts_max = search_params.get("retrieved_contexts_max_count", 3)
            
            orchestrator_state["qa_search_context"]["cafeteria"] = {
                "last_query": user_text,
                "last_answer": answer_text,
                "retrieved_contexts": result.get("retrieved_contexts", [])[:retrieved_contexts_max],
            }
            
            # conversation_history에 답변 추가 (Redis 저장용)
            if "conversation_history" not in orchestrator_state:
                orchestrator_state["conversation_history"] = []
            if not isinstance(orchestrator_state.get("conversation_history"), list):
                orchestrator_state["conversation_history"] = []
            
            # 사용자 메시지가 없으면 추가
            conversation_history = orchestrator_state["conversation_history"]
            if not conversation_history or conversation_history[-1].get("role") != "user":
                from datetime import datetime
                conversation_history.append({
                    "role": "user",
                    "content": user_text,
                    "timestamp": datetime.now().isoformat()
                })
            
            # Assistant 답변 추가
            from datetime import datetime
            conversation_history.append({
                "role": "assistant",
                "content": answer_text,
                "timestamp": datetime.now().isoformat()
            })

        # 반환할 컨텍스트 개수 (YAML 설정 사용)
        config = load_qa_cafeteria_config()
        search_params = config.get("search_params", {})
        retrieved_contexts_max = search_params.get("retrieved_contexts_max_count", 3)
        
        return {
            "ok": True,
            "message": answer_text,
            "retrieved_contexts": result.get("retrieved_contexts", [])[:retrieved_contexts_max],
            "timings": result.get("timings", {}),
            "echo": user_text
        }

    except Exception as e:
        import traceback
        error_msg = f"식단표 검색 중 오류 발생: {str(e)}"
        print(f"[qa_cafeteria_agent 오류] {error_msg}")
        traceback.print_exc()

        return {
            "ok": False,
            "message": error_msg,
            "echo": user_text
        }






