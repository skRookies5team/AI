# 프로젝트 실제 에러 및 버그 체크리스트

> **목적**: 실제 코드 분석을 통해 발견한 **구체적인 에러와 버그**를 정리한 체크리스트입니다.
> 
> **업데이트 날짜**: 2025-12-20
> **분석 범위**: 전체 코드베이스

---

## 🔴 Critical Bugs (치명적 버그 - 즉시 수정 필요)

### 1. 정의되지 않은 변수 사용 (Undefined Variable)

#### 1.1 `tech_article_agent.py:565` - Bare except 내부의 안전하지 않은 코드
**파일**: `agents/master_graphs/tech/tech_article_agent.py`  
**라인**: 565  
**위험도**: 🔴 Critical  
**문제**: 중첩된 bare except 블록이 있고, 내부에서 `locals()`를 사용하는 안티패턴이 있습니다.

```python
# 현재 코드 (556-569줄)
except Exception as exc:
    LOGGER.exception(f"article_master 실행 중 오류: {exc}")
    refined_keyword = query
    try:
        if 'final_state' in locals():  # ❌ 안티패턴: locals() 사용
            article_ctx = final_state.get("tech_search_context", {}).get("article", {})
            refined_keyword = article_ctx.get("keyword", query)
    except:  # ❌ Bare except
        pass
    fb = await article_search(refined_keyword)
    fb["message"] = f"{fb.get('message', '')} (agent 연동 실패로 폴백: {exc})"
    return fb
```

**수정 방법**:
```python
except Exception as exc:
    LOGGER.exception(f"article_master 실행 중 오류: {exc}")
    refined_keyword = query
    try:
        # final_state를 명시적으로 전달받도록 리팩토링 필요
        # 현재는 locals() 사용으로 인해 불안정함
        if final_state:  # ✅ 명시적인 변수 사용
            article_ctx = final_state.get("tech_search_context", {}).get("article", {})
            refined_keyword = article_ctx.get("keyword", query)
    except (KeyError, AttributeError) as e:  # ✅ 구체적인 예외 처리
        LOGGER.debug(f"키워드 추출 실패: {e}")
    fb = await article_search(refined_keyword)
    fb["message"] = f"{fb.get('message', '')} (agent 연동 실패로 폴백: {exc})"
    return fb
```

- [ ] 수정 완료

---

### 2. Bare Except 절 (6개 발견)

#### 2.1 `tech_article_agent.py:565` - bare except
**파일**: `agents/master_graphs/tech/tech_article_agent.py`  
**라인**: 565  
**위험도**: 🔴 Critical  
**문제**: 모든 예외를 잡아버려 KeyboardInterrupt, SystemExit까지 잡을 수 있습니다.

- [ ] 수정 완료

#### 2.2 `tech_article_agent.py:750` - bare except
**파일**: `agents/master_graphs/tech/tech_article_agent.py`  
**라인**: 750  
**위험도**: 🔴 Critical  

- [ ] 수정 완료

#### 2.3 `article_agent_module/vector_db.py:945` - bare except
**파일**: `agents/master_graphs/tech/article_agent_module/vector_db.py`  
**라인**: 945  
**위험도**: 🔴 Critical  

- [ ] 수정 완료

#### 2.4 `html_filler.py:984` - bare except
**파일**: `agents/master_graphs/document/generate/html_filler.py`  
**라인**: 984  
**위험도**: 🔴 Critical  
**문제**: 날짜 파싱 실패 시 모든 예외를 무시합니다.

```python
# 현재 코드
try:
    dt = datetime.strptime(date_value, fmt)
    input_reference_date['value'] = dt.strftime('%Y-%m-%d')
    break
except:  # ❌ 모든 예외 무시
    continue
```

**수정 방법**:
```python
try:
    dt = datetime.strptime(date_value, fmt)
    input_reference_date['value'] = dt.strftime('%Y-%m-%d')
    break
except (ValueError, TypeError) as e:  # ✅ 구체적인 예외만 처리
    logger.debug(f"날짜 파싱 실패 (형식: {fmt}): {e}")
    continue
```

- [ ] 수정 완료

#### 2.5 `html_filler.py:986` - bare except
**파일**: `agents/master_graphs/document/generate/html_filler.py`  
**라인**: 986  
**위험도**: 🔴 Critical  

- [ ] 수정 완료

#### 2.6 `email_compose_agent.py:398` - bare except
**파일**: `agents/master_graphs/email/email_compose_agent.py`  
**라인**: 398  
**위험도**: 🔴 Critical  
**문제**: JSON 파싱 실패 시 모든 예외를 무시합니다.

