"""월별 수익 분석 리포트 필드 추출 모듈."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

try:
    from langchain_openai import ChatOpenAI

    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from agents.llm_router import load_agent_prompts
from ...document_utils import get_env

_DOCUMENT_PROMPTS_CACHE: Optional[Dict[str, Any]] = None


def _get_document_prompts() -> Dict[str, Any]:
    """문서 도메인 YAML 프롬프트를 캐시하여 반환."""
    global _DOCUMENT_PROMPTS_CACHE
    if _DOCUMENT_PROMPTS_CACHE is None:
        try:
            _DOCUMENT_PROMPTS_CACHE = load_agent_prompts().get("document", {})
        except Exception:
            _DOCUMENT_PROMPTS_CACHE = {}
    return _DOCUMENT_PROMPTS_CACHE or {}


def _get_conclusion_prompt() -> str:
    """결론 생성 프롬프트를 YAML에서 조회하고, 없으면 기본값을 반환한다."""
    prompts = _get_document_prompts()
    return prompts.get("prompts", {}).get("conclusion_generation", "") or """다음 월별 수익 분석 리포트 데이터를 분석하여 결론 및 제언을 작성해주세요.

=== 문서 정보 ===
제목: {document_title}
요구사항: {requirements}

=== 분석 데이터 요약 ===
{summary_text}
{db_summary}

=== 작업 지시 ===
위의 데이터를 분석하여 다음 형식으로 결론 및 제언을 작성해주세요:
1. 주요 성과: 매출, 수익, 성장 등 긍정적인 내용
2. 개선사항: 부족한 부분, 개선이 필요한 영역
3. 향후 계획: 다음 달을 위한 제안사항

각 항목은 구체적이고 실용적인 내용으로 작성해주세요.

