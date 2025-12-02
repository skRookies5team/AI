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
from datetime import datetime

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState, Message
from agents.llm_router import classify_intent_with_llm
from agents.redis_checkpoint import get_redis_checkpointer
from agents.structured_output import extract_structured_output, QueryIntent
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


async def route_to_domain(state: OrchestratorState) -> Dict[str, Any]:
    """사용자 요청을 분석하여 도메인만 결정 (AI-001: Intent Router).
    
    오케스트레이터는 도메인 레벨에서만 라우팅하고,
    세부 처리는 각 마스터그래프에서 담당합니다.
    
    Structured Output Parser를 활용하여 구조화된 의도 분석을 수행합니다.
    
    Args:
        state: 오케스트레이터 상태
        
    Returns:
        domain: 도메인 이름 (schedule, document, qa, email, tech)
    """
    print("--- [Orchestrator] Routing to Domain ---")
    query = state.get("query", "").strip()
    session_id = state.get("session_id")
    last_domain = state.get("last_domain")
    conversation_history = state.get("conversation_history", [])
    
    # 대화 히스토리에 사용자 메시지 추가
    user_message: Message = {
        "role": "user",
        "content": query,
        "timestamp": datetime.now().isoformat()
    }
    
    # 명시적 도메인 키워드 확인 (단순 선택지 감지 전에 확인)
    # 명시적 키워드가 있으면 새로운 요청으로 간주
    explicit_domain_keywords = {
        "논문": "tech",
        "paper": "tech",
        "학술": "tech",
        "ScienceON": "tech",
        "특허": "tech",
        "patent": "tech",
        "Patent": "tech",
        "메일": "email",
        "이메일": "email",
        "메일함": "email",
        "email": "email",
        "Email": "email",
        "일정": "schedule",
        "연차": "schedule",
        "회의실": "schedule",
        "문서": "document",
        "보고서": "document",
        "템플릿": "document",
    }
    
    query_lower = query.lower()
    for keyword, domain in explicit_domain_keywords.items():
        if keyword.lower() in query_lower:
            print(f"✅ 명시적 도메인 키워드 '{keyword}' 감지: {domain}로 라우팅")
            return {
                "domain": domain,
                "conversation_history": [user_message]
            }
    
    # 단순 숫자/선택지 답변 감지 (1, 2, 3 등)
    # 이전 대화의 도메인으로 라우팅
    # "웹에서 검색", "사내 서버에서" 등 선택지 답변 처리 강화
    is_simple_selection = False
    
    # 1. 숫자만 있는 경우
    if query.isdigit():
        is_simple_selection = True
    # 2. 짧은 선택지 답변 (5글자 이하)
    elif len(query) <= 5 and any(k in query for k in ["1", "2", "3", "4", "5", "웹", "사내"]):
        is_simple_selection = True
    # 3. 명확한 선택지 키워드 포함 ("웹에서", "사내에서", "첫번째", "두번째" 등)
    # 단, 명시적 도메인 키워드가 없는 경우에만
    elif any(k in query for k in ["웹에서", "사내에서", "웹 검색", "사내 검색", "사내 서버", "첫번째", "두번째", "첫 번째", "두 번째"]):
        is_simple_selection = True
        
    if is_simple_selection:
        print(f"--- [Orchestrator] 단순 선택지 답변 감지: {query} ---")
        
        # 이전 대화의 도메인이 있으면 그대로 사용
        if last_domain and last_domain in MASTER_GRAPHS:
            print(f"✅ 이전 대화 도메인으로 라우팅: {last_domain}")
            return {
                "domain": last_domain,
                "conversation_history": [user_message]
            }
        else:
            print(f"⚠️ 이전 대화 도메인을 찾을 수 없음 (last_domain={last_domain}), LLM으로 분류")
    
    # Structured Output Parser를 사용한 의도 분석 시도
    try:
        # 대화 히스토리 컨텍스트 구성
        context = ""
        if conversation_history:
            recent_history = conversation_history[-5:]  # 최근 5개 메시지만 사용
            context = "\n\n이전 대화 내용:\n"
            for msg in recent_history:
                context += f"{msg.get('role', 'unknown')}: {msg.get('content', '')}\n"
        
        system_prompt = f"""사용자 질의를 분석하여 다음 도메인 중 하나로 분류하세요:

**도메인 분류 기준:**

1. **email (이메일)**: 
   - "메일에서", "이메일에서", "메일함에서", "메일에서 찾아줘", "이메일 검색" 등의 표현이 포함된 경우
   - 이메일 검색, 이메일 답장문 생성 요청
   - **중요**: 질의에 "메일", "이메일", "메일함" 등의 키워드가 포함되어 있으면 반드시 email 도메인으로 분류하세요.

2. **schedule (일정)**: 
   - 연차/반차/병가/경조사 신청, 회의실 예약, 일정 관리

3. **document (문서)**: 
   - 문서 작성, 보고서 작성, 템플릿 생성

4. **qa (질문/문의)**: 
   - Q&A, 질문, 문의, 메뉴얼, 복리후생 정책 관련
   - **주의**: "메일에서", "이메일에서" 같은 표현이 있으면 qa가 아닌 email로 분류해야 합니다.

5. **tech (기술 검색)**: 
   - 특허, 논문, KISTI, ScienceON 관련

**분류 우선순위:**
- "메일에서", "이메일에서" 등의 표현이 있으면 → email
- 특허, 논문 관련 키워드가 있으면 → tech
- 연차, 반차, 회의실 예약 등이 있으면 → schedule
- 문서 작성 요청이 있으면 → document
- 그 외 일반 질문/문의 → qa

{context}"""
        
        intent_result = await extract_structured_output(
            query=query,
            model_class=QueryIntent,
            system_prompt=system_prompt
        )
        
        domain = intent_result.intent
        print(f"✅ Structured Output으로 도메인 결정: {domain} (신뢰도: {intent_result.confidence:.2f})")
        
    except Exception as e:
        print(f"⚠️ Structured Output 파싱 실패: {e}, LLM 라우터로 fallback")
        # llm_router를 사용한 LLM 기반 도메인 분류 (fallback)
        domain = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="supervisor",
            fallback_route="qa",
            session_id=session_id
        )
    
    # 유효성 검사
    if domain not in MASTER_GRAPHS:
        print(f"⚠️ 알 수 없는 도메인: {domain}, qa로 fallback")
        domain = "qa"
    
    print(f"✅ 도메인 결정: {domain}")
    return {
        "domain": domain,
        "conversation_history": [user_message]
    }


