"""Milvus 벡터DB 관리 및 임베딩 검색 모듈."""

import re
import hashlib
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

# 로거 설정
LOGGER = logging.getLogger(__name__)

try:
    from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType
    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False
    Collection = None
    utility = None

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    TEXT_SPLITTER_AVAILABLE = True
except ImportError:
    TEXT_SPLITTER_AVAILABLE = False
    RecursiveCharacterTextSplitter = None

from agents.embedding_utils import get_embedding_generator
from .patent_config import _get_env, PATENT_DATA_DIR, OPENAI_MODEL
from .patent_keyword import generate_patent_keyword_from_text
from .patent_pdf_utils import get_full_text_from_file, get_pdf_link_from_metadata


# ───────────────────────────────────────────────────────────
# 임베딩 모델 및 텍스트 스플리터 (지연 로딩)
# ───────────────────────────────────────────────────────────
_embedding_model = None  # OpenAIEmbeddingGenerator 인스턴스
_splitter: Optional[RecursiveCharacterTextSplitter] = None


def _initialize_models() -> None:
    """임베딩 모델 및 텍스트 스플리터 초기화."""
    global _embedding_model, _splitter
    
    if not TEXT_SPLITTER_AVAILABLE:
        raise ImportError("langchain_text_splitters가 설치되지 않았습니다. pip install langchain-text-splitters")
    
    if _embedding_model is None:
        try:
            _embedding_model = get_embedding_generator()
        except Exception as e:
            raise RuntimeError(f"임베딩 모델 로딩 실패: {e}")
    
    if _splitter is None:
        try:
            _splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        except Exception as e:
            raise RuntimeError(f"텍스트 스플리터 초기화 실패: {e}")


def _initialize_milvus() -> None:
    """Milvus 연결 초기화."""
    if not MILVUS_AVAILABLE:
        raise ImportError("pymilvus가 설치되지 않았습니다. pip install pymilvus")

    # 공용 서버 주소 (.env 파일에서 환경 변수 읽기)
    milvus_host = _get_env("HOST") or _get_env("MILVUS_HOST")
    milvus_port_str = _get_env("MILVUS_PORT")
    milvus_port = int(milvus_port_str.strip()) if milvus_port_str else ""
    
    try:
        connections.connect("default", host=milvus_host, port=milvus_port)
    except Exception as e:
        raise RuntimeError(f"Milvus 연결 실패 (호스트: {milvus_host}:{milvus_port}): {e}")


def extract_keyword_from_text(text: str) -> str:
    """특허 제목/초록에서 주요 키워드 추출 (LLM 기반).
    
    특허 전용 키워드 생성 모듈을 사용하여 동적으로 키워드를 생성합니다.
    """
    try:
        keyword = generate_patent_keyword_from_text(text)
        return keyword if keyword else "other"
    except Exception as e:
        return "other"


def _translate_keyword_to_english_with_llm(keyword: str) -> Optional[str]:
    """LLM을 사용하여 한글 키워드를 영문명으로 변환.
    
    Args:
        keyword: 한글 키워드
        
    Returns:
        영문명 (예: "smartphone_camera", "lithium_ion_battery") 또는 None
    """
    try:
        from langchain_openai import ChatOpenAI
        
        api_key = _get_env("OPENAI_API_KEY")
        if not api_key:
            return None
        
        prompt = f"""다음 한글 기술 키워드를 영문명으로 변환해주세요.
- 출력 형식: 영문자, 숫자, 언더스코어만 사용 (예: smartphone_camera, lithium_ion_battery)
- 간결하고 명확한 기술 용어로 변환하세요.
- 복합 키워드는 언더스코어로 연결하세요.
- 예시: "스마트폰 카메라" → "smartphone_camera"
- 예시: "리튬이온배터리" → "lithium_ion_battery"
- 예시: "전기차 배터리" → "electric_vehicle_battery" 또는 "battery"

입력: {keyword}
출력:"""
        
        llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0, api_key=api_key)
        response = llm.invoke(prompt)
        english_name = response.content.strip()
        
        # 영문자, 숫자, 언더스코어만 추출
        english_name = re.sub(r'[^a-zA-Z0-9_]', '_', english_name)
        english_name = re.sub(r'_+', '_', english_name).strip('_').lower()
        
        # 유효성 검사
        if english_name and len(english_name) >= 2 and len(english_name) <= 240:
            return english_name
        return None
    except Exception:
        return None


