"""LangGraph 노드 정의 모듈."""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Literal

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.graph import StateGraph, END
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

from agents.embedding_utils import get_embedding_generator

from .patent_types import PatentSearchState
from .patent_config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    _notify_progress,
    load_golden_set,
    get_golden_set,
    _set_session_id,
    set_waiting_for_search_mode,
    is_waiting_for_web_search,
    set_waiting_for_web_search,
    get_original_search_keyword,
    set_original_search_keyword,
    generate_web_search_question,
)
# 전역 변수 접근을 위한 모듈 import
import agents.master_graphs.tech.patent.patent_config as patent_config_module
from .patent_kipris_api import kipris_search, kipris_download_pdf
from .patent_pdf_utils import (
    ensure_list,
    clean_text,
    extract_pdf_text,
    save_text_file,
    save_metadata,
)
from .patent_milvus_db import (
    check_vector_db,
    embed_and_store_patent,
    extract_keyword_from_text,
    get_collection_name,
)
from .patent_utils import _extract_keyword
from .patent_keyword import generate_patent_search_phrase

LOGGER = logging.getLogger(__name__)

# 전역 임베딩 모델 (rag_answer_node에서 사용)
_embedding_model = None


def _get_embedding_model():
    """임베딩 모델 가져오기 (지연 초기화)."""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = get_embedding_generator()
    return _embedding_model


