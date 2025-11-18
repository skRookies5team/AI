# LLM 사용 현황

이 프로젝트에서는 **OpenAI GPT-4o-mini**를 사용하여 다음과 같은 기능을 구현합니다.

## 1. 의도 분류 (Intent Classification)

### 위치: `agents/llm_router.py`

**사용 목적**: 사용자 질의를 분석하여 적절한 에이전트로 라우팅

**사용 모델**: `gpt-4o-mini` (temperature=0)

**사용 위치**:
- `supervisor.py`: 메인 라우터 (schedule/document/qa/tech 분류)
- `schedule_master.py`: 스케줄 서브 라우터 (leave/meeting/calendar 분류)
- `qa_master.py`: QA 서브 라우터 (email/benefit/cafeteria/manual 분류)
- `document_master.py`: 문서 서브 라우터 (email_auto_reply/doc_generate 분류)
- `tech_master.py`: 기술 서브 라우터 (patent/article 분류)

**예시**:
```python
# "사번 4001 일정 알려줘" → LLM이 "schedule"로 분류
# "복리후생 정책 알려줘" → LLM이 "qa"로 분류
```

## 2. 응답 생성 (Response Generation)

### 위치: `agents/schedule_langgraph.py`

**사용 목적**: 조회된 일정 데이터를 자연어로 요약하여 사용자에게 제공

**사용 모델**: `gpt-4o-mini` (temperature=0.2)

**프롬프트**:
```
너는 사내 일정 관리 도우미야. 주어진 일정 목록을 한국어로 이해하기 쉽게 요약해줘.
각 일정에는 담당자 이름(employee_name)과 사번이 포함되어 있으니, 이를 활용해서
사용자가 누구의 일정을 찾는지 명확하게 알 수 있도록 응답해줘.
```

**동작 방식**:
1. DB에서 일정 데이터 조회
2. 데이터를 텍스트로 포맷팅
3. LLM에 사용자 질의와 일정 목록 전달
4. LLM이 자연어 응답 생성

## 3. 기타 LLM 사용

### `agents/qa_benefit_agent.py`
- 복리후생 정책 질문에 대한 답변 생성

### `agents/tech_patent_agent.py`
- 특허 검색 결과 요약 및 설명

## LLM 사용 조건

1. **환경 변수**: `OPENAI_API_KEY`가 설정되어 있어야 함
2. **의존성**: `langchain-openai` 패키지 설치 필요
3. **Fallback**: LLM 사용 불가 시 키워드 기반 fallback 사용

## LLM 없이도 작동 가능

- LLM이 없어도 키워드 기반 라우팅으로 기본 기능은 작동
- 응답 생성 시 LLM이 없으면 단순 포맷팅된 텍스트 반환

