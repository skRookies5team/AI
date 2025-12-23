# 워크플로우 핸들러 모듈

이 모듈은 복합 워크플로우(여러 도메인을 순차적으로 실행하는 작업)를 관리합니다.

## 구조

```
agents/workflows/
├── __init__.py              # 워크플로우 핸들러 팩토리
├── base.py                  # BaseWorkflowHandler 추상 클래스
├── business_trip_handler.py # 출장 워크플로우 핸들러
└── README.md                # 이 파일
```

## 사용 방법

### 1. 워크플로우 핸들러 가져오기

```python
from agents.workflows import get_workflow_handler

handler = get_workflow_handler("business_trip")
if handler:
    result = await handler.handle_step(step_index, state, query, domain)
```

### 2. 새로운 워크플로우 핸들러 추가

1. `BaseWorkflowHandler`를 상속받는 클래스 생성
2. `workflow_type` 프로퍼티 구현
3. `handle_step` 메서드 구현
4. `agents/workflows/__init__.py`에 등록

예시:

```python
# agents/workflows/recruitment_handler.py
from agents.workflows.base import BaseWorkflowHandler

class RecruitmentWorkflowHandler(BaseWorkflowHandler):
    @property
    def workflow_type(self) -> str:
        return "recruitment"
    
    async def handle_step(self, step_index, state, query, domain):
        # 단계별 처리 로직
        pass
```

```python
# agents/workflows/__init__.py
from agents.workflows.recruitment_handler import RecruitmentWorkflowHandler

WORKFLOW_HANDLERS = {
    "business_trip": BusinessTripWorkflowHandler,
    "recruitment": RecruitmentWorkflowHandler,  # 추가
}
```

## 워크플로우 정의

워크플로우는 `agents/workflows/workflows.yaml`에 정의되어 있습니다.

각 워크플로우는 다음 정보를 포함합니다:
- `name`: 워크플로우 이름
- `description`: 워크플로우 설명
- `detection_prompt`: 워크플로우 감지 프롬프트
- `steps`: 워크플로우 단계 정의
- `step_completion_messages`: 단계별 완료 메시지
- `completion_message`: 워크플로우 완료 메시지

## 인덱스 검증

워크플로우 핸들러는 `validate_step_transition` 메서드를 통해 단계 전환을 검증합니다.

- 순차적 증가 확인 (next_index > current_index)
- 범위 확인 (0 <= next_index < len(workflow_steps))
- 비순차적 증가 시 경고 (특수 케이스 허용 가능)

## 현재 지원하는 워크플로우

1. **business_trip**: 출장/휴가 관리 서비스
   - Step 0: schedule - 일정 조회 및 날짜 확인
   - Step 1: document - 출장 신청서 작성
   - Step 2: email - 부재중 자동 응답 초안 생성
   - Step 3: schedule - 캘린더 등록 및 RDB 저장

2. **recruitment**: 채용 프로세스 자동화 (정의됨, 핸들러 미구현)
3. **meeting_preparation**: 회의 준비 자동화 시스템 (정의됨, 핸들러 미구현)

