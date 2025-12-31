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
import re
from datetime import datetime
from pathlib import Path

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState, Message
from agents.llm_router import classify_intent_with_llm
from agents.structured_output import extract_structured_output, QueryIntent, WorkflowDetectionResult
from agents.redis_optimizer import optimize_state_before_save
from agents.domain_node_handler import process_domain_node_result
from agents.utils import check_detailed_response_in_history, get_clarification_message
from agents.history_check_handler import detect_history_check_query, handle_history_check_query
from agents.ml_classifier import classify_workflow_request, is_model_available
from agents.master_graphs import (
    get_schedule_master_graph,
    get_document_master_graph,
    get_qa_master_graph,
    get_email_master_graph,
    get_tech_master_graph,
    get_recruitment_master_graph,
)
from agents.workflows import get_workflow_handler


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
_workflows_config: Optional[Dict[str, Any]] = None
_workflow_classification_config: Optional[Dict[str, Any]] = None

def _load_orchestrator_prompts_sync() -> Dict[str, Any]:
    """오케스트레이터 프롬프트를 YAML 파일에서 동기적으로 로드합니다.
    
    모듈 로드 시 또는 첫 호출 시 실행됩니다.
    common_prompts.yaml 파일에서 orchestrator 섹션을 로드합니다.
    
    Returns:
        오케스트레이터 프롬프트 딕셔너리
    """
    global _orchestrator_prompts
    
    if _orchestrator_prompts is not None:
        return _orchestrator_prompts
    
    # common_prompts.yaml 파일 경로 찾기
    current_dir = Path(__file__).parent
    yaml_path = current_dir / "common_prompts.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        all_prompts = yaml.safe_load(f)
    
    _orchestrator_prompts = all_prompts.get("orchestrator", {})
    _orchestrator_config = all_prompts.get("orchestrator_config", {})
    
    if not _orchestrator_prompts:
        raise ValueError("common_prompts.yaml에 'orchestrator' 섹션이 없습니다.")
    
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
    yaml_path = current_dir / "common_prompts.yaml"
    
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
    llm_router의 load_agent_prompts 함수를 사용하여 모든 분리된 yaml 파일을 병합합니다.

    Returns:
        전체 프롬프트 딕셔너리
    """
    from agents.llm_router import load_agent_prompts
    return load_agent_prompts()

# 모듈 로드 시 프롬프트 미리 로드 (첫 요청 시 지연 방지)
try:
    _load_orchestrator_prompts_sync()
    print("✅ Orchestrator 프롬프트 사전 로드 완료")
except Exception as e:
    # 모듈 로드 시 오류가 발생해도 나중에 다시 시도할 수 있도록 무시
    print(f"[WARNING] Orchestrator 프롬프트 사전 로드 실패 (나중에 다시 시도): {e}")


def load_workflows_config() -> Dict[str, Any]:
    """워크플로우 설정을 YAML 파일에서 로드합니다.
    
    Returns:
        워크플로우 설정 딕셔너리
    """
    global _workflows_config
    
    if _workflows_config is not None:
        return _workflows_config
    
    current_dir = Path(__file__).parent
    yaml_path = current_dir / "workflows" / "workflows.yaml"
    
    if not yaml_path.exists():
        print(f"⚠️ 워크플로우 파일을 찾을 수 없습니다: {yaml_path}")
        return {}
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        _workflows_config = yaml.safe_load(f)
    
    return _workflows_config


def load_workflow_classification_config() -> Dict[str, Any]:
    """워크플로우 분류 설정을 YAML 파일에서 로드합니다.
    
    Returns:
        워크플로우 분류 설정 딕셔너리
    """
    global _workflow_classification_config
    
    if _workflow_classification_config is not None:
        return _workflow_classification_config
    
    current_dir = Path(__file__).parent
    yaml_path = current_dir / "workflow_classification.yaml"
    
    if not yaml_path.exists():
        print(f"⚠️ 워크플로우 분류 파일을 찾을 수 없습니다: {yaml_path}")
        return {}
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        _workflow_classification_config = yaml.safe_load(f)
    
    return _workflow_classification_config


# 키워드 기반 워크플로우 감지는 더 이상 사용하지 않음 (LLM 기반만 사용)
# 이전에 사용하던 detect_workflow_type_keyword 함수는 제거되었습니다.


async def classify_request_type(query: str) -> Dict[str, Any]:
    """1단계: 사용자 요청을 워크플로우 요청과 일반 요청으로 분류합니다.
    
    ML 모델을 우선 사용하고, 사용 불가능한 경우 LLM fallback을 사용합니다.

    Args:
        query: 사용자 쿼리
        
    Returns:
        {
            'is_workflow_request': bool,
            'confidence': float,
            'output': int  # 1: 워크플로우, 0: 일반
        }
    """
    step_start = time.time()

    # ML 모델 사용 가능 여부 확인 (조용히 시도)
    try:
        if is_model_available():
            # ML 모델로 분류 (동기 함수이지만 빠르므로 await 불필요)
            result = classify_workflow_request(query)

            step_elapsed = (time.time() - step_start) * 1000

            if result.get('method') == 'ml':
                step_elapsed_sec = step_elapsed / 1000
                print(f"✅ 1단계 분류 (ML): {'워크플로우' if result['is_workflow_request'] else '일반 요청'} (신뢰도: {result['confidence']:.2f}, {step_elapsed_sec:.2f}초)")
                return {
                    "is_workflow_request": result['is_workflow_request'],
                    "confidence": result['confidence'],
                    "output": 1 if result['is_workflow_request'] else 0
                }
            # ML 모델 fallback 발생 시 조용히 LLM으로 전환 (로그 없음)
    except Exception as e:
        # ML 모델 오류 시 조용히 LLM으로 fallback
        logging.debug(f"ML 모델 분류 실패: {e}, LLM으로 fallback")

    # LLM fallback
    classification_config = load_workflow_classification_config()
    request_classification = classification_config.get("request_classification", {})
    
    if not request_classification:
        step_elapsed = (time.time() - step_start) * 1000
        print(f"⚠️ 워크플로우 분류 설정을 찾을 수 없습니다. 기본값(일반 요청)으로 처리 ({step_elapsed/1000:.2f}초)")
        return {"is_workflow_request": False, "confidence": 0.5, "output": 0}
    
    system_prompt_template = request_classification.get("system_prompt", "")
    if not system_prompt_template:
        step_elapsed = (time.time() - step_start) * 1000
        print(f"⚠️ 워크플로우 분류 프롬프트를 찾을 수 없습니다. 기본값(일반 요청)으로 처리 ({step_elapsed/1000:.2f}초)")
        return {"is_workflow_request": False, "confidence": 0.5, "output": 0}
    
    # 프롬프트에 query 삽입
    system_prompt = system_prompt_template.format(query=query)
    
    try:
        result = await extract_structured_output(
            query=query,
            model_class=WorkflowDetectionResult,
            system_prompt=system_prompt,
            temperature=0.0
        )
        
        step_elapsed = (time.time() - step_start) * 1000
        print(f"✅ 1단계 분류 (LLM): {'워크플로우' if result.is_workflow_request else '일반 요청'} (신뢰도: {result.confidence:.2f}, {step_elapsed/1000:.2f}초)")
        return {
            "is_workflow_request": result.is_workflow_request,
            "confidence": result.confidence,
            "output": 1 if result.is_workflow_request else 0
        }
    except Exception as e:
        step_elapsed = (time.time() - step_start) * 1000
        print(f"⚠️ 워크플로우 분류 중 오류 발생: {e}, 기본값(일반 요청)으로 처리 ({step_elapsed/1000:.2f}초)")
        return {"is_workflow_request": False, "confidence": 0.5, "output": 0}


async def detect_workflow_type_with_llm(query: str) -> Optional[Dict[str, Any]]:
    """LLM 기반 워크플로우 타입 감지.
    
    Args:
        query: 사용자 쿼리
        
    Returns:
        {'workflow_type': str, 'confidence': float} 또는 None
    """
    step_start = time.time()
    workflows = load_workflows_config()
    
    for workflow_type, workflow_config in workflows.get("workflows", {}).items():
        detection_prompt_template = workflow_config.get("detection_prompt", "")
        if not detection_prompt_template:
            continue
        
        # 프롬프트에 query 삽입
        detection_prompt = detection_prompt_template.format(query=query)
        
        try:
            result = await extract_structured_output(
                query=query,
                model_class=WorkflowDetectionResult,
                system_prompt=detection_prompt,
                temperature=0.0
            )
            
            if result.is_workflow_request and result.workflow_type == workflow_type and result.confidence >= 0.8:
                step_elapsed = (time.time() - step_start) * 1000
                print(f"✅ LLM 기반 워크플로우 감지: {workflow_type} (신뢰도: {result.confidence:.2f}, {step_elapsed/1000:.2f}초)")
                return {
                    "workflow_type": workflow_type,
                    "confidence": result.confidence
                }
        except Exception as e:
            print(f"⚠️ 워크플로우 감지 중 오류 발생 ({workflow_type}): {e}")
            continue
    
    step_elapsed = (time.time() - step_start) * 1000
    print(f"⚠️ 워크플로우 타입 감지 실패 ({step_elapsed/1000:.2f}초)")
    return None


async def detect_workflow_type(query: str) -> Optional[Dict[str, Any]]:
    """워크플로우 타입 감지 (LLM 기반만 사용).
    
    LLM을 사용하여 사용자 질의를 분석하고 워크플로우 실행 요청인지 판단합니다.
    키워드 기반 감지는 사용하지 않습니다.
    
    Args:
        query: 사용자 쿼리
        
    Returns:
        {'workflow_type': str, 'confidence': float} 또는 None
    """
    # LLM 기반 감지만 사용
    llm_result = await detect_workflow_type_with_llm(query)
    return llm_result


async def extract_workflow_data(query: str, workflow_type: str, current_datetime: Optional[datetime] = None) -> Dict[str, Any]:
    """워크플로우 실행에 필요한 데이터를 추출합니다."""
    workflows = load_workflows_config()
    workflow_config = workflows.get("workflows", {}).get(workflow_type)
    
    if not workflow_config:
        return {}
    
    data_extraction_prompt = workflow_config.get("data_extraction_prompt", "")
    if not data_extraction_prompt:
        return {}
    
    try:
        from agents.structured_output import ScheduleParams
        
        # 출장/휴가 워크플로우의 경우 ScheduleParams로 데이터 추출
        if workflow_type == "business_trip":
            result = await extract_structured_output(
                query=query,
                model_class=ScheduleParams,
                system_prompt=data_extraction_prompt,
                temperature=0.0
            )
            
            extracted_data = {}
            if result.start_date:
                extracted_data["start_date"] = result.start_date
            if result.end_date:
                extracted_data["end_date"] = result.end_date
            if result.destination:
                extracted_data["destination"] = result.destination
            if result.reason:
                extracted_data["reason"] = result.reason
            
            # 현재 날짜 정보를 프롬프트에 추가
            if current_datetime:
                from datetime import datetime
                now = current_datetime
                today_str = now.strftime("%Y-%m-%d")
                data_extraction_prompt = f"""{data_extraction_prompt}

