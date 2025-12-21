"""문서 마스터그래프 - 문서 작성/업로드/변환 (AI-011~AI-015)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
import asyncio
import time
from datetime import datetime

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.document.generate.doc_generate_agent import doc_generate

# LLM 관련 optional import
try:
    from langchain_openai import ChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    ChatOpenAI = None
# sentence_transformers 의존성 때문에 모듈 레벨 import 시 충돌 발생 가능


class DocumentMasterGraph(BaseMasterGraph):
    """문서 도메인 마스터그래프.
    
    요구사항:
    - AI-011: 문서 라우팅 - 문서 작업 유형(작성/업데이트/변환) 분류 → 템플릿 매핑
    - AI-012: 문서 자동 작성 - 템플릿 로드 → DB 데이터 매핑(Data Binding) → 초안 생성
    - AI-014: 문서 업데이트 - 복리후생/식단/메뉴얼 파일 업데이트 (파일 파싱 → 청킹 → 임베딩 → Vector DB(Milvus) Upsert)
    """
    
    def get_domain_name(self) -> str:
        return "document"
    
    def get_graph(self) -> StateGraph:
        """문서 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)  # AI-011: 문서 라우팅 (작업 유형 분류 + 템플릿 매핑)
        workflow.add_node("generate_document", self.generate_document)  # AI-012: 문서 작성 (템플릿 로드 → DB 데이터 매핑 → 초안 생성)
        workflow.add_node("update_document", self.update_document)  # AI-014: 문서 업데이트 (복리후생/식단/메뉴얼 파일 업데이트)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # AI-011: 문서 작업 유형 분류 및 템플릿 매핑
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "generate": "generate_document",  # 문서 작성
                "update": "update_document",  # 문서 업데이트 (복리후생/식단/메뉴얼)
            }
        )
        
        # 문서 생성 후 바로 종료
        workflow.add_edge("generate_document", END)
        workflow.add_edge("update_document", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """AI-011: 문서 라우팅 - 문서 작업 유형(작성/업데이트/변환) 분류 → 템플릿 매핑.
        
        작업 유형:
        - generate: 문서 작성 (템플릿 기반 문서 생성)
        - update: 문서 업데이트 (복리후생/식단/메뉴얼 파일 업데이트)
        - convert: 문서 변환 (PDF 등 형식 변환)
        """
        print("--- [AI-011] Document Routing: 작업 유형 분류 및 템플릿 매핑 ---")
        step_start = time.time()
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # 1단계: 작업 유형 분류 (작성/업데이트/변환)
        import re
        
        # 키워드 기반 작업 유형 판단
        if any(k in query for k in ["업데이트", "수정", "갱신", "변경", "update"]):
            operation_type = "update"
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
            elif any(k in llm_intent for k in ["update", "업데이트", "수정"]):
                operation_type = "update"
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
                    ("vacation_request_form", ["휴가 신청서", "휴가 신청", "연차 신청", "반차 신청", "휴가", "연차", "반차", "병가", "경조사"]),
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
                
                # 템플릿명이 없으면 None으로 유지 (generate_document에서 문서 목록 표시)
                if not template_name:
                    print(f"ℹ️ 템플릿을 찾을 수 없음 - 문서 목록 표시 예정")
            
            print(f"📋 [route_intent] 템플릿 매핑 완료: {template_name}")
        
        step_elapsed = (time.time() - step_start) * 1000
        print(f"✅ 작업 유형 결정: {operation_type}, 템플릿: {template_name or 'N/A'} ({step_elapsed/1000:.2f}초)")
        
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
        
        워크플로우 모드 지원:
        - workflow_temp_data에서 출장 정보 가져오기
        - 출장 사유(description)가 없으면 재요청
        - 문서 작성 후 결재 진행 여부 확인 (휴먼 인 더 루프)
        """
        print("--- [AI-012] Document Generation: 템플릿 로드 → DB 데이터 매핑 → 초안 생성 ---")
        # datetime을 전역 변수로 명시적으로 참조
        from datetime import datetime as dt_now
        
        query = state.get("query", "")
        orchestrator_state = state  # orchestrator_state는 state와 동일
        
        # 워크플로우 모드 확인
        workflow_in_progress = state.get("workflow_in_progress", False)
        workflow_type = state.get("workflow_type")
        workflow_temp_data = state.get("workflow_temp_data") or {}
        
        # 워크플로우 모드이고 business_trip 워크플로우인 경우
        if workflow_in_progress and workflow_type == "business_trip":
            # 출장 사유 확인
            description = workflow_temp_data.get("description") or query.strip()
            
            # 출장 사유가 없거나 너무 짧으면 재요청
            if not description or len(description.strip()) < 3:
                buttons_html = """
<div style="margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap;">
    <button onclick="sendMessageFromButton('종료')" style="padding: 10px 20px; background: #dc3545; color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(220, 53, 69, 0.3);">
        ❌ 종료
    </button>
</div>
"""
                question = "출장 사유를 알려주세요.\n예: 거래처 미팅, 워크샵, 고객 방문 등" + buttons_html
                return {
                    "final_response": question,
                    "messages": [question],
                    "workflow_temp_data": workflow_temp_data,
                    "document_context": orchestrator_state.get("document_context", {}),
                    "requires_input": True,
                }
            
            # workflow_temp_data에 description 저장
            workflow_temp_data["description"] = description
            
            # 템플릿명 설정 (출장 신청서)
            template_name = "business_trip_request_form"
            
            # workflow_temp_data에서 정보 가져오기
            start_time_str = workflow_temp_data.get("start_time")
            end_time_str = workflow_temp_data.get("end_time")
            title = workflow_temp_data.get("title", "출장")
            destination = workflow_temp_data.get("destination", "")
            
            # 요구사항 구성
            requirements = f"출장 기간: {start_time_str} ~ {end_time_str}\n"
            if destination:
                requirements += f"목적지: {destination}\n"
            requirements += f"출장 사유: {description}\n"
            requirements += f"제목: {title}"
            
            document_title = title
            
            print(f"✅ [워크플로우 모드] 출장 신청서 작성: {title}")
            print(f"   기간: {start_time_str} ~ {end_time_str}")
            print(f"   사유: {description}")
        else:
            # 일반 모드 (기존 로직)
            # Redis 기반 컨텍스트 초기화
            if orchestrator_state.get("document_context") is None:
                orchestrator_state["document_context"] = {}
            
            # State에서 템플릿명 가져오기 (route_intent에서 이미 매핑됨)
            template_name = state.get("template_name")
            
            document_title = None
            requirements = None
        
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
                    ("vacation_request_form", ["휴가 신청서", "휴가 신청", "연차 신청", "반차 신청", "휴가", "연차", "반차", "병가", "경조사"]),
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
        
        # Human-in-the-loop: 템플릿명이 없으면 작성 가능한 문서 목록 반환
        if not template_name:
            from agents.master_graphs.document.generate.template_examples import get_available_templates
            
            available_templates = get_available_templates()
            
            if not available_templates:
                return {
                    "final_response": "❌ 작성 가능한 문서 템플릿을 찾을 수 없습니다.",
                    "conversation_history": []
                }
            
            # 문서 목록을 HTML 형식으로 생성 (클릭 가능한 버튼 형태)
            template_list_html = """<div style="font-family: 'Segoe UI', 'Malgun Gothic', sans-serif; line-height: 1.6;">
    <div style="font-size: 20px; font-weight: bold; color: #2c3e50; margin-bottom: 20px;">
        📋 <strong>작성 가능한 문서 목록</strong>
    </div>
    
    <div style="color: #495057; margin-bottom: 25px;">
        아래 문서 중 하나를 선택하여 작성하실 수 있습니다. 원하는 문서를 클릭하거나 문서 이름을 입력해주세요.
    </div>
    
    <div style="display: flex; flex-direction: column; gap: 12px; margin-bottom: 20px;">"""
            
            # 각 템플릿을 버튼 형태로 추가
            for idx, (tpl_name, tpl_title) in enumerate(available_templates.items(), 1):
                template_list_html += f"""
        <div style="background: #f8f9fa; border: 2px solid #dee2e6; border-radius: 8px; padding: 15px; cursor: pointer; transition: all 0.2s;"
             onmouseover="this.style.background='#e9ecef'; this.style.borderColor='#667eea';"
             onmouseout="this.style.background='#f8f9fa'; this.style.borderColor='#dee2e6';">
            <div style="display: flex; align-items: center; gap: 12px;">
                <span style="font-size: 18px; font-weight: bold; color: #667eea; min-width: 30px;">{idx}.</span>
                <div style="flex: 1;">
                    <div style="font-size: 16px; font-weight: 600; color: #2c3e50; margin-bottom: 4px;">
                        {tpl_title}
                    </div>
                    <div style="font-size: 13px; color: #6c757d;">
                        "{tpl_title} 작성" 또는 "{tpl_title} 만들어줘"라고 입력하세요
                    </div>
                </div>
            </div>
        </div>"""
            
            template_list_html += """
    </div>
    
    <div style="background: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 6px; padding: 12px; margin-top: 20px; color: #004085;">
        💡 <strong>사용 방법:</strong> 위 문서 중 하나의 이름을 입력하시면 해당 문서 작성을 시작할 수 있습니다.
        <br>예: "제품개발 계획서 작성", "발주서 만들어줘", "업무 성과 평가서 작성" 등
    </div>
</div>"""
            
            from agents.state import Message
            
            return {
                "final_response": template_list_html,
                "requires_input": True,  # 사용자 입력 필요 플래그
                "conversation_history": [{
                    "role": "assistant",
                    "content": template_list_html,
                    "timestamp": dt_now.now().isoformat()
                }]
            }
        
        # 제목과 요구사항이 모두 없으면 예시문 반환 (모든 템플릿에 적용)
        if not document_title and not requirements:
            from agents.master_graphs.document.generate.template_examples import get_template_example, get_template_title
            
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
            
            # 워크플로우 모드: 문서 작성 후 결재 진행 여부 확인
            if workflow_in_progress and workflow_type == "business_trip":
                # workflow_temp_data에 description 저장 (이미 저장되어 있지만 확실히)
                if workflow_temp_data.get("description"):
                    # Step 2 완료 메시지 추가
                    step2_complete_msg = "\n\n✅ [Step 2 완료] 출장 신청서가 생성되었습니다."
                    # 결재 진행 여부 확인 메시지 추가
                    buttons_html = """
<div style="margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap;">
    <button onclick="sendMessageFromButton('진행')" style="padding: 10px 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(102, 126, 234, 0.3);">
        ✅ 진행
    </button>
    <button onclick="sendMessageFromButton('취소')" style="padding: 10px 20px; background: #e0e0e0; color: #333; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);">
        ❌ 취소
    </button>
</div>
"""
                    approval_message = "\n\n결재를 진행하시겠습니까?" + buttons_html
                    final_response = document_content + step2_complete_msg + approval_message
                    # workflow_temp_data 업데이트
                    workflow_temp_data["description"] = workflow_temp_data.get("description")
            
            # conversation_history에 assistant 메시지 추가
            from agents.state import Message
            assistant_message: Message = {
                "role": "assistant",
                "content": final_response,
                "timestamp": datetime.now().isoformat()
            }
            conversation_history = state.get("conversation_history", [])
            conversation_history.append(assistant_message)
        else:
            final_response = result.get("message", "문서 생성에 실패했습니다.")
            conversation_history = state.get("conversation_history", [])
        
        return_result = {
            "document_content": document_content,
            "document_path": document_path,  # HTML 파일 경로
            "document_title": document_title,  # 문서 제목 저장
            "messages": ["문서가 생성되었습니다."],
            "final_response": final_response,  # 생성된 HTML 문서 내용을 final_response에 설정
            "conversation_history": conversation_history,  # 업데이트된 대화 히스토리
            "document_context": orchestrator_state["document_context"],  # Redis 컨텍스트 반환 (HTML 포함)
        }
        
        # 워크플로우 모드: workflow_temp_data 반환
        if workflow_in_progress and workflow_type == "business_trip":
            return_result["workflow_temp_data"] = workflow_temp_data
            return_result["requires_input"] = True  # 결재 진행 여부 확인을 위한 플래그
        
        return return_result
    
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
    
    async def update_document(self, state: OrchestratorState) -> Dict[str, Any]:
        """AI-014: 문서 업데이트 - 복리후생/식단/메뉴얼 파일 업데이트.
        
        프로세스:
        1. 파일 파싱: PDF/DOCX 등의 파일에서 텍스트 추출
        2. 청킹: 추출된 텍스트를 의미 있는 단위로 분할
        3. 임베딩: 각 청크를 벡터로 변환
        4. Vector DB Upsert: 임베딩 벡터를 Milvus에 저장/업데이트
        
        업데이트 가능한 문서:
        - 복리후생
        - 식단
        - 메뉴얼
        
        Redis 기반 컨텍스트 관리:
        - 업데이트한 파일 경로, 청크 수, 임베딩 수 등을 Redis에 저장
        - 이전 업데이트 내역을 기억하여 멀티턴 대화 지원
        """
        print("--- [AI-014] Document Update: 파일 파싱 → 청킹 → 임베딩 → Vector DB Upsert ---")
        query = state.get("query", "")
        orchestrator_state = state
        
        # Redis 기반 컨텍스트 초기화
        if orchestrator_state.get("document_context") is None:
            orchestrator_state["document_context"] = {}
        
        # Structured Output Parser를 사용하여 쿼리에서 파라미터 추출
        from agents.structured_output import extract_structured_output, DocumentUpdateParams
        from agents.master_graphs.document.update.document_update_service import (
            process_update_file,
            get_updatable_documents,
        )
        from agents.master_graphs.document.document_utils import get_update_document_prompt
        from pathlib import Path
        
        # 문서 타입 초기화
        document_type = None
        
        try:
            # agent_prompts.yaml에서 프롬프트 로드
            system_prompt = get_update_document_prompt()
            
            params = await extract_structured_output(
                query=query,
                model_class=DocumentUpdateParams,
                system_prompt=system_prompt,
                temperature=0.0
            )
            
            document_title = params.document_title
            document_type = params.document_type
            file_path = params.file_path or state.get("document_path")
            
            print(f"✅ [Structured Output] 추출된 파라미터: document_title={document_title}, document_type={document_type}, file_path={file_path}")
            
        except Exception as e:
            print(f"⚠️ Structured Output 파싱 실패, state에서 직접 가져오기: {e}")
            # Fallback: state에서 직접 가져오기
            file_path = state.get("document_path")
            document_title = state.get("document_title")
            
            # document_title이 없으면 쿼리에서 추출 시도
            if not document_title:
                updatable_docs = get_updatable_documents()
                for doc in updatable_docs:
                    if doc in query:
                        document_title = doc
                        document_type = doc  # document_title과 동일하게 설정
                        break
        
        if not file_path:
            # 파일 경로가 없으면 문서 업데이트 UI 표시 요청
            updatable_docs = get_updatable_documents()
            print(f"🔍 [Document Update] 파일 경로 없음 - 문서 업데이트 UI 표시 요청")
            result = {
                "ok": False,
                "final_response": "문서를 업데이트하시려면 아래의 + 버튼을 클릭하여 파일을 선택하고, 문서 제목을 입력한 후 업데이트 버튼을 클릭해주세요! 📤",
                "messages": ["파일 경로가 필요합니다."],
                "document_context": orchestrator_state["document_context"],
                "show_document_update_ui": True,  # 프론트엔드에 UI 표시 요청
            }
            print(f"🔍 [Document Update] 반환값에 show_document_update_ui 포함: {result.get('show_document_update_ui')}")
            return result
        
        if not document_title:
            # 문서 제목이 없으면 파일명 사용
            document_title = Path(file_path).stem
        
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return {
                "ok": False,
                "final_response": f"파일을 찾을 수 없습니다: {file_path}",
                "messages": ["파일을 찾을 수 없습니다."],
                "document_context": orchestrator_state["document_context"],
            }
        
        try:
            # 파일 내용 읽기
            with open(file_path_obj, "rb") as f:
                file_content = f.read()
            
            # 문서 업데이트 서비스 호출
            # Structured Output으로 추출된 document_type이 있으면 우선 사용
            # 없거나 신뢰할 수 없으면 파일 내용으로 재확인
            service_document_type = document_type if document_type else None
            result = process_update_file(
                file_content=file_content,
                filename=file_path_obj.name,
                document_title=document_title,
                document_type=service_document_type  # Structured Output 결과 우선 사용
            )
            
            if result.get("ok"):
                print(f"✅ 문서 업데이트 완료: {result.get('message', '')}")
                
                # Redis 컨텍스트에 저장
                update_info = {
                    "file_path": str(file_path),
                    "document_title": document_title,
                    "chunks_count": result.get("chunks_count", 0),
                    "updated_at": datetime.now().isoformat(),
                }
                orchestrator_state["document_context"]["last_update"] = update_info
                
                # conversation_history 업데이트 (다른 agent들과 동일한 방식)
                if "conversation_history" not in orchestrator_state:
                    orchestrator_state["conversation_history"] = []
                if not isinstance(orchestrator_state.get("conversation_history"), list):
                    orchestrator_state["conversation_history"] = []
                
                conversation_history = orchestrator_state["conversation_history"]
                
                # 사용자 메시지가 없으면 추가
                user_message_text = f"문서 '{document_title}' 업데이트 요청"
                if not conversation_history or conversation_history[-1].get("role") != "user":
                    conversation_history.append({
                        "role": "user",
                        "content": user_message_text,
                        "timestamp": datetime.now().isoformat()
                    })
                
                # Assistant 답변 추가
                assistant_message_text = result.get("message", "문서가 지식 베이스에 업데이트되었습니다.")
                conversation_history.append({
                    "role": "assistant",
                    "content": assistant_message_text,
                    "timestamp": datetime.now().isoformat()
                })
                
                return {
                    "final_response": assistant_message_text,
                    "messages": [f"문서 업데이트 완료: {result.get('chunks_count', 0)}개 청크 저장됨"],
                    "document_context": orchestrator_state["document_context"],
                    "conversation_history": conversation_history,
                }
            else:
                # 실패 시에도 conversation_history 업데이트 (다른 agent들과 동일한 방식)
                if "conversation_history" not in orchestrator_state:
                    orchestrator_state["conversation_history"] = []
                if not isinstance(orchestrator_state.get("conversation_history"), list):
                    orchestrator_state["conversation_history"] = []
                
                conversation_history = orchestrator_state["conversation_history"]
                
                # 사용자 메시지가 없으면 추가
                user_message_text = f"문서 '{document_title}' 업데이트 요청"
                if not conversation_history or conversation_history[-1].get("role") != "user":
                    conversation_history.append({
                        "role": "user",
                        "content": user_message_text,
                        "timestamp": datetime.now().isoformat()
                    })
                
                # Assistant 답변 추가 (실패 메시지)
                error_message = f"문서 업데이트 실패: {result.get('message', '알 수 없는 오류')}"
                conversation_history.append({
                    "role": "assistant",
                    "content": error_message,
                    "timestamp": datetime.now().isoformat()
                })
                
                return {
                    "final_response": error_message,
                    "messages": ["문서 업데이트에 실패했습니다."],
                    "document_context": orchestrator_state["document_context"],
                    "conversation_history": conversation_history,
                }
                
        except Exception as e:
            print(f"❌ 문서 업데이트 중 오류 발생: {e}")
            import traceback
            traceback.print_exc()
            
            # 오류 발생 시에도 conversation_history 업데이트 (다른 agent들과 동일한 방식)
            if "conversation_history" not in orchestrator_state:
                orchestrator_state["conversation_history"] = []
            if not isinstance(orchestrator_state.get("conversation_history"), list):
                orchestrator_state["conversation_history"] = []
            
            conversation_history = orchestrator_state["conversation_history"]
            
            # 사용자 메시지가 없으면 추가
            user_message_text = query or "문서 업데이트 요청"
            if not conversation_history or conversation_history[-1].get("role") != "user":
                conversation_history.append({
                    "role": "user",
                    "content": user_message_text,
                    "timestamp": datetime.now().isoformat()
                })
            
            # Assistant 답변 추가 (오류 메시지)
            error_message = f"문서 업데이트 중 오류가 발생했습니다: {str(e)}"
            conversation_history.append({
                "role": "assistant",
                "content": error_message,
                "timestamp": datetime.now().isoformat()
            })
            
            return {
                "ok": False,
                "final_response": error_message,
                "messages": [f"오류: {str(e)}"],
                "document_context": orchestrator_state.get("document_context", {}),
                "conversation_history": conversation_history,
            }


def get_document_master_graph() -> StateGraph:
    """문서 마스터그래프 인스턴스 반환."""
    master = DocumentMasterGraph()
    return master.get_graph()

