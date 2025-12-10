# 백엔드 API 연동 가이드

## 🔌 현재 상태

**실제 백엔드 API 사용 모드** (`http://localhost:8080/api`)

Mock 서버가 비활성화되었으며, 실제 백엔드 서버와 통신합니다.

---

## ⚙️ 설정 방법

### 1. Mock 서버 / 실제 백엔드 전환

`lib/api.ts` 파일의 5번째 줄을 수정:

```typescript
// 실제 백엔드 사용 (현재 설정)
const USE_MOCK_SERVER = process.env.NEXT_PUBLIC_USE_MOCK === 'true'

// Mock 서버 사용으로 전환하려면:
const USE_MOCK_SERVER = process.env.NEXT_PUBLIC_USE_MOCK === 'true' || true
```

### 2. 환경 변수 설정

프로젝트 루트에 `.env.local` 파일 생성:

```env
# 백엔드 서버 URL (기본값: http://localhost:8080/api)
NEXT_PUBLIC_API_URL=http://localhost:8080/api

# Mock 서버 사용 여부 (개발 시에만 true)
NEXT_PUBLIC_USE_MOCK=false
```

---

## 📡 백엔드 API 명세

프론트엔드에서 기대하는 API 응답 형식입니다.

### **인증 API**

#### 1. 회원가입
```http
POST /api/auth/signup
Content-Type: application/json

Request:
{
  "email": "user@example.com",
  "password": "password123",
  "nickname": "사용자"
}

Response: 200 OK
{
  "message": "가입 완료"
}
```

#### 2. 로그인
```http
POST /api/auth/login
Content-Type: application/json

Request:
{
  "email": "user@example.com",
  "password": "password123"
}

Response: 200 OK
{
  "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

#### 3. 사용자 정보 조회
```http
GET /api/auth/me
Authorization: Bearer {accessToken}

Response: 200 OK
{
  "userId": "user_123",
  "email": "user@example.com",
  "nickname": "사용자",
  "createdAt": "2025-01-01T00:00:00Z"
}
```

#### 4. 닉네임 중복 확인
```http
GET /api/auth/check-nickname?nickname=사용자
Authorization: Bearer {accessToken}

Response: 200 OK
{
  "available": true
}
```

#### 5. 로그아웃
```http
POST /api/auth/logout
Authorization: Bearer {accessToken}

Response: 200 OK
{
  "message": "로그아웃 완료"
}
```

---

### **회의록 API**

#### 1. 회의 목록 조회
```http
GET /api/meetings?page=1&size=10
Authorization: Bearer {accessToken}

Response: 200 OK
{
  "content": [
    {
      "meetingId": "meeting_123",
      "title": "프로젝트 회의",
      "date": "2025-01-15T10:00:00Z",
      "status": "completed",  // "uploaded" | "processing" | "completed"
      "summary": "회의 요약 내용...",
      "keywords": ["프로젝트", "일정", "역할"],
      "speakers": [
        {
          "speakerId": "speaker_1",
          "segments": [
            {
              "start": 0,
              "end": 5,
              "text": "안녕하세요..."
            }
          ]
        }
      ],
      "createdAt": "2025-01-15T10:00:00Z"
    }
  ],
  "page": 1,
  "size": 10,
  "totalPages": 5
}
```

#### 2. 회의 생성 (파일 업로드)
```http
POST /api/meetings
Authorization: Bearer {accessToken}
Content-Type: multipart/form-data

Request (FormData):
- title: "프로젝트 회의"
- date: "2025-01-15T10:00:00Z"
- file: (audio file)

Response: 200 OK
{
  "meetingId": "meeting_123",
  "status": "uploaded",
  "message": "파일 업로드 완료"
}
```

#### 3. 회의 상세 조회
```http
GET /api/meetings/{meetingId}
Authorization: Bearer {accessToken}

