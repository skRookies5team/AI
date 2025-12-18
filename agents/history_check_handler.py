"""이전 대화 확인 질문 처리 핸들러.

이전 대화 확인 질문을 감지하고 처리하는 로직을 담당합니다.
orchestrator_v3.py에서 분리하여 단일 책임 원칙을 준수합니다.
"""

from __future__ import annotations

from typing import Dict, Any, Optional, List, Tuple
import re
import time
import logging
from datetime import datetime

from agents.state import OrchestratorState, Message
from agents.structured_output import extract_structured_output, HistoryCheckIntent
from agents.redis_optimizer import optimize_conversation_history


# 로거 설정 (orchestrator와 동일한 로거 사용)
_routing_logger = logging.getLogger("routing_performance")


def get_history_check_prompt(history_context: str = "") -> str:
    """이전 대화 확인 질문 분류를 위한 프롬프트를 반환합니다.
    
    Args:
        history_context: 이전 대화 내용 컨텍스트
        
    Returns:
        시스템 프롬프트 문자열
    """
    from pathlib import Path
    import yaml
    
    # 프롬프트 파일 직접 로드 (순환 참조 방지)
    current_dir = Path(__file__).parent
    yaml_path = current_dir / "common_prompts.yaml"
    
    if not yaml_path.exists():
        raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}")
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        all_prompts = yaml.safe_load(f)
    
    prompts = all_prompts.get("orchestrator", {})
    history_check_prompt = prompts.get("prompts", {}).get("history_check", {}).get("system_prompt", "")
    
    if not history_check_prompt:
        raise ValueError("orchestrator.prompts.history_check.system_prompt가 없습니다.")
    
    # 플레이스홀더 치환
    placeholder = prompts.get("prompts", {}).get("history_check", {}).get("history_context_placeholder", "{history_context}")
    prompt = history_check_prompt.replace(placeholder, history_context)
    
    return prompt


