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
from typing import Annotated, Any, Dict, List, Literal, Optional, Sequence, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

import urllib.parse
import requests
from bs4 import BeautifulSoup

from .ai_keyword import generate_search_keyword, generate_summary
from .crawler import download_pdfs_from_results, search_scienceon_papers

load_dotenv()

LOGGER = logging.getLogger(__name__)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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
                "   - 특징: 검색어나 주제가 명시되어 있고, 다운로드 관련 키워드가 없음\n\n"
                "2. download: 이전 검색 결과 중 특정 논문의 PDF 다운로드 요청\n"
                "   - 예시: '2번째 논문 pdf 다운받아줘', '첫 번째 논문 다운로드', '3번 pdf 받아줘'\n"
                "   - 특징: '다운', 'pdf', '받아', '저장', '다운로드' 같은 키워드 + 순번(1번째, 2번째 등)\n"
                "   - 중요: 이전에 검색 결과가 있어야 함 (results_count > 0)\n\n"
                "반드시 아래 형식의 JSON만 반환하세요 (설명 없이 JSON만):\n"
                '{{"intent": "search" 또는 "download", "query": "검색어 또는 빈 문자열", "download_index": 숫자 또는 null}}\n\n'
                "규칙:\n"
                "- intent가 download이면 query는 빈 문자열 \"\"\n"
                "- intent가 search이면 query에 검색어를 넣고 download_index는 null\n"
                "- download_index는 1부터 시작하는 정수 (1번째=1, 2번째=2, 첫 번째=1, 두 번째=2)\n"
                "- 순번이 없으면 download_index는 null"
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

INTENT_CHAIN = INTENT_PROMPT | LLM | StrOutputParser()

class ScienceONAgentState(TypedDict):
    """ScienceON 서브 에이전트의 상태.
    
    통합 마스터 에이전트의 상태에 포함되거나 독립적으로 사용 가능합니다.
    """

    messages: Annotated[List[BaseMessage], "대화 이력"]
    user_input: str
    intent: Literal["search", "download", "detail", "ask_source", "continue_search", "unknown"]
    query: str
    download_index: int
    keyword: str
    results: List[Dict[str, Any]]
    summary: str
    response: str
    error: Optional[str]
    search_source: Literal["web", "internal", "unknown"]  # 검색 소스: 웹 또는 사내 서버
    current_status: str  # 현재 작업 상태 메시지 (딜레이 시 표시용)
    conversation_history: List[Dict[str, Any]]  # 단기 기억용 대화 이력 (나중에 통합하기 쉬운 형식)
    displayed_count: int  # 현재까지 사용자에게 보여준 결과 개수 (페이지네이션용)


# 하위 호환성을 위한 별칭
AgentState = ScienceONAgentState


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


