"""DB 커넥터 모듈 - MariaDB 연결 및 쿼리 실행."""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import pymysql
    PYMYSQL_AVAILABLE = True
except ImportError:
    PYMYSQL_AVAILABLE = False
    print("⚠️ pymysql이 설치되지 않았습니다. pip install pymysql 필요")


def get_db_config() -> Optional[Dict[str, Any]]:
    """데이터베이스 연결 설정을 반환합니다."""
    host = os.environ.get("HOST")
    port_str = os.environ.get("DB_PORT")
    user = os.environ.get("DB_USERNAME")
    password = os.environ.get("DB_PASSWORD")
    database = os.environ.get("DB_NAME")
    charset = os.environ.get("DB_CHARSET", "utf8mb4")
    
    # 필수 환경 변수 확인
    if not all([host, port_str, user, password, database]):
        missing = []
        if not host:
            missing.append("HOST")
        if not port_str:
            missing.append("DB_PORT")
        if not user:
            missing.append("DB_USERNAME")
        if not password:
            missing.append("DB_PASSWORD")
        if not database:
            missing.append("DB_NAME")
        print(f"⚠️ 필수 DB 환경 변수가 없습니다: {', '.join(missing)}")
        return None
    
    try:
        port = int(port_str)
    except (ValueError, TypeError):
        print(f"⚠️ DB_PORT 값이 유효하지 않습니다: {port_str}")
        return None
    
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "charset": charset,
        "cursorclass": pymysql.cursors.DictCursor if PYMYSQL_AVAILABLE else None
    }


def execute_query(
    query: str,
    params: Optional[Tuple] = None
) -> List[Dict[str, Any]]:
    """SQL 쿼리를 실행하고 결과를 반환합니다.
    
    Args:
        query: SQL 쿼리 문자열
        params: 쿼리 파라미터 (튜플)
    
    Returns:
        쿼리 결과 리스트 (딕셔너리 리스트)
    """
    if not PYMYSQL_AVAILABLE:
        print("⚠️ pymysql이 설치되지 않아 DB 쿼리 실행 불가")
        return []
    
    config = get_db_config()
    if not config:
        print("⚠️ DB 설정을 가져올 수 없음")
        return []
    
    try:
        print(f"🔌 DB 연결 시도 중... (host: {config['host']}, db: {config['database']})")
        
        connection = pymysql.connect(**config)
        
        try:
            with connection.cursor() as cursor:
                if params:
                    # 파라미터가 튜플이 아닌 경우 튜플로 변환
                    if not isinstance(params, tuple):
                        params = tuple(params)
                    print(f"🔍 쿼리 실행: {query[:200]}...")
                    print(f"🔍 파라미터 개수: {len(params)}개")
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                
                results = cursor.fetchall()
                print(f"📊 조회된 행 수: {len(results) if results else 0}개")
                
                # 결과를 딕셔너리 리스트로 변환
                if results:
                    # DictCursor를 사용하면 딕셔너리 리스트가 반환됨
                    if isinstance(results[0], dict):
                        return list(results)
                    else:
                        # 튜플 리스트를 딕셔너리 리스트로 변환
                        columns = [desc[0] for desc in cursor.description] if cursor.description else []
                        return [dict(zip(columns, row)) for row in results]
                else:
                    return []
                
        finally:
            connection.close()
            
    except Exception as e:
        print(f"⚠️ DB 쿼리 실행 실패: {e}")
        import traceback
        traceback.print_exc()
        return []


