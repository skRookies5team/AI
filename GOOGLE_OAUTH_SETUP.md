# 🔐 Google OAuth 토큰 DB 관리 설정 가이드

Google Calendar 연동을 위한 OAuth 토큰을 **DB(MariaDB) + Redis**로 안전하게 관리하는 시스템입니다.

## 📋 목차

1. [개요](#개요)
2. [구조](#구조)
3. [설정 방법](#설정-방법)
4. [사용 방법](#사용-방법)
5. [문제 해결](#문제-해결)

---

## 개요

### 왜 DB + Redis인가?

| 항목 | Redis만 사용 | **DB + Redis (권장)** |
|------|-------------|---------------------|
| 서버 재시작 | ❌ 토큰 소실 | ✅ DB에서 복구 |
| 보안 | ⚠️ 메모리에 평문 | ✅ 암호화하여 DB 저장 |
| 성능 | ⚠️ TTL 만료 시 재인증 | ✅ 자동 갱신 |
| 확장성 | ⚠️ 제한적 | ✅ 사원 수 증가 OK |
| 실서비스 | ❌ 부적합 | ✅ 프로덕션 환경 표준 |

### 플로우

```
사용자 요청
    ↓
Redis에서 access_token 캐시 확인 (1시간)
    ↓ (없음)
DB에서 refresh_token 조회 (암호화된 상태)
    ↓
복호화 → access_token 갱신
    ↓
Redis에 캐시 + Google Calendar API 호출
```

---

## 구조

### 1. 파일 구조

```
AI/
├── agents/
│   └── master_graphs/
│       └── schedule/
│           └── services/
│               ├── token_encryption_service.py    # 토큰 암호화/복호화
│               ├── google_token_service.py        # DB 토큰 관리
│               ├── google_calendar_service.py     # Calendar API (수정됨)
│               └── database_service.py            # 기존 DB 서비스
└── fastapi_app.py                                # OAuth 콜백 (수정됨)
```

### 2. DB 테이블

```sql
-- google_credentials 테이블 (이미 생성됨)
CREATE TABLE google_credentials (
    employee_id INT PRIMARY KEY,
    google_email VARCHAR(255) NOT NULL,
    google_sub VARCHAR(128) NULL,
    refresh_token TEXT NOT NULL,        -- 암호화됨
    scope TEXT NOT NULL,
    token_version INT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_used_at DATETIME NULL
);
```

### 3. Redis 캐시

```
Key: google_tokens:{employee_id}
Value: {
    "access_token": "ya29.a0...",
    "refresh_token": "1//0g...",    # 백업용
    "expiry": "2024-12-16T15:30:00"
}
TTL: 3600초 (1시간)
```

---

## 설정 방법

### Step 1: 암호화 키 생성

Python으로 암호화 키를 생성합니다:

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

출력 예시:
```
nF3qJ8kL9mP2rT5vW8yB1cE4gH7jK0nQ3sV6xZ9aC2=
```

### Step 2: .env 파일에 추가

`.env` 파일에 다음을 추가합니다:

```bash
# Google OAuth 설정 (기존)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
APP_BASE_URL=http://localhost:8000

# 토큰 암호화 키 (새로 추가) ⭐
TOKEN_ENCRYPTION_KEY=nF3qJ8kL9mP2rT5vW8yB1cE4gH7jK0nQ3sV6xZ9aC2=

# MariaDB 설정 (기존)
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=ai_agent
DB_USERNAME=root
DB_PASSWORD=your-password

# Redis 설정 (기존)
HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
REDIS_TTL=1800
```

⚠️ **중요**: `TOKEN_ENCRYPTION_KEY`를 설정하지 않으면 자동 생성되지만, 서버 재시작 시 키가 바뀌어 기존 토큰을 복호화할 수 없습니다!

### Step 3: 의존성 설치

`pyproject.toml`에 이미 포함되어 있지만, 확인:

```toml
dependencies = [
    "cryptography>=41.0.0",
    "google-auth>=2.23.0",
    "google-auth-oauthlib>=1.1.0",
    "google-api-python-client>=2.100.0",
    # ... 기타
]
```

---

## 사용 방법

### 1. Google Calendar 연동 (사원 관점)

#### ① 연동 시작

```
사용자: "오늘 일정 알려줘"
       ↓
시스템: "Google Calendar 연동이 필요합니다."
        [연동하기] 버튼 클릭
       ↓
Google 로그인 화면
       ↓
권한 동의 (캘린더 접근)
       ↓
완료! DB에 안전하게 저장됨
```

#### ② 연동 후

```python
# 자동으로 처리됨:
# 1. Redis에서 access_token 확인
# 2. 없으면 DB에서 refresh_token 조회
# 3. 자동 갱신 후 Calendar API 호출
```

#### ③ 연동 해제

```bash
curl -X POST "http://localhost:8000/auth/google/disconnect?employee_id=4001"
```

### 2. 직접 테스트 (개발자)

#### Python 코드로 테스트

```python
import asyncio
from agents.master_graphs.schedule.services.google_token_service import get_token_service

async def test():
    service = get_token_service()
    
    # 토큰 저장
    success = await service.save_google_credentials(
        employee_id=4001,
        google_email="test@example.com",
        refresh_token="1//0g...",
        scope="https://www.googleapis.com/auth/calendar",
        google_sub="12345678901234567890",
    )
    print(f"저장 성공: {success}")
    
    # 토큰 조회
    creds = await service.get_google_credentials(4001)
    if creds:
        print(f"이메일: {creds['google_email']}")
        print(f"마지막 사용: {creds['last_used_at']}")

asyncio.run(test())
```

#### API로 테스트

```bash
# 1. OAuth 플로우 시작
curl "http://localhost:8000/auth/google?user_id=4001"

# 2. 콜백 (자동으로 처리됨)
# → DB + Redis에 토큰 저장

# 3. 일정 조회 (토큰 자동 사용)
curl -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"query": "오늘 일정 알려줘", "employee_id": 4001}'

# 4. 연동 해제
curl -X POST "http://localhost:8000/auth/google/disconnect?employee_id=4001"
```

---

## 문제 해결

### ❌ "TOKEN_ENCRYPTION_KEY가 설정되지 않았습니다"

**원인**: `.env`에 암호화 키가 없음

**해결**:
1. 키 생성: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. `.env`에 추가: `TOKEN_ENCRYPTION_KEY=생성된키`
3. 서버 재시작

### ❌ "토큰 복호화 실패"

**원인**: 암호화 키가 변경됨

**해결**:
1. 기존 토큰 삭제: `DELETE FROM google_credentials WHERE employee_id=4001;`
2. 재연동: Google OAuth 플로우 다시 진행

### ❌ "refresh_token을 받지 못했습니다"

**원인**: OAuth 플로우에서 `access_type=offline`이 누락됨

**해결**:
`fastapi_app.py`의 `_create_google_oauth_flow()`에서 확인:
```python
flow.authorization_url(
    access_type="offline",      # ⭐ 필수
    prompt="consent",           # ⭐ 필수 (재동의 시)
    include_granted_scopes="true",
)
```

### ❌ "DB 연결 실패"

**원인**: MariaDB 설정 오류

**해결**:
```bash
# .env 확인
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=ai_agent
DB_USERNAME=root
DB_PASSWORD=your-password

# MariaDB 연결 테스트
mysql -h 127.0.0.1 -P 3306 -u root -p ai_agent
```

### ❌ "Redis 연결 실패"

**원인**: Redis 서버가 실행되지 않음

**해결**:
```bash
# Redis 서버 시작 (Windows)
redis-server

# Redis 연결 테스트
redis-cli ping
# 응답: PONG
```

---

## 보안 고려사항

### ✅ 현재 적용된 보안

1. **refresh_token 암호화** (Fernet 대칭키 암호화)
2. **access_token은 Redis에만** (메모리, 1시간 TTL)
3. **state 값 검증** (CSRF 방지)
4. **DB 접근 권한 제한** (애플리케이션 계정만)

### 🔒 프로덕션 환경 추가 권장사항

1. **암호화 키 관리**
   - AWS Secrets Manager, GCP Secret Manager 사용
   - 환경 변수 대신 KMS 사용

2. **네트워크 보안**
   - DB/Redis는 VPC 내부에서만 접근
   - TLS/SSL 연결 강제

3. **감사 로그**
   - 토큰 사용 이력 로그
   - 비정상 접근 탐지

4. **토큰 만료 정책**
   - 일정 기간 사용하지 않은 토큰 자동 삭제
   - 정기적인 재인증 요구

---

## FAQ

### Q1. 서버를 재시작하면 다시 로그인해야 하나요?

**A**: 아니요! DB에 저장되므로 서버 재시작 후에도 자동으로 복구됩니다.

### Q2. 팀원마다 각자 Google 계정으로 연동하나요?

**A**: 네! 각 사원(employee_id)마다 개인 Google 계정을 연동합니다.

### Q3. 회사 Google Workspace가 필요한가요?

**A**: 아니요! 개인 Google 계정으로도 충분히 작동합니다.

### Q4. 토큰이 만료되면 어떻게 되나요?

**A**: 자동으로 `refresh_token`으로 갱신됩니다. 사용자 개입 불필요!

### Q5. 여러 명이 동시에 사용할 수 있나요?

**A**: 네! `employee_id`로 구분되므로 무제한 사용자 지원.

---

## 변경 이력

- **2024-12-16**: 초기 버전 (DB + Redis 통합 구조)
  - `token_encryption_service.py` 추가
  - `google_token_service.py` 추가
  - `google_calendar_service.py` 수정 (DB 통합)
  - `fastapi_app.py` OAuth 콜백 수정

---

## 라이선스 & 기여

이 프로젝트는 내부 팀 프로젝트로, Google OAuth 표준 가이드라인을 따릅니다.

문의: 팀 리드에게 연락주세요.

