# tech_patent_agent.py 코드 분석 및 흐름 비교

## 📋 현재 코드의 동작 흐름

### 1. 메인 진입점: `patent_search()` 함수 (라인 980-1306)

```
사용자 입력
    ↓
[특허 번호 패턴 감지?]
    ├─ YES → 특허 번호로 직접 검색 → download_pdf → rag_answer
    └─ NO
        ↓
[선택 번호 패턴 감지? (예: "1번", "7번")]
    ├─ YES → 캐시에서 선택 → download_pdf → rag_answer
    └─ NO
        ↓
[LangGraph 워크플로우 실행]
```

### 2. LangGraph 워크플로우 (라인 898-930)

```
check_db (벡터DB 확인)
    ↓
[벡터DB에 유사 특허가 있는가?]
    ├─ YES (score <= 0.6 또는 관련 결과 있음)
    │   └─→ rag_answer (RAG 답변만 제공)
    │
    └─ NO
        └─→ search_kipris (KIPRIS API 검색)
            └─→ END (리스트만 반환)
```

### 3. 각 노드의 역할

#### `check_db_node()` (라인 667-697)
- 벡터DB에서 유사 특허 검색
- **문제점**: 벡터DB에 있으면 PDF 링크나 전체 내용을 제공하지 않음
- RAG 답변만 제공

#### `search_kipris_node()` (라인 700-749)
- KIPRIS API로 특허 검색
- 검색 결과 리스트 반환
- **정상 동작**

#### `download_pdf_node()` (라인 752-843)
- PDF 다운로드
- PDF → TXT 변환
- 벡터DB에 임베딩 저장
- **정상 동작**

#### `rag_answer_node()` (라인 846-884)
- RAG로 답변 생성
- **문제점**: 벡터DB 결과일 때 PDF 링크를 제공하지 않음

---

## 🔍 사용자가 원하는 흐름 vs 현재 구현

### ✅ 일치하는 부분

1. **벡터DB 확인** ✓
   - `check_db_node()`에서 구현됨

2. **벡터DB에 없을 때** ✓
   - KIPRIS 검색 → 리스트업 → 사용자 선택 → PDF 다운로드 → 임베딩 저장
   - 대부분 구현됨

3. **PDF → TXT 변환** ✓
   - `extract_pdf_text()` 함수로 구현됨

4. **벡터DB에 임베딩 저장** ✓
   - `embed_and_store_patent()` 함수로 구현됨

5. **RAG 답변** ✓
   - `rag_answer_node()`로 구현됨

### ❌ 차이점 및 문제점

#### 1. **벡터DB에 있을 때 전체 내용 + PDF 링크 제공 안 함**

**원하는 동작:**
```
벡터DB에 있음 → 전체 내용 가져오기 + PDF 링크 제공
```

**현재 동작:**
```
벡터DB에 있음 → RAG 답변만 제공 (PDF 링크 없음, 전체 내용도 제한적)
```

**문제 코드 위치:**
- `check_db_node()` (라인 667-697): 벡터DB 결과를 찾아도 PDF 링크를 가져오지 않음
- `rag_answer_node()` (라인 846-884): 벡터DB 결과일 때 PDF 링크를 state에 포함하지 않음

**해결 방법:**
- 벡터DB에서 특허를 찾으면, 해당 특허의 application_number로 PDF 링크를 다시 조회해야 함
- 또는 벡터DB 스키마에 `pdf_link` 필드를 추가하여 저장

#### 2. **벡터DB 결과에서 PDF 링크 정보 부족**

**현재 벡터DB 스키마** (라인 524-531):
```python
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="app_no", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="abstract", dtype=DataType.VARCHAR, max_length=2000),
    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),
]
```

**문제:** `pdf_link` 필드가 없음

#### 3. **TXT 내용을 사용자에게 직접 제공하지 않음**

**원하는 동작:**
```
PDF 다운로드 → TXT 변환 → 벡터DB 저장 → 사용자에게 TXT 내용 + PDF 링크 제공
```

**현재 동작:**
```
PDF 다운로드 → TXT 변환 → 벡터DB 저장 → RAG 답변만 제공 (TXT 내용은 제공하지 않음)
```

**문제 코드 위치:**
- `download_pdf_node()` (라인 825): `state["pdf_content"]`에 저장하지만, 반환값에서 TXT 전체 내용을 제공하지 않음
- `patent_search()` 함수 (라인 1058-1079): PDF 다운로드 후 TXT 내용을 반환하지 않음

---

## 🔧 수정이 필요한 부분

### 1. 벡터DB 스키마에 PDF 링크 필드 추가
```python
FieldSchema(name="pdf_link", dtype=DataType.VARCHAR, max_length=512),
```

### 2. `check_db_node()` 수정
- 벡터DB에서 특허를 찾으면 PDF 링크도 함께 조회
- 또는 application_number로 PDF 링크를 다시 조회

### 3. `download_pdf_node()` 수정
- PDF 링크를 벡터DB에 저장할 때 함께 저장

### 4. `patent_search()` 함수 수정
- 벡터DB 결과일 때 PDF 링크를 반환값에 포함
- PDF 다운로드 후 TXT 전체 내용을 반환값에 포함

### 5. `rag_answer_node()` 수정
- 벡터DB 결과일 때도 PDF 링크를 state에 포함

---

## 📊 전체 흐름 비교표

| 단계 | 원하는 동작 | 현재 구현 | 상태 |
|------|------------|----------|------|
| 1. 사용자 입력 | ✓ | ✓ | ✅ |
| 2. 벡터DB 확인 | ✓ | ✓ | ✅ |
| 3. 벡터DB에 있음 | 전체 내용 + PDF 링크 | RAG 답변만 | ❌ |
| 4. 벡터DB에 없음 | KIPRIS 검색 | KIPRIS 검색 | ✅ |
| 5. 리스트업 | ✓ | ✓ | ✅ |
| 6. 사용자 선택 | ✓ | ✓ | ✅ |
| 7. PDF 다운로드 | ✓ | ✓ | ✅ |
| 8. PDF → TXT 변환 | ✓ | ✓ | ✅ |
| 9. 벡터DB 저장 | ✓ | ✓ | ✅ |
| 10. TXT 내용 제공 | ✓ | ❌ (RAG만) | ❌ |
| 11. PDF 링크 제공 | ✓ | 부분적 | ⚠️ |
| 12. RAG 답변 | ✓ | ✓ | ✅ |

---

## 💡 요약

**현재 코드는 대부분의 흐름을 구현했지만, 다음 두 가지가 부족합니다:**

1. **벡터DB에 있을 때**: 전체 내용과 PDF 링크를 제공하지 않음
2. **PDF 다운로드 후**: TXT 전체 내용을 사용자에게 직접 제공하지 않음 (RAG 답변만 제공)

이 두 가지를 수정하면 사용자가 원하는 흐름과 완전히 일치하게 됩니다.



