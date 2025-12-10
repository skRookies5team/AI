"""개선된 LangGraph Orchestrator - 도메인 레벨 라우팅 + 마스터그래프 구조.

멘토님 피드백 반영:
- 오케스트레이터는 도메인만 결정하고 마스터그래프로 라우팅
- 각 마스터그래프에서 세부 처리 (도구 선택, 다음 노드 결정, Human-in-the-loop)

개선 사항:
- Redis 체크포인터를 사용한 state 관리
- Structured Output Parser를 활용한 구조화된 파라미터 추출
- 멀티턴 대화 지원
"""

from __future__ import annotations

from typing import Dict, Any, Optional
import asyncio
import os
import yaml
import time
import logging
from datetime import datetime
from pathlib import Path

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState, Message
from agents.llm_router import classify_intent_with_llm
from agents.structured_output import extract_structured_output, QueryIntent, HistoryCheckIntent
from agents.redis_optimizer import optimize_state_before_save
from agents.domain_node_handler import process_domain_node_result
from agents.utils import check_detailed_response_in_history, get_clarification_message
from agents.master_graphs import (
    get_schedule_master_graph,
    get_document_master_graph,
    get_qa_master_graph,
    get_email_master_graph,
    get_tech_master_graph,
)


# 도메인별 마스터그래프 매핑
MASTER_GRAPHS = {
    "schedule": get_schedule_master_graph,
    "document": get_document_master_graph,
    "qa": get_qa_master_graph,
    "email": get_email_master_graph,
    "tech": get_tech_master_graph,
}


