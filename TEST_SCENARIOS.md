# 터미널 테스트 시나리오

## 사전 준비

### 1. Redis 서버 실행 확인
```bash
# Redis가 실행 중인지 확인
redis-cli ping
# 응답: PONG

# 또는 Docker로 Redis 실행
docker run -d -p 6379:6379 redis:latest
```

### 2. 환경 변수 설정 확인
```bash
# .env 파일 확인
cat .env | grep REDIS
```

### 3. 의존성 설치 확인
```bash
# 패키지 설치
uv sync

# 또는 pip 사용 시
pip install -r requirements.txt
```

## 테스트 시나리오

### 시나리오 1: Redis 체크포인터 연결 테스트

```bash
# Python 인터프리터 실행
python

# 다음 코드 실행
from agents.redis_checkpoint import get_redis_checkpointer

checkpointer = get_redis_checkpointer()
if checkpointer:
    print("✅ Redis 체크포인터 정상 작동")
else:
    print("⚠️ Redis 체크포인터 사용 불가 (메모리 체크포인터로 fallback)")
```

### 시나리오 2: Structured Output Parser 테스트

```bash
# Python 인터프리터 실행
python

# 다음 코드 실행 (비동기이므로 주의)
import asyncio
from agents.structured_output import extract_structured_output, QueryIntent, DocumentGenerationParams, EmailSearchParams

# 1. 의도 분류 테스트
async def test_intent():
    result = await extract_structured_output(
        query="스마트워치 신제품 개발 계획서 작성해줘",
        model_class=QueryIntent
    )
    print(f"의도: {result.intent}")
    print(f"신뢰도: {result.confidence}")
    print(f"엔티티: {result.extracted_entities}")

asyncio.run(test_intent())

# 2. 문서 생성 파라미터 추출 테스트
async def test_document_params():
    result = await extract_structured_output(
        query="2024년 1분기 인사정보 요약 리포트 작성",
        model_class=DocumentGenerationParams
    )
    print(f"템플릿 타입: {result.template_type}")
    print(f"변수: {result.variables}")
    print(f"출력 형식: {result.output_format}")

asyncio.run(test_document_params())

# 3. 이메일 검색 파라미터 추출 테스트
async def test_email_params():
    result = await extract_structured_output(
        query="육아휴직 관련 이메일 찾아줘",
        model_class=EmailSearchParams
    )
    print(f"키워드: {result.keywords}")
    print(f"발신자: {result.from_address}")
    print(f"날짜 범위: {result.date_range}")

asyncio.run(test_email_params())
```

### 시나리오 3: FastAPI 서버 실행 및 API 테스트

#### 3-1. 서버 실행
```bash
# FastAPI 서버 실행
uvicorn fastapi_app:app --reload --host 0.0.0.0 --port 8000

# 또는
python -m uvicorn fastapi_app:app --reload
```

#### 3-2. 헬스 체크
```bash
# 별도 터미널에서
curl http://localhost:8000/healthz

# 또는 PowerShell (Windows)
Invoke-WebRequest -Uri http://localhost:8000/healthz -Method GET
```

#### 3-3. 단일 턴 대화 테스트
```bash
# 새 세션으로 대화 시작
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"2024년 1분기 인사정보 요약 리포트 작성해줘\", \"employee_id\": 1001}"

# PowerShell (Windows)
$body = @{
    query = "2024년 1분기 인사정보 요약 리포트 작성해줘"
    employee_id = 1001
} | ConvertTo-Json

Invoke-WebRequest -Uri http://localhost:8000/api/chat -Method POST -Body $body -ContentType "application/json"
```

#### 3-4. 멀티턴 대화 테스트

