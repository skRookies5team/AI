# Meeting One-Line - Frontend 개발 명세서

## 1. 기술 스택

- **Framework**: Next.js 15.2.4 (App Router)
- **언어**: TypeScript 5
- **UI 라이브러리**: React 19
- **상태 관리**: React Context API + Custom Hooks
- **스타일링**: Tailwind CSS 4.1.9
- **UI 컴포넌트**: shadcn/ui (Radix UI 기반)
- **폼 관리**: React Hook Form 7.60.0 + Zod 3.25.76
- **아이콘**: Lucide React 0.454.0
- **테마**: next-themes 0.4.6 (다크모드 지원)
- **애널리틱스**: Vercel Analytics

## 2. 프로젝트 구조

```
meeting-recorder/
├── app/                        # Next.js App Router
│   ├── api/                      # API Routes (Mock 서버)
│   │   ├── auth/                   # 인증 관련 API
│   │   │   ├── login/
│   │   │   └── signup/
│   │   └── meetings/               # 회의록 관련 API
│   ├── login/                    # 로그인 페이지
│   ├── signup/                   # 회원가입 페이지
│   ├── meetings/                 # 회의록 관련 페이지
│   │   ├── [id]/                   # 회의록 상세 페이지
│   │   └── page.tsx                # 회의록 목록 페이지
│   ├── layout.tsx                # 루트 레이아웃
│   ├── page.tsx                  # 메인 페이지 (홈/녹음)
│   └── globals.css               # 전역 스타일
├── components/                 # React 컴포넌트
│   ├── ui/                       # shadcn/ui 기본 컴포넌트 (30+ 컴포넌트)
│   ├── recording-view.tsx        # 녹음 화면 컴포넌트
│   ├── processing-view.tsx       # 처리 중 화면 컴포넌트
│   ├── result-view.tsx           # 결과 화면 컴포넌트
│   └── theme-provider.tsx        # 테마 프로바이더
├── contexts/                   # React Context
│   └── AuthContext.tsx           # 인증 상태 관리
├── hooks/                      # Custom Hooks
│   ├── use-toast.ts              # Toast 알림 훅
│   ├── use-mobile.ts             # 모바일 감지 훅
│   └── useMeetings.ts            # 회의록 관리 훅
├── lib/                        # 유틸리티 라이브러리
│   ├── api.ts                    # API 클라이언트
│   ├── mock-server.ts            # Mock 서버 구현
│   └── utils.ts                  # 공통 유틸리티 (cn 함수 등)
├── types/                      # TypeScript 타입 정의
│   └── api.ts                    # API 관련 타입
├── public/                     # 정적 파일
│   ├── logo.png
│   └── placeholder-*.{jpg,svg}
├── styles/                     # 추가 스타일
├── package.json
├── next.config.mjs
├── tsconfig.json
├── components.json             # shadcn/ui 설정
└── postcss.config.mjs

```

## 3. 환경 설정 (`.env.local`)

```bash
# Mock 서버 사용 여부 (개발 시)
NEXT_PUBLIC_USE_MOCK=true

# 실제 백엔드 API URL (프로덕션)
NEXT_PUBLIC_API_URL=http://localhost:8080/api

```

**환경 전환:**
- 개발/테스트: `NEXT_PUBLIC_USE_MOCK=true` → Mock 서버 사용
- 프로덕션: `NEXT_PUBLIC_USE_MOCK=false` → 실제 백엔드 서버 연동

## 4. 주요 페이지

| 페이지 경로 | 파일 위치 | 주요 기능 | 인증 필요 |
|------------|----------|----------|---------|
| `/` | `app/page.tsx` | 메인 대시보드, 녹음 시작, 파일 업로드 | ✅ |
| `/login` | `app/login/page.tsx` | 로그인 (이메일 + 비밀번호) | ❌ |
| `/signup` | `app/signup/page.tsx` | 회원가입 (이메일, 비밀번호, 닉네임) | ❌ |
| `/meetings` | `app/meetings/page.tsx` | 회의록 목록, 상태별 필터링 | ✅ |
| `/meetings/[id]` | `app/meetings/[id]/page.tsx` | 회의록 상세, 요약/결정사항/액션아이템/원문 | ✅ |

