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
    get_recruitment_master_graph,
)


# 도메인별 마스터그래프 매핑
MASTER_GRAPHS = {
    "schedule": get_schedule_master_graph,
    "document": get_document_master_graph,
    "qa": get_qa_master_graph,
    "email": get_email_master_graph,
    "tech": get_tech_master_graph,
    "recruitment": get_recruitment_master_graph,
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

def load_all_prompts() -> Dict[str, Any]:
    """전체 프롬프트 YAML 파일을 로드합니다 (tech, qa, email 등 모든 섹션 포함).

    특허 검색 모드 응답 감지를 위해 tech 섹션에 접근할 때 사용됩니다.

    Returns:
        전체 프롬프트 딕셔너리
    """
    current_dir = Path(__file__).parent
    yaml_path = current_dir / "agent_prompts.yaml"

    if not yaml_path.exists():
        raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        all_prompts = yaml.safe_load(f)

    return all_prompts

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
    
    # YAML에서 키워드 로드 (함수 시작 부분에서 한 번만 로드 - 특허 검색 관련)
    config = load_orchestrator_config()
    keywords_config = config.get("keywords", {})
    tech_continuation_keywords = keywords_config.get("tech_continuation_keywords", [])
    server_data_keywords = keywords_config.get("server_data_keywords", [])  # YAML에서 로드

    # tech_search_context가 있는 경우 기존 로직 수행
    if tech_search_context:
        # article 또는 patent의 상태 확인
        for agent_type in ["article", "patent"]:
            if agent_type in tech_search_context:
                agent_ctx = tech_search_context[agent_type]
                # agent_ctx가 None이 아니고 딕셔너리인지 확인
                if agent_ctx and isinstance(agent_ctx, dict):
                    intent = agent_ctx.get("intent")

                    # ask_source 또는 ask_search_mode 상태 확인
                    if intent == "ask_source" or intent == "ask_search_mode":
                        # "웹에서 검색", "사내 서버에서 검색", "1", "2" 같은 응답은 tech 도메인으로 유지
                        # YAML에서 이미 로드된 키워드 사용
                        if any(k in query for k in tech_continuation_keywords) or query.isdigit():
                            print(f"✅ Tech 도메인 ask_source 상태 유지: '{query}' → tech 도메인")
                            return {
                                "domain": "tech",
                                "conversation_history": [user_message]
                            }
                        # ask_search_mode 상태에서도 서버 데이터 질문 처리
                        # YAML에서 이미 로드된 키워드 사용
                        if any(k in query for k in server_data_keywords):
                            print(f"✅ Tech 도메인 ask_search_mode 상태에서 서버 질문 감지: '{query}' → tech 도메인")
                            return {
                                "domain": "tech",
                                "conversation_history": [user_message]
                            }

                    # 일반 특허 검색 컨텍스트 확인 (추가)
                    elif intent == "search":
                        # "서버에 데이터가 있나", "서버에 있나" 같은 질문 처리
                        # YAML에서 이미 로드된 키워드 사용
                        if any(k in query for k in server_data_keywords):
                            print(f"✅ Tech 도메인 검색 컨텍스트 유지: '{query}' → tech 도메인")
                            return {
                                "domain": "tech",
                                "conversation_history": [user_message]
                            }

    # tech_search_context가 비어있을 때도 대화 히스토리에서 특허 검색 관련 질문 확인
    # (Redis 저장이 지연되거나 누락된 경우 대비 - LLM 기반)
    conversation_history = state.get("conversation_history", [])
    if conversation_history and len(conversation_history) >= 2:
        try:
            # 대화 히스토리 컨텍스트 구성
            recent_messages = conversation_history[-2:]
            history_context = "\n\n최근 대화 내용:\n"
            for msg in recent_messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                history_context += f"{role}: {content}\n"

            # YAML에서 프롬프트 로드 (전체 YAML 로드 필요 - tech 섹션 접근)
            prompts = load_all_prompts()
            tech_config = prompts.get("tech", {})
            patent_config = tech_config.get("sub_agents", {}).get("patent", {})
            prompt_template = patent_config.get("search_mode_response_detection_prompt", "")

            if prompt_template:
                # 프롬프트 치환
                system_prompt = prompt_template.replace("{history_context}", history_context).replace("{user_query}", query)

                # LLM 직접 호출 (기존 parse_search_mode_with_llm() 방식과 동일)
                import json
                import re
                from langchain_openai import ChatOpenAI

                api_key = os.environ.get("OPENAI_API_KEY")
                if api_key:
                    # OPENAI_MODEL 환경 변수에서 가져오기 (기본값: gpt-4o-mini)
                    model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

                    llm = ChatOpenAI(
                        model=model_name,
                        temperature=0,
                        api_key=api_key
                    )

                    response = llm.invoke(system_prompt)
                    content = response.content.strip()

                    # JSON 파싱 (parse_search_mode_with_llm()과 동일한 방식)
                    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
                    if json_match:
                        try:
                            result = json.loads(json_match.group())
                            is_search_mode_response = result.get("is_search_mode_response", False)
                            confidence = result.get("confidence", 0.0)

                            if is_search_mode_response and confidence >= 0.7:
                                search_mode = result.get("search_mode")
                                print(f"✅ Tech 도메인 LLM 기반 감지: '{query}' → tech 도메인 (검색 모드: {search_mode}, 신뢰도: {confidence:.2f})")
                                return {
                                    "domain": "tech",
                                    "conversation_history": [user_message]
                                }
                        except json.JSONDecodeError:
                            print(f"⚠️ 특허 검색 모드 응답 JSON 파싱 실패: {content}")
            else:
                print("⚠️ 특허 검색 모드 응답 감지 프롬프트가 YAML에 없습니다.")
        except Exception as e:
            print(f"⚠️ 특허 검색 모드 LLM 판단 실패: {e}")
            # Fallback: 키워드 기반 (기존 로직 유지)
            recent_messages = conversation_history[-2:]
            has_patent_query = False
            has_search_mode_question = False

            for msg in recent_messages:
                content = msg.get("content", "").lower()

                # 특허 관련 키워드 확인
                patent_keywords = ["특허", "patent", "특허를", "특허 찾아", "특허 검색"]
                if any(kw in content for kw in patent_keywords):
                    has_patent_query = True

                # 검색 방식 선택 질문 확인
                if "검색 방식을 선택" in content or "서버 기반 검색" in content or "웹 검색" in content:
                    has_search_mode_question = True

            # 특허 검색 질문이 있고 검색 방식 선택 질문이 있었으면, 현재 입력이 검색 모드 선택 응답일 가능성
            if has_patent_query and has_search_mode_question:
                # 검색 모드 선택 관련 키워드 확인
                if any(k in query for k in tech_continuation_keywords) or query.isdigit() or any(k in query for k in server_data_keywords):
                    print(f"✅ Tech 도메인 대화 히스토리 기반 감지 (Fallback): '{query}' → tech 도메인 (특허 검색 모드 선택 응답)")
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
    
    # 현재 날짜와 시간 정보 설정 (모든 agent가 공통으로 사용) - YAML 설정 사용
    config = load_orchestrator_config()
    date_time_config = config.get("date_time", {})
    if not date_time_config:
        raise ValueError("YAML에 date_time 설정이 정의되어 있지 않습니다.")
    weekday_names = date_time_config.get("weekday_names", [])
    date_format = date_time_config.get("date_format", "")
    time_format = date_time_config.get("time_format", "")
    if not weekday_names or not date_format or not time_format:
        raise ValueError("YAML에 date_time 설정이 완전하지 않습니다.")
    
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
        previous_qa_query = None  # 이전 QA 요청 내용
        if qa_search_context:
            # 식단 관련 이전 검색 결과 확인
            cafeteria_context = qa_search_context.get("cafeteria", {})
            if cafeteria_context:
                if cafeteria_context.get("last_answer"):
                    previous_qa_results = cafeteria_context.get("last_answer", "")
                if cafeteria_context.get("last_query"):
                    previous_qa_query = cafeteria_context.get("last_query", "")
            # 복리후생 관련 이전 검색 결과 확인
            benefit_context = qa_search_context.get("benefit", {})
            if benefit_context:
                if benefit_context.get("last_answer"):
                    previous_qa_results = benefit_context.get("last_answer", "")
                if benefit_context.get("last_query"):
                    previous_qa_query = benefit_context.get("last_query", "")
        
        # 질문에서 언급된 키워드 추출 (YAML 설정 사용)
        import re
        config = load_orchestrator_config()
        search_params = config.get("search_params", {})
        matching_config = search_params.get("history_check_matching", {})

        # YAML에서 키워드 패턴 로드
        keyword_pattern_str = matching_config.get("keyword_pattern", "")
        if not keyword_pattern_str:
            raise ValueError("YAML에 keyword_pattern이 정의되어 있지 않습니다.")
        keyword_pattern = re.compile(keyword_pattern_str)
        mentioned_keywords = re.findall(keyword_pattern, query)
        
        # 사용자 질문에서 직접 언급된 키워드 추출 (YAML 설정 사용)
        query_keyword_mapping = matching_config.get("query_keyword_mapping", {})
        if not query_keyword_mapping:
            raise ValueError("YAML에 query_keyword_mapping이 정의되어 있지 않습니다.")
        query_keywords = []

        # 사용자 질문의 도메인 타입 확인 (우선순위: 사용자 질문에서 언급된 도메인)
        query_domain_type = None  # "email" 또는 "qa" 또는 None

        # 이메일 키워드 확인
        email_keywords_list = query_keyword_mapping.get("email", [])
        if email_keywords_list and any(k in query for k in email_keywords_list):
            query_keywords.append("이메일")
            query_domain_type = "email"

        # QA 키워드 확인 및 구체적인 키워드 추출
        qa_keywords_list = query_keyword_mapping.get("qa", [])
        qa_specific_keywords = query_keyword_mapping.get("qa_specific", [])  # 복리후생, 연차사용 등 구체적 키워드

        # 우선순위: 구체적인 QA 키워드를 먼저 확인 (복리후생, 연차사용 등)
        has_qa_keyword = False
        if qa_specific_keywords:
            for kw in qa_specific_keywords:
                if kw in query:
                    query_keywords.append(kw)
                    has_qa_keyword = True
                    if query_domain_type is None:
                        query_domain_type = "qa"

        # 일반 QA 키워드 확인 (식단, 메뉴 등)
        if qa_keywords_list and any(k in query for k in qa_keywords_list):
            has_qa_keyword = True
            # 이메일 키워드가 없으면 QA로 설정
            if query_domain_type is None:
                query_domain_type = "qa"
            # 구체적 키워드가 없을 때만 일반 키워드 추가
            if not any(kw in query_keywords for kw in qa_specific_keywords if qa_specific_keywords):
                for kw in qa_keywords_list:
                    if kw in query:
                        query_keywords.append(kw)
                        break

        # 정규식 패턴으로 추출된 키워드도 추가 (중복 제거)
        for kw in mentioned_keywords:
            if kw not in query_keywords:
                query_keywords.append(kw)

        # conversation_history에서 이전 요청 확인 (YAML 설정 사용)
        max_messages = search_params.get("conversation_history_max_messages", 20)
        recent_messages = list(reversed(conversation_history[-max_messages:]))

        # 점수 계산 가중치 로드 (YAML 설정 사용)
        scoring_weights = matching_config.get("scoring_weights", {})
        base_score = scoring_weights.get("base_score", 10)
        type_match_bonus = scoring_weights.get("type_match_bonus", 20)
        keyword_match_bonus = scoring_weights.get("keyword_match_bonus", 15)
        exact_keyword_match_bonus = scoring_weights.get("exact_keyword_match_bonus", 25)
        domain_type_match_bonus = scoring_weights.get("domain_type_match_bonus", 30)  # 도메인 타입 일치 보너스

        # 요청 타입별 키워드 로드 (YAML 설정 사용)
        request_type_keywords = matching_config.get("request_type_keywords", {})
        if not request_type_keywords:
            raise ValueError("YAML에 request_type_keywords가 정의되어 있지 않습니다.")
        email_search_verbs = request_type_keywords.get("email", {}).get("search_verbs", [])
        qa_keywords_list_config = request_type_keywords.get("qa", {}).get("keywords", [])

        # QA 구체적 키워드도 추가 (복리후생, 연차사용 등)
        qa_specific_keywords_config = query_keyword_mapping.get("qa_specific", [])
        if qa_specific_keywords_config:
            qa_keywords_list_config = list(qa_keywords_list_config) + qa_specific_keywords_config

        # 키워드 매칭 점수를 계산하여 가장 관련성 높은 요청 찾기
        best_match = None
        best_score = 0

        for msg in recent_messages:
            content = msg.get("content", "")
            role = msg.get("role", "")
            
            # 사용자 메시지에서 이전 요청 찾기
            if role == "user":
                score = 0
                match_type = None
                matched_keywords = []  # 매칭된 키워드 추적

                # 이메일 검색 요청인지 확인 (YAML 설정 사용)
                if "이메일" in content or "메일" in content:
                    if any(k in content for k in email_search_verbs):
                        score += base_score
                        match_type = "email"

                        # 사용자 질문의 도메인 타입과 일치하면 높은 보너스 점수
                        if query_domain_type == "email":
                            score += domain_type_match_bonus

                        # 사용자 질문에서 "이메일"을 언급했으면 점수 증가
                        if "이메일" in query_keywords or "메일" in query_keywords:
                            score += type_match_bonus

                        # 키워드 매칭 점수 (언급된 키워드가 이전 요청에도 있으면 높은 점수)
                        for keyword in query_keywords:
                            if keyword in content:
                                score += keyword_match_bonus
                                if keyword not in matched_keywords:
                                    matched_keywords.append(keyword)

                        # 키워드 추출 (예: "육아휴직 관련" -> "육아휴직")
                        keyword_match = re.search(keyword_pattern, content)
                        if keyword_match:
                            extracted_keyword = keyword_match.group(1)
                            email_keywords.append(extracted_keyword)
                            # 사용자 질문의 언급된 키워드와 일치하면 높은 점수
                            if extracted_keyword in query_keywords:
                                score += exact_keyword_match_bonus
                
                # QA 검색 요청인지 확인 (YAML 설정 사용)
                elif any(k in content for k in qa_keywords_list_config):
                    score += base_score
                    match_type = "qa"

                    # 사용자 질문의 도메인 타입과 일치하면 높은 보너스 점수
                    if query_domain_type == "qa":
                        score += domain_type_match_bonus

                    # 사용자 질문에서 QA 관련 키워드를 언급했으면 점수 증가
                    qa_general_keywords = ["식단", "메뉴", "구내식당"]
                    if any(k in query_keywords for k in qa_general_keywords):
                        score += type_match_bonus

                    # 키워드 매칭 점수 (언급된 키워드가 이전 요청에도 있으면 높은 점수)
                    # 구체적 키워드(복리후생, 연차사용 등)를 우선적으로 확인
                    matched_keywords = []
                    for keyword in query_keywords:
                        if keyword in content:
                            score += keyword_match_bonus
                            matched_keywords.append(keyword)

                    # 키워드 추출 (예: "식단", "메뉴", "복리후생", "연차사용", "메뉴얼")
                    qa_keywords_in_content = [k for k in qa_keywords_list_config if k in content]
                    if qa_keywords_in_content:
                        qa_keywords.extend(qa_keywords_in_content)
                        # 사용자 질문의 키워드와 일치하는 QA 키워드가 있으면 높은 점수
                        for qa_kw in qa_keywords_in_content:
                            if qa_kw in query_keywords:
                                score += exact_keyword_match_bonus
                                if qa_kw not in matched_keywords:
                                    matched_keywords.append(qa_kw)

                    # 정규식 패턴으로도 키워드 추출 시도 (예: "복리후생 관련" -> "복리후생")
                    keyword_match = re.search(keyword_pattern, content)
                    if keyword_match:
                        extracted_keyword = keyword_match.group(1)
                        if extracted_keyword not in qa_keywords:
                            qa_keywords.append(extracted_keyword)
                        # 사용자 질문의 언급된 키워드와 일치하면 높은 점수
                        if extracted_keyword in query_keywords:
                            score += exact_keyword_match_bonus
                            if extracted_keyword not in matched_keywords:
                                matched_keywords.append(extracted_keyword)

                    # 모든 키워드가 일치하는 경우 추가 보너스 (정확한 매칭)
                    # 사용자 질문의 모든 키워드가 이전 요청에 포함되어 있으면 매우 높은 점수
                    keyword_matching_config = matching_config.get("keyword_matching", {})
                    min_keyword_length = keyword_matching_config.get("min_keyword_length", 2)
                    all_keywords_match_multiplier = scoring_weights.get("all_keywords_match_multiplier", 2)

                    if len(query_keywords) > 1:
                        all_keywords_match = all(kw in content for kw in query_keywords if len(kw) >= min_keyword_length)
                        if all_keywords_match:
                            score += exact_keyword_match_bonus * all_keywords_match_multiplier  # 모든 키워드 일치 보너스

                    # 매칭된 키워드를 best_match에 저장 (나중에 답변 생성 시 사용)
                    if matched_keywords:
                        # best_match에 matched_keywords 정보 추가 (임시로 score에 포함)
                        pass  # matched_keywords는 나중에 mentioned_keywords로 사용됨

                # 가장 높은 점수의 매칭 저장
                if score > best_score:
                    best_score = score
                    best_match = {
                        "content": content,
                        "type": match_type,
                        "score": score,
                        "matched_keywords": matched_keywords if matched_keywords else []
                    }

        # 가장 관련성 높은 매칭 처리
        if best_match and best_score > 0:
            found_previous_request = True
            request_details.append(best_match["content"])

            if best_match["type"] == "email":
                email_search_found = True
            elif best_match["type"] == "qa":
                qa_search_found = True
        
        # assistant 메시지에서 이전 검색 결과 확인 (email_search_context가 없는 경우)
        # 성능 최적화: 최근 메시지만 확인 (YAML 설정 사용)
        email_results_max = search_params.get("email_results_max_count", 5)
        if not previous_email_results:
            # YAML에서 정규식 패턴 로드
            regex_patterns = matching_config.get("regex_patterns", {})
            if not regex_patterns:
                raise ValueError("YAML에 regex_patterns가 정의되어 있지 않습니다.")
            email_doc_number_pattern = regex_patterns.get("email_doc_number", "")
            email_title_pattern = regex_patterns.get("email_title", "")
            if not email_doc_number_pattern or not email_title_pattern:
                raise ValueError("YAML에 email_doc_number 또는 email_title 패턴이 정의되어 있지 않습니다.")

            for msg in recent_messages[:email_results_max]:
                content = msg.get("content", "")
                role = msg.get("role", "")
                
                # assistant 메시지에서 이메일 검색 결과 찾기
                if role == "assistant" and ("메일" in content or "이메일" in content):
                    # 문서번호 패턴 찾기 (YAML 설정 사용)
                    doc_numbers = re.findall(email_doc_number_pattern, content)
                    if doc_numbers:
                        # 제목 추출 시도 (YAML 설정 사용)
                        titles = re.findall(email_title_pattern, content)
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
            # best_match에서 매칭된 키워드 우선 사용 (가장 정확함)
            if best_match and best_match.get("matched_keywords"):
                mentioned_keywords = best_match["matched_keywords"]
            # 사용자 질문에서 직접 추출한 키워드 사용
            elif query_keywords:
                # query_keywords에서 도메인 타입 키워드("이메일", "식단" 등) 제외하고 구체적 키워드만 사용
                specific_keywords = [kw for kw in query_keywords if kw not in ["이메일", "메일", "식단", "메뉴", "구내식당"]]
                if specific_keywords:
                    mentioned_keywords = specific_keywords
                else:
                    mentioned_keywords = query_keywords
            # 이메일 또는 QA 키워드 중 하나 선택 (fallback)
            elif email_keywords:
                mentioned_keywords = email_keywords
            elif qa_keywords:
                mentioned_keywords = qa_keywords
            else:
                mentioned_keywords = []
        
        # 대화식 답변 생성 (이전 검색 결과 포함) - YAML 설정 사용
        response_messages = matching_config.get("response_messages", {})
        check_type_mapping = matching_config.get("check_type_mapping", {})

        if found_previous_request and qa_search_found:
            # QA 검색 (식단 등) 관련 이전 요청 확인
            if mentioned_keywords:
                keywords_str = ", ".join(set(mentioned_keywords))
                response = response_messages.get("previous_qa_request", "네, 이전에 '{keywords}' 관련 내용을 요청하신 적이 있습니다.").format(keywords=keywords_str)
            elif qa_keywords:
                keywords_str = ", ".join(set(qa_keywords))
                response = response_messages.get("previous_qa_request", "네, 이전에 '{keywords}' 관련 내용을 요청하신 적이 있습니다.").format(keywords=keywords_str)
            else:
                response = response_messages.get("previous_qa_menu", "네, 이전에 식단/메뉴 관련 요청을 하신 적이 있습니다.")
            
            # 이전 요청과 답변 함께 출력 (best_match가 있으면 항상 출력)
            if best_match and best_match.get("content"):
                response += "\n\n**이전 대화 내역:**\n"

                # 이전 요청 내용 출력
                if previous_qa_query:
                    response += f"**요청:** {previous_qa_query}\n\n"
                elif request_details and len(request_details) > 0:
                    # request_details에서 이전 요청 내용 가져오기
                    response += f"**요청:** {request_details[0]}\n\n"
                elif best_match.get("content"):
                    # best_match에서 이전 요청 내용 가져오기
                    response += f"**요청:** {best_match['content']}\n\n"

                # 이전 답변 내용 출력
                if previous_qa_results:
                    # 이전 답변의 핵심 내용만 추출 (너무 길면 생략)
                    if len(previous_qa_results) > 500:
                        response += f"**답변:** {previous_qa_results[:500]}...\n"
                    else:
                        response += f"**답변:** {previous_qa_results}\n"
                else:
                    # conversation_history에서 이전 답변 찾기
                    # conversation_history는 시간 순서대로 정렬되어 있음
                    found_answer = False
                    best_match_content = best_match.get("content", "").strip()

                    # 사용자 질문에서 추출한 키워드들 (정확한 매칭을 위해)
                    keyword_matching_config = matching_config.get("keyword_matching", {})
                    min_keyword_length = keyword_matching_config.get("min_keyword_length", 2)
                    query_keywords_for_matching = [kw for kw in query_keywords if len(kw) >= min_keyword_length]

                    # 이전 요청을 찾을 때 우선순위:
                    # 1. 정확히 일치하는 요청
                    # 2. 모든 키워드가 포함된 요청
                    # 3. 일부 키워드가 포함된 요청
                    candidate_matches = []

                    for i, msg in enumerate(conversation_history):
                        if msg.get("role") != "user":
                            continue

                        msg_content = msg.get("content", "").strip()

                        # 정확히 일치하는 경우 (최우선)
                        if msg_content == best_match_content:
                            candidate_matches.append((i, msg_content, 100, "exact"))
                        # 모든 키워드가 포함된 경우
                        elif query_keywords_for_matching and all(kw in msg_content for kw in query_keywords_for_matching):
                            matched_count = sum(1 for kw in query_keywords_for_matching if kw in msg_content)
                            candidate_matches.append((i, msg_content, 80 + matched_count * 5, "all_keywords"))
                        # 일부 키워드가 포함된 경우
                        elif best_match_content in msg_content or msg_content in best_match_content:
                            matched_count = sum(1 for kw in query_keywords_for_matching if kw in msg_content) if query_keywords_for_matching else 1
                            candidate_matches.append((i, msg_content, 50 + matched_count * 5, "partial"))

                    # 점수가 높은 순으로 정렬
                    candidate_matches.sort(key=lambda x: x[2], reverse=True)

                    # 가장 높은 점수의 매칭에서 답변 찾기
                    max_candidate_messages = keyword_matching_config.get("max_candidate_messages", 5)
                    exclude_patterns = keyword_matching_config.get("exclude_history_check_patterns", [])

                    for i, msg_content, score, match_type in candidate_matches:
                        # 다음 메시지들 중 assistant 메시지 찾기
                        for j in range(i + 1, min(i + max_candidate_messages, len(conversation_history))):
                            next_msg = conversation_history[j]
                            if next_msg.get("role") == "assistant":
                                assistant_content = next_msg.get("content", "")
                                if assistant_content:
                                    # 이전 대화 확인 질문이 아닌 실제 답변인지 확인
                                    # YAML에서 정의한 패턴으로 제외
                                    if not any(pattern in assistant_content for pattern in exclude_patterns):
                                        # 너무 긴 답변은 일부만 표시
                                        if len(assistant_content) > 500:
                                            response += f"**답변:** {assistant_content[:500]}...\n"
                                        else:
                                            response += f"**답변:** {assistant_content}\n"
                                        found_answer = True
                                        break
                        if found_answer:
                            break

                    # 답변을 찾지 못한 경우
                    if not found_answer:
                        response += "**답변:** (이전 답변을 찾을 수 없습니다)\n"
        elif found_previous_request and email_search_found:
            if mentioned_keywords:
                keywords_str = ", ".join(set(mentioned_keywords))
                response = response_messages.get("previous_email_request", "네, 이전에 '{keywords}' 관련 내용을 이메일에서 찾아달라는 요청을 하신 적이 있습니다.").format(keywords=keywords_str)
            elif email_keywords:
                keywords_str = ", ".join(set(email_keywords))
                response = response_messages.get("previous_email_request", "네, 이전에 '{keywords}' 관련 내용을 이메일에서 찾아달라는 요청을 하신 적이 있습니다.").format(keywords=keywords_str)
            else:
                response = response_messages.get("previous_email_search", "네, 이전에 이메일에서 검색/찾기 요청을 하신 적이 있습니다.")
            
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
                more_help_msg = response_messages.get("more_email_help", "\n더 많은 메일을 검색하거나 특정 메일의 상세 내용을 원하신다면 말씀해 주세요! 😊")
                response += more_help_msg
            elif request_details:
                # 검색 결과는 없지만 요청은 있었던 경우
                last_request = request_details[0]  # 가장 최근 요청
                response += f"\n\n이전 요청: '{last_request}'"
        elif found_previous_request:
            if mentioned_keywords:
                keywords_str = ", ".join(set(mentioned_keywords))
                response = response_messages.get("previous_generic_request", "네, 이전에 '{keywords}' 관련 요청을 하신 적이 있습니다.").format(keywords=keywords_str)
            else:
                response = response_messages.get("previous_generic_search", "네, 이전에 검색/찾기 요청을 하신 적이 있습니다.")
        else:
            # LLM이 확인 질문으로 분류했지만 이전 요청을 찾지 못한 경우 (YAML 설정 사용)
            if history_check_result and history_check_result.check_type:
                request_type = check_type_mapping.get(history_check_result.check_type, "해당 요청")
                response = response_messages.get("no_previous_request", "아니요, 이전에 {request_type} 요청은 없었습니다.").format(request_type=request_type)
            else:
                response = response_messages.get("no_email_search", "아니요, 이전에 이메일에서 검색/찾기 요청은 없었습니다.")
        
        # orchestrator에서 직접 답변 반환 (QA 마스터 그래프로 라우팅하지 않음)
        # conversation_history에 assistant 메시지 추가
        # 주의: operator.add를 사용하므로 기존 conversation_history에 추가만 하면 됨
        assistant_message: Message = {
            "role": "assistant",
            "content": response,
            "timestamp": datetime.now().isoformat()
        }
        
        # 기존 conversation_history에 새 메시지만 추가 (operator.add 사용)
        # user_message는 이미 state에 추가되어 있을 수 있으므로 확인
        new_messages = []
        if not conversation_history or conversation_history[-1].get("role") != "user":
            new_messages.append(user_message)
        new_messages.append(assistant_message)

        result = {
            "domain": None,  # 명시적으로 None으로 설정하여 END로 라우팅
            "final_response": response,
            "conversation_history": new_messages,  # operator.add로 기존 히스토리에 추가됨
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
    
    # 키워드 기반 빠른 라우팅: "메뉴얼" 키워드가 있으면 qa로 강제 라우팅 (LLM 호출 전)
    step_start = time.time()
    manual_keywords = ["메뉴얼", "매뉴얼", "manual"]
    if any(k in query for k in manual_keywords):
        # "메뉴얼에서"와 "메일에서" 구분
        if "메뉴얼에서" in query or "매뉴얼에서" in query or "메뉴얼" in query or "매뉴얼" in query:
            print(f"✅ [키워드 기반 빠른 라우팅] '메뉴얼' 키워드 감지 - qa 도메인으로 강제 라우팅")
            total_elapsed = (time.time() - route_start_time) * 1000
            _routing_logger.info(f"[라우팅 완료] {total_elapsed:.2f}ms (메뉴얼 키워드로 qa 도메인으로 종료)")
            return {
                "domain": "qa",
                "conversation_history": [user_message]
            }

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
    
    # 유효성 검사 (YAML 설정 사용)
    config = load_orchestrator_config()
    search_params = config.get("search_params", {})
    routing_config = search_params.get("routing", {})
    fallback_domain = routing_config.get("fallback_domain", "qa")

    if domain not in MASTER_GRAPHS:
        print(f"⚠️ 알 수 없는 도메인: {domain}, {fallback_domain}로 fallback")
        domain = fallback_domain
    
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
    # 도메인별 완료 메시지 로드 (YAML 설정 사용)
    config = load_orchestrator_config()
    search_params = config.get("search_params", {})
    routing_config = search_params.get("routing", {})
    completion_messages = routing_config.get("domain_completion_messages", {})

    async def schedule_node(state: OrchestratorState) -> Dict[str, Any]:
        """스케줄 마스터그래프 실행."""
        graph = get_schedule_master_graph()
        result = await graph.ainvoke(state)
        default_msg = completion_messages.get("schedule", "스케줄 작업을 완료했습니다.")
        return process_domain_node_result(state, result, "schedule", default_msg)
    
    async def document_node(state: OrchestratorState) -> Dict[str, Any]:
        """문서 마스터그래프 실행."""
        graph = get_document_master_graph()
        result = await graph.ainvoke(state)
        default_msg = completion_messages.get("document", "문서 작업을 완료했습니다.")
        return process_domain_node_result(state, result, "document", default_msg)
    
    async def qa_node(state: OrchestratorState) -> Dict[str, Any]:
        """QA 마스터그래프 실행."""
        graph = get_qa_master_graph()
        result = await graph.ainvoke(state)
        default_msg = completion_messages.get("qa", "질문에 답변했습니다.")
        return process_domain_node_result(state, result, "qa", default_msg)
    
    async def email_node(state: OrchestratorState) -> Dict[str, Any]:
        """이메일 마스터그래프 실행."""
        print("--- [Orchestrator] Executing Email Master Graph ---")
        graph = get_email_master_graph()
        result = await graph.ainvoke(state)
        default_msg = completion_messages.get("email", "이메일 작업을 완료했습니다.")
        return process_domain_node_result(state, result, "email", default_msg)
    
    async def tech_node(state: OrchestratorState) -> Dict[str, Any]:
        """기술 마스터그래프 실행."""
        graph = get_tech_master_graph()
        result = await graph.ainvoke(state)
        default_msg = completion_messages.get("tech", "기술 검색 작업을 완료했습니다.")
        return process_domain_node_result(state, result, "tech", default_msg)

    async def recruitment_node(state: OrchestratorState) -> Dict[str, Any]:
        """채용 마스터그래프 실행."""
        print("--- [Orchestrator] Executing Recruitment Master Graph ---")
        graph = get_recruitment_master_graph()
        result = await graph.ainvoke(state)
        default_msg = completion_messages.get("recruitment", "채용 작업을 완료했습니다.")
        return process_domain_node_result(state, result, "recruitment", default_msg)
    
    # 각 도메인별 노드 등록
    workflow.add_node("schedule", schedule_node)
    workflow.add_node("document", document_node)
    workflow.add_node("qa", qa_node)
    workflow.add_node("email", email_node)
    workflow.add_node("tech", tech_node)
    workflow.add_node("recruitment", recruitment_node)

    print(f"✅ 마스터그래프 등록 완료: schedule, document, qa, email, tech, recruitment")
    
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
        
        # domain이 없거나 None이면 기본값 사용 (YAML 설정 사용)
        config = load_orchestrator_config()
        search_params = config.get("search_params", {})
        routing_config = search_params.get("routing", {})
        fallback_domain_on_empty = routing_config.get("fallback_domain_on_empty", "document")
        fallback_domain = routing_config.get("fallback_domain", "qa")

        if not domain:
            print(f"⚠️ domain이 state에 없습니다. {fallback_domain_on_empty}로 fallback")
            domain = fallback_domain_on_empty
        
        # 유효성 검사
        if domain not in MASTER_GRAPHS:
            print(f"⚠️ 알 수 없는 도메인: {domain}, {fallback_domain}로 fallback")
            domain = fallback_domain
        
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