async def detect_history_check_query(
    query: str,
    conversation_history: List[Message],
    route_start_time: float
) -> Tuple[bool, Optional[HistoryCheckIntent]]:
    """이전 대화 확인 질문인지 감지합니다.
    
    Args:
        query: 사용자 질의
        conversation_history: 대화 히스토리
        route_start_time: 라우팅 시작 시간 (성능 측정용)
        
    Returns:
        (is_history_check_query, history_check_result) 튜플
    """
    from pathlib import Path
    import yaml
    
    def load_orchestrator_config() -> Dict[str, Any]:
        """오케스트레이터 설정을 반환합니다."""
        current_dir = Path(__file__).parent
        yaml_path = current_dir / "common_prompts.yaml"
        
        if not yaml_path.exists():
            raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}")
        
        with open(yaml_path, "r", encoding="utf-8") as f:
            all_prompts = yaml.safe_load(f)
        
        return all_prompts.get("orchestrator_config", {})
    
    step_start = time.time()
    is_history_check_query = False
    history_check_result = None
    
    # 문서 작성 요청 패턴 체크 (가장 먼저, LLM 호출 전에 필터링)
    document_creation_patterns = ["제목:", "요구사항:", "작성해줘", "만들어줘", "생성해줘"]
    has_document_pattern = any(pattern in query for pattern in document_creation_patterns)
    if has_document_pattern:
        print(f"✅ [문서 작성 요청 패턴 감지] '{query[:50]}...' → 이전 대화 확인 스킵, 일반 요청으로 처리")
        return False, None
    
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
                
                # check_type 일관성 검증 (오분류 방지)
                if is_history_check_query and history_check_result.check_type:
                    check_type = history_check_result.check_type
                    
                    # 문서 작성 요청 패턴 체크 (가장 우선)
                    document_creation_patterns = ["제목:", "요구사항:", "작성해줘", "만들어줘", "생성해줘", "작성", "만들어", "생성"]
                    has_document_pattern = any(pattern in query for pattern in document_creation_patterns)
                    if has_document_pattern:
                        print(f"⚠️ [문서 작성 요청 패턴 감지] '{query[:50]}...' → 일반 요청으로 처리")
                        is_history_check_query = False
                    
                    # 이메일 관련 질문인데 check_type이 email_search가 아니면 오분류 가능성
                    elif ("이메일" in query or "메일" in query) and check_type != "email_search":
                        print(f"⚠️ [check_type 불일치] 이메일 관련 질문인데 check_type={check_type} → 일반 요청으로 처리")
                        is_history_check_query = False
                    # 논문 관련 질문인데 check_type이 article_search가 아니면 오분류 가능성 (단, 이메일 키워드가 없는 경우에만)
                    elif ("논문" in query or "paper" in query.lower() or "article" in query.lower()) and check_type != "article_search":
                        if "이메일" not in query and "메일" not in query:
                            print(f"⚠️ [check_type 불일치] 논문 관련 질문인데 check_type={check_type} → 일반 요청으로 처리")
                            is_history_check_query = False
                    # 연차/휴가 신청서 작성 요청인데 check_type이 leave_request면 오분류 가능성
                    elif (("연차 신청서" in query or "휴가 신청서" in query or "반차 신청서" in query) and 
                          ("작성" in query or "만들어" in query or "생성" in query or "제목:" in query or "요구사항:" in query)):
                        print(f"⚠️ [문서 작성 요청 오분류] 연차/휴가 신청서 작성 요청인데 check_type={check_type} → 일반 요청으로 처리")
                        is_history_check_query = False
                
                # 신뢰도 임계값 검증 (YAML에서 로드)
                config = load_orchestrator_config()
                search_params = config.get("search_params", {})
                history_check_config = search_params.get("history_check", {})
                confidence_threshold = history_check_config.get("confidence_threshold", 0.85)
                
                if is_history_check_query and history_check_result.confidence < confidence_threshold:
                    print(f"⚠️ [신뢰도 낮음] confidence={history_check_result.confidence:.2f} < {confidence_threshold} → 일반 요청으로 처리")
                    is_history_check_query = False
                
                total_llm_elapsed = (time.time() - llm_start) * 1000
                _routing_logger.info(f"[이전 대화 확인 LLM 전체] {total_llm_elapsed:.2f}ms")
                print(f"🔍 [LLM 기반 이전 대화 확인] is_history_check={is_history_check_query}, check_type={history_check_result.check_type}, confidence={history_check_result.confidence:.2f}")
                
            except Exception as e:
                print(f"⚠️ 이전 대화 확인 질문 분류 실패: {e}, 키워드 패턴으로 fallback")
                # Fallback: 키워드 패턴 매칭
                is_history_check_query = has_strong_keyword or has_weak_keyword
    
    return is_history_check_query, history_check_result


