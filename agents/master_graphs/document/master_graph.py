"""문서 마스터그래프 - 문서 작성/업로드/변환 (AI-011~AI-015)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
import asyncio

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.document.doc_generate_agent import doc_generate

# LLM 관련 optional import
try:
    from langchain_openai import ChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    ChatOpenAI = None
# document_upload_service는 실제 사용 시점에만 import (lazy import)
# sentence_transformers 의존성 때문에 모듈 레벨 import 시 충돌 발생 가능


class DocumentMasterGraph(BaseMasterGraph):
    """문서 도메인 마스터그래프.
    
    요구사항:
    - AI-011: 문서 라우팅 - 문서 작업 유형(작성/업로드/변환) 분류 → 템플릿 매핑
    - AI-012: 문서 자동 작성 - 템플릿 로드 → DB 데이터 매핑(Data Binding) → 초안 생성
    - AI-013: 문서 업로드 (RAG) - 파일 파싱(Text Extraction) → 청킹(Chunking) → 임베딩(Embedding) → Vector DB(Milvus) Upsert
    """
    
    def get_domain_name(self) -> str:
        return "document"
    
    def get_graph(self) -> StateGraph:
        """문서 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)  # AI-011: 문서 라우팅 (작업 유형 분류 + 템플릿 매핑)
        workflow.add_node("generate_document", self.generate_document)  # AI-012: 문서 작성 (템플릿 로드 → DB 데이터 매핑 → 초안 생성)
        workflow.add_node("check_approval_required", self.check_approval_required)  # 휴먼인더루프: 결재 필요 여부 판단
        workflow.add_node("request_user_confirmation", self.request_user_confirmation)  # 휴먼인더루프: 사용자 확인 요청
        workflow.add_node("convert_to_pdf", self.convert_to_pdf)
        workflow.add_node("request_approval", self.request_approval)
        workflow.add_node("upload_knowledge", self.upload_knowledge)  # AI-013: 문서 업로드 (파일 파싱 → 청킹 → 임베딩 → Vector DB Upsert)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # AI-011: 문서 작업 유형 분류 및 템플릿 매핑
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "generate": "generate_document",  # 문서 작성
                "upload": "upload_knowledge",  # 문서 업로드 (RAG)
                "convert": "convert_to_pdf",  # 문서 변환 (향후 구현)
            }
        )
        
        # 문서 생성 후 결재 필요 여부 확인
        workflow.add_conditional_edges(
            "generate_document",
            self.should_check_approval,
            {
                "check": "check_approval_required",
                "skip": END,  # document_content가 없으면 종료
            }
        )
        
        # 결재 필요 여부에 따라 분기
        workflow.add_conditional_edges(
            "check_approval_required",
            self.route_by_approval_status,
            {
                "required": "request_user_confirmation",  # 결재 필요 시 사용자 확인
                "not_required": END,  # 결재 불필요 시 바로 종료
            }
        )
        
        # 사용자 확인 후 PDF 변환 및 결재 프로세스
        workflow.add_conditional_edges(
            "request_user_confirmation",
            self.handle_user_confirmation,
            {
                "approved": "convert_to_pdf",  # 승인 시 PDF 변환 진행
                "rejected": END,  # 거부 시 종료
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
        """AI-011: 문서 라우팅 - 문서 작업 유형(작성/업로드/변환) 분류 → 템플릿 매핑.
        
        작업 유형:
        - generate: 문서 작성 (템플릿 기반 문서 생성)
        - upload: 문서 업로드 (RAG를 위한 지식 업로드)
        - convert: 문서 변환 (PDF 등 형식 변환)
        """
        print("--- [AI-011] Document Routing: 작업 유형 분류 및 템플릿 매핑 ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # 1단계: 작업 유형 분류 (작성/업로드/변환)
        import re
        
        # 키워드 기반 작업 유형 판단
        if any(k in query for k in ["업로드", "저장", "지식", "RAG", "벡터", "벡터DB", "밀브스"]):
            operation_type = "upload"
        elif any(k in query for k in ["변환", "PDF", "엑셀", "워드", "convert"]):
            operation_type = "convert"
        else:
            operation_type = "generate"  # 기본값: 문서 작성
        
        # LLM 기반 세밀한 분류 (선택적)
        try:
            llm_intent = await asyncio.to_thread(
                classify_intent_with_llm,
                query=query,
                route_type="document",
                fallback_route="doc_generate",
                session_id=session_id
            )
            
            if llm_intent == "doc_generate":
                operation_type = "generate"
            elif any(k in llm_intent for k in ["upload", "지식", "RAG"]):
                operation_type = "upload"
        except Exception as e:
            print(f"⚠️ LLM 기반 분류 실패, 키워드 기반 사용: {e}")
        
        # 2단계: 템플릿 매핑 (문서 작성의 경우)
        template_name = None
        if operation_type == "generate":
            # 쿼리에서 템플릿명 추출
            template_match = re.search(r'양식[:\s]+(\w+)|템플릿[:\s]+(\w+)|template[:\s]+(\w+)', query, re.IGNORECASE)
            if template_match:
                template_name = template_match.group(1) or template_match.group(2) or template_match.group(3)
            else:
                # 기본 템플릿 매핑 (키워드 기반)
                template_keywords = {
                    "product_development_plan": ["제품개발", "신제품", "개발계획"],
                    "purchase_order": ["발주", "구매", "주문"],
                    "hr_summary_report": ["인사", "인력", "직원"],
                    "monthly_profit_analysis_report": ["수익", "매출", "분석"],
                    "work_performance_review": ["성과", "평가", "업무"],
                    "supplier_evaluation_form": ["공급업체", "협력사", "평가"],
                    "contract_renewal_report": ["계약", "갱신", "연장"],
                    "activity_based_costing": ["원가", "비용", "계산"],
                    "project_schedule": ["프로젝트", "일정", "스케줄"],
                    "product_comparison_table": ["비교", "제품비교"],
                }
                
                for tpl, keywords in template_keywords.items():
                    if any(kw in query for kw in keywords):
                        template_name = tpl
                        break
                
                # 기본값
                if not template_name:
                    template_name = "product_development_plan"
                    print(f"⚠️ 템플릿을 찾을 수 없어 기본 템플릿 사용: {template_name}")
            
            print(f"📋 템플릿 매핑: {template_name}")
        
        print(f"✅ 작업 유형 결정: {operation_type}, 템플릿: {template_name or 'N/A'}")
        
        return {
            "intent": operation_type,
            "template_name": template_name,  # 템플릿 정보 저장
        }
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "generate")
        return intent
    
    async def generate_document(self, state: OrchestratorState) -> Dict[str, Any]:
        """AI-012: 문서 자동 작성 - 템플릿 로드 → DB 데이터 매핑(Data Binding) → 초안 생성.
        
        프로세스:
        1. 템플릿 로드: 선택된 템플릿 HTML 파일 로드
        2. DB 데이터 매핑: 템플릿 필드에 필요한 데이터를 DB에서 조회 및 매핑
        3. 초안 생성: 매핑된 데이터와 사용자 요구사항을 기반으로 LLM으로 초안 생성
        """
        print("--- [AI-012] Document Generation: 템플릿 로드 → DB 데이터 매핑 → 초안 생성 ---")
        query = state.get("query", "")
        
        # State에서 템플릿명 가져오기 (route_intent에서 이미 매핑됨)
        template_name = state.get("template_name")
        
        # 쿼리에서 제목, 요구사항 추출
        import re
        
        document_title = None
        requirements = None
        
        # "제목: ..." 형식 추출
        title_match = re.search(r'제목[:\s]+([^.]+?)(?:\.|요구사항|$)', query)
        if title_match:
            document_title = title_match.group(1).strip()
        
        # "요구사항: ..." 형식 추출
        req_match = re.search(r'요구사항[:\s]+(.+)', query, re.DOTALL)
        if req_match:
            requirements = req_match.group(1).strip()
        
        print(f"📋 템플릿: {template_name}, 제목: {document_title}")
        print(f"💡 요구사항: {requirements[:100] if requirements else None}...")
        
        # DB 데이터 매핑을 위한 정보 추출
        db_data = await self._fetch_db_data_for_template(template_name, query, requirements)
        
        # doc_generate_agent 호출 (DB 데이터 포함)
        result = doc_generate(
            user_text=query,
            template_name=template_name,
            document_title=document_title,
            requirements=requirements,
            db_data=db_data  # DB 데이터 전달
        )
        
        # 결과를 state에 저장
        document_content = result.get("message", "")
        document_path = result.get("file_path")  # HTML 파일 경로 (이미 저장됨)
        
        # 생성 완료 메시지
        if result.get("ok"):
            print("✅ 문서 생성이 완료되었습니다!")
            print(f"📊 HTML 문서 크기: {len(document_content):,} 문자")
            if document_path:
                print(f"💾 저장된 파일 경로: {document_path}")
        else:
            print(f"❌ 문서 생성 실패: {result.get('error', '알 수 없는 오류')}")
        
        # document_title이 없으면 result에서 가져오기
        if not document_title:
            document_title = result.get("document_title", "문서")
        
        return {
            "document_content": document_content,
            "document_path": document_path,  # HTML 파일 경로
            "document_title": document_title,  # 문서 제목 저장 (결재 판단에 사용)
            "messages": ["문서가 생성되었습니다."],
            # final_response는 결재 필요 여부 확인 후 설정
        }
    
    async def _fetch_db_data_for_template(
        self, 
        template_name: Optional[str], 
        query: str, 
        requirements: Optional[str]
    ) -> Dict[str, Any]:
        """템플릿에 필요한 DB 데이터를 조회합니다 (Data Binding).
        
        템플릿별로 필요한 데이터를 DB에서 조회하여 반환합니다.
        예: 제품개발 계획서 → 제품 정보, 예산 데이터 등
        
        Args:
            template_name: 템플릿 이름
            query: 사용자 쿼리
            requirements: 사용자 요구사항
        
        Returns:
            템플릿에 매핑할 DB 데이터 딕셔너리
        """
        print(f"📊 [DB Data Binding] 템플릿별 데이터 조회: {template_name}")
        
        db_data = {}
        
        try:
            # MariaDB 연결을 위한 설정 확인
            # 실제 구현 시 ai_module의 schedule 도메인과 유사한 방식으로 구현
            # 현재는 기본 구조만 제공
            
            # 템플릿별 필요한 데이터 매핑 규칙
            data_mapping_rules = {
                "product_development_plan": {
                    "tables": ["product_info", "budget_data"],
                    "fields": ["product_name", "budget", "timeline"],
                },
                "purchase_order": {
                    "tables": ["supplier_info", "inventory"],
                    "fields": ["supplier_name", "item_list", "price"],
                },
                "hr_summary_report": {
                    "tables": ["recruitment_data_korean_v3", "employee_info"],
                    "fields": ["applicant_count", "hired_count", "departments"],
                },
                "monthly_profit_analysis_report": {
                    "tables": ["spend_analysis_dataset_converted", "revenue_data"],
                    "fields": ["total_spend", "revenue", "profit"],
                },
                # 추가 템플릿별 매핑 규칙...
            }
            
            if template_name and template_name in data_mapping_rules:
                rules = data_mapping_rules[template_name]
                print(f"  → 테이블: {rules.get('tables', [])}")
                print(f"  → 필드: {rules.get('fields', [])}")
                
                # 실제 DB 조회는 향후 구현
                # 현재는 쿼리와 요구사항에서 데이터 추출 시뮬레이션
                db_data = {
                    "template_name": template_name,
                    "query_context": query,
                    "requirements": requirements,
                    "mapped_fields": rules.get("fields", []),
                }
            else:
                # 기본 데이터 (템플릿별 규칙이 없는 경우)
                db_data = {
                    "template_name": template_name or "unknown",
                    "query_context": query,
                    "requirements": requirements,
                }
            
            print(f"✅ DB 데이터 매핑 완료: {len(db_data)} 필드")
            
        except Exception as e:
            print(f"⚠️ DB 데이터 조회 실패: {e}")
            # 에러 시 빈 데이터 반환
            db_data = {
                "template_name": template_name,
                "query_context": query,
                "error": str(e),
            }
        
        return db_data
    
    def should_check_approval(self, state: OrchestratorState) -> str:
        """결재 필요 여부 확인이 필요한지 결정."""
        # document_content가 있으면 결재 필요 여부 확인
        if state.get("document_content"):
            return "check"
        return "skip"
    
    async def check_approval_required(self, state: OrchestratorState) -> Dict[str, Any]:
        """문서의 중요도를 판단하여 결재 필요 여부를 결정합니다 (LLM 기반).
        
        결재가 필요한 경우:
        - 예산이 큰 문서 (예: 1억원 이상)
        - 법적 구속력이 있는 문서 (계약서, 발주서 등)
        - 중요한 정책 변경 문서
        - 외부 공개 문서
        """
        print("--- [Document Master] Checking if Approval Required ---")
        query = state.get("query", "")
        document_content = state.get("document_content", "")
        document_title = state.get("document_title", "")
        
        # LLM을 사용하여 문서의 중요도 및 결재 필요 여부 판단
        try:
            from langchain_openai import ChatOpenAI
            import json
            import re
            
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")
            
            llm = ChatOpenAI(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                temperature=0.3,
                api_key=api_key
            )
            
            approval_prompt = f"""다음 문서의 중요도를 판단하여 결재가 필요한지 결정해주세요.

문서 제목: {document_title}
사용자 요구사항: {query[:1000]}

결재가 필요한 경우:
- 예산이 큰 문서 (1억원 이상, 큰 금액)
- 계약서, 발주서, 계약 갱신 보고서 등 법적 구속력이 있는 문서
- 중요한 정책 변경 문서
- 외부 공개 문서
- 회사 전체에 영향을 미치는 문서

결재가 불필요한 경우:
- 내부 보고서
- 일상적인 업무 문서
- 개인 작업 문서
- 간단한 요약 리포트

JSON 형식으로 응답하세요:
{{"approval_required": true 또는 false, "reason": "판단 사유"}}"""
            
            response = await asyncio.to_thread(llm.invoke, approval_prompt)
            content = response.content.strip()
            
            # JSON 파싱
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    is_approval_required = result.get("approval_required", False)
                    approval_reason = result.get("reason", "LLM 판단")
                except json.JSONDecodeError:
                    # JSON 파싱 실패 시 텍스트 기반 판단
                    is_approval_required = "required" in content.lower() or "true" in content.lower() or "필요" in content
                    approval_reason = "LLM 응답 파싱 실패 (텍스트 기반 판단)"
            else:
                # 키워드 기반 fallback 판단
                approval_keywords = ["계약", "발주", "예산", "억원", "법적", "공개", "정책", "규정", "결재"]
                is_approval_required = any(keyword in query for keyword in approval_keywords)
                approval_reason = "키워드 기반 판단"
            
            print(f"📋 결재 필요 여부 판단: {'필요' if is_approval_required else '불필요'} ({approval_reason})")
            
            return {
                "approval_required": is_approval_required,
                "approval_reason": approval_reason,
                "messages": [f"결재 필요 여부: {'필요' if is_approval_required else '불필요'}"],
            }
            
        except Exception as e:
            print(f"⚠️ 결재 필요 여부 판단 실패: {e}")
            import traceback
            traceback.print_exc()
            
            # 키워드 기반 fallback 판단
            approval_keywords = ["계약", "발주", "예산", "억원", "법적", "공개", "정책"]
            is_approval_required = any(keyword in query for keyword in approval_keywords)
            approval_reason = f"에러 발생, 키워드 기반 판단: {'필요' if is_approval_required else '불필요'}"
            
            return {
                "approval_required": is_approval_required,
                "approval_reason": approval_reason,
                "messages": ["결재 필요 여부 판단에 실패했습니다. 키워드 기반으로 판단합니다."],
            }
    
    def route_by_approval_status(self, state: OrchestratorState) -> str:
        """결재 필요 여부에 따라 라우팅."""
        approval_required = state.get("approval_required", False)
        return "required" if approval_required else "not_required"
    
    async def request_user_confirmation(self, state: OrchestratorState) -> Dict[str, Any]:
        """사용자에게 결재 확인을 요청합니다 (휴먼인더루프).
        
        실제 운영 환경에서는:
        - 웹 인터페이스에서 사용자에게 알림 전송
        - 결재 시스템 API 호출
        - 이메일 또는 메신저 알림
        
        현재는 시뮬레이션: 사용자 입력을 기다리거나 자동 승인
        """
        print("--- [Document Master] Requesting User Confirmation ---")
        document_title = state.get("document_title", "문서")
        approval_reason = state.get("approval_reason", "중요한 문서")
        
        # 실제 환경에서는 여기서 사용자 입력을 기다림
        # 현재는 자동 승인 시뮬레이션 (실제로는 사용자 입력 필요)
        
        # State에 사용자 확인 대기 상태 저장
        # 실제 구현에서는 외부 시스템(웹, 메신저 등)을 통해 사용자에게 알림
        confirmation_message = (
            f"📋 결재 확인 요청\n\n"
            f"문서: {document_title}\n"
            f"사유: {approval_reason}\n"
            f"\n이 문서는 결재가 필요한 문서로 판단되었습니다.\n"
            f"PDF 변환 및 결재 프로세스를 진행하시겠습니까?\n"
        )
        
        print(confirmation_message)
        
        # 시뮬레이션: 자동 승인 (실제로는 사용자 입력 필요)
        # TODO: 실제 환경에서는 사용자 입력을 받아야 함
        user_approved = True  # 기본값: 자동 승인
        
        # State에서 사용자 응답 확인 (있다면)
        user_response = state.get("user_approval_response")
        if user_response is not None:
            user_approved = user_response in [True, "yes", "yes", "승인", "approved", "ok"]
        elif state.get("auto_approve", False):
            user_approved = True
        else:
            # 실제로는 여기서 사용자 입력을 기다려야 함
            # 현재는 기본값 사용
            user_approved = True
        
        if user_approved:
            print("✅ 사용자 승인 완료 - PDF 변환 및 결재 프로세스 진행")
            return {
                "user_approved": True,
                "messages": ["사용자가 결재를 승인했습니다."],
            }
        else:
            print("❌ 사용자가 결재를 거부했습니다 - 프로세스 종료")
            return {
                "user_approved": False,
                "final_response": "사용자가 결재를 거부하여 문서 생성 프로세스가 중단되었습니다.",
                "messages": ["결재가 거부되었습니다."],
            }
    
    def handle_user_confirmation(self, state: OrchestratorState) -> str:
        """사용자 확인 결과에 따라 라우팅."""
        user_approved = state.get("user_approved", False)
        return "approved" if user_approved else "rejected"
    
    async def convert_to_pdf(self, state: OrchestratorState) -> Dict[str, Any]:
        """PDF 변환 (AI-013)."""
        print("--- [Document Master] Converting to PDF ---")
        document_content = state.get("document_content", "")
        html_file_path = state.get("document_path")  # 이미 저장된 HTML 파일 경로
        
        # PDF 변환 로직은 향후 구현 예정
        # 현재는 HTML 파일만 저장되고 있음
        if html_file_path:
            print(f"📁 HTML 파일이 저장되었습니다: {html_file_path}")
            print("💡 PDF 변환 기능은 향후 구현 예정입니다.")
        
        return {
            "document_path": html_file_path,  # HTML 파일 경로 반환
            "messages": ["HTML 파일이 저장되었습니다. PDF 변환은 향후 구현 예정입니다."],
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
        document_title = state.get("document_title", "문서")
        approval_reason = state.get("approval_reason", "중요한 문서")
        
        # 결재 요청 로직 (실제 구현 필요)
        # 실제 환경에서는:
        # - 결재 시스템 API 호출
        # - 결재자에게 알림 전송
        # - 결재 워크플로우 시작
        
        approval_message = (
            f"✅ 결재 요청 완료\n\n"
            f"📄 문서: {document_title}\n"
            f"📁 파일: {document_path}\n"
            f"📋 사유: {approval_reason}\n"
            f"\n문서가 생성되었고 결재 프로세스에 등록되었습니다."
        )
        
        return {
            "final_response": approval_message,
            "messages": ["결재 요청이 완료되었습니다."],
        }
    
    async def upload_knowledge(self, state: OrchestratorState) -> Dict[str, Any]:
        """AI-013: 문서 업로드 (RAG) - 파일 파싱(Text Extraction) → 청킹(Chunking) → 임베딩(Embedding) → Vector DB(Milvus) Upsert.
        
        프로세스:
        1. 파일 파싱: PDF/DOCX 등의 파일에서 텍스트 추출
        2. 청킹: 추출된 텍스트를 의미 있는 단위로 분할
        3. 임베딩: 각 청크를 벡터로 변환
        4. Vector DB Upsert: 임베딩 벡터를 Milvus에 저장/업데이트
        """
        print("--- [AI-013] Document Upload (RAG): 파일 파싱 → 청킹 → 임베딩 → Vector DB Upsert ---")
        query = state.get("query", "")
        
        # document_upload_service는 lazy import (sentence_transformers 의존성 때문)
        from agents.master_graphs.document.document_upload_service import (
            extract_text_from_file,
            chunk_text,
            get_embedding_model,
            upsert_to_milvus
        )
        from pathlib import Path
        from datetime import datetime
        
        # 파일 경로가 state에 있으면 사용
        file_path = state.get("document_path")
        
        if not file_path:
            # 파일 경로가 없으면 에러
            return {
                "ok": False,
                "message": "업로드할 파일 경로가 필요합니다.",
                "error": "file_path is required",
            }
        
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return {
                "ok": False,
                "message": f"파일을 찾을 수 없습니다: {file_path}",
                "error": "file_not_found",
            }
        
        try:
            # 1단계: 파일 파싱 (Text Extraction)
            print(f"📄 [1/4] 파일 파싱: {file_path_obj.name}")
            text_content = extract_text_from_file(file_path_obj)
            print(f"  → 추출된 텍스트 길이: {len(text_content):,} 문자")
            
            # 2단계: 청킹 (Chunking)
            print(f"✂️ [2/4] 텍스트 청킹...")
            chunks = chunk_text(text_content, chunk_size=500, chunk_overlap=100)
            print(f"  → 생성된 청크 수: {len(chunks)}개")
            
            # 3단계: 임베딩 (Embedding)
            print(f"🔢 [3/4] 임베딩 생성...")
            embedding_model = get_embedding_model()
            embeddings = []
            for i, chunk in enumerate(chunks):
                embedding = embedding_model.encode(chunk)
                embeddings.append(embedding.tolist())
                if (i + 1) % 10 == 0:
                    print(f"  → 진행: {i + 1}/{len(chunks)} 청크 임베딩 완료")
            print(f"  → 임베딩 완료: {len(embeddings)}개 벡터")
            
            # 4단계: Vector DB Upsert (Milvus)
            print(f"💾 [4/4] Milvus에 업로드...")
            result = upsert_to_milvus(
                file_path=str(file_path),
                chunks=chunks,
                embeddings=embeddings,
                metadata={"source": file_path_obj.name, "uploaded_at": datetime.now().isoformat()}
            )
            
            if result.get("ok"):
                print(f"✅ 문서 업로드 완료: {result.get('message', '')}")
                return {
                    "final_response": result.get("message", "문서가 지식 베이스에 업로드되었습니다."),
                    "messages": [f"문서 업로드 완료: {len(chunks)}개 청크, {len(embeddings)}개 벡터 저장됨"],
                }
            else:
                return {
                    "final_response": f"문서 업로드 실패: {result.get('error', '알 수 없는 오류')}",
                    "messages": ["문서 업로드에 실패했습니다."],
                }
                
        except Exception as e:
            print(f"❌ 문서 업로드 중 오류 발생: {e}")
            import traceback
            traceback.print_exc()
            return {
                "ok": False,
                "final_response": f"문서 업로드 중 오류가 발생했습니다: {str(e)}",
                "messages": [f"오류: {str(e)}"],
            }


def get_document_master_graph() -> StateGraph:
    """문서 마스터그래프 인스턴스 반환."""
    master = DocumentMasterGraph()
    return master.get_graph()

