# tech_master.py 파일 설명

## 📋 역할

**`tech_master.py`는 특허(Patent)와 논문(Article) 검색을 통합하는 마스터 라우터입니다.**

두 개의 서브 에이전트를 하나의 인터페이스로 통합하여, 사용자가 "특허" 또는 "논문"을 명시하지 않아도 자동으로 적절한 에이전트를 선택합니다.

---

## 🏗️ 아키텍처

```
사용자 입력
    ↓
tech_master() [라우터]
    ↓
parse_tech_intent() [의도 분류]
    ├─ "patent" → patent_search() [tech_patent_agent]
    └─ "article" → article_master() → run_scienceon_agent() [tech_article_agent]
```

---

## 🔑 주요 함수

### 1. `tech_master()` - 메인 진입점

```python
def tech_master(user_text: str, session_id: Optional[str] = None) -> dict:
```

**역할:**
- 사용자 입력을 받아서 특허/논문 중 어느 것으로 분류할지 결정
- 적절한 서브 에이전트를 호출

**흐름:**
```
1. parse_tech_intent() 호출 → 의도 분류
2. intent.kind == "patent" → patent_search() 호출
3. intent.kind == "article" → article_master() 호출
4. 기본값: patent_search() (의도 불명확 시)
```

---

### 2. `parse_tech_intent()` - 의도 분류

```python
def parse_tech_intent(user_text: str, session_id: Optional[str] = None) -> TechIntent:
```

**역할:**
- 사용자 입력을 분석하여 "patent" 또는 "article"로 분류
- LLM 기반 분류 + 키워드 기반 fallback

**분류 전략 (우선순위):**

1. **상세 요청 패턴 체크** (최우선)
   ```python
   # "1번에 대해 알려줘", "1번 상세" 같은 패턴
   if re.match(r"^\d+", t) and any(k in t for k in ["번", "에 대해", "알려줘", "자세히", "상세"]):
       # 이전 검색 결과가 있으면 그 타입으로 분류
       if has_pending_article_results(session_id):
           return TechIntent(kind="article", ...)
       if has_pending_patent_results(session_id):
           return TechIntent(kind="patent", ...)
   ```

2. **LLM 기반 분류**
   ```python
   llm_intent = classify_intent_with_llm(t, route_type="tech", fallback_route=None)
   # "patent" 또는 "article" 반환
   ```

3. **키워드 기반 fallback**
   ```python
   if any(k in t for k in ["특허", "Patent", "patent"]):
       return TechIntent(kind="patent", ...)
   if any(k in t for k in ["논문", "Article", "paper", "학술"]):
       return TechIntent(kind="article", ...)
   ```

4. **기본값**
   ```python
   # 기본은 특허 조회
   return TechIntent(kind="patent", ...)
   ```

**TechIntent 데이터 클래스:**
```python
@dataclass
class TechIntent:
    kind: Literal["patent", "article"]  # 분류 결과
    keyword: Optional[str] = None        # 추출된 키워드
    details: Optional[str] = None        # 상세 요청 시 원본 텍스트
```

---

### 3. `article_master()` - 논문 검색/다운로드 처리

```python
def article_master(user_text: str, session_id: Optional[str] = None) -> Dict[str, Any]:
```

**역할:**
- 논문 검색 및 PDF 다운로드를 처리하는 래퍼 함수
- `tech_article_agent`의 `run_scienceon_agent()`를 호출
- 세션 상태 관리 및 결과 정규화

**흐름:**
```
1. 입력 전처리
   - 숫자만 입력 + 이전 검색 결과 있으면 → "N번째 논문 pdf 다운받아줘"로 변환

2. 에이전트 실행
   - 세션 상태 복원 (_ARTICLE_MEMORY에서)
   - run_scienceon_agent() 호출
   - 세션 상태 저장

3. 결과 처리
   - 검색 결과 정규화 (_normalize_items())
   - PDF 다운로드된 경우 Milvus 저장 시도 (_maybe_store_pdf_to_milvus())

4. 폴백 처리
   - 에이전트 실패 시 HTML 스크래핑으로 폴백
```

**반환값:**
```python
{
    "ok": bool,              # 성공 여부
    "intent": str,            # "search", "download", "detail", "unknown"
    "message": str,           # 사용자 메시지
    "summary": str,           # AI 요약 (검색 시)
    "link": str,              # 참고 링크
    "items": List[Dict],      # 정규화된 검색 결과
    "results": List[Dict],    # 원본 검색 결과
}
```

---

### 4. `_maybe_store_pdf_to_milvus()` - 선택적 벡터DB 저장

```python
def _maybe_store_pdf_to_milvus(pdf_path: str) -> None:
```

**역할:**
- 논문 PDF를 다운로드한 후, 선택적으로 Milvus 벡터DB에 저장
- 환경변수 설정 시에만 동작 (`MILVUS_URI`, `OPENAI_API_KEY` 필요)

