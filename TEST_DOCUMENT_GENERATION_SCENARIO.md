# 문서 생성 Agent 테스트 시나리오

## 테스트 실행 방법

```bash
uv run main_agents.py
```

또는 CLI 모드로 직접 실행:

```bash
uv run main_agents.py --mode cli
```

---

## 시나리오 1: 제품개발 계획서 생성

### 입력 예시 1 (자연어 형태)
```
양식: product_development_plan 제목: 2024년 스마트워치 신제품 개발 계획서 요구사항: 
1. 신제품의 핵심 기능: 건강 모니터링(심박수, 혈압, 수면 분석), GPS 추적, 스마트 알림, 7일 배터리 수명
2. 개발 일정: 2024년 1월~6월 (6개월), Q1: 설계 및 프로토타입, Q2: 테스트 및 양산 준비
3. 예산: 총 50억원 (인력비 30억, 부품비 15억, 마케팅 5억)
4. 마케팅 전략: 젊은 층 타겟, SNS 마케팅, 인플루언서 협업
5. 기술적 난이도: 배터리 수명 최적화가 주요 도전 과제
6. 차별화 포인트: AI 기반 건강 코칭 기능, 무선 충전, 방수 기능
```

### 예상 출력 결과 (모든 필드가 채워진 샘플 HTML)

위 요구사항을 기반으로 생성되어야 하는 완성된 HTML 문서의 샘플은 다음 파일을 참고하세요:
- `output/documents/sample_filled_document.html`

**주요 필드 값 요약:**
- **createDate**: `2024년 11월 26일` (자동 설정)
- **author**: `AI 생성`
- **itemName**: `스마트워치 Pro Max`
- **specifications**: `7일 배터리 수명, GPS 추적, 건강 모니터링(심박수, 혈압, 수면 분석), 스마트 알림, AI 기반 건강 코칭 기능, 무선 충전, 방수 기능`
- **purpose**: `건강 관리 및 일상 활동 추적`
- **developmentPurpose**: `신제품 개발을 통한 시장 경쟁력 강화 및 매출 증대. 젊은 층 타겟 시장 확보를 위한 혁신적인 건강 관리 디바이스 개발...`
- **developmentPeriod**: `2024년 1월~6월 (6개월)`
- **scheduleTable**: 
  - 행 1: `설계 및 프로토타입` / `2024년 1월~3월` / `Q1`
  - 행 2: `테스트 및 양산 준비` / `2024년 4월~6월` / `Q2`
- **developmentContent**: `핵심 기능: 건강 모니터링(심박수, 혈압, 수면 분석), GPS 추적, 스마트 알림, 7일 배터리 수명...`
- **department**: `연구개발부서`
- **responsiblePerson**: `홍길동`
- **expectedEffects**: `젊은 층 타겟 시장 확보, SNS 마케팅 및 인플루언서 협업을 통한 브랜드 인지도 향상...`
- **costTable**:
  - 행 1: `인력비` / `3000000000` (30억원) / `개발 인력`
  - 행 2: `부품비` / `1500000000` (15억원) / `제품 부품`
  - 행 3: `마케팅` / `500000000` (5억원) / `마케팅 비용`
- **specialNotes**: `배터리 수명 최적화가 주요 도전 과제. AI 기반 건강 코칭 기능으로 경쟁사 대비 차별화...`

**참고**: 샘플 HTML 파일(`output/documents/sample_filled_document.html`)을 브라우저에서 열어 모든 필드가 올바르게 채워진 모습을 확인할 수 있습니다.

---

## 📋 HTML 양식 필드별 입력 예시 (null 값 없이 완성된 문서)

위 요구사항을 기반으로 HTML 양식의 각 필드에 들어가야 할 값들:

### 기본 정보 필드
- **createDate** (작성일자): `2024년 11월 26일` (자동 설정)
- **author** (작성자): `AI 생성` 또는 `홍길동`

### 개발품목 섹션
- **itemName** (품목명): `스마트워치 Pro Max` 또는 `스마트워치`
- **specifications** (규격 사양): `7일 배터리 수명, GPS 추적, 건강 모니터링(심박수, 혈압, 수면 분석), 스마트 알림, AI 기반 건강 코칭 기능, 무선 충전, 방수 기능`
- **purpose** (용도): `건강 관리 및 일상 활동 추적` 또는 `건강 모니터링 및 피트니스 추적`

### 개발목적 섹션
- **developmentPurpose** (개발목적): `신제품 개발을 통한 시장 경쟁력 강화 및 매출 증대. 젊은 층 타겟 시장 확보를 위한 혁신적인 건강 관리 디바이스 개발.`

### 개발기간 섹션
- **developmentPeriod** (개발기간): `2024년 1월~6월 (6개월)`

### 개발일정 섹션 (scheduleTable - 테이블)
최소 2개 행 이상 생성:
- **행 1:**
  - task_name: `설계 및 프로토타입`
  - task_period: `2024년 1월~3월`
  - remarks: `Q1`
- **행 2:**
  - task_name: `테스트 및 양산 준비`
  - task_period: `2024년 4월~6월`
  - remarks: `Q2`

