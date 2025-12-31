"""LangGraph 노드 함수들."""

from __future__ import annotations

import logging
import re
from typing import List, Dict, Any

from langchain_core.messages import AIMessage, HumanMessage

from agents.master_graphs.tech.article_agent_module.state import (
    # ScienceONAgentState,
    OPENAI_MODEL,
    get_rag_llm,
)
from agents.master_graphs.tech.article_agent_module.utils import (
    validate_user_input as _validate_user_input,
    sanitize_user_input as _sanitize_user_input,
    get_user_friendly_error_message as _get_user_friendly_error_message,
    validate_state as _validate_state,
    render_history as _render_history,
    extract_json_from_text as _extract_json_from_text,
    parse_search_source_keywords as _parse_search_source_keywords,
)
from agents.master_graphs.tech.article_agent_module.keyword import generate_search_keyword, generate_summary
from agents.master_graphs.tech.article_agent_module.crawler import search_scienceon_papers
from agents.master_graphs.tech.article_agent_module.vector_db import (
    extract_keyword_from_text,
    check_vector_db,
    retrieve_paper_chunks_from_milvus,
    convert_vector_db_results_to_paper_results,
    check_paper_exists,
    download_and_vectorize_paper,
)
from agents.master_graphs.tech.article_agent_module.prompts import (
    LLM, INTENT_PROMPT, get_intent_llm, get_article_prompt
)
from agents.state import OrchestratorState

LOGGER = logging.getLogger(__name__)


async def _reset_for_new_search(
    article_ctx: Dict[str, Any],
    user_input: str,
    current_keyword: str = "",
) -> None:
    """새로운 검색 요청을 위해 상태를 리셋하고 키워드를 생성한다.
    
    Args:
        article_ctx: article context 딕셔너리
        user_input: 정제된 사용자 입력
        current_keyword: 현재 keyword (있으면 last_keyword로 저장)
        reset_additional_fields: download_index, error, response 등 추가 필드도 초기화할지 여부
    """
    # 새로운 검색 키워드 생성
    try:
        search_keyword = await generate_search_keyword(user_input)
    except Exception as e:
        LOGGER.warning(f"generate_search_keyword 실패: {e}")
        search_keyword = extract_keyword_from_text(user_input)

    def normalize_keywords(kw: str) -> List[str]:
        """키워드를 정규화하여 정렬된 리스트로 반환"""
        if not kw:
            return []
        return sorted([k.strip().lower() for k in kw.split(',') if k.strip()])

    search_keywords = normalize_keywords(search_keyword)
    current_keywords = normalize_keywords(current_keyword)

    existing_keyword = article_ctx.get("keyword", "")
    existing_keywords = normalize_keywords(existing_keyword)
    
    if existing_keywords == search_keywords and article_ctx.get("last_keyword"):
        LOGGER.info(f"[DEBUG] _reset_for_new_search - 중복 호출 방지: keyword={search_keyword} 이미 설정됨")
        return
    
    if current_keyword and current_keywords != search_keywords:
        article_ctx["last_keyword"] = current_keyword
        LOGGER.info(f"[DEBUG] _reset_for_new_search - last_keyword 저장: {current_keyword} -> keyword: {search_keyword}")
    elif current_keyword and current_keywords == search_keywords:
        # 같은 키워드로 재검색하는 경우: last_keyword는 유지 (이전 검색 키워드 정보 보존)
        LOGGER.info(f"[DEBUG] _reset_for_new_search - 같은 키워드 재검색: keyword={search_keyword}, last_keyword 유지={article_ctx.get('last_keyword', '')}")
    else:
        LOGGER.info(f"[DEBUG] _reset_for_new_search - current_keyword 없음, keyword만 생성: {search_keyword}")
    
    article_ctx["keyword"] = search_keyword

    # 공통 필드 초기화
    article_ctx["results"] = []
    article_ctx["vector_db_results"] = []
    article_ctx["intent"] = "search"
    article_ctx["next_action"] = ""
    article_ctx["last_selected_paper_indices"] = []
    article_ctx["download_index"] = []
    article_ctx["error"] = None
    article_ctx["response"] = ""
    article_ctx["summary"] = ""
    article_ctx["search_source"] = "unknown"


