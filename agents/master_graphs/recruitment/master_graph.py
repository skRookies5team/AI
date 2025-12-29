"""채용 마스터그래프 - 인재 추천 및 이력서 요약."""

from __future__ import annotations

from typing import Any, Dict
import asyncio
import re
import logging
import time
from datetime import datetime
from pathlib import Path

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.structured_output import extract_structured_output
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.recruitment.resume_search_agent import search_resumes
from agents.master_graphs.recruitment.resume_summarize_agent import summarize_resume
from pydantic import BaseModel, Field
from typing import Optional

# 로거 설정
logger = logging.getLogger(__name__)


class SearchParameters(BaseModel):
    """검색 파라미터 추출 결과."""
    suggested_count: Optional[int] = Field(
        default=None,
        description=(
            "사용자가 요청한 인재 수. "
            "예: '2명', '3-5명', '한 명', '여러 명' 등에서 추출. "
            "명시적 요청이 없으면 None. "
            "1 이상 10 이하의 값만 설정하세요."
        ),
        ge=1,
        le=10
    )


class RecruitmentMasterGraph(BaseMasterGraph):
    """채용 도메인 마스터그래프.
    
    기능:
    - 인재 검색 및 추천: 벡터 DB에서 이력서를 검색하여 인재 추천
    - 이력서 핵심 요약: 선택된 이력서를 LLM으로 핵심 요약
    """
    
    def get_domain_name(self) -> str:
        return "recruitment"
    
    def get_graph(self) -> StateGraph:
        """채용 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("search_resumes", self.search_resumes)
        workflow.add_node("summarize_resume", self.summarize_resume)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "search": "search_resumes",
                "summarize": "summarize_resume",
            }
        )
        
        workflow.add_edge("search_resumes", END)
        workflow.add_edge("summarize_resume", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """채용 도메인 내부 의도 분류."""
        print("--- [Recruitment Master] Routing Intent ---")
        step_start = time.time()
        query = state.get("query", "")
        session_id = state.get("session_id")
        recruitment_context = state.get("recruitment_context", {})
        
        # 상세 조회 요청인지 확인 (숫자로 시작하거나 "번"이 포함된 경우)
        # 예: "1번 요약", "2번 자세히", "3. 요약해줘"
        is_summarize_request = re.match(r"^\d+\.?\s*", query.strip()) or re.search(r"\d+\s*번", query)
        
        if is_summarize_request:
            # 번호 추출
            selection_index = None
            try:
                # "1번", "2번 요약" 같은 패턴에서 숫자 추출
                digits = re.findall(r"\d+", query)
                if digits:
                    selection_index = int(digits[0])
            except Exception:
                selection_index = None
            
            # 이전 검색 결과가 있으면 summarize로 분류
            resume_search = recruitment_context.get("resume_search", {})
            last_results = resume_search.get("last_results", [])
            
            if selection_index and last_results:
                step_elapsed = (time.time() - step_start) * 1000
                print(f"✅ 이력서 상세 요약 요청 감지: '{query}' - 선택된 인재 번호: {selection_index} ({step_elapsed/1000:.2f}초)")
                return {
                    "intent": "summarize",
                    "selected_resume_index": selection_index,
                }
        
        # 일반 검색 요청
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="recruitment",
            fallback_route="search",
            session_id=session_id
        )
        
        # llm_router는 "search" 또는 "summarize"를 반환
        if intent not in ["search", "summarize"]:
            # 키워드 기반 fallback
            if any(k in query for k in ["추천", "검색", "찾아", "인재", "이력서"]):
                intent = "search"
            else:
                intent = "search"  # 기본값
        
        step_elapsed = (time.time() - step_start) * 1000
        print(f"✅ Recruitment 의도 결정: {intent} ({step_elapsed/1000:.2f}초)")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "search")
        return intent
    
    async def search_resumes(self, state: OrchestratorState) -> Dict[str, Any]:
        """이력서 검색 & 인재 추천."""
        print("--- [Recruitment Master] Searching Resumes ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        conversation_history = state.get("conversation_history", [])
        
        # 이전 요청 확인 질문인지 감지
        is_history_check = any(k in query for k in [
            "요청한적", "요청한 적", "요청했어", "요청했었어", "요청한적있어", "요청한 적 있어",
            "한적", "한 적", "했어?", "했었어?", "했나?", "했었나?",
            "추천해달라고 요청", "검색 요청한적", "검색한적", "검색한 적", "검색했어",
            "찾아달라는 요청", "요청했던", "요청했었던"
        ])
        
        if is_history_check:
            print(f"✅ 이전 요청 확인 질문 감지: '{query}' - 대화 히스토리 확인")
            
            # 대화 히스토리에서 이전 인재 추천 요청 확인
            found_previous_request = False
            search_keywords = []
            
            # conversation_history에서 이전 검색 요청 확인
            if conversation_history:
                for msg in conversation_history:
                    content = msg.get("content", "")
                    role = msg.get("role", "")
                    
                    # 사용자 메시지에서 인재 추천 요청 찾기
                    if role == "user" and any(k in content for k in ["인재", "이력서", "지원자", "채용", "추천"]):
                        if any(k in content for k in ["추천", "검색", "찾아줘", "검색해줘", "찾아달라", "검색해달라"]):
                            found_previous_request = True
                            # 키워드 추출 시도
                            keyword_match = re.search(r'([가-힣a-zA-Z]+(?:\s+[가-힣a-zA-Z]+)*)\s*(?:추천|검색|찾아)', content)
                            if keyword_match:
                                search_keywords.append(keyword_match.group(1))
            
            # 대화식 답변 생성
            if found_previous_request:
                if search_keywords:
                    keywords_str = ", ".join(set(search_keywords))
                    response = f"네, 이전에 {keywords_str} 관련 인재를 추천해드린 적이 있습니다."
                else:
                    response = "네, 이전에 인재 추천을 요청해주신 적이 있습니다."
            else:
                response = "아니요, 이전에 인재 추천 요청은 없었습니다. 추천을 원하시면 말씀해주세요."
            
            return {
                "final_response": response,
                "messages": [response],
            }
        
        # 일반 검색 요청 처리
        # 구체적인 조건이 없는 일반적인 요청인지 확인
        query_lower = query.lower().strip()
        query_cleaned = re.sub(r'[^\w\s가-힣]', '', query_lower)  # 특수문자 제거
        
        # 일반적인 요청 패턴 확인 (예: "인재 추천해줘", "인재 찾아줘", "추천해줘" 등)
        general_request_keywords = [
            "인재 추천", "인재 찾아", "인재 검색",
            "인재를 추천", "인재를 찾아", "인재를 검색",
            "인재 추천해", "인재 찾아줘", "인재 검색해",
            "추천해줘", "찾아줘", "검색해줘",
            "추천해달라", "찾아달라", "검색해달라",
            "추천해주세요", "찾아주세요", "검색해주세요",
            "인재 추천해줘", "인재 찾아줘", "인재 검색해줘",
        ]
        
        is_general_request = any(keyword in query_cleaned for keyword in general_request_keywords)
        
        # 구체적인 조건이 있는지 확인
        # 포지션 키워드 확인
        position_keywords_list = [
            "백엔드", "프론트엔드", "풀스택", "데이터", "ai", "데브옵스", "qa", "기획자", "디자이너",
            "전자", "하드웨어", "임베디드", "반도체",
            "backend", "frontend", "fullstack", "devops", "electronics", "hardware", "embedded"
        ]
        has_position = any(keyword in query_lower for keyword in position_keywords_list)
        
        # 기술 스택 키워드 확인 (일부 주요 기술만)
        tech_keywords_list = [
            "python", "java", "javascript", "typescript", "react", "vue", "angular",
            "spring", "django", "flask", "node", "mysql", "postgresql",
            "docker", "kubernetes", "aws", "gcp", "azure"
        ]
        has_tech = any(keyword in query_lower for keyword in tech_keywords_list)
        
        # 경력 관련 키워드 확인
        experience_keywords = ["경력", "년", "year", "신입", "주니어", "시니어", "senior", "junior", "경험"]
        has_experience = any(keyword in query_lower for keyword in experience_keywords)
        
        # 숫자 포함 여부 확인 (경력년수일 가능성)
        has_numbers = bool(re.search(r'\d+', query))
        
        # 구체적인 조건이 있는지 확인
        has_specific_conditions = has_position or has_tech or has_experience or has_numbers
        
        # 일반적인 요청이고 구체적인 조건이 없으면 안내 메시지 반환
        if is_general_request and not has_specific_conditions:
            print(f"✅ 일반적인 요청 감지 (구체 조건 없음): '{query}' - 안내 메시지 반환")
            response = (
                "어떤 인재를 추천 받고 싶은지 말씀해주세요!\n\n"
                "예를 들어:\n"
                "• \"Python 개발자 추천해줘\"\n"
                "• \"데이터 엔지니어 경력 5년 이상 추천해줘\"\n"
                "• \"백엔드 개발자 Java, Spring 경험자 추천해줘\"\n"
                "• \"전자 엔지니어 추천해줘\"\n\n"
                "포지션, 기술 스택, 경력년수 등을 포함해서 요청해주시면 더 정확한 추천을 받으실 수 있습니다."
            )
            return {
                "final_response": response,
                "messages": [response],
            }
        
        # LLM을 사용하여 프롬프트에서 적절한 검색 개수 추출
        print("--- [Recruitment Master] 검색 파라미터 추출 중 ---")
        search_params_prompt = f"""
