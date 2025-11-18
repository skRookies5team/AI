# tech_patent_agent.py — LangGraph 기반 특허 검색 에이전트
from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import TypedDict, Annotated, Literal, Optional, Dict, List, Any
import requests
import xmltodict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
    from langgraph.graph import StateGraph, END
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

try:
    from pymilvus import connections, Collection, utility, FieldSchema, CollectionSchema, DataType
    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    TEXT_SPLITTER_AVAILABLE = True
except ImportError:
    TEXT_SPLITTER_AVAILABLE = False

try:
    from PyPDF2 import PdfReader
    PDF_SUPPORTED = True
except ImportError:
    PdfReader = None
    PDF_SUPPORTED = False

# ───────────────────────────────────────────────────────────
# .env 파일 로드
# ───────────────────────────────────────────────────────────
def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    try:
        env_path = os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass


_load_env_file()


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 안전 조회."""
    return os.environ.get(name, default)


# ───────────────────────────────────────────────────────────
# KIPRIS API 설정
# ───────────────────────────────────────────────────────────
KIPRIS_API_KEY = _get_env("KIPRIS_API_KEY")
OPENAI_API_KEY = _get_env("OPENAI_API_KEY")

# ───────────────────────────────────────────────────────────
# 전역 상태 관리 (멀티턴 대화 지원)
# ───────────────────────────────────────────────────────────
_PATENT_SEARCH_CACHE: Dict[str, List[Dict]] = {}  # 세션별 검색 결과 캐시
_PATENT_SESSION_ID: Optional[str] = None


def _set_session_id(session_id: Optional[str] = None) -> str:
    """세션 ID 설정."""
    global _PATENT_SESSION_ID
    if session_id:
        _PATENT_SESSION_ID = session_id
    elif not _PATENT_SESSION_ID:
        import uuid
        _PATENT_SESSION_ID = str(uuid.uuid4())
    return _PATENT_SESSION_ID


def _get_search_results(session_id: Optional[str] = None) -> List[Dict]:
    """캐시된 검색 결과 가져오기."""
    sid = _set_session_id(session_id)
    return _PATENT_SEARCH_CACHE.get(sid, [])


def _save_search_results(results: List[Dict], session_id: Optional[str] = None) -> None:
    """검색 결과 캐시에 저장."""
    sid = _set_session_id(session_id)
    _PATENT_SEARCH_CACHE[sid] = results


def has_pending_patent_results(session_id: Optional[str] = None) -> bool:
    """대기 중인 특허 검색 결과가 있는지 확인."""
    return len(_get_search_results(session_id)) > 0


# ───────────────────────────────────────────────────────────
# KIPRIS API 함수들
# ───────────────────────────────────────────────────────────
def kipris_search(word: str, year: int = 0, rows: int = 10, page: int = 1) -> Dict[str, Any]:
    """KIPRIS 검색 API."""
    url = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch"
    params = {
        "word": word,
        "year": year,
        "patent": "true",
        "utility": "false",
        "numOfRows": rows,
        "pageNo": page,
        "ServiceKey": KIPRIS_API_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        r.encoding = "utf-8"
        r.raise_for_status()
        parsed_data = xmltodict.parse(r.text)
        # xmltodict.parse가 None을 반환할 수 있으므로 체크
        if parsed_data is None:
            return {"response": {"body": {"items": {"item": []}}, "header": {"resultCode": "ERROR", "resultMsg": "XML 파싱 실패"}}}
        return parsed_data
    except Exception as e:
        return {"response": {"body": {"items": {"item": []}}, "header": {"resultCode": "ERROR", "resultMsg": str(e)}}}


def kipris_download_pdf(application_number: str, save_dir: Path) -> tuple[Optional[Path], Dict[str, Any]]:
    """KIPRIS PDF 다운로드."""
    meta_url = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getPubFullTextInfoSearch"
    meta_params = {"applicationNumber": application_number, "ServiceKey": KIPRIS_API_KEY}
    
    download_info = {
        "application_number": application_number,
        "error": "",
        "pdf_saved": False,
        "download_url": "",
        "doc_name": f"{application_number}.pdf",
    }
    
    try:
        meta_resp = requests.get(meta_url, params=meta_params, timeout=20)
        meta_resp.raise_for_status()
        meta_data = xmltodict.parse(meta_resp.text)
        
        # xmltodict.parse가 None을 반환할 수 있으므로 체크
        if not meta_data or not isinstance(meta_data, dict):
            download_info["error"] = "XML 파싱 실패: 응답 데이터가 비어있습니다."
            return None, download_info
        
    except Exception as e:
        download_info["error"] = str(e)
        return None, download_info
    
    try:
        response = meta_data.get("response", {})
        if not response or not isinstance(response, dict):
            download_info["error"] = "응답 형식 오류: response 필드가 없습니다."
            return None, download_info
        
        body = response.get("body", {})
        if not body or not isinstance(body, dict):
            download_info["error"] = "응답 형식 오류: body 필드가 없습니다."
            return None, download_info
        
        item = body.get("item") or {}
        if not isinstance(item, dict):
            download_info["error"] = "응답 형식 오류: item 필드가 없습니다."
            return None, download_info
        
        download_url = item.get("path")
        doc_name = item.get("docName") or f"{application_number}.pdf"
        download_info["doc_name"] = doc_name
        download_info["download_url"] = download_url or ""
    except Exception as e:
        download_info["error"] = f"메타데이터 파싱 오류: {str(e)}"
        return None, download_info

    if not download_url:
        download_info["error"] = "PDF 다운로드 URL이 없습니다."
        return None, download_info
    
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^0-9A-Za-z_.-]", "_", doc_name)
    out_path = save_dir / safe_name
    download_info["pdf_path"] = str(out_path)

    headers = {
        "Range": "bytes=0-",
        "User-Agent": "Mozilla/5.0 (compatible; PatentAgent/1.0)",
    }

    try:
        with requests.get(download_url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "").lower()
            is_pdf_header = "application/pdf" in content_type or "octet-stream" in content_type
            is_pdf_magic = False

            with open(out_path, "wb") as f:
                for idx, chunk in enumerate(r.iter_content(chunk_size=8192)):
                    if not chunk:
                        continue
                    if idx == 0:
                        is_pdf_magic = chunk.startswith(b"%PDF")
                    f.write(chunk)

        if (is_pdf_header and is_pdf_magic) and out_path.exists() and out_path.stat().st_size > 0:
            download_info["pdf_saved"] = True
            return out_path, download_info
        else:
            download_info["error"] = "PDF 파일 형식이 올바르지 않거나 파일이 비어있습니다."
    except Exception as e:
        download_info["error"] = f"PDF 다운로드 중 오류: {str(e)}"

    try:
        if out_path.exists():
            out_path.unlink(missing_ok=True)
    except Exception:
        pass

    return None, download_info


def ensure_list(x: Any) -> List[Any]:
    """리스트/단일 아이템 정규화."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def clean_text(s: Optional[str]) -> str:
    """텍스트 정리."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def extract_pdf_text(pdf_path: Path) -> str:
    """PDF 텍스트 추출."""
    if not PDF_SUPPORTED:
        raise ImportError("PyPDF2 미설치: pip install PyPDF2 로 설치 후 다시 시도하세요.")
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        pages.append(page_text.strip())
    return "\n\n".join(filter(None, pages))


def save_text_file(text_dir: Path, application_number: str, title: str, abstract: str, body: str) -> Path:
    """텍스트 파일 저장."""
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path = text_dir / f"{application_number}.txt"
    parts = [
        f"제목: {title}",
        f"초록: {abstract}",
        "",
        "본문:",
        body.strip(),
    ]
    with open(text_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts).strip() + "\n")
    return text_path


def save_metadata(meta_dir: Path, metadata: Dict[str, Any]) -> Path:
    """메타데이터 저장."""
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{metadata['application_number']}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return meta_path


# ───────────────────────────────────────────────────────────
# 임베딩 모델 및 벡터DB 초기화 (지연 로딩)
# ───────────────────────────────────────────────────────────
_embedding_model: Optional[SentenceTransformer] = None
_splitter: Optional[RecursiveCharacterTextSplitter] = None


def _initialize_models() -> None:
    """임베딩 모델 및 텍스트 스플리터 초기화."""
    global _embedding_model, _splitter
    
    if not SENTENCE_TRANSFORMERS_AVAILABLE:
        raise ImportError("sentence-transformers가 설치되지 않았습니다. pip install sentence-transformers")
    
    if not TEXT_SPLITTER_AVAILABLE:
        raise ImportError("langchain_text_splitters가 설치되지 않았습니다. pip install langchain-text-splitters")
    
    if _embedding_model is None:
        try:
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
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
    
    milvus_host = _get_env("MILVUS_HOST", "127.0.0.1")
    milvus_port = _get_env("MILVUS_PORT", "19530")
    
    try:
        connections.connect("default", host=milvus_host, port=milvus_port)
    except Exception as e:
        raise RuntimeError(f"Milvus 연결 실패 (호스트: {milvus_host}:{milvus_port}): {e}")


def extract_keyword_from_text(text: str) -> str:
    """특허 제목/초록에서 주요 키워드 추출."""
    keywords = [
        "디스플레이", "display", "화면", "스크린",
        "베터리", "battery", "배터리", "전지", "충전",
        "와이파이", "wifi", "wi-fi", "무선통신", "네트워크",
        "볼펜", "pen", "펜",
        "반도체", "semiconductor", "칩",
        "센서", "sensor", "감지",
        "로봇", "robot", "자동화",
        "인공지능", "ai", "머신러닝", "딥러닝",
        "자동차", "차량", "vehicle", "car",
        "스마트폰", "휴대폰", "모바일",
    ]
    
    text_lower = text.lower()
    for keyword in keywords:
        if keyword.lower() in text_lower:
            if any(ord(char) >= 0xAC00 and ord(char) <= 0xD7A3 for char in keyword):
                return keyword
            keyword_map = {
                "display": "디스플레이",
                "battery": "베터리",
                "wifi": "와이파이",
                "wi-fi": "와이파이",
                "pen": "볼펜",
                "semiconductor": "반도체",
                "sensor": "센서",
                "robot": "로봇",
                "ai": "인공지능",
                "vehicle": "자동차",
                "car": "자동차",
                "mobile": "스마트폰",
            }
            return keyword_map.get(keyword.lower(), keyword)
    
    return "기타"


def _sanitize_collection_name(keyword: str) -> str:
    """키워드를 Milvus 컬렉션 이름으로 변환 (한글을 영문으로 변환).
    
    Milvus 컬렉션 이름은 영문자, 숫자, 언더스코어만 허용합니다.
    한글 키워드는 영문으로 변환하거나 해시 기반으로 변환합니다.
    """
    import re
    import hashlib
    
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
    }
    
    # 키워드 맵에서 직접 매핑 확인
    normalized_keyword = keyword.strip()
    
    # 정확히 일치하는 키워드가 있으면 반환
    if normalized_keyword in keyword_map:
        return keyword_map[normalized_keyword]
    
    # 키워드에 포함된 단어들이 맵에 있는지 확인 (부분 일치)
    # 예: "배터리 충전" -> "battery" 추출
    for korean_key, english_value in keyword_map.items():
        if korean_key in normalized_keyword:
            return english_value
    
    # 2. 키워드에 한글이 포함되어 있는지 확인
    has_korean = any('가' <= char <= '힣' for char in normalized_keyword)
    
    # 3. 영문자, 숫자, 언더스코어만 추출
    english_only = re.sub(r'[^a-zA-Z0-9_]', '_', normalized_keyword)
    english_only = re.sub(r'_+', '_', english_only).strip('_')
    
    # 4. 한글이 포함되어 있으면 해시 기반으로 변환 (영문 변환 실패 시)
    if has_korean:
        # 키워드의 해시값을 사용하여 고유한 컬렉션 이름 생성
        # 한글 키워드는 항상 해시 기반으로 변환하여 안전하게 처리
        hash_value = hashlib.md5(normalized_keyword.encode('utf-8')).hexdigest()[:12]
        # 영문자/숫자 부분이 있으면 포함 (하이브리드)
        if english_only and len(english_only) >= 2:
            return f"{english_only}_{hash_value}"
        else:
            # 순수 한글인 경우 해시만 사용
            return f"kw_{hash_value}"
    elif english_only:
        # 영문자만 있으면 그대로 사용 (길이 제한)
        if len(english_only) > 240:
            english_only = english_only[:240]
        return english_only.lower()
    else:
        # 영문자도 없으면 해시값 사용
        hash_value = hashlib.md5(normalized_keyword.encode('utf-8')).hexdigest()[:12]
        return f"kw_{hash_value}"


def get_collection_name(keyword: str) -> str:
    """키워드로 컬렉션 이름 생성 (Milvus 호환).
    
    Args:
        keyword: 검색 키워드 (한글 가능)
    
    Returns:
        Milvus 컬렉션 이름 (영문자, 숫자, 언더스코어만 포함)
    
    Raises:
        ValueError: 컬렉션 이름에 한글이나 특수문자가 포함된 경우
    """
    import re
    
    collection_suffix = _sanitize_collection_name(keyword)
    collection_name = f"patents_embeddings_{collection_suffix}"
    
    # 최종 검증: 컬렉션 이름이 Milvus 규칙을 준수하는지 확인
    # 영문자, 숫자, 언더스코어만 허용
    if not re.match(r'^[a-zA-Z0-9_]+$', collection_name):
        # 한글이나 특수문자가 포함된 경우 강제로 해시값 사용
        import hashlib
        hash_value = hashlib.md5(keyword.encode('utf-8')).hexdigest()[:12]
        collection_name = f"patents_embeddings_kw_{hash_value}"
    
    # 길이 제한 (Milvus 컬렉션 이름은 최대 255자)
    if len(collection_name) > 255:
        import hashlib
        hash_value = hashlib.md5(keyword.encode('utf-8')).hexdigest()[:12]
        collection_name = f"patents_embeddings_kw_{hash_value}"
    
    return collection_name


def get_or_create_collection(collection_name: str) -> Collection:
    """컬렉션 가져오기 또는 생성."""
    _initialize_milvus()
    
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="app_no", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="abstract", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),
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


def check_vector_db(query: str, limit: int = 5, max_distance: float = 0.8) -> List[Dict[str, Any]]:
    """벡터DB에서 유사 특허 검색."""
    try:
        _initialize_models()
        _initialize_milvus()
        
        keyword = extract_keyword_from_text(query)
        collection_name = get_collection_name(keyword)
        
        try:
            target_collection = Collection(collection_name)
            target_collection.load()
        except Exception:
            return []
        
        query_vec = _embedding_model.encode([query])[0].tolist()  # type: ignore
        results = target_collection.search(
            data=[query_vec],
            anns_field="embedding",
            param={"metric_type": "L2", "params": {"nprobe": 10}},
            limit=limit * 5,
            output_fields=["app_no", "title", "abstract", "content"]
        )
        
        found_patents = []
        seen_app_nos = set()
        query_lower = query.lower()
        query_words = re.findall(r'[\w가-힣]+', query_lower)
        
        for hit in results[0]:
            app_no = hit.entity.get('app_no')
            if not app_no or app_no in seen_app_nos:
                continue
            
            title = hit.entity.get('title', '').lower()
            abstract = hit.entity.get('abstract', '').lower()
            content = hit.entity.get('content', '').lower()
            
            full_match = (query_lower in title or query_lower in abstract or query_lower in content)
            word_match = any(word in title or word in abstract or word in content for word in query_words if len(word) > 1)
            keyword_match = full_match or word_match
            
            effective_max_distance = 1.2 if keyword_match else max_distance
            
            if hit.distance > effective_max_distance:
                continue
            
            seen_app_nos.add(app_no)
            similarity = 1.0 / (1.0 + hit.distance)
            found_patents.append({
                "application_number": app_no,
                "title": hit.entity.get('title'),
                "abstract": hit.entity.get('abstract'),
                "content": hit.entity.get('content'),
                "score": hit.distance,
                "similarity": similarity
            })
        
        found_patents.sort(key=lambda x: x["score"])
        return found_patents[:limit]
    except Exception as e:
        print(f"⚠️ 벡터DB 검색 오류: {e}")
        return []


def embed_and_store_patent(app_no: str, title: str, abstract: str, text_path: Path) -> int:
    """특허 텍스트를 임베딩하고 분야별 컬렉션에 저장."""
    try:
        _initialize_models()
        _initialize_milvus()
        
        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        
        combined_text = f"{title} {abstract}"
        keyword = extract_keyword_from_text(combined_text)
        collection_name = get_collection_name(keyword)
        
        target_collection = get_or_create_collection(collection_name)
        
        chunks = _splitter.split_text(text)  # type: ignore
        embeddings = _embedding_model.encode(chunks, show_progress_bar=False)  # type: ignore
        
        app_nos = [app_no] * len(chunks)
        titles = [title] * len(chunks)
        abstracts = [abstract] * len(chunks)
        
        data = [app_nos, titles, abstracts, chunks, embeddings.tolist()]
        target_collection.insert(data)
        target_collection.flush()
        
        return len(chunks)
    except Exception as e:
        print(f"⚠️ 임베딩 저장 오류: {e}")
        return 0


# ───────────────────────────────────────────────────────────
# LangGraph State 정의
# ───────────────────────────────────────────────────────────
if LANGCHAIN_AVAILABLE:
    class PatentSearchState(TypedDict):
        user_query: str
        keyword: str
        search_results: List[Dict[str, Any]]
        selected_patent: Optional[Dict[str, Any]]
        selected_indices: List[int]
        vector_db_results: List[Dict[str, Any]]
        pdf_content: str
        pdf_link: str
        answer: str
        next_action: Literal["check_db", "search_kipris", "download_pdf", "rag_answer", "end"]


# ───────────────────────────────────────────────────────────
# LangGraph 노드들
# ───────────────────────────────────────────────────────────
def check_db_node(state: PatentSearchState) -> PatentSearchState:
    """벡터DB에 정보가 있는지 확인."""
    query = state["user_query"]
    results = check_vector_db(query, limit=5, max_distance=0.8)
    
    if results and len(results) > 0:
        best_score = results[0]["score"]
        query_lower = query.lower()
        query_words = re.findall(r'[\w가-힣]+', query_lower)
        
        relevant_results = []
        for patent in results:
            title = patent.get('title', '').lower()
            abstract = patent.get('abstract', '').lower()
            content = patent.get('content', '').lower()
            
            full_match = query_lower in title or query_lower in abstract or query_lower in content
            word_match = any(word in title or word in abstract or word in content for word in query_words if len(word) > 1)
            
            if full_match or word_match:
                relevant_results.append(patent)
        
        if best_score <= 0.6 or (best_score <= 1.2 and relevant_results):
            state["vector_db_results"] = relevant_results[:5] if relevant_results else results[:5]
            state["next_action"] = "rag_answer"
        else:
            state["next_action"] = "search_kipris"
    else:
        state["next_action"] = "search_kipris"
    
    return state


def search_kipris_node(state: PatentSearchState) -> PatentSearchState:
    """KIPRIS API로 특허 검색."""
    query = state.get("keyword") or state["user_query"]
    try:
        data = kipris_search(query, rows=10)
        
        if not data or not isinstance(data, dict):
            state["search_results"] = []
            state["next_action"] = "end"
            return state
        
        response = data.get("response", {})
        if not response or not isinstance(response, dict):
            state["search_results"] = []
            state["next_action"] = "end"
            return state
        
        body = response.get("body", {})
        if not body or not isinstance(body, dict):
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
        
        state["search_results"] = search_results
        state["next_action"] = "end"
    except Exception as e:
        print(f"⚠️ KIPRIS 검색 오류: {e}")
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
    
    base_dir = Path(__file__).resolve().parent.parent
    data_root = base_dir / "data"
    pdf_dir = data_root / "pdf"
    text_dir = data_root / "text"
    meta_dir = data_root / "meta"
    
    try:
        pdf_path, download_info = kipris_download_pdf(app_no, pdf_dir)
        
        if not pdf_path or not pdf_path.exists():
            error_msg = download_info.get("error", "알 수 없는 오류")
            state["pdf_content"] = f"PDF 다운로드 실패: {error_msg}"
            state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
            state["next_action"] = "end"
            return state
        
        try:
            body = extract_pdf_text(pdf_path)
            if not body or len(body.strip()) == 0:
                state["pdf_content"] = "PDF 텍스트 추출 결과가 비어있습니다."
                state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
                state["next_action"] = "end"
                return state
            
            text_path = save_text_file(text_dir, app_no, title, abstract, body)
            
            try:
                chunks_count = embed_and_store_patent(app_no, title, abstract, text_path)
            except Exception as e:
                print(f"⚠️ 임베딩 저장 오류 (계속 진행): {e}")
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
                print(f"⚠️ 메타데이터 저장 오류 (계속 진행): {e}")
            
            state["pdf_content"] = body
            state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
            state["next_action"] = "rag_answer"
            
        except Exception as e:
            error_msg = str(e)
            print(f"⚠️ PDF 텍스트 추출 오류: {error_msg}")
            state["pdf_content"] = f"텍스트 추출 실패: {error_msg}"
            state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
            state["next_action"] = "end"
    
    except Exception as e:
        error_msg = str(e)
        print(f"⚠️ PDF 다운로드 오류: {error_msg}")
        state["pdf_content"] = f"PDF 다운로드 실패: {error_msg}"
        state["pdf_link"] = ""
        state["next_action"] = "end"
    
    return state


def rag_answer_node(state: PatentSearchState) -> PatentSearchState:
    """RAG로 사용자 질문에 답변."""
    if not LANGCHAIN_AVAILABLE or not OPENAI_API_KEY:
        state["answer"] = "OpenAI API 키가 설정되지 않았거나 langchain이 설치되지 않았습니다."
        state["next_action"] = "end"
        return state
    
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
    
    if state.get("vector_db_results"):
        context_patents = state["vector_db_results"]
        context_text = "\n\n".join([
            f"특허 {idx+1}:\n제목: {p['title']}\n초록: {p['abstract']}\n내용: {p.get('content', '')[:500]}"
            for idx, p in enumerate(context_patents[:3])
        ])
    elif state.get("pdf_content"):
        context_text = f"제목: {state['selected_patent']['title']}\n초록: {state['selected_patent']['abstract']}\n본문: {state['pdf_content'][:2000]}"
    else:
        context_text = "관련 특허 정보가 없습니다."
    
    prompt = f"""다음 특허 정보를 참고하여 사용자의 질문에 답변하세요.

