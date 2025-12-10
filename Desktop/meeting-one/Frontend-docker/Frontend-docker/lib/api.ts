// API 클라이언트 설정
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080/api'

// API 응답 타입
interface ApiResponse<T> {
  data: T
  message?: string
  success: boolean
}

// API 에러 타입
interface ApiError {
  message: string
  status: number
}

// HTTP 클라이언트 클래스
class ApiClient {
  private baseURL: string
  private token: string | null = null
  private isRefreshing: boolean = false
  private refreshSubscribers: Array<(token: string) => void> = []

  constructor(baseURL: string) {
    this.baseURL = baseURL
    this.token = typeof window !== 'undefined' ? localStorage.getItem('accessToken') : null
  }

  // 토큰 설정
  setToken(accessToken: string, refreshToken?: string) {
    this.token = accessToken
    if (typeof window !== 'undefined') {
      localStorage.setItem('accessToken', accessToken)
      if (refreshToken) {
        localStorage.setItem('refreshToken', refreshToken)
      }
    }
  }

  // 토큰 제거
  clearToken() {
    this.token = null
    if (typeof window !== 'undefined') {
      localStorage.removeItem('accessToken')
      localStorage.removeItem('refreshToken')
      localStorage.removeItem('user')
    }
  }

  // Refresh Token으로 새 Access Token 발급
  private async refreshAccessToken(): Promise<string | null> {
    try {
      const refreshToken = typeof window !== 'undefined' ? localStorage.getItem('refreshToken') : null
      
      if (!refreshToken) {
        console.warn('⚠️ Refresh Token이 없습니다')
        return null
      }

      console.log('🔄 Access Token 갱신 중...')
      
      // 백엔드 API: 헤더에 Refresh Token 포함
      const response = await fetch(`${this.baseURL}/auth/refresh`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${refreshToken}`,  // 헤더에 Refresh Token 포함
          'Content-Type': 'application/json',
        },
      })

      if (!response.ok) {
        console.error('❌ Token 갱신 실패:', response.status)
        return null
      }

      const data = await response.json()
      const newAccessToken = data.accessToken || data.data?.accessToken
      const newRefreshToken = data.refreshToken || data.data?.refreshToken
      
      if (newAccessToken) {
        console.log('✅ Access Token 갱신 완료')
        // 새 Access Token과 Refresh Token 모두 저장 (Refresh Token도 갱신되는 경우 대비)
        this.setToken(newAccessToken, newRefreshToken || refreshToken)
        return newAccessToken
      }

      return null
    } catch (error) {
      console.error('❌ Token 갱신 중 오류:', error)
      return null
    }
  }

  // Refresh 대기 중인 요청들을 처리
  private subscribeTokenRefresh(callback: (token: string) => void) {
    this.refreshSubscribers.push(callback)
  }

  private onTokenRefreshed(token: string) {
    this.refreshSubscribers.forEach(callback => callback(token))
    this.refreshSubscribers = []
  }

  // 기본 헤더 설정
  private getHeaders(): HeadersInit {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    }

    if (this.token) {
      headers.Authorization = `Bearer ${this.token}`
    }

    return headers
  }

  // HTTP 요청 메서드 (401 시 자동 토큰 갱신)
  private async request<T>(
    endpoint: string,
    options: RequestInit = {},
    retry: boolean = true
  ): Promise<ApiResponse<T>> {
    const url = `${this.baseURL}${endpoint}`
    
    const config: RequestInit = {
      ...options,
      headers: {
        ...this.getHeaders(),
        ...options.headers,
      },
    }

    try {
      const response = await fetch(url, config)
      
      // 401 에러 (Unauthorized) - 토큰 만료
      if (response.status === 401 && retry && !endpoint.includes('/auth/refresh')) {
        console.log('🔐 401 에러 감지 - 토큰 갱신 시도')
        
        // 이미 갱신 중이면 대기
        if (this.isRefreshing) {
          return new Promise((resolve, reject) => {
            this.subscribeTokenRefresh((newToken: string) => {
              // 새 토큰으로 재시도
              this.request<T>(endpoint, options, false)
                .then(resolve)
                .catch(reject)
            })
          })
        }

        this.isRefreshing = true

        // 토큰 갱신 시도
        const newToken = await this.refreshAccessToken()
        
        if (newToken) {
          this.isRefreshing = false
          this.onTokenRefreshed(newToken)
          
          // 새 토큰으로 원래 요청 재시도
          return this.request<T>(endpoint, options, false)
        } else {
          // 갱신 실패 - 로그아웃 처리
          this.isRefreshing = false
          this.clearToken()
          
          if (typeof window !== 'undefined') {
            window.location.href = '/login'
          }
          
          throw {
            message: '인증이 만료되었습니다. 다시 로그인해주세요.',
            status: 401,
          } as ApiError
        }
      }
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw {
          message: errorData.message || 'API 요청 실패',
          status: response.status,
        } as ApiError
      }

      const data = await response.json()
      return data
    } catch (error) {
      if ((error as ApiError).status) {
        throw error
      }
      
      if (error instanceof Error) {
        throw {
          message: error.message,
          status: 0,
        } as ApiError
      }
      throw error
    }
  }

  // GET 요청
  async get<T>(endpoint: string): Promise<ApiResponse<T>> {
    return this.request<T>(endpoint, { method: 'GET' })
  }

  // POST 요청
  async post<T>(endpoint: string, data?: any): Promise<ApiResponse<T>> {
    // FormData인 경우 그대로 전송
    if (data instanceof FormData) {
      const url = `${this.baseURL}${endpoint}`
      const headers: HeadersInit = {}
      
      if (this.token) {
        headers.Authorization = `Bearer ${this.token}`
      }

      try {
        const response = await fetch(url, {
          method: 'POST',
          headers,
          body: data,
        })

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}))
          throw {
            message: errorData.message || 'API 요청 실패',
            status: response.status,
          } as ApiError
        }

        const responseData = await response.json()
        return responseData
      } catch (error) {
        if (error instanceof Error) {
          throw {
            message: error.message,
            status: 0,
          } as ApiError
        }
        throw error
      }
    }
    
    return this.request<T>(endpoint, {
      method: 'POST',
      body: data ? JSON.stringify(data) : undefined,
    })
  }

  // PUT 요청
  async put<T>(endpoint: string, data?: any): Promise<ApiResponse<T>> {
    return this.request<T>(endpoint, {
      method: 'PUT',
      body: data ? JSON.stringify(data) : undefined,
    })
  }

  // DELETE 요청
  async delete<T>(endpoint: string): Promise<ApiResponse<T>> {
    return this.request<T>(endpoint, { method: 'DELETE' })
  }

  // 파일 업로드
  async uploadFile<T>(endpoint: string, file: File): Promise<ApiResponse<T>> {
    const formData = new FormData()
    formData.append('file', file)

    const url = `${this.baseURL}${endpoint}`
    const headers: HeadersInit = {}
    
    if (this.token) {
      headers.Authorization = `Bearer ${this.token}`
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: formData,
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw {
          message: errorData.message || '파일 업로드 실패',
          status: response.status,
        } as ApiError
      }

      const data = await response.json()
      return data
    } catch (error) {
      if (error instanceof Error) {
        throw {
          message: error.message,
          status: 0,
        } as ApiError
      }
      throw error
    }
  }

  // AI 분석 요청
  async requestAnalysis<T>(meetingId: string, filePath: string): Promise<ApiResponse<T>> {
    return this.post<T>('/ai/analyze', {
      meetingId,
      filePath
    })
  }

  // 닉네임 중복 확인
  async checkNickname(nickname: string): Promise<{ available: boolean }> {
    try {
      // 백엔드는 { isDuplicate: boolean } 형태로 직접 반환
      const response = await this.get<{ isDuplicate: boolean }>(`/auth/check-nickname?nickname=${encodeURIComponent(nickname)}`)
      
      // ApiResponse<T> 타입이므로 response는 전체 응답, response.data는 없을 수 있음
      // 백엔드가 직접 { isDuplicate: boolean } 반환하는 경우
      const isDuplicate = (response as any).isDuplicate
      
      // 백엔드 응답을 프론트엔드 형식으로 변환
      return { available: !isDuplicate }
    } catch (error) {
      console.error('닉네임 확인 중 오류:', error)
      throw error
    }
  }

  // 회의 분석 진행 상태 조회 (Polling)
  async checkMeetingStatus(): Promise<any> {
    try {
      // Next.js API 라우트를 통해 백엔드 API 호출
      const url = '/api/meetings/check'
      const headers: HeadersInit = {
        'Content-Type': 'application/json',
      }
      
      if (this.token) {
        headers.Authorization = `Bearer ${this.token}`
      }

      const response = await fetch(url, {
        method: 'GET',
        headers,
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw {
          message: errorData.message || 'API 요청 실패',
          status: response.status,
        } as ApiError
      }

      const data = await response.json()
      return data
    } catch (error) {
      console.error('회의 분석 상태 조회 중 오류:', error)
      throw error
    }
  }
}

// API 클라이언트 인스턴스 생성 (실제 백엔드 서버)
export const apiClient = new ApiClient(API_BASE_URL)

// API 에러 타입 export
export type { ApiError, ApiResponse }
