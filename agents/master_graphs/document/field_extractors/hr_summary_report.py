"""인사정보 요약 리포트 필드 추출 모듈."""

import json
import re
from typing import Any, Dict, List, Optional
from datetime import datetime

try:
    from langchain_openai import ChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from ..utils import get_env


def extract_hr_summary_report_fields(
    field_values: Dict[str, Any],
    requirements: str,
    document_title: str,
    db_data: Optional[List[Dict[str, Any]]] = None
) -> None:
    """인사정보 요약 리포트 필드 값을 추출합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (수정됨)
        requirements: 사용자 요구사항
        document_title: 문서 제목
        db_data: DB에서 조회한 데이터 (선택사항)
    """
    print(f"🔍 [hr_summary_report] 필드 값 추출 시작...")
    
    # 1. 리포트 작성일 설정
    today = datetime.now()
    field_values['reportDate'] = f"{today.year}.{today.month:02d}.{today.day:02d}"
    print(f"✅ reportDate 추출: {field_values['reportDate']}")
    
    # 2. DB 데이터 기반 테이블 데이터 생성
    print(f"📊 DB 데이터 확인: {type(db_data)}, 길이: {len(db_data) if db_data else 0}")
    if db_data and isinstance(db_data, list) and len(db_data) > 0:
        print(f"📊 DB 데이터 기반 테이블 생성 시작... (데이터 {len(db_data)}개)")
        
        # 첫 번째 행 샘플 출력 (디버깅)
        first_row = db_data[0]
        print(f"🔍 첫 번째 DB 행 샘플 (키 목록): {list(first_row.keys())}")
        print(f"🔍 첫 번째 DB 행 샘플 (전체 데이터): {first_row}")
        
        # employeeTable 생성
        employee_table = []
        
        for idx, row in enumerate(db_data):
            # 실제 employee 테이블 컬럼명 사용 (정확한 컬럼명 우선, 대소문자 변형 지원)
            # employee 테이블 컬럼: employee_id, name, dept, job_title, address_state, email, 
            # gender, employment_status, salary_million_won, city, job_role, performance_score,
            # evaluation_grade, position, date_of_birth, tenure_years, hire_data
            
            # 실제 employee 테이블 컬럼명 사용
            name = row.get('name') or ''
            
            dept = row.get('dept') or ''
            
            # 직급/직위: position 컬럼 우선, 없으면 job_title 사용
            # 실제 DB에는 position과 job_title 둘 다 존재
            position = row.get('position') or row.get('job_title') or ''
            
            # 직원 상태: employment_status 컬럼 사용
            status = row.get('employment_status') or ''
            
            # 역할: job_role 컬럼 사용
            job_role = row.get('job_role') or ''
            
            # 이메일: email 컬럼 사용
            email = row.get('email') or ''
            
            # None 값 처리 및 문자열 변환
            employee_row = {
                'name': str(name) if name is not None else '',
                'dept': str(dept) if dept is not None else '',
                'position': str(position) if position is not None else '',
                'status': str(status) if status is not None else '',
                'job_role': str(job_role) if job_role is not None else '',
                'email': str(email) if email is not None else '',
            }
            
            # 디버깅: 첫 번째 행 상세 정보 출력
            if idx == 0:
                print(f"🔍 첫 번째 직원 데이터 상세:")
                print(f"  - name: '{employee_row['name']}' (원본: {row.get('name', 'N/A')})")
                print(f"  - dept: '{employee_row['dept']}' (원본: {row.get('dept', 'N/A')})")
                print(f"  - position: '{employee_row['position']}' (원본 position: {row.get('position', 'N/A')}, job_title: {row.get('job_title', 'N/A')})")
                print(f"  - job_role: '{employee_row['job_role']}' (원본: {row.get('job_role', 'N/A')})")
                print(f"  - email: '{employee_row['email']}' (원본: {row.get('email', 'N/A')})")
                # 모든 컬럼 확인 (디버깅)
                print(f"  - 전체 컬럼 키: {list(row.keys())}")
            
            employee_table.append(employee_row)
        
        field_values['employeeTable'] = employee_table
        print(f"✅ employeeTable: {len(employee_table)}개 행 생성 완료")
        print(f"🔍 employeeTable 첫 번째 행 확인: {employee_table[0] if len(employee_table) > 0 else 'N/A'}")
    
    else:
        # DB 데이터가 없으면 빈 테이블 구조만 생성
        print("⚠️ DB 데이터가 없거나 비어있습니다. 빈 테이블 구조만 생성합니다.")
        print(f"🔍 db_data 타입: {type(db_data)}, 값: {db_data}")
        field_values['employeeTable'] = []
    
    print(f"✅ [hr_summary_report] 필드 값 추출 완료")
    print(f"🔍 최종 field_values['employeeTable'] 길이: {len(field_values.get('employeeTable', []))}")

