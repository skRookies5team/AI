"use client"

import type React from "react"

import { useState, useEffect, useMemo } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Mic, Upload, LogOut, Loader2, FileText, Clock, CheckCircle2, ListTodo, ChevronLeft, ChevronRight, Star, Trash2, Search, X } from "lucide-react"
import { useAuth } from "@/contexts/AuthContext"
import { useMeetings } from "@/hooks/useMeetings"
import { validateAudioFile } from "@/lib/file-validation"
import { apiClient } from "@/lib/api"

export default function MeetingsPage() {
  const router = useRouter()
  const { user, logout } = useAuth()
  const [currentPage, setCurrentPage] = useState(1)
  const pageSize = 5
  const [allMeetings, setAllMeetings] = useState<any[]>([])
  
  // 검색 상태
  const [searchKeyword, setSearchKeyword] = useState("")
  const [statusFilter, setStatusFilter] = useState<string>("all")
  const [debouncedKeyword, setDebouncedKeyword] = useState("")
  
  // 디바운스 적용 (500ms)
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedKeyword(searchKeyword)
      setCurrentPage(1) // 검색 시 첫 페이지로 이동
    }, 500)
    
    return () => clearTimeout(timer)
  }, [searchKeyword])
  
  const { meetings, isLoading, uploadAudioFile, totalPages, toggleFavorite, deleteMeeting } = useMeetings({
    pagination: {
      page: currentPage,
      limit: pageSize
    },
    keyword: debouncedKeyword || undefined,
    status: statusFilter !== "all" ? statusFilter : undefined
  })

  // 전체 회의 목록 가져오기 (즐겨찾기용)
  const { meetings: allMeetingsData } = useMeetings({
    autoFetch: true,
    pagination: {
      page: 1,
      limit: 100  // 충분히 많은 수
    }
  })

  // allMeetings 업데이트
  useEffect(() => {
    setAllMeetings(allMeetingsData)
  }, [allMeetingsData])
  
  // 검색/필터 초기화
  const handleResetFilters = () => {
    setSearchKeyword("")
    setStatusFilter("all")
    setCurrentPage(1)
  }
  
  // 상태 필터 변경 시 첫 페이지로 이동
  const handleStatusFilterChange = (value: string) => {
    setStatusFilter(value)
    setCurrentPage(1)
  }

  // 즐겨찾기 항목과 일반 항목 분리
  const favoriteMeetings = useMemo(() => {
    return allMeetings.filter(m => m.isFavorite)
  }, [allMeetings])

  const regularMeetings = useMemo(() => {
    return meetings.filter(m => !m.isFavorite)
  }, [meetings])

  const handleLogout = () => {
    logout()
  }

  const handleNewMeeting = () => {
    router.push("/")
  }

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      // 파일 검증
      const validation = validateAudioFile(file)
      if (!validation.valid) {
        alert(`❌ 파일 업로드 실패\n\n${validation.error}`)
        e.target.value = '' // 파일 input 초기화
        return
      }

      // 제목 입력 받기
      const title = window.prompt('회의 제목을 입력하세요:', file.name.replace(/\.[^/.]+$/, ''))
      if (!title || !title.trim()) {
        alert('❌ 회의 제목을 입력해주세요.')
        e.target.value = ''
        return
      }

      try {
        // FormData 생성
        const formData = new FormData()
        formData.append('title', title.trim())
        formData.append('date', new Date().toISOString())
        formData.append('file', file)

        // 디버깅: FormData 내용 확인
        console.log('📤 [프론트엔드] 목록에서 업로드 요청 데이터:')
        console.log('  - title:', title.trim())
        console.log('  - date:', new Date().toISOString())
        console.log('  - file:', file.name, file.type, file.size)

        // 백엔드로 업로드
        const response = await apiClient.post('/meetings', formData)
        console.log('📥 [백엔드] 응답 데이터:', response)
        
        alert('✅ 회의가 성공적으로 업로드되었습니다!')
      } catch (error: any) {
        console.error('파일 업로드 실패:', error)
        alert(`❌ 파일 업로드 실패\n\n${error.message || '알 수 없는 오류가 발생했습니다.'}`)
      } finally {
        e.target.value = '' // 파일 input 초기화
      }
    }
  }

  const handleMeetingClick = (meetingId: string) => {
    router.push(`/meetings/${meetingId}`)
  }

  const handleToggleFavorite = async (e: React.MouseEvent, meetingId: string) => {
    e.stopPropagation() // 카드 클릭 이벤트 전파 방지
    try {
      await toggleFavorite(meetingId)
      
      // allMeetings 상태도 업데이트
      const favoritesStr = localStorage.getItem('favorites')
      const favorites: string[] = favoritesStr ? JSON.parse(favoritesStr) : []
      
      setAllMeetings(prev => prev.map(m => ({
        ...m,
        isFavorite: favorites.includes(m.meetingId)
      })))
    } catch (error) {
      console.error('즐겨찾기 설정 실패:', error)
    }
  }

  const handleDeleteMeeting = async (e: React.MouseEvent, meetingId: string, meetingTitle: string) => {
    e.stopPropagation() // 카드 클릭 이벤트 전파 방지
    
    const confirmed = window.confirm(
      `"${meetingTitle}" 회의록을 삭제하시겠습니까?\n\n삭제된 회의록은 복구할 수 없습니다.`
    )
    
    if (!confirmed) return
    
    try {
      await deleteMeeting(meetingId)
      alert('✅ 회의록이 삭제되었습니다.')
    } catch (error: any) {
      console.error('회의록 삭제 실패:', error)
      alert(`❌ 회의록 삭제 실패\n\n${error.message || '알 수 없는 오류가 발생했습니다.'}`)
    }
  }

  if (!user) {
    return null
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-background via-background to-secondary/20">
      {/* Header */}
      <header className="border-b border-border/50 bg-card sticky top-0 z-50 shadow-sm">
        <div className="container mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3 cursor-pointer group" onClick={() => router.push("/")}>
            <div className="w-12 h-12 rounded-xl gradient-primary flex items-center justify-center shadow-lg">
              <Mic className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-balance bg-gradient-to-r from-primary to-primary/70 bg-clip-text text-transparent">
                Meeting One-Line
              </h1>
              <p className="text-xs text-muted-foreground font-medium">버튼 한 번으로 회의 → 액션아이템</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right hidden sm:block">
              <p className="text-sm font-semibold">{user.nickname || user.email}</p>
              <p className="text-xs text-muted-foreground">{user.email}</p>
            </div>
            <Button variant="outline" size="sm" onClick={handleLogout} className="font-medium bg-transparent">
              <LogOut className="w-4 h-4 mr-2" />
              로그아웃
            </Button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="container mx-auto px-4 py-12">
        <div className="max-w-5xl mx-auto space-y-8">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-4xl font-bold mb-2">회의록 목록</h2>
              <p className="text-muted-foreground text-lg">모든 회의록을 확인하고 관리하세요</p>
            </div>
            <div className="flex gap-3">
              <Button onClick={handleNewMeeting} size="lg" className="gradient-primary shadow-md font-semibold">
                <Mic className="w-4 h-4 mr-2" />새 회의
              </Button>
              <Button
                variant="outline"
                size="lg"
                onClick={() => document.getElementById("file-upload-list")?.click()}
                className="border-2 font-semibold"
              >
                <Upload className="w-4 h-4 mr-2" />
                업로드
              </Button>
              <input
                id="file-upload-list"
                type="file"
                accept="audio/*,.webm,.wav,.mp3,.m4a,.ogg"
                className="hidden"
                onChange={handleFileUpload}
              />
            </div>
          </div>

          {/* 검색 및 필터 */}
          <Card className="p-6 shadow-md border-border/50">
            <div className="flex flex-col sm:flex-row gap-4">
              <div className="flex-1 relative">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-muted-foreground" />
                <Input
                  type="text"
                  placeholder="제목, 요약, 키워드로 검색..."
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  className="pl-10 pr-10 h-11 text-base"
                />
                {searchKeyword && (
                  <button
                    onClick={() => setSearchKeyword("")}
                    className="absolute right-3 top-1/2 transform -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
              
              <Select value={statusFilter} onValueChange={handleStatusFilterChange}>
                <SelectTrigger className="w-full sm:w-[180px] h-11">
                  <SelectValue placeholder="상태 선택" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">전체</SelectItem>
                  <SelectItem value="completed">완료</SelectItem>
                  <SelectItem value="processing">분석 중</SelectItem>
                  <SelectItem value="uploaded">업로드됨</SelectItem>
                </SelectContent>
              </Select>
              
              {(searchKeyword || statusFilter !== "all") && (
                <Button
                  variant="outline"
                  onClick={handleResetFilters}
                  className="h-11 font-semibold"
                >
                  <X className="w-4 h-4 mr-2" />
                  초기화
                </Button>
              )}
            </div>
            
            {(debouncedKeyword || statusFilter !== "all") && (
              <div className="mt-4 pt-4 border-t border-border/50">
                <p className="text-sm text-muted-foreground">
                  {debouncedKeyword && (
                    <span className="mr-4">
                      🔍 검색어: <span className="font-semibold text-foreground">"{debouncedKeyword}"</span>
                    </span>
                  )}
                  {statusFilter !== "all" && (
                    <span>
                      📊 상태: <span className="font-semibold text-foreground">
                        {statusFilter === "completed" ? "완료" : statusFilter === "processing" ? "분석 중" : "업로드됨"}
                      </span>
                    </span>
                  )}
                </p>
              </div>
            )}
          </Card>

          {/* Meetings List */}
          {isLoading ? (
            <Card className="p-16 text-center shadow-md">
              <div className="space-y-6">
                <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary/10 to-primary/5 mx-auto flex items-center justify-center">
                  <Loader2 className="w-10 h-10 text-primary animate-spin" />
                </div>
                <div>
                  <h3 className="text-2xl font-bold mb-3">회의록을 불러오는 중...</h3>
                  <p className="text-muted-foreground text-lg">잠시만 기다려주세요</p>
                </div>
              </div>
            </Card>
          ) : meetings.length === 0 ? (
            <Card className="p-16 text-center shadow-md">
              <div className="space-y-6">
                <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary/10 to-primary/5 mx-auto flex items-center justify-center">
                  <FileText className="w-10 h-10 text-primary" />
                </div>
                <div>
                  <h3 className="text-2xl font-bold mb-3">아직 회의록이 없습니다</h3>
                  <p className="text-muted-foreground text-lg">새 회의를 시작하거나 음성 파일을 업로드하세요</p>
                </div>
                <div className="flex gap-3 justify-center pt-4">
                  <Button onClick={handleNewMeeting} size="lg" className="gradient-primary shadow-md font-semibold">
                    <Mic className="w-4 h-4 mr-2" />새 회의 시작
                  </Button>
                  <Button
                    variant="outline"
                    size="lg"
                    onClick={() => document.getElementById("file-upload-list")?.click()}
                    className="border-2 font-semibold"
                  >
                    <Upload className="w-4 h-4 mr-2" />
                    파일 업로드
                  </Button>
                </div>
              </div>
            </Card>
          ) : (
            <>
              {/* 즐겨찾기 섹션 */}
              {favoriteMeetings.length > 0 && (
                <div className="mb-8">
                  <div className="flex items-center gap-2 mb-4">
                    <Star className="w-5 h-5 fill-yellow-400 text-yellow-400" />
                    <h3 className="text-xl font-bold">즐겨찾기</h3>
                    <Badge variant="secondary" className="ml-2">
                      {favoriteMeetings.length}
                    </Badge>
                  </div>
                  <div className="space-y-4">
                    {favoriteMeetings.map((meeting) => (
                      <Card
                        key={meeting.meetingId}
                        className="p-8 transition-all shadow-sm cursor-pointer card-hover border-yellow-200 bg-yellow-50/30"
                        onClick={() => handleMeetingClick(meeting.meetingId)}
                      >
                        <div className="flex items-start justify-between gap-6">
                          <div className="flex-1 space-y-4">
                            <div className="flex items-center gap-4">
                              <Button
                                variant="ghost"
                                size="sm"
                                className="p-1 h-auto hover:bg-transparent"
                                onClick={(e) => handleToggleFavorite(e, meeting.meetingId)}
                              >
                                <Star className="w-5 h-5 fill-yellow-400 text-yellow-400" />
                              </Button>
                              <h3 className="text-xl font-bold flex-1">{meeting.title}</h3>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="p-1 h-auto hover:bg-red-50"
                                onClick={(e) => handleDeleteMeeting(e, meeting.meetingId, meeting.title)}
                              >
                                <Trash2 className="w-4 h-4 text-red-500" />
                              </Button>
                              {meeting.status === "processing" ? (
                                <Badge
                                  variant="secondary"
                                  className="flex items-center gap-2 px-3 py-1 text-sm font-semibold"
                                >
                                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                  분석 중
                                </Badge>
                              ) : meeting.status === "completed" ? (
                                <Badge className="flex items-center gap-2 px-3 py-1 text-sm font-semibold gradient-primary border-0">
                                  <CheckCircle2 className="w-3.5 h-3.5" />
                                  완료
                                </Badge>
                              ) : (
                                <Badge variant="outline" className="flex items-center gap-2 px-3 py-1 text-sm font-semibold">
                                  업로드됨
                                </Badge>
                              )}
                            </div>

                            <div className="flex flex-wrap gap-6 text-sm text-muted-foreground font-medium">
                              <div className="flex items-center gap-2">
                                <Clock className="w-4 h-4" />
                                <span>
                                  {new Date(meeting.date || meeting.createdAt).toLocaleDateString("ko-KR", {
                                    year: "numeric",
                                    month: "long",
                                    day: "numeric",
                                    hour: "2-digit",
                                    minute: "2-digit",
                                  })}
                                </span>
                              </div>
                              {meeting.keywords && meeting.keywords.length > 0 && (
                                <div className="flex items-center gap-2">
                                  <span className="font-semibold">키워드 {meeting.keywords.length}개</span>
                                </div>
                              )}
                            </div>

                            {meeting.status === "processing" ? (
                              <div className="space-y-3 pt-2">
                                <div className="flex items-center justify-between text-sm">
                                  <span className="text-muted-foreground font-medium">AI가 회의 내용을 분석 중...</span>
                                  <span className="text-primary font-bold">진행 중</span>
                                </div>
                                <div className="h-2.5 bg-secondary rounded-full overflow-hidden">
                                  <div className="h-full gradient-primary rounded-full animate-pulse w-2/3 shadow-sm" />
                                </div>
                              </div>
                            ) : meeting.status === "completed" && meeting.summary ? (
                              <p className="text-sm text-muted-foreground text-pretty leading-relaxed bg-muted/50 p-4 rounded-lg">
                                {meeting.summary}
                              </p>
                            ) : null}
                          </div>
                        </div>
                      </Card>
                    ))}
                  </div>
                </div>
              )}

              {/* 일반 회의록 섹션 */}
              <div>
                {favoriteMeetings.length > 0 && (
                  <div className="flex items-center gap-2 mb-4">
                    <h3 className="text-xl font-bold">전체 회의록</h3>
                  </div>
                )}
                <div className="space-y-4">
                  {regularMeetings.map((meeting) => (
                <Card
                  key={meeting.meetingId}
                  className="p-8 transition-all shadow-sm cursor-pointer card-hover border-border/50"
                  onClick={() => handleMeetingClick(meeting.meetingId)}
                >
                  <div className="flex items-start justify-between gap-6">
                    <div className="flex-1 space-y-4">
                      <div className="flex items-center gap-4">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="p-1 h-auto hover:bg-transparent"
                          onClick={(e) => handleToggleFavorite(e, meeting.meetingId)}
                        >
                          <Star 
                            className={`w-5 h-5 ${meeting.isFavorite ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground'}`} 
                          />
                        </Button>
                        <h3 className="text-xl font-bold flex-1">{meeting.title}</h3>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="p-1 h-auto hover:bg-red-50"
                          onClick={(e) => handleDeleteMeeting(e, meeting.meetingId, meeting.title)}
                        >
                          <Trash2 className="w-4 h-4 text-red-500" />
                        </Button>
                        {meeting.status === "processing" ? (
                          <Badge
                            variant="secondary"
                            className="flex items-center gap-2 px-3 py-1 text-sm font-semibold"
                          >
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            분석 중
                          </Badge>
                        ) : meeting.status === "completed" ? (
                          <Badge className="flex items-center gap-2 px-3 py-1 text-sm font-semibold gradient-primary border-0">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            완료
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="flex items-center gap-2 px-3 py-1 text-sm font-semibold">
                            업로드됨
                          </Badge>
                        )}
                      </div>

                      <div className="flex flex-wrap gap-6 text-sm text-muted-foreground font-medium">
                        <div className="flex items-center gap-2">
                          <Clock className="w-4 h-4" />
                          <span>
                            {new Date(meeting.date || meeting.createdAt).toLocaleDateString("ko-KR", {
                              year: "numeric",
                              month: "long",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                        </div>
                        {meeting.keywords && meeting.keywords.length > 0 && (
                          <div className="flex items-center gap-2">
                            <span className="font-semibold">키워드 {meeting.keywords.length}개</span>
                          </div>
                        )}
                      </div>

                      {meeting.status === "processing" ? (
                        <div className="space-y-3 pt-2">
                          <div className="flex items-center justify-between text-sm">
                            <span className="text-muted-foreground font-medium">AI가 회의 내용을 분석 중...</span>
                            <span className="text-primary font-bold">진행 중</span>
                          </div>
                          <div className="h-2.5 bg-secondary rounded-full overflow-hidden">
                            <div className="h-full gradient-primary rounded-full animate-pulse w-2/3 shadow-sm" />
                          </div>
                        </div>
                      ) : meeting.status === "completed" && meeting.summary ? (
                        <p className="text-sm text-muted-foreground text-pretty leading-relaxed bg-muted/50 p-4 rounded-lg">
                          {meeting.summary}
                        </p>
                      ) : null}
                    </div>
                  </div>
                </Card>
              ))}
            </div>
              </div>
            </>
          )}

          {/* Pagination */}
          {!isLoading && meetings.length > 0 && totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-8">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
                disabled={currentPage === 1}
                className="font-semibold"
              >
                <ChevronLeft className="w-4 h-4 mr-1" />
                이전
              </Button>
              
              <div className="flex items-center gap-2">
                {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
                  <Button
                    key={page}
                    variant={currentPage === page ? "default" : "outline"}
                    size="sm"
                    onClick={() => setCurrentPage(page)}
                    className={`w-10 h-10 font-semibold ${currentPage === page ? "gradient-primary" : ""}`}
                  >
                    {page}
                  </Button>
                ))}
              </div>

              <Button
                variant="outline"
                size="sm"
                onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
                disabled={currentPage === totalPages}
                className="font-semibold"
              >
                다음
                <ChevronRight className="w-4 h-4 ml-1" />
              </Button>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
