"""LangGraph 기반 ScienceON 서브 에이전트.

이 모듈은 통합 마스터 에이전트의 서브 에이전트로 사용될 수 있도록 설계되었습니다.
독립적으로 실행하거나, 상위 마스터 에이전트의 노드/서브 그래프로 통합 가능합니다.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict, Union

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

import urllib.parse
import requests
from bs4 import BeautifulSoup

from .ai_keyword import generate_search_keyword, generate_summary
from .crawler import download_pdfs_from_results, search_scienceon_papers
from .vector_db_utils import (
    initialize_models,
    initialize_milvus,
    extract_keyword_from_text,
    get_collection_name,
    encode_texts,
    get_text_splitter,
)

load_dotenv()

# 벡터DB 관련 임포트 (선택적)
try:
    from pymilvus import Collection, utility, FieldSchema, CollectionSchema, DataType
    VECTOR_DB_AVAILABLE = True
except ImportError:
    VECTOR_DB_AVAILABLE = False
    Collection = None

LOGGER = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL")

LLM = ChatOpenAI(
    model=OPENAI_MODEL,
    temperature=0.0,
    max_tokens=512,
)

INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "당신은 ScienceON 논문 검색 챗봇의 의도 분류 전문가입니다.\n"
                "사용자의 입력을 분석하여 다음 중 하나로 분류하세요:\n\n"
                "1. search: 새로운 논문 검색 요청\n"
                "   - 예시: '인공지능 논문 찾아줘', '디스플레이 관련 논문 검색', '머신러닝 논문'\n"
                "   - 특징: 검색어나 주제가 명시되어 있고, 다운로드/상세 정보 관련 키워드가 없음\n\n"
                "2. detail: 이전 검색 결과 중 특정 논문의 상세 정보 요청\n"
                "   - 예시: '1번 논문에 대해 알려줘', '2번째 논문 상세 정보', '첫 번째 논문 설명해줘', '1번', '2번', '1번 pdf 다운받아줘'\n"
                "   - 특징: '알려', '상세', '정보', '설명', '에 대해', '다운', 'pdf', '받아', '저장', '다운로드' 같은 키워드 + 순번(1번째, 2번째 등)\n"
                "   - 중요: 이전에 검색 결과가 있어야 함 (results_count > 0)\n"
                "   - 매우 중요: results_count > 0이고 입력이 '1번', '2번', '첫 번째', '두 번째' 같은 순번만 포함하면 detail로 분류\n\n"
                "반드시 아래 형식의 JSON만 반환하세요 (설명 없이 JSON만):\n"
                '{{"intent": "search" 또는 "detail", "query": "검색어 또는 빈 문자열", "download_index": 숫자 또는 null}}\n\n'
                "규칙:\n"
                "- intent가 detail이면 query는 빈 문자열 \"\"\n"
                "- intent가 search이면 query에 검색어를 넣고 download_index는 null\n"
                "- download_index는 1부터 시작하는 정수 (1번째=1, 2번째=2, 첫 번째=1, 두 번째=2)\n"
                "- 순번이 없으면 download_index는 null\n"
                "- results_count가 0이고 intent가 detail이면, intent를 search로 변경하고 query에 사용자 입력을 넣으세요\n"
                "- results_count > 0이고 사용자 입력이 순번만 포함 ('1번', '2번', '첫 번째' 등)하면 detail로 분류"
            ),
        ),
        (
            "human",
            (
                "상황 정보:\n"
                "- 이전 검색 결과 개수: {results_count}개\n"
                "- 사용자 입력: {user_input}\n\n"
                "이전 대화:\n{history}\n\n"
                "위 정보를 바탕으로 의도를 분류하고 JSON을 반환하세요:"
            ),
        ),
    ]
)

class ScienceONAgentState(TypedDict):
    """ScienceON 서브 에이전트의 상태.

    통합 마스터 에이전트의 상태에 포함되거나 독립적으로 사용 가능합니다.
    """

    messages: Annotated[List[BaseMessage], "대화 이력"]
    user_input: str
    intent: Literal["search", "detail", "ask_source", "continue_search", "unknown"]
    query: str
    download_index: int
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


# 하위 호환성을 위한 별칭
AgentState = ScienceONAgentState


# ============================================================================
# 공통 유틸리티 함수들
# ============================================================================

def _validate_text_quality(text: str, min_length: int = 100) -> tuple[bool, str]:
    """추출된 텍스트의 품질을 검증한다.
    
    이미지 기반 PDF의 OCR 결과가 이상한 문자로 저장되는 것을 방지합니다.
    
    Args:
        text: 검증할 텍스트
        min_length: 최소 텍스트 길이 (기본값: 100자)
        
    Returns:
        (is_valid, reason) 튜플
        - is_valid: 품질이 좋으면 True, 나쁘면 False
        - reason: 품질이 나쁜 경우 이유 설명
    """
    if not text or not text.strip():
        return False, "텍스트가 비어있음"
    
    text_len = len(text)
    
    # 1. 최소 길이 체크
    if text_len < min_length:
        return False, f"텍스트가 너무 짧음 ({text_len}자 < {min_length}자)"
    
    # 2. 정상 문자 비율 계산 (한글, 영문, 숫자, 일반적인 특수문자)
    # 한글: \uAC00-\uD7A3
    # 영문/숫자: a-zA-Z0-9
    # 일반적인 특수문자: 공백, 줄바꿈, 기본 문장부호 (. , ; : ! ? - ( ) [ ] { } " ' / \)
    normal_chars = re.findall(r'[가-힣a-zA-Z0-9\s\.\,\;\:\!\?\-\(\)\[\]\{\}"\'/\\]', text)
    normal_ratio = len(normal_chars) / text_len if text_len > 0 else 0
    
    # 3. 이상한 문자 비율 계산 (중국어, 일본어 등 OCR 오인식 가능한 문자)
    # 중국어 한자 범위, 일본어 등
    weird_chars = re.findall(r'[^\x00-\x7F가-힣a-zA-Z0-9\s\.\,\;\:\!\?\-\(\)\[\]\{\}"\'/\\]', text)
    weird_ratio = len(weird_chars) / text_len if text_len > 0 else 0
    
    # 4. 의미있는 단어 비율 계산 (한글 단어 또는 영문 단어가 2글자 이상)
    meaningful_words = re.findall(r'[가-힣]{2,}|[a-zA-Z]{2,}', text)
    meaningful_chars = sum(len(word) for word in meaningful_words)
    meaningful_ratio = meaningful_chars / text_len if text_len > 0 else 0
    
    # 5. 연속된 특수문자나 이상한 패턴 체크
    weird_patterns = [
        r'[^\w\s가-힣]{3,}',  # 3개 이상 연속된 특수문자
        r'[？]{2,}',  # 연속된 물음표
        r'[\[\]]{2,}',  # 연속된 대괄호
    ]
    has_weird_pattern = any(re.search(pattern, text) for pattern in weird_patterns)
    
    # 검증 기준
    if normal_ratio < 0.5:
        return False, f"정상 문자 비율이 너무 낮음 ({normal_ratio:.2%} < 50%)"
    
    if weird_ratio > 0.3:
        return False, f"이상한 문자 비율이 너무 높음 ({weird_ratio:.2%} > 30%)"
    
    if meaningful_ratio < 0.3:
        return False, f"의미있는 단어 비율이 너무 낮음 ({meaningful_ratio:.2%} < 30%)"
    
    if has_weird_pattern:
        return False, "이상한 문자 패턴이 포함됨 (OCR 오인식 가능성)"
    
    return True, "품질 검증 통과"


def extract_text_from_pdf_with_vision(
    pdf_path: Union[Path, str],
    model: Optional[str] = None
) -> Optional[str]:
    """Vision API를 사용하여 이미지 기반 PDF에서 텍스트 추출.
    
    Args:
        pdf_path: PDF 파일 경로
        model: 사용할 모델 (기본값: OPENAI_MODEL 또는 "gpt-4o-mini")
    
    Returns:
        추출된 텍스트 문자열. 실패 시 None.
    """
    try:
        from pdf2image import convert_from_path
        from openai import OpenAI
        import base64
        from io import BytesIO
        
        if isinstance(pdf_path, str):
            pdf_path = Path(pdf_path)
        
        if not pdf_path.exists():
            LOGGER.warning(f"PDF 파일이 존재하지 않습니다: {pdf_path}")
            return None
        
        # 모델 설정 (환경 변수 또는 기본값 사용)
        vision_model = model or OPENAI_MODEL or "gpt-4o-mini"
        openai_api_key = os.getenv("OPENAI_API_KEY")
        
        if not openai_api_key:
            LOGGER.warning("OPENAI_API_KEY가 설정되지 않았습니다.")
            return None
        
        client = OpenAI(api_key=openai_api_key)
        text_parts = []
        
        LOGGER.info(f"PDF를 이미지로 변환 중: {pdf_path.name} (Vision 모델: {vision_model})")
        
        # PDF를 이미지로 변환 (각 페이지)
        try:
            images = convert_from_path(str(pdf_path), dpi=200)  # 200 DPI로 충분
        except Exception as e:
            LOGGER.warning(f"PDF를 이미지로 변환 실패: {e}")
            return None
        
        total_pages = len(images)
        LOGGER.info(f"총 {total_pages}페이지를 Vision API로 처리합니다...")
        
        for i, image in enumerate(images):
            try:
                # 이미지를 base64로 인코딩
                buffered = BytesIO()
                image.save(buffered, format="PNG", optimize=True)
                img_base64 = base64.b64encode(buffered.getvalue()).decode()
                
                # Vision API 호출
                response = client.chat.completions.create(
                    model=vision_model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "이 이미지는 학술 논문의 한 페이지입니다. "
                                        "모든 텍스트를 정확하게 추출해주세요. "
                                        "한국어와 영어가 혼재되어 있을 수 있습니다. "
                                        "수식, 표, 그림 설명, 참고문헌도 포함해서 추출해주세요. "
                                        "마크다운 형식으로 구조를 유지하면서 추출해주세요."
                                    )
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=4096,
                    temperature=0.0
                )
                
                page_text = response.choices[0].message.content
                if page_text and page_text.strip():
                    text_parts.append(page_text.strip())
                    LOGGER.debug(f"페이지 {i+1}/{total_pages} 처리 완료")
                
            except Exception as page_error:
                LOGGER.warning(f"페이지 {i+1} Vision API 처리 실패: {page_error}")
                continue
        
        if not text_parts:
            LOGGER.warning(f"Vision API로 텍스트를 추출할 수 없었습니다: {pdf_path.name}")
            return None
        
        extracted_text = "\n\n".join(text_parts)
        LOGGER.info(
            f"Vision API 텍스트 추출 성공: {pdf_path.name}, "
            f"{len(text_parts)}페이지, {len(extracted_text)}자"
        )
        
        return extracted_text
        
    except ImportError as import_error:
        LOGGER.warning(
            f"Vision API 필수 패키지가 설치되지 않았습니다: {import_error}. "
            f"pip install pdf2image openai pillow 설치 필요"
        )
        return None
    except Exception as e:
        LOGGER.warning(f"Vision API 텍스트 추출 실패: {e}", exc_info=True)
        return None


def extract_text_from_pdf(pdf_path: Union[Path, str]) -> Optional[str]:
    """PDF에서 텍스트를 추출한다 (docling 사용).
    
    Args:
        pdf_path: PDF 파일 경로 (Path 객체 또는 문자열)
        
    Returns:
        추출된 텍스트 문자열. 실패 시 None.
    """
    try:
        # Path 객체로 변환
        if isinstance(pdf_path, str):
            pdf_path = Path(pdf_path)
        
        if not pdf_path.exists():
            LOGGER.warning(f"PDF 파일이 존재하지 않습니다: {pdf_path}")
            return None
        
        # docling을 사용한 PDF 변환
        try:
            from docling.document_converter import DocumentConverter
            from docling_core.transforms.chunker import HierarchicalChunker
        except ImportError as import_error:
            LOGGER.warning(f"docling import 실패: {import_error}")
            raise ImportError("docling이 설치되지 않았습니다. pip install docling을 실행하세요.")
        
        LOGGER.info(f"PDF 변환 시작: {pdf_path.name}")
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        doc = result.document
        
        if not doc:
            LOGGER.warning(f"PDF 변환 결과가 비어있습니다: {pdf_path.name}")
            return None
        
        # HierarchicalChunker를 사용하여 문서를 청크로 분할
        chunker = HierarchicalChunker()
        chunks = list(chunker.chunk(doc))
        
        if not chunks:
            LOGGER.warning(f"PDF에서 청크를 생성할 수 없습니다: {pdf_path.name}")
            return None
        
        # 청크들을 텍스트로 결합
        text_parts = []
        for chunk in chunks:
            if hasattr(chunk, 'text') and chunk.text:
                text_parts.append(chunk.text.strip())
            elif isinstance(chunk, dict) and 'text' in chunk:
                text_parts.append(chunk['text'].strip())
            elif isinstance(chunk, str):
                text_parts.append(chunk.strip())
        
        if not text_parts:
            LOGGER.warning(f"PDF에서 텍스트를 추출할 수 없습니다: {pdf_path.name}, 청크 수: {len(chunks)}")
            return None
        
        extracted_text = "\n\n".join(text_parts)
        
    # 텍스트 품질 검증
        is_valid, reason = _validate_text_quality(extracted_text)
        if not is_valid:
            LOGGER.warning(
                f"PDF 텍스트 품질 검증 실패: {pdf_path.name} - {reason}. "
                f"이미지 기반 PDF일 가능성이 높아 Vision API로 폴백 시도..."
            )
            # 품질이 낮으면 Vision API로 폴백
            vision_text = extract_text_from_pdf_with_vision(pdf_path)
            if vision_text:
                # Vision API 결과도 품질 검증
                is_valid, reason = _validate_text_quality(vision_text)
                if is_valid:
                    LOGGER.info(
                        f"Vision API로 텍스트 추출 성공 (품질 검증 통과): {pdf_path.name}, "
                        f"{len(vision_text)}자"
                    )
                    return vision_text
                else:
                    LOGGER.warning(f"Vision API 결과도 품질 낮음: {reason}. 벡터DB 저장을 건너뜁니다.")
            return None
        
        LOGGER.info(f"PDF 텍스트 추출 성공: {pdf_path.name}, 청크 {len(chunks)}개, {len(extracted_text)}자")
        return extracted_text
        
    except ImportError as e:
        LOGGER.warning(f"docling이 설치되지 않았습니다. pypdf로 폴백: {e}")
        # docling 실패 시 pypdf로 폴백
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            total_pages = len(reader.pages)
            LOGGER.info(f"PDF 읽기 성공 (pypdf): {pdf_path.name}, 총 {total_pages}페이지")
            
            text_chunks = []
            for i, page in enumerate(reader.pages):
                try:
                    content = page.extract_text() or ""
                    if content.strip():
                        text_chunks.append(content.strip())
                except Exception as page_error:
                    LOGGER.warning(f"페이지 {i+1} 텍스트 추출 실패: {page_error}")
            
            if not text_chunks:
                LOGGER.warning(f"PDF에서 텍스트를 추출할 수 없습니다 (이미지 기반일 가능성): {pdf_path.name}")
                return None
            
            extracted_text = "\n\n".join(text_chunks)
            
        # pypdf 폴백 결과도 품질 검증
            is_valid, reason = _validate_text_quality(extracted_text)
            if not is_valid:
                LOGGER.warning(
                    f"PDF 텍스트 품질 검증 실패 (pypdf): {pdf_path.name} - {reason}. "
                    f"Vision API로 폴백 시도..."
                )
                # 품질이 낮으면 Vision API로 폴백
                vision_text = extract_text_from_pdf_with_vision(pdf_path)
                if vision_text:
                    # Vision API 결과도 품질 검증
                    is_valid, reason = _validate_text_quality(vision_text)
                    if is_valid:
                        LOGGER.info(
                            f"Vision API로 텍스트 추출 성공 (pypdf 폴백 후): {pdf_path.name}, "
                            f"{len(vision_text)}자"
                        )
                        return vision_text
                    else:
                        LOGGER.warning(f"Vision API 결과도 품질 낮음: {reason}. 벡터DB 저장을 건너뜁니다.")
                return None
            
            LOGGER.info(f"PDF 텍스트 추출 성공 (pypdf): {pdf_path.name}, {len(text_chunks)}페이지, {len(extracted_text)}자")
            return extracted_text
        except Exception as fallback_error:
            LOGGER.warning(f"pypdf 폴백도 실패: {fallback_error}")
            return None
    except Exception as e:
        LOGGER.warning(f"PDF 텍스트 추출 실패: {e}", exc_info=True)
        return None


def _render_history(messages: List[BaseMessage]) -> str:
    """LangChain 메시지 목록을 문자열로 변환한다."""
    if not messages:
        return "(이전 대화 없음)"
    lines: List[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            speaker = "사용자"
        elif isinstance(message, AIMessage):
            speaker = "AI"
        else:
            speaker = message.type
        lines.append(f"{speaker}: {message.content}")
    return "\n".join(lines)


def _extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """텍스트에서 JSON 객체를 추출한다 (중첩된 중괄호 처리)."""
    # 첫 번째 { 찾기
    start_idx = text.find('{')
    if start_idx == -1:
        return None
    
    # 중괄호 매칭
    brace_count = 0
    for i in range(start_idx, len(text)):
        if text[i] == '{':
            brace_count += 1
        elif text[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                json_str = text[start_idx:i+1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    return None
    return None


def parse_search_source_keywords(user_input: str) -> tuple[bool, bool]:
    """사용자 입력에서 웹/사내 서버 키워드를 파싱한다.
    
    Args:
        user_input: 사용자 입력 문자열
        
    Returns:
        (has_web_keyword, has_internal_keyword) 튜플
    """
    web_keywords = ["웹", "인터넷", "온라인", "web", "internet"]
    internal_keywords = ["사내", "내부", "서버", "internal", "server"]
    
    user_input_lower = user_input.lower()
    has_web = any(kw in user_input_lower for kw in web_keywords)
    has_internal = any(kw in user_input_lower for kw in internal_keywords)
    
    return has_web, has_internal


# ============================================================================
# 벡터DB 관련 함수들
# ============================================================================

# 벡터DB 관련 함수들은 vector_db_utils 모듈에서 가져옴
def get_or_create_paper_collection(collection_name: str) -> Optional[Any]:
    """논문 컬렉션 가져오기 또는 생성."""
    if not VECTOR_DB_AVAILABLE:
        return None
    
    try:
        initialize_milvus()
        
        if collection_name in utility.list_collections():
            collection = Collection(collection_name)
            collection.load()
            return collection
        
        # 새 컬렉션 생성
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="identifier", dtype=DataType.VARCHAR, max_length=128),  # DOI 또는 내부 ID
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
            FieldSchema(name="paper_keyword", dtype=DataType.VARCHAR, max_length=128),  # 논문_키워드 형식
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1536),  # OpenAI text-embedding-3-small 차원
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
        return collection
    except Exception as e:
        LOGGER.warning(f"컬렉션 생성/로드 실패: {e}")
        return None


def check_vector_db(query: str, limit: int = 5, max_distance: float = 1.5, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """벡터DB에서 유사 논문 검색.
    
    Args:
        query: 검색 쿼리
        limit: 반환할 최대 결과 수
        max_distance: 최대 거리 임계값
        keyword: 이미 생성된 키워드 (있으면 재사용, 없으면 extract_keyword_from_text 사용)
    """
    if not VECTOR_DB_AVAILABLE:
        return []
    
    try:
        initialize_models()
        initialize_milvus()
        
        # 키워드가 제공되지 않으면 extract_keyword_from_text 사용
        if not keyword:
            keyword = extract_keyword_from_text(query)
        
        collection_name = get_collection_name(keyword, prefix="papers_embeddings")
        
        try:
            collection = Collection(collection_name)
            collection.load()
            
            if not utility.has_collection(collection_name):
                return []
            
            num_entities = collection.num_entities
            if num_entities == 0:
                return []
        except Exception:
            return []
        
        embeddings_result = encode_texts([query])
        if embeddings_result is None or len(embeddings_result) == 0:
            return []
        query_vec = embeddings_result[0].tolist()
        
        results = collection.search(
            data=[query_vec],
            anns_field="embedding",
            param={"metric_type": "L2", "params": {"nprobe": 10}},
            limit=limit * 5,
            expr=None,  # 필터링 없음
            output_fields=["identifier", "title", "content"]
        )
        
        if not results or len(results) == 0 or len(results[0]) == 0:
            return []
        
        found_papers = []
        seen_identifiers = set()
        query_lower = query.lower()
        query_words = re.findall(r'[\w가-힣]+', query_lower)
        
        for hit in results[0]:
            identifier = hit.entity.get('identifier')
            if not identifier or identifier in seen_identifiers:
                continue
            
            title = hit.entity.get('title', '').lower()
            content = hit.entity.get('content', '').lower()
            
            full_match = (query_lower in title or query_lower in content)
            word_match = any(word in title or word in content for word in query_words if len(word) > 1)
            keyword_match = full_match or word_match
            
            effective_max_distance = 2.0 if keyword_match else max_distance
            
            if hit.distance > effective_max_distance:
                continue
            
            seen_identifiers.add(identifier)
            similarity = 1.0 / (1.0 + hit.distance)
            
            found_papers.append({
                "identifier": identifier,
                "title": hit.entity.get('title'),
                "content": hit.entity.get('content', ''),
                "score": hit.distance,
                "similarity": similarity,
            })
        
        found_papers.sort(key=lambda x: x["score"])
        return found_papers[:limit]
    except Exception as e:
        LOGGER.warning(f"벡터DB 검색 오류: {e}")
        return []


def search_paper_chunks_by_identifier(
    identifier: str, 
    query: str, 
    limit: int = 10,
    max_distance: float = 1.5,
    keyword: Optional[str] = None
) -> List[Dict[str, Any]]:
    """특정 논문의 identifier로 필터링하여 관련 청크들을 검색한다.
    
    Args:
        identifier: 논문 식별자
        query: 검색 쿼리
        limit: 반환할 최대 청크 수
        max_distance: 최대 거리 임계값
        keyword: 컬렉션 이름 결정용 키워드
        
    Returns:
        검색된 청크 리스트
    """
    if not VECTOR_DB_AVAILABLE:
        return []
    
    try:
        initialize_models()
        initialize_milvus()
        
        # 컬렉션 이름 결정
        if keyword:
            collection_keyword = keyword.split(',')[0].strip().replace(' ', '').replace('_', '')
        else:
            collection_keyword = extract_keyword_from_text(query)
        
        collection_name = get_collection_name(collection_keyword, prefix="papers_embeddings")
        
        try:
            collection = Collection(collection_name)
            collection.load()
            
            if not utility.has_collection(collection_name):
                return []
            
            num_entities = collection.num_entities
            if num_entities == 0:
                return []
        except Exception:
            return []
        
        # 쿼리 임베딩 생성
        embeddings_result = encode_texts([query])
        if embeddings_result is None or len(embeddings_result) == 0:
            return []
        query_vec = embeddings_result[0].tolist()
        
        # identifier로 필터링하여 검색
        expr = f'identifier == "{identifier}"'
        results = collection.search(
            data=[query_vec],
            anns_field="embedding",
            param={"metric_type": "L2", "params": {"nprobe": 10}},
            limit=limit * 3,  # 더 많이 가져와서 필터링
            expr=expr,  # identifier 필터링
            output_fields=["identifier", "title", "content"]
        )
        
        if not results or len(results) == 0 or len(results[0]) == 0:
            return []
        
        found_chunks = []
        for hit in results[0]:
            if hit.distance > max_distance:
                continue
            
            found_chunks.append({
                "identifier": hit.entity.get('identifier'),
                "title": hit.entity.get('title'),
                "content": hit.entity.get('content', ''),
                "score": hit.distance,
                "similarity": 1.0 / (1.0 + hit.distance),
            })
        
        found_chunks.sort(key=lambda x: x["score"])
        return found_chunks[:limit]
    except Exception as e:
        LOGGER.warning(f"논문 청크 검색 오류: {e}")
        return []


def _load_paper_collections_for_query(keyword: Optional[str]) -> List[str]:
    """질의 키워드 기준으로 탐색할 논문 컬렉션 목록을 반환."""
    initialize_milvus()
    if keyword:
        normalized = keyword.split(",")[0].strip().replace(" ", "").replace("_", "")
        return [get_collection_name(normalized, prefix="papers_embeddings")]
    try:
        return [
            name
            for name in utility.list_collections()
            if name.startswith("papers_embeddings_")
        ]
    except Exception:
        return []


def retrieve_paper_chunks_from_milvus(
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

    embeddings = encode_texts([query])
    if embeddings is None or len(embeddings) == 0:
        return []

    query_vec = embeddings[0].tolist()
    collections = _load_paper_collections_for_query(keyword)
    if not collections:
        return []

    aggregated: List[Dict[str, Any]] = []
    for collection_name in collections:
        try:
            collection = Collection(collection_name)
            collection.load()
        except Exception:
            continue

        expr = f'identifier == "{identifier}"' if identifier else None
        try:
            hits = collection.search(
                data=[query_vec],
                anns_field="embedding",
                param={"metric_type": "L2", "params": {"nprobe": 10}},
                limit=top_k * 3,
                expr=expr,
                output_fields=["identifier", "title", "content"],
            )
        except Exception:
            continue

        if not hits or not hits[0]:
            continue

        for hit in hits[0]:
            score = 1 / (1 + hit.distance)
            if score < score_floor:
                continue

            aggregated.append(
                {
                    "chunk_id": f"{collection_name}:{hit.id}",
                    "identifier": hit.entity.get("identifier"),
                    "title": hit.entity.get("title", ""),
                    "content": hit.entity.get("content", ""),
                    "score": score,
                }
            )

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


def _convert_vector_db_results_to_paper_results(vector_db_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """벡터DB 검색 결과를 논문 목록 형식으로 변환."""
    paper_results = []
    for vdb_result in vector_db_results:
        paper_results.append({
            "title": vdb_result.get("title", "제목 없음"),
            "identifier": vdb_result.get("identifier", ""),
            "pdf_link": "",  # 벡터DB에는 PDF 링크가 없을 수 있음
            "detail_link": "",
            "pdf_path": None,  # 나중에 찾을 수 있음
            "content": vdb_result.get("content", ""),  # 상세 답변에 사용
        })
    return paper_results


def check_paper_exists(identifier: str) -> bool:
    """벡터DB에 해당 identifier를 가진 논문이 이미 존재하는지 확인한다.
    
    Args:
        identifier: 논문 식별자 (DOI 또는 내부 ID)
        
    Returns:
        존재하면 True, 없으면 False
    """
    if not VECTOR_DB_AVAILABLE:
        LOGGER.debug(f"[중복 체크] 벡터DB가 사용 불가능하여 체크 건너뜀: {identifier}")
        return False
    
    try:
        initialize_models()
        initialize_milvus()
        
        # 모든 논문 컬렉션 이름 패턴: papers_embeddings_*
        all_collections = utility.list_collections()
        paper_collections = [col for col in all_collections if col.startswith("papers_embeddings_")]
        
        LOGGER.debug(f"[중복 체크] identifier '{identifier}' 확인 시작 - 검색할 컬렉션 수: {len(paper_collections)}")
        
        if not paper_collections:
            LOGGER.debug(f"[중복 체크] 논문 컬렉션이 없어 중복 없음으로 판단: {identifier}")
            return False
        
        for collection_name in paper_collections:
            try:
                collection = Collection(collection_name)
                collection.load()
                
                # identifier 필드로 조회 (identifier가 이미 있는지 확인)
                expr = f'identifier == "{identifier}"'
                results = collection.query(
                    expr=expr,
                    output_fields=["identifier"],
                    limit=1
                )
                
                if results and len(results) > 0:
                    LOGGER.info(f"[중복 체크] identifier '{identifier}'가 이미 컬렉션 '{collection_name}'에 존재함")
                    return True
            except Exception as e:
                # 특정 컬렉션 확인 실패는 무시하고 다음 컬렉션 계속 확인
                LOGGER.debug(f"[중복 체크] 컬렉션 '{collection_name}' 확인 중 오류: {e}")
                continue
        
        LOGGER.debug(f"[중복 체크] identifier '{identifier}'가 모든 컬렉션에서 발견되지 않음 (중복 없음)")
        return False
    except Exception as e:
        LOGGER.warning(f"[중복 체크] identifier '{identifier}' 확인 중 오류: {e}")
        return False


def embed_and_store_paper(
    identifier: str,
    title: str,
    text_path: Path,
    paper_keyword: Optional[str] = None,
    collection_keyword: Optional[str] = None
) -> int:
    """논문을 벡터DB에 임베딩하여 저장한다.
    
    Args:
        identifier: 논문 식별자
        title: 논문 제목
        text_path: 텍스트 파일 경로
        paper_keyword: 논문 키워드 (메타데이터용)
        collection_keyword: 컬렉션 이름 결정용 키워드 (generate_search_keyword로 생성된 키워드)
    """
    if not VECTOR_DB_AVAILABLE:
        return 0
    
    try:
        initialize_models()
        initialize_milvus()
        
        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        
        # 컬렉션 이름 결정: collection_keyword가 있으면 사용, 없으면 extract_keyword_from_text 사용
        if collection_keyword:
            # 쉼표로 구분된 키워드에서 첫 번째만 추출
            keyword = collection_keyword.split(',')[0].strip()
            # 공백 제거 (keyword_map 매칭을 위해)
            keyword = keyword.replace(' ', '').replace('_', '')
        else:
            keyword = extract_keyword_from_text(title)
        
        if paper_keyword is None:
            paper_keyword = f"논문_{keyword}"
        
        collection_name = get_collection_name(keyword, prefix="papers_embeddings")
        collection = get_or_create_paper_collection(collection_name)
        
        if not collection:
            return 0
        
        # 스키마 확인
        schema = collection.schema
        field_names = [field.name for field in schema.fields]
        has_paper_keyword = "paper_keyword" in field_names
        
        splitter = get_text_splitter()
        if splitter is None:
            return 0
        chunks = splitter.split_text(text)
        
        embeddings_result = encode_texts(chunks)
        if embeddings_result is None:
            return 0
        
        # 길이 검증: 임베딩과 청크의 길이가 일치해야 함
        if len(embeddings_result) != len(chunks):
            LOGGER.warning(
                f"임베딩 길이({len(embeddings_result)})와 청크 길이({len(chunks)})가 불일치합니다. "
                f"논문 ID: {identifier}, 제목: {title[:50]}..."
            )
            return 0
        
        embeddings = embeddings_result
        
        identifiers = [identifier] * len(chunks)
        titles = [title] * len(chunks)
        
        if has_paper_keyword:
            paper_keywords = [paper_keyword] * len(chunks)
            data = [identifiers, titles, chunks, paper_keywords, embeddings.tolist()]
        else:
            data = [identifiers, titles, chunks, embeddings.tolist()]
        
        collection.insert(data)
        collection.flush()
        
        return len(chunks)
    except Exception as e:
        LOGGER.warning(f"논문 벡터DB 저장 오류: {e}")
        return 0


def analyze_input_node(state: ScienceONAgentState) -> ScienceONAgentState:
    """사용자 입력을 분석하여 검색 소스를 결정한다."""
    user_input = state.get("user_input", "")
    messages = state.get("messages", [])
    results = state.get("results", [])
    results_count = len(results) if results else 0
    next_action = state.get("next_action", "")
    intent = state.get("intent", "")
    
    # 중요: 이전 intent가 ask_source인 경우 (선택 질문에 대한 답변)
    # 하지만 새로운 검색 요청이면 리셋해야 함
    if intent == "ask_source":
        # 새로운 검색 요청인지 확인 (검색 키워드가 포함되어 있으면 새로운 검색)
        search_keywords = ["논문", "찾아", "검색", "paper", "article", "찾아줘", "찾아주세요"]
        is_new_search = any(kw in user_input for kw in search_keywords)
        
        if is_new_search:
            # 새로운 검색 요청이므로 리셋하고 일반 검색으로 처리
            LOGGER.debug(
                f"[라우팅 디버그] analyze_input_node - intent=ask_source에서 새로운 검색 요청 감지: '{user_input}' - 리셋"
            )
            state["results"] = []
            state["displayed_count"] = 0
            state["keyword"] = ""
            state["vector_db_results"] = []
            state["response"] = ""
            state["intent"] = "search"  # 리셋
            state["next_action"] = ""  # 리셋하여 아래 로직으로 진행
        else:
            # 선택 질문에 대한 답변으로 처리
            state["next_action"] = "ask_search_source_with_db"
            return state
    
    # 중요: ask_search_source_with_db 상태에서는 숫자를 검색 소스 선택으로 우선 해석
    # 이 경우 analyze_input_node를 건너뛰고 ask_search_source_with_db_result로 직접 이동
    # 단, 새로운 검색 요청이면 리셋하고 일반 검색으로 처리
    if next_action == "ask_search_source_with_db":
        # 새로운 검색 요청인지 확인 (검색 키워드가 포함되어 있으면 새로운 검색)
        search_keywords = ["논문", "찾아", "검색", "paper", "article"]
        is_new_search = any(kw in user_input for kw in search_keywords)
        
        if is_new_search:
            # 새로운 검색 요청이므로 리셋하고 일반 검색으로 처리
            state["results"] = []
            state["displayed_count"] = 0
            state["keyword"] = ""
            state["next_action"] = ""  # 리셋
            # 아래 일반 검색 로직으로 진행
        else:
            # 순번 패턴 확인 (예: "1번", "2번째", "첫 번째" 등)
            # 순번 패턴이 있으면 detail로 처리해야 함
            user_input_lower = user_input.lower()
            number_patterns = [
                r'(\d+)번',
                r'(\d+)번째',
                r'첫.*번째?',
                r'두.*번째?',
                r'세.*번째?',
                r'(\d+)번.*알려|(\d+)번.*상세|(\d+)번.*정보|(\d+)번.*설명|(\d+)번.*에 대해',
                r'(\d+)번.*다운|(\d+)번.*pdf|(\d+)번.*받아|(\d+)번.*저장',
            ]
            has_number_pattern = any(re.search(p, user_input_lower) for p in number_patterns)
            
            # 순번 패턴이 있고 검색 결과가 있으면 detail로 처리
            # 단, "1번", "2번"만 있고 다른 키워드가 없으면 검색 소스 선택으로 처리
            if has_number_pattern and results_count > 0:
                # "1번", "2번"만 있는지 확인 (검색 소스 선택 가능성)
                simple_number_only = re.match(r'^[12]번$', user_input_lower.strip())
                if simple_number_only:
                    # "1번", "2번"만 있으면 검색 소스 선택으로 처리
                    old_intent = state.get("intent")
                    state["intent"] = "ask_source"
                    LOGGER.debug(
                        f"[라우팅 디버그] analyze_input_node - ask_search_source_with_db 상태에서 '1번' 또는 '2번' 감지 - "
                        f"old_intent={old_intent} -> new_intent={state.get('intent')}, "
                        f"next_action={state.get('next_action')}, user_input={user_input[:50]}"
                    )
                    state["next_action"] = "ask_search_source_with_db"
                    return state
                else:
                    # 다른 키워드가 있으면 detail로 처리
                    LOGGER.debug(f"ask_search_source_with_db 상태에서 순번 패턴 감지: '{user_input}' -> detail로 처리")
                    state["next_action"] = ""  # 리셋하여 일반 로직으로 진행
            else:
                # 검색 소스 선택 질문에 대한 답변으로 처리
                # analyze_input_node에서는 그냥 pass하고 ask_search_source_with_db_result에서 처리
                old_intent = state.get("intent")
                state["intent"] = "ask_source"
                LOGGER.debug(
                    f"[라우팅 디버그] analyze_input_node - ask_search_source_with_db 상태, 검색 소스 선택 질문 답변으로 처리 - "
                    f"old_intent={old_intent} -> new_intent={state.get('intent')}, "
                    f"next_action={state.get('next_action')}, user_input={user_input[:50]}"
                )
                state["next_action"] = "ask_search_source_with_db"
                return state
    
    # 새로운 검색 요청 감지: 명확한 검색 키워드가 있고, 
    # 이전 검색 결과와 다른 주제를 요청하는 경우 search로 강제 분류
    search_keywords = ["논문", "찾아", "검색", "paper", "article", "찾아줘", "찾아주세요"]
    has_search_keyword = any(kw in user_input for kw in search_keywords)
    
    # 명확한 검색 키워드가 있고, 순번 패턴이 없으면 새로운 검색으로 간주
    if has_search_keyword and results_count > 0:
        # 순번 패턴 확인 (예: "1번", "2번째", "첫 번째" 등)
        number_patterns = [
            r'(\d+)번',
            r'(\d+)번째',
            r'첫.*번째?',
            r'두.*번째?',
            r'세.*번째?',
            r'마지막',
        ]
        has_index_pattern = any(re.search(p, user_input.lower()) for p in number_patterns)
        
        # 순번 패턴이 없으면 새로운 검색 요청으로 간주
        if not has_index_pattern:
            LOGGER.debug(f"새로운 검색 요청 감지: '{user_input}' - 이전 결과 리셋")
            state["results"] = []
            state["displayed_count"] = 0
            state["keyword"] = ""
            state["vector_db_results"] = []
            state["download_index"] = None
            state["error"] = None  # 에러 초기화
            state["response"] = ""  # 응답 초기화
            # 검색 키워드 추출
            # "시스템 논문", "배터리 논문" 등에서 주제 추출
            state["query"] = user_input
            state["intent"] = "search"
            # 아래 LLM 분류 로직으로 진행 (results_count=0이 됨)
            results_count = 0  # results_count 업데이트
    
    # 정규식으로 순번을 먼저 파싱 (LLM보다 우선하여 정확도 향상)
    parsed_download_index = None
    user_input_lower = user_input.lower()
    
    # 먼저 순번을 파싱 (detail_match와 무관하게, results_count > 0이면)
    if results_count > 0:
        if user_input_lower.strip() in ["0번", "0번째"]:
            parsed_download_index = -1
            LOGGER.debug(f"정규식으로 파싱: '0번' -> -1 (에러 처리)")
        else:
            num_match = re.search(r'(\d+)번', user_input_lower)
            if num_match:
                raw_num = int(num_match.group(1))
                if raw_num == 0:
                    parsed_download_index = -1
                    LOGGER.debug(f"정규식으로 파싱: 숫자 0 -> -1 (에러 처리)")
                else:
                    parsed_download_index = raw_num - 1
                    LOGGER.debug(f"정규식으로 파싱: '{raw_num}번' -> {parsed_download_index} (0-based)")
            elif '첫' in user_input_lower:
                parsed_download_index = 0
                LOGGER.debug(f"정규식으로 파싱: '첫' -> 0 (0-based)")
    
    # detail 패턴 체크 (순번 파싱 후에도 추가 키워드 확인용)
    detail_patterns = [
        r'(\d+)번.*알려|(\d+)번.*상세|(\d+)번.*정보|(\d+)번.*설명|(\d+)번.*에 대해',
        r'(\d+)번.*다운|(\d+)번.*pdf|(\d+)번.*받아|(\d+)번.*저장',
        r'(\d+)번.*논문',
        r'(\d+)번.*결론',
        r'(\d+)번.*요약',
        r'^(\d+)번$',
        r'^(\d+)번째$',
        r'첫.*알려|첫.*상세|첫.*정보|첫.*설명|첫.*에 대해',
        r'첫.*다운|첫.*pdf|첫.*받아|첫.*저장',
        r'첫.*논문',
        r'^첫.*번째?$',
    ]
    
    detail_match = any(re.search(p, user_input_lower) for p in detail_patterns)
    
    # 순번이 파싱되었으면 detail로 간주
    if parsed_download_index is not None:
        detail_match = True
    
    # LLM을 사용한 intent 분류
    try:
        history = _render_history(messages[:-1] if len(messages) > 1 else [])  # 마지막 사용자 입력 제외
        
        prompt = INTENT_PROMPT.format_messages(
            results_count=results_count,
            user_input=user_input,
            history=history
        )
        
        response = LLM.invoke(prompt)
        response_text = response.content.strip()
        
        # JSON 파싱 (중첩된 중괄호 처리)
        intent_data = _extract_json_from_text(response_text)
        
        if intent_data:
            intent = intent_data.get("intent", "search")
            # download intent는 detail로 변환 (PDF 다운로드는 검색 시 자동 수행되므로)
            if intent == "download":
                intent = "detail"
            query = intent_data.get("query", "")
            download_index_raw = intent_data.get("download_index")
            
            # 정규식으로 파싱한 값이 있으면 우선 사용 (LLM 오류 방지)
            if parsed_download_index is not None:
                download_index = parsed_download_index
                # 정규식으로 순번을 파싱했다면 intent도 detail로 강제 설정
                intent = "detail"
                query = ""  # detail intent이면 query는 빈 문자열
                LOGGER.debug(f"정규식으로 파싱한 값 우선 사용: intent=detail, download_index={download_index} (0-based), LLM intent={intent_data.get('intent')} 무시")
            elif download_index_raw is not None:
                # LLM이 반환한 download_index는 1-based이므로 0-based로 변환
                try:
                    # 문자열이나 숫자 모두 처리
                    if isinstance(download_index_raw, str):
                        # 숫자만 추출 (예: "1", "1번", "1번째" 등)
                        num_match = re.search(r'(\d+)', str(download_index_raw))
                        if num_match:
                            download_index = int(num_match.group(1)) - 1
                        else:
                            download_index = None
                    elif isinstance(download_index_raw, (int, float)):
                        download_index = int(download_index_raw) - 1
                    else:
                        download_index = None
                except (ValueError, TypeError, AttributeError) as e:
                    LOGGER.warning(f"download_index 파싱 실패: {download_index_raw}, 오류: {e}")
                    download_index = None
            else:
                download_index = None
        else:
            # JSON 파싱 실패 시 기본값
            intent = "search"
            query = user_input
            download_index = None
            
            # 정규식으로 파싱한 값이 있으면 사용
            if parsed_download_index is not None:
                intent = "detail"
                download_index = parsed_download_index
            
    except Exception as e:
        LOGGER.warning(f"LLM intent 분류 실패, 정규식으로 폴백: {e}")
        # LLM 실패 시 정규식 폴백
        intent = "search"
        query = user_input
        download_index = None
        
        # 정규식으로 파싱한 값이 있으면 사용
        if parsed_download_index is not None:
            intent = "detail"
            download_index = parsed_download_index
        else:
            # 정규식으로 다시 파싱 시도
            detail_match = any(re.search(p, user_input_lower) for p in detail_patterns)
            
            # results_count > 0이고 순번만 입력한 경우 detail로 분류
            if not detail_match and results_count > 0 and next_action != "ask_search_source_with_db":
                simple_number_patterns = [
                    r'^(\d+)번$',
                    r'^(\d+)번째$',
                    r'^첫.*번째?$',
                ]
                if any(re.search(p, user_input_lower) for p in simple_number_patterns):
                    detail_match = True
            
            if detail_match and results_count > 0:
                intent = "detail"
                # "0번"은 유효하지 않으므로 -1로 설정 (범위 체크에서 걸리게 함)
                if user_input_lower.strip() in ["0번", "0번째"]:
                    download_index = -1
                    LOGGER.warning(f"사용자 입력 '0번'은 유효하지 않으므로 -1로 설정: {user_input}")
                # "1번째", "1번" 모두 처리
                else:
                    num_match = re.search(r'(\d+)번', user_input_lower)
                    if num_match:
                        raw_num = int(num_match.group(1))
                        if raw_num == 0:
                            # 혹시 모르니 다시 체크
                            download_index = -1
                            LOGGER.warning(f"download_index가 0이므로 -1로 설정 (에러 처리): {user_input}")
                        else:
                            download_index = raw_num - 1
                    elif '첫' in user_input_lower:
                        download_index = 0
                    else:
                        download_index = None
                        LOGGER.warning(f"정규식으로 download_index를 찾을 수 없음: {user_input_lower}")
    
    # intent에 따라 처리
    state["intent"] = intent
    state["query"] = query if query else user_input
    
    if intent == "detail":
        if download_index is not None and results_count > 0:
            # 🆕 인덱스 범위 체크 (Early validation)
            if download_index < 0 or download_index >= results_count:
                available_indices = ", ".join([str(i + 1) for i in range(results_count)])
                requested_num = download_index + 1 if download_index >= 0 else "알 수 없는"
                error_msg = (
                    f"죄송합니다. 요청하신 {requested_num}번째 논문을 찾을 수 없습니다.\n\n"
                    f"현재 검색 결과는 {results_count}개입니다. "
                    f"사용 가능한 순번: {available_indices}번째."
                )
                LOGGER.warning(
                    f"analyze_input: download_index 범위 초과 - download_index={download_index}, "
                    f"results_count={results_count}, user_input={user_input}"
                )
                state["error"] = error_msg
                state["response"] = error_msg
                state["next_action"] = "generate_response"
                # 🆕 디버그: 범위 초과 에러 발생 시 state 상태 로깅
                LOGGER.debug(
                    f"[라우팅 디버그] analyze_input 범위 초과 에러 발생 시 state 상태 - "
                    f"intent={state.get('intent')}, next_action={state.get('next_action')}, "
                    f"search_source={state.get('search_source')}, results_count={results_count}"
                )
                return state
            
            state["download_index"] = download_index
            state["last_selected_paper_index"] = download_index  # 마지막 선택 인덱스 저장
            LOGGER.debug(
                f"analyze_input: detail intent 설정 - download_index={download_index} (0-based), "
                f"results_count={results_count}, user_input={user_input}"
            )
            # 🆕 search_source와 벡터DB 존재 여부에 따라 다른 노드로 라우팅
            search_source = state.get("search_source", "unknown")
            if search_source == "internal":
                # 사내 검색 → rag_answer (Milvus DB에서 필요한 정보를 가져와 LLM이 답변)
                state["next_action"] = "rag_answer"
            else:
                # 웹 검색이더라도 벡터DB에 해당 논문이 있으면 rag_answer 사용
                target_result = results[download_index]
                identifier = target_result.get("identifier", "")
                
                if identifier and check_paper_exists(identifier):
                    # 벡터DB에 존재 → rag_answer로 라우팅 (더 상세한 RAG 답변)
                    LOGGER.debug(
                        f"analyze_input: 웹 검색 결과지만 벡터DB에 논문이 존재하여 rag_answer로 라우팅 - "
                        f"identifier={identifier}, download_index={download_index}"
                    )
                    state["next_action"] = "rag_answer"
                else:
                    # 벡터DB에 없음 → show_article_detail로 라우팅 (PDF 기반 요약)
                    LOGGER.debug(
                        f"analyze_input: 벡터DB에 논문이 없어 show_article_detail로 라우팅 - "
                        f"identifier={identifier or '없음'}, download_index={download_index}"
                    )
                    state["next_action"] = "show_article_detail"
            return state
        elif results_count > 0 and state.get("last_selected_paper_index", -1) >= 0:
            # 🆕 순번이 없지만 이전에 선택한 논문이 있으면 그것 사용
            last_index = state.get("last_selected_paper_index", 0)
            # 🆕 last_selected_paper_index도 범위 체크
            if last_index < 0 or last_index >= results_count:
                # 범위를 벗어난 last_selected_paper_index는 무시하고 search로 처리
                LOGGER.debug(
                    f"analyze_input: last_selected_paper_index 범위 초과 - last_index={last_index}, "
                    f"results_count={results_count}, search로 전환"
                )
                state["intent"] = "search"
                state["query"] = user_input
            else:
                state["download_index"] = last_index
                # 🆕 search_source와 벡터DB 존재 여부에 따라 다른 노드로 라우팅
                search_source = state.get("search_source", "unknown")
                if search_source == "internal":
                    # 사내 검색 → rag_answer (Milvus DB에서 필요한 정보를 가져와 LLM이 답변)
                    state["next_action"] = "rag_answer"
                else:
                    # 웹 검색이더라도 벡터DB에 해당 논문이 있으면 rag_answer 사용
                    target_result = results[last_index]
                    identifier = target_result.get("identifier", "")
                    
                    if identifier and check_paper_exists(identifier):
                        # 벡터DB에 존재 → rag_answer로 라우팅 (더 상세한 RAG 답변)
                        LOGGER.debug(
                            f"analyze_input: 웹 검색 결과지만 벡터DB에 논문이 존재하여 rag_answer로 라우팅 - "
                            f"identifier={identifier}, last_index={last_index}"
                        )
                        state["next_action"] = "rag_answer"
                    else:
                        # 벡터DB에 없음 → show_article_detail로 라우팅 (PDF 기반 요약)
                        LOGGER.debug(
                            f"analyze_input: 벡터DB에 논문이 없어 show_article_detail로 라우팅 - "
                            f"identifier={identifier or '없음'}, last_index={last_index}"
                        )
                        state["next_action"] = "show_article_detail"
                return state
        else:
            # 결과가 없으면 search로 변경
            state["intent"] = "search"
            state["query"] = user_input
    
    # 검색 의도인 경우
    state["intent"] = "search"
    
    # 새로운 검색 요청이면 이전 검색 결과 리셋
    previous_query = state.get("previous_query", "")
    current_query = state.get("query", "").strip() or user_input.strip()
    
    # 검색어가 변경되었거나 이전 검색 결과가 있으면 리셋
    if current_query != previous_query or results_count > 0:
        # 새로운 검색이므로 마지막 선택 논문 인덱스도 리셋
        state["last_selected_paper_index"] = -1
        state["results"] = []
        state["displayed_count"] = 0
        state["keyword"] = ""  # 새로운 검색이므로 키워드도 리셋
        state["previous_query"] = current_query  # 현재 검색어 저장
    
    # 웹/사내 서버 명시 여부 확인 - 원본 user_input 사용!
    # 중요: state["query"]는 LLM이 반환한 값일 수 있어 "웹에서" 같은 키워드가 누락될 수 있음
    has_web_keyword, has_internal_keyword = parse_search_source_keywords(user_input)
    
    if has_web_keyword and not has_internal_keyword:
        state["search_source"] = "web"
        state["next_action"] = "search_papers"
    elif has_internal_keyword and not has_web_keyword:
        state["search_source"] = "internal"
        state["next_action"] = "check_db"
    else:
        state["search_source"] = "unknown"
        state["next_action"] = "check_db"
    
    return state


def check_db_node(state: ScienceONAgentState) -> ScienceONAgentState:
    """벡터DB에서 논문 검색."""
    # 디버그: check_db_node 노드 진입 시 state 상태 로깅
    LOGGER.debug(
        f"[라우팅 디버그] check_db_node 노드 진입 - "
        f"intent={state.get('intent')}, next_action={state.get('next_action')}, "
        f"search_source={state.get('search_source')}, "
        f"results_count={len(state.get('results', []))}, "
        f"user_input={state.get('user_input', '')[:50]}"
    )
    
    # 웹 검색 결과가 이미 있고, detail intent이면 check_db를 건너뛰고 generate_response로
    results = state.get("results", [])
    intent = state.get("intent", "")
    search_source = state.get("search_source", "unknown")
    
    if search_source == "web" and len(results) > 0 and intent == "detail":
        # 웹 검색 결과가 이미 있고 detail intent이면 check_db를 건너뛰고 generate_response로
        LOGGER.debug(
            f"[라우팅 디버그] check_db_node - 웹 검색 결과 이미 있음, detail intent -> generate_response로 스킵"
        )
        state["next_action"] = "generate_response"
        return state
    
    query = state.get("user_input", "")
    
    # 키워드 생성 (한 번만 호출하여 비용 절감)
    # 쉼표로 구분된 키워드에서 첫 번째만 추출 (컬렉션 이름용)
    keyword = state.get("keyword", "")
    collection_keyword = None  # 컬렉션 이름용 키워드
    
    if not keyword:
        try:
            search_keyword = generate_search_keyword(query)
            # 쉼표로 구분된 키워드에서 첫 번째 키워드만 추출
            collection_keyword = search_keyword.split(',')[0].strip()
            state["keyword"] = search_keyword  # 전체 키워드는 state에 저장 (search_papers에서 재사용)
        except Exception as e:
            LOGGER.warning(f"키워드 생성 실패: {e}")
            collection_keyword = extract_keyword_from_text(query)
    else:
        # 이미 생성된 키워드가 있으면 첫 번째만 추출
        collection_keyword = keyword.split(',')[0].strip()
    
    # 벡터DB 검색 (생성된 키워드 전달)
    results = check_vector_db(query, limit=5, max_distance=1.5, keyword=collection_keyword)
    
    if results and len(results) > 0:
        # 벡터DB에 있음
        state["vector_db_results"] = results  # 공통으로 설정
        
        if search_source == "internal":
            # 🆕 사내 서버 명시됨 → 논문 목록 표시 (웹 검색처럼)
            paper_results = _convert_vector_db_results_to_paper_results(results)
            state["results"] = paper_results
            state["displayed_count"] = len(paper_results)
            
            # 논문 목록 요약 생성 (state["keyword"]를 사용)
            keyword_for_summary = state.get("keyword", "").split(',')[0].strip() if state.get("keyword") else query
            summary = generate_summary(
                user_query=query,
                keyword=keyword_for_summary,
                results=paper_results,
                search_source="사내 서버"
            )
            state["summary"] = summary
            state["intent"] = "search"
            state["search_source"] = "internal"
            state["next_action"] = "generate_response"
            # 🆕 디버그: check_db_node에서 next_action 설정
            LOGGER.debug(
                f"[라우팅 디버그] check_db_node - 벡터DB 결과 있음, 사내 서버 명시됨 - "
                f"next_action={state.get('next_action')}, intent={state.get('intent')}"
            )
        else:
            # 명시 안 함 → 웹/사내 선택 질문
            state["next_action"] = "ask_search_source_with_db"
            # 🆕 디버그: check_db_node에서 next_action 설정
            LOGGER.debug(
                f"[라우팅 디버그] check_db_node - 벡터DB 결과 있음, 선택 질문 필요 - "
                f"next_action={state.get('next_action')}, intent={state.get('intent')}"
            )
    else:
        # 벡터DB에 없음
        if search_source == "internal":
            # 사내 서버 명시됨 → 웹으로 전환 메시지
            LOGGER.debug(
                f"[라우팅 디버그] check_db_node - 벡터DB 결과 없음, 사내 서버 명시됨 → 웹 검색으로 전환"
            )
            state["response"] = "사내 서버에 정보가 없어서 웹에서 검색을 실행합니다."
            state["search_source"] = "web"
            state["next_action"] = "search_papers"
        else:
            # 명시 안 함 → 웹으로 전환 메시지
            LOGGER.debug(
                f"[라우팅 디버그] check_db_node - 벡터DB 결과 없음, 웹 검색으로 전환: "
                f"next_action='search_papers' 설정"
            )
            state["response"] = "사내 서버에 관련 정보가 없습니다. 웹에서 검색을 시작하겠습니다."
            state["search_source"] = "web"
            state["next_action"] = "search_papers"
    
    # 디버그: check_db_node 함수 종료 직전 최종 state 값 로깅
    LOGGER.debug(
        f"[라우팅 디버그] check_db_node - 함수 종료: "
        f"next_action={state.get('next_action')}, "
        f"search_source={state.get('search_source')}, "
        f"vector_db_results_count={len(state.get('vector_db_results', []))}"
    )
    
    return state


def ask_search_source_with_db_result(state: ScienceONAgentState) -> ScienceONAgentState:
    """벡터DB에 결과가 있을 때 웹/사내 서버 선택 질문."""
    # 디버그: ask_search_source_with_db_result 노드 진입 시 state 상태 로깅
    LOGGER.debug(
        f"[라우팅 디버그] ask_search_source_with_db_result 노드 진입 - "
        f"intent={state.get('intent')}, next_action={state.get('next_action')}, "
        f"search_source={state.get('search_source')}, "
        f"results_count={len(state.get('results', []))}, "
        f"user_input={state.get('user_input', '')[:50]}"
    )
    user_input = state.get("user_input", "")
    messages = state.get("messages", [])
    next_action = state.get("next_action", "")
    
    # 숫자 키워드 추가 확인 (정규표현식으로 단어 경계 확인하여 더 엄격하게)
    user_input_lower = user_input.lower()
    
    # 정규표현식으로 정확히 "2번", "2", "1번", "1"만 매칭 (단어 경계 확인)
    # "이", "일" 같은 단일 문자는 제거 (다른 단어에 포함될 수 있음)
    web_patterns = [r'\b2번\b', r'\b2\b', r'^2번$', r'^2$']
    internal_patterns = [r'\b1번\b', r'\b1\b', r'^1번$', r'^1$']
    
    has_web_number = any(re.search(p, user_input_lower) for p in web_patterns)
    has_internal_number = any(re.search(p, user_input_lower) for p in internal_patterns)
    
    has_web_keyword, has_internal_keyword = parse_search_source_keywords(user_input)
    has_web_keyword = has_web_keyword or has_web_number
    has_internal_keyword = has_internal_keyword or has_internal_number
    
    # 새로운 검색 요청인지 확인
    # next_action이 ask_search_source_with_db이고, 사용자 입력에 선택 키워드가 없으면
    # 이것은 새로운 검색 요청이므로 첫 질문을 표시해야 함
    # 단, detail intent이고 results가 있고 error가 있으면 범위 초과 에러 상황이므로 새로운 검색 요청이 아님
    intent = state.get("intent", "")
    results = state.get("results", [])
    error = state.get("error")
    is_range_error = (
        intent == "detail" and 
        len(results) > 0 and 
        error is not None and 
        "번째 논문을 찾을 수 없습니다" in str(error)
    )
    
    # 새로운 검색 요청 감지: 검색 키워드가 포함되어 있으면 새로운 검색으로 간주
    search_keywords = ["논문", "찾아", "검색", "paper", "article", "찾아줘", "찾아주세요"]
    has_search_keyword_in_input = any(kw in user_input for kw in search_keywords)
    
    is_new_search_request = (
        next_action == "ask_search_source_with_db" and
        not has_web_keyword and
        not has_internal_keyword and
        user_input and
        len(user_input.strip()) > 0 and
        not is_range_error and  # 🆕 범위 초과 에러 상황이면 새로운 검색 요청이 아님
        has_search_keyword_in_input  # 🆕 검색 키워드가 있어야 새로운 검색 요청
    )
    
    # 사용자가 선택 질문에 답변한 경우인지 확인
    is_selection_response = has_web_keyword or has_internal_keyword
    
    # 디버그: is_new_search_request 평가 전 조건값들 로깅
    LOGGER.debug(
        f"[라우팅 디버그] ask_search_source_with_db_result - 조건 평가: "
        f"next_action={next_action}, "
        f"has_web_keyword={has_web_keyword}, "
        f"has_internal_keyword={has_internal_keyword}, "
        f"has_web_number={has_web_number}, "
        f"has_internal_number={has_internal_number}, "
        f"has_search_keyword_in_input={has_search_keyword_in_input}, "
        f"user_input='{user_input[:50]}', "
        f"user_input.strip()='{user_input.strip()[:50]}', "
        f"len(user_input.strip())={len(user_input.strip()) if user_input else 0}, "
        f"is_range_error={is_range_error}, "
        f"is_new_search_request={is_new_search_request}, "
        f"is_selection_response={is_selection_response}, "
        f"messages_count={len(messages)}"
    )
    
    if is_new_search_request:
        # 새로운 검색 요청이므로 첫 선택 질문 표시
        # 사용자 입력을 기다려야 하므로 generate_response로 이동
        response_msg = (
            "사내 서버에 관련 정보가 있는데 어디서 검색하시겠습니까?\n\n"
            "1. 사내 서버에서 검색 (빠른 답변)\n"
            "2. 웹에서 검색 (최신 정보)"
        )
        state["response"] = response_msg
        state["next_action"] = "generate_response"  # 사용자 입력 대기
        # 디버그: intent 변경 전후 로깅
        old_intent = state.get("intent")
        state["intent"] = "ask_source"  # 선택 질문 상태임을 표시
        LOGGER.debug(
            f"[라우팅 디버그] ask_search_source_with_db_result - 새로운 검색 요청 감지, intent 변경 - "
            f"old_intent={old_intent} -> new_intent={state.get('intent')}, "
            f"next_action={state.get('next_action')}, user_input={user_input[:50]}"
        )
        state["ask_source_retry_count"] = 0
        return state
    
    # 무한 루프 방지: 재시도 카운터 확인
    MAX_ASK_SOURCE_RETRIES = 3
    retry_count = state.get("ask_source_retry_count", 0)
    
    is_user_response = len(messages) >= 2
    
    # 디버그: 분기 판단을 위한 상태값 로깅
    LOGGER.debug(
        f"[라우팅 디버그] ask_search_source_with_db_result - 분기 판단: "
        f"is_user_response={is_user_response} (messages_count={len(messages)}), "
        f"is_selection_response={is_selection_response}, "
        f"retry_count={retry_count}"
    )
    
    if is_user_response and is_selection_response:
        LOGGER.debug(
            f"[라우팅 디버그] ask_search_source_with_db_result - 분기: is_user_response AND is_selection_response"
        )
        if has_internal_keyword and not has_web_keyword:
            # 사내 서버 선택 → 벡터DB 결과를 논문 목록으로 변환하여 표시
            LOGGER.debug(
                f"[라우팅 디버그] ask_search_source_with_db_result - 사내 서버 선택 감지: "
                f"has_internal_keyword={has_internal_keyword}, user_input='{user_input[:50]}'"
            )
            vector_db_results = state.get("vector_db_results", [])
            if vector_db_results:
                # 벡터DB 결과를 논문 목록 형식으로 변환
                paper_results = _convert_vector_db_results_to_paper_results(vector_db_results)
                state["results"] = paper_results
                state["displayed_count"] = len(paper_results)
                
                # 논문 목록 요약 생성
                query = state.get("query", "") or state.get("user_input", "")
                keyword = state.get("keyword", "")
                if not keyword:
                    try:
                        keyword = generate_search_keyword(query)
                        state["keyword"] = keyword
                    except Exception:
                        keyword = query
                
                summary = generate_summary(
                    user_query=query,
                    keyword=keyword.split(',')[0].strip() if keyword else query,
                    results=paper_results,
                    search_source="사내 서버"
                )
                state["summary"] = summary
                state["intent"] = "search"
                state["search_source"] = "internal"
                state["next_action"] = "generate_response"
                state["ask_source_retry_count"] = 0
                state["response"] = ""  # 선택 질문 메시지 제거
            else:
                state["response"] = "벡터DB 검색 결과가 없습니다."
                state["next_action"] = "generate_response"
        elif has_web_keyword and not has_internal_keyword:
            # 웹 선택 → 웹 검색
            LOGGER.debug(
                f"[라우팅 디버그] ask_search_source_with_db_result - 웹 선택 감지: "
                f"has_web_keyword={has_web_keyword}, user_input='{user_input[:50]}', "
                f"next_action을 'search_papers'로 설정"
            )
            state["search_source"] = "web"
            state["next_action"] = "search_papers"
            state["ask_source_retry_count"] = 0  # 리셋
            state["response"] = ""  # 선택 질문 메시지 제거
        else:
            # 명확하지 않으면 다시 질문 (하지만 최대 재시도 횟수 확인)
            if retry_count >= MAX_ASK_SOURCE_RETRIES:
                # 최대 재시도 횟수 초과 → 기본값(사내 서버) 선택
                LOGGER.warning(f"검색 소스 선택 질문이 {retry_count}회 반복되어 기본값(사내 서버) 선택")
                state["search_source"] = "internal"
                state["next_action"] = "rag_answer"
                state["ask_source_retry_count"] = 0  # 리셋
            else:
                # 명확하지 않으면 다시 질문
                response_msg = (
                    "사내 서버에 관련 정보가 있는데 어디서 검색하시겠습니까?\n"
                    "1. 사내 서버에서 검색 (빠른 답변)\n"
                    "2. 웹에서 검색 (최신 정보)"
                )
                state["response"] = response_msg
                state["next_action"] = "ask_search_source_with_db"
                state["ask_source_retry_count"] = retry_count + 1
    elif is_user_response and not is_selection_response:
        # 사용자가 답변했지만 선택 키워드가 없는 경우 (명확하지 않은 답변)
        LOGGER.debug(
            f"[라우팅 디버그] ask_search_source_with_db_result - 분기: is_user_response AND NOT is_selection_response"
        )
        if retry_count >= MAX_ASK_SOURCE_RETRIES:
            # 최대 재시도 횟수 초과 → 기본값(사내 서버) 선택
            LOGGER.warning(f"검색 소스 선택 질문이 {retry_count}회 반복되어 기본값(사내 서버) 선택")
            state["search_source"] = "internal"
            state["next_action"] = "rag_answer"
            state["ask_source_retry_count"] = 0  # 리셋
        else:
            # 명확하지 않으면 다시 질문
            response_msg = (
                "사내 서버에 관련 정보가 있는데 어디서 검색하시겠습니까?\n"
                "1. 사내 서버에서 검색 (빠른 답변)\n"
                "2. 웹에서 검색 (최신 정보)"
            )
            state["response"] = response_msg
            state["next_action"] = "ask_search_source_with_db"
            state["ask_source_retry_count"] = retry_count + 1
    else:
        # 첫 질문이거나 기타 경우 (이론적으로는 도달하지 않아야 함)
        LOGGER.debug(
            f"[라우팅 디버그] ask_search_source_with_db_result - 분기: else (첫 질문이거나 기타 경우), "
            f"is_user_response={is_user_response}, is_selection_response={is_selection_response}"
        )
        response_msg = (
            "사내 서버에 관련 정보가 있는데 어디서 검색하시겠습니까?\n\n"
            "1. 사내 서버에서 검색 (빠른 답변)\n"
            "2. 웹에서 검색 (최신 정보)"
        )
        state["response"] = response_msg
        state["next_action"] = "ask_search_source_with_db"
        state["ask_source_retry_count"] = 0  # 첫 질문이므로 0으로 설정
    
    # 디버그: 함수 종료 직전 최종 state 값 로깅
    LOGGER.debug(
        f"[라우팅 디버그] ask_search_source_with_db_result - 함수 종료: "
        f"next_action={state.get('next_action')}, "
        f"search_source={state.get('search_source')}, "
        f"intent={state.get('intent')}, "
        f"response_length={len(state.get('response', ''))}"
    )
    
    return state


def rag_answer_node(state: ScienceONAgentState) -> ScienceONAgentState:
    """RAG로 사용자 질문에 답변."""
    # 이전 에러 상태 초기화 (이전 에러가 남아있지 않도록)
    state["error"] = None
    
    if not OPENAI_MODEL:
        state["response"] = "OpenAI API 키가 설정되지 않았습니다."
        state["next_action"] = "generate_response"
        return state
    
    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0, max_tokens=1500)
    
    download_index = state.get("download_index", -1)
    results = state.get("results", [])
    
    # download_index가 None이거나 -1이면 last_selected_paper_index 확인
    if download_index == -1 or download_index is None:
        last_index = state.get("last_selected_paper_index", -1)
        if last_index >= 0:
            download_index = last_index
    
    if not results:
        state["response"] = "먼저 논문을 검색한 뒤에 상세 정보를 요청해 주세요."
        state["next_action"] = "generate_response"
        return state
    
    # show_article_detail과 동일한 방식으로 인덱스 검증
    # download_index가 범위를 벗어난 경우 (0번 또는 범위 초과) 에러 메시지 반환
    if download_index < 0 or download_index >= len(results):
        # 범위를 벗어남: show_article_detail과 동일한 에러 메시지
        available_indices = ", ".join([str(i + 1) for i in range(len(results))])
        requested_num = download_index + 1 if download_index >= 0 else "0"
        error_msg = (
            f"죄송합니다. 요청하신 {requested_num}번째 논문을 찾을 수 없습니다.\n\n"
            f"현재 검색 결과는 {len(results)}개입니다. "
            f"사용 가능한 순번: {available_indices}번째."
        )
        LOGGER.warning(
            f"rag_answer: download_index 범위 초과 - download_index={download_index}, "
            f"results_count={len(results)}, user_input={state.get('user_input', '')}"
        )
        state["response"] = error_msg
        state["error"] = error_msg
        state["next_action"] = "generate_response"
        return state
    
    has_valid_download_index = (download_index >= 0 and download_index < len(results))
    
    if has_valid_download_index:
        # 특정 논문에 대한 질문 → 해당 논문의 청크만 검색
        target_result = results[download_index]
        identifier = target_result.get("identifier", "")
        title = target_result.get("title", f"{download_index + 1}번째 논문")
        user_input = state.get("user_input", "")
        
        if identifier:
            # 해당 논문의 관련 청크 검색
            paper_chunks = retrieve_paper_chunks_from_milvus(
                query=user_input,
                identifier=identifier,
                keyword=state.get("keyword"),
                top_k=10,
            )
            
            if paper_chunks:
                # 관련 청크들을 하나의 텍스트로 결합
                context_text = "\n\n".join([
                    f"[청크 {idx+1}]\n{chunk.get('content', '')}"
                    for idx, chunk in enumerate(paper_chunks[:5])  # 상위 5개만 사용
                ])
                
                prompt = (
                    f"다음은 논문 '{title}'의 관련 내용입니다.\n\n"
                    f"사용자 질문: {user_input}\n\n"
                    f"논문 내용:\n{context_text}\n\n"
                    f"위 내용을 바탕으로 사용자 질문에 대해 정확하고 상세하게 답변해주세요. "
                    f"답변은 다음 형식을 따라주세요:\n"
                    f"- 논문의 제목과 관련 섹션을 명시\n"
                    f"- 사용자 질문에 대한 핵심 내용을 구조화하여 설명\n"
                    f"- 가능한 경우 구체적인 내용을 인용하여 설명\n"
                    f"- 한국어로 친절하고 전문적으로 작성\n\n"
                    f"답변 시작 부분에 '사용자 질문에 대한 답변입니다.' 같은 서론 문구 없이 바로 본문 내용으로 시작해주세요."
                )
                
                try:
                    response = llm.invoke(prompt)
                    answer = response.content if hasattr(response, 'content') else str(response)
                    response_msg = (
                        f"📄 {download_index + 1}번째 논문: {title}\n\n"
                        f"📝 답변:\n{answer}"
                    )
                    state["response"] = response_msg
                    state["intent"] = "detail"
                except Exception as e:
                    LOGGER.exception("RAG 답변 생성 중 오류 발생")
                    state["response"] = f"답변 생성 중 오류가 발생했습니다: {str(e)}"
            else:
                # 청크를 찾지 못한 경우 모든 논문에서 검색하도록 폴백
                LOGGER.debug(f"rag_answer: 논문 '{title}'의 청크를 찾지 못해 모든 논문에서 검색")
                has_valid_download_index = False
        else:
            # identifier가 없으면 모든 논문에서 검색하도록 폴백
            LOGGER.debug(f"rag_answer: 논문 '{title}'의 식별자가 없어 모든 논문에서 검색")
            has_valid_download_index = False
    
    # download_index가 없거나 -1인 경우만 모든 논문에서 검색 (순번이 명시되지 않은 경우)
    # 특정 논문 검색 실패(청크 없음, identifier 없음)도 모든 논문에서 검색
    if not has_valid_download_index:
        # download_index가 명시되지 않은 경우(download_index < 0)에만 모든 논문 검색
        # 범위를 벗어난 경우는 위에서 이미 에러 메시지 반환
        if download_index < 0 or download_index is None:
            LOGGER.debug(f"rag_answer: download_index가 명시되지 않아 모든 논문에서 검색 (download_index={download_index})")
        else:
            LOGGER.debug(f"rag_answer: 특정 논문 검색 실패로 모든 논문에서 검색 (download_index={download_index})")
        # 일반 RAG 답변 (벡터DB 결과 전체 사용)
        # 벡터DB 결과에서 컨텍스트 구성
        context_chunks = retrieve_paper_chunks_from_milvus(
            query=state.get("user_input", ""),
            keyword=state.get("keyword"),
            top_k=6,
        )

        if context_chunks:
            context_text = "\n\n".join(
                f"[{chunk.get('title') or '논문 정보'}]\n{chunk.get('content', '')}"
                for chunk in context_chunks
            )

            prompt = (
                f"다음은 사용자의 질문과 관련된 논문 정보입니다.\n\n"
                f"사용자 질문: {state.get('user_input', '')}\n\n"
                f"관련 논문 정보:\n{context_text}\n\n"
                f"위 정보를 바탕으로 사용자 질문에 대해 정확하고 상세하게 답변해주세요. "
                f"한국어로 친절하게 작성해주세요. "
                f"답변 시작 부분에 '사용자 질문에 대한 답변입니다.' 같은 서론 문구 없이 바로 본문 내용으로 시작해주세요. "
                f"논문 제목을 먼저 명시한 후, 내용을 설명해주세요."
            )

            try:
                response = llm.invoke(prompt)
                answer = response.content if hasattr(response, 'content') else str(response)
                state["response"] = answer
            except Exception as e:
                LOGGER.exception("RAG 답변 생성 중 오류 발생")
                state["response"] = f"답변 생성 중 오류가 발생했습니다: {str(e)}"
        elif state.get("vector_db_results"):
            # 이전 결과가 남아 있다면 하위 호환을 위해 사용
            context_papers = state["vector_db_results"]
            context_text = "\n\n".join(
                f"논문 {idx+1}:\n제목: {p['title']}\n내용: {p.get('content', '')[:2000]}"
                for idx, p in enumerate(context_papers[:3])
            )

            prompt = (
                f"다음은 사용자의 질문과 관련된 논문 정보입니다.\n\n"
                f"사용자 질문: {state.get('user_input', '')}\n\n"
                f"관련 논문 정보:\n{context_text}\n\n"
                f"위 정보를 바탕으로 사용자 질문에 대해 정확하고 상세하게 답변해주세요. "
                f"한국어로 친절하게 작성해주세요. "
                f"답변 시작 부분에 '사용자 질문에 대한 답변입니다.' 같은 서론 문구 없이 바로 본문 내용으로 시작해주세요. "
                f"논문 제목을 먼저 명시한 후, 내용을 설명해주세요."
            )

            try:
                response = llm.invoke(prompt)
                answer = response.content if hasattr(response, 'content') else str(response)
                state["response"] = answer
            except Exception as e:
                LOGGER.exception("RAG 답변 생성 중 오류 발생")
                state["response"] = f"답변 생성 중 오류가 발생했습니다: {str(e)}"
        else:
            state["response"] = "벡터DB 검색 결과가 없습니다."
    
    state["next_action"] = "generate_response"
    return state


def search_papers(state: ScienceONAgentState) -> ScienceONAgentState:
    """논문 검색을 수행한다."""
    displayed_count = state.get("displayed_count", 0)
    results = state.get("results", [])
    current_query = state.get("query", "").strip()
    previous_keyword = state.get("keyword", "")  # 이전 검색 키워드
    
    # 이미 생성된 키워드가 있으면 재사용 (비용 절감)
    keyword = None
    if previous_keyword:
        # 이미 생성된 키워드 재사용
        keyword = previous_keyword
    else:
        # 키워드가 없으면 생성
        try:
            if current_query:
                keyword = generate_search_keyword(current_query)
            else:
                keyword = ""
        except Exception as e:
            LOGGER.warning(f"키워드 생성 실패: {e}")
            keyword = ""
    
    is_new_search = (
        not previous_keyword or 
        keyword != previous_keyword or
        (displayed_count == 0 and not results)
    )
    
    # 페이지네이션: 이미 검색 결과가 있고 displayed_count > 0이고 같은 검색어면 다음 결과 가져오기
    if not is_new_search and displayed_count > 0 and results:
        # 저장된 결과에서 다음 3개 가져오기
        next_results = results[displayed_count:displayed_count + 3]

        if not next_results:
            return {
                "error": "더 이상 검색 결과가 없습니다.",
                "results": results,
                "summary": "모든 검색 결과를 확인하셨습니다.",
                "displayed_count": displayed_count,
            }

        # 다음 결과에 대한 summary 생성
        search_source = state.get("search_source", "웹")
        search_source_label = "웹" if search_source == "web" else "사내 서버" if search_source == "internal" else "웹"
        summary = generate_summary(
            user_query=state.get("query", ""),
            keyword=keyword,  # 이미 추출한 keyword 사용
            results=next_results,
            search_source=search_source_label
        )

        new_displayed_count = displayed_count + len(next_results)
        LOGGER.info("추가 검색 결과 표시: %d~%d번째 논문 (%d건)",
                    displayed_count + 1, new_displayed_count, len(next_results))

        return {
            "query": state.get("query", ""),
            "keyword": keyword,  # keyword도 함께 반환
            "results": results,
            "summary": summary,
            "error": None,
            "displayed_count": new_displayed_count,
        }

    query = state.get("query", "").strip()
    if not query:
        return {
            "error": "검색어가 없습니다.",
            "results": [],
            "summary": "검색어를 입력해 주세요.",
        }

    try:
        # keyword는 이미 위에서 추출했으므로 재사용 (중복 호출 제거)
        if not keyword:  # keyword가 없으면 한 번 더 시도
            keyword = generate_search_keyword(query)

        # 새로운 검색이므로 displayed_count 리셋
        if is_new_search:
            displayed_count = 0

        # check_db_node에서 이미 벡터DB 확인을 완료했고,
        # search_papers에 도달했다는 것은 웹 검색으로 전환된 상태이므로 바로 웹 검색을 수행합니다.
        # 처음 검색 시 더 많은 결과를 가져오기 (기본값 limit=10)
        all_results = search_scienceon_papers(keyword, enable_downloads=False)

        # 처음 3개만 표시하고 summary 생성
        displayed_results = all_results[:3]
        # 검색 소스 확인 (웹 검색으로 전환된 상태이므로 "웹"으로 설정)
        search_source = state.get("search_source", "웹")
        search_source_label = "웹" if search_source == "web" else "사내 서버" if search_source == "internal" else "웹"
        summary = generate_summary(
            user_query=query,
            keyword=keyword,
            results=displayed_results,
            search_source=search_source_label
        )

        LOGGER.info("웹 논문 검색 완료: 전체 %d건, 표시 %d건", len(all_results), len(displayed_results))

        # check_db_node에서 설정한 메시지 유지
        pre_message = state.get("response", "")
        
        # intent 설정
        state["intent"] = "search"

        # 벡터DB에 없었던 경우 PDF 다운로드 및 벡터DB 저장
        # 벡터DB 확인 결과가 없었고, 웹 검색을 실행한 경우에만 저장
        vector_db_results = state.get("vector_db_results")
        if (vector_db_results is None or len(vector_db_results) == 0) and all_results:
            # 각 논문별 처리 통계
            success_count = 0
            failure_count = 0
            
            # 검색 결과에서 PDF 다운로드 시도 (최대 3개)
            base_dir = Path(__file__).resolve().parent.parent
            pdf_dir = base_dir / "data" / "pdf"
            text_dir = base_dir / "data" / "text"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            text_dir.mkdir(parents=True, exist_ok=True)
            
            for idx, result in enumerate(all_results[:3]):  # 최대 3개만 처리
                pdf_link = result.get("pdf_link", "")
                title = result.get("title", "")
                identifier = result.get("identifier", "")  # DOI 또는 내부 ID
                
                LOGGER.info(f"[벡터DB 저장 시작] {idx+1}/3번째 논문 - 제목: {title[:50]}..., ID: {identifier}")
                
                # 필수 필드 검증
                if not pdf_link or pdf_link == "원문보기 링크 없음":
                    LOGGER.warning(f"[벡터DB 저장 건너뜀] {idx+1}번째 논문 - PDF 링크 없음: {title[:50]}...")
                    failure_count += 1
                    continue
                
                if not identifier:
                    LOGGER.warning(f"[벡터DB 저장 건너뜀] {idx+1}번째 논문 - 식별자(ID) 없음: {title[:50]}...")
                    failure_count += 1
                    continue
                
                # 중복 체크: 이미 벡터DB에 존재하는지 확인
                LOGGER.debug(f"[중복 체크 시작] {idx+1}번째 논문 - identifier: {identifier}")
                if check_paper_exists(identifier):
                    LOGGER.info(f"[벡터DB 저장 건너뜀] {idx+1}번째 논문 - 이미 벡터DB에 존재함 (중복 방지): {identifier}")
                    # 중복은 실패가 아니므로 카운트하지 않음
                    continue
                LOGGER.debug(f"[중복 체크 완료] {idx+1}번째 논문 - 중복 없음, 저장 진행: {identifier}")
                
                # 각 논문별 처리를 개별 try-except로 감싸서, 한 논문 실패가 다른 논문에 영향을 주지 않도록 함
                try:
                    # PDF 다운로드
                    LOGGER.debug(f"[벡터DB 저장] {idx+1}번째 논문 - PDF 다운로드 시도: {pdf_link}")
                    updated = download_pdfs_from_results([result], download_dir=pdf_dir)
                    
                    if not updated or len(updated) == 0:
                        LOGGER.warning(f"[벡터DB 저장 실패] {idx+1}번째 논문 - PDF 다운로드 결과 없음: {title[:50]}...")
                        failure_count += 1
                        continue
                    
                    if not updated[0].get("pdf_path"):
                        LOGGER.warning(f"[벡터DB 저장 실패] {idx+1}번째 논문 - PDF 경로 없음: {title[:50]}...")
                        failure_count += 1
                        continue
                    
                    pdf_path = updated[0].get("pdf_path")
                    LOGGER.info(f"[벡터DB 저장] {idx+1}번째 논문 - PDF 다운로드 성공: {pdf_path}")
                    
                    # all_results에 pdf_path 업데이트
                    all_results[idx] = updated[0]
                    
                    # PDF에서 텍스트 추출 (Path 객체로 변환)
                    pdf_path_obj = Path(pdf_path) if isinstance(pdf_path, str) else pdf_path
                    if not pdf_path_obj.exists():
                        LOGGER.warning(f"[벡터DB 저장 실패] {idx+1}번째 논문 - PDF 파일 존재하지 않음: {pdf_path}")
                        failure_count += 1
                        continue
                    
                    LOGGER.debug(f"[벡터DB 저장] {idx+1}번째 논문 - 텍스트 추출 시도: {pdf_path_obj.name}")
                    full_text = extract_text_from_pdf(pdf_path_obj)
                    
                    if not full_text:
                        LOGGER.warning(f"[벡터DB 저장 실패] {idx+1}번째 논문 - 텍스트 추출 실패 또는 빈 텍스트: {title[:50]}...")
                        failure_count += 1
                        continue
                    
                    # 텍스트 파일 저장
                    text_filename = f"{identifier}.txt"
                    text_path = text_dir / text_filename
                    
                    # 디렉토리 생성 (identifier에 슬래시가 포함된 경우)
                    text_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    try:
                        with open(text_path, "w", encoding="utf-8") as f:
                            f.write(full_text)
                        LOGGER.info(f"[벡터DB 저장] {idx+1}번째 논문 - 텍스트 파일 저장 성공: {text_path}, {len(full_text)}자")
                    except Exception as text_save_error:
                        LOGGER.error(f"[벡터DB 저장 실패] {idx+1}번째 논문 - 텍스트 파일 저장 오류: {text_save_error}", exc_info=True)
                        failure_count += 1
                        continue
                    
                    # 벡터DB에 저장
                    paper_keyword = f"논문_{keyword}" if keyword else None
                    # 컬렉션 이름용 키워드 (generate_search_keyword로 생성된 키워드)
                    collection_keyword = keyword  # 이미 generate_search_keyword로 생성된 키워드
                    LOGGER.debug(f"[벡터DB 저장] {idx+1}번째 논문 - 벡터DB 저장 시도 (키워드: {paper_keyword}, 컬렉션 키워드: {collection_keyword})")
                    
                    chunks_count = embed_and_store_paper(
                        identifier=identifier,
                        title=title,
                        text_path=text_path,
                        paper_keyword=paper_keyword,
                        collection_keyword=collection_keyword
                    )
                    
                    if chunks_count > 0:
                        LOGGER.info(f"[벡터DB 저장 완료] {idx+1}번째 논문 - ID: {identifier}, 제목: {title[:50]}..., 청크: {chunks_count}개")
                        success_count += 1
                    else:
                        LOGGER.warning(f"[벡터DB 저장 실패] {idx+1}번째 논문 - 벡터DB 저장 실패 (청크 0개): {title[:50]}...")
                        failure_count += 1
                        
                except Exception as exc:
                    # 각 논문별 예외는 로깅하고 다음 논문 계속 처리
                    LOGGER.error(f"[벡터DB 저장 실패] {idx+1}번째 논문 - 예외 발생: {exc}", exc_info=True)
                    failure_count += 1
                    # 예외가 발생해도 다음 논문 처리 계속
                    continue
            
            # 전체 처리 결과 요약 로깅
            total_attempted = success_count + failure_count
            if total_attempted > 0:
                LOGGER.info(f"[벡터DB 저장 요약] 전체 {total_attempted}개 논문 중 성공: {success_count}개, 실패: {failure_count}개")

        return {
            "query": query,
            "keyword": keyword,
            "results": all_results,  # 전체 결과 저장
            "summary": summary,
            "error": None,
            "displayed_count": len(displayed_results),
            "response": pre_message,  # check_db_node에서 설정한 메시지 유지
            "intent": "search",
        }
    except Exception as exc:
        LOGGER.exception("논문 검색 중 오류 발생")
        return {
            "error": str(exc),
            "results": [],
            "summary": f"검색 중 오류가 발생했습니다: {str(exc)}",
        }


def show_article_detail(state: ScienceONAgentState) -> ScienceONAgentState:
    """특정 논문의 상세 정보를 보여준다 (PDF 다운로드 및 요약)."""
    state["error"] = None
    
    results = state.get("results", [])
    if not results:
        return {
            "error": "먼저 논문을 검색한 뒤에 상세 정보를 요청해 주세요.",
            "response": "먼저 논문을 검색한 뒤에 상세 정보를 요청해 주세요.",
        }

    index = state.get("download_index", -1)
    
    # download_index가 None이거나 -1이면 last_selected_paper_index 확인
    if index == -1 or index is None:
        last_index = state.get("last_selected_paper_index", -1)
        if last_index >= 0:
            index = last_index
    
    if index < 0 or index >= len(results):
        available_indices = ", ".join([str(i + 1) for i in range(len(results))])
        requested_num = index + 1 if index >= 0 else "알 수 없는"
        error_msg = (
            f"죄송합니다. 요청하신 {requested_num}번째 논문을 찾을 수 없습니다.\n\n"
            f"현재 검색 결과는 {len(results)}개입니다. "
            f"사용 가능한 순번: {available_indices}번째."
        )
        LOGGER.warning(
            f"show_article_detail: download_index 범위 초과 - download_index={index}, "
            f"results_count={len(results)}, user_input={state.get('user_input', '')}"
        )
        return {
            "error": error_msg,
            "response": error_msg,
        }

    target_result = results[index]
    title = target_result.get("title", f"{index + 1}번째 논문")
    pdf_link = target_result.get("pdf_link", "")
    pdf_path = target_result.get("pdf_path")
    identifier = target_result.get("identifier", "")
    content = target_result.get("content", "")  # 벡터DB 결과에서 온 경우 content가 있을 수 있음
    user_input = state.get("user_input", "")
    search_source = state.get("search_source", "unknown")
    
    # 사내 서버 검색이고 구체적인 질문인 경우
    if search_source == "internal" and identifier and user_input:
        # 구체적인 질문인지 확인 (섹션, 주제, 내용 등에 대한 질문)
        specific_question_keywords = [
            "서론", "결론", "본론", "방법", "결과", "논의", "요약",
            "주제", "내용", "다루는", "제시한", "핵심", "요약",
            "앞으로", "향후", "과제", "기술", "연구", "어떤", "무엇"
        ]
        is_specific_question = any(kw in user_input for kw in specific_question_keywords)
        
        if is_specific_question:
            try:
                # 해당 논문의 관련 청크 검색
                paper_chunks = search_paper_chunks_by_identifier(
                    identifier=identifier,
                    query=user_input,
                    limit=10,
                    keyword=state.get("keyword")
                )
                
                if paper_chunks:
                    # 관련 청크들을 하나의 텍스트로 결합
                    context_text = "\n\n".join([
                        f"[청크 {idx+1}]\n{chunk.get('content', '')}"
                        for idx, chunk in enumerate(paper_chunks[:5])  # 상위 5개만 사용
                    ])
                    
                    summary_llm = ChatOpenAI(
                        model=OPENAI_MODEL,
                        temperature=0.3,
                        max_tokens=1500,
                    )
                    
                    summary_prompt = (
                        f"다음은 논문 '{title}'의 관련 내용입니다.\n\n"
                        f"사용자 질문: {user_input}\n\n"
                        f"논문 내용:\n{context_text}\n\n"
                        f"위 내용을 바탕으로 사용자 질문에 대해 정확하고 상세하게 답변해주세요. "
                        f"답변은 다음 형식을 따라주세요:\n"
                        f"- 논문의 제목과 관련 섹션을 명시\n"
                        f"- 사용자 질문에 대한 핵심 내용을 구조화하여 설명\n"
                        f"- 가능한 경우 구체적인 내용을 인용하여 설명\n"
                        f"- 한국어로 친절하고 전문적으로 작성\n\n"
                        f"답변 시작 부분에 '사용자 질문에 대한 답변입니다.' 같은 서론 문구 없이 바로 본문 내용으로 시작해주세요."
                    )
                    
                    summary_response = summary_llm.invoke(summary_prompt)
                    summary_text = summary_response.content if hasattr(summary_response, 'content') else str(summary_response)
                    
                    response_msg = (
                        f"📄 {index + 1}번째 논문: {title}\n\n"
                        f"📝 답변:\n{summary_text}"
                    )
                    
                    LOGGER.info("논문 상세 정보 생성 완료 (Golden_Summary 스타일): %s번째 논문", index + 1)
                    
                    return {
                        "results": results,
                        "response": response_msg,
                        "summary": summary_text,
                        "error": None,
                    }
            except Exception as exc:
                LOGGER.exception("Golden_Summary 스타일 답변 생성 중 오류 발생")
                # 오류 발생 시 기존 로직으로 폴백
    
    # 벡터DB 결과에 content가 있으면 PDF 다운로드 없이 바로 사용
    if content and content.strip():
        try:
            # content를 사용하여 요약 생성
            full_text = content
            # PDF 텍스트를 청크로 나누어 요약 (너무 길면 앞부분만 사용)
            if len(full_text) > 10000:
                full_text = full_text[:10000] + "..."

            # LLM으로 요약 생성
            summary_llm = ChatOpenAI(
                model=OPENAI_MODEL,
                temperature=0.3,
                max_tokens=1000,
            )

            summary_prompt = (
                f"다음은 논문 '{title}'의 원문 내용입니다. "
                "이 논문의 주요 내용을 한국어로 요약해 주세요.\n\n"
                "요약에는 다음을 포함해 주세요:\n"
                "- 논문의 주제 및 목적\n"
                "- 주요 연구 내용 및 방법\n"
                "- 주요 결과 및 결론\n\n"
                f"논문 원문:\n{full_text}\n\n"
                "답변 시작 부분에 '사용자 질문에 대한 답변입니다.' 같은 서론 문구 없이 바로 본문 내용으로 시작해주세요. "
                "논문 제목을 먼저 명시한 후, 내용을 설명해주세요."
            )

            summary_response = summary_llm.invoke(summary_prompt)
            summary_text = summary_response.content if hasattr(summary_response, 'content') else str(summary_response)

            response_msg = (
                f"📄 {index + 1}번째 논문: {title}\n\n"
                f"📝 논문 요약:\n{summary_text}"
            )
            if pdf_path:
                response_msg += f"\n\n💾 PDF 파일: {pdf_path}"

            LOGGER.info("논문 상세 정보 생성 완료 (벡터DB content 사용): %s번째 논문", index + 1)

            return {
                "results": results,
                "response": response_msg,
                "summary": summary_text,
                "error": None,
            }
        except Exception as exc:
            LOGGER.exception("논문 상세 정보 생성 중 오류 발생 (벡터DB content 사용)")
            # content 사용 실패 시 PDF 다운로드로 폴백
    
    # PDF 경로 확인 및 정규화
    base_dir = Path(__file__).resolve().parent.parent
    pdf_dir = base_dir / "data" / "pdf"
    pdf_path_obj = None
    
    if pdf_path:
        # 문자열이면 Path로 변환
        if isinstance(pdf_path, str):
            pdf_path_obj = Path(pdf_path)
        else:
            pdf_path_obj = pdf_path
        
        # 경로가 존재하는지 확인
        if pdf_path_obj.exists():
            pdf_path = str(pdf_path_obj)
        else:
            # 경로가 없으면 파일명만 추출해서 다시 찾기
            filename = pdf_path_obj.name
            alternative_path = pdf_dir / filename
            if alternative_path.exists():
                pdf_path = str(alternative_path)
                pdf_path_obj = alternative_path
            else:
                pdf_path = None
                pdf_path_obj = None
    
    # pdf_path가 없으면 title 기반으로 이미 다운로드된 PDF 찾기
    if not pdf_path_obj or not pdf_path_obj.exists():
        pdf_dir.mkdir(parents=True, exist_ok=True)
        # title을 기반으로 파일명 생성 (crawler의 _sanitize_filename 로직과 동일)
        from .crawler import _sanitize_filename
        sanitized_title = _sanitize_filename(title, f"paper_{index:02d}")
        potential_path = pdf_dir / sanitized_title
        if potential_path.exists():
            pdf_path = str(potential_path)
            pdf_path_obj = potential_path

    # PDF가 이미 다운로드되어 있지 않으면 다운로드 시도
    if not pdf_path_obj or not pdf_path_obj.exists():
        if not pdf_link or pdf_link == "원문보기 링크 없음":
            error_msg = f"{index + 1}번째 논문('{title}')의 원문 링크가 없어 상세 정보를 제공할 수 없습니다."
            return {
                "error": error_msg,
                "response": error_msg,
            }

        try:
            LOGGER.info("PDF 다운로드 시작 (상세 정보용): %s번째 논문 - %s", index + 1, title)
            # PDF 경로 통일 (data/pdf 사용)
            base_dir = Path(__file__).resolve().parent.parent
            pdf_dir = base_dir / "data" / "pdf"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            updated = download_pdfs_from_results([target_result], download_dir=str(pdf_dir))
            if updated and len(updated) > 0:
                results[index] = updated[0]
                pdf_path = updated[0].get("pdf_path")
                if pdf_path:
                    pdf_path_obj = Path(pdf_path) if isinstance(pdf_path, str) else pdf_path
                if not pdf_path or not pdf_path_obj.exists():
                    error_msg = f"{index + 1}번째 논문('{title}')의 PDF 다운로드에 실패했습니다."
                    return {
                        "error": error_msg,
                        "response": error_msg,
                    }
        except Exception as exc:
            LOGGER.exception("PDF 다운로드 중 오류 발생")
            error_msg = f"PDF 다운로드 중 오류가 발생했습니다: {str(exc)}"
            return {
                "error": error_msg,
                "response": error_msg,
            }

    # PDF 원문 요약
    try:
        import warnings

        # pypdf의 인코딩 경고 메시지 필터링
        warnings.filterwarnings("ignore", message=".*Advanced encoding.*not implemented yet.*")

        # PDF에서 텍스트 추출 (Path 객체로 전달)
        # pdf_path_obj가 None이면 pdf_path 문자열로 다시 시도
        if not pdf_path_obj:
            if pdf_path:
                pdf_path_obj = Path(pdf_path)
            else:
                error_msg = f"{index + 1}번째 논문('{title}')의 PDF 경로를 찾을 수 없습니다."
                return {
                    "error": error_msg,
                    "response": error_msg,
                }
        
        full_text = extract_text_from_pdf(pdf_path_obj)
        if not full_text:
            error_msg = f"{index + 1}번째 논문('{title}')의 PDF에서 텍스트를 추출할 수 없습니다."
            return {
                "error": error_msg,
                "response": error_msg,
            }

        # PDF 텍스트를 청크로 나누어 요약 (너무 길면 앞부분만 사용)
        if len(full_text) > 10000:
            full_text = full_text[:10000] + "..."

        # LLM으로 요약 생성
        summary_llm = ChatOpenAI(
            model=OPENAI_MODEL,
            temperature=0.3,
            max_tokens=1000,
        )

        summary_prompt = (
            f"다음은 논문 '{title}'의 원문 내용입니다. "
            "이 논문의 주요 내용을 한국어로 요약해 주세요.\n\n"
            "요약에는 다음을 포함해 주세요:\n"
            "- 논문의 주제 및 목적\n"
            "- 주요 연구 내용 및 방법\n"
            "- 주요 결과 및 결론\n\n"
            f"논문 원문:\n{full_text}\n\n"
            "답변 시작 부분에 '사용자 질문에 대한 답변입니다.' 같은 서론 문구 없이 바로 본문 내용으로 시작해주세요. "
            "논문 제목을 먼저 명시한 후, 내용을 설명해주세요."
        )

        summary_response = summary_llm.invoke(summary_prompt)
        summary_text = summary_response.content if hasattr(summary_response, 'content') else str(summary_response)

        response_msg = (
            f"📄 {index + 1}번째 논문: {title}\n\n"
            f"📝 논문 요약:\n{summary_text}\n\n"
            f"💾 PDF 파일: {pdf_path}"
        )

        LOGGER.info("논문 상세 정보 생성 완료: %s번째 논문", index + 1)

        return {
            "results": results,
            "response": response_msg,
            "summary": summary_text,
            "error": None,
        }
    except Exception as exc:
        LOGGER.exception("논문 상세 정보 생성 중 오류 발생")
        error_msg = f"논문 상세 정보 생성 중 오류가 발생했습니다: {str(exc)}"
        return {
            "error": error_msg,
            "response": error_msg,
        }


def generate_response(state: ScienceONAgentState) -> ScienceONAgentState:
    """사용자에게 응답 메시지를 생성한다."""
    intent = state.get("intent", "unknown")
    error = state.get("error")
    
    # 디버그: generate_response 노드 진입 시 state 상태 로깅
    LOGGER.debug(
        f"[라우팅 디버그] generate_response 노드 진입 - "
        f"intent={intent}, next_action={state.get('next_action')}, "
        f"search_source={state.get('search_source')}, "
        f"error={error is not None}, results_count={len(state.get('results', []))}, "
        f"user_input={state.get('user_input', '')[:50]}"
    )
    
    # check_db_node에서 설정한 메시지가 있으면 먼저 확인
    pre_message = state.get("response", "")

    if error:
        response = error
    elif intent == "search":
        summary = state.get("summary", "")
        displayed_count = state.get("displayed_count", 0)
        total_results = len(state.get("results", []))
        
        # pre_message가 있으면 먼저 표시하고 summary 추가
        if pre_message:
            response = f"{pre_message}\n\n{summary}" if summary else pre_message
        else:
            response = f"검색이 완료되었습니다. 전체 {total_results}건 중 {displayed_count}건을 표시했습니다."
            if summary:
                response += f"\n\n{summary}"
    elif intent == "continue_search":
        summary = state.get("summary", "")
        displayed_count = state.get("displayed_count", 0)
        total_results = len(state.get("results", []))
        response = f"추가 검색 결과를 표시했습니다. (전체 {total_results}건 중 {displayed_count}건 확인)"
        if summary:
            response += f"\n\n{summary}"
    elif intent == "detail":
        response = state.get("response", "논문 상세 정보를 제공합니다.")
    elif intent == "ask_source":
        response = state.get("response", "어디서 검색하시겠습니까?")
    else:
        # RAG 답변 등 기타 intent의 경우 response만 사용 (summary 무시)
        response = state.get("response", "요청을 처리했습니다.")

    # 응답을 메시지에 추가
    messages = state.get("messages", [])
    messages.append(AIMessage(content=response))

    return {
        "response": response,
        "messages": messages,
    }


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
    # workflow.add_node("ask_search_source", ask_search_source)  # 사용되지 않음
    workflow.add_node("search_papers", search_papers)
    # workflow.add_node("download_pdf", download_pdf)  # PDF는 검색 시 자동 다운로드되므로 제거
    workflow.add_node("show_article_detail", show_article_detail)
    workflow.add_node("generate_response", generate_response)

    # 엣지 설정
    workflow.set_entry_point("analyze_input")
    
    # analyze_input -> check_db, search_papers, show_article_detail, ask_search_source_with_db, generate_response
    def route_after_analyze(state: ScienceONAgentState) -> Literal["check_db", "search_papers", "show_article_detail", "ask_search_source_with_db", "rag_answer", "generate_response"]:
        next_action = state.get("next_action", "check_db")
        intent = state.get("intent", "unknown")
        LOGGER.debug(
            f"[라우팅 디버그] route_after_analyze - "
            f"next_action={next_action}, intent={intent}, "
            f"search_source={state.get('search_source')}, "
            f"results_count={len(state.get('results', []))}, "
            f"user_input={state.get('user_input', '')[:50]}"
        )
        if next_action == "show_article_detail":
            LOGGER.debug(f"[라우팅 디버그] 라우팅 결정: show_article_detail")
            return "show_article_detail"
        elif next_action == "rag_answer":
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
            "show_article_detail": "show_article_detail",
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
    
    # ask_search_source 관련 엣지 제거됨 (사용되지 않음)
    
    workflow.add_edge("search_papers", "generate_response")
    # workflow.add_edge("download_pdf", "generate_response")  # download_pdf 노드 제거됨
    workflow.add_edge("show_article_detail", "generate_response")
    workflow.add_edge("generate_response", END)

    return workflow.compile()


# 전역 에이전트 그래프 인스턴스
SCIENCEON_AGENT_GRAPH = build_scienceon_agent_graph()

# 하위 호환성을 위한 별칭
AGENT_GRAPH = SCIENCEON_AGENT_GRAPH


def run_scienceon_agent(
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
            - results: 검색 결과 목록 (각 항목에 title, pdf_link, detail_link, pdf_path 포함)
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
            "download_index": -1,
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
            "download_index": -1,
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
        "download_index": -1,
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
        final_state = SCIENCEON_AGENT_GRAPH.invoke(
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
            "download_index": -1,
            "keyword": "",
            "results": initial_state.get("results", []),  # 이전 검색 결과 유지
            "summary": "",
            "response": f"논문 검색 중 오류가 발생했습니다: {str(exc)}",
            "error": str(exc),
            "search_source": initial_state.get("search_source", "unknown"),
            "displayed_count": initial_state.get("displayed_count", 0),
            "vector_db_results": initial_state.get("vector_db_results", []),
            "next_action": "generate_response",
            "ask_source_retry_count": initial_state.get("ask_source_retry_count", 0),
            "previous_query": initial_state.get("previous_query", ""),
            "last_selected_paper_index": initial_state.get("last_selected_paper_index", -1),
        }
        return error_state


# 하위 호환성을 위한 별칭
def run_agent(user_input: str, state: Optional[ScienceONAgentState] = None) -> ScienceONAgentState:
    """하위 호환성을 위한 별칭. run_scienceon_agent를 사용하세요."""
    return run_scienceon_agent(user_input, state)


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
            "download_index": master_state.get("download_index", -1),
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
    def scienceon_node(master_state: Dict[str, Any]) -> Dict[str, Any]:
        """ScienceON 서브 에이전트를 실행하는 노드 함수."""
        # 상태 추출
        scienceon_state = extract_scienceon_state(master_state)
        user_input = master_state.get("user_input", master_state.get("message", ""))

        # 에이전트 실행
        result_state = run_scienceon_agent(user_input, scienceon_state)

        # 결과 병합
        return merge_scienceon_state(master_state, result_state)

    return scienceon_node


# ============================================================================
# 내보내기 (통합 마스터 에이전트에서 사용)
# ============================================================================

__all__ = [
    # 상태 타입
    "ScienceONAgentState",
    "AgentState",  # 하위 호환성

    # 그래프 생성
    "build_scienceon_agent_graph",
    "create_scienceon_subgraph",

    # 실행 함수
    "run_scienceon_agent",
    "run_agent",  # 하위 호환성

    # 통합 헬퍼
    "extract_scienceon_state",
    "merge_scienceon_state",
    "create_scienceon_node",

    # 노드 함수들 (직접 사용 가능)
    "search_papers",
    # "download_pdf",  # PDF는 검색 시 자동 다운로드되므로 제거
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
                session_state = run_scienceon_agent(user_input, session_state)

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
_AGENT_AVAILABLE = True

# 세션 별 LangGraph 상태 메모리
_ARTICLE_MEMORY: Dict[str, ScienceONAgentState] = {}


def is_waiting_for_article_search_source(session_id: Optional[str]) -> bool:
    """논문 검색에서 웹/사내 서버 선택 질문 대기 중인지 확인."""
    if not session_id:
        return False
    st = _ARTICLE_MEMORY.get(session_id)
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
        pdf_path = row.get("pdf_path")
        if title:
            items.append({
                "title": title,
                "pdf_link": pdf_link or None,
                "detail_link": detail_link or None,
                "pdf_path": pdf_path or None,
            })
    return items

def has_pending_article_results(session_id: Optional[str]) -> bool:
    """세션에 이전 논문 검색 결과가 저장되어 있는지 확인한다."""
    if not session_id:
        return False
    st = _ARTICLE_MEMORY.get(session_id)
    return bool(st and isinstance(st, dict) and st.get("results") and len(st.get("results")) > 0)

def article_master(query: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """LangGraph 기반 논문 검색/다운로드 마스터 엔트리.

    - 세션 상태를 복원/저장한다.
    - 숫자만 입력되었고, 이전 결과가 있으면 다운로드 명령으로 해석한다.
    - 에이전트 사용 불가 시 폴백으로 검색만 수행한다.
    """
    t = (query or "").strip()
    # 숫자만 입력 + 이전 결과가 있을 때: 다운로드로 변환
    if _AGENT_AVAILABLE and t.isdigit() and has_pending_article_results(session_id):
        t = f"{t}번째 논문 pdf 다운받아줘"

    if _AGENT_AVAILABLE:
        try:
            # 세션 상태 복원
            st: Optional[ScienceONAgentState] = _ARTICLE_MEMORY.get(session_id) if session_id else None
            st = run_scienceon_agent(user_input=t, state=st)
            # 세션 상태 저장
            if session_id:
                _ARTICLE_MEMORY[session_id] = st  # type: ignore[assignment]
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
            fb = article_search(query)
            fb["message"] = f"{fb.get('message', '')} (agent 연동 실패로 폴백: {exc})"
            return fb

    # 폴백만 가능한 환경: 검색만
    return article_search(query)

def article_search(keyword: str) -> Dict:
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
                - pdf_path (Optional[str]): 로컬 저장 경로(가능 시)
    """
    # 1) 에이전트 경로 (정확/풍부한 결과)
    if _AGENT_AVAILABLE:
        try:
            state: Optional[ScienceONAgentState] = None
            result_state = run_scienceon_agent(user_input=keyword, state=state)
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
        except Exception:
            # 에이전트 실패 시 폴백으로 이어감
            pass

    # 2) 폴백: 간단 HTML 스크래핑(기존 동작 유지)
    url = _search_url(keyword)
    items: List[Dict] = []
    try:
        resp = requests.get(url, timeout=15)
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
                    "pdf_path": None,
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

    def search(self, query: str) -> Dict[str, Any]:
        """검색 수행. 내부 상태가 최신 결과로 갱신된다."""
        if _AGENT_AVAILABLE:
            try:
                # 세션 메모리와 동기화
                if self._session_id and self._session_id in _ARTICLE_MEMORY and self._state is None:
                    self._state = _ARTICLE_MEMORY[self._session_id]
                self._state = run_scienceon_agent(user_input=query, state=self._state)
                if self._session_id:
                    _ARTICLE_MEMORY[self._session_id] = self._state  # type: ignore[assignment]
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
                fb = article_search(query)
                fb["message"] = f"{fb.get('message', '')} (agent 연동 실패로 폴백: {exc})"
                return fb
        # 폴백만 가능한 환경
        return article_search(query)

    def download(self, ordinal: int) -> Dict[str, Any]:
        """검색 결과의 N번째(1-base) 논문 PDF 다운로드 시도."""
        if not isinstance(ordinal, int) or ordinal <= 0:
            return {"ok": False, "message": "순번은 1 이상의 정수여야 합니다."}
        if not _AGENT_AVAILABLE:
            return {"ok": False, "message": "다운로드는 에이전트 연동이 필요합니다."}
        try:
            # 한국어 지시로 agent의 다운로드 경로를 트리거
            command = f"{ordinal}번째 논문 pdf 다운받아줘"
            if self._session_id and self._session_id in _ARTICLE_MEMORY and self._state is None:
                self._state = _ARTICLE_MEMORY[self._session_id]
            self._state = run_scienceon_agent(user_input=command, state=self._state)
            if self._session_id:
                _ARTICLE_MEMORY[self._session_id] = self._state  # type: ignore[assignment]
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