def check_db_node(state: PatentSearchState) -> PatentSearchState:
    """벡터DB에 정보가 있는지 확인."""
    query = state["user_query"]
    
    # 검색 모드가 설정되지 않았고, 사용자가 명시적으로 선택하지 않은 경우 질문
    # 단, 이미 검색 모드가 설정되어 있으면 질문하지 않음
    current_search_mode = state.get("search_mode")
    use_server_first = state.get("use_server_first", True)
    LOGGER.info("check_db_node: current_search_mode=%s, use_server_first=%s", current_search_mode, use_server_first)
    
    if current_search_mode is None and use_server_first is True:
        # 첫 검색 시 검색 모드 선택 질문
        LOGGER.info("check_db_node: 검색 모드 선택 질문으로 라우팅")
        state["progress_status"] = "❓ 검색 방식을 선택해주세요."
        state["next_action"] = "ask_search_mode"
        return state

    # 검색 모드가 "web"이면 바로 웹 검색으로
    if current_search_mode == "web":
        LOGGER.info("check_db_node: 웹 검색 모드로 라우팅")
        state["progress_status"] = "🌐 웹 검색 모드로 전환합니다..."
        _notify_progress(state["progress_status"])
        state["next_action"] = "search_kipris"
        return state

    # 서버 기반 검색 모드 (search_mode == "server" 또는 None이지만 use_server_first == True)
    state["progress_status"] = "🔍 벡터DB에서 관련 특허를 검색 중입니다..."
    LOGGER.info(state["progress_status"])
    _notify_progress(state["progress_status"])

    # 특허_키워드 필터링 (세부 원문 요청 시)
    search_keyword = state.get("keyword")
    if not search_keyword:
        # state["keyword"]가 없으면 orchestrator_state에서 저장된 키워드 찾기
        # 저장 시 사용한 키워드와 동일한 키워드 사용
        orchestrator_state = patent_config_module._current_orchestrator_state_for_node
        if orchestrator_state and "tech_search_context" in orchestrator_state:
            tech_context = orchestrator_state.get("tech_search_context", {})
            patent_context = tech_context.get("patent", {})
            search_keyword = patent_context.get("last_keyword")
            if search_keyword:
                LOGGER.info("check_db_node: orchestrator_state에서 저장된 키워드 사용: '%s' (저장 시와 동일)", search_keyword)
    
    # search_keyword는 이미 정규화된 키워드이므로 그대로 전달
    filter_keyword = search_keyword  # "특허_" 접두사 없이 정규화된 키워드만 전달
    if filter_keyword:
        LOGGER.info("check_db_node: filter_keyword 설정: '%s' (정규화된 키워드, 저장 시와 동일)", filter_keyword)
    else:
        LOGGER.warning("check_db_node: 키워드를 찾을 수 없음 - query='%s'", query)

    # 거리 임계값을 더 완화하여 검색 (1.5로 증가)
    results = check_vector_db(query, limit=5, max_distance=1.5, filter_keyword=filter_keyword)

    # 결과가 있고 유효한 경우에만 처리
    if results and len(results) > 0:
        # 구문 기반 필터링: 검색 구문 추출
        search_phrase = generate_patent_search_phrase(query)
        phrase_lower = search_phrase.lower()
        phrase_words = re.findall(r'[\w가-힣]+', phrase_lower)
        
        # 핵심 단어 추출 (불필요한 일반 설명 단어만 제거, 기술 용어는 유지)
        stop_words = {
            "특허", "찾아", "검색", "관련", "정보", "자료", 
            "요청", "알려", "줘", "주세요", "patent", "찾아서", "검색해", "검색해줘",
            "기술", "현황", "동향"  # 일반적인 설명 단어만 제거
        }
        main_keywords = [
            word for word in phrase_words 
            if len(word) > 1 and word not in stop_words
        ]

        relevant_results = []
        for patent in results:
            title = patent.get('title', '').lower()
            abstract = patent.get('abstract', '').lower()
            content = patent.get('content', '').lower()
            full_text = title + " " + abstract + " " + content
            
            # 구문 전체 매칭 확인 (가장 중요)
            phrase_match = phrase_lower in full_text
            
            # 핵심 단어 매칭 확인
            if main_keywords:
                # 핵심 단어가 모두 포함된 경우 (구문 의미 보존)
                keyword_count = sum(
                    1 for keyword in main_keywords
                    if keyword in full_text
                )
                # 구문 전체 매칭이 있거나, 핵심 단어가 모두 포함된 경우만 통과
                if not phrase_match and keyword_count < len(main_keywords):
                    continue  # 관련성 낮음
            
            # 기존 키워드 매칭 로직 (하위 호환성 유지)
            word_match = any(word in full_text for word in phrase_words if len(word) > 1)
            
            if phrase_match or word_match:
                relevant_results.append(patent)
        
        # 조건: relevant_results가 있을 때만 사용 (관련성 없는 결과 제외)
        # relevant_results가 비어있으면 웹 검색 질문으로 이동
        if relevant_results:
            # 관련 결과만 사용
            vector_results = relevant_results[:5]
            state["vector_db_results"] = vector_results
            # search_results는 설정하지 않음 (rag_answer_node에서 처리)
            if "search_results" in state:
                del state["search_results"]
            state["progress_status"] = "✅ 벡터DB에서 관련 특허를 찾았습니다. 답변을 생성 중입니다..."
            LOGGER.info("check_db_node: 서버 검색 결과 있음 - vector_db_results=%d개, next_action=rag_answer", len(vector_results))
            _notify_progress(state["progress_status"])
            state["next_action"] = "rag_answer"
        else:
            # 거리가 너무 멀거나 관련 결과가 없으면 항상 웹 검색 질문으로 이동
            # 사용자가 명시적으로 "web" 모드를 선택한 경우만 바로 검색 (위에서 처리됨)
            LOGGER.info("check_db_node: 서버 검색 결과 없음 - 웹 검색 질문으로 라우팅")
            state["next_action"] = "ask_web_search"
            return state
    else:
        # 서버 기반 검색이 실패했고, 웹 검색 모드가 아니면 항상 웹 검색 질문으로 이동
        # 사용자가 명시적으로 "web" 모드를 선택한 경우만 바로 검색 (위에서 처리됨)
        LOGGER.info("check_db_node: 벡터DB 검색 실패 - 웹 검색 질문으로 라우팅")
        state["next_action"] = "ask_web_search"
        return state
    
    return state


def ask_search_mode_node(state: PatentSearchState) -> PatentSearchState:
    """검색 모드 선택 질문."""
    # 세션 ID 가져오기
    session_id = state.get("session_id")
    # user_query를 우선 사용 (검색어가 더 정확함)
    user_query = state.get("user_query", "") or state.get("keyword", "")
    # 🔧 정규화된 키워드 추출
    from .patent_utils import _extract_keyword
    normalized_keyword = _extract_keyword(user_query) if user_query else ""
    
    # orchestrator_state는 전역 변수에서 가져오기
    orchestrator_state = patent_config_module._current_orchestrator_state_for_node
    if session_id and normalized_keyword:
        # 🔧 정규화된 키워드를 pending_search_keyword에 저장
        set_waiting_for_search_mode(session_id, True, keyword=normalized_keyword, orchestrator_state=orchestrator_state)
        set_original_search_keyword(session_id, user_query, orchestrator_state)
        LOGGER.info("검색어 저장: session_id=%s, normalized_keyword=%s, user_query=%s", session_id, normalized_keyword, user_query)

    state["progress_status"] = "❓ 검색 방식을 선택해주세요:\n1. 서버 기반 검색 (빠르지만 제한적)\n2. 웹 검색 (느리지만 광범위)\n\n'1' 또는 '서버', '2' 또는 '웹'으로 답변해주세요.\n\n서버 기반 검색을 선택하면 벡터DB에서 먼저 검색하고, 결과가 없으면 자동으로 웹 검색으로 전환됩니다."
    state["next_action"] = "end"
    return state


