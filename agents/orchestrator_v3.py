"""개선된 LangGraph Orchestrator - 도메인 레벨 라우팅 + 마스터그래프 구조.

멘토님 피드백 반영:
- 오케스트레이터는 도메인만 결정하고 마스터그래프로 라우팅
- 각 마스터그래프에서 세부 처리 (도구 선택, 다음 노드 결정, Human-in-the-loop)
"""

from __future__ import annotations

from typing import Dict, Any
import asyncio
import re
import logging

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
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


# Tech 도메인 논문 후속 질의 키워드
TECH_FOLLOWUP_KEYWORDS = [
    "요약", "한줄 요약", "정리해줘", "정리해서",
    "결론", "결론 부분", "결론 좀", "결론만",
    "핵심", "핵심만", "중요한 점", "포인트",
    "설명해줘", "자세히", "자세하게", "다시 말해줘", "다시 설명해줘",
    "이 논문", "논문에서", "이 연구", "연구에서", "이 결과", "결과에서",
    "장점", "단점", "한계", "제한점", "의의", "시사점",
    "정의", "뜻이 뭐", "뜻이 뭐야", "무슨 뜻", "뭐야", "뭔데", "약자", "풀어서 설명",
]

# Tech 컨텍스트에서의 yes/no 응답 키워드
TECH_YES_NO_KEYWORDS = ["네", "예", "넵", "응", "아니오", "아니요"]


logger = logging.getLogger(__name__)

async def route_to_domain(state: OrchestratorState) -> Dict[str, Any]:
    """사용자 요청을 분석하여 도메인만 결정 (AI-001: Intent Router).
    
    오케스트레이터는 도메인 레벨에서만 라우팅하고,
    세부 처리는 각 마스터그래프에서 담당합니다.
    
    Args:
        state: 오케스트레이터 상태
        
    Returns:
        domain: 도메인 이름 (schedule, document, qa, email, tech)
    """
    logger.info("--- [Orchestrator] Routing to Domain ---")
    query = state.get("query", "").strip()
    session_id = state.get("session_id")
    last_domain = state.get("last_domain")
    
    # 단순 숫자/선택지 답변 감지 (숫자, 숫자+번, 콤마로 구분된 숫자들, 웹/사내 선택지 등)
    # 이전 대화의 도메인으로 라우팅
    is_single_number = query.isdigit()
    is_number_list = bool(re.fullmatch(r"\d+(\s*,\s*\d+)*", query))
    is_number_with_suffix = bool(re.search(r"\d+\s*번", query))

    is_number_choice = is_single_number or is_number_list or is_number_with_suffix
    is_source_choice = query in ["웹", "사내", "웹에서", "사내에서"]

    if is_number_choice or is_source_choice:
        logger.info(f"--- [Orchestrator] 단순 선택지 답변 감지: {query} ---")
        
        # 이전 대화의 도메인이 있으면 그대로 사용
        if last_domain and last_domain in MASTER_GRAPHS:
            logger.info(f"✅ 이전 대화 도메인으로 라우팅: {last_domain}")
            return {"domain": last_domain}
        else:
            logger.warning(f"⚠️ 이전 대화 도메인을 찾을 수 없음 (last_domain={last_domain}), LLM으로 분류")
    
    # Tech 도메인 yes/no 후속 질의 휴리스틱:
    # 직전 도메인이 tech이고, 사용자가 '네/예/아니오/아니요'로 답하면
    # Tech 도메인으로 라우팅하여 특허/논문 에이전트 내부 로직에 위임
    if last_domain == "tech" and query in TECH_YES_NO_KEYWORDS:
        logger.info("✅ Tech yes/no 후속 질의로 판단, tech 도메인으로 라우팅")
        return {"domain": "tech"}

    # Tech 도메인 논문 후속 질의 휴리스틱:
    # 직전 도메인이 tech이고, 요약/결론/핵심/설명 등 키워드가 들어가면 tech로 고정 라우팅
    if last_domain == "tech":
        if any(k in query for k in TECH_FOLLOWUP_KEYWORDS):
            logger.info("✅ Tech 논문 후속 질의로 판단, tech 도메인으로 라우팅")
            return {"domain": "tech"}

    # llm_router를 사용한 LLM 기반 도메인 분류
    # 동기 함수를 async에서 호출하기 위해 asyncio.to_thread 사용
    domain = await asyncio.to_thread(
        classify_intent_with_llm,
        query=query,
        route_type="supervisor",
        fallback_route="qa",
        session_id=session_id
    )
    
    # 유효성 검사
    if domain not in MASTER_GRAPHS:
        logger.warning(f"⚠️ 알 수 없는 도메인: {domain}, qa로 fallback")
        domain = "qa"
    
    logger.info(f"✅ 도메인 결정: {domain}")
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
        graph = get_schedule_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트
        result["last_domain"] = "schedule"
        return result
    
    async def document_node(state: OrchestratorState) -> Dict[str, Any]:
        """문서 마스터그래프 실행."""
        graph = get_document_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트
        result["last_domain"] = "document"
        return result
    
    async def qa_node(state: OrchestratorState) -> Dict[str, Any]:
        """QA 마스터그래프 실행."""
        graph = get_qa_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트
        result["last_domain"] = "qa"
        return result
    
    async def email_node(state: OrchestratorState) -> Dict[str, Any]:
        """이메일 마스터그래프 실행."""
        logger.info("--- [Orchestrator] Executing Email Master Graph ---")
        graph = get_email_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트
        result["last_domain"] = "email"
        return result
    
    async def tech_node(state: OrchestratorState) -> Dict[str, Any]:
        """기술 마스터그래프 실행."""
        graph = get_tech_master_graph()
        result = await graph.ainvoke(state)
        # 이전 도메인 정보 업데이트
        result["last_domain"] = "tech"
        return result
    
    # 각 도메인별 노드 등록
    workflow.add_node("schedule", schedule_node)
    workflow.add_node("document", document_node)
    workflow.add_node("qa", qa_node)
    workflow.add_node("email", email_node)
    workflow.add_node("tech", tech_node)
    
    logger.info("✅ 마스터그래프 등록 완료: schedule, document, qa, email, tech")
    
    # 엣지 설정
    workflow.set_entry_point("route_to_domain")
    
    # 조건부 라우팅: 도메인에 따라 해당 마스터그래프로 라우팅
    def route_to_master_graph(state: OrchestratorState) -> str:
        """도메인에 따라 마스터그래프로 라우팅."""
        domain = state.get("domain")
        logger.info(f"--- [Orchestrator] State domain value: {domain} ---")
        logger.info(f"--- [Orchestrator] State keys: {list(state.keys())} ---")
        
        # domain이 없거나 None이면 기본값 사용
        if not domain:
            logger.warning("⚠️ domain이 state에 없습니다. qa로 fallback")
            domain = "qa"
        
        # 유효성 검사
        if domain not in MASTER_GRAPHS:
            logger.warning(f"⚠️ 알 수 없는 도메인: {domain}, qa로 fallback")
            domain = "qa"
        
        logger.info(f"--- [Orchestrator] Routing to {domain} master graph ---")
        return domain
    
    workflow.add_conditional_edges(
        "route_to_domain",
        route_to_master_graph,
        MASTER_GRAPHS.keys()
    )
    
    # 모든 마스터그래프는 END로 연결
    for domain_name in MASTER_GRAPHS.keys():
        workflow.add_edge(domain_name, END)
    
    logger.info(f"📊 총 {len(MASTER_GRAPHS)}개 마스터그래프 등록 완료")
    return workflow.compile()

