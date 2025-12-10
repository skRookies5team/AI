"""Redis 저장 구조 최적화 유틸리티.

응답 속도 개선을 위한 Redis 저장 구조 최적화:
1. conversation_history 크기 제한 (최근 N개만 유지)
2. 도메인 컨텍스트 최적화 (필요한 데이터만 저장)
3. 큰 데이터 압축 또는 요약
"""

from __future__ import annotations

from typing import Dict, Any, Optional, List
from agents.state import Message, OrchestratorState

# 설정 상수
MAX_CONVERSATION_HISTORY = 15  # 최대 대화 히스토리 개수 (20 -> 15로 감소)
MAX_CONTEXT_ITEMS = 8  # 각 컨텍스트에서 최대 저장할 아이템 수 (10 -> 8로 감소)
MAX_CONTEXT_TEXT_LENGTH = 400  # 컨텍스트 텍스트 최대 길이 (500 -> 400으로 감소)


def optimize_conversation_history(history: List[Message]) -> List[Message]:
    """대화 히스토리 크기를 제한합니다.
    
    최근 N개만 유지하여 Redis 저장/로드 시간을 단축합니다.
    
    Args:
        history: 대화 히스토리 리스트
        
    Returns:
        최적화된 대화 히스토리 (최근 N개만)
    """
    if not history:
        return []
    
    # 최근 N개만 유지
    return history[-MAX_CONVERSATION_HISTORY:]