# 캐시: (원본 키워드) -> (영문 변환 결과)
_keyword_translation_cache: Dict[str, str] = {}
_CACHE_MAX_SIZE = 1000

def _sanitize_collection_name(keyword: str) -> str:
    """키워드를 Milvus 컬렉션 이름으로 변환 (한글을 영문으로 변환).

    Milvus 컬렉션 이름은 영문자, 숫자, 언더스코어만 허용합니다.
    한글 키워드는 영문으로 변환하거나 해시 기반으로 변환합니다.
    캐싱을 통해 동일한 키워드는 항상 동일한 컬렉션 이름을 반환합니다.
    """
    original_keyword = keyword.strip()
    
    # 캐시 확인
    if original_keyword in _keyword_translation_cache:
        return _keyword_translation_cache[original_keyword]
    
    # 1. 키워드 맵에서 직접 매핑 확인 (확장된 맵)
    keyword_map = {
        "디스플레이": "display",
        "배터리": "battery",
        "베터리": "battery",  # 오타 대응
        "와이파이": "wifi",
        "볼펜": "pen",
        "반도체": "semiconductor",
        "센서": "sensor",
        "로봇": "robot",
        "인공지능": "ai",
        "자동차": "vehicle",
        "차량": "vehicle",
        "스마트폰": "smartphone",
        "기타": "other",
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
        "바이러스": "virus",
        "전기차": "electric_vehicle",
        "전기": "electric",
        "차": "car",
    }
    
    # 키워드 맵에서 직접 매핑 확인
    normalized_keyword = original_keyword

    # 정확히 일치하는 키워드가 있으면 반환
    if normalized_keyword in keyword_map:
        result = keyword_map[normalized_keyword]
        # 캐시에 저장
        if len(_keyword_translation_cache) >= _CACHE_MAX_SIZE:
            oldest_key = next(iter(_keyword_translation_cache))
            del _keyword_translation_cache[oldest_key]
        _keyword_translation_cache[original_keyword] = result
        return result

    # 2. 키워드 맵에 없으면 LLM으로 영문명 변환 시도 (하이브리드 방식)
    has_korean = any('가' <= char <= '힣' for char in normalized_keyword)
    
    if has_korean:
        # LLM으로 영문명 변환 시도
        llm_translated = _translate_keyword_to_english_with_llm(normalized_keyword)
        if llm_translated:
            result = llm_translated
            # 캐시에 저장
            if len(_keyword_translation_cache) >= _CACHE_MAX_SIZE:
                oldest_key = next(iter(_keyword_translation_cache))
                del _keyword_translation_cache[oldest_key]
            _keyword_translation_cache[original_keyword] = result
            return result
        
        # LLM 실패 시 최소한의 fallback: 원본 키워드에서 영문자 추출
        english_only = re.sub(r'[^a-zA-Z0-9_]', '_', normalized_keyword)
        english_only = re.sub(r'_+', '_', english_only).strip('_')
        
        if english_only and len(english_only) >= 2:
            # 영문자 부분이 있으면 사용 (길이 제한)
            if len(english_only) > 240:
                english_only = english_only[:240]
            result = english_only.lower()
            # 캐시에 저장
            if len(_keyword_translation_cache) >= _CACHE_MAX_SIZE:
                oldest_key = next(iter(_keyword_translation_cache))
                del _keyword_translation_cache[oldest_key]
            _keyword_translation_cache[original_keyword] = result
            return result
        
        hash_value = hashlib.md5(normalized_keyword.encode('utf-8')).hexdigest()[:12]
        result = f"kw_{hash_value}"
        # 캐시에 저장
        if len(_keyword_translation_cache) >= _CACHE_MAX_SIZE:
            oldest_key = next(iter(_keyword_translation_cache))
            del _keyword_translation_cache[oldest_key]
        _keyword_translation_cache[original_keyword] = result
        return result
    
    # 3. 영문자만 있는 경우 그대로 사용 (길이 제한)
    english_only = re.sub(r'[^a-zA-Z0-9_]', '_', normalized_keyword)
    english_only = re.sub(r'_+', '_', english_only).strip('_')
    
    if english_only:
        if english_only.isdigit():
            result = "other"
        else:
            if len(english_only) > 240:
                english_only = english_only[:240]
            result = english_only.lower()
        if len(_keyword_translation_cache) >= _CACHE_MAX_SIZE:
            oldest_key = next(iter(_keyword_translation_cache))
            del _keyword_translation_cache[oldest_key]
        _keyword_translation_cache[original_keyword] = result
        return result
    
    # 4. 영문자도 없으면 해시값 사용 (거의 발생하지 않음)
    hash_value = hashlib.md5(normalized_keyword.encode('utf-8')).hexdigest()[:12]
    result = f"kw_{hash_value}"
    # 캐시에 저장
    if len(_keyword_translation_cache) >= _CACHE_MAX_SIZE:
        oldest_key = next(iter(_keyword_translation_cache))
        del _keyword_translation_cache[oldest_key]
    _keyword_translation_cache[original_keyword] = result
    return result