async def analyze_input_node(state: OrchestratorState) -> Dict[str, Any]:
    """사용자 입력을 분석하여 검색 소스를 결정한다."""
    # tech_search_context.article 초기화 및 접근
    if state.get("tech_search_context") is None:
        state["tech_search_context"] = {}
    if state["tech_search_context"].get("article") is None:
        state["tech_search_context"]["article"] = {}
    
    article_ctx = state["tech_search_context"]["article"]
    
    article_ctx["error"] = None
    article_ctx["response"] = ""
    article_ctx["download_index"] = []

    # conversation_history를 messages로 변환
    messages = []
    conversation_history = state.get("conversation_history", [])
    recent_history = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
    
    for msg in recent_history:
        if msg.get("role") == "user":
            messages.append(HumanMessage(content=msg.get("content", "")))
        elif msg.get("role") == "assistant":
            messages.append(AIMessage(content=msg.get("content", "")))
    
    # 현재 사용자 입력
    user_input = state.get("query", "")
    messages.append(HumanMessage(content=user_input))
    
    # article context에서 상태 복원
    keyword = article_ctx.get("keyword", "")
    results = article_ctx.get("results", [])
    last_keyword = article_ctx.get("last_keyword", "")
    intent = article_ctx.get("intent", "unknown")
    next_action = article_ctx.get("next_action", "")
    search_source = article_ctx.get("search_source", "unknown")
    last_selected_paper_indices = article_ctx.get("last_selected_paper_indices", [])

    if next_action == "generate_response":
        article_ctx["next_action"] = ""
        next_action = ""
    
    LOGGER.info(f"[DEBUG] analyze_input_node 상태 복원: intent={intent}, results_count={len(results)}, next_action={next_action}, search_source={search_source}, last_selected_indices={last_selected_paper_indices}")
    
    # 입력 검증
    is_valid, error_msg = _validate_user_input(user_input)
    if not is_valid:
        article_ctx["error"] = error_msg
        article_ctx["response"] = _get_user_friendly_error_message(Exception(error_msg))
        article_ctx["next_action"] = "generate_response"
        return state
    
    # 입력 정제
    user_input = _sanitize_user_input(user_input)
    results_count = len(results) if results else 0
    
    # 중요: 이전 intent가 ask_source인 경우 (선택 질문에 대한 답변)
    # LLM을 먼저 호출하여 새로운 검색 요청인지 확인
    llm_intent_result = None  # LLM 호출 결과 저장 (중복 호출 방지)
    if intent == "ask_source":
        # 단순 선택 답변("1", "2", "1번", "2번" 등)과 키워드 기반 선택 답변("사내에서 검색해줘" 등)을 LLM 호출 전에 먼저 체크
        # LLM이 results_count=0일 때 search로 분류하는 것을 방지하기 위함
        user_input_lower = user_input.lower().strip()
        
        # 1. 단순 선택 패턴 체크
        simple_selection_patterns = [
            r'^1$', r'^2$', r'^1번$', r'^2번$',
            r'^사내$', r'^웹$'
        ]
        is_simple_selection = any(re.match(p, user_input_lower) for p in simple_selection_patterns)
        
        # 2. 키워드 기반 선택 답변 체크 ("사내에서 검색해줘", "웹에서 검색해줘" 등)
        has_web_keyword, has_internal_keyword = _parse_search_source_keywords(user_input)
        is_keyword_selection = has_web_keyword or has_internal_keyword
        
        if is_simple_selection or is_keyword_selection:
            # 선택 답변이므로 LLM 호출 없이 바로 처리
            LOGGER.debug(
                f"[라우팅 디버그] analyze_input_node - intent=ask_source에서 선택 답변 감지: "
                f"'{user_input}' (simple={is_simple_selection}, keyword={is_keyword_selection})"
            )
            article_ctx["next_action"] = "ask_search_source_with_db"
            return state
        
        # LLM을 사용한 intent 분류 (새로운 검색 요청인지 확인)
        try:
            history = _render_history(messages[:-1] if len(messages) > 1 else [])
            previous_keyword = last_keyword if last_keyword else ""
            last_selected_paper_indices_str = (
                ", ".join([str(idx + 1) for idx in last_selected_paper_indices]) 
                if last_selected_paper_indices 
                else "없음"
            )

            # ask_source 상태일 때는 선택 질문 컨텍스트를 프롬프트에 추가
            prompt = INTENT_PROMPT.format_messages(
                results_count=results_count,
                previous_keyword=previous_keyword,
                last_selected_paper_indices_str=last_selected_paper_indices_str,
                user_input=user_input,
                history=history
            )
            
            # system 메시지에 선택 질문 컨텍스트 추가
            # format_messages()는 리스트를 반환하므로 인덱스로 접근
            if len(prompt) > 0 and hasattr(prompt[0], 'content'):
                selection_context = get_article_prompt('intent_classification', 'selection_context')
                if selection_context:
                    prompt[0].content += "\n\n" + selection_context
            
            # LLM이 None이면 get_intent_llm()으로 지연 초기화
            llm = LLM if LLM is not None else get_intent_llm()
            response = await llm.ainvoke(prompt)
            response_text = response.content.strip()
            intent_data = _extract_json_from_text(response_text)
            
            if intent_data:
                llm_intent = intent_data.get("intent", "search")
                llm_query = intent_data.get("query", "")
                llm_download_index_raw = intent_data.get("download_index")
                
                # LLM 호출 결과 저장 (아래 일반 로직에서 재사용)
                llm_intent_result = {
                    "intent": llm_intent,
                    "query": llm_query,
                    "download_index_raw": llm_download_index_raw
                }
                
                if llm_intent == "search":
                    # LLM이 search로 분류했으면 새로운 검색 요청
                    LOGGER.debug(
                        f"[라우팅 디버그] analyze_input_node - intent=ask_source에서 LLM이 search로 분류: '{user_input}' - 리셋"
                    )
                    await _reset_for_new_search(article_ctx, user_input, current_keyword=keyword)
                    results_count = 0
                    results = []
                else:
                    # LLM이 search로 분류하지 않았으면 선택 질문에 대한 답변으로 처리
                    article_ctx["next_action"] = "ask_search_source_with_db"
                    return state
            else:
                # JSON 파싱 실패 시 사용자 입력 분석
                # 새로운 검색 요청인지 확인
                search_keywords = ["논문", "찾아", "검색", "paper", "article", "찾아줘", "찾아주세요"]
                has_search_keyword = any(kw in user_input for kw in search_keywords)
                
                if has_search_keyword:
                    # 새로운 검색 요청으로 처리
                    LOGGER.warning(f"LLM JSON 파싱 실패 (ask_source 처리 중), 검색 키워드 감지 - search로 전환. 원본 응답: {response_text[:200]}")
                    await _reset_for_new_search(article_ctx, user_input, current_keyword=keyword)
                    results_count = 0
                    results = []
                else:
                    # 선택 질문 답변으로 처리
                    LOGGER.warning("LLM JSON 파싱 실패 (ask_source 처리 중), 선택 질문 답변으로 처리")
                    article_ctx["next_action"] = "ask_search_source_with_db"
                    return state
        except Exception as e:
            # LLM 호출 실패 시 사용자 입력 분석
            search_keywords = ["논문", "찾아", "검색", "paper", "article", "찾아줘", "찾아주세요"]
            has_search_keyword = any(kw in user_input for kw in search_keywords)
            
            if has_search_keyword:
                # 새로운 검색 요청으로 처리
                LOGGER.warning(f"LLM intent 분류 실패 (ask_source 처리 중), 검색 키워드 감지 - search로 전환: {e}")
                await _reset_for_new_search(article_ctx, user_input, current_keyword=keyword)
                results_count = 0
                results = []
            else:
                # 선택 질문 답변으로 처리
                LOGGER.warning(f"LLM intent 분류 실패 (ask_source 처리 중), 선택 질문 답변으로 처리: {e}")
                article_ctx["next_action"] = "ask_search_source_with_db"
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
            await _reset_for_new_search(article_ctx, user_input, current_keyword=keyword)
            results_count = 0
            results = []
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
                    old_intent = intent
                    article_ctx["intent"] = "ask_source"
                    LOGGER.debug(
                        f"[라우팅 디버그] analyze_input_node - ask_search_source_with_db 상태에서 '1번' 또는 '2번' 감지 - "
                        f"old_intent={old_intent} -> new_intent=ask_source, "
                        f"next_action=ask_search_source_with_db, user_input={user_input[:50]}"
                    )
                    article_ctx["next_action"] = "ask_search_source_with_db"
                    return state
                else:
                    # 다른 키워드가 있으면 detail로 처리
                    LOGGER.debug(f"ask_search_source_with_db 상태에서 순번 패턴 감지: '{user_input}' -> detail로 처리")
                    article_ctx["next_action"] = ""  # 리셋하여 일반 로직으로 진행
            else:
                # 검색 소스 선택 질문에 대한 답변으로 처리
                # analyze_input_node에서는 그냥 pass하고 ask_search_source_with_db_result에서 처리
                old_intent = intent
                article_ctx["intent"] = "ask_source"
                LOGGER.debug(
                    f"[라우팅 디버그] analyze_input_node - ask_search_source_with_db 상태, 검색 소스 선택 질문 답변으로 처리 - "
                    f"old_intent={old_intent} -> new_intent=ask_source, "
                    f"next_action=ask_search_source_with_db, user_input={user_input[:50]}"
                )
                article_ctx["next_action"] = "ask_search_source_with_db"
                return state
    
    # 새로운 검색 요청 감지: 명확한 검색 키워드가 있고, 
    # 이전 검색 결과와 다른 주제를 요청하는 경우 search로 강제 분류
    search_keywords = ["논문", "찾아", "검색", "paper", "article", "찾아줘", "찾아주세요"]
    has_search_keyword = any(kw in user_input for kw in search_keywords)
    
    # 🔧 수정: 순번 패턴을 먼저 확인 (검색 키워드보다 우선)
    number_patterns = [
        r'(\d+)번',
        r'(\d+)번째',
        r'첫.*번째?',
        r'두.*번째?',
        r'세.*번째?',
        r'마지막',
    ]
    has_index_pattern = any(re.search(p, user_input.lower()) for p in number_patterns)
    
    # 순번 패턴이 있으면 detail 또는 download로 처리 (검색 키워드 무시)
    if has_index_pattern and results_count > 0:
        # download 키워드 확인
        download_keywords = ["다운", "pdf", "받아", "저장", "download", "다운로드"]
        detail_keywords = ["알려", "상세", "정보", "설명", "에 대해", "자세히", "비교", "요약", "결론"]
        
        has_download_keyword = any(kw in user_input.lower() for kw in download_keywords)
        has_detail_keyword = any(kw in user_input.lower() for kw in detail_keywords)
        
        # download 키워드가 있으면 download, detail 키워드가 있거나 둘 다 없으면 detail
        if has_download_keyword:
            intent = "download"
        elif has_detail_keyword or (not has_download_keyword and not has_detail_keyword):
            # detail 키워드가 있거나, 둘 다 없으면 detail로 (순번만 있어도 detail)
            intent = "detail"
        else:
            intent = "detail"  # 기본값
        
        LOGGER.debug(f"순번 패턴 감지 (검색 키워드 무시): '{user_input}' -> {intent}로 처리")
        # 아래 download_index 파싱 로직으로 진행
    # 명확한 검색 키워드가 있고, 순번 패턴이 없으면 새로운 검색으로 간주
    elif has_search_keyword and results_count > 0:
        # 순번 패턴이 없으면 새로운 검색 요청으로 간주
        LOGGER.debug(f"새로운 검색 요청 감지: '{user_input}' - 이전 결과 리셋")
        await _reset_for_new_search(article_ctx, user_input, current_keyword=keyword)
        results = []
        results_count = 0
        
    # LLM을 사용한 intent 분류 (정규식 제거, LLM이 모든 것을 처리)
    # 단, ask_source에서 이미 LLM을 호출한 경우 결과 재사용 (중복 호출 방지)
    if llm_intent_result:
        # ask_source에서 이미 LLM을 호출했으므로 결과 재사용
        intent = llm_intent_result["intent"]
        query = llm_intent_result["query"]
        download_index_raw = llm_intent_result["download_index_raw"]
        LOGGER.debug(f"LLM intent 분류 (재사용): intent={intent}, query={query}")
    else:
        # 일반적인 경우: LLM 호출
        try:
            history = _render_history(messages[:-1] if len(messages) > 1 else [])  # 마지막 사용자 입력 제외
            
            previous_keyword = last_keyword if last_keyword else ""
            last_selected_paper_indices_str = (
                ", ".join([str(idx + 1) for idx in last_selected_paper_indices]) 
                if last_selected_paper_indices 
                else "없음"
            )
            
            prompt = INTENT_PROMPT.format_messages(
                results_count=results_count,
                previous_keyword=previous_keyword,
                last_selected_paper_indices_str=last_selected_paper_indices_str,
                user_input=user_input,
                history=history
            )
            
            # LLM이 None이면 get_intent_llm()으로 지연 초기화
            llm = LLM if LLM is not None else get_intent_llm()
            response = await llm.ainvoke(prompt)
            response_text = response.content.strip()
            intent_data = _extract_json_from_text(response_text)
            
            if intent_data:
                intent = intent_data.get("intent", "search")
                query = intent_data.get("query", "")
                download_index_raw = intent_data.get("download_index")
                
                # Fallback: 순번 패턴이 있는데 LLM이 search로 분류한 경우 detail로 강제 변경
                if results_count > 0 and intent == "search":
                    # 순번 패턴 재확인 (has_index_pattern이 정의되지 않았을 수 있음)
                    number_patterns = [
                        r'(\d+)번',
                        r'(\d+)번째',
                        r'첫.*번째?',
                        r'두.*번째?',
                        r'세.*번째?',
                        r'마지막',
                    ]
                    has_index_pattern = any(re.search(p, user_input.lower()) for p in number_patterns)
                    has_search_keyword_in_fallback = any(kw in user_input for kw in ["찾아", "검색", "찾아줘", "찾아주세요"])
                    
                    if has_index_pattern:
                        # 순번 패턴이 있으면 detail 또는 download로 처리
                        download_keywords = ["다운로드", "다운", "pdf", "받아", "저장", "download"]
                        detail_keywords = ["에 대해", "알려", "설명", "상세", "정보", "비교", "요약", "결론"]
                        
                        has_download_keyword = any(kw in user_input.lower() for kw in download_keywords)
                        has_detail_keyword = any(kw in user_input.lower() for kw in detail_keywords)
                        
                        # download 키워드가 있으면 download, detail 키워드가 있거나 둘 다 없으면 detail
                        if has_download_keyword:
                            intent = "download"
                        elif has_detail_keyword or (not has_download_keyword and not has_detail_keyword):
                            # detail 키워드가 있거나, 둘 다 없으면 detail로 (순번만 있어도 detail)
                            intent = "detail"
                        
                        if intent in ["detail", "download"]:
                            query = ""
                            # 순번 추출
                            user_input_indices = []
                            
                            comma_pattern = r'((?:\d+\s*,\s*)+\d+)\s*번'
                            comma_matches = re.findall(comma_pattern, user_input.lower())
                            for match in comma_matches:
                                # 쉼표로 구분된 모든 숫자 추출
                                nums = re.findall(r'\d+', match)
                                for num_str in nums:
                                    try:
                                        num = int(num_str)
                                        if num > 0:
                                            user_input_indices.append(num - 1)  # 0-based로 변환
                                    except (ValueError, TypeError):
                                        pass
                            
                            number_patterns = [
                                r'(\d+)번',
                                r'(\d+)번째',
                            ]
                            for pattern in number_patterns:
                                matches = re.findall(pattern, user_input.lower())
                                if matches:
                                    for match in matches:
                                        if isinstance(match, tuple):
                                            match = match[0]
                                        try:
                                            num = int(match)
                                            if num > 0:
                                                user_input_indices.append(num - 1)  # 0-based로 변환
                                        except (ValueError, TypeError):
                                            pass
                            
                            user_input_indices = sorted(list(set(user_input_indices)))
                            
                            if user_input_indices:
                                download_index_raw = user_input_indices
                            else:
                                download_index_raw = None
                    elif len(last_selected_paper_indices) > 0 and not has_search_keyword_in_fallback:
                        # 이전에 선택한 논문이 있고, 검색 키워드가 없으면 detail로 분류
                        intent = "detail"
                        query = ""
                        download_index_raw = [idx + 1 for idx in last_selected_paper_indices]
                        LOGGER.debug(
                            f"Fallback: last_selected_paper_indices가 있고 검색 키워드 없음 - "
                            f"'{user_input}' -> detail로 강제 변경, download_index_raw={download_index_raw} (1-based from last_selected_paper_indices={last_selected_paper_indices})"
                        )
                
                # 파싱된 결과 로깅 (디버깅용) - download_index_raw 상세 정보
                LOGGER.debug(
                    f"LLM 파싱 결과 - intent={intent}, query={query}, "
                    f"download_index_raw={download_index_raw} (type: {type(download_index_raw)})"
                )
            else:
                # JSON 파싱 실패 시 기본값
                intent = "search"
                query = user_input
                download_index_raw = None
                LOGGER.warning(f"LLM JSON 파싱 실패, search로 폴백. 원본 응답: {response_text[:200]}")
        except Exception as e:
            LOGGER.warning(f"LLM intent 분류 실패: {e}, search로 폴백")
            intent = "search"
            query = user_input
            download_index_raw = None
    
    # download_index 파싱 (LLM 결과 재사용 여부와 관계없이 동일하게 처리)
    download_index: List[int] = []
    if download_index_raw is not None:
        try:
            import json
            if isinstance(download_index_raw, list):
                # 배열인 경우: 1-based를 0-based로 변환
                download_index = [int(x) - 1 for x in download_index_raw if isinstance(x, (int, float)) and x > 0]
            elif isinstance(download_index_raw, (int, float)):
                # 단일 값인 경우
                if download_index_raw > 0:
                    download_index = [int(download_index_raw) - 1]
            elif isinstance(download_index_raw, str):
                # 문자열인 경우 (예: "[2, 3]" 또는 "2,3" 또는 "2")
                try:
                    # JSON 배열 문자열인 경우
                    parsed = json.loads(download_index_raw)
                    if isinstance(parsed, list):
                        download_index = [int(x) - 1 for x in parsed if isinstance(x, (int, float)) and x > 0]
                    elif isinstance(parsed, (int, float)):
                        download_index = [int(parsed) - 1] if int(parsed) > 0 else []
                except (json.JSONDecodeError, ValueError):
                    # 쉼표로 구분된 숫자 (예: "2, 3" 또는 "2")
                    nums = re.findall(r'\d+', download_index_raw)
                    download_index = [int(x) - 1 for x in nums if int(x) > 0]
        except (ValueError, TypeError, AttributeError) as e:
            LOGGER.warning(f"download_index 파싱 실패: {download_index_raw}, 오류: {e}")
            download_index = []
    
    # intent에 따라 처리
    article_ctx["intent"] = intent
    
    if intent == "detail" or intent == "download":
        if results_count > 0:
            # 사용자 입력에서 직접 순번 추출
            user_input_indices = []
            
            comma_pattern = r'((?:\d+\s*,\s*)+\d+)\s*번'
            comma_matches = re.findall(comma_pattern, user_input.lower())
            for match in comma_matches:
                # 쉼표로 구분된 모든 숫자 추출
                nums = re.findall(r'\d+', match)
                for num_str in nums:
                    try:
                        num = int(num_str)
                        if num > 0:
                            user_input_indices.append(num - 1)  # 0-based로 변환
                    except (ValueError, TypeError):
                        pass
            
            # 그 다음 일반 패턴 처리
            number_patterns = [
                r'(\d+)번',
                r'(\d+)번째',
            ]
            for pattern in number_patterns:
                matches = re.findall(pattern, user_input.lower())
                if matches:
                    for match in matches:
                        if isinstance(match, tuple):
                            match = match[0]
                        try:
                            num = int(match)
                            if num > 0:
                                user_input_indices.append(num - 1)  # 0-based로 변환
                        except (ValueError, TypeError):
                            pass
            
            # 중복 제거 및 정렬
            user_input_indices = sorted(list(set(user_input_indices)))
            
            # 디버그 로그 추가
            LOGGER.info(f"[DEBUG] analyze_input_node - user_input_indices 파싱 결과: {user_input_indices}, download_index (파싱 전): {download_index}")
            
            # LLM이 순번을 누락한 경우 사용자 입력에서 추출한 순번 사용
            if user_input_indices:
                if not download_index or len(user_input_indices) > len(download_index):
                    download_index = user_input_indices
                    article_ctx["download_index"] = download_index
                    LOGGER.info(f"[DEBUG] analyze_input_node - download_index 설정: {download_index} (0-based)")
            
            if not download_index and last_selected_paper_indices:
                download_index = last_selected_paper_indices
                LOGGER.info(f"[DEBUG] analyze_input_node - last_selected_paper_indices를 download_index로 사용: {download_index}")
            
            if download_index:
                LOGGER.info(f"[DEBUG] analyze_input_node - download_index 범위 체크 시작: download_index={download_index}, results_count={results_count}")
                # 인덱스 범위 체크 (Early validation)
                valid_indices = [idx for idx in download_index if 0 <= idx < results_count]
                invalid_indices = [idx for idx in download_index if idx < 0 or idx >= results_count]
                
                LOGGER.info(f"[DEBUG] analyze_input_node - 범위 체크 결과: valid_indices={valid_indices}, invalid_indices={invalid_indices}")
                
                # 일부 인덱스가 범위를 벗어난 경우 에러 메시지 생성
                if invalid_indices:
                    available_indices = ", ".join([str(i + 1) for i in range(results_count)])
                    requested_nums = ", ".join([str(idx + 1) for idx in invalid_indices])
                    error_msg = (
                        f"⚠️ 요청하신 {requested_nums}번째 논문을 찾을 수 없습니다.\n\n"
                        f"현재 검색 결과는 {results_count}개입니다. "
                        f"사용 가능한 순번: {available_indices}번째."
                    )
                    # invalid_indices가 있으면 바로 에러 메시지만 반환 (유효한 논문에 대한 답변 생성 안 함)
                    article_ctx["error"] = error_msg
                    article_ctx["response"] = error_msg
                    article_ctx["next_action"] = "generate_response"
                    article_ctx["download_index"] = []
                    article_ctx["last_selected_paper_indices"] = [] 
                    return state
                
                # 모든 인덱스가 범위를 벗어난 경우 (안전장치)
                if not valid_indices:
                    available_indices = ", ".join([str(i + 1) for i in range(results_count)])
                    requested_nums = ", ".join([str(idx + 1) for idx in download_index])
                    error_msg = (
                        f"죄송합니다. 요청하신 {requested_nums}번째 논문을 찾을 수 없습니다.\n\n"
                        f"현재 검색 결과는 {results_count}개입니다. "
                        f"사용 가능한 순번: {available_indices}번째."
                    )
                    LOGGER.warning(
                        f"analyze_input: download_index 범위 초과 - download_index={download_index}, "
                        f"results_count={results_count}, user_input={user_input}"
                    )
                    article_ctx["error"] = error_msg
                    article_ctx["response"] = error_msg
                    article_ctx["next_action"] = "generate_response"
                    article_ctx["download_index"] = []
                    article_ctx["last_selected_paper_indices"] = [] 
                    # 디버그: 범위 초과 에러 발생 시 state 상태 로깅
                    LOGGER.debug(
                        f"[라우팅 디버그] analyze_input 범위 초과 에러 발생 시 state 상태 - "
                        f"intent={intent}, next_action={article_ctx.get('next_action')}, "
                        f"search_source={search_source}, results_count={results_count}"
                    )
                    return state
                
                article_ctx["download_index"] = valid_indices
                article_ctx["last_selected_paper_indices"] = valid_indices
                LOGGER.debug(
                    f"analyze_input: {intent} intent 설정 - download_index={valid_indices} (0-based), "
                    f"results_count={results_count}, user_input={user_input}"
                )
                
                # download intent는 벡터DB 체크 없이 바로 다운로드로 라우팅
                if intent == "download":
                    article_ctx["next_action"] = "rag_answer"  # rag_answer_node에서 다운로드 처리
                    article_ctx["response"] = ""  # 이전 응답 메시지 초기화 (중복 방지)
                    return state
                
                # detail intent는 rag_answer_node에서 벡터DB 체크 수행 (중복 체크 방지)
                # search_source와 벡터DB 존재 여부에 따라 다른 노드로 라우팅
                # 벡터DB 체크는 rag_answer_node에서 수행하므로 여기서는 바로 라우팅
                article_ctx["next_action"] = "rag_answer"
                # 에러 메시지가 있으면 유지, 없으면 초기화 (중복 방지)
                if not article_ctx.get("error"):
                    article_ctx["response"] = ""  # 이전 응답 메시지 초기화 (중복 방지)
                LOGGER.debug(
                    f"analyze_input: detail intent 설정 - download_index={valid_indices} (0-based), "
                    f"rag_answer로 라우팅 (벡터DB 체크는 rag_answer_node에서 수행)"
                )
                return state
        else:
            article_ctx["download_index"] = []
            article_ctx["last_selected_paper_indices"] = []
            article_ctx["error"] = "검색 결과가 없습니다. 먼저 논문을 검색해주세요."
            article_ctx["response"] = article_ctx["error"]
            article_ctx["next_action"] = "generate_response"
            LOGGER.warning(
                f"analyze_input: {intent} intent인데 results_count=0, last_selected_paper_indices도 없음 - "
                f"user_input={user_input}"
            )
            return state
    
    # 검색 의도인 경우에만 처리
    if intent == "search":
        # 새로운 검색이므로 마지막 선택 논문 인덱스 리셋
        current_keyword = article_ctx.get("keyword", "")
        LOGGER.info(f"[DEBUG] analyze_input_node - _reset_for_new_search 호출 전: current_keyword={current_keyword}, last_keyword={article_ctx.get('last_keyword', '')}")
        await _reset_for_new_search(article_ctx, user_input, current_keyword=current_keyword)
        results = []
        results_count = 0

        # 웹/사내 서버 명시 여부 확인 - 원본 user_input 사용!
        has_web_keyword, has_internal_keyword = _parse_search_source_keywords(user_input)
        
        # 웹/사내 둘 다 동일하게 동작하도록 수정
        if has_web_keyword or has_internal_keyword:
            # 웹 또는 사내 키워드가 있으면 해당 소스로 설정
            if has_web_keyword and not has_internal_keyword:
                article_ctx["search_source"] = "web"
                article_ctx["next_action"] = "search_papers"
            elif has_internal_keyword and not has_web_keyword:
                article_ctx["search_source"] = "internal"
                article_ctx["next_action"] = "check_db"
            else:
                # 둘 다 있으면 기본값 (unknown으로 설정하고 check_db로)
                article_ctx["search_source"] = "unknown"
                article_ctx["next_action"] = "check_db"
        else:
            # 키워드가 없으면 unknown으로 설정하고 check_db로
            article_ctx["search_source"] = "unknown"
            article_ctx["next_action"] = "check_db"
    
    LOGGER.info(f"[DEBUG] analyze_input_node 완료: intent={intent}, next_action={article_ctx.get('next_action')}, search_source={article_ctx.get('search_source')}, results_count={len(results)}")
    
    return state