def get_orchestrator_graph(checkpointer: Optional[Any] = None):
    """개선된 오케스트레이터 그래프 생성.
    
    Redis 체크포인터를 사용하여 state를 관리하고 멀티턴 대화를 지원합니다.
    
    Args:
        checkpointer: Redis 체크포인터 인스턴스 (선택적, None이면 자동 생성 시도)
    
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
    # 클로저 문제를 피하기 위해 각 도메인별로 별도 함수 생성
    async def schedule_node(state: OrchestratorState) -> Dict[str, Any]:
        """스케줄 마스터그래프 실행."""
        graph = get_schedule_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트 및 응답 메시지 추가
        result["last_domain"] = "schedule"
        response_message: Message = {
            "role": "assistant",
            "content": result.get("final_response", "스케줄 작업을 완료했습니다."),
            "timestamp": datetime.now().isoformat()
        }
        result["conversation_history"] = [response_message]
        return result
    
    async def document_node(state: OrchestratorState) -> Dict[str, Any]:
        """문서 마스터그래프 실행."""
        graph = get_document_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트 및 응답 메시지 추가
        result["last_domain"] = "document"
        response_message: Message = {
            "role": "assistant",
            "content": result.get("final_response", "문서 작업을 완료했습니다."),
            "timestamp": datetime.now().isoformat()
        }
        result["conversation_history"] = [response_message]
        return result
    
    async def qa_node(state: OrchestratorState) -> Dict[str, Any]:
        """QA 마스터그래프 실행."""
        graph = get_qa_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트 및 응답 메시지 추가
        result["last_domain"] = "qa"
        response_message: Message = {
            "role": "assistant",
            "content": result.get("final_response", "질문에 답변했습니다."),
            "timestamp": datetime.now().isoformat()
        }
        result["conversation_history"] = [response_message]
        return result
    
    async def email_node(state: OrchestratorState) -> Dict[str, Any]:
        """이메일 마스터그래프 실행."""
        print("--- [Orchestrator] Executing Email Master Graph ---")
        graph = get_email_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트 및 응답 메시지 추가
        result["last_domain"] = "email"
        response_message: Message = {
            "role": "assistant",
            "content": result.get("final_response", "이메일 작업을 완료했습니다."),
            "timestamp": datetime.now().isoformat()
        }
        result["conversation_history"] = [response_message]
        return result
    
    async def tech_node(state: OrchestratorState) -> Dict[str, Any]:
        """기술 마스터그래프 실행."""
        graph = get_tech_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트 및 응답 메시지 추가
        result["last_domain"] = "tech"
        response_message: Message = {
            "role": "assistant",
            "content": result.get("final_response", "기술 검색 작업을 완료했습니다."),
            "timestamp": datetime.now().isoformat()
        }
        result["conversation_history"] = [response_message]
        return result
    
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
        domain = state.get("domain")
        print(f"--- [Orchestrator] State domain value: {domain} ---")
        print(f"--- [Orchestrator] State keys: {list(state.keys())} ---")
        
        # domain이 없거나 None이면 기본값 사용
        if not domain:
            print("⚠️ domain이 state에 없습니다. qa로 fallback")
            domain = "qa"
        
        # 유효성 검사
        if domain not in MASTER_GRAPHS:
            print(f"⚠️ 알 수 없는 도메인: {domain}, qa로 fallback")
            domain = "qa"
        
        print(f"--- [Orchestrator] Routing to {domain} master graph ---")
        return domain
    
    workflow.add_conditional_edges(
        "route_to_domain",
        route_to_master_graph,
        MASTER_GRAPHS.keys()
    )
    
    # 모든 마스터그래프는 END로 연결
    for domain_name in MASTER_GRAPHS.keys():
        workflow.add_edge(domain_name, END)
    
    print(f"📊 총 {len(MASTER_GRAPHS)}개 마스터그래프 등록 완료")
    
    # 체크포인터 설정: RedisSaver와 RedisStore만 사용
    if checkpointer is not None:
        # 명시적으로 전달된 체크포인터 사용
        print("✅ 전달된 Redis 체크포인터 사용")
        return workflow.compile(checkpointer=checkpointer)
    
    # 체크포인터가 전달되지 않았으면 Redis 체크포인터 자동 생성
    print("🔍 Redis 체크포인터 자동 생성 시도...")
    redis_checkpointer = get_redis_checkpointer()
    
    if redis_checkpointer:
        print("✅ Redis 체크포인터 자동 생성 성공 - 숏텀 메모리 활성화")
        print("   - RedisSaver를 사용합니다 (Redis에 state 저장)")
        print("   - 세션별 state가 Redis에 저장됩니다")
        print("   - 멀티턴 대화가 지원됩니다")
        return workflow.compile(checkpointer=redis_checkpointer)
    else:
        # Redis가 필수이므로 에러 발생
        raise RuntimeError(
            "❌ Redis 체크포인터를 사용할 수 없습니다. "
            "Redis 서버가 실행 중인지 확인하고, 환경 변수를 설정해주세요.\n"
            "필요한 환경 변수:\n"
            "  - HOST 또는 REDIS_HOST (기본값: localhost)\n"
            "  - REDIS_PORT (기본값: 6379)\n"
            "  - REDIS_PASSWORD (선택적)\n"
            "  - REDIS_DB (기본값: 0)\n"
            "설치 명령: pip install redis langgraph-checkpoint-redis"
        )

