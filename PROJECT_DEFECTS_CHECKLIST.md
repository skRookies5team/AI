# 프로젝트 결함 리스트 및 개선 체크리스트

> **목적**: 프로젝트의 잠재적 결함과 개선 사항을 체계적으로 관리하고, 개발자가 하나씩 체크하면서 수정할 수 있도록 합니다.
> 
> **업데이트 날짜**: 2025-01-XX
> **총 결함 수**: 약 100+ 항목

---

## 📋 체크리스트 사용법

- [ ] 체크박스를 사용하여 완료된 항목을 표시하세요
- 각 항목을 수정한 후 `[ ]`를 `[x]`로 변경하세요
- 우선순위가 높은 항목부터 처리하세요
- 수정 완료 시 커밋 메시지에 `[FIX]` 태그를 사용하세요

---

## 🔴 Critical (치명적 결함 - 즉시 수정 필요)

### 1. 예외 처리 (Exception Handling)

#### 1.1 Bare Except 절 사용 (6개 발견)
**위험도**: 🔴 Critical  
**설명**: `except:` 구문은 모든 예외를 잡아버려 디버깅을 어렵게 만들고, KeyboardInterrupt나 SystemExit까지 잡아버릴 수 있습니다.

- [ ] `agents/master_graphs/tech/tech_article_agent.py:565` - bare except 제거
- [ ] `agents/master_graphs/tech/tech_article_agent.py:750` - bare except 제거
- [ ] `agents/master_graphs/tech/article_agent_module/vector_db.py:945` - bare except 제거
- [ ] `agents/master_graphs/document/generate/html_filler.py:984` - bare except 제거
- [ ] `agents/master_graphs/document/generate/html_filler.py:986` - bare except 제거
- [ ] `agents/master_graphs/email/email_compose_agent.py:398` - bare except 제거

**수정 예시**:
```python
# ❌ 나쁜 예
try:
    # 코드
except:
    pass

# ✅ 좋은 예
try:
    # 코드
except SpecificException as e:
    logger.error(f"구체적인 오류 발생: {e}", exc_info=True)
    # 적절한 처리
```

#### 1.2 과도하게 광범위한 Exception 처리 (89개 발견)
**위험도**: 🟡 High  
**설명**: `except Exception:`은 구체적인 예외 타입을 사용하는 것이 좋습니다. 가능하면 구체적인 예외 타입을 사용하세요.

**주요 파일**:
- [ ] `agents/master_graphs/email/email_search_agent.py` - 30+ 개 Exception 처리 구체화
- [ ] `agents/master_graphs/tech/patent/patent_config.py` - 6개 Exception 처리 구체화
- [ ] `agents/master_graphs/tech/article_agent_module/vector_db.py` - 7개 Exception 처리 구체화
- [ ] `agents/llm_router.py` - 2개 Exception 처리 구체화
- [ ] 기타 파일들 - Exception 처리 구체화

**수정 예시**:
```python
# ❌ 나쁜 예
try:
    result = api_call()
except Exception:
    return {}

# ✅ 좋은 예
try:
    result = api_call()
except (ConnectionError, TimeoutError) as e:
    logger.error(f"API 연결 오류: {e}")
    return {}
except ValueError as e:
    logger.error(f"값 오류: {e}")
    return {}
```

---

### 2. 로깅 (Logging)

#### 2.1 print 문 사용 (로깅 대신)
**위험도**: 🟡 High  
**설명**: 프로덕션 환경에서는 `print` 대신 `logging` 모듈을 사용해야 합니다. 로그 레벨 관리와 파일 출력이 가능합니다.

**주요 파일**:
- [ ] `agents/master_graphs/document/generate/doc_generate_agent.py` - print → logger로 변경
- [ ] `agents/master_graphs/document/generate/html_filler.py` - print → logger로 변경
- [ ] `agents/master_graphs/document/generate/llm_generator.py` - print → logger로 변경
- [ ] `agents/master_graphs/document/generate/field_extractors/*.py` - print → logger로 변경

**수정 예시**:
```python
# ❌ 나쁜 예
print(f"✅ LLM 필드 값 생성 성공: {len(field_values)}개 필드")

# ✅ 좋은 예
import logging
logger = logging.getLogger(__name__)
logger.info(f"LLM 필드 값 생성 성공: {len(field_values)}개 필드")
```

---

### 3. 타입 힌팅 (Type Hints)

#### 3.1 타입 힌팅 누락 또는 불완전
**위험도**: 🟡 High  
**설명**: 모든 함수, 메서드, 클래스 멤버에 타입 힌팅이 필요합니다. `typing` 모듈을 적극 활용하세요.

