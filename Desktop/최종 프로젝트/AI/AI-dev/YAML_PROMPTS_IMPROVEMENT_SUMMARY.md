# YAML 기반 프롬프트 시스템 개선 요약

## 개요

각 마스터그래프와 하위 에이전트의 용도와 기능을 `agent_prompts.yaml` 파일로 관리하여, 사용자 요청문에서 의도 분류를 더 정확하게 수행할 수 있도록 개선했습니다.

## 주요 변경 사항

### 1. `agent_prompts.yaml` 파일 생성

**위치**: `agents/agent_prompts.yaml`

**내용**:
- 각 도메인(supervisor, schedule, document, qa, email, tech)의 설명
- 각 하위 에이전트의 용도, 기능, 키워드, 예시
- 분류 기준 및 중요 규칙
- 출력 형식 정의

**구조**:
```yaml
supervisor:
  description: "최상위 오케스트레이터"
  classification_criteria: "분류 기준..."
  examples: {...}
  important_rules: [...]
  
schedule:
  description: "스케줄 도메인"
  sub_agents:
    leave: {...}
    schedule: {...}
    meeting: {...}
```

### 2. `llm_router.py` 개선

**변경 사항**:
- YAML 파일 로드 기능 추가 (`load_agent_prompts()`)
- YAML 기반 프롬프트 빌드 기능 추가 (`build_prompt_from_yaml()`)
- 하드코딩된 프롬프트 제거
- Fallback 메커니즘 유지 (YAML 로드 실패 시 기본 프롬프트 사용)

**새로운 함수**:
```python
def load_agent_prompts() -> Dict[str, Any]:
    """agent_prompts.yaml 파일을 로드"""
    
def build_prompt_from_yaml(route_type: str, query: str, context_info: str = "") -> str:
    """YAML에서 프롬프트를 빌드"""
```

### 3. 의존성 추가

**`pyproject.toml`**:
```toml
"pyyaml>=6.0.0",
```

## 에이전트 구조 정리

### Schedule Master Graph
- **leave**: 연차/반차/병가/경조사 관리
- **schedule**: 일정 관리 (캘린더, 스케줄 조회/등록/취소)
- **meeting**: 회의실 예약 및 회의 관리

### Document Master Graph
- **doc_generate**: 문서 자동 작성 (템플릿 기반)
- **upload_knowledge**: 지식 업로드 (RAG)

### QA Master Graph
- **manual**: 매뉴얼 검색 (RAG)
- **benefit**: 복리후생 조회
- **menu**: 식단 조회
- **recommend**: 메뉴 추천

### Email Master Graph
- **search**: 이메일 검색
- **auto_reply**: 이메일 답장문 생성

### Tech Master Graph
- **patent**: 특허 검색
- **article**: 논문 검색

## 사용 방법

### 1. 프롬프트 수정

`agents/agent_prompts.yaml` 파일을 직접 수정하여:
- 예시 추가/수정
- 키워드 추가/수정
- 분류 기준 개선
- 중요 규칙 추가

### 2. 자동 반영

수정 사항은 다음 호출 시 자동으로 반영됩니다:

```python
from agents.llm_router import classify_intent_with_llm

result = classify_intent_with_llm(
    query="내일 연차 신청하고 싶어",
    route_type="supervisor"
)
```

### 3. 프롬프트 확인

```python
from agents.llm_router import build_prompt_from_yaml

prompt = build_prompt_from_yaml("supervisor", "내일 연차 신청하고 싶어")
print(prompt)
```

## 개선 효과

### 1. 유지보수성 향상
- 모든 프롬프트가 중앙화되어 관리
- 코드 수정 없이 프롬프트만 수정 가능
- 버전 관리 용이

### 2. 분류 정확도 향상
- 구조화된 예시로 LLM 학습 품질 향상
- 키워드 기반 분류 지원
- 중요 규칙 명시화

### 3. 확장성 향상
- 새로운 도메인/에이전트 추가 용이
- 기존 코드 수정 최소화

### 4. 협업 편의성
- 비개발자도 YAML 파일 수정 가능
- 프롬프트 개선 작업 분리 가능

## 테스트

### YAML 로드 테스트
```python
from agents.llm_router import load_agent_prompts

prompts = load_agent_prompts()
assert "supervisor" in prompts
assert "schedule" in prompts
```

### 프롬프트 빌드 테스트
```python
from agents.llm_router import build_prompt_from_yaml

prompt = build_prompt_from_yaml("schedule", "내일 연차 신청하고 싶어")
assert "schedule" in prompt
assert "연차" in prompt
```

## 파일 구조

```
agents/
├── agent_prompts.yaml           # 에이전트 프롬프트 정의 (신규)
├── llm_router.py                # YAML 기반 프롬프트 로더 (개선)
└── ...

docs/
├── AGENT_PROMPTS_YAML_GUIDE.md  # 사용 가이드 (신규)
└── YAML_PROMPTS_IMPROVEMENT_SUMMARY.md  # 개선 요약 (신규)
```

## 주의사항

1. **YAML 문법**: 들여쓰기와 구조를 정확히 지켜야 함
2. **인코딩**: UTF-8 인코딩 사용
3. **Fallback**: YAML 로드 실패 시 기본 프롬프트 사용 (기능 저하 없음)

## 향후 개선 방향

1. **다국어 지원**: 여러 언어의 예시 추가
2. **A/B 테스트**: 여러 버전의 프롬프트 테스트
3. **자동 학습**: 실제 사용 데이터 기반 프롬프트 개선
4. **시각화**: 에이전트 구조 및 분류 기준 시각화 도구



