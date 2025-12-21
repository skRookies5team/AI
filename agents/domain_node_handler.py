"""도메인 노드 공통 로직 처리 모듈.

각 도메인 노드에서 공통으로 사용되는 로직을 처리합니다:
- 도메인 전환 시 이전 도메인 정보 정리
- 작업 완료 시 추천 메시지 추가
- conversation_history 관리
- Redis state 최적화
"""

from typing import Dict, Any
from datetime import datetime

from agents.state import OrchestratorState, Message
from agents.redis_optimizer import optimize_state_before_save
from agents.workflows import get_workflow_handler




def cleanup_previous_domain_info(
    state: OrchestratorState, 
    result: Dict[str, Any], 
    current_domain: str
) -> None:
    """도메인 전환 시 이전 도메인 관련 정보를 정리합니다.
    
    document_path는 문서 도메인에서만 사용되므로, 다른 도메인으로 전환 시 제거해야 합니다.
    
    Args:
        state: 오케스트레이터 상태
        result: 마스터그래프 실행 결과
        current_domain: 현재 도메인 이름
    """
    last_domain = state.get("last_domain")
    if last_domain and last_domain != current_domain:
        # 도메인이 변경된 경우 이전 도메인 관련 정보 정리
        result.pop("document_path", None)
        result.pop("document_content", None)
        state.pop("document_path", None)
        state.pop("document_content", None)
        print(f"🧹 [도메인 전환] {last_domain} → {current_domain}: 이전 도메인의 document_path 제거 완료")




def ensure_conversation_history(
    result: Dict[str, Any], 
    default_message: str
) -> None:
    """conversation_history가 없으면 기본 메시지로 추가합니다.
    
    Args:
        result: 마스터그래프 실행 결과
        default_message: 기본 메시지
    """
    if "conversation_history" not in result or not result.get("conversation_history"):
        response_message: Message = {
            "role": "assistant",
            "content": result.get("final_response", default_message),
            "timestamp": datetime.now().isoformat()
        }
        result["conversation_history"] = [response_message]


