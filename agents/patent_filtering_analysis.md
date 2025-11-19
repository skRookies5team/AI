# 특허 에이전트 필터링 메커니즘 분석

## 🔍 문제 상황

특허 에이전트(`tech_patent_agent.py`)를 실행할 때, 논문과 내용이 겹치는 경우 필터링이 되는 것처럼 보이는 현상.

---

## 📊 벡터DB 구조 분석

### 1. 컬렉션 분리

**특허 데이터:**
```python
# tech_patent_agent.py - get_collection_name()
collection_name = f"patents_embeddings_{collection_suffix}"
# 예: patents_embeddings_display, patents_embeddings_battery
```

**논문 데이터:**
```python
# tech_master.py - _maybe_store_pdf_to_milvus()
collection_name = os.getenv("MILVUS_COLLECTION", "scienceon_papers")
# 하나의 통합 컬렉션
```

**핵심 차이점:**
- ✅ **특허**: 키워드별로 분리된 컬렉션 (`patents_embeddings_*`)
- ✅ **논문**: 하나의 통합 컬렉션 (`scienceon_papers`)
- ❌ **서로 다른 컬렉션에 저장** → 검색 시 격리됨

---

## 🔎 필터링이 일어나는 위치

### 1. `check_vector_db()` 함수 (라인 550-612)

```python
def check_vector_db(query: str, limit: int = 5, max_distance: float = 0.8) -> List[Dict[str, Any]]:
    """벡터DB에서 유사 특허 검색."""
    
    # 1. 키워드 기반 컬렉션 선택
    keyword = extract_keyword_from_text(query)
    collection_name = get_collection_name(keyword)  # "patents_embeddings_*"만 검색
    
    # 2. 특허 컬렉션에서만 검색
    target_collection = Collection(collection_name)
    results = target_collection.search(...)
    
    # 3. 거리 기반 필터링
    for hit in results[0]:
        # 키워드 매칭 확인
        full_match = (query_lower in title or query_lower in abstract or query_lower in content)
        word_match = any(word in title or word in abstract or word in content for word in query_words)
        keyword_match = full_match or word_match
        
        # 거리 임계값 조정
        effective_max_distance = 1.2 if keyword_match else max_distance  # 0.8
        
        # 거리 필터링 (이 부분에서 제외됨)
        if hit.distance > effective_max_distance:
            continue  # ← 여기서 필터링!
```

**필터링 조건:**
- `max_distance = 0.8` (기본값)
- 키워드 매칭이 있으면 `effective_max_distance = 1.2`
- 키워드 매칭이 없으면 `effective_max_distance = 0.8`
- **거리가 이 값보다 크면 제외됨**

---

### 2. `check_db_node()` 함수 (라인 667-697)

```python
def check_db_node(state: PatentSearchState) -> PatentSearchState:
    """벡터DB에 정보가 있는지 확인."""
    query = state["user_query"]
    results = check_vector_db(query, limit=5, max_distance=0.8)
    
    if results and len(results) > 0:
        best_score = results[0]["score"]
        query_lower = query.lower()
        query_words = re.findall(r'[\w가-힣]+', query_lower)
        
        # 관련성 필터링
        relevant_results = []
        for patent in results:
            title = patent.get('title', '').lower()
            abstract = patent.get('abstract', '').lower()
            content = patent.get('content', '').lower()
            
            full_match = query_lower in title or query_lower in abstract or query_lower in content
            word_match = any(word in title or word in abstract or word in content for word in query_words if len(word) > 1)
            
            if full_match or word_match:
                relevant_results.append(patent)  # ← 관련성 필터링
        
        # 최종 결정
        if best_score <= 0.6 or (best_score <= 1.2 and relevant_results):
            state["vector_db_results"] = relevant_results[:5] if relevant_results else results[:5]
            state["next_action"] = "rag_answer"
        else:
            state["next_action"] = "search_kipris"  # ← 벡터DB 결과가 부족하면 KIPRIS 검색
```

**필터링 단계:**
1. **거리 필터링**: `check_vector_db()`에서 `max_distance` 기준으로 제외
2. **관련성 필터링**: 키워드 매칭이 있는 결과만 선택
3. **점수 기반 결정**: `best_score <= 0.6` 또는 `best_score <= 1.2 and relevant_results`가 아니면 KIPRIS 검색으로 전환

---

## 🎯 필터링이 일어나는 이유

### 1. **컬렉션 격리**

```python
# 특허 에이전트는 특허 컬렉션만 검색
collection_name = get_collection_name(keyword)  # "patents_embeddings_*"
target_collection = Collection(collection_name)  # 특허 컬렉션만
```