def ask_web_search_node(state: PatentSearchState) -> PatentSearchState:
    """웹 검색 여부 질문 (LLM 기반 자연스러운 질문 생성)."""
    # 원래 검색어 저장 (웹 검색 시 사용)
    session_id = state.get("session_id")
    keyword = state.get("keyword", "") or state.get("user_query", "")
    # orchestrator_state는 전역 변수에서 가져오기
    orchestrator_state = patent_config_module._current_orchestrator_state_for_node
    
    if session_id and keyword:
        # Redis 우선 저장
        set_original_search_keyword(session_id, keyword, orchestrator_state)
        set_waiting_for_web_search(session_id, True, orchestrator_state)
    
    # 키워드를 구문으로 변환 (분리된 키워드 방지: "전기차, 배터리" → "전기차 배터리 기술")
    from .patent_keyword import generate_patent_search_phrase
    search_phrase = generate_patent_search_phrase(keyword)
    
    # LLM으로 자연스러운 질문 생성 (구문 사용)
    try:
        question = generate_web_search_question(search_phrase)
        state["progress_status"] = question + "\n\n'네' 또는 '예'로 답변하시면 웹에서 검색하고, '아니오'로 답변하시면 종료됩니다."
    except Exception as e:
        LOGGER.warning("웹 검색 질문 생성 실패: %s", e)
        # Fallback: 기본 질문 사용 (구문 사용)
        state["progress_status"] = f"서버에 '{search_phrase}' 관련 특허 자료가 저장되어 있지 않습니다. 웹에서 찾아드릴까요?\n\n'네' 또는 '예'로 답변하시면 웹에서 검색하고, '아니오'로 답변하시면 종료됩니다."
    
    state["next_action"] = "end"
    return state


def search_kipris_node(state: PatentSearchState) -> PatentSearchState:
    """KIPRIS API로 특허 검색."""
    # keyword가 있으면 사용, 없으면 user_query에서 추출
    query = state.get("keyword")
    if not query:
        query = _extract_keyword(state["user_query"])
        state["keyword"] = query
        
    state["progress_status"] = f"🌐 KIPRIS 웹 API에서 특허를 검색 중입니다... (검색어: {query})"
    LOGGER.info("KIPRIS 검색 시도: query='%s'", query)
    _notify_progress(state["progress_status"])

    try:
        LOGGER.info("KIPRIS API 호출: query='%s', rows=10", query)
        data = kipris_search(query, rows=10)

        if not data or not isinstance(data, dict):
            LOGGER.warning("KIPRIS API 응답이 비어있거나 dict가 아님: %s", data)
            state["search_results"] = []
            state["next_action"] = "end"
            return state

        response = data.get("response", {})
        header = response.get("header", {})
        result_msg = header.get("resultMsg", "")
        LOGGER.info("KIPRIS API 응답 헤더: %s", header)

        if not response or not isinstance(response, dict):
            LOGGER.warning("response 필드 없음")
            state["search_results"] = []
            state["next_action"] = "end"
            return state

        body = response.get("body", {})
        if not body or not isinstance(body, dict):
            LOGGER.warning("body 필드 없음")
            state["search_results"] = []
            state["next_action"] = "end"
            return state

        items_dict = body.get("items", {})
        if not items_dict or not isinstance(items_dict, dict):
            state["search_results"] = []
            state["next_action"] = "end"
            return state

        items = items_dict.get("item")
        items = ensure_list(items)

        search_results = []
        for it in items:
            if not isinstance(it, dict):
                continue
            search_results.append({
                "application_number": clean_text(it.get("applicationNumber")),
                "title": clean_text(it.get("inventionTitle")),
                "abstract": clean_text(it.get("astrtCont")),
            })

        LOGGER.info("KIPRIS 검색 완료: %d건 발견", len(search_results))
        state["search_results"] = search_results
        state["next_action"] = "end"
    except Exception as e:
        LOGGER.exception("KIPRIS 검색 오류: %s", e)
        state["search_results"] = []
        state["next_action"] = "end"

    return state


