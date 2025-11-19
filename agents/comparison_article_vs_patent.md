# tech_article_agent.py vs tech_patent_agent.py 비교

## 📚 기본 정보

### tech_article_agent.py
- **목적**: 논문(Paper/Article) 검색 및 다운로드
- **데이터 소스**: ScienceON (한국과학기술정보통신원)
- **주요 기능**: 논문 검색, PDF 다운로드, 논문 상세 정보 제공

### tech_patent_agent.py
- **목적**: 특허(Patent) 검색 및 분석
- **데이터 소스**: KIPRIS (한국지식재산청)
- **주요 기능**: 특허 검색, PDF 다운로드, 벡터DB 임베딩, RAG 답변

---

## 🔍 주요 차이점

### 1. 데이터 소스 및 API

| 항목 | tech_article_agent.py | tech_patent_agent.py |
|------|---------------------|---------------------|
| **API** | ScienceON 웹 크롤링 | KIPRIS API (공식 API) |
| **URL** | `scienceon.kisti.re.kr` | `plus.kipris.or.kr` |
| **인증** | 불필요 (크롤링) | API Key 필요 (`KIPRIS_API_KEY`) |
| **데이터 형식** | HTML 스크래핑 | XML 응답 파싱 |

### 2. 벡터DB 및 임베딩

| 항목 | tech_article_agent.py | tech_patent_agent.py |
|------|---------------------|---------------------|
| **벡터DB 사용** | ❌ 없음 | ✅ Milvus 사용 |
| **임베딩 저장** | ❌ 없음 | ✅ PDF → TXT → 임베딩 저장 |
| **RAG 기능** | ❌ 없음 | ✅ 벡터DB 검색 후 RAG 답변 |
| **키워드 기반 분류** | ❌ 없음 | ✅ 키워드별 컬렉션 분리 |

**tech_patent_agent.py의 벡터DB 구조:**
```python
# 분야별 컬렉션 생성 (예: patents_embeddings_display, patents_embeddings_battery)
- app_no: 특허 번호
- title: 제목
- abstract: 초록
- content: 본문 내용
- embedding: 384차원 벡터
```

### 3. 워크플로우 구조

#### tech_article_agent.py
```
사용자 입력
    ↓
classify_intent (의도 분류)
    ├─ search → search_papers → generate_response
    ├─ download → download_pdf → generate_response
    └─ detail → show_article_detail → generate_response
```

**의도 분류:**
- `search`: 논문 검색
- `download`: PDF 다운로드
- `detail`: 논문 상세 정보 (PDF 요약)

#### tech_patent_agent.py
```
사용자 입력
    ↓
check_db (벡터DB 확인)
    ├─ 있음 → rag_answer (RAG 답변)
    └─ 없음 → search_kipris (KIPRIS 검색)
        ↓
    사용자 선택 → download_pdf → rag_answer
```

**워크플로우 특징:**
- 벡터DB 우선 검색
- 벡터DB에 없으면 KIPRIS API 검색
- PDF 다운로드 후 자동 임베딩 저장

### 4. 상태 관리 (State)

#### tech_article_agent.py
```python
class ScienceONAgentState(TypedDict):
    messages: List[BaseMessage]  # 대화 이력
    user_input: str
    intent: Literal["search", "download", "detail", "unknown"]
    query: str
    download_index: int
    keyword: str
    results: List[Dict[str, Any]]
    summary: str
    response: str
    error: Optional[str]
```

#### tech_patent_agent.py
```python
class PatentSearchState(TypedDict):
    user_query: str
    keyword: str
    search_results: List[Dict[str, Any]]
    selected_patent: Optional[Dict[str, Any]]
    selected_indices: List[int]
    vector_db_results: List[Dict[str, Any]]  # 벡터DB 검색 결과
    pdf_content: str
    pdf_link: str
    answer: str  # RAG 답변
    next_action: Literal["check_db", "search_kipris", "download_pdf", "rag_answer", "end"]
```

### 5. PDF 처리 방식

#### tech_article_agent.py
- PDF 다운로드만 수행
- PDF 요약 생성 (LLM 사용)
- **임베딩 저장 없음**

```python
# show_article_detail() 함수에서
- PDF 다운로드
- PDF → 텍스트 추출
- LLM으로 요약 생성
- 사용자에게 요약 제공
```

#### tech_patent_agent.py
- PDF 다운로드
- PDF → TXT 변환
- **벡터DB에 임베딩 저장** (분야별 컬렉션)
- RAG 답변 생성

```python
# download_pdf_node() 함수에서
- PDF 다운로드
- PDF → 텍스트 추출
- 텍스트를 청크로 분할
- 각 청크를 임베딩하여 벡터DB 저장
- 키워드 기반으로 컬렉션 선택
```

### 6. 검색 결과 처리

#### tech_article_agent.py
```python
# 검색 결과 구조
{
    "title": "논문 제목",
    "pdf_link": "PDF 링크",
    "detail_link": "상세 페이지 링크",
    "pdf_path": "로컬 저장 경로 (다운로드 후)"
}
```

#### tech_patent_agent.py
```python
# 검색 결과 구조
{
    "application_number": "특허 출원번호",
    "title": "특허 제목",
    "abstract": "초록"
}

# 벡터DB 결과 구조
{
    "application_number": "특허 번호",
    "title": "제목",
    "abstract": "초록",
    "content": "본문 내용",
    "score": 0.5,  # 유사도 점수
    "similarity": 0.67
}
```