**문제:**
- 논문은 `scienceon_papers` 컬렉션에 저장
- 특허는 `patents_embeddings_*` 컬렉션에 저장
- **특허 에이전트는 특허 컬렉션만 검색** → 논문은 검색되지 않음

### 2. **거리 임계값이 엄격함**

```python
max_distance = 0.8  # 기본값
effective_max_distance = 1.2 if keyword_match else 0.8
```

**문제:**
- 거리(distance)가 0.8보다 크면 제외
- 유사도가 낮은 특허는 필터링됨
- 논문과 겹치는 내용이라도 거리가 멀면 제외될 수 있음

### 3. **관련성 필터링**

```python
# 키워드 매칭이 있는 결과만 선택
if full_match or word_match:
    relevant_results.append(patent)
```

**문제:**
- 제목/초록/본문에 쿼리 키워드가 없으면 제외
- 의미적으로 유사하지만 키워드가 다르면 필터링됨

---

## 📈 필터링 흐름도

```
사용자 쿼리: "배터리 관련 특허"
    ↓
check_vector_db("배터리 관련 특허")
    ↓
1. 키워드 추출: "배터리"
2. 컬렉션 선택: "patents_embeddings_battery"
3. 벡터 검색 (limit=25개)
    ↓
4. 거리 필터링
   - distance > 0.8 (또는 1.2) → 제외
   - 남은 결과: 10개
    ↓
5. 관련성 필터링
   - 키워드 매칭 확인
   - 관련 결과: 5개
    ↓
6. 최종 반환: 5개
```

**논문과 겹치는 경우:**
- 논문은 `scienceon_papers` 컬렉션에 있음
- 특허 에이전트는 `patents_embeddings_*`만 검색
- **논문은 검색 대상에서 제외됨** (컬렉션 격리)

---

## 🔍 실제 필터링 예시

### 시나리오: "배터리 충전 기술"

**특허 컬렉션 (`patents_embeddings_battery`):**
- 특허 A: "리튬 이온 배터리 충전 방법" (distance: 0.5) ✅
- 특허 B: "배터리 관리 시스템" (distance: 0.7) ✅
- 특허 C: "전기차 충전 기술" (distance: 0.9) ❌ **필터링** (distance > 0.8)
- 특허 D: "배터리 수명 연장" (distance: 0.6, 키워드 없음) ❌ **필터링** (관련성 없음)

**논문 컬렉션 (`scienceon_papers`):**
- 논문 A: "배터리 충전 알고리즘 연구" (검색 안 됨) ❌ **컬렉션 격리**

**결과:**
- 특허 A, B만 반환됨
- 특허 C는 거리 필터링으로 제외
- 특허 D는 관련성 필터링으로 제외
- 논문 A는 컬렉션 격리로 검색되지 않음

---

## 💡 필터링 메커니즘 요약

### 필터링이 일어나는 3가지 지점:

1. **컬렉션 격리** (가장 큰 원인)
   - 특허: `patents_embeddings_*` 컬렉션만 검색
   - 논문: `scienceon_papers` 컬렉션은 검색 안 됨
   - **논문과 특허가 겹치는 내용이어도 검색되지 않음**

2. **거리 필터링**
   - `max_distance = 0.8` (기본값)
   - 키워드 매칭 없으면 `effective_max_distance = 0.8`
   - **거리가 0.8보다 크면 제외**

3. **관련성 필터링**
   - 키워드 매칭이 있는 결과만 선택
   - **의미적으로 유사하지만 키워드가 다르면 제외**

---

## 🎯 결론

**특허 에이전트에서 논문과 겹치는 내용이 필터링되는 이유:**

1. ✅ **컬렉션 격리**: 특허는 `patents_embeddings_*`, 논문은 `scienceon_papers` → 서로 다른 컬렉션
2. ✅ **거리 임계값**: `max_distance = 0.8`로 엄격한 필터링
3. ✅ **관련성 필터링**: 키워드 매칭이 없으면 제외

**특히 컬렉션 격리가 가장 큰 원인입니다.**
- 특허 에이전트는 특허 컬렉션만 검색
- 논문은 논문 컬렉션에 있으므로 검색되지 않음
- 같은 주제라도 컬렉션이 다르면 격리됨

---

## 📝 참고사항

이 필터링은 **의도된 동작**일 수 있습니다:
- 특허 에이전트는 특허만 검색해야 함
- 논문은 논문 에이전트가 담당
- 각 에이전트가 자신의 도메인에 집중

하지만 **통합 검색**이 필요하다면:
- 여러 컬렉션을 동시에 검색
- 결과를 통합하여 반환
- 거리 임계값 조정

현재는 **도메인 분리**가 명확하게 되어 있습니다.



