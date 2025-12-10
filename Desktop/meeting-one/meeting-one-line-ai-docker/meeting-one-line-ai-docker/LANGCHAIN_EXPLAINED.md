# LangChain이란? & 무엇이 바뀌었나?

## 🤔 LangChain이 뭔가요?

### 쉬운 비유

**LangChain = AI 프로그래밍을 위한 "레고 블록"**

- **직접 OpenAI 사용** = 나무를 베고, 못을 박고, 집을 직접 짓기
- **LangChain 사용** = 조립식 가구 키트로 집을 짓기

---

### 구체적으로 뭐하는 도구?

LangChain은 **AI 애플리케이션을 만들 때 반복되는 작업을 쉽게 해주는 라이브러리**입니다.

#### 예시: AI에게 질문하기

**LangChain 없이 (직접 OpenAI 사용)**:
```python
# 1단계: 클라이언트 만들기
client = OpenAI(api_key="my-key")

# 2단계: 메시지 직접 구성
messages = [
    {"role": "system", "content": "당신은 회의록 전문가입니다"},
    {"role": "user", "content": "이 회의를 요약해주세요: ..."}
]

# 3단계: API 호출
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=messages,
    temperature=0.3,
    max_tokens=4000
)

# 4단계: 결과 추출
result = response.choices[0].message.content
```

**LangChain 사용 (간편하게)**:
```python
# 1단계: LLM과 프롬프트 준비
llm = ChatOpenAI(model="gpt-4o-mini")
prompt = ChatPromptTemplate.from_messages([
    ("system", "당신은 회의록 전문가입니다"),
    ("user", "이 회의를 요약해주세요: {transcript}")
])

# 2단계: 체인으로 연결 (파이프라인)
chain = prompt | llm

# 3단계: 실행
result = chain.invoke({"transcript": "..."})
```

---

## 🔄 우리 프로젝트에서 무엇이 바뀌었나?

### 변경 전 (이전 코드)

```python
class ReportGenerator:
    def __init__(self):
        self.client = OpenAI(api_key=api_key)

    def summarize(self, transcript: str) -> str:
        # 직접 OpenAI API 호출
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "시스템 프롬프트"},
                {"role": "user", "content": f"요약해주세요: {transcript}"}
            ],
            temperature=0.3,
            max_tokens=4000
        )

        # 응답에서 결과 추출
        return response.choices[0].message.content.strip()
```

### 변경 후 (LangChain 사용)

```python
class ReportGenerator:
    def __init__(self):
        # LangChain의 ChatOpenAI 사용
        self.llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=api_key,
            temperature=0.3
        )

    def summarize(self, transcript: str) -> str:
        # ChatPromptTemplate로 프롬프트 구성
        prompt = ChatPromptTemplate.from_messages([
            ("system", "시스템 프롬프트"),
            ("user", f"요약해주세요: {transcript}")
        ])

        # 체인으로 연결
        chain = prompt | self.llm

        # 실행
        response = chain.invoke({})
        return response.content.strip()
```

---

## 📊 차이점 비교표

| 항목 | 이전 (OpenAI SDK 직접) | 이후 (LangChain) |
|------|------------------------|------------------|
| **OpenAI 호출** | `client.chat.completions.create()` | `ChatOpenAI` 객체 |
| **프롬프트 관리** | 딕셔너리 직접 작성 | `ChatPromptTemplate` |
| **파이프라인** | 없음 (단일 호출) | `prompt \| llm` 체인 |
| **임베딩 생성** | `client.embeddings.create()` | `OpenAIEmbeddings` |
| **코드 길이** | 길고 반복적 | 짧고 간결 |
| **재사용성** | 낮음 | 높음 |
| **확장성** | 어려움 | 쉬움 |

---

## 🎯 왜 바꿨나요?

### 1. 평가 기준 충족
**평가표에 명시된 요구사항**:
> "LangChain 또는 LangGraph를 사용한 LLM 활용"