def process_domain_node_result(
    state: OrchestratorState,
    result: Dict[str, Any],
    domain_name: str,
    default_message: str = "작업을 완료했습니다."
) -> Dict[str, Any]:
    """도메인 노드 실행 결과를 처리합니다.
    
    공통 로직:
    1. last_domain 업데이트
    2. 워크플로우 상태 업데이트 (워크플로우 진행 중인 경우)
    3. 도메인 전환 시 이전 도메인 정보 정리
    4. conversation_history 보장
    5. 추천 메시지 추가
    6. 불필요한 다른 도메인 컨텍스트 제거 (메모리 최적화)
    7. Redis state 최적화 (성능 최적화: 필요한 경우에만)
    
    Args:
        state: 오케스트레이터 상태
        result: 마스터그래프 실행 결과
        domain_name: 도메인 이름 (schedule, document, qa, email, tech)
        default_message: 기본 완료 메시지
        
    Returns:
        처리된 결과 딕셔너리
    """
    # 1. domain 및 last_domain 업데이트 (LLM 라우팅 시 결정된 도메인 정보 유지)
    result["domain"] = domain_name
    
    # 2. 워크플로우 상태 업데이트
    workflow_in_progress = state.get("workflow_in_progress", False)
    requires_input = result.get("requires_input", False)
    
    if workflow_in_progress:
        # 워크플로우 진행 중일 때는 last_domain을 워크플로우 단계 정보로 설정
        workflow_type = state.get("workflow_type")
        current_step_index = state.get("current_workflow_step_index", 0)
        
        # 워크플로우 타입을 영어로 변환 (사용자 요청 형식에 맞춤)
        workflow_type_en = {
            "business_trip": "Apply for a business trip",
            "recruitment": "Recruitment process",
            "meeting_preparation": "Meeting preparation"
        }.get(workflow_type, workflow_type.replace("_", " ").title())
        
        # last_domain을 워크플로우 단계 정보로 설정 (예: "Apply for a business trip step1")
        result["last_domain"] = f"{workflow_type_en} step{current_step_index + 1}"
        print(f"✅ 워크플로우 last_domain 설정: {result['last_domain']}")
    else:
        # 워크플로우가 아닐 때는 기본 도메인 사용
        result["last_domain"] = domain_name
    
    if workflow_in_progress:
        workflow_steps = state.get("workflow_steps", [])
        current_step_index = state.get("current_workflow_step_index", 0)
        workflow_completed_steps = state.get("workflow_completed_steps", [])
        
        # 사용자 입력이 필요한 경우 (HITL) - 다음 단계로 진행하지 않음
        if requires_input:
            result["next_domain"] = None  # 사용자 입력 대기
            result["current_workflow_step_index"] = current_step_index  # 현재 단계 유지
            result["workflow_completed_steps"] = workflow_completed_steps
            result["workflow_in_progress"] = True
            result["requires_input"] = True  # 명시적으로 설정
            print(f"⏸️ 워크플로우 진행 중 - 사용자 입력 대기 (step {current_step_index + 1}/{len(workflow_steps)})")
        else:
            # 현재 단계를 완료된 단계 목록에 추가
            if domain_name not in workflow_completed_steps:
                workflow_completed_steps = workflow_completed_steps + [domain_name]
            
            # 다음 단계 인덱스 계산
            next_step_index = current_step_index + 1
            
            # 워크플로우 핸들러를 통한 인덱스 검증
            workflow_type = state.get("workflow_type")
            workflow_handler = get_workflow_handler(workflow_type) if workflow_type else None
            
            if workflow_handler:
                if not workflow_handler.validate_step_transition(
                    current_step_index, next_step_index, workflow_steps
                ):
                    print(f"❌ 워크플로우 인덱스 검증 실패: {current_step_index} → {next_step_index}")
                    # 검증 실패 시 현재 단계 유지
                    result["current_workflow_step_index"] = current_step_index
                    result["next_domain"] = None
                    result["workflow_in_progress"] = True
                    result["requires_input"] = True
                    result["final_response"] = result.get("final_response", "") + "\n\n⚠️ 워크플로우 진행 오류가 발생했습니다."
                    return result
            
            # 다음 단계 도메인 확인
            if next_step_index < len(workflow_steps):
                next_domain = workflow_steps[next_step_index]
                result["current_workflow_step_index"] = next_step_index
                result["next_domain"] = next_domain
                result["workflow_completed_steps"] = workflow_completed_steps
                result["workflow_in_progress"] = True
                result["requires_input"] = False  # 다음 단계로 진행하므로 입력 불필요
                
                # 워크플로우 단계 완료 메시지 추가 (선택적)
                # step_completion_messages가 workflows.yaml에 정의되어 있으면 사용
                try:
                    from agents.orchestrator_v3 import load_workflows_config
                    workflows = load_workflows_config()
                    workflow_type = state.get("workflow_type")
                    workflow_config = workflows.get("workflows", {}).get(workflow_type, {})
                    step_completion_messages = workflow_config.get("step_completion_messages", {})
                    
                    # 단계별 메시지 키 생성
                    # workflows.yaml의 step 정의에서 action 필드를 확인
                    steps = workflow_config.get("steps", [])
                    if current_step_index < len(steps):
                        step_config = steps[current_step_index]
                        action = step_config.get("action", "")
                        
                        # action을 기반으로 메시지 키 생성
                        # workflows.yaml의 키 형식: {domain}_{action_first_word}
                        # 예: check_conflicts -> schedule_check, generate_document -> document_generate
                        #     compose_absence_email -> email_compose, register_absence -> schedule_register
                        if action:
                            # action의 첫 단어 추출 (예: check_conflicts -> check)
                            action_first_word = action.split("_")[0]
                            step_message_key = f"{domain_name}_{action_first_word}"
                            
                            if step_message_key in step_completion_messages:
                                step_message = step_completion_messages[step_message_key]
                                existing_response = result.get("final_response", default_message)
                                result["final_response"] = f"{existing_response}\n\n{step_message}"
                except Exception as e:
                    # 완료 메시지 로드 실패는 치명적이지 않으므로 계속 진행
                    pass
                
                print(f"✅ 워크플로우 단계 완료: {domain_name} (step {current_step_index + 1}/{len(workflow_steps)}), 다음 단계: {next_domain}")
            else:
                # 모든 단계 완료 여부 확인
                # 실제로 모든 단계가 완료되었는지 확인 (슬롯 채우기 질문 등이 있는 경우는 완료가 아님)
                existing_response = result.get("final_response", default_message)
                
                # 슬롯 채우기 질문 패턴 확인 (워크플로우가 아직 완료되지 않았음을 의미)
                slot_filling_keywords = [
                    "다음 정보를 알려주세요",
                    "알려주세요",
                    "입력해주세요",
                    "필요합니다",
                    "필요해요",
                    "필요한 정보",
                    "다음 정보",
                    "다음과 같은 정보",
                    "다음 항목",
                ]
                
                is_slot_filling = any(keyword in existing_response for keyword in slot_filling_keywords)
                
                if is_slot_filling:
                    # 슬롯 채우기 질문이 있으면 워크플로우가 아직 완료되지 않음
                    # 다음 단계 인덱스를 유지하고 워크플로우 진행 상태 유지
                    result["current_workflow_step_index"] = current_step_index  # 인덱스 유지
                    result["next_domain"] = None  # 사용자 입력 대기
                    result["workflow_completed_steps"] = workflow_completed_steps
                    result["workflow_in_progress"] = True  # 진행 중 유지
                    print(f"⚠️ 워크플로우 진행 중 - 슬롯 채우기 질문 감지, 사용자 입력 대기 (step {current_step_index + 1}/{len(workflow_steps)})")
                else:
                    # 실제로 모든 단계가 완료된 경우
                    result["current_workflow_step_index"] = next_step_index
                    result["next_domain"] = None
                    result["workflow_completed_steps"] = workflow_completed_steps
                    result["workflow_in_progress"] = False
                    
                    # 워크플로우 완료 메시지 추가
                    try:
                        from agents.orchestrator_v3 import load_workflows_config
                        workflows = load_workflows_config()
                        workflow_type = state.get("workflow_type")
                        workflow_config = workflows.get("workflows", {}).get(workflow_type, {})
                        completion_message = workflow_config.get("completion_message", "워크플로우가 완료되었습니다.")
                        
                        result["final_response"] = f"{existing_response}\n\n{completion_message}"
                    except Exception as e:
                        # 완료 메시지 로드 실패는 치명적이지 않으므로 기본 메시지 사용
                        result["final_response"] = f"{existing_response}\n\n워크플로우가 완료되었습니다."
                    
                    print(f"✅ 워크플로우 완료: 모든 단계 완료 ({len(workflow_steps)}/{len(workflow_steps)})")
    
    # 2. 도메인 전환 시 이전 도메인 정보 정리 (빠른 작업)
    cleanup_previous_domain_info(state, result, domain_name)
    
    # 3. 불필요한 다른 도메인 컨텍스트 제거 (메모리 최적화)
    # 현재 도메인과 관련 없는 컨텍스트는 제거하여 메모리 사용량 감소
    domain_contexts = {
        "schedule": "schedule_context",
        "document": "document_context",
        "qa": "qa_search_context",
        "email": ["email_search_context", "email_compose_context"],
        "tech": "tech_search_context",
        "recruitment": "recruitment_context"
    }
    
    # 현재 도메인의 컨텍스트는 유지하고, 다른 도메인의 컨텍스트는 제거
    current_context_keys = domain_contexts.get(domain_name, [])
    if not isinstance(current_context_keys, list):
        current_context_keys = [current_context_keys]
    
    all_context_keys = []
    for ctx_key in domain_contexts.values():
        if isinstance(ctx_key, list):
            all_context_keys.extend(ctx_key)
        else:
            all_context_keys.append(ctx_key)
    
    # result에 명시적으로 다른 도메인 컨텍스트를 None으로 설정하여 제거
    # LangGraph는 result에 None이 설정된 키를 state에서도 제거합니다
    for ctx_key in all_context_keys:
        if ctx_key not in current_context_keys:
            # 현재 도메인이 아닌 컨텍스트는 None으로 설정하여 제거
            # state에 값이 있더라도 result에 None을 설정하면 제거됨
            if ctx_key in state and state.get(ctx_key) is not None:
                result[ctx_key] = None
                # 디버그 메시지 제거 (프로덕션 환경에서는 불필요)
    
    # 4. conversation_history 보장 (빠른 작업)
    ensure_conversation_history(result, default_message)
    
    # 4. Redis 저장 전 state 최적화 (성능 최적화: 필요한 경우에만)
    # LangGraph의 체크포인터가 자동으로 저장하므로, 여기서는 최소한의 최적화만 수행
    # tech 도메인의 경우 tech_search_context만 최적화하여 전체 state 최적화 시간 단축
    if domain_name == "tech" and ("tech_search_context" in result or "tech_search_context" in state):
        # tech 도메인이고 tech_search_context가 변경된 경우, tech_search_context만 최적화
        from agents.redis_optimizer import optimize_tech_search_context, optimize_conversation_history
        
        # conversation_history 최적화
        if "conversation_history" in result:
            result["conversation_history"] = optimize_conversation_history(result["conversation_history"])
        
        # tech_search_context만 최적화 (전체 state 최적화 대신)
        tech_context = result.get("tech_search_context") or state.get("tech_search_context")
        if tech_context:
            result["tech_search_context"] = optimize_tech_search_context(tech_context)
    elif "tech_search_context" in result or "tech_search_context" in state:
        # 다른 도메인에서 tech_search_context가 있는 경우 전체 최적화
        merged_state = {**state, **result}
        result = optimize_state_before_save(merged_state)
    else:
        # tech_search_context가 없으면 최소한의 최적화만 (conversation_history만)
        if "conversation_history" in result:
            from agents.redis_optimizer import optimize_conversation_history
            result["conversation_history"] = optimize_conversation_history(result["conversation_history"])
    
    return result

