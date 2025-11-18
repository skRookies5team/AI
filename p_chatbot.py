# p_chatbot.py — LangGraph 기반 특허 검색 챗봇
import os
import json
from pathlib import Path
from typing import TypedDict, Annotated, Literal
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from pymilvus import connections, Collection
from sentence_transformers import SentenceTransformer

from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter
import requests
import xmltodict
from datetime import datetime

try:
    from PyPDF2 import PdfReader
    PDF_SUPPORTED = True
except ImportError:
    PdfReader = None
    PDF_SUPPORTED = False

# ───────────────────────────────────────────────────────────
# KIPRIS API 설정 (독립적으로 관리)
# ───────────────────────────────────────────────────────────
KIPRIS_API_KEY = "qIbDAYDHWblzGOh7iHbXzOBjflL5ovmtshmsg83xaPQ="

# ───────────────────────────────────────────────────────────
# KIPRIS API 함수들 (p_agent.py와 독립적으로 구현)
# ───────────────────────────────────────────────────────────
def kipris_search(word: str, year: int = 0, rows: int = 3, page: int = 1):
    """KIPRIS 검색 API"""
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
    r = requests.get(url, params=params, timeout=20)
    r.encoding = "utf-8"
    r.raise_for_status()
    return xmltodict.parse(r.text)


def kipris_download_pdf(application_number: str, save_dir: Path) -> tuple[Path | None, dict]:
    """KIPRIS PDF 다운로드"""
    meta_url = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getPubFullTextInfoSearch"
    meta_params = {"applicationNumber": application_number, "ServiceKey": KIPRIS_API_KEY}
    meta_resp = requests.get(meta_url, params=meta_params, timeout=20)
    meta_resp.raise_for_status()

    meta_data = xmltodict.parse(meta_resp.text)
    item = (
        meta_data.get("response", {})
        .get("body", {})
        .get("item")
    ) or {}
    download_url = item.get("path")
    doc_name = item.get("docName") or f"{application_number}.pdf"

    save_dir.mkdir(parents=True, exist_ok=True)
    import re
    safe_name = re.sub(r"[^0-9A-Za-z_.-]", "_", doc_name)
    out_path = save_dir / safe_name

    download_info = {
        "application_number": application_number,
        "doc_name": doc_name,
        "download_url": download_url,
        "pdf_path": str(out_path),
        "pdf_saved": False,
    }

    if not download_url:
        return None, download_info

    headers = {
        "Range": "bytes=0-",
        "User-Agent": "Mozilla/5.0 (compatible; PatentAgent/1.0)",
    }

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

    try:
        out_path.unlink(missing_ok=True)
    except Exception:
        pass

    return None, download_info


def ensure_list(x):
    """리스트/단일 아이템 정규화"""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def clean_text(s: str | None) -> str:
    """텍스트 정리"""
    if not s:
        return ""
    import re
    return re.sub(r"\s+", " ", s).strip()


def extract_pdf_text(pdf_path: Path) -> str:
    """PDF 텍스트 추출"""
    if not PDF_SUPPORTED:
        raise ImportError("PyPDF2 미설치: pip install PyPDF2 로 설치 후 다시 시도하세요.")
    reader = PdfReader(str(pdf_path))
    pages = []
    for idx, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        pages.append(page_text.strip())
    return "\n\n".join(filter(None, pages))


def save_text_file(text_dir: Path, application_number: str, title: str, abstract: str, body: str) -> Path:
    """텍스트 파일 저장"""
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


def save_metadata(meta_dir: Path, metadata: dict) -> Path:
    """메타데이터 저장"""
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{metadata['application_number']}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return meta_path

# 임베딩 모델 초기화
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

# ───────────────────────────────────────────────────────────
# 환경 변수 로드
# ───────────────────────────────────────────────────────────
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("❌ .env 파일에 OPENAI_API_KEY를 설정하세요.")

# ───────────────────────────────────────────────────────────
# Milvus 연결
# ───────────────────────────────────────────────────────────
connections.connect("default", host="127.0.0.1", port="19530")

