"""채용 마스터그래프 - 인재 추천 및 이력서 요약."""

from __future__ import annotations

from typing import Any, Dict
import asyncio
import re
import logging
import time
from datetime import datetime
from pathlib import Path
import yaml

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.recruitment.resume_search_agent import search_resumes
from agents.master_graphs.recruitment.resume_summarize_agent import summarize_resume

# 로거 설정
logger = logging.getLogger(__name__)


class RecruitmentMasterGraph(BaseMasterGraph):
    """채용 도메인 마스터그래프.
    
    기능:
    - 인재 검색 및 추천: 벡터 DB에서 이력서를 검색하여 인재 추천
    - 이력서 핵심 요약: 선택된 이력서를 LLM으로 핵심 요약
    
    멀티턴 대화 지원:
    - Redis에 검색된 인재 목록, 선택된 이력서 정보를 저장
    - "1번 요약해줘", "2번 자세히" 같은 요청 시 이전 검색 컨텍스트를 활용
    """
    
    def __init__(self):
        """초기화 및 YAML 설정 로드."""
        super().__init__()
        
        # prompts.yaml 로드
        yaml_path = Path(__file__).parent / "prompts.yaml"
        with open(yaml_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        logger.info(f"✅ Recruitment 도메인 YAML 설정 로드 완료: {yaml_path}")
    
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
        recruitment_context = state.get("recruitment_context", {}) or {}
        
        # ✅ YAML에서 상세 정보 요청 키워드 로드
        detail_keywords = self.config.get("detail_request", {}).get("keywords", [])
        is_detail_request = any(k in query for k in detail_keywords)
        
        if is_detail_request:
            print(f"✅ 상세 정보 요청 감지: '{query}'")
            
            # ✅ YAML에서 이름 추출 키워드 로드
            name_extraction_keywords = self.config.get("detail_request", {}).get("name_extraction", {}).get("before_keywords", [])
            excluded_words = self.config.get("detail_request", {}).get("name_extraction", {}).get("excluded_words", [])
            
            if name_extraction_keywords:
                pattern = r'([가-힣]{2,4})\s*(?:' + '|'.join(name_extraction_keywords) + ')'
                name_match = re.search(pattern, query)
                
                if name_match:
                    target_name = name_match.group(1)
                    
                    # ✅ 일반 명사 제외
                    if target_name in excluded_words:
                        print(f"⚠️ '{target_name}'은(는) 일반 명사로 제외됨 → 일반 검색으로 처리")
                        # 이름 기반 요청이 아니므로 아래 일반 검색 로직으로 이동
                    elif recruitment_context:
                        last_results = recruitment_context.get("resume_search", {}).get("last_results", [])
                        
                        print(f"🔍 '{target_name}'님 검색 중... (저장된 결과: {len(last_results)}개)")
                        
                        # 이름으로 검색
                        for resume in last_results:
                            if resume.get("name") == target_name:
                                content = resume.get("content", "상세 정보를 찾을 수 없습니다.")
                                print(f"✅ '{target_name}'님 정보 찾음!")
                                
                                return {
                                    "final_response": f"📄 **{target_name}님의 상세 이력서**\n\n{content}",
                                    "messages": [f"{target_name}님의 상세 정보"],
                                    "search_results": [],
                                    "recruitment_context": recruitment_context,
                                }
                        
                        print(f"⚠️ '{target_name}'님을 찾을 수 없음")
                        # ✅ YAML에서 에러 메시지 로드
                        error_msg = self.config.get("detail_request", {}).get("error_messages", {}).get("not_found", "정보를 찾을 수 없습니다.")
                        error_msg = error_msg.format(name=target_name)
                        
                        return {
                            "final_response": error_msg,
                            "messages": ["정보를 찾을 수 없음"],
                            "search_results": [],
                            "recruitment_context": recruitment_context,
                        }
            
            # ✅ YAML에서 에러 메시지 로드
            no_name_msg = self.config.get("detail_request", {}).get("error_messages", {}).get("no_name", "이름을 명시해주세요.")
            
            return {
                "final_response": no_name_msg,
                "messages": ["이름을 명시해주세요"],
                "search_results": [],
                "recruitment_context": recruitment_context,
            }
        
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
        # 먼저 조건이 있는지 확인
        has_position = any(keyword in query for keyword in [
            "개발자", "엔지니어", "백엔드", "프론트엔드", "풀스택", "AI", 
            "인사", "담당자", "매니저", "디자이너", "기획자", "PM", "PO",
            "데브옵스", "DevOps", "QA", "데이터", "분석가", "마케터"
        ])
        
        has_experience = any(keyword in query for keyword in [
            "년", "경력", "신입", "초급", "중급", "시니어", "주니어"
        ]) or bool(re.search(r'\d+[-~]\d+\s*년', query))  # "4-6년", "3~5년" 형태
        
        has_skills = any(keyword in query for keyword in [
            "Python", "Java", "JavaScript", "TypeScript", "React", "Vue", "Angular",
            "Node", "Django", "Flask", "Spring", "AWS", "Azure", "GCP",
            "Docker", "Kubernetes", "MySQL", "PostgreSQL", "MongoDB", "Redis"
        ])
        
        # 조건이 하나도 없으면 사용자에게 조건 요청
        if not (has_position or has_experience or has_skills):
            print(f"⚠️ 조건 없는 인재 추천 요청 감지: '{query}'")
            guidance_message = (
                "인재 추천을 도와드리겠습니다! 다음 정보를 알려주세요:\n\n"
                "1️⃣ **포지션**: 어떤 직무의 인재를 찾으시나요?\n"
                "   (예: 백엔드 개발자, 프론트엔드 개발자, AI 엔지니어, 인사담당자 등)\n\n"
                "2️⃣ **경력**: 경력은 얼마나 되어야 하나요?\n"
                "   (예: 신입, 1-3년, 4-6년, 7-9년, 10년 이상)\n\n"
                "3️⃣ **기술** (선택): 특정 기술 스택이 필요하신가요?\n"
                "   (예: Python, Java, AWS, React 등)\n\n"
                "💡 **예시**: \"백엔드 개발자 경력 4-6년 Python, AWS 경험자\""
            )
            return {
                "final_response": guidance_message,
                "messages": [guidance_message],
                "requires_input": True,
                "search_results": [],
                "recruitment_context": state.get("recruitment_context", {}) or {},
            }
        
        print(f"✅ 조건이 포함된 검색 요청 - 포지션: {has_position}, 경력: {has_experience}, 스킬: {has_skills}")
        
        # 검색 파라미터 추출
        search_limit = 5  # 기본값
        
        # 검색 실행
        result = await search_resumes(
            query=query,
            limit=search_limit,
            session_id=session_id,
            state=state
        )
        
        # recruitment_context 업데이트
        recruitment_context = state.get("recruitment_context", {}) or {}
        if not isinstance(recruitment_context, dict):
            recruitment_context = {}
        
        recruitment_context["resume_search"] = {
            "last_query": query,
            "last_results": result.get("resumes", []),
            "displayed_count": len(result.get("resumes", [])),
        }
        
        print(f"✅ recruitment_context 업데이트 완료: {len(result.get('resumes', []))}개 인재 저장")
        print(f"📋 저장된 인재 샘플: {result.get('resumes', [])[0] if result.get('resumes') else '없음'}")
        
        return {
            "final_response": result.get("message", "인재 검색 결과를 찾을 수 없습니다."),
            "messages": [result.get("message", "")],
            "search_results": result.get("resumes", []),
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
        
        # ✅ 이름 기반 상세 정보 요청 처리 (우선)
        detail_keywords = self.config.get("detail_request", {}).get("keywords", [])
        is_detail_request = any(k in query for k in detail_keywords)
        
        if is_detail_request:
            name_extraction_keywords = self.config.get("detail_request", {}).get("name_extraction", {}).get("before_keywords", [])
            excluded_words = self.config.get("detail_request", {}).get("name_extraction", {}).get("excluded_words", [])
            
            if name_extraction_keywords:
                pattern = r'([가-힣]{2,4})\s*(?:' + '|'.join(name_extraction_keywords) + ')'
                name_match = re.search(pattern, query)
                
                if name_match:
                    target_name = name_match.group(1)
                    
                    # ✅ 일반 명사 제외
                    if target_name in excluded_words:
                        print(f"⚠️ '{target_name}'은(는) 일반 명사로 제외됨 → 번호 기반 처리로 이동")
                        # 이름 기반 요청이 아니므로 아래 번호 기반 로직으로 이동
                    else:
                        last_results = recruitment_context.get("resume_search", {}).get("last_results", [])
                        
                        print(f"🔍 '{target_name}'님 검색 중... (저장된 결과: {len(last_results)}개)")
                        
                        for resume in last_results:
                            if resume.get("name") == target_name:
                                content = resume.get("content", "상세 정보를 찾을 수 없습니다.")
                                print(f"✅ '{target_name}'님 정보 찾음!")
                                
                                return {
                                    "final_response": f"📄 **{target_name}님의 상세 이력서**\n\n{content}",
                                    "messages": [f"{target_name}님의 상세 정보"],
                                    "search_results": [],
                                    "recruitment_context": recruitment_context,
                                }
                        
                        # 이름을 찾았지만 결과에 없음
                        error_msg = self.config.get("detail_request", {}).get("error_messages", {}).get("not_found", "'{name}'님의 정보를 찾을 수 없습니다.")
                        error_msg = error_msg.format(name=target_name)
                        
                        return {
                            "final_response": error_msg,
                            "messages": ["정보를 찾을 수 없음"],
                            "search_results": [],
                            "recruitment_context": recruitment_context,
                        }
        
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