사용자 요청을 분석하여 적절한 인재 추천 개수를 추출하세요.

사용자 요청: {query}

추출 규칙:
1. 사용자가 명시적으로 인재 수를 요청한 경우 (예: "2명", "3-5명", "한 명", "여러 명", "몇 명") 그 값을 suggested_count에 설정
2. 명시적 요청이 없으면 suggested_count는 None으로 설정

예시:
- "Python 개발자 2명 추천해줘" → suggested_count=2
- "데이터 엔지니어 한 명만" → suggested_count=1
- "백엔드 개발자 여러 명" → suggested_count=None
- "경력 5년 이상인 개발자 3명" → suggested_count=3
"""
        
        try:
            search_params_result = await extract_structured_output(
                query=query,
                model_class=SearchParameters,
                system_prompt=search_params_prompt,
                temperature=0.0
            )
            
            suggested_count = search_params_result.suggested_count
            
            print(f"✅ 검색 파라미터 추출: suggested_count={suggested_count}")
        except Exception as e:
            print(f"⚠️ 검색 파라미터 추출 실패: {e}, 기본값 사용")
            suggested_count = None
        
        # suggested_count가 있으면 그 값을 사용, 없으면 기본값 5 사용
        # 단, suggested_count가 너무 크면 최대 10으로 제한
        if suggested_count:
            search_limit = min(suggested_count, 10)
        else:
            search_limit = 5  # 기본값
        
        # 검색 실행 (더 많은 결과를 가져와서 필터링할 수 있도록 limit을 여유있게 설정)
        search_limit_for_query = max(search_limit * 2, 10)  # 필터링을 위해 여유있게 가져오기
        
        result = await search_resumes(
            query=query,
            limit=search_limit_for_query,  # 필터링을 위해 더 많이 가져오기
            session_id=session_id,
            state=state
        )
        
        # 검색 결과에서 최종 점수 기준 정렬 및 개수 조정
        all_resumes = result.get("resumes", [])
        scores = result.get("scores", [])
        
        # 최종 점수 순으로 정렬 (높은 순)
        sorted_resumes = []
        if all_resumes and scores:
            # final_score가 있으면 그것을 사용, 없으면 score 사용
            resume_score_pairs = []
            for i, resume in enumerate(all_resumes):
                resume_score = resume.get("final_score") or resume.get("score") or (scores[i] if i < len(scores) else 0.0)
                resume_score_pairs.append((resume, resume_score))
            
            # 점수 순으로 정렬 (높은 순)
            sorted_pairs = sorted(resume_score_pairs, key=lambda x: x[1], reverse=True)
            sorted_resumes = [pair[0] for pair in sorted_pairs]
            
            # index 재설정 (모든 인재에 대해)
            for idx, resume in enumerate(sorted_resumes, 1):
                resume["index"] = str(idx)
        elif all_resumes:
            # scores가 없는 경우에도 처리
            sorted_resumes = all_resumes
            for idx, resume in enumerate(sorted_resumes, 1):
                resume["index"] = str(idx)
        
        # 사용자에게 보여줄 결과 (검색된 모든 인재 표시)
        # suggested_count가 있으면 그 개수만큼만, 없으면 모든 인재 표시
        if suggested_count:
            displayed_resumes = sorted_resumes[:suggested_count]
        else:
            # suggested_count가 없으면 검색된 모든 인재 표시
            displayed_resumes = sorted_resumes
        
        # recruitment_context 업데이트 (검색된 모든 인재 저장)
        recruitment_context = state.get("recruitment_context", {}) or {}
        if not isinstance(recruitment_context, dict):
            recruitment_context = {}
        
        recruitment_context["resume_search"] = {
            "last_query": query,
            "last_results": sorted_resumes,  # 검색된 모든 인재 저장 (정렬된 상태)
            "displayed_count": len(displayed_resumes),  # 사용자에게 보여준 개수
            "total_count": len(sorted_resumes),  # 검색된 전체 개수
        }
        
        print(f"✅ recruitment_context 업데이트 완료: {len(sorted_resumes)}개 인재 저장 (요청: {suggested_count}, 표시: {len(displayed_resumes)}개)")
        if sorted_resumes:
            print(f"📋 저장된 인재 샘플: {sorted_resumes[0]}")
        
        return {
            "final_response": result.get("message", "인재 검색 결과를 찾을 수 없습니다."),
            "messages": [result.get("message", "")],
            "search_results": displayed_resumes,  # 검색된 모든 인재 표시 (suggested_count가 있으면 그 개수만큼)
            "recruitment_context": recruitment_context,
        }
    
    async def summarize_resume(self, state: OrchestratorState) -> Dict[str, Any]:
        """이력서 핵심 요약."""
        print("--- [Recruitment Master] Summarizing Resume ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        recruitment_context = state.get("recruitment_context") or {}
        if not isinstance(recruitment_context, dict):
            recruitment_context = {}
        
        # 선택된 인재 번호 가져오기
        selected_resume_index = state.get("selected_resume_index")
        
        if not selected_resume_index:
            # 번호 추출 재시도
            try:
                digits = re.findall(r"\d+", query)
                if digits:
                    selected_resume_index = int(digits[0])
            except Exception:
                pass
        
        if not selected_resume_index:
            return {
                "final_response": "인재 번호를 찾을 수 없습니다. 번호를 입력해주세요 (예: '1번 요약해줘').",
                "messages": ["인재 번호를 찾을 수 없습니다."],
            }
        
        # 이전 검색 결과 확인
        resume_search = recruitment_context.get("resume_search", {})
        last_results = resume_search.get("last_results", [])
        
        print(f"🔍 recruitment_context 확인:")
        print(f"   - recruitment_context 타입: {type(recruitment_context)}")
        print(f"   - recruitment_context 내용: {recruitment_context}")
        print(f"   - resume_search: {resume_search}")
        print(f"   - last_results 개수: {len(last_results) if last_results else 0}")
        
        if not last_results:
            # 디버깅: state 전체 확인
            print(f"⚠️ 검색 결과를 찾을 수 없음. state의 recruitment_context: {state.get('recruitment_context')}")
            return {
                "final_response": "검색 결과를 찾을 수 없습니다. 먼저 인재를 검색해주세요.",
                "messages": ["검색 결과를 찾을 수 없습니다."],
            }
        
        # 요약 실행
        result = await summarize_resume(
            resume_index=selected_resume_index,
            session_id=session_id,
            state=state
        )
        
        if not result.get("ok"):
            return {
                "final_response": result.get("message", "이력서 요약 중 오류가 발생했습니다."),
                "messages": [result.get("message", "")],
            }
        
        # recruitment_context 업데이트
        recruitment_context["resume_summarize"] = {
            "last_resume_id": result.get("summary_data", {}).get("resume_id"),
            "last_summary": result.get("summary_data", {}),
        }
        
        return {
            "final_response": result.get("message", "이력서 요약을 완료했습니다."),
            "messages": [result.get("message", "")],
            "recruitment_context": recruitment_context,
        }


def get_recruitment_master_graph() -> StateGraph:
    """채용 마스터그래프 인스턴스 반환."""
    master = RecruitmentMasterGraph()
    return master.get_graph()