# ───────────────────────────────────────────────────────────
# 분야별 컬렉션 관리 함수
# ───────────────────────────────────────────────────────────
def extract_keyword_from_text(text: str) -> str:
    """특허 제목/초록에서 주요 키워드 추출"""
    import re
    # 주요 기술 분야 키워드 리스트
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
            # 한글 키워드 우선 반환
            if any(ord(char) >= 0xAC00 and ord(char) <= 0xD7A3 for char in keyword):
                return keyword
            # 영문 키워드는 한글로 변환
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
    
    # 키워드를 찾지 못하면 "기타" 반환
    return "기타"


def get_collection_name(keyword: str) -> str:
    """키워드로 컬렉션 이름 생성"""
    # 영문으로 변환 (컬렉션 이름은 영문 권장)
    keyword_map = {
        "디스플레이": "display",
        "베터리": "battery",
        "배터리": "battery",
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
    }
    
    collection_suffix = keyword_map.get(keyword, keyword.lower().replace(" ", "_"))
    return f"patents_embeddings_{collection_suffix}"


def get_or_create_collection(collection_name: str):
    """컬렉션 가져오기 또는 생성"""
    from pymilvus import FieldSchema, CollectionSchema, DataType, utility
    
    # 컬렉션 스키마 정의
    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="app_no", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="abstract", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),
    ]
    schema = CollectionSchema(fields, description=f"KIPRIS 특허 임베딩 데이터 - {collection_name}")
    
    # 컬렉션이 없으면 생성
    if collection_name not in utility.list_collections():
        collection = Collection(name=collection_name, schema=schema)
        # 인덱스 생성
        collection.create_index(
            field_name="embedding",
            index_params={
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 1024}
            }
        )
        print(f"✅ 새 컬렉션 '{collection_name}' 생성 완료")
    else:
        collection = Collection(collection_name)
    
    collection.load()
    return collection

# ───────────────────────────────────────────────────────────
# LangGraph State 정의
# ───────────────────────────────────────────────────────────
class ChatState(TypedDict):
    user_query: str
    search_results: list[dict]
    selected_patent: dict | None
    vector_db_results: list[dict]
    pdf_content: str
    pdf_link: str
    conversation_history: Annotated[list, lambda x, y: x + y]
    next_action: Literal["check_db", "search_kipris", "download_pdf", "rag_answer", "end"]


# ───────────────────────────────────────────────────────────
# 유틸 함수
# ───────────────────────────────────────────────────────────
def check_vector_db(query: str, limit: int = 5, max_distance: float = 0.8) -> list[dict]:
    """벡터DB에서 유사 특허 검색 (분야별 컬렉션 검색)"""
    try:
        # 검색어에서 키워드 추출
        keyword = extract_keyword_from_text(query)
        collection_name = get_collection_name(keyword)
        
        # 해당 분야 컬렉션 가져오기
        try:
            target_collection = Collection(collection_name)
            target_collection.load()
        except Exception:
            # 컬렉션이 없으면 빈 결과 반환
            return []
        
        query_vec = embedding_model.encode([query])[0].tolist()
        results = target_collection.search(
            data=[query_vec],
            anns_field="embedding",
            param={"metric_type": "L2", "params": {"nprobe": 10}},
            limit=limit * 5,  # 더 많이 가져와서 필터링
            output_fields=["app_no", "title", "abstract", "content"]
        )
        
        found_patents = []
        seen_app_nos = set()
        query_lower = query.lower()
        
        # L2 거리를 유사도로 변환 (거리가 작을수록 유사도 높음)
        # 같은 컬렉션에서는 키워드 매칭이 있으면 거리 임계값을 완화
        for hit in results[0]:
            app_no = hit.entity.get('app_no')
            if not app_no or app_no in seen_app_nos:
                continue
            
            title = hit.entity.get('title', '').lower()
            abstract = hit.entity.get('abstract', '').lower()
            content = hit.entity.get('content', '').lower()
            
            # 검색어를 단어 단위로 분리 (한글/영문 모두)
            import re
            query_words = re.findall(r'[\w가-힣]+', query_lower)
            
            # 검색어가 제목/초록/본문에 포함되어 있는지 확인 (전체 검색어 또는 단어 단위)
            full_match = (query_lower in title or query_lower in abstract or query_lower in content)
            word_match = any(word in title or word in abstract or word in content for word in query_words if len(word) > 1)
            keyword_match = full_match or word_match
            
            # 거리 필터링: 키워드 매칭이 있으면 거리 1.2까지 허용, 없으면 max_distance로 제한
            effective_max_distance = 1.2 if keyword_match else max_distance
            
            if hit.distance > effective_max_distance:
                continue
            
            seen_app_nos.add(app_no)
            similarity = 1.0 / (1.0 + hit.distance)  # 거리를 유사도로 변환
            found_patents.append({
                "application_number": app_no,
                "title": hit.entity.get('title'),
                "abstract": hit.entity.get('abstract'),
                "content": hit.entity.get('content'),
                "score": hit.distance,  # L2 거리 (작을수록 유사)
                "similarity": similarity
            })
        
        # 유사도가 높은 순으로 정렬하고 상위 limit개만 반환
        found_patents.sort(key=lambda x: x["score"])
        return found_patents[:limit]
    except Exception as e:
        print(f"⚠️ 벡터DB 검색 오류: {e}")
        return []