### 4.1 페이지별 상세 설명

#### 📍 `/` - 메인 페이지
- **주요 기능:**
  - 회의 시작하기 (브라우저 녹음)
  - 음성 파일 업로드 (.webm, .wav, .mp3, .m4a, .ogg)
  - 사용자 정보 표시
  - 로그아웃
- **상태 관리:**
  - `viewState`: `start` | `recording`
  - 로컬스토리지를 통한 회의 데이터 임시 저장

#### 📍 `/meetings` - 회의록 목록
- **주요 기능:**
  - 전체 회의록 목록 표시 (최신순)
  - 상태별 뱃지: `uploaded`, `processing`, `completed`
  - 실시간 처리 진행률 표시 (processing 상태)
  - 회의록 클릭 시 상세 페이지로 이동
  - 새 회의 시작 / 파일 업로드 버튼
- **데이터 구조:**
  ```typescript
  {
    meetingId: string
    title: string
    date: string
    status: 'uploaded' | 'processing' | 'completed'
    summary?: string
    keywords?: string[]
  }
  ```

#### 📍 `/meetings/[id]` - 회의록 상세
- **탭 구조:**
  1. **결정사항**: 주요 키워드 목록
  2. **액션아이템**: 담당자별 TODO 리스트 (예정)
  3. **원문**: 화자별 STT 변환 텍스트
- **다운로드 기능:**
  - `.txt`: 원문 자막
  - `.md`: Markdown 형식 회의록
  - `.json`: 구조화된 데이터

## 5. 컴포넌트

### 5.1 공통 UI 컴포넌트 (`components/ui/`)

shadcn/ui 기반 30+ 컴포넌트 제공:

| 컴포넌트 | 용도 | 주요 Props |
|---------|------|----------|
| `Button` | 버튼 | `variant`, `size`, `onClick` |
| `Card` | 카드 레이아웃 | `className` |
| `Badge` | 상태 뱃지 | `variant` (`default`, `secondary`, `destructive`, `outline`) |
| `Input` | 입력 필드 | `type`, `value`, `onChange` |
| `Tabs` | 탭 네비게이션 | `defaultValue` |
| `Dialog` | 모달 다이얼로그 | `open`, `onOpenChange` |
| `Spinner` | 로딩 스피너 | - |
| `Progress` | 진행률 바 | `value` |
| `Toast` | 알림 메시지 | `title`, `description`, `variant` |

**특징:**
- Radix UI 기반으로 접근성(a11y) 준수
- Tailwind CSS로 완전히 커스터마이징 가능
- TypeScript 타입 안전성 보장

### 5.2 기능 컴포넌트

#### `RecordingView` (`components/recording-view.tsx`)
- **역할**: 브라우저 기반 음성 녹음 UI
- **핵심 기능:**
  - `MediaRecorder` API를 통한 실시간 녹음
  - 녹음 시간 타이머 (mm:ss 형식)
  - 녹음 중 시각적 애니메이션 (파동 효과)
  - 녹음 완료 시 Blob 반환
- **Props:**
  ```typescript
  {
    onComplete: (blob: Blob) => void
  }
  ```

#### `ProcessingView` (`components/processing-view.tsx`)
- **역할**: AI 분석 중 로딩 화면
- **기능**: 진행률 표시, 상태 폴링

#### `ResultView` (`components/result-view.tsx`)
- **역할**: AI 분석 결과 표시
- **기능:**
  - 요약문, 결정사항, 액션아이템 표시
  - 파일 다운로드 (.txt, .md, .json)
  - 새 회의 시작 버튼

## 6. 상태 관리

### 6.1 React Context API

#### `AuthContext` (`contexts/AuthContext.tsx`)

**제공 기능:**
```typescript
interface AuthContextType {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  login: (credentials: LoginRequest) => Promise<void>
  signup: (userData: SignupRequest) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
}
```

