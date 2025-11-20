# 통합 가이드 (Integration Guide)

Spring Boot 또는 다른 백엔드 시스템과 AI 에이전트 챗봇을 통합할 때 필요한 파일 및 설정 가이드입니다.

## 📁 필수 파일 목록

### 1. **핵심 애플리케이션 파일**

```
fastapi_app.py              # FastAPI 메인 애플리케이션 (API 엔드포인트)
main_agents.py              # 대화형 챗봇 실행 파일 (선택사항)
```

### 2. **에이전트 모듈 디렉토리 (`agents/`)**

```
agents/
├── __init__.py
├── supervisor.py           # 메인 라우터 (모든 에이전트를 관리)
├── schedule_master.py      # 일정/연차 에이전트 마스터
├── schedule_data.py        # 일정 데이터베이스 접근
├── schedule_langgraph.py   # 일정 LangGraph 워크플로우
├── schedule_api.py         # Spring Boot API 호출 (일정 생성/취소)
├── leave_agent.py          # 휴가 에이전트
├── leave_data.py           # 휴가 데이터베이스 접근
├── llm_router.py           # LLM 기반 의도 분류
├── document_master.py      # 문서 에이전트 마스터
├── qa_master.py            # Q&A 에이전트 마스터
└── tech_master.py          # 기술 에이전트 마스터 (선택사항)
```

### 3. **설정 파일**

```
pyproject.toml              # 프로젝트 의존성 및 메타데이터
uv.lock                     # 의존성 버전 고정 (uv 사용 시)
.env                        # 환경 변수 (DB 연결, API 키 등)
```

### 4. **테스트 파일 (선택사항, 개발/디버깅용)**

```
tests/                      # 단위 테스트
test_*.py                   # 통합 테스트 스크립트
```

## 🔧 통합 방법

### 방법 1: FastAPI 서버로 독립 실행 (권장)

#### 1.1 서버 실행

```bash
# 가상환경 활성화
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac

# FastAPI 서버 실행
uvicorn fastapi_app:app --host 0.0.0.0 --port 8000 --reload
```

#### 1.2 Spring Boot에서 호출

```java
// WebClient 사용 예시
@Service
public class ChatbotService {
    private final WebClient webClient;
    private final String CHATBOT_API_URL = "http://localhost:8000";
    
    public ChatbotService(WebClient.Builder webClientBuilder) {
        this.webClient = webClientBuilder
            .baseUrl(CHATBOT_API_URL)
            .build();
    }
    
    public ChatResponse chat(String query, String sessionId) {
        ChatRequest request = new ChatRequest(query, sessionId);
        
        return webClient.post()
            .uri("/api/chat")
            .bodyValue(request)
            .retrieve()
            .bodyToMono(ChatResponse.class)
            .block();
    }
}
```

### 방법 2: Docker 컨테이너로 실행

#### 2.1 Dockerfile 생성

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# uv 설치
COPY uv /usr/local/bin/uv
RUN chmod +x /usr/local/bin/uv

# 의존성 설치
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# 애플리케이션 파일 복사
COPY . .

# FastAPI 서버 실행
CMD ["uv", "run", "uvicorn", "fastapi_app:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### 2.2 docker-compose.yml (이미 존재)

```yaml
version: '3.8'
services:
  chatbot:
    build: .
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - SCHEDULE_DB_HOST=${SCHEDULE_DB_HOST}
      - SCHEDULE_DB_USER=${SCHEDULE_DB_USER}
      - SCHEDULE_DB_PASSWORD=${SCHEDULE_DB_PASSWORD}
      - SCHEDULE_DB_NAME=${SCHEDULE_DB_NAME}
    env_file:
      - .env
```

### 방법 3: Python 패키지로 설치

#### 3.1 패키지 빌드

```bash
uv build
```

#### 3.2 다른 프로젝트에서 설치

```bash
pip install dist/ai-module-0.1.0-py3-none-any.whl
```

## 📡 API 엔드포인트

### 1. 통합 챗봇 API (권장)

```http
POST /api/chat
Content-Type: application/json

{
  "query": "강하은 연차 얼마나 남았어?",
  "session_id": "optional-session-id"
}
```

**응답:**
```json
{
  "ok": true,
  "message": "강하은(사번: 4001)의 잔여 연차는...",
  "rows": [],
  "error": null,
  "session_id": "optional-session-id"
}
```

### 2. 일정 전용 API

```http
POST /api/schedule/chat
Content-Type: application/json

{
  "query": "강하은 출장 일정 알려줘"
}
```

### 3. 휴가 전용 API

```http
POST /api/leave/chat
Content-Type: application/json

{
  "query": "강하은 잔여 연차 조회해줘"
}
```

### 4. 헬스 체크

```http
GET /healthz
```

## 🔐 환경 변수 설정

`.env` 파일에 다음 변수들을 설정해야 합니다:

```env
# OpenAI API
OPENAI_API_KEY=your-openai-api-key

# MariaDB 연결 정보
SCHEDULE_DB_HOST=175.120.35.228
SCHEDULE_DB_PORT=3306
SCHEDULE_DB_USER=root
SCHEDULE_DB_PASSWORD=root
SCHEDULE_DB_NAME=ai_agent

# Spring Boot API (일정 생성/취소용)
SPRING_BOOT_API_URL=http://localhost:8080
SPRING_BOOT_API_KEY=optional-api-key

# Milvus (QA 에이전트용, 선택사항)
MILVUS_HOST=175.120.35.228
MILVUS_PORT=19530
MILVUS_COLLECTION_NAME=Employee_Benefits_embeddings
```

## 📦 최소 통합 파일 세트

다른 시스템과 통합할 때 **최소한** 다음 파일들만 있으면 됩니다:

```
필수 파일:
├── fastapi_app.py
├── agents/
│   ├── __init__.py
│   ├── supervisor.py
│   ├── schedule_master.py
│   ├── schedule_data.py
│   ├── schedule_langgraph.py
│   ├── schedule_api.py
│   ├── leave_agent.py
│   ├── leave_data.py
│   └── llm_router.py
├── pyproject.toml
├── uv.lock (또는 requirements.txt)
└── .env

선택 파일 (기능 확장):
├── agents/document_master.py
├── agents/qa_master.py
├── agents/tech_master.py
└── 기타 에이전트 파일들
```

## 🚀 배포 체크리스트

- [ ] `.env` 파일 설정 완료
- [ ] MariaDB 연결 테스트
- [ ] OpenAI API 키 설정
- [ ] Spring Boot API URL 설정 (일정 생성/취소 기능 사용 시)
- [ ] FastAPI 서버 실행 테스트
- [ ] `/healthz` 엔드포인트 확인
- [ ] `/api/chat` 엔드포인트 테스트
- [ ] Spring Boot에서 API 호출 테스트

## 🔍 문제 해결

### 1. 연결 오류
- MariaDB 서버가 실행 중인지 확인
- 방화벽 설정 확인
- `.env` 파일의 연결 정보 확인

### 2. 모듈 import 오류
- 가상환경이 활성화되어 있는지 확인
- `uv sync` 또는 `pip install -r requirements.txt` 실행

### 3. API 응답 오류
- FastAPI 서버 로그 확인
- `OPENAI_API_KEY` 설정 확인
- 데이터베이스 연결 상태 확인

## 📞 추가 지원

문제가 발생하면 다음을 확인하세요:
1. `fastapi_app.py`의 로그 출력
2. `agents/supervisor.py`의 라우팅 로직
3. 데이터베이스 연결 상태
4. 환경 변수 설정