def get_collection_name(keyword: str) -> str:
    """키워드로 컬렉션 이름 생성 (Milvus 호환).

    Args:
        keyword: 검색 키워드 (이미 정규화된 영문 키워드)

    Returns:
        Milvus 컬렉션 이름 (영문자, 숫자, 언더스코어만 포함)
    """
    # 키워드가 이미 정규화되어 있다고 가정
    # 하지만 혹시 모를 경우를 대비해 한 번 더 정규화
    normalized_suffix = _sanitize_collection_name(keyword.strip())
    collection_name = f"patents_embeddings_{normalized_suffix}"

    # 최종 검증: 컬렉션 이름이 Milvus 규칙을 준수하는지 확인
    # 영문자, 숫자, 언더스코어만 허용
    if not re.match(r'^[a-zA-Z0-9_]+$', collection_name):
        # 한글이나 특수문자가 포함된 경우 강제로 해시값 사용
        hash_value = hashlib.md5(keyword.encode('utf-8')).hexdigest()[:12]
        collection_name = f"patents_embeddings_kw_{hash_value}"

    # 길이 제한 (Milvus 컬렉션 이름은 최대 255자)
    if len(collection_name) > 255:
        hash_value = hashlib.md5(keyword.encode('utf-8')).hexdigest()[:12]
        collection_name = f"patents_embeddings_kw_{hash_value}"

    return collection_name


def get_or_create_collection(collection_name: str) -> Collection:
    """컬렉션 가져오기 또는 생성 (임베딩 모델 초기화 확인)."""
    # 임베딩 모델 초기화 확인
    if _embedding_model is None:
        _initialize_models()
    
    # Milvus 연결 초기화
    _initialize_milvus()
    
    # 기존 컬렉션이 있는지 확인
    if collection_name in utility.list_collections():
        # 기존 컬렉션 사용
        collection = Collection(collection_name)
        collection.load()

        # 기존 컬렉션의 스키마 확인
        schema = collection.schema
        field_names = [field.name for field in schema.fields]

        # patent_keyword 필드가 없으면 경고 (하지만 계속 진행)
        if "patent_keyword" not in field_names:
            print(f"⚠️ 기존 컬렉션 '{collection_name}'에 'patent_keyword' 필드가 없습니다. 필터링 기능이 제한될 수 있습니다.")

        return collection

    # 새 컬렉션 생성
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="app_no", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="abstract", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="patent_keyword", dtype=DataType.VARCHAR, max_length=128),  # 특허_키워드 형식
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=_embedding_model.dimension),
    ]
    schema = CollectionSchema(fields, description=f"KIPRIS 특허 임베딩 데이터 - {collection_name}")
    
    if collection_name not in utility.list_collections():
        collection = Collection(name=collection_name, schema=schema)
        collection.create_index(
            field_name="embedding",
            index_params={
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 1024}
            }
        )

    collection = Collection(collection_name)
    collection.load()
    return collection


