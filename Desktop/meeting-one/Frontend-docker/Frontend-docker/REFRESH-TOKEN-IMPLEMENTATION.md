# ✅ Refresh Token 구현 완료

백엔드에서 Refresh Token이 추가됨에 따라 프론트엔드도 자동 토큰 갱신 기능을 구현했습니다.

---

## 🎯 구현 내용

### 1. **자동 토큰 갱신 (401 에러 시)**

API 요청 중 401 Unauthorized 에러 발생 시, 자동으로 Refresh Token을 사용해 Access Token을 갱신합니다.

#### 동작 흐름:
```
1. API 요청 → 401 에러 발생
2. Refresh Token으로 새 Access Token 발급 요청
3. 성공 시: 새 토큰으로 원래 요청 재시도
4. 실패 시: 로그아웃 처리 → 로그인 페이지로 이동
```

#### 구현 위치: `lib/api.ts`

```typescript
// 401 에러 감지 시 자동 처리
if (response.status === 401 && retry) {
  // Refresh Token으로 갱신 시도
  const newToken = await this.refreshAccessToken()
  
  if (newToken) {
    // 새 토큰으로 재시도
    return this.request<T>(endpoint, options, false)
  } else {
    // 갱신 실패 → 로그아웃
    this.clearToken()
    window.location.href = '/login'
  }
}
```

### 2. **Refresh Token 저장 및 관리**

#### 로그인/회원가입 시:
- Access Token과 Refresh Token을 모두 localStorage에 저장
- `contexts/AuthContext.tsx`에서 처리

```typescript
// 로그인 응답
{
  accessToken: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  refreshToken: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}

// 저장
apiClient.setToken(accessToken, refreshToken)
```

#### 로그아웃 시:
- Access Token, Refresh Token, 사용자 정보 모두 제거

```typescript
clearToken() {
  localStorage.removeItem('accessToken')
  localStorage.removeItem('refreshToken')
  localStorage.removeItem('user')
}
```

### 3. **동시 요청 처리**

여러 API 요청이 동시에 401 에러를 받아도, Refresh Token 요청은 **단 한 번만** 실행됩니다.

```typescript
// 토큰 갱신 중이면 다른 요청들은 대기
if (this.isRefreshing) {
  return new Promise((resolve) => {
    this.subscribeTokenRefresh((newToken) => {
      // 새 토큰으로 재시도
      this.request<T>(endpoint, options, false).then(resolve)
    })
  })
}
```

---

## 🔌 백엔드 API 요구사항

### 1. POST `/auth/refresh`

Refresh Token으로 새 Access Token 발급

**Request:**
```http
POST /api/auth/refresh
Authorization: Bearer {refreshToken}  ← 헤더에 Refresh Token 포함
```

**Response (200 OK):**
```json
{
  "accessToken": "새로운_액세스_토큰",
  "refreshToken": "새로운_리프레시_토큰"  // 선택사항
}
```

**Response (401 Unauthorized):**
```json
{
  "message": "Refresh Token이 만료되었습니다"
}
```

### 2. POST `/auth/login`

로그인 시 Refresh Token도 함께 반환

**Response:**
```json
{
  "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### 3. POST `/auth/signup`

회원가입 시에도 토큰 반환 (선택사항)

**Response:**
```json
{
  "accessToken": "...",
  "refreshToken": "..."
}
```

---

## 🧪 테스트 방법

### 1. 정상 동작 테스트

```bash
# 1. 로그인
로그인 → localStorage 확인

# 예상 결과:
accessToken: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
refreshToken: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### 2. 자동 갱신 테스트

```bash
# 브라우저 콘솔 (F12)에서 확인

# 1. 로그인 성공 시:
🔐 로그인 성공: { hasAccessToken: true, hasRefreshToken: true }

# 2. Access Token 만료 시:
🔐 401 에러 감지 - 토큰 갱신 시도
🔄 Access Token 갱신 중...
✅ Access Token 갱신 완료

# 3. Refresh Token도 만료 시:
🔐 401 에러 감지 - 토큰 갱신 시도
🔄 Access Token 갱신 중...
❌ Token 갱신 실패: 401
→ 로그인 페이지로 자동 이동
```

### 3. 수동 테스트

```javascript
// 브라우저 콘솔에서 실행

// Access Token 강제 만료 (짧은 시간으로 설정)
localStorage.setItem('accessToken', '만료된_토큰')

// API 요청 시도
// → 자동으로 Refresh Token으로 갱신 후 재시도됨
```

---

## 📝 변경된 파일

### 1. `lib/api.ts`
- ✅ `refreshAccessToken()` 메서드 추가
- ✅ `request()` 메서드에 401 자동 처리 로직 추가
- ✅ 동시 요청 처리를 위한 `isRefreshing`, `refreshSubscribers` 추가

### 2. `contexts/AuthContext.tsx`
- ✅ 로그인 시 refreshToken 저장
- ✅ 회원가입 시 refreshToken 저장
- ✅ 콘솔 로그 추가 (디버깅용)

### 3. `types/api.ts`
- ✅ `LoginResponse` 인터페이스에 `refreshToken?` 추가

---

## 🔒 보안 고려사항

### 1. **Token 저장 위치**
- 현재: `localStorage` (편의성 우선)
- 보안 강화 옵션: `httpOnly` 쿠키 사용 권장

### 2. **Refresh Token 만료 시간**
- Access Token: 15분 ~ 1시간 권장
- Refresh Token: 7일 ~ 30일 권장

### 3. **Token Rotation**
- Refresh Token 사용 시마다 새로운 Refresh Token 발급 권장
- 기존 Refresh Token은 즉시 무효화

---

## 🚀 다음 단계 (선택사항)

### 1. **Silent Refresh**
Access Token 만료 전에 미리 갱신

```typescript
// 만료 5분 전에 자동 갱신
setInterval(() => {
  const expiresAt = getTokenExpiry()
  if (expiresAt - Date.now() < 5 * 60 * 1000) {
    refreshAccessToken()
  }
}, 60 * 1000) // 1분마다 체크
```

### 2. **Refresh Token 백그라운드 갱신**
사용자가 브라우저를 닫았다가 다시 열었을 때도 자동 로그인

### 3. **Device Tracking**
Refresh Token에 디바이스 정보 포함하여 보안 강화

---

## ✅ 체크리스트

프론트엔드:
- [x] 로그인/회원가입 시 refreshToken 저장
- [x] 401 에러 시 자동 갱신
- [x] 갱신 실패 시 로그아웃 처리
- [x] 동시 요청 중복 갱신 방지
- [x] 타입 정의 업데이트

백엔드 (확인 필요):
- [ ] POST `/auth/refresh` API 구현
- [ ] 로그인 응답에 refreshToken 포함
- [ ] Refresh Token 검증 로직
- [ ] Token Rotation 구현 (권장)

---

## 📞 문의사항

Refresh Token 관련 문제가 발생하면:

1. 브라우저 콘솔(F12) 확인
2. localStorage에 refreshToken이 저장되어 있는지 확인
3. 백엔드 `/auth/refresh` API가 구현되어 있는지 확인

**완료! 🎉**

