"""채용 마스터그래프 - 인재 추천 및 이력서 요약."""

from __future__ import annotations

from typing import Any, Dict, Optional, List, Tuple
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
from agents.master_graphs.recruitment.resume_search_agent import (
    search_resumes,
    _extract_position_keywords,
    _extract_skill_keywords,
    _extract_experience_range,
)

# ==================== 검증 및 정규화 함수 ====================

def _normalize_position(position: Optional[str]) -> Optional[str]:
    """포지션 문자열을 정규화합니다.
    
    Args:
        position: 원본 포지션 문자열
        
    Returns:
        정규화된 포지션 문자열
    """
    if not position:
        return None
    
    # 공백 제거 및 소문자 변환
    normalized = position.strip()
    
    # 일반적인 정규화 규칙
    # "백엔드" -> "백엔드 개발자"
    if normalized in ["백엔드", "backend"]:
        return "백엔드 개발자"
    if normalized in ["프론트엔드", "프론트", "frontend"]:
        return "프론트엔드 개발자"
    if normalized in ["풀스택", "fullstack", "full stack"]:
        return "풀스택 개발자"
    if normalized in ["데이터", "data"]:
        return "데이터 엔지니어"
    if normalized in ["ai", "인공지능"]:
        return "AI 엔지니어"
    if normalized in ["ml", "머신러닝"]:
        return "머신러닝 엔지니어"
    if normalized in ["임베디드", "embedded"]:
        return "임베디드 엔지니어"
    if normalized in ["전자", "electronics"]:
        return "전자 엔지니어"
    if normalized in ["반도체", "semiconductor"]:
        return "반도체 엔지니어"
    if normalized in ["qa", "테스트", "test"]:
        return "QA 엔지니어"
    if normalized in ["데브옵스", "devops"]:
        return "데브옵스 엔지니어"
    if normalized in ["기획", "pm", "product"]:
        return "기획자"
    if normalized in ["디자인", "design"]:
        return "디자이너"
    if normalized in ["인사", "hr", "human resources", "human resource"]:
        return "인사 담당자"
    if normalized in ["채용", "recruiter", "recruitment", "talent acquisition"]:
        return "채용 담당자"
    if normalized in ["인사채용", "hr recruitment", "hr and recruitment"]:
        return "인사채용 담당자"
    if normalized in ["경영", "business", "business management"]:
        return "경영 관리자"
    if normalized in ["경영지원", "business support"]:
        return "경영지원 담당자"
    
    return normalized

def _normalize_experience_type(experience_type: Optional[str]) -> Optional[str]:
    """경력 타입을 정규화합니다.
    
    Args:
        experience_type: 원본 경력 타입
        
    Returns:
        정규화된 경력 타입 ('신입' 또는 '경력직')
    """
    if not experience_type:
        return None
    
    normalized = experience_type.strip().lower()
    
    # 신입 관련 키워드
    if any(kw in normalized for kw in ["신입", "junior", "entry", "초급", "주니어"]):
        return "신입"
    
    # 경력직 관련 키워드
    if any(kw in normalized for kw in ["경력", "senior", "experienced", "시니어", "중급", "고급"]):
        return "경력직"
    
    return None

def _validate_and_normalize_tech_stack(tech_stack: Optional[List[str]]) -> List[str]:
    """기술 스택을 검증하고 정규화합니다.
    
    Args:
        tech_stack: 원본 기술 스택 리스트
        
    Returns:
        정규화된 기술 스택 리스트
    """
    if not tech_stack:
        return []
    
    normalized = []
    seen = set()  # 중복 제거
    
    for tech in tech_stack:
        if not tech:
            continue
        
        # 공백 제거 및 정규화
        tech_normalized = tech.strip()
        
        # 빈 문자열 제외
        if not tech_normalized:
            continue
        
        # 중복 제거 (대소문자 무시)
        tech_lower = tech_normalized.lower()
        if tech_lower in seen:
            continue
        seen.add(tech_lower)
        
        # 일반적인 정규화 규칙
        # "React.js" -> "React"
        if tech_lower in ["react.js", "reactjs"]:
            tech_normalized = "React"
        elif tech_lower in ["vue.js", "vuejs"]:
            tech_normalized = "Vue.js"
        elif tech_lower in ["node.js", "nodejs"]:
            tech_normalized = "Node.js"
        elif tech_lower in ["next.js", "nextjs"]:
            tech_normalized = "Next.js"
        elif tech_lower in ["nuxt.js", "nuxtjs"]:
            tech_normalized = "Nuxt.js"
        elif tech_lower == "js":
            tech_normalized = "JavaScript"
        elif tech_lower == "ts":
            tech_normalized = "TypeScript"
        elif tech_lower in ["py", "python3"]:
            tech_normalized = "Python"
        elif tech_lower == "csharp":
            tech_normalized = "C#"
        elif tech_lower == "cpp":
            tech_normalized = "C++"
        
        normalized.append(tech_normalized)
    
    return normalized

def _validate_experience_years(years: Optional[float]) -> Optional[float]:
    """경력년수를 검증합니다.
    
    Args:
        years: 경력년수
        
    Returns:
        검증된 경력년수 (0.5 이상 50 이하)
    """
    if years is None:
        return None
    
    # 범위 검증 (0.5년 ~ 50년)
    if years < 0.5:
        return None
    if years > 50:
        return 50.0
    
    # 소수점 첫째 자리까지 반올림
    return round(years, 1)
from agents.master_graphs.recruitment.resume_summarize_agent import summarize_resume
from pydantic import BaseModel, Field

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


class PositionInfo(BaseModel):
    """포지션 정보 추출 결과."""
    position: Optional[str] = Field(
        default=None,
        description=(
            "사용자가 요청한 포지션. "
            "예: '개발자', '백엔드 개발자', '프론트엔드', '데이터 엔지니어', '전자 엔지니어' 등. "
            "명시적으로 언급되지 않았으면 None."
        )
    )
    confidence: float = Field(
        default=0.0,
        description="추출 신뢰도 (0.0 ~ 1.0)",
        ge=0.0,
        le=1.0
    )


class ExperienceInfo(BaseModel):
    """경력 정보 추출 결과."""
    experience_type: Optional[str] = Field(
        default=None,
        description=(
            "경력 타입: '신입' 또는 '경력직'. "
            "예: '신입', '주니어', 'entry level' -> '신입' "
            "'경력', 'senior', 'experienced' -> '경력직'"
        )
    )
    experience_years: Optional[float] = Field(
        default=None,
        description=(
            "경력년수. "
            "예: '5년', '3-5년', '5년 이상' -> 5.0 "
            "신입이거나 명시되지 않았으면 None."
        ),
        ge=0.0
    )
    confidence: float = Field(
        default=0.0,
        description="추출 신뢰도 (0.0 ~ 1.0)",
        ge=0.0,
        le=1.0
    )


class TechStackInfo(BaseModel):
    """기술 스택 정보 추출 결과."""
    tech_stack: List[str] = Field(
        default_factory=list,
        description=(
            "사용자가 요청한 기술 스택 리스트. "
            "예: 'React, Node.js, AWS' -> ['React', 'Node.js', 'AWS'] "
            "기술 스택이 없거나 '없어', '필요없어' 같은 답변이면 빈 리스트."
        )
    )
    skip_requested: bool = Field(
        default=False,
        description=(
            "사용자가 기술 스택을 건너뛰기를 요청했는지 여부. "
            "예: '없어', '필요없어', 'skip' 등 -> True"
        )
    )
    confidence: float = Field(
        default=0.0,
        description="추출 신뢰도 (0.0 ~ 1.0)",
        ge=0.0,
        le=1.0
    )


