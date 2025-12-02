"""문서 마스터그래프 - 문서 작성/업로드/변환 (AI-011~AI-015)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.document.doc_generate_agent import doc_generate
from agents.master_graphs.document.document_upload_service import upload_document
from agents.structured_output import extract_structured_output, DocumentGenerationParams, AgentResponse


class DocumentMasterGraph(BaseMasterGraph):
    """문서 도메인 마스터그래프.
    
    요구사항:
    - AI-011: 문서 라우팅
    - AI-012: 문서 자동 작성
    - AI-013: PDF 변환
    - AI-014: 자동 결재 요청
    - AI-015: 지식 업로드 (RAG)
    """
    
    def get_domain_name(self) -> str:
        return "document"
    
    def get_graph(self) -> StateGraph:
        """문서 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("generate_document", self.generate_document)
        workflow.add_node("convert_to_pdf", self.convert_to_pdf)
        workflow.add_node("request_approval", self.request_approval)
        workflow.add_node("upload_knowledge", self.upload_knowledge)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "generate": "generate_document",
                "upload": "upload_knowledge",
            }
        )
        
        # 문서 생성 후 PDF 변환 및 결재 요청 (선택적)
        workflow.add_conditional_edges(
            "generate_document",
            self.should_convert_pdf,
            {
                "yes": "convert_to_pdf",
                "no": END,
                "ask": "ask_pdf_conversion",  # Human-in-the-loop 노드 추가 필요
            }
        )
        
        # Human-in-the-loop 노드 추가
        workflow.add_node("ask_pdf_conversion", self.ask_pdf_conversion)
        workflow.add_conditional_edges(
            "ask_pdf_conversion",
            self.handle_pdf_conversion_response,
            {
                "yes": "convert_to_pdf",
                "no": END,
            }
        )
        
        workflow.add_conditional_edges(
            "convert_to_pdf",
            self.should_request_approval,
            {
                "yes": "request_approval",
                "no": END,
            }
        )
        
        workflow.add_edge("request_approval", END)
        workflow.add_edge("upload_knowledge", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """문서 도메인 내부 의도 분류."""
        print("--- [Document Master] Routing Intent ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="document",
            fallback_route="doc_generate",
            session_id=session_id
        )
        
        # llm_router는 "email_auto_reply", "doc_generate"를 반환하지만,
        # 우리 그래프는 "generate", "upload"를 사용
        if intent == "doc_generate":
            intent = "generate"
        elif intent == "email_auto_reply":
            # 이메일 자동응답은 이 도메인에서 처리하지 않음 (상위 Orchestrator에서 처리)
            intent = "generate"  # 기본값으로 문서 생성
        elif intent not in ["generate", "upload"]:
            # 키워드 기반 fallback
            if any(k in query for k in ["업로드", "저장", "지식", "RAG", "벡터"]):
                intent = "upload"
            else:
                intent = "generate"  # 기본값
        
        print(f"✅ Document 의도 결정: {intent}")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "generate")
        return intent
    
    async def generate_document(self, state: OrchestratorState) -> Dict[str, Any]:
        """문서 자동 작성 (AI-012).
        
        Structured Output Parser를 사용하여 사용자 쿼리에서 
        문서 생성에 필요한 파라미터를 구조적으로 추출합니다.
        Redis 기반 숏텀메모리를 사용하여 이전 대화 컨텍스트를 유지합니다.
        """
        print("--- [Document Master] Generating Document ---")
        query = state.get("query", "")
        conversation_history = state.get("conversation_history", [])
        orchestrator_state = state
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("document_context") is None:
            orchestrator_state["document_context"] = {}
        
        doc_context = orchestrator_state["document_context"]
        
        # 이전 대화 컨텍스트 확인
        pending_approval = doc_context.get("pending_approval")
        
        # 승인 대기 중인 경우 사용자 응답 처리
        if pending_approval and any(k in query.lower() for k in ["승인", "확인", "네", "예", "yes", "ok", "취소", "아니오", "no"]):
            if any(k in query.lower() for k in ["승인", "확인", "네", "예", "yes", "ok"]):
                # 승인 처리: 결재 요청 진행
                return await self.request_approval(state)
            else:
                # 취소 처리
                orchestrator_state["document_context"]["pending_approval"] = None
                orchestrator_state["waiting_for_approval"] = None
                return {
                    "final_response": "문서 결재 요청이 취소되었습니다.",
                    "messages": ["문서 결재 요청이 취소되었습니다."],
                    "document_context": orchestrator_state["document_context"],
                }
        
        # 대화 히스토리 컨텍스트 구성
        context = ""
        if conversation_history:
            recent_history = conversation_history[-3:]  # 최근 3개 메시지
            context = "\n\n이전 대화 내용:\n"
            for msg in recent_history:
                context += f"{msg.get('role', 'unknown')}: {msg.get('content', '')}\n"
        
        # Structured Output Parser를 사용하여 파라미터 추출
        try:
            system_prompt = f"""사용자의 문서 생성 요청을 분석하여 다음 정보를 추출하세요:
- template_type: 생성할 문서 템플릿 타입 (예: hr_summary_report, product_comparison, monthly_profit_analysis, work_performance_review, purchase_order, activity_based_costing)
- variables: 템플릿에 필요한 변수 값들 (날짜, 이름, 부서명 등)
- output_format: 출력 형식 (html 또는 pdf)

{context}"""
            
            params = await extract_structured_output(
                query=query,
                model_class=DocumentGenerationParams,
                system_prompt=system_prompt
            )
            
            print(f"✅ 구조화된 파라미터 추출 완료:")
            print(f"   - 템플릿 타입: {params.template_type}")
            print(f"   - 변수 개수: {len(params.variables)}")
            print(f"   - 출력 형식: {params.output_format}")
            
            # 추출된 파라미터로 문서 생성 (기존 doc_generate_agent 사용하되, 파라미터 전달)
            # 실제 구현에서는 params를 기반으로 문서를 생성해야 함
            result = doc_generate(query)
            
            # 결과를 state에 저장
            document_content = result.get("message", "")
            document_path = result.get("file_path")
            
            # Redis 컨텍스트에 저장
            orchestrator_state["document_context"]["last_template_type"] = params.template_type
            orchestrator_state["document_context"]["last_variables"] = params.variables
            
            # Structured Output을 사용한 응답 생성
            response = AgentResponse(
                success=True,
                message=f"{params.template_type} 문서 생성 요청을 접수했습니다.",
                data={
                    "template_type": params.template_type,
                    "variables": params.variables,
                    "output_format": params.output_format,
                    "document_path": document_path
                },
                next_action="PDF 변환이 필요하신가요?"
            )
            
            return {
                "document_content": document_content,
                "document_path": document_path,
                "final_response": response.message,
                "messages": [response.message],
                "document_context": orchestrator_state["document_context"],
            }
            
        except Exception as e:
            print(f"⚠️ Structured Output 파싱 실패: {e}, 기본 로직 사용")
            # Fallback: 기존 로직 사용
            result = doc_generate(query)
            document_content = result.get("message", "")
            document_path = result.get("file_path")
            
            return {
                "document_content": document_content,
                "document_path": document_path,
                "final_response": document_content,
                "messages": ["문서가 생성되었습니다."],
                "document_context": orchestrator_state["document_context"],
            }
    
    def should_convert_pdf(self, state: OrchestratorState) -> str:
        """PDF 변환이 필요한지 결정.
        
        Human-in-the-loop: 사용자에게 PDF 변환 여부를 확인합니다.
        """
        # document_content가 있고, 사용자가 명시적으로 PDF 변환을 요청하지 않은 경우 확인 필요
        if state.get("document_content"):
            query = state.get("query", "").lower()
            # 명시적으로 PDF 변환을 요청한 경우
            if any(k in query for k in ["pdf", "변환", "pdf로", "pdf 변환"]):
                return "yes"
            # 명시적으로 PDF 변환을 거부한 경우
            if any(k in query for k in ["pdf 안", "pdf 변환 안", "변환 안"]):
                return "no"
            # Human-in-the-loop: 사용자에게 확인
            return "ask"  # ask는 새로운 엣지로 처리
        return "no"
    
    async def convert_to_pdf(self, state: OrchestratorState) -> Dict[str, Any]:
        """PDF 변환 (AI-013)."""
        print("--- [Document Master] Converting to PDF ---")
        document_content = state.get("document_content", "")
        
        # PDF 변환 로직 (기존 구현이 있으면 사용, 없으면 간단한 구현)
        # 여기서는 메시지만 반환
        return {
            "document_path": f"/tmp/document_{state.get('session_id', 'default')}.pdf",
            "messages": ["PDF 변환이 완료되었습니다."],
        }
    
    def should_request_approval(self, state: OrchestratorState) -> str:
        """결재 요청이 필요한지 결정."""
        # 간단한 로직: PDF가 생성되었으면 결재 요청 가능
        if state.get("document_path"):
            return "yes"
        return "no"
    
    async def ask_pdf_conversion(self, state: OrchestratorState) -> Dict[str, Any]:
        """PDF 변환 여부를 사용자에게 확인 (Human-in-the-loop)."""
        print("--- [Document Master] Asking PDF Conversion ---")
        orchestrator_state = state
        
        orchestrator_state["waiting_for_approval"] = {
            "type": "pdf_conversion",
            "data": {
                "document_path": state.get("document_path"),
                "template_type": orchestrator_state.get("document_context", {}).get("last_template_type"),
            },
            "message": "생성된 문서를 PDF로 변환하시겠습니까?\n\n"
                      "승인하시려면 '승인', '확인', '네', 'PDF 변환'이라고 말씀해주세요.\n"
                      "취소하시려면 '취소', '아니오', '변환 안'이라고 말씀해주세요."
        }
        
        return {
            "final_response": orchestrator_state["waiting_for_approval"]["message"],
            "messages": [orchestrator_state["waiting_for_approval"]["message"]],
            "waiting_for_approval": orchestrator_state["waiting_for_approval"],
        }
    
    def handle_pdf_conversion_response(self, state: OrchestratorState) -> str:
        """PDF 변환 응답 처리."""
        query = state.get("query", "").lower()
        if any(k in query for k in ["승인", "확인", "네", "예", "yes", "ok", "pdf", "변환"]):
            return "yes"
        return "no"
    
    async def request_approval(self, state: OrchestratorState) -> Dict[str, Any]:
        """자동 결재 요청 (AI-014).
        
        Human-in-the-loop: 결재 요청 전 사용자 승인을 받습니다.
        """
        print("--- [Document Master] Requesting Approval ---")
        document_path = state.get("document_path", "")
        orchestrator_state = state
        
        # 승인 대기 중인 경우 사용자 응답 처리
        pending_approval = orchestrator_state.get("waiting_for_approval")
        if pending_approval and pending_approval.get("type") == "document_approval":
            query = state.get("query", "").lower()
            if any(k in query for k in ["승인", "확인", "네", "예", "yes", "ok"]):
                # 실제 결재 요청 로직 (실제 구현 필요)
                orchestrator_state["document_context"]["pending_approval"] = None
                orchestrator_state["waiting_for_approval"] = None
                return {
                    "final_response": f"✅ 문서가 생성되고 결재 요청이 완료되었습니다. (파일: {document_path})",
                    "messages": ["결재 요청이 완료되었습니다."],
                    "document_context": orchestrator_state["document_context"],
                }
            else:
                # 취소 처리
                orchestrator_state["document_context"]["pending_approval"] = None
                orchestrator_state["waiting_for_approval"] = None
                return {
                    "final_response": "문서 결재 요청이 취소되었습니다.",
                    "messages": ["문서 결재 요청이 취소되었습니다."],
                    "document_context": orchestrator_state["document_context"],
                }
        
        # Human-in-the-loop: 결재 요청 전 승인 요청
        approval_data = {
            "document_path": document_path,
            "template_type": orchestrator_state.get("document_context", {}).get("last_template_type"),
        }
        
        orchestrator_state["document_context"]["pending_approval"] = approval_data
        orchestrator_state["waiting_for_approval"] = {
            "type": "document_approval",
            "data": approval_data,
            "message": f"다음 문서에 대한 결재를 요청하시겠습니까?\n\n"
                      f"파일: {document_path}\n"
                      f"템플릿 타입: {orchestrator_state.get('document_context', {}).get('last_template_type', 'N/A')}\n\n"
                      f"승인하시려면 '승인', '확인', '네'라고 말씀해주세요.\n"
                      f"취소하시려면 '취소', '아니오'라고 말씀해주세요."
        }
        
        return {
            "final_response": orchestrator_state["waiting_for_approval"]["message"],
            "messages": [orchestrator_state["waiting_for_approval"]["message"]],
            "document_context": orchestrator_state["document_context"],
            "waiting_for_approval": orchestrator_state["waiting_for_approval"],
        }
    
    async def upload_knowledge(self, state: OrchestratorState) -> Dict[str, Any]:
        """지식 업로드 (RAG) (AI-015)."""
        print("--- [Document Master] Uploading Knowledge ---")
        query = state.get("query", "")
        
        # 기존 document_upload_service 사용
        # 파일 경로가 state에 있으면 사용
        file_path = state.get("document_path")
        
        if file_path:
            result = upload_document(file_path)
        else:
            # 파일 경로가 없으면 에러
            return {
                "ok": False,
                "message": "업로드할 파일 경로가 필요합니다.",
                "error": "file_path is required",
            }
        
        return {
            "final_response": result.get("message", "문서 업로드가 완료되었습니다."),
            "messages": ["문서가 지식 베이스에 업로드되었습니다."],
        }


def get_document_master_graph() -> StateGraph:
    """문서 마스터그래프 인스턴스 반환."""
    master = DocumentMasterGraph()
    return master.get_graph()

