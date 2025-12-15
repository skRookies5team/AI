"""문서 자동 생성 에이전트 (AI-012)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 분리된 모듈 import
from .. import document_utils
from . import html_filler
from . import llm_generator
from . import db_query_generator
from . import db_connector
from .field_extractors import TEMPLATE_EXTRACTORS


def doc_generate(
    user_text: str,
    template_name: Optional[str] = None,
    document_title: Optional[str] = None,
    requirements: Optional[str] = None,
    db_data: Optional[Dict[str, Any]] = None,
    orchestrator_state: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[list] = None
) -> Dict:
    """문서 자동 생성 메인 함수 (AI-012).
    
    프로세스:
    1. 템플릿 로드: HTML 템플릿 파일 읽기
    2. DB 데이터 매핑: 템플릿 필드에 필요한 데이터 매핑 (db_data 사용)
    3. 초안 생성: LLM을 사용하여 매핑된 데이터와 요구사항으로 초안 생성
    
    Redis 기반 멀티턴 대화 지원:
    - 이전에 생성한 문서 정보를 Redis에 저장하여 유지
    - 대화 히스토리를 활용하여 이전 요청을 기억하고 활용
    - 템플릿 타입, 변수 정보 등을 컨텍스트로 관리
    
    Args:
        user_text: 사용자 입력 텍스트 (전체 쿼리)
        template_name: 템플릿 이름 (선택사항, user_text에서 추출 가능)
        document_title: 문서 제목 (선택사항, user_text에서 추출 가능)
        requirements: 사용자 요구사항 (선택사항, user_text에서 추출 가능)
        db_data: DB에서 조회한 데이터 (선택사항, 템플릿 필드 매핑용)
        orchestrator_state: 오케스트레이터 상태 (Redis 컨텍스트 관리용)
        conversation_history: 대화 히스토리 (이전 대화 참조용)
    
    Returns:
        생성 결과 딕셔너리
    """
    print(f"--- [Document Generate Agent] 시작 ---")
    print(f"사용자 입력: {user_text}")
    
    # Redis 기반 컨텍스트 초기화
    if orchestrator_state is None:
        orchestrator_state = {}
    
    if orchestrator_state.get("document_context") is None:
        orchestrator_state["document_context"] = {}
    
    doc_context = orchestrator_state["document_context"]
    
    # 이전 대화 컨텍스트 확인
    previous_template = doc_context.get("last_template_type")
    previous_variables = doc_context.get("last_variables", {})
    previous_title = doc_context.get("last_document_title")
    
    # 이메일 내용 등 불필요한 정보 제외를 위한 키워드
    excluded_keywords = ["보낸 사람", "받는 사람", "메일", "이메일", "email", "문서번호", "고용센터", "결정통지서", "edi@"]
    
    # ===== 템플릿 추출 우선순위 =====
    # 1. 전달받은 template_name (route_intent에서 매핑된 것) - 최우선
    if template_name:
        print(f"✅ 전달받은 템플릿 사용: {template_name}")
    # 2. user_text에서 추출 (현재 요청)
    else:
        # "양식: product_development_plan" 형식 추출
        template_match = re.search(r'양식[:\s]+(\w+)|템플릿[:\s]+(\w+)|template[:\s]+(\w+)', user_text, re.IGNORECASE)
        if template_match:
            template_name = template_match.group(1) or template_match.group(2) or template_match.group(3)
            print(f"✅ 현재 요청에서 템플릿 추출: {template_name}")
        
        # 키워드 기반 템플릿 매핑 (명시적 템플릿명이 없는 경우)
        if not template_name:
            template_keywords = {
                # 제품 비교표는 제품개발보다 먼저 체크 (더 구체적인 키워드)
                "product_comparison_table": ["제품 비교표", "제품 비교", "제품성능 비교", "성능 비교", "비교표", "제품비교", "비교 대상", "비교 목적"],
                "product_development_plan": ["제품개발", "신제품", "개발계획", "제품개발 계획서"],
                "purchase_order": ["발주", "구매", "주문", "발주서"],
                "hr_summary_report": ["인사", "인력", "직원", "인사정보"],
                "monthly_profit_analysis_report": ["수익", "매출", "분석", "수익 분석"],
                "work_performance_review": ["성과", "평가", "업무", "성과 평가"],
                "supplier_evaluation_form": ["공급업체", "협력사", "평가"],
                "contract_renewal_report": ["계약", "갱신", "연장"],
                "activity_based_costing": ["원가", "비용", "계산"],
                "project_schedule": ["프로젝트", "일정", "스케줄"],
            }
            
            for tpl, keywords in template_keywords.items():
                if any(kw in user_text for kw in keywords):
                    template_name = tpl
                    print(f"✅ 키워드 기반 템플릿 매핑: {template_name}")
                    break
    
    # 3. 대화 히스토리에서 추출 (이전 대화)
    if not template_name and conversation_history:
        print(f"📚 대화 히스토리에서 템플릿 검색: {len(conversation_history)}개 메시지")
        for msg in reversed(conversation_history[-5:]):  # 최근 5개 메시지만 확인
            content = msg.get("content", "")
            
            # 이메일 관련 내용은 제외
            if any(excluded in content for excluded in excluded_keywords):
                continue
            
            if any(k in content for k in ["양식", "템플릿", "template"]):
                # 이전 대화에서 템플릿 정보 추출 (문서 작성 관련 컨텍스트만)
                if any(k in content for k in ["문서 작성", "작성해줘", "생성", "제품개발", "발주", "계획서"]):
                    template_match = re.search(r'양식[:\s]+(\w+)|템플릿[:\s]+(\w+)|template[:\s]+(\w+)', content, re.IGNORECASE)
                    if template_match:
                        template_name = template_match.group(1) or template_match.group(2) or template_match.group(3)
                        print(f"✅ 대화 히스토리에서 템플릿 추출: {template_name}")
                        break
    
    # 4. 이전 컨텍스트에서 복원 (Redis)
    if not template_name and previous_template:
        template_name = previous_template
        print(f"⚠️ 이전 컨텍스트에서 템플릿 복원: {template_name} (현재 요청에 템플릿 정보 없음)")
    
    # ===== 제목 추출 우선순위 =====
    # 1. 전달받은 document_title - 최우선
    if document_title:
        print(f"✅ 전달받은 제목 사용: {document_title}")
    # 2. user_text에서 추출 (현재 요청)
    else:
        # "제목: ..." 형식 추출
        title_match = re.search(r'제목[:\s]+([^.]+?)(?:\.|요구사항|$)', user_text)
        if title_match:
            document_title = title_match.group(1).strip()
            print(f"✅ 현재 요청에서 제목 추출: {document_title}")
    
    # 3. 대화 히스토리에서 추출 (이전 대화)
    if not document_title and conversation_history:
        print(f"📚 대화 히스토리에서 제목 검색")
        for msg in reversed(conversation_history[-5:]):  # 최근 5개 메시지만 확인
            content = msg.get("content", "")
            
            # 이메일 관련 내용은 제외
            if any(excluded in content for excluded in excluded_keywords):
                continue
            
            if any(k in content for k in ["제목", "title"]):
                # 이전 대화에서 제목 추출 (문서 작성 관련 컨텍스트만)
                if any(k in content for k in ["문서 작성", "작성해줘", "생성", "제품개발", "발주", "계획서"]):
                    title_match = re.search(r'제목[:\s]+([^.]+?)(?:\.|요구사항|$)', content)
                    if title_match:
                        extracted_title = title_match.group(1).strip()
                        # 이메일 제목 등 불필요한 정보 제외
                        if not any(excluded in extracted_title for excluded in excluded_keywords):
                            document_title = extracted_title
                            print(f"✅ 대화 히스토리에서 제목 추출: {document_title}")
                            break
    
    # 4. 이전 컨텍스트에서 복원 (Redis)
    if not document_title and previous_title:
        document_title = previous_title
        print(f"⚠️ 이전 컨텍스트에서 제목 복원: {document_title} (현재 요청에 제목 정보 없음)")
    
    if not requirements:
        # "요구사항: ..." 형식 추출 (더 강력한 패턴)
        patterns = [
            r'요구사항[:\s]+(.+?)(?:\s*\(이전|RAG|참조|$)',  # 괄호 전까지 또는 끝까지
            r'요구사항[:\s]+(.+)',  # 기본 패턴
            r'\.\s*요구사항[:\s]+(.+)',  # 점 뒤 요구사항
        ]
        
        for pattern in patterns:
            req_match = re.search(pattern, user_text, re.DOTALL | re.IGNORECASE)
            if req_match:
                requirements = req_match.group(1).strip()
                # 괄호나 추가 설명 제거 (예: "(이전 문서 참조)" 제거)
                requirements = re.sub(r'\s*\([^)]*이전[^)]*\)\s*', '', requirements)
                requirements = re.sub(r'\s*\([^)]*참조[^)]*\)\s*', '', requirements)
                requirements = re.sub(r'\s*\([^)]*RAG[^)]*\)\s*', '', requirements)
                requirements = requirements.strip()
                if requirements and len(requirements) > 5:  # 최소 5자 이상이어야 함
                    break
        
        # 여전히 없으면 "제목" 다음 부분을 요구사항으로 간주
        if not requirements or len(requirements) < 5:
            # "제목: ..." 다음 부분을 요구사항으로 간주
            title_patterns = [
                r'제목[:\s]+[^.]*\.\s*(.+)',  # 제목 다음 점 이후
                r'제목[:\s]+[^요]*요구사항[:\s]+(.+)',  # 제목과 요구사항 사이
            ]
            
            for pattern in title_patterns:
                match = re.search(pattern, user_text, re.DOTALL | re.IGNORECASE)
                if match:
                    requirements = match.group(1).strip()
                    # 괄호 제거
                    requirements = re.sub(r'\s*\([^)]*이전[^)]*\)\s*', '', requirements)
                    requirements = re.sub(r'\s*\([^)]*참조[^)]*\)\s*', '', requirements)
                    requirements = requirements.strip()
                    if requirements and len(requirements) > 5:
                        break
            
            # 마지막 시도: "양식: ... 제목: ..." 이후 부분
            if not requirements or len(requirements) < 5:
                template_idx = user_text.find('양식:') if '양식:' in user_text else -1
                title_idx = user_text.find('제목:') if '제목:' in user_text else -1
                
                if title_idx != -1:
                    # 제목: 이후 부분 추출
                    after_title = user_text[title_idx:].split('제목:', 1)
                    if len(after_title) > 1:
                        title_and_after = after_title[1]
                        # 점으로 분리하여 두 번째 부분
                        parts = title_and_after.split('.', 1)
                        if len(parts) > 1:
                            requirements = parts[1].strip()
                            # "요구사항:" 키워드 제거
                            requirements = re.sub(r'^요구사항[:\s]+', '', requirements, flags=re.IGNORECASE)
                            requirements = requirements.strip()
                            # 괄호 제거
                            requirements = re.sub(r'\s*\([^)]*이전[^)]*\)\s*', '', requirements)
                            requirements = re.sub(r'\s*\([^)]*참조[^)]*\)\s*', '', requirements)
                            requirements = requirements.strip()
    
    # 기본값 설정
    if not template_name:
        template_name = "product_development_plan"  # 기본 템플릿
        print(f"⚠️ 템플릿명을 찾을 수 없어 기본값 사용: {template_name}")
    
    if not document_title:
        document_title = "문서"
        print(f"⚠️ 문서 제목을 찾을 수 없어 기본값 사용: {document_title}")
    
    if not requirements or requirements == "사용자 요구사항이 없습니다.":
        requirements = "사용자 요구사항이 없습니다."
        print(f"⚠️ 요구사항을 찾을 수 없어 기본값 사용")
    else:
        print(f"✅ 요구사항 추출 성공! (길이: {len(requirements)}자)")
    
    print(f"템플릿: {template_name}, 제목: {document_title}")
    print(f"요구사항: {requirements[:100]}...")
    if db_data:
        print(f"DB 데이터: {len(db_data)} 필드 매핑됨")
    
    # 1단계: 템플릿 로드
    print("📋 [1/3] 템플릿 로드 중...")
    template_html = document_utils.load_template(template_name)
    if not template_html:
        return {
            "ok": False,
            "message": f"템플릿 파일을 찾을 수 없습니다: {template_name}",
            "error": "template_not_found"
        }
    
    # 템플릿 구조 추출
    template_structure = document_utils.extract_template_structure(template_html)
    
    # 2단계: DB 데이터 매핑 (Data Binding)
    print("🔗 [2/4] DB 데이터 매핑 중...")
    
    # RDB에서 데이터 조회가 필요한 템플릿인 경우
    db_product_data = None
    db_profit_data = None
    db_costing_data = None
    db_hr_data = None
    
    if template_name == "product_comparison_table" and requirements and requirements != "사용자 요구사항이 없습니다.":
        print("📊 [RDB 조회] 제품 비교표를 위한 DB 데이터 조회 시작...")
        
        # 2-1. LLM으로 사용자 요구사항에서 필요한 DB 테이블/컬럼 추출
        query_info = db_query_generator.generate_db_query_info(
            user_requirements=requirements,
            template_name=template_name,
            document_title=document_title
        )
        
        if query_info:
            # 2-2. 제품명 추출
            product_names = query_info.get('product_names', [])
            print(f"📋 추출된 제품명: {product_names}")
            
            # 2-3. 실제 DB에서 데이터 조회
            if product_names:
                db_product_data = db_connector.fetch_product_comparison_data(
                    query_info=query_info,
                    product_names=product_names
                )
                print(f"✅ DB 조회 완료: {len(db_product_data) if db_product_data else 0}개 제품 데이터")
            else:
                print("⚠️ 제품명을 찾을 수 없어 DB 조회 건너뜀")
        else:
            print("⚠️ DB 쿼리 정보 생성 실패, DB 조회 건너뜀")
    
    elif template_name == "monthly_profit_analysis_report" and requirements and requirements != "사용자 요구사항이 없습니다.":
        print("📊 [RDB 조회] 월별 수익 분석 리포트를 위한 DB 데이터 조회 시작...")
        
        # 2-1. LLM으로 사용자 요구사항에서 필요한 DB 테이블/컬럼 추출
        query_info = db_query_generator.generate_db_query_info(
            user_requirements=requirements,
            template_name=template_name,
            document_title=document_title
        )
        
        # 2-2. 년도, 월, 지역, 제품 카테고리 추출 (query_info 생성 전에도 추출 가능)
        year = None
        month = None
        regions = []
        product_categories = []
        
        # 년도 추출
        year_match = re.search(r'(\d{4})\s*년', requirements)
        if year_match:
            year = int(year_match.group(1))
        
        # 월 추출
        month_match = re.search(r'(\d{1,2})\s*월', requirements)
        if month_match:
            month = int(month_match.group(1))
        
        # 지역 추출 (더 많은 변형 지원)
        region_keywords = ['한국', '미국', '일본', '중국', '유럽', 'Korea', 'USA', 'Japan', 'China', 'Europe']
        for keyword in region_keywords:
            if keyword in requirements:
                if keyword not in regions:
                    regions.append(keyword)
        
        # 제품 카테고리 추출 (더 많은 키워드 지원)
        category_keywords = [
            '스마트폰', '휴대폰', '핸드폰', 
            '태블릿', '노트북', '스마트워치', '이어폰',
            '모바일', '휴대전화', '폰'
        ]
        for keyword in category_keywords:
            if keyword in requirements:
                # 중복 제거
                if keyword not in product_categories:
                    product_categories.append(keyword)
        
        # "휴대폰" -> "스마트폰" 매핑 (DB에서 스마트폰으로 저장된 경우 대비)
        if '휴대폰' in product_categories or '핸드폰' in product_categories:
            if '스마트폰' not in product_categories:
                product_categories.append('스마트폰')
        
        print(f"📋 추출된 필터 조건:")
        print(f"  년도: {year}, 월: {month}")
        print(f"  지역: {regions}")
        print(f"  제품 카테고리: {product_categories}")
        
        # 2-3. 실제 DB에서 데이터 조회
        # query_info가 없어도 기본 query_info 생성하여 DB 조회 시도
        if not query_info:
            print("⚠️ LLM이 query_info를 생성하지 못함. 기본 query_info로 DB 조회 시도...")
            query_info = {
                "tables": ["company_sales"],
                "columns": {"company_sales": ["*"]}
            }
        
        if query_info:
            db_profit_data = db_connector.fetch_monthly_profit_analysis_data(
                query_info=query_info,
                year=year,
                month=month,
                regions=regions if regions else None,
                product_categories=product_categories if product_categories else None
            )
            print(f"✅ DB 조회 완료: {len(db_profit_data) if db_profit_data else 0}개 데이터 행")
            
            # db_profit_data를 db_data에도 설정 (LLM 프롬프트에 포함)
            if db_profit_data:
                db_data = db_profit_data
        else:
            print("⚠️ query_info를 생성할 수 없어 DB 조회 건너뜀")
            db_profit_data = None
    
    elif template_name == "activity_based_costing" and requirements and requirements != "사용자 요구사항이 없습니다.":
        print("📊 [RDB 조회] 활동기준 원가 계산서를 위한 DB 데이터 조회 시작...")
        
        # 2-1. LLM으로 사용자 요구사항에서 필요한 DB 테이블/컬럼 추출
        query_info = db_query_generator.generate_db_query_info(
            user_requirements=requirements,
            template_name=template_name,
            document_title=document_title
        )
        
        # 2-2. 실제 DB에서 데이터 조회
        if query_info:
            db_costing_data = db_connector.fetch_activity_based_costing_data(
                query_info=query_info,
                user_requirements=requirements
            )
            print(f"✅ DB 조회 완료: {len(db_costing_data) if db_costing_data else 0}개 데이터 행")
            
            # db_costing_data를 db_data에도 설정 (LLM 프롬프트에 포함)
            if db_costing_data:
                db_data = db_costing_data
        else:
            print("⚠️ query_info를 생성할 수 없어 DB 조회 건너뜀")
            db_costing_data = None
    
    elif template_name == "hr_summary_report" and requirements and requirements != "사용자 요구사항이 없습니다.":
        print("📊 [RDB 조회] 인사정보 요약 리포트를 위한 DB 데이터 조회 시작...")
        
        # 2-1. LLM으로 사용자 요구사항에서 필요한 DB 테이블/컬럼 추출
        query_info = db_query_generator.generate_db_query_info(
            user_requirements=requirements,
            template_name=template_name,
            document_title=document_title
        )
        
        # 2-2. 실제 DB에서 데이터 조회
        # query_info가 없어도 user_requirements만으로도 조회 가능하도록 수정
        db_hr_data = db_connector.fetch_hr_summary_report_data(
            query_info=query_info if query_info else {},
            user_requirements=requirements
        )
        print(f"✅ DB 조회 완료: {len(db_hr_data) if db_hr_data else 0}개 데이터 행")
        if db_hr_data and len(db_hr_data) > 0:
            print(f"📊 첫 번째 DB 행 샘플: {list(db_hr_data[0].keys())}")
            print(f"📊 첫 번째 DB 행 데이터: name={db_hr_data[0].get('name', 'N/A')}, dept={db_hr_data[0].get('dept', 'N/A')}, position={db_hr_data[0].get('position', 'N/A')}")
        
        # db_hr_data를 db_data에도 설정 (LLM 프롬프트에 포함)
        if db_hr_data:
            db_data = db_hr_data
            print(f"✅ db_data에 {len(db_hr_data)}개 행 설정 완료")
        else:
            print("⚠️ db_hr_data가 비어있습니다.")
            db_hr_data = []  # 빈 리스트로 초기화하여 extractor_func에 전달
        
        # db_hr_data를 전역 변수로도 저장 (extractor_func에서 접근 가능하도록)
        globals()['db_hr_data'] = db_hr_data
    
    # 기존 db_data가 있으면 LLM 프롬프트에 포함
    db_context = ""
    if db_data:
        db_context = f"\n\nDB 데이터 (템플릿 필드 매핑용):\n{str(db_data)}"
    
    # 3단계: 초안 생성 (2단계 접근)
    print("🤖 [3/4] LLM을 사용하여 초안 생성 중...")
    
    # 방법 1: LLM이 필드 값 매핑을 JSON으로 생성하고 BeautifulSoup으로 채우기 (더 정확함)
    field_values = llm_generator.generate_field_values_with_llm(
        template_structure=template_structure,
        user_requirements=requirements,
        document_title=document_title,
        template_name=template_name,
        db_data=db_data
    )
    
    # LLM이 실패했거나 빈 딕셔너리를 반환한 경우 빈 딕셔너리로 초기화
    if not field_values or not isinstance(field_values, dict):
        print("⚠️ LLM 필드 값 생성 실패, 요구사항에서 직접 추출 시작...")
        field_values = {}
    
    # 필드 값이 실제로 있는지 확인 (디버깅)
    non_empty_fields = {k: v for k, v in field_values.items() if v and (not isinstance(v, str) or v.strip())}
    empty_fields = {k: v for k, v in field_values.items() if not v or (isinstance(v, str) and not v.strip())}
    
    if len(field_values) > 0:
        print(f"✅ 필드 값 매핑 생성 성공! ({len(field_values)}개 필드)")
        print(f"📋 필드 목록: {list(field_values.keys())}")
    else:
        print("⚠️ LLM이 필드 값을 생성하지 못함, 요구사항에서 직접 추출합니다...")
    
    print(f"📊 필드 상태: {len(non_empty_fields)}개 비어있지 않음, {len(empty_fields)}개 비어있음")
    if empty_fields:
        print(f"⚠️ 비어있는 필드: {list(empty_fields.keys())[:10]}...")
    
    # document_title도 field_values에 추가
    if 'document_title' not in field_values:
        field_values['document_title'] = document_title
    
    # 비어있는 필수 필드에 대해 요구사항에서 직접 추출하여 채우기 (템플릿별 분기)
    if requirements and requirements != "사용자 요구사항이 없습니다.":
        print(f"🔍 [4/4] 요구사항에서 필드 값 추출 시작... (템플릿: {template_name})")
        
        # 템플릿별 추출 함수 호출
        extractor_func = TEMPLATE_EXTRACTORS.get(template_name)
        if extractor_func:
            # RDB 기반 템플릿의 경우 DB 데이터 전달
            if template_name == "product_comparison_table":
                # db_product_data가 None이거나 빈 리스트여도 extractor_func 호출 (기본 comparisonTable 생성)
                extractor_func(field_values, requirements, document_title, db_data=db_product_data if db_product_data else None)
            elif template_name == "monthly_profit_analysis_report":
                # db_profit_data가 None이거나 빈 리스트여도 extractor_func 호출 (기본 테이블 생성)
                # db_profit_data가 로컬 변수로 정의되지 않았을 경우를 대비
                if 'db_profit_data' in locals():
                    extractor_func(field_values, requirements, document_title, db_data=db_profit_data if db_profit_data else None)
                elif db_data:
                    extractor_func(field_values, requirements, document_title, db_data=db_data)
                else:
                    extractor_func(field_values, requirements, document_title, db_data=None)
                    print("⚠️ db_profit_data가 없어 빈 데이터로 extractor_func 호출")
            elif template_name == "activity_based_costing":
                # db_costing_data가 None이거나 빈 리스트여도 extractor_func 호출 (기본 테이블 생성)
                if 'db_costing_data' in locals():
                    extractor_func(field_values, requirements, document_title, db_data=db_costing_data if db_costing_data else None)
                elif db_data:
                    extractor_func(field_values, requirements, document_title, db_data=db_data)
                else:
                    extractor_func(field_values, requirements, document_title, db_data=None)
                    print("⚠️ db_costing_data가 없어 빈 데이터로 extractor_func 호출")
            elif template_name == "hr_summary_report":
                # db_hr_data가 None이거나 빈 리스트여도 extractor_func 호출 (기본 테이블 생성)
                # db_hr_data를 우선 사용하고, 없으면 db_data 사용
                data_to_pass = None
                
                # locals()에서 db_hr_data 확인
                if 'db_hr_data' in locals():
                    local_db_hr_data = locals()['db_hr_data']
                    if local_db_hr_data:
                        data_to_pass = local_db_hr_data
                        print(f"✅ db_hr_data (local) 사용: {len(local_db_hr_data)}개 행")
                    else:
                        print(f"⚠️ db_hr_data (local)가 빈 리스트입니다.")
                
                # globals()에서도 확인
                if not data_to_pass and 'db_hr_data' in globals():
                    global_db_hr_data = globals()['db_hr_data']
                    if global_db_hr_data:
                        data_to_pass = global_db_hr_data
                        print(f"✅ db_hr_data (global) 사용: {len(global_db_hr_data)}개 행")
                
                # db_data 확인
                if not data_to_pass and db_data:
                    data_to_pass = db_data
                    print(f"✅ db_data 사용: {len(db_data)}개 행")
                
                if not data_to_pass:
                    print("⚠️ DB 데이터가 없어 빈 데이터로 extractor_func 호출")
                    data_to_pass = []
                
                print(f"🔍 extractor_func 호출 전: data_to_pass 타입={type(data_to_pass)}, 길이={len(data_to_pass) if isinstance(data_to_pass, list) else 'N/A'}")
                if isinstance(data_to_pass, list) and len(data_to_pass) > 0:
                    print(f"🔍 data_to_pass 첫 번째 행 샘플: {list(data_to_pass[0].keys()) if isinstance(data_to_pass[0], dict) else 'N/A'}")
                
                extractor_func(field_values, requirements, document_title, db_data=data_to_pass)
                
                print(f"🔍 extractor_func 호출 후: employeeTable 길이={len(field_values.get('employeeTable', [])) if isinstance(field_values.get('employeeTable'), list) else 'N/A'}")
                if isinstance(field_values.get('employeeTable'), list) and len(field_values.get('employeeTable', [])) > 0:
                    print(f"🔍 employeeTable 첫 번째 행: {field_values['employeeTable'][0]}")
            else:
                extractor_func(field_values, requirements, document_title)
        else:
            print(f"⚠️ 템플릿별 특화 추출 로직 없음, 기본 추출 시도...")
        
        print(f"✅ 요구사항 기반 필드 값 추출 완료!")
    
    # 요구사항 추출 후에도 필드 값이 없으면 기본값으로 채우기
    if not field_values or len(field_values) == 0:
        print("⚠️ 필드 값이 전혀 없어 기본값으로 초기화합니다...")
        if template_name == "product_development_plan":
            field_values = {
                'document_title': document_title,
                'itemName': '제품',
                'author': 'AI 생성',
                'department': '연구개발부서',
                'responsiblePerson': '담당자',
            }
        elif template_name == "purchase_order":
            field_values = {
                'document_title': document_title,
                'orderNumber': 'PO-2024-001',
                'buyerName': '(주)깐부전자',
                'companyName': '(주)깐부전자',
                'representative': '홍길동',
            }
        elif template_name == "work_performance_review":
            field_values = {
                'document_title': document_title,
                'departmentName': '인사팀',
                'codeNumber': 'HR-001',
            }
        else:
            field_values = {
                'document_title': document_title,
            }
    
    # comparisonTable 데이터 확인 (디버깅)
    if 'comparisonTable' in field_values:
        comparison_table = field_values.get('comparisonTable')
        if isinstance(comparison_table, list):
            print(f"📊 comparisonTable 데이터 확인: {len(comparison_table)}개 행")
            if len(comparison_table) > 0:
                first_row = comparison_table[0]
                print(f"  첫 번째 행 샘플: {first_row}")
        else:
            print(f"⚠️ comparisonTable이 리스트가 아님: {type(comparison_table)}")
    else:
        print(f"⚠️ comparisonTable이 field_values에 없음!")
    
    generated_html = html_filler.fill_fields_with_beautifulsoup(
        template_html=template_html,
        field_values=field_values,
        document_title=document_title,
        template_name=template_name
    )
    print("✅ BeautifulSoup 필드 채우기 완료!")
    
    # 최종 검증: 생성된 HTML에 필수 필드가 모두 채워졌는지 확인 (템플릿별)
    try:
        from bs4 import BeautifulSoup
        verification_soup = BeautifulSoup(generated_html, 'html.parser')
        
        # 템플릿별 필수 필드 목록
        if template_name == "product_development_plan":
            essential_fields = ['itemName', 'author', 'specifications', 'purpose', 
                              'developmentPurpose', 'developmentPeriod', 'developmentContent',
                              'expectedEffects', 'specialNotes', 'department', 'responsiblePerson']
        elif template_name == "purchase_order":
            essential_fields = ['orderNumber', 'supplierName', 'buyerName', 'companyName', 'representative']
        elif template_name == "work_performance_review":
            essential_fields = ['departmentName', 'codeNumber', 'year']
        else:
            # 기본 필수 필드 (템플릿별 특화가 없는 경우)
            essential_fields = []
        
        if essential_fields:
            filled_essential = 0
            for field_id in essential_fields:
                element = verification_soup.find(id=field_id)
                if element:
                    if element.name == 'input' and element.get('value', '').strip():
                        filled_essential += 1
                    elif element.name == 'textarea' and element.get_text(strip=True):
                        filled_essential += 1
            print(f"🔍 최종 검증: 필수 필드 {filled_essential}/{len(essential_fields)}개 채워짐")
            if filled_essential < len(essential_fields) * 0.7:  # 70% 미만이면 경고
                print(f"⚠️ 경고: 일부 필수 필드가 비어있을 수 있습니다.")
    except Exception as e:
        print(f"⚠️ 최종 검증 실패: {e}")
    
    # 제목 업데이트 (템플릿의 title 태그 수정)
    if document_title and document_title != "문서":
        generated_html = re.sub(
            r'<title>[^<]+</title>',
            f'<title>{document_title}</title>',
            generated_html
        )
        # 헤더의 h1도 업데이트 (있는 경우)
        generated_html = re.sub(
            r'<h1[^>]*>[^<]+</h1>',
            f'<h1>{document_title}</h1>',
            generated_html,
            count=1
        )
    
    # 생성 완료 로그
    html_length = len(generated_html)
    print(f"✅ 문서 생성 완료! (HTML 길이: {html_length:,} 문자)")
    print(f"📄 문서 제목: {document_title}")
    print(f"📋 템플릿: {template_name}")
    
    # HTML 파일로 저장
    document_path = None
    try:
        # output/documents 디렉토리 경로 생성
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent.parent.parent
        output_dir = project_root / "output" / "documents"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 파일명 생성 (제목 기반, 특수문자 제거)
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', document_title)
        safe_title = re.sub(r'\s+', '_', safe_title)
        
        # 타임스탬프 추가하여 파일명 중복 방지
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_title}_{timestamp}.html"
        
        # 파일 경로
        file_path = output_dir / filename
        
        # HTML 파일 저장
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(generated_html)
        
        document_path = str(file_path)
        print(f"💾 HTML 파일 저장 완료: {document_path}")
        print(f"💡 생성된 HTML은 final_response에 포함되어 있으며, 파일로도 저장되었습니다.")
        
    except Exception as e:
        print(f"⚠️ HTML 파일 저장 실패: {e}")
        import traceback
        traceback.print_exc()
    
    # Redis 컨텍스트에 저장
    doc_context["last_template_type"] = template_name
    doc_context["last_document_title"] = document_title
    doc_context["last_variables"] = field_values  # 생성에 사용된 변수들 저장
    doc_context["last_file_path"] = document_path
    doc_context["last_query"] = user_text
    # 생성된 HTML 내용도 Redis에 저장
    doc_context["last_generated_html"] = generated_html
    
    print(f"💾 Redis 컨텍스트에 저장 완료:")
    print(f"   - 템플릿: {template_name}")
    print(f"   - 제목: {document_title}")
    print(f"   - 변수 개수: {len(field_values)}")
    print(f"   - HTML 길이: {len(generated_html):,} 문자")
    
    return {
        "ok": True,
        "message": generated_html,  # HTML 문서 전체를 반환
        "template_name": template_name,
        "document_title": document_title,
        "file_path": document_path,  # 저장된 파일 경로
        "document_context": orchestrator_state.get("document_context", {})  # 컨텍스트 반환 (HTML 포함)
    }