특허 정보:
{context_text}

사용자 질문: {state['user_query']}

답변을 한국어로 작성하고, 특허의 핵심 기술과 특징을 명확히 설명하세요."""
    
    messages = [
        SystemMessage(content="당신은 특허 정보를 분석하는 전문가입니다."),
        HumanMessage(content=prompt)
    ]
    
    response = llm.invoke(messages)
    state["answer"] = response.content
    state["next_action"] = "end"
    
    return state


# ───────────────────────────────────────────────────────────
# LangGraph 그래프 구성
# ───────────────────────────────────────────────────────────
def _should_continue(state: PatentSearchState) -> str:
    """다음 노드 결정."""
    return state["next_action"]


_workflow = None


def _get_workflow() -> Any:
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
        
        workflow.set_entry_point("check_db")
        
        workflow.add_conditional_edges(
            "check_db",
            _should_continue,
            {
                "rag_answer": "rag_answer",
                "search_kipris": "search_kipris",
            }
        )
        
        workflow.add_edge("search_kipris", END)
        workflow.add_edge("download_pdf", "rag_answer")
        workflow.add_edge("rag_answer", END)
        
        _workflow = workflow.compile()
    
    return _workflow


# ───────────────────────────────────────────────────────────
# 메인 함수 (기존 인터페이스 유지)
# ───────────────────────────────────────────────────────────
def _extract_keyword(user_text: str) -> str:
    """사용자 질문에서 키워드 추출."""
    t = user_text.strip()
    
    # "관련" 앞 단어 추출
    if "관련" in t:
        keyword = t.split("관련")[0].strip()
        # "검색해줘", "찾아줘" 같은 동사 제거
        keyword = re.sub(r"\s*(검색|찾아|찾아줘|검색해줘|검색해|찾아서).*$", "", keyword).strip()
        return keyword
    
    # 따옴표 안 내용 추출
    for quote in ['"', "'"]:
        if t.count(quote) >= 2:
            first = t.find(quote) + 1
            second = t.find(quote, first)
            if second > first:
                return t[first:second].strip()
    
    # 기본: "특허" 키워드 제거 후 나머지
    keyword = re.sub(r"(특허|patent|Patent)\s*", "", t, flags=re.IGNORECASE).strip()
    keyword = re.sub(r"\s*(검색|찾아|찾아줘|검색해줘|검색해|찾아서).*$", "", keyword).strip()
    
    return keyword if keyword else t


def _parse_selection_number(user_text: str) -> Optional[int]:
    """사용자 입력에서 선택 번호 파싱 (예: "1번", "7번", "7 상세")."""
    t = user_text.strip()
    
    # 숫자만 입력된 경우
    if re.fullmatch(r"\d+", t):
        return int(t) - 1
    
    # 숫자 + "번", "상세", "선택" 등
    match = re.match(r"^(\d+)", t)
    if match:
        num = int(match.group(1))
        if any(k in t for k in ["번", "상세", "선택", "자세히"]):
            return num - 1
    
    return None


def patent_search(keyword: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """특허 검색 메인 함수 (기존 인터페이스 유지).
    
    Args:
        keyword: 검색 키워드 또는 사용자 질문
        session_id: 세션 ID (멀티턴 대화 지원)
        
    Returns:
        검색 결과 또는 상세 정보
    """
    # 특허 번호 패턴 감지 (10자리 이상 숫자)
    patent_number_match = re.search(r'\b(\d{10,})\b', keyword)
    if patent_number_match:
        app_no = patent_number_match.group(1)
        cached_results = _get_search_results(session_id)
        
        # 캐시된 결과에서 해당 특허 번호 찾기
        selected_patent = None
        for patent in cached_results:
            if patent.get("application_number") == app_no:
                selected_patent = patent
                break
        
        # 캐시에 없으면 해당 번호로 직접 검색 시도
        if not selected_patent:
            try:
                data = kipris_search(app_no, rows=1)
                if data and isinstance(data, dict):
                    response = data.get("response", {})
                    if response and isinstance(response, dict):
                        body = response.get("body", {})
                        if body and isinstance(body, dict):
                            items_dict = body.get("items", {})
                            if items_dict and isinstance(items_dict, dict):
                                items = items_dict.get("item")
                                items = ensure_list(items)
                                
                                if items and len(items) > 0:
                                    it = items[0]
                                    if isinstance(it, dict):
                                        selected_patent = {
                                            "application_number": clean_text(it.get("applicationNumber")),
                                            "title": clean_text(it.get("inventionTitle")),
                                            "abstract": clean_text(it.get("astrtCont")),
                                        }
            except Exception as e:
                print(f"⚠️ 특허 번호 검색 오류: {e}")
        
        # 특허를 찾았으면 상세 처리
        if selected_patent:
            if LANGCHAIN_AVAILABLE:
                try:
                    workflow = _get_workflow()
                    if workflow:
                        initial_state: PatentSearchState = {
                            "user_query": keyword,
                            "keyword": app_no,
                            "search_results": cached_results if cached_results else [selected_patent],
                            "selected_patent": selected_patent,
                            "selected_indices": [],
                            "vector_db_results": [],
                            "pdf_content": "",
                            "pdf_link": "",
                            "answer": "",
                            "next_action": "download_pdf",
                        }
                        
                        result = download_pdf_node(initial_state)
                        
                        # result가 dict인지 확인
                        if not isinstance(result, dict):
                            raise ValueError(f"download_pdf_node가 dict를 반환하지 않았습니다: {type(result)}")
                        
                        if result.get("next_action") == "rag_answer":
                            result = rag_answer_node(result)
                            if not isinstance(result, dict):
                                raise ValueError(f"rag_answer_node가 dict를 반환하지 않았습니다: {type(result)}")
                        
                        if result.get("answer"):
                            return {
                                "ok": True,
                                "message": result["answer"],
                                "patent": selected_patent,
                                "pdf_link": result.get("pdf_link", ""),
                            }
                        elif result.get("pdf_content") and "실패" not in result.get("pdf_content", ""):
                            return {
                                "ok": True,
                                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\nPDF 다운로드 완료",
                                "patent": selected_patent,
                                "pdf_link": result.get("pdf_link", ""),
                            }
                        elif result.get("pdf_content"):
                            # PDF 다운로드 실패 시에도 기본 정보는 반환
                            return {
                                "ok": True,
                                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\n⚠️ {result.get('pdf_content', 'PDF 다운로드 실패')}",
                                "patent": selected_patent,
                                "pdf_link": result.get("pdf_link", ""),
                            }
                except Exception as e:
                    import traceback
                    error_detail = traceback.format_exc()
                    print(f"⚠️ 상세 처리 오류: {e}")
                    print(f"⚠️ 상세 처리 오류 상세:\n{error_detail}")
                    # 에러 발생 시에도 기본 정보는 반환
                    try:
                        return {
                            "ok": True,
                            "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\n⚠️ 상세 정보 처리 중 오류가 발생했습니다: {str(e)}",
                            "patent": selected_patent,
                        }
                    except Exception:
                        return {
                            "ok": False,
                            "error": f"특허 상세 정보 처리 중 오류가 발생했습니다: {str(e)}",
                        }
            
            # LangGraph 미사용 시 기본 정보 반환
            return {
                "ok": True,
                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}",
                "patent": selected_patent,
            }
        else:
            return {
                "ok": False,
                "error": f"특허 번호 '{app_no}'를 찾을 수 없습니다.",
            }
    
    # 키워드 추출
    extracted_keyword = _extract_keyword(keyword)
    
    # 선택 번호 확인 (이전 검색 결과가 있는 경우)
    selection_index = _parse_selection_number(keyword)
    if selection_index is not None:
        cached_results = _get_search_results(session_id)
        if cached_results and 0 <= selection_index < len(cached_results):
            # 선택된 특허의 상세 정보 반환
            selected_patent = cached_results[selection_index]
            
            # LangGraph 사용 가능하면 상세 처리
            if LANGCHAIN_AVAILABLE:
                try:
                    workflow = _get_workflow()
                    if workflow:
                        initial_state: PatentSearchState = {
                            "user_query": keyword,
                            "keyword": extracted_keyword,
                            "search_results": cached_results,
                            "selected_patent": selected_patent,
                            "selected_indices": [selection_index],
                            "vector_db_results": [],
                            "pdf_content": "",
                            "pdf_link": "",
                            "answer": "",
                            "next_action": "download_pdf",
                        }
                        
                        # PDF 다운로드 및 RAG 답변
                        result = download_pdf_node(initial_state)
                        
                        # result가 dict인지 확인
                        if not isinstance(result, dict):
                            raise ValueError(f"download_pdf_node가 dict를 반환하지 않았습니다: {type(result)}")
                        
                        if result.get("next_action") == "rag_answer":
                            result = rag_answer_node(result)
                            if not isinstance(result, dict):
                                raise ValueError(f"rag_answer_node가 dict를 반환하지 않았습니다: {type(result)}")
                        
                        if result.get("answer"):
                            return {
                                "ok": True,
                                "message": result["answer"],
                                "patent": selected_patent,
                                "pdf_link": result.get("pdf_link", ""),
                            }
                        elif result.get("pdf_content") and "실패" not in result.get("pdf_content", ""):
                            return {
                                "ok": True,
                                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\nPDF 다운로드 완료",
                                "patent": selected_patent,
                                "pdf_link": result.get("pdf_link", ""),
                            }
                        elif result.get("pdf_content"):
                            # PDF 다운로드 실패 시에도 기본 정보는 반환
                            return {
                                "ok": True,
                                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\n⚠️ {result.get('pdf_content', 'PDF 다운로드 실패')}",
                                "patent": selected_patent,
                                "pdf_link": result.get("pdf_link", ""),
                            }
                except Exception as e:
                    import traceback
                    error_detail = traceback.format_exc()
                    print(f"⚠️ 상세 처리 오류: {e}")
                    print(f"⚠️ 상세 처리 오류 상세:\n{error_detail}")
                    # 에러 발생 시에도 기본 정보는 반환
                    try:
                        return {
                            "ok": True,
                            "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\n⚠️ 상세 정보 처리 중 오류가 발생했습니다: {str(e)}",
                            "patent": selected_patent,
                        }
                    except Exception:
                        return {
                            "ok": False,
                            "error": f"특허 상세 정보 처리 중 오류가 발생했습니다: {str(e)}",
                        }
            
            # LangGraph 미사용 시 기본 정보 반환
            return {
                "ok": True,
                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}",
                "patent": selected_patent,
            }
    
    # LangGraph 사용 가능하면 전체 워크플로우 실행
    if LANGCHAIN_AVAILABLE:
        try:
            workflow = _get_workflow()
            if workflow:
                initial_state: PatentSearchState = {
                    "user_query": keyword,
                    "keyword": extracted_keyword,
                    "search_results": [],
                    "selected_patent": None,
                    "selected_indices": [],
                    "vector_db_results": [],
                    "pdf_content": "",
                    "pdf_link": "",
                    "answer": "",
                    "next_action": "check_db",
                }
                
                result = workflow.invoke(initial_state)
                
                # 벡터DB 결과가 있으면 RAG 답변 반환
                if result.get("answer"):
                    return {
                        "ok": True,
                        "message": result["answer"],
                        "vector_db_results": result.get("vector_db_results", []),
                    }
                
                # KIPRIS 검색 결과 반환
                if result.get("search_results"):
                    search_results = result["search_results"]
                    _save_search_results(search_results, session_id)
                    
                    items = []
                    for idx, patent in enumerate(search_results, 1):
                        items.append({
                            "index": idx,
                            "application_number": patent["application_number"],
                            "title": patent["title"],
                            "abstract": patent["abstract"][:200] + "..." if len(patent.get("abstract", "")) > 200 else patent.get("abstract", ""),
                        })
                    
                    message_lines = [
                        f"🔍 '{extracted_keyword}' 관련 특허 {len(search_results)}개를 찾았습니다."
                    ]
                    
                    return {
                        "ok": True,
                        "message": "\n".join(message_lines),
                        "items": items,
                        "search_results": search_results,
                    }
        except Exception as e:
            print(f"⚠️ LangGraph 실행 오류: {e}, 기본 검색으로 fallback")
    
    # 기본 KIPRIS 검색 (LangGraph 미사용 또는 오류 시)
    try:
        data = kipris_search(extracted_keyword, rows=5)
        if not data or not isinstance(data, dict):
            return {
                "ok": False,
                "error": f"KIPRIS API 응답 오류: 검색 결과를 가져올 수 없습니다.",
            }
        
        items_data = data.get("response", {}).get("body", {}).get("items", {}).get("item")
        items_data = ensure_list(items_data)
        
        search_results = []
        items = []
        for idx, it in enumerate(items_data, 1):
            if not isinstance(it, dict):
                continue
            app_no = clean_text(it.get("applicationNumber"))
            title = clean_text(it.get("inventionTitle"))
            abstract = clean_text(it.get("astrtCont"))
            
            patent_info = {
                "application_number": app_no,
                "title": title,
                "abstract": abstract,
            }
            search_results.append(patent_info)
            
            items.append({
                "index": idx,
                "application_number": app_no,
                "title": title,
                "abstract": abstract[:200] + "..." if len(abstract) > 200 else abstract,
            })
        
        if not search_results:
            return {
                "ok": False,
                "error": f"'{extracted_keyword}' 관련 특허를 찾을 수 없습니다.",
            }
        
        _save_search_results(search_results, session_id)
        
        return {
            "ok": True,
            "message": f"🔍 '{extracted_keyword}' 관련 특허 {len(search_results)}개를 찾았습니다.",
            "items": items,
            "search_results": search_results,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"특허 검색 중 오류가 발생했습니다: {str(e)}",
        }