### 개발내용 섹션
- **developmentContent** (개발내용): `핵심 기능: 건강 모니터링(심박수, 혈압, 수면 분석), GPS 추적, 스마트 알림, 7일 배터리 수명. 기술적 특징: AI 기반 건강 코칭 기능, 무선 충전, 방수 기능. 배터리 수명 최적화가 주요 기술적 도전 과제이며, 이를 통해 경쟁사 대비 차별화된 제품을 개발한다.`
- **department** (주관부서): `연구개발부서` 또는 `개발팀`
- **responsiblePerson** (책임자): `홍길동` 또는 `담당자`

### 기대효과 섹션
- **expectedEffects** (기대효과): `젊은 층 타겟 시장 확보, SNS 마케팅 및 인플루언서 협업을 통한 브랜드 인지도 향상. 건강 관리 시장에서 경쟁 우위 확보 및 매출 증대 기대.`

### 소요비용 섹션 (costTable - 테이블)
최소 3개 행 생성:
- **행 1:**
  - task_name: `인력비`
  - cost: `3000000000` (30억원, 숫자로만)
  - remarks: `개발 인력`
- **행 2:**
  - task_name: `부품비`
  - cost: `1500000000` (15억원, 숫자로만)
  - remarks: `제품 부품`
- **행 3:**
  - task_name: `마케팅`
  - cost: `500000000` (5억원, 숫자로만)
  - remarks: `마케팅 비용`

### 특기사항 섹션
- **specialNotes** (특기사항): `배터리 수명 최적화가 주요 도전 과제. AI 기반 건강 코칭 기능으로 경쟁사 대비 차별화. 무선 충전 및 방수 기능을 통해 사용자 편의성 향상.`

---

## ✅ 검증 체크리스트

문서 생성 후 다음을 확인하세요:

- [ ] **createDate**: 자동으로 오늘 날짜가 설정됨
- [ ] **author**: "AI 생성" 또는 적절한 이름이 입력됨
- [ ] **itemName**: 요구사항의 제품명이 반영됨 (예: "스마트워치")
- [ ] **specifications**: 요구사항의 핵심 기능이 모두 포함됨
- [ ] **purpose**: 용도가 명확히 기술됨
- [ ] **developmentPurpose**: 개발 목적이 상세히 작성됨
- [ ] **developmentPeriod**: 요구사항의 개발 기간이 반영됨 (예: "2024년 1월~6월")
- [ ] **scheduleTable**: 최소 2개 행이 생성되고, Q1, Q2 정보가 반영됨
- [ ] **developmentContent**: 요구사항의 핵심 기능, 기술적 특징이 모두 포함됨
- [ ] **department**: 주관부서가 입력됨
- [ ] **responsiblePerson**: 책임자명이 입력됨
- [ ] **expectedEffects**: 마케팅 전략, 기대효과가 반영됨
- [ ] **costTable**: 최소 3개 행이 생성되고, 인력비 30억, 부품비 15억, 마케팅 5억이 반영됨
- [ ] **specialNotes**: 기술적 난이도, 차별화 포인트가 반영됨
- [ ] **모든 필드에 null이나 빈 문자열이 없음**
```

### 입력 예시 2 (간단한 형태)
```
양식: product_development_plan 제목: 2024년 AI 스피커 개발 계획서 요구사항: 음성 인식 AI 스피커 개발, 3개월 일정, 예산 10억원
```

### 입력 예시 3 (한국어 자연스러운 형태)
```
제품개발 계획서를 만들어줘. 제목은 "2024년 무선 이어폰 신제품 개발 계획서"이고, 요구사항은 다음과 같아:
- 노이즈 캔슬링 기술 적용
- 배터리 수명 30시간
- 개발 기간 4개월
- 예산 20억원
```

---

## 시나리오 2: 발주서 생성

### 입력 예시 1 (상세 형태)
```
양식: purchase_order 제목: 2024년 1분기 부품 발주서 요구사항:
1. 발주번호: PO-2024-001
2. 발주 업체: 삼성전자 부품사업부
3. 발주 항목: 디스플레이 패널 10,000개, 배터리 15,000개
4. 납기일: 2024년 3월 31일
5. 납품 장소: 서울 공장
6. 결제 조건: 계약금 30%, 납품시 70%
```

### 입력 예시 2 (상세 품목 정보 포함)
```
양식: purchase_order 제목: 2024년 2분기 전자부품 발주서 요구사항:
1. 발주번호: PO-2024-002
2. 수주처: LG전자 부품사업부
3. 발주처: (주)깐부전자
4. 발주 항목:
   - 품명: 디스플레이 패널, 규격: 10.1인치, 수량: 10,000개, 단가: 5만원
   - 품명: 배터리, 규격: 5000mAh, 수량: 15,000개, 단가: 3만원
   - 품명: 메인보드, 규격: 표준형, 수량: 5,000개, 단가: 8만원
