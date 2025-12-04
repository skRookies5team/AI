"""활동기준 원가 계산서 필드 추출 모듈."""

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


def extract_activity_based_costing_fields(
    field_values: Dict[str, Any],
    requirements: str,
    document_title: str,
    db_data: Optional[List[Dict[str, Any]]] = None
) -> None:
    """활동기준 원가 계산서 필드 값을 추출합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (수정됨)
        requirements: 사용자 요구사항
        document_title: 문서 제목
        db_data: DB에서 조회한 데이터 (선택사항)
    """
    print(f"🔍 [activity_based_costing] 필드 값 추출 시작...")
    
    # 1. 기간 추출
    period_match = re.search(r'(\d{4})\s*년\s*(\d{1,2})\s*분기', requirements)
    if period_match:
        year = period_match.group(1)
        quarter = period_match.group(2)
        field_values['period'] = f"{year}년 {quarter}분기"
        print(f"✅ period 추출: {field_values['period']}")
    else:
        period_match = re.search(r'(\d{4})\s*년', requirements)
        if period_match:
            year = period_match.group(1)
            field_values['period'] = f"{year}년"
            print(f"✅ period 추출: {field_values['period']}")
    
    # 2. 부서/센터 추출
    dept_match = re.search(r'부서/센터[:\s]*([^,\n]+)', requirements)
    if dept_match:
        dept = dept_match.group(1).strip()
        field_values['department'] = dept
        print(f"✅ department 추출: {field_values['department']}")
    else:
        dept_match = re.search(r'부서[:\s]*([^,\n]+)', requirements)
        if dept_match:
            dept = dept_match.group(1).strip()
            field_values['department'] = dept
            print(f"✅ department 추출: {field_values['department']}")
    
    # 3. DB 데이터 기반 테이블 데이터 생성
    print(f"📊 DB 데이터 확인: {type(db_data)}, 길이: {len(db_data) if db_data else 0}")
    if db_data and isinstance(db_data, list) and len(db_data) > 0:
        print(f"📊 DB 데이터 기반 테이블 생성 시작... (데이터 {len(db_data)}개)")
        
        # 첫 번째 행 샘플 출력 (디버깅)
        first_row = db_data[0]
        print(f"🔍 첫 번째 DB 행 샘플 (키 목록): {list(first_row.keys())[:10]}")
        
        # costingTable 생성
        costing_table = []
        
        for idx, row in enumerate(db_data):
            # 컬럼명의 다양한 변형 처리
            activity_name = (
                row.get('activity_name') or 
                row.get('activityName') or 
                row.get('Activity_Name') or 
                row.get('ActivityName') or 
                ''
            )
            
            cost_driver = (
                row.get('cost_driver') or 
                row.get('costDriver') or 
                row.get('Cost_Driver') or 
                row.get('CostDriver') or 
                ''
            )
            
            cost_rate_per_unit = (
                row.get('cost_rate_per_unit') or 
                row.get('costRatePerUnit') or 
                row.get('Cost_Rate_Per_Unit') or 
                row.get('CostRatePerUnit') or 
                0
            )
            
            activity_quantity = (
                row.get('activity_quantity') or 
                row.get('activityQuantity') or 
                row.get('Activity_Quantity') or 
                row.get('ActivityQuantity') or 
                0
            )
            
            total_activity_cost = (
                row.get('total_activity_cost') or 
                row.get('totalActivityCost') or 
                row.get('Total_Activity_Cost') or 
                row.get('TotalActivityCost') or 
                0
            )
            
            fixed_cost = (
                row.get('fixed_cost') or 
                row.get('fixedCost') or 
                row.get('Fixed_Cost') or 
                row.get('FixedCost') or 
                0
            )
            
            variable_cost = (
                row.get('variable_cost') or 
                row.get('variableCost') or 
                row.get('Variable_Cost') or 
                row.get('VariableCost') or 
                0
            )
            
            fixed_variable_ratio = (
                row.get('fixed_variable_ratio') or 
                row.get('fixedVariableRatio') or 
                row.get('Fixed_Variable_Ratio') or 
                row.get('FixedVariableRatio') or 
                ''
            )
            
            activity_type = (
                row.get('activity_type') or 
                row.get('activityType') or 
                row.get('Activity_Type') or 
                row.get('ActivityType') or 
                ''
            )
            
            department = (
                row.get('department') or 
                row.get('Department') or 
                field_values.get('department', '') or 
                ''
            )
            
            potential_savings_cost = (
                row.get('potential_savings_cost') or 
                row.get('potentialSavingsCost') or 
                row.get('Potential_Savings_Cost') or 
                row.get('PotentialSavingsCost') or 
                0
            )
            
            non_essential_activity_cost = (
                row.get('non_essential_activity_cost') or 
                row.get('nonEssentialActivityCost') or 
                row.get('Non_Essential_Activity_Cost') or 
                row.get('NonEssentialActivityCost') or 
                0
            )
            
            productivity_impact_level = (
                row.get('productivity_impact_level') or 
                row.get('productivityImpactLevel') or 
                row.get('Productivity_Impact_Level') or 
                row.get('ProductivityImpactLevel') or 
                ''
            )
            
            cost_pool = (
                row.get('cost_pool') or 
                row.get('costPool') or 
                row.get('Cost_Pool') or 
                row.get('CostPool') or 
                ''
            )
            
            data_reference_date = (
                row.get('data_reference_date') or 
                row.get('dataReferenceDate') or 
                row.get('Data_Reference_Date') or 
                row.get('DataReferenceDate') or 
                ''
            )
            
            # total_activity_cost가 없으면 fixed_cost + variable_cost로 계산
            if not total_activity_cost or total_activity_cost == 0:
                total_activity_cost = (fixed_cost or 0) + (variable_cost or 0)
            
            # cost_rate_per_unit이 없으면 total_activity_cost / activity_quantity으로 계산
            if (not cost_rate_per_unit or cost_rate_per_unit == 0) and activity_quantity and activity_quantity > 0:
                cost_rate_per_unit = total_activity_cost / activity_quantity
            
            # 날짜 형식 변환 (문자열이면 그대로, datetime이면 문자열로)
            if data_reference_date:
                if isinstance(data_reference_date, datetime):
                    data_reference_date = data_reference_date.strftime('%Y-%m-%d')
                elif isinstance(data_reference_date, str):
                    # 이미 문자열이면 그대로 사용
                    pass
                else:
                    data_reference_date = str(data_reference_date)
            
            costing_row = {
                'activity_name': str(activity_name) if activity_name else '',
                'cost_driver': str(cost_driver) if cost_driver else '',
                'cost_rate_per_unit': float(cost_rate_per_unit) if cost_rate_per_unit else 0,
                'activity_quantity': float(activity_quantity) if activity_quantity else 0,
                'total_activity_cost': float(total_activity_cost) if total_activity_cost else 0,
                'fixed_cost': float(fixed_cost) if fixed_cost else 0,
                'variable_cost': float(variable_cost) if variable_cost else 0,
                'fixed_variable_ratio': str(fixed_variable_ratio) if fixed_variable_ratio else '',
                'activity_type': str(activity_type) if activity_type else '',
                'department': str(department) if department else '',
                'potential_savings_cost': float(potential_savings_cost) if potential_savings_cost else 0,
                'non_essential_activity_cost': float(non_essential_activity_cost) if non_essential_activity_cost else 0,
                'productivity_impact_level': str(productivity_impact_level) if productivity_impact_level else '',
                'cost_pool': str(cost_pool) if cost_pool else '',
                'data_reference_date': data_reference_date if data_reference_date else '',
            }
            
            costing_table.append(costing_row)
        
        field_values['costingTable'] = costing_table
        print(f"✅ costingTable: {len(costing_table)}개 행 생성 완료")
    
    else:
        # DB 데이터가 없으면 빈 테이블 구조만 생성
        print("⚠️ DB 데이터가 없거나 비어있습니다. 빈 테이블 구조만 생성합니다.")
        field_values['costingTable'] = []
        
        # 요구사항에서 간단한 정보 추출 시도
        if '주요 활동' in requirements:
            activities_match = re.search(r'주요 활동[:\s]*([^\n]+)', requirements)
            if activities_match:
                activities_text = activities_match.group(1)
                activities = [a.strip() for a in activities_text.split(',')]
                for activity in activities:
                    field_values['costingTable'].append({
                        'activity_name': activity,
                        'cost_driver': '',
                        'cost_rate_per_unit': 0,
                        'activity_quantity': 0,
                        'total_activity_cost': 0,
                        'fixed_cost': 0,
                        'variable_cost': 0,
                        'fixed_variable_ratio': '',
                        'activity_type': '',
                        'department': field_values.get('department', ''),
                        'potential_savings_cost': 0,
                        'non_essential_activity_cost': 0,
                        'productivity_impact_level': '',
                        'cost_pool': '',
                        'data_reference_date': '',
                    })
    
    print(f"✅ [activity_based_costing] 필드 값 추출 완료")

