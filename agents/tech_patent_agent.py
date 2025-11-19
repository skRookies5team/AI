# tech_patent_agent.py — LangGraph 기반 특허 검색 에이전트
from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import TypedDict, Annotated, Literal, Optional, Dict, List, Any, Callable
import requests
import xmltodict

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None

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
_WAITING_FOR_SEARCH_MODE: Dict[str, bool] = {}  # 세션별 검색 모드 선택 대기 상태
_PENDING_SEARCH_KEYWORD: Dict[str, str] = {}  # 세션별 검색 모드 선택 대기 중인 검색어 저장
_SESSION_SEARCH_MODE: Dict[str, Optional[Literal["server", "web"]]] = {}  # 세션별 검색 모드 저장
_WAITING_FOR_WEB_SEARCH: Dict[str, bool] = {}  # 세션별 웹 검색 여부 대기 상태
_ORIGINAL_SEARCH_KEYWORD: Dict[str, str] = {}  # 세션별 원래 검색어 저장 (웹 검색용)

# ───────────────────────────────────────────────────────────
# 진행 상황 콜백 및 골든셋 관리
# ───────────────────────────────────────────────────────────
_PROGRESS_CALLBACK: Optional[Callable[[str], None]] = None  # 진행 상황 콜백 함수
_GOLDEN_SET: Dict[str, str] = {}  # 골든셋 (키: 질문, 값: 답변)

def set_progress_callback(callback: Optional[Callable[[str], None]]) -> None:
    """진행 상황 콜백 함수 설정."""
    global _PROGRESS_CALLBACK
    _PROGRESS_CALLBACK = callback

def _notify_progress(message: str) -> None:
    """진행 상황 알림."""
    if _PROGRESS_CALLBACK:
        try:
            _PROGRESS_CALLBACK(message)
        except Exception:
            pass

def load_golden_set(file_path: Optional[str] = None) -> None:
    """골든셋 로드 (JSON 파일)."""
    global _GOLDEN_SET
    if file_path is None:
        base_dir = Path(__file__).resolve().parent.parent
        file_path = base_dir / "data" / "golden_set.json"
    
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                _GOLDEN_SET = json.load(f)
    except Exception:
        _GOLDEN_SET = {}


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


def is_waiting_for_search_mode(session_id: Optional[str] = None) -> bool:
    """검색 모드 선택 대기 중인지 확인."""
    sid = _set_session_id(session_id)
    return _WAITING_FOR_SEARCH_MODE.get(sid, False)


def set_waiting_for_search_mode(session_id: Optional[str] = None, waiting: bool = True, keyword: Optional[str] = None) -> None:
    """검색 모드 선택 대기 상태 설정."""
    sid = _set_session_id(session_id)
    _WAITING_FOR_SEARCH_MODE[sid] = waiting
    if keyword and waiting:
        _PENDING_SEARCH_KEYWORD[sid] = keyword
    # waiting=False일 때는 keyword를 삭제하지 않음 (검색 모드 선택 후 사용하기 위해)


def get_pending_search_keyword(session_id: Optional[str] = None) -> Optional[str]:
    """대기 중인 검색어 가져오기."""
    sid = _set_session_id(session_id)
    return _PENDING_SEARCH_KEYWORD.get(sid)


def get_session_search_mode(session_id: Optional[str] = None) -> Optional[Literal["server", "web"]]:
    """세션별 저장된 검색 모드 가져오기."""
    sid = _set_session_id(session_id)
    return _SESSION_SEARCH_MODE.get(sid)


def set_session_search_mode(session_id: Optional[str] = None, mode: Optional[Literal["server", "web"]] = None) -> None:
    """세션별 검색 모드 저장."""
    sid = _set_session_id(session_id)
    if mode:
        _SESSION_SEARCH_MODE[sid] = mode
    else:
        _SESSION_SEARCH_MODE.pop(sid, None)