async def check_db_node(state: OrchestratorState) -> Dict[str, Any]:
    """벡터DB에서 논문 검색."""
    # tech_search_context.article 초기화 및 접근
    if state.get("tech_search_context") is None:
        state["tech_search_context"] = {}
    if state["tech_search_context"].get("article") is None:
        state["tech_search_context"]["article"] = {}
    
    article_ctx = state["tech_search_context"]["article"]
    
    # article context에서 상태 복원
    results = article_ctx.get("results", [])
    intent = article_ctx.get("intent", "unknown")
    search_source = article_ctx.get("search_source", "unknown")
    keyword = article_ctx.get("keyword", "")
    
    # 디버그: check_db_node 노드 진입 시 state 상태 로깅
    LOGGER.debug(
        f"[라우팅 디버그] check_db_node 노드 진입 - "
        f"intent={intent}, next_action={article_ctx.get('next_action')}, "
        f"search_source={search_source}, "
        f"results_count={len(results)}, "
        f"query={state.get('query', '')[:50]}"
    )
    
    # 웹 검색 결과가 이미 있고, detail intent이면 check_db를 건너뛰고 generate_response로
    if search_source == "web" and len(results) > 0 and intent == "detail":
        # 웹 검색 결과가 이미 있고 detail intent이면 check_db를 건너뛰고 generate_response로
        LOGGER.debug(
            f"[라우팅 디버그] check_db_node - 웹 검색 결과 이미 있음, detail intent -> generate_response로 스킵"
        )
        article_ctx["next_action"] = "generate_response"
        return state
    
    # 사용자 입력 가져오기 (OrchestratorState의 최상위 query 필드)
    query = state.get("query", "")
    
    if not keyword:
        current_keyword = article_ctx.get("keyword", "")
        if current_keyword:
            article_ctx["last_keyword"] = current_keyword
            LOGGER.info(f"[DEBUG] check_db_node - 현재 keyword를 last_keyword로 저장: {current_keyword}")
        try:
            keyword = await generate_search_keyword(query)
            article_ctx["keyword"] = keyword
        except Exception as e:
            LOGGER.warning(f"키워드 생성 실패: {e}")
            keyword = extract_keyword_from_text(query)
            article_ctx["keyword"] = keyword
    
    # 벡터DB 검색 (생성된 키워드 전달)
    try:
        results = await check_vector_db(query, limit=5, max_distance=1.5, keyword=keyword)
    except Exception as e:
        LOGGER.error(f"벡터DB 검색 중 예외 발생: {e}")
        error_str = str(e).lower()
        if "milvus" in error_str or "503" in error_str or "channel" in error_str or "connection" in error_str:
            LOGGER.warning(f"벡터DB 연결 오류 감지: {e}")
            if search_source == "internal":
                article_ctx["response"] = "사내 서버 연결에 문제가 발생했습니다. 웹에서 검색을 시도하겠습니다."
            else:
                article_ctx["response"] = "사내 서버 연결에 문제가 발생했습니다. 웹에서 검색을 시작하겠습니다."
            article_ctx["search_source"] = "web"
            article_ctx["next_action"] = "search_papers"
            return state
        results = []
    
    if results and len(results) > 0:
        # 벡터DB에 있음
        article_ctx["vector_db_results"] = results  # 공통으로 설정
        
        if search_source == "internal":
            # 사내 서버 명시됨 → 논문 목록 표시 (웹 검색처럼)
            paper_results = convert_vector_db_results_to_paper_results(results)
            article_ctx["results"] = paper_results
            
            # 논문 목록 요약 생성
            keyword_for_summary = article_ctx.get("keyword", "") if article_ctx.get("keyword") else query
            summary = await generate_summary(
                user_query=query,
                keyword=keyword_for_summary,
                results=paper_results,
                search_source="사내 서버"
            )
            article_ctx["summary"] = summary
            article_ctx["intent"] = "search"
            article_ctx["search_source"] = "internal"
            article_ctx["next_action"] = "generate_response"
            # 디버그: check_db_node에서 next_action 설정
            LOGGER.debug(
                f"[라우팅 디버그] check_db_node - 벡터DB 결과 있음, 사내 서버 명시됨 - "
                f"next_action={article_ctx.get('next_action')}, intent={article_ctx.get('intent')}"
            )
        else:
            # 명시 안 함 → 웹/사내 선택 질문
            article_ctx["next_action"] = "ask_search_source_with_db"
            # 디버그: check_db_node에서 next_action 설정
            LOGGER.debug(
                f"[라우팅 디버그] check_db_node - 벡터DB 결과 있음, 선택 질문 필요 - "
                f"next_action={article_ctx.get('next_action')}, intent={article_ctx.get('intent')}"
            )
    else:
        # 벡터DB에 없음
        if search_source == "internal":
            # 사내 서버 명시됨 → 웹으로 전환 메시지
            LOGGER.debug(
                f"[라우팅 디버그] check_db_node - 벡터DB 결과 없음, 사내 서버 명시됨 → 웹 검색으로 전환"
            )
            article_ctx["response"] = "사내 서버에 정보가 없어서 웹에서 검색을 실행합니다."
            article_ctx["search_source"] = "web"
            article_ctx["next_action"] = "search_papers"
        else:
            # 명시 안 함 → 웹으로 전환 메시지
            LOGGER.debug(
                f"[라우팅 디버그] check_db_node - 벡터DB 결과 없음, 웹 검색으로 전환: "
                f"next_action='search_papers' 설정"
            )
            article_ctx["response"] = "사내 서버에 관련 정보가 없습니다. 웹에서 검색을 시작하겠습니다."
            article_ctx["search_source"] = "web"
            article_ctx["next_action"] = "search_papers"
    
    # 디버그: check_db_node 함수 종료 직전 최종 state 값 로깅
    LOGGER.debug(
        f"[라우팅 디버그] check_db_node - 함수 종료: "
        f"next_action={article_ctx.get('next_action')}, "
        f"search_source={article_ctx.get('search_source')}, "
        f"vector_db_results_count={len(article_ctx.get('vector_db_results', []))}"
    )
    
    LOGGER.info(f"[DEBUG] check_db_node 완료: next_action={article_ctx.get('next_action')}, search_source={article_ctx.get('search_source')}")
    
    return state