# 라우팅 성능 측정 로거 설정
def _setup_routing_logger() -> logging.Logger:
    """라우팅 성능 측정을 위한 로거를 설정합니다.
    
    Returns:
        설정된 로거 인스턴스
    """
    logger = logging.getLogger("routing_performance")
    
    # 이미 핸들러가 설정되어 있으면 재설정하지 않음
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.INFO)
    
    # logs 디렉토리 생성
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # 로그 파일 경로 (날짜별로 생성)
    log_file = log_dir / f"routing_performance_{datetime.now().strftime('%Y%m%d')}.log"
    
    # 파일 핸들러 생성
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    
    # 포맷 설정: 시간 | 파일명:라인번호 | 함수명 | 레벨 | 메시지
    formatter = logging.Formatter(
        '%(asctime)s | %(pathname)s:%(lineno)d | %(funcName)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    
    return logger


# 라우팅 성능 측정 로거 인스턴스
_routing_logger = _setup_routing_logger()


# 프롬프트 캐시 (모듈 로드 시 즉시 로드하여 첫 요청 시 지연 방지)
_orchestrator_prompts = None
_orchestrator_config = None

def _load_orchestrator_prompts_sync() -> Dict[str, Any]:
    """오케스트레이터 프롬프트를 YAML 파일에서 동기적으로 로드합니다.
    
    모듈 로드 시 또는 첫 호출 시 실행됩니다.
    
    Returns:
        오케스트레이터 프롬프트 딕셔너리
    """
    global _orchestrator_prompts
    
    if _orchestrator_prompts is not None:
        return _orchestrator_prompts
    
    # agent_prompts.yaml 파일 경로 찾기
    current_dir = Path(__file__).parent
    yaml_path = current_dir / "agent_prompts.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        all_prompts = yaml.safe_load(f)
    
    _orchestrator_prompts = all_prompts.get("orchestrator", {})
    _orchestrator_config = all_prompts.get("orchestrator_config", {})
    
    if not _orchestrator_prompts:
        raise ValueError("agent_prompts.yaml에 'orchestrator' 섹션이 없습니다.")
    
    return _orchestrator_prompts

def load_orchestrator_config() -> Dict[str, Any]:
    """오케스트레이터 설정을 반환합니다 (캐시된 값 사용).
    
    Returns:
        오케스트레이터 설정 딕셔너리
    """
    global _orchestrator_config
    
    # 이미 로드되었으면 바로 반환
    if _orchestrator_config is not None:
        return _orchestrator_config
    
    # 첫 호출 시에만 로드
    current_dir = Path(__file__).parent
    yaml_path = current_dir / "agent_prompts.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        all_prompts = yaml.safe_load(f)
    
    _orchestrator_config = all_prompts.get("orchestrator_config", {})
    return _orchestrator_config

def load_orchestrator_prompts() -> Dict[str, Any]:
    """오케스트레이터 프롬프트를 반환합니다 (캐시된 값 사용).
    
    Returns:
        오케스트레이터 프롬프트 딕셔너리
    """
    # 이미 로드되었으면 바로 반환 (I/O 없음)
    if _orchestrator_prompts is not None:
        return _orchestrator_prompts
    
    # 첫 호출 시에만 로드
    prompts = _load_orchestrator_prompts_sync()
    print("✅ Orchestrator 프롬프트 로드 완료")
    return prompts

# 모듈 로드 시 프롬프트 미리 로드 (첫 요청 시 지연 방지)
try:
    _load_orchestrator_prompts_sync()
    print("✅ Orchestrator 프롬프트 사전 로드 완료")
except Exception as e:
    # 모듈 로드 시 오류가 발생해도 나중에 다시 시도할 수 있도록 무시
    print(f"[WARNING] Orchestrator 프롬프트 사전 로드 실패 (나중에 다시 시도): {e}")


def get_history_check_prompt(history_context: str = "") -> str:
    """이전 대화 확인 질문 분류를 위한 프롬프트를 반환합니다.
    
    Args:
        history_context: 이전 대화 내용 컨텍스트
        
    Returns:
        시스템 프롬프트 문자열
    """
    prompts = load_orchestrator_prompts()
    history_check_prompt = prompts.get("prompts", {}).get("history_check", {}).get("system_prompt", "")
    
    if not history_check_prompt:
        raise ValueError("orchestrator.prompts.history_check.system_prompt가 없습니다.")
    
    # 플레이스홀더 치환
    placeholder = prompts.get("prompts", {}).get("history_check", {}).get("history_context_placeholder", "{history_context}")
    prompt = history_check_prompt.replace(placeholder, history_context)
    
    return prompt


def check_tech_ask_source_state(query: str, state: OrchestratorState, user_message: Message) -> Optional[Dict[str, Any]]:
    """Tech 도메인의 ask_source 상태를 확인하고 tech 도메인으로 유지할지 결정."""
    tech_search_context = state.get("tech_search_context", {})
    if not tech_search_context:
        return None
    
    # article 또는 patent의 ask_source 상태 확인
    for agent_type in ["article", "patent"]:
        if agent_type in tech_search_context:
            agent_ctx = tech_search_context[agent_type]
            # agent_ctx가 None이 아니고 딕셔너리인지 확인
            if agent_ctx and isinstance(agent_ctx, dict):
                if agent_ctx.get("intent") == "ask_source" or agent_ctx.get("intent") == "ask_search_mode":
                    # "웹에서 검색", "사내 서버에서 검색", "1", "2" 같은 응답은 tech 도메인으로 유지
                    config = load_orchestrator_config()
                    keywords_config = config.get("keywords", {})
                    tech_continuation_keywords = keywords_config.get("tech_continuation_keywords", [])
                    if any(k in query for k in tech_continuation_keywords) or query.isdigit():
                        print(f"✅ Tech 도메인 ask_source 상태 유지: '{query}' → tech 도메인")
                        return {
                            "domain": "tech",
                            "conversation_history": [user_message]
                        }
    return None


def get_domain_classification_prompt(context: str = "") -> str:
    """도메인 분류를 위한 프롬프트를 반환합니다.
    
    Args:
        context: 이전 대화 내용 컨텍스트
        
    Returns:
        시스템 프롬프트 문자열
    """
    prompts = load_orchestrator_prompts()
    domain_prompt = prompts.get("prompts", {}).get("domain_classification", {}).get("system_prompt", "")
    
    if not domain_prompt:
        raise ValueError("orchestrator.prompts.domain_classification.system_prompt가 없습니다.")
    
    # 플레이스홀더 치환
    placeholder = prompts.get("prompts", {}).get("domain_classification", {}).get("context_placeholder", "{context}")
    prompt = domain_prompt.replace(placeholder, context)
    
    return prompt


async def route_to_domain(state: OrchestratorState) -> Dict[str, Any]:
    """사용자 요청을 분석하여 도메인만 결정 (AI-001: Intent Router).
    
    오케스트레이터는 도메인 레벨에서만 라우팅하고,
    세부 처리는 각 마스터그래프에서 담당합니다.
    
    라우팅 방법:
    1. Tech 도메인 ask_source 상태 확인 (특수 케이스)
    2. 이전 대화 확인 질문 처리 (키워드 기반 + LLM)
    3. 단순 선택지 처리 (특수 케이스)
    4. Structured Output Parser (LLM + YAML 프롬프트) - 주요 방법
    5. LLM 라우터 (LLM + YAML 프롬프트) - fallback
    
    하드코딩된 키워드 매칭은 제거되어 LLM 기반 분류만 사용합니다.
    
    Args:
        state: 오케스트레이터 상태
        
    Returns:
        domain: 도메인 이름 (schedule, document, qa, email, tech)
    """
    print("--- [Orchestrator] Routing to Domain ---")
    route_start_time = time.time()
    
    query = state.get("query", "").strip()
    session_id = state.get("session_id")
    last_domain = state.get("last_domain")
    conversation_history = state.get("conversation_history", [])
    
    # 현재 날짜와 시간 정보 설정 (모든 agent가 공통으로 사용)
    config = load_orchestrator_config()
    date_time_config = config.get("date_time", {})
    weekday_names = date_time_config.get("weekday_names", ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"])
    date_format = date_time_config.get("date_format", "%Y년 %m월 %d일")
    time_format = date_time_config.get("time_format", "%H시 %M분")
    
    now = datetime.now()
    current_date_info = {
        "current_date": now.strftime(date_format),
        "current_time": now.strftime(time_format),
        "current_datetime": now.isoformat(),
        "current_weekday": weekday_names[now.weekday()],
    }
    
    # state에 날짜/시간 정보 추가 (이미 있으면 업데이트)
    state.update(current_date_info)
    
    config = load_orchestrator_config()
    search_params = config.get("search_params", {})
    query_preview_length = search_params.get("query_preview_length", 50)
    _routing_logger.info(f"[라우팅 시작] query='{query[:query_preview_length]}...', session_id={session_id}, current_date={current_date_info['current_date']}")
    
    # 대화 히스토리에 사용자 메시지 추가
    user_message: Message = {
        "role": "user",
        "content": query,
        "timestamp": datetime.now().isoformat()
    }
    
    # Tech 도메인 ask_source 상태 확인 (최우선 처리)
    step_start = time.time()
    tech_result = check_tech_ask_source_state(query, state, user_message)
    step_elapsed = (time.time() - step_start) * 1000
    _routing_logger.info(f"[Tech 도메인 상태 확인] {step_elapsed:.2f}ms")
    if tech_result:
        total_elapsed = (time.time() - route_start_time) * 1000
        _routing_logger.info(f"[라우팅 완료] {total_elapsed:.2f}ms (Tech 도메인 상태 확인으로 종료)")
        return tech_result
    
    # 이전 대화 확인 질문 감지 (비용 최적화: 키워드 기반 패턴 매칭 우선)
    # LLM 호출을 최소화하기 위해 키워드 패턴 매칭을 먼저 시도
    step_start = time.time()
    is_history_check_query = False
    history_check_result = None
    
    # 키워드 패턴 매칭 (YAML에서 로드)
    config = load_orchestrator_config()
    keywords_config = config.get("keywords", {})
    strong_history_keywords = keywords_config.get("strong_history_keywords", [])
    weak_history_keywords = keywords_config.get("weak_history_keywords", [])
    
    # 강력한 키워드가 있으면 LLM 호출 없이 바로 처리
    has_strong_keyword = any(k in query for k in strong_history_keywords)
    has_weak_keyword = any(k in query for k in weak_history_keywords)
    step_elapsed = (time.time() - step_start) * 1000
    _routing_logger.info(f"[이전 대화 확인 키워드 체크] {step_elapsed:.2f}ms")
    
    if conversation_history:
        if has_strong_keyword:
            # 강력한 키워드가 있으면 LLM 호출 없이 바로 처리
            is_history_check_query = True
            print(f"✅ [키워드 기반 이전 대화 확인] 강력한 키워드 감지 - LLM 호출 스킵")
        elif has_weak_keyword and len(query) <= 20:
            # 약한 키워드 + 짧은 질문이면 LLM 호출 없이 처리
            is_history_check_query = True
            print(f"✅ [키워드 기반 이전 대화 확인] 약한 키워드 + 짧은 질문 - LLM 호출 스킵")
        else:
            # 확실하지 않은 경우에만 LLM 호출 (비용 절감)
            try:
                llm_start = time.time()
                # 대화 히스토리 컨텍스트 최소화 (YAML 설정 사용)
                config = load_orchestrator_config()
                search_params = config.get("search_params", {})
                recent_count = search_params.get("history_check_recent_messages", 3)
                max_length = search_params.get("history_check_message_max_length", 200)
                
                history_context = ""
                if conversation_history:
                    recent_history = conversation_history[-recent_count:]
                    history_context = "\n\n이전 대화 내용:\n"
                    for msg in recent_history:
                        content = msg.get('content', '')
                        # 각 메시지 길이 제한
                        if len(content) > max_length:
                            content = content[:max_length] + "..."
                        history_context += f"{msg.get('role', 'unknown')}: {content}\n"
                
                # YAML 파일에서 프롬프트 로드
                prompt_start = time.time()
                system_prompt = get_history_check_prompt(history_context)
                prompt_elapsed = (time.time() - prompt_start) * 1000
                _routing_logger.info(f"[이전 대화 확인 프롬프트 로드] {prompt_elapsed:.2f}ms")
                
                llm_call_start = time.time()
                history_check_result = await extract_structured_output(
                    query=query,
                    model_class=HistoryCheckIntent,
                    system_prompt=system_prompt
                )
                llm_elapsed = (time.time() - llm_call_start) * 1000
                _routing_logger.info(f"[이전 대화 확인 LLM 호출] {llm_elapsed:.2f}ms")
                
                is_history_check_query = history_check_result.is_history_check
                total_llm_elapsed = (time.time() - llm_start) * 1000
                _routing_logger.info(f"[이전 대화 확인 LLM 전체] {total_llm_elapsed:.2f}ms")
                print(f"🔍 [LLM 기반 이전 대화 확인] is_history_check={is_history_check_query}, check_type={history_check_result.check_type}, confidence={history_check_result.confidence:.2f}")
                
            except Exception as e:
                print(f"⚠️ 이전 대화 확인 질문 분류 실패: {e}, 키워드 패턴으로 fallback")
                # Fallback: 키워드 패턴 매칭
                is_history_check_query = has_strong_keyword or has_weak_keyword
    
    if is_history_check_query and conversation_history:
        step_start = time.time()
        print(f"✅ 이전 대화 확인 질문 감지: '{query}' - conversation_history 확인 (빠른 응답)")
        
        # conversation_history에서 이전 요청 및 검색 결과 확인
        found_previous_request = False
        request_details = []
        email_search_found = False
        qa_search_found = False  # QA 검색 (식단, 메뉴 등)
        email_keywords = []
        qa_keywords = []  # QA 관련 키워드 (식단, 메뉴 등)
        previous_email_results = []  # 이전 이메일 검색 결과
        previous_qa_results = None  # 이전 QA 검색 결과 (식단 등)
        
        # email_search_context에서 이전 검색 결과 확인 (Redis에서 복원된 state)
        email_search_context = state.get("email_search_context", {})
        if email_search_context and email_search_context.get("items"):
            previous_email_results = email_search_context.get("items", [])
        
        # qa_search_context에서 이전 검색 결과 확인 (식단 등)
        qa_search_context = state.get("qa_search_context", {})
        if qa_search_context:
            # 식단 관련 이전 검색 결과 확인
            cafeteria_context = qa_search_context.get("cafeteria", {})
            if cafeteria_context and cafeteria_context.get("last_answer"):
                previous_qa_results = cafeteria_context.get("last_answer", "")
        
        # 질문에서 언급된 키워드 추출 (예: "육아휴직", "식단")
        import re
        keyword_pattern = r'([가-힣a-zA-Z]+)\s*(?:관련|관해서|에 대해|에 대한|내용)'
        mentioned_keywords = re.findall(keyword_pattern, query)
        
        # conversation_history에서 이전 요청 확인 (YAML 설정 사용)
        config = load_orchestrator_config()
        search_params = config.get("search_params", {})
        max_messages = search_params.get("conversation_history_max_messages", 10)
        recent_messages = list(reversed(conversation_history[-max_messages:]))
        for msg in recent_messages:
            content = msg.get("content", "")
            role = msg.get("role", "")
            
            # 사용자 메시지에서 이전 요청 찾기
            if role == "user":
                # 이메일 검색 요청인지 확인 (간단한 키워드 체크)
                if "이메일" in content or "메일" in content:
                    if any(k in content for k in ["찾아", "검색", "찾아줘", "검색해줘"]):
                        email_search_found = True
                        found_previous_request = True
                        request_details.append(content)
                        # 키워드 추출 (예: "육아휴직 관련" -> "육아휴직")
                        keyword_match = re.search(keyword_pattern, content)
                        if keyword_match:
                            email_keywords.append(keyword_match.group(1))
                        break  # 가장 최근 요청만 확인
                
                # QA 검색 요청인지 확인 (식단, 메뉴 등)
                elif any(k in content for k in ["식단", "메뉴", "구내식당"]):
                    qa_search_found = True
                    found_previous_request = True
                    request_details.append(content)
                    # 키워드 추출 (예: "식단", "메뉴")
                    qa_keywords_in_content = [k for k in ["식단", "메뉴", "구내식당"] if k in content]
                    if qa_keywords_in_content:
                        qa_keywords.extend(qa_keywords_in_content)
                    break  # 가장 최근 요청만 확인
        
        # assistant 메시지에서 이전 검색 결과 확인 (email_search_context가 없는 경우)
        # 성능 최적화: 최근 메시지만 확인 (YAML 설정 사용)
        email_results_max = search_params.get("email_results_max_count", 5)
        if not previous_email_results:
            for msg in recent_messages[:email_results_max]:
                content = msg.get("content", "")
                role = msg.get("role", "")
                
                # assistant 메시지에서 이메일 검색 결과 찾기
                if role == "assistant" and ("메일" in content or "이메일" in content):
                    # 문서번호 패턴 찾기 (예: "151652025MAB10121776-27427607")
                    doc_numbers = re.findall(r'\d{11,}MAB\d+-\d+', content)
                    if doc_numbers:
                        # 제목 추출 시도
                        titles = re.findall(r'\*\*\[([^\]]+)\]\*\*', content)
                        if titles or doc_numbers:
                            previous_email_results = [
                                {"title": titles[i] if i < len(titles) else "이메일", "doc_number": doc_num}
                                for i, doc_num in enumerate(doc_numbers[:5])  # 최대 5개
                            ]
                            break
        
        # LLM이 추출한 키워드 활용 (있는 경우)
        if history_check_result and history_check_result.mentioned_keywords:
            mentioned_keywords = history_check_result.mentioned_keywords
        else:
            # 이메일 또는 QA 키워드 중 하나 선택
            if email_keywords:
                mentioned_keywords = email_keywords
            elif qa_keywords:
                mentioned_keywords = qa_keywords
            else:
                mentioned_keywords = []
        
        # 대화식 답변 생성 (이전 검색 결과 포함)
        if found_previous_request and qa_search_found:
            # QA 검색 (식단 등) 관련 이전 요청 확인
            if mentioned_keywords:
                keywords_str = ", ".join(set(mentioned_keywords))
                response = f"네, 이전에 '{keywords_str}' 관련 내용을 요청하신 적이 있습니다."
            elif qa_keywords:
                keywords_str = ", ".join(set(qa_keywords))
                response = f"네, 이전에 '{keywords_str}' 관련 내용을 요청하신 적이 있습니다."
            else:
                response = "네, 이전에 식단/메뉴 관련 요청을 하신 적이 있습니다."
            
            # 이전 QA 검색 결과가 있으면 간단히 언급
            if previous_qa_results:
                # 이전 답변의 핵심 내용만 추출 (너무 길면 생략)
                if len(previous_qa_results) > 200:
                    response += f"\n\n이전에 조회한 내용이 있습니다."
                else:
                    response += f"\n\n이전 조회 내용: {previous_qa_results[:200]}"
        elif found_previous_request and email_search_found:
            if mentioned_keywords:
                keywords_str = ", ".join(set(mentioned_keywords))
                response = f"네, 이전에 '{keywords_str}' 관련 내용을 이메일에서 찾아달라는 요청을 하신 적이 있습니다."
            elif email_keywords:
                keywords_str = ", ".join(set(email_keywords))
                response = f"네, 이전에 '{keywords_str}' 관련 내용을 이메일에서 찾아달라는 요청을 하신 적이 있습니다."
            else:
                response = "네, 이전에 이메일에서 검색/찾기 요청을 하신 적이 있습니다."
            
            # 이전 검색 결과가 있으면 포함
            if previous_email_results:
                response += "\n\n"
                for i, result in enumerate(previous_email_results[:5], 1):  # 최대 5개
                    title = result.get("title", "이메일")
                    doc_number = result.get("doc_number", result.get("document_number", ""))
                    if doc_number:
                        response += f"{i}. **{title}** - 문서번호: {doc_number}\n"
                    else:
                        response += f"{i}. **{title}**\n"
                response += "\n더 많은 메일을 검색하거나 특정 메일의 상세 내용을 원하신다면 말씀해 주세요! 😊"
            elif request_details:
                # 검색 결과는 없지만 요청은 있었던 경우
                last_request = request_details[0]  # 가장 최근 요청
                response += f"\n\n이전 요청: '{last_request}'"
        elif found_previous_request:
            if mentioned_keywords:
                keywords_str = ", ".join(set(mentioned_keywords))
                response = f"네, 이전에 '{keywords_str}' 관련 요청을 하신 적이 있습니다."
            else:
                response = "네, 이전에 검색/찾기 요청을 하신 적이 있습니다."
        else:
            # LLM이 확인 질문으로 분류했지만 이전 요청을 찾지 못한 경우
            if history_check_result and history_check_result.check_type:
                check_type_map = {
                    "email_search": "이메일 검색",
                    "patent_search": "특허 검색",
                    "article_search": "논문 검색",
                    "leave_request": "연차/휴가 신청",
                    "document_generation": "문서 생성",
                    "meeting_reservation": "회의실 예약"
                }
                request_type = check_type_map.get(history_check_result.check_type, "해당 요청")
                response = f"아니요, 이전에 {request_type} 요청은 없었습니다."
            else:
                response = "아니요, 이전에 이메일에서 검색/찾기 요청은 없었습니다."
        
        # orchestrator에서 직접 답변 반환 (QA 마스터 그래프로 라우팅하지 않음)
        # conversation_history에 assistant 메시지 추가
        assistant_message: Message = {
            "role": "assistant",
            "content": response,
            "timestamp": datetime.now().isoformat()
        }
        
        result = {
            "domain": None,  # 명시적으로 None으로 설정하여 END로 라우팅
            "final_response": response,
            "conversation_history": [user_message, assistant_message],  # 사용자 메시지와 답변 모두 추가
            "messages": [response]
        }
        
        # Redis 저장 전 state 최적화 (이전 대화 확인 질문은 간단하므로 최소한의 최적화만)
        # conversation_history만 최적화하고 나머지는 그대로 유지
        opt_start = time.time()
        from agents.redis_optimizer import optimize_conversation_history
        result["conversation_history"] = optimize_conversation_history(result["conversation_history"])
        opt_elapsed = (time.time() - opt_start) * 1000
        _routing_logger.info(f"[이전 대화 확인 state 최적화] {opt_elapsed:.2f}ms")
        
        step_elapsed = (time.time() - step_start) * 1000
        total_elapsed = (time.time() - route_start_time) * 1000
        _routing_logger.info(f"[이전 대화 확인 처리 전체] {step_elapsed:.2f}ms")
        _routing_logger.info(f"[라우팅 완료] {total_elapsed:.2f}ms (이전 대화 확인으로 종료)")
        return result
    
    # 빠른 라우팅: 단순 선택지/숫자 답변만 처리
    # 복잡한 요청은 LLM 기반 분류로 처리하여 문맥을 정확히 이해
    step_start = time.time()
    is_simple_selection = False
    
    # 단순 선택지 체크 (YAML 설정 사용)
    config = load_orchestrator_config()
    search_params = config.get("search_params", {})
    simple_selection_max_length = search_params.get("simple_selection_max_length", 3)
    simple_selection_keywords = search_params.get("simple_selection_keywords", [])
    
    # 1. 숫자만 있는 경우 (예: "1", "2", "3")
    if query.isdigit():
        is_simple_selection = True
    # 2. 매우 짧은 선택지 답변 + 숫자/선택 키워드
    elif len(query) <= simple_selection_max_length and any(k in query for k in ["1", "2", "3", "4", "5"]):
        is_simple_selection = True
    # 3. 명확한 선택지 키워드만
    elif query.strip() in simple_selection_keywords:
        is_simple_selection = True
    
    step_elapsed = (time.time() - step_start) * 1000
    _routing_logger.info(f"[단순 선택지 체크] {step_elapsed:.2f}ms")
        
    if is_simple_selection:
        print(f"--- [Orchestrator] 단순 선택지 답변 감지: {query} ---")
        
        # Tech 도메인 ask_source 상태 확인 (최우선 처리)
        tech_result = check_tech_ask_source_state(query, state, user_message)
        if tech_result:
            return tech_result
        
        # 숫자만 입력한 경우, 이전 대화에서 상세한 답변이 있었는지 확인
        if query.isdigit() and conversation_history:
            # 공통 유틸리티 함수 사용
            has_detailed_response = check_detailed_response_in_history(conversation_history)
            
            # 상세한 답변이 있었다면 명확한 요청을 부탁하는 메시지 반환
            if has_detailed_response:
                print(f"⚠️ 숫자만 입력했지만 이전에 상세한 답변이 제공됨 - 명확한 요청 안내")
                response = get_clarification_message()
                
                assistant_message: Message = {
                    "role": "assistant",
                    "content": response,
                    "timestamp": datetime.now().isoformat()
                }
                
                result = {
                    "domain": None,  # END로 라우팅
                    "final_response": response,
                    "conversation_history": [user_message, assistant_message],
                    "messages": [response]
                }
                
                # Redis 저장 전 state 최적화 (단순 답변은 최소한의 최적화만)
                from agents.redis_optimizer import optimize_conversation_history
                result["conversation_history"] = optimize_conversation_history(result["conversation_history"])
                return result
        
        # 이전 대화의 도메인이 있으면 그대로 사용
        if last_domain and last_domain in MASTER_GRAPHS:
            print(f"✅ 이전 대화 도메인으로 라우팅: {last_domain}")
            total_elapsed = (time.time() - route_start_time) * 1000
            _routing_logger.info(f"[라우팅 완료] {total_elapsed:.2f}ms (이전 대화 도메인: {last_domain}으로 종료)")
            # conversation_history는 operator.add를 사용하므로 새 메시지만 추가
            return {
                "domain": last_domain,
                "conversation_history": [user_message]  # operator.add로 기존 히스토리에 추가됨
            }
        else:
            print(f"⚠️ 이전 대화 도메인을 찾을 수 없음 (last_domain={last_domain}), LLM으로 분류")
    
    # Structured Output Parser를 사용한 의도 분석 (LLM + YAML 프롬프트)
    # 하드코딩된 키워드 매칭 제거로 인해 LLM 기반 분류만 사용
    step_start = time.time()
    try:
        # 대화 히스토리 컨텍스트 최소화 (YAML 설정 사용)
        config = load_orchestrator_config()
        search_params = config.get("search_params", {})
        recent_count = search_params.get("domain_classification_recent_messages", 2)
        max_length = search_params.get("domain_classification_message_max_length", 100)
        
        context_start = time.time()
        context = ""
        if conversation_history:
            recent_history = conversation_history[-recent_count:]
            context = "\n\n이전 대화 내용:\n"
            for msg in recent_history:
                content = msg.get('content', '')
                # 각 메시지 길이 제한
                if len(content) > max_length:
                    content = content[:max_length] + "..."
                context += f"{msg.get('role', 'unknown')}: {content}\n"
        context_elapsed = (time.time() - context_start) * 1000
        _routing_logger.info(f"[컨텍스트 생성] {context_elapsed:.2f}ms")
        
        # YAML 파일에서 프롬프트 로드 (이미 메모리에 캐시되어 있음)
        prompt_start = time.time()
        system_prompt = get_domain_classification_prompt(context)
        prompt_elapsed = (time.time() - prompt_start) * 1000
        _routing_logger.info(f"[도메인 분류 프롬프트 로드] {prompt_elapsed:.2f}ms")
        
        # Structured Output Parser 호출 (LLM 호출)
        llm_start = time.time()
        query_preview_length = search_params.get("query_preview_length", 50)
        _routing_logger.info(f"[Structured Output Parser LLM 호출 시작] query='{query[:query_preview_length]}...'")
        intent_result = await extract_structured_output(
            query=query,
            model_class=QueryIntent,
            system_prompt=system_prompt
        )
        llm_elapsed = (time.time() - llm_start) * 1000
        _routing_logger.info(f"[Structured Output Parser LLM 호출 완료] {llm_elapsed:.2f}ms")
        
        domain = intent_result.intent
        step_elapsed = (time.time() - step_start) * 1000
        _routing_logger.info(f"[Structured Output Parser 전체] {step_elapsed:.2f}ms")
        print(f"✅ Structured Output으로 도메인 결정: {domain} (신뢰도: {intent_result.confidence:.2f})")
        
    except Exception as e:
        step_elapsed = (time.time() - step_start) * 1000
        _routing_logger.warning(f"[Structured Output Parser 실패] {step_elapsed:.2f}ms, error={str(e)}")
        print(f"⚠️ Structured Output 파싱 실패: {e}, LLM 라우터로 fallback")
        
        # llm_router를 사용한 LLM 기반 도메인 분류 (fallback, YAML 프롬프트 참고)
        llm_router_start = time.time()
        query_preview_length = search_params.get("query_preview_length", 50)
        _routing_logger.info(f"[LLM 라우터 호출 시작] query='{query[:query_preview_length]}...'")
        domain = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="supervisor",
            fallback_route="qa",
            session_id=session_id
        )
        llm_router_elapsed = (time.time() - llm_router_start) * 1000
        _routing_logger.info(f"[LLM 라우터 호출 완료] {llm_router_elapsed:.2f}ms")
    
    # 유효성 검사
    if domain not in MASTER_GRAPHS:
        print(f"⚠️ 알 수 없는 도메인: {domain}, qa로 fallback")
        domain = "qa"
    
    total_elapsed = (time.time() - route_start_time) * 1000
    print(f"✅ 도메인 결정: {domain}")
    _routing_logger.info(f"[라우팅 완료] {total_elapsed:.2f}ms (도메인: {domain})")
    return {
        "domain": domain,
        "conversation_history": [user_message]
    }


def get_orchestrator_graph(checkpointer: Optional[Any] = None):
    """개선된 오케스트레이터 그래프 생성.
    
    세션 관리자(SessionManager)를 사용하여 state를 관리하고 멀티턴 대화를 지원합니다.
    
    Args:
        checkpointer: 체크포인터 인스턴스 (현재는 사용하지 않음, None으로 설정)
    
    구조:
    1. route_to_domain: 도메인 결정
    2. 각 도메인별 마스터그래프 (서브그래프)
    
    메모리 관리:
    - Redis 체크포인터가 사용 가능하면 Redis에 state 저장 (영구 저장)
    - Redis가 없으면 MemorySaver 사용 (인메모리, 서버 재시작 시 초기화)
    - session_id를 thread_id로 사용하여 세션별 독립적인 메모리 관리
    """
    workflow = StateGraph(OrchestratorState)
    
    # Router 노드 추가
    workflow.add_node("route_to_domain", route_to_domain)
    
    # 각 도메인별 마스터그래프를 서브그래프로 추가
    # 도메인 노드 공통 로직은 domain_node_handler 모듈에서 처리
    async def schedule_node(state: OrchestratorState) -> Dict[str, Any]:
        """스케줄 마스터그래프 실행."""
        graph = get_schedule_master_graph()
        result = await graph.ainvoke(state)
        return process_domain_node_result(state, result, "schedule", "스케줄 작업을 완료했습니다.")
    
    async def document_node(state: OrchestratorState) -> Dict[str, Any]:
        """문서 마스터그래프 실행."""
        graph = get_document_master_graph()
        result = await graph.ainvoke(state)
        return process_domain_node_result(state, result, "document", "문서 작업을 완료했습니다.")
    
    async def qa_node(state: OrchestratorState) -> Dict[str, Any]:
        """QA 마스터그래프 실행."""
        graph = get_qa_master_graph()
        result = await graph.ainvoke(state)
        return process_domain_node_result(state, result, "qa", "질문에 답변했습니다.")
    
    async def email_node(state: OrchestratorState) -> Dict[str, Any]:
        """이메일 마스터그래프 실행."""
        print("--- [Orchestrator] Executing Email Master Graph ---")
        graph = get_email_master_graph()
        result = await graph.ainvoke(state)
        return process_domain_node_result(state, result, "email", "이메일 작업을 완료했습니다.")
    
    async def tech_node(state: OrchestratorState) -> Dict[str, Any]:
        """기술 마스터그래프 실행."""
        graph = get_tech_master_graph()
        result = await graph.ainvoke(state)
        return process_domain_node_result(state, result, "tech", "기술 검색 작업을 완료했습니다.")
    
    # 각 도메인별 노드 등록
    workflow.add_node("schedule", schedule_node)
    workflow.add_node("document", document_node)
    workflow.add_node("qa", qa_node)
    workflow.add_node("email", email_node)
    workflow.add_node("tech", tech_node)
    
    print(f"✅ 마스터그래프 등록 완료: schedule, document, qa, email, tech")
    
    # 엣지 설정
    workflow.set_entry_point("route_to_domain")
    
    # 조건부 라우팅: 도메인에 따라 해당 마스터그래프로 라우팅
    def route_to_master_graph(state: OrchestratorState) -> str:
        """도메인에 따라 마스터그래프로 라우팅."""
        # 이전 대화 확인 질문은 orchestrator에서 직접 처리하므로 END로 이동
        final_response = state.get("final_response")
        domain = state.get("domain")
        
        # final_response가 있고 domain이 None이면 이전 대화 확인 질문으로 간주
        if final_response and (domain is None or domain == ""):
            print("✅ 이전 대화 확인 질문 - orchestrator에서 직접 처리 완료, END로 이동")
            return END
        
        print(f"--- [Orchestrator] State domain value: {domain} ---")
        print(f"--- [Orchestrator] State keys: {list(state.keys())} ---")
        
        # domain이 없거나 None이면 기본값 사용
        if not domain:
            print("⚠️ domain이 state에 없습니다. document로 fallback")
            domain = "document"
        
        # 유효성 검사
        if domain not in MASTER_GRAPHS:
            print(f"⚠️ 알 수 없는 도메인: {domain}, document로 fallback")
            domain = "document"
        
        print(f"--- [Orchestrator] Routing to {domain} master graph ---")
        return domain
    
    # 조건부 엣지 설정: 도메인별 마스터그래프 + END (이전 대화 확인 질문 처리용)
    routing_map = {domain: domain for domain in MASTER_GRAPHS.keys()}
    routing_map[END] = END  # END도 라우팅 맵에 추가
    
    workflow.add_conditional_edges(
        "route_to_domain",
        route_to_master_graph,
        routing_map
    )
    
    # 모든 마스터그래프는 END로 연결
    for domain_name in MASTER_GRAPHS.keys():
        workflow.add_edge(domain_name, END)
    
    print(f"📊 총 {len(MASTER_GRAPHS)}개 마스터그래프 등록 완료")
    
    # 체크포인터 없이 컴파일 (세션 관리자가 대화 히스토리 관리)
    # SessionManager가 Redis에 대화 히스토리와 컨텍스트를 저장합니다
    # 세션당 하나의 키만 사용합니다 (session:{session_id})
    print("ℹ️ 체크포인터 없이 실행 (세션 관리자가 대화 히스토리 관리)")
    compiled_graph = workflow.compile(checkpointer=None)
    print("✅ Orchestrator 그래프 컴파일 완료")
    return compiled_graph

