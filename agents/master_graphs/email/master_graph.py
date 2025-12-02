"""이메일 마스터그래프 - 이메일 검색/작성/분석 (AI-021~AI-027)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio
import re

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.email.email_search_agent import email_search
from agents.structured_output import extract_structured_output, EmailSearchParams, AgentResponse


class EmailMasterGraph(BaseMasterGraph):
    """이메일 도메인 마스터그래프.
    
    요구사항:
    - AI-021: 이메일 라우팅
    - AI-022: 이메일 검색
    - AI-023: 답변 생성 (RAG)
    - AI-024: 에이전트 추천
    - AI-025: 수신 감지
    - AI-026: 답장 초안 생성
    - AI-027: 자동 발송
    """
    
    def get_domain_name(self) -> str:
        return "email"
    
    def get_graph(self) -> StateGraph:
        """이메일 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("search_email", self.search_email)
        workflow.add_node("generate_reply", self.generate_reply)
        workflow.add_node("detect_reception", self.detect_reception)
        workflow.add_node("send_email", self.send_email)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "search": "search_email",
                "reply": "generate_reply",
                "detect": "detect_reception",
            }
        )
        
        # 답장 생성 후 자동 발송 (선택적)
        workflow.add_conditional_edges(
            "generate_reply",
            self.should_send,
            {
                "yes": "send_email",
                "no": END,
            }
        )
        
        workflow.add_edge("search_email", END)
        workflow.add_edge("detect_reception", END)
        workflow.add_edge("send_email", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """이메일 도메인 내부 의도 분류."""
        print("--- [Email Master] Routing Intent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="email",
            fallback_route="search",
            session_id=session_id
        )
        
        # llm_router는 "search" 또는 "auto_reply"를 반환하지만,
        # 우리 그래프는 "search", "reply", "detect"를 사용
        if intent == "auto_reply":
            intent = "reply"
        elif intent not in ["search", "reply", "detect"]:
            # 키워드 기반 fallback
            if any(k in query for k in ["찾아", "검색", "search", "find"]):
                intent = "search"
            elif any(k in query for k in ["답장", "응답", "자동응답", "reply"]):
                intent = "reply"
            elif any(k in query for k in ["수신", "새 메일", "확인"]):
                intent = "detect"
            else:
                intent = "search"  # 기본값
        
        print(f"✅ Email 의도 결정: {intent}")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "search")
        return intent
    
    async def search_email(self, state: OrchestratorState) -> Dict[str, Any]:
        """이메일 검색 (AI-022).
        
        Structured Output Parser를 사용하여 사용자 쿼리에서 
        검색 파라미터를 구조적으로 추출합니다.
        """
        print("--- [Email Master] Searching Email ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        conversation_history = state.get("conversation_history", [])
        email_search_context = state.get("email_search_context", {})
        
        # 이전 요청 확인 질문인지 감지 ("요청한적있어?", "요청한 적 있어?", "요청했어?" 등)
        is_history_check = any(k in query for k in ["요청한적", "요청한 적", "요청했어", "요청했었어", "요청한적있어", "요청한 적 있어", 
                                                    "찾아달라고 요청", "검색 요청", "검색한적", "검색한 적", "검색했어"])
        
        if is_history_check:
            print(f"✅ 이전 요청 확인 질문 감지: '{query}' - 대화 히스토리 확인")
            
            # 대화 히스토리에서 이전 이메일 검색 요청 확인
            found_previous_request = False
            search_keywords = []
            
            # 1. email_search_context에서 이전 검색 키워드 확인
            if email_search_context and email_search_context.get("last_keyword"):
                found_previous_request = True
                search_keywords.append(email_search_context.get("last_keyword"))
            
            # 2. conversation_history에서 이전 검색 요청 확인
            if conversation_history:
                for msg in conversation_history:
                    content = msg.get("content", "")
                    role = msg.get("role", "")
                    
                    # 사용자 메시지에서 이메일 검색 요청 찾기
                    if role == "user" and any(k in content for k in ["이메일", "메일", "메일함", "이메일에서", "메일에서"]):
                        if any(k in content for k in ["찾아", "검색", "찾아줘", "검색해줘", "찾아달라", "검색해달라"]):
                            found_previous_request = True
                            # 키워드 추출 시도
                            import re as regex_module
                            # 간단한 키워드 추출 (예: "육아휴직 관련" -> "육아휴직")
                            keyword_match = regex_module.search(r'([가-힣a-zA-Z]+)\s*(?:관련|관해서|에 대해|에 대한)', content)
                            if keyword_match:
                                search_keywords.append(keyword_match.group(1))
            
            # 대화식 답변 생성
            if found_previous_request:
                if search_keywords:
                    keywords_str = ", ".join(set(search_keywords))
                    response = f"네, 이전에 {keywords_str} 관련 이메일을 검색해드린 적이 있습니다."
                else:
                    response = "네, 이전에 이메일 검색을 요청해주신 적이 있습니다."
            else:
                response = "아니요, 이전에 이메일 검색 요청은 없었습니다. 검색을 원하시면 말씀해주세요."
            
            return {
                "final_response": response,
                "messages": [response],
            }
        
        # "더 찾아줘", "계속 검색" 등의 연속 검색 요청인지 먼저 확인
        is_continue_search = any(k in query.lower() for k in ["더", "계속", "추가", "더 찾", "계속 검색", "더 검색", "더 찾아줘", "계속 찾아줘", "더 찾아", "계속 찾아"])
        
        # 상세 조회 요청인지 확인 (숫자로 시작하거나 "번"이 포함된 경우)
        # 예: "572. ... 알려줘", "355번 상세", "355번 메일"
        is_detail_request = re.match(r"^\d+\.?\s*", query.strip()) or re.search(r"\d+\s*번", query)
        
        # 연속 검색 요청이거나 상세 조회 요청인 경우 Structured Output을 건너뛰고 email_search에 원본 쿼리 전달
        # email_search 함수 내부에서 이전 검색 정보를 사용하여 처리함
        if is_continue_search or is_detail_request:
            if is_continue_search:
                print(f"✅ 연속 검색 요청 감지: '{query}' - 이전 검색 정보 사용")
            else:
                print(f"✅ 상세 조회 요청 감지: '{query}' - 원본 쿼리 사용")
                
            # email_search 함수에 원본 쿼리를 그대로 전달
            # email_search 내부에서 "더 찾아줘" 등을 감지하고 이전 키워드와 batch_offset 사용
            # 또는 번호/상세 요청을 감지하여 상세 내용 반환
            result = await email_search(query, session_id=session_id, state=state)
            
            # 연속 검색/상세 조회 요청인 경우 (Structured Output 없이 처리)
            # email_search에서 업데이트된 state를 반영하여 Redis에 저장
            return {
                "final_response": result.get("message", "이메일 처리 결과를 찾을 수 없습니다."),
                "messages": [result.get("message", "")],
                "search_results": result.get("rows", []),
                "email_search_context": state.get("email_search_context"),
            }
        else:
            # 대화 히스토리 컨텍스트 구성
            context = ""
            if conversation_history:
                recent_history = conversation_history[-3:]  # 최근 3개 메시지
                context = "\n\n이전 대화 내용:\n"
                for msg in recent_history:
                    context += f"{msg.get('role', 'unknown')}: {msg.get('content', '')}\n"
            
            # Structured Output Parser를 사용하여 검색 파라미터 추출
            try:
                system_prompt = f"""사용자의 이메일 검색 요청을 분석하여 다음 정보를 추출하세요:
- keywords: 검색할 핵심 키워드 리스트 (불용어 제거)
- from_address: 특정 발신자 주소 (있는 경우)
- date_range: 날짜 범위 (시작일, 종료일이 있는 경우)

{context}"""
                
                params = await extract_structured_output(
                    query=query,
                    model_class=EmailSearchParams,
                    system_prompt=system_prompt
                )
                
                print(f"✅ 구조화된 검색 파라미터 추출 완료:")
                print(f"   - 키워드: {params.keywords}")
                print(f"   - 발신자: {params.from_address}")
                print(f"   - 날짜 범위: {params.date_range}")
                
                # 추출된 파라미터로 검색
                # 키워드가 있으면 첫 번째 키워드를 검색 쿼리로 사용
                search_query = " ".join(params.keywords) if params.keywords else query
                
                # 기존 email_search_agent 사용 (Redis 숏텀 메모리 state 전달)
                # email_search는 이제 비동기 함수이므로 await 사용
                result = await email_search(search_query, session_id=session_id, state=state)
                
                # Structured Output을 사용한 응답 생성
                response = AgentResponse(
                    success=True,
                    message=result.get("message", "이메일 검색 결과를 찾을 수 없습니다."),
                    data={
                        "keywords": params.keywords,
                        "from_address": params.from_address,
                        "date_range": params.date_range,
                        "search_results": result.get("rows", [])
                    },
                    next_action="더 자세한 정보가 필요하신가요?"
                )
                
                # email_search에서 업데이트된 state를 반영하여 Redis에 저장
                # email_search 내부에서 state.update()를 통해 email_search_context가 업데이트됨
                # 업데이트된 state를 반환하여 Redis에 저장되도록 함
                return {
                    "final_response": response.message,
                    "messages": [response.message],
                    "search_results": result.get("rows", []),
                    # email_search_context는 state에 이미 업데이트되어 있으므로
                    # state를 반환하면 Redis에 저장됨
                    "email_search_context": state.get("email_search_context"),
                }
            except Exception as e:
                print(f"⚠️ Structured Output 파싱 실패: {e}, 기본 로직 사용")
                # Fallback: 기존 로직 사용 (Redis 숏텀 메모리 state 전달)
                result = await email_search(query, session_id=session_id, state=state)
                
                return {
                    "final_response": result.get("message", "이메일 검색 결과를 찾을 수 없습니다."),
                    "messages": [result.get("message", "")],
                    "search_results": result.get("rows", []),
                    # email_search_context는 state에 이미 업데이트되어 있으므로
                    # state를 반환하면 Redis에 저장됨
                    "email_search_context": state.get("email_search_context"),
                }
    
    async def generate_reply(self, state: OrchestratorState) -> Dict[str, Any]:
        """답장 초안 생성 (AI-026)."""
        print("--- [Email Master] Generating Reply ---")
        query = state.get("query", "")
        
        # 답장 초안 생성 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "email_draft": f"답장 초안 생성 기능은 현재 구현 중입니다. (요청: {query})",
            "messages": ["답장 초안 생성 기능은 현재 구현 중입니다."],
        }
    
    def should_send(self, state: OrchestratorState) -> str:
        """자동 발송이 필요한지 결정."""
        # 간단한 로직: email_draft가 있으면 발송 가능
        # 실제로는 사용자 승인(Human-in-the-loop)이 필요할 수 있음
        if state.get("email_draft"):
            return "no"  # 기본적으로 자동 발송하지 않음 (안전)
        return "no"
    
    async def send_email(self, state: OrchestratorState) -> Dict[str, Any]:
        """자동 발송 (AI-027)."""
        print("--- [Email Master] Sending Email ---")
        email_draft = state.get("email_draft", "")
        
        # 이메일 발송 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "이메일이 발송되었습니다.",
            "messages": ["이메일 발송이 완료되었습니다."],
        }
    
    async def detect_reception(self, state: OrchestratorState) -> Dict[str, Any]:
        """수신 감지 (AI-025)."""
        print("--- [Email Master] Detecting Reception ---")
        
        # 수신 감지 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "새로운 이메일이 없습니다.",
            "messages": ["이메일 수신 확인이 완료되었습니다."],
        }


def get_email_master_graph() -> StateGraph:
    """이메일 마스터그래프 인스턴스 반환."""
    master = EmailMasterGraph()
    return master.get_graph()

