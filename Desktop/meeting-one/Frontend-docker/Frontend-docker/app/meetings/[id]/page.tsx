"use client"

import { useState, useEffect } from "react"
import { useRouter, useParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Download, ArrowLeft, CheckCircle2, Clock, User, Mic, LogOut, Edit, Save, X, Star, Trash2, Tag, HelpCircle } from "lucide-react"
import { useAuth } from "@/contexts/AuthContext"
import { apiClient } from "@/lib/api"
import { useMeetings } from "@/hooks/useMeetings"
import type { Meeting, UpdateMeetingRequest, MeetingFeedback } from "@/types/api"

export default function MeetingDetailPage() {
  const router = useRouter()
  const params = useParams()
  const { user, logout, isLoading: authLoading } = useAuth()
  const { toggleFavorite, deleteMeeting } = useMeetings({ autoFetch: false })
  const [meeting, setMeeting] = useState<Meeting | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isEditing, setIsEditing] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [editedTitle, setEditedTitle] = useState("")
  const [editedSummary, setEditedSummary] = useState("")
  const [editedKeywords, setEditedKeywords] = useState("")
  
  // 화자 및 원문 편집 상태
  const [editedSpeakers, setEditedSpeakers] = useState<Meeting['speakers']>([])
  const [editingSpeakerId, setEditingSpeakerId] = useState<string | null>(null)
  const [editingSegmentKey, setEditingSegmentKey] = useState<string | null>(null)

  // AI 피드백 상태
  const [feedback, setFeedback] = useState<MeetingFeedback | null>(null)
  const [isFeedbackLoading, setIsFeedbackLoading] = useState(false)
  const [feedbackError, setFeedbackError] = useState<string | null>(null)

  // Polling 상태
  const [isPolling, setIsPolling] = useState(false)

  // 인증 체크 - user가 없고 로딩이 끝나면 로그인 페이지로
  useEffect(() => {
    if (!authLoading && !user) {
      console.log('🔒 인증되지 않은 사용자. 로그인 페이지로 이동')
      router.push('/login')
    }
  }, [authLoading, user, router])

  // 피드백 데이터 가져오기
  const fetchFeedback = async () => {
    if (!params.id) return
    
    try {
      setIsFeedbackLoading(true)
      setFeedbackError(null)
      
      console.log('🔍 피드백 요청 시작:', `/meetings/${params.id}/feedback`)
      const response = await apiClient.get<MeetingFeedback>(`/meetings/${params.id}/feedback`)
      const feedbackData = (response as any).data || response
      
      console.log('✅ AI 피드백 데이터 수신:', feedbackData)
      console.log('  - 액션아이템:', feedbackData?.actionItems?.length || 0)
      console.log('  - 주제:', feedbackData?.topics?.length || 0)
      console.log('  - 후속질문 카테고리:', feedbackData?.followUpCategories?.length || 0)
      
      setFeedback(feedbackData)
    } catch (error: any) {
      console.error('❌ 피드백 조회 실패:')
      console.error('  - Status:', error.status)
      console.error('  - Message:', error.message)
      console.error('  - Full error:', error)
      
      // 모든 에러를 조용히 처리 (피드백은 선택적 기능)
      // 피드백이 아직 생성되지 않았거나 API가 준비되지 않은 경우
      setFeedbackError(null) // 에러 메시지 표시하지 않음
      setFeedback(null)
    } finally {
      setIsFeedbackLoading(false)
    }
  }

  useEffect(() => {
    const fetchMeeting = async () => {
      // 인증이 완료될 때까지 대기
      if (authLoading) {
        console.log('⏳ 인증 로딩 중... API 요청 대기')
        return
      }

      // 사용자가 없으면 대기 (AuthContext에서 리다이렉트 처리)
      if (!user) {
        console.log('❌ 사용자 정보 없음. 대기 중...')
        return
      }

      try {
        console.log('🔐 인증 완료! 회의록 데이터 요청 시작')
        const response = await apiClient.get<Meeting>(`/meetings/${params.id}`)
        // 백엔드 응답 구조 확인: response.data 또는 response 직접 사용
        const meetingData = (response as any).data || response
        
        // localStorage에서 즐겨찾기 상태 확인
        const favoritesStr = localStorage.getItem('favorites')
        const favorites: string[] = favoritesStr ? JSON.parse(favoritesStr) : []
        const isFavorite = favorites.includes(params.id as string)
        
        console.log('📥 회의록 데이터:', meetingData)
        console.log('👥 화자 정보:', meetingData.speakers)
        console.log('📊 회의 상태:', meetingData.status, '(타입:', typeof meetingData.status, ')')
        
        setMeeting({ ...meetingData, isFavorite })
        // 편집 필드 초기화
        setEditedTitle(meetingData.title || "")
        setEditedSummary(meetingData.summary || "")
        setEditedKeywords(meetingData.keywords?.join(", ") || "")
        setEditedSpeakers(meetingData.speakers || [])  // 화자 정보 초기화

        // 회의가 완료 상태이거나 실패 상태이면 피드백 시도 (API 테스트용)
        const statusUpper = meetingData.status?.toUpperCase()
        console.log('🔍 상태 체크:', statusUpper)
        
        if (statusUpper === 'COMPLETED' || statusUpper === 'FAILED') {
          console.log('✅ 피드백 요청 가능 상태! (상태:', meetingData.status, ')')
          fetchFeedback().catch(error => {
            // 피드백 조회 실패는 무시 (회의 상세는 정상 표시)
            console.log('피드백 조회 실패, 계속 진행:', error.message)
          })
        } else {
          console.log('⏳ UPLOADED/PROCESSING 상태. 현재 상태:', meetingData.status)
        }
      } catch (error) {
        console.error('회의록 상세 정보 가져오기 실패:', error)
      } finally {
        setIsLoading(false)
      }
    }

    if (params.id) {
      fetchMeeting()
    }
  }, [params.id, authLoading, user])

  // Polling: AI 분석 완료 체크
  useEffect(() => {
    if (!meeting || !params.id) return

    const status = meeting.status.toUpperCase()
    
    // UPLOADED 또는 PROCESSING 상태일 때만 polling 시작
    // COMPLETED나 FAILED는 이미 최종 상태이므로 polling 불필요
    if (status !== 'UPLOADED' && status !== 'PROCESSING') {
      console.log('🛑 Polling 불필요 (최종 상태):', status)
      return
    }

    console.log('🔄 Polling 시작:', meeting.title, status)
    setIsPolling(true)

    const pollingInterval = setInterval(async () => {
      try {
        console.log('📡 분석 상태 확인 중...')
        const response = await apiClient.checkMeetingStatus()
        const data = response.completedMeetings || []
        
        console.log('📊 미완료 회의 목록:', data)

        // 현재 회의가 목록에 없으면 분석 완료된 것
        const isStillPending = data.some((m: any) => m.meetingId === params.id)
        
        if (!isStillPending) {
          console.log('✅ 분석 완료! 회의 정보 다시 가져오는 중...')
          clearInterval(pollingInterval)
          setIsPolling(false)
          
          // 회의 정보 다시 가져오기
          const meetingResponse = await apiClient.get<Meeting>(`/meetings/${params.id}`)
          const meetingData = (meetingResponse as any).data || meetingResponse
          
          // localStorage에서 즐겨찾기 상태 확인
          const favoritesStr = localStorage.getItem('favorites')
          const favorites: string[] = favoritesStr ? JSON.parse(favoritesStr) : []
          const isFavorite = favorites.includes(params.id as string)
          
          setMeeting({ ...meetingData, isFavorite })
          setEditedTitle(meetingData.title || "")
          setEditedSummary(meetingData.summary || "")
          setEditedKeywords(meetingData.keywords?.join(", ") || "")
          setEditedSpeakers(meetingData.speakers || [])

          // 피드백도 가져오기 (API 테스트용)
          const statusUpper = meetingData.status?.toUpperCase()
          console.log('🔄 Polling 후 회의 상태:', meetingData.status, '→', statusUpper)
          
          if (statusUpper === 'COMPLETED' || statusUpper === 'FAILED') {
            console.log('✅ 피드백 요청 가능 상태! (Polling 완료, 상태:', meetingData.status, ')')
            fetchFeedback().catch(error => {
              console.log('피드백 조회 실패, 계속 진행:', error.message)
            })
          } else {
            console.log('⚠️ Polling 완료했지만 피드백 요청 안 함. 상태:', meetingData.status)
          }

          // 상태에 따른 알림
          if (statusUpper === 'COMPLETED') {
            alert('✅ 회의 분석이 완료되었습니다!')
          } else if (statusUpper === 'FAILED') {
            alert('⚠️ 회의 분석에 실패했습니다. 서버 로그를 확인해주세요.')
          }
        }
      } catch (error) {
        console.error('❌ Polling 오류:', error)
      }
    }, 5000) // 5초마다 체크

    // Cleanup
    return () => {
      console.log('🛑 Polling 중지')
      clearInterval(pollingInterval)
      setIsPolling(false)
    }
  }, [meeting, params.id])

  const handleLogout = () => {
    logout()
  }

  const handleToggleFavorite = async () => {
    if (!meeting || !params.id) return
    try {
      // localStorage에서 즐겨찾기 목록 가져오기
      const favoritesStr = localStorage.getItem('favorites')
      const favorites: string[] = favoritesStr ? JSON.parse(favoritesStr) : []
      
      let newFavorites: string[]
      let isFavorite: boolean
      
      if (favorites.includes(params.id as string)) {
        // 즐겨찾기 해제
        newFavorites = favorites.filter(id => id !== params.id)
        isFavorite = false
      } else {
        // 즐겨찾기 추가
        newFavorites = [...favorites, params.id as string]
        isFavorite = true
      }
      
      // localStorage에 저장
      localStorage.setItem('favorites', JSON.stringify(newFavorites))
      
      // 로컬 상태 업데이트
      setMeeting(prev => prev ? { ...prev, isFavorite } : null)
    } catch (error) {
      console.error('즐겨찾기 설정 실패:', error)
    }
  }

  const handleDelete = async () => {
    if (!meeting || !params.id) return
    
    const confirmed = window.confirm(
      `"${meeting.title}" 회의록을 삭제하시겠습니까?\n\n삭제된 회의록은 복구할 수 없습니다.`
    )
    
    if (!confirmed) return
    
    try {
      setIsDeleting(true)
      await deleteMeeting(params.id as string)
      alert('✅ 회의록이 삭제되었습니다.')
      router.push('/meetings')
    } catch (error: any) {
      console.error('회의록 삭제 중 오류:', error)
      alert(`❌ 회의록 삭제 실패\n\n${error.message || '알 수 없는 오류가 발생했습니다.'}`)
    } finally {
      setIsDeleting(false)
    }
  }

  const handleEditStart = () => {
    setIsEditing(true)
  }

  const handleEditCancel = () => {
    setIsEditing(false)
    setEditingSpeakerId(null)
    setEditingSegmentKey(null)
    // 원래 값으로 복원
    if (meeting) {
      setEditedTitle(meeting.title)
      setEditedSummary(meeting.summary || "")
      setEditedKeywords(meeting.keywords?.join(", ") || "")
      setEditedSpeakers(meeting.speakers || [])
    }
  }

  const handleEditSave = async () => {
    if (!meeting || !params.id) return

    try {
      setIsSaving(true)

      const updateData: UpdateMeetingRequest = {
        title: editedTitle.trim(),
        summary: editedSummary.trim(),
        keywords: editedKeywords.split(",").map(k => k.trim()).filter(k => k.length > 0),
        speakers: editedSpeakers  // 화자 정보 포함
      }

      console.log('📤 회의록 수정 요청:', updateData)

      await apiClient.put(`/meetings/${params.id}`, updateData)
      
      // 업데이트된 회의 정보 다시 가져오기
      const meetingResponse = await apiClient.get<Meeting>(`/meetings/${params.id}`)
      const meetingData = (meetingResponse as any).data || meetingResponse
        setMeeting(meetingData)
        setEditedTitle(meetingData.title || "")
        setEditedSummary(meetingData.summary || "")
        setEditedKeywords(meetingData.keywords?.join(", ") || "")
        setEditedSpeakers(meetingData.speakers || [])
      
      setIsEditing(false)
      setEditingSpeakerId(null)
      setEditingSegmentKey(null)
      alert("✅ 회의록이 성공적으로 수정되었습니다!")
    } catch (error: any) {
      console.error('회의록 수정 중 오류:', error)
      alert(`❌ 회의록 수정 실패\n\n${error.message || '알 수 없는 오류가 발생했습니다.'}`)
    } finally {
      setIsSaving(false)
    }
  }

  // 화자 이름 수정
  const handleSpeakerNameChange = (speakerId: string, newName: string) => {
    setEditedSpeakers(prev => 
      (prev || []).map(speaker => 
        speaker.speakerId === speakerId 
          ? { ...speaker, name: newName }
          : speaker
      )
    )
  }

  // segment text 수정
  const handleSegmentTextChange = (speakerId: string, segmentIndex: number, newText: string) => {
    setEditedSpeakers(prev =>
      (prev || []).map(speaker =>
        speaker.speakerId === speakerId
          ? {
              ...speaker,
              segments: speaker.segments.map((seg, idx) =>
                idx === segmentIndex ? { ...seg, text: newText } : seg
              )
            }
          : speaker
      )
    )
  }

  const handleDownload = (format: "txt" | "md" | "json" | "pdf") => {
    if (!meeting) {
      alert("❌ 회의록 데이터가 없습니다")
      return
    }

    let content = ""
    let filename = ""
    let mimeType = ""

    // 원문 텍스트 생성
    const generateTranscript = () => {
      if (!meeting.speakers || meeting.speakers.length === 0) {
        return "원문 데이터가 없습니다."
      }
      
      return meeting.speakers
        .map(speaker => {
          const speakerName = speaker.name || `화자 ${speaker.speakerId}`
          return speaker.segments
            .map(seg => {
              const minutes = Math.floor(seg.start / 60)
              const seconds = Math.floor(seg.start % 60)
              return `[${minutes}:${seconds.toString().padStart(2, '0')}] ${speakerName}: ${seg.text}`
            })
            .join('\n')
        })
        .join('\n\n')
    }

    switch (format) {
      case "txt":
        content = generateTranscript()
        filename = `${meeting.title}_원문.txt`
        mimeType = "text/plain"
        break
      case "md":
        const keywords = meeting.keywords?.length ? `## 키워드\n${meeting.keywords.map(k => `- ${k}`).join('\n')}\n\n` : ''
        const dateStr = meeting.date ? new Date(meeting.date).toLocaleDateString('ko-KR') : '날짜 정보 없음'
        content = `# ${meeting.title}\n\n**날짜:** ${dateStr}\n\n## 요약\n${meeting.summary || '요약 정보가 없습니다.'}\n\n${keywords}## 원문\n${generateTranscript()}`
        filename = `${meeting.title}.md`
        mimeType = "text/markdown"
        break
      case "json":
        content = JSON.stringify(meeting, null, 2)
        filename = `${meeting.title}_전체데이터.json`
        mimeType = "application/json"
        break
      case "pdf":
        handleDownloadPDF()
        return
    }

    const blob = new Blob([content], { type: mimeType })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleDownloadPDF = () => {
    if (!meeting) return
    
    // 브라우저 인쇄 기능 사용 (한글 지원)
    window.print()
  }

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case "HIGH":
        return "destructive"
      case "MEDIUM":
        return "default"
      case "LOW":
        return "secondary"
      default:
        return "default"
    }
  }

  const getRelevanceColor = (relevance: string) => {
    switch (relevance) {
      case "HIGH":
        return "destructive"
      case "MEDIUM":
        return "default"
      case "LOW":
        return "secondary"
      default:
        return "default"
    }
  }

  const getCategoryColor = (category: string) => {
    const colors: { [key: string]: string } = {
      기술: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
      비즈니스: "bg-green-500/10 text-green-700 dark:text-green-400",
      디자인: "bg-purple-500/10 text-purple-700 dark:text-purple-400",
      QA: "bg-orange-500/10 text-orange-700 dark:text-orange-400",
      일정: "bg-pink-500/10 text-pink-700 dark:text-pink-400",
    }
    return colors[category] || "bg-gray-500/10 text-gray-700 dark:text-gray-400"
  }

  const getImportanceColor = (importance: '높음' | '중간' | '낮음') => {
    switch (importance) {
      case '높음':
        return 'destructive'
      case '중간':
        return 'default'
      case '낮음':
        return 'secondary'
      default:
        return 'default'
    }
  }

  // 인증 로딩 중
  if (authLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="animate-spin rounded-full h-32 w-32 border-b-2 border-primary mx-auto"></div>
          <p className="text-muted-foreground">인증 확인 중...</p>
        </div>
      </div>
    )
  }

  // 사용자 정보 없음
  if (!user) {
    return null
  }

  // 회의록 로딩 중
  if (isLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="animate-spin rounded-full h-32 w-32 border-b-2 border-primary mx-auto"></div>
          <p className="text-muted-foreground">회의록을 불러오는 중...</p>
        </div>
      </div>
    )
  }

  if (!meeting) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-4">
          <h2 className="text-2xl font-bold">회의록을 찾을 수 없습니다</h2>
          <p className="text-muted-foreground">요청하신 회의록이 존재하지 않습니다.</p>
          <Button onClick={() => router.push("/meetings")}>
            목록으로 돌아가기
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background print:bg-white">
      {/* Header */}
      <header className="border-b border-border bg-card sticky top-0 z-50 no-print">
        <div className="container mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3 cursor-pointer" onClick={() => router.push("/")}>
            <div className="w-10 h-10 rounded-lg bg-primary flex items-center justify-center">
              <Mic className="w-5 h-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-balance">Meeting One-Line</h1>
              <p className="text-xs text-muted-foreground">버튼 한 번으로 회의 → 액션아이템</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right hidden sm:block">
              <p className="text-sm font-medium">{user.nickname || user.email}</p>
              <p className="text-xs text-muted-foreground">{user.email}</p>
            </div>
            <Button variant="outline" size="sm" onClick={handleLogout}>
              <LogOut className="w-4 h-4 mr-2" />
              로그아웃
            </Button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-4 py-8">
        <div className="max-w-5xl mx-auto space-y-6">
          {/* Back Button */}
          <Button variant="ghost" onClick={() => router.push("/meetings")}>
            <ArrowLeft className="w-4 h-4 mr-2" />
            목록으로 돌아가기
          </Button>

          {/* Polling 상태 배너 */}
          {isPolling && (
            <Card className="p-4 bg-amber-50 dark:bg-amber-950 border-amber-200 dark:border-amber-800">
              <div className="flex items-center gap-3">
                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-amber-600"></div>
                <div className="flex-1">
                  <p className="font-semibold text-amber-900 dark:text-amber-100">
                    🤖 AI 분석 진행 중
                  </p>
                  <p className="text-sm text-amber-700 dark:text-amber-300">
                    회의 내용을 분석하고 있습니다. 완료되면 자동으로 업데이트됩니다. (5초마다 자동 확인)
                  </p>
                </div>
              </div>
            </Card>
          )}

          {/* 분석 실패 배너 */}
          {meeting && meeting.status.toUpperCase() === 'FAILED' && (
            <Card className="p-4 bg-red-50 dark:bg-red-950 border-red-200 dark:border-red-800">
              <div className="flex items-center gap-3">
                <div className="w-5 h-5 flex items-center justify-center">
                  <span className="text-red-600 text-xl">⚠️</span>
                </div>
                <div className="flex-1">
                  <p className="font-semibold text-red-900 dark:text-red-100">
                    AI 분석 실패
                  </p>
                  <p className="text-sm text-red-700 dark:text-red-300">
                    회의 내용 분석 중 오류가 발생했습니다. 백엔드 서버 로그를 확인해주세요.
                  </p>
                </div>
              </div>
            </Card>
          )}

          {/* 디버깅: 피드백 수동 요청 버튼 */}
          {process.env.NODE_ENV === 'development' && (
            <Card className="p-4 bg-blue-50 dark:bg-blue-950 border-blue-200 dark:border-blue-800">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-semibold text-blue-900 dark:text-blue-100">
                    🔧 디버깅 모드
                  </p>
                  <p className="text-sm text-blue-700 dark:text-blue-300">
                    회의 상태: <strong>{meeting?.status}</strong>
                  </p>
                </div>
                <Button 
                  variant="outline" 
                  size="sm"
                  onClick={() => {
                    console.log('🔧 수동 피드백 요청')
                    fetchFeedback()
                  }}
                  disabled={isFeedbackLoading}
                >
                  {isFeedbackLoading ? '요청 중...' : '피드백 수동 요청'}
                </Button>
              </div>
            </Card>
          )}

          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex-1">
              {isEditing ? (
                <Input
                  value={editedTitle}
                  onChange={(e) => setEditedTitle(e.target.value)}
                  className="text-3xl font-bold h-auto py-2 mb-2"
                  placeholder="회의 제목"
                />
              ) : (
                <h2 className="text-3xl font-bold">{meeting.title}</h2>
              )}
              <p className="text-muted-foreground mt-1">
                {meeting.date && new Date(meeting.date).toLocaleDateString("ko-KR", {
                  year: "numeric",
                  month: "long",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </p>
            </div>
            <div className="flex gap-2 no-print">
              {isEditing ? (
                <>
                  <Button 
                    variant="outline" 
                    onClick={handleEditCancel}
                    disabled={isSaving}
                  >
                    <X className="w-4 h-4 mr-2" />
                    취소
                  </Button>
                  <Button 
                    onClick={handleEditSave}
                    disabled={isSaving}
                  >
                    <Save className="w-4 h-4 mr-2" />
                    {isSaving ? "저장 중..." : "저장"}
                  </Button>
                </>
              ) : (
                <>
                  <Button 
                    variant="outline" 
                    onClick={handleToggleFavorite}
                    className={meeting.isFavorite ? "border-yellow-400" : ""}
                  >
                    <Star className={`w-4 h-4 mr-2 ${meeting.isFavorite ? 'fill-yellow-400 text-yellow-400' : ''}`} />
                    {meeting.isFavorite ? "즐겨찾기 해제" : "즐겨찾기"}
                  </Button>
                  <Button variant="outline" onClick={handleEditStart}>
                    <Edit className="w-4 h-4 mr-2" />
                    수정
                  </Button>
                  <Button 
                    variant="outline" 
                    onClick={handleDelete}
                    disabled={isDeleting}
                    className="border-red-200 text-red-600 hover:bg-red-50"
                  >
                    <Trash2 className="w-4 h-4 mr-2" />
                    {isDeleting ? "삭제 중..." : "삭제"}
                  </Button>
                  <Button variant="outline" onClick={() => handleDownload("txt")}>
                    <Download className="w-4 h-4 mr-2" />
                    .txt
                  </Button>
                  <Button variant="outline" onClick={() => handleDownload("md")}>
                    <Download className="w-4 h-4 mr-2" />
                    .md
                  </Button>
                  <Button variant="outline" onClick={() => handleDownload("json")}>
                    <Download className="w-4 h-4 mr-2" />
                    .json
                  </Button>
                  <Button variant="outline" onClick={() => handleDownload("pdf")} className="border-primary text-primary" title="브라우저 인쇄 기능을 사용합니다">
                    <Download className="w-4 h-4 mr-2" />
                    PDF 출력
                  </Button>
                </>
              )}
            </div>
          </div>

          {/* Summary Card */}
          <Card className="p-6">
            <h3 className="text-lg font-semibold mb-3">회의 요약</h3>
            {isEditing ? (
              <Textarea
                value={editedSummary}
                onChange={(e) => setEditedSummary(e.target.value)}
                className="min-h-[120px] text-base"
                placeholder="회의 요약을 입력하세요"
              />
            ) : (
              <p className="text-muted-foreground leading-relaxed text-pretty">
                {meeting.summary || "아직 분석이 완료되지 않았습니다."}
              </p>
            )}
          </Card>

          {/* Keywords Card (편집 모드용) */}
          {(isEditing || (meeting.keywords && meeting.keywords.length > 0)) && (
            <Card className="p-6">
              <h3 className="text-lg font-semibold mb-3">키워드</h3>
              {isEditing ? (
                <div className="space-y-2">
                  <Input
                    value={editedKeywords}
                    onChange={(e) => setEditedKeywords(e.target.value)}
                    placeholder="키워드를 쉼표로 구분하여 입력하세요 (예: 프로젝트, 일정, 마감일)"
                  />
                  <p className="text-xs text-muted-foreground">
                    쉼표(,)로 구분하여 입력해주세요
                  </p>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {meeting.keywords?.map((keyword, index) => (
                    <Badge key={index} variant="secondary">
                      {keyword}
                    </Badge>
                  ))}
                </div>
              )}
            </Card>
          )}

          {/* Tabs */}
          <Tabs defaultValue="actions" className="space-y-4">
            <TabsList className="grid w-full grid-cols-2 md:grid-cols-4">
              <TabsTrigger value="actions">액션아이템</TabsTrigger>
              <TabsTrigger value="topics">주제 분류</TabsTrigger>
              <TabsTrigger value="questions">후속 질문</TabsTrigger>
              <TabsTrigger value="transcript">원문</TabsTrigger>
            </TabsList>

            <TabsContent value="actions" className="space-y-4">
              {isFeedbackLoading ? (
                <Card className="p-6 text-center">
                  <div className="flex items-center justify-center gap-2">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary"></div>
                    <p className="text-muted-foreground">액션아이템을 불러오는 중...</p>
                  </div>
                </Card>
              ) : feedbackError ? (
                <Card className="p-6 text-center">
                  <p className="text-destructive">❌ {feedbackError}</p>
                </Card>
              ) : feedback && feedback.actionItems.length > 0 ? (
                <div className="grid gap-4">
                  {feedback.actionItems
                    .sort((a, b) => a.orderIndex - b.orderIndex)
                    .map((item, index) => (
                      <Card key={index} className="p-6">
                        <div className="flex items-start gap-4">
                          <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                            <CheckCircle2 className="w-5 h-5 text-primary" />
                          </div>
                          <div className="flex-1 space-y-2">
                            <div className="flex items-center gap-3">
                              <Badge variant="outline" className="text-xs">
                                #{item.orderIndex}
                              </Badge>
                              {item.name && (
                                <div className="flex items-center gap-1 text-sm text-muted-foreground">
                                  <User className="w-4 h-4" />
                                  <span>{item.name}</span>
                                </div>
                              )}
                            </div>
                            <p className="text-base leading-relaxed">{item.content}</p>
                          </div>
                        </div>
                      </Card>
                    ))}
                </div>
              ) : (
                <Card className="p-6 text-center">
                  <p className="text-muted-foreground">아직 액션아이템이 생성되지 않았습니다.</p>
                </Card>
              )}
            </TabsContent>

            <TabsContent value="topics" className="space-y-4">
              {isFeedbackLoading ? (
                <Card className="p-6 text-center">
                  <div className="flex items-center justify-center gap-2">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary"></div>
                    <p className="text-muted-foreground">주제를 분석하는 중...</p>
                  </div>
                </Card>
              ) : feedbackError ? (
                <Card className="p-6 text-center">
                  <p className="text-destructive">❌ {feedbackError}</p>
                </Card>
              ) : feedback && feedback.topics.length > 0 ? (
                <div className="grid gap-4">
                  {feedback.topics.map((topic, index) => (
                    <Card key={index} className="p-6">
                      <div className="flex items-start gap-4">
                        <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                          <Tag className="w-5 h-5 text-primary" />
                        </div>
                        <div className="flex-1 space-y-3">
                          <div className="flex items-center gap-3 flex-wrap">
                            <h4 className="font-semibold text-lg">{topic.title}</h4>
                            <Badge variant={getImportanceColor(topic.importance)}>
                              {topic.importance}
                            </Badge>
                          </div>
                          <p className="text-muted-foreground text-sm">{topic.summary}</p>
                          <div className="flex items-center gap-2">
                            <div className="flex-1 bg-secondary rounded-full h-2 overflow-hidden">
                              <div
                                className="h-full bg-primary transition-all"
                                style={{ width: `${topic.proportion}%` }}
                              />
                            </div>
                            <span className="text-xs font-medium text-muted-foreground min-w-[45px] text-right">
                              {topic.proportion}%
                            </span>
                          </div>
                        </div>
                      </div>
                    </Card>
                  ))}
                </div>
              ) : (
                <Card className="p-6 text-center">
                  <p className="text-muted-foreground">아직 주제 분류가 생성되지 않았습니다.</p>
                </Card>
              )}
            </TabsContent>

            <TabsContent value="questions" className="space-y-4">
              {isFeedbackLoading ? (
                <Card className="p-6 text-center">
                  <div className="flex items-center justify-center gap-2">
                    <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary"></div>
                    <p className="text-muted-foreground">후속 질문을 생성하는 중...</p>
                  </div>
                </Card>
              ) : feedbackError ? (
                <Card className="p-6 text-center">
                  <p className="text-destructive">❌ {feedbackError}</p>
                </Card>
              ) : feedback && feedback.followUpCategories.length > 0 ? (
                <div className="space-y-6">
                  {feedback.followUpCategories.map((category, categoryIndex) => (
                    <div key={categoryIndex} className="space-y-3">
                      <div className="flex items-center gap-2 mb-4">
                        <div className="h-px flex-1 bg-border" />
                        <h3 className="text-sm font-semibold text-muted-foreground px-3">
                          {category.category}
                        </h3>
                        <div className="h-px flex-1 bg-border" />
                      </div>
                      <div className="grid gap-3">
                        {category.questions
                          .sort((a, b) => a.orderIndex - b.orderIndex)
                          .map((question, questionIndex) => (
                            <Card key={questionIndex} className="p-5">
                              <div className="flex items-start gap-3">
                                <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0">
                                  <HelpCircle className="w-4 h-4 text-amber-600 dark:text-amber-400" />
                                </div>
                                <div className="flex-1 space-y-1">
                                  <div className="flex items-center gap-2">
                                    <Badge variant="outline" className="text-xs">
                                      Q{question.orderIndex}
                                    </Badge>
                                  </div>
                                  <p className="text-base leading-relaxed">{question.question}</p>
                                </div>
                              </div>
                            </Card>
                          ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <Card className="p-6 text-center">
                  <p className="text-muted-foreground">아직 후속 질문이 생성되지 않았습니다.</p>
                </Card>
              )}
            </TabsContent>

            <TabsContent value="transcript">
              <Card className="p-6">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-semibold">회의 원문 (STT)</h3>
                  {isEditing && (
                    <p className="text-sm text-muted-foreground">
                      💡 화자 이름과 원문을 클릭하여 수정할 수 있습니다
                    </p>
                  )}
                </div>
                {editedSpeakers && editedSpeakers.length > 0 ? (
                  <div className="space-y-6">
                    {editedSpeakers.map((speaker, speakerIndex) => (
                      <div key={speakerIndex} className="space-y-3 p-4 bg-muted/30 rounded-lg">
                        {/* 화자 이름 - 인라인 편집 */}
                        <div className="flex items-center gap-2">
                          {isEditing && editingSpeakerId === speaker.speakerId ? (
                            <div className="flex items-center gap-2 flex-1">
                              <Input
                                value={speaker.name || ""}
                                onChange={(e) => handleSpeakerNameChange(speaker.speakerId, e.target.value)}
                                onBlur={() => setEditingSpeakerId(null)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') setEditingSpeakerId(null)
                                  if (e.key === 'Escape') {
                                    setEditingSpeakerId(null)
                                    if (meeting) setEditedSpeakers(meeting.speakers || [])
                                  }
                                }}
                                placeholder="화자 이름 입력"
                                className="max-w-xs text-sm font-semibold"
                                autoFocus
                              />
                              <span className="text-xs text-muted-foreground">(Enter: 저장, Esc: 취소)</span>
                            </div>
                          ) : (
                            <h4 
                              className={`font-semibold text-sm ${isEditing ? 'cursor-pointer hover:text-primary transition-colors px-2 py-1 rounded hover:bg-muted' : 'text-primary'}`}
                              onClick={() => isEditing && setEditingSpeakerId(speaker.speakerId)}
                            >
                              <User className="w-4 h-4 inline mr-1" />
                              {speaker.name || `화자 ${speaker.speakerId}`}
                              {isEditing && <Edit className="w-3 h-3 inline ml-1 opacity-50" />}
                            </h4>
                          )}
                        </div>
                        
                        {/* Segments - 원문 */}
                        <div className="space-y-2 pl-2">
                          {speaker.segments.map((segment, segmentIndex) => {
                            const segmentKey = `${speaker.speakerId}-${segmentIndex}`
                            const isEditingSegment = editingSegmentKey === segmentKey
                            
                            return (
                              <div key={segmentIndex} className="flex gap-2 items-start">
                                <span className="text-xs text-muted-foreground font-mono min-w-[50px] mt-1">
                                  [{Math.floor(segment.start / 60)}:{String(Math.floor(segment.start % 60)).padStart(2, '0')}]
                                </span>
                                
                                {isEditing && isEditingSegment ? (
                                  <div className="flex-1">
                                    <Textarea
                                      value={segment.text}
                                      onChange={(e) => handleSegmentTextChange(speaker.speakerId, segmentIndex, e.target.value)}
                                      onBlur={() => setEditingSegmentKey(null)}
                                      onKeyDown={(e) => {
                                        if (e.key === 'Escape') {
                                          setEditingSegmentKey(null)
                                          if (meeting) setEditedSpeakers(meeting.speakers || [])
                                        }
                                      }}
                                      className="text-sm min-h-[60px]"
                                      autoFocus
                                    />
                                    <p className="text-xs text-muted-foreground mt-1">
                                      Esc: 취소
                                    </p>
                                  </div>
                                ) : (
                                  <p 
                                    className={`text-sm text-muted-foreground leading-relaxed flex-1 ${isEditing ? 'cursor-pointer hover:bg-muted/50 p-2 rounded transition-colors' : ''}`}
                                    onClick={() => isEditing && setEditingSegmentKey(segmentKey)}
                                  >
                                    {segment.text}
                                    {isEditing && <Edit className="w-3 h-3 inline ml-2 opacity-30" />}
                                  </p>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-muted-foreground">아직 분석이 완료되지 않았습니다.</p>
                )}
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      </main>
    </div>
  )
}