5. 납기일: 2024년 6월 30일
6. 납품 장소: 경기 공장
7. 결제 조건: 계약금 20%, 납품시 80%
```

### 예상 출력 결과 (주요 필드 값)

**주요 필드 값 요약:**
- **orderNumber**: `PO-2024-001` (또는 자동 생성)
- **orderDate**: `2024년 11월 26일` (자동 설정)
- **supplierName**: `삼성전자 부품사업부`
- **buyerName**: `(주)깐부전자`
- **supplierContact**: `김담당` (추론)
- **buyerContact**: `이담당` (추론)
- **supplierPhone**: `02-1234-5678` (추론)
- **buyerPhone**: `02-9876-5432` (추론)
- **itemsTable**: 
  - 행 1: `디스플레이 패널` / `10.1인치` / `10,000` / `50,000` / `₩500,000,000`
  - 행 2: `배터리` / `5000mAh` / `15,000` / `30,000` / `₩450,000,000`
- **remarks**: `납기일: 2024년 3월 31일, 납품 장소: 서울 공장, 결제 조건: 계약금 30%, 납품시 70%`
- **representative**: `홍길동`
- **companyName**: `(주)깐부전자`

### 입력 예시 3 (간단한 형태)
```
양식: purchase_order 제목: 2024년 부품 발주서 요구사항: 삼성전자 부품사업부에 디스플레이 패널 10,000개, 배터리 15,000개 발주, 납기일 3월 31일
```

---

## 시나리오 3: 업무성과 평가서 생성

### 입력 예시 1 (상세한 형태)
```
양식: work_performance_review 제목: 2024년 상반기 근무 평가서 요구사항:
1. 부서명: 인사팀, 코드번호: HR-2024-001
2. 평가 기간: 2024년 1월 1일 ~ 6월 30일
3. 업무명: 인사 시스템 구축 프로젝트
4. 작성자명: 김인사
5. 담당 부서장명: 이부장
6. 주평가대상: 신입사원 교육 프로그램 운영
7. 벌점 및 주당근무시간: 벌점 0점, 주당 40시간
8. 평가 방법: 주별 업무 성과 평가
9. 평가 목표 및 개요: 신입사원 교육 프로그램의 효율성을 평가하고, 개선 방안을 도출한다. 주별로 진행 상황과 성과를 측정하여 체계적인 교육 과정을 구축한다.
10. 주별 평가:
    - 1주차: 85점 (기본 교육 과정 안정적 운영)
    - 2주차: 88점 (실무 교육 시작, 적극적 피드백)
    - 3주차: 90점 (프로젝트 과제 수행 우수)
    - 4주차: 87점 (팀워크 향상)
    - 5주차: 89점 (실무 적응도 증가)
    - 6주차: 92점 (최종 프로젝트 우수)