```bash
# 1턴: 첫 번째 질문 (세션 ID 자동 생성)
RESPONSE1=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "스마트워치 신제품 개발 계획서 작성해줘", "employee_id": 1001}')

# 세션 ID 추출 (jq 필요)
SESSION_ID=$(echo $RESPONSE1 | jq -r '.session_id')
echo "세션 ID: $SESSION_ID"

# 2턴: 같은 세션으로 후속 질문
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"그 계획서를 PDF로 변환해줘\", \"session_id\": \"$SESSION_ID\", \"employee_id\": 1001}"

# 3턴: 이전 대화 기반 질문
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"1번 문서에 대해 자세히 알려줘\", \"session_id\": \"$SESSION_ID\", \"employee_id\": 1001}"
```

**PowerShell 버전 (Windows):**
```powershell
# 1턴: 첫 번째 질문
$response1 = Invoke-RestMethod -Uri http://localhost:8000/api/chat -Method POST -Body (@{
    query = "스마트워치 신제품 개발 계획서 작성해줘"
    employee_id = 1001
} | ConvertTo-Json) -ContentType "application/json"

$sessionId = $response1.session_id
Write-Host "세션 ID: $sessionId"

# 2턴: 같은 세션으로 후속 질문
Invoke-RestMethod -Uri http://localhost:8000/api/chat -Method POST -Body (@{
    query = "그 계획서를 PDF로 변환해줘"
    session_id = $sessionId
    employee_id = 1001
} | ConvertTo-Json) -ContentType "application/json"

# 3턴: 이전 대화 기반 질문
Invoke-RestMethod -Uri http://localhost:8000/api/chat -Method POST -Body (@{
    query = "1번 문서에 대해 자세히 알려줘"
    session_id = $sessionId
    employee_id = 1001
} | ConvertTo-Json) -ContentType "application/json"
```

#### 3-5. 이메일 검색 테스트
```bash
# 이메일 검색
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "육아휴직 관련 이메일 찾아줘", "employee_id": 1001}'

# 숫자로 시작하는 질문 (이전 검색 결과 참조)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "938번 이메일에 대해 자세히 알려줘", "session_id": "SESSION_ID", "employee_id": 1001}'
```

### 시나리오 4: 통합 테스트 스크립트

#### test_api.sh (Linux/Mac)
```bash
#!/bin/bash

BASE_URL="http://localhost:8000"

echo "=== 1. 헬스 체크 ==="
curl -s "$BASE_URL/healthz" | jq

echo -e "\n=== 2. 문서 생성 요청 (새 세션) ==="
RESPONSE=$(curl -s -X POST "$BASE_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"query": "2024년 1분기 인사정보 요약 리포트 작성", "employee_id": 1001}')

SESSION_ID=$(echo $RESPONSE | jq -r '.session_id')
echo "세션 ID: $SESSION_ID"
echo "$RESPONSE" | jq

echo -e "\n=== 3. 후속 질문 (같은 세션) ==="
curl -s -X POST "$BASE_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"그 문서를 PDF로 변환해줘\", \"session_id\": \"$SESSION_ID\", \"employee_id\": 1001}" | jq

echo -e "\n=== 4. 이메일 검색 ==="
curl -s -X POST "$BASE_URL/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"query": "육아휴직 관련 이메일 찾아줘", "employee_id": 1001}' | jq
```

#### test_api.ps1 (Windows PowerShell)
```powershell
$baseUrl = "http://localhost:8000"

Write-Host "=== 1. 헬스 체크 ===" -ForegroundColor Green
Invoke-RestMethod -Uri "$baseUrl/healthz" | ConvertTo-Json

Write-Host "`n=== 2. 문서 생성 요청 (새 세션) ===" -ForegroundColor Green
$response = Invoke-RestMethod -Uri "$baseUrl/api/chat" -Method POST -Body (@{
    query = "2024년 1분기 인사정보 요약 리포트 작성"
    employee_id = 1001
} | ConvertTo-Json) -ContentType "application/json"

$sessionId = $response.session_id
Write-Host "세션 ID: $sessionId" -ForegroundColor Yellow
$response | ConvertTo-Json

Write-Host "`n=== 3. 후속 질문 (같은 세션) ===" -ForegroundColor Green
Invoke-RestMethod -Uri "$baseUrl/api/chat" -Method POST -Body (@{
    query = "그 문서를 PDF로 변환해줘"
    session_id = $sessionId
    employee_id = 1001
} | ConvertTo-Json) -ContentType "application/json" | ConvertTo-Json