**⚠️ 매우 중요: 현재 날짜 정보**
- 현재 날짜: {today_str}
- 현재 연도: {now.year}년
- 반드시 현재 연도({now.year}년)를 사용하여 날짜를 추출하세요.
- 2023년, 2024년 등 과거 연도를 절대 사용하지 마세요.
"""

            return extracted_data
    except Exception as e:
        print(f"⚠️ 워크플로우 데이터 추출 실패: {e}")
        return {}
    
    return {}


def check_tech_ask_source_state(query: str, state: OrchestratorState, user_message: Message) -> Optional[Dict[str, Any]]:
    """Tech 도메인의 ask_source 상태를 확인하고 tech 도메인으로 유지할지 결정."""
    
    # 먼저 recruitment 컨텍스트 확인 (우선순위)
    # 번호 선택 패턴이고 recruitment 결과가 있으면 tech로 라우팅하지 않음
    recruitment_context = state.get("recruitment_context", {})
    if recruitment_context:
        resume_search = recruitment_context.get("resume_search", {})
        last_results = resume_search.get("last_results", [])
        if last_results:
            number_pattern = re.compile(r'^\d+번?\s*(자세히|알려줘|보여줘|설명|상세|보기|요약)?', re.IGNORECASE)
            if number_pattern.match(query.strip()) or (query.strip().isdigit() and len(query.strip()) <= 2):
                # recruitment 결과가 있으면 tech로 라우팅하지 않음
                return None
    
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
    
    # "1번", "2번" 같은 번호 선택이 특허 검색 결과 선택일 수 있음을 확인
    if conversation_history and len(conversation_history) >= 2:
        # 최근 대화에서 특허 검색 결과가 있었는지 확인
        recent_messages = conversation_history[-5:]  # 최근 5개 메시지 확인 (더 넓은 범위)
        has_patent_search_results = False
        has_number_selection_pattern = False
        
        # 번호 선택 패턴 확인 (예: "1번", "1번 자세히", "1번 알려줘", "1", "1번 보여줘" 등)
        number_pattern = re.compile(r'^\d+번?\s*(자세히|알려줘|보여줘|설명|상세|보기)?', re.IGNORECASE)
        if number_pattern.match(query.strip()) or (query.strip().isdigit() and len(query.strip()) <= 2):
            has_number_selection_pattern = True
        
        # 최근 대화에서 특허 검색 결과 메시지 확인
        for msg in recent_messages:
            content = msg.get("content", "")
            role = msg.get("role", "")
            
            # assistant 메시지에서 특허 검색 결과 패턴 확인
            if role == "assistant":
                # 특허 검색 결과 패턴: "특허 X개를 찾았습니다", "다음 특허 중에서", "번호를 입력하세요" 등
                patent_result_patterns = [
                    r'특허\s*\d+개',
                    r'다음\s*특허\s*중에서',
                    r'번호를\s*입력하세요',
                    r'자세히\s*보려면\s*번호를',
                    r'선택해주세요',
                    r'특허\s*중에서\s*자세히',
                    r'관련\s*특허\s*\d+개',
                    r'찾았습니다',
                ]
                for pattern in patent_result_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        has_patent_search_results = True
                        break
            
            # user 메시지에서 특허 검색 요청 확인
            if role == "user":
                patent_keywords = ["특허", "patent", "특허를", "특허 찾아", "특허 검색", "바이러스 특허"]
                if any(kw in content for kw in patent_keywords):
                    has_patent_search_results = True
        
        # 번호 선택 패턴이 있고 특허 검색 결과가 있었으면 tech 도메인으로 유지
        if has_number_selection_pattern and has_patent_search_results:
            print(f"✅ Tech 도메인 대화 히스토리 기반 감지: '{query}' → tech 도메인 (특허 검색 결과 선택)")
            return {
                "domain": "tech",
                "conversation_history": [user_message]
            }
    
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


def get_domain_classification_prompt(context: str = "", last_domain: Optional[str] = None) -> str:
    """도메인 분류를 위한 프롬프트를 반환합니다.
    
    Args:
        context: 이전 대화 내용 컨텍스트
        last_domain: 이전 대화의 도메인 (선택적)
        
    Returns:
        시스템 프롬프트 문자열
    """
    prompts = load_orchestrator_prompts()
    domain_prompt = prompts.get("prompts", {}).get("domain_classification", {}).get("system_prompt", "")
    
    if not domain_prompt:
        raise ValueError("orchestrator.prompts.domain_classification.system_prompt가 없습니다.")
    
    # 이전 도메인 정보 구성
    if last_domain:
        domain_names = {
            "schedule": "일정 관리",
            "document": "문서 작성",
            "qa": "질문/문의",
            "email": "이메일",
            "tech": "기술 검색",
            "recruitment": "채용"
        }
        domain_name = domain_names.get(last_domain, last_domain)
        previous_domain_info = f"""