def classify_intent(state: ScienceONAgentState) -> ScienceONAgentState:
    """사용자 입력의 의도를 분류한다."""
    messages = state.get("messages", [])
    user_input = state["user_input"]
    user_input_lower = user_input.lower()
    
    # 이전 검색 결과 개수 확인
    results = state.get("results", [])
    results_count = len(results) if results else 0
    
    # 규칙 기반 분류 (우선 적용)
    download_keywords = ["다운", "pdf", "받아", "저장", "다운로드", "받기", "내려받", "저장해"]
    has_download_keyword = any(keyword in user_input_lower for keyword in download_keywords)
    
    # 규칙 1: "더 찾아줘" 요청 패턴 체크 (우선 처리)
    # "더 찾아줘", "추가 검색", "더 검색" 등 키워드 감지
    continue_search_keywords = ["더 찾아", "더찾아", "추가 검색", "추가검색", "더 검색", "더검색", "더 찾아줘", "더찾아줘"]
    has_continue_search_keyword = any(keyword in user_input_lower for keyword in continue_search_keywords)
    
    # 이전 검색 키워드가 있으면 continue_search로 분류
    previous_keyword = state.get("keyword", "")
    if has_continue_search_keyword and previous_keyword:
        LOGGER.info(
            "규칙 기반 분류: continue_search (이전 키워드=%s)",
            previous_keyword,
        )
        return {
            "intent": "continue_search",
            "query": previous_keyword,
            "download_index": -1,
        }
    
    # 순번 추출 (1번째, 2번째, 첫 번째, 두 번째, 1번, 2번 등)
    number_patterns = [
        (r"(\d+)\s*번째", lambda m: int(m.group(1))),
        (r"(\d+)\s*번", lambda m: int(m.group(1))),
        (r"첫\s*번째", lambda m: 1),
        (r"두\s*번째", lambda m: 2),
        (r"세\s*번째", lambda m: 3),
        (r"네\s*번째", lambda m: 4),
        (r"다섯\s*번째", lambda m: 5),
        (r"(\d+)\s*번째\s*논문", lambda m: int(m.group(1))),
        (r"(\d+)\s*번\s*논문", lambda m: int(m.group(1))),
    ]
    
    detected_index: Optional[int] = None
    for pattern, extractor in number_patterns:
        match = re.search(pattern, user_input_lower)
        if match:
            try:
                detected_index = extractor(match)
                break
            except (ValueError, IndexError, AttributeError):
                continue
    
    # 규칙 2: 상세 정보 요청 패턴 체크 (우선 처리)
    # "1번에 대해 알려줘", "1번 자세히", "1번 상세" 같은 패턴
    detail_keywords = ["에 대해", "에대해", "알려줘", "자세히", "상세", "알려"]
    has_detail_keyword = any(keyword in user_input_lower for keyword in detail_keywords)
    if has_detail_keyword and detected_index is not None and results_count > 0:
        LOGGER.info(
            "규칙 기반 분류: detail (순번=%d, 결과수=%d)",
            detected_index,
            results_count,
        )
        return {
            "intent": "detail",
            "query": "",
            "download_index": detected_index - 1,
        }
    
    # 규칙 3: 다운로드 키워드 + 순번 + 이전 검색 결과가 있으면 → download
    if has_download_keyword and detected_index is not None and results_count > 0:
        LOGGER.info(
            "규칙 기반 분류: download (키워드=%s, 순번=%d, 결과수=%d)",
            has_download_keyword,
            detected_index,
            results_count,
        )
        return {
            "intent": "download",
            "query": "",
            "download_index": detected_index - 1,
        }
    
    # 규칙 4: 다운로드 키워드가 있지만 순번이 없거나 결과가 없으면 → 검색 결과 필요 안내
    if has_download_keyword:
        if results_count == 0:
            LOGGER.info("규칙 기반 분류: download (하지만 검색 결과 없음)")
            return {
                "intent": "download",
                "query": "",
                "download_index": -1,
            }
        elif detected_index is None:
            LOGGER.info("규칙 기반 분류: download (하지만 순번 없음)")
            return {
                "intent": "download",
                "query": "",
                "download_index": -1,
            }
    
    # 규칙 5: 웹/사내 서버 선택 로직 (LLM 호출 전에 명시적 키워드 확인)
    # 이전 검색 결과가 없고 search_source가 없을 때, 명시적 키워드가 있으면 LLM 호출 생략
    search_source = state.get("search_source", "unknown")
    
    if results_count == 0 and search_source == "unknown":
        web_keywords = ["웹", "인터넷", "온라인", "web", "internet"]
        internal_keywords = ["사내", "내부", "서버", "internal", "server"]
        
        has_web_keyword = any(kw in user_input_lower for kw in web_keywords)
        has_internal_keyword = any(kw in user_input_lower for kw in internal_keywords)
        
        if has_web_keyword:
            # 웹 검색 명시 - LLM 호출 없이 바로 검색 의도로 처리
            LOGGER.info("웹 검색 실행 (LLM 호출 생략)")
            return {
                "intent": "search",
                "query": user_input,
                "download_index": -1,
                "search_source": "web",
            }
        elif has_internal_keyword:
            # 사내 서버 검색 명시 - LLM 호출 없이 바로 검색 의도로 처리
            LOGGER.info("사내 서버 검색 실행 (LLM 호출 생략)")
            return {
                "intent": "search",
                "query": user_input,
                "download_index": -1,
                "search_source": "internal",
            }
    
    # 규칙 6: 그 외 모든 경우 → LLM으로 분류
    # 이전 대화 이력 (현재 메시지 제외)
    prior_messages = messages[:-1] if messages else []
    history_text = _render_history(prior_messages)
    
    # LLM으로 의도 분류
    raw_response = ""
    try:
        raw_response = INTENT_CHAIN.invoke({
            "history": history_text,
            "user_input": user_input,
            "results_count": results_count,
        })
        LOGGER.info("LLM 의도 분류 응답: %s", raw_response)
        
        # JSON 추출
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            json_str = re.sub(r'```json\s*', '', raw_response, flags=re.IGNORECASE)
            json_str = re.sub(r'```\s*', '', json_str)
            json_str = json_str.strip()
        
        data = json.loads(json_str)
    except (json.JSONDecodeError, AttributeError, Exception) as e:
        LOGGER.warning("LLM 의도 분류 JSON 파싱 실패: %s, 원본: %s", e, raw_response)
        data = {"intent": "search", "query": user_input, "download_index": None}
    
    intent = str(data.get("intent", "search")).lower().strip()
    if intent not in {"search", "download", "detail"}:
        # "에 대해", "알려줘" 같은 키워드가 있고 순번이 있으면 detail로 분류
        detail_keywords_check = ["에 대해", "에대해", "알려줘", "자세히", "상세", "알려"]
        has_detail_keyword_check = any(keyword in user_input_lower for keyword in detail_keywords_check)
        if has_detail_keyword_check and detected_index is not None and results_count > 0:
            intent = "detail"
        else:
            intent = "search"
    
    # 순번 추출
    download_idx: Optional[int] = None
    llm_index = data.get("download_index")
    if llm_index is not None:
        try:
            if isinstance(llm_index, int):
                download_idx = llm_index - 1
            elif isinstance(llm_index, str) and llm_index.lower() != "null":
                download_idx = int(llm_index) - 1
        except (ValueError, TypeError):
            pass
    
    # LLM이 순번을 제공하지 않았지만 download intent인 경우 정규식으로 추출
    if intent == "download" and download_idx is None:
        user_input_lower = user_input.lower()
        number_patterns = [
            (r"(\d+)\s*번째", lambda m: int(m.group(1))),
            (r"(\d+)\s*번", lambda m: int(m.group(1))),
            (r"첫\s*번째", lambda m: 1),
            (r"두\s*번째", lambda m: 2),
            (r"세\s*번째", lambda m: 3),
            (r"네\s*번째", lambda m: 4),
            (r"다섯\s*번째", lambda m: 5),
        ]
        
        for pattern, extractor in number_patterns:
            match = re.search(pattern, user_input_lower)
            if match:
                try:
                    detected = extractor(match)
                    download_idx = detected - 1
                    LOGGER.info("정규식으로 순번 추출: %d", detected)
                    break
                except (ValueError, IndexError, AttributeError):
                    continue
    
    # query 처리
    query_candidate = data.get("query", "")
    if intent == "download":
        query_value = ""
    else:
        query_value = (
            query_candidate.strip()
            if isinstance(query_candidate, str) and query_candidate.strip()
            else user_input
        )
    
    # 웹/사내 서버 선택 역질문 로직 (LLM 분류 후)
    # LLM이 search로 분류했고, 이전 검색 결과가 없으며 search_source가 없고, 명시적 키워드도 없었을 때 역질문
    if intent == "search" and results_count == 0 and search_source == "unknown":
        # 명시되지 않았으면 역질문
        LOGGER.info("검색 소스가 명시되지 않았습니다.")
        return {
            "intent": "ask_source",
            "query": query_value,
            "download_index": download_idx if download_idx is not None else -1,
        }
    
    LOGGER.info(
        "의도 분류 결과: intent=%s, query=%s, download_index=%s, search_source=%s",
        intent,
        query_value[:50] if query_value else "",
        download_idx if download_idx is not None else "None",
        search_source,
    )
    
    return {
        "intent": intent,
        "query": query_value,
        "download_index": download_idx if download_idx is not None else -1,
    }


