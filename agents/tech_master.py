from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from .tech_patent_agent import patent_search
from .llm_router import classify_intent_with_llm

# ScienceON LangGraph 에이전트 사용 (agent.py)
try:
    from .tech_article_agent import run_scienceon_agent, ScienceONAgentState  # type: ignore
    _ARTICLE_AGENT_AVAILABLE = True
    _ARTICLE_AGENT_IMPORT_ERROR: Optional[Exception] = None
except Exception as _e:
    ScienceONAgentState = Dict[str, Any]  # type: ignore[assignment]
    run_scienceon_agent = None  # type: ignore[assignment]
    _ARTICLE_AGENT_AVAILABLE = False
    _ARTICLE_AGENT_IMPORT_ERROR = _e  # type: ignore[assignment]

# 세션별 논문 검색 상태 메모리
_ARTICLE_MEMORY: Dict[str, ScienceONAgentState] = {}

# 선택: Milvus 벡터 DB 저장 지원 (환경에 따라 비활성화 가능)
def _maybe_store_pdf_to_milvus(pdf_path: str) -> None:
    """PDF를 텍스트로 추출해 임베딩 후 Milvus에 저장 (환경변수 설정 시).
    
    필요한 의존성/환경이 없으면 조용히 무시한다.
    """
    import os
    try:
        milvus_uri = os.getenv("MILVUS_URI")
        milvus_token = os.getenv("MILVUS_TOKEN", "")
        collection_name = os.getenv("MILVUS_COLLECTION", "scienceon_papers")
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not (milvus_uri and openai_api_key):
            return
        # 의존성 로드
        from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType, utility
        from langchain_openai import OpenAIEmbeddings
        from pypdf import PdfReader

        # PDF 텍스트 추출
        reader = PdfReader(pdf_path)
        text_chunks: List[str] = []
        for page in reader.pages:
            content = page.extract_text() or ""
            content = content.strip()
            if content:
                # 너무 긴 페이지는 잘라서 저장
                for i in range(0, len(content), 1500):
                    text_chunks.append(content[i:i+1500])
        if not text_chunks:
            return

        # 임베딩
        embeddings = OpenAIEmbeddings(model=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"))
        vectors = embeddings.embed_documents(text_chunks)

        # Milvus 연결/스키마 준비
        connections.connect(alias="default", uri=milvus_uri, token=milvus_token or None)
        if not utility.has_collection(collection_name):
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=8192),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=len(vectors[0])),
                FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=1024),
            ]
            schema = CollectionSchema(fields, description="ScienceON paper chunks")
            coll = Collection(name=collection_name, schema=schema)
            # 기본 IVF_FLAT 인덱스
            coll.create_index(field_name="vector", index_params={"index_type":"IVF_FLAT","metric_type":"IP","params":{"nlist":1024}})
        coll = Collection(collection_name)

        # 데이터 삽입
        sources = [pdf_path] * len(text_chunks)
        coll.insert([text_chunks, vectors, sources], timeout=120)  # type: ignore[arg-type]
        coll.flush()
    except Exception:
        # 조용히 무시 (로깅은 중앙 로거가 있으면 추가 가능)
        return