def download_pdf_node(state: PatentSearchState) -> PatentSearchState:
    """선택된 특허의 PDF 다운로드 및 임베딩."""
    patent = state.get("selected_patent")
    if not patent or not isinstance(patent, dict):
        state["pdf_content"] = "선택된 특허 정보가 없습니다."
        state["pdf_link"] = ""
        state["next_action"] = "end"
        return state
    
    app_no = patent.get("application_number")
    title = patent.get("title", "제목 없음")
    abstract = patent.get("abstract", "")

    if not app_no:
        state["pdf_content"] = "특허 번호가 없습니다."
        state["pdf_link"] = ""
        state["next_action"] = "end"
        return state

    state["progress_status"] = f"📥 특허 PDF 다운로드 중: {app_no}"
    LOGGER.info(state["progress_status"])
    _notify_progress(state["progress_status"])

    # 임시 디렉토리 사용 (처리 후 삭제)
    import tempfile
    import shutil
    temp_dir = Path(tempfile.mkdtemp(prefix="patent_"))
    pdf_dir = temp_dir / "pdf"
    text_dir = temp_dir / "text"
    meta_dir = temp_dir / "meta"
    
    try:
        pdf_path, download_info = kipris_download_pdf(app_no, pdf_dir)

        if not pdf_path or not pdf_path.exists():
            error_msg = download_info.get("error", "알 수 없는 오류")
            LOGGER.warning("PDF 다운로드 실패: %s", error_msg)
            state["pdf_content"] = f"PDF 다운로드 실패: {error_msg}"
            state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
            state["next_action"] = "end"
            return state

        try:
            state["progress_status"] = "📄 PDF에서 텍스트 추출 중..."
            LOGGER.info(state["progress_status"])
            _notify_progress(state["progress_status"])
            body = extract_pdf_text(pdf_path)
            if not body or len(body.strip()) == 0:
                LOGGER.warning("PDF 텍스트 추출 결과가 비어있음")
                state["pdf_content"] = "PDF 텍스트 추출 결과가 비어있습니다."
                state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
                state["next_action"] = "end"
                return state

            text_path = save_text_file(text_dir, app_no, title, abstract, body)

            try:
                # 특허_키워드 형식으로 저장
                keyword = state.get("keyword", extract_keyword_from_text(f"{title} {abstract}"))
                patent_keyword = f"특허_{keyword}"
                LOGGER.info("download_pdf_node: 사용할 키워드='%s', 컬렉션 예상 이름='%s'", keyword, get_collection_name(keyword))

                state["progress_status"] = "💾 벡터DB에 특허 정보 저장 중..."
                LOGGER.info(state["progress_status"])
                _notify_progress(state["progress_status"])
                chunks_count = embed_and_store_patent(app_no, title, abstract, text_path, patent_keyword=patent_keyword)
                LOGGER.info("임베딩 저장 완료: %d개 청크", chunks_count)
            except Exception as e:
                LOGGER.exception("임베딩 저장 오류 (계속 진행): %s", e)
                chunks_count = 0

            metadata = {
                "application_number": app_no,
                "title": title,
                "abstract": abstract,
                "keyword": state.get("keyword", ""),
                "pdf": {
                    "saved": True,
                    "path": str(pdf_path),
                    "doc_name": download_info.get("doc_name", "") if isinstance(download_info, dict) else "",
                    "download_url": download_info.get("download_url", "") if isinstance(download_info, dict) else "",
                },
                "text": {
                    "extraction": "completed",
                    "path": str(text_path),
                    "char_count": len(body),
                },
            }
            try:
                save_metadata(meta_dir, metadata)
            except Exception as e:
                LOGGER.exception("메타데이터 저장 오류 (계속 진행): %s", e)

            state["pdf_content"] = body
            state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
            state["next_action"] = "rag_answer"
            
            # Milvus 임베딩 저장 확인
            try:
                keyword = state.get("keyword", extract_keyword_from_text(f"{title} {abstract}"))
                collection_name = get_collection_name(keyword)
                from pymilvus import Collection, utility
                
                if utility.has_collection(collection_name):
                    collection = Collection(collection_name)
                    collection.load()
                    num_entities = collection.num_entities
                    LOGGER.info("Milvus 임베딩 저장 확인: 컬렉션 '%s'에 %d개 엔티티 저장됨", collection_name, num_entities)
                    LOGGER.info("특허 '%s' 임베딩 저장 완료: %d개 청크 저장됨", app_no, chunks_count)
            except Exception as e:
                LOGGER.exception("Milvus 임베딩 저장 확인 오류: %s", e)
            
            # 임시 파일 삭제 (PDF, 텍스트, 메타데이터)
            try:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir)
                    LOGGER.info("임시 파일 삭제 완료: %s", temp_dir)
            except Exception as e:
                LOGGER.warning("임시 파일 삭제 오류 (무시): %s", e)

        except Exception as e:
            error_msg = str(e)
            LOGGER.exception("PDF 텍스트 추출 오류: %s", error_msg)
            state["pdf_content"] = f"텍스트 추출 실패: {error_msg}"
            state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
            state["next_action"] = "end"
            
            # 오류 발생 시에도 임시 파일 삭제
            try:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir)
            except Exception:
                pass

    except Exception as e:
        error_msg = str(e)
        LOGGER.exception("PDF 다운로드 오류: %s", error_msg)
        state["pdf_content"] = f"PDF 다운로드 실패: {error_msg}"
        state["pdf_link"] = ""
        state["next_action"] = "end"
        
        # 오류 발생 시에도 임시 파일 삭제
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
        except Exception:
            pass
    
    return state