Write-Host "`n=== 4. 이메일 검색 ===" -ForegroundColor Green
Invoke-RestMethod -Uri "$baseUrl/api/chat" -Method POST -Body (@{
    query = "육아휴직 관련 이메일 찾아줘"
    employee_id = 1001
} | ConvertTo-Json) -ContentType "application/json" | ConvertTo-Json
```

### 시나리오 5: Python 스크립트로 테스트

#### test_integration.py
```python
"""통합 테스트 스크립트."""
import asyncio
import aiohttp
import json
from typing import Optional


async def test_chat(
    session: aiohttp.ClientSession,
    query: str,
    session_id: Optional[str] = None,
    employee_id: int = 1001
) -> dict:
    """챗봇 API 테스트."""
    url = "http://localhost:8000/api/chat"
    payload = {
        "query": query,
        "employee_id": employee_id
    }
    if session_id:
        payload["session_id"] = session_id
    
    async with session.post(url, json=payload) as response:
        return await response.json()


async def main():
    """메인 테스트 시나리오."""
    async with aiohttp.ClientSession() as session:
        print("=== 1. 헬스 체크 ===")
        async with session.get("http://localhost:8000/healthz") as response:
            result = await response.json()
            print(json.dumps(result, indent=2, ensure_ascii=False))
        
        print("\n=== 2. 문서 생성 요청 (새 세션) ===")
        response1 = await test_chat(
            session,
            "2024년 1분기 인사정보 요약 리포트 작성"
        )
        session_id = response1.get("session_id")
        print(f"세션 ID: {session_id}")
        print(json.dumps(response1, indent=2, ensure_ascii=False))
        
        if session_id:
            print("\n=== 3. 후속 질문 (같은 세션) ===")
            response2 = await test_chat(
                session,
                "그 문서를 PDF로 변환해줘",
                session_id=session_id
            )
            print(json.dumps(response2, indent=2, ensure_ascii=False))
            
            print("\n=== 4. 이전 대화 기반 질문 ===")
            response3 = await test_chat(
                session,
                "1번 문서에 대해 자세히 알려줘",
                session_id=session_id
            )
            print(json.dumps(response3, indent=2, ensure_ascii=False))
        
        print("\n=== 5. 이메일 검색 ===")
        response4 = await test_chat(
            session,
            "육아휴직 관련 이메일 찾아줘"
        )
        print(json.dumps(response4, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
```

실행:
```bash
python test_integration.py
```

### 시나리오 6: Redis 상태 확인

```bash
# Redis CLI 접속
redis-cli

# 체크포인트 확인
KEYS *
# 또는 특정 패턴
KEYS checkpoint:*

# 특정 체크포인트 확인
GET checkpoint:세션ID

# TTL 확인
TTL checkpoint:세션ID

# 모든 체크포인트 삭제 (테스트용)
FLUSHDB
```

## 예상 결과

### 성공적인 응답 예시
```json
{
  "ok": true,
  "message": "2024년 1분기 인사정보 요약 리포트 작성 요청을 접수했습니다.",
  "rows": [],
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 에러 응답 예시
```json
{
  "detail": "query 값이 필요합니다."
}
```

## 트러블슈팅

### Redis 연결 오류
```bash
# Redis가 실행 중인지 확인
redis-cli ping

# Redis 포트 확인
netstat -an | grep 6379

# Docker Redis 재시작
docker restart redis-container
```

### 포트 충돌
```bash
# 8000 포트 사용 중인 프로세스 확인
lsof -i :8000  # Mac/Linux
netstat -ano | findstr :8000  # Windows

# 다른 포트로 실행
uvicorn fastapi_app:app --reload --port 8001
```

### 의존성 오류
```bash
# 패키지 재설치
uv sync --refresh

# 특정 패키지 설치
uv pip install langgraph-checkpoint-redis
```