**확인 필요 파일** (588개 함수/클래스 발견):
- [ ] `agents/master_graphs/document/generate/doc_generate_agent.py` - 모든 함수 타입 힌팅 확인
- [ ] `agents/master_graphs/email/email_search_agent.py` - 모든 함수 타입 힌팅 확인
- [ ] `agents/master_graphs/schedule/nodes/*.py` - 모든 함수 타입 힌팅 확인
- [ ] `agents/master_graphs/tech/*.py` - 모든 함수 타입 힌팅 확인
- [ ] 기타 모든 모듈 - 타입 힌팅 완성도 확인

**수정 예시**:
```python
# ❌ 나쁜 예
def process_data(data, options):
    return result

# ✅ 좋은 예
from typing import Dict, List, Optional, Any

def process_data(
    data: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return result
```

---

### 4. Docstring (문서화)

#### 4.1 Google 스타일 Docstring 불완전
**위험도**: 🟡 Medium  
**설명**: 모든 함수, 메서드, 클래스에 Google 스타일 docstring이 필요합니다.

**확인 필요**:
- [ ] 모든 public 함수에 docstring 추가/보완
- [ ] Args, Returns, Raises 섹션 완성
- [ ] 사용 예시 추가 (복잡한 함수의 경우)

**수정 예시**:
```python
def generate_field_values_with_llm(
    template_structure: Dict[str, Any],
    user_requirements: Optional[str] = None,
    document_title: Optional[str] = None,
    template_name: Optional[str] = None,
    db_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """LLM을 사용하여 템플릿 필드 값을 생성합니다.
    
    Args:
        template_structure: 템플릿 구조 정보 (필드 목록 등)
        user_requirements: 사용자 요구사항
        document_title: 문서 제목
        template_name: 템플릿 이름
        db_data: DB에서 조회한 데이터
        
    Returns:
        필드 값 딕셔너리. 실패 시 빈 딕셔너리 반환.
        
    Raises:
        ValueError: 필수 파라미터가 누락된 경우
        
    Example:
        >>> template = {"inputs": [{"id": "title", "type": "input"}]}
        >>> result = generate_field_values_with_llm(template, "제목을 입력하세요")
        >>> assert isinstance(result, dict)
    """
```

---

## 🟡 High Priority (높은 우선순위)

### 5. 환경 변수 검증

#### 5.1 환경 변수 누락 시 기본값 처리 부족
**위험도**: 🟡 High  
**설명**: 환경 변수가 없을 때 적절한 기본값이나 명확한 에러 메시지가 필요합니다.

**확인 필요 파일**:
- [ ] `agents/master_graphs/document/generate/llm_generator.py` - OPENAI_API_KEY 검증 강화
- [ ] `agents/structured_output.py` - 환경 변수 검증 추가
- [ ] `agents/llm_router.py` - 환경 변수 검증 추가
- [ ] 모든 LLM 호출 모듈 - 환경 변수 검증 추가

**수정 예시**:
```python
# ❌ 나쁜 예
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("⚠️ OPENAI_API_KEY가 설정되지 않았습니다.")
    return {}

# ✅ 좋은 예
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logger.error("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
    raise ValueError(
        "OPENAI_API_KEY 환경 변수가 필요합니다. "
        ".env 파일 또는 환경 변수를 확인하세요."
    )
```

---

### 6. 에러 메시지 및 사용자 피드백

#### 6.1 에러 메시지가 사용자 친화적이지 않음
**위험도**: 🟡 Medium  
**설명**: 에러 메시지가 기술적이거나 불명확한 경우가 있습니다.

**확인 필요**:
- [ ] 모든 사용자에게 노출되는 에러 메시지 검토
- [ ] 기술적 에러를 사용자 친화적 메시지로 변환
- [ ] 에러 발생 시 해결 방법 제시

---

### 7. 비동기 처리

#### 7.1 동기 함수를 비동기로 변환 필요
**위험도**: 🟡 Medium  
**설명**: I/O 바운드 작업은 비동기로 처리하는 것이 성능상 유리합니다.

**확인 필요**:
- [ ] DB 쿼리 함수들이 비동기인지 확인
- [ ] 외부 API 호출이 비동기인지 확인
- [ ] 파일 I/O 작업이 비동기인지 확인

---

### 8. 리소스 관리

#### 8.1 리소스 해제 누락 가능성
**위험도**: 🟡 Medium  
**설명**: 파일, DB 연결, 네트워크 연결 등의 리소스가 제대로 해제되는지 확인 필요.

**확인 필요**:
- [ ] 파일 열기 후 `with` 문 사용 확인
- [ ] DB 연결이 적절히 닫히는지 확인
- [ ] 네트워크 세션이 적절히 닫히는지 확인

---

## 🟢 Medium Priority (중간 우선순위)

### 9. 테스트 커버리지

#### 9.1 단위 테스트 부족
**위험도**: 🟢 Medium  
**설명**: 프로젝트에 테스트 파일이 거의 없습니다. pytest를 사용한 단위 테스트가 필요합니다.

**현재 상태**:
- 테스트 파일: `scripts/test_query_preprocessing.py` (1개만 존재)
- 목표: 90% 이상 테스트 커버리지