def rag_answer_node(state: PatentSearchState) -> PatentSearchState:
    """RAG로 사용자 질문에 답변 (골든셋 비교 포함)."""
    if not LANGCHAIN_AVAILABLE or not OPENAI_API_KEY:
        state["answer"] = "OpenAI API 키가 설정되지 않았거나 langchain이 설치되지 않았습니다."
        state["next_action"] = "end"
        return state
    
    state["progress_status"] = "🤖 AI가 답변을 생성 중입니다..."
    LOGGER.info(state["progress_status"])
    _notify_progress(state["progress_status"])

    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0, api_key=OPENAI_API_KEY)
    
    # PDF 링크 수집 (벡터DB 결과에서)
    pdf_links = []

    if state.get("vector_db_results"):
        context_patents = state["vector_db_results"]
        context_text = "\n\n".join([
            f"특허 {idx+1}:\n제목: {p['title']}\n초록: {p['abstract']}\n내용: {p.get('content', '')[:2000]}"
            for idx, p in enumerate(context_patents[:3])
        ])
        # PDF 링크 수집
        for p in context_patents:
            pdf_link = p.get('pdf_link', '')
            if pdf_link:
                pdf_links.append(f"[{p.get('application_number', 'N/A')}] {p.get('title', '제목 없음')}: {pdf_link}")
    elif state.get("pdf_content"):
        context_text = f"제목: {state['selected_patent']['title']}\n초록: {state['selected_patent']['abstract']}\n본문: {state['pdf_content'][:2000]}"
    else:
        context_text = "관련 특허 정보가 없습니다."
    
    # 골든셋에서 유사 질문 찾기
    golden_answer = None
    user_query = state['user_query']

    # 골든셋 로드 (처음 한 번만)
    golden_set = get_golden_set()
    if not golden_set:
        load_golden_set()
        golden_set = get_golden_set()

    # 유사 질문 찾기 (임베딩 기반 유사도 계산으로 개선)
    query_lower = user_query.lower()
    best_similarity = 0.0

    # 임베딩 모델로 쿼리 벡터 생성
    embedding_model = _get_embedding_model()
    try:
        query_embedding = embedding_model.embed(user_query)
    except Exception:
        query_embedding = None

    for golden_query, golden_ans in golden_set.items():
        golden_query_lower = golden_query.lower()

        # 1. 임베딩 기반 유사도 계산 (더 정확)
        similarity = 0.0
        if query_embedding is not None:
            try:
                golden_embedding = embedding_model.embed(golden_query)
                # 코사인 유사도 계산
                if not NUMPY_AVAILABLE:
                    raise ImportError("numpy가 필요합니다")
                dot_product = np.dot(query_embedding, golden_embedding)
                norm_query = np.linalg.norm(query_embedding)
                norm_golden = np.linalg.norm(golden_embedding)
                if norm_query > 0 and norm_golden > 0:
                    cosine_sim = dot_product / (norm_query * norm_golden)
                    similarity = (cosine_sim + 1) / 2  # -1~1 범위를 0~1로 변환
            except Exception:
                pass

        # 2. 키워드 기반 유사도 계산 (fallback)
        if similarity < 0.5:
            query_words = set(re.findall(r'[\w가-힣]+', query_lower))
            golden_words = set(re.findall(r'[\w가-힣]+', golden_query_lower))
            if len(query_words) > 0:
                keyword_similarity = len(query_words & golden_words) / len(query_words | golden_words)
                similarity = max(similarity, keyword_similarity)

        # 90% 이상 유사한 골든셋 찾기
        if similarity >= 0.9 and similarity > best_similarity:
            golden_answer = golden_ans
            best_similarity = similarity
            break

    # 프롬프트 구성
    if golden_answer:
        # 골든셋 답변을 참고하여 더 정확한 답변 생성
        prompt = f"""다음 특허 정보를 참고하여 사용자의 질문에 답변하세요.

특허 정보:
{context_text}

사용자 질문: {user_query}

참고 답변 (골든셋):
{golden_answer}

위 참고 답변의 구조와 스타일을 참고하되, 제공된 특허 정보를 바탕으로 정확하고 상세한 답변을 작성하세요.
답변을 한국어로 작성하고, 특허의 핵심 기술과 특징을 명확히 설명하세요.
답변은 깔끔하고 구조화된 형식으로 작성해주세요."""
    else:
        prompt = f"""다음 특허 정보를 참고하여 사용자의 질문에 답변하세요.

특허 정보:
{context_text}

사용자 질문: {user_query}

답변을 한국어로 작성하고, 특허의 핵심 기술과 특징을 명확히 설명하세요.
답변은 깔끔하고 구조화된 형식으로 작성해주세요."""

    messages = [
        SystemMessage(content="당신은 특허 정보를 분석하는 전문가입니다. 정확하고 상세한 답변을 제공하세요."),
        HumanMessage(content=prompt)
    ]
    
    response = llm.invoke(messages)
    answer = response.content

    # 골든셋이 있고 유사도가 높으면 답변 품질 검증 (90% 이상 유사도 목표)
    if golden_answer:
        # 임베딩 기반 유사도 체크로 개선
        answer_similarity = 0.0
        try:
            answer_embedding = embedding_model.embed(answer)
            golden_embedding = embedding_model.embed(golden_answer)
            if not NUMPY_AVAILABLE:
                raise ImportError("numpy가 필요합니다")
            dot_product = np.dot(answer_embedding, golden_embedding)
            norm_answer = np.linalg.norm(answer_embedding)
            norm_golden = np.linalg.norm(golden_embedding)
            if norm_answer > 0 and norm_golden > 0:
                cosine_sim = dot_product / (norm_answer * norm_golden)
                answer_similarity = (cosine_sim + 1) / 2  # -1~1 범위를 0~1로 변환
        except Exception:
            # Fallback: 키워드 기반 유사도
            answer_words = set(re.findall(r'[\w가-힣]+', answer.lower()))
            golden_words = set(re.findall(r'[\w가-힣]+', golden_answer.lower()))
            if len(answer_words | golden_words) > 0:
                answer_similarity = len(answer_words & golden_words) / len(answer_words | golden_words)

        if answer_similarity < 0.9:  # 90% 미만이면 재생성 시도
                # 더 강한 프롬프트로 재시도
                retry_prompt = f"""다음 특허 정보와 참고 답변을 바탕으로 사용자 질문에 답변하세요.

특허 정보:
{context_text}

사용자 질문: {user_query}

참고 답변 (반드시 이 스타일과 구조를 따르세요):
{golden_answer}

위 참고 답변의 구조, 스타일, 깊이를 최대한 유사하게 따라 답변을 작성하세요.
특허 정보를 바탕으로 하되, 참고 답변의 품질과 구조를 반영하세요."""
                retry_messages = [
                    SystemMessage(content="당신은 특허 정보를 분석하는 전문가입니다. 참고 답변의 품질과 구조를 최대한 유사하게 따라 답변하세요."),
                    HumanMessage(content=retry_prompt)
                ]
                retry_response = llm.invoke(retry_messages)
                answer = retry_response.content

    # PDF 링크가 있으면 답변에 추가 (중복 방지)
    if pdf_links:
        # 이미 answer에 PDF 링크가 포함되어 있는지 확인
        if "🔗 참고 PDF 링크" not in answer and "🔗 PDF 링크" not in answer:
            answer += "\n\n🔗 참고 PDF 링크:\n" + "\n".join(pdf_links)
    elif state.get("pdf_link") and "🔗 PDF 링크" not in answer:
        answer += f"\n\n🔗 PDF 링크: {state['pdf_link']}"

    # 서버 기반 검색 결과가 있고, 아직 웹 검색을 하지 않은 경우 웹 검색 여부 질문을 답변에 직접 추가
    search_mode = state.get("search_mode")
    vector_db_results = state.get("vector_db_results")
    LOGGER.info("rag_answer_node: search_mode=%s, vector_db_results=%s (개수: %d)", 
                search_mode, bool(vector_db_results), len(vector_db_results) if vector_db_results else 0)
    
    if search_mode == "server" and vector_db_results:
        session_id = state.get("session_id")
        # orchestrator_state는 전역 변수에서 가져오기
        orchestrator_state = patent_config_module._current_orchestrator_state_for_node
        if session_id:
            # 원래 검색어 저장 (웹 검색 시 사용, Redis 우선)
            original_query = state.get("user_query", "") or state.get("keyword", "")
            if original_query:
                set_original_search_keyword(session_id, original_query, orchestrator_state)
            set_waiting_for_web_search(session_id, True, orchestrator_state)
        # 답변에 웹 검색 질문 직접 추가
        answer += "\n\n❓ 웹에서 추가로 검색해드릴까요?\n\n'네' 또는 '예'로 답변하시면 웹에서 추가 정보를 검색하고, '아니오'로 답변하시면 종료됩니다."
        state["next_action"] = "ask_web_search"
        LOGGER.info("rag_answer_node: 웹 검색 질문 추가됨 (search_mode=server, vector_db_results 있음)")
    else:
        state["next_action"] = "end"
        LOGGER.info("rag_answer_node: 웹 검색 질문 추가 안 함 (search_mode=%s, vector_db_results=%s)", 
                    search_mode, bool(vector_db_results))
    
    state["answer"] = answer
    state["progress_status"] = "✅ 답변 생성 완료"
    LOGGER.info("rag_answer_node: 답변 생성 완료 - answer 길이=%d, next_action=%s", len(answer), state.get("next_action"))
    LOGGER.info("rag_answer_node: answer 내용 (처음 200자): %s", answer[:200] if answer else "(비어있음)")
    _notify_progress(state["progress_status"])

    return state