class RecruitmentSearchInfo(BaseModel):
    """인재 검색에 필요한 모든 정보를 한 번에 추출."""
    position: Optional[str] = Field(
        default=None,
        description="포지션 (예: '개발자', '백엔드 개발자', '데이터 엔지니어')"
    )
    experience_type: Optional[str] = Field(
        default=None,
        description="경력 타입: '신입' 또는 '경력직'"
    )
    experience_years: Optional[float] = Field(
        default=None,
        description="경력년수 (예: 3.0, 5.0)",
        ge=0.0
    )
    tech_stack: Optional[List[str]] = Field(
        default_factory=list,
        description="기술 스택 리스트 (예: ['React', 'Node.js', 'AWS']). 없으면 빈 리스트 또는 null"
    )
    missing_fields: List[str] = Field(
        default_factory=list,
        description=(
            "누락된 필드 목록. "
            "가능한 값: 'position', 'experience', 'tech_stack'. "
            "예: position이 없으면 ['position'], experience가 없으면 ['experience']"
        )
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
        workflow.add_node("check_search_parameters", self.check_search_parameters)
        workflow.add_node("collect_position_info", self.collect_position_info)
        workflow.add_node("collect_experience_info", self.collect_experience_info)
        workflow.add_node("collect_tech_stack_info", self.collect_tech_stack_info)
        workflow.add_node("search_resumes", self.search_resumes)
        workflow.add_node("summarize_resume", self.summarize_resume)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # route_intent -> check_search_parameters 또는 직접 search/summarize
        workflow.add_conditional_edges(
            "route_intent",
            self.route_after_intent,
            {
                "check_params": "check_search_parameters",
                "search": "search_resumes",
                "summarize": "summarize_resume",
            }
        )
        
        # check_search_parameters -> 정보 수집 노드들 또는 search_resumes
        workflow.add_conditional_edges(
            "check_search_parameters",
            self.route_collection_step,
            {
                "position": "collect_position_info",
                "experience": "collect_experience_info",
                "tech_stack": "collect_tech_stack_info",
                "complete": "search_resumes",
            }
        )
        
        # 정보 수집 노드들 -> check_search_parameters (다음 단계 확인) 또는 END (사용자 입력 대기)
        workflow.add_conditional_edges(
            "collect_position_info",
            self.route_after_collection,
            {
                "continue": "check_search_parameters",
                "wait_input": END,
            }
        )
        
        workflow.add_conditional_edges(
            "collect_experience_info",
            self.route_after_collection,
            {
                "continue": "check_search_parameters",
                "wait_input": END,
            }
        )
        
        workflow.add_conditional_edges(
            "collect_tech_stack_info",
            self.route_after_collection_tech_stack,
            {
                "search": "search_resumes",
                "continue": "check_search_parameters",
                "wait_input": END,
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
        
        # 휴먼인더루프 진행 중인지 확인 (이전 대화에서 정보 수집 중이면 워크플로우 아님)
        is_hitl_in_progress = False
        if recruitment_context:
            search_params = recruitment_context.get("search_parameters", {})
            collection_step = search_params.get("collection_step")
            if collection_step and collection_step != "complete":
                is_hitl_in_progress = True
                print(f"✅ 휴먼인더루프 진행 중 감지: collection_step={collection_step}")
        
        # 휴먼인더루프 진행 중이면 무조건 워크플로우 차단
        if is_hitl_in_progress:
            return {
                "intent": intent,
                "workflow_in_progress": False,  # 명시적으로 워크플로우 차단
                "workflow_type": None,  # 워크플로우 타입 제거
                "workflow_steps": None,  # 워크플로우 단계 제거
            }
        
        # 워크플로우 요청인지 확인 (LLM 기반으로 더 정확하게)
        # 단순 검색 요청: "개발자 추천해줘", "인재 찾아줘" 등
        # 워크플로우 요청: "이력서 검토하고 면접 일정 잡아줘", "지원자 검토하고 문서 작성해줘" 등
        workflow_keywords = [
            "검토하고", "검토 후", "검토한 후", "검토해서",
            "면접 일정", "면접 스케줄", "면접 잡아", "면접 예약",
            "문서 작성", "문서 생성", "보고서 작성",
            "이메일 보내", "이메일 발송", "메일 보내"
        ]
        
        # 키워드 기반 1차 체크
        has_workflow_keyword = any(kw in query for kw in workflow_keywords)
        
        # 단순 검색 키워드가 있으면 워크플로우가 아닐 가능성이 높음
        simple_search_keywords = [
            "추천", "검색", "찾아", "인재", "이력서", "지원자"
        ]
        has_simple_search_keyword = any(kw in query for kw in simple_search_keywords)
        
        # 워크플로우 키워드가 없거나, 단순 검색 키워드만 있으면 워크플로우 아님
        if not has_workflow_keyword or (has_simple_search_keyword and not has_workflow_keyword):
            return {
                "intent": intent,
                "workflow_in_progress": False,  # 명시적으로 워크플로우 차단
                "workflow_type": None,  # 워크플로우 타입 제거
                "workflow_steps": None,  # 워크플로우 단계 제거
            }
        
        # 워크플로우 키워드가 있는 경우에도, 오케스트레이터 레벨에서 워크플로우 감지하도록 함
        # 여기서는 명시적으로 차단하지 않고 오케스트레이터에 맡김
        return {"intent": intent}
    
    async def check_search_parameters(self, state: OrchestratorState) -> Dict[str, Any]:
        """검색 파라미터 확인 및 다음 단계 결정."""
        print("--- [Recruitment Master] Checking Search Parameters ---")
        query = state.get("query", "")
        recruitment_context = state.get("recruitment_context", {}) or {}
        conversation_history = state.get("conversation_history", [])
        
        # search_parameters 초기화
        if "search_parameters" not in recruitment_context:
            recruitment_context["search_parameters"] = {}
        
        search_params = recruitment_context.get("search_parameters", {})
        collection_step = search_params.get("collection_step")
        
        # collection_step이 "complete"이면 이미 모든 정보가 수집된 상태
        # 이 경우 check_search_parameters를 건너뛰고 바로 search_resumes로 가야 함
        # 하지만 route_collection_step에서 처리하므로 여기서는 그대로 진행
        
        # 이전 검색이 완료되었고 새로운 검색 요청이면 초기화
        # 단, 휴먼인더루프 진행 중이 아닐 때만 초기화 (무한 루프 방지)
        last_search_completed = recruitment_context.get("resume_search", {}).get("last_results")
        is_hitl_in_progress = collection_step and collection_step != "complete"
        
        if last_search_completed and collection_step == "complete" and not is_hitl_in_progress:
            # 새로운 검색 요청인지 확인 (이전 쿼리와 다른 경우, 요약 요청이 아닌 경우)
            last_query = recruitment_context.get("resume_search", {}).get("last_query", "")
            is_summarize_request = re.match(r"^\d+\.?\s*", query.strip()) or re.search(r"\d+\s*번", query)
            
            if query != last_query and not is_summarize_request:
                print("🔄 새로운 검색 요청 감지 - search_parameters 초기화")
                search_params = {}
                recruitment_context["search_parameters"] = {}
                # 초기화 후 search_params 재할당
                search_params = recruitment_context["search_parameters"]
                collection_step = None  # 초기화되었으므로 collection_step도 초기화
        
        # 이전 대화에서 수집된 정보 확인 (자연스러운 대화 흐름 지원)
        # 사용자가 연속으로 정보를 제공할 때 이전 정보를 유지하면서 새 정보만 추가
        previous_query = ""
        if conversation_history:
            # 최근 사용자 메시지들 확인 (최대 3개)
            recent_user_messages = [
                msg.get("content", "") 
                for msg in conversation_history[-6:]  # 최근 6개 메시지 (사용자+어시스턴트 3턴)
                if msg.get("role") == "user"
            ]
            if recent_user_messages:
                previous_query = " ".join(recent_user_messages[:-1])  # 현재 쿼리 제외한 이전 쿼리들
        
        # 전체 컨텍스트 구성 (이전 대화 + 현재 쿼리)
        full_context = f"{previous_query} {query}".strip() if previous_query else query
        
        # LLM으로 한 번에 모든 정보 추출 및 누락 정보 분석
        try:
            # 이전에 수집된 정보가 있으면 컨텍스트에 포함
            existing_info = []
            if search_params.get("position"):
                existing_info.append(f"이미 수집된 포지션: {search_params.get('position')}")
            if search_params.get("experience_type"):
                existing_info.append(f"이미 수집된 경력 타입: {search_params.get('experience_type')}")
            if search_params.get("experience_years"):
                existing_info.append(f"이미 수집된 경력년수: {search_params.get('experience_years')}년")
            if search_params.get("tech_stack"):
                existing_info.append(f"이미 수집된 기술 스택: {', '.join(search_params.get('tech_stack', [])[:3])}")
            
            context_info = "\n".join(existing_info) if existing_info else "없음"
            
            system_prompt = f"""
사용자 입력에서 인재 검색에 필요한 모든 정보를 추출하고, 누락된 정보를 분석하세요.

**이미 수집된 정보:**
{context_info}

**필요한 정보:**
1. position: 포지션 (예: '개발자', '백엔드 개발자', '프론트엔드 개발자', '풀스택 개발자', '데이터 엔지니어', 'AI 엔지니어', '전자 엔지니어', '임베디드 엔지니어', '반도체 엔지니어', '기획자', '디자이너', 'QA 엔지니어', '데브옵스 엔지니어', '인사 담당자', '채용 담당자', '인사채용 담당자', '경영 관리자', '경영지원 담당자')
   - 다양한 표현 인식: "개발자", "백엔드", "프론트엔드", "풀스택", "데이터 엔지니어", "AI 엔지니어", "ML 엔지니어", "전자 엔지니어", "임베디드", "반도체", "기획자", "디자이너", "QA", "테스터", "데브옵스", "인사", "채용", "인사채용", "경영", "경영지원", "HR", "recruiter"
   - 복합 표현: "백엔드 개발자", "프론트엔드 개발자", "풀스택 개발자", "인사 담당자", "채용 담당자", "경영 관리자" 등
   - 약어: "백엔드" → "백엔드 개발자", "프론트" → "프론트엔드 개발자", "데이터" → "데이터 엔지니어", "인사" → "인사 담당자", "채용" → "채용 담당자", "경영" → "경영 관리자", "HR" → "인사 담당자"
2. experience_type: 경력 타입 ('신입' 또는 '경력직')
   - 신입: "신입", "주니어", "junior", "entry level", "신입 개발자", "주니어 개발자", "초급", "entry", "신입급"
   - 경력직: "경력", "시니어", "senior", "experienced", "경력직", "시니어 개발자", "중급", "고급", "경력자", "경력 있는"
3. experience_years: 경력년수 (숫자, 0.5 이상 50 이하)
   - 다양한 표현: "3년", "3-5년", "5년 이상", "약 5년", "5년차", "3년 이상", "최소 3년", "3년 이상 경력"
   - 범위 표현: "3-5년" → 3.0 (최소값 사용), "3~5년" → 3.0
   - 신입이면 None
4. tech_stack: 기술 스택 리스트 (중복 제거, 정규화)
   - 프론트엔드: React, Vue.js, Angular, Next.js, Nuxt.js, Svelte, TypeScript, JavaScript, HTML, CSS, SCSS, Sass, Tailwind CSS, Bootstrap, Material-UI, Ant Design
   - 백엔드: Node.js, Express, NestJS, Python, Django, Flask, FastAPI, Java, Spring, Spring Boot, Kotlin, C#, .NET, ASP.NET, Go, Rust, Ruby, Ruby on Rails, PHP, Laravel
   - 모바일: React Native, Flutter, Swift, Kotlin, iOS, Android, Xamarin, Ionic
   - 데이터베이스: MySQL, PostgreSQL, MongoDB, Redis, Oracle, SQL Server, MariaDB, DynamoDB, Cassandra, Elasticsearch
   - 클라우드/인프라: AWS, GCP, Azure, Docker, Kubernetes, Terraform, Ansible, Jenkins, GitLab CI/CD, GitHub Actions, CircleCI, Travis CI
   - AI/ML: Python, TensorFlow, PyTorch, Keras, Scikit-learn, Pandas, NumPy, Jupyter, R, MATLAB
   - 빅데이터: Hadoop, Spark, Kafka, Airflow, Presto, Hive, HBase
   - 모니터링/로깅: Prometheus, Grafana, ELK Stack, Kibana, Splunk, Datadog, New Relic
   - 하드웨어/임베디드: C, C++, ARM, STM32, Arduino, Raspberry Pi, Verilog, VHDL, PCB Design, Altium, Cadence, OrCAD, KiCad, Eagle, LabVIEW
   - 기타: Git, SVN, Jira, Confluence, Figma, Sketch, Adobe XD, Postman, Swagger
   - 약어 정규화: "React.js" → "React", "Vue.js" → "Vue.js", "Node.js" → "Node.js", "JS" → "JavaScript", "TS" → "TypeScript", "Py" → "Python"

**누락된 정보 분석 (우선순위: position > experience > tech_stack):**
- position이 없으면 missing_fields에 'position' 추가 (최우선)
- experience_type과 experience_years가 모두 없으면 missing_fields에 'experience' 추가
- tech_stack이 없고 사용자가 명시적으로 건너뛰지 않았으면 missing_fields에 'tech_stack' 추가

**추출 규칙:**
1. 포지션 추출:
   - 명시적 언급: "개발자", "백엔드 개발자", "인사 담당자", "채용 담당자", "경영 관리자" 등 → 그대로 추출
   - 암시적 언급: "백엔드" → "백엔드 개발자", "프론트" → "프론트엔드 개발자", "인사" → "인사 담당자", "채용" → "채용 담당자", "경영" → "경영 관리자", "HR" → "인사 담당자"
   - 맥락 기반: "데이터 분석할 수 있는" → "데이터 엔지니어", "인사 업무" → "인사 담당자", "채용 업무" → "채용 담당자", "경영 관리" → "경영 관리자"
2. 경력 정보 추출:
   - 신입: "신입", "주니어", "junior", "entry", "초급" → "신입"
   - 경력직: "경력", "시니어", "senior", "experienced", "중급", "고급" → "경력직"
   - 경력년수: "3년", "3-5년", "5년 이상", "5년차", "최소 3년" → 숫자 추출
3. 기술 스택 추출:
   - 직접 언급: "React", "Node.js", "Python" → 리스트에 추가
   - 동사 패턴: "React 사용하는", "Python 경험 있는", "Java 할 수 있는" → 기술명 추출
   - 필요 패턴: "React 필요", "Node.js 요구", "Python 필수" → 기술명 추출
   - 건너뛰기: "없어", "필요없어", "없음", "skip", "건너뛰" → skip_requested=True

**예시:**
- "인재 추천해줘" → position=None, missing_fields=["position", "experience", "tech_stack"]
- "개발자 추천해줘" → position="개발자", missing_fields=["experience", "tech_stack"]
- "백엔드 개발자 경력 3년" → position="백엔드 개발자", experience_type="경력직", experience_years=3.0, missing_fields=["tech_stack"]
- "React, Node.js 사용하는 개발자" → position="개발자", tech_stack=["React", "Node.js"], missing_fields=["experience"]
- "신입 개발자" → position="개발자", experience_type="신입", missing_fields=["tech_stack"]
- "경력 5년 이상 백엔드 개발자 Python, Django 경험" → position="백엔드 개발자", experience_type="경력직", experience_years=5.0, tech_stack=["Python", "Django"], missing_fields=[]
- "전자 엔지니어 3-5년 경력 ARM, STM32 경험" → position="전자 엔지니어", experience_type="경력직", experience_years=3.0, tech_stack=["ARM", "STM32"], missing_fields=[]
- "AI 엔지니어 시니어 Python, TensorFlow, PyTorch" → position="AI 엔지니어", experience_type="경력직", tech_stack=["Python", "TensorFlow", "PyTorch"], missing_fields=["experience_years"]
- "백엔드" → position="백엔드 개발자", missing_fields=["experience", "tech_stack"]
- "인사" → position="인사 담당자", missing_fields=["experience", "tech_stack"]
- "채용" → position="채용 담당자", missing_fields=["experience", "tech_stack"]
- "경영" → position="경영 관리자", missing_fields=["experience", "tech_stack"]
- "HR" → position="인사 담당자", missing_fields=["experience", "tech_stack"]
- "React.js, Node.js" → tech_stack=["React", "Node.js"], missing_fields=["position", "experience"]

**중요:**
1. 포지션이 명시되지 않은 경우 (예: "인재 추천해줘", "추천해줘", "인재 찾아줘") 반드시 missing_fields에 "position"을 포함해야 합니다.
2. 이미 수집된 정보가 있으면 그것을 활용하고, 새로운 정보만 추출하세요.
3. 기술 스택은 중복을 제거하고 정규화된 형태로 반환하세요.
4. 경력년수는 0.5 이상 50 이하의 값만 유효합니다.
"""
            
            search_info = await extract_structured_output(
                query=full_context,
                model_class=RecruitmentSearchInfo,
                system_prompt=system_prompt,
                temperature=0.0
            )
            
            # 추출된 정보를 검증 및 정규화하여 search_params 업데이트
            if search_info.position:
                normalized_position = _normalize_position(search_info.position)
                if normalized_position:
                    search_params["position"] = normalized_position
                    if normalized_position != search_info.position:
                        print(f"✅ LLM 포지션 추출 및 정규화: {search_info.position} → {normalized_position}")
                    else:
                        print(f"✅ LLM 포지션 추출: {normalized_position}")
            
            if search_info.experience_type:
                normalized_experience_type = _normalize_experience_type(search_info.experience_type)
                if normalized_experience_type:
                    search_params["experience_type"] = normalized_experience_type
                    if normalized_experience_type != search_info.experience_type:
                        print(f"✅ LLM 경력 타입 추출 및 정규화: {search_info.experience_type} → {normalized_experience_type}")
                    else:
                        print(f"✅ LLM 경력 타입 추출: {normalized_experience_type}")
            
            if search_info.experience_years:
                validated_years = _validate_experience_years(search_info.experience_years)
                if validated_years:
                    search_params["experience_years"] = validated_years
                    if validated_years != search_info.experience_years:
                        print(f"✅ LLM 경력년수 추출 및 검증: {search_info.experience_years} → {validated_years}")
                    else:
                        print(f"✅ LLM 경력년수 추출: {validated_years}")
            
            if search_info.tech_stack:
                normalized_tech_stack = _validate_and_normalize_tech_stack(search_info.tech_stack)
                if normalized_tech_stack:
                    search_params["tech_stack"] = normalized_tech_stack
                    if set(normalized_tech_stack) != set(search_info.tech_stack):
                        print(f"✅ LLM 기술 스택 추출 및 정규화: {search_info.tech_stack} → {normalized_tech_stack}")
                    else:
                        print(f"✅ LLM 기술 스택 추출: {normalized_tech_stack}")
            elif search_info.tech_stack is None:
                # tech_stack이 None이면 빈 리스트로 설정
                search_params["tech_stack"] = []
                print(f"✅ LLM 기술 스택: 없음 (빈 리스트로 설정)")
            
            # 누락된 정보 확인 및 collection_step 설정
            # position이 없으면 무조건 position부터 시작 (LLM이 missing_fields에 포함하지 않았어도)
            if not search_params.get("position") and not search_info.position:
                # LLM이 포지션을 추출하지 못했고, search_params에도 없으면
                # missing_fields에 "position"이 없어도 강제로 추가
                if "position" not in search_info.missing_fields:
                    search_info.missing_fields.insert(0, "position")  # 맨 앞에 추가 (우선순위)
                    print("⚠️ 포지션이 없는데 missing_fields에 없어서 강제로 추가")
            
            # 누락된 정보가 있으면 사용자에게 안내
            if search_info.missing_fields:
                missing_messages = []
                if "position" in search_info.missing_fields:
                    missing_messages.append("포지션")
                if "experience" in search_info.missing_fields:
                    missing_messages.append("경력 정보 (신입/경력직 및 경력년수)")
                if "tech_stack" in search_info.missing_fields:
                    missing_messages.append("기술 스택")
                
                missing_str = ", ".join(missing_messages)
                print(f"ℹ️ 누락된 정보: {missing_str}")
                
                # 다음 단계 결정 (우선순위: position > experience > tech_stack)
                if "position" in search_info.missing_fields:
                    search_params["collection_step"] = "position"
                    print(f"✅ collection_step 설정: position (포지션 수집 필요)")
                elif "experience" in search_info.missing_fields:
                    search_params["collection_step"] = "experience"
                    print(f"✅ collection_step 설정: experience (경력 정보 수집 필요)")
                elif "tech_stack" in search_info.missing_fields:
                    search_params["collection_step"] = "tech_stack"
                    print(f"✅ collection_step 설정: tech_stack (기술 스택 수집 필요)")
                else:
                    search_params["collection_step"] = "complete"
                    print(f"✅ collection_step 설정: complete")
            else:
                # 모든 정보가 있으면 완료
                search_params["collection_step"] = "complete"
                print("✅ 모든 정보 추출 완료")
                
        except Exception as e:
            print(f"⚠️ LLM 기반 정보 추출 실패: {e}, 기존 로직 사용")
            # 기존 로직으로 fallback (검증 및 정규화 포함)
            position_updated = False
            if not search_params.get("position"):
                position = await self._extract_position_with_llm(full_context)
                if position:
                    normalized_position = _normalize_position(position)
                    if normalized_position:
                        search_params["position"] = normalized_position
                        position_updated = True
                        if normalized_position != position:
                            print(f"✅ 포지션 추출 및 정규화: {position} → {normalized_position}")
                        else:
                            print(f"✅ 포지션 추출: {normalized_position}")
            
            experience_updated = False
            if not search_params.get("experience_type") and not search_params.get("experience_years"):
                experience_type, experience_years = await self._extract_experience_with_llm(full_context)
                if experience_type:
                    normalized_experience_type = _normalize_experience_type(experience_type)
                    if normalized_experience_type:
                        search_params["experience_type"] = normalized_experience_type
                        experience_updated = True
                        if normalized_experience_type != experience_type:
                            print(f"✅ 경력 타입 추출 및 정규화: {experience_type} → {normalized_experience_type}")
                        else:
                            print(f"✅ 경력 타입 추출: {normalized_experience_type}")
                if experience_years:
                    validated_years = _validate_experience_years(experience_years)
                    if validated_years:
                        search_params["experience_years"] = validated_years
                        experience_updated = True
                        if validated_years != experience_years:
                            print(f"✅ 경력년수 추출 및 검증: {experience_years} → {validated_years}")
                        else:
                            print(f"✅ 경력년수 추출: {validated_years}")
            
            tech_stack_updated = False
            if not search_params.get("tech_stack"):
                tech_stack, skip_requested = await self._extract_tech_stack_with_llm(full_context)
                if tech_stack:
                    normalized_tech_stack = _validate_and_normalize_tech_stack(tech_stack)
                    if normalized_tech_stack:
                        search_params["tech_stack"] = normalized_tech_stack
                        tech_stack_updated = True
                        if set(normalized_tech_stack) != set(tech_stack):
                            print(f"✅ 기술 스택 추출 및 정규화: {tech_stack} → {normalized_tech_stack}")
                        else:
                            print(f"✅ 기술 스택 추출: {normalized_tech_stack}")
                if skip_requested:
                    search_params["tech_stack"] = []
                    tech_stack_updated = True
                    print(f"✅ 기술 스택 건너뛰기 요청 확인")
            
            # 다음 단계 결정 (우선순위: position > experience > tech_stack)
            # collection_step이 이미 "complete"이면 유지 (무한 루프 방지)
            current_collection_step = search_params.get("collection_step")
            if current_collection_step == "complete":
                print("ℹ️ collection_step이 이미 'complete'이므로 유지 (무한 루프 방지)")
            elif not search_params.get("position"):
                search_params["collection_step"] = "position"
                print(f"✅ collection_step 설정: position (포지션 수집 필요)")
            elif not search_params.get("experience_type") and not search_params.get("experience_years"):
                search_params["collection_step"] = "experience"
                print(f"✅ collection_step 설정: experience (경력 정보 수집 필요)")
            elif not search_params.get("tech_stack"):
                search_params["collection_step"] = "tech_stack"
                print(f"✅ collection_step 설정: tech_stack (기술 스택 수집 필요)")
            else:
                search_params["collection_step"] = "complete"
                print(f"✅ collection_step 설정: complete")
        
        recruitment_context["search_parameters"] = search_params
        
        # 단순 검색 요청임을 명시 (워크플로우 차단 - 일반 요청으로 분류)
        return {
            "recruitment_context": recruitment_context,
            "workflow_in_progress": False,  # 명시적으로 워크플로우 차단
            "workflow_type": None,  # 워크플로우 타입 제거
            "workflow_steps": None,  # 워크플로우 단계 제거
        }
    
    async def collect_position_info(self, state: OrchestratorState) -> Dict[str, Any]:
        """포지션 정보 수집 (휴먼인더루프 1단계)."""
        print("--- [Recruitment Master] Collecting Position Info ---")
        query = state.get("query", "")
        recruitment_context = state.get("recruitment_context", {}) or {}
        conversation_history = state.get("conversation_history", [])
        search_params = recruitment_context.get("search_parameters", {})
        
        # 이전 대화 컨텍스트 확인
        previous_context = ""
        if conversation_history:
            recent_messages = [
                msg.get("content", "") 
                for msg in conversation_history[-4:]  # 최근 2턴
                if msg.get("role") == "user"
            ]
            if recent_messages:
                previous_context = " ".join(recent_messages[:-1]) if len(recent_messages) > 1 else ""
        
        # 전체 컨텍스트로 포지션 추출 시도
        full_context = f"{previous_context} {query}".strip() if previous_context else query
        position = await self._extract_position_with_llm(full_context)
        
        if position:
            # 포지션을 정규화하여 저장
            normalized_position = _normalize_position(position)
            if normalized_position:
                search_params["position"] = normalized_position
                search_params["collection_step"] = "experience"
                recruitment_context["search_parameters"] = search_params
                
                # 자연스러운 AI 답변 형식
                response = f"네, '{normalized_position}' 포지션으로 인재를 찾아드리겠습니다.\n\n다음으로 경력 정보를 알려주시면 더 정확한 추천을 드릴 수 있습니다. 신입을 찾으시나요, 아니면 경력직을 찾으시나요? 경력직이시라면 원하시는 경력 년수도 함께 알려주세요. (예: 신입, 경력 3년, 5년 이상 등)"
                
                return {
                    "recruitment_context": recruitment_context,
                    "requires_input": False,
                    "final_response": response,
                    "messages": [response],
                    "workflow_in_progress": False,  # 워크플로우 차단
                    "workflow_type": None,
                    "workflow_steps": None,
                }
        
        # 포지션을 찾지 못했으면 사용자에게 질문
        response = "안녕하세요! 인재 추천을 도와드리겠습니다.\n\n먼저 어떤 포지션의 지원자를 찾으시나요? (예: 개발자, 백엔드 개발자, 데이터 엔지니어, 전자 엔지니어 등)"
        return {
            "recruitment_context": recruitment_context,
            "requires_input": True,
            "final_response": response,
            "messages": [response],
            "workflow_in_progress": False,  # 워크플로우 차단
            "workflow_type": None,
            "workflow_steps": None,
        }
    
    async def collect_experience_info(self, state: OrchestratorState) -> Dict[str, Any]:
        """경력 정보 수집 (휴먼인더루프 2단계)."""
        print("--- [Recruitment Master] Collecting Experience Info ---")
        query = state.get("query", "")
        recruitment_context = state.get("recruitment_context", {}) or {}
        conversation_history = state.get("conversation_history", [])
        search_params = recruitment_context.get("search_parameters", {})
        
        # 이전 대화 컨텍스트 확인 (포지션 정보 포함)
        previous_context = ""
        if conversation_history:
            recent_messages = [
                msg.get("content", "") 
                for msg in conversation_history[-4:]  # 최근 2턴
                if msg.get("role") == "user"
            ]
            if recent_messages:
                previous_context = " ".join(recent_messages[:-1]) if len(recent_messages) > 1 else ""
        
        # 이미 수집된 포지션 정보도 컨텍스트에 포함
        if search_params.get("position"):
            previous_context = f"{previous_context} {search_params.get('position')}".strip()
        
        # 전체 컨텍스트로 경력 정보 추출
        full_context = f"{previous_context} {query}".strip() if previous_context else query
        experience_type, experience_years = await self._extract_experience_with_llm(full_context)
        
        # 경력 정보 정규화 및 검증
        normalized_experience_type = None
        validated_years = None
        
        if experience_type:
            normalized_experience_type = _normalize_experience_type(experience_type)
        if experience_years:
            validated_years = _validate_experience_years(experience_years)
        
        if normalized_experience_type or validated_years:
            if normalized_experience_type:
                search_params["experience_type"] = normalized_experience_type
            if validated_years:
                search_params["experience_years"] = validated_years
            search_params["collection_step"] = "tech_stack"
            recruitment_context["search_parameters"] = search_params
            
            # 수집된 정보 요약
            position_info = search_params.get("position", "포지션")
            exp_info = []
            if normalized_experience_type:
                exp_info.append(normalized_experience_type)
            if validated_years:
                exp_info.append(f"{validated_years}년")
            
            exp_str = " ".join(exp_info) if exp_info else "경력 정보"
            
            # 자연스러운 AI 답변 형식
            response = f"네, {exp_str} 확인했습니다.\n\n**현재까지 수집된 정보:**\n- 포지션: {position_info}\n- 경력: {exp_str}\n\n마지막으로, 이 포지션에 필요한 기술 스택이 있다면 알려주세요. 없으시면 '없어' 또는 '필요없어'라고 답변해주시면 됩니다. (예: React, Node.js, Python, Java 등)"
            
            return {
                "recruitment_context": recruitment_context,
                "requires_input": False,
                "final_response": response,
                "messages": [response],
                "workflow_in_progress": False,  # 워크플로우 차단
                "workflow_type": None,
                "workflow_steps": None,
            }
        else:
            # 포지션 정보가 있으면 포함
            position_info = ""
            if search_params.get("position"):
                position_info = f"'{search_params.get('position')}' 포지션에 대해 "
            
            response = f"{position_info}경력 정보를 알려주세요. 신입을 찾으시나요, 아니면 경력직을 찾으시나요? 경력직이시라면 원하시는 경력 년수도 함께 알려주세요. (예: 신입, 경력 3년, 5년 이상 등)"
            return {
                "recruitment_context": recruitment_context,
                "requires_input": True,
                "final_response": response,
                "messages": [response],
                "workflow_in_progress": False,  # 워크플로우 차단
                "workflow_type": None,
                "workflow_steps": None,
            }
    
    async def collect_tech_stack_info(self, state: OrchestratorState) -> Dict[str, Any]:
        """기술 스택 정보 수집 (휴먼인더루프 3단계)."""
        print("--- [Recruitment Master] Collecting Tech Stack Info ---")
        query = state.get("query", "")
        recruitment_context = state.get("recruitment_context", {}) or {}
        conversation_history = state.get("conversation_history", [])
        search_params = recruitment_context.get("search_parameters", {})
        
        # 이전 대화 컨텍스트 확인 (포지션, 경력 정보 포함)
        previous_context = ""
        if conversation_history:
            recent_messages = [
                msg.get("content", "") 
                for msg in conversation_history[-6:]  # 최근 3턴
                if msg.get("role") == "user"
            ]
            if recent_messages:
                previous_context = " ".join(recent_messages[:-1]) if len(recent_messages) > 1 else ""
        
        # 이미 수집된 정보도 컨텍스트에 포함
        context_parts = []
        if search_params.get("position"):
            context_parts.append(search_params.get("position"))
        if search_params.get("experience_type"):
            context_parts.append(search_params.get("experience_type"))
        if search_params.get("experience_years"):
            context_parts.append(f"{search_params.get('experience_years')}년")
        if context_parts:
            previous_context = f"{previous_context} {' '.join(context_parts)}".strip()
        
        # 전체 컨텍스트로 기술 스택 추출
        full_context = f"{previous_context} {query}".strip() if previous_context else query
        tech_stack, skip_requested = await self._extract_tech_stack_with_llm(full_context)
        
        if tech_stack:
            # 기술 스택 정규화 및 검증
            normalized_tech_stack = _validate_and_normalize_tech_stack(tech_stack)
            if normalized_tech_stack:
                search_params["tech_stack"] = normalized_tech_stack
                search_params["collection_step"] = "complete"
            else:
                # 정규화 후 빈 리스트가 되면 질문
                return {
                    "recruitment_context": recruitment_context,
                    "requires_input": True,
                    "final_response": "포지션에 필요한 기술 스택이 있다면 적어주세요. 없으면 '없어' 또는 '필요없어'라고 답변해주세요. (예: React, Node.js, Python, Java 등)",
                    "messages": ["포지션에 필요한 기술 스택이 있다면 적어주세요. 없으면 '없어' 또는 '필요없어'라고 답변해주세요. (예: React, Node.js, Python, Java 등)"],
                    "workflow_in_progress": False,  # 워크플로우 차단
                    "workflow_type": None,
                    "workflow_steps": None,
                }
        elif skip_requested:
            search_params["tech_stack"] = []
            search_params["collection_step"] = "complete"
        else:
            # 기술 스택이 없으면 질문
            return {
                "recruitment_context": recruitment_context,
                "requires_input": True,
                "final_response": "포지션에 필요한 기술 스택이 있다면 적어주세요. 없으면 '없어' 또는 '필요없어'라고 답변해주세요. (예: React, Node.js, Python, Java 등)",
                "messages": ["포지션에 필요한 기술 스택이 있다면 적어주세요. 없으면 '없어' 또는 '필요없어'라고 답변해주세요. (예: React, Node.js, Python, Java 등)"],
                "workflow_in_progress": False,  # 워크플로우 차단
                "workflow_type": None,
                "workflow_steps": None,
            }
        
        recruitment_context["search_parameters"] = search_params
        
        # 수집된 모든 정보 요약
        position_info = search_params.get("position", "포지션")
        exp_info = []
        if search_params.get("experience_type"):
            exp_info.append(search_params.get("experience_type"))
        if search_params.get("experience_years"):
            exp_info.append(f"{search_params.get('experience_years')}년")
        exp_str = " ".join(exp_info) if exp_info else "경력 정보 없음"
        
        tech_stack_info = ""
        if search_params.get("tech_stack"):
            tech_list = ", ".join(search_params["tech_stack"][:5])  # 최대 5개만 표시
            if len(search_params["tech_stack"]) > 5:
                tech_list += f" 외 {len(search_params['tech_stack']) - 5}개"
            tech_stack_info = tech_list
        else:
            tech_stack_info = "기술 스택 없음"
        
        # 자연스러운 AI 답변 형식 - 최종 확인 및 검색 시작
        response = f"완벽합니다! 모든 정보를 수집했습니다.\n\n**검색 조건 요약:**\n- 포지션: {position_info}\n- 경력: {exp_str}\n- 기술 스택: {tech_stack_info}\n\n이제 위 조건에 맞는 인재를 검색해드리겠습니다. 잠시만 기다려주세요..."
        
        return {
            "recruitment_context": recruitment_context,
            "requires_input": False,
            "final_response": response,
            "messages": [response],
            "workflow_in_progress": False,  # 워크플로우 차단
            "workflow_type": None,
            "workflow_steps": None,
        }
    
    def route_after_intent(self, state: OrchestratorState) -> str:
        """의도 분류 후 라우팅."""
        intent = state.get("intent", "search")
        
        # 워크플로우 진행 중이 아니고 단순 검색 요청인지 확인
        workflow_in_progress = state.get("workflow_in_progress", False)
        workflow_type = state.get("workflow_type")
        
        # 워크플로우가 명시적으로 차단되었거나 진행 중이 아니면 단순 검색으로 처리
        if not workflow_in_progress and not workflow_type:
            if intent == "summarize":
                return "summarize"
            elif intent == "search":
                # 검색 요청인 경우 정보 수집이 필요한지 확인
                return "check_params"
            else:
                return "search"
        
        # 워크플로우 진행 중인 경우 (명시적 워크플로우 요청)
        if intent == "summarize":
            return "summarize"
        elif intent == "search":
            return "check_params"
        else:
            return "search"
    
    def route_collection_step(self, state: OrchestratorState) -> str:
        """수집 단계에 따른 라우팅."""
        recruitment_context = state.get("recruitment_context", {}) or {}
        search_params = recruitment_context.get("search_parameters", {})
        collection_step = search_params.get("collection_step")
        
        # collection_step이 없으면 position부터 시작
        if not collection_step:
            collection_step = "position"
        
        # collection_step이 "complete"이면 바로 검색으로
        if collection_step == "complete":
            print(f"✅ 모든 정보 수집 완료 - search_resumes로 라우팅")
            return "complete"  # "complete"는 "search_resumes"로 매핑됨
        
        print(f"✅ 수집 단계: {collection_step}")
        return collection_step
    
    def route_after_collection(self, state: OrchestratorState) -> str:
        """정보 수집 후 라우팅."""
        requires_input = state.get("requires_input", False)
        if requires_input:
            return "wait_input"  # 사용자 입력 대기
        else:
            return "continue"  # 다음 단계로
    
    def route_after_collection_tech_stack(self, state: OrchestratorState) -> str:
        """기술 스택 정보 수집 후 라우팅."""
        requires_input = state.get("requires_input", False)
        if requires_input:
            return "wait_input"  # 사용자 입력 대기
        
        # collection_step이 "complete"이면 바로 검색으로
        recruitment_context = state.get("recruitment_context", {}) or {}
        search_params = recruitment_context.get("search_parameters", {})
        collection_step = search_params.get("collection_step")
        
        if collection_step == "complete":
            return "search"  # 바로 검색으로
        else:
            return "continue"  # 다음 단계 확인
    
    async def _extract_position_with_llm(self, query: str) -> Optional[str]:
        """LLM 기반 포지션 추출 (키워드 기반 fallback 포함)."""
        try:
            system_prompt = """
사용자 입력에서 포지션 정보를 추출하세요.

포지션 예시 (다양한 표현 인식):
- 개발자: "개발자", "백엔드 개발자", "프론트엔드 개발자", "풀스택 개발자", "웹 개발자", "모바일 개발자", "게임 개발자"
- 데이터/AI: "데이터 엔지니어", "데이터 사이언티스트", "AI 엔지니어", "머신러닝 엔지니어", "딥러닝 엔지니어", "ML 엔지니어", "DL 엔지니어"
- 인프라/운영: "데브옵스 엔지니어", "DevOps", "SRE", "인프라 엔지니어", "시스템 엔지니어", "네트워크 엔지니어"
- QA/테스트: "QA 엔지니어", "테스터", "QA", "테스트 엔지니어", "QA 엔지니어"
- 기획/디자인: "기획자", "PM", "프로덕트 매니저", "디자이너", "UI/UX 디자이너", "그래픽 디자이너"
- 하드웨어/임베디드: "전자 엔지니어", "하드웨어 엔지니어", "임베디드 엔지니어", "반도체 엔지니어", "펌웨어 엔지니어", "시스템 엔지니어"
- 인사/채용: "인사 담당자", "인사담당자", "인사팀", "인사관리", "HR", "HR Manager", "HR Specialist", "채용 담당자", "채용담당자", "채용팀", "Recruiter", "Talent Acquisition", "인사채용 담당자", "인사채용담당자"
- 경영: "경영 관리자", "경영관리자", "경영팀", "경영지원", "Business Manager", "경영전략", "경영기획", "경영지원 담당자", "경영지원담당자"
- 기타: "보안 엔지니어", "보안 전문가", "블록체인 개발자", "솔루션 아키텍트", "시스템 아키텍트"

추출 규칙:
1. 사용자가 명시적으로 포지션을 언급한 경우 그 값을 position에 설정
2. 포지션의 다양한 표현을 인식 (예: "백엔드"만 언급해도 "백엔드 개발자"로 추출)
3. 포지션이 명시되지 않았으면 position은 None
4. confidence는 추출 신뢰도를 0.0~1.0 사이 값으로 설정
   - 명시적 언급: 0.9~0.95
   - 암시적 언급: 0.7~0.85
   - 없음: 0.0

예시:
- "개발자 포지션 지원자 찾아줘" → position="개발자", confidence=0.9
- "백엔드 개발자 추천해줘" → position="백엔드 개발자", confidence=0.95
- "백엔드 추천해줘" → position="백엔드 개발자", confidence=0.85
- "전자 엔지니어 필요해" → position="전자 엔지니어", confidence=0.9
- "임베디드 개발자 찾아줘" → position="임베디드 엔지니어", confidence=0.9
- "AI 엔지니어 추천" → position="AI 엔지니어", confidence=0.95
- "인사 담당자 추천해줘" → position="인사 담당자", confidence=0.9
- "채용 담당자 찾아줘" → position="채용 담당자", confidence=0.9
- "경영 관리자 추천" → position="경영 관리자", confidence=0.9
- "HR 추천해줘" → position="인사 담당자", confidence=0.85
- "인재 추천해줘" → position=None, confidence=0.0
- "데이터 분석할 수 있는 사람" → position="데이터 엔지니어", confidence=0.8
"""
            
            result = await extract_structured_output(
                query=query,
                model_class=PositionInfo,
                system_prompt=system_prompt,
                temperature=0.0
            )
            
            # 신뢰도가 0.7 이상이면 LLM 결과 사용
            if result.position and result.confidence >= 0.7:
                print(f"✅ LLM 기반 포지션 추출: {result.position} (신뢰도: {result.confidence:.2f})")
                return result.position
            
        except Exception as e:
            print(f"⚠️ LLM 기반 포지션 추출 실패: {e}, 키워드 기반 fallback 사용")
        
        # 키워드 기반 fallback
        positions = _extract_position_keywords(query)
        if positions:
            print(f"✅ 키워드 기반 포지션 추출: {positions[0]}")
            return positions[0]
        
        return None
    
    async def _extract_experience_with_llm(self, query: str) -> Tuple[Optional[str], Optional[float]]:
        """LLM 기반 경력 정보 추출 (키워드 기반 fallback 포함)."""
        try:
            system_prompt = """
사용자 입력에서 경력 정보를 추출하세요.

경력 타입 (다양한 표현 인식):
- 신입: "신입", "주니어", "junior", "entry level", "신입 개발자", "주니어 개발자", "초급", "entry", "신입급", "신입자", "신입 수준"
- 경력직: "경력", "시니어", "senior", "experienced", "경력직", "경력 개발자", "시니어 개발자", "중급", "고급", "경력자", "경력 있는", "경력자급", "경력직급"
- 명시되지 않았으면 experience_type=None

경력년수 (다양한 표현 인식):
- "5년", "5년차", "5년 이상", "5년 이상 경력", "최소 5년", "약 5년", "5년 정도", "5년 이상의 경력"
- "3-5년", "3~5년", "3년 이상 5년 이하", "3년에서 5년"
- "3년 이상", "3년 이상 경력", "최소 3년", "3년 이상의 경력"
- "신입"이면 experience_years=None
- 명시되지 않았으면 experience_years=None
- 범위가 주어진 경우 (예: "3-5년") 최소값을 experience_years로 설정

추출 규칙:
1. 경력 타입과 경력년수를 정확히 추출
2. 다양한 표현 패턴을 인식 (예: "5년차", "5년 이상", "최소 5년" 모두 5.0으로 추출)
3. 범위 표현 (예: "3-5년")은 최소값을 사용
4. confidence는 추출 신뢰도를 0.0~1.0 사이 값으로 설정
   - 명시적 언급: 0.9~0.95
   - 암시적 언급: 0.7~0.85
   - 없음: 0.0

예시:
- "경력 5년" → experience_type="경력직", experience_years=5.0, confidence=0.95
- "5년차 개발자" → experience_type="경력직", experience_years=5.0, confidence=0.95
- "5년 이상 경력" → experience_type="경력직", experience_years=5.0, confidence=0.95
- "3-5년 경력" → experience_type="경력직", experience_years=3.0, confidence=0.95
- "최소 3년 경력" → experience_type="경력직", experience_years=3.0, confidence=0.95
- "신입 찾아줘" → experience_type="신입", experience_years=None, confidence=0.9
- "주니어 개발자" → experience_type="신입", experience_years=None, confidence=0.9
- "시니어 개발자" → experience_type="경력직", experience_years=None, confidence=0.85
- "5년 이상 경력 개발자" → experience_type="경력직", experience_years=5.0, confidence=0.95
- "개발자 추천해줘" → experience_type=None, experience_years=None, confidence=0.0
"""
            
            result = await extract_structured_output(
                query=query,
                model_class=ExperienceInfo,
                system_prompt=system_prompt,
                temperature=0.0
            )
            
            # 신뢰도가 0.7 이상이면 LLM 결과 사용
            if result.confidence >= 0.7:
                experience_type = result.experience_type
                experience_years = result.experience_years
                print(f"✅ LLM 기반 경력 정보 추출: type={experience_type}, years={experience_years} (신뢰도: {result.confidence:.2f})")
                return experience_type, experience_years
            
        except Exception as e:
            print(f"⚠️ LLM 기반 경력 정보 추출 실패: {e}, 키워드 기반 fallback 사용")
        
        # 키워드 기반 fallback (더 많은 패턴 인식)
        query_lower = query.lower()
        experience_type = None
        
        # 신입 키워드 (더 많은 패턴)
        junior_keywords = [
            "신입", "junior", "entry", "entry level", "주니어", "초급",
            "신입 개발자", "주니어 개발자", "초급 개발자", "신입급", "신입자"
        ]
        if any(kw in query_lower for kw in junior_keywords):
            experience_type = "신입"
        
        # 경력직 키워드 (더 많은 패턴)
        senior_keywords = [
            "경력", "senior", "experienced", "경력직", "경력 개발자",
            "시니어", "시니어 개발자", "중급", "고급", "경력자", "경력 있는"
        ]
        if any(kw in query_lower for kw in senior_keywords):
            experience_type = "경력직"
        
        min_years, max_years = _extract_experience_range(query)
        experience_years = min_years if min_years else None
        
        if experience_type or experience_years:
            print(f"✅ 키워드 기반 경력 정보 추출: type={experience_type}, years={experience_years}")
        
        return experience_type, experience_years
    
    async def _extract_tech_stack_with_llm(self, query: str) -> Tuple[List[str], bool]:
        """LLM 기반 기술 스택 추출 (키워드 기반 fallback 포함)."""
        try:
            system_prompt = """
사용자 입력에서 기술 스택 정보를 추출하세요.

기술 스택 (다양한 기술 인식):
- 프론트엔드: React, Vue.js, Angular, Next.js, Nuxt.js, Svelte, TypeScript, JavaScript, HTML, CSS, SCSS, Sass, Tailwind CSS, Bootstrap, Material-UI, Ant Design
- 백엔드: Node.js, Express, NestJS, Python, Django, Flask, FastAPI, Java, Spring, Spring Boot, Kotlin, C#, .NET, ASP.NET, Go, Rust, Ruby, Ruby on Rails, PHP, Laravel
- 모바일: React Native, Flutter, Swift, Kotlin, iOS, Android, Xamarin, Ionic
- 데이터베이스: MySQL, PostgreSQL, MongoDB, Redis, Oracle, SQL Server, MariaDB, DynamoDB, Cassandra, Elasticsearch
- 클라우드/인프라: AWS, GCP, Azure, Docker, Kubernetes, Terraform, Ansible, Jenkins, GitLab CI/CD, GitHub Actions, CircleCI, Travis CI
- AI/ML: Python, TensorFlow, PyTorch, Keras, Scikit-learn, Pandas, NumPy, Jupyter, R, MATLAB
- 빅데이터: Hadoop, Spark, Kafka, Airflow, Presto, Hive, HBase
- 모니터링/로깅: Prometheus, Grafana, ELK Stack, Kibana, Splunk, Datadog, New Relic
- 하드웨어/임베디드: C, C++, ARM, STM32, Arduino, Raspberry Pi, Verilog, VHDL, PCB Design, Altium, Cadence, OrCAD, KiCad, Eagle, LabVIEW
- 기타: Git, SVN, Jira, Confluence, Figma, Sketch, Adobe XD, Postman, Swagger

추출 규칙:
1. 기술 스택이 명시된 경우 tech_stack에 리스트로 추가
2. 다양한 표현 패턴 인식:
   - 직접 언급: "React", "Node.js", "Python"
   - 기술명 + 동사: "React 사용하는", "Python 경험 있는", "Java 할 수 있는", "AWS 사용 가능한"
   - 기술명 + 필요/요구: "React 필요", "Node.js 요구", "Python 필수"
   - 기술명 + 개발자: "Python 개발자", "Java 개발자" (기술명만 추출)
3. 건너뛰기 요청 인식:
   - "없어", "필요없어", "없음", "없습니다", "skip", "건너뛰", "필요 없어요", "없어요", "없습니다", "필요 없음"
4. confidence는 추출 신뢰도를 0.0~1.0 사이 값으로 설정
   - 명시적 언급: 0.9~0.95
   - 암시적 언급: 0.7~0.85
   - 없음: 0.0

예시:
- "React, Node.js, AWS가 필요해" → tech_stack=["React", "Node.js", "AWS"], skip_requested=False, confidence=0.95
- "React와 Node.js 사용하는 개발자" → tech_stack=["React", "Node.js"], skip_requested=False, confidence=0.9
- "Python, Django 경험 있는" → tech_stack=["Python", "Django"], skip_requested=False, confidence=0.9
- "Python 개발자" → tech_stack=["Python"], skip_requested=False, confidence=0.8
- "Java와 Spring Boot 사용 가능한" → tech_stack=["Java", "Spring Boot"], skip_requested=False, confidence=0.9
- "ARM, STM32 경험" → tech_stack=["ARM", "STM32"], skip_requested=False, confidence=0.9
- "TensorFlow, PyTorch 사용하는 AI 엔지니어" → tech_stack=["TensorFlow", "PyTorch"], skip_requested=False, confidence=0.9
- "없어" → tech_stack=[], skip_requested=True, confidence=0.9
- "필요없어요" → tech_stack=[], skip_requested=True, confidence=0.9
- "개발자 추천해줘" → tech_stack=[], skip_requested=False, confidence=0.0
"""
            
            result = await extract_structured_output(
                query=query,
                model_class=TechStackInfo,
                system_prompt=system_prompt,
                temperature=0.0
            )
            
            # 신뢰도가 0.7 이상이면 LLM 결과 사용
            if result.confidence >= 0.7:
                print(f"✅ LLM 기반 기술 스택 추출: {result.tech_stack}, skip={result.skip_requested} (신뢰도: {result.confidence:.2f})")
                return result.tech_stack, result.skip_requested
            
        except Exception as e:
            print(f"⚠️ LLM 기반 기술 스택 추출 실패: {e}, 키워드 기반 fallback 사용")
        
        # 키워드 기반 fallback (더 많은 패턴 인식)
        query_lower = query.lower()
        skip_keywords = [
            "없어", "필요없어", "없음", "없습니다", "no", "skip", "건너뛰",
            "필요 없어", "필요 없어요", "없어요", "없습니다", "필요 없음",
            "기술 스택 없어", "기술 스택 필요없어", "기술 스택 없음"
        ]
        if any(kw in query_lower for kw in skip_keywords):
            print(f"✅ 키워드 기반 기술 스택 건너뛰기 감지")
            return [], True
        
        tech_stack = _extract_skill_keywords(query)
        if tech_stack:
            print(f"✅ 키워드 기반 기술 스택 추출: {tech_stack}")
        
        return tech_stack, False
    
    async def search_resumes(self, state: OrchestratorState) -> Dict[str, Any]:
        """이력서 검색 & 인재 추천."""
        print("--- [Recruitment Master] Searching Resumes ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        conversation_history = state.get("conversation_history", [])
        recruitment_context = state.get("recruitment_context", {}) or {}
        
        # 수집된 파라미터 사용
        search_params = recruitment_context.get("search_parameters", {})
        
        # 검색 쿼리 구성 (수집된 파라미터 우선)
        query_parts = []
        if search_params.get("position"):
            query_parts.append(search_params["position"])
        
        if search_params.get("experience_type") == "경력직" and search_params.get("experience_years"):
            query_parts.append(f"경력 {search_params['experience_years']}년")
        elif search_params.get("experience_type") == "신입":
            query_parts.append("신입")
        
        if search_params.get("tech_stack"):
            query_parts.extend(search_params["tech_stack"])
        
        # 쿼리 구성 (수집된 파라미터가 있으면 우선 사용)
        if query_parts:
            enhanced_query = " ".join(query_parts)
            print(f"✅ 수집된 파라미터로 검색 쿼리 구성: {enhanced_query}")
        else:
            # 수집된 파라미터가 없으면 원본 쿼리 사용
            enhanced_query = query
        
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
                "workflow_in_progress": False,  # 워크플로우 차단
                "workflow_type": None,
                "workflow_steps": None,
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
                "workflow_in_progress": False,  # 워크플로우 차단
                "workflow_type": None,
                "workflow_steps": None,
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
        
        # enhanced_query 사용 (수집된 파라미터가 있으면 우선)
        search_query = enhanced_query if query_parts else query
        
        result = await search_resumes(
            query=search_query,
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
        if not isinstance(recruitment_context, dict):
            recruitment_context = {}
        
        recruitment_context["resume_search"] = {
            "last_query": search_query,  # 실제 검색에 사용된 쿼리
            "last_results": sorted_resumes,  # 검색된 모든 인재 저장 (정렬된 상태)
            "displayed_count": len(displayed_resumes),  # 사용자에게 보여준 개수
            "total_count": len(sorted_resumes),  # 검색된 전체 개수
        }
        
        # 검색 완료 후 search_parameters 초기화 (다음 검색을 위해)
        recruitment_context["search_parameters"] = {}
        print("🔄 검색 완료 - search_parameters 초기화")
        
        print(f"✅ recruitment_context 업데이트 완료: {len(sorted_resumes)}개 인재 저장 (요청: {suggested_count}, 표시: {len(displayed_resumes)}개)")
        if sorted_resumes:
            print(f"📋 저장된 인재 샘플: {sorted_resumes[0]}")
        
        # AI 답변 형식으로 검색 결과 메시지 생성
        if displayed_resumes:
            # 검색 조건 요약
            search_condition = []
            if search_params.get("position"):
                search_condition.append(f"포지션: {search_params.get('position')}")
            if search_params.get("experience_type"):
                exp_info = search_params.get("experience_type")
                if search_params.get("experience_years"):
                    exp_info += f" {search_params.get('experience_years')}년"
                search_condition.append(f"경력: {exp_info}")
            if search_params.get("tech_stack"):
                tech_list = ", ".join(search_params["tech_stack"][:3])
                if len(search_params["tech_stack"]) > 3:
                    tech_list += f" 외 {len(search_params['tech_stack']) - 3}개"
                search_condition.append(f"기술 스택: {tech_list}")
            
            condition_str = "\n".join([f"- {cond}" for cond in search_condition]) if search_condition else "- 전체"
            
            # 자연스러운 AI 답변 형식
            response = f"검색이 완료되었습니다! 조건에 맞는 인재 {len(displayed_resumes)}명을 찾았습니다.\n\n**검색 조건:**\n{condition_str}\n\n**추천 인재 목록:**\n"
            
            # 인재 목록 추가 (최대 5명까지 상세 표시)
            for idx, resume in enumerate(displayed_resumes[:5], 1):
                name = resume.get("name", "이름 없음")
                position = resume.get("position", "포지션 없음")
                experience = resume.get("experience", "경력 정보 없음")
                score = resume.get("final_score") or resume.get("score", 0.0)
                response += f"{idx}. {name} ({position}, {experience}) - 매칭도: {score:.1%}\n"
            
            if len(displayed_resumes) > 5:
                response += f"... 외 {len(displayed_resumes) - 5}명\n"
            
            response += f"\n더 자세한 정보가 필요하시면 인재 번호를 말씀해주세요. (예: '1번 자세히', '2번 요약해줘')"
        else:
            # 검색 결과가 없을 때
            response = f"죄송합니다. 조건에 맞는 인재를 찾지 못했습니다.\n\n다른 조건으로 검색해보시겠어요? 포지션이나 경력, 기술 스택을 조정해보시면 더 많은 인재를 찾을 수 있습니다."
        
        return {
            "final_response": response,
            "messages": [response],
            "search_results": displayed_resumes,  # 검색된 모든 인재 표시 (suggested_count가 있으면 그 개수만큼)
            "recruitment_context": recruitment_context,
            "workflow_in_progress": False,  # 단순 검색 요청이므로 워크플로우 차단 (일반 요청으로 분류)
            "workflow_type": None,  # 워크플로우 타입 제거
            "workflow_steps": None,  # 워크플로우 단계 제거
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
                "workflow_in_progress": False,  # 워크플로우 차단
                "workflow_type": None,
                "workflow_steps": None,
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
                "workflow_in_progress": False,  # 워크플로우 차단
                "workflow_type": None,
                "workflow_steps": None,
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
                "workflow_in_progress": False,  # 워크플로우 차단
                "workflow_type": None,
                "workflow_steps": None,
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
            "workflow_in_progress": False,  # 워크플로우 차단
            "workflow_type": None,
            "workflow_steps": None,
        }


def get_recruitment_master_graph() -> StateGraph:
    """채용 마스터그래프 인스턴스 반환."""
    master = RecruitmentMasterGraph()
    return master.get_graph()

