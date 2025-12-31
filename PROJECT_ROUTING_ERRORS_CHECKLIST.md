# 프로젝트 라우팅 오류 가능성 체크리스트

> **목적**: 라우팅 로직 분석을 통해 발견한 **잘못된 라우팅 가능성**을 정리한 체크리스트입니다.
> 
> **업데이트 날짜**: 2025-12-20
> **분석 범위**: `agents/orchestrator_v3.py`, `agents/llm_router.py`

---

## 🔴 Critical Routing Issues (치명적 라우팅 오류 가능성)

### 1. 숫자 선택 패턴 라우팅 오류

#### 1.1 Redis last_domain 조회 실패 시 잘못된 라우팅
**파일**: `agents/orchestrator_v3.py`  
**라인**: 773-805  
**위험도**: 🔴 Critical  
**문제**: "1번", "2번" 같은 숫자 선택 요청이 Redis에서 `last_domain`을 조회하는데 실패하면, LLM으로 분류를 계속 진행하지만 이전 컨텍스트를 잃어버릴 수 있습니다.

**시나리오**:
```
1. 사용자: "특허 검색해줘" → tech 도메인으로 라우팅, 결과 5개 반환
2. 사용자: "1번" → Redis 조회 실패 → LLM 분류 → 잘못된 도메인으로 라우팅 가능
```

**수정 방법**:
```python
# 현재 코드 (804줄)
except Exception as e:
    print(f"⚠️ Redis에서 last_domain 조회 실패: {e}, LLM으로 분류 계속 진행")

# ✅ 개선안
except Exception as e:
    logger.error(f"Redis에서 last_domain 조회 실패: {e}")
    # 대화 히스토리에서 마지막 도메인 추출 시도
    if conversation_history:
        # 마지막 assistant 메시지에서 도메인 추출 시도
        # 또는 state의 last_domain 사용
        if last_domain and last_domain in MASTER_GRAPHS:
            logger.warning(f"Redis 실패, state의 last_domain 사용: {last_domain}")
            return {"domain": last_domain, ...}
    # 그래도 실패하면 LLM으로 분류하되, 컨텍스트 정보 포함
```

- [ ] 수정 완료

#### 1.2 숫자 선택 패턴이 너무 넓게 매칭됨
**파일**: `agents/orchestrator_v3.py`  
**라인**: 773-774  
**위험도**: 🟡 High  
**문제**: `query.strip().isdigit() and len(query.strip()) <= 2` 조건이 "10", "11" 같은 숫자도 매칭합니다. 하지만 실제로는 "10번"이 아닌 단순 숫자일 수도 있습니다.

**예시**:
- "10" → 숫자 선택으로 인식 → 잘못된 라우팅 가능
- "2024년" → 숫자로 시작하지만 선택이 아님

**수정 방법**:
```python
# 현재 코드
is_number_selection = number_selection_pattern.match(query.strip()) or (query.strip().isdigit() and len(query.strip()) <= 2)

# ✅ 개선안
# "번"이 포함된 경우만 숫자 선택으로 인식
is_number_selection = bool(number_selection_pattern.match(query.strip()))
# 또는 더 엄격하게: "1번", "2번" 같은 형식만
```

- [ ] 수정 완료

---

### 2. 멀티턴 대화 컨텍스트 오인식

#### 2.1 이전 도메인 정보가 잘못된 경우
**파일**: `agents/orchestrator_v3.py`  
**라인**: 653-675  
**위험도**: 🟡 High  
**문제**: `last_domain`이 잘못 저장되었거나, 사용자가 명시적으로 다른 도메인을 언급했는데도 이전 도메인으로 라우팅될 수 있습니다.

**시나리오**:
```
1. 사용자: "이메일 검색해줘" → email 도메인
2. 사용자: "문서 작성해줘" → 이전 도메인(email) 정보 때문에 혼란 가능
```

**수정 방법**:
- 명시적인 도메인 키워드가 있으면 `last_domain` 무시
- LLM 분류 시 명시적 도메인 언급을 우선 고려

- [ ] 수정 완료

#### 2.2 Human-in-the-loop 컨텍스트 오인식
**파일**: `agents/llm_router.py`  
**라인**: 294-297  
**위험도**: 🟡 High  
**문제**: "승인", "확인", "네", "예" 같은 응답이 이전 작업의 승인일 수도 있지만, 새로운 질문에 대한 긍정 응답일 수도 있습니다.

**시나리오**:
```
1. 시스템: "연차 신청하시겠습니까?"
2. 사용자: "네" → schedule로 라우팅 (올바름)
3. 시스템: "이메일 검색 결과를 보시겠습니까?"
4. 사용자: "네" → email로 라우팅되어야 하는데, 이전 컨텍스트 때문에 schedule로 갈 수 있음
```

**수정 방법**:
- 최근 대화 히스토리를 더 정확히 분석
- Human-in-the-loop 상태를 명시적으로 관리

- [ ] 수정 완료

---

### 3. 워크플로우 진행 중 라우팅 오류

