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
        current_dir = Path(__file__).parent.parent.parent.parent
        yaml_path = current_dir / "agents" / "agent_prompts.yaml"
        
        if not yaml_path.exists():
            # Fallback: 상대 경로로 시도
            yaml_path = Path(__file__).parent.parent.parent / "agent_prompts.yaml"
        
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                all_config = yaml.safe_load(f)
            _qa_cafeteria_config = all_config.get("qa_cafeteria", {})
        else:
            _qa_cafeteria_config = {}
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
    current_date: Optional[str]  # 현재 날짜 (예: "2024년 12월 10일")
    current_time: Optional[str]  # 현재 시간 (예: "14시 30분")
    current_datetime: Optional[str]  # ISO 형식 (예: "2024-12-10T14:30:00")
    current_weekday: Optional[str]  # 현재 요일 (예: "월요일")


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
def analyze_query(state: GraphState, collection: Collection, embedding_model) -> GraphState:
    """사용자 질문을 임베딩 벡터로 변환하고 키워드 추출."""
    start_time = time.time()
    question = state["question"]

    query_embedding = embedding_model.embed(question)
    query_keywords = extract_keywords(question)

    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["analyze_query"] = elapsed_time

    return {
        "query_embedding": query_embedding,
        "query_keywords": query_keywords,
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


def generate_answer(state: GraphState) -> GraphState:
    """검색된 컨텍스트를 바탕으로 GPT가 답변 생성."""
    start_time = time.time()
    question = state["question"]
    contexts = state["retrieved_contexts"]

    context_text = "\n\n".join([f"[문서 {i+1}]\n{ctx}" for i, ctx in enumerate(contexts)])
    conversation_history = state.get("conversation_history")
    
    # state에서 날짜/시간 정보 가져오기
    current_date = state.get("current_date", "")
    current_time = state.get("current_time", "")
    current_weekday = state.get("current_weekday", "")
    
    # 날짜 정보가 없으면 기본값 사용 (fallback, YAML 설정 사용)
    if not current_date:
        from datetime import datetime
        config = load_qa_cafeteria_config()
        date_time_config = config.get("date_time", {})
        weekday_names = date_time_config.get("weekday_names", ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"])
        date_format = date_time_config.get("date_format", "%Y년 %m월 %d일")
        time_format = date_time_config.get("time_format", "%H시 %M분")
        
        now = datetime.now()
        current_date = now.strftime(date_format)
        current_time = now.strftime(time_format)
        current_weekday = weekday_names[now.weekday()]

    # 프롬프트 및 설정 (YAML에서 로드)
    config = load_qa_cafeteria_config()
    prompts = config.get("prompts", {})
    system_prompt_template = prompts.get("system_prompt", """당신은 깐부전자의 직원 식단표를 안내하는 전문 AI 어시스턴트입니다.

**현재 날짜와 시간: {current_date} ({current_weekday}) {current_time}**

**날짜 계산 규칙:**
- "오늘" → 현재 날짜 ({current_date})
- "내일" → 현재 날짜 + 1일
- "모레" → 현재 날짜 + 2일  
- "내일 모레" → 현재 날짜 + 2일 (내일의 다음 날)
- "다음주 [요일]" → 다음 주의 해당 요일
- "이번주 [요일]" → 이번 주의 해당 요일

제공된 식단표 데이터를 기반으로 직원들의 식단 관련 질문에 대해 정확하고 상세한 답변을 제공하세요.
사용자가 "내일 모레", "다음주 월요일" 같은 상대적 날짜 표현을 사용하면, 위의 현재 날짜를 기준으로 정확한 날짜를 계산한 후 해당 날짜의 식단을 찾아 답변하세요.
답변할 때는 식단표의 내용을 직접 참고하여 답변하며, 날짜, 요일, 메뉴, 칼로리 등의 정보를 명확히 안내하세요.
식단표에 없는 내용이라면, 그렇다고 명확히 말씀해주세요.
이전 대화 내용을 기억하고 자연스럽게 대화하듯이 답변하세요.
답변은 친절하고 이해하기 쉽게 작성해주세요.""")
    
    # 시스템 프롬프트에 날짜/시간 정보 치환
    # 모든 인스턴스를 치환하고, 남은 중괄호를 이스케이프 처리하여 ChatPromptTemplate 파싱 오류 방지
    system_prompt = system_prompt_template.replace("{current_date}", current_date)
    system_prompt = system_prompt.replace("{current_weekday}", current_weekday)
    system_prompt = system_prompt.replace("{current_time}", current_time)
    
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
        for msg in conversation_history[-conversation_history_max:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                # content의 중괄호를 이스케이프 처리 (템플릿 변수로 인식되지 않도록)
                # 단, 이미 이스케이프된 경우는 제외
                escaped_content = content.replace("{", "{{").replace("}", "}}")
                messages.append((role, escaped_content))
    
    user_prompt = prompts.get("user_prompt", """다음은 깐부전자의 직원 식단표 데이터에서 검색된 관련 내용입니다:

{context}

질문: {question}

위 식단표 데이터를 참고하여 질문에 대한 상세하고 실용적인 답변을 한국어로 제공해주세요.
답변에는 가능한 한 구체적인 날짜, 요일, 메뉴 정보, 칼로리 등을 포함해주세요.
이전 대화 내용이 있으면, 그것을 참고하여 자연스럽고 대화하듯이 답변하세요.""")
    messages.append(("user", user_prompt))
    
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

        # OrchestratorState에서 날짜/시간 정보 가져오기
        current_date = orchestrator_state.get("current_date") if orchestrator_state else None
        current_time = orchestrator_state.get("current_time") if orchestrator_state else None
        current_datetime = orchestrator_state.get("current_datetime") if orchestrator_state else None
        current_weekday = orchestrator_state.get("current_weekday") if orchestrator_state else None
        
        # 날짜 정보가 없으면 기본값 사용 (fallback)
        if not current_date:
            from datetime import datetime
            now = datetime.now()
            weekday_names = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
            current_date = now.strftime("%Y년 %m월 %d일")
            current_time = now.strftime("%H시 %M분")
            current_datetime = now.isoformat()
            current_weekday = weekday_names[now.weekday()]

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