### 7. LLM 사용 방식

#### tech_article_agent.py
- **의도 분류**: LLM 사용 (ChatPromptTemplate)
- **논문 요약**: LLM 사용 (PDF 텍스트 기반)
- **응답 생성**: 간단한 템플릿 기반

#### tech_patent_agent.py
- **의도 분류**: 규칙 기반 (LLM 사용 안 함)
- **RAG 답변**: LLM 사용 (벡터DB 결과 또는 PDF 내용 기반)
- **키워드 추출**: 규칙 기반 (키워드 맵 사용)

### 8. 세션 관리

#### tech_article_agent.py
```python
# 전역 메모리 사용
_ARTICLE_MEMORY: Dict[str, ScienceONAgentState] = {}

# 세션별 상태 저장
if session_id:
    _ARTICLE_MEMORY[session_id] = state
```

#### tech_patent_agent.py
```python
# 전역 캐시 사용
_PATENT_SEARCH_CACHE: Dict[str, List[Dict]] = {}  # 세션별 검색 결과만 캐시
_PATENT_SESSION_ID: Optional[str] = None

# 검색 결과만 캐시, 전체 상태는 저장하지 않음
_save_search_results(results, session_id)
```

### 9. 에러 처리 및 폴백

#### tech_article_agent.py
- 에이전트 실패 시 HTML 스크래핑 폴백
- 다운로드 실패 시 에러 메시지 반환

```python
# article_search() 함수에서
if _AGENT_AVAILABLE:
    try:
        # 에이전트 사용
    except Exception:
        # HTML 스크래핑 폴백
```

#### tech_patent_agent.py
- LangGraph 미사용 시 기본 KIPRIS 검색으로 폴백
- 벡터DB 실패 시 KIPRIS 검색으로 자동 전환

```python
# patent_search() 함수에서
if LANGCHAIN_AVAILABLE:
    try:
        # LangGraph 워크플로우 실행
    except Exception:
        # 기본 KIPRIS 검색으로 폴백
```

### 10. 통합 마스터 에이전트 지원

#### tech_article_agent.py
- ✅ **마스터 에이전트 통합 지원**
- `create_scienceon_subgraph()`: 서브 그래프 생성
- `extract_scienceon_state()`: 상태 추출
- `merge_scienceon_state()`: 상태 병합
- `create_scienceon_node()`: 노드 함수 생성

#### tech_patent_agent.py
- ❌ **마스터 에이전트 통합 기능 없음**
- 독립 실행만 지원
- `patent_search()` 함수로 직접 호출

---

## 📊 기능 비교표

| 기능 | tech_article_agent.py | tech_patent_agent.py |
|------|----------------------|---------------------|
| **데이터 타입** | 논문 (Paper/Article) | 특허 (Patent) |
| **데이터 소스** | ScienceON (크롤링) | KIPRIS (API) |
| **벡터DB** | ❌ | ✅ Milvus |
| **임베딩 저장** | ❌ | ✅ |
| **RAG 답변** | ❌ | ✅ |
| **의도 분류** | ✅ LLM 기반 | ❌ 규칙 기반 |
| **PDF 요약** | ✅ LLM 요약 | ❌ (RAG 답변만) |
| **세션 관리** | ✅ 전체 상태 저장 | ⚠️ 검색 결과만 캐시 |
| **마스터 에이전트 통합** | ✅ 완전 지원 | ❌ 없음 |
| **키워드 기반 분류** | ❌ | ✅ 분야별 컬렉션 |

---

## 💡 사용 시나리오

### tech_article_agent.py 사용 시나리오
```
사용자: "인공지능 논문 찾아줘"
    → 검색 결과 리스트 제공
사용자: "1번에 대해 알려줘"
    → PDF 다운로드 → LLM 요약 제공
사용자: "2번째 논문 pdf 다운받아줘"
    → PDF 다운로드만 수행
```

### tech_patent_agent.py 사용 시나리오
```
사용자: "배터리 관련 특허 찾아줘"
    → 벡터DB 확인 → 없으면 KIPRIS 검색 → 리스트 제공
사용자: "1번"
    → PDF 다운로드 → TXT 변환 → 벡터DB 저장 → RAG 답변
사용자: "이 특허의 핵심 기술은?"
    → 벡터DB에서 검색 → RAG 답변
```

---

## 🔑 핵심 차이점 요약

1. **데이터 타입**: 논문 vs 특허
2. **벡터DB**: article은 없음, patent는 있음
3. **RAG**: article은 없음, patent는 있음
4. **임베딩 저장**: article은 없음, patent는 있음
5. **마스터 에이전트 통합**: article은 지원, patent는 미지원
6. **의도 분류**: article은 LLM, patent는 규칙 기반
7. **PDF 처리**: article은 요약만, patent는 임베딩 저장까지

---

## ✅ 결론

**tech_article_agent.py**는:
- 논문 검색 및 다운로드에 특화
- 마스터 에이전트 통합 지원
- 간단한 구조 (벡터DB 없음)

**tech_patent_agent.py**는:
- 특허 검색 및 분석에 특화
- 벡터DB 기반 RAG 지원
- 장기적 지식 축적 (임베딩 저장)
- 더 복잡한 구조

두 에이전트는 **서로 다른 목적**을 가지고 있으며, **상호 보완적**으로 사용할 수 있습니다.