#### 3.1 워크플로우 상태 복원 실패 시 오류
**파일**: `agents/orchestrator_v3.py`  
**라인**: 744-769  
**위험도**: 🔴 Critical  
**문제**: Redis에서 `workflow_in_progress` 조회가 실패하면, 워크플로우가 진행 중인데도 일반 요청으로 처리될 수 있습니다.

**시나리오**:
```
1. 출장 신청 워크플로우 진행 중 (schedule → document → email)
2. Redis 연결 실패
3. 사용자: "다음 단계 진행" → 워크플로우 상태를 잃어버림
```

**수정 방법**:
- Redis 조회 실패 시 state의 `workflow_in_progress` 확인
- 여러 단계 fallback 로직 추가

- [ ] 수정 완료

#### 3.2 날짜 키워드가 너무 넓게 매칭됨
**파일**: `agents/orchestrator_v3.py`  
**라인**: 854-855  
**위험도**: 🟡 High  
**문제**: `date_keywords = ["월", "일", "부터", "까지", "다음주", "내일", "모레", "오늘"]`에서 "월", "일" 같은 단어가 다른 맥락에서도 사용될 수 있습니다.

**예시**:
- "월간 보고서 작성해줘" → "월" 키워드 때문에 schedule로 잘못 라우팅 가능
- "일일 매출 보고서" → "일" 키워드 때문에 schedule로 잘못 라우팅 가능

**수정 방법**:
```python
# ✅ 개선안: 더 구체적인 패턴 사용
date_patterns = [
    r'\d+월\s*\d+일',  # "12월 20일"
    r'\d+일부터',      # "20일부터"
    r'다음주|내일|모레|오늘',  # 명확한 날짜 표현
]
has_date_keywords = any(re.search(pattern, query) for pattern in date_patterns)
```

- [ ] 수정 완료

---

### 4. 도메인 간 경계 케이스 오류

#### 4.1 문서 작성 요청에 일정 키워드 포함 시
**파일**: `agents/orchestrator_v3.py`, `common_prompts.yaml`  
**위험도**: 🟡 High  
**문제**: 이미 수정되었지만, 여전히 경계 케이스가 있을 수 있습니다.

**잠재적 문제 케이스**:
- "문서 작성해줘. 일정 확인도 해줘" → document로 가야 하는데 schedule로 갈 수 있음
- "일정 포함 문서 작성해줘" → document로 가야 함

**확인 필요**:
- [ ] YAML 프롬프트에 경계 케이스 예시 추가
- [ ] 테스트 케이스 작성

#### 4.2 Tech 도메인 ask_source 상태 체크 순서
**파일**: `agents/orchestrator_v3.py`  
**라인**: 431-635  
**위험도**: 🟡 Medium  
**문제**: Tech 도메인의 `ask_source` 상태를 체크하는 로직이 다른 라우팅 로직보다 먼저 실행되는데, 이게 다른 도메인 요청을 가로챌 수 있습니다.

**시나리오**:
```
1. 사용자: "이메일 검색해줘"
2. Tech ask_source 체크가 먼저 실행됨
3. 잘못된 라우팅 가능성
```

**확인 필요**:
- [ ] ask_source 체크가 적절한 조건에서만 실행되는지 확인
- [ ] 다른 도메인 키워드가 명확하면 ask_source 체크 건너뛰기

- [ ] 수정 완료

---

## 🟡 High Priority Routing Issues

### 5. LLM 기반 분류 일관성 문제

#### 5.1 LLM 응답 파싱 실패 시 fallback 부족
**파일**: `agents/llm_router.py`  
**라인**: 313-340  
**위험도**: 🟡 High  
**문제**: JSON 파싱이 실패하면 텍스트에서 직접 추출을 시도하지만, 여전히 실패할 수 있습니다.

**현재 코드**:
```python
# JSON 파싱 실패 시 텍스트에서 직접 추출 시도
if route_type == "supervisor":
    for route in ["schedule", "document", "qa", "email", "tech"]:
        if f'"route": "{route}"' in content or f'"route":"{route}"' in content:
            return route
```

**문제점**:
- LLM이 JSON 형식이 아닌 다른 형식으로 응답할 수 있음
- fallback이 없으면 "unknown" 반환 → 오류 발생 가능

**수정 방법**:
- 키워드 기반 fallback 추가
- LLM 응답 형식 검증 강화

- [ ] 수정 완료

#### 5.2 LLM 분류 신뢰도 부족
**파일**: `agents/orchestrator_v3.py`  
**위험도**: 🟡 Medium  
**문제**: LLM 분류 결과에 대한 신뢰도 점수가 없어서, 잘못된 분류를 감지하기 어렵습니다.

**개선 방안**:
- LLM 응답에 confidence score 추가
- 낮은 신뢰도일 때 사용자에게 확인 요청

- [ ] 수정 완료

---

### 6. Recruitment 컨텍스트 충돌

#### 6.1 Recruitment와 Tech 컨텍스트 동시 존재 시
**파일**: `agents/orchestrator_v3.py`  
**라인**: 807-827  
**위험도**: 🟡 Medium  
**문제**: Recruitment 컨텍스트가 있으면 무조건 recruitment로 라우팅하는데, Tech 컨텍스트도 있을 수 있습니다.