async def handle_history_check_query(
    query: str,
    state: OrchestratorState,
    conversation_history: List[Message],
    user_message: Message,
    history_check_result: Optional[HistoryCheckIntent],
    route_start_time: float
) -> Dict[str, Any]:
    """이전 대화 확인 질문을 처리하고 답변을 생성합니다.
    
    Args:
        query: 사용자 질의
        state: 오케스트레이터 상태
        conversation_history: 대화 히스토리
        user_message: 사용자 메시지
        history_check_result: 이전 대화 확인 질문 감지 결과
        route_start_time: 라우팅 시작 시간 (성능 측정용)
        
    Returns:
        처리 결과 딕셔너리
    """
    from pathlib import Path
    import yaml
    
    def load_orchestrator_config() -> Dict[str, Any]:
        """오케스트레이터 설정을 반환합니다."""
        current_dir = Path(__file__).parent
        yaml_path = current_dir / "common_prompts.yaml"
        
        if not yaml_path.exists():
            raise FileNotFoundError(f"프롬프트 파일을 찾을 수 없습니다: {yaml_path}")
        
        with open(yaml_path, "r", encoding="utf-8") as f:
            all_prompts = yaml.safe_load(f)
        
        return all_prompts.get("orchestrator_config", {})
    
    step_start = time.time()
    print(f"✅ 이전 대화 확인 질문 감지: '{query}' - conversation_history 확인 (빠른 응답)")
    
    # conversation_history에서 이전 요청 및 검색 결과 확인
    found_previous_request = False
    request_details = []
    email_search_found = False
    qa_search_found = False  # QA 검색 (식단, 메뉴 등)
    recruitment_search_found = False  # Recruitment 검색 (인재 추천, 이력서 검색)
    email_keywords = []
    qa_keywords = []  # QA 관련 키워드 (식단, 메뉴 등)
    recruitment_keywords = []  # Recruitment 관련 키워드 (인재, 이력서 등)
    previous_email_results = []  # 이전 이메일 검색 결과
    previous_qa_results = None  # 이전 QA 검색 결과 (식단 등)
    previous_recruitment_results = None  # 이전 Recruitment 검색 결과
    
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
    
    # recruitment_context에서 이전 검색 결과 확인 (인재 추천 등)
    recruitment_context = state.get("recruitment_context", {})
    previous_recruitment_query = None  # 이전 Recruitment 요청 내용
    if recruitment_context:
        resume_search_context = recruitment_context.get("resume_search", {})
        if resume_search_context:
            if resume_search_context.get("last_results"):
                previous_recruitment_results = resume_search_context.get("last_results", [])
            if resume_search_context.get("last_query"):
                previous_recruitment_query = resume_search_context.get("last_query", "")
    
    # 질문에서 언급된 키워드 추출 (YAML 설정 사용)
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
    query_domain_type = None  # "email" 또는 "qa" 또는 "recruitment" 또는 None

    # 이메일 키워드 확인
    email_keywords_list = query_keyword_mapping.get("email", [])
    if email_keywords_list and any(k in query for k in email_keywords_list):
        query_keywords.append("이메일")
        query_domain_type = "email"

    # Recruitment 키워드 확인
    recruitment_keywords_list = query_keyword_mapping.get("recruitment", [])
    if recruitment_keywords_list and any(k in query for k in recruitment_keywords_list):
        # 이메일 키워드가 없으면 recruitment로 설정
        if query_domain_type is None:
            query_domain_type = "recruitment"
        # recruitment 키워드 추가
        for kw in recruitment_keywords_list:
            if kw in query:
                query_keywords.append(kw)
                break

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
    recruitment_keywords_list_config = request_type_keywords.get("recruitment", {}).get("keywords", [])
    recruitment_search_verbs = request_type_keywords.get("recruitment", {}).get("search_verbs", [])

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
            
            # Recruitment 검색 요청인지 확인 (YAML 설정 사용)
            elif recruitment_keywords_list_config and any(k in content for k in recruitment_keywords_list_config):
                # search_verbs도 확인
                if any(k in content for k in recruitment_search_verbs):
                    score += base_score
                    match_type = "recruitment"

                    # 사용자 질문의 도메인 타입과 일치하면 높은 보너스 점수
                    if query_domain_type == "recruitment":
                        score += domain_type_match_bonus

                    # 사용자 질문에서 recruitment 관련 키워드를 언급했으면 점수 증가
                    if any(k in query_keywords for k in recruitment_keywords_list_config):
                        score += type_match_bonus

                    # 키워드 매칭 점수 (언급된 키워드가 이전 요청에도 있으면 높은 점수)
                    matched_keywords = []
                    for keyword in query_keywords:
                        if keyword in content:
                            score += keyword_match_bonus
                            matched_keywords.append(keyword)

                    # 키워드 추출 (예: "인재", "이력서", "채용", "추천")
                    recruitment_keywords_in_content = [k for k in recruitment_keywords_list_config if k in content]
                    if recruitment_keywords_in_content:
                        recruitment_keywords.extend(recruitment_keywords_in_content)
                        # 사용자 질문의 키워드와 일치하는 recruitment 키워드가 있으면 높은 점수
                        for rec_kw in recruitment_keywords_in_content:
                            if rec_kw in query_keywords:
                                score += exact_keyword_match_bonus
                                if rec_kw not in matched_keywords:
                                    matched_keywords.append(rec_kw)

                    # 정규식 패턴으로도 키워드 추출 시도 (예: "개발자 인재 추천" -> "인재")
                    keyword_match = re.search(keyword_pattern, content)
                    if keyword_match:
                        extracted_keyword = keyword_match.group(1)
                        if extracted_keyword not in recruitment_keywords:
                            recruitment_keywords.append(extracted_keyword)
                        # 사용자 질문의 언급된 키워드와 일치하면 높은 점수
                        if extracted_keyword in query_keywords:
                            score += exact_keyword_match_bonus
                            if extracted_keyword not in matched_keywords:
                                matched_keywords.append(extracted_keyword)

                    # 모든 키워드가 일치하는 경우 추가 보너스 (정확한 매칭)
                    keyword_matching_config = matching_config.get("keyword_matching", {})
                    min_keyword_length = keyword_matching_config.get("min_keyword_length", 2)
                    all_keywords_match_multiplier = scoring_weights.get("all_keywords_match_multiplier", 2)

                    if len(query_keywords) > 1:
                        all_keywords_match = all(kw in content for kw in query_keywords if len(kw) >= min_keyword_length)
                        if all_keywords_match:
                            score += exact_keyword_match_bonus * all_keywords_match_multiplier

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
        elif best_match["type"] == "recruitment":
            recruitment_search_found = True
    
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
            specific_keywords = [kw for kw in query_keywords if kw not in ["이메일", "메일", "식단", "메뉴", "구내식당", "인재", "이력서", "채용", "지원자", "추천"]]
            if specific_keywords:
                mentioned_keywords = specific_keywords
            else:
                mentioned_keywords = query_keywords
        # 이메일, QA, 또는 Recruitment 키워드 중 하나 선택 (fallback)
        elif email_keywords:
            mentioned_keywords = email_keywords
        elif qa_keywords:
            mentioned_keywords = qa_keywords
        elif recruitment_keywords:
            mentioned_keywords = recruitment_keywords
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
    elif found_previous_request and recruitment_search_found:
        # Recruitment 검색 (인재 추천 등) 관련 이전 요청 확인
        if mentioned_keywords:
            keywords_str = ", ".join(set(mentioned_keywords))
            response = response_messages.get("previous_recruitment_request", "네, 이전에 '{keywords}' 관련 인재 추천/이력서 검색 요청을 하신 적이 있습니다.").format(keywords=keywords_str)
        elif recruitment_keywords:
            keywords_str = ", ".join(set(recruitment_keywords))
            response = response_messages.get("previous_recruitment_request", "네, 이전에 '{keywords}' 관련 인재 추천/이력서 검색 요청을 하신 적이 있습니다.").format(keywords=keywords_str)
        else:
            response = response_messages.get("previous_recruitment_search", "네, 이전에 인재 추천/이력서 검색 요청을 하신 적이 있습니다.")
        
        # 이전 요청과 답변 함께 출력 (best_match가 있으면 항상 출력)
        if best_match and best_match.get("content"):
            response += "\n\n**이전 대화 내역:**\n"

            # 이전 요청 내용 출력
            if previous_recruitment_query:
                response += f"**요청:** {previous_recruitment_query}\n\n"
            elif request_details and len(request_details) > 0:
                response += f"**요청:** {request_details[0]}\n\n"
            elif best_match.get("content"):
                response += f"**요청:** {best_match['content']}\n\n"

            # 이전 검색 결과가 있으면 포함
            if previous_recruitment_results:
                response += "**검색 결과:**\n"
                for i, result in enumerate(previous_recruitment_results[:5], 1):
                    name = result.get("name", "인재")
                    position = result.get("position", "")
                    experience = result.get("experience", "")
                    if position or experience:
                        response += f"{i}. **{name}** ({position}, {experience})\n"
                    else:
                        response += f"{i}. **{name}**\n"
            else:
                # conversation_history에서 이전 답변 찾기
                found_answer = False
                best_match_content = best_match.get("content", "").strip()

                keyword_matching_config = matching_config.get("keyword_matching", {})
                min_keyword_length = keyword_matching_config.get("min_keyword_length", 2)
                query_keywords_for_matching = [kw for kw in query_keywords if len(kw) >= min_keyword_length]

                candidate_matches = []
                for i, msg in enumerate(conversation_history):
                    if msg.get("role") != "user":
                        continue

                    msg_content = msg.get("content", "").strip()
                    if msg_content == best_match_content:
                        candidate_matches.append((i, msg_content, 100, "exact"))
                    elif query_keywords_for_matching and all(kw in msg_content for kw in query_keywords_for_matching):
                        matched_count = sum(1 for kw in query_keywords_for_matching if kw in msg_content)
                        candidate_matches.append((i, msg_content, 80 + matched_count * 5, "all_keywords"))
                    elif best_match_content in msg_content or msg_content in best_match_content:
                        matched_count = sum(1 for kw in query_keywords_for_matching if kw in msg_content) if query_keywords_for_matching else 1
                        candidate_matches.append((i, msg_content, 50 + matched_count * 5, "partial"))

                candidate_matches.sort(key=lambda x: x[2], reverse=True)

                max_candidate_messages = keyword_matching_config.get("max_candidate_messages", 5)
                exclude_patterns = keyword_matching_config.get("exclude_history_check_patterns", [])

                for i, msg_content, score, match_type in candidate_matches:
                    for j in range(i + 1, min(i + max_candidate_messages, len(conversation_history))):
                        next_msg = conversation_history[j]
                        if next_msg.get("role") == "assistant":
                            assistant_content = next_msg.get("content", "")
                            if assistant_content:
                                if not any(pattern in assistant_content for pattern in exclude_patterns):
                                    if len(assistant_content) > 500:
                                        response += f"**답변:** {assistant_content[:500]}...\n"
                                    else:
                                        response += f"**답변:** {assistant_content}\n"
                                    found_answer = True
                                    break
                        if found_answer:
                            break

                if not found_answer:
                    response += "**답변:** (이전 답변을 찾을 수 없습니다)\n"
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
    
    # recruitment 검색 결과가 있으면 search_results 필드에 포함 (카드 렌더링을 위해)
    if found_previous_request and recruitment_search_found and previous_recruitment_results:
        result["search_results"] = previous_recruitment_results[:5]  # 최대 5개만
        print(f"✅ 이전 recruitment 검색 결과 포함: {len(previous_recruitment_results)}개 인재 (카드 렌더링용)")
    
    # Redis 저장 전 state 최적화 (이전 대화 확인 질문은 간단하므로 최소한의 최적화만)
    # conversation_history만 최적화하고 나머지는 그대로 유지
    opt_start = time.time()
    result["conversation_history"] = optimize_conversation_history(result["conversation_history"])
    opt_elapsed = (time.time() - opt_start) * 1000
    _routing_logger.info(f"[이전 대화 확인 state 최적화] {opt_elapsed:.2f}ms")
    
    step_elapsed = (time.time() - step_start) * 1000
    total_elapsed = (time.time() - route_start_time) * 1000
    _routing_logger.info(f"[이전 대화 확인 처리 전체] {step_elapsed:.2f}ms")
    _routing_logger.info(f"[라우팅 완료] {total_elapsed:.2f}ms (이전 대화 확인으로 종료)")
    return result