async def ask_search_source_with_db_result(state: OrchestratorState) -> Dict[str, Any]:
    """벡터DB에 결과가 있을 때 웹/사내 서버 선택 질문."""
    # tech_search_context.article 초기화 및 접근
    if state.get("tech_search_context") is None:
        state["tech_search_context"] = {}
    if state["tech_search_context"].get("article") is None:
        state["tech_search_context"]["article"] = {}
    
    article_ctx = state["tech_search_context"]["article"]
    
    # conversation_history를 messages로 변환
    messages = []
    conversation_history = state.get("conversation_history", [])
    recent_history = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
    
    for msg in recent_history:
        if msg.get("role") == "user":
            messages.append(HumanMessage(content=msg.get("content", "")))
        elif msg.get("role") == "assistant":
            messages.append(AIMessage(content=msg.get("content", "")))
    
    # 현재 사용자 입력
    user_input = state.get("query", "")
    messages.append(HumanMessage(content=user_input))
    
    # article context에서 상태 복원
    next_action = article_ctx.get("next_action", "")
    intent = article_ctx.get("intent", "unknown")
    results = article_ctx.get("results", [])
    error = article_ctx.get("error")
    
    # 디버그: ask_search_source_with_db_result 노드 진입 시 state 상태 로깅
    LOGGER.debug(
        f"[라우팅 디버그] ask_search_source_with_db_result 노드 진입 - "
        f"intent={intent}, next_action={next_action}, "
        f"search_source={article_ctx.get('search_source')}, "
        f"results_count={len(results)}, "
        f"query={user_input[:50]}"
    )
    
    # 숫자 키워드 추가 확인 (정규표현식으로 단어 경계 확인하여 더 엄격하게)
    user_input_lower = user_input.lower()
    
    # 정규표현식으로 정확히 "2번", "2", "1번", "1"만 매칭 (단어 경계 확인)
    web_patterns = [r'\b2번\b', r'\b2\b', r'^2번$', r'^2$']
    internal_patterns = [r'\b1번\b', r'\b1\b', r'^1번$', r'^1$']
    
    has_web_number = any(re.search(p, user_input_lower) for p in web_patterns)
    has_internal_number = any(re.search(p, user_input_lower) for p in internal_patterns)
    
    has_web_keyword, has_internal_keyword = _parse_search_source_keywords(user_input)
    has_web_keyword = has_web_keyword or has_web_number
    has_internal_keyword = has_internal_keyword or has_internal_number
    
    # 새로운 검색 요청인지 확인
    # next_action이 ask_search_source_with_db이고, 사용자 입력에 선택 키워드가 없으면
    # 이것은 새로운 검색 요청이므로 첫 질문을 표시해야 함
    # 단, detail intent이고 results가 있고 error가 있으면 범위 초과 에러 상황이므로 새로운 검색 요청이 아님
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
        not is_range_error and  # 범위 초과 에러 상황이면 새로운 검색 요청이 아님
        has_search_keyword_in_input  # 검색 키워드가 있어야 새로운 검색 요청
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
        response_msg = get_article_prompt('ask_search_source', 'question')
        article_ctx["response"] = response_msg
        article_ctx["next_action"] = "generate_response"  # 사용자 입력 대기
        # 디버그: intent 변경 전후 로깅
        old_intent = intent
        article_ctx["intent"] = "ask_source"  # 선택 질문 상태임을 표시
        LOGGER.debug(
            f"[라우팅 디버그] ask_search_source_with_db_result - 새로운 검색 요청 감지, intent 변경 - "
            f"old_intent={old_intent} -> new_intent={article_ctx.get('intent')}, "
            f"next_action={article_ctx.get('next_action')}, user_input={user_input[:50]}"
        )
        article_ctx["ask_source_retry_count"] = 0
        return state
    
    # 무한 루프 방지: 재시도 카운터 확인
    MAX_ASK_SOURCE_RETRIES = 3
    retry_count = article_ctx.get("ask_source_retry_count", 0)
    
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
            vector_db_results = article_ctx.get("vector_db_results", [])
            if vector_db_results:
                # 벡터DB 결과를 논문 목록 형식으로 변환
                paper_results = convert_vector_db_results_to_paper_results(vector_db_results)
                article_ctx["results"] = paper_results
                
                # 논문 목록 요약 생성
                query = state.get("query", "")
                keyword = article_ctx.get("keyword", "")
                if not keyword:
                    current_keyword = article_ctx.get("keyword", "")
                    if current_keyword:
                        article_ctx["last_keyword"] = current_keyword
                    try:
                        keyword = await generate_search_keyword(query)
                        article_ctx["keyword"] = keyword
                    except Exception:
                        keyword = extract_keyword_from_text(query)
                        article_ctx["keyword"] = keyword
                
                summary = await generate_summary(
                    user_query=query,
                    keyword=keyword if keyword else query,
                    results=paper_results,
                    search_source="사내 서버"
                )
                article_ctx["summary"] = summary
                article_ctx["intent"] = "search"
                article_ctx["search_source"] = "internal"
                article_ctx["next_action"] = "generate_response"
                article_ctx["ask_source_retry_count"] = 0
                article_ctx["response"] = ""  # 선택 질문 메시지 제거
            else:
                article_ctx["response"] = "벡터DB 검색 결과가 없습니다."
                article_ctx["next_action"] = "generate_response"
        elif has_web_keyword and not has_internal_keyword:
            # 웹 선택 → 웹 검색
            LOGGER.debug(
                f"[라우팅 디버그] ask_search_source_with_db_result - 웹 선택 감지: "
                f"has_web_keyword={has_web_keyword}, user_input='{user_input[:50]}', "
                f"next_action을 'search_papers'로 설정"
            )
            article_ctx["search_source"] = "web"
            article_ctx["next_action"] = "search_papers"
            article_ctx["ask_source_retry_count"] = 0  # 리셋
            article_ctx["response"] = ""  # 선택 질문 메시지 제거
            # intent 제거 (웹 검색으로 전환)
            article_ctx.pop("intent", None)
        else:
            # 명확하지 않으면 다시 질문 (하지만 최대 재시도 횟수 확인)
            if retry_count >= MAX_ASK_SOURCE_RETRIES:
                # 최대 재시도 횟수 초과 → 기본값(사내 서버) 선택
                LOGGER.warning(f"검색 소스 선택 질문이 {retry_count}회 반복되어 기본값(사내 서버) 선택")
                article_ctx["search_source"] = "internal"
                article_ctx["next_action"] = "rag_answer"
                article_ctx["ask_source_retry_count"] = 0  # 리셋
            else:
                response_msg = get_article_prompt('ask_search_source', 'question')
                article_ctx["response"] = response_msg
                article_ctx["next_action"] = "ask_search_source_with_db"
                article_ctx["ask_source_retry_count"] = retry_count + 1
    elif is_user_response and not is_selection_response:
        # 사용자가 답변했지만 선택 키워드가 없는 경우 (명확하지 않은 답변)
        LOGGER.debug(
            f"[라우팅 디버그] ask_search_source_with_db_result - 분기: is_user_response AND NOT is_selection_response"
        )
        if retry_count >= MAX_ASK_SOURCE_RETRIES:
            # 최대 재시도 횟수 초과 → 기본값(사내 서버) 선택
            LOGGER.warning(f"검색 소스 선택 질문이 {retry_count}회 반복되어 기본값(사내 서버) 선택")
            article_ctx["search_source"] = "internal"
            article_ctx["next_action"] = "rag_answer"
            article_ctx["ask_source_retry_count"] = 0  # 리셋
        else:
            # 명확하지 않으면 다시 질문
            response_msg = get_article_prompt('ask_search_source', 'question')
            article_ctx["response"] = response_msg
            article_ctx["next_action"] = "ask_search_source_with_db"
            article_ctx["ask_source_retry_count"] = retry_count + 1
    else:
        # 첫 질문이거나 기타 경우 (이론적으로는 도달하지 않아야 함)
        LOGGER.debug(
            f"[라우팅 디버그] ask_search_source_with_db_result - 분기: else (첫 질문이거나 기타 경우), "
            f"is_user_response={is_user_response}, is_selection_response={is_selection_response}"
        )
        response_msg = get_article_prompt('ask_search_source', 'question')
        article_ctx["response"] = response_msg
        article_ctx["next_action"] = "ask_search_source_with_db"
        article_ctx["ask_source_retry_count"] = 0  # 첫 질문이므로 0으로 설정
    
    # 디버그: 함수 종료 직전 최종 state 값 로깅
    LOGGER.debug(
        f"[라우팅 디버그] ask_search_source_with_db_result - 함수 종료: "
        f"next_action={article_ctx.get('next_action')}, "
        f"search_source={article_ctx.get('search_source')}, "
        f"intent={article_ctx.get('intent')}, "
        f"response_length={len(article_ctx.get('response', ''))}"
    )
    
    LOGGER.info(f"[DEBUG] ask_search_source_with_db_result 완료: next_action={article_ctx.get('next_action')}, search_source={article_ctx.get('search_source')}")
    
    return state