```python
# 현재 코드 (395-400줄)
try:
    result = json.loads(json_match.group(0))
    subject = result.get("subject", f"Re: {original_subject}" if is_reply else "이메일")
    body = result.get("body", content)
except:  # ❌ 모든 예외 무시
    subject = f"Re: {original_subject}" if is_reply else "이메일"
    body = content
```

**수정 방법**:
```python
try:
    result = json.loads(json_match.group(0))
    subject = result.get("subject", f"Re: {original_subject}" if is_reply else "이메일")
    body = result.get("body", content)
except (json.JSONDecodeError, AttributeError) as e:  # ✅ 구체적인 예외 처리
    logger.warning(f"JSON 파싱 실패, 기본값 사용: {e}")
    subject = f"Re: {original_subject}" if is_reply else "이메일"
    body = content
```

- [ ] 수정 완료

---

### 3. 안티패턴: globals()/locals() 사용

#### 3.1 `doc_generate_agent.py:552` - globals() 사용
**파일**: `agents/master_graphs/document/generate/doc_generate_agent.py`  
**라인**: 552  
**위험도**: 🟡 High  
**문제**: `globals()`와 `locals()`를 사용하여 변수 존재 여부를 확인하는 것은 안티패턴입니다. 명시적인 파라미터 전달이 필요합니다.

```python
# 현재 코드 (542-552줄)
if 'db_hr_data' in locals():
    local_db_hr_data = locals()['db_hr_data']
    if local_db_hr_data:
        data_to_pass = local_db_hr_data
        print(f"✅ db_hr_data (local) 사용: {len(local_db_hr_data)}개 행")
    else:
        print(f"⚠️ db_hr_data (local)가 빈 리스트입니다.")

# globals()에서도 확인
if not data_to_pass and 'db_hr_data' in globals():
    global_db_hr_data = globals()['db_hr_data']
    if global_db_hr_data:
        data_to_pass = global_db_hr_data
        print(f"✅ db_hr_data (global) 사용: {len(global_db_hr_data)}개 행")
```

**수정 방법**: 함수 파라미터로 명시적으로 전달하도록 리팩토링 필요

- [ ] 수정 완료

---

## 🟡 High Priority Issues (높은 우선순위 문제)

### 4. 과도하게 광범위한 Exception 처리 (89개 발견)

#### 4.1 `email_search_agent.py` - 30+ 개 Exception 처리
**파일**: `agents/master_graphs/email/email_search_agent.py`  
**위험도**: 🟡 High  
**문제**: `except Exception:`이 너무 많아 구체적인 에러 원인 파악이 어렵습니다.

**주요 위치**:
- 라인 50, 420, 685, 710, 766, 784, 807, 819, 822, 838, 876, 887, 895, 908, 1017, 1021, 1023, 1038, 1042, 1044, 1063, 1071, 1081, 1311, 1391, 1430, 1442, 1459, 1509, 1515, 1583, 1627, 1707, 1754, 1773, 1854, 1917, 1937, 2057

**수정 예시**:
```python
# ❌ 나쁜 예
try:
    result = imap_search()
except Exception:
    return {}

# ✅ 좋은 예
try:
    result = imap_search()
except (ConnectionError, TimeoutError) as e:
    logger.error(f"IMAP 연결 오류: {e}")
    return {}
except ValueError as e:
    logger.error(f"검색 파라미터 오류: {e}")
    return {}
```

- [ ] 수정 완료 (30+ 개)

#### 4.2 `patent_config.py` - 6개 Exception 처리
**파일**: `agents/master_graphs/tech/patent/patent_config.py`  
**위험도**: 🟡 High  

- [ ] 수정 완료 (6개)

#### 4.3 `article_agent_module/vector_db.py` - 7개 Exception 처리
**파일**: `agents/master_graphs/tech/article_agent_module/vector_db.py`  
**위험도**: 🟡 High  

- [ ] 수정 완료 (7개)

#### 4.4 `llm_router.py` - 2개 Exception 처리
**파일**: `agents/llm_router.py`  
**위험도**: 🟡 High  

- [ ] 수정 완료 (2개)

---

### 5. print 문 사용 (로깅 대신)

#### 5.1 `doc_generate_agent.py` - 다수의 print 문
**파일**: `agents/master_graphs/document/generate/doc_generate_agent.py`  
**위험도**: 🟡 High  
**문제**: 프로덕션 환경에서 로그 레벨 관리가 불가능합니다.

**주요 위치**: 52, 75, 527, 547, 556, 561, 564, 567, 569, 573, 575, 579, 585 등