**작성 필요**:
- [ ] `agents/master_graphs/document/generate/` 모듈 테스트
- [ ] `agents/master_graphs/email/` 모듈 테스트
- [ ] `agents/master_graphs/schedule/` 모듈 테스트
- [ ] `agents/master_graphs/tech/` 모듈 테스트
- [ ] `agents/llm_router.py` 테스트
- [ ] `agents/orchestrator_v3.py` 테스트
- [ ] 기타 핵심 모듈 테스트

**테스트 예시**:
```python
import pytest
from agents.master_graphs.document.generate.llm_generator import (
    generate_field_values_with_llm
)

def test_generate_field_values_with_llm_success():
    """LLM 필드 값 생성 성공 케이스 테스트."""
    template_structure = {
        "inputs": [
            {"id": "title", "type": "input", "label": "제목"}
        ]
    }
    result = generate_field_values_with_llm(
        template_structure,
        user_requirements="테스트 제목",
        document_title="테스트 문서"
    )
    assert isinstance(result, dict)

def test_generate_field_values_with_llm_no_api_key():
    """API 키가 없을 때 테스트."""
    import os
    original_key = os.environ.get("OPENAI_API_KEY")
    try:
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
        result = generate_field_values_with_llm({})
        assert result == {}
    finally:
        if original_key:
            os.environ["OPENAI_API_KEY"] = original_key
```

---

### 10. 코드 중복

#### 10.1 중복 코드 제거
**위험도**: 🟢 Low  
**설명**: 유사한 로직이 여러 곳에 반복되는 경우 공통 함수로 추출 필요.

**확인 필요**:
- [ ] 에러 처리 로직 중복 제거
- [ ] LLM 호출 패턴 통일
- [ ] DB 쿼리 패턴 통일
- [ ] 유틸리티 함수 통합

---

### 11. 설정 관리

#### 11.1 하드코딩된 값들
**위험도**: 🟢 Low  
**설명**: 매직 넘버나 하드코딩된 문자열을 상수나 설정 파일로 이동.

**확인 필요**:
- [ ] 매직 넘버를 상수로 추출
- [ ] 하드코딩된 문자열을 설정 파일로 이동
- [ ] 환경별 설정 분리 (dev, staging, prod)

---

### 12. 성능 최적화

#### 12.1 캐싱 전략
**위험도**: 🟢 Low  
**설명**: 반복적으로 계산되는 값이나 조회 결과를 캐싱하여 성능 개선.

**확인 필요**:
- [ ] LLM 호출 결과 캐싱 (적절한 경우)
- [ ] DB 쿼리 결과 캐싱
- [ ] 파일 읽기 결과 캐싱
- [ ] `functools.lru_cache` 활용

---

## 📊 진행 상황 추적

### 전체 진행률
- **Critical**: 0/6 완료 (0%)
- **High Priority**: 0/50+ 완료 (0%)
- **Medium Priority**: 0/30+ 완료 (0%)
- **전체**: 0/100+ 완료 (0%)

### 우선순위별 완료 현황
- [ ] Critical 항목 완료
- [ ] High Priority 항목 완료
- [ ] Medium Priority 항목 완료

---

## 📝 수정 가이드라인

### 예외 처리 수정 시
1. 구체적인 예외 타입 사용
2. 적절한 로깅 추가
3. 사용자에게 친화적인 에러 메시지 제공
4. 필요한 경우 재시도 로직 추가

### 로깅 추가 시
1. 적절한 로그 레벨 사용 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
2. 구조화된 로그 메시지 (컨텍스트 포함)
3. 민감한 정보는 로그에 포함하지 않기
4. 프로덕션 환경에서는 DEBUG 레벨 제한

### 타입 힌팅 추가 시
1. 모든 함수 파라미터에 타입 지정
2. 반환 타입 명시
3. Optional, Union 등 적절히 활용
4. Generic 타입 활용 (필요한 경우)

### 테스트 작성 시
1. 각 함수별로 성공 케이스와 실패 케이스 모두 테스트
2. Edge case 테스트 포함
3. Mock을 활용한 외부 의존성 격리
4. 테스트 커버리지 90% 이상 목표

---

## 🔄 정기 점검

이 체크리스트는 다음 주기에 따라 업데이트해야 합니다:
- **주간**: Critical 및 High Priority 항목 점검
- **월간**: 전체 항목 점검 및 새로 발견된 결함 추가
- **릴리스 전**: 전체 체크리스트 완료 확인

---

## 📌 참고 사항

- 모든 수정 사항은 코드 리뷰를 거쳐야 합니다
- 수정 전에 관련 테스트를 작성하거나 업데이트하세요
- Breaking change가 있는 경우 문서를 업데이트하세요
- 수정 완료 후 이 체크리스트를 업데이트하세요

---

**마지막 업데이트**: 2025-01-XX  
**다음 점검 예정일**: 2025-01-XX