async def rag_answer_node(state: OrchestratorState) -> Dict[str, Any]:
    """RAG로 사용자 질문에 답변 (질문이 없으면 요약 생성)."""
    # tech_search_context.article 초기화 및 접근
    if state.get("tech_search_context") is None:
        state["tech_search_context"] = {}
    if state["tech_search_context"].get("article") is None:
        state["tech_search_context"]["article"] = {}
    
    article_ctx = state["tech_search_context"]["article"]

    article_ctx["error"] = None
    article_ctx["response"] = ""
    intent = article_ctx.get("intent", "unknown")
    if intent in ["detail", "download"]:
        article_ctx["summary"] = ""
    
    # article context에서 상태 복원
    error = article_ctx.get("error")
    results = article_ctx.get("results", [])
    download_index = article_ctx.get("download_index", [])
    intent = article_ctx.get("intent", "unknown")
    search_source = article_ctx.get("search_source", "unknown")
    keyword = article_ctx.get("keyword", "")
    vector_db_results = article_ctx.get("vector_db_results", [])
    last_selected_paper_indices = article_ctx.get("last_selected_paper_indices", [])
    
    # 범위 초과 에러가 있으면 바로 에러 메시지만 반환 (유효한 논문에 대한 답변 생성 안 함)
    range_error = error
    if range_error and "요청하신" in str(range_error) and "번째 논문을 찾을 수 없습니다" in str(range_error):
        # 범위 초과 에러가 있으면 바로 에러 메시지만 반환
        article_ctx["response"] = str(range_error)
        article_ctx["error"] = None
        article_ctx["next_action"] = "generate_response"
        return state
    
    # 범위 초과 에러가 없으면 이전 에러 상태 초기화
    article_ctx["error"] = None
    
    if not OPENAI_MODEL:
        article_ctx["response"] = "OpenAI API 키가 설정되지 않았습니다."
        article_ctx["next_action"] = "generate_response"
        return state
    
    # RAG 답변용 싱글톤 LLM 사용 (async-safe)
    llm = await get_rag_llm()
    if llm is None:
        article_ctx["response"] = "OpenAI API 초기화에 실패했습니다."
        article_ctx["next_action"] = "generate_response"
        return state
    
    if not results:
        article_ctx["response"] = "먼저 논문을 검색한 뒤에 상세 정보를 요청해 주세요."
        article_ctx["next_action"] = "generate_response"
        return state
    
    if not download_index:
        if last_selected_paper_indices:
            download_index = last_selected_paper_indices
            LOGGER.info(f"[DEBUG] rag_answer_node - last_selected_paper_indices 사용: {download_index}")
        else:
            # 폴백: 사용자 입력에서 직접 추출
            user_input = state.get("query", "").strip()
            user_input_indices = []
            
            # 쉼표로 구분된 숫자 패턴 (예: "1, 2번")
            comma_pattern = r'((?:\d+\s*,\s*)+\d+)\s*번'
            comma_matches = re.findall(comma_pattern, user_input.lower())
            for match in comma_matches:
                nums = re.findall(r'\d+', match)
                for num_str in nums:
                    try:
                        num = int(num_str)
                        if num > 0:
                            user_input_indices.append(num - 1)  # 0-based로 변환
                    except (ValueError, TypeError):
                        pass
            
            # 일반 패턴 (예: "1번", "1번째")
            number_patterns = [
                r'(\d+)번',
                r'(\d+)번째',
            ]
            for pattern in number_patterns:
                matches = re.findall(pattern, user_input.lower())
                if matches:
                    for match in matches:
                        if isinstance(match, tuple):
                            match = match[0]
                        try:
                            num = int(match)
                            if num > 0:
                                user_input_indices.append(num - 1)  # 0-based로 변환
                        except (ValueError, TypeError):
                            pass
            
            user_input_indices = sorted(list(set(user_input_indices)))
            
            if user_input_indices:
                download_index = user_input_indices
                article_ctx["download_index"] = download_index
                LOGGER.info(f"[DEBUG] rag_answer_node - 폴백: 사용자 입력에서 추출한 download_index={download_index}")
            else:
                LOGGER.info(f"[DEBUG] rag_answer_node - ⚠️ download_index를 추출할 수 없음: user_input={user_input}")
        
    # download intent이고 순번이 있으면 다운로드만 수행
    if intent == "download" and download_index:
        # 범위 체크
        valid_indices = [idx for idx in download_index if 0 <= idx < len(results)]
        if not valid_indices:
            available_indices = ", ".join([str(i + 1) for i in range(len(results))])
            article_ctx["response"] = (
                f"⚠️ 요청하신 순번을 찾을 수 없습니다.\n\n"
                f"현재 검색 결과는 {len(results)}개입니다. "
                f"사용 가능한 순번: {available_indices}번째."
            )
            article_ctx["next_action"] = "generate_response"
            return state
        
        # 요청한 모든 논문의 정보 수집
        try:
            import json
            
            # 모든 논문을 JSON 배열에 포함 (링크가 없어도 포함)
            all_papers_data = []
            
            for idx in valid_indices:
                target_result = results[idx]
                pdf_link = target_result.get("pdf_link", "")
                title = target_result.get("title", f"{idx + 1}번째 논문")
                
                # PDF 링크가 있으면 실제 링크, 없으면 "pdf 링크가 없습니다"
                paper_data = {
                    "index": idx + 1,
                    "title": title,
                    "pdf_link": pdf_link if (pdf_link and pdf_link != "원문보기 링크 없음") else "pdf 링크가 없습니다"
                }
                
                all_papers_data.append(paper_data)
                
                if not pdf_link or pdf_link == "원문보기 링크 없음":
                    LOGGER.warning(f"PDF 링크 없음: {title}")
            
            # JSON 형식으로 응답 생성
            if len(all_papers_data) == 1:
                # 단일 논문인 경우
                json_response = json.dumps(
                    {
                        "pdf_link": all_papers_data[0]["pdf_link"],
                        "title": all_papers_data[0]["title"],
                        "index": all_papers_data[0]["index"]
                    },
                    ensure_ascii=False,
                    indent=2
                )
            else:
                # 여러 논문인 경우 배열 형식
                json_response = json.dumps(
                    [
                        {
                            "index": item["index"],
                            "title": item["title"],
                            "pdf_link": item["pdf_link"]
                        }
                        for item in all_papers_data
                    ],
                    ensure_ascii=False,
                    indent=2
                )
            
            response_msg = f"{json_response}"
            
            article_ctx["response"] = response_msg
            article_ctx["next_action"] = "generate_response"
            return state
        except Exception as e:
            LOGGER.exception(f"PDF 링크 수집 중 오류 발생: {e}")
            article_ctx["response"] = f"⚠️ PDF 링크 수집 중 오류가 발생했습니다: {str(e)}"
            article_ctx["next_action"] = "generate_response"
            return state
    
    # 여러 논문에 대한 질문 처리 (download_index가 있고 download intent가 아닌 경우)
    if download_index and intent != "download":
        # 범위 체크
        valid_indices = [idx for idx in download_index if 0 <= idx < len(results)]
        if not valid_indices:
            available_indices = ", ".join([str(i + 1) for i in range(len(results))])
            article_ctx["response"] = (
                f"⚠️ 요청하신 순번을 찾을 수 없습니다.\n\n"
                f"현재 검색 결과는 {len(results)}개입니다. "
                f"사용 가능한 순번: {available_indices}번째."
            )
            article_ctx["next_action"] = "generate_response"
            return state
        
        user_input = state.get("query", "").strip()
        if not user_input:
            user_input = "이 논문들의 주요 내용을 요약해주세요."
        
        # 여러 논문의 정보 수집
        target_results = [results[idx] for idx in valid_indices]
        paper_titles = [result.get("title", f"{idx + 1}번째 논문") for idx, result in zip(valid_indices, target_results)]
        identifiers = [result.get("identifier", "") for result in target_results]
        
        # 각 논문이 벡터DB에 없으면 다운로드 및 벡터화
        checked_identifiers = set()  # 중복 체크 방지용
        for idx, target_result in zip(valid_indices, target_results):
            identifier = target_result.get("identifier", "")
            if not identifier:
                continue
            
            # 이미 체크한 identifier는 건너뛰기
            if identifier in checked_identifiers:
                continue
            checked_identifiers.add(identifier)
            
            if search_source == "web" and not await check_paper_exists(identifier, keyword=keyword):
                pdf_link = target_result.get("pdf_link", "")
                if not pdf_link or pdf_link == "원문보기 링크 없음":
                    title = target_result.get("title", f"{idx + 1}번째 논문")
                    LOGGER.warning(f"PDF 링크 없음: {title}")
                    continue
                
                LOGGER.info(f"[rag_answer] 논문이 벡터DB에 없어 PDF 다운로드 및 벡터화 시작: {identifier}")
                success = await download_and_vectorize_paper(
                    result=target_result,
                    keyword=keyword,
                )
                if not success:
                    title = target_result.get("title", f"{idx + 1}번째 논문")
                    LOGGER.warning(f"PDF 벡터화 실패: {title}")
        
        # 여러 논문의 청크를 모두 검색
        all_chunks = []
        paper_chunks_map = {}  # 논문별 청크 저장
        
        for idx, identifier, title in zip(valid_indices, identifiers, paper_titles):
            if identifier:
                paper_chunks = await retrieve_paper_chunks_from_milvus(
                    query=user_input,
                    identifier=identifier,
                    keyword=keyword,
                    top_k=10,
                )
                # 각 청크에 논문 정보 추가
                for chunk in paper_chunks:
                    chunk["paper_index"] = idx + 1
                    chunk["paper_title"] = title
                all_chunks.extend(paper_chunks)
                paper_chunks_map[idx] = paper_chunks  # 논문별 청크 저장
            else:
                paper_chunks_map[idx] = []  # identifier가 없으면 빈 리스트
        
        # 논문별로 청크가 있는지 확인
        papers_with_chunks = [idx for idx in valid_indices if paper_chunks_map.get(idx)]
        papers_without_chunks = [idx for idx in valid_indices if not paper_chunks_map.get(idx)]
        
        # search_source="internal"일 때 청크가 없으면 vector_db_results에서 content 사용
        if search_source == "internal" and papers_without_chunks:
            for idx in papers_without_chunks.copy():
                target_result = results[idx]
                identifier = target_result.get("identifier", "")
                if identifier:
                    # vector_db_results에서 해당 identifier 찾기
                    for vdb_result in vector_db_results:
                        if vdb_result.get("identifier") == identifier:
                            content = vdb_result.get("content", "")
                            if content:
                                # vector_db_results의 content를 청크로 변환
                                fake_chunk = {
                                    "chunk_id": f"vector_db:{identifier}",
                                    "identifier": identifier,
                                    "title": target_result.get("title", ""),
                                    "content": content[:2000],  # 처음 2000자만
                                    "score": 1.0,
                                    "paper_index": idx + 1,
                                    "paper_title": target_result.get("title", "")
                                }
                                all_chunks.append(fake_chunk)
                                if idx not in paper_chunks_map:
                                    paper_chunks_map[idx] = []
                                paper_chunks_map[idx].append(fake_chunk)
                                papers_without_chunks.remove(idx)
                                papers_with_chunks.append(idx)
                            break
        
        if all_chunks:
            # 관련도 순으로 정렬 (score가 높은 순)
            all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
            
            # 상위 청크들을 논문별로 그룹화하여 정리 (청크가 있는 논문만)
            context_parts = []
            for idx in papers_with_chunks:
                title = paper_titles[valid_indices.index(idx)]
                paper_chunks = [chunk for chunk in all_chunks if chunk.get("paper_index") == idx + 1]
                if paper_chunks:
                    paper_context = "\n\n".join([
                        f"[{title} - 청크 {i+1}]\n{chunk.get('content', '')}"
                        for i, chunk in enumerate(paper_chunks[:5])  # 논문당 상위 5개만
                    ])
                    context_parts.append(paper_context)
            
            context_text = "\n\n" + "="*50 + "\n\n".join(context_parts) if context_parts else ""
            
            # 논문 제목 목록 생성 (청크가 없는 논문도 포함하되 명시)
            papers_list_parts = []
            for idx, title in zip(valid_indices, paper_titles):
                if idx in papers_without_chunks:
                    papers_list_parts.append(f"- {idx + 1}번째 논문: {title} (⚠️ 관련 내용 없음)")
                else:
                    papers_list_parts.append(f"- {idx + 1}번째 논문: {title}")
            papers_list = "\n".join(papers_list_parts)
            
            # YAML에서 프롬프트 템플릿 로드
            template = get_article_prompt('rag_answer', 'multiple_papers_template')
            
            # context_text가 있으면 포함, 없으면 빈 문자열
            context_section = f"논문 내용:\n{context_text}\n\n" if context_text else ""
            
            # 프롬프트 포맷팅
            prompt = template.format(
                paper_count=len(valid_indices),
                papers_list=papers_list,
                user_input=user_input,
                context_text=context_section
            )
            
            try:
                response = await llm.ainvoke(prompt)
                answer = response.content if hasattr(response, 'content') else str(response)
                
                # 범위 초과 에러 메시지가 있으면 바로 에러 메시지만 반환 (유효한 논문에 대한 답변 생성 안 함)
                error_msg = article_ctx.get("error", "")
                if error_msg and "요청하신" in error_msg and "번째 논문을 찾을 수 없습니다" in error_msg:
                    # 에러 메시지만 반환
                    article_ctx["response"] = error_msg
                    article_ctx["error"] = None
                    article_ctx["next_action"] = "generate_response"
                    return state
                response_msg = answer
                
                article_ctx["response"] = response_msg
                article_ctx["next_action"] = "generate_response"
                return state
            except Exception as e:
                LOGGER.exception("RAG 답변 생성 중 오류 발생")
                article_ctx["response"] = _get_user_friendly_error_message(e)
                article_ctx["error"] = str(e)  # 기술적 에러는 error 필드에만 저장
                article_ctx["next_action"] = "generate_response"
                return state
        else:
            # 청크가 없으면 기본 메시지
            papers_list = "\n".join([f"- {idx + 1}번째 논문: {title}" for idx, title in zip(valid_indices, paper_titles)])
            article_ctx["response"] = (
                f"📄 요청하신 {len(valid_indices)}개 논문:\n\n"
                f"{papers_list}\n\n"
                f"⚠️ 해당 논문들의 관련 내용을 찾을 수 없습니다."
            )
            article_ctx["next_action"] = "generate_response"
            return state
    
    # 기존 단일 논문 처리 로직 (detail intent, download_index가 하나만 있는 경우)
    if download_index and len(download_index) == 1:
        idx = download_index[0]
        if 0 <= idx < len(results):
            # 특정 논문에 대한 질문 → 해당 논문의 청크만 검색
            target_result = results[idx]
            identifier = target_result.get("identifier", "")
            title = target_result.get("title", f"{idx + 1}번째 논문")
            user_input = state.get("query", "").strip()
            
            # 질문이 없으면 기본 요약 질문 사용
            if not user_input:
                user_input = "이 논문의 주요 내용을 요약해주세요. 주제, 목적, 주요 연구 내용 및 방법, 결과 및 결론을 포함해주세요."
            
            if identifier:
                # 벡터DB에 없으면 PDF 다운로드 + 벡터화
                if search_source == "web" and not await check_paper_exists(identifier, keyword=keyword):
                    # PDF 링크 확인
                    pdf_link = target_result.get("pdf_link", "")
                    if not pdf_link or pdf_link == "원문보기 링크 없음":
                        # PDF 링크가 없으면 사용자에게 바로 알림
                        article_ctx["response"] = (
                            f"📄 {idx + 1}번째 논문: {title}\n\n"
                            f"⚠️ 이 논문의 PDF 링크가 제공되지 않아 상세 정보를 조회할 수 없습니다.\n"
                            f"다른 논문을 선택해 주세요."
                        )
                        article_ctx["next_action"] = "generate_response"
                        return state
                    
                    LOGGER.info(f"[rag_answer] 논문이 벡터DB에 없어 PDF 다운로드 및 벡터화 시작: {identifier}")
                    success = await download_and_vectorize_paper(
                        result=target_result,
                        keyword=keyword,
                    )
                    
                    # 벡터화 실패 시 사용자에게 알림
                    if not success:
                        article_ctx["response"] = (
                            f"📄 {idx + 1}번째 논문: {title}\n\n"
                            f"⚠️ PDF 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                        )
                        article_ctx["next_action"] = "generate_response"
                        return state
                
                # 해당 논문의 관련 청크 검색
                paper_chunks = await retrieve_paper_chunks_from_milvus(
                    query=user_input,
                    identifier=identifier,
                    keyword=keyword,
                    top_k=10,
                )
                
                if paper_chunks:
                    # 관련 청크들을 하나의 텍스트로 결합
                    context_text = "\n\n".join([
                        f"[청크 {chunk_idx+1}]\n{chunk.get('content', '')}"
                        for chunk_idx, chunk in enumerate(paper_chunks[:5])  # 상위 5개만 사용
                    ])
                    
                    # YAML에서 프롬프트 템플릿 로드
                    template = get_article_prompt('rag_answer', 'single_paper_template')
                    prompt = template.format(
                        user_input=user_input,
                        context_text=context_text
                    )
                    
                    try:
                        response = await llm.ainvoke(prompt)
                        answer = response.content if hasattr(response, 'content') else str(response)
                        response_msg = (
                            f"📄 {idx + 1}번째 논문: {title}\n\n"
                            f"📝 답변:\n{answer}"
                        )
                        article_ctx["response"] = response_msg
                    except Exception as e:
                        LOGGER.exception("RAG 답변 생성 중 오류 발생")
                        article_ctx["response"] = _get_user_friendly_error_message(e)
                        article_ctx["error"] = str(e)  # 기술적 에러는 error 필드에만 저장
                else:
                    # 청크를 찾지 못한 경우 모든 논문에서 검색하도록 폴백
                    LOGGER.debug(f"rag_answer: 논문 '{title}'의 청크를 찾지 못해 모든 논문에서 검색")
                    download_index = []  # 폴백을 위해 빈 리스트로 설정
            else:
                # identifier가 없으면 모든 논문에서 검색하도록 폴백
                LOGGER.debug(f"rag_answer: 논문 '{title}'의 식별자가 없어 모든 논문에서 검색")
                download_index = []  # 폴백을 위해 빈 리스트로 설정
        else:
            # 범위를 벗어난 경우
            download_index = []  # 폴백을 위해 빈 리스트로 설정
    
    # download_index가 비어있는 경우만 모든 논문에서 검색 (순번이 명시되지 않은 경우)
    # 특정 논문 검색 실패(청크 없음, identifier 없음)도 모든 논문에서 검색
    if not download_index:
        LOGGER.debug(f"rag_answer: download_index가 비어있어 모든 논문에서 검색 (download_index={download_index})")
        # 일반 RAG 답변 (벡터DB 결과 전체 사용)
        # 벡터DB 결과에서 컨텍스트 구성
        user_input = state.get("query", "")
        context_chunks = await retrieve_paper_chunks_from_milvus(
            query=user_input,
            keyword=keyword,
            top_k=6,
        )

        if context_chunks:
            context_text = "\n\n".join(
                f"[{chunk.get('title') or '논문 정보'}]\n{chunk.get('content', '')}"
                for chunk in context_chunks
            )

            # YAML에서 프롬프트 템플릿 로드
            template = get_article_prompt('rag_answer', 'general_rag_template')
            prompt = template.format(
                user_input=user_input,
                context_text=context_text
            )

            try:
                response = llm.invoke(prompt)
                answer = response.content if hasattr(response, 'content') else str(response)
                article_ctx["response"] = answer
            except Exception as e:
                LOGGER.exception("RAG 답변 생성 중 오류 발생")
                article_ctx["response"] = _get_user_friendly_error_message(e)
                article_ctx["error"] = str(e)  # 기술적 에러는 error 필드에만 저장
        elif vector_db_results:
            # 이전 결과가 남아 있다면 하위 호환을 위해 사용
            context_papers = vector_db_results
            context_text = "\n\n".join(
                f"논문 {idx+1}:\n제목: {p['title']}\n내용: {p.get('content', '')[:2000]}"
                for idx, p in enumerate(context_papers[:3])
            )

            # YAML에서 프롬프트 템플릿 로드
            template = get_article_prompt('rag_answer', 'general_rag_template')
            prompt = template.format(
                user_input=user_input,
                context_text=context_text
            )

            try:
                response = llm.invoke(prompt)
                answer = response.content if hasattr(response, 'content') else str(response)
                article_ctx["response"] = answer
            except Exception as e:
                LOGGER.exception("RAG 답변 생성 중 오류 발생")
                article_ctx["response"] = _get_user_friendly_error_message(e)
                article_ctx["error"] = str(e)  # 기술적 에러는 error 필드에만 저장
        else:
            article_ctx["response"] = "벡터DB 검색 결과가 없습니다."
    
    article_ctx["next_action"] = "generate_response"
    return state


async def search_papers(state: OrchestratorState) -> Dict[str, Any]:
    """논문 검색을 수행한다."""
    # tech_search_context.article 초기화 및 접근
    if state.get("tech_search_context") is None:
        state["tech_search_context"] = {}
    if state["tech_search_context"].get("article") is None:
        state["tech_search_context"]["article"] = {}
    
    article_ctx = state["tech_search_context"]["article"]
    
    # article context에서 상태 복원
    results = article_ctx.get("results", [])
    current_query = state.get("query", "").strip()
    previous_keyword = article_ctx.get("last_keyword", "")
    
    keyword = article_ctx.get("keyword", "")
    if not keyword and current_query:
        current_keyword = article_ctx.get("keyword", "")
        if current_keyword:
            article_ctx["last_keyword"] = current_keyword
        try:
            keyword = await generate_search_keyword(current_query)
            article_ctx["keyword"] = keyword
        except Exception as e:
            LOGGER.warning(f"키워드 생성 실패: {e}")
            keyword = extract_keyword_from_text(current_query)
            article_ctx["keyword"] = keyword
    
    # 🔧 수정: 새로운 검색인지 확인 (생성된 키워드와 이전 키워드 비교)
    is_new_search = False
    if keyword:
        # 모든 키워드를 정렬해서 비교 (순서 무관하게 비교)
        current_keywords = sorted([kw.strip().lower() for kw in keyword.split(',') if kw.strip()])
        previous_keywords = sorted([kw.strip().lower() for kw in previous_keyword.split(',') if kw.strip()]) if previous_keyword else []
        
        if previous_keywords:
            # 이전 키워드와 다르면 새로운 검색
            if current_keywords != previous_keywords:
                is_new_search = True
                LOGGER.info(
                    f"search_papers: 새로운 검색 감지 - "
                    f"previous_keywords={previous_keywords}, current_keywords={current_keywords}"
                )
        else:
            # 이전 키워드가 없으면 새로운 검색
            is_new_search = True
    else:
        # 키워드 생성 실패 시 새로운 검색으로 간주
        is_new_search = True
    
    if not is_new_search and previous_keyword:
        keyword = previous_keyword
        article_ctx["keyword"] = keyword
        LOGGER.info(f"search_papers: 이전 키워드 재사용 - keyword='{keyword}'")
    
    query = current_query
    if not query:
        article_ctx["error"] = "검색어가 없습니다."
        article_ctx["results"] = []
        article_ctx["response"] = "검색어를 입력해 주세요."
        article_ctx["next_action"] = "generate_response"
        return state
    
    try:
        # keyword는 이미 위에서 결정했으므로 재사용
        if not keyword:
            current_keyword = article_ctx.get("keyword", "")
            if current_keyword:
                article_ctx["last_keyword"] = current_keyword
            try:
                keyword = await generate_search_keyword(current_query)
                article_ctx["keyword"] = keyword
            except Exception as e:
                LOGGER.warning(f"키워드 생성 실패: {e}")
                keyword = extract_keyword_from_text(current_query)
                article_ctx["keyword"] = keyword

        # check_db_node에서 이미 벡터DB 확인을 완료했고,
        # search_papers에 도달했다는 것은 웹 검색으로 전환된 상태이므로 바로 웹 검색을 수행합니다.
        # 처음 검색 시 더 많은 결과를 가져오기 (기본값 limit=10)
        keywords = keyword.split(',') if keyword else []
        all_results = []
        errors = []  # 에러 수집
        
        for kw in keywords:
            kw = kw.strip()
            if kw:
                try:
                    results = await search_scienceon_papers(kw)
                    all_results.extend(results)
                except RuntimeError as e:
                    error_msg = str(e)
                    errors.append(f"{kw}: {error_msg}")
                    LOGGER.warning(f"검색 실패 (키워드: {kw}): {e}")
                    # Playwright 브라우저 미설치 에러는 즉시 반환
                    if "playwright install" in error_msg.lower() or "Executable doesn't exist" in error_msg:
                        # 모든 검색이 실패했고 Playwright 에러가 있으면 사용자에게 전달
                        error_message = (
                            "웹 검색을 수행하는 데 필요한 Playwright 브라우저가 설치되지 않았습니다.\n\n"
                            "다음 명령을 실행하여 브라우저를 설치해 주세요:\n"
                            "```\n"
                            "playwright install\n"
                            "```\n\n"
                            "또는 가상환경이 활성화된 상태에서:\n"
                            "```\n"
                            "uv run playwright install\n"
                            "```\n\n"
                            "설치 후 다시 검색을 시도해 주세요."
                        )
                        article_ctx["error"] = error_message
                        article_ctx["results"] = []
                        article_ctx["summary"] = error_message
                        article_ctx["next_action"] = "generate_response"
                        return state
                    # 에러 발생해도 다른 키워드 검색 계속 진행
                    continue
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    error_msg = str(e)
                    errors.append(f"{kw}: {error_msg}")
                    LOGGER.warning(f"검색 중 예상치 못한 오류 발생 (키워드: {kw}): {e}")
                    # 에러 발생해도 다른 키워드 검색 계속 진행
                    continue
        
        # 모든 검색이 실패했고 에러가 있으면 사용자에게 전달
        if not all_results and errors:
            # Playwright 브라우저 미설치 에러가 있는지 확인
            playwright_error = any("playwright install" in err.lower() or "Executable doesn't exist" in err for err in errors)
            if playwright_error:
                error_message = (
                    "웹 검색을 수행하는 데 필요한 Playwright 브라우저가 설치되지 않았습니다.\n\n"
                    "다음 명령을 실행하여 브라우저를 설치해 주세요:\n"
                    "```\n"
                    "playwright install\n"
                    "```\n\n"
                    "또는 가상환경이 활성화된 상태에서:\n"
                    "```\n"
                    "uv run playwright install\n"
                    "```\n\n"
                    "설치 후 다시 검색을 시도해 주세요."
                )
            else:
                error_message = f"웹 검색 중 오류가 발생했습니다: {errors[0]}"
            
            article_ctx["error"] = error_message
            article_ctx["results"] = []
            article_ctx["summary"] = error_message
            article_ctx["next_action"] = "generate_response"
            return state

        seen_identifiers = set()
        deduped_results = []
        for result in all_results:
            identifier = result.get("identifier", "")
            if identifier and identifier not in seen_identifiers:
                seen_identifiers.add(identifier)
                deduped_results.append(result)
        all_results = deduped_results
        
        # 검색 결과 summary 생성
        # 검색 소스 확인 (웹 검색으로 전환된 상태이므로 "웹"으로 설정)
        search_source = article_ctx.get("search_source", "web")
        search_source_label = "웹" if search_source == "web" else "사내 서버" if search_source == "internal" else "웹"
        summary = await generate_summary(
            user_query=query,
            keyword=keyword,
            results=all_results,
            search_source=search_source_label
        )

        LOGGER.info("웹 논문 검색 완료: 전체 %d건", len(all_results))

        # check_db_node에서 설정한 메시지 유지
        pre_message = article_ctx.get("response", "")
        
        # intent 설정
        article_ctx["intent"] = "search"
        article_ctx["results"] = all_results
        article_ctx["summary"] = summary
        article_ctx["error"] = None
        article_ctx["response"] = pre_message
        article_ctx["keyword"] = keyword
        
        LOGGER.info(f"[DEBUG] search_papers 완료: results_count={len(all_results)}, keyword={keyword}")
        
        return state
    except Exception as exc:
        LOGGER.exception("논문 검색 중 오류 발생")
        article_ctx["error"] = str(exc)
        article_ctx["results"] = []
        article_ctx["summary"] = _get_user_friendly_error_message(exc)
        article_ctx["next_action"] = "generate_response"
        return state


async def generate_response(state: OrchestratorState) -> Dict[str, Any]:
    """사용자에게 응답 메시지를 생성한다."""
    # tech_search_context.article 초기화 및 접근
    if state.get("tech_search_context") is None:
        state["tech_search_context"] = {}
    if state["tech_search_context"].get("article") is None:
        state["tech_search_context"]["article"] = {}
    
    article_ctx = state["tech_search_context"]["article"]
    
    # article context에서 상태 복원
    intent = article_ctx.get("intent", "unknown")
    error = article_ctx.get("error")
    results = article_ctx.get("results", [])
    summary = article_ctx.get("summary", "")
    pre_message = article_ctx.get("response", "")
    search_source = article_ctx.get("search_source", "unknown")
    
    # 디버그: generate_response 노드 진입 시 state 상태 로깅
    LOGGER.debug(
        f"[라우팅 디버그] generate_response 노드 진입 - "
        f"intent={intent}, next_action={article_ctx.get('next_action')}, "
        f"search_source={search_source}, "
        f"error={error is not None}, results_count={len(results)}, "
        f"query={state.get('query', '')[:50]}"
    )

    if error:
        response = error
        search_results = []
    elif intent == "search":
        total_results = len(results)
        
        # pre_message가 있으면 먼저 표시하고 summary 추가
        if pre_message:
            response = f"{pre_message}\n\n{summary}" if summary else pre_message
        else:
            response = f"검색이 완료되었습니다. 총 {total_results}건의 논문을 찾았습니다."
            if summary:
                response += f"\n\n{summary}"
    
        # 구조화된 검색 결과 생성
        search_results = []
        for idx, result in enumerate(results, 1):
            search_results.append({
                "index": str(idx),
                "title": result.get("title", "제목 없음"),
                "pdf_link": result.get("pdf_link", ""),
                "identifier": result.get("identifier", ""),
                "domain": "article",
                "intent": "search"
            })
    elif intent == "detail":
        response = article_ctx.get("response", "")
        if not response:
            response = "논문 상세 정보를 제공합니다."
        
        # download_index와 results를 사용하여 구조화된 결과 생성
        download_index = article_ctx.get("download_index", [])
        search_results = []
        
        if download_index and results:
            # download_index는 이미 0-based로 변환되어 있음
            valid_indices = [idx for idx in download_index if 0 <= idx < len(results)]
            for idx in valid_indices:
                result_item = results[idx]
                search_results.append({
                    "index": str(idx + 1),  # 1-based로 표시
                    "title": result_item.get("title", "제목 없음"),
                    "pdf_link": result_item.get("pdf_link", ""),
                    "identifier": result_item.get("identifier", ""),
                    "domain": "article",
                    "intent": "detail"
                })
    elif intent == "download":
        response = article_ctx.get("response", "PDF 다운로드를 완료했습니다.")
        
        # download_papers에서 구조화된 결과 생성
        download_papers = article_ctx.get("download_papers", [])
        search_results = []
        
        if download_papers:
            # 이미 구조화된 형식이면 그대로 사용
            for paper in download_papers:
                search_results.append({
                    "index": str(paper.get("index", "")),
                    "title": paper.get("title", "제목 없음"),
                    "pdf_link": paper.get("pdf_link", ""),
                    "identifier": paper.get("identifier", ""),
                    "domain": "article",
                    "intent": "download"
                })
        else:
            # download_papers가 없으면 results에서 추출 (하위 호환성)
            download_index = article_ctx.get("download_index", [])
            if download_index and results:
                valid_indices = [idx for idx in download_index if 0 <= idx < len(results)]
                for idx in valid_indices:
                    result_item = results[idx]
                    search_results.append({
                        "index": str(idx + 1),
                        "title": result_item.get("title", "제목 없음"),
                        "pdf_link": result_item.get("pdf_link", ""),
                        "identifier": result_item.get("identifier", ""),
                        "domain": "article",
                        "intent": "download"  # 추가
                    })
    elif intent == "ask_source":
        response = article_ctx.get("response", "어디서 검색하시겠습니까?")
        search_results = []
    else:
        # RAG 답변 등 기타 intent의 경우
        response = article_ctx.get("response", "요청을 처리했습니다.")
        search_results = []

    # article_ctx에 최종 response 저장
    article_ctx["response"] = response

    LOGGER.info(f"[DEBUG] generate_response 완료: intent={intent}, response_length={len(response)}, search_results_count={len(search_results)}")

    return {
        "messages": [response],
        "final_response": response,
        "search_results": search_results,
    }