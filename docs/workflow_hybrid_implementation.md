# 워크플로우 하이브리드 방식 구현 가이드

## 🎯 구현 완료

30개 이상의 워크플로우를 **하이브리드 방식**으로 관리하는 시스템을 구축했습니다.

## 📁 구조

```
agents/workflows/
├── __init__.py              # 하이브리드 레지스트리 초기화
├── registry.py              # Python 기반 레지스트리 (기본)
├── registry_yaml.py         # YAML 기반 레지스트리 (오버라이드)
├── workflows.yaml           # 워크플로우 메타데이터 설정 (30개 이상)
├── business_trip.py         # 출장/휴가 워크플로우
├── research.py              # R&D 리서치 워크플로우
├── meeting.py               # 회의 준비 워크플로우
└── workflow_example.py.example  # 새 워크플로우 추가 예시
```

## 🔄 작동 방식

### 1. Python 코드 (기본값)

```python
# agents/workflows/business_trip.py
from agents.workflows import workflow

@workflow(
    name="business_trip_flow",
    category="travel",
    description="출장/휴가 관리",
    priority=5,  # 기본값
    enabled=True,
    examples=["다음주 제주 출장"]
)
def get_business_trip_graph():
    # 워크플로우 로직
    ...
```

### 2. YAML 파일 (오버라이드)

```yaml
# agents/workflows/workflows.yaml
workflows:
  business_trip_flow:
    priority: 10  # 5 → 10으로 오버라이드
    enabled: true
    examples:
      - "다음주 제주 출장"
      - "휴가 신청해줘"  # 추가 예시
```

### 3. 최종 결과

- `priority`: **10** (YAML 우선)
- `examples`: YAML의 예시 사용
- 로직: Python 코드 그대로

## ✨ 주요 기능

### 1. 자동 등록

```python
# get_xxx_graph() 함수만 있으면 자동으로 등록됨
def get_my_workflow_graph():
    ...
    return workflow.compile()
```

### 2. YAML 오버라이드

- Python 기본값: 타입 안정성, IDE 지원
- YAML 오버라이드: 런타임 변경, 운영 편의성

### 3. 동적 라우팅

- 등록된 모든 워크플로우로부터 자동으로 라우팅 프롬프트 생성
- LLM이 30개 이상의 워크플로우 중 자동 선택

### 4. 카테고리별 관리

```yaml
categories:
  travel:      priority: 10  # 출장/휴가
  research:    priority: 9   # R&D/기술
  meeting:     priority: 10  # 회의
  hr:          priority: 8   # 인사
  sales:       priority: 8   # 영업
  # ... 14개 카테고리
```

## 📝 워크플로우 목록 (YAML에 등록된 30개 이상)

### 출장/휴가 관리 (Travel)
1. `business_trip_flow` - 출장/휴가 관리
2. `vacation_return_briefing_flow` - 휴가 복귀 브리핑
3. `expense_report_flow` - 법인카드 내역 소명

### R&D/기술 (Research)
4. `research_flow` - R&D 인사이트 및 리포트
5. `competitor_analysis_flow` - 경쟁사 동향 분석
6. `patent_infringement_flow` - 특허 침해 대응
7. `seminar_summary_flow` - 세미나 요약 공유
8. `newsletter_curation_flow` - 뉴스레터 큐레이션

### 회의 준비 (Meeting)
9. `meeting_flow` - 스마트 회의 준비
10. `customer_meeting_prep_flow` - 고객사 미팅 준비
11. `interview_scheduling_flow` - 면접 일정 조율
12. `contract_renewal_flow` - 계약 갱신 준비
13. `rfp_analysis_flow` - RFP 분석
14. `project_retrospective_flow` - 프로젝트 회고
15. `meeting_transcription_flow` - 회의 녹취록

### HR/인사
16. `onboarding_automation_flow` - 신규 입사자 온보딩
17. `mandatory_training_flow` - 의무 교육 독려
18. `business_card_request_flow` - 명함 신청
19. `handover_document_flow` - 인수인계서 작성
20. `birthday_congratulations_flow` - 생일 축하

### 문서/보고서
21. `monthly_report_flow` - 월간 업무 성과 보고서
22. `survey_creation_flow` - 설문조사 생성

### 복리후생
23. `benefit_recommendation_flow` - 복리후생 추천
24. `weekly_menu_announcement_flow` - 주간 식단 공지
25. `lunch_decision_flow` - 점심 메뉴 추천

### 일반/행사
26. `event_planning_flow` - 행사 기획
27. `server_maintenance_flow` - 서버 점검 공지
28. `vip_preparation_flow` - VIP 의전 준비
29. `overseas_buyer_flow` - 해외 바이어 응대

(총 30개 이상의 워크플로우 메타데이터가 YAML에 등록됨)

## 🚀 사용 방법

### 새 워크플로우 추가

1. **워크플로우 파일 생성**
```python
# agents/workflows/vacation_request.py
from agents.workflows import workflow

@workflow(
    name="vacation_request_flow",
    category="travel",
    description="휴가 신청 프로세스",
    priority=5,
    examples=["휴가 신청"]
)
def get_vacation_request_graph():
    # 로직 구현
    ...
    return workflow.compile()
```

2. **YAML에 메타데이터 추가 (선택적)**
```yaml
# agents/workflows/workflows.yaml
workflows:
  vacation_request_flow:
    priority: 8  # 오버라이드
    enabled: true
    examples:
      - "휴가 신청"
      - "연차 요청"  # 추가 예시
```

3. **자동 등록**: 파일 저장 시 자동으로 등록됨!

### 런타임 YAML 재로드

```python
from agents.workflows import yaml_registry

# YAML 파일 변경 후 재로드 (서버 재시작 불필요)
yaml_registry.reload_yaml()
```

## 💡 장점

### ✅ Python 코드 기반
- 타입 안정성
- IDE 자동완성
- 코드 리뷰 용이

### ✅ YAML 오버라이드
- 런타임 변경 가능
- 비개발자도 수정 가능
- 대량 관리 용이

### ✅ 자동화
- 워크플로우 추가 시 코드 수정 불필요
- 라우팅 프롬프트 자동 생성
- 30개 이상도 쉽게 관리

## 📊 비교

| 방식 | Python 코드 | YAML | 하이브리드 |
|------|------------|------|----------|
| 타입 안정성 | ⭐⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| 런타임 변경 | ❌ | ✅ | ✅ |
| 대량 관리 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 코드 수정 | 필요 | 불필요 | 불필요 |

**결론: 하이브리드 방식이 30개 이상의 워크플로우 관리에 최적!** 🎉