11. 참고: 평가 결과를 바탕으로 교육 과정 개선안 마련 예정
12. 선수과목: 기업 문화 이해, 후속과목: 실무 프로젝트
```

### 입력 예시 2 (간단한 형태)
```
양식: work_performance_review 제목: 2024년 1분기 근무 평가서 요구사항:
부서명: 개발팀, 코드번호: DEV-001, 평가 기간: 2024년 1월 1일 ~ 3월 31일
업무명: 웹 애플리케이션 개발, 작성자명: 박개발, 담당 부서장명: 최부장
주평가대상: 신입 개발자 교육, 벌점 0점, 주당 40시간
평가 방법: 주별 코드 리뷰 및 과제 평가
평가 목표: 신입 개발자의 기술 역량 향상 및 협업 능력 평가
주별 평가: 1주 80점, 2주 85점, 3주 88점, 4주 90점, 5주 92점, 6주 95점
```

### 예상 출력 결과

**주요 필드 값 요약:**
- **departmentName**: `인사팀`
- **codeNumber**: `HR-2024-001`
- **year**: `2024`
- **startMonth/startDay**: `1월 1일`
- **endMonth/endDay**: `6월 30일`
- **workName**: `인사 시스템 구축 프로젝트`
- **authorName**: `김인사`
- **departmentHeadName**: `이부장`
- **evaluationTarget**: `신입사원 교육 프로그램 운영`
- **penaltyAndWorkHours**: `벌점 0점, 주당 40시간`
- **evaluationMethod**: `주별 업무 성과 평가`
- **evaluationGoal**: `신입사원 교육 프로그램의 효율성을 평가하고, 개선 방안을 도출한다...`
- **evaluationTable**: 
  - 행 1: `1주` / `85점` / `기본 교육 과정 안정적 운영`
  - 행 2: `2주` / `88점` / `실무 교육 시작, 적극적 피드백`
  - 행 3: `3주` / `90점` / `프로젝트 과제 수행 우수`
  - 행 4: `4주` / `87점` / `팀워크 향상`
  - 행 5: `5주` / `89점` / `실무 적응도 증가`
  - 행 6: `6주` / `92점` / `최종 프로젝트 우수`
- **remarks**: `평가 결과를 바탕으로 교육 과정 개선안 마련 예정`
- **prerequisite**: `기업 문화 이해`
- **followUp**: `실무 프로젝트`

---

## 시나리오 4: 인사정보 요약 리포트 생성 (RDB 기반)

### 입력 예시 1 (RDB 기반 - employee 테이블에서 자동 조회)
```
양식: hr_summary_report 제목: 2024년 1분기 인사정보 요약 리포트 요구사항:
- 부서: 마케팅팀
- 직급: 과장, 차장
```

**작동 방식:**
1. 사용자가 부서, 직급 등을 언급하면
2. LLM이 필요한 DB 테이블/컬럼을 추출 (`employee` 테이블)
3. RDB에서 해당 부서/직급별 데이터 자동 조회
   - `employee` 테이블에서 전체 데이터 조회 또는 필터링
   - `dept` 컬럼에서 부서 매칭
   - `position` 또는 `job_title` 컬럼에서 직급 매칭
4. HTML 양식에 자동 기입:
   - 리포트 작성일: `reportDate` (자동 설정)
   - 직원 테이블 (`employeeTable`): 
     * 컬럼: 이름(name), 부서(dept), 직급/직위(position/job_title), 직원 상태(employment_status), 역할(job_role), 이메일(email)
     * DB에서 조회한 모든 직원 정보가 테이블에 자동 채워짐
   - 통계 카드:
     * 총 직원 수: 테이블 행 수
     * 재직 중: employment_status = 'Active'인 직원 수
     * 부서 수: 고유 부서(dept) 개수

### 입력 예시 2 (RDB 기반 - 부서 필터링)
```
양식: hr_summary_report 제목: 2024년 1분기 인사정보 요약 리포트 요구사항:
- 부서: 개발팀, 영업팀, 인사팀
```

### 입력 예시 3 (RDB 기반 - 전체 직원 조회)
```
양식: hr_summary_report 제목: 2024년 1분기 인사정보 요약 리포트 요구사항:
- 전체 직원 인사정보 조회
```

또는 간단히:
```
양식: hr_summary_report 제목: 2024년 1분기 인사정보 요약 리포트 요구사항:
전체 직원 조회
```

### 입력 예시 4 (RDB 기반 - 직급 필터링)
```
양식: hr_summary_report 제목: 2024년 1분기 인사정보 요약 리포트 요구사항:
- 직급: 과장, 차장, 부장
```

### 예상 출력 결과 (RDB 기반)

**주요 필드 값 요약:**
- **reportDate**: `2024.12.19` (자동 설정)
- **employeeTable**: DB에서 조회한 직원 데이터로 자동 채워짐
  - 행 1: `홍길동` / `개발팀` / `과장` / `Active` (재직) / `Backend Developer` / `hong@company.com`
  - 행 2: `김영희` / `마케팅팀` / `차장` / `Active` (재직) / `Marketing Manager` / `kim@company.com`
  - 행 3: `박민수` / `영업팀` / `대리` / `Active` (재직) / `Sales Representative` / `park@company.com`
- **통계 카드**:
  - 총 직원 수: `50` (조회된 직원 수)
  - 재직 중: `45` (employment_status = 'Active'인 직원 수)
  - 부서 수: `5` (고유 부서 개수)

**참고:**
- 실제 DB 데이터는 `employee` 테이블의 내용에 따라 달라질 수 있습니다.
- DB에 해당 부서/직급 데이터가 없으면 필터링되지 않고 모든 데이터가 조회됩니다.
- `employee` 테이블의 주요 컬럼:
  * `employee_id` (직원 ID)
  * `name` (이름)
  * `dept` (부서)
  * `job_title` (직위)
  * `position` (직급)
  * `employment_status` (직원 상태: Active, On Leave, Resigned, Terminated)
  * `job_role` (역할)
  * `email` (이메일)
  * `gender` (성별)
  * `salary_million_won` (연봉, 만원 단위)
  * `tenure_years` (근속년수)
  * `hire_data` (입사일)
- 입력하지 않은 조건은 필터링되지 않고 모든 데이터가 조회됩니다.

---

## 시나리오 5: 월별 수익 분석 리포트 생성

### 입력 예시 1 (RDB 기반 - company_sales 테이블에서 자동 조회)
```
양식: monthly_profit_analysis_report 제목: 2019년 5월 수익 분석 리포트 요구사항:
- 분석 기간: 2019년 5월
- 지역: 중국, 미국, 일본
- 제품 카테고리: 휴대폰
```

**작동 방식:**
1. 사용자가 분석 기간(년도, 월), 지역, 제품 카테고리 등을 언급하면
2. LLM이 필요한 DB 테이블/컬럼을 추출 (`company_sales` 테이블)
3. RDB에서 해당 기간/지역/제품별 데이터 자동 조회
   - `company_sales` 테이블에서 `year = 2024`, `month = 1` 조건으로 필터링
   - `region` 컬럼에서 "한국", "미국", "일본" 매칭
   - `product_category` 컬럼에서 "스마트폰", "태블릿", "노트북" 매칭
4. HTML 양식에 자동 기입:
   - 헤더: `year` (2024), `month` (1)
   - 지역별 수익현황 테이블 (`regionalTable`): 
     * 컬럼: NO, 지역(region), 국가(country), 총매출(sales_amount 합계), 총수익(profit_amount 합계), 수익률(계산)
   - 제품별 손익분석 테이블 (`productTable`): 
     * 컬럼: NO, 제품명(product_name), 제품 카테고리(product_category), 제조업체(manufacturer), 매출액(sales_amount), 원가(cost_amount), 수익(profit_amount 또는 sales-cost), 수익률(계산)
   - 프로모션 효과분석 테이블 (`promotionTable`): 
     * 컬럼: NO, 프로모션명(promotion_name), 참여제품(product_name), 총비용(promotion_cost), 추가매출(additional_sales), ROI(roi 또는 계산)
   - 판매채널별 요약 테이블 (`channelTable`): 
     * 컬럼: 채널(channel), 총매출(sales_amount 합계), 총수익(profit_amount 합계), 비중(계산), 증감률(기본값 0)
   - 결론 및 제언 테이블 (`conclusionTable`): LLM이 생성 (DB 미사용)

### 입력 예시 2 (RDB 기반 - 간단한 형태)
```
양식: monthly_profit_analysis_report 제목: 2024년 3월 수익 분석 리포트 요구사항:
- 분석 기간: 2024년 3월
- 지역: 한국, 중국
- 제품: 스마트폰, 태블릿
```

### 입력 예시 3 (기존 방식 - 수동 입력, RDB 미사용)
```
양식: monthly_profit_analysis_report 제목: 2024년 1월 수익 분석 리포트 요구사항:
- 매출액: 100억원
- 영업이익: 20억원
- 순이익: 15억원
- 전월 대비 성장률: 15% 증가
- 주요 매출원: 제품 A 40%, 제품 B 35%, 제품 C 25%
- 지역별 매출: 한국 50억원, 미국 30억원, 일본 20억원
- 제품별 매출: 스마트폰 60억원, 태블릿 25억원, 노트북 15억원
```

### 예상 출력 결과 (RDB 기반)

**주요 필드 값 요약:**
- **year**: `2024`
- **month**: `1`
- **regionalTable**: DB에서 조회한 지역별 데이터로 자동 채워짐
  - 행 1: `한국` / `Korea` / `50,000,000,000` / `10,000,000,000` / `20%`
  - 행 2: `미국` / `USA` / `30,000,000,000` / `6,000,000,000` / `20%`
  - 행 3: `일본` / `Japan` / `20,000,000,000` / `4,000,000,000` / `20%`
- **productTable**: DB에서 조회한 제품별 데이터로 자동 채워짐
  - 행 1: `iPhone 15 Pro` / `스마트폰` / `Apple` / `30,000,000,000` / `20,000,000,000` / `10,000,000,000` / `33.33%`
  - 행 2: `Galaxy S24` / `스마트폰` / `Samsung` / `25,000,000,000` / `17,500,000,000` / `7,500,000,000` / `30%`
  - 행 3: `iPad Pro` / `태블릿` / `Apple` / `15,000,000,000` / `10,000,000,000` / `5,000,000,000` / `33.33%`
- **promotionTable**: DB에서 조회한 프로모션 데이터로 자동 채워짐
- **channelTable**: DB에서 조회한 채널별 데이터로 자동 채워짐
- **conclusionTable**: LLM이 생성한 결론 및 제언

**참고:**
- 실제 DB 데이터는 `company_sales` 테이블의 내용에 따라 달라질 수 있습니다.
- DB에 해당 기간/지역/제품 데이터가 없으면 자동으로 빈 값이 채워지거나, 요구사항에서 추출한 값으로 대체될 수 있습니다.
- `company_sales` 테이블의 주요 컬럼:
  * `region`, `country` (지역 정보)
  * `product_name`, `product_category`, `manufacturer` (제품 정보)
  * `sales_amount`, `cost_amount`, `profit_amount`, `profit_margin` (매출/수익 정보)
  * `channel` (판매채널)
  * `promotion_name`, `promotion_cost`, `additional_sales`, `roi` (프로모션 정보)
  * `year`, `month`, `date` (날짜 정보)
- 컬럼명은 다양한 변형(snake_case, camelCase, 대소문자 등)을 지원하여 자동으로 매칭됩니다.

---


## 시나리오 6: 공급업체 평가서 생성

### 입력 예시
```
양식: supplier_evaluation_form 제목: 삼성전자 부품사업부 평가서 요구사항:
- 평가 기간: 2023년 1년
- 품질 점수: 95점
- 납기 준수도: 98%
- 가격 경쟁력: 우수
- 서비스 평가: 매우 만족
- 종합 평가: A등급
```

---

## 시나리오 7: 계약 갱신 보고서 생성

### 입력 예시
```
양식: contract_renewal_report 제목: 네이버 클라우드 플랫폼 계약 갱신 보고서 요구사항:
- 현재 계약 기간: 2023년 1월~12월
- 갱신 계약 기간: 2024년 1월~12월
- 계약 금액: 전년 대비 10% 증가 (5억원)
- 주요 변경사항: 저장 용량 확대, 보안 강화
- 갱신 사유: 서비스 만족도 높음, 비즈니스 확장 필요
```

---

## 시나리오 8: 활동기준 원가 계산서 생성

### 입력 예시 1 (RDB 기반 - activity_based_costing 테이블에서 자동 조회)
```
양식: activity_based_costing 제목: 2024년 1분기 활동기준 원가 계산서 요구사항:
- 분석 기간: 2024년 1분기
- 부서/센터: 생산부서, 품질관리부서
- 활동 유형: 생산, 품질 관리, 배송
```

**작동 방식:**
1. 사용자가 분석 기간, 부서, 활동 유형 등을 언급하면
2. LLM이 필요한 DB 테이블/컬럼을 추출 (`activity_based_costing` 테이블)
3. RDB에서 해당 기간/부서/활동별 데이터 자동 조회
   - `activity_based_costing` 테이블에서 `data_reference_date`로 기간 필터링
   - `department` 컬럼에서 부서 매칭
   - `activity_type` 컬럼에서 활동 유형 매칭
4. HTML 양식에 자동 기입:
   - 헤더: `period` (기간), `department` (부서/센터)
   - 원가 계산 테이블 (`costingTable`): 
     * 컬럼: Activity(활동명), Overhead(예상 제조간접비), Per Activity(총 활동량), Overhead Rate per Activ(활동당 배부율)
     * `activity_name` → Activity
     * `total_activity_cost` 또는 (`fixed_cost` + `variable_cost`) → Overhead
     * `activity_quantity` + `cost_driver` → Per Activity
     * `cost_rate_per_unit` → Overhead Rate per Activ

### 입력 예시 2 (RDB 기반 - 간단한 형태)
```
양식: activity_based_costing 제목: 2024년 1분기 활동기준 원가 계산서 요구사항:
- 분석 기간: 2024년 1분기
- 부서: 생산부서
```

### 입력 예시 3 (기존 방식 - 수동 입력, RDB 미사용)
```
양식: activity_based_costing 제목: 2024년 1분기 활동기준 원가 계산서 요구사항:
- 주요 활동: 제품 설계, 생산, 품질 관리, 배송
- 총 원가: 100억원
- 제품 A 원가: 40억원 (40%)
- 제품 B 원가: 35억원 (35%)
- 제품 C 원가: 25억원 (25%)
```

### 예상 출력 결과 (RDB 기반)

**주요 필드 값 요약:**
- **period**: `2024년 1분기`
- **department**: `생산부서` (또는 조회된 부서명)
- **costingTable**: DB에서 조회한 활동별 데이터로 자동 채워짐
  - 행 1: `PCB 조립` / `120,000,000` / `60,000 hours` / `₩2,000`
  - 행 2: `설비 셋업` / `180,000,000` / `3,000 setups` / `₩60,000`
  - 행 3: `품질 검사` / `150,000,000` / `8,000 inspections` / `₩18,750`

**참고:**
- 실제 DB 데이터는 `activity_based_costing` 테이블의 내용에 따라 달라질 수 있습니다.
- DB에 해당 기간/부서/활동 데이터가 없으면 자동으로 빈 값이 채워지거나, 요구사항에서 추출한 값으로 대체될 수 있습니다.
- `activity_based_costing` 테이블의 주요 컬럼:
  * `activity_name` (활동명)
  * `cost_driver` (원가 드라이버, 예: "hours", "setups", "inspections")
  * `cost_rate_per_unit` (단위당 원가율)
  * `activity_quantity` (활동량)
  * `total_activity_cost` (총 활동 원가) 또는 `fixed_cost` + `variable_cost`
  * `activity_type` (활동 유형)
  * `department` (부서)
  * `data_reference_date` (데이터 참조일, 기간 필터링용)

---

## 시나리오 9: 프로젝트 일정표 생성

### 입력 예시
```
양식: project_schedule 제목: 2024년 신제품 출시 프로젝트 일정표 요구사항:
- 프로젝트 기간: 2024년 1월~6월
- 주요 마일스톤: 
  * 1월: 요구사항 정의 완료
  * 2월: 설계 완료
  * 3월: 개발 시작
  * 4월: 테스트 시작
  * 5월: 양산 준비
  * 6월: 출시