def embed_and_store_patent(app_no: str, title: str, abstract: str, text_path: Path):
    """특허 텍스트를 임베딩하고 분야별 컬렉션에 저장"""
    with open(text_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    
    # 특허 제목과 초록에서 키워드 추출
    combined_text = f"{title} {abstract}"
    keyword = extract_keyword_from_text(combined_text)
    collection_name = get_collection_name(keyword)
    
    # 해당 분야 컬렉션 가져오기 또는 생성
    target_collection = get_or_create_collection(collection_name)
    
    chunks = splitter.split_text(text)
    embeddings = embedding_model.encode(chunks, show_progress_bar=False)
    
    app_nos = [app_no] * len(chunks)
    titles = [title] * len(chunks)
    abstracts = [abstract] * len(chunks)
    
    data = [app_nos, titles, abstracts, chunks, embeddings.tolist()]
    target_collection.insert(data)
    target_collection.flush()
    
    print(f"✅ [{app_no}] '{keyword}' 분야 컬렉션 '{collection_name}'에 저장 완료 ({len(chunks)}개 청크)")
    return len(chunks)


# ───────────────────────────────────────────────────────────
# LangGraph 노드들
# ───────────────────────────────────────────────────────────
def check_db_node(state: ChatState) -> ChatState:
    """벡터DB에 정보가 있는지 확인 (엄격한 유사도 기준)"""
    query = state["user_query"]
    # L2 거리 0.8 이하인 것만 검색 (엄격한 기준)
    results = check_vector_db(query, limit=10, max_distance=0.8)
    
    # 디버깅: 검색 결과 확인
    if not results or len(results) == 0:
        # 더 넓은 범위로 다시 검색해보기 (디버깅용)
        debug_results = check_vector_db(query, limit=10, max_distance=2.0)
        if debug_results and len(debug_results) > 0:
            print(f"\n🔍 벡터DB에 데이터는 있지만 유사도가 낮습니다 (최소 거리: {debug_results[0]['score']:.3f})")
            print(f"   상위 검색 결과:")
            for idx, patent in enumerate(debug_results[:5], 1):
                print(f"   {idx}. [{patent['application_number']}] {patent['title'][:60]}... (거리: {patent['score']:.3f})")
            print(f"\n   검색어 '{query}'가 제목/초록에 포함되어 있는지 확인 중...")
            
            # 검색어가 제목이나 초록에 포함되어 있는지 확인
            query_lower = query.lower()
            relevant_results = []
            for patent in debug_results:
                title = patent.get('title', '').lower()
                abstract = patent.get('abstract', '').lower()
                content = patent.get('content', '').lower()
                # 제목, 초록, 본문 내용에서 검색어 확인
                if query_lower in title or query_lower in abstract or query_lower in content:
                    relevant_results.append(patent)
            
            if relevant_results:
                print(f"   ✅ 검색어가 포함된 특허 {len(relevant_results)}개 발견!")
                for idx, patent in enumerate(relevant_results[:3], 1):
                    print(f"      {idx}. [{patent['application_number']}] {patent['title'][:50]}...")
                # 검색어가 포함된 결과가 있으면 사용
                state["vector_db_results"] = relevant_results[:5]
                state["next_action"] = "rag_answer"
                return state
            else:
                print(f"   ⚠️ 검색어 '{query}'가 제목/초록/본문에 포함되지 않습니다.")
                print(f"   💡 벡터DB에 '{query}' 관련 특허가 저장되어 있지 않을 수 있습니다.")
    
    if results and len(results) > 0:
        # 가장 유사한 결과의 거리 확인
        best_score = results[0]["score"]
        query_lower = query.lower()
        # 검색어를 단어 단위로 분리 (한글/영문 모두)
        import re
        query_words = re.findall(r'[\w가-힣]+', query_lower)
        
        # 키워드 매칭 확인: 전체 검색어 또는 검색어의 단어들이 제목/초록/본문에 포함되어 있는지
        relevant_results = []
        for patent in results:
            title = patent.get('title', '').lower()
            abstract = patent.get('abstract', '').lower()
            content = patent.get('content', '').lower()
            
            # 전체 검색어가 포함되어 있거나, 검색어의 단어 중 하나라도 포함되어 있으면 관련성 높음
            full_match = query_lower in title or query_lower in abstract or query_lower in content
            word_match = any(word in title or word in abstract or word in content for word in query_words if len(word) > 1)
            
            if full_match or word_match:
                relevant_results.append(patent)
        
        # 거리가 0.6 이하면 확실히 관련 있음
        if best_score <= 0.6:
            state["vector_db_results"] = results[:5]  # 상위 5개만
            state["next_action"] = "rag_answer"
        # 거리가 0.6~1.2 사이면 키워드 매칭 확인
        elif best_score <= 1.2:
            if relevant_results:
                # 키워드가 포함된 결과가 있으면 사용 (거리 1.2까지 허용)
                state["vector_db_results"] = relevant_results[:5]
                state["next_action"] = "rag_answer"
            elif best_score <= 0.8:
                # 키워드 매칭이 없지만 거리가 0.8 이하면 사용자에게 확인
                print(f"\n⚠️ 벡터DB에서 유사한 특허를 찾았지만 검색어와 직접적인 관련이 없습니다 (거리: {best_score:.3f})")
                print("다음 특허들이 검색되었습니다:")
                for idx, patent in enumerate(results[:3], 1):
                    print(f"  {idx}. [{patent['application_number']}] {patent['title'][:50]}...")
                
                choice = input("\n💡 이 정보를 사용하시겠습니까? (y/n, Enter=n): ").strip().lower()
                if choice in ['y', 'yes', '예']:
                    state["vector_db_results"] = results[:5]
                    state["next_action"] = "rag_answer"
                else:
                    print("🔄 KIPRIS에서 새로 검색합니다.")
                    state["next_action"] = "search_kipris"
            else:
                # 거리가 0.8~1.2 사이이고 키워드 매칭도 없으면 KIPRIS 검색
                print(f"⚠️ 벡터DB 검색 결과 유사도가 낮습니다 (거리: {best_score:.3f}). KIPRIS에서 새로 검색합니다.")
                state["next_action"] = "search_kipris"
        else:
            # 유사도가 너무 낮으면 KIPRIS 검색
            print(f"⚠️ 벡터DB 검색 결과 유사도가 낮습니다 (거리: {best_score:.3f}). KIPRIS에서 새로 검색합니다.")
            state["next_action"] = "search_kipris"
    else:
        # 결과가 없으면 KIPRIS 검색
        print("ℹ️ 벡터DB에 관련 정보가 없습니다. KIPRIS에서 새로 검색합니다.")
        state["next_action"] = "search_kipris"
    
    return state


def search_kipris_node(state: ChatState) -> ChatState:
    """KIPRIS API로 특허 검색"""
    query = state["user_query"]
    data = kipris_search(query, rows=10)
    
    items = data.get("response", {}).get("body", {}).get("items", {}).get("item")
    items = ensure_list(items)
    
    search_results = []
    for it in items:
        search_results.append({
            "application_number": clean_text(it.get("applicationNumber")),
            "title": clean_text(it.get("inventionTitle")),
            "abstract": clean_text(it.get("astrtCont")),
        })
    
    state["search_results"] = search_results
    state["next_action"] = "end"  # 사용자 선택 대기
    return state


def download_pdf_node(state: ChatState) -> ChatState:
    """선택된 특허의 PDF 다운로드 및 임베딩"""
    patent = state["selected_patent"]
    app_no = patent["application_number"]
    title = patent["title"]
    abstract = patent["abstract"]
    
    base_dir = Path(__file__).resolve().parent
    data_root = base_dir / "data"
    pdf_dir = data_root / "pdf"
    text_dir = data_root / "text"
    meta_dir = data_root / "meta"
    
    # PDF 다운로드
    pdf_path, download_info = kipris_download_pdf(app_no, pdf_dir)
    
    if not pdf_path or not pdf_path.exists():
        state["pdf_content"] = "PDF 다운로드 실패"
        state["pdf_link"] = download_info.get("download_url", "")
        state["next_action"] = "end"
        return state
    
    # 텍스트 추출
    try:
        body = extract_pdf_text(pdf_path)
        text_path = save_text_file(text_dir, app_no, title, abstract, body)
        
        # 임베딩 및 저장
        chunks_count = embed_and_store_patent(app_no, title, abstract, text_path)
        
        # 메타데이터 저장
        metadata = {
            "application_number": app_no,
            "title": title,
            "abstract": abstract,
            "keyword": state["user_query"],
            "pdf": {
                "saved": True,
                "path": str(pdf_path),
                "doc_name": download_info.get("doc_name"),
                "download_url": download_info.get("download_url"),
            },
            "text": {
                "extraction": "completed",
                "path": str(text_path),
                "char_count": len(body),
            },
        }
        save_metadata(meta_dir, metadata)
        
        state["pdf_content"] = body
        state["pdf_link"] = download_info.get("download_url", "")
        state["next_action"] = "rag_answer"
        
    except Exception as e:
        state["pdf_content"] = f"텍스트 추출 실패: {e}"
        state["pdf_link"] = download_info.get("download_url", "")
        state["next_action"] = "end"
    
    return state


def rag_answer_node(state: ChatState) -> ChatState:
    """RAG로 사용자 질문에 답변"""
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)
    
    # 벡터DB 결과 또는 새로 저장된 특허 정보 사용
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
    answer = response.content
    
    state["conversation_history"].append(AIMessage(content=answer))
    state["next_action"] = "end"
    
    return state


