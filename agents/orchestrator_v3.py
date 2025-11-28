"""개선된 LangGraph Orchestrator - 도메인 레벨 라우팅 + 마스터그래프 구조.

멘토님 피드백 반영:
- 오케스트레이터는 도메인만 결정하고 마스터그래프로 라우팅
- 각 마스터그래프에서 세부 처리 (도구 선택, 다음 노드 결정, Human-in-the-loop)
"""

from __future__ import annotations

from typing import Dict, Any
import asyncio

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs import (
    # get_schedule_master_graph,  # 주석 처리 (schedule 폴더가 없음)
    get_document_master_graph,
    # get_email_master_graph,  # 주석 처리 (email 폴더가 없을 수 있음)
    # get_tech_master_graph,  # 주석 처리 (tech 폴더가 없을 수 있음)
    get_schedule_master_graph,  # lazy import로 제공됨
    get_email_master_graph,  # lazy import로 제공됨
    get_tech_master_graph,  # lazy import로 제공됨
)
# qa 마스터그래프는 lazy import (sentence_transformers 의존성 때문)
from agents.master_graphs import get_qa_master_graph


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
    
    Args:
        state: 오케스트레이터 상태
        
    Returns:
        domain: 도메인 이름 (schedule, document, qa, email, tech)
    """
    print("--- [Orchestrator] Routing to Domain ---")
    query = state.get("query", "").strip()
    session_id = state.get("session_id")
    last_domain = state.get("last_domain")
    
    # 단순 숫자/선택지 답변 감지 (1, 2, 3 등)
    # 이전 대화의 도메인으로 라우팅
    if query.isdigit() or query in ["1", "2", "3", "4", "5", "웹", "사내", "웹에서", "사내에서"]:
        print(f"--- [Orchestrator] 단순 선택지 답변 감지: {query} ---")
        
        # 이전 대화의 도메인이 있으면 그대로 사용
        if last_domain and last_domain in MASTER_GRAPHS:
            print(f"✅ 이전 대화 도메인으로 라우팅: {last_domain}")
            return {"domain": last_domain}
        else:
            print(f"⚠️ 이전 대화 도메인을 찾을 수 없음 (last_domain={last_domain}), LLM으로 분류")
    
    # 키워드 기반 사전 필터링 (LLM 호출 전에 명확한 케이스 처리)
    query_lower = query.lower()
    
    # 문서 작성 키워드 명확한 경우 document로 우선 분류
    document_keywords = ["작성", "계획서", "보고서", "문서", "양식", "템플릿", "생성", "만들기"]
    if any(kw in query_lower for kw in document_keywords):
        # 문서 작성 관련 키워드가 있으면 document로 분류
        print(f"✅ 키워드 기반 분류: document (문서 작성 키워드 감지)")
        return {"domain": "document"}
    
    # llm_router를 사용한 LLM 기반 도메인 분류
    # 동기 함수를 async에서 호출하기 위해 asyncio.to_thread 사용
    domain = await asyncio.to_thread(
        classify_intent_with_llm,
        query=query,
        route_type="supervisor",
        fallback_route="document",  # fallback을 document로 변경 (qa보다 document가 더 일반적)
        session_id=session_id
    )
    
    # 유효성 검사
    if domain not in MASTER_GRAPHS:
        print(f"⚠️ 알 수 없는 도메인: {domain}, document로 fallback")
        domain = "document"  # qa 대신 document로 fallback
    
    print(f"✅ 도메인 결정: {domain}")
    return {"domain": domain}


def get_orchestrator_graph():
    """개선된 오케스트레이터 그래프 생성.
    
    구조:
    1. route_to_domain: 도메인 결정
    2. 각 도메인별 마스터그래프 (서브그래프)
    """
    workflow = StateGraph(OrchestratorState)
    
    # Router 노드 추가
    workflow.add_node("route_to_domain", route_to_domain)
    
    # 각 도메인별 마스터그래프를 서브그래프로 추가
    # 클로저 문제를 피하기 위해 각 도메인별로 별도 함수 생성
    async def schedule_node(state: OrchestratorState) -> Dict[str, Any]:
        """스케줄 마스터그래프 실행."""
        try:
            graph = get_schedule_master_graph()
            result = await graph.ainvoke(state)
            # 이전 도메인 정보 업데이트
            result["last_domain"] = "schedule"
            return result
        except ImportError as e:
            error_msg = "스케줄 서비스가 현재 사용할 수 없습니다. schedule 폴더가 필요합니다."
            print(f"⚠️ {error_msg} ({str(e)})")
            return {
                "final_response": error_msg,
                "messages": [error_msg],
                "last_domain": "schedule",
            }
    
    async def document_node(state: OrchestratorState) -> Dict[str, Any]:
        """문서 마스터그래프 실행."""
        graph = get_document_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트
        result["last_domain"] = "document"
        return result
    
    async def qa_node(state: OrchestratorState) -> Dict[str, Any]:
        """QA 마스터그래프 실행."""
        try:
            graph = get_qa_master_graph()
            result = await graph.ainvoke(state)
            # 이전 도메인 정보 업데이트
            result["last_domain"] = "qa"
            return result
        except ImportError as e:
            # qa 마스터그래프가 없을 경우 에러 메시지 반환
            error_msg = "QA 서비스가 현재 사용할 수 없습니다. qa 폴더가 필요합니다."
            print(f"⚠️ {error_msg} ({str(e)})")
            return {
                "final_response": error_msg,
                "messages": [error_msg],
                "last_domain": "qa",
            }
        except Exception as e:
            # 기타 예외 처리
            error_msg = f"QA 서비스 실행 중 오류가 발생했습니다: {str(e)}"
            print(f"❌ {error_msg}")
            import traceback
            traceback.print_exc()
            return {
                "final_response": error_msg,
                "messages": [error_msg],
                "last_domain": "qa",
            }
    
    async def email_node(state: OrchestratorState) -> Dict[str, Any]:
        """이메일 마스터그래프 실행."""
        print("--- [Orchestrator] Executing Email Master Graph ---")
        try:
            graph = get_email_master_graph()
            result = await graph.ainvoke(state)
            # 이전 도메인 정보 업데이트
            result["last_domain"] = "email"
            return result
        except ImportError as e:
            error_msg = "이메일 서비스가 현재 사용할 수 없습니다. email 폴더가 필요합니다."
            print(f"⚠️ {error_msg} ({str(e)})")
            return {
                "final_response": error_msg,
                "messages": [error_msg],
                "last_domain": "email",
            }
    
    async def tech_node(state: OrchestratorState) -> Dict[str, Any]:
        """기술 마스터그래프 실행."""
        try:
            graph = get_tech_master_graph()
            result = await graph.ainvoke(state)
            # 이전 도메인 정보 업데이트
            result["last_domain"] = "tech"
            return result
        except ImportError as e:
            error_msg = "기술 서비스가 현재 사용할 수 없습니다. tech 폴더가 필요합니다."
            print(f"⚠️ {error_msg} ({str(e)})")
            return {
                "final_response": error_msg,
                "messages": [error_msg],
                "last_domain": "tech",
            }
    
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
    return workflow.compile()