**주요 메서드:**
- `login()`: 로그인 후 자동으로 사용자 정보 가져오기
- `signup()`: 회원가입 후 자동 로그인
- `logout()`: 토큰 제거 후 로그인 페이지로 리다이렉트
- `refreshUser()`: 토큰 기반 사용자 정보 갱신

**HOC 제공:**
```typescript
withAuth(Component) // 인증이 필요한 페이지를 감싸는 HOC
```

### 6.2 Custom Hooks

#### `useMeetings` (`hooks/useMeetings.ts`)

**제공 기능:**
```typescript
interface UseMeetingsReturn {
  meetings: Meeting[]
  isLoading: boolean
  error: string | null
  stats: MeetingStats | null
  createMeeting: (data: CreateMeetingRequest) => Promise<Meeting>
  updateMeeting: (id: string, data: UpdateMeetingRequest) => Promise<Meeting>
  deleteMeeting: (id: string) => Promise<void>
  refreshMeetings: () => Promise<void>
  uploadAudioFile: (file: File) => Promise<Meeting>
  uploadAudioBlob: (blob: Blob, title: string) => Promise<Meeting>
}
```

**사용 예시:**
```typescript
const { meetings, uploadAudioBlob, isLoading } = useMeetings()

const handleRecordingComplete = async (blob: Blob) => {
  await uploadAudioBlob(blob, "회의 제목")
}
```

#### `useToast` (`hooks/use-toast.ts`)
- Toast 알림 표시 관리
- `toast()` 함수로 알림 트리거

#### `useMobile` (`hooks/use-mobile.ts`)
- 모바일 디바이스 감지
- 반응형 UI 조건부 렌더링

## 7. API 연동

### 7.1 API 클라이언트 (`lib/api.ts`)

**클래스 구조:**
```typescript
class ApiClient {
  // 토큰 관리
  setToken(accessToken: string, refreshToken?: string)
  clearToken()
  
  // HTTP 메서드
  get<T>(endpoint: string): Promise<ApiResponse<T>>
  post<T>(endpoint: string, data?: any): Promise<ApiResponse<T>>
  put<T>(endpoint: string, data?: any): Promise<ApiResponse<T>>
  delete<T>(endpoint: string): Promise<ApiResponse<T>>
  
  // 파일 업로드
  uploadFile<T>(endpoint: string, file: File): Promise<ApiResponse<T>>
  
  // AI 분석 요청
  requestAnalysis<T>(meetingId: string, filePath: string): Promise<ApiResponse<T>>
  
  // 닉네임 중복 확인
  checkNickname(nickname: string): Promise<{ available: boolean }>
}
```

**자동 토큰 주입:**
- 모든 요청에 `Authorization: Bearer {token}` 헤더 자동 추가
- localStorage에서 토큰 자동 로드

### 7.2 주요 API 엔드포인트

#### 인증 API
```typescript
POST /auth/signup          // 회원가입
POST /auth/login           // 로그인
POST /auth/logout          // 로그아웃
GET  /auth/me              // 내 정보 조회
GET  /auth/check-nickname  // 닉네임 중복 확인
```

#### 회의록 API
```typescript
GET    /meetings              // 회의록 목록 (페이지네이션)
POST   /meetings              // 회의록 생성 (파일 업로드)
GET    /meetings/:id          // 회의록 상세 조회
PUT    /meetings/:id          // 회의록 수정
DELETE /meetings/:id          // 회의록 삭제
```

#### AI 분석 API
```typescript
POST /ai/analyze  // AI 분석 요청
{
  meetingId: string
  filePath: string
}
```

### 7.3 Mock 서버 (`lib/mock-server.ts`)

**특징:**
- 실제 API와 동일한 인터페이스
- 메모리 기반 데이터 저장
- 자동 상태 전환 시뮬레이션:
  1. `uploaded` → 즉시
  2. `processing` → 3초 후
  3. `completed` → 10초 후

