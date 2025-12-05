"""LangGraph 노드 함수들."""

from __future__ import annotations

import logging
import re
from typing import List

from langchain_core.messages import AIMessage

from agents.master_graphs.tech.article_agent_module.state import (
    ScienceONAgentState,
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
from agents.master_graphs.tech.article_agent_module.crawler import download_pdfs_from_results, search_scienceon_papers
from agents.master_graphs.tech.article_agent_module.vector_db import (
    extract_keyword_from_text,
    check_vector_db,
    retrieve_paper_chunks_from_milvus,
    convert_vector_db_results_to_paper_results,
    check_paper_exists,
    download_and_vectorize_paper,
)
from agents.master_graphs.tech.article_agent_module.prompts import LLM, INTENT_PROMPT

LOGGER = logging.getLogger(__name__)


async def analyze_input_node(state: ScienceONAgentState) -> ScienceONAgentState:
    """사용자 입력을 분석하여 검색 소스를 결정한다."""
    user_input = state.get("user_input", "")
    
    # 입력 검증
    is_valid, error_msg = _validate_user_input(user_input)
    if not is_valid:
        state["error"] = error_msg
        state["response"] = _get_user_friendly_error_message(Exception(error_msg))
        state["next_action"] = "generate_response"
        return state
    
    # 입력 정제
    user_input = _sanitize_user_input(user_input)
    state["user_input"] = user_input
    
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
            state["download_index"] = []
            state["error"] = None  # 에러 초기화
            state["response"] = ""  # 응답 초기화
            # 검색 키워드 추출
            # "시스템 논문", "배터리 논문" 등에서 주제 추출
            state["query"] = user_input
            state["intent"] = "search"
            # 아래 LLM 분류 로직으로 진행 (results_count=0이 됨)
            results_count = 0  # results_count 업데이트
    
    # LLM을 사용한 intent 분류 (정규식 제거, LLM이 모든 것을 처리)
    try:
        history = _render_history(messages[:-1] if len(messages) > 1 else [])  # 마지막 사용자 입력 제외
        
        prompt = INTENT_PROMPT.format_messages(
            results_count=results_count,
            user_input=user_input,
            history=history
        )
        
        response = await LLM.ainvoke(prompt)
        response_text = response.content.strip()
        
        # JSON 파싱 (중첩된 중괄호 처리)
        intent_data = _extract_json_from_text(response_text)
        
        if intent_data:
            intent = intent_data.get("intent", "search")
            query = intent_data.get("query", "")
            download_index_raw = intent_data.get("download_index")
            
            # download_index 파싱 (배열 또는 단일 값)
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
            
            # intent에 따라 query 조정
            if intent in ["detail", "download"]:
                query = ""
            
            LOGGER.debug(f"LLM intent 분류: intent={intent}, download_index={download_index} (0-based), query={query}")
        else:
            # JSON 파싱 실패 시 기본값
            intent = "search"
            query = user_input
            download_index: List[int] = []
            LOGGER.warning("LLM JSON 파싱 실패, search로 폴백")
            
    except Exception as e:
        LOGGER.warning(f"LLM intent 분류 실패: {e}, search로 폴백")
        intent = "search"
        query = user_input
        download_index: List[int] = []
    
    # intent에 따라 처리
    state["intent"] = intent
    # detail/download intent이면 query는 빈 문자열, search intent이면 query 사용
    if intent in ["detail", "download"]:
        state["query"] = ""
    else:
        state["query"] = query if query else user_input
    
    if intent == "detail" or intent == "download":
        if download_index and results_count > 0:
            # 🆕 인덱스 범위 체크 (Early validation)
            valid_indices = [idx for idx in download_index if 0 <= idx < results_count]
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
                state["error"] = error_msg
                state["response"] = error_msg
                state["next_action"] = "generate_response"
                state["download_index"] = []
                # 🆕 디버그: 범위 초과 에러 발생 시 state 상태 로깅
                LOGGER.debug(
                    f"[라우팅 디버그] analyze_input 범위 초과 에러 발생 시 state 상태 - "
                    f"intent={state.get('intent')}, next_action={state.get('next_action')}, "
                    f"search_source={state.get('search_source')}, results_count={results_count}"
                )
                return state
            
            state["download_index"] = valid_indices
            state["last_selected_paper_index"] = valid_indices[0] if valid_indices else -1  # 마지막 선택 인덱스 저장
            LOGGER.debug(
                f"analyze_input: {intent} intent 설정 - download_index={valid_indices} (0-based), "
                f"results_count={results_count}, user_input={user_input}"
            )
            
            # download intent는 벡터DB 체크 없이 바로 다운로드로 라우팅
            if intent == "download":
                state["next_action"] = "rag_answer"  # rag_answer_node에서 다운로드 처리
                return state
            
            # detail intent는 rag_answer_node에서 벡터DB 체크 수행 (중복 체크 방지)
            # 🆕 search_source와 벡터DB 존재 여부에 따라 다른 노드로 라우팅
            search_source = state.get("search_source", "unknown")
            # 벡터DB 체크는 rag_answer_node에서 수행하므로 여기서는 바로 라우팅
            state["next_action"] = "rag_answer"
            LOGGER.debug(
                f"analyze_input: detail intent 설정 - download_index={valid_indices} (0-based), "
                f"rag_answer로 라우팅 (벡터DB 체크는 rag_answer_node에서 수행)"
            )
            return state
        elif results_count > 0 and state.get("last_selected_paper_index", -1) >= 0:
            # 🆕 순번이 없지만 이전에 선택한 논문이 있으면 그것 사용
            last_index = state.get("last_selected_paper_index", -1)
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
                state["download_index"] = [last_index]
                
                # download intent는 벡터DB 체크 없이 바로 다운로드로 라우팅
                if intent == "download":
                    state["next_action"] = "rag_answer"  # rag_answer_node에서 다운로드 처리
                    return state
                
                # detail intent는 rag_answer_node에서 벡터DB 체크 수행 (중복 체크 방지)
                # 🆕 search_source와 벡터DB 존재 여부에 따라 다른 노드로 라우팅
                search_source = state.get("search_source", "unknown")
                # 벡터DB 체크는 rag_answer_node에서 수행하므로 여기서는 바로 라우팅
                state["next_action"] = "rag_answer"
                LOGGER.debug(
                    f"analyze_input: detail intent 설정 (last_selected_paper_index 사용) - "
                    f"last_index={last_index}, rag_answer로 라우팅 (벡터DB 체크는 rag_answer_node에서 수행)"
                )
                return state
        else:
            # 결과가 없으면 search로 변경
            state["intent"] = "search"
            state["query"] = user_input
    
    # 검색 의도인 경우에만 처리
    if intent == "search":
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
        has_web_keyword, has_internal_keyword = _parse_search_source_keywords(user_input)
        
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


async def check_db_node(state: ScienceONAgentState) -> ScienceONAgentState:
    """벡터DB에서 논문 검색."""
    # 상태 검증
    is_valid, error_msg = _validate_state(state)
    if not is_valid:
        state["error"] = error_msg
        state["response"] = _get_user_friendly_error_message(Exception(error_msg))
        state["next_action"] = "generate_response"
        return state
    
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
            search_keyword = await generate_search_keyword(query)
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
    results = await check_vector_db(query, limit=5, max_distance=1.5, keyword=collection_keyword)
    
    if results and len(results) > 0:
        # 벡터DB에 있음
        state["vector_db_results"] = results  # 공통으로 설정
        
        if search_source == "internal":
            # 🆕 사내 서버 명시됨 → 논문 목록 표시 (웹 검색처럼)
            paper_results = convert_vector_db_results_to_paper_results(results)
            state["results"] = paper_results
            state["displayed_count"] = len(paper_results)
            
            # 논문 목록 요약 생성 (state["keyword"]를 사용)
            keyword_for_summary = state.get("keyword", "").split(',')[0].strip() if state.get("keyword") else query
            summary = await generate_summary(
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


async def ask_search_source_with_db_result(state: ScienceONAgentState) -> ScienceONAgentState:
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
    
    has_web_keyword, has_internal_keyword = _parse_search_source_keywords(user_input)
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
                paper_results = convert_vector_db_results_to_paper_results(vector_db_results)
                state["results"] = paper_results
                state["displayed_count"] = len(paper_results)
                
                # 논문 목록 요약 생성
                query = state.get("query", "") or state.get("user_input", "")
                keyword = state.get("keyword", "")
                if not keyword:
                    try:
                        keyword = await generate_search_keyword(query)
                        state["keyword"] = keyword
                    except Exception:
                        keyword = query
                
                summary = await generate_summary(
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


async def rag_answer_node(state: ScienceONAgentState) -> ScienceONAgentState:
    """RAG로 사용자 질문에 답변 (질문이 없으면 요약 생성)."""
    # 상태 검증
    is_valid, error_msg = _validate_state(state)
    if not is_valid:
        state["error"] = error_msg
        state["response"] = _get_user_friendly_error_message(Exception(error_msg))
        state["next_action"] = "generate_response"
        return state
    
    # 이전 에러 상태 초기화 (이전 에러가 남아있지 않도록)
    state["error"] = None
    
    if not OPENAI_MODEL:
        state["response"] = "OpenAI API 키가 설정되지 않았습니다."
        state["next_action"] = "generate_response"
        return state
    
    # RAG 답변용 싱글톤 LLM 사용 (async-safe)
    llm = await get_rag_llm()
    if llm is None:
        state["response"] = "OpenAI API 초기화에 실패했습니다."
        state["next_action"] = "generate_response"
        return state
    
    download_index = state.get("download_index", [])
    results = state.get("results", [])
    
    if not results:
        state["response"] = "먼저 논문을 검색한 뒤에 상세 정보를 요청해 주세요."
        state["next_action"] = "generate_response"
        return state
    
    # download_index가 비어있으면 last_selected_paper_index 확인
    # last_selected_paper_index를 사용한 경우에만 범위 체크 (analyze_input_node에서 이미 체크한 경우 제외)
    used_last_index = False
    if not download_index:
        last_index = state.get("last_selected_paper_index", -1)
        if last_index >= 0:
            download_index = [last_index]
            used_last_index = True
            # last_selected_paper_index를 사용한 경우에만 범위 체크
    
    # download intent인 경우 여러 논문 다운로드 처리
    intent = state.get("intent", "unknown")
    
    # download intent이고 순번이 있으면 다운로드만 수행
    if intent == "download" and download_index:
        # 범위 체크
        valid_indices = [idx for idx in download_index if 0 <= idx < len(results)]
        if not valid_indices:
            available_indices = ", ".join([str(i + 1) for i in range(len(results))])
            state["response"] = (
                f"⚠️ 요청하신 순번을 찾을 수 없습니다.\n\n"
                f"현재 검색 결과는 {len(results)}개입니다. "
                f"사용 가능한 순번: {available_indices}번째."
            )
            state["next_action"] = "generate_response"
            return state
        
        # 다운로드할 논문들 수집
        download_targets = []
        for idx in valid_indices:
            target_result = results[idx]
            pdf_link = target_result.get("pdf_link", "")
            if pdf_link and pdf_link != "원문보기 링크 없음":
                download_targets.append(target_result)
            else:
                title = target_result.get("title", f"{idx + 1}번째 논문")
                LOGGER.warning(f"PDF 링크 없음: {title}")
        
        if not download_targets:
            state["response"] = "⚠️ 다운로드 가능한 PDF 링크가 없습니다."
            state["next_action"] = "generate_response"
            return state
        
        try:
            # download_pdfs_from_results는 파일 상단에서 이미 import됨
            from pathlib import Path
            
            # 다운로드 디렉토리 설정
            download_dir = Path(__file__).resolve().parent / "pdf"
            download_dir.mkdir(parents=True, exist_ok=True)
            
            # 여러 논문 다운로드
            download_results = await download_pdfs_from_results(
                results=download_targets,
                download_dir=str(download_dir)
            )
            
            # 성공/실패 메시지 생성
            success_list = []
            fail_list = []
            for i, result in enumerate(download_results):
                idx = valid_indices[i]
                title = result.get("title", f"{idx + 1}번째 논문")
                if result.get("pdf_path"):
                    success_list.append(f"  ✅ {idx + 1}번째: {title} → {result['pdf_path']}")
                else:
                    fail_list.append(f"  ❌ {idx + 1}번째: {title}")
            
            response_parts = ["📄 PDF 다운로드 결과:\n"]
            if success_list:
                response_parts.append("성공:\n" + "\n".join(success_list))
            if fail_list:
                response_parts.append("\n실패:\n" + "\n".join(fail_list))
            
            state["response"] = "\n".join(response_parts)
            state["next_action"] = "generate_response"
            return state
        except Exception as e:
            LOGGER.exception(f"PDF 다운로드 중 오류 발생: {e}")
            state["response"] = f"⚠️ PDF 다운로드 중 오류가 발생했습니다: {str(e)}"
            state["next_action"] = "generate_response"
            return state
    
    # 여러 논문에 대한 질문 처리 (download_index가 있고 download intent가 아닌 경우)
    if download_index and intent != "download":
        # 범위 체크
        valid_indices = [idx for idx in download_index if 0 <= idx < len(results)]
        if not valid_indices:
            available_indices = ", ".join([str(i + 1) for i in range(len(results))])
            state["response"] = (
                f"⚠️ 요청하신 순번을 찾을 수 없습니다.\n\n"
                f"현재 검색 결과는 {len(results)}개입니다. "
                f"사용 가능한 순번: {available_indices}번째."
            )
            state["next_action"] = "generate_response"
            return state
        
        user_input = state.get("user_input", "").strip()
        if not user_input:
            user_input = "이 논문들의 주요 내용을 요약해주세요."
        
        # 여러 논문의 정보 수집
        target_results = [results[idx] for idx in valid_indices]
        paper_titles = [result.get("title", f"{idx + 1}번째 논문") for idx, result in zip(valid_indices, target_results)]
        identifiers = [result.get("identifier", "") for result in target_results]
        
        # 각 논문이 벡터DB에 없으면 다운로드 및 벡터화
        search_source = state.get("search_source", "unknown")
        checked_identifiers = set()  # 중복 체크 방지용
        for idx, target_result in zip(valid_indices, target_results):
            identifier = target_result.get("identifier", "")
            if not identifier:
                continue
            
            # 이미 체크한 identifier는 건너뛰기
            if identifier in checked_identifiers:
                continue
            checked_identifiers.add(identifier)
            
            if search_source == "web" and not await check_paper_exists(identifier, keyword=state.get("keyword")):
                pdf_link = target_result.get("pdf_link", "")
                if not pdf_link or pdf_link == "원문보기 링크 없음":
                    title = target_result.get("title", f"{idx + 1}번째 논문")
                    LOGGER.warning(f"PDF 링크 없음: {title}")
                    continue
                
                LOGGER.info(f"[rag_answer] 논문이 벡터DB에 없어 PDF 다운로드 및 벡터화 시작: {identifier}")
                success = await download_and_vectorize_paper(
                    result=target_result,
                    keyword=state.get("keyword"),
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
                    keyword=state.get("keyword"),
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
            
            prompt = (
                f"다음은 {len(valid_indices)}개 논문의 관련 내용입니다.\n\n"
                f"논문 목록:\n{papers_list}\n\n"
                f"사용자 질문: {user_input}\n\n"
            )
            
            if context_text:
                prompt += (
                    f"논문 내용:\n{context_text}\n\n"
                )
            
            prompt += (
                f"중요: 위 논문 목록에서 '(⚠️ 관련 내용 없음)'으로 표시된 논문은 벡터DB에 관련 내용이 없습니다. "
                f"이런 논문에 대해서는 임의로 답변을 생성하지 말고, '해당 논문의 관련 내용을 찾을 수 없습니다'라고만 명시하세요.\n\n"
                f"위 내용을 바탕으로 사용자 질문에 대해 정확하고 상세하게 답변해주세요. "
                f"답변은 다음 형식을 따라주세요:\n"
                f"- 각 논문의 제목을 명시하고 해당 논문의 관련 내용을 설명 (관련 내용이 있는 경우만)\n"
                f"- 관련 내용이 없는 논문은 '해당 논문의 관련 내용을 찾을 수 없습니다'라고만 명시\n"
                f"- 여러 논문의 내용을 비교하거나 통합하여 설명 (가능한 경우, 관련 내용이 있는 논문들만)\n"
                f"- 사용자 질문에 대한 핵심 내용을 구조화하여 설명\n"
                f"- 가능한 경우 구체적인 내용을 인용하여 설명\n"
                f"- 한국어로 친절하고 전문적으로 작성\n\n"
                f"답변 시작 부분에 '사용자 질문에 대한 답변입니다.' 같은 서론 문구 없이 바로 본문 내용으로 시작해주세요."
            )
            
            try:
                response = await llm.ainvoke(prompt)
                answer = response.content if hasattr(response, 'content') else str(response)
                response_msg = (
                    f"📄 요청하신 {len(valid_indices)}개 논문에 대한 답변:\n\n"
                    f"{papers_list}\n\n"
                    f"📝 답변:\n{answer}"
                )
                state["response"] = response_msg
                state["next_action"] = "generate_response"
                return state
            except Exception as e:
                LOGGER.exception("RAG 답변 생성 중 오류 발생")
                state["response"] = _get_user_friendly_error_message(e)
                state["error"] = str(e)  # 기술적 에러는 error 필드에만 저장
                state["next_action"] = "generate_response"
                return state
        else:
            # 청크가 없으면 기본 메시지
            papers_list = "\n".join([f"- {idx + 1}번째 논문: {title}" for idx, title in zip(valid_indices, paper_titles)])
            state["response"] = (
                f"📄 요청하신 {len(valid_indices)}개 논문:\n\n"
                f"{papers_list}\n\n"
                f"⚠️ 해당 논문들의 관련 내용을 찾을 수 없습니다."
            )
            state["next_action"] = "generate_response"
            return state
    
    # 기존 단일 논문 처리 로직 (detail intent, download_index가 하나만 있는 경우)
    if download_index and len(download_index) == 1:
        idx = download_index[0]
        if 0 <= idx < len(results):
            # 특정 논문에 대한 질문 → 해당 논문의 청크만 검색
            target_result = results[idx]
            identifier = target_result.get("identifier", "")
            title = target_result.get("title", f"{idx + 1}번째 논문")
            user_input = state.get("user_input", "").strip()
            
            # 질문이 없으면 기본 요약 질문 사용
            if not user_input:
                user_input = "이 논문의 주요 내용을 요약해주세요. 주제, 목적, 주요 연구 내용 및 방법, 결과 및 결론을 포함해주세요."
            
            if identifier:
                # 벡터DB에 없으면 PDF 다운로드 + 벡터화
                search_source = state.get("search_source", "unknown")
                if search_source == "web" and not await check_paper_exists(identifier, keyword=state.get("keyword")):
                    # PDF 링크 확인
                    pdf_link = target_result.get("pdf_link", "")
                    if not pdf_link or pdf_link == "원문보기 링크 없음":
                        # PDF 링크가 없으면 사용자에게 바로 알림
                        state["response"] = (
                            f"📄 {idx + 1}번째 논문: {title}\n\n"
                            f"⚠️ 이 논문의 PDF 링크가 제공되지 않아 상세 정보를 조회할 수 없습니다.\n"
                            f"다른 논문을 선택해 주세요."
                        )
                        state["next_action"] = "generate_response"
                        return state
                    
                    LOGGER.info(f"[rag_answer] 논문이 벡터DB에 없어 PDF 다운로드 및 벡터화 시작: {identifier}")
                    success = await download_and_vectorize_paper(
                        result=target_result,
                        keyword=state.get("keyword"),
                    )
                    
                    # 벡터화 실패 시 사용자에게 알림
                    if not success:
                        state["response"] = (
                            f"📄 {idx + 1}번째 논문: {title}\n\n"
                            f"⚠️ PDF 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                        )
                        state["next_action"] = "generate_response"
                        return state
                
                # 해당 논문의 관련 청크 검색
                paper_chunks = await retrieve_paper_chunks_from_milvus(
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
                        response = await llm.ainvoke(prompt)
                        answer = response.content if hasattr(response, 'content') else str(response)
                        response_msg = (
                            f"📄 {idx + 1}번째 논문: {title}\n\n"
                            f"📝 답변:\n{answer}"
                        )
                        state["response"] = response_msg
                    except Exception as e:
                        LOGGER.exception("RAG 답변 생성 중 오류 발생")
                        state["response"] = _get_user_friendly_error_message(e)
                        state["error"] = str(e)  # 기술적 에러는 error 필드에만 저장
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
        context_chunks = await retrieve_paper_chunks_from_milvus(
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
                state["response"] = _get_user_friendly_error_message(e)
                state["error"] = str(e)  # 기술적 에러는 error 필드에만 저장
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
                state["response"] = _get_user_friendly_error_message(e)
                state["error"] = str(e)  # 기술적 에러는 error 필드에만 저장
        else:
            state["response"] = "벡터DB 검색 결과가 없습니다."
    
    state["next_action"] = "generate_response"
    return state


async def search_papers(state: ScienceONAgentState) -> ScienceONAgentState:
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
                keyword = await generate_search_keyword(current_query)
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
        summary = await generate_summary(
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
            keyword = await generate_search_keyword(query)

        # 새로운 검색이므로 displayed_count 리셋
        if is_new_search:
            displayed_count = 0

        # check_db_node에서 이미 벡터DB 확인을 완료했고,
        # search_papers에 도달했다는 것은 웹 검색으로 전환된 상태이므로 바로 웹 검색을 수행합니다.
        # 처음 검색 시 더 많은 결과를 가져오기 (기본값 limit=10)
        keywords = keyword.split(',') if keyword else []
        all_results = []
        for kw in keywords:
            kw = kw.strip()
            if kw:
                try:
                    results = await search_scienceon_papers(kw, enable_downloads=False)
                    all_results.extend(results)
                except RuntimeError as e:
                    LOGGER.warning(f"검색 실패 (키워드: {kw}): {e}")
                    # 에러 발생해도 다른 키워드 검색 계속 진행
                    continue
                except Exception as e:
                    LOGGER.warning(f"검색 중 예상치 못한 오류 발생 (키워드: {kw}): {e}")
                    # 에러 발생해도 다른 키워드 검색 계속 진행
                    continue

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
        search_source = state.get("search_source", "웹")
        search_source_label = "웹" if search_source == "web" else "사내 서버" if search_source == "internal" else "웹"
        summary = await generate_summary(
            user_query=query,
            keyword=keyword,
            results=all_results,
            search_source=search_source_label
        )

        LOGGER.info("웹 논문 검색 완료: 전체 %d건", len(all_results))

        # check_db_node에서 설정한 메시지 유지
        pre_message = state.get("response", "")
        
        # intent 설정
        state["intent"] = "search"


        return {
            "query": query,
            "keyword": keyword,
            "results": all_results,  # 전체 결과 저장
            "summary": summary,
            "error": None,
            "displayed_count": len(all_results),
            "response": pre_message,  # check_db_node에서 설정한 메시지 유지
            "intent": "search",
        }
    except Exception as exc:
        LOGGER.exception("논문 검색 중 오류 발생")
        return {
            "error": str(exc),
            "results": [],
            "summary": _get_user_friendly_error_message(exc),
        }


async def generate_response(state: ScienceONAgentState) -> ScienceONAgentState:
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
    elif intent == "detail":
        response = state.get("response", "논문 상세 정보를 제공합니다.")
    elif intent == "download":
        response = state.get("response", "PDF 다운로드를 완료했습니다.")
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