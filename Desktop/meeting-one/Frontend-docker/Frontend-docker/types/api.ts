// 사용자 관련 타입 (API 스펙에 맞게 수정)
export interface User {
  userId: string
  email: string
  nickname: string
  createdAt: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface LoginResponse {
  accessToken: string
  refreshToken?: string  // 백엔드가 refreshToken 추가함
}

export interface SignupRequest {
  email: string
  password: string
  nickname: string
}

export interface SignupResponse {
  accessToken: string  // 백엔드는 회원가입 시 바로 토큰 반환
}

export interface Me {
  userId: string
  email: string
  nickname: string
  createdAt: string
}

// 회의 관련 타입 (API 스펙에 맞게 수정)
export interface Meeting {
  meetingId: string
  title: string
  date?: string  // DetailResponse에만 있음
  status: 'UPLOADED' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'uploaded' | 'processing' | 'completed' | 'failed'  // 대소문자 둘 다 허용
  summary?: string
  keywords?: string[]  // DetailResponse에만 있음
  speakers?: Speaker[]  // DetailResponse에만 있음
  filePath?: string  // DetailResponse에만 있음
  isFavorite?: boolean  // 즐겨찾기 여부
  createdAt: string
}

export interface Speaker {
  speakerId: string
  name?: string  // 화자 이름 (사용자가 지정)
  segments: Segment[]
}

export interface Segment {
  start: number
  end: number
  text: string
}

export interface CreateMeetingRequest {
  title: string
  date: string
  file: File
}

export interface CreateMeetingResponse {
  meetingId: string
  status: 'UPLOADED' | 'uploaded'  // 백엔드는 대문자로 반환
  message: string
}

export interface UpdateMeetingRequest {
  title?: string
  summary?: string
  keywords?: string[]
  speakers?: Speaker[]  // 화자 정보 수정
}

export interface UpdateMeetingResponse {
  message: string
}

// API 응답 타입
export interface ApiResponse<T> {
  data: T
  message?: string
  success: boolean
}

export interface ApiError {
  message: string
  status: number
}

// 페이지네이션 타입
export interface PaginationParams {
  page?: number
  limit?: number
  sortBy?: string
  sortOrder?: 'asc' | 'desc'
}

export interface PaginatedResponse<T> {
  data: T[]
  pagination: {
    page: number
    limit: number
    total: number
    totalPages: number
  }
}

// 파일 업로드 타입
export interface FileUploadResponse {
  fileUrl: string
  fileName: string
  fileSize: number
  mimeType: string
}

// 녹음 관련 타입
export interface RecordingData {
  blob: Blob
  duration: number
  mimeType: string
}

// 회의 분석 결과 타입
export interface MeetingAnalysis {
  summary: string
  actionItems: ActionItem[]
  keyPoints: string[]
  participants: string[]
  sentiment: 'positive' | 'neutral' | 'negative'
  confidence: number
}

export interface ActionItem {
  id: string
  description: string
  assignee?: string
  dueDate?: string
  priority: 'low' | 'medium' | 'high'
  status: 'pending' | 'in_progress' | 'completed'
}

// 통계 타입
export interface MeetingStats {
  totalMeetings: number
  totalDuration: number
  averageDuration: number
  completedMeetings: number
  processingMeetings: number
  failedMeetings: number
  totalActionItems: number
  completedActionItems: number
}

// AI 분석 관련 타입
export interface AnalyzeRequest {
  meetingId: string
  filePath: string
}

export interface AnalyzeAccepted {
  status: 'processing'
}

export interface AiCallbackPayload {
  status: 'completed'
  summary: string
  keywords: string[]
  speakers: Speaker[]
}

// AI 피드백 타입 (5.1 피드백 조회 API)
export interface FeedbackActionItem {
  name: string
  content: string
  orderIndex: number
}

export interface FeedbackTopic {
  title: string
  importance: '높음' | '중간' | '낮음'
  summary: string
  proportion: number
}

export interface FeedbackQuestion {
  question: string
  orderIndex: number
}

export interface FeedbackFollowUpCategory {
  category: string
  questions: FeedbackQuestion[]
}

export interface MeetingFeedback {
  meetingId: string
  actionItems: FeedbackActionItem[]
  topics: FeedbackTopic[]
  followUpCategories: FeedbackFollowUpCategory[]
}

// 회의 분석 진행 상태 조회 (Polling)
export interface PendingMeeting {
  meetingId: string
  title: string
  status: 'UPLOADED' | 'PROCESSING'
  createdAt: string
}

export interface CheckMeetingStatusResponse {
  completedMeetings: PendingMeeting[]
}