**수정 예시**:
```python
# ❌ 나쁜 예
print(f"✅ LLM 필드 값 생성 성공: {len(field_values)}개 필드")

# ✅ 좋은 예
import logging
logger = logging.getLogger(__name__)
logger.info(f"LLM 필드 값 생성 성공: {len(field_values)}개 필드")
```

- [ ] 수정 완료

#### 5.2 `html_filler.py` - print 문 사용
**파일**: `agents/master_graphs/document/generate/html_filler.py`  
**위험도**: 🟡 High  

- [ ] 수정 완료

#### 5.3 `llm_generator.py` - print 문 사용
**파일**: `agents/master_graphs/document/generate/llm_generator.py`  
**위험도**: 🟡 High  

- [ ] 수정 완료

#### 5.4 `email_compose_agent.py` - print 문 사용
**파일**: `agents/master_graphs/email/email_compose_agent.py`  
**위험도**: 🟡 High  

- [ ] 수정 완료

---

### 6. 환경 변수 검증 부족

#### 6.1 `llm_generator.py` - OPENAI_API_KEY 검증 부족
**파일**: `agents/master_graphs/document/generate/llm_generator.py`  
**라인**: 42-45  
**위험도**: 🟡 High  
**문제**: 환경 변수가 없을 때 빈 딕셔너리를 반환하지만, 명확한 에러 메시지가 없습니다.

```python
# 현재 코드
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("⚠️ OPENAI_API_KEY가 설정되지 않았습니다. 빈 딕셔너리 반환")
    return {}
```

**수정 방법**:
```python
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    logger.error("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
    raise ValueError(
        "OPENAI_API_KEY 환경 변수가 필요합니다. "
        ".env 파일 또는 환경 변수를 확인하세요."
    )
```

- [ ] 수정 완료

---

## 🟢 Medium Priority Issues (중간 우선순위)

### 7. 타입 힌팅 누락

#### 7.1 전체 모듈 타입 힌팅 확인 필요
**위험도**: 🟢 Medium  
**설명**: 588개 함수/클래스 중 일부에 타입 힌팅이 누락되어 있습니다.

**주요 확인 필요 파일**:
- `agents/master_graphs/document/generate/doc_generate_agent.py`
- `agents/master_graphs/email/email_search_agent.py`
- `agents/master_graphs/schedule/nodes/*.py`
- `agents/master_graphs/tech/*.py`

- [ ] 타입 힌팅 추가 완료

---

### 8. Docstring 불완전

#### 8.1 Google 스타일 Docstring 보완 필요
**위험도**: 🟢 Medium  

- [ ] 모든 public 함수에 docstring 추가/보완

---

### 9. 테스트 커버리지 부족

#### 9.1 단위 테스트 부족
**위험도**: 🟢 Medium  
**현재 상태**: 테스트 파일 1개만 존재 (`scripts/test_query_preprocessing.py`)  
**목표**: 90% 이상 테스트 커버리지

**작성 필요**:
- [ ] `agents/master_graphs/document/generate/` 모듈 테스트
- [ ] `agents/master_graphs/email/` 모듈 테스트
- [ ] `agents/master_graphs/schedule/` 모듈 테스트
- [ ] `agents/master_graphs/tech/` 모듈 테스트
- [ ] `agents/llm_router.py` 테스트
- [ ] `agents/orchestrator_v3.py` 테스트

---

## 📊 진행 상황 추적

### Critical Bugs
- **총 7개**: 0개 완료 (0%)
  - 정의되지 않은 변수 사용: 1개
  - Bare except 절: 6개

### High Priority Issues
- **총 50+개**: 0개 완료 (0%)
  - Exception 처리 구체화: 45+개
  - print → logger 변경: 20+개
  - 환경 변수 검증: 3개

### Medium Priority Issues
- **총 30+개**: 0개 완료 (0%)
  - 타입 힌팅: 588개 함수 확인 필요
  - Docstring: 전체 확인 필요
  - 테스트: 6개 모듈 테스트 작성 필요

---

## 📝 수정 우선순위

1. **즉시 수정** (Critical):
   - `tech_article_agent.py:568` - 정의되지 않은 `exc` 변수 사용
   - 모든 bare except 절 (6개)

2. **높은 우선순위** (High):
   - `email_search_agent.py`의 Exception 처리 구체화
   - print 문을 logger로 변경
   - 환경 변수 검증 강화

3. **중간 우선순위** (Medium):
   - 타입 힌팅 추가
   - Docstring 보완
   - 테스트 작성

---

**마지막 업데이트**: 2025-12-20  
**다음 점검 예정일**: 2025-12-27

