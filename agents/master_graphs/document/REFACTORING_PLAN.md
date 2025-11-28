# doc_generate_agent.py 리팩토링 계획

## 현재 상태
- 파일 크기: ~2000줄
- 함수 수: 9개 (주요 함수)
- 유지보수 어려움: 높음

## 목표
- 모듈화를 통해 각 파일의 책임을 명확히 분리
- 파일 크기를 200-300줄 이하로 유지
- 가독성 및 유지보수성 향상

## 새로운 구조

```
agents/master_graphs/document/
├── __init__.py
├── doc_generate_agent.py        # 메인 함수만 (orchestrator)
├── utils.py                     # 유틸리티 함수들 ✅ 완료
├── html_filler.py               # HTML 필드 채우기 로직
├── llm_generator.py             # LLM 필드 값 생성
└── field_extractors/            # 템플릿별 필드 추출 함수들
    ├── __init__.py
    ├── product_development.py
    ├── purchase_order.py
    └── work_performance_review.py
```

## 모듈별 책임

### 1. utils.py (✅ 완료)
- `get_env()`: 환경 변수 조회
- `load_template()`: 템플릿 파일 로드
- `extract_template_structure()`: HTML 구조 추출

### 2. html_filler.py
- `fill_fields_with_beautifulsoup()`: HTML 필드 채우기
- 테이블 데이터 채우기 (scheduleTable, costTable, itemsTable, evaluationTable)
- 템플릿별 특수 처리 로직

### 3. llm_generator.py
- `generate_field_values_with_llm()`: LLM을 사용한 필드 값 생성
- 프롬프트 구성 및 JSON 파싱

### 4. field_extractors/
각 템플릿별 필드 추출 함수:
- `product_development.py`: `extract_product_development_fields()`
- `purchase_order.py`: `extract_purchase_order_fields()`
- `work_performance_review.py`: `extract_work_performance_review_fields()`

### 5. doc_generate_agent.py
- `doc_generate()`: 메인 함수 (orchestrator)
- 다른 모듈들을 조합하여 문서 생성 프로세스 실행

## 진행 순서
1. ✅ utils.py 생성 (완료)
2. ⏳ html_filler.py 생성
3. ⏳ llm_generator.py 생성
4. ⏳ field_extractors/ 디렉토리 생성 및 파일 분리
5. ⏳ doc_generate_agent.py 리팩토링 (import 및 함수 호출 수정)