**동작:**
```
1. 환경변수 확인
   - MILVUS_URI, OPENAI_API_KEY 필요

2. PDF 텍스트 추출
   - PyPDF2로 PDF 읽기
   - 페이지별 텍스트 추출
   - 1500자 단위로 청크 분할

3. 임베딩 생성
   - OpenAI Embeddings 사용 (text-embedding-3-small)

4. Milvus 저장
   - 컬렉션 생성 (없으면)
   - 텍스트 청크 + 임베딩 벡터 저장
```

**참고:**
- `tech_patent_agent`는 자체적으로 벡터DB 저장 기능이 있음
- `tech_article_agent`는 벡터DB 저장 기능이 없어서, `tech_master`에서 선택적으로 제공

---

## 🔄 전체 흐름도

### 시나리오 1: 특허 검색
```
사용자: "배터리 특허 찾아줘"
    ↓
tech_master("배터리 특허 찾아줘")
    ↓
parse_tech_intent()
    → LLM 분류 또는 키워드 매칭 → "patent"
    ↓
patent_search("배터리 특허 찾아줘")
    ↓
tech_patent_agent의 워크플로우 실행
    → 벡터DB 확인 → KIPRIS 검색 → 결과 반환
```

### 시나리오 2: 논문 검색
```
사용자: "인공지능 논문 찾아줘"
    ↓
tech_master("인공지능 논문 찾아줘")
    ↓
parse_tech_intent()
    → LLM 분류 또는 키워드 매칭 → "article"
    ↓
article_master("인공지능 논문 찾아줘")
    ↓
run_scienceon_agent("인공지능 논문 찾아줘")
    ↓
tech_article_agent의 워크플로우 실행
    → 의도 분류 → 논문 검색 → 결과 반환
```

### 시나리오 3: 상세 요청 (이전 검색 결과 기반)
```
사용자: "1번에 대해 알려줘"
    ↓
tech_master("1번에 대해 알려줘")
    ↓
parse_tech_intent()
    → 상세 요청 패턴 감지
    → has_pending_article_results() 확인
    → "article"로 분류
    ↓
article_master("1번에 대해 알려줘")
    ↓
run_scienceon_agent("1번에 대해 알려줘")
    ↓
tech_article_agent의 show_article_detail() 실행
    → PDF 다운로드 → LLM 요약 → 결과 반환
```

---

## 📊 세션 관리

### 논문 검색 상태 메모리
```python
_ARTICLE_MEMORY: Dict[str, ScienceONAgentState] = {}
```

**역할:**
- 세션별로 논문 검색 상태를 저장
- 멀티턴 대화 지원 (이전 검색 결과 유지)

**사용 예시:**
```python
# 첫 번째 요청
result1 = tech_master("인공지능 논문 찾아줘", session_id="user123")
# _ARTICLE_MEMORY["user123"]에 검색 결과 저장

# 두 번째 요청 (같은 세션)
result2 = tech_master("1번", session_id="user123")
# _ARTICLE_MEMORY["user123"]에서 이전 검색 결과 복원
# "1번" → "1번째 논문 pdf 다운받아줘"로 자동 변환
```

---

## 🔧 주요 특징

### 1. **에러 처리 및 폴백**

**논문 에이전트 실패 시:**
```python
try:
    # 에이전트 실행
except Exception:
    # HTML 스크래핑 폴백
    fallback = _html_fallback_search(user_text)
```

**에이전트 임포트 실패 시:**
```python
try:
    from .tech_article_agent import run_scienceon_agent
    _ARTICLE_AGENT_AVAILABLE = True
except Exception as _e:
    _ARTICLE_AGENT_AVAILABLE = False
    # 폴백 모드로 동작
```

### 2. **선택적 벡터DB 저장**

- 논문 PDF 다운로드 후 자동으로 Milvus에 저장 시도
- 환경변수 설정 시에만 동작
- 실패해도 조용히 무시 (에러 발생 안 함)

### 3. **스마트 입력 처리**

- 숫자만 입력: 이전 검색 결과가 있으면 자동으로 다운로드 명령으로 변환
- 상세 요청: 이전 검색 결과 타입에 따라 자동 분류

---

## 🔗 다른 파일과의 관계

```
supervisor.py (최상위 라우터)
    ↓
tech_master.py (Tech 마스터 라우터)
    ├─→ tech_patent_agent.py (특허 에이전트)
    └─→ tech_article_agent.py (논문 에이전트)
        └─→ llm_router.py (LLM 기반 의도 분류)
```

---

## 📝 요약

**`tech_master.py`는:**

1. ✅ **통합 라우터**: 특허와 논문 검색을 하나의 인터페이스로 통합
2. ✅ **스마트 분류**: LLM + 키워드 기반으로 자동 의도 분류
3. ✅ **세션 관리**: 멀티턴 대화 지원 (이전 검색 결과 유지)
4. ✅ **에러 처리**: 폴백 메커니즘으로 안정성 보장
5. ✅ **선택적 기능**: 벡터DB 저장 등 선택적 기능 제공

**사용자가 "특허" 또는 "논문"을 명시하지 않아도 자동으로 적절한 에이전트를 선택하여 검색을 수행합니다.**