**테스트 계정:**
```
이메일: test@example.com
비밀번호: password123
```

## 8. 주요 기능

### 8.1 브라우저 음성 녹음

**기술 스택:**
- `MediaRecorder` API
- `getUserMedia()` API (마이크 권한)

**구현:**
```typescript
const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
const mediaRecorder = new MediaRecorder(stream)

mediaRecorder.ondataavailable = (e) => {
  chunks.push(e.data)
}

mediaRecorder.onstop = () => {
  const blob = new Blob(chunks, { type: "audio/webm" })
  onComplete(blob)
}

mediaRecorder.start()
```

**지원 포맷:**
- WebM (기본)
- WAV, MP3, M4A, OGG (업로드 시)

### 8.2 실시간 상태 폴링

**시나리오:**
1. 파일 업로드 → `uploaded` 상태
2. AI 분석 요청 → `processing` 상태
3. 주기적으로 상태 확인 (`setInterval`)
4. `completed` 상태 감지 시 결과 페이지 표시

**구현 예시:**
```typescript
useEffect(() => {
  const interval = setInterval(async () => {
    const response = await apiClient.get(`/meetings/${meetingId}`)
    if (response.data.status === 'completed') {
      clearInterval(interval)
      router.push(`/meetings/${meetingId}`)
    }
  }, 3000) // 3초마다 폴링

  return () => clearInterval(interval)
}, [meetingId])
```

### 8.3 파일 다운로드

**지원 포맷:**
- `.txt`: 원문 자막
- `.md`: Markdown 형식 회의록
- `.json`: 구조화된 JSON 데이터

**구현:**
```typescript
const handleDownload = (format: 'txt' | 'md' | 'json') => {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
```

### 8.4 인증 보호 (Protected Routes)

**방식 1: HOC 사용**
```typescript
export default withAuth(MeetingsPage)
```

**방식 2: useEffect 체크**
```typescript
useEffect(() => {
  const user = localStorage.getItem('user')
  if (!user) {
    router.push('/login')
  }
}, [])
```

### 8.5 토스트 알림

**사용 예시:**
```typescript
import { useToast } from '@/hooks/use-toast'

const { toast } = useToast()

toast({
  title: "업로드 완료",
  description: "회의록이 성공적으로 업로드되었습니다.",
  variant: "default"
})
```

## 9. 스타일링

### 9.1 Tailwind CSS 설정

**주요 유틸리티 클래스:**
```css
/* 그라데이션 버튼 */
.gradient-primary {
  background: linear-gradient(to right, hsl(var(--primary)), hsl(var(--primary)));
}

/* 카드 호버 효과 */
.card-hover {
  transition: all 0.2s;
}
.card-hover:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 25px rgba(0,0,0,0.1);
}
```

**커스텀 컬러:**
- CSS 변수 기반 (`--primary`, `--secondary`, etc.)
- 다크모드 자동 지원

### 9.2 반응형 디자인

**브레이크포인트:**
```css
sm: 640px   /* 모바일 */
md: 768px   /* 태블릿 */
lg: 1024px  /* 데스크톱 */
xl: 1280px  /* 대형 화면 */
```

**사용 예시:**
```jsx
<div className="grid grid-cols-1 md:grid-cols-3 gap-6">
  {/* 모바일: 1열, 데스크톱: 3열 */}
</div>
```

## 10. TypeScript 타입 정의

### 10.1 주요 타입 (`types/api.ts`)

```typescript
// 사용자
interface User {
  userId: string
  email: string
  nickname: string
  createdAt: string
}

// 회의록
interface Meeting {
  meetingId: string
  title: string
  date: string
  status: 'uploaded' | 'processing' | 'completed'
  summary?: string
  keywords?: string[]
  speakers?: Speaker[]
}

// 화자 정보
interface Speaker {
  speakerId: string
  segments: Segment[]
}

interface Segment {
  start: number  // 초 단위
  end: number
  text: string
}

// API 요청/응답
interface LoginRequest {
  email: string
  password: string
}

interface LoginResponse {
  id: string
  name: string
  email: string
  token: string
  createdAt: string
  updatedAt: string
}

interface ApiResponse<T> {
  data: T
  message?: string
  success: boolean
}

interface ApiError {
  message: string
  status: number
}
```