def check_vector_db(query: str, limit: int = 5, max_distance: float = 0.8, filter_keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """벡터DB에서 유사 특허 검색.
    
    컬렉션 이름으로만 필터링합니다 (논문 agent 방식).
    patent_keyword 필드로 필터링하지 않습니다.

    Args:
        query: 검색 쿼리 (임베딩용)
        limit: 반환할 최대 결과 수
        max_distance: 최대 거리 임계값
        filter_keyword: 컬렉션 선택용 키워드 (None이면 query에서 추출)
            - "특허_" 접두사가 있으면 제거 후 사용
            - None이면 query에서 자동 추출
    """
    try:
        _initialize_models()
        _initialize_milvus()
        
        if filter_keyword:
            keyword = filter_keyword.strip()
            LOGGER.info("check_vector_db: filter_keyword에서 키워드 사용: '%s' (정규화된 키워드, 저장 시와 동일)", keyword)
            from .patent_utils import _remove_search_mode_keywords
            embedding_query = _remove_search_mode_keywords(query)
        else:
            # filter_keyword가 없으면 query에서 추출
            from .patent_keyword import generate_patent_search_phrase
            search_phrase = generate_patent_search_phrase(query)
            keyword = extract_keyword_from_text(search_phrase)
            LOGGER.info("check_vector_db: query에서 키워드 추출: '%s' (query='%s')", keyword, query)
            # 키워드가 없으면 search_phrase 사용
            embedding_query = search_phrase
        
        collection_name = get_collection_name(keyword)
        LOGGER.info("check_vector_db: 컬렉션 이름: '%s' (keyword='%s', 저장 시와 동일한 정규화된 키워드)", collection_name, keyword)
        
        try:
            target_collection = Collection(collection_name)
            target_collection.load()
            
            # 컬렉션에 데이터가 있는지 확인
            if not utility.has_collection(collection_name):
                return []
            
            # 컬렉션의 엔티티 수 확인
            num_entities = target_collection.num_entities
            if num_entities == 0:
                return []
        except Exception:
            return []
        
        # 구문 벡터화
        query_vec = _embedding_model.embed(embedding_query)
        
        # 컬렉션 스키마 확인
        schema = target_collection.schema
        field_names = [field.name for field in schema.fields]
        has_patent_keyword = "patent_keyword" in field_names
        
        # output_fields 동적 구성
        output_fields = ["app_no", "title", "abstract", "content"]
        if has_patent_keyword:
            output_fields.append("patent_keyword")
        
        # 구문 기반 매칭용 search_phrase 생성
        from .patent_keyword import generate_patent_search_phrase
        search_phrase = generate_patent_search_phrase(embedding_query)
        phrase_lower = search_phrase.lower()
        phrase_words = re.findall(r'[\w가-힣]+', phrase_lower)
        
        # 핵심 단어 추출
        stop_words = {
            "특허", "찾아", "검색", "관련", "정보", "자료", 
            "요청", "알려", "줘", "주세요", "patent", "찾아서", "검색해", "검색해줘",
            "기술", "현황", "동향"
        }
        main_keywords = [
            word for word in phrase_words 
            if len(word) > 1 and word not in stop_words
        ]
        
        try:
            results = target_collection.search(
                data=[query_vec],
                anns_field="embedding",
                param={"metric_type": "L2", "params": {"nprobe": 10}},
                limit=limit * 5,
                expr=None,
                output_fields=output_fields
            )
        except Exception:
            return []
        
        if not results or len(results) == 0 or len(results[0]) == 0:
            return []
        
        found_patents = []
        seen_app_nos = set()
        
        for hit in results[0]:
            app_no = hit.entity.get('app_no')
            if not app_no or app_no in seen_app_nos:
                continue
            
            title = hit.entity.get('title', '').lower()
            abstract = hit.entity.get('abstract', '').lower()
            content = hit.entity.get('content', '').lower()
            full_text = title + " " + abstract + " " + content
            
            # 구문 전체 매칭 확인
            phrase_match = phrase_lower in full_text
            
            # 핵심 단어 매칭 확인
            if main_keywords:
                keyword_count = sum(
                    1 for kw in main_keywords
                    if kw in full_text
                )
                if not phrase_match and keyword_count < len(main_keywords):
                    continue
            
            # 기존 키워드 매칭 로직
            word_match = any(word in full_text for word in phrase_words if len(word) > 1)
            keyword_match = phrase_match or word_match
            
            # 키워드 매칭이 있으면 거리 임계값 완화
            effective_max_distance = 2.0 if keyword_match else max_distance
            
            if hit.distance > effective_max_distance:
                continue
            
            seen_app_nos.add(app_no)
            similarity = 1.0 / (1.0 + hit.distance)
            
            # 전체 텍스트 내용 가져오기
            full_content = get_full_text_from_file(app_no)
            if not full_content:
                full_content = hit.entity.get('content', '')
            
            # PDF 링크 가져오기
            pdf_link = get_pdf_link_from_metadata(app_no)

            found_patents.append({
                "application_number": app_no,
                "title": hit.entity.get('title'),
                "abstract": hit.entity.get('abstract'),
                "content": full_content,
                "score": hit.distance,
                "similarity": similarity,
                "pdf_link": pdf_link
            })
        
        found_patents.sort(key=lambda x: x["score"])
        return found_patents[:limit]
    except Exception as e:
        print(f"⚠️ 벡터DB 검색 오류: {e}")
        return []


def embed_and_store_patent(app_no: str, title: str, abstract: str, text_path: Path, patent_keyword: Optional[str] = None) -> int:
    """특허 텍스트를 임베딩하고 분야별 컬렉션에 저장.

    Args:
        app_no: 특허 출원번호
        title: 특허 제목
        abstract: 특허 초록
        text_path: 텍스트 파일 경로
        patent_keyword: 특허_키워드 형식의 키워드 (예: "특허_battery", None이면 자동 추출)

    Returns:
        저장된 청크 개수
    """
    try:
        _initialize_models()
        _initialize_milvus()
        
        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        
        # patent_keyword가 있으면 그것을 사용, 없으면 제목/초록에서 추출
        if patent_keyword:
            # "특허_" 접두사 제거하여 키워드 추출
            if patent_keyword.startswith("특허_"):
                keyword = patent_keyword[3:]  # "특허_" 제거
            else:
                keyword = patent_keyword
            LOGGER.info(f"[embed_and_store_patent] patent_keyword에서 키워드 추출: '{keyword}'")
        else:
            # patent_keyword가 없으면 제목/초록에서 추출
            combined_text = f"{title} {abstract}"
            keyword = extract_keyword_from_text(combined_text)
            patent_keyword = f"특허_{keyword}"
            LOGGER.info(f"[embed_and_store_patent] 제목/초록에서 키워드 추출: '{keyword}'")

        # 키워드는 이미 정규화되어 있으므로 바로 컬렉션 이름 생성
        collection_name = get_collection_name(keyword)
        LOGGER.info(f"[embed_and_store_patent] 최종 컬렉션 이름: '{collection_name}' (원본 키워드: '{keyword}')")
        
        target_collection = get_or_create_collection(collection_name)
        
        # 컬렉션 스키마 확인 (patent_keyword 필드 존재 여부)
        schema = target_collection.schema
        field_names = [field.name for field in schema.fields]
        has_patent_keyword = "patent_keyword" in field_names

        chunks = _splitter.split_text(text)  # type: ignore
        embeddings = _embedding_model.embed_batch(chunks)
        
        # embeddings가 numpy 배열이면 tolist() 호출, 이미 리스트면 그대로 사용
        if hasattr(embeddings, 'tolist'):
            embeddings_list = embeddings.tolist()
        else:
            embeddings_list = embeddings
        
        app_nos = [app_no] * len(chunks)
        titles = [title] * len(chunks)
        abstracts = [abstract] * len(chunks)
        
        # patent_keyword 필드가 있을 때만 포함
        if has_patent_keyword:
            patent_keywords = [patent_keyword] * len(chunks)  # 특허_키워드 필드
            data = [app_nos, titles, abstracts, chunks, patent_keywords, embeddings_list]
        else:
            # 기존 컬렉션 (patent_keyword 필드 없음)
            data = [app_nos, titles, abstracts, chunks, embeddings_list]

        target_collection.insert(data)
        target_collection.flush()
        
        return len(chunks)
    except Exception as e:
        print(f"⚠️ 임베딩 저장 오류: {e}")
        return 0