**시나리오**:
```
1. 사용자: "인재 검색해줘" → recruitment, 결과 5개
2. 사용자: "특허 검색해줘" → tech, 결과 3개
3. 사용자: "1번" → recruitment 컨텍스트가 우선되어 recruitment로 라우팅
   하지만 실제로는 tech의 "1번"을 원할 수도 있음
```

**수정 방법**:
- 최근 검색 결과의 타임스탬프 확인
- 사용자 입력에 도메인 힌트가 있으면 우선 적용

- [ ] 수정 완료

---

### 7. 세션 관리 오류

#### 7.1 employee_id가 없을 때 라우팅 실패
**파일**: `agents/orchestrator_v3.py`  
**라인**: 802-803  
**위험도**: 🟡 Medium  
**문제**: `employee_id`가 없으면 Redis 조회를 건너뛰는데, 이 경우 컨텍스트를 잃을 수 있습니다.

**수정 방법**:
- `employee_id` 없이도 세션 조회 가능하도록 개선
- 또는 세션 ID만으로도 조회 가능하도록 변경

- [ ] 수정 완료

#### 7.2 세션 만료 시 라우팅 오류
**위험도**: 🟡 Medium  
**문제**: 세션이 만료되어 Redis에서 데이터를 가져올 수 없을 때, 이전 컨텍스트를 잃어버립니다.

**수정 방법**:
- 세션 만료 시 명확한 에러 메시지
- 새 세션으로 시작하도록 안내

- [ ] 수정 완료

---

## 🟢 Medium Priority Routing Issues

### 8. 프롬프트 기반 분류 문제

#### 8.1 YAML 프롬프트 업데이트 누락
**파일**: `agents/common_prompts.yaml`  
**위험도**: 🟢 Medium  
**문제**: 프롬프트가 업데이트되지 않으면 새로운 케이스를 처리하지 못할 수 있습니다.

**확인 필요**:
- [ ] 경계 케이스 예시가 프롬프트에 포함되어 있는지
- [ ] 새로운 도메인 추가 시 프롬프트 업데이트

- [ ] 확인 완료

#### 8.2 프롬프트 캐싱 문제
**파일**: `agents/orchestrator_v3.py`  
**라인**: 98-194  
**위험도**: 🟢 Low  
**문제**: 프롬프트가 캐싱되어 있어서, 런타임에 YAML 파일을 수정해도 반영되지 않을 수 있습니다.

**수정 방법**:
- 개발 환경에서는 캐싱 비활성화
- 또는 캐시 무효화 메커니즘 추가

- [ ] 수정 완료

---

### 9. 로깅 및 디버깅 부족

#### 9.1 라우팅 결정 과정 로깅 부족
**파일**: `agents/orchestrator_v3.py`  
**위험도**: 🟢 Medium  
**문제**: 라우팅이 왜 특정 도메인으로 결정되었는지 추적하기 어렵습니다.

**개선 방안**:
- 라우팅 결정 이유 상세 로깅
- 각 단계별 신뢰도 점수 로깅

- [ ] 수정 완료

---

## 📊 테스트 시나리오

### Critical 시나리오 테스트

1. **숫자 선택 패턴 테스트**:
   - [ ] "1번" 입력 시 이전 도메인으로 정확히 라우팅되는지
   - [ ] Redis 실패 시에도 올바르게 처리되는지
   - [ ] "10", "11" 같은 숫자가 잘못 매칭되지 않는지

2. **워크플로우 라우팅 테스트**:
   - [ ] 워크플로우 진행 중 "다음 단계" 요청이 올바른 도메인으로 라우팅되는지
   - [ ] Redis 실패 시 워크플로우 상태가 유지되는지
   - [ ] 날짜 키워드가 잘못 매칭되지 않는지

3. **멀티턴 대화 테스트**:
   - [ ] 이전 도메인 정보가 올바르게 활용되는지
   - [ ] 명시적 도메인 언급 시 이전 도메인을 무시하는지
   - [ ] Human-in-the-loop 응답이 올바르게 처리되는지

4. **경계 케이스 테스트**:
   - [ ] "문서 작성해줘. 일정 확인도 해줘" → document로 라우팅
   - [ ] "문서 검토 일정 알려줘" → schedule로 라우팅
   - [ ] "월간 보고서 작성해줘" → document로 라우팅 (schedule로 잘못 가지 않음)

---

## 📝 수정 우선순위

1. **즉시 수정** (Critical):
   - Redis last_domain 조회 실패 시 fallback 개선
   - 워크플로우 상태 복원 실패 시 처리 개선
   - 숫자 선택 패턴 매칭 개선

2. **높은 우선순위** (High):
   - Human-in-the-loop 컨텍스트 오인식 개선
   - LLM 응답 파싱 실패 시 fallback 강화
   - 날짜 키워드 매칭 개선

3. **중간 우선순위** (Medium):
   - Recruitment/Tech 컨텍스트 충돌 해결
   - 세션 관리 개선
   - 라우팅 로깅 강화

---

**마지막 업데이트**: 2025-12-20  
**다음 점검 예정일**: 2025-12-27