## 11. 빌드 및 배포

### 11.1 개발 서버

```bash
npm run dev
# 또는
pnpm dev

# 접속: http://localhost:3000
```

### 11.2 프로덕션 빌드

```bash
npm run build
npm start
```

**빌드 산출물:**
- 정적 파일: `.next/static/`
- 서버 컴포넌트: `.next/server/`

### 11.3 환경 변수 관리

**개발 환경:**
```bash
# .env.local
NEXT_PUBLIC_USE_MOCK=true
NEXT_PUBLIC_API_URL=http://localhost:8080/api
```

**프로덕션 환경:**
```bash
# Vercel 환경 변수 설정
NEXT_PUBLIC_USE_MOCK=false
NEXT_PUBLIC_API_URL=https://api.example.com/api
```

### 11.4 Docker 배포 (예정)

```dockerfile
# Dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./
RUN npm ci --production

EXPOSE 3000
CMD ["npm", "start"]
```

## 12. 성능 최적화

### 12.1 Next.js 최적화 기능

- **자동 코드 분할**: 페이지별 청크 생성
- **이미지 최적화**: `next/image` 사용 (`unoptimized: true` 설정)
- **폰트 최적화**: Google Fonts 자동 최적화
- **서버 컴포넌트**: 초기 로딩 속도 향상

### 12.2 성능 모니터링

- **Vercel Analytics**: 실시간 성능 추적
- **Core Web Vitals**: LCP, FID, CLS 측정

## 13. 보안

### 13.1 인증 토큰 관리

- JWT 토큰을 localStorage에 저장
- 모든 API 요청에 `Authorization` 헤더 자동 추가
- 토큰 만료 시 로그인 페이지로 리다이렉트

### 13.2 XSS 방지

- React의 자동 이스케이프 기능
- `dangerouslySetInnerHTML` 사용 금지

### 13.3 CSRF 방지

- Next.js API Routes는 기본적으로 CSRF 보호 제공

## 14. 개발 워크플로우

### 14.1 브랜치 전략

```
main          # 프로덕션 브랜치
develop       # 개발 브랜치
feature/*     # 기능 개발
bugfix/*      # 버그 수정
```

### 14.2 코드 스타일

- **Linter**: ESLint (Next.js 권장 설정)
- **포맷터**: Prettier (권장)
- **타입 체크**: TypeScript strict 모드

### 14.3 커밋 컨벤션

```
feat: 새로운 기능 추가
fix: 버그 수정
docs: 문서 수정
style: 코드 포맷팅
refactor: 코드 리팩토링
test: 테스트 코드
chore: 빌드 업무 수정
```

## 15. 향후 개선 사항

### 15.1 기능 추가
- [ ] 실시간 협업 기능 (WebSocket)
- [ ] 회의록 공유 기능
- [ ] 액션아이템 Kanban 보드
- [ ] 화자 별 스피커 라벨링
- [ ] 다국어 지원 (i18n)

### 15.2 성능 개선
- [ ] React Query 도입 (캐싱 최적화)
- [ ] Service Worker (오프라인 지원)
- [ ] Progressive Web App (PWA)

### 15.3 테스트
- [ ] Jest + React Testing Library
- [ ] E2E 테스트 (Playwright)
- [ ] 시각적 회귀 테스트 (Chromatic)

---

## 📚 참고 자료

- **Next.js 문서**: https://nextjs.org/docs
- **shadcn/ui**: https://ui.shadcn.com
- **Tailwind CSS**: https://tailwindcss.com/docs
- **React Hook Form**: https://react-hook-form.com
- **MediaRecorder API**: https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder

---

**작성일**: 2025년 10월 21일  
**프로젝트**: Meeting One-Line  
**버전**: 0.1.0