- ❌ 이전: OpenAI SDK 직접 사용 → **감점**
- ✅ 이후: LangChain 사용 → **만점**

### 2. 코드 품질 향상

**이전 (OpenAI 직접)**:
- 매번 메시지 구조 직접 작성
- 에러 처리 직접 구현
- 프롬프트 관리 어려움
- 여러 AI 호출 연결 복잡

**이후 (LangChain)**:
- 프롬프트 템플릿 재사용
- 에러 처리 자동화
- 프롬프트 체계적 관리
- 파이프라인으로 연결 쉬움

### 3. 향후 확장 가능성

**LangChain 사용 시 추가 가능**:
- **LangGraph**: 복잡한 AI Agent 워크플로우
- **ChromaDB**: 벡터 데이터베이스 연동
- **Tools**: 외부 API (웹 검색 등) 연결
- **Memory**: 대화 히스토리 관리

---

## 🔍 실제 코드 변화 예시

### 예시 1: 요약 기능

#### Before (이전)
```python
def summarize(self, transcript: str) -> str:
    try:
        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "당신은 회의록 전문가입니다..."},
                {"role": "user", "content": f"요약: {transcript}"}
            ],
            temperature=0.3,
            max_tokens=4000
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        raise ReportGeneratorError(f"오류: {e}")
```

#### After (LangChain)
```python
def summarize(self, transcript: str) -> str:
    try:
        chat_prompt = ChatPromptTemplate.from_messages([
            ("system", "당신은 회의록 전문가입니다..."),
            ("user", f"요약: {transcript}")
        ])

        chain = chat_prompt | self.llm
        response = chain.invoke({})
        return response.content.strip()
    except Exception as e:
        raise ReportGeneratorError(f"오류: {e}")
```

**차이점**:
- ✅ `ChatPromptTemplate` 사용 → 프롬프트 구조화
- ✅ `chain = prompt | llm` → 파이프라인 패턴
- ✅ `invoke()` → 통일된 실행 방식

---

### 예시 2: 임베딩 생성

#### Before (이전)
```python
def generate_embedding(self, text: str) -> list[float]:
    try:
        response = self.client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        raise ReportGeneratorError(f"오류: {e}")
```

#### After (LangChain)
```python
def generate_embedding(self, text: str) -> list[float]:
    try:
        # LangChain의 OpenAIEmbeddings 사용
        return self.embeddings.embed_query(text)
    except Exception as e:
        raise ReportGeneratorError(f"오류: {e}")
```

**차이점**:
- ✅ `OpenAIEmbeddings` 객체 사용
- ✅ `embed_query()` 메서드로 간편하게
- ✅ 코드 3줄 → 1줄

---

## 🎨 시각적 비교

### Architecture: Before

```
사용자 요청
    ↓
server.py (FastAPI)
    ↓
ReportGenerator.summarize()
    ↓
[직접 OpenAI API 호출]
    ├── messages 딕셔너리 생성
    ├── client.chat.completions.create()
    ├── response.choices[0].message.content
    └── 결과 반환
    ↓
결과 응답
```

### Architecture: After (LangChain)

```
사용자 요청
    ↓
server.py (FastAPI)
    ↓
ReportGenerator.summarize()
    ↓
[LangChain 파이프라인]
    ├── ChatPromptTemplate 구성
    ├── ChatOpenAI 준비
    ├── chain = prompt | llm
    ├── chain.invoke()
    └── 결과 반환
    ↓
결과 응답
```

**핵심**:
- 이전: OpenAI API를 **직접** 호출
- 이후: LangChain이 **중간에서 관리**

---

## 🚀 LangChain의 장점 (우리가 얻은 것)

### 1. ✅ 평가 기준 충족
```
AI 연동 구현 (25점)
├─ LangChain 사용 ✅ (+10점)
├─ LLM 활용 ✅ (유지)
└─ Embeddings ✅ (+5점)

예상 점수: 10-12점 → 20-23점
```