=== 응답 형식 ===
반드시 JSON 형식으로만 응답하세요. 코드 블록(```json) 없이 JSON만 반환하세요.

{
    "conclusionTable": [
        {
            "category": "주요 성과",
            "content": "상세한 성과 내용 (2-3문장)"
        },
        {
            "category": "개선사항",
            "content": "상세한 개선사항 내용 (2-3문장)"
        },
        {
            "category": "향후 계획",
            "content": "상세한 향후 계획 내용 (2-3문장)"
        }
    ]
}

**중요:**
1. JSON 형식만 반환 (설명이나 다른 텍스트 없이)
2. conclusionTable은 최소 3개 항목 이상 포함
3. category는 "주요 성과", "개선사항", "향후 계획" 등으로 작성
4. content는 구체적이고 실용적인 내용으로 작성 (2-3문장)
"""


def extract_monthly_profit_analysis_fields(
    field_values: Dict[str, Any],
    requirements: str,
    document_title: str,
    db_data: Optional[List[Dict[str, Any]]] = None
) -> None:
    """월별 수익 분석 리포트 필드 값을 추출합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (수정됨)
        requirements: 사용자 요구사항
        document_title: 문서 제목
        db_data: DB에서 조회한 데이터 (선택사항)
    """
    print("🔍 [monthly_profit_analysis] 필드 값 추출 시작...")
    
    # 1. 년도, 월 추출
    year_match = re.search(r'(\d{4})\s*년', requirements)
    if year_match:
        field_values['year'] = year_match.group(1)
        print(f"✅ year 추출: {field_values['year']}")
    
    month_match = re.search(r'(\d{1,2})\s*월', requirements)
    if month_match:
        field_values['month'] = month_match.group(1)
        print(f"✅ month 추출: {field_values['month']}")
    
    # 2. DB 데이터 기반 테이블 데이터 생성
    print(f"📊 DB 데이터 확인: {type(db_data)}, 길이: {len(db_data) if db_data else 0}")
    if db_data and isinstance(db_data, list) and len(db_data) > 0:
        print(f"📊 DB 데이터 기반 테이블 생성 시작... (데이터 {len(db_data)}개)")
        
        # 첫 번째 행 샘플 출력 (디버깅)
        first_row = db_data[0]
        print(f"🔍 첫 번째 DB 행 샘플 (키 목록): {list(first_row.keys())[:10]}")
        print(f"🔍 첫 번째 DB 행 샘플 (값 일부): {dict(list(first_row.items())[:5])}")
        
        # 2-1. 지역별 수익현황 테이블 (regionalTable)
        regional_data = {}
        for row in db_data:
            # company_sales 테이블의 실제 컬럼명 사용
            region = row.get('region') or ''
            country = row.get('country') or ''
            city = row.get('city') or ''
            # revenue: 매출액, profit: 수익
            sales = row.get('revenue') or 0
            profit = row.get('profit') or 0
            
            # region 또는 country 중 하나라도 있으면 처리
            if region or country:
                # 둘 다 있으면 조합, 하나만 있으면 그것만 사용
                if region and country:
                    key = f"{region}_{country}"
                elif region:
                    key = f"{region}_"
                elif country:
                    key = f"_{country}"
                else:
                    continue
                
                if key not in regional_data:
                    regional_data[key] = {
                        'region': region if region else '',
                        'country': country if country else '',
                        'sales': 0,
                        'profit': 0
                    }
                regional_data[key]['sales'] += float(sales) if sales else 0
                regional_data[key]['profit'] += float(profit) if profit else 0
        
        if regional_data:
            regional_table = []
            for idx, (key, data) in enumerate(regional_data.items(), 1):
                sales = data['sales']
                profit = data['profit']
                margin = (profit / sales * 100) if sales > 0 else 0
                
                regional_table.append({
                    'no': idx,
                    'region': data['region'],
                    'country': data['country'],
                    'total_sales': int(sales),
                    'total_profit': int(profit),
                    'profit_margin': round(margin, 2)
                })
            
            field_values['regionalTable'] = regional_table
            print(f"✅ regionalTable 생성: {len(regional_table)}개 행")
        
        # 2-2. 제품별 손익분석 테이블 (productTable)
        product_data = {}
        for row in db_data:
            # company_sales 테이블의 실제 컬럼명 사용
            product_name = row.get('product_name') or ''
            category = row.get('product_category') or row.get('product_subcategory') or ''
            manufacturer = row.get('manufacturer') or ''
            # revenue: 매출액, cost_of_sales: 원가, profit: 수익
            sales = row.get('revenue') or 0
            cost = row.get('cost_of_sales') or 0
            profit = row.get('profit') or 0
            
            if product_name:
                if product_name not in product_data:
                    product_data[product_name] = {
                        'product_name': product_name,
                        'category': category,
                        'manufacturer': manufacturer,
                        'sales': 0,
                        'cost': 0,
                        'profit': 0
                    }
                product_data[product_name]['sales'] += float(sales) if sales else 0
                product_data[product_name]['cost'] += float(cost) if cost else 0
                product_data[product_name]['profit'] += float(profit) if profit else 0
        
        if product_data:
            product_table = []
            for idx, (product_name, data) in enumerate(product_data.items(), 1):
                sales = data['sales']
                cost = data['cost']
                profit = data['profit'] if data['profit'] > 0 else (sales - cost)
                margin = (profit / sales * 100) if sales > 0 else 0
                
                product_table.append({
                    'no': idx,
                    'product_name': data['product_name'],
                    'category': data['category'],
                    'manufacturer': data['manufacturer'],
                    'sales_amount': int(sales),
                    'cost_amount': int(cost),
                    'profit_amount': int(profit),
                    'profit_margin': round(margin, 2)
                })
            
            field_values['productTable'] = product_table
            print(f"✅ productTable 생성: {len(product_table)}개 행")
        
        # 2-3. 프로모션 효과분석 테이블 (promotionTable)
        # promotion_name이 있는 경우만 처리
        promotion_data = {}
        for row in db_data:
            promotion_name = row.get('promotion_name') or ''
            # promotion_name이 비어있으면 건너뜀
            if not promotion_name:
                continue
                
            product_name = row.get('product_name') or ''
            # 프로모션 비용과 추가 매출은 현재 컬럼에 직접 없으므로 계산
            # revenue에서 일반 매출과 구분하거나 별도 컬럼이 있을 수 있음
            # 일단 revenue를 사용하되, 추후 개선 필요
            cost = 0  # promotion_cost 컬럼이 없으면 0으로 설정
            additional_sales = row.get('revenue') or 0  # 프로모션 관련 매출
            roi = 0  # ROI는 계산 필요
            
            if promotion_name:
                if promotion_name not in promotion_data:
                    promotion_data[promotion_name] = {
                        'promotion_name': promotion_name,
                        'products': set(),
                        'cost': 0,
                        'additional_sales': 0,
                        'roi': 0
                    }
                if product_name:
                    promotion_data[promotion_name]['products'].add(product_name)
                promotion_data[promotion_name]['cost'] += float(cost) if cost else 0
                promotion_data[promotion_name]['additional_sales'] += float(additional_sales) if additional_sales else 0
                if roi:
                    promotion_data[promotion_name]['roi'] = float(roi)
        
        if promotion_data:
            promotion_table = []
            for idx, (promotion_name, data) in enumerate(promotion_data.items(), 1):
                cost = data['cost']
                additional_sales = data['additional_sales']
                roi = data['roi'] if data['roi'] > 0 else ((additional_sales - cost) / cost * 100) if cost > 0 else 0
                products = ', '.join(list(data['products'])) if data['products'] else ''
                
                promotion_table.append({
                    'no': idx,
                    'promotion_name': promotion_name,
                    'products': products,
                    'total_cost': int(cost),
                    'additional_sales': int(additional_sales),
                    'roi': round(roi, 2),
                    'remarks': ''
                })
            
            field_values['promotionTable'] = promotion_table
            print(f"✅ promotionTable 생성: {len(promotion_table)}개 행")
        
        # 2-4. 판매채널별 요약 테이블 (channelTable)
        channel_data = {}
        for row in db_data:
            # company_sales 테이블의 실제 컬럼명 사용
            channel = row.get('sales_channel') or ''
            # revenue: 매출액, profit: 수익
            sales = row.get('revenue') or 0
            profit = row.get('profit') or 0
            
            if channel:
                if channel not in channel_data:
                    channel_data[channel] = {
                        'channel': channel,
                        'sales': 0,
                        'profit': 0
                    }
                channel_data[channel]['sales'] += float(sales) if sales else 0
                channel_data[channel]['profit'] += float(profit) if profit else 0
        
        if channel_data:
            total_sales = sum(data['sales'] for data in channel_data.values())
            channel_table = []
            for channel, data in channel_data.items():
                sales = data['sales']
                profit = data['profit']
                proportion = (sales / total_sales * 100) if total_sales > 0 else 0
                
                channel_table.append({
                    'channel': channel,
                    'total_sales': int(sales),
                    'total_profit': int(profit),
                    'proportion': round(proportion, 2),
                    'change_rate': 0  # 증감률은 이전 월 데이터와 비교 필요
                })
            
            field_values['channelTable'] = channel_table
            print(f"✅ channelTable 생성: {len(channel_table)}개 행")
        
        # 2-5. 결론 및 제언 테이블 생성 (LLM 사용)
        # conclusionTable이 아직 생성되지 않았으면 LLM으로 생성
        if 'conclusionTable' not in field_values or not field_values.get('conclusionTable'):
            _generate_conclusion_table(field_values, db_data, requirements, document_title)
        else:
            print("✅ conclusionTable이 이미 존재하여 생성 건너뜀")
    
    # 3. 요구사항에서 직접 추출 (DB 데이터가 없는 경우)
    if not db_data or len(db_data) == 0:
        print("⚠️ DB 데이터 없음, 요구사항에서 직접 추출...")
        
        # 기본 테이블 구조 생성
        if 'regionalTable' not in field_values:
            field_values['regionalTable'] = []
        if 'productTable' not in field_values:
            field_values['productTable'] = []
        if 'promotionTable' not in field_values:
            field_values['promotionTable'] = []
        if 'channelTable' not in field_values:
            field_values['channelTable'] = []
        # DB 데이터가 없어도 요구사항 기반으로 결론 생성 시도
        if 'conclusionTable' not in field_values or not field_values.get('conclusionTable'):
            _generate_conclusion_table(field_values, None, requirements, document_title)
    
    print("✅ [monthly_profit_analysis] 필드 값 추출 완료!")


def _generate_conclusion_table(
    field_values: Dict[str, Any],
    db_data: Optional[List[Dict[str, Any]]],
    requirements: str,
    document_title: str
) -> None:
    """LLM을 사용하여 결론 및 제언 테이블을 생성합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (수정됨)
        db_data: DB에서 조회한 데이터
        requirements: 사용자 요구사항
        document_title: 문서 제목
    """
    if not LLM_AVAILABLE:
        print("⚠️ LLM이 사용 불가능하여 결론 및 제언 생성 건너뜀")
        return
    
    api_key = get_env("OPENAI_API_KEY")
    if not api_key:
        print("⚠️ OPENAI_API_KEY가 설정되지 않아 결론 및 제언 생성 건너뜀")
        return
    
    print("🤖 LLM을 사용하여 결론 및 제언 생성 시작...")
    
    try:
        # 생성된 테이블 데이터 요약
        summary_text = ""
        if field_values.get('regionalTable'):
            summary_text += f"\n지역별 수익현황: {len(field_values['regionalTable'])}개 지역\n"
            for row in field_values['regionalTable'][:3]:  # 상위 3개만
                summary_text += f"  - {row.get('region', '')} {row.get('country', '')}: 매출 {row.get('total_sales', 0):,}원, 수익률 {row.get('profit_margin', 0)}%\n"
        
        if field_values.get('productTable'):
            summary_text += f"\n제품별 손익분석: {len(field_values['productTable'])}개 제품\n"
            for row in field_values['productTable'][:3]:  # 상위 3개만
                summary_text += f"  - {row.get('product_name', '')}: 매출 {row.get('sales_amount', 0):,}원, 수익률 {row.get('profit_margin', 0)}%\n"
        
        if field_values.get('channelTable'):
            summary_text += f"\n판매채널별 요약: {len(field_values['channelTable'])}개 채널\n"
            for row in field_values['channelTable'][:3]:  # 상위 3개만
                summary_text += f"  - {row.get('channel', '')}: 매출 {row.get('total_sales', 0):,}원, 비중 {row.get('proportion', 0)}%\n"
        
        # DB 데이터 요약
        db_summary = ""
        if db_data and len(db_data) > 0:
            total_revenue = sum(float(row.get('revenue', 0) or 0) for row in db_data)
            total_profit = sum(float(row.get('profit', 0) or 0) for row in db_data)
            avg_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
            db_summary = f"""
데이터 요약:
- 총 데이터 행 수: {len(db_data)}개
- 총 매출액: {total_revenue:,.0f}원
- 총 수익: {total_profit:,.0f}원
- 평균 수익률: {avg_margin:.2f}%
"""
        
        prompt_template = _get_conclusion_prompt()
        prompt_text = prompt_template.format(
            document_title=document_title,
            requirements=requirements,
            summary_text=summary_text,
            db_summary=db_summary,
        )
        
        llm = ChatOpenAI(
            model=get_env("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.7,  # 창의적인 분석을 위해 약간 높게 설정
            api_key=api_key
        )
        
        print("🔄 LLM 모델 호출 중... (결론 및 제언 생성)")
        response = llm.invoke(prompt_text)
        response_text = response.content.strip()
        
        print(f"📥 LLM 응답 수신 완료 (길이: {len(response_text)}자)")
        print(f"📄 응답 미리보기: {response_text[:200]}...")
        
        # JSON 추출
        json_text = re.sub(r'```json\s*', '', response_text)
        json_text = re.sub(r'```\s*', '', json_text)
        json_text = json_text.strip()
        
        try:
            result = json.loads(json_text)
            
            if isinstance(result, dict) and 'conclusionTable' in result:
                conclusion_table = result['conclusionTable']
                
                if isinstance(conclusion_table, list) and len(conclusion_table) > 0:
                    field_values['conclusionTable'] = conclusion_table
                    print(f"✅ conclusionTable 생성: {len(conclusion_table)}개 항목")
                    for idx, item in enumerate(conclusion_table, 1):
                        category = item.get('category', '')
                        content = item.get('content', '')
                        print(f"  {idx}. {category}: {content[:50]}...")
                else:
                    print("⚠️ conclusionTable이 빈 리스트입니다")
            else:
                print("⚠️ JSON 형식이 올바르지 않습니다 (conclusionTable 키 없음)")
                # 기본값으로 빈 테이블 생성
                field_values['conclusionTable'] = []
                
        except json.JSONDecodeError as exc:
            print(f"⚠️ JSON 파싱 실패: {exc}")
            print(f"응답 내용 (처음 500자): {response_text[:500]}")
            # 기본값으로 빈 테이블 생성
            field_values['conclusionTable'] = []
            
    except Exception as exc:
        print(f"⚠️ 결론 및 제언 생성 실패: {exc}")
        import traceback
        traceback.print_exc()
        # 기본값으로 빈 테이블 생성
        if 'conclusionTable' not in field_values:
            field_values['conclusionTable'] = []








