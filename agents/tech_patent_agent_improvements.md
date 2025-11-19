# tech_patent_agent.py 개선 사항

## ✅ 추가된 4가지 기능

### 1. 작업 진행 상황 표시 기능

**목적**: 챗봇에서 진행 시간이 오래 걸릴 때 사용자에게 현재 작업 상태를 알림

**구현 내용:**
- `set_progress_callback()`: 진행 상황 콜백 함수 설정
- `_notify_progress()`: 각 노드에서 진행 상황 메시지 전송
- `progress_status` 필드: State에 진행 상황 메시지 저장

**사용 예시:**
```python
from AI.agents.tech_patent_agent import set_progress_callback

def progress_handler(message: str):
    print(f"진행 상황: {message}")

set_progress_callback(progress_handler)
result = patent_search("배터리 특허 찾아줘")
```

**진행 상황 메시지 예시:**
- "🔍 특허 검색을 시작합니다..."
- "🔍 벡터DB에서 관련 특허를 검색 중입니다..."
- "✅ 벡터DB에서 관련 특허를 찾았습니다. 답변을 생성 중입니다..."
- "📥 특허 PDF 다운로드 중: 1020230001234"
- "📄 PDF에서 텍스트 추출 중..."
- "💾 벡터DB에 특허 정보 저장 중..."
- "🤖 AI가 답변을 생성 중입니다..."
- "✅ 답변 생성 완료"

---

### 2. 서버 기반 vs 웹 검색 선택 기능

**목적**: 사용자가 서버 기반 검색 또는 웹 검색을 선택할 수 있도록 함

**구현 내용:**
- `search_mode` 파라미터: "server" 또는 "web" 선택
- `use_server_first` 파라미터: 서버 기반 먼저 시도 여부 (기본값: True)
- 서버 기반 검색 실패 시 자동으로 웹 검색으로 전환

**사용 예시:**
```python
# 서버 기반 먼저 시도 (실패 시 자동으로 웹 검색)
result = patent_search("배터리 특허", search_mode="server", use_server_first=True)

# 웹 검색만 사용
result = patent_search("배터리 특허", search_mode="web", use_server_first=False)

# 자동 선택 (서버 기반 먼저 시도)
result = patent_search("배터리 특허", use_server_first=True)
```

**동작 흐름:**
1. `use_server_first=True`이고 `search_mode="server"`인 경우:
   - 벡터DB에서 검색 시도
   - 결과가 없거나 부족하면 자동으로 웹 검색으로 전환
   - 진행 상황 메시지: "⚠️ 서버 기반 검색 결과가 없습니다. 웹 검색으로 전환합니다..."

2. `search_mode="web"`인 경우:
   - 바로 웹 검색 수행

---

### 3. 골든셋과 비교하여 90% 이상 유사도로 답변 개선

**목적**: 골든셋(표준 답변)과 비교하여 더 깔끔하고 구조화된 답변 생성

**구현 내용:**
- `load_golden_set()`: 골든셋 JSON 파일 로드 (`data/golden_set.json`)
- `_GOLDEN_SET`: 전역 골든셋 딕셔너리 (키: 질문, 값: 답변)
- RAG 답변 생성 시 골든셋과 유사한 질문 찾기
- 골든셋 답변을 참고하여 더 정확하고 구조화된 답변 생성
- 답변 품질 검증 및 재생성 (유사도가 낮으면 재시도)

**골든셋 파일 형식 (`data/golden_set.json`):**
```json
{
  "배터리 충전 기술은?": "배터리 충전 기술은 다음과 같은 특징을 가집니다:\n\n1. 충전 방식\n2. 효율성\n3. 안전성\n...",
  "디스플레이 기술의 특징은?": "..."
}
```

**동작 흐름:**
1. 사용자 질문과 골든셋의 질문을 키워드 기반으로 비교
2. 유사도가 50% 이상이면 골든셋 답변을 참고
3. 골든셋 답변의 구조와 스타일을 참고하여 답변 생성
4. 생성된 답변과 골든셋 답변의 유사도 검증
5. 유사도가 30% 미만이면 더 강한 프롬프트로 재생성

**프롬프트 개선:**
- 골든셋이 있으면: "참고 답변의 구조와 스타일을 참고하되, 제공된 특허 정보를 바탕으로 정확하고 상세한 답변을 작성하세요."
- 답변은 깔끔하고 구조화된 형식으로 작성

---

### 4. 세부 원문 요청 시 "특허_키워드" 형식으로 저장하여 RAG 시 필터링

**목적**: RAG 검색 시 관련 없는 내용을 필터링하여 더 정확한 결과 제공

**구현 내용:**
- `patent_keyword` 필드: Milvus 컬렉션에 "특허_키워드" 형식 필드 추가
- `embed_and_store_patent()`: 특허 저장 시 `patent_keyword` 파라미터 추가
- `check_vector_db()`: `filter_keyword` 파라미터로 필터링 지원
- 세부 원문 요청 시 해당 키워드로만 검색