def search_internal_papers(state: ScienceONAgentState) -> ScienceONAgentState:
    """사내 서버(벡터 DB 등)에서 논문을 검색한다."""
    query = state.get("query", "").strip()
    if not query:
        return {
            "error": "검색어가 없습니다.",
            "results": [],
            "summary": "검색어를 입력해 주세요.",
        }
    
    try:
        keyword = generate_search_keyword(query)
        LOGGER.info("사내 서버에서 논문 검색 시작: keyword=%s", keyword)
        
        # TODO: 실제 벡터 DB 연결 및 검색 구현
        # 현재는 플레이스홀더로 빈 결과 반환
        # 예시:
        # from pymilvus import Collection
        # collection = Collection("papers_collection")
        # query_embedding = embedding_model.encode([query])[0]
        # results = collection.search(data=[query_embedding], ...)
        
        # 사내 서버 검색 결과가 없는 경우
        results = []
        LOGGER.info("사내 서버 검색 결과: %d건", len(results))
        
        return {
            "keyword": keyword,
            "results": results,
            "summary": "",
            "error": None,
        }
    except Exception as exc:
        LOGGER.exception("사내 서버 논문 검색 중 오류 발생")
        return {
            "error": str(exc),
            "results": [],
            "summary": f"사내 서버 검색 중 오류가 발생했습니다: {str(exc)}",
        }


