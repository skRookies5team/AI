# 특허 검색 챗봇 (LangGraph 기반)

LangGraph 기반의 특허 검색 챗봇입니다. KIPRIS API를 사용하여 특허를 검색하고, PDF를 다운로드하여 FAISS 벡터 저장소에 저장합니다.

## 기능

1. **특허 검색**: 키워드로 KIPRIS에서 특허 검색 (최대 5개)
2. **상세 정보 조회**: 특허 제목 또는 출원번호로 상세 정보 조회
3. **PDF 다운로드**: 특허 PDF 다운로드 및 텍스트 추출
4. **FAISS 벡터 저장**: PDF 텍스트를 FAISS 벡터 저장소에 저장

## 설치

### 필수 의존성

```bash
pip install requests xmltodict langgraph PyPDF2
```

### 선택적 의존성 (FAISS 벡터 저장용)

```bash
pip install faiss-cpu sentence-transformers langchain-text-splitters
```

또는 GPU 버전:

```bash
pip install faiss-gpu sentence-transformers langchain-text-splitters
```

## 사용법

### 1. 기본 사용

```bash
python patent_chatbot.py
```

### 2. 대화 예시

```
사용자> 디스플레이 관련 특허 찾아줘
에이전트> '디스플레이' 관련 특허 5개를 찾았습니다.

1. 디스플레이 장치
   출원번호: 1020200049352
   초록: 디스플레이 장치에 관한 발명...

2. 디스플레이 패널
   출원번호: 1020200086902
   초록: 디스플레이 패널에 관한 발명...

사용자> 디스플레이 장치에 대해 자세히 알고싶어
에이전트> 📘 특허 상세 정보
제목: 디스플레이 장치
출원번호: 1020200049352
PDF 경로: data/pdf/1020200049352.pdf
📄 PDF 요약:
[PDF 텍스트 요약 내용...]
```

## 파일 구조

```
ai_module/
├── patent_agent.py      # LangGraph 기반 특허 검색 에이전트
├── patent_chatbot.py    # 챗봇 실행 스크립트
└── data/
    ├── pdf/             # 다운로드된 PDF 파일
    ├── faiss/           # FAISS 벡터 저장소
    │   ├── patents.index
    │   └── patents_meta.json
    └── ...
```

## 주요 함수

### `patent_search(user_query: str) -> Dict[str, Any]`

특허 검색을 실행합니다.

**Parameters:**
- `user_query`: 사용자 질의 (예: "디스플레이 관련 특허 찾아줘")

**Returns:**
- 검색 결과 또는 상세 정보 딕셔너리

### LangGraph 워크플로우

1. **classify_intent**: 사용자 의도 분류 (검색 vs 상세 보기)
2. **search_patents**: KIPRIS API를 통한 특허 검색
3. **fetch_patent_detail**: PDF 다운로드 및 텍스트 추출
4. **save_to_faiss**: FAISS 벡터 저장소에 저장

## 환경 변수 설정 (.env 파일)

프로젝트 루트에 `.env` 파일을 생성하고 다음 환경 변수를 설정하세요:

```env
# KIPRIS API 키
KIPRIS_API_KEY=qIbDAYDHWblzGOh7iHbXzOBjflL5ovmtshmsg83xaPQ=

# OpenAI API 키 (선택적, 질문-답변 기능용)
OPENAI_API_KEY=your-openai-api-key-here
```

또는 `API_KEY`로도 설정 가능합니다 (하위 호환성).

## 주의사항

1. **KIPRIS API 키**: `.env` 파일에 `KIPRIS_API_KEY` 또는 `API_KEY`를 설정해야 합니다.
2. **FAISS 저장소**: FAISS가 설치되지 않으면 벡터 저장 기능을 사용할 수 없습니다.
3. **PDF 텍스트 추출**: PyPDF2가 설치되지 않으면 텍스트 추출이 실패할 수 있습니다.
4. **OpenAI API 키**: 질문-답변 기능에서 LLM을 사용하려면 `OPENAI_API_KEY`를 설정하세요 (선택적).

## 문제 해결

### FAISS 오류

FAISS가 설치되지 않았거나 오류가 발생하면 벡터 저장 기능만 사용할 수 없습니다. 검색 및 PDF 다운로드는 정상 작동합니다.

### PDF 다운로드 실패

KIPRIS API에서 PDF를 제공하지 않는 특허는 다운로드할 수 없습니다. 오류 메시지가 표시됩니다.

### 텍스트 추출 실패

PDF 파일이 손상되었거나 텍스트가 없는 경우 텍스트 추출이 실패할 수 있습니다.

## 라이선스

이 프로젝트는 기존 코드를 기반으로 LangGraph를 사용하여 재구현되었습니다.