def parse_search_mode_from_input(user_input: str) -> Optional[Literal["server", "web"]]:
    """사용자 입력에서 검색 모드 파싱."""
    t = user_input.strip().lower()
    
    # 숫자로 선택
    if t == "1" or t.startswith("1번") or t.startswith("1 "):
        return "server"
    if t == "2" or t.startswith("2번") or t.startswith("2 "):
        return "web"
    
    # 키워드로 선택 (구문 포함)
    # 서버 기반 검색 관련 키워드
    server_keywords = ["서버", "server", "벡터", "db", "데이터베이스", "서버 기반", "서버기반", "벡터db", "벡터 db"]
    if any(k in t for k in server_keywords):
        return "server"
    
    # 웹 검색 관련 키워드
    web_keywords = ["웹", "web", "kipris", "인터넷", "온라인", "웹 검색", "웹검색", "온라인 검색", "온라인검색"]
    if any(k in t for k in web_keywords):
        return "web"
    
    return None


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


def load_metadata(application_number: str) -> Optional[Dict[str, Any]]:
    """메타데이터 로드."""
    try:
        base_dir = Path(__file__).resolve().parent.parent
        meta_dir = base_dir / "data" / "meta"
        meta_path = meta_dir / f"{application_number}.json"
        
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ 메타데이터 로드 오류 ({application_number}): {e}")
    return None


def get_pdf_link_from_metadata(application_number: str) -> str:
    """메타데이터에서 PDF 다운로드 링크 가져오기."""
    metadata = load_metadata(application_number)
    if metadata and isinstance(metadata, dict):
        pdf_info = metadata.get("pdf", {})
        if isinstance(pdf_info, dict):
            return pdf_info.get("download_url", "")
    return ""


def get_full_text_from_file(application_number: str) -> str:
    """텍스트 파일에서 전체 내용 가져오기."""
    try:
        base_dir = Path(__file__).resolve().parent.parent
        text_dir = base_dir / "data" / "text"
        text_path = text_dir / f"{application_number}.txt"
        
        if text_path.exists():
            with open(text_path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception as e:
        print(f"⚠️ 텍스트 파일 로드 오류 ({application_number}): {e}")
    return ""


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
    
    # 공용 서버 주소 (환경 변수로 오버라이드 가능)
    milvus_host = _get_env("MILVUS_HOST", "175.120.35.228")
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
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),
    ]
    schema = CollectionSchema(fields, description=f"KIPRIS 특허 임베딩 데이터 - {collection_name}")
    
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


