# main_agents.py 코드 분석

## 📋 파일 구조

### 1. 주요 함수

#### `format_result(result) -> str`
- **역할**: 에이전트 결과를 채팅에 표시하기 좋은 형식으로 포맷팅
- **입력**: `dict` 형태의 에이전트 결과
- **출력**: 포맷팅된 문자열

**처리하는 결과 타입:**
1. **에러 메시지**: `ok: False, error: "..."` → 에러 메시지 포맷팅
2. **일반 메시지**: `ok: True, message: "...", items: [...]` → 메시지 + 아이템 리스트
3. **특허 검색 결과**: `items`에 `application_number`가 있으면 특허 형식으로 표시
4. **이메일 검색 결과**: `items`에 `subject` 또는 `title`이 있으면 이메일 형식으로 표시
5. **연차 현황**: `balance` 필드가 있으면 연차 정보 표시
6. **연차 신청**: `request` 필드가 있으면 신청 정보 표시

**특허 검색 결과 처리 (라인 36-51):**
```python
if items and "index" in items[0] and "application_number" in items[0]:
    # 특허 검색 결과 형식
    for it in items:
        idx = it.get("index", "")
        app_no = it.get("application_number", "")
        title = it.get("title", "제목 없음")
        abstract = it.get("abstract", "")
        lines.append(f"{idx}. [{app_no}] {title}")
        if abstract:
            abstract_preview = abstract[:150] + "..." if len(abstract) > 150 else abstract
            lines.append(f"   📄 {abstract_preview}")
    
    lines.append("💡 자세히 보려면 번호를 입력하세요. 예: '1번' 또는 '1번 상세'")
```

#### `chat_loop() -> None`
- **역할**: 대화 루프 실행 (메모리 기능을 통해 멀티턴 대화 지원)
- **흐름**:
  1. 세션 ID 생성
  2. 사용자 입력 받기
  3. `run_supervisor()` 호출
  4. `format_result()`로 결과 포맷팅
  5. 결과 출력

---

## 🔗 호출 체인

```
main_agents.py
    ↓
chat_loop()
    ↓
run_supervisor(user, session_id)  [supervisor.py]
    ↓
tech_master(user_text, session_id)  [tech_master.py]
    ↓
patent_search(keyword, session_id)  [tech_patent_agent.py]
    ↓
반환값: {
    "ok": True/False,
    "message": "...",
    "items": [...],
    "progress_status": "..."  # ← 이미 포함되어 있음
}
    ↓
format_result(result)  [main_agents.py]
    ↓
포맷팅된 문자열 출력
```

---

## 📊 현재 진행 상황 표시 방식

### 현재 상태
- ✅ `tech_patent_agent.py`에서 `progress_status` 필드를 반환값에 포함
- ❌ `main_agents.py`의 `format_result()`에서 `progress_status`를 표시하지 않음

### 진행 상황 메시지가 표시되는 위치
1. **실시간 표시 (콜백)**: `_notify_progress()` → 콜백 함수 호출
   - 현재: 콜백이 설정되지 않아 표시 안 됨
   - 해결: `main_agents.py`에서 `set_progress_callback()` 호출 필요

2. **최종 결과에 포함**: `result["progress_status"]` → `format_result()`에서 표시
   - 현재: `format_result()`가 `progress_status`를 처리하지 않음
   - 해결: `format_result()`에 `progress_status` 표시 로직 추가

---

## 💡 진행 상황 표시 방법

### 방법 1: `tech_patent_agent.py`만 수정 (반환값 활용)
- `patent_search()` 반환값에 이미 `progress_status` 포함됨
- `main_agents.py`의 `format_result()`에서 `progress_status` 표시
- **장점**: `tech_patent_agent.py`만 수정
- **단점**: 실시간 표시 안 됨 (최종 결과에만 표시)

### 방법 2: `main_agents.py`에 콜백 추가 (실시간 표시)
- `main_agents.py`에서 `set_progress_callback()` 호출
- 실시간으로 진행 상황 표시
- **장점**: 실시간 표시 가능
- **단점**: `main_agents.py` 수정 필요

---

## 🎯 권장 방법

**하이브리드 접근:**
1. `tech_patent_agent.py`: 이미 `progress_status` 반환 (완료)
2. `main_agents.py`: `format_result()`에서 `progress_status` 표시 추가
3. `main_agents.py`: `chat_loop()`에서 콜백 설정 (선택사항, 실시간 표시)

**하지만 사용자가 `tech_patent_agent.py`만 수정하라고 했으므로:**
- 방법 1만 사용: `format_result()`에서 `progress_status` 표시
- 또는 사용자에게 `main_agents.py` 수정 허용 여부 확인

---

## 📝 `format_result()` 수정 예시

```python
def format_result(result) -> str:
    """에이전트 결과를 채팅에 표시하기 좋은 형식으로 포맷팅."""
    if isinstance(result, dict):
        # 진행 상황 표시 (있는 경우)
        progress_status = result.get("progress_status", "")
        if progress_status:
            # 진행 상황을 먼저 표시하거나 메시지에 포함
            pass
        
        # 기존 로직...
```

---

## ✅ 결론

**현재 상황:**
- `tech_patent_agent.py`는 이미 `progress_status`를 반환함
- `main_agents.py`의 `format_result()`는 `progress_status`를 표시하지 않음

**선택지:**
1. `tech_patent_agent.py`만 수정: 반환값 구조 변경 (이미 완료)
2. `main_agents.py`도 수정: `format_result()`에서 `progress_status` 표시 추가

**사용자 요청:**
- `tech_patent_agent.py`만 수정하라고 했음
- 하지만 진행 상황을 표시하려면 `main_agents.py`도 수정 필요

**제안:**
- `main_agents.py`의 `format_result()`에 `progress_status` 표시 로직 추가
- 또는 사용자에게 확인 후 진행

