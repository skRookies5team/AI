"""채용 마스터그래프 - 이력서 검색/추천/요약 (AI-030~AI-032)."""

from __future__ import annotations

from typing import Any, Dict, Optional, List
import asyncio
import re

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.recruitment.recruitment_agent import (
    search_resumes,
    recommend_talents,
    summarize_resume,
    get_resume_detail,
)


class RecruitmentMasterGraph(BaseMasterGraph):
    """채용 도메인 마스터그래프.
    
    요구사항:
    - AI-030: 채용 라우팅 (이력서 검색/추천/요약 분류)
    - AI-031: 이력서 검색 (벡터 DB 기반)
    - AI-032: 인재 추천 및 이력서 요약
    """
    
    def get_domain_name(self) -> str:
        return "recruitment"
    
    def get_graph(self) -> StateGraph:
        """채용 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("handle_search", self.handle_search)
        workflow.add_node("handle_recommend", self.handle_recommend)
        workflow.add_node("handle_summarize", self.handle_summarize)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "search": "handle_search",
                "recommend": "handle_recommend",
                "summarize": "handle_summarize",
            }
        )
        
        # 모든 하위 에이전트는 END로
        workflow.add_edge("handle_search", END)
        workflow.add_edge("handle_recommend", END)
        workflow.add_edge("handle_summarize", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """채용 도메인 내부 의도 분류."""
        print("--- [Recruitment Master] Routing Intent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        orchestrator_state = state
        
        # Redis 컨텍스트 확인
        recruitment_context = orchestrator_state.get("recruitment_context", {})
        recommended_resumes = recruitment_context.get("recommended_resumes", [])
        
        # 요약 대기 상태 확인 (최우선 처리)
        pending_summary = recruitment_context.get("pending_summary")
        print(f"🔍 [Route Intent] pending_summary 존재 여부: {pending_summary is not None}")
        if pending_summary:
            print(f"🔍 [Route Intent] pending_summary 내용: {pending_summary.get('selected_title', 'N/A')}")
            # "네", "예", "아니오" 등의 단순 응답 확인
            query_lower = query.lower().strip()
            is_response = any(keyword in query_lower for keyword in [
                "네", "예", "yes", "아니오", "아니", "no", 
                "요약", "요약해", "요약해줘", "안해", "안할래", "좋아", "ok", "okay"
            ])
            print(f"🔍 [Route Intent] 응답 감지 여부: {is_response}, 쿼리: '{query}'")
            if is_response:
                print(f"✅ 요약 확인 응답 감지: '{query}' -> summarize intent로 라우팅")
                return {"intent": "summarize"}
        
        # 이전에 추천한 인재가 있고, 사용자가 선택하는 패턴인지 확인
        if recommended_resumes:
            # 숫자로 시작하거나 "번"이 포함된 경우 (예: "1번", "첫 번째", "1. 요약")
            is_selection = (
                re.match(r"^\d+\.?\s*", query.strip()) or 
                re.search(r"\d+\s*번", query) or
                any(k in query for k in ["첫 번째", "두 번째", "세 번째", "네 번째", "다섯 번째"]) or
                any(k in query for k in ["요약", "핵심", "상세", "자세히"])
            )
            
            if is_selection:
                print(f"✅ 이전 추천 결과에서 선택 요청 감지: '{query}'")
                return {"intent": "summarize"}
        
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="recruitment",
            fallback_route="search",
            session_id=session_id
        )
        
        # 키워드 기반 재검증
        query_lower = query.lower()
        
        # "다른 인재", "추가 인재", "더 추천" 등의 요청 감지
        if any(k in query_lower for k in ["다른 인재", "추가 인재", "더 추천", "더 보여줘", "다른 사람", "다른 지원자"]):
            if recommended_resumes:
                print(f"✅ 다른 인재 요청 감지: '{query}'")
                intent = "recommend"  # 추가 인재 추천
            else:
                intent = "recommend"  # 첫 추천
        elif any(k in query for k in ["추천", "추천해", "추천해줘", "인재 추천", "인재를 추천"]):
            intent = "recommend"
        elif any(k in query for k in ["검색", "찾아", "찾아줘"]):
            intent = "search"
        elif intent not in ["search", "recommend", "summarize"]:
            intent = "recommend"  # 기본값: 추천
        
        print(f"✅ Recruitment 의도 결정: {intent}")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "recommend")
        return intent
    
    async def handle_search(self, state: OrchestratorState) -> Dict[str, Any]:
        """이력서 검색 (AI-031) - 벡터 DB 기반."""
        print("--- [Recruitment Master] Handling Search ---")
        query = state.get("query", "")
        orchestrator_state = state
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("recruitment_context") is None:
            orchestrator_state["recruitment_context"] = {}
        if orchestrator_state["recruitment_context"].get("search") is None:
            orchestrator_state["recruitment_context"]["search"] = {}
        
        search_context = orchestrator_state["recruitment_context"]["search"]
        
        # 이력서 검색
        result = await search_resumes(
            query=query,
            state=orchestrator_state
        )
        
        # Redis 컨텍스트 업데이트
        search_context["last_query"] = query
        search_context["last_results"] = result.get("resumes", [])
        
        return {
            "final_response": result.get("message", "이력서 검색 결과를 찾을 수 없습니다."),
            "messages": [result.get("message", "")],
            "search_results": result.get("resumes", []),
            "recruitment_context": orchestrator_state["recruitment_context"],
        }
    
    async def handle_recommend(self, state: OrchestratorState) -> Dict[str, Any]:
        """인재 추천 (AI-032)."""
        print("--- [Recruitment Master] Handling Recommend ---")
        query = state.get("query", "")
        orchestrator_state = state
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("recruitment_context") is None:
            orchestrator_state["recruitment_context"] = {}
        if orchestrator_state["recruitment_context"].get("recommend") is None:
            orchestrator_state["recruitment_context"]["recommend"] = {}
        
        recommend_context = orchestrator_state["recruitment_context"]["recommend"]
        
        # "다른 인재" 요청인지 확인
        query_lower = query.lower()
        is_more_request = any(k in query_lower for k in ["다른 인재", "추가 인재", "더 추천", "더 보여줘", "다른 사람", "다른 지원자"])
        
        # 이미 추천한 인재 목록 가져오기 (중복 제거용)
        existing_recommended = orchestrator_state["recruitment_context"].get("recommended_resumes", [])
        existing_titles = {resume.get("document_title", "") for resume in existing_recommended}
        
        # 이전 추천 쿼리 가져오기 (다른 인재 요청 시 같은 조건으로 검색)
        last_query = recommend_context.get("last_query", query)
        search_query = last_query if is_more_request else query
        
        # 인재 추천
        result = await recommend_talents(
            query=search_query,
            state=orchestrator_state,
            exclude_titles=existing_titles if is_more_request else None  # 다른 인재 요청 시 기존 추천 제외
        )
        
        # 추천 결과를 Redis에 저장
        new_recommended_resumes = result.get("resumes", [])
        
        if is_more_request and existing_recommended:
            # 기존 추천에 새 추천 추가 (최대 10명까지)
            all_recommended = existing_recommended + new_recommended_resumes
            orchestrator_state["recruitment_context"]["recommended_resumes"] = all_recommended[:10]
            if new_recommended_resumes:
                message = f"⭐ 추가로 {len(new_recommended_resumes)}명의 인재를 찾았습니다. 아래 카드를 클릭하여 상세 정보를 확인하세요."
            else:
                message = "⚠️ 추가로 추천할 인재를 찾을 수 없습니다. 다른 키워드로 시도해보세요."
        else:
            # 첫 추천이거나 일반 추천
            orchestrator_state["recruitment_context"]["recommended_resumes"] = new_recommended_resumes
            message = result.get("message", "추천 결과를 찾을 수 없습니다.")
        
        recommend_context["last_query"] = search_query
        recommend_context["last_recommendation_time"] = asyncio.get_event_loop().time()
        recommend_context["last_action"] = "recommend"  # 추천 액션 표시
        
        final_recommended = orchestrator_state["recruitment_context"]["recommended_resumes"]
        
        return {
            "final_response": message,
            "messages": [message],
            "recommended_resumes": final_recommended,
            "recruitment_context": orchestrator_state["recruitment_context"],
        }
    
    async def handle_summarize(self, state: OrchestratorState) -> Dict[str, Any]:
        """이력서 상세 정보 표시 및 요약 (AI-032) - 사용자가 선택한 인재의 상세 정보를 보여주고 요약 여부 확인."""
        print("--- [Recruitment Master] Handling Summarize ---")
        query = state.get("query", "")
        orchestrator_state = state
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("recruitment_context") is None:
            orchestrator_state["recruitment_context"] = {}
        
        recruitment_context = orchestrator_state["recruitment_context"]
        
        # 요약 대기 상태 확인 (사용자가 "네"/"아니오"로 응답한 경우)
        pending_summary = recruitment_context.get("pending_summary")
        if pending_summary:
            # "네", "예", "요약해줘" 등의 긍정 응답 확인
            query_lower = query.lower().strip()
            is_positive = any(keyword in query_lower for keyword in ["네", "예", "yes", "요약", "요약해", "요약해줘", "좋아", "ok", "okay"])
            is_negative = any(keyword in query_lower for keyword in ["아니오", "아니", "no", "안해", "안할래", "괜찮아", "필요없어"])
            
            if is_positive:
                # 요약 실행
                print(f"✅ [Summarize] 사용자 요약 승인: '{query}'")
                selected_resume = pending_summary.get("selected_resume")
                if selected_resume:
                    result = await summarize_resume(
                        query=query,
                        resume=selected_resume,
                        state=orchestrator_state
                    )
                    # pending_summary 제거
                    recruitment_context.pop("pending_summary", None)
                    recommend_context = recruitment_context.get("recommend", {})
                    recommend_context["last_action"] = "summarize"  # 요약 액션 표시 (카드 표시 안 함)
                    return {
                        "final_response": result.get("message", "이력서 요약을 생성할 수 없습니다."),
                        "messages": [result.get("message", "")],
                        "summary": result.get("summary", ""),
                        "recruitment_context": orchestrator_state.get("recruitment_context", {}),
                    }
            elif is_negative:
                # 요약 취소
                print(f"❌ [Summarize] 사용자 요약 거부: '{query}'")
                recruitment_context.pop("pending_summary", None)
                recommend_context = recruitment_context.get("recommend", {})
                recommend_context["last_action"] = "summarize"  # 요약 액션 표시 (카드 표시 안 함)
                return {
                    "final_response": "알겠습니다. 요약을 취소했습니다.",
                    "messages": ["요약을 취소했습니다."],
                    "recruitment_context": orchestrator_state.get("recruitment_context", {}),
                }
        
        # 이전 추천 결과 가져오기
        recommended_resumes = recruitment_context.get("recommended_resumes", [])
        
        if not recommended_resumes:
            # 이전 추천 결과가 없으면 먼저 검색 시도
            search_result = await search_resumes(
                query=query,
                state=orchestrator_state
            )
            recommended_resumes = search_result.get("resumes", [])
        
        if not recommended_resumes:
            return {
                "final_response": "요약할 인재를 찾을 수 없습니다. 먼저 '인재 추천해줘'라고 말씀해주세요.",
                "messages": ["인재를 찾을 수 없습니다."],
                "recruitment_context": orchestrator_state.get("recruitment_context", {}),
            }
        
        # 디버깅: 추천된 인재 목록 확인
        print(f"🔍 [Summarize] 추천된 인재 목록 ({len(recommended_resumes)}명):")
        for idx, resume in enumerate(recommended_resumes, 1):
            title = resume.get("document_title", "제목 없음")
            print(f"  {idx}. {title}")
        
        print(f"🔍 [Summarize] 사용자 쿼리: '{query}'")
        
        # 사용자가 선택한 인재 찾기
        selected_resume = self._parse_selection(query, recommended_resumes)
        
        if selected_resume:
            selected_title = selected_resume.get("document_title", "제목 없음")
            print(f"✅ [Summarize] 선택된 인재: {selected_title}")
        else:
            print(f"⚠️ [Summarize] 선택 파싱 실패, 첫 번째 인재를 기본값으로 사용")
            # 선택을 파싱하지 못한 경우, 첫 번째 인재를 기본값으로 사용
            selected_resume = recommended_resumes[0] if recommended_resumes else None
            if selected_resume:
                selected_title = selected_resume.get("document_title", "제목 없음")
                print(f"  → 기본값으로 선택된 인재: {selected_title}")
        
        if not selected_resume:
            return {
                "final_response": "요약할 인재를 선택할 수 없습니다. 번호나 이름으로 선택해주세요. (예: '1번', '첫 번째', '김철수')",
                "messages": ["인재를 선택할 수 없습니다."],
                "recruitment_context": orchestrator_state.get("recruitment_context", {}),
            }
        
        # 선택된 인재의 상세 정보 표시 (요약 없이)
        detail_result = await get_resume_detail(
            resume=selected_resume,
            state=orchestrator_state
        )
        
        # 요약 대기 상태로 저장
        selected_title = selected_resume.get("document_title", "제목 없음")
        recruitment_context["pending_summary"] = {
            "selected_resume": selected_resume,
            "selected_title": selected_title
        }
        recommend_context = recruitment_context.get("recommend", {})
        recommend_context["last_action"] = "summarize"  # 요약 액션 표시 (카드 표시 안 함)
        print(f"✅ [Summarize] pending_summary 저장 완료: {selected_title}")
        print(f"🔍 [Summarize] recruitment_context 상태: {list(recruitment_context.keys())}")
        
        # 상세 정보와 요약 확인 메시지를 별도 메시지로 분리
        detail_message = detail_result.get("message", "")
        confirmation_message = "🤖 AI가 이력서를 요약해드릴까요? (네/아니오)"
        
        return {
            "final_response": detail_message,  # 상세 정보만
            "messages": [detail_message, confirmation_message],  # 두 개의 별도 메시지
            "recruitment_context": orchestrator_state.get("recruitment_context", {}),
            "requires_input": True,  # 사용자 입력 필요
        }
    
    def _parse_selection(self, query: str, recommended_resumes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """사용자 쿼리에서 선택한 인재를 파싱.
        
        Args:
            query: 사용자 쿼리 (예: "1번", "첫 번째", "김철수")
            recommended_resumes: 추천된 인재 목록
        
        Returns:
            선택된 인재 딕셔너리 또는 None
        """
        if not recommended_resumes:
            print(f"⚠️ [Parse Selection] recommended_resumes가 비어있습니다.")
            return None
        
        query_lower = query.lower().strip()
        print(f"🔍 [Parse Selection] 쿼리 파싱: '{query}' -> '{query_lower}'")
        
        # 숫자로 시작하는 경우 (예: "1번", "1.", "1번 요약해줘")
        number_match = re.match(r"^(\d+)", query_lower)
        if number_match:
            card_number = int(number_match.group(1))
            index = card_number - 1  # 0-based index (카드는 1부터 시작)
            print(f"🔍 [Parse Selection] 숫자 매칭: 카드 번호 {card_number} -> 인덱스 {index}")
            
            if 0 <= index < len(recommended_resumes):
                selected_resume = recommended_resumes[index]
                selected_title = selected_resume.get("document_title", "제목 없음")
                print(f"✅ [Parse Selection] 인재 선택 성공: {selected_title}")
                return selected_resume
            else:
                print(f"⚠️ [Parse Selection] 인덱스 범위 초과: {index} (최대: {len(recommended_resumes) - 1})")
                return None
        
        # "첫 번째", "두 번째" 등의 표현
        ordinal_map = {
            "첫": 0, "두": 1, "세": 2, "네": 3, "다섯": 4,
            "여섯": 5, "일곱": 6, "여덟": 7, "아홉": 8, "열": 9
        }
        for ordinal, index in ordinal_map.items():
            if ordinal in query_lower:
                if index < len(recommended_resumes):
                    return recommended_resumes[index]
        
        # 이름으로 검색 (document_title에서 이름 추출 시도)
        # 쿼리에서 한글 이름 패턴 추출 (예: "1번 김철수 요약해줘" -> "김철수")
        name_pattern = re.search(r'([가-힣]{2,4})', query_lower)
        if name_pattern:
            query_name = name_pattern.group(1)
            print(f"🔍 [Parse Selection] 이름 패턴 추출: '{query_name}'")
            
            # 숫자와 이름이 모두 있는 경우, 숫자를 우선하고 이름으로 검증
            number_match = re.match(r"^(\d+)", query_lower)
            if number_match:
                card_number = int(number_match.group(1))
                index = card_number - 1
                
                # 숫자로 먼저 선택
                if 0 <= index < len(recommended_resumes):
                    selected_resume = recommended_resumes[index]
                    selected_title = selected_resume.get("document_title", "제목 없음")
                    
                    # 선택된 인재의 이름 추출
                    title_name_pattern = re.search(r'([가-힣]{2,4})', selected_title)
                    if title_name_pattern:
                        title_name = title_name_pattern.group(1)
                        # 이름이 일치하면 선택 성공
                        if query_name == title_name:
                            print(f"✅ [Parse Selection] 숫자+이름 매칭 성공: 카드 {card_number}번, 이름 '{query_name}' -> {selected_title}")
                            return selected_resume
                        else:
                            print(f"⚠️ [Parse Selection] 숫자로 선택했지만 이름 불일치: '{title_name}' != '{query_name}', 이름으로 재검색")
                    
                    # 이름이 일치하지 않으면 이름으로 재검색
                    for idx, resume in enumerate(recommended_resumes):
                        resume_title = resume.get("document_title", "")
                        resume_name_pattern = re.search(r'([가-힣]{2,4})', resume_title)
                        if resume_name_pattern:
                            resume_name = resume_name_pattern.group(1)
                            if query_name == resume_name:
                                print(f"✅ [Parse Selection] 이름으로 재매칭 성공: '{query_name}' -> 인덱스 {idx}, {resume_title}")
                                return resume
            
            # 숫자가 없고 이름만 있는 경우
            else:
                for idx, resume in enumerate(recommended_resumes):
                    document_title = resume.get("document_title", "")
                    title_name_pattern = re.search(r'([가-힣]{2,4})', document_title)
                    if title_name_pattern:
                        title_name = title_name_pattern.group(1)
                        if query_name == title_name:
                            print(f"✅ [Parse Selection] 이름만으로 매칭 성공: '{query_name}' -> 인덱스 {idx}, {document_title}")
                            return resume
        
        return None


def get_recruitment_master_graph() -> StateGraph:
    """채용 마스터그래프 인스턴스 반환."""
    master = RecruitmentMasterGraph()
    return master.get_graph()

