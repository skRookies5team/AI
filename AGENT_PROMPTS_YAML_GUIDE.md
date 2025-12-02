# Agent Prompts YAML 가이드

## 개요

`agent_prompts.yaml` 파일을 통해 각 마스터그래프와 하위 에이전트의 용도, 기능, 분류 기준을 관리합니다. 이를 통해 하드코딩된 프롬프트를 YAML 파일로 분리하여 유지보수성을 높이고, 의도 분류 성능을 개선할 수 있습니다.

## 파일 구조

```
agents/
├── agent_prompts.yaml      # 에이전트 프롬프트 정의
├── llm_router.py           # YAML 기반 프롬프트 로더 (개선됨)
└── ...
```

## YAML 구조

### 최상위 레벨

- `supervisor`: 최상위 오케스트레이터 (도메인 분류)
- `schedule`: 스케줄 도메인
- `document`: 문서 도메인
- `qa`: Q&A 도메인
- `email`: 이메일 도메인
- `tech`: 기술 도메인

### 각 도메인 구조

```yaml
domain_name:
  description: "도메인 설명"
  classification_criteria: "분류 기준 (프롬프트)"
  examples:
    sub_category:
      - "예시 질의 1"
      - "예시 질의 2"
  important_rules:
    - "중요 규칙 1"
    - "중요 규칙 2"
  output_format: '{"intent": "value1|value2|..."}'
  sub_agents:
    sub_agent_name:
      description: "하위 에이전트 설명"
      capabilities:
        - "기능 1"
        - "기능 2"
      keywords:
        positive:
          - "키워드 1"
          - "키워드 2"
        negative:
          - "제외 키워드 1"
      examples:
        - "예시 1"
        - "예시 2"
```

## 사용 방법

### 1. YAML 파일 수정

`agents/agent_prompts.yaml` 파일을 직접 수정하여:
- 새로운 예시 추가
- 키워드 추가/수정
- 분류 기준 개선
- 중요 규칙 추가

### 2. 자동 반영

수정된 YAML 파일은 다음 호출 시 자동으로 로드됩니다:

```python
from agents.llm_router import classify_intent_with_llm

# YAML 프롬프트가 자동으로 사용됨
result = classify_intent_with_llm(
    query="내일 연차 신청하고 싶어",
    route_type="supervisor"
)
```

### 3. 프롬프트 확인

```python
from agents.llm_router import build_prompt_from_yaml

# YAML에서 빌드된 프롬프트 확인
prompt = build_prompt_from_yaml("supervisor", "내일 연차 신청하고 싶어")
print(prompt)
```

## 주요 개선 사항

### 1. 중앙화된 프롬프트 관리

- 모든 프롬프트가 하나의 YAML 파일에 집중
- 버전 관리 용이
- 팀 협업 편의성 향상

### 2. 구조화된 예시 관리

- 각 도메인/하위 에이전트별 예시 명확히 정의
- LLM 학습 품질 향상

### 3. 키워드 기반 분류 지원

- positive/negative 키워드 정의
- 더 정확한 의도 분류

### 4. 확장 가능한 구조

- 새로운 도메인/에이전트 추가 용이
- 기존 코드 수정 최소화

## 프롬프트 작성 가이드

### 1. 명확한 설명

```yaml
description: "도메인이나 에이전트의 목적을 명확히 설명"
```

### 2. 구체적인 예시

```yaml
examples:
  - "실제 사용자가 입력할 수 있는 질의 예시"
  - "다양한 표현 방식 포함"
```

### 3. 중요한 규칙

```yaml
important_rules:
  - "예외 케이스나 특별 주의사항"
  - "다른 도메인과의 경계 명확히 정의"
```

### 4. 키워드 정의

```yaml
keywords:
  positive:
    - "포함되어야 할 키워드"
  negative:
    - "제외되어야 할 키워드 (다른 도메인으로 라우팅)"
```

## 테스트

### YAML 로드 테스트

```python
from agents.llm_router import load_agent_prompts

prompts = load_agent_prompts()
print(prompts.keys())  # ['supervisor', 'schedule', 'document', 'qa', 'email', 'tech']
```

### 프롬프트 빌드 테스트

```python
from agents.llm_router import build_prompt_from_yaml

prompt = build_prompt_from_yaml("schedule", "내일 연차 신청하고 싶어")
assert "schedule" in prompt
assert "연차" in prompt
```

## Fallback 메커니즘

YAML 파일 로드 실패 시:
1. 기본 하드코딩된 프롬프트 사용
2. 경고 메시지 출력
3. 시스템은 정상 작동 (기능 저하 없음)

## 주의사항

1. **YAML 문법 준수**: 들여쓰기와 구조를 정확히 지켜야 함
2. **인코딩**: UTF-8 인코딩 사용
3. **문자열 이스케이프**: 특수 문자 주의
4. **출력 형식**: `output_format`은 정확히 정의해야 함 (JSON 파싱용)

## 향후 개선 방향

1. **다국어 지원**: 여러 언어의 예시 추가
2. **A/B 테스트**: 여러 버전의 프롬프트 테스트
3. **자동 학습**: 실제 사용 데이터 기반 프롬프트 개선
4. **시각화**: 에이전트 구조 및 분류 기준 시각화 도구



