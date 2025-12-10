"""특허 검색 메인 에이전트 모듈."""

from __future__ import annotations

import re
import logging
from typing import Optional, Dict, Any, List, Literal

try:
    from langgraph.graph import END
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

from .patent_types import PatentSearchState
from .patent_config import (
    _notify_progress,
    get_search_results,
    save_search_results,
    _set_session_id,
    is_waiting_for_search_mode,
    set_waiting_for_search_mode,
    get_pending_search_keyword,
    get_session_search_mode,
    set_session_search_mode,
    is_waiting_for_web_search,
    set_waiting_for_web_search,
    get_original_search_keyword,
    set_original_search_keyword,
    parse_search_mode_from_input,
    parse_search_mode_with_llm,
    parse_yes_no_with_llm,
)
from .patent_kipris_api import kipris_search
from .patent_pdf_utils import (
    ensure_list,
    clean_text,
)
from .patent_milvus_db import extract_keyword_from_text
from .patent_utils import (
    _extract_keyword,
    _parse_selection_number,
    _parse_selection_numbers,
)
from .patent_nodes import (
    get_workflow,
    download_pdf_node,
    rag_answer_node,
)

LOGGER = logging.getLogger(__name__)


async def patent_search(
    keyword: str,
    session_id: Optional[str] = None,
    search_mode: Optional[Literal["server", "web"]] = None,
    use_server_first: bool = True,
    orchestrator_state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """특허 검색 메인 함수 (기존 인터페이스 유지).
    
    Args:
        keyword: 검색 키워드 또는 사용자 질문
        session_id: 세션 ID (멀티턴 대화 지원)
        search_mode: 검색 모드 ("server": 서버 기반, "web": 웹 검색, None: 자동 선택)
        use_server_first: 서버 기반 먼저 시도 여부 (True면 서버 실패 시 자동으로 웹 검색)
        orchestrator_state: OrchestratorState (Redis 기반 세션 상태)

    Returns:
        검색 결과 또는 상세 정보
    """
    if orchestrator_state and "tech_search_context" in orchestrator_state:
        tech_context = orchestrator_state.get("tech_search_context") or {}
        patent_context = tech_context.get("patent", {})
        last_intent = patent_context.get("intent")
        last_query = patent_context.get("query")
        previous_results = patent_context.get("results", [])
        
        LOGGER.info("orchestrator_state 확인: last_intent=%s, last_query=%s, keyword=%s, results_count=%d",
                   last_intent, last_query, keyword, len(previous_results) if previous_results else 0)
        
        user_input_lower = keyword.strip().lower()
        question_keywords = ["요청한적", "요청한 적", "찾아달라고", "찾아달라고 했", "검색한적", "검색한 적",
                            "이전에", "전에", "했어", "했어?", "했나", "했나?", "요청했", "요청했어", "요청했나"]
        is_question = any(qk in user_input_lower for qk in question_keywords) or user_input_lower.endswith("?") or user_input_lower.endswith("요?")
        
        display_query = last_query or patent_context.get("last_keyword", "특허")
        
        if is_question and previous_results:
            results_count = len(previous_results)
            LOGGER.info("질문 감지 및 이전 결과 확인: is_question=%s, results_count=%d, display_query=%s",
                       is_question, results_count, display_query)
            message = f"네, '{display_query}'에 대한 특허 검색을 요청하셨고, {results_count}개의 특허를 찾았습니다.\n\n"
            message += "찾은 특허 목록:\n"
            for idx, patent in enumerate(previous_results[:5], 1):
                message += f"{idx}. [{patent.get('application_number', 'N/A')}] {patent.get('title', '제목 없음')}\n"
            if results_count > 5:
                message += f"\n... 외 {results_count - 5}개 더 있습니다."
            message += "\n\n자세히 보고 싶은 특허 번호를 선택해주세요."
            
            return {
                "ok": True,
                "message": message,
                "items": [{"index": idx, "application_number": p.get("application_number"), "title": p.get("title"), "abstract": p.get("abstract", "")[:200]} for idx, p in enumerate(previous_results, 1)],
                "search_results": previous_results,
            }
        
        if last_intent == "ask_search_mode" and last_query:
            parsed_mode = await parse_search_mode_with_llm(keyword, session_id)
            LOGGER.info("검색 모드 파싱: keyword=%s, parsed_mode=%s", keyword, parsed_mode)
            if not parsed_mode:
                # LLM 실패 시 사용자에게 재질문
                return {
                    "ok": False,
                    "error": "검색 방식을 이해하지 못했습니다. '서버 기반 검색' 또는 '웹 검색' 중 하나를 선택해주세요.\n\n예시:\n- '내부 데이터에서 찾아볼래' (서버 기반)\n- '인터넷에서 찾아볼래' (웹 검색)"
                }
            if parsed_mode:
                LOGGER.info("이전 쿼리 복원: '%s', 모드: %s", last_query, parsed_mode)
                if orchestrator_state is not None:
                    if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
                        orchestrator_state["tech_search_context"] = {}
                    if "patent" not in orchestrator_state["tech_search_context"]:
                        orchestrator_state["tech_search_context"]["patent"] = {}
                    orchestrator_state["tech_search_context"]["patent"]["intent"] = "search"
                    orchestrator_state["tech_search_context"]["patent"]["query"] = last_query
                
                if session_id:
                    set_waiting_for_search_mode(session_id, False, orchestrator_state=orchestrator_state)
                result = await patent_search(last_query, session_id=session_id, search_mode=parsed_mode, use_server_first=False, orchestrator_state=orchestrator_state)
                LOGGER.info("재귀 호출 결과: ok=%s", result.get('ok'))
                if result:
                    return result

    sid = _set_session_id(session_id)
    if is_waiting_for_web_search(session_id, orchestrator_state):
        answer = await parse_yes_no_with_llm(
            keyword,
            context="웹에서 추가로 검색해드릴까요?"
        )
        
        if answer is True:
            set_waiting_for_web_search(session_id, False, orchestrator_state)
            original_query = get_original_search_keyword(session_id, orchestrator_state) or keyword
            set_original_search_keyword(session_id, None, orchestrator_state)
            return await patent_search(original_query, session_id=session_id, search_mode="web", use_server_first=False, orchestrator_state=orchestrator_state)
        elif answer is False:
            set_waiting_for_web_search(session_id, False, orchestrator_state)
            set_original_search_keyword(session_id, None, orchestrator_state)
            return {
                "ok": True,
                "message": "알겠습니다. 추가 검색 없이 종료합니다.",
            }
        else:
            return {
                "ok": False,
                "error": "❓ 웹에서 추가로 검색해드릴까요?\n\n'네' 또는 '예'로 답변하시면 웹에서 추가 정보를 검색하고, '아니오'로 답변하시면 종료됩니다.",
            }

    if is_waiting_for_search_mode(session_id, orchestrator_state):
        parsed_mode = await parse_search_mode_with_llm(keyword, session_id)
        
        if not parsed_mode:
            # LLM 실패 시 사용자에게 재질문
            return {
                "ok": False,
                "error": "검색 방식을 이해하지 못했습니다. '서버 기반 검색' 또는 '웹 검색' 중 하나를 선택해주세요.\n\n예시:\n- '내부 데이터에서 찾아볼래' (서버 기반)\n- '인터넷에서 찾아볼래' (웹 검색)"
            }
        
        if parsed_mode:
            set_waiting_for_search_mode(session_id, False, orchestrator_state=orchestrator_state)
            set_session_search_mode(session_id, parsed_mode, orchestrator_state)
            search_mode = parsed_mode
            pending_keyword = get_pending_search_keyword(session_id, orchestrator_state)
            LOGGER.info("검색 모드 선택: session_id=%s, mode=%s, pending_keyword=%s", session_id, parsed_mode, pending_keyword)
            if pending_keyword:
                # Redis에서 keyword 삭제
                if orchestrator_state is not None:
                    if "tech_search_context" in orchestrator_state and orchestrator_state["tech_search_context"]:
                        patent_context = orchestrator_state["tech_search_context"].get("patent", {})
                        patent_context.pop("pending_search_keyword", None)
                return await patent_search(pending_keyword, session_id=session_id, search_mode=search_mode, use_server_first=use_server_first, orchestrator_state=orchestrator_state)
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

    if search_mode is None:
        cached_results = get_search_results(session_id, orchestrator_state)
        if cached_results:
            saved_mode = get_session_search_mode(session_id, orchestrator_state)
            if saved_mode:
                search_mode = saved_mode

    patent_number_match = re.search(r'\b(\d{10,})\b', keyword)
    if patent_number_match:
        app_no = patent_number_match.group(1)
        cached_results = get_search_results(session_id, orchestrator_state)

        selected_patent = None
        for patent in cached_results:
            if patent.get("application_number") == app_no:
                selected_patent = patent
                break

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
                LOGGER.exception("특허 번호 검색 오류: %s", e)

        if selected_patent:
            previous_search_keyword = None
            if orchestrator_state and "tech_search_context" in orchestrator_state:
                tech_context = orchestrator_state.get("tech_search_context") or {}
                patent_context = tech_context.get("patent", {})
                previous_search_keyword = patent_context.get("last_keyword") or patent_context.get("query")
                if previous_search_keyword:
                    previous_search_keyword = extract_keyword_from_text(previous_search_keyword)
                    LOGGER.info("이전 검색 키워드 복원: '%s' (특허 번호: %s)", previous_search_keyword, app_no)
            
            if not previous_search_keyword:
                previous_search_keyword = extract_keyword_from_text(f"{selected_patent.get('title', '')} {selected_patent.get('abstract', '')}")
                LOGGER.info("특허 제목/초록에서 키워드 추출: '%s'", previous_search_keyword)
            
            if LANGCHAIN_AVAILABLE:
                try:
                    workflow = get_workflow()
                    if workflow:
                        initial_state: PatentSearchState = {
                            "user_query": keyword,
                            "keyword": previous_search_keyword,
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
                            return {
                                "ok": True,
                                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\n⚠️ {result.get('pdf_content', 'PDF 다운로드 실패')}",
                                "patent": selected_patent,
                                "pdf_link": result.get("pdf_link", ""),
                            }
                except Exception as e:
                    import traceback
                    error_detail = traceback.format_exc()
                    LOGGER.exception("상세 처리 오류: %s", e)
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

    extracted_keyword = _extract_keyword(keyword)
    selection_indices = _parse_selection_numbers(keyword)
    if selection_indices is not None and len(selection_indices) > 0:
        cached_results = get_search_results(session_id, orchestrator_state)
        if not cached_results:
            return {
                "ok": False,
                "error": "이전 검색 결과가 없습니다. 먼저 특허를 검색해주세요.",
            }

        valid_indices = [idx for idx in selection_indices if 0 <= idx < len(cached_results)]
        if not valid_indices:
            return {
                "ok": False,
                "error": f"선택한 번호가 검색 결과 범위를 벗어났습니다. (1-{len(cached_results)}번 중 선택)",
            }

        previous_search_keyword = None
        if orchestrator_state and "tech_search_context" in orchestrator_state:
            tech_context = orchestrator_state.get("tech_search_context") or {}
            patent_context = tech_context.get("patent", {})
            previous_search_keyword = patent_context.get("last_keyword") or patent_context.get("query")
            if previous_search_keyword:
                previous_search_keyword = extract_keyword_from_text(previous_search_keyword)
                LOGGER.info("이전 검색 키워드 복원 (여러 특허): '%s'", previous_search_keyword)
        
        if not previous_search_keyword:
            previous_search_keyword = extracted_keyword
        
        results = []
        for i, idx in enumerate(valid_indices, 1):
            selected_patent = cached_results[idx]

            if LANGCHAIN_AVAILABLE:
                try:
                    workflow = get_workflow()
                    if workflow:
                        initial_state: PatentSearchState = {
                            "user_query": keyword,
                            "keyword": previous_search_keyword,
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
                    LOGGER.exception("특허 %d 상세 처리 오류: %s", i, e)
                    results.append({
                        "patent": selected_patent,
                        "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}",
                    })
            else:
                results.append({
                    "patent": selected_patent,
                    "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}",
                })

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

    selection_index = _parse_selection_number(keyword)
    if selection_index is not None:
        cached_results = get_search_results(session_id, orchestrator_state)
        if cached_results and 0 <= selection_index < len(cached_results):
            selected_patent = cached_results[selection_index]
            
            previous_search_keyword = None
            if orchestrator_state and "tech_search_context" in orchestrator_state:
                tech_context = orchestrator_state.get("tech_search_context") or {}
                patent_context = tech_context.get("patent", {})
                previous_search_keyword = patent_context.get("last_keyword") or patent_context.get("query")
                if previous_search_keyword:
                    previous_search_keyword = extract_keyword_from_text(previous_search_keyword)
                    LOGGER.info("이전 검색 키워드 복원 (단일 특허): '%s'", previous_search_keyword)
            
            if not previous_search_keyword:
                previous_search_keyword = extracted_keyword
            
            if LANGCHAIN_AVAILABLE:
                try:
                    workflow = get_workflow()
                    if workflow:
                        initial_state: PatentSearchState = {
                            "user_query": keyword,
                            "keyword": previous_search_keyword,
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

                        result = download_pdf_node(initial_state)

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
                            return {
                                "ok": True,
                                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}\n\n⚠️ {result.get('pdf_content', 'PDF 다운로드 실패')}",
                                "patent": selected_patent,
                                "pdf_link": result.get("pdf_link", ""),
                            }
                except Exception as e:
                    import traceback
                    LOGGER.exception("상세 처리 오류: %s", e)
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

            return {
                "ok": True,
                "message": f"[{selected_patent.get('application_number', 'N/A')}] {selected_patent.get('title', '제목 없음')}\n\n초록: {selected_patent.get('abstract', '초록 없음')}",
                "patent": selected_patent,
            }
    
    # 검색 모드 결정
    if search_mode is None and not use_server_first:
        search_mode = "web"

    # LangGraph 사용 가능하면 전체 워크플로우 실행
    if LANGCHAIN_AVAILABLE:
        try:
            workflow = get_workflow()
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
                    "session_id": session_id,
                }

                _notify_progress(initial_state["progress_status"])

                # orchestrator_state를 노드 함수에 전달하기 위해 전역 변수에 저장
                from .patent_config import _current_orchestrator_state_for_node
                import agents.master_graphs.tech.patent.patent_config as patent_config_module
                patent_config_module._current_orchestrator_state_for_node = orchestrator_state

                LOGGER.info("LangGraph 워크플로우 실행 시작: search_mode=%s, keyword=%s", search_mode, extracted_keyword)
                try:
                    result = await workflow.ainvoke(initial_state)
                finally:
                    # 전역 변수 정리
                    patent_config_module._current_orchestrator_state_for_node = None
                LOGGER.info("LangGraph 워크플로우 실행 완료: next_action=%s, search_results=%d, answer=%s",
                           result.get('next_action'), len(result.get('search_results', [])), bool(result.get('answer')))
                
                if result.get("next_action") == "end" and "검색 방식을 선택" in result.get("progress_status", "") and search_mode is None:
                    if orchestrator_state is not None:
                        if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
                            orchestrator_state["tech_search_context"] = {}
                        if "patent" not in orchestrator_state["tech_search_context"]:
                            orchestrator_state["tech_search_context"]["patent"] = {}
                        
                        orchestrator_state["tech_search_context"]["patent"]["intent"] = "ask_search_mode"
                        orchestrator_state["tech_search_context"]["patent"]["query"] = keyword
                    
                    return {
                        "ok": True,
                        "message": "검색 방식을 선택해주세요:\n1. 서버 기반 검색 (빠르지만 제한적)\n2. 웹 검색 (느리지만 광범위)",
                        "requires_user_input": True,
                        "progress_status": result.get("progress_status", ""),
                    }

                if result.get("answer"):
                    pdf_links = []
                    vector_results = result.get("vector_db_results", [])
                    for p in vector_results:
                        pdf_link = p.get('pdf_link', '')
                        if pdf_link:
                            pdf_links.append(f"[{p.get('application_number', 'N/A')}] {p.get('title', '제목 없음')}: {pdf_link}")

                    message = result["answer"]
                    if pdf_links and "🔗 참고 PDF 링크" not in message and "🔗 PDF 링크" not in message:
                        message += "\n\n🔗 참고 PDF 링크:\n" + "\n".join(pdf_links)

                    return {
                        "ok": True,
                        "message": message,
                        "vector_db_results": vector_results,
                        "progress_status": result.get("progress_status", ""),
                    }
                
                search_results = result.get("search_results", [])
                if search_results or result.get("next_action") == "end":
                    if search_results:
                        save_search_results(search_results, session_id, orchestrator_state)
                        
                        if orchestrator_state is not None:
                            if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
                                orchestrator_state["tech_search_context"] = {}
                            if "patent" not in orchestrator_state["tech_search_context"]:
                                orchestrator_state["tech_search_context"]["patent"] = {}
                            
                            orchestrator_state["tech_search_context"]["patent"]["results"] = search_results
                            orchestrator_state["tech_search_context"]["patent"]["last_keyword"] = extracted_keyword
                            orchestrator_state["tech_search_context"]["patent"]["query"] = keyword
                            orchestrator_state["tech_search_context"]["patent"]["intent"] = "search"
                        
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
                    else:
                        LOGGER.warning("KIPRIS 검색 결과가 비어있음: query=%s", extracted_keyword)
                        return {
                            "ok": False,
                            "error": f"'{extracted_keyword}' 관련 특허를 찾을 수 없습니다. 다른 키워드로 검색해보세요.",
                            "progress_status": result.get("progress_status", ""),
                        }
        except Exception as e:
            import traceback
            LOGGER.exception("LangGraph 실행 오류: %s", e)
    
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

        save_search_results(search_results, session_id, orchestrator_state)
        
        if orchestrator_state is not None:
            if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
                orchestrator_state["tech_search_context"] = {}
            if "patent" not in orchestrator_state["tech_search_context"]:
                orchestrator_state["tech_search_context"]["patent"] = {}
            
            orchestrator_state["tech_search_context"]["patent"]["results"] = search_results
            orchestrator_state["tech_search_context"]["patent"]["last_keyword"] = extracted_keyword

        return {
            "ok": True,
            "message": f"🔍 '{extracted_keyword}' 관련 특허 {len(search_results)}개를 찾았습니다.",
            "items": items,
            "search_results": search_results,
        }
    except Exception as e:
        LOGGER.exception("특허 검색 중 오류: %s", e)
        return {
            "ok": False,
            "error": f"특허 검색 중 오류가 발생했습니다: {str(e)}",
        }

