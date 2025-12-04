# RDB 기반 데이터 조회 통합 요약

## 개요
사용자 요구사항을 분석하여 RDB에서 필요한 데이터를 자동으로 조회하고, HTML 문서 템플릿에 기입하는 기능을 추가했습니다.

## 생성된 모듈

### 1. `db_query_generator.py`
- **기능**: LLM을 사용하여 사용자 요구사항에서 필요한 DB 테이블/컬럼 정보 추출
- **주요 함수**:
  - `generate_db_query_info()`: 사용자 요구사항 분석 → DB 쿼리 정보 생성

### 2. `db_connector.py`
- **기능**: MariaDB 연결 및 쿼리 실행
- **주요 함수**:
  - `get_db_config()`: DB 연결 설정 반환
  - `execute_query()`: SQL 쿼리 실행
  - `generate_sql_query()`: 쿼리 정보로부터 SQL 생성
  - `fetch_product_comparison_data()`: 제품 비교표용 데이터 조회

### 3. `field_extractors/product_comparison.py`
- **기능**: 제품 비교표 템플릿의 필드 값을 요구사항과 DB 데이터에서 추출
- **주요 함수**:
  - `extract_product_comparison_fields()`: DB 데이터 기반 필드 추출

### 4. `html_filler.py` 업데이트
- **추가 기능**:
  - `comparisonTable` 테이블 채우기 로직 추가
  - `_handle_product_comparison_special()`: 제품 비교표 특수 처리

## 작업 흐름

### 기존 방식 (사용자 작성문 기반)
```
사용자 요구사항 → LLM → HTML 필드 기입
```

### 새로운 방식 (RDB 기반)
```
사용자 요구사항 → LLM(DB 쿼리 정보 추출) → RDB 데이터 조회 → HTML 필드 기입
```

## 제품 비교표(`product_comparison_table.html`) 처리 흐름

1. **사용자 요구사항 분석**
   - 예: "iPhone 15 Pro, Samsung Galaxy S24, Google Pixel 8 비교"

2. **LLM으로 DB 쿼리 정보 추출**
   - 필요한 테이블: `Mobiles_Dataset_(2025)`
   - 필요한 컬럼: `Brand`, `Model`, `Weight`, `Battery`, `Display_Size`, etc.
   - 제품명: `["iPhone 15 Pro", "Samsung Galaxy S24", "Google Pixel 8"]`

3. **RDB에서 데이터 조회**
   - SQL 쿼리 생성 및 실행
   - 제품별 상세 정보 조회

4. **HTML 필드 기입**
   - `product1Name`, `product2Name`, `product3Name`: 제품명
   - `comparisonTable`: 비교 항목 테이블 (무게, 배터리, 디스플레이, 등)

## 지원하는 템플릿

### 현재 지원
- ✅ `product_comparison_table`: RDB 기반 데이터 조회

### 기존 지원 (사용자 작성문 기반)
- ✅ `product_development_plan`
- ✅ `purchase_order`
- ✅ `work_performance_review`

## DB 연결 설정

환경 변수로 설정 (기본값 제공):
- `DB_HOST` / `MARIADB_HOST`: 175.114.229.152
- `DB_PORT` / `MARIADB_PORT`: 3306
- `DB_USER` / `MARIADB_USER`: root
- `DB_PASSWORD` / `MARIADB_PASSWORD`: (필요시 설정)
- `DB_NAME` / `MARIADB_DATABASE`: ai_agent

## 다음 단계

다른 템플릿에도 RDB 기반 데이터 조회 기능을 확장할 수 있습니다:
- HR 요약 보고서 → `recruitment_data_korean_v3` 테이블 조회
- 월별 수익 분석 보고서 → `spend_analysis_dataset_converted` 테이블 조회


