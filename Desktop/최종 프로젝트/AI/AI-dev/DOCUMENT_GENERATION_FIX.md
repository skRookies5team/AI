# 문서 작성 요청 오류 수정 가이드

## 문제 분석

### 발생한 오류
- **오류 메시지**: `TimeoutError` (30초 타임아웃)
- **HTTP 상태**: 500 Internal Server Error
- **원인**: 문서 작성 요청이 `schedule` 도메인으로 잘못 라우팅됨

### 문제의 근본 원인

1. **라우팅 오류**:
   - 문서 작성 UI에서 "문서 작성해줘. 양식: ... 제목: ... 요구사항: ..." 형태로 요청
   - 요구사항에 "일정" 키워드 포함 (예: "Q1: 설계 및 프로토타입, Q2: 테스트")
   - Orchestrator가 "일정" 키워드를 먼저 감지하여 `schedule` 도메인으로 라우팅
   - `schedule` 도메인은 문서 작성을 처리할 수 없어 타임아웃 발생

2. **키워드 우선순위 문제**:
   - 기존 코드에서 키워드를 순서대로 체크
   - "일정" 키워드가 "문서" 키워드보다 먼저 매칭될 수 있음
   - 문서 작성 요청임에도 불구하고 schedule 도메인으로 잘못 라우팅

## 수정 내용

### 1. Orchestrator 도메인 분류 로직 개선

**파일**: `agents/orchestrator_v3.py`

**변경 사항**:
- 문서 작성 관련 키워드를 **최우선**으로 체크하도록 변경
- 구체적인 문서 작성 키워드 추가:
  - "문서 작성해줘", "문서 작성", "문서 생성"
  - "보고서 작성해줘", "보고서 작성"
  - "템플릿 생성", "문서 양식"
  - "양식:", "template:"
  - "제품개발 계획서", "인사정보 요약 리포트" 등 템플릿 이름

**우선순위**:
1. **문서 작성 키워드** (최우선)
2. 다른 도메인 키워드 (tech, email 등)
3. 일정(schedule) 키워드 (마지막)

## 해결 방법

### 즉시 적용 가능한 해결책

수정이 완료되었으므로, FastAPI 서버를 재시작하면 됩니다:

```bash
# 서버 재시작
# (현재 실행 중인 서버를 중지하고 다시 시작)
uvicorn fastapi_app:app --reload --host 0.0.0.0 --port 8000
```

### 테스트 방법

1. **문서 작성 UI 접속**:
   ```
   http://localhost:8000/document-writer
   ```

2. **문서 작성 요청 테스트**:
   - 문서 양식 선택: "제품개발 계획서"
   - 문서 제목: "2024년 스마트워치 신제품 개발 계획서"
   - 요구사항에 "일정" 키워드 포함 (예: "Q1: 설계 및 프로토타입")
   - "AI 문서 작성 시작" 버튼 클릭

3. **예상 결과**:
   - ✅ `document` 도메인으로 정확히 라우팅됨
   - ✅ 문서 생성이 정상적으로 진행됨
   - ✅ 타임아웃 오류가 발생하지 않음

## 추가 개선 사항

### 1. 문서 작성 전용 엔드포인트 고려

현재는 `/api/chat` 엔드포인트를 사용하고 있지만, 문서 작성 전용 엔드포인트를 만드는 것도 고려할 수 있습니다:

```python
@app.post("/api/document/generate")
async def generate_document_endpoint(payload: DocumentGenerateRequest):
    """문서 작성 전용 엔드포인트."""
    # document 도메인으로 직접 라우팅
    ...
```

### 2. 쿼리 구성 개선

UI에서 보내는 쿼리를 더 명확하게 구성:

**현재**:
```javascript
let query = `문서 작성해줘. 양식: ${template}, 제목: ${title}. 요구사항: ${requirements}`;
```

**개선안**:
```javascript
let query = `[문서 작성 요청] 양식: ${template}, 제목: ${title}. 요구사항: ${requirements}`;
```

### 3. 타임아웃 시간 조정

문서 생성은 시간이 오래 걸릴 수 있으므로, 타임아웃 시간을 조정할 수 있습니다:

**파일**: `agents/master_graphs/schedule/schedule_langgraph.py`
```python
result = future.result(timeout=60)  # 30초 → 60초로 증가
```

단, 이는 schedule 도메인에만 적용되므로, document 도메인은 별도로 확인해야 합니다.

## 확인 사항

### 1. Document 도메인 동작 확인

문서 작성 요청이 `document` 도메인으로 라우팅되는지 확인:

**터미널 로그 확인**:
```
✅ 명시적 도메인 키워드 '문서 작성' 감지: document로 라우팅
--- [Orchestrator] Routing to document master graph ---
```

### 2. Document Master Graph 확인

문서 작성 요청이 document master graph에서 정상적으로 처리되는지 확인:

- Document master graph가 정상적으로 등록되어 있는지
- Document generation agent가 정상적으로 동작하는지
- 타임아웃이나 다른 오류가 없는지

## 관련 파일

- `agents/orchestrator_v3.py` - 도메인 라우팅 로직
- `templates/document_writer.html` - 문서 작성 UI
- `fastapi_app.py` - API 엔드포인트
- `agents/master_graphs/document/` - 문서 생성 에이전트

## 향후 개선 방향

1. **문서 작성 전용 엔드포인트** 구현
2. **요청 타입 자동 감지** 개선 (키워드 기반 → LLM 기반)
3. **오류 처리** 강화 (타임아웃, 도메인 불일치 등)
4. **로깅** 개선 (라우팅 결정 과정 상세 기록)




