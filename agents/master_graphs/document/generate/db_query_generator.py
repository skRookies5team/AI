"""DB 쿼리 생성 모듈 - 사용자 요구사항에서 필요한 DB 테이블/컬럼을 LLM으로 추출."""

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
from ..document_utils import get_env

# YAML 캐시
_DOCUMENT_PROMPTS_CACHE: Optional[Dict[str, Any]] = None

# Fallback 프롬프트 (YAML 부재 시 사용)
FALLBACK_DB_QUERY_PROMPT = """사용자의 요구사항을 분석하여 필요한 데이터베이스 테이블과 컬럼 정보를 추출해주세요.

=== 문서 정보 ===
템플릿: {template_name}
제목: {document_title}

=== 사용자 요구사항 ===
{user_requirements}

=== 데이터베이스 스키마 정보 ===
{schema_info}

=== 작업 지시 ===
1. 사용자 요구사항에서 언급된 제품명, 비교 대상을 정확히 추출하세요.
   - 예: "iPhone 15 Pro" → product_names: ["iPhone 15 Pro"]
   - 예: "Galaxy S24 Ultra, Pixel 8 Pro" → product_names: ["Galaxy S24 Ultra", "Pixel 8 Pro"]
   - 예: "iPhone 15 Pro 512GB, Galaxy S24 Ultra 128GB" → product_names: ["iPhone 15 Pro 512GB", "Galaxy S24 Ultra 128GB"] (저장공간 정보 포함)
   - 제품명은 Brand + model_name 조합 또는 model_name만으로도 가능합니다.
   - **중요: 저장공간 정보(128GB, 256GB, 512GB 등)를 포함한 전체 제품명을 그대로 추출하세요.**
   - model_name 컬럼에는 저장공간 정보가 포함되어 있으므로, 사용자가 입력한 제품명을 그대로 사용해야 정확한 매칭이 가능합니다.
2. 위 스키마 정보에서 필요한 테이블과 컬럼을 찾아주세요.
   - mobile_devices 테이블을 사용하세요.
   - 비교 항목에 해당하는 컬럼들(Brand, model_name, Price, Weight, Battery, Display_Size, Processor, Camera, Storage, RAM, Operating_System, Network, Color)을 포함하세요.
3. 비교 대상 제품들이 mobile_devices 테이블에 있는지 확인하세요.
4. 비교 항목(무게, 배터리, 디스플레이 등)에 해당하는 컬럼을 찾아주세요.

=== 응답 형식 ===
반드시 JSON 형식으로만 응답하세요. 코드 블록(```json) 없이 JSON만 반환하세요.

{{
    "tables": ["테이블1", "테이블2"],
    "columns": {{
        "테이블1": ["컬럼1", "컬럼2"],
        "테이블2": ["컬럼3", "컬럼4"]
    }},
    "product_names": ["제품1", "제품2", "제품3"],
    "comparison_items": ["항목1", "항목2", "항목3"],
    "conditions": {{
        "테이블1": "WHERE 조건문"
    }},
    "join_conditions": "JOIN 조건 (필요시)"
}}
"""