def optimize_email_search_context(context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """이메일 검색 컨텍스트를 최적화합니다.
    
    성능 최적화: 필요한 경우에만 복사하고 최적화.
    
    Args:
        context: 이메일 검색 컨텍스트
        
    Returns:
        최적화된 컨텍스트
    """
    if not context:
        return None
    
    # 변경이 필요한지 먼저 확인 (불필요한 복사 방지)
    needs_optimization = False
    
    # items 리스트 크기 확인
    if "items" in context and isinstance(context["items"], list):
        if len(context["items"]) > MAX_CONTEXT_ITEMS:
            needs_optimization = True
        else:
            # 각 아이템의 큰 필드 확인
            for item in context["items"]:
                if isinstance(item, dict):
                    for key in ["content", "body", "text"]:
                        if key in item and isinstance(item[key], str):
                            if len(item[key]) > MAX_CONTEXT_TEXT_LENGTH:
                                needs_optimization = True
                                break
                    if needs_optimization:
                        break
    
    # ids 리스트 크기 확인
    if "ids" in context and isinstance(context["ids"], list):
        if len(context["ids"]) > MAX_CONTEXT_ITEMS * 2:
            needs_optimization = True
    
    # 최적화가 필요 없으면 원본 반환
    if not needs_optimization:
        return context
    
    # 최적화가 필요한 경우에만 복사 및 최적화
    optimized = {}
    
    # items 리스트 크기 제한
    if "items" in context and isinstance(context["items"], list):
        if len(context["items"]) > MAX_CONTEXT_ITEMS:
            optimized["items"] = context["items"][:MAX_CONTEXT_ITEMS]
        else:
            optimized["items"] = context["items"].copy()
        
        # 각 아이템의 내용도 제한
        for item in optimized["items"]:
            if isinstance(item, dict):
                # content나 body 같은 큰 필드 제한
                for key in ["content", "body", "text"]:
                    if key in item and isinstance(item[key], str):
                        if len(item[key]) > MAX_CONTEXT_TEXT_LENGTH:
                            item[key] = item[key][:MAX_CONTEXT_TEXT_LENGTH] + "..."
    else:
        if "items" in context:
            optimized["items"] = context["items"]
    
    # ids 리스트 크기 제한
    if "ids" in context and isinstance(context["ids"], list):
        if len(context["ids"]) > MAX_CONTEXT_ITEMS * 2:
            optimized["ids"] = context["ids"][:MAX_CONTEXT_ITEMS * 2]
        else:
            optimized["ids"] = context["ids"]
    elif "ids" in context:
        optimized["ids"] = context["ids"]
    
    # 나머지 키 복사
    for key in context:
        if key not in optimized:
            optimized[key] = context[key]
    
    return optimized


def optimize_qa_search_context(context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """QA 검색 컨텍스트를 최적화합니다.
    
    성능 최적화: 필요한 경우에만 복사하고 최적화.
    
    Args:
        context: QA 검색 컨텍스트
        
    Returns:
        최적화된 컨텍스트
    """
    if not context:
        return None
    
    # 변경이 필요한지 먼저 확인 (불필요한 복사 방지)
    needs_optimization = False
    
    for sub_key in ["manual", "benefit", "cafeteria"]:
        if sub_key in context and isinstance(context[sub_key], dict):
            sub_context = context[sub_key]
            
            # last_answer 길이 확인
            if "last_answer" in sub_context and isinstance(sub_context["last_answer"], str):
                if len(sub_context["last_answer"]) > MAX_CONTEXT_TEXT_LENGTH:
                    needs_optimization = True
                    break
            
            # retrieved_contexts 크기 확인
            if "retrieved_contexts" in sub_context and isinstance(sub_context["retrieved_contexts"], list):
                if len(sub_context["retrieved_contexts"]) > 3:
                    needs_optimization = True
                    break
                # 각 컨텍스트 텍스트 길이 확인
                for ctx in sub_context["retrieved_contexts"]:
                    if isinstance(ctx, str) and len(ctx) > MAX_CONTEXT_TEXT_LENGTH:
                        needs_optimization = True
                        break
                if needs_optimization:
                    break
    
    # 최적화가 필요 없으면 원본 반환
    if not needs_optimization:
        return context
    
    # 최적화가 필요한 경우에만 복사 및 최적화
    optimized = {}
    
    # 각 서브 컨텍스트 최적화
    for sub_key in ["manual", "benefit", "cafeteria"]:
        if sub_key in context:
            if isinstance(context[sub_key], dict):
                sub_context = context[sub_key].copy()
                
                # last_answer 길이 제한
                if "last_answer" in sub_context and isinstance(sub_context["last_answer"], str):
                    if len(sub_context["last_answer"]) > MAX_CONTEXT_TEXT_LENGTH:
                        sub_context["last_answer"] = sub_context["last_answer"][:MAX_CONTEXT_TEXT_LENGTH] + "..."
                
                # retrieved_contexts 크기 제한
                if "retrieved_contexts" in sub_context and isinstance(sub_context["retrieved_contexts"], list):
                    if len(sub_context["retrieved_contexts"]) > 3:
                        sub_context["retrieved_contexts"] = sub_context["retrieved_contexts"][:3]
                    # 각 컨텍스트 텍스트 길이 제한
                    for i, ctx in enumerate(sub_context["retrieved_contexts"]):
                        if isinstance(ctx, str) and len(ctx) > MAX_CONTEXT_TEXT_LENGTH:
                            sub_context["retrieved_contexts"][i] = ctx[:MAX_CONTEXT_TEXT_LENGTH] + "..."
                
                optimized[sub_key] = sub_context
            else:
                optimized[sub_key] = context[sub_key]
    
    # 나머지 키 복사
    for key in context:
        if key not in optimized:
            optimized[key] = context[key]
    
    return optimized


def optimize_tech_search_context(context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """기술 검색 컨텍스트를 최적화합니다.
    
    성능 최적화: 필요한 경우에만 복사하고 최적화.
    
    Args:
        context: 기술 검색 컨텍스트
        
    Returns:
        최적화된 컨텍스트
    """
    if not context:
        return None
    
    # 변경이 필요한지 먼저 확인 (불필요한 복사 방지)
    needs_optimization = False
    for sub_key in ["patent", "article"]:
        if sub_key in context and isinstance(context[sub_key], dict):
            sub_context = context[sub_key]
            if "results" in sub_context and isinstance(sub_context["results"], list):
                if len(sub_context["results"]) > MAX_CONTEXT_ITEMS:
                    needs_optimization = True
                    break
                # 큰 필드가 있는지 확인
                for result in sub_context["results"][:MAX_CONTEXT_ITEMS]:
                    if isinstance(result, dict):
                        for key in ["abstract", "description", "content"]:
                            if key in result and isinstance(result[key], str):
                                if len(result[key]) > MAX_CONTEXT_TEXT_LENGTH:
                                    needs_optimization = True
                                    break
                        if needs_optimization:
                            break
                if needs_optimization:
                    break
    
    # 최적화가 필요 없으면 원본 반환
    if not needs_optimization:
        return context
    
    # 최적화가 필요한 경우에만 복사 및 최적화
    optimized = {}
    
    # 각 서브 컨텍스트 최적화
    for sub_key in ["patent", "article"]:
        if sub_key in context:
            if isinstance(context[sub_key], dict):
                sub_context = context[sub_key].copy()
                
                # results 리스트 크기 제한
                if "results" in sub_context and isinstance(sub_context["results"], list):
                    if len(sub_context["results"]) > MAX_CONTEXT_ITEMS:
                        sub_context["results"] = sub_context["results"][:MAX_CONTEXT_ITEMS]
                    # 각 결과의 큰 필드 제한 (슬라이싱만 사용하여 빠르게)
                    for result in sub_context["results"]:
                        if isinstance(result, dict):
                            for key in ["abstract", "description", "content"]:
                                if key in result and isinstance(result[key], str):
                                    if len(result[key]) > MAX_CONTEXT_TEXT_LENGTH:
                                        result[key] = result[key][:MAX_CONTEXT_TEXT_LENGTH] + "..."
                
                optimized[sub_key] = sub_context
            else:
                optimized[sub_key] = context[sub_key]
        else:
            # 다른 키는 그대로 복사
            if sub_key not in optimized:
                optimized[sub_key] = context.get(sub_key)
    
    # 나머지 키 복사
    for key in context:
        if key not in optimized:
            optimized[key] = context[key]
    
    return optimized


def optimize_schedule_context(context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """스케줄 컨텍스트를 최적화합니다.
    
    성능 최적화: 필요한 경우에만 복사하고 최적화.
    
    Args:
        context: 스케줄 컨텍스트
        
    Returns:
        최적화된 컨텍스트
    """
    if not context:
        return None
    
    # 변경이 필요한지 먼저 확인 (불필요한 복사 방지)
    needs_optimization = False
    
    for sub_key in ["leave", "meeting", "schedule"]:
        if sub_key in context and isinstance(context[sub_key], dict):
            sub_context = context[sub_key]
            
            # last_results 리스트 크기 확인
            if "last_results" in sub_context and isinstance(sub_context["last_results"], list):
                if len(sub_context["last_results"]) > MAX_CONTEXT_ITEMS:
                    needs_optimization = True
                    break
            
            # recommended_rooms 리스트 크기 확인
            if "recommended_rooms" in sub_context and isinstance(sub_context["recommended_rooms"], list):
                if len(sub_context["recommended_rooms"]) > MAX_CONTEXT_ITEMS:
                    needs_optimization = True
                    break
    
    # 최적화가 필요 없으면 원본 반환
    if not needs_optimization:
        return context
    
    # 최적화가 필요한 경우에만 복사 및 최적화
    optimized = {}
    
    # 각 서브 컨텍스트 최적화
    for sub_key in ["leave", "meeting", "schedule"]:
        if sub_key in context:
            if isinstance(context[sub_key], dict):
                sub_context = context[sub_key].copy()
                
                # last_results 리스트 크기 제한
                if "last_results" in sub_context and isinstance(sub_context["last_results"], list):
                    if len(sub_context["last_results"]) > MAX_CONTEXT_ITEMS:
                        sub_context["last_results"] = sub_context["last_results"][:MAX_CONTEXT_ITEMS]
                
                # recommended_rooms 리스트 크기 제한
                if "recommended_rooms" in sub_context and isinstance(sub_context["recommended_rooms"], list):
                    if len(sub_context["recommended_rooms"]) > MAX_CONTEXT_ITEMS:
                        sub_context["recommended_rooms"] = sub_context["recommended_rooms"][:MAX_CONTEXT_ITEMS]
                
                optimized[sub_key] = sub_context
            else:
                optimized[sub_key] = context[sub_key]
    
    # 나머지 키 복사
    for key in context:
        if key not in optimized:
            optimized[key] = context[key]
    
    return optimized


def optimize_document_context(context: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """문서 컨텍스트를 최적화합니다.
    
    성능 최적화: 필요한 경우에만 복사하고 최적화.
    
    Args:
        context: 문서 컨텍스트
        
    Returns:
        최적화된 컨텍스트
    """
    if not context:
        return None
    
    # 변경이 필요한지 먼저 확인 (불필요한 복사 방지)
    needs_optimization = False
    
    # last_variables 크기 확인
    if "last_variables" in context and isinstance(context["last_variables"], dict):
        if len(context["last_variables"]) > 20:
            needs_optimization = True
    
    # 최적화가 필요 없으면 원본 반환
    if not needs_optimization:
        return context
    
    # 최적화가 필요한 경우에만 복사 및 최적화
    optimized = {}
    
    # last_variables 크기 제한 (너무 큰 딕셔너리는 제한)
    if "last_variables" in context and isinstance(context["last_variables"], dict):
        if len(context["last_variables"]) > 20:
            # 중요한 키만 유지
            important_keys = ["title", "template_type", "date", "author"]
            optimized["last_variables"] = {
                k: v for k, v in context["last_variables"].items()
                if k in important_keys
            }
        else:
            optimized["last_variables"] = context["last_variables"]
    elif "last_variables" in context:
        optimized["last_variables"] = context["last_variables"]
    
    # 나머지 키 복사
    for key in context:
        if key not in optimized:
            optimized[key] = context[key]
    
    return optimized


def optimize_state_before_save(state: OrchestratorState) -> OrchestratorState:
    """Redis 저장 전 state를 최적화합니다.
    
    성능 최적화: 변경된 컨텍스트만 최적화하여 처리 속도 향상.
    
    Args:
        state: 오케스트레이터 state
        
    Returns:
        최적화된 state
    """
    # 얕은 복사로 시작 (깊은 복사는 필요할 때만)
    optimized = {}
    
    # 필수 필드만 먼저 복사 (빠른 필드)
    essential_keys = ["query", "user_id", "session_id", "domain", "last_domain", 
                      "intent", "template_name", "employee_id", "final_response", "messages"]
    for key in essential_keys:
        if key in state:
            optimized[key] = state[key]
    
    # conversation_history 최적화 (항상 필요)
    if "conversation_history" in state:
        optimized["conversation_history"] = optimize_conversation_history(
            state.get("conversation_history", [])
        )
    
    # 각 도메인 컨텍스트 최적화
    # 최적화 함수들이 "필요한 경우에만 복사"하도록 개선되었으므로
    # 모든 컨텍스트를 최적화 함수에 통과시켜도 성능 저하 없음
    if "email_search_context" in state:
        optimized["email_search_context"] = optimize_email_search_context(
            state.get("email_search_context")
        )
    
    if "qa_search_context" in state:
        optimized["qa_search_context"] = optimize_qa_search_context(
            state.get("qa_search_context")
        )
    
    if "tech_search_context" in state:
        optimized["tech_search_context"] = optimize_tech_search_context(
            state.get("tech_search_context")
        )
    
    if "schedule_context" in state:
        optimized["schedule_context"] = optimize_schedule_context(
            state.get("schedule_context")
        )
    
    if "document_context" in state:
        optimized["document_context"] = optimize_document_context(
            state.get("document_context")
        )
    
    # 큰 중간 데이터 제거 (필요시에만 저장)
    # document_content는 파일로 저장되므로 state에서 제거 가능
    if "document_content" in state and state.get("document_path"):
        # document_path가 있으면 document_content는 제거 가능 (파일에서 로드 가능)
        # 하지만 멀티턴 대화를 위해 유지하는 것이 좋을 수 있음
        pass
    elif "document_content" in state:
        optimized["document_content"] = state["document_content"]
    
    if "document_path" in state:
        optimized["document_path"] = state["document_path"]
    
    # search_results 크기 제한 (간단한 슬라이싱만)
    if "search_results" in state and isinstance(state["search_results"], list):
        if len(state["search_results"]) > MAX_CONTEXT_ITEMS:
            optimized["search_results"] = state["search_results"][:MAX_CONTEXT_ITEMS]
        else:
            optimized["search_results"] = state["search_results"]
    
    # 나머지 필드 복사 (최적화하지 않고 그대로)
    for key in state:
        if key not in optimized:
            optimized[key] = state[key]
    
    return optimized