**중요: 이전 대화 도메인 정보**
이전 대화에서 '{domain_name}' 도메인으로 작업을 수행했습니다.

**이전 도메인 고려 사항:**
- 사용자의 현재 질의가 이전 도메인과 관련된 후속 질문일 가능성을 고려하세요.
- 예: "1번", "2번", "더 찾아줘", "자세히", "요약해줘", "1번 자세히 알려줘" 같은 짧은 질의는 이전 도메인의 연속일 가능성이 높습니다.
- 하지만 명시적으로 다른 도메인을 언급하면 새로운 요청으로 처리하세요.
- 숫자로 시작하는 질문(예: "1번", "2번")은 이전 검색 결과의 상세 요청일 가능성이 매우 높습니다.

"""
        # context 앞에 이전 도메인 정보 추가
        context = previous_domain_info + context
    
    # 플레이스홀더 치환
    placeholder = prompts.get("prompts", {}).get("domain_classification", {}).get("context_placeholder", "{context}")
    prompt = domain_prompt.replace(placeholder, context)
    
    return prompt


async def route_to_domain(state: OrchestratorState) -> Dict[str, Any]:
    """사용자 요청을 분석하여 도메인만 결정 (AI-001: Intent Router).
    
    오케스트레이터는 도메인 레벨에서만 라우팅하고,
    세부 처리는 각 마스터그래프에서 담당합니다.
    
    라우팅 방법:
    1. 이메일 수신자 선택 처리 (우선순위 높음)
    2. Tech 도메인 ask_source 상태 확인 (특수 케이스)
    3. 이전 대화 확인 질문 처리 (키워드 기반 + LLM)
    4. 단순 선택지 처리 (특수 케이스)
    5. Structured Output Parser (LLM + YAML 프롬프트) - 주요 방법
    6. LLM 라우터 (LLM + YAML 프롬프트) - fallback
    
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
    
    # ✅ user_message를 함수 시작 부분에서 항상 정의 (모든 경로에서 사용 가능하도록)
    user_message: Message = {
        "role": "user",
        "content": query,
        "timestamp": datetime.now().isoformat()
    }
    
    # ✅ 이메일 수신자 선택 처리 (우선순위 높음)
    email_compose_context = state.get("email_compose_context") or {}
    pending_selection = email_compose_context.get("pending_recipient_selection", {})

    if pending_selection:
        pick_pattern = re.compile(r'^\s*(\d+)\s*번?\s*$')
        match = pick_pattern.match(query)

        if match:
            print(f"✅ 이메일 수신자 선택 요청 감지: '{query}' → email 도메인으로 라우팅")
            _routing_logger.info(f"[라우팅 완료] 이메일 수신자 선택: email (요청: {query})")
            # 이미 위에서 정의된 user_message 사용 (중복 정의 제거)
            return {
                "domain": "email",
                "conversation_history": [user_message]
            }

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
    
    # 1. state에서 직접 확인
    workflow_in_progress = state.get("workflow_in_progress", False)
    workflow_type = state.get("workflow_type")
    workflow_steps = state.get("workflow_steps") or [] # ✅ None 방지
    
    # current_workflow_step_index가 state에 None으로 저장되어 있을 수 있으므로 방어적 처리
    current_step_index = state.get("current_workflow_step_index")
    if current_step_index is None:
        current_step_index = 0
    else:
        current_step_index = int(current_step_index)
        
    workflow_temp_data = state.get("workflow_temp_data") or {} # ✅ None 방지

    # 디버깅: 워크플로우 상태 확인
    print(f"🔍 [워크플로우 상태 확인] workflow_in_progress={workflow_in_progress}, workflow_type={workflow_type}, workflow_steps={workflow_steps}, current_step_index={current_step_index}, last_domain={last_domain}")

    # 2. 워크플로우 관련 필드가 있으면 워크플로우 진행 중으로 간주 (Redis에서 로드 시 누락될 수 있음)
    # ✅ 중요: workflow_in_progress가 명시적으로 False인 경우(워크플로우 완료 후)에는 복구하지 않음
    if not workflow_in_progress:
        # state에서 명시적으로 workflow_in_progress=False로 설정되어 있으면 복구하지 않음
        explicit_workflow_in_progress = state.get("workflow_in_progress")
        if explicit_workflow_in_progress is False:
            print(f"ℹ️ 워크플로우 완료 상태 확인: workflow_in_progress=False, 일반 라우팅으로 진행")
            # 워크플로우 정보를 명시적으로 초기화하여 복구 방지
            workflow_type = None
            workflow_steps = []
            current_step_index = 0
            workflow_temp_data = {}
            state["workflow_type"] = None
            state["workflow_steps"] = []
            state["current_workflow_step_index"] = None
            state["workflow_temp_data"] = {}
        # workflow_type이나 workflow_steps가 있으면 워크플로우 진행 중으로 복구
        # 단, workflow_in_progress가 명시적으로 False가 아닌 경우에만 (None이거나 누락된 경우)
        elif workflow_type and workflow_steps and explicit_workflow_in_progress is not False:
            workflow_in_progress = True
            display_step = current_step_index + 1
            print(f"🔄 워크플로우 상태 복구: {workflow_type} (step {display_step}/{len(workflow_steps)}) from state fields")
            state["workflow_in_progress"] = True
        # Redis에서 세션 데이터를 직접 로드하여 워크플로우 상태 확인
        elif session_id:
            redis_load_success = False
            try:
                from agents.session_manager import get_session_manager
                session_manager = await get_session_manager()
                if session_manager:
                    employee_id = state.get("employee_id")
                    if employee_id:
                        session_data = await session_manager.load_session_data(session_id, employee_id)
                        if session_data:
                            redis_load_success = True
                            # ✅ Redis에서도 workflow_in_progress 확인 (명시적으로 False면 복구하지 않음)
                            redis_workflow_in_progress = session_data.get("workflow_in_progress")
                            if redis_workflow_in_progress is False:
                                print(f"ℹ️ Redis에서 워크플로우 완료 상태 확인: workflow_in_progress=False, 일반 라우팅으로 진행")
                                # Redis의 워크플로우 정보도 초기화하여 복구 방지
                                workflow_type = None
                                workflow_steps = []
                                current_step_index = 0
                                workflow_temp_data = {}
                            else:
                                redis_workflow_type = session_data.get("workflow_type")
                                redis_workflow_steps = session_data.get("workflow_steps")
                                redis_current_step = session_data.get("current_workflow_step_index", 0)
                                redis_last_domain = session_data.get("last_domain")
                                redis_workflow_temp_data = session_data.get("workflow_temp_data", {})

                                # workflow_type과 workflow_steps가 있으면 복구
                                # 단, workflow_in_progress가 명시적으로 False가 아닌 경우에만
                                if redis_workflow_type and redis_workflow_steps and redis_workflow_in_progress is not False:
                                    workflow_type = redis_workflow_type
                                    workflow_steps = redis_workflow_steps
                                    current_step_index = int(redis_current_step) if redis_current_step is not None else 0
                                    workflow_in_progress = True
                                    
                                    # ✅ Redis 데이터를 최우선으로 복구 (시나리오 1 스타일)
                                    if redis_workflow_temp_data:
                                        workflow_temp_data = redis_workflow_temp_data
                                        state["workflow_temp_data"] = redis_workflow_temp_data
                                        print(f"🔄 [라우팅] Redis에서 workflow_temp_data 복구 완료: {list(redis_workflow_temp_data.keys())}")
                                    
                                    display_step = current_step_index + 1
                                    print(f"🔄 워크플로우 상태 복구: {workflow_type} (step {display_step}/{len(workflow_steps)}) from Redis session")
                                # workflow_temp_data가 있으면 워크플로우 진행 중일 가능성이 높음
                                # 단, workflow_in_progress가 명시적으로 False가 아닌 경우에만
                                elif redis_workflow_temp_data and redis_workflow_in_progress is not False:
                                    # workflow_temp_data가 있으면 워크플로우 진행 중으로 간주
                                    # last_domain에서 워크플로우 정보 추출 시도
                                    if redis_last_domain and "step" in redis_last_domain.lower():
                                        last_domain = redis_last_domain
                                        workflow_temp_data = redis_workflow_temp_data
                                        state["workflow_temp_data"] = redis_workflow_temp_data  # state에도 설정
                                # last_domain에서 워크플로우 정보 복구
                                elif redis_last_domain and "step" in redis_last_domain.lower() and redis_workflow_in_progress is not False:
                                    last_domain = redis_last_domain
            except Exception as e:
                print(f"⚠️ Redis에서 워크플로우 상태 복구 실패: {e}")
            
            # ✅ Redis 로드 실패 시 state의 워크플로우 정보로 복구 시도
            if not redis_load_success and not workflow_in_progress:
                # state에 워크플로우 정보가 있으면 복구
                if workflow_type and workflow_steps and explicit_workflow_in_progress is not False:
                    workflow_in_progress = True
                    display_step = (current_step_index or 0) + 1
                    print(f"🔄 워크플로우 상태 복구 (Redis 실패 후 state 사용): {workflow_type} (step {display_step}/{len(workflow_steps)})")
                    state["workflow_in_progress"] = True
                # workflow_temp_data가 있으면 워크플로우 진행 중일 가능성이 높음
                elif state.get("workflow_temp_data") and explicit_workflow_in_progress is not False:
                    workflow_temp_data = state.get("workflow_temp_data", {})
                    if workflow_temp_data:
                        # last_domain에서 워크플로우 정보 추출 시도
                        if last_domain and "step" in last_domain.lower():
                            print(f"🔄 워크플로우 상태 복구 (Redis 실패 후 state의 workflow_temp_data 사용): {list(workflow_temp_data.keys())}")

        # last_domain에서 워크플로우 정보 복구
        # 단, workflow_type이나 workflow_steps가 명시적으로 없는 경우(종료된 경우)에는 복구하지 않음
        if not workflow_in_progress and not workflow_type and not workflow_steps and last_domain and "step" in last_domain.lower():
            # 워크플로우가 진행 중이 아니고 타입/단계 정보도 없는데 last_domain에만 step이 있다면,
            # 이는 방금 종료된 워크플로우의 잔재일 가능성이 높으므로 복구하지 않고 일반 라우팅 진행
            print(f"ℹ️ 종료된 워크플로우 잔재 감지 ({last_domain}). 복구하지 않고 일반 라우팅 진행.")
            # last_domain을 일반 도메인으로 정규화 (예: "Apply for a business trip step4" -> "email")
            if "business trip" in last_domain.lower() or "출장" in last_domain:
                last_domain = "schedule"
            elif "recruitment" in last_domain.lower() or "채용" in last_domain:
                last_domain = "recruitment"
            elif "document" in last_domain.lower() or "문서" in last_domain:
                last_domain = "document"
            elif "email" in last_domain.lower() or "이메일" in last_domain:
                last_domain = "email"
            else:
                last_domain = None
            
            state["last_domain"] = last_domain

        # 실제 복구 로직 (위의 필터링을 통과한 경우에만)
        if not workflow_in_progress and last_domain and "step" in last_domain.lower():
            # last_domain이 "Apply for a business trip step1" 형식이면 워크플로우 진행 중으로 간주
            # 워크플로우 타입 추출
            if "business trip" in last_domain.lower() or "출장" in last_domain:
                workflow_type = "business_trip"
            elif "recruitment" in last_domain.lower() or "채용" in last_domain:
                workflow_type = "recruitment"
            elif "meeting preparation" in last_domain.lower() or "회의 준비" in last_domain:
                workflow_type = "meeting_preparation"

            # step 번호 추출
            step_match = re.search(r'step\s*(\d+)', last_domain, re.IGNORECASE)
            if step_match:
                current_step_index = int(step_match.group(1)) - 1  # step1 -> index 0

                # 워크플로우 설정에서 steps 가져오기
                if workflow_type:
                    workflows = load_workflows_config()
                    workflow_config = workflows.get("workflows", {}).get(workflow_type)
                    if workflow_config:
                        steps = workflow_config.get("steps", [])
                        workflow_steps = [step.get("domain") for step in steps if step.get("domain")]

                        if workflow_steps and 0 <= current_step_index < len(workflow_steps):
                            workflow_in_progress = True
                            print(f"🔄 워크플로우 상태 복구: {workflow_type} (step {current_step_index + 1}/{len(workflow_steps)}) from last_domain={last_domain}")
                            # state 업데이트
                            state["workflow_in_progress"] = True
                            state["workflow_type"] = workflow_type
                            state["workflow_steps"] = workflow_steps
                            state["current_workflow_step_index"] = current_step_index

    if workflow_in_progress:
        next_domain = state.get("next_domain")
        # workflow_temp_data 가져오기 (state에서)
        workflow_temp_data = state.get("workflow_temp_data") or {}

        # ✅ None 방지 로직 강화
        workflow_type = workflow_type or state.get("workflow_type")
        workflow_steps = workflow_steps or state.get("workflow_steps") or []
        
        # current_step_index를 확실히 정수화
        current_step_index = int(current_step_index) if current_step_index is not None else 0

        display_step = current_step_index + 1
        print(f"🔄 워크플로우 진행 중 감지: {workflow_type} (step {display_step}/{len(workflow_steps) if workflow_steps else 0})")

        # "진행" 또는 "취소"/"종료" 버튼 선택 확인
        proceed_keywords = ["진행", "예", "네", "yes", "ok", "확인", "계속"]
        cancel_keywords = ["취소", "중단", "그만", "stop", "cancel", "아니오", "아니", "종료"]

        is_proceed = any(kw in query for kw in proceed_keywords)
        is_cancel = any(kw in query.lower() for kw in cancel_keywords)

        # ✅ [개선] 단순 "진행" 요청인지 아니면 도메인 특정 요청(예: "1번 면접 진행해줘")인지 구분
        # 숫자가 포함되어 있거나 쿼리가 너무 길면(예: 10자 이상) 도메인 특정 요청으로 간주하여 도메인 분류로 넘김
        is_pure_proceed = is_proceed and not is_cancel and (len(query) < 10 and not any(char.isdigit() for char in query))
        
        # 만약 "1번 면접 진행해줘" 같이 "진행"은 포함되어 있지만 번호가 있는 경우
        if is_proceed and not is_pure_proceed and any(char.isdigit() for char in query):
            print(f"ℹ️ [route_to_domain] 도메인 특정 진행 요청 감지 ('{query}') - 도메인 분류로 진행")
            is_proceed = False # 워크플로우 자동 진행 차단

        # "진행" 선택 시 다음 단계로 진행
        if is_pure_proceed:
            # ⚠️ 중요: "진행" 시 Redis에서 최신 workflow_temp_data를 다시 로드
            print(f"🔍 [route_to_domain] '진행' 키워드 감지 - session_id: {session_id}, workflow_temp_data (현재): {list(workflow_temp_data.keys()) if workflow_temp_data else '비어있음'}")

            # 결재 진행 요청인 경우 document 도메인으로 라우팅 (결재 처리는 document 도메인에서 담당)
            last_domain = state.get("last_domain")
            document_context = state.get("document_context", {})

            # 결재 완료 상태 확인: approval_pending이 False이고 approval_id가 있으면 이미 결재 완료
            is_approval_completed = (
                last_domain == "document"
                and not document_context.get("approval_pending", True)
                and document_context.get("approval_id")
            )

            if is_approval_completed:
                # 결재가 이미 완료된 상태에서는 "진행" 키워드를 일반 대화로 처리
                print(f"ℹ️ [route_to_domain] 결재가 이미 완료된 상태입니다. 일반 대화로 처리합니다.")
                # 워크플로우 진행 로직을 건너뛰고 도메인 분류로 진행
            elif last_domain == "document" and document_context.get("approval_pending"):
                # 결재 진행 요청: document 도메인으로 라우팅 (결재 처리는 document 도메인에서 담당)
                print(f"✅ [route_to_domain] 결재 진행 요청 감지 - document 도메인으로 라우팅")
                return {
                    "domain": "document",
                    "conversation_history": [user_message],
                    **current_date_info
                }

            if session_id:
                try:
                    from agents.session_manager import get_session_manager
                    session_manager = await get_session_manager()
                    if session_manager:
                        employee_id = state.get("employee_id")
                        if employee_id:
                            session_data = await session_manager.load_session_data(session_id, employee_id)
                            if session_data:
                                redis_workflow_temp_data = session_data.get("workflow_temp_data", {})
                                print(f"🔍 [route_to_domain] Redis에서 로드한 workflow_temp_data: {list(redis_workflow_temp_data.keys()) if redis_workflow_temp_data else '비어있음'}")
                                if redis_workflow_temp_data:
                                    workflow_temp_data = redis_workflow_temp_data
                                    state["workflow_temp_data"] = redis_workflow_temp_data
                                    print(f"🔍 [route_to_domain] '진행' 입력 시 Redis에서 최신 workflow_temp_data 로드: {list(workflow_temp_data.keys())}")
                                else:
                                    print(f"⚠️ [route_to_domain] Redis에 workflow_temp_data가 없습니다. Step 1에서 저장되지 않았을 수 있습니다.")
                except Exception as e:
                    print(f"⚠️ Redis에서 workflow_temp_data 로드 실패: {e}")
            else:
                print(f"⚠️ [route_to_domain] session_id가 없어 Redis에서 workflow_temp_data를 로드할 수 없습니다.")

            workflow_handler = get_workflow_handler(workflow_type)
            next_step_index = current_step_index + 1

            if workflow_handler:
                if not workflow_handler.validate_step_transition(current_step_index, next_step_index, workflow_steps):
                    print(f"❌ 워크플로우 인덱스 검증 실패: {current_step_index} → {next_step_index}")
                    return {
                        "final_response": f"❌ 워크플로우 진행 오류: 유효하지 않은 단계 전환입니다.",
                        "messages": ["워크플로우 진행 오류가 발생했습니다."],
                        "conversation_history": [user_message],
                        **current_date_info
                    }

            if next_domain and next_domain in MASTER_GRAPHS:
                print(f"✅ 워크플로우 진행: '{query}' → 다음 단계 {next_domain} (step {next_step_index + 1}/{len(workflow_steps)})")
                return {
                    "domain": next_domain,
                    "workflow_type": workflow_type,
                    "workflow_steps": workflow_steps,
                    "current_workflow_step_index": next_step_index,
                    "workflow_in_progress": True,
                    "workflow_temp_data": workflow_temp_data,  # workflow_temp_data 포함
                    "requires_input": False,
                    "conversation_history": [user_message],
                    **current_date_info
                }
            elif next_step_index < len(workflow_steps):
                next_domain = workflow_steps[next_step_index]
                if next_domain:
                    print(f"✅ 워크플로우 진행: '{query}' → 다음 단계 {next_domain} (step {next_step_index + 1}/{len(workflow_steps)})")
                    return {
                        "domain": next_domain,
                        "workflow_type": workflow_type,
                        "workflow_steps": workflow_steps,
                        "current_workflow_step_index": next_step_index,
                        "workflow_in_progress": True,
                        "workflow_temp_data": workflow_temp_data,  # workflow_temp_data 포함
                        "next_domain": next_domain,
                        "requires_input": False,
                        "conversation_history": [user_message],
                        **current_date_info
                    }

        # 취소 요청 확인
        if is_cancel:
            print(f"⚠️ 워크플로우 취소 요청: {workflow_type}")
            if workflow_type == "business_trip" and any(kw in query for kw in ["출장", "business_trip"]):
                # 워크플로우 취소
                print(f"✅ 워크플로우 취소 요청 감지: {workflow_type}")
                return {
                    "workflow_type": None,
                    "workflow_steps": None,
                    "current_workflow_step_index": None,
                    "workflow_completed_steps": None,
                    "workflow_in_progress": False,
                    "workflow_temp_data": {},  # 임시 데이터 초기화
                    "next_domain": None,
                    "domain": "schedule",
                    "last_domain": "schedule",  # last_domain 정규화
                    "conversation_history": [user_message],
                    **current_date_info
                }
            return {
                "workflow_type": None,
                "workflow_steps": None,
                "current_workflow_step_index": None,
                "workflow_completed_steps": None,
                "workflow_in_progress": False,
                "workflow_temp_data": {},  # 임시 데이터 초기화
                "next_domain": None,
                "domain": None,
                "last_domain": None,  # last_domain 초기화
                "final_response": f"워크플로우({workflow_type})가 취소되었습니다.",
                "conversation_history": [user_message],
                **current_date_info
            }

        # next_domain이 None이면 현재 단계에서 사용자 입력 대기 중
        # 현재 도메인으로 다시 라우팅하여 사용자 입력 처리
        if not next_domain and current_step_index < len(workflow_steps):
            current_domain = workflow_steps[current_step_index] if current_step_index < len(workflow_steps) else None
            if current_domain and current_domain in MASTER_GRAPHS:
                print(f"⏸️ 워크플로우 진행 중 - 사용자 입력 수신, 현재 단계({current_domain}, step {current_step_index + 1}/{len(workflow_steps)})로 라우팅")
                return {
                    "domain": current_domain,
                    "workflow_type": workflow_type,
                    "workflow_steps": workflow_steps,
                    "current_workflow_step_index": current_step_index,
                    "workflow_in_progress": True,
                    "workflow_temp_data": workflow_temp_data,  # workflow_temp_data 포함
                    "requires_input": False,
                    "conversation_history": [user_message],
                    **current_date_info
                }

        # 다음 단계 도메인 설정
        if next_domain and next_domain in MASTER_GRAPHS:
            # current_step_index가 정수임을 보장 (위에서 이미 처리했지만 한번 더 확인)
            safe_current_step = int(current_step_index) if current_step_index is not None else 0
            print(f"✅ 워크플로우 다음 단계: {next_domain} (step {safe_current_step + 1}/{len(workflow_steps)})")
            return {
                "domain": next_domain,
                "workflow_type": workflow_type,
                "workflow_steps": workflow_steps,
                "current_workflow_step_index": current_step_index,
                "workflow_in_progress": True,
                "workflow_temp_data": workflow_temp_data,  # workflow_temp_data 포함
                "conversation_history": [user_message],
                **current_date_info
            }
        else:
            # 워크플로우 완료
            print(f"✅ 워크플로우 완료: {workflow_type}")
            workflows = load_workflows_config()
            workflow_config = workflows.get("workflows", {}).get(workflow_type, {})
            completion_message = workflow_config.get("completion_message", "워크플로우가 완료되었습니다.")
            return {
                "workflow_type": None,
                "workflow_steps": None,
                "current_workflow_step_index": None,
                "workflow_completed_steps": None,
                "workflow_in_progress": False,
                "next_domain": None,
                "domain": None,
                "last_domain": None,  # last_domain 초기화
                "workflow_temp_data": {},
                "final_response": completion_message,
                "conversation_history": [user_message],
                **current_date_info
            }

    # "n번" 같은 요청이면 Redis에서 last_domain을 가져와서 사용
    # 단, 동명이인 선택 중이면 건너뜀 (이미 위에서 처리됨)
    email_compose_context = state.get("email_compose_context") or {}
    pending_selection = email_compose_context.get("pending_recipient_selection", {})

    # 숫자 선택 패턴 체크 (동명이인 선택 중이 아닐 때만 last_domain 기반 라우팅 수행)
    number_selection_pattern = re.compile(r'^\d+번?\s*(자세히|알려줘|보여줘|설명|상세|보기)?', re.IGNORECASE)
    is_number_selection = number_selection_pattern.match(query.strip()) or (query.strip().isdigit() and len(query.strip()) <= 2)
    
    if not pending_selection and is_number_selection and session_id:  # 동명이인 선택 중이 아닐 때만
        # Redis에서 last_domain 가져오기
        try:
            from agents.session_manager import get_session_manager
            session_manager = await get_session_manager()
            if session_manager:
                # employee_id를 state에서 가져오기
                employee_id = state.get("employee_id")
                if employee_id:
                    session_data = await session_manager.load_session_data(session_id, employee_id)
                    if session_data and session_data.get("last_domain"):
                        redis_last_domain = session_data.get("last_domain")
                        if redis_last_domain and redis_last_domain in MASTER_GRAPHS:
                            print(f"✅ Redis에서 last_domain 확인: '{redis_last_domain}' → '{query}' 요청을 {redis_last_domain} 도메인으로 라우팅")
                            _routing_logger.info(f"[라우팅 완료] Redis last_domain 기반: {redis_last_domain} (요청: {query})")
                            # 이미 함수 시작 부분에서 정의된 user_message 사용 (중복 정의 제거)
                            return {
                                "domain": redis_last_domain,
                                "conversation_history": [user_message],
                                **current_date_info
                            }
                else:
                    print(f"⚠️ employee_id가 없어서 last_domain 조회 불가")
        except Exception as e:
            print(f"⚠️ Redis에서 last_domain 조회 실패: {e}, LLM으로 분류 계속 진행")
    
    # 번호 선택 패턴이고 recruitment 컨텍스트가 있으면 recruitment로 우선 라우팅
    if is_number_selection:
        recruitment_context = state.get("recruitment_context", {})
        if recruitment_context:
            resume_search = recruitment_context.get("resume_search", {})
            last_results = resume_search.get("last_results", [])
            if last_results:
                # recruitment 결과가 있으면 tech 체크를 건너뛰고 recruitment로 라우팅
                print(f"✅ Recruitment 컨텍스트 확인: '{query}' → recruitment 도메인으로 라우팅 (인재 검색 결과 선택)")
                _routing_logger.info(f"[라우팅 완료] Recruitment 컨텍스트 기반: recruitment (요청: {query})")
                return {
                    "domain": "recruitment",
                    "conversation_history": [user_message],
                    **current_date_info
                }

    # ============================================
    # 1단계: 워크플로우 vs 일반 요청 분류
    # ============================================
    print("--- [Orchestrator] 1단계: 워크플로우 vs 일반 요청 분류 ---")
    step_start = time.time()
    request_classification_result = await classify_request_type(query)
    step_elapsed = (time.time() - step_start) * 1000
    _routing_logger.info(f"[1단계 분류] {step_elapsed:.2f}ms")
    
    is_workflow_request = request_classification_result.get("is_workflow_request", False)
    classification_confidence = request_classification_result.get("confidence", 0.0)
    
    # ============================================
    # 2단계: 분류 결과에 따라 라우팅
    # ============================================
    
    # 2-1. 워크플로우 요청인 경우
    # 단, 워크플로우 진행 중이 아닐 때만 새 워크플로우 감지 시도
    if is_workflow_request and classification_confidence >= 0.7 and not workflow_in_progress:
        print(f"--- [Orchestrator] 2단계: 워크플로우 감지 및 처리 ---")
        step_start = time.time()
        workflow_detection_result = await detect_workflow_type(query)
        step_elapsed = (time.time() - step_start) * 1000
        _routing_logger.info(f"[2단계 워크플로우 감지] {step_elapsed:.2f}ms")
        
        # 워크플로우 타입 감지 (workflows.yaml 사용)
        if workflow_detection_result and workflow_detection_result.get("confidence", 0) >= 0.8:
            workflow_type = workflow_detection_result["workflow_type"]
            workflows = load_workflows_config()
            workflow_config = workflows.get("workflows", {}).get(workflow_type)
            
            if workflow_config:
                steps = workflow_config.get("steps", [])
                workflow_domains = [step.get("domain") for step in steps if step.get("domain")]
                
                if workflow_domains:
                    # 워크플로우 데이터 추출
                    workflow_data = await extract_workflow_data(query, workflow_type)
                    
                    # 첫 번째 단계 도메인 설정
                    first_domain = workflow_domains[0]
                    
                    print(f"✅ 워크플로우 감지: {workflow_type} (단계 수: {len(workflow_domains)})")
                    print(f"✅ 첫 번째 단계 도메인: {first_domain}")
                    
                    return {
                        "workflow_type": workflow_type,
                        "workflow_steps": workflow_domains,
                        "current_workflow_step_index": 0,
                        "workflow_completed_steps": [],
                        "workflow_in_progress": True,
                        "next_domain": None,
                        "domain": first_domain,
                        "conversation_history": [user_message],
                        **workflow_data,  # start_date, end_date, destination, reason 등
                        **current_date_info
                    }
        
        # 워크플로우 감지 실패 시 일반 라우팅으로 fallback
        print(f"⚠️ 워크플로우 감지 실패, 일반 라우팅으로 fallback")
        _routing_logger.info(f"[워크플로우 감지 실패] 일반 라우팅으로 fallback")
    
    # 2-2. 일반 요청인 경우 (common_prompts.yaml 사용)
    print(f"--- [Orchestrator] 2단계: 일반 요청 도메인 분류 ---")

    # Recruitment 휴먼인더루프 진행 중 확인 (최우선 처리)
    # last_domain이 recruitment이고 search_parameters의 collection_step이 complete가 아니면
    # 휴먼인더루프 진행 중이므로 recruitment로 강제 라우팅
    if last_domain == "recruitment" and not workflow_in_progress:
        recruitment_context = state.get("recruitment_context", {})
        if recruitment_context:
            search_parameters = recruitment_context.get("search_parameters", {})
            collection_step = search_parameters.get("collection_step")
            if collection_step and collection_step != "complete":
                print(f"✅ Recruitment 휴먼인더루프 진행 중 감지: collection_step={collection_step} → recruitment 도메인으로 강제 라우팅")
                _routing_logger.info(f"[라우팅 완료] Recruitment 휴먼인더루프 진행 중: recruitment (요청: {query})")
                return {
                    "domain": "recruitment",
                    "conversation_history": [user_message],
                    **current_date_info
                }

    # Tech 도메인 ask_source 상태 확인
    step_start = time.time()
    tech_result = check_tech_ask_source_state(query, state, user_message)
    step_elapsed = (time.time() - step_start) * 1000
    _routing_logger.info(f"[Tech 도메인 상태 확인] {step_elapsed:.2f}ms")
    if tech_result:
        total_elapsed = (time.time() - route_start_time) * 1000
        _routing_logger.info(f"[라우팅 완료] {total_elapsed:.2f}ms (Tech 도메인 상태 확인으로 종료)")
        # 날짜 정보 포함
        tech_result.update(current_date_info)
        return tech_result

    # 이전 대화 확인 질문 감지 및 처리
    # 단, 워크플로우 진행 중이면 건너뛰기 (워크플로우 진행이 우선)
    if conversation_history and not workflow_in_progress:
        is_history_check_query, history_check_result = await detect_history_check_query(
            query=query,
            conversation_history=conversation_history,
            route_start_time=route_start_time
        )
        
        if is_history_check_query:
            # 이전 대화 확인 질문 처리
            result = await handle_history_check_query(
                query=query,
                state=state,
                conversation_history=conversation_history,
                user_message=user_message,
                history_check_result=history_check_result,
                route_start_time=route_start_time
            )
            # 날짜 정보 포함
            result.update(current_date_info)
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
                    "messages": [response],
                    **current_date_info  # 날짜 정보 포함
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
                "conversation_history": [user_message],  # operator.add로 기존 히스토리에 추가됨
                **current_date_info  # 날짜 정보 포함
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
                "conversation_history": [user_message],
                **current_date_info  # 날짜 정보 포함
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
        # Redis에서 last_domain 가져오기 (state의 last_domain이 없을 수 있음)
        redis_last_domain = last_domain
        if not redis_last_domain and session_id:
            try:
                from agents.session_manager import get_session_manager
                session_manager = await get_session_manager()
                if session_manager:
                    # employee_id를 state에서 가져오기
                    employee_id = state.get("employee_id")
                    if employee_id:
                        session_data = await session_manager.load_session_data(session_id, employee_id)
                        if session_data and session_data.get("last_domain"):
                            redis_last_domain = session_data.get("last_domain")
            except Exception as e:
                print(f"⚠️ Redis에서 last_domain 조회 실패 (프롬프트용): {e}")
        
        system_prompt = get_domain_classification_prompt(context, last_domain=redis_last_domain)
        prompt_elapsed = (time.time() - prompt_start) * 1000
        _routing_logger.info(f"[도메인 분류 프롬프트 로드] {prompt_elapsed:.2f}ms (last_domain={redis_last_domain})")
        
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
    
    # 결재 완료 상태 확인은 document 도메인에서 처리하므로 여기서는 제거

    total_elapsed = (time.time() - route_start_time) * 1000
    print(f"✅ 도메인 결정: {domain}")
    _routing_logger.info(f"[라우팅 완료] {total_elapsed:.2f}ms (도메인: {domain})")
    return {
        "domain": domain,
        "conversation_history": [user_message],
        **current_date_info  # 날짜 정보 포함
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
        # ⚠️ 중요: 현재 state의 workflow_temp_data를 보존
        current_temp_data = state.get("workflow_temp_data", {})
        result = await graph.ainvoke(state)
        
        # result에 workflow_temp_data가 없으면 기존 데이터 유지
        if "workflow_temp_data" not in result and current_temp_data:
            result["workflow_temp_data"] = current_temp_data
            
        default_msg = completion_messages.get("schedule", "스케줄 작업을 완료했습니다.")
        return process_domain_node_result(state, result, "schedule", default_msg)
    
    async def document_node(state: OrchestratorState) -> Dict[str, Any]:
        """문서 마스터그래프 실행."""
        graph = get_document_master_graph()
        current_temp_data = state.get("workflow_temp_data", {})
        result = await graph.ainvoke(state)
        
        if "workflow_temp_data" not in result and current_temp_data:
            result["workflow_temp_data"] = current_temp_data
            
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
        current_temp_data = state.get("workflow_temp_data", {})
        result = await graph.ainvoke(state)
        
        if "workflow_temp_data" not in result and current_temp_data:
            result["workflow_temp_data"] = current_temp_data
            
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
        current_temp_data = state.get("workflow_temp_data", {})
        result = await graph.ainvoke(state)
        
        if "workflow_temp_data" not in result and current_temp_data:
            result["workflow_temp_data"] = current_temp_data
            
        default_msg = completion_messages.get("recruitment", "채용 작업을 완료했습니다.")
        return process_domain_node_result(state, result, "recruitment", default_msg)
    
    # 각 도메인별 노드 등록
    workflow.add_node("schedule", schedule_node)
    workflow.add_node("document", document_node)
    workflow.add_node("qa", qa_node)
    workflow.add_node("email", email_node)
    workflow.add_node("tech", tech_node)
    workflow.add_node("recruitment", recruitment_node)

    print(f"[SUCCESS] 마스터그래프 등록 완료: schedule, document, qa, email, tech, recruitment")
    
    # 엣지 설정
    workflow.set_entry_point("route_to_domain")
    
    # 조건부 라우팅: 도메인에 따라 해당 마스터그래프로 라우팅
    def route_to_master_graph(state: OrchestratorState) -> str:
        """도메인에 따라 마스터그래프로 라우팅."""
        # domain 변수를 먼저 state에서 가져오기
        domain = state.get("domain")

        # 워크플로우 진행 중인지 먼저 확인 (최우선)
        workflow_in_progress = state.get("workflow_in_progress", False)

        # 워크플로우 진행 중이면 이전 대화 확인 질문 체크를 건너뛰기
        if not workflow_in_progress:
            # 이전 대화 확인 질문은 orchestrator에서 직접 처리하므로 END로 이동
            final_response = state.get("final_response")

            # final_response가 있고 domain이 None이면 이전 대화 확인 질문으로 간주
            if final_response and (domain is None or domain == ""):
                print("✅ 이전 대화 확인 질문 - orchestrator에서 직접 처리 완료, END로 이동")
                return END

        if workflow_in_progress:
            # 워크플로우 진행 중인 경우, next_domain 또는 domain 확인
            next_domain = state.get("next_domain")
            workflow_steps = state.get("workflow_steps", [])
            current_step_index = state.get("current_workflow_step_index", 0)
            workflow_type = state.get("workflow_type")
            
            # next_domain이 있으면 다음 단계로 진행
            if next_domain and next_domain in MASTER_GRAPHS:
                print(f"--- [Orchestrator] 워크플로우 다음 단계 라우팅: {next_domain} ---")
                return next_domain
            
            # next_domain이 없지만 domain이 있으면 현재 단계 실행 (첫 단계)
            if domain and domain in MASTER_GRAPHS:
                print(f"--- [Orchestrator] 워크플로우 첫 단계 라우팅: {domain} ---")
                return domain
            
            # domain도 없으면 workflow_steps에서 현재 단계 도메인 가져오기
            if current_step_index < len(workflow_steps):
                current_domain = workflow_steps[current_step_index]
                if current_domain and current_domain in MASTER_GRAPHS:
                    print(f"--- [Orchestrator] 워크플로우 현재 단계 라우팅: {current_domain} (step {current_step_index + 1}/{len(workflow_steps)}) ---")
                    return current_domain

            # 워크플로우가 완료되었거나 모든 단계가 끝났는지 확인
            if current_step_index >= len(workflow_steps):
                print("✅ 워크플로우 완료 - END로 이동")
                return END
            
            # 예상치 못한 경우 - 워크플로우 진행 중이지만 라우팅할 도메인을 찾을 수 없음
            print(f"⚠️ 워크플로우 라우팅 오류: domain={domain}, next_domain={next_domain}, step_index={current_step_index}, workflow_steps={workflow_steps}")
            # 워크플로우 진행 중이므로 END로 이동하여 사용자 입력 대기
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
    
    # 도메인 노드 이후 라우팅 함수 (워크플로우 처리)
    def route_after_domain(state: OrchestratorState) -> str:
        """도메인 노드 실행 후 라우팅.
        
        워크플로우가 진행 중이면 route_to_domain으로 돌아가서 다음 단계를 처리하고,
        그렇지 않으면 END로 이동합니다.

        requires_input이 True이면 사용자 입력을 기다려야 하므로 END로 이동합니다.
        """
        workflow_in_progress = state.get("workflow_in_progress", False)
        requires_input = state.get("requires_input", False)
        next_domain = state.get("next_domain")

        # 사용자 입력이 필요한 경우 (HITL)
        if requires_input:
            print("⏸️ 사용자 입력 대기 중 - END로 이동하여 사용자 응답 대기")
            return END

        if workflow_in_progress:
            # next_domain이 있으면 다음 단계로 진행
            if next_domain:
                print(f"✅ 워크플로우 진행 중 - route_to_domain으로 돌아가서 다음 단계({next_domain}) 처리")
                return "route_to_domain"
            else:
                # next_domain이 없으면 사용자 입력 대기 (현재 단계에서 추가 정보 필요)
                print("⏸️ 워크플로우 진행 중이지만 사용자 입력 대기 - END로 이동")
                return END
        else:
            print("✅ 일반 작업 완료 - END로 이동")
            return END
    
    # 모든 마스터그래프는 route_after_domain으로 연결
    for domain_name in MASTER_GRAPHS.keys():
        workflow.add_conditional_edges(
            domain_name,
            route_after_domain,
            {
                "route_to_domain": "route_to_domain",
                END: END
            }
        )
    
    print(f"📊 총 {len(MASTER_GRAPHS)}개 마스터그래프 등록 완료")
    
    # 체크포인터 없이 컴파일 (세션 관리자가 대화 히스토리 관리)
    # SessionManager가 Redis에 대화 히스토리와 컨텍스트를 저장합니다
    # 세션당 하나의 키만 사용합니다 (session:{session_id})
    print("ℹ️ 체크포인터 없이 실행 (세션 관리자가 대화 히스토리 관리)")
    compiled_graph = workflow.compile(checkpointer=None)
    print("✅ Orchestrator 그래프 컴파일 완료")
    return compiled_graph

