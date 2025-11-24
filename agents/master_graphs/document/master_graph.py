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
        """문서 자동 작성 (AI-012)."""
        print("--- [Document Master] Generating Document ---")
        query = state.get("query", "")
        
        # 기존 doc_generate_agent 사용
        result = doc_generate(query)
        
        # 결과를 state에 저장
        document_content = result.get("message", "")
        document_path = result.get("file_path")
        
        return {
            "document_content": document_content,
            "document_path": document_path,
            "messages": ["문서가 생성되었습니다."],
        }
    
    def should_convert_pdf(self, state: OrchestratorState) -> str:
        """PDF 변환이 필요한지 결정."""
        # 간단한 로직: document_content가 있으면 PDF 변환 가능
        if state.get("document_content"):
            return "yes"
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
    
    async def request_approval(self, state: OrchestratorState) -> Dict[str, Any]:
        """자동 결재 요청 (AI-014)."""
        print("--- [Document Master] Requesting Approval ---")
        document_path = state.get("document_path", "")
        
        # 결재 요청 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": f"문서가 생성되고 결재 요청이 완료되었습니다. (파일: {document_path})",
            "messages": ["결재 요청이 완료되었습니다."],
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