def generate_sql_query(
    query_info: Dict[str, Any],
    product_names: Optional[List[str]] = None
) -> Tuple[Optional[str], Optional[Tuple]]:
    """DB 쿼리 정보로부터 SQL 쿼리를 생성합니다.
    
    Args:
        query_info: DB 쿼리 정보 (tables, columns, conditions 등)
        product_names: 제품명 리스트 (필터링용)
    
    Returns:
        (SQL 쿼리 문자열, 파라미터 튜플) 또는 (None, None)
    """
    if not query_info or "tables" not in query_info:
        return None, None
    
    tables = query_info.get("tables", [])
    columns_info = query_info.get("columns", {})
    conditions = query_info.get("conditions", {})
    
    if not tables:
        return None, None
    
    # 첫 번째 테이블을 메인 테이블로 사용
    main_table = tables[0]
    
    # 컬럼 선택
    columns = columns_info.get(main_table, [])
    if not columns:
        # 컬럼이 지정되지 않으면 모든 컬럼 선택
        column_list = "*"
    else:
        column_list = ", ".join([f"`{col}`" for col in columns])
    
    # 기본 쿼리 생성 (mobile_devices 테이블 기본 컬럼 선택)
    if main_table == "mobile_devices" and column_list == "*":
        # 실제 컬럼명을 확인하기 위해 SELECT * 사용 (안전한 방법)
        # 결과에서 컬럼명을 확인하고 필요한 컬럼만 선택
        column_list = "*"  # 일단 모든 컬럼 선택
        print(f"📋 mobile_devices 테이블: 모든 컬럼 선택 (*)")
    
    query = f"SELECT {column_list} FROM `{main_table}`"
    query_params = []
    
    # 제품명 필터 추가 (Model 컬럼으로 정확한 검색)
    if product_names:
        # 각 제품명에 대해 Model 컬럼으로 검색
        product_conditions = []
        
        for product_name in product_names[:3]:  # 최대 3개
            original_name = product_name.strip()
            
            print(f"  🔍 제품명 검색: '{original_name}'")
            
            # model_name 컬럼으로 정확히 검색
            # 전체 제품명으로 model_name 검색
            product_conditions.append("model_name = %s")
            query_params.append(original_name)
            
        if product_conditions:
            # 각 제품명 조건을 OR로 연결
            where_clause = " OR ".join(product_conditions)
            query += f" WHERE ({where_clause})"
    
    # 추가 조건 (conditions는 무시 - 제품명 필터만 사용)
    # conditions는 복잡한 SQL 구문이 포함될 수 있어 오류를 일으킬 수 있으므로 사용하지 않음
    # if main_table in conditions and conditions[main_table]:
    #     condition_str = str(conditions[main_table])
    #     # WHERE 키워드 제거 (이미 WHERE가 쿼리에 있으므로)
    #     condition_str = re.sub(r'^\s*WHERE\s+', '', condition_str, flags=re.IGNORECASE).strip()
    #     
    #     if condition_str:
    #         if "WHERE" in query:
    #             query += f" AND {condition_str}"
    #         else:
    #             query += f" WHERE {condition_str}"
    
    # LIMIT 추가 (최대 3개 제품)
    query += " LIMIT 3"
    
    print(f"📝 생성된 SQL 쿼리: {query}")
    if query_params:
        print(f"📝 쿼리 파라미터 개수: {len(query_params)}개")
        print(f"📝 쿼리 파라미터 샘플 (처음 3개): {query_params[:3]}")
        # 튜플로 변환하여 반환
        return query, tuple(query_params)
    
    return query, None


