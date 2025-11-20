# tech_patent_agent.py 수정 사항 Q&A

## 질문 1: Milvus DB 공용 서버에서 임베딩값 저장되는 위치나 방식은 바뀐 게 없는가?

### ✅ 답변: **기본적으로 동일합니다. 필드 하나만 추가되었습니다.**

**변경 전:**
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

**변경 후:**
```python
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="app_no", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="abstract", dtype=DataType.VARCHAR, max_length=2000),
    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
    FieldSchema(name="patent_keyword", dtype=DataType.VARCHAR, max_length=128),  # ← 새로 추가
    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),
]
```

**변경되지 않은 부분:**
- ✅ **Milvus 연결 방식**: `_initialize_milvus()` 함수 동일
  ```python
  milvus_host = _get_env("MILVUS_HOST", "127.0.0.1")
  milvus_port = _get_env("MILVUS_PORT", "19530")
  connections.connect("default", host=milvus_host, port=milvus_port)
  ```

- ✅ **컬렉션 이름 생성 방식**: `get_collection_name()` 함수 동일
  ```python
  collection_name = f"patents_embeddings_{collection_suffix}"
  # 예: patents_embeddings_display, patents_embeddings_battery
  ```

- ✅ **임베딩 모델**: 동일 (`all-MiniLM-L6-v2`, 384차원)
  ```python
  _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
  ```

- ✅ **인덱스 설정**: 동일 (IVF_FLAT, L2 거리)
  ```python
  index_params={
      "metric_type": "L2",
      "index_type": "IVF_FLAT",
      "params": {"nlist": 1024}
  }
  ```

- ✅ **저장 방식**: 동일 (`target_collection.insert(data)`)
  ```python
  data = [app_nos, titles, abstracts, chunks, patent_keywords, embeddings.tolist()]
  target_collection.insert(data)
  target_collection.flush()
  ```

**변경된 부분:**
- ⚠️ **필드 추가**: `patent_keyword` 필드만 추가됨 (기존 컬렉션에는 없을 수 있음)
- ⚠️ **데이터 삽입**: `patent_keywords` 리스트 추가 (기존 데이터와 호환성 문제 없음)

**결론:**
- Milvus 서버 연결, 컬렉션 이름, 임베딩 모델, 저장 방식 모두 **동일**
- `patent_keyword` 필드만 추가되어 필터링 기능 강화
- 기존 컬렉션은 그대로 사용 가능 (새 컬렉션 생성 시에만 새 필드 포함)

---

## 질문 2: 기존과 같이 데이터 임베딩 시켜 DB 저장 후 RAG (LangGraph) 방식으로 작성한 것인가?

### ✅ 답변: **네, 동일합니다. LangGraph 워크플로우는 그대로 유지되었습니다.**

**임베딩 및 저장 흐름 (동일):**
```python
# 1. PDF 다운로드
pdf_path, download_info = kipris_download_pdf(app_no, pdf_dir)

# 2. PDF → 텍스트 추출
body = extract_pdf_text(pdf_path)

# 3. 텍스트 파일 저장
text_path = save_text_file(text_dir, app_no, title, abstract, body)

# 4. 임베딩 생성 및 벡터DB 저장
chunks = _splitter.split_text(text)  # 텍스트 분할
embeddings = _embedding_model.encode(chunks)  # 임베딩 생성
target_collection.insert(data)  # 벡터DB 저장
```

**LangGraph 워크플로우 (동일):**
```python
workflow = StateGraph(PatentSearchState)

# 노드 구성 (기존과 동일)
workflow.add_node("check_db", check_db_node)        # 벡터DB 확인
workflow.add_node("search_kipris", search_kipris_node)  # KIPRIS 검색
workflow.add_node("download_pdf", download_pdf_node)    # PDF 다운로드
workflow.add_node("rag_answer", rag_answer_node)        # RAG 답변 생성

# 엣지 구성 (기존과 동일)
workflow.set_entry_point("check_db")
workflow.add_conditional_edges("check_db", _should_continue, {...})
workflow.add_edge("download_pdf", "rag_answer")
workflow.add_edge("rag_answer", END)
```

**RAG 답변 생성 (동일):**
```python
def rag_answer_node(state: PatentSearchState) -> PatentSearchState:
    # 벡터DB 결과 또는 PDF 내용 사용
    if state.get("vector_db_results"):
        context_text = ...  # 벡터DB 결과 사용
    elif state.get("pdf_content"):
        context_text = ...  # PDF 내용 사용
    
    # LLM으로 답변 생성
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    response = llm.invoke(messages)
    state["answer"] = response.content
```

**추가된 기능 (기존 로직 유지):**
- ✅ 골든셋 비교 기능 추가 (기존 RAG 로직은 그대로)
- ✅ 진행 상황 표시 추가 (기존 워크플로우는 그대로)
- ✅ 검색 모드 선택 기능 추가 (기존 워크플로우는 그대로)

