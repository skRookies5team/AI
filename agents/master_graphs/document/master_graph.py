"""문서 마스터그래프 - 문서 작성/업로드/변환 (AI-011~AI-015)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
import asyncio
from datetime import datetime

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
            }
        )
        
        # 문서 생성 후 바로 종료
        workflow.add_edge("generate_document", END)
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
            # 쿼리에서 템플릿명 추출 (언더스코어 포함한 전체 템플릿명 추출)
            # 예: "양식: work_performance_review" -> "work_performance_review"
            # "양식: work_performance_review, 제목: ..." 형태에서 추출
            template_match = re.search(r'양식[:\s]+([a-zA-Z0-9_]+)|템플릿[:\s]+([a-zA-Z0-9_]+)|template[:\s]+([a-zA-Z0-9_]+)', query, re.IGNORECASE)
            if template_match:
                template_name = template_match.group(1) or template_match.group(2) or template_match.group(3)
                if template_name:
                    print(f"✅ [route_intent] 양식에서 템플릿명 추출: {template_name}")
            else:
                # 양식 패턴이 없으면 키워드 기반 매핑으로 진행
                # 기본 템플릿 매핑 (키워드 기반)
                # 중요: 더 구체적인 키워드가 먼저 매칭되도록 순서 조정
                template_keywords = [
                    # 구체적인 키워드 우선 (순서 중요!)
                    ("work_performance_review", ["업무 성과 평가", "근무 평가", "성과 평가서", "업무 평가서", "업무 성과", "성과 평가"]),
                    # 제품 비교표는 제품개발보다 먼저 체크 (더 구체적인 키워드)
                    ("product_comparison_table", ["제품 비교표", "제품 비교", "제품성능 비교", "성능 비교", "비교표", "제품비교", "비교 대상", "비교 목적"]),
                    ("product_development_plan", ["제품개발", "신제품", "개발계획", "제품개발 계획서"]),
                    ("purchase_order", ["발주", "구매", "주문", "발주서"]),
                    ("monthly_profit_analysis_report", ["수익", "매출", "분석", "수익 분석", "수익 분석 리포트"]),
                    ("supplier_evaluation_form", ["공급업체", "협력사", "공급업체 평가"]),
                    ("contract_renewal_report", ["계약", "갱신", "연장", "계약 갱신"]),
                    ("activity_based_costing", ["원가", "비용", "계산", "활동기준 원가"]),
                    ("project_schedule", ["프로젝트", "일정", "스케줄", "프로젝트 일정"]),
                    # 일반적인 키워드는 나중에 (hr_summary_report는 "인사정보 리포트" 같은 구체적 키워드가 있을 때만)
                    ("hr_summary_report", ["인사정보 리포트", "인사정보", "인력 리포트"]),
                ]
                
                for tpl, keywords in template_keywords:
                    if any(kw in query for kw in keywords):
                        template_name = tpl
                        matched_keyword = [kw for kw in keywords if kw in query][0] if any(kw in query for kw in keywords) else None
                        print(f"✅ [route_intent] 키워드 매칭: '{tpl}' (매칭 키워드: {matched_keyword}, 쿼리: {query[:50]})")
                        break
                
                # 기본값
                if not template_name:
                    template_name = "product_development_plan"
                    print(f"⚠️ 템플릿을 찾을 수 없어 기본 템플릿 사용: {template_name}")
            
            print(f"📋 [route_intent] 템플릿 매핑 완료: {template_name}")
        
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
        # datetime을 전역 변수로 명시적으로 참조
        from datetime import datetime as dt_now
        
        query = state.get("query", "")
        orchestrator_state = state  # orchestrator_state는 state와 동일
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("document_context") is None:
            orchestrator_state["document_context"] = {}
        
        # State에서 템플릿명 가져오기 (route_intent에서 이미 매핑됨)
        template_name = state.get("template_name")
        
        print(f"🔍 [generate_document] State에서 템플릿 확인: {template_name}")
        print(f"🔍 [generate_document] State keys: {list(state.keys())}")
        
        # 현재 요청의 템플릿이 있으면 우선 사용 (이전 컨텍스트보다 우선)
        if template_name:
            print(f"✅ 현재 요청의 템플릿 사용: {template_name}")
        else:
            # 이전 컨텍스트에서 템플릿명 복원 (없는 경우)
            template_name = orchestrator_state.get("document_context", {}).get("last_template_type")
            if template_name:
                print(f"⚠️ 이전 컨텍스트에서 템플릿 복원: {template_name} (현재 요청에 템플릿 정보 없음)")
            else:
                # 쿼리에서 직접 템플릿 추출 시도 (fallback)
                # 중요: 더 구체적인 키워드가 먼저 매칭되도록 순서 조정
                # "업무 성과 평가"가 "인사"보다 우선되어야 함
                template_keywords = [
                    # 구체적인 키워드 우선 (순서 중요!)
                    ("work_performance_review", ["업무 성과 평가", "근무 평가", "성과 평가서", "업무 평가서", "성과", "평가서"]),
                    ("product_development_plan", ["제품개발", "신제품", "개발계획", "제품개발 계획서"]),
                    ("purchase_order", ["발주", "구매", "주문", "발주서"]),
                    ("monthly_profit_analysis_report", ["수익", "매출", "분석", "수익 분석 리포트"]),
                    ("supplier_evaluation_form", ["공급업체", "협력사", "공급업체 평가"]),
                    ("contract_renewal_report", ["계약", "갱신", "연장", "계약 갱신"]),
                    ("activity_based_costing", ["원가", "비용", "계산", "활동기준 원가"]),
                    ("project_schedule", ["프로젝트", "일정", "스케줄", "프로젝트 일정"]),
                    ("product_comparison_table", ["비교", "제품비교", "제품 비교표"]),
                    # 일반적인 키워드는 나중에 (hr_summary_report는 "인사정보 리포트" 같은 구체적 키워드가 있을 때만)
                    ("hr_summary_report", ["인사정보 리포트", "인사정보", "인력 리포트"]),
                ]
                
                for tpl, keywords in template_keywords:
                    if any(kw in query for kw in keywords):
                        template_name = tpl
                        print(f"✅ 쿼리에서 직접 템플릿 추출: {template_name} (매칭 키워드: {[kw for kw in keywords if kw in query][:1]})")
                        break
        
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
        
        # Human-in-the-loop: 제목과 요구사항이 없으면 예시문 반환
        if not template_name:
            return {
                "final_response": "❌ 문서 양식을 선택해주세요. 예: '제품개발 계획서 작성', '발주서 작성' 등",
                "conversation_history": []
            }
        
        # 제목과 요구사항이 모두 없으면 예시문 반환 (모든 템플릿에 적용)
        if not document_title and not requirements:
            from agents.master_graphs.document.template_examples import get_template_example, get_template_title
            
            example_text = get_template_example(template_name)
            template_title = get_template_title(template_name) or template_name
            
            if example_text:
                response_message = example_text  # 예시문에 이미 안내 메시지가 포함되어 있음
                print(f"📋 {template_title} 작성 예시문 반환 (Human-in-the-loop)")
            else:
                response_message = f"📋 {template_title} 작성을 시작하시겠어요?\n\n다음 형식으로 제목과 요구사항을 입력해주세요:\n\n```\n제목: [문서 제목]\n요구사항: [상세 요구사항]\n```"
            
            from agents.state import Message
            
            return {
                "final_response": response_message,
                "template_name": template_name,  # 템플릿 정보 유지
                "requires_input": True,  # 사용자 입력 필요 플래그
                "conversation_history": [{
                    "role": "assistant",
                    "content": response_message,
                    "timestamp": dt_now.now().isoformat()
                }]
            }
        
        # DB 데이터 매핑을 위한 정보 추출
        db_data = await self._fetch_db_data_for_template(template_name, query, requirements)
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("document_context") is None:
            orchestrator_state["document_context"] = {}
        
        # 대화 히스토리 가져오기
        conversation_history = state.get("conversation_history", [])
        
        # doc_generate_agent 호출 (DB 데이터 및 Redis 컨텍스트 포함)
        result = doc_generate(
            user_text=query,
            template_name=template_name,
            document_title=document_title,
            requirements=requirements,
            db_data=db_data,  # DB 데이터 전달
            orchestrator_state=orchestrator_state,  # Redis 컨텍스트 전달
            conversation_history=conversation_history  # 대화 히스토리 전달
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
        
        # Redis 컨텍스트 업데이트 (doc_generate에서 이미 저장했지만 확실히 하기 위해)
        if result.get("document_context"):
            orchestrator_state["document_context"] = result["document_context"]
        
        # 생성된 HTML도 document_context에 저장 (이미 doc_generate_agent에서 저장했지만 확실히 하기 위해)
        if result.get("ok") and document_content:
            if orchestrator_state.get("document_context") is None:
                orchestrator_state["document_context"] = {}
            orchestrator_state["document_context"]["last_generated_html"] = document_content
        
        # final_response 설정 (문서 내용을 우선 표시)
        if result.get("ok") and document_content:
            final_response = document_content
            # conversation_history에 assistant 메시지 추가
            from agents.state import Message
            assistant_message: Message = {
                "role": "assistant",
                "content": document_content,
                "timestamp": datetime.now().isoformat()
            }
            conversation_history = state.get("conversation_history", [])
            conversation_history.append(assistant_message)
        else:
            final_response = result.get("message", "문서 생성에 실패했습니다.")
            conversation_history = state.get("conversation_history", [])
        
        return {
            "document_content": document_content,
            "document_path": document_path,  # HTML 파일 경로
            "document_title": document_title,  # 문서 제목 저장
            "messages": ["문서가 생성되었습니다."],
            "final_response": final_response,  # 생성된 HTML 문서 내용을 final_response에 설정
            "conversation_history": conversation_history,  # 업데이트된 대화 히스토리
            "document_context": orchestrator_state["document_context"],  # Redis 컨텍스트 반환 (HTML 포함)
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
    
    async def upload_knowledge(self, state: OrchestratorState) -> Dict[str, Any]:
        """AI-013: 문서 업로드 (RAG) - 파일 파싱(Text Extraction) → 청킹(Chunking) → 임베딩(Embedding) → Vector DB(Milvus) Upsert.
        
        프로세스:
        1. 파일 파싱: PDF/DOCX 등의 파일에서 텍스트 추출
        2. 청킹: 추출된 텍스트를 의미 있는 단위로 분할
        3. 임베딩: 각 청크를 벡터로 변환
        4. Vector DB Upsert: 임베딩 벡터를 Milvus에 저장/업데이트
        
        Redis 기반 컨텍스트 관리:
        - 업로드한 파일 경로, 청크 수, 임베딩 수 등을 Redis에 저장
        - 이전 업로드 내역을 기억하여 멀티턴 대화 지원
        """
        print("--- [AI-013] Document Upload (RAG): 파일 파싱 → 청킹 → 임베딩 → Vector DB Upsert ---")
        query = state.get("query", "")
        orchestrator_state = state
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("document_context") is None:
            orchestrator_state["document_context"] = {}
        
        # document_upload_service는 lazy import (sentence_transformers 의존성 때문)
        from agents.master_graphs.document.document_upload_service import (
            extract_text_from_file,
            chunk_text,
            get_embedding_model,
            upsert_to_milvus
        )
        from pathlib import Path
        
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
                
                # Redis 컨텍스트에 저장
                upload_info = {
                    "file_path": str(file_path),
                    "chunks_count": len(chunks),
                    "embeddings_count": len(embeddings),
                    "uploaded_at": datetime.now().isoformat(),
                }
                orchestrator_state["document_context"]["last_upload"] = upload_info
                
                return {
                    "final_response": result.get("message", "문서가 지식 베이스에 업로드되었습니다."),
                    "messages": [f"문서 업로드 완료: {len(chunks)}개 청크, {len(embeddings)}개 벡터 저장됨"],
                    "document_context": orchestrator_state["document_context"],
                }
            else:
                return {
                    "final_response": f"문서 업로드 실패: {result.get('error', '알 수 없는 오류')}",
                    "messages": ["문서 업로드에 실패했습니다."],
                    "document_context": orchestrator_state["document_context"],
                }
                
        except Exception as e:
            print(f"❌ 문서 업로드 중 오류 발생: {e}")
            import traceback
            traceback.print_exc()
            return {
                "ok": False,
                "final_response": f"문서 업로드 중 오류가 발생했습니다: {str(e)}",
                "messages": [f"오류: {str(e)}"],
                "document_context": orchestrator_state.get("document_context", {}),
            }


def get_document_master_graph() -> StateGraph:
    """문서 마스터그래프 인스턴스 반환."""
    master = DocumentMasterGraph()
    return master.get_graph()