def fetch_product_comparison_data(
    query_info: Dict[str, Any],
    product_names: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """제품 비교표를 위한 데이터를 조회합니다.
    
    각 제품명에 대해 개별적으로 쿼리를 실행하여 정확히 하나의 결과만 반환합니다.
    
    Args:
        query_info: DB 쿼리 정보
        product_names: 비교할 제품명 리스트
    
    Returns:
        제품 데이터 리스트 (각 제품명에 대해 하나씩, 최대 3개)
    """
    if not query_info or not product_names:
        return []
    
    tables = query_info.get("tables", [])
    if not tables:
        return []
    
    main_table = tables[0]
    
    # 각 제품명에 대해 개별적으로 쿼리 실행
    all_results = []
    
    for product_name in product_names[:3]:  # 최대 3개
        original_name = product_name.strip()
        
        print(f"  🔍 제품명 개별 검색: '{original_name}'")
        
        # model_name 컬럼으로 검색하는 쿼리 생성
        # 전체 제품명으로 정확히 검색
        
        # 1단계: 정확한 일치 검색 (가장 우선)
        # 대소문자 구분 없이 검색하기 위해 LOWER() 함수 사용
        query_exact = f"SELECT * FROM `{main_table}` WHERE LOWER(model_name) = LOWER(%s) LIMIT 1"
        params_exact = (original_name,)
        result_exact = execute_query(query_exact, params_exact)
        
        if result_exact and len(result_exact) > 0:
            matched_model = str(result_exact[0].get('model_name')).strip()
            matched_product_id = result_exact[0].get('product_id', 'N/A')
            all_results.append(result_exact[0])
            print(f"    ✅ '{original_name}' 정확 일치 성공: {matched_model} (product_id: {matched_product_id})")
            continue
        else:
            print(f"    ⚠️ '{original_name}' 정확 일치 실패, 포함 검색으로 진행...")
        
        # 2단계: 포함 검색 (정확 일치 실패 시) - 입력값의 모든 부분이 포함된 결과만 선택
        # 입력값을 단어로 분리하여 각 단어가 포함되어 있는지 확인
        # 대소문자 구분 없이 검색
        query_contains = f"SELECT * FROM `{main_table}` WHERE LOWER(model_name) LIKE LOWER(%s) LIMIT 20"
        contains = f"%{original_name}%"
        params_contains = (contains,)
        result_contains = execute_query(query_contains, params_contains)
        
        print(f"    🔍 포함 검색 결과: {len(result_contains) if result_contains else 0}개 발견")
        
        if result_contains and len(result_contains) > 0:
            # 입력값의 모든 단어가 포함된 결과 중에서 가장 정확한 매칭 선택
            best_match = None
            best_score = 0
            
            # 입력값을 단어로 분리 (공백 기준)
            input_words = original_name.lower().split()
            
            # 숫자+단위 패턴 추출 (예: "512GB", "128GB") - 숫자와 단위를 함께 추출
            input_number_patterns = re.findall(r'(\d+)\s*(gb|tb)', original_name.lower())
            # (숫자, 단위) 튜플 리스트로 변환 (예: [("512", "gb"), ("128", "gb")])
            input_number_tuples = [(num, unit) for num, unit in input_number_patterns]
            
            for row in result_contains:
                matched_model = str(row.get('model_name')).strip()
                matched_product_id = row.get('product_id', 'N/A')
                matched_lower = matched_model.lower()
                original_lower = original_name.lower()
                
                # 매칭 점수 계산
                score = 0
                
                # 1. 정확 일치 (이미 1단계에서 실패했으므로 여기서는 발생하지 않지만 체크)
                if matched_lower == original_lower:
                    score = 100
                    print(f"      ✅ 정확 일치 발견: {matched_model} (product_id: {matched_product_id})")
                # 2. 시작 일치
                elif matched_lower.startswith(original_lower):
                    score = 90
                    print(f"      ✅ 시작 일치: {matched_model} (product_id: {matched_product_id})")
                # 3. 입력값의 모든 단어가 포함되어 있는지 확인
                elif all(word in matched_lower for word in input_words if len(word) > 1):
                    # 숫자+단위 패턴이 있으면 정확히 일치해야 함
                    if input_number_tuples:
                        matched_number_tuples = re.findall(r'(\d+)\s*(gb|tb)', matched_lower)
                        print(f"      🔍 숫자 패턴 비교: 입력={input_number_tuples}, 매칭={matched_number_tuples}")
                        
                        # 입력값의 모든 숫자+단위 조합이 매칭된 값에도 정확히 포함되어 있는지 확인
                        all_patterns_match = all(
                            any(
                                input_num == matched_num and input_unit == matched_unit
                                for matched_num, matched_unit in matched_number_tuples
                            )
                            for input_num, input_unit in input_number_tuples
                        )
                        
                        if not all_patterns_match:
                            # 숫자+단위 패턴이 일치하지 않으면 제외
                            score = 0
                            print(f"      ❌ 숫자 패턴 불일치로 제외: {matched_model} (product_id: {matched_product_id})")
                            continue
                        else:
                            print(f"      ✅ 숫자 패턴 일치: {matched_model} (product_id: {matched_product_id})")
                    
                    # 모든 단어가 포함되어 있고, 숫자 패턴도 일치하면 점수 부여
                    word_match_count = sum(1 for word in input_words if word in matched_lower)
                    total_words = len(input_words)
                    
                    if word_match_count == total_words:
                        # 모든 단어가 포함되어 있음
                        # 입력값의 길이와 매칭된 값의 길이 차이를 고려
                        length_diff = abs(len(matched_lower) - len(original_lower))
                        if length_diff == 0:
                            score = 85  # 길이도 동일
                        elif length_diff <= 5:
                            score = 80  # 길이 차이가 작음
                        else:
                            score = 70  # 길이 차이가 큼
                    else:
                        score = 50  # 일부 단어만 일치
                else:
                    score = 0  # 모든 단어가 포함되지 않음
                    print(f"      ❌ 단어 불일치로 제외: {matched_model} (product_id: {matched_product_id})")
                
                if score > best_score:
                    best_score = score
                    best_match = row
                    print(f"      📊 현재 최고 점수: {score} ({matched_model}, product_id: {matched_product_id})")
            
            if best_match and best_score >= 50:  # 최소 매칭 점수
                matched_model = str(best_match.get('model_name')).strip()
                matched_product_id = best_match.get('product_id', 'N/A')
                all_results.append(best_match)
                print(f"    ✅ '{original_name}' 부분 일치 성공 (점수: {best_score}): {matched_model} (product_id: {matched_product_id})")
                continue
            else:
                print(f"    ⚠️ '{original_name}' 적절한 매칭 결과 없음 (최고 점수: {best_score})")
                if result_contains:
                    print(f"    💡 발견된 후보들:")
                    for idx, row in enumerate(result_contains[:5], 1):
                        candidate_model = str(row.get('model_name', '')).strip()
                        candidate_id = row.get('product_id', 'N/A')
                        print(f"      {idx}. {candidate_model} (product_id: {candidate_id})")
        
        # 매칭 실패
        print(f"    ⚠️ '{original_name}' 매칭 실패")
    
    if all_results:
        print(f"✅ DB 데이터 조회 성공: {len(all_results)}개 제품")
        
        # 첫 번째 결과에서 실제 컬럼명 확인
        if all_results and len(all_results) > 0:
            first_row = all_results[0]
            actual_columns = list(first_row.keys())
            print(f"📋 실제 조회된 컬럼명: {actual_columns[:10]}... (총 {len(actual_columns)}개)")
        
        for idx, row in enumerate(all_results, 1):
            # 대소문자 구분 없이 brand, model_name 찾기
            brand = None
            model = None
            for key in row.keys():
                key_lower = key.lower()
                if key_lower in [ 'company_name']:
                    brand = row[key]
                elif key_lower in [ 'model_name']:
                    model = row[key]
            
            brand_str = str(brand) if brand else ''
            model_str = str(model) if model else ''
            print(f"  제품 {idx}: {brand_str} {model_str}".strip())
    else:
        print(f"⚠️ DB 데이터 조회 결과 없음")
        print(f"💡 제품명을 확인하거나 mobile_devices 테이블에 데이터가 있는지 확인하세요.")
    
    return all_results


def fetch_monthly_profit_analysis_data(
    query_info: Dict[str, Any],
    year: Optional[int] = None,
    month: Optional[int] = None,
    regions: Optional[List[str]] = None,
    product_categories: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """월별 수익 분석 리포트를 위한 데이터를 조회합니다.
    
    Args:
        query_info: DB 쿼리 정보
        year: 년도 (선택사항)
        month: 월 (선택사항)
        regions: 지역 리스트 (선택사항)
        product_categories: 제품 카테고리 리스트 (선택사항)
    
    Returns:
        수익 분석 데이터 리스트
    """
    if not query_info:
        return []
    
    tables = query_info.get("tables", [])
    if not tables:
        return []
    
    main_table = tables[0]  # company_sales 테이블
    
    print(f"📊 [월별 수익 분석] DB 데이터 조회 시작...")
    print(f"  테이블: {main_table}")
    print(f"  년도: {year}, 월: {month}")
    print(f"  지역: {regions}")
    print(f"  제품 카테고리: {product_categories}")
    
    # SQL 쿼리 생성
    query = f"SELECT * FROM `{main_table}` WHERE 1=1"
    query_params = []
    
    # 년도, 월 필터 (order_date에서 추출)
    if year or month:
        if year and month:
            # YEAR()와 MONTH() 함수 사용
            query += " AND YEAR(order_date) = %s AND MONTH(order_date) = %s"
            query_params.extend([year, month])
        elif year:
            query += " AND YEAR(order_date) = %s"
            query_params.append(year)
        elif month:
            query += " AND MONTH(order_date) = %s"
            query_params.append(month)
    
    # 지역 필터 (대소문자 구분 없이 검색, 부분 일치 지원)
    # 한국/한국어/ Korea/KOREA 등 다양한 변형 지원
    if regions:
        region_mapping = {
            '한국': ['한국', 'Korea', 'KOREA', 'South Korea', 'SOUTH KOREA'],
            '미국': ['미국', 'USA', 'United States', 'US', 'America'],
            '일본': ['일본', 'Japan', 'JAPAN'],
            '중국': ['중국', 'China', 'CHINA'],
            '유럽': ['유럽', 'Europe', 'EUROPE', 'EU']
        }
        
        region_conditions = []
        for region in regions:
            # 지역 매핑에서 해당하는 모든 변형 찾기
            region_variants = region_mapping.get(region, [region])
            region_variants.append(region)  # 원본도 추가
            
            # 각 변형에 대해 region 또는 country 컬럼에서 검색
            for variant in set(region_variants):  # 중복 제거
                region_conditions.append("(LOWER(region) LIKE LOWER(%s) OR LOWER(country) LIKE LOWER(%s))")
                region_param = f"%{variant}%"
                query_params.extend([region_param, region_param])
        
        if region_conditions:
            query += " AND (" + " OR ".join(region_conditions) + ")"
    
    # 제품 카테고리 필터 (대소문자 구분 없이 검색, 부분 일치 지원)
    if product_categories:
        category_conditions = []
        for category in product_categories:
            category_conditions.append("LOWER(product_category) LIKE LOWER(%s)")
            query_params.append(f"%{category}%")
        
        if category_conditions:
            query += " AND (" + " OR ".join(category_conditions) + ")"
    
    query += " ORDER BY region, product_name, sales_channel"
    
    print(f"📝 생성된 SQL 쿼리: {query}")
    if query_params:
        print(f"📝 쿼리 파라미터: {query_params}")
    
    results = execute_query(query, tuple(query_params) if query_params else None)
    
    if results:
        print(f"✅ DB 데이터 조회 성공: {len(results)}개 행")
        # 첫 번째 결과에서 실제 컬럼명 확인
        if len(results) > 0:
            first_row = results[0]
            actual_columns = list(first_row.keys())
            print(f"📋 실제 조회된 컬럼명: {actual_columns[:10]}... (총 {len(actual_columns)}개)")
    else:
        print(f"⚠️ DB 데이터 조회 결과 없음")
    
    return results if results else []


def fetch_activity_based_costing_data(
    query_info: Dict[str, Any],
    user_requirements: str
) -> List[Dict[str, Any]]:
    """activity_based_costing 테이블에서 데이터를 조회합니다.
    
    Args:
        query_info: DB 쿼리 정보
        user_requirements: 사용자 요구사항
    
    Returns:
        조회된 데이터 리스트
    """
    if not PYMYSQL_AVAILABLE:
        print("⚠️ pymysql이 사용 불가능하여 DB 조회 건너뜀")
        return []
    
    db_config = get_db_config()
    if not db_config:
        print("⚠️ DB 설정이 없어 조회를 건너뜀")
        return []
    
    print(f"🔍 [activity_based_costing] DB 데이터 조회 시작...")
    
    # 사용자 요구사항에서 기간, 부서, 활동 유형 추출
    import re
    
    # 기간 추출 (예: "2024년 1분기")
    period_pattern = re.search(r'(\d{4})\s*년\s*(\d{1,2})\s*분기', user_requirements)
    year = None
    quarter = None
    if period_pattern:
        year = int(period_pattern.group(1))
        quarter = int(period_pattern.group(2))
        print(f"📅 기간 추출: {year}년 {quarter}분기")
    else:
        year_pattern = re.search(r'(\d{4})\s*년', user_requirements)
        if year_pattern:
            year = int(year_pattern.group(1))
            print(f"📅 년도 추출: {year}년")
    
    # 부서 한글-영어 매핑
    department_mapping = {
        '생산부서': ['Operations', 'Production', 'Manufacturing'],
        '생산': ['Operations', 'Production', 'Manufacturing'],
        '품질관리부서': ['Quality Management', 'Quality Control', 'QC'],
        '품질관리': ['Quality Management', 'Quality Control', 'QC'],
        '품질': ['Quality Management', 'Quality Control', 'QC'],
        '연구개발부서': ['R&D', 'Research and Development', 'Research'],
        '연구개발': ['R&D', 'Research and Development', 'Research'],
        'R&D': ['R&D', 'Research and Development', 'Research'],
        '고객서비스부서': ['Customer Service', 'CS', 'Support'],
        '고객서비스': ['Customer Service', 'CS', 'Support'],
        '구매부서': ['Purchasing', 'Procurement', 'Purchase'],
        '구매': ['Purchasing', 'Procurement', 'Purchase'],
        '영업부서': ['Sales', 'Business Development'],
        '영업': ['Sales', 'Business Development'],
        '마케팅부서': ['Marketing', 'Brand Management'],
        '마케팅': ['Marketing', 'Brand Management'],
        '인사부서': ['HR', 'Human Resources', 'Personnel'],
        '인사': ['HR', 'Human Resources', 'Personnel'],
        '재무부서': ['Finance', 'Accounting', 'Financial'],
        '재무': ['Finance', 'Accounting', 'Financial'],
        'IT부서': ['IT', 'Information Technology', 'Tech'],
        'IT': ['IT', 'Information Technology', 'Tech'],
    }
    
    # 부서 추출
    departments_kr = []
    dept_match = re.search(r'부서/센터[:\s]*([^,\n]+)', user_requirements)
    if dept_match:
        dept_text = dept_match.group(1).strip()
        departments_kr = [d.strip() for d in dept_text.split(',')]
    else:
        dept_match = re.search(r'부서[:\s]*([^,\n]+)', user_requirements)
        if dept_match:
            dept_text = dept_match.group(1).strip()
            departments_kr = [d.strip() for d in dept_text.split(',')]
    
    # 한글 부서명을 영어로 변환
    departments = []
    for dept_kr in departments_kr:
        dept_kr_clean = dept_kr.strip()
        # 매핑에서 찾기
        if dept_kr_clean in department_mapping:
            departments.extend(department_mapping[dept_kr_clean])
            print(f"  🔄 '{dept_kr_clean}' -> {department_mapping[dept_kr_clean]}")
        else:
            # 매핑에 없으면 부분 일치로 찾기
            matched = False
            for kr_key, en_values in department_mapping.items():
                if kr_key in dept_kr_clean or dept_kr_clean in kr_key:
                    departments.extend(en_values)
                    print(f"  🔄 '{dept_kr_clean}' (부분 일치) -> {en_values}")
                    matched = True
                    break
            if not matched:
                # 매핑에 없으면 원본도 추가 (영어일 수도 있음)
                departments.append(dept_kr_clean)
                print(f"  ⚠️ '{dept_kr_clean}' 매핑 없음, 원본 사용")
    
    # 중복 제거
    departments = list(set(departments))
    
    if departments:
        print(f"🏢 부서 추출 (한글->영어 변환): {departments_kr} -> {departments}")
    
    # 활동 유형 한글-영어 매핑
    activity_type_mapping = {
        '생산': ['Production', 'Manufacturing', 'Manufacturing Operations'],
        '품질 관리': ['Quality Management', 'Quality Control', 'QC', 'Quality Assurance'],
        '품질관리': ['Quality Management', 'Quality Control', 'QC', 'Quality Assurance'],
        '품질': ['Quality Management', 'Quality Control', 'QC', 'Quality Assurance'],
        '배송': ['Shipping', 'Delivery', 'Logistics', 'Distribution'],
        '물류': ['Logistics', 'Shipping', 'Distribution', 'Supply Chain'],
        '개발': ['Development', 'R&D', 'Research and Development'],
        '연구개발': ['Development', 'R&D', 'Research and Development'],
        '지원': ['Support', 'Customer Support', 'Technical Support'],
        '고객지원': ['Support', 'Customer Support', 'Technical Support'],
        '판매': ['Sales', 'Sales Operations'],
        '영업': ['Sales', 'Sales Operations'],
        '마케팅': ['Marketing', 'Brand Management'],
        '구매': ['Purchasing', 'Procurement'],
        '인사': ['HR', 'Human Resources', 'Personnel'],
        '재무': ['Finance', 'Accounting'],
        'IT': ['IT', 'Information Technology', 'Tech Support'],
    }
    
    # 활동 유형 추출
    activity_types_kr = []
    activity_match = re.search(r'활동 유형[:\s]*([^,\n]+)', user_requirements)
    if activity_match:
        activity_text = activity_match.group(1).strip()
        activity_types_kr = [a.strip() for a in activity_text.split(',')]
    
    # 한글 활동 유형을 영어로 변환
    activity_types = []
    for activity_kr in activity_types_kr:
        activity_kr_clean = activity_kr.strip()
        # 매핑에서 찾기
        if activity_kr_clean in activity_type_mapping:
            activity_types.extend(activity_type_mapping[activity_kr_clean])
            print(f"  🔄 '{activity_kr_clean}' -> {activity_type_mapping[activity_kr_clean]}")
        else:
            # 매핑에 없으면 부분 일치로 찾기
            matched = False
            for kr_key, en_values in activity_type_mapping.items():
                if kr_key in activity_kr_clean or activity_kr_clean in kr_key:
                    activity_types.extend(en_values)
                    print(f"  🔄 '{activity_kr_clean}' (부분 일치) -> {en_values}")
                    matched = True
                    break
            if not matched:
                # 매핑에 없으면 원본도 추가 (영어일 수도 있음)
                activity_types.append(activity_kr_clean)
                print(f"  ⚠️ '{activity_kr_clean}' 매핑 없음, 원본 사용")
    
    # 중복 제거
    activity_types = list(set(activity_types))
    
    if activity_types:
        print(f"📋 활동 유형 추출 (한글->영어 변환): {activity_types_kr} -> {activity_types}")
    
    # SQL 쿼리 생성
    query = "SELECT * FROM activity_based_costing WHERE 1=1"
    query_params = []
    
    # 기간 필터링 (data_reference_date 기반)
    if year:
        if quarter:
            # 분기별 필터링
            start_month = (quarter - 1) * 3 + 1
            end_month = quarter * 3
            query += " AND YEAR(data_reference_date) = %s AND MONTH(data_reference_date) >= %s AND MONTH(data_reference_date) <= %s"
            query_params.extend([year, start_month, end_month])
        else:
            # 년도만 필터링
            query += " AND YEAR(data_reference_date) = %s"
            query_params.append(year)
    
    # 부서 필터링 (대소문자 구분 없이 검색)
    if departments:
        dept_conditions = []
        for dept in departments:
            dept_conditions.append("LOWER(department) LIKE LOWER(%s)")
            query_params.append(f"%{dept}%")
        
        if dept_conditions:
            query += " AND (" + " OR ".join(dept_conditions) + ")"
    
    # 활동 유형 필터링 (대소문자 구분 없이 검색)
    if activity_types:
        activity_conditions = []
        for activity_type in activity_types:
            activity_conditions.append("LOWER(activity_type) LIKE LOWER(%s)")
            query_params.append(f"%{activity_type}%")
        
        if activity_conditions:
            query += " AND (" + " OR ".join(activity_conditions) + ")"
    
    query += " ORDER BY department, activity_type, activity_name"
    
    print(f"📝 생성된 SQL 쿼리: {query}")
    if query_params:
        print(f"📝 쿼리 파라미터: {query_params}")
    
    results = execute_query(query, tuple(query_params) if query_params else None)
    
    if results:
        print(f"✅ DB 데이터 조회 성공: {len(results)}개 행")
        # 첫 번째 결과에서 실제 컬럼명 확인
        if len(results) > 0:
            first_row = results[0]
            actual_columns = list(first_row.keys())
            print(f"📋 실제 조회된 컬럼명: {actual_columns} (총 {len(actual_columns)}개)")
            print(f"📋 첫 번째 행 데이터 샘플:")
            print(f"  - name: {first_row.get('name', 'N/A')}")
            print(f"  - dept: {first_row.get('dept', 'N/A')}")
            print(f"  - position: {first_row.get('position', 'N/A')}")
            print(f"  - job_title: {first_row.get('job_title', 'N/A')}")
            print(f"  - employment_status: {first_row.get('employment_status', 'N/A')}")
            print(f"  - job_role: {first_row.get('job_role', 'N/A')}")
            print(f"  - email: {first_row.get('email', 'N/A')}")
    else:
        print(f"⚠️ DB 데이터 조회 결과 없음 - 쿼리 실행은 되었지만 결과가 비어있습니다.")
        print(f"   실행된 쿼리: {query}")
        if query_params:
            print(f"   쿼리 파라미터: {query_params}")
    
    return results if results else []


def fetch_hr_summary_report_data(
    query_info: Dict[str, Any],
    user_requirements: str
) -> List[Dict[str, Any]]:
    """employee 테이블에서 인사정보 데이터를 조회합니다.
    
    Args:
        query_info: DB 쿼리 정보
        user_requirements: 사용자 요구사항
    
    Returns:
        조회된 데이터 리스트
    """
    if not PYMYSQL_AVAILABLE:
        print("⚠️ pymysql이 사용 불가능하여 DB 조회 건너뜀")
        return []
    
    db_config = get_db_config()
    if not db_config:
        print("⚠️ DB 설정이 없어 조회를 건너뜀")
        return []
    
    print(f"🔍 [hr_summary_report] DB 데이터 조회 시작...")
    print(f"📝 user_requirements: {user_requirements[:200]}...")  # 요구사항 일부 출력
    
    # 사용자 요구사항에서 필터 조건 추출
    import re
    
    # 부서 추출 (다양한 형식 지원)
    departments = []
    # "- 부서: 개발팀, 마케팅팀" 형식 및 여러 줄 형식 지원
    dept_patterns = [
        r'[-•]\s*부서[:\s]*([^\n]+)',  # "- 부서: ..." 형식
        r'^부서[:\s]*([^\n]+)',        # 줄 시작 "부서: ..." 형식
        r'부서[:\s]*([^,\n]+)',        # "부서: ..." 형식
        r'부서\s*=\s*([^,\n]+)',       # "부서=..." 형식
    ]
    
    for pattern in dept_patterns:
        dept_matches = re.finditer(pattern, user_requirements, re.IGNORECASE | re.MULTILINE)
        for dept_match in dept_matches:
            dept_text = dept_match.group(1).strip()
            # 쉼표로 분리하여 여러 부서 처리
            dept_list = [d.strip() for d in dept_text.split(',') if d.strip()]
            departments.extend(dept_list)
    
    # 중복 제거
    departments = list(set(departments))
    
    if departments:
        print(f"🏢 부서 추출: {departments}")
    else:
        print(f"⚠️ 부서 필터 조건을 찾을 수 없습니다. 전체 데이터를 조회합니다.")
    
    # 직급 추출 (다양한 형식 지원)
    positions = []
    # "- 직급: 과장, 차장" 형식 및 여러 줄 형식 지원
    position_patterns = [
        r'[-•]\s*직급[:\s]*([^\n]+)',  # "- 직급: ..." 형식
        r'^직급[:\s]*([^\n]+)',        # 줄 시작 "직급: ..." 형식
        r'직급[:\s]*([^,\n]+)',        # "직급: ..." 형식
        r'직급\s*=\s*([^,\n]+)',       # "직급=..." 형식
    ]
    
    for pattern in position_patterns:
        position_matches = re.finditer(pattern, user_requirements, re.IGNORECASE | re.MULTILINE)
        for position_match in position_matches:
            position_text = position_match.group(1).strip()
            # 쉼표로 분리하여 여러 직급 처리
            position_list = [p.strip() for p in position_text.split(',') if p.strip()]
            positions.extend(position_list)
    
    # 중복 제거
    positions = list(set(positions))
    
    if positions:
        print(f"👔 직급 추출: {positions}")
    else:
        print(f"⚠️ 직급 필터 조건을 찾을 수 없습니다. 전체 데이터를 조회합니다.")
    
    # SQL 쿼리 생성
    query = "SELECT * FROM employee WHERE 1=1"
    query_params = []
    
    # 부서 필터링 (대소문자 구분 없이 검색) - dept 컬럼 사용
    # 한글 검색을 위해 LIKE 패턴 사용 (LOWER는 한글에 대해 제대로 작동하지 않을 수 있음)
    if departments:
        dept_conditions = []
        for dept in departments:
            # 부서명에 '팀'이 포함되어 있으면 제거하지 않고 그대로 검색
            # 한글 검색을 위해 LIKE만 사용 (LOWER 제거)
            dept_conditions.append("dept LIKE %s")
            query_params.append(f"%{dept}%")
        
        if dept_conditions:
            query += " AND (" + " OR ".join(dept_conditions) + ")"
        print(f"🏢 부서 필터 조건 추가: {departments}")
        print(f"🏢 부서 필터 SQL: {query.split('AND')[-1] if 'AND' in query else ''}")
    
    # 직급 필터링 (대소문자 구분 없이 검색) - position 또는 job_title 컬럼 사용
    # position 컬럼에 "3 책임", "2 책임" 형식으로 저장되어 있으므로 부분 매칭 사용
    if positions:
        position_conditions = []
        for pos in positions:
            # 한글 검색을 위해 LIKE만 사용 (LOWER 제거)
            # position 컬럼에 "3 책임" 형식이 있으므로 부분 매칭으로 검색
            position_conditions.append("(position LIKE %s OR job_title LIKE %s)")
            query_params.append(f"%{pos}%")
            query_params.append(f"%{pos}%")
        
        if position_conditions:
            query += " AND (" + " OR ".join(position_conditions) + ")"
        print(f"👔 직급 필터 조건 추가: {positions}")
        print(f"👔 직급 필터 SQL: {query.split('AND')[-1] if 'AND' in query else ''}")
    
    # ORDER BY 절: 실제 컬럼명 사용 (dept, position, name)
    query += " ORDER BY dept, position, name"
    
    # 쿼리 파라미터를 튜플로 변환
    query_params_tuple = tuple(query_params) if query_params else None
    
    print(f"🔍 최종 SQL 쿼리: {query}")
    if query_params_tuple:
        print(f"🔍 쿼리 파라미터: {query_params_tuple}")
    
    results = execute_query(query, query_params_tuple)
    
    if results:
        print(f"✅ DB 데이터 조회 성공: {len(results)}개 행")
        # 첫 번째 결과에서 실제 컬럼명 확인
        if len(results) > 0:
            first_row = results[0]
            actual_columns = list(first_row.keys())
            print(f"📋 실제 조회된 컬럼명: {actual_columns} (총 {len(actual_columns)}개)")
            print(f"📋 첫 번째 행 데이터 샘플:")
            print(f"  - name: {first_row.get('name', 'N/A')}")
            print(f"  - dept: {first_row.get('dept', 'N/A')}")
            print(f"  - position: {first_row.get('position', 'N/A')}")
            print(f"  - job_title: {first_row.get('job_title', 'N/A')}")
            print(f"  - employment_status: {first_row.get('employment_status', 'N/A')}")
            print(f"  - job_role: {first_row.get('job_role', 'N/A')}")
            print(f"  - email: {first_row.get('email', 'N/A')}")
    else:
        print(f"⚠️ DB 데이터 조회 결과 없음 - 쿼리 실행은 되었지만 결과가 비어있습니다.")
        print(f"   실행된 쿼리: {query}")
        if query_params_tuple:
            print(f"   쿼리 파라미터: {query_params_tuple}")
        
        # 디버깅: 전체 데이터 조회 시도
        print(f"🔍 디버깅: 필터 없이 전체 데이터 조회 시도...")
        test_query = "SELECT * FROM employee LIMIT 5"
        test_results = execute_query(test_query, None)
        if test_results:
            print(f"✅ 전체 데이터 조회 성공: {len(test_results)}개 행 (샘플)")
            if len(test_results) > 0:
                test_row = test_results[0]
                print(f"📋 샘플 데이터:")
                print(f"  - dept: {test_row.get('dept', 'N/A')}")
                print(f"  - position: {test_row.get('position', 'N/A')}")
                print(f"  - job_title: {test_row.get('job_title', 'N/A')}")
        else:
            print(f"⚠️ 전체 데이터 조회도 실패했습니다. 테이블이 비어있거나 접근 권한이 없을 수 있습니다.")
    
    return results if results else []