def check_vector_db(query: str, limit: int = 5, max_distance: float = 0.8, filter_keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    """벡터DB에서 유사 특허 검색.
    
    Args:
        query: 검색 쿼리
        limit: 반환할 최대 결과 수
        max_distance: 최대 거리 임계값
        filter_keyword: 필터링할 키워드 (특허_키워드 형식, None이면 필터링 안 함)
    """
    try:
        _initialize_models()
        _initialize_milvus()
        
        keyword = extract_keyword_from_text(query)
        collection_name = get_collection_name(keyword)
        
        try:
            target_collection = Collection(collection_name)
            target_collection.load()
            
            # 컬렉션에 데이터가 있는지 확인
            from pymilvus import utility
            if not utility.has_collection(collection_name):
                return []
            
            # 컬렉션의 엔티티 수 확인 (데이터가 없으면 빈 리스트 반환)
            num_entities = target_collection.num_entities
            if num_entities == 0:
                return []
        except Exception:
            return []
        
        # 컬렉션 스키마 확인 (patent_keyword 필드 존재 여부)
        schema = target_collection.schema
        field_names = [field.name for field in schema.fields]
        has_patent_keyword = "patent_keyword" in field_names
        
        query_vec = _embedding_model.encode([query])[0].tolist()  # type: ignore
        
        # 필터링 조건 설정 (patent_keyword 필드가 있을 때만)
        expr = None
        if filter_keyword and has_patent_keyword:
            # 특허_키워드 형식으로 필터링
            expr = f'patent_keyword == "{filter_keyword}"'
        
        # output_fields 동적 구성 (필드가 있을 때만 포함)
        output_fields = ["app_no", "title", "abstract", "content"]
        if has_patent_keyword:
            output_fields.append("patent_keyword")
        
        results = target_collection.search(
            data=[query_vec],
            anns_field="embedding",
            param={"metric_type": "L2", "params": {"nprobe": 10}},
            limit=limit * 5,
            expr=expr,  # 키워드 필터링 (필드가 있을 때만)
            output_fields=output_fields
        )
        
        # 검색 결과가 비어있는지 확인
        if not results or len(results) == 0 or len(results[0]) == 0:
            return []
        
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
            
            # 키워드 매칭이 있으면 거리 임계값을 더 완화
            effective_max_distance = 2.0 if keyword_match else max_distance
            
            if hit.distance > effective_max_distance:
                continue
            
            seen_app_nos.add(app_no)
            similarity = 1.0 / (1.0 + hit.distance)
            
            # 전체 텍스트 내용 가져오기 (텍스트 파일에서)
            full_content = get_full_text_from_file(app_no)
            if not full_content:
                # 텍스트 파일이 없으면 벡터DB의 content 사용
                full_content = hit.entity.get('content', '')
            
            # PDF 링크 가져오기 (메타데이터에서)
            pdf_link = get_pdf_link_from_metadata(app_no)
            
            found_patents.append({
                "application_number": app_no,
                "title": hit.entity.get('title'),
                "abstract": hit.entity.get('abstract'),
                "content": full_content,  # 전체 텍스트 내용
                "score": hit.distance,
                "similarity": similarity,
                "pdf_link": pdf_link  # PDF 다운로드 링크
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
        patent_keyword: 특허_키워드 형식의 키워드 (예: "특허_배터리", None이면 자동 추출)
    
    Returns:
        저장된 청크 개수
    """
    try:
        _initialize_models()
        _initialize_milvus()
        
        with open(text_path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        
        combined_text = f"{title} {abstract}"
        keyword = extract_keyword_from_text(combined_text)
        
        # 특허_키워드 형식 생성
        if patent_keyword is None:
            patent_keyword = f"특허_{keyword}"
        
        collection_name = get_collection_name(keyword)
        
        target_collection = get_or_create_collection(collection_name)
        
        # 컬렉션 스키마 확인 (patent_keyword 필드 존재 여부)
        schema = target_collection.schema
        field_names = [field.name for field in schema.fields]
        has_patent_keyword = "patent_keyword" in field_names
        
        chunks = _splitter.split_text(text)  # type: ignore
        embeddings = _embedding_model.encode(chunks, show_progress_bar=False)  # type: ignore
        
        app_nos = [app_no] * len(chunks)
        titles = [title] * len(chunks)
        abstracts = [abstract] * len(chunks)
        
        # patent_keyword 필드가 있을 때만 포함
        if has_patent_keyword:
            patent_keywords = [patent_keyword] * len(chunks)  # 특허_키워드 필드
            data = [app_nos, titles, abstracts, chunks, patent_keywords, embeddings.tolist()]
        else:
            # 기존 컬렉션 (patent_keyword 필드 없음)
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
        next_action: Literal["check_db", "search_kipris", "download_pdf", "rag_answer", "end", "ask_search_mode", "ask_web_search"]
        progress_status: str  # 진행 상황 메시지
        search_mode: Optional[Literal["server", "web"]]  # 검색 모드: 서버 기반 또는 웹 검색
        use_server_first: bool  # 서버 기반 먼저 시도 여부
        session_id: Optional[str]  # 세션 ID (검색 모드 선택 대기 상태 관리용)


# ───────────────────────────────────────────────────────────
# LangGraph 노드들
# ───────────────────────────────────────────────────────────
def check_db_node(state: PatentSearchState) -> PatentSearchState:
    """벡터DB에 정보가 있는지 확인."""
    query = state["user_query"]
    
    # 검색 모드가 설정되지 않았고, 사용자가 명시적으로 선택하지 않은 경우 질문
    # 단, 이미 검색 모드가 설정되어 있으면 질문하지 않음
    current_search_mode = state.get("search_mode")
    if current_search_mode is None and state.get("use_server_first") is True:
        # 첫 검색 시 검색 모드 선택 질문
        state["progress_status"] = "❓ 검색 방식을 선택해주세요."
        state["next_action"] = "ask_search_mode"
        return state
    
    # 검색 모드가 "web"이면 바로 웹 검색으로
    if current_search_mode == "web":
        state["progress_status"] = "🌐 웹 검색 모드로 전환합니다..."
        _notify_progress(state["progress_status"])
        state["next_action"] = "search_kipris"
        return state
    
    # 서버 기반 검색 모드 (search_mode == "server" 또는 None이지만 use_server_first == True)
    state["progress_status"] = "🔍 벡터DB에서 관련 특허를 검색 중입니다..."
    _notify_progress(state["progress_status"])
    
    # 특허_키워드 필터링 (세부 원문 요청 시)
    filter_keyword = None
    if current_search_mode == "server" and state.get("keyword"):
        filter_keyword = f"특허_{state['keyword']}"
    
    # 거리 임계값을 더 완화하여 검색 (1.5로 증가)
    results = check_vector_db(query, limit=5, max_distance=1.5, filter_keyword=filter_keyword)
    
    # 결과가 있고 유효한 경우에만 처리
    if results and len(results) > 0:
        best_score = results[0]["score"]
        query_lower = query.lower()
        # 검색 쿼리에서 불필요한 단어 제거
        query_clean = re.sub(r'\s*(관련|찾아|찾아줘|검색|검색해줘|검색해|찾아서|특허|patent)\s*', ' ', query_lower).strip()
        query_words = re.findall(r'[\w가-힣]+', query_clean)
        
        relevant_results = []
        for patent in results:
            title = patent.get('title', '').lower()
            abstract = patent.get('abstract', '').lower()
            content = patent.get('content', '').lower()
            
            full_match = query_clean in title or query_clean in abstract or query_clean in content
            word_match = any(word in title or word in abstract or word in content for word in query_words if len(word) > 1)
            
            if full_match or word_match:
                relevant_results.append(patent)
        
        # 조건: best_score가 합리적인 범위 내에 있고, 관련 결과가 있거나 거리가 충분히 가까운 경우만 사용
        # max_distance가 1.5이므로 best_score가 1.5 이하인 경우만 유효한 결과로 간주
        if best_score <= 1.5 and (relevant_results or best_score <= 1.0):
            # 관련 결과가 있으면 우선 사용, 없으면 거리 기준으로 사용
            state["vector_db_results"] = relevant_results[:5] if relevant_results else results[:5]
            state["progress_status"] = "✅ 벡터DB에서 관련 특허를 찾았습니다. 답변을 생성 중입니다..."
            _notify_progress(state["progress_status"])
            state["next_action"] = "rag_answer"
        else:
            # 거리가 너무 멀거나 관련 결과가 없으면 웹 검색으로 전환
            if state.get("use_server_first") and current_search_mode != "web":
                state["progress_status"] = "⚠️ 서버 기반 검색 결과가 없습니다. 웹 검색으로 자동 전환합니다..."
                _notify_progress(state["progress_status"])
                state["search_mode"] = "web"  # 검색 모드를 web으로 변경
                state["next_action"] = "search_kipris"
            else:
                state["next_action"] = "search_kipris"
    else:
        # 서버 기반 검색이 실패했고, 웹 검색 모드가 아니면 웹 검색으로 전환
        if state.get("use_server_first") and current_search_mode != "web":
            state["progress_status"] = "⚠️ 서버 기반 검색 결과가 없습니다. 웹 검색으로 자동 전환합니다..."
            _notify_progress(state["progress_status"])
            state["search_mode"] = "web"  # 검색 모드를 web으로 변경
        else:
            state["progress_status"] = "🔍 웹에서 특허를 검색 중입니다..."
            _notify_progress(state["progress_status"])
        state["next_action"] = "search_kipris"
    
    return state


def ask_search_mode_node(state: PatentSearchState) -> PatentSearchState:
    """검색 모드 선택 질문."""
    # 세션 ID 가져오기
    session_id = state.get("session_id")
    # user_query를 우선 사용 (검색어가 더 정확함)
    keyword = state.get("user_query", "") or state.get("keyword", "")
    if session_id and keyword:
        # 검색어를 저장하고 대기 상태 설정
        set_waiting_for_search_mode(session_id, True, keyword=keyword)
        # 디버깅: 저장 확인
        print(f"🔍 [DEBUG] 검색어 저장: session_id={session_id}, keyword={keyword}")
    
    state["progress_status"] = "❓ 검색 방식을 선택해주세요:\n1. 서버 기반 검색 (빠르지만 제한적)\n2. 웹 검색 (느리지만 광범위)\n\n'1' 또는 '서버', '2' 또는 '웹'으로 답변해주세요.\n\n서버 기반 검색을 선택하면 벡터DB에서 먼저 검색하고, 결과가 없으면 자동으로 웹 검색으로 전환됩니다."
    state["next_action"] = "end"
    return state


def ask_web_search_node(state: PatentSearchState) -> PatentSearchState:
    """웹 검색 여부 질문."""
    # 원래 검색어 저장 (웹 검색 시 사용)
    session_id = state.get("session_id")
    original_query = state.get("user_query", "") or state.get("keyword", "")
    if session_id and original_query:
        sid = _set_session_id(session_id)
        _ORIGINAL_SEARCH_KEYWORD[sid] = original_query
    
    state["progress_status"] = "❓ 웹에서 추가로 검색해드릴까요?\n\n'네' 또는 '예'로 답변하시면 웹에서 추가 정보를 검색하고, '아니오'로 답변하시면 종료됩니다."
    state["next_action"] = "end"
    return state


def search_kipris_node(state: PatentSearchState) -> PatentSearchState:
    """KIPRIS API로 특허 검색."""
    query = state.get("keyword") or state["user_query"]
    state["progress_status"] = "🌐 KIPRIS 웹 API에서 특허를 검색 중입니다..."
    _notify_progress(state["progress_status"])
    
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
    
    state["progress_status"] = f"📥 특허 PDF 다운로드 중: {app_no}"
    _notify_progress(state["progress_status"])
    
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
            state["progress_status"] = "📄 PDF에서 텍스트 추출 중..."
            _notify_progress(state["progress_status"])
            body = extract_pdf_text(pdf_path)
            if not body or len(body.strip()) == 0:
                state["pdf_content"] = "PDF 텍스트 추출 결과가 비어있습니다."
                state["pdf_link"] = download_info.get("download_url", "") if isinstance(download_info, dict) else ""
                state["next_action"] = "end"
                return state
            
            text_path = save_text_file(text_dir, app_no, title, abstract, body)
            
            try:
                # 특허_키워드 형식으로 저장
                keyword = state.get("keyword", extract_keyword_from_text(f"{title} {abstract}"))
                patent_keyword = f"특허_{keyword}"
                
                state["progress_status"] = "💾 벡터DB에 특허 정보 저장 중..."
                _notify_progress(state["progress_status"])
                chunks_count = embed_and_store_patent(app_no, title, abstract, text_path, patent_keyword=patent_keyword)
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
    """RAG로 사용자 질문에 답변 (골든셋 비교 포함)."""
    if not LANGCHAIN_AVAILABLE or not OPENAI_API_KEY:
        state["answer"] = "OpenAI API 키가 설정되지 않았거나 langchain이 설치되지 않았습니다."
        state["next_action"] = "end"
        return state
    
    state["progress_status"] = "🤖 AI가 답변을 생성 중입니다..."
    _notify_progress(state["progress_status"])
    
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
    
    # PDF 링크 수집 (벡터DB 결과에서)
    pdf_links = []
    
    if state.get("vector_db_results"):
        context_patents = state["vector_db_results"]
        context_text = "\n\n".join([
            f"특허 {idx+1}:\n제목: {p['title']}\n초록: {p['abstract']}\n내용: {p.get('content', '')[:2000]}"  # 전체 내용 사용
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
    if not _GOLDEN_SET:
        load_golden_set()
    
    # 유사 질문 찾기 (임베딩 기반 유사도 계산으로 개선)
    query_lower = user_query.lower()
    best_similarity = 0.0
    
    # 임베딩 모델로 쿼리 벡터 생성
    try:
        query_embedding = _embedding_model.encode([user_query])[0]  # type: ignore
    except Exception:
        query_embedding = None
    
    for golden_query, golden_ans in _GOLDEN_SET.items():
        golden_query_lower = golden_query.lower()
        
        # 1. 임베딩 기반 유사도 계산 (더 정확)
        similarity = 0.0
        if query_embedding is not None:
            try:
                golden_embedding = _embedding_model.encode([golden_query])[0]  # type: ignore
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
            answer_embedding = _embedding_model.encode([answer])[0]  # type: ignore
            golden_embedding = _embedding_model.encode([golden_answer])[0]  # type: ignore
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
    if state.get("search_mode") == "server" and state.get("vector_db_results"):
        session_id = state.get("session_id")
        if session_id:
            _WAITING_FOR_WEB_SEARCH[_set_session_id(session_id)] = True
            # 원래 검색어 저장 (웹 검색 시 사용)
            original_query = state.get("user_query", "") or state.get("keyword", "")
            if original_query:
                _ORIGINAL_SEARCH_KEYWORD[_set_session_id(session_id)] = original_query
        # 답변에 웹 검색 질문 직접 추가
        answer += "\n\n❓ 웹에서 추가로 검색해드릴까요?\n\n'네' 또는 '예'로 답변하시면 웹에서 추가 정보를 검색하고, '아니오'로 답변하시면 종료됩니다."
        state["next_action"] = "ask_web_search"
    else:
        state["next_action"] = "end"
    
    state["answer"] = answer
    state["progress_status"] = "✅ 답변 생성 완료"
    _notify_progress(state["progress_status"])
    
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


def _parse_selection_numbers(user_text: str) -> Optional[List[int]]:
    """사용자 입력에서 여러 선택 번호 파싱 (예: "1,2,5", "1번,2번,5번", "1 2 5").
    
    Returns:
        파싱된 번호 리스트 (0-based 인덱스), None이면 단일 번호 파싱 시도
    """
    t = user_text.strip()
    
    # 쉼표로 구분된 번호들 (예: "1,2,5,8,10" 또는 "1번,2번,5번")
    if "," in t:
        numbers = []
        parts = [p.strip() for p in t.split(",")]
        for part in parts:
            # "1번" -> 1, "2" -> 2
            match = re.match(r"^(\d+)", part)
            if match:
                num = int(match.group(1))
                numbers.append(num - 1)  # 0-based 인덱스로 변환
        if numbers:
            return numbers
    
    # 공백으로 구분된 번호들 (예: "1 2 5 8 10")
    if re.search(r"\d+\s+\d+", t):
        numbers = []
        matches = re.findall(r"\d+", t)
        for match in matches:
            num = int(match)
            numbers.append(num - 1)  # 0-based 인덱스로 변환
        if len(numbers) > 1:  # 여러 개가 있을 때만 반환
            return numbers
    
    return None


def patent_search(keyword: str, session_id: Optional[str] = None, search_mode: Optional[Literal["server", "web"]] = None, use_server_first: bool = True) -> Dict[str, Any]:
    """특허 검색 메인 함수 (기존 인터페이스 유지).
    
    Args:
        keyword: 검색 키워드 또는 사용자 질문
        session_id: 세션 ID (멀티턴 대화 지원)
        search_mode: 검색 모드 ("server": 서버 기반, "web": 웹 검색, None: 자동 선택)
        use_server_first: 서버 기반 먼저 시도 여부 (True면 서버 실패 시 자동으로 웹 검색)
        
    Returns:
        검색 결과 또는 상세 정보
    """
    # 웹 검색 여부 대기 중이면 사용자 입력을 파싱
    sid = _set_session_id(session_id)
    if _WAITING_FOR_WEB_SEARCH.get(sid, False):
        user_input_lower = keyword.strip().lower()
        if any(k in user_input_lower for k in ["네", "예", "yes", "y", "좋아", "해줘", "해주세요", "웹 검색"]):
            _WAITING_FOR_WEB_SEARCH[sid] = False
            # 원래 검색어 가져오기
            original_query = _ORIGINAL_SEARCH_KEYWORD.get(sid, keyword)
            _ORIGINAL_SEARCH_KEYWORD.pop(sid, None)  # 사용 후 삭제
            # 웹 검색 모드로 재검색
            return patent_search(original_query, session_id=session_id, search_mode="web", use_server_first=False)
        elif any(k in user_input_lower for k in ["아니오", "아니", "no", "n", "안해", "필요없", "괜찮", "안해도"]):
            _WAITING_FOR_WEB_SEARCH[sid] = False
            _ORIGINAL_SEARCH_KEYWORD.pop(sid, None)  # 사용하지 않으므로 삭제
            return {
                "ok": True,
                "message": "알겠습니다. 추가 검색 없이 종료합니다.",
            }
        else:
            return {
                "ok": False,
                "error": "❓ 웹에서 추가로 검색해드릴까요?\n\n'네' 또는 '예'로 답변하시면 웹에서 추가 정보를 검색하고, '아니오'로 답변하시면 종료됩니다.",
            }
    
    # 검색 모드 선택 대기 중이면 사용자 입력을 검색 모드로 파싱
    if is_waiting_for_search_mode(session_id):
        parsed_mode = parse_search_mode_from_input(keyword)
        if parsed_mode:
            set_waiting_for_search_mode(session_id, False)
            set_session_search_mode(session_id, parsed_mode)  # 검색 모드를 세션에 저장
            search_mode = parsed_mode
            # 저장된 검색어로 자동 검색 진행
            pending_keyword = get_pending_search_keyword(session_id)
            # 디버깅: 저장된 검색어 확인
            print(f"🔍 [DEBUG] 검색 모드 선택: session_id={session_id}, mode={parsed_mode}, pending_keyword={pending_keyword}")
            if pending_keyword:
                # 검색어 사용 후 삭제
                _PENDING_SEARCH_KEYWORD.pop(_set_session_id(session_id), None)
                # 재귀 호출로 검색 진행 (search_mode가 설정된 상태로)
                return patent_search(pending_keyword, session_id=session_id, search_mode=search_mode, use_server_first=use_server_first)
            else:
                return {
                    "ok": True,
                    "message": f"✅ '{parsed_mode}' 모드로 설정되었습니다. 검색어를 다시 입력해주세요.",
                    "search_mode_selected": parsed_mode,
                    "requires_new_query": True,
                }
        else:
            return {
                "ok": False,
                "error": "검색 모드를 선택해주세요:\n1. 서버 기반 검색 (빠르지만 제한적)\n2. 웹 검색 (느리지만 광범위)\n\n'1' 또는 '서버', '2' 또는 '웹'으로 답변해주세요.",
            }
    
    # 검색 모드가 명시적으로 전달되지 않았지만 세션에 저장된 검색 모드가 있으면 사용
    if search_mode is None:
        saved_mode = get_session_search_mode(session_id)
        if saved_mode:
            search_mode = saved_mode
    
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
                            "progress_status": f"📥 특허 상세 정보를 가져오는 중: {app_no}",
                            "search_mode": search_mode,
                            "use_server_first": use_server_first,
                            "session_id": session_id,
                        }
                        _notify_progress(initial_state["progress_status"])
                        
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
    
    # 여러 선택 번호 확인 (이전 검색 결과가 있는 경우)
    selection_indices = _parse_selection_numbers(keyword)
    if selection_indices is not None and len(selection_indices) > 0:
        # 여러 특허 선택 처리
        cached_results = _get_search_results(session_id)
        if not cached_results:
            return {
                "ok": False,
                "error": "이전 검색 결과가 없습니다. 먼저 특허를 검색해주세요.",
            }
        
        # 유효한 인덱스만 필터링
        valid_indices = [idx for idx in selection_indices if 0 <= idx < len(cached_results)]
        if not valid_indices:
            return {
                "ok": False,
                "error": f"선택한 번호가 검색 결과 범위를 벗어났습니다. (1-{len(cached_results)}번 중 선택)",
            }
        
        # 여러 특허의 상세 정보를 순차적으로 가져오기
        results = []
        for i, idx in enumerate(valid_indices, 1):
            selected_patent = cached_results[idx]
            
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
                            "selected_indices": [idx],
                            "vector_db_results": [],
                            "pdf_content": "",
                            "pdf_link": "",
                            "answer": "",
                            "next_action": "download_pdf",
                            "progress_status": f"📥 특허 {i}/{len(valid_indices)} 상세 정보를 가져오는 중... ({selected_patent.get('application_number', 'N/A')})",
                            "search_mode": search_mode,
                            "use_server_first": use_server_first,
                            "session_id": session_id,
                        }
                        _notify_progress(initial_state["progress_status"])
                        
                        # PDF 다운로드 및 RAG 답변
                        result = download_pdf_node(initial_state)
                        
                        if not isinstance(result, dict):
                            continue
                        
                        if result.get("next_action") == "rag_answer":
                            result = rag_answer_node(result)
                            if not isinstance(result, dict):
                                continue
                        
                        if result.get("answer"):
                            results.append({
                                "patent": selected_patent,
                                "message": result["answer"],
                                "pdf_link": result.get("pdf_link", ""),
                            })
                        elif result.get("pdf_content") and "실패" not in result.get("pdf_content", ""):
                            results.append({
                                "patent": selected_patent,
                                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\nPDF 다운로드 완료",
                                "pdf_link": result.get("pdf_link", ""),
                            })
                        else:
                            results.append({
                                "patent": selected_patent,
                                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}",
                            })
                except Exception as e:
                    print(f"⚠️ 특허 {i} 상세 처리 오류: {e}")
                    results.append({
                        "patent": selected_patent,
                        "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}",
                    })
            else:
                # LangGraph 미사용 시 기본 정보 반환
                results.append({
                    "patent": selected_patent,
                    "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}",
                })
        
        # 여러 결과를 하나의 메시지로 합치기
        if results:
            message_lines = [f"📋 선택한 {len(results)}개 특허의 상세 정보:\n"]
            for i, res in enumerate(results, 1):
                message_lines.append(f"\n{'='*60}")
                message_lines.append(f"[{i}] {res['patent'].get('application_number', 'N/A')}")
                message_lines.append(f"{res['patent'].get('title', '제목 없음')}")
                message_lines.append(f"\n{res['message']}")
                if res.get('pdf_link'):
                    message_lines.append(f"\n🔗 PDF 링크: {res['pdf_link']}")
            
            return {
                "ok": True,
                "message": "\n".join(message_lines),
                "patents": [r["patent"] for r in results],
                "multiple": True,
            }
        else:
            return {
                "ok": False,
                "error": "선택한 특허의 상세 정보를 가져올 수 없습니다.",
            }
    
    # 단일 선택 번호 확인 (이전 검색 결과가 있는 경우)
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
                            "progress_status": f"📥 선택한 특허의 상세 정보를 가져오는 중...",
                            "search_mode": search_mode,
                            "use_server_first": use_server_first,
                            "session_id": session_id,
                        }
                        _notify_progress(initial_state["progress_status"])
                        
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
    
    # 검색 모드 결정
    # 사용자가 명시적으로 선택하지 않았고, 서버 기반 먼저 시도하는 경우
    # None으로 유지하여 check_db_node에서 검색 모드 선택 질문을 하도록 함
    if search_mode is None and not use_server_first:
        search_mode = "web"
    
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
                    "progress_status": "🔍 특허 검색을 시작합니다...",
                    "search_mode": search_mode,
                    "use_server_first": use_server_first,
                    "session_id": session_id,  # 세션 ID 추가
                }
                
                _notify_progress(initial_state["progress_status"])
                
                result = workflow.invoke(initial_state)
                
                # 검색 모드 선택 질문인 경우
                if result.get("next_action") == "end" and "검색 방식을 선택" in result.get("progress_status", ""):
                    return {
                        "ok": True,
                        "message": "검색 방식을 선택해주세요:\n1. 서버 기반 검색 (빠르지만 제한적)\n2. 웹 검색 (느리지만 광범위)",
                        "requires_user_input": True,
                        "progress_status": result.get("progress_status", ""),
                    }
                
                # 벡터DB 결과가 있으면 RAG 답변 반환
                if result.get("answer"):
                    # PDF 링크 수집
                    pdf_links = []
                    vector_results = result.get("vector_db_results", [])
                    for p in vector_results:
                        pdf_link = p.get('pdf_link', '')
                        if pdf_link:
                            pdf_links.append(f"[{p.get('application_number', 'N/A')}] {p.get('title', '제목 없음')}: {pdf_link}")
                    
                    message = result["answer"]
                    # 이미 answer에 PDF 링크가 포함되어 있는지 확인
                    if pdf_links and "🔗 참고 PDF 링크" not in message and "🔗 PDF 링크" not in message:
                        message += "\n\n🔗 참고 PDF 링크:\n" + "\n".join(pdf_links)
                    
                    # 웹 검색 여부 질문이 이미 answer에 포함되어 있으면 그대로 사용
                    # (rag_answer_node에서 이미 추가되었으므로)
                    
                    return {
                        "ok": True,
                        "message": message,
                        "vector_db_results": vector_results,
                        "progress_status": result.get("progress_status", ""),
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
                        f"🔍 '{extracted_keyword}' 관련 특허 {len(search_results)}개를 찾았습니다.",
                        "",
                        "다음 특허 중에서 자세히 보고 싶은 번호를 선택해주세요:"
                    ]
                    
                    # 특허 목록 추가
                    for item in items:
                        message_lines.append(f"{item['index']}. [{item['application_number']}] {item['title']}")
                        if item.get('abstract'):
                            message_lines.append(f"   {item['abstract']}")
                    
                    message_lines.append("")
                    message_lines.append("💡 자세히 보려면 번호를 입력하세요. 예: '1번', '1,3,5' 또는 '1 3 5'")
                    
                    return {
                        "ok": True,
                        "message": "\n".join(message_lines),
                        "items": items,
                        "search_results": search_results,
                        "progress_status": result.get("progress_status", ""),
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