# ───────────────────────────────────────────────────────────
# LangGraph 그래프 구성
# ───────────────────────────────────────────────────────────
def should_continue(state: ChatState) -> str:
    """다음 노드 결정"""
    return state["next_action"]


workflow = StateGraph(ChatState)

workflow.add_node("check_db", check_db_node)
workflow.add_node("search_kipris", search_kipris_node)
workflow.add_node("download_pdf", download_pdf_node)
workflow.add_node("rag_answer", rag_answer_node)

workflow.set_entry_point("check_db")

workflow.add_conditional_edges(
    "check_db",
    should_continue,
    {
        "rag_answer": "rag_answer",
        "search_kipris": "search_kipris",
    }
)

workflow.add_edge("search_kipris", END)
workflow.add_edge("download_pdf", "rag_answer")
workflow.add_edge("rag_answer", END)

app = workflow.compile()


# ───────────────────────────────────────────────────────────
# 메인 챗봇 루프
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🤖 특허 검색 챗봇 시작 (종료: 'exit' 또는 'quit' 입력)\n")
    
    while True:
        user_input = input("\n👤 사용자: ").strip()
        
        if user_input.lower() in ["exit", "quit", "종료"]:
            print("👋 챗봇을 종료합니다.")
            break
        
        if not user_input:
            continue
        
        # 초기 상태
        initial_state = {
            "user_query": user_input,
            "search_results": [],
            "selected_patent": None,
            "vector_db_results": [],
            "pdf_content": "",
            "pdf_link": "",
            "conversation_history": [HumanMessage(content=user_input)],
            "next_action": "check_db",
        }
        
        # LangGraph 실행
        result = app.invoke(initial_state)
        
        # 결과 출력
        if result.get("vector_db_results"):
            print("\n✅ 벡터DB에서 관련 특허를 찾았습니다:")
            for idx, patent in enumerate(result["vector_db_results"][:3], 1):
                print(f"\n{idx}. [{patent['application_number']}] {patent['title']}")
                print(f"   초록: {patent['abstract'][:150]}...")
                print(f"   유사도: {patent.get('similarity', 0):.3f} (거리: {patent.get('score', 0):.3f})")
            
            # 사용자에게 선택권 제공
            print("\n" + "="*70)
            choice = input("💡 기존 저장된 정보를 보시겠습니까? (y/n, Enter=기본값 y): ").strip().lower()
            
            if choice in ['', 'y', 'yes', '예']:
                # 기존 벡터DB 결과 사용
                # PDF 링크 가져오기 (메타데이터에서)
                base_dir = Path(__file__).resolve().parent
                meta_dir = base_dir / "data" / "meta"
                
                for patent in result["vector_db_results"][:3]:
                    app_no = patent["application_number"]
                    meta_path = meta_dir / f"{app_no}.json"
                    if meta_path.exists():
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                            pdf_link = meta.get("pdf", {}).get("download_url")
                            if pdf_link:
                                print(f"\n🔗 [{app_no}] PDF 원본 링크: {pdf_link}")
                
                # RAG 답변 생성
                result = rag_answer_node(result)
                if result.get("conversation_history"):
                    last_message = result["conversation_history"][-1]
                    if isinstance(last_message, AIMessage):
                        print(f"\n🤖 AI 답변:\n{last_message.content}")
            else:
                # KIPRIS에서 새로 검색
                print("\n🔄 KIPRIS에서 새로운 정보를 검색합니다...")
                result = search_kipris_node(result)
                
                # KIPRIS 검색 결과 표시 (아래 elif 블록에서 처리)
                if result.get("search_results"):
                    print("\n📋 KIPRIS 검색 결과:")
                    for idx, patent in enumerate(result["search_results"], 1):
                        print(f"\n{idx}. [{patent['application_number']}] {patent['title']}")
                        print(f"   초록: {patent['abstract'][:150]}...")
                    
                    # 사용자 선택 대기 (여러 개 선택 가능)
                    try:
                        choice = input("\n🔢 자세히 볼 특허 번호를 입력하세요 (예: 1 또는 1,2,3 또는 1 2 3, Enter로 건너뛰기): ").strip()
                        if choice:
                            # 쉼표 또는 공백으로 구분된 번호 파싱
                            selected_indices = []
                            for part in choice.replace(',', ' ').split():
                                if part.strip().isdigit():
                                    idx = int(part.strip()) - 1
                                    if 0 <= idx < len(result["search_results"]):
                                        selected_indices.append(idx)
                            
                            if selected_indices:
                                total_count = len(selected_indices)
                                print(f"\n📥 {total_count}개 특허를 선택하셨습니다. 다운로드 및 임베딩을 시작합니다...\n")
                                
                                success_count = 0
                                for i, idx in enumerate(selected_indices, 1):
                                    patent = result["search_results"][idx]
                                    print(f"\n[{i}/{total_count}] {patent['title'][:50]}...")
                                    
                                    result["selected_patent"] = patent
                                    result["next_action"] = "download_pdf"
                                    
                                    # PDF 다운로드 및 임베딩
                                    result = download_pdf_node(result)
                                    
                                    if result.get("pdf_content") and "실패" not in result["pdf_content"]:
                                        print(f"   ✅ PDF 다운로드 및 임베딩 완료!")
                                        print(f"   🔗 원본 링크: {result.get('pdf_link', 'N/A')}")
                                        success_count += 1
                                    else:
                                        print(f"   ⚠️ PDF 다운로드 실패")
                                
                                # 성공/실패 개수 표시
                                if success_count == total_count:
                                    print(f"\n✅ 총 {total_count}개 특허 처리 완료!")
                                else:
                                    print(f"\n✅ 총 {total_count}개 중 {success_count}개 특허 처리 완료 ({total_count - success_count}개 실패)")
                                
                                # 마지막 성공한 특허로 RAG 답변 생성
                                if success_count > 0 and result.get("pdf_content") and "실패" not in result["pdf_content"]:
                                    result = rag_answer_node(result)
                                    print(f"\n🤖 AI 답변:\n{result['conversation_history'][-1].content}")
                                elif success_count == 0:
                                    print("\n⚠️ 모든 특허 다운로드가 실패했습니다.")
                            else:
                                print("⚠️ 유효한 번호를 입력해주세요.")
                    except (ValueError, KeyboardInterrupt):
                        pass
        
        elif result.get("search_results"):
            print("\n📋 KIPRIS 검색 결과:")
            for idx, patent in enumerate(result["search_results"], 1):
                print(f"\n{idx}. [{patent['application_number']}] {patent['title']}")
                print(f"   초록: {patent['abstract'][:150]}...")
            
            # 사용자 선택 대기 (여러 개 선택 가능)
            try:
                choice = input("\n🔢 자세히 볼 특허 번호를 입력하세요 (예: 1 또는 1,2,3 또는 1 2 3, Enter로 건너뛰기): ").strip()
                if choice:
                    # 쉼표 또는 공백으로 구분된 번호 파싱
                    selected_indices = []
                    for part in choice.replace(',', ' ').split():
                        if part.strip().isdigit():
                            idx = int(part.strip()) - 1
                            if 0 <= idx < len(result["search_results"]):
                                selected_indices.append(idx)
                    
                    if selected_indices:
                        total_count = len(selected_indices)
                        print(f"\n📥 {total_count}개 특허를 선택하셨습니다. 다운로드 및 임베딩을 시작합니다...\n")
                        
                        success_count = 0
                        for i, idx in enumerate(selected_indices, 1):
                            patent = result["search_results"][idx]
                            print(f"\n[{i}/{total_count}] {patent['title'][:50]}...")
                            
                            result["selected_patent"] = patent
                            result["next_action"] = "download_pdf"
                            
                            # PDF 다운로드 및 임베딩
                            result = download_pdf_node(result)
                            
                            if result.get("pdf_content") and "실패" not in result["pdf_content"]:
                                print(f"   ✅ PDF 다운로드 및 임베딩 완료!")
                                print(f"   🔗 원본 링크: {result.get('pdf_link', 'N/A')}")
                                success_count += 1
                            else:
                                print(f"   ⚠️ PDF 다운로드 실패")
                        
                        # 성공/실패 개수 표시
                        if success_count == total_count:
                            print(f"\n✅ 총 {total_count}개 특허 처리 완료!")
                        else:
                            print(f"\n✅ 총 {total_count}개 중 {success_count}개 특허 처리 완료 ({total_count - success_count}개 실패)")
                        
                        # 마지막 성공한 특허로 RAG 답변 생성
                        if success_count > 0 and result.get("pdf_content") and "실패" not in result["pdf_content"]:
                            result = rag_answer_node(result)
                            print(f"\n🤖 AI 답변:\n{result['conversation_history'][-1].content}")
                        elif success_count == 0:
                            print("\n⚠️ 모든 특허 다운로드가 실패했습니다.")
                    else:
                        print("⚠️ 유효한 번호를 입력해주세요.")
            except (ValueError, KeyboardInterrupt):
                pass
        
        elif result.get("conversation_history"):
            # RAG 답변 출력
            last_message = result["conversation_history"][-1]
            if isinstance(last_message, AIMessage):
                print(f"\n🤖 AI 답변:\n{last_message.content}")
                if result.get("pdf_link"):
                    print(f"\n🔗 PDF 원본 링크: {result['pdf_link']}")