Response: 200 OK
{
  "meetingId": "meeting_123",
  "title": "프로젝트 회의",
  "date": "2025-01-15T10:00:00Z",
  "status": "completed",
  "summary": "회의 요약...",
  "keywords": ["프로젝트", "일정"],
  "speakers": [...],
  "createdAt": "2025-01-15T10:00:00Z"
}
```

#### 4. 회의 수정
```http
PUT /api/meetings/{meetingId}
Authorization: Bearer {accessToken}
Content-Type: application/json

Request:
{
  "title": "수정된 제목",
  "summary": "수정된 요약",
  "keywords": ["키워드1", "키워드2"]
}

Response: 200 OK
{
  "message": "수정 완료"
}
```

#### 5. 회의 삭제
```http
DELETE /api/meetings/{meetingId}
Authorization: Bearer {accessToken}

Response: 200 OK
{
  "message": "삭제 완료"
}
```

#### 6. AI 분석 요청 (선택사항)
```http
POST /api/ai/analyze
Authorization: Bearer {accessToken}
Content-Type: application/json

Request:
{
  "meetingId": "meeting_123",
  "filePath": "/path/to/audio.webm"
}

Response: 202 Accepted
{
  "status": "processing"
}
```

---

## 🔐 인증 처리

### 토큰 관리
- **액세스 토큰**: `localStorage.getItem('accessToken')`
- **리프레시 토큰**: `localStorage.getItem('refreshToken')`
- **헤더**: `Authorization: Bearer {accessToken}`

### 401 Unauthorized 처리
프론트엔드에서 401 에러 발생 시 자동으로 로그인 페이지로 이동합니다.

---

## 📝 에러 응답 형식

모든 에러는 다음 형식으로 반환되어야 합니다:

```json
{
  "message": "에러 메시지",
  "status": 400
}
```

---

## ✅ 파일 업로드 제한

프론트엔드에서 검증하는 항목:
- **최대 크기**: 100MB
- **지원 형식**: `.webm`, `.wav`, `.mp3`, `.m4a`, `.ogg`, `.mp4`
- **MIME 타입**: `audio/*`

백엔드에서도 동일한 검증을 수행하는 것을 권장합니다.

---

## 🧪 테스트

### 백엔드 서버 실행 확인
```bash
curl http://localhost:8080/api/health
```

### 프론트엔드 개발 서버 실행
```bash
npm run dev
# 또는
pnpm dev
```

http://localhost:3000 접속

---

## 🔄 CORS 설정

백엔드에서 다음 CORS 설정이 필요합니다:

```
Access-Control-Allow-Origin: http://localhost:3000
Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, Authorization
Access-Control-Allow-Credentials: true
```

---

## 📚 참고 파일

- **API 클라이언트**: `lib/api.ts`
- **타입 정의**: `types/api.ts`
- **Mock 서버** (참고용): `lib/mock-server.ts`
- **API 명세 상세**: `FRONTEND-SPEC.md`

---

## 🐛 문제 해결

### 1. 연결 실패 (ECONNREFUSED)
- 백엔드 서버가 `localhost:8080`에서 실행 중인지 확인
- 포트 번호가 맞는지 확인

### 2. CORS 에러
- 백엔드에서 CORS 설정 확인
- 개발 중이면 백엔드에서 `*` 허용

### 3. 401 Unauthorized
- 토큰이 올바르게 전달되는지 확인
- 토큰 만료 시간 확인

### 4. 파일 업로드 실패
- `Content-Type: multipart/form-data` 확인
- 파일 크기 제한 확인 (100MB)
- 백엔드 파일 업로드 경로 확인

---

## 💡 추천 백엔드 구조

```
backend/
├── src/
│   ├── auth/              # 인증 모듈
│   ├── meetings/          # 회의록 모듈
│   ├── ai/                # AI 분석 모듈
│   └── uploads/           # 파일 업로드 처리
├── prisma/                # DB 스키마 (Prisma)
└── package.json
```

---

**문의사항이 있으면 프론트엔드 개발자에게 연락하세요!** 🚀