- 담당자: 개발팀 10명, QA팀 5명, 마케팅팀 3명
```

---

## 시나리오 10: 제품 비교표 생성 (RDB 기반)

### 입력 예시 1 (RDB 기반 - mobile_devices 테이블에서 자동 조회)
```
양식: product_comparison_table 제목: 2024년 프리미엄 스마트폰 제품 비교표 요구사항:
- 비교 목적: 신제품 선정을 위한 프리미엄 스마트폰 비교
- 비교 대상: iPhone 15 Pro 512GB, Galaxy S24 Ultra 128GB, Pixel 8 Pro 256GB
```

**작동 방식:**
1. 사용자가 제품명을 언급하면 (`iPhone 15 Pro`, `Galaxy S24 Ultra`, `Pixel 8 Pro`)
2. LLM이 필요한 DB 테이블/컬럼을 추출 (`mobile_devices` 테이블)
3. RDB에서 제품 데이터 자동 조회
4. HTML 양식에 자동 기입:
   - 제품명 3개 (`product1Name`, `product2Name`, `product3Name`)
   - 비교 항목 테이블 (`comparisonTable`):
     * 가격 (Price)
     * 무게 (Weight)
     * 배터리 (Battery)
     * 디스플레이 (Display_Size)
     * 프로세서 (Processor)
     * 카메라 (Camera)
     * 저장공간 (Storage)
     * RAM
     * 운영체제 (Operating_System)
     * 네트워크 (Network)
     * 색상 (Color)

### 입력 예시 2 (RDB 기반 - 간단한 형태)
```
양식: product_comparison_table 제목: 2024년 중급형 스마트폰 비교표 요구사항:
비교 목적: 중급형 스마트폰 3종 비교 검토
비교 대상: Galaxy A54, iPhone SE, Pixel 7a
```

### 입력 예시 3 (RDB 기반 - 모델명만 언급)
```
양식: product_comparison_table 제목: 2024년 플래그십 스마트폰 비교표 요구사항:
비교 목적: 최신 플래그십 스마트폰 성능 비교
비교 대상 제품: S24 Ultra, 15 Pro Max, Pixel 8 Pro
```

### 예상 출력 결과 (RDB 기반)

**주요 필드 값 요약:**
- **comparisonPurpose**: `신제품 선정을 위한 프리미엄 스마트폰 비교`
- **product1Name**: `Apple iPhone 15 Pro` (DB에서 조회된 값)
- **product2Name**: `Samsung Galaxy S24 Ultra` (DB에서 조회된 값)
- **product3Name**: `Google Pixel 8 Pro` (DB에서 조회된 값)
- **comparisonTable**: DB에서 조회한 실제 데이터로 자동 채워짐
  - 행 1: `가격` / `1,500,000원` / `1,450,000원` / `1,200,000원`
  - 행 2: `무게` / `187g` / `233g` / `213g`
  - 행 3: `배터리` / `3274mAh` / `5000mAh` / `5050mAh`
  - 행 4: `디스플레이` / `6.1인치` / `6.8인치` / `6.7인치`
  - 행 5: `프로세서` / `A17 Pro` / `Snapdragon 8 Gen 3` / `Tensor G3`
  - 행 6: `카메라` / `48MP Triple` / `200MP Quad` / `50MP Triple`
  - 행 7: `저장공간` / `256GB` / `512GB` / `128GB`
  - 행 8: `RAM` / `8GB` / `12GB` / `8GB`
  - 행 9: `운영체제` / `iOS 17` / `Android 14` / `Android 14`
  - 행 10: `네트워크` / `5G` / `5G` / `5G`
  - 행 11: `색상` / `Natural Titanium` / `Titanium Gray` / `Obsidian`

**참고:**
- 실제 DB 데이터는 `mobile_devices` 테이블의 내용에 따라 달라질 수 있습니다.
- DB에 해당 제품 데이터가 없으면 자동으로 빈 값이 채워지거나, 요구사항에서 추출한 값으로 대체될 수 있습니다.

### 입력 예시 4 (기존 방식 - 수동 입력, RDB 미사용)
```
양식: product_comparison_table 제목: 2024년 스마트폰 제품 비교표 요구사항:
- 비교 대상: 아이폰 15, 갤럭시 S24, 픽셀 8
- 비교 항목: 가격, 화면 크기, 배터리, 카메라 성능
- 아이폰 15: 가격 130만원, 화면 6.1인치, 배터리 3,200mAh, 카메라 48MP
- 갤럭시 S24: 가격 110만원, 화면 6.1인치, 배터리 4,000mAh, 카메라 50MP
- 픽셀 8: 가격 90만원, 화면 6.2인치, 배터리 4,575mAh, 카메라 50MP
- 추천: 가성비 측면에서 픽셀 8 추천
```

---

## 시나리오 11: 휴가 신청서 생성

### 입력 예시 1 (상세한 형태)
```
양식: vacation_request_form 제목: 2024년 연차 휴가 신청서 요구사항:
- 휴가 유형: 연차
- 휴가 기간: 2024년 3월 15일 ~ 2024년 3월 17일
- 휴가 사유: 개인 사정으로 인한 휴가
```

**작동 방식:**
1. 사용자가 휴가 유형, 기간, 사유를 입력하면
2. LLM이 필요한 정보를 추출하여 HTML 양식에 자동 기입
3. 신청자 정보는 로그인한 사용자 정보(employee_id)로 자동 설정
4. HTML 양식에 자동 기입:
   - 신청자 정보: `applicantName`, `department`, `position`, `employeeId`, `phone` (DB에서 조회)
   - 신청일: `requestDate` (자동 설정)
   - 휴가 정보: `vacationType`, `startDate`, `endDate`, `vacationDays` (자동 계산)
   - 휴가 사유: `reason`
   - 결재선: `approver1Name`, `approver1Position`, `approver1Signature`, `approver1Date`, `approver2Name`, `approver2Position`, `approver2Signature`, `approver2Date`

### 입력 예시 2 (간단한 형태)
```
양식: vacation_request_form 제목: 반차 신청서 요구사항:
휴가 유형: 반차(오전), 휴가 기간: 2024년 4월 10일
휴가 사유: 병원 방문
```

### 입력 예시 3 (자연스러운 형태)
```
휴가 신청서를 만들어줘. 연차로 3월 15일부터 17일까지 3일간 신청하고, 사유는 개인 사정이야.
```

### 예상 출력 결과

**주요 필드 값 요약:**
- **formNumber**: `VAC-20241221-001` (자동 생성)
- **applicantName**: `홍길동` (DB에서 조회)
- **department**: `개발팀` (DB에서 조회)
- **position**: `과장` (DB에서 조회)
- **employeeId**: `4000` (DB에서 조회)
- **phone**: `hong@company.com` (DB에서 조회)
- **requestDate**: `2024-12-21` (자동 설정)
- **vacationType**: `연차`
- **startDate**: `2024-03-15`
- **endDate**: `2024-03-17`
- **vacationDays**: `3일` (자동 계산, 주말 제외)
- **reason**: `개인 사정으로 인한 휴가`
- **approver1Name**: `이부장` (추론 또는 DB 조회)
- **approver1Position**: `부장`
- **approver2Name**: `최대표` (추론 또는 DB 조회)
- **approver2Position**: `대표이사`

**참고:**
- 신청자 정보는 로그인한 사용자의 `employee_id`를 기반으로 DB에서 자동 조회됩니다.
- 휴가 일수는 주말을 제외하고 자동 계산됩니다 (반차는 0.5일).
- 신청서 번호는 `VAC-YYYYMMDD-XXX` 형식으로 자동 생성됩니다.

---

## 시나리오 12: 출장 신청서 생성

### 입력 예시 1 (상세한 형태)
```
양식: business_trip_request_form 제목: 2024년 서울 출장 신청서 요구사항:
- 출장지: 서울
- 출장 목적: 고객 미팅
- 출장 기간: 2024년 3월 15일 ~ 2024년 3월 17일
- 출장 사유: 주요 고객사와의 계약 협상
```

**작동 방식:**
1. 사용자가 출장지, 목적, 기간, 사유를 입력하면
2. LLM이 필요한 정보를 추출하여 HTML 양식에 자동 기입
3. 신청자 정보는 로그인한 사용자 정보(employee_id)로 자동 설정
4. HTML 양식에 자동 기입:
   - 신청자 정보: `applicantName`, `department`, `position`, `employeeId`, `phone` (DB에서 조회)
   - 신청일: `requestDate` (자동 설정)
   - 출장 정보: `destination`, `purpose`, `startDate`, `endDate`, `tripDays` (자동 계산)
   - 출장 사유: `reason`
   - 결재선: `approver1Name`, `approver1Position`, `approver1Signature`, `approver1Date`, `approver2Name`, `approver2Position`, `approver2Signature`, `approver2Date`

### 입력 예시 2 (간단한 형태)
```
양식: business_trip_request_form 제목: 부산 출장 신청서 요구사항:
출장지: 부산, 출장 목적: 전시회 참관, 출장 기간: 2024년 4월 10일 ~ 2024년 4월 12일
출장 사유: IT 전시회 참관 및 신제품 홍보
```

### 입력 예시 3 (자연스러운 형태)
```
출장 신청서를 만들어줘. 다음 주 3일간 울산으로 출장 가는데, 목적은 거래처 미팅이고 사유는 신규 계약 협상이야.
```

### 예상 출력 결과

**주요 필드 값 요약:**
- **formNumber**: `BT-20241221-001` (자동 생성)
- **applicantName**: `홍길동` (DB에서 조회)
- **department**: `영업팀` (DB에서 조회)
- **position**: `과장` (DB에서 조회)
- **employeeId**: `4000` (DB에서 조회)
- **phone**: `hong@company.com` (DB에서 조회)
- **requestDate**: `2024-12-21` (자동 설정)
- **destination**: `서울`
- **purpose**: `고객 미팅`
- **startDate**: `2024-03-15`
- **endDate**: `2024-03-17`
- **tripDays**: `3일` (자동 계산, 주말 포함)
- **reason**: `주요 고객사와의 계약 협상`
- **approver1Name**: `이부장` (추론 또는 DB 조회)
- **approver1Position**: `부장`
- **approver2Name**: `최대표` (추론 또는 DB 조회)
- **approver2Position**: `대표이사`

**참고:**
- 신청자 정보는 로그인한 사용자의 `employee_id`를 기반으로 DB에서 자동 조회됩니다.
- 출장 일수는 주말을 포함하여 자동 계산됩니다.
- 신청서 번호는 `BT-YYYYMMDD-XXX` 형식으로 자동 생성됩니다.
- 워크플로우 모드에서 출장 신청 시, `workflow_temp_data`에서 `start_time`, `end_time`, `destination`, `description` 정보를 가져와 자동으로 채워집니다.

---

## 테스트 체크리스트

### 기본 기능 테스트
- [ ] 각 시나리오별로 문서가 정상적으로 생성되는지 확인
- [ ] 생성된 문서가 HTML 형식으로 출력되는지 확인
- [ ] 문서 제목이 올바르게 설정되는지 확인
- [ ] 요구사항 내용이 문서에 반영되는지 확인

### 에러 처리 테스트
- [ ] 존재하지 않는 템플릿 이름 입력 시 에러 처리 확인
- [ ] 요구사항 없이 입력 시 기본값 처리 확인
- [ ] 제목 없이 입력 시 기본값 처리 확인

### 템플릿별 테스트
각 템플릿별로 최소 1회씩 테스트:
- [ ] product_development_plan
- [ ] purchase_order
- [ ] hr_summary_report
- [ ] monthly_profit_analysis_report
- [ ] work_performance_review
- [ ] supplier_evaluation_form
- [ ] contract_renewal_report
- [ ] activity_based_costing
- [ ] project_schedule
- [ ] product_comparison_table
- [ ] vacation_request_form
- [ ] business_trip_request_form

---

## 예상 출력 형식

정상적인 경우:
```
에이전트> [생성된 HTML 문서 전체 내용]
```

에러가 발생한 경우:
```
에이전트> ❌ [에러 메시지]
```

---

## 주의사항

1. **환경 변수**: `OPENAI_API_KEY`가 설정되어 있어야 LLM을 사용한 문서 생성이 가능합니다.
2. **템플릿 파일**: `templates/` 폴더에 해당 템플릿 HTML 파일이 존재해야 합니다.
3. **출력 형식**: 생성된 문서는 HTML 형식으로 출력되므로, 긴 출력이 예상됩니다.
4. **파싱 형식**: 입력 형식은 유연하지만, 다음 형식을 권장합니다:
   - `양식: [template_name]`
   - `제목: [document_title]`
   - `요구사항: [requirements]`

---

## 문제 해결

### 템플릿을 찾을 수 없다는 에러가 나는 경우
- 템플릿 파일명이 정확한지 확인 (확장자 제외)
- `templates/` 폴더에 해당 파일이 존재하는지 확인

### LLM 생성이 실패하는 경우
- `OPENAI_API_KEY` 환경 변수 확인
- API 키 유효성 확인
- 네트워크 연결 확인

### 문서가 생성되지 않는 경우
- 입력 형식 확인 (양식, 제목, 요구사항 포함)
- 로그 메시지 확인 (어디서 실패했는지)