# Fallback 스키마 (YAML 부재 시 사용)
FALLBACK_DB_SCHEMAS: Dict[str, str] = {
	"product_comparison_table": """
데이터베이스: ai_agent

테이블 목록:
1. mobile_devices - 스마트폰/모바일 기기 제품 정보
   - 주요 컬럼:
     * company_name (브랜드명, 예: Apple, Samsung, Google 등)
     * model_name (모델명, 예: iPhone 15 Pro 512GB, Galaxy S24 Ultra 128GB, Pixel 8 Pro 256GB 등)
     * launch_price_kor (가격, 숫자)
     * launch_price_usa (가격, 숫자)
     * weight (무게, g 단위)
     * battery_capacity (배터리 용량, mAh 단위)
     * screen_size (디스플레이 크기, 인치)
     * processor (프로세서명)
     * front_camera (카메라 정보, 예: "48MP", "50MP Triple" 등)
     * rear_camera (카메라 정보, 예: "48MP", "50MP Triple" 등)
     * ram (메모리, 예: "8GB", "12GB" 등)
     * 기타 스펙 정보

비고:
- 제품 비교표는 mobile_devices 테이블에서 데이터를 가져옴
- 사용자가 언급한 제품명(model_name 컬럼으로 검색, 예: "iPhone 15 Pro 512GB", "Galaxy S24 Ultra 128GB" 등)
- 비교 항목은 Weight, Battery, Display_Size, Processor, Camera, Storage, RAM, Color, Network, Operating_System, Price 등
- model_name 컬럼에 저장공간 정보가 포함되어 있을 수 있음 (예: "iPhone 15 Pro 512GB")
""",
	"monthly_profit_analysis_report": """
데이터베이스: ai_agent

테이블 목록:
1. company_sales - 회사 매출 및 수익 분석 데이터
   - 주요 컬럼:
     * order_id (주문 ID)
     * order_date (주문일자, DATE 형식 - 여기서 년도, 월 추출 가능)
     * region (지역명, 예: 한국, 미국, 일본, 중국 등)
     * city (도시명)
     * country (국가명, 예: Korea, USA, Japan, China 등)
     * product_name (제품명, 예: iPhone 15 Pro, Galaxy S24, iPad Pro 등)
     * product_category (제품 카테고리, 예: 스마트폰, 태블릿, 노트북 등)
     * product_subcategory (제품 하위 카테고리)
     * manufacturer (제조업체, 예: Apple, Samsung, Google 등)
     * unit_cost (단위 원가, 숫자)
     * unit_price (단위 가격, 숫자)
     * order_quantity (주문 수량, 숫자)
     * cost_of_sales (판매원가, 숫자, 원화 단위)
     * revenue (매출액, 숫자, 원화 단위)
     * profit (수익, 숫자, 원화 단위)
     * sales_channel (판매채널, 예: 온라인, 오프라인, 대리점 등)
     * promotion_name (프로모션명, 예: 신년 특가, 여름 세일 등, NULL 가능)

비고:
- 월별 수익 분석 리포트는 company_sales 테이블에서 데이터를 가져옴
- 사용자가 언급한 기간(년도, 월)은 order_date에서 추출하여 필터링
- 지역별 수익현황: region, country, city, revenue(합계), profit(합계), 수익률(계산)
- 제품별 손익분석: product_name, product_category, manufacturer, revenue(합계), cost_of_sales(합계), profit(합계), 수익률(계산)
- 프로모션 효과분석: promotion_name이 있는 데이터만, product_name, revenue(추가 매출), ROI(계산)
- 판매채널별 요약: sales_channel, revenue(합계), profit(합계), 비중 계산, 증감률
""",
	"activity_based_costing": """
데이터베이스: ai_agent

테이블 목록:
1. activity_based_costing - 활동기준 원가 계산 데이터
   - 주요 컬럼:
     * activity_name (활동명, 예: PCB 조립, 설비 셋업, 품질 검사 등)
     * cost_driver (원가 드라이버, 예: hours, setups, inspections, orders 등)
     * cost_rate_per_unit (단위당 원가율, 숫자)
     * activity_quantity (활동량, 숫자)
     * total_activity_cost (총 활동 원가, 숫자, 원화 단위) 또는 fixed_cost + variable_cost
     * fixed_cost (고정비, 숫자, 원화 단위)
     * variable_cost (변동비, 숫자, 원화 단위)
     * fixed_variable_ratio (고정/변동 비율, 예: "70:30", "80:20" 등)
     * activity_type (활동 유형, 예: 생산, 품질 관리, 배송 등)
     * department (부서/센터명, 예: 생산부서, 품질관리부서 등)
     * potential_savings_cost (절감 가능 비용, 숫자, 원화 단위)
     * non_essential_activity_cost (비필수 활동 원가, 숫자, 원화 단위)
     * productivity_impact_level (생산성 영향 수준, 예: High, Medium, Low)
     * cost_pool (원가 풀, 예: 제조간접비, 관리비 등)
     * data_reference_date (데이터 참조일, DATE 형식 - 여기서 기간 추출 가능)

비고:
- 활동기준 원가 계산서는 activity_based_costing 테이블에서 데이터를 가져옴
- 사용자가 언급한 기간(예: 2024년 1분기)은 data_reference_date에서 추출하여 필터링
- 부서/센터는 department 컬럼으로 필터링
- 활동 유형은 activity_type 컬럼으로 필터링
- 테이블 구조:
  * Activity(활동명): activity_name
  * Overhead(예상 제조간접비): total_activity_cost 또는 (fixed_cost + variable_cost)
  * Per Activity(총 활동량): activity_quantity + cost_driver (예: "60,000 hours")
  * Overhead Rate per Activ(활동당 배부율): cost_rate_per_unit
""",
	"hr_summary_report": """
데이터베이스: ai_agent

테이블 목록:
1. employee - 직원 인사 정보
   - 주요 컬럼:
     * employee_id (직원 ID, 기본키)
     * korean_name (국문 이름) 또는 name_kr
     * english_name (영문 이름) 또는 name_en
     * status (직원 상태, 예: 재직, 휴직, 퇴직, 퇴사예정) 또는 employment_status
     * position (직급, 예: 대리, 과장, 차장, 부장 등) 또는 rank
     * contract_type (계약형태, 예: 정규, 비정규, 인턴, 계약직) 또는 employment_type
     * department (부서명, 예: 개발팀, 영업팀, 인사팀 등)
     * hire_date (입사일, DATE 형식)
     * phone (전화번호)
     * email (이메일)
     * address (주소)

비고:
- 인사정보 요약 리포트는 employee 테이블에서 데이터를 가져옴
- 테이블 구조:
  * 이름: korean_name (국문), english_name (영문)
  * 직원별 상태: status (재직, 휴직, 퇴직, 퇴사예정)
  * 직급: position (대리, 과장, 차장, 부장 등)
  * 계약형태: contract_type (정규, 비정규, 인턴, 계약직)
- 통계 계산:
  * 총 직원 수: 전체 행 수
  * 재직 중: status = '재직'인 직원 수
  * 정규직: contract_type = '정규'인 직원 수
""",
}