def has_pending_article_results(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    st = _ARTICLE_MEMORY.get(session_id)
    return bool(st and isinstance(st, dict) and st.get("results"))


def _normalize_items(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in results[:10]:
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


def article_master(user_text: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """LangGraph 기반 ScienceON 논문 검색/다운로드 엔트리.
    
    사용자 채팅 입력을 기반으로 논문 검색 및 PDF 다운로드를 수행합니다.
    supervisor.py → tech_master.py → article_master 경로로 호출됩니다.
    
    Args:
        user_text: 사용자 채팅 입력
            - 검색 예시: "인공지능 논문 찾아줘", "디스플레이 관련 논문 검색"
            - 다운로드 예시: "2번째 논문 pdf 다운받아줘", "첫 번째 논문 다운로드"
            - 숫자만 입력: "2" (이전 검색 결과가 있으면 자동으로 다운로드 명령으로 변환)
        session_id: 세션 ID (멀티턴 대화 지원)
    
    Returns:
        dict: 검색/다운로드 결과
            - ok: 성공 여부
            - intent: "search" 또는 "download"
            - message: 사용자에게 보여줄 메시지
            - summary: AI 요약 (검색 시)
            - items: 정규화된 검색 결과 목록
            - results: 원본 검색 결과 목록
            - link: 참고 링크
    """
    # HTML 폴백 검색기 (에이전트 사용 불가 시)
    def _html_fallback_search(keyword: str) -> Dict[str, Any]:
        import urllib.parse
        import requests
        from bs4 import BeautifulSoup
        base_url = "https://scienceon.kisti.re.kr/srch/selectPORSrchArticleList.do"
        q = urllib.parse.quote(keyword or "", safe="")
        url = f"{base_url}?queryText={q}"
        items: List[Dict[str, Any]] = []
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
            "results": [],
        }

    # 입력 전처리
    t = (user_text or "").strip()
    if not t:
        return {
            "ok": False,
            "intent": "unknown",
            "message": "검색어를 입력해 주세요.",
            "summary": "",
            "link": "https://scienceon.kisti.re.kr/",
            "items": [],
            "results": [],
        }
    
    # 숫자만 입력 + 이전 검색 결과 있으면 다운로드 명령으로 변환
    if _ARTICLE_AGENT_AVAILABLE and t.isdigit() and has_pending_article_results(session_id):
        t = f"{t}번째 논문 pdf 다운받아줘"

    # 에이전트 흐름 (LangGraph 기반)
    if _ARTICLE_AGENT_AVAILABLE and run_scienceon_agent is not None:
        try:
            # 세션 상태 복원
            st: Optional[ScienceONAgentState] = _ARTICLE_MEMORY.get(session_id) if session_id else None
            
            # 에이전트 실행 (사용자 채팅 입력 기반)
            st = run_scienceon_agent(user_input=t, state=st)  # type: ignore[misc]
            
            # 세션 상태 저장
            if session_id:
                _ARTICLE_MEMORY[session_id] = st  # type: ignore[assignment]
            
            # 결과 추출 및 정규화
            results: List[Dict[str, Any]] = st.get("results", [])
            items = _normalize_items(results)
            response = st.get("response") or ""
            summary = st.get("summary") or ""
            intent = st.get("intent") or "unknown"
            error = st.get("error")
            ok = error is None

            # 새로 저장된 PDF가 있으면 Milvus 저장 시도
            for row in results:
                pdf_path = row.get("pdf_path")
                if isinstance(pdf_path, str) and pdf_path:
                    _maybe_store_pdf_to_milvus(pdf_path)

            return {
                "ok": ok,
                "intent": intent,
                "message": response if response else (summary or ""),
                "summary": summary,
                "link": "" if intent == "ask_source" else "https://scienceon.kisti.re.kr/",
                "items": items,
                "results": results,
            }
        except Exception as exc:
            # 에이전트 실행 중 오류 발생 시 폴백으로 전환
            import logging
            logging.exception("article_master 에이전트 실행 중 오류")
            fallback = _html_fallback_search(user_text)
            fallback["ok"] = False
            fallback["message"] = f"{fallback.get('message', '')}\n(에이전트 실행 오류: {str(exc)})"
            return fallback

    # 폴백: HTML 간이 검색 수행 (사용성 향상)
    fallback = _html_fallback_search(user_text)
    # 디버깅을 위해 임포트 에러 메시지를 덧붙임(있을 때만)
    if '_ARTICLE_AGENT_IMPORT_ERROR' in globals() and _ARTICLE_AGENT_IMPORT_ERROR:
        err_text = str(_ARTICLE_AGENT_IMPORT_ERROR)
        fallback["message"] = f"{fallback.get('message','')}\n(에이전트 임포트 실패로 폴백: {err_text})"
    return fallback


@dataclass
class TechIntent:
    kind: Literal["patent", "article"]
    keyword: Optional[str] = None
    details: Optional[str] = None


def _extract_keyword(user_text: str) -> str:
    t = user_text.strip()
    # 매우 단순: 따옴표 안, 또는 '관련' 앞 단어를 키워드로 사용
    for quote in ['"', "'"]:
        if t.count(quote) >= 2:
            first = t.find(quote) + 1
            second = t.find(quote, first)
            if second > first:
                return t[first:second].strip()
    if "관련" in t:
        return t.split("관련")[0].strip()
    return t


def parse_tech_intent(user_text: str, session_id: Optional[str] = None) -> TechIntent:
    """LLM 기반 의도 분류 (키워드 fallback 포함)."""
    t = user_text.strip()
    import re
    
    # 웹 검색 여부 대기 중이면 특허 검색으로 처리 (최우선 체크)
    try:
        from .tech_patent_agent import _WAITING_FOR_WEB_SEARCH, _set_session_id
        if session_id:
            sid = _set_session_id(session_id)
            if _WAITING_FOR_WEB_SEARCH.get(sid, False):
                # "네", "아니오" 같은 단어는 특허 검색으로 라우팅
                if any(k in t.lower() for k in ["네", "예", "yes", "y", "아니오", "아니", "no", "n", "좋아", "해줘", "해주세요", "안해", "필요없", "괜찮", "안해도"]):
                    return TechIntent(kind="patent", keyword=None, details=t)
    except Exception:
        pass

    # 검색 모드 선택 대기 중이면 특허 검색으로 처리 (최우선 체크)
    try:
        from .tech_patent_agent import is_waiting_for_search_mode, parse_search_mode_from_input
        if is_waiting_for_search_mode(session_id):
            # 검색 모드 파싱 시도
            parsed_mode = parse_search_mode_from_input(t)
            if parsed_mode:
                # 검색 모드 선택이면 특허 검색으로 처리
                return TechIntent(kind="patent", keyword=None, details=t)
    except Exception:
        pass

    # 공백으로 구분된 숫자들 (예: "8 10", "1 2 5") - 특허 선택 패턴 (우선 처리)
    if re.match(r"^[\d\s]+$", t) and re.search(r"\d+\s+\d+", t):
        numbers = re.findall(r"\d+", t)
        if len(numbers) > 1:  # 여러 숫자가 있으면
            # 특허 검색 결과가 있으면 특허 검색으로
            from .tech_patent_agent import has_pending_patent_results
            if has_pending_patent_results(session_id):
                return TechIntent(kind="patent", keyword=None, details=t)
            # 특허 검색 결과가 없어도 숫자 선택 패턴이면 특허 검색으로 (기본값)
            return TechIntent(kind="patent", keyword=None, details=t)

    # 쉼표로 구분된 숫자들 (예: "1,2,5") - 특허 선택 패턴 (우선 처리)
    if re.match(r"^[\d\s,]+$", t) and "," in t:
        numbers = re.findall(r"\d+", t)
        if len(numbers) > 1:  # 여러 숫자가 있으면
            # 특허 검색 결과가 있으면 특허 검색으로
            from .tech_patent_agent import has_pending_patent_results
            if has_pending_patent_results(session_id):
                return TechIntent(kind="patent", keyword=None, details=t)
            # 특허 검색 결과가 없어도 숫자 선택 패턴이면 특허 검색으로 (기본값)
            return TechIntent(kind="patent", keyword=None, details=t)

    # 단일 숫자 입력 (예: "4", "1", "10") - 특허 선택 패턴 (우선 처리)
    if re.fullmatch(r"\d+", t):
        # 특허 검색 결과가 있으면 특허 검색으로
        from .tech_patent_agent import has_pending_patent_results
        if has_pending_patent_results(session_id):
            return TechIntent(kind="patent", keyword=None, details=t)
        # 논문 검색 결과가 있으면 논문 검색으로
        if has_pending_article_results(session_id):
            return TechIntent(kind="article", keyword=None, details=t)
        # 검색 결과가 없어도 숫자만 있으면 특허 검색으로 (기본값)
        return TechIntent(kind="patent", keyword=None, details=t)

    # 논문 상세 요청 패턴 체크 (우선 처리)
    # "1번에 대해 알려줘", "1번 상세", "1번 자세히" 같은 패턴
    if re.match(r"^\d+", t) and any(k in t for k in ["번", "에 대해", "에대해", "알려줘", "자세히", "상세"]):
        # 논문 검색 결과가 있으면 article로 분류
        if has_pending_article_results(session_id):
            return TechIntent(kind="article", keyword=None, details=t)
        # 특허 검색 결과가 있으면 patent로 분류
        from .tech_patent_agent import has_pending_patent_results
        if has_pending_patent_results(session_id):
            return TechIntent(kind="patent", keyword=None, details=t)
    
    # LLM 기반 라우팅 시도
    llm_intent = classify_intent_with_llm(t, route_type="tech", fallback_route=None)
    
    if llm_intent == "patent":
        return TechIntent(kind="patent", keyword=_extract_keyword(t), details=t)
    elif llm_intent == "article":
        return TechIntent(kind="article", keyword=_extract_keyword(t), details=t)
    
    # LLM 실패 시 키워드 기반 fallback
    if any(k in t for k in ["특허", "Patent", "patent"]):
        return TechIntent(kind="patent", keyword=_extract_keyword(t), details=t)
    if any(k in t for k in ["논문", "Article", "paper", "학술"]):
        return TechIntent(kind="article", keyword=_extract_keyword(t), details=t)
    # 기본은 특허 조회
    return TechIntent(kind="patent", keyword=_extract_keyword(t), details=t)


def tech_master(user_text: str, session_id: Optional[str] = None) -> dict:
    """Tech 마스터 에이전트: 특허/논문 검색.
    
    Args:
        user_text: 사용자 질의
        session_id: 세션 ID (멀티턴 대화 지원)
    """
    # 빈 입력 처리: 특허 검색 결과가 있으면 이전 결과를 다시 보여줌
    if not user_text.strip():
        try:
            from .tech_patent_agent import has_pending_patent_results, _get_search_results
            if has_pending_patent_results(session_id):
                # 이전 특허 검색 결과를 다시 보여줌
                search_results = _get_search_results(session_id)
                if search_results:
                    items = []
                    for idx, patent in enumerate(search_results, 1):
                        items.append({
                            "index": idx,
                            "application_number": patent.get("application_number", "N/A"),
                            "title": patent.get("title", "제목 없음"),
                            "abstract": patent.get("abstract", "")[:200] + "..." if len(patent.get("abstract", "")) > 200 else patent.get("abstract", ""),
                        })

                    message_lines = [
                        f"🔍 이전 검색 결과를 다시 보여드립니다. ({len(search_results)}개)",
                        "",
                        "다음 특허 중에서 자세히 보고 싶은 번호를 선택해주세요:"
                    ]

                    for item in items:
                        message_lines.append(f"{item['index']}. [{item['application_number']}] {item['title']}")
                        if item.get('abstract'):
                            message_lines.append(f"   {item['abstract']}")

                    message_lines.append("")
                    message_lines.append("💡 자세히 보려면 번호를 입력하세요. 예: '1번', '1,2,5' 또는 '1 2 5'")

                    return {
                        "ok": True,
                        "message": "\n".join(message_lines),
                        "items": items,
                        "search_results": search_results,
                    }
        except Exception:
            pass
        # 특허 검색 결과가 없으면 기본 메시지 반환
        return {
            "ok": False,
            "message": "검색어를 입력해 주세요.",
        }

    intent = parse_tech_intent(user_text, session_id=session_id)
    if intent.kind == "patent":
        return patent_search(intent.keyword or user_text, session_id=session_id)
    if intent.kind == "article":
        # 논문 상세 요청인 경우 원본 텍스트를 그대로 전달
        return article_master(intent.details or intent.keyword or user_text, session_id=session_id)
    # 기본은 특허 검색
    return patent_search(intent.keyword or user_text, session_id=session_id)