### 2. ✅ 코드 추상화
```python
# 이전: OpenAI API 세부사항 노출
response = client.chat.completions.create(...)
result = response.choices[0].message.content

# 이후: 간결한 인터페이스
result = chain.invoke({})
```

### 3. ✅ 프롬프트 재사용
```python
# 템플릿 한 번 정의
summary_prompt = ChatPromptTemplate.from_messages([...])

# 여러 번 재사용
chain1 = summary_prompt | llm1
chain2 = summary_prompt | llm2
```

### 4. ✅ 파이프라인 구성
```python
# 여러 단계 연결 가능
chain = (
    prompt_template
    | llm
    | output_parser
    | another_step
)
```

### 5. ✅ 미래 확장성
```python
# 나중에 추가 가능
from langchain.vectorstores import Chroma
from langchain.chains import RetrievalQA
from langgraph.graph import StateGraph

# ChromaDB로 벡터 검색
vectorstore = Chroma(...)

# RAG 체인 구성
qa_chain = RetrievalQA.from_chain_type(...)

# AI Agent 워크플로우
workflow = StateGraph(...)
```

---

## 🎯 핵심 정리

### LangChain이란?
> **AI 애플리케이션을 쉽게 만들기 위한 프레임워크**
>
> OpenAI API를 직접 쓰는 대신, LangChain이 제공하는 편리한 도구들을 사용

### 무엇이 바뀌었나?
> **AI 모듈 내부 구현만 OpenAI SDK → LangChain으로 교체**
>
> - 11개 AI 기능 모두 LangChain으로 재작성
> - 외부 인터페이스는 완전히 동일 (Backend/Frontend 영향 없음)

### 왜 바꿨나?
> 1. **평가 기준 충족** - LangChain 사용 요구사항 (25점 중 10점)
> 2. **코드 품질** - 더 간결하고 관리하기 쉬움
> 3. **확장 가능성** - 나중에 고급 기능 추가 용이

### 다른 팀원 영향?
> **전혀 없음!**
> - Backend: 0분 작업
> - Frontend: 0분 작업
> - 배포: pip install만 (5분)

---

## 📝 비유로 정리

### 음식 주문으로 비유

**이전 (OpenAI 직접 사용)**:
```
1. 식재료 직접 구매
2. 레시피 직접 찾기
3. 요리 직접 하기
4. 설거지 직접 하기
```

**이후 (LangChain 사용)**:
```
1. 밀키트 주문 (재료 + 레시피 제공)
2. 설명서대로 조리
3. 맛은 동일
4. 시간 절약 + 실패 확률 감소
```

**고객 (Backend/Frontend) 입장**:
```
음식(결과)만 받음
→ 직접 만들었든, 밀키트였든 상관없음
→ 맛(품질)만 좋으면 OK
```

---

## ✅ 최종 정리

| 관점 | 설명 |
|------|------|
| **LangChain이란?** | AI 프로그래밍을 쉽게 해주는 도구 모음 |
| **무엇이 바뀌었나?** | AI 모듈 내부가 OpenAI SDK → LangChain으로 |
| **외부에서 보면?** | 아무 변화 없음 (API 동일, 결과 동일) |
| **왜 바꿨나?** | 평가 기준 충족 + 코드 품질 향상 |
| **장점은?** | 평가 점수 +15점, 관리 용이, 확장 가능 |
| **다른 팀 영향?** | 없음 (Backend/Frontend 작업 0분) |

---

**한 줄 요약**:
> LangChain = "AI 프로그래밍 편의 도구"
>
> OpenAI를 **직접 부르던** 코드를 → LangChain이 **대신 부르도록** 변경
>
> 결과는 똑같지만 코드가 더 깔끔하고, 평가 점수도 올라감! 🎉