def _should_continue(state: PatentSearchState) -> str:
    """다음 노드 결정."""
    return state["next_action"]


_workflow = None


def get_workflow():
    """LangGraph 워크플로우 가져오기 (지연 초기화)."""
    global _workflow
    
    if not LANGCHAIN_AVAILABLE:
        return None
    
    if _workflow is None:
        workflow = StateGraph(PatentSearchState)
        
        workflow.add_node("check_db", check_db_node)
        workflow.add_node("search_kipris", search_kipris_node)
        workflow.add_node("download_pdf", download_pdf_node)
        workflow.add_node("rag_answer", rag_answer_node)
        workflow.add_node("ask_search_mode", ask_search_mode_node)
        workflow.add_node("ask_web_search", ask_web_search_node)

        workflow.set_entry_point("check_db")
        
        workflow.add_conditional_edges(
            "check_db",
            _should_continue,
            {
                "rag_answer": "rag_answer",
                "search_kipris": "search_kipris",
                "ask_search_mode": "ask_search_mode",
                "ask_web_search": "ask_web_search",  # 서버 검색 결과 없을 때 웹 검색 질문
            }
        )
        
        workflow.add_edge("search_kipris", END)
        workflow.add_edge("download_pdf", "rag_answer")
        # rag_answer는 조건부로 ask_web_search 또는 end로 이동
        workflow.add_conditional_edges(
            "rag_answer",
            _should_continue,
            {
                "ask_web_search": "ask_web_search",
                "end": END,
            }
        )
        workflow.add_edge("ask_web_search", END)
        workflow.add_edge("ask_search_mode", END)

        # checkpointer=None으로 컴파일 (직렬화 불가능한 객체 방지)
        # 실제 상태는 OrchestratorState.tech_search_context를 통해 Redis에 저장됨
        _workflow = workflow.compile(checkpointer=None)
    
    return _workflow