GENERIC_SCHEMA_INFO = """
데이터베이스: ai_agent

사용 가능한 테이블:
- 테이블 목록을 확인하여 필요한 테이블과 컬럼을 추출하세요.
"""


def _get_document_prompts() -> Dict[str, Any]:
	"""문서 도메인 YAML 프롬프트 로더 (캐시)."""
	global _DOCUMENT_PROMPTS_CACHE
	if _DOCUMENT_PROMPTS_CACHE is None:
		try:
			_DOCUMENT_PROMPTS_CACHE = load_agent_prompts().get("document", {})
		except Exception:
			_DOCUMENT_PROMPTS_CACHE = {}
	return _DOCUMENT_PROMPTS_CACHE or {}


def _get_template_schema_info(template_name: str) -> str:
	"""템플릿별 데이터베이스 스키마 정보를 YAML에서 우선 조회."""
	prompts = _get_document_prompts()
	schema_from_yaml = prompts.get("db_schemas", {}).get(template_name)
	if schema_from_yaml:
		return schema_from_yaml
	return FALLBACK_DB_SCHEMAS.get(template_name, GENERIC_SCHEMA_INFO)


def generate_db_query_info(
	user_requirements: str,
	template_name: str,
	document_title: str
) -> Optional[Dict[str, Any]]:
	"""사용자 요구사항을 분석하여 필요한 DB 테이블/컬럼 정보를 추출합니다.
	
	Args:
		user_requirements: 사용자 요구사항
		template_name: 템플릿 이름
		document_title: 문서 제목
	
	Returns:
		DB 쿼리 정보 딕셔너리:
		{
			"tables": ["table1", "table2"],
			"columns": {"table1": ["col1", "col2"], "table2": ["col3"]},
			"conditions": {"table1": "WHERE ..."},
			"join_conditions": "..."
		}
	"""
	if not LLM_AVAILABLE:
		print("⚠️ LLM이 사용 불가능하여 DB 쿼리 정보 생성 건너뜀")
		return None

	api_key = get_env("OPENAI_API_KEY")
	if not api_key:
		print("⚠️ OPENAI_API_KEY가 설정되지 않았습니다.")
		return None

	try:
		schema_info = _get_template_schema_info(template_name)

		prompts = _get_document_prompts()
		prompt_template = (
			prompts.get("prompts", {}).get("db_query_generation")
			or FALLBACK_DB_QUERY_PROMPT
		)
		prompt_text = prompt_template.format(
			template_name=template_name,
			document_title=document_title,
			user_requirements=user_requirements,
			schema_info=schema_info,
		)

		llm = ChatOpenAI(
			model=get_env("OPENAI_MODEL", "gpt-4o-mini"),
			temperature=0.3,
			api_key=api_key
		)

		print("🔄 LLM 모델 호출 중... (DB 쿼리 정보 생성)")
		print(f"📝 요구사항 길이: {len(user_requirements)}자")

		response = llm.invoke(prompt_text)
		response_text = response.content.strip()

		print(f"📥 LLM 응답 수신 완료 (길이: {len(response_text)}자)")
		print(f"📄 응답 미리보기: {response_text[:300]}...")

		# JSON 추출
		json_text = re.sub(r'```json\\s*', '', response_text)
		json_text = re.sub(r'```\\s*', '', json_text)
		json_text = json_text.strip()

		try:
			query_info = json.loads(json_text)

			if isinstance(query_info, dict):
				print("✅ DB 쿼리 정보 생성 완료!")
				print(f"📋 테이블: {query_info.get('tables', [])}")
				print(f"📋 제품명: {query_info.get('product_names', [])}")
				print(f"📋 비교 항목: {query_info.get('comparison_items', [])}")
				return query_info

			print("⚠️ JSON 형식 오류")
			return None

		except json.JSONDecodeError as exc:  # pragma: no cover - 로그용
			print(f"⚠️ JSON 파싱 실패: {exc}")
			print(f"응답 내용 (처음 1000자): {response_text[:1000]}")
			return None

	except Exception as exc:  # pragma: no cover - 운영 로그용
		print(f"⚠️ DB 쿼리 정보 생성 실패: {exc}")
		import traceback
		traceback.print_exc()
		return None