def search_papers(state: ScienceONAgentState) -> ScienceONAgentState:
    """논문 검색을 수행한다."""
    # 페이지네이션: 이미 검색 결과가 있고 displayed_count > 0이면 다음 결과 가져오기
    displayed_count = state.get("displayed_count", 0)
    results = state.get("results", [])
    
    if displayed_count > 0 and results:
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
        summary = generate_summary(
            user_query=state.get("query", ""),
            keyword=state.get("keyword", ""),
            results=next_results
        )
        
        new_displayed_count = displayed_count + len(next_results)
        LOGGER.info("추가 검색 결과 표시: %d~%d번째 논문 (%d건)", 
                    displayed_count + 1, new_displayed_count, len(next_results))
        
        return {
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
        keyword = generate_search_keyword(query)
        
        search_source = state.get("search_source", "unknown")
        if search_source == "unknown":
            return {
                "error": "검색 소스가 명시되지 않았습니다.",
                "results": [],
                "summary": "검색 소스를 명시해 주세요.",
            }

        # 사내 서버 검색인 경우
        if search_source == "internal":
            LOGGER.info("사내 서버에서 검색 수행: query=%s", query)
            # state에 keyword 설정 후 전달
            state["keyword"] = keyword
            internal_result = search_internal_papers(state)
            
            # 사내 서버 결과가 없으면 자동으로 웹 검색으로 전환
            if not internal_result.get("results"):
                LOGGER.info("사내 서버 검색 결과가 없어 웹 검색으로 전환")
                # search_source를 web으로 변경하고 웹 검색 수행
                state["search_source"] = "web"
                return search_papers(state)
            
            return internal_result
        
        # 웹 검색인 경우
        # 처음 검색 시 더 많은 결과를 가져오기 (기본값 limit=10)
        all_results = search_scienceon_papers(keyword, enable_downloads=False)
        
        # 처음 3개만 표시하고 summary 생성
        displayed_results = all_results[:3]
        summary = generate_summary(user_query=query, keyword=keyword, results=displayed_results)
        
        LOGGER.info("웹 논문 검색 완료: 전체 %d건, 표시 %d건", len(all_results), len(displayed_results))
        
        return {
            "keyword": keyword,
            "results": all_results,  # 전체 결과 저장
            "summary": summary,
            "error": None,
            "displayed_count": len(displayed_results),
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
    results = state.get("results", [])
    if not results:
        return {
            "error": "먼저 논문을 검색한 뒤에 상세 정보를 요청해 주세요.",
            "response": "먼저 논문을 검색한 뒤에 상세 정보를 요청해 주세요.",
        }
    
    index = state.get("download_index", -1)
    if index < 0 or index >= len(results):
        available_indices = ", ".join([str(i + 1) for i in range(len(results))])
        error_msg = (
            f"요청한 순번의 논문을 찾을 수 없습니다. "
            f"현재 검색 결과는 {len(results)}개입니다. "
            f"사용 가능한 순번: {available_indices}번째."
        )
        return {
            "error": error_msg,
            "response": error_msg,
        }
    
    target_result = results[index]
    title = target_result.get("title", f"{index + 1}번째 논문")
    pdf_link = target_result.get("pdf_link", "")
    pdf_path = target_result.get("pdf_path")
    
    # PDF가 이미 다운로드되어 있지 않으면 다운로드 시도
    if not pdf_path or not os.path.exists(pdf_path):
        if not pdf_link or pdf_link == "원문보기 링크 없음":
            error_msg = f"{index + 1}번째 논문('{title}')의 원문 링크가 없어 상세 정보를 제공할 수 없습니다."
            return {
                "error": error_msg,
                "response": error_msg,
            }
        
        try:
            LOGGER.info("PDF 다운로드 시작 (상세 정보용): %s번째 논문 - %s", index + 1, title)
            updated = download_pdfs_from_results([target_result])
            if updated and len(updated) > 0:
                results[index] = updated[0]
                pdf_path = updated[0].get("pdf_path")
                if not pdf_path:
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
        from pypdf import PdfReader
        from langchain_openai import ChatOpenAI
        
        # pypdf의 인코딩 경고 메시지 필터링
        warnings.filterwarnings("ignore", message=".*Advanced encoding.*not implemented yet.*")
        
        reader = PdfReader(pdf_path)
        text_chunks: List[str] = []
        for page in reader.pages:
            content = page.extract_text() or ""
            content = content.strip()
            if content:
                text_chunks.append(content)
        
        if not text_chunks:
            error_msg = f"{index + 1}번째 논문('{title}')의 PDF에서 텍스트를 추출할 수 없습니다."
            return {
                "error": error_msg,
                "response": error_msg,
            }
        
        # PDF 텍스트를 청크로 나누어 요약 (너무 길면 앞부분만 사용)
        full_text = "\n\n".join(text_chunks)
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
            "요약:"
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


def download_pdf(state: ScienceONAgentState) -> ScienceONAgentState:
    """특정 논문의 PDF를 다운로드한다."""
    results = state.get("results", [])
    if not results:
        return {
            "error": "먼저 논문을 검색한 뒤에 다운로드를 요청해 주세요.",
            "response": "먼저 논문을 검색한 뒤에 다운로드를 요청해 주세요.",
        }
    
    index = state.get("download_index", -1)
    if index < 0 or index >= len(results):
        available_indices = ", ".join([str(i + 1) for i in range(len(results))])
        error_msg = (
            f"요청한 순번의 논문을 찾을 수 없습니다. "
            f"현재 검색 결과는 {len(results)}개입니다. "
            f"사용 가능한 순번: {available_indices}번째."
        )
        return {
            "error": error_msg,
            "response": error_msg,
        }
    
    target_result = results[index]
    title = target_result.get("title", f"{index + 1}번째 논문")
    pdf_link = target_result.get("pdf_link", "")
    
    if not pdf_link or pdf_link == "원문보기 링크 없음":
        error_msg = f"{index + 1}번째 논문('{title}')의 원문 링크가 없어 다운로드할 수 없습니다."
        return {
            "error": error_msg,
            "response": error_msg,
        }
    
    try:
        LOGGER.info("PDF 다운로드 시작: %s번째 논문 - %s", index + 1, title)
        updated = download_pdfs_from_results([target_result])
        
        if updated and len(updated) > 0:
            results[index] = updated[0]
            pdf_path = updated[0].get("pdf_path")
            if pdf_path:
                response_msg = f"{index + 1}번째 논문('{title}')의 PDF를 저장했습니다.\n파일 경로: {pdf_path}"
                LOGGER.info("PDF 다운로드 성공: %s", pdf_path)
                return {
                    "results": results,
                    "response": response_msg,
                    "error": None,
                }
            else:
                error_msg = f"{index + 1}번째 논문('{title}')의 PDF 다운로드에 실패했습니다."
                return {
                    "error": error_msg,
                    "response": error_msg,
                }
        else:
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


def ask_search_source(state: ScienceONAgentState) -> ScienceONAgentState:
    """사용자에게 웹 또는 사내 서버에서 검색할지 물어본다."""
    query = state.get("query", "").strip()
    messages = state.get("messages", [])
    
    # 사용자 입력에서 검색 소스 선택 확인
    # ask_source로 분류된 경우, 사용자가 이미 질문에 답했을 수도 있음
    user_input = state.get("user_input", "").lower()
    web_keywords = ["웹", "인터넷", "온라인", "web", "internet", "1", "일"]
    internal_keywords = ["사내", "내부", "서버", "internal", "server", "2", "이"]
    
    has_web_keyword = any(kw in user_input for kw in web_keywords)
    has_internal_keyword = any(kw in user_input for kw in internal_keywords)
    
    # 이전 메시지가 2개 이상이면 (사용자 입력 + AI 질문) 사용자가 답변한 것으로 간주
    is_user_response = len(messages) >= 2
    
    if is_user_response:
        # 사용자가 답변한 경우
        if has_web_keyword and not has_internal_keyword:
            # 웹 검색 선택
            LOGGER.info("사용자가 웹 검색을 선택함")
            return {
                "intent": "search",
                "search_source": "web",
                "response": "웹에서 검색을 시작합니다.",
                "current_status": "웹에서 논문을 검색 중입니다...",
            }
        elif has_internal_keyword and not has_web_keyword:
            # 사내 서버 검색 선택
            LOGGER.info("사용자가 사내 서버 검색을 선택함")
            return {
                "intent": "search",
                "search_source": "internal",
                "response": "사내 서버에서 검색을 시작합니다.",
                "current_status": "사내 서버에서 논문을 검색 중입니다...",
            }
        else:
            # 명확하지 않으면 다시 질문
            response_msg = (
                "어디서 검색하시겠습니까?\n"
                "1. 웹에서 검색\n"
                "2. 사내 서버에서 검색\n\n"
                f"검색어: {query}"
            )
            return {
                "intent": "ask_source",
                "response": response_msg,
                "current_status": "검색 소스 선택 대기 중...",
            }
    else:
        # 첫 질문이므로 역질문
        response_msg = (
            "어디서 검색하시겠습니까?\n\n"
            "1. 웹에서 검색\n"
            "2. 사내 서버에서 검색"
            # f"검색어: {query}"
        )
        return {
            "intent": "ask_source",
            "response": response_msg,
            "current_status": "검색 소스 선택 대기 중...",
        }


def generate_response(state: ScienceONAgentState) -> ScienceONAgentState:
    """사용자에게 응답 메시지를 생성한다."""
    intent = state.get("intent", "unknown")
    error = state.get("error")
    
    if error:
        response = error
    elif intent == "search":
        summary = state.get("summary", "")
        displayed_count = state.get("displayed_count", 0)
        total_results = len(state.get("results", []))
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
    elif intent == "download":
        response = state.get("response", "요청을 처리했습니다.")
    elif intent == "detail":
        response = state.get("response", "논문 상세 정보를 제공합니다.")
    elif intent == "ask_source":
        response = state.get("response", "어디서 검색하시겠습니까?")
    else:
        response = (
            "요청을 이해하지 못했습니다. 예시: '인공지능 논문 찾아줘', "
            "'2번째 논문 pdf 다운받아줘', '1번에 대해 알려줘'."
        )
    
    # 응답을 메시지에 추가
    messages = state.get("messages", [])
    messages.append(AIMessage(content=response))
    
    return {
        "response": response,
        "messages": messages,
    }


def route_intent(state: ScienceONAgentState) -> Literal["search_papers", "download_pdf", "show_article_detail", "ask_search_source", "generate_response"]:
    """의도에 따라 다음 노드를 결정한다."""
    intent = state.get("intent", "unknown")
    if intent == "search" or intent == "continue_search":
        return "search_papers"
    elif intent == "download":
        return "download_pdf"
    elif intent == "detail":
        return "show_article_detail"
    elif intent == "ask_source":
        return "ask_search_source"
    else:
        return "generate_response"


def build_scienceon_agent_graph() -> Any:
    """ScienceON 서브 에이전트 그래프를 생성한다.
    
    Returns:
        CompiledGraph: 컴파일된 LangGraph 그래프.
        통합 마스터 에이전트의 서브 그래프로 사용 가능합니다.
    """
    workflow = StateGraph(ScienceONAgentState)
    
    # 노드 추가
    workflow.add_node("classify_intent", classify_intent)
    workflow.add_node("ask_search_source", ask_search_source)
    workflow.add_node("search_papers", search_papers)
    workflow.add_node("download_pdf", download_pdf)
    workflow.add_node("show_article_detail", show_article_detail)
    workflow.add_node("generate_response", generate_response)
    
    # 엣지 설정
    workflow.set_entry_point("classify_intent")
    workflow.add_conditional_edges(
        "classify_intent",
        route_intent,
        {
            "ask_search_source": "ask_search_source",
            "search_papers": "search_papers",
            "download_pdf": "download_pdf",
            "show_article_detail": "show_article_detail",
            "generate_response": "generate_response",
        },
    )
    # ask_search_source에서 검색 소스를 선택했으면 search_papers로, 아니면 generate_response로
    def route_after_ask_source(state: ScienceONAgentState) -> Literal["search_papers", "generate_response"]:
        intent = state.get("intent", "unknown")
        if intent == "search":
            return "search_papers"
        else:
            return "generate_response"
    
    workflow.add_conditional_edges(
        "ask_search_source",
        route_after_ask_source,
        {
            "search_papers": "search_papers",
            "generate_response": "generate_response",
        },
    )
    workflow.add_edge("search_papers", "generate_response")
    workflow.add_edge("download_pdf", "generate_response")
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
            "current_status": "",
            "conversation_history": state.get("conversation_history", []) if state else [],
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
            "current_status": "",
            "conversation_history": state.get("conversation_history", []) if state else [],
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
        "current_status": "",
        "conversation_history": [],
        "displayed_count": 0,
    }
    
    # 기존 state에서 새 필드가 없으면 기본값 설정
    if "search_source" not in initial_state:
        initial_state["search_source"] = "unknown"
    if "current_status" not in initial_state:
        initial_state["current_status"] = ""
    if "conversation_history" not in initial_state:
        initial_state["conversation_history"] = []
    if "displayed_count" not in initial_state:
        initial_state["displayed_count"] = 0
    
    # 사용자 입력을 메시지에 추가
    messages = initial_state.get("messages", [])
    messages.append(HumanMessage(content=user_input))
    initial_state["messages"] = messages
    initial_state["user_input"] = user_input
    
    # 에이전트 실행 (LangGraph 기반)
    try:
        LOGGER.info("ScienceON 에이전트 실행 시작: user_input=%s", user_input[:100])
        final_state = SCIENCEON_AGENT_GRAPH.invoke(initial_state)
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
            "current_status": "",
            "conversation_history": initial_state.get("conversation_history", []),
            "displayed_count": initial_state.get("displayed_count", 0),
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
    "classify_intent",
    "search_papers",
    "download_pdf",
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
        elif intent == "download":
            print(f"\n{response}")
            # PDF 다운로드 시에는 검색 결과를 출력하지 않음
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


def _search_url(keyword: str) -> str:
    base_url = "https://scienceon.kisti.re.kr/srch/selectPORSrchArticleList.do"
    q = urllib.parse.quote(keyword, safe="")
    return f"{base_url}?queryText={q}"

def _normalize_items_from_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
            items = _normalize_items_from_results(results)
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

            # items 정규화: agent.py 결과 필드를 그대로 보존한다
            items = _normalize_items_from_results(results)

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
                items = _normalize_items_from_results(results)
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
            items = _normalize_items_from_results(results)
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








