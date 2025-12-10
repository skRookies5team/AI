"use client"

import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '@/lib/api'
import type { 
  Meeting, 
  CreateMeetingRequest, 
  UpdateMeetingRequest,
  PaginationParams,
  PaginatedResponse,
  MeetingStats 
} from '@/types/api'

interface UseMeetingsOptions {
  autoFetch?: boolean
  pagination?: PaginationParams
  keyword?: string
  title?: string
  summary?: string
  status?: string
}

interface UseMeetingsReturn {
  meetings: Meeting[]
  isLoading: boolean
  error: string | null
  stats: MeetingStats | null
  totalPages: number
  currentPage: number
  createMeeting: (data: CreateMeetingRequest) => Promise<Meeting>
  updateMeeting: (id: string, data: UpdateMeetingRequest) => Promise<Meeting>
  deleteMeeting: (id: string) => Promise<void>
  toggleFavorite: (id: string) => Promise<void>
  refreshMeetings: () => Promise<void>
  uploadAudioFile: (file: File) => Promise<Meeting>
  uploadAudioBlob: (blob: Blob, title: string) => Promise<Meeting>
}

export function useMeetings(options: UseMeetingsOptions = {}): UseMeetingsReturn {
  const { autoFetch = true, pagination, keyword, title, summary, status } = options
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [stats, setStats] = useState<MeetingStats | null>(null)
  const [totalPages, setTotalPages] = useState(0)
  const [currentPage, setCurrentPage] = useState(pagination?.page || 1)

  // 회의 목록 가져오기
  const fetchMeetings = useCallback(async () => {
    try {
      setIsLoading(true)
      setError(null)

      const params = new URLSearchParams()
      if (pagination?.page) params.append('page', pagination.page.toString())
      if (pagination?.limit) params.append('size', pagination.limit.toString())
      if (pagination?.sortBy) params.append('sortBy', pagination.sortBy)
      if (pagination?.sortOrder) params.append('sortOrder', pagination.sortOrder)
      
      // 검색 파라미터 추가
      if (keyword) params.append('keyword', keyword)
      if (title) params.append('title', title)
      if (summary) params.append('summary', summary)
      if (status) params.append('status', status)

      const response = await apiClient.get<{ content: Meeting[], page: number, size: number, totalPages: number }>(`/meetings${params.toString() ? '?' + params.toString() : ''}`)
      
      // 백엔드가 직접 { content, page, size, totalPages } 반환
      const data = (response as any).content ? response : (response as any).data
      
      // 데이터가 없으면 빈 배열로 처리
      if (!data || !data.content) {
        setMeetings([])
        setTotalPages(0)
        setCurrentPage(1)
        return
      }
      
      // localStorage에서 즐겨찾기 목록 가져오기
      const favoritesStr = typeof window !== 'undefined' ? localStorage.getItem('favorites') : null
      const favorites: string[] = favoritesStr ? JSON.parse(favoritesStr) : []
      
      // 회의 목록에 isFavorite 필드 추가
      const meetingsWithFavorites = data.content.map((meeting: Meeting) => ({
        ...meeting,
        isFavorite: meeting.meetingId ? favorites.includes(meeting.meetingId) : false
      }))
      
      setMeetings(meetingsWithFavorites)
      setTotalPages(data.totalPages || 0)
      setCurrentPage(data.page || pagination?.page || 1)
    } catch (err: any) {
      const errorMessage = err?.message || '회의 목록을 가져오는데 실패했습니다'
      setError(errorMessage)
      console.error('회의 목록 가져오기 실패:', err)
    } finally {
      setIsLoading(false)
    }
  }, [pagination?.page, pagination?.limit, pagination?.sortBy, pagination?.sortOrder, keyword, title, summary, status])

  // 통계 정보 계산 (meetings가 변경될 때마다 자동으로 업데이트)
  useEffect(() => {
    const totalMeetings = meetings.length
    const completedMeetings = meetings.filter((m: Meeting) => m.status === 'completed').length
    const processingMeetings = meetings.filter((m: Meeting) => m.status === 'processing').length
    const failedMeetings = meetings.filter((m: Meeting) => m.status === 'failed').length
    const totalDuration = meetings.reduce((sum: number, m: Meeting) => sum + (m.duration || 0), 0)
    const totalActionItems = meetings.reduce((sum: number, m: Meeting) => sum + (m.actionCount || 0), 0)

    setStats({
      totalMeetings,
      totalDuration,
      averageDuration: totalMeetings > 0 ? totalDuration / totalMeetings : 0,
      completedMeetings,
      processingMeetings,
      failedMeetings,
      totalActionItems,
      completedActionItems: 0,
    })
  }, [meetings])

  // 회의 생성 (파일 업로드)
  const createMeeting = useCallback(async (data: CreateMeetingRequest): Promise<Meeting> => {
    try {
      setError(null)

      const formData = new FormData()
      formData.append('title', data.title)
      formData.append('date', data.date)
      formData.append('file', data.file)

      const response = await apiClient.post<CreateMeetingResponse>('/meetings', formData)
      
      // 새로 생성된 회의 정보로 Meeting 객체 생성
      const newMeeting: Meeting = {
        meetingId: response.data.meetingId,
        title: data.title,
        date: data.date,
        status: response.data.status,
        createdAt: new Date().toISOString(),
      }
      
      setMeetings(prev => [newMeeting, ...prev])
      
      // AI 분석 요청 (백그라운드에서 실행)
      try {
        await apiClient.requestAnalysis<{ status: 'processing' }>(response.data.meetingId, '')
      } catch (analysisError) {
        console.warn('AI 분석 요청 실패:', analysisError)
        // 분석 요청 실패해도 회의 생성은 성공으로 처리
      }
      
      return newMeeting
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '회의 생성에 실패했습니다'
      setError(errorMessage)
      throw err
    }
  }, [])

  // 회의 업데이트
  const updateMeeting = useCallback(async (id: string, data: UpdateMeetingRequest): Promise<Meeting> => {
    try {
      setError(null)

      const response = await apiClient.put<UpdateMeetingResponse>(`/meetings/${id}`, data)
      
      // 업데이트된 회의 정보 가져오기
      const meetingResponse = await apiClient.get<Meeting>(`/meetings/${id}`)
      const updatedMeeting = meetingResponse.data
      
      setMeetings(prev => prev.map(m => m.meetingId === id ? updatedMeeting : m))
      return updatedMeeting
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '회의 업데이트에 실패했습니다'
      setError(errorMessage)
      throw err
    }
  }, [])

  // 회의 삭제
  const deleteMeeting = useCallback(async (id: string): Promise<void> => {
    try {
      setError(null)

      await apiClient.delete(`/meetings/${id}`)
      setMeetings(prev => prev.filter(m => m.meetingId !== id))
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '회의 삭제에 실패했습니다'
      setError(errorMessage)
      throw err
    }
  }, [])

  // 즐겨찾기 토글 (localStorage 사용)
  const toggleFavorite = useCallback(async (id: string): Promise<void> => {
    try {
      // localStorage에서 즐겨찾기 목록 가져오기
      const favoritesStr = localStorage.getItem('favorites')
      const favorites: string[] = favoritesStr ? JSON.parse(favoritesStr) : []
      
      let newFavorites: string[]
      let isFavorite: boolean
      
      if (favorites.includes(id)) {
        // 즐겨찾기 해제
        newFavorites = favorites.filter(fId => fId !== id)
        isFavorite = false
      } else {
        // 즐겨찾기 추가
        newFavorites = [...favorites, id]
        isFavorite = true
      }
      
      // localStorage에 저장
      localStorage.setItem('favorites', JSON.stringify(newFavorites))
      
      // 로컬 상태 업데이트
      setMeetings(prev => prev.map(m => 
        m.meetingId === id ? { ...m, isFavorite } : m
      ))
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '즐겨찾기 설정에 실패했습니다'
      setError(errorMessage)
      throw err
    }
  }, [])

  // 오디오 파일 업로드
  const uploadAudioFile = useCallback(async (file: File): Promise<Meeting> => {
    try {
      setError(null)

      const title = file.name.replace(/\.[^/.]+$/, "")
      const date = new Date().toISOString()
      
      return createMeeting({ 
        title, 
        date, 
        file 
      })
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '파일 업로드에 실패했습니다'
      setError(errorMessage)
      throw err
    }
  }, [createMeeting])

  // 오디오 블롭 업로드
  const uploadAudioBlob = useCallback(async (blob: Blob, title: string): Promise<Meeting> => {
    try {
      setError(null)

      // Blob을 File로 변환
      const file = new File([blob], `${title}.webm`, { type: blob.type })
      const date = new Date().toISOString()
      
      return createMeeting({ 
        title, 
        date, 
        file 
      })
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '오디오 업로드에 실패했습니다'
      setError(errorMessage)
      throw err
    }
  }, [createMeeting])

  // 데이터 새로고침
  const refreshMeetings = useCallback(async () => {
    await fetchMeetings()
  }, [fetchMeetings])

  // 초기 데이터 로드 및 pagination/검색 변경 시 데이터 로드
  useEffect(() => {
    if (autoFetch) {
      fetchMeetings()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFetch, pagination?.page, pagination?.limit, keyword, title, summary, status])

  return {
    meetings,
    isLoading,
    error,
    stats,
    totalPages,
    currentPage,
    createMeeting,
    updateMeeting,
    deleteMeeting,
    toggleFavorite,
    refreshMeetings,
    uploadAudioFile,
    uploadAudioBlob,
  }
}
