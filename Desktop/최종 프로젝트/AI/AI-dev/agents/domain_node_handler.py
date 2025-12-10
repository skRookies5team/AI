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
    2. 도메인 전환 시 이전 도메인 정보 정리
    3. conversation_history 보장
    4. 추천 메시지 추가
    5. Redis state 최적화 (성능 최적화: 필요한 경우에만)
    
    Args:
        state: 오케스트레이터 상태
        result: 마스터그래프 실행 결과
        domain_name: 도메인 이름 (schedule, document, qa, email, tech)
        default_message: 기본 완료 메시지
        
    Returns:
        처리된 결과 딕셔너리
    """
    # 1. last_domain 및 domain 업데이트
    result["last_domain"] = domain_name
    result["domain"] = domain_name  # FastAPI에서 current_domain 확인용
    
    # 채용 도메인인 경우 recommended_resumes를 최상위 레벨에도 복사 (FastAPI에서 접근하기 쉽도록)
    if domain_name == "recruitment" and "recommended_resumes" in result:
        # 이미 최상위 레벨에 있으면 그대로 유지
        pass
    elif domain_name == "recruitment" and "recruitment_context" in result:
        # recruitment_context에서 recommended_resumes를 최상위 레벨로 복사
        recruitment_context = result.get("recruitment_context", {})
        if "recommended_resumes" in recruitment_context:
            result["recommended_resumes"] = recruitment_context["recommended_resumes"]
            print(f"✅ [Domain Handler] recommended_resumes를 최상위 레벨로 복사: {len(result['recommended_resumes'])}개")
    
    # 2. 도메인 전환 시 이전 도메인 정보 정리 (빠른 작업)
    cleanup_previous_domain_info(state, result, domain_name)
    
    # 3. conversation_history 보장 (빠른 작업)
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