**결론:**
- ✅ 임베딩 생성 및 저장 방식 **동일**
- ✅ LangGraph 워크플로우 **동일**
- ✅ RAG 답변 생성 방식 **동일**
- ✅ 추가 기능은 기존 로직에 **부가적으로 추가**됨

---

## 질문 3: 기존 특허를 가져오면 data에 meta + pdf + text 형식으로 저장되는 것이 그대로이고, PDF 주소도 바로 연결해주는 로직이 같은가?

### ✅ 답변: **네, 완전히 동일합니다. 변경 사항 없습니다.**

**파일 저장 구조 (동일):**
```python
base_dir = Path(__file__).resolve().parent.parent
data_root = base_dir / "data"
pdf_dir = data_root / "pdf"      # PDF 파일 저장
text_dir = data_root / "text"    # 텍스트 파일 저장
meta_dir = data_root / "meta"    # 메타데이터 JSON 저장
```

**저장 흐름 (동일):**
```python
# 1. PDF 다운로드 및 저장
pdf_path, download_info = kipris_download_pdf(app_no, pdf_dir)
# → data/pdf/{application_number}.pdf

# 2. PDF → 텍스트 추출 및 저장
body = extract_pdf_text(pdf_path)
text_path = save_text_file(text_dir, app_no, title, abstract, body)
# → data/text/{application_number}.txt

# 3. 메타데이터 저장
metadata = {
    "application_number": app_no,
    "title": title,
    "abstract": abstract,
    "keyword": state.get("keyword", ""),
    "pdf": {
        "saved": True,
        "path": str(pdf_path),
        "doc_name": download_info.get("doc_name", ""),
        "download_url": download_info.get("download_url", ""),  # ← PDF 원본 링크
    },
    "text": {
        "extraction": "completed",
        "path": str(text_path),
        "char_count": len(body),
    },
}
save_metadata(meta_dir, metadata)
# → data/meta/{application_number}.json
```

**PDF 링크 반환 (동일):**
```python
# download_pdf_node()에서
state["pdf_link"] = download_info.get("download_url", "")
# → KIPRIS 원본 PDF 다운로드 URL

# patent_search() 반환값에 포함
return {
    "ok": True,
    "message": result["answer"],
    "patent": selected_patent,
    "pdf_link": result.get("pdf_link", ""),  # ← PDF 링크 반환
}
```

**저장된 파일 구조 (동일):**
```
data/
├── pdf/
│   └── {application_number}.pdf          # PDF 파일
├── text/
│   └── {application_number}.txt         # 텍스트 파일
└── meta/
    └── {application_number}.json        # 메타데이터 (PDF 링크 포함)
```

**메타데이터 JSON 구조 (동일):**
```json
{
  "application_number": "1020230001234",
  "title": "배터리 충전 장치",
  "abstract": "...",
  "keyword": "배터리",
  "pdf": {
    "saved": true,
    "path": "data/pdf/1020230001234.pdf",
    "doc_name": "1020230001234.pdf",
    "download_url": "http://plus.kipris.or.kr/.../1020230001234.pdf"  // ← 원본 링크
  },
  "text": {
    "extraction": "completed",
    "path": "data/text/1020230001234.txt",
    "char_count": 12345
  }
}
```

**결론:**
- ✅ **파일 저장 구조**: `data/pdf/`, `data/text/`, `data/meta/` **동일**
- ✅ **저장 방식**: PDF, 텍스트, 메타데이터 저장 로직 **동일**
- ✅ **PDF 링크**: `download_url` 필드에 원본 링크 저장 및 반환 **동일**
- ✅ **반환값**: `pdf_link` 필드로 PDF 링크 제공 **동일**

---

## 종합 요약

| 항목 | 변경 여부 | 설명 |
|------|----------|------|
| **Milvus 연결** | ❌ 변경 없음 | 동일한 서버, 동일한 연결 방식 |
| **컬렉션 이름** | ❌ 변경 없음 | 키워드 기반 동일한 방식 |
| **임베딩 모델** | ❌ 변경 없음 | all-MiniLM-L6-v2, 384차원 |
| **저장 방식** | ⚠️ 필드 추가 | `patent_keyword` 필드만 추가 |
| **LangGraph 워크플로우** | ❌ 변경 없음 | 동일한 노드 및 엣지 구성 |
| **RAG 답변 생성** | ⚠️ 기능 추가 | 골든셋 비교 기능 추가 (기존 로직 유지) |
| **파일 저장 구조** | ❌ 변경 없음 | data/pdf/, data/text/, data/meta/ |
| **PDF 링크 반환** | ❌ 변경 없음 | download_url 필드 동일 |

**결론:**
- 모든 기존 기능은 **그대로 유지**됨
- 추가된 기능은 **부가적으로 동작**하며 기존 로직에 영향 없음
- 하위 호환성 **완벽하게 유지**됨