**컬렉션 스키마 변경:**
```python
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="app_no", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="abstract", dtype=DataType.VARCHAR, max_length=2000),
    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
    FieldSchema(name="patent_keyword", dtype=DataType.VARCHAR, max_length=128),  # 새로 추가
    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),
]
```

**사용 예시:**
```python
# 특허 저장 시
embed_and_store_patent(
    app_no="1020230001234",
    title="배터리 충전 장치",
    abstract="...",
    text_path=Path("data/text/1020230001234.txt"),
    patent_keyword="특허_배터리"  # 특허_키워드 형식
)

# 검색 시 필터링
results = check_vector_db(
    query="배터리 충전 기술",
    limit=5,
    filter_keyword="특허_배터리"  # 특허_배터리 키워드만 검색
)
```

**동작 흐름:**
1. 세부 원문 요청 시 (`download_pdf_node`):
   - 키워드 추출: "배터리" → `patent_keyword = "특허_배터리"`
   - 벡터DB 저장 시 `patent_keyword` 필드에 저장

2. RAG 검색 시 (`check_db_node`):
   - `search_mode == "server"`이고 키워드가 있으면
   - `filter_keyword = f"특허_{keyword}"`로 필터링
   - Milvus 검색 시 `expr = f'patent_keyword == "{filter_keyword}"'` 조건 적용

**효과:**
- 관련 없는 특허가 검색 결과에 포함되지 않음
- 더 정확한 RAG 답변 생성
- 검색 성능 향상 (필터링으로 후보 수 감소)

---

## 📝 State 필드 추가

```python
class PatentSearchState(TypedDict):
    # 기존 필드들...
    progress_status: str  # 진행 상황 메시지
    search_mode: Optional[Literal["server", "web"]]  # 검색 모드
    use_server_first: bool  # 서버 기반 먼저 시도 여부
    next_action: Literal[..., "ask_search_mode"]  # 검색 모드 선택 질문 추가
```

---

## 🔧 함수 시그니처 변경

### `patent_search()`
```python
def patent_search(
    keyword: str,
    session_id: Optional[str] = None,
    search_mode: Optional[Literal["server", "web"]] = None,  # 새로 추가
    use_server_first: bool = True  # 새로 추가
) -> Dict[str, Any]:
```

### `check_vector_db()`
```python
def check_vector_db(
    query: str,
    limit: int = 5,
    max_distance: float = 0.8,
    filter_keyword: Optional[str] = None  # 새로 추가
) -> List[Dict[str, Any]]:
```

### `embed_and_store_patent()`
```python
def embed_and_store_patent(
    app_no: str,
    title: str,
    abstract: str,
    text_path: Path,
    patent_keyword: Optional[str] = None  # 새로 추가
) -> int:
```

---

## 📋 새로운 함수

### `set_progress_callback()`
```python
def set_progress_callback(callback: Optional[Callable[[str], None]]) -> None:
    """진행 상황 콜백 함수 설정."""
```

### `load_golden_set()`
```python
def load_golden_set(file_path: Optional[str] = None) -> None:
    """골든셋 로드 (JSON 파일)."""
```

### `ask_search_mode_node()`
```python
def ask_search_mode_node(state: PatentSearchState) -> PatentSearchState:
    """검색 모드 선택 질문."""
```

---

## 🎯 사용 예시

### 전체 예시
```python
from AI.agents.tech_patent_agent import (
    patent_search,
    set_progress_callback,
    load_golden_set
)

# 진행 상황 콜백 설정
def progress_handler(message: str):
    print(f"[진행] {message}")

set_progress_callback(progress_handler)

# 골든셋 로드 (선택사항)
load_golden_set("data/golden_set.json")

# 특허 검색 (서버 기반 먼저 시도)
result = patent_search(
    "배터리 충전 기술 특허",
    session_id="user123",
    search_mode="server",
    use_server_first=True
)

# 결과 확인
if result.get("ok"):
    print(result.get("message"))
    print(f"진행 상황: {result.get('progress_status', '')}")
```

---

## ⚠️ 주의사항

1. **기존 컬렉션 마이그레이션**: 기존에 생성된 Milvus 컬렉션에는 `patent_keyword` 필드가 없을 수 있습니다. 새로 생성되는 컬렉션에만 적용됩니다.

2. **골든셋 파일**: `data/golden_set.json` 파일이 없어도 동작하지만, 골든셋 기능은 사용되지 않습니다.

3. **하위 호환성**: 기존 코드와의 호환성을 위해 모든 새 파라미터는 기본값이 설정되어 있습니다.

---

## ✅ 완료된 작업

- [x] 작업 진행 상황 표시 기능 추가
- [x] 서버 기반 vs 웹 검색 선택 기능 추가
- [x] 골든셋과 비교하여 답변 개선
- [x] 특허_키워드 형식으로 저장하여 RAG 시 필터링

**모든 기능이 `tech_patent_agent.py` 파일에만 추가되었으며, `tech_master.py`는 수정하지 않았습니다.**



