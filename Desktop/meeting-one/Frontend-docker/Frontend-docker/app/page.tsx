"use client"

import type React from "react"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { RecordingView } from "@/components/recording-view"
import { Mic, Upload, LogOut, Sparkles, Share2, List } from "lucide-react"
import { Button } from "@/components/ui/button"
import { validateAudioFile } from "@/lib/file-validation"
import { apiClient } from "@/lib/api"
import type { CreateMeetingResponse } from "@/types/api"

type ViewState = "start" | "recording"

export default function Home() {
  const router = useRouter()
  const [viewState, setViewState] = useState<ViewState>("start")
  const [user, setUser] = useState<{ name?: string; email: string } | null>(null)

  useEffect(() => {
    const userStr = localStorage.getItem("user")
    if (!userStr) {
      router.push("/login")
    } else {
      setUser(JSON.parse(userStr))
    }
  }, [router])

  const handleLogout = () => {
    localStorage.removeItem("user")
    router.push("/login")
  }

  const handleStartRecording = () => {
    setViewState("recording")
  }

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      // 파일 검증
      const validation = validateAudioFile(file)
      if (!validation.valid) {
        alert(`❌ 파일 업로드 실패\n\n${validation.error}`)
        e.target.value = ''
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
        console.log('📤 [프론트엔드] 업로드 요청 데이터:')
        console.log('  - title:', title.trim())
        console.log('  - date:', new Date().toISOString())
        console.log('  - file:', file.name, file.type, file.size)

        // 백엔드로 업로드
        const response = await apiClient.post('/meetings', formData)
        console.log('📥 [백엔드] 응답 데이터:', response)
        
        alert('✅ 회의가 성공적으로 업로드되었습니다!')
        router.push('/meetings')
      } catch (error: any) {
        console.error('파일 업로드 실패:', error)
        alert(`❌ 파일 업로드 실패\n\n${error.message || '알 수 없는 오류가 발생했습니다.'}`)
      } finally {
        e.target.value = ''
      }
    }
  }

  const handleRecordingComplete = async (blob: Blob, title: string) => {
    try {
      // FormData 생성
      const formData = new FormData()
      formData.append('title', title)
      formData.append('date', new Date().toISOString())
      formData.append('file', blob, 'recording.webm')

      // 디버깅: FormData 내용 확인
      console.log('📤 [프론트엔드] 녹음 업로드 요청 데이터:')
      console.log('  - title:', title)
      console.log('  - date:', new Date().toISOString())
      console.log('  - blob size:', blob.size, 'bytes')

      // 백엔드로 업로드
      const response = await apiClient.post<CreateMeetingResponse>('/meetings', formData)
      console.log('📥 [백엔드] 응답 데이터:', response)
      const data = (response as any).data || response

      alert('✅ 회의가 성공적으로 업로드되었습니다!')
      router.push('/meetings')
    } catch (error: any) {
      console.error('회의 업로드 중 오류:', error)
      alert(`❌ 회의 업로드 실패\n\n${error.message || '알 수 없는 오류가 발생했습니다.'}`)
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
          <div className="flex items-center gap-3 cursor-pointer group" onClick={() => router.push("/meetings")}>
            <div className="w-12 h-12 rounded-xl gradient-primary flex items-center justify-center shadow-lg">
              <Mic className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-balance bg-gradient-to-r from-primary via-primary to-primary/70 bg-clip-text text-transparent">
                Meeting One-Line
              </h1>
              <p className="text-xs text-muted-foreground font-medium">버튼 한 번으로 회의 → 액션아이템</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right hidden sm:block">
              <p className="text-sm font-semibold">{user.name || user.email}</p>
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
        {viewState === "start" && (
          <div className="max-w-4xl mx-auto text-center space-y-12">
            <div className="space-y-6">
              <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-primary/10 border border-primary/20 text-primary text-sm font-semibold">
                <Sparkles className="w-4 h-4" />
                AI 기반 자동 회의록 생성
              </div>
              <h2 className="text-5xl md:text-6xl font-bold text-balance leading-tight">
                회의를 자동으로
                <br />
                <span className="bg-gradient-to-r from-primary via-primary to-primary/70 bg-clip-text text-transparent">
                  기록하고 정리합니다
                </span>
              </h2>
              <p className="text-xl text-muted-foreground text-pretty leading-relaxed max-w-2xl mx-auto">
                녹음 버튼만 누르면 회의 내용이 자동으로 텍스트로 변환되고,
                <br />
                AI가 요약, 결정사항, 액션아이템을 추출합니다.
              </p>
            </div>

            <div className="flex flex-col sm:flex-row gap-4 justify-center items-center pt-4">
              <Button
                size="lg"
                className="text-lg px-8 py-7 h-auto gradient-primary shadow-lg hover:shadow-xl transition-all font-semibold w-full sm:w-auto"
                onClick={handleStartRecording}
              >
                <Mic className="w-5 h-5 mr-2" />
                회의 시작하기
              </Button>

              <Button
                size="lg"
                variant="outline"
                className="text-lg px-8 py-7 h-auto border-2 hover:bg-accent font-semibold bg-transparent w-full sm:w-auto"
                onClick={() => document.getElementById("file-upload")?.click()}
              >
                <Upload className="w-5 h-5 mr-2" />
                음성 파일 업로드
              </Button>

              <Button
                size="lg"
                variant="outline"
                className="text-lg px-8 py-7 h-auto border-2 hover:bg-accent font-semibold bg-transparent w-full sm:w-auto"
                onClick={() => router.push("/meetings")}
              >
                <List className="w-5 h-5 mr-2" />
                회의록 목록
              </Button>

              <input
                id="file-upload"
                type="file"
                accept="audio/*,.webm,.wav,.mp3,.m4a,.ogg"
                className="hidden"
                onChange={handleFileUpload}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-16">
              <div className="p-8 rounded-2xl bg-card border border-border shadow-sm card-hover">
                <div className="w-14 h-14 rounded-xl gradient-primary flex items-center justify-center mb-5 shadow-md">
                  <Mic className="w-7 h-7 text-white" />
                </div>
                <h3 className="text-lg font-bold mb-3">원클릭 녹음</h3>
                <p className="text-sm text-muted-foreground text-pretty leading-relaxed">
                  별도 설치 없이 웹에서 바로 녹음하고 정리합니다
                </p>
              </div>

              <div className="p-8 rounded-2xl bg-card border border-border shadow-sm card-hover">
                <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center mb-5 shadow-md">
                  <Sparkles className="w-7 h-7 text-white" />
                </div>
                <h3 className="text-lg font-bold mb-3">구조화 산출물</h3>
                <p className="text-sm text-muted-foreground text-pretty leading-relaxed">
                  요약, 결정사항, 담당자, 마감일을 자동 추출합니다
                </p>
              </div>

              <div className="p-8 rounded-2xl bg-card border border-border shadow-sm card-hover">
                <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-orange-500 to-amber-600 flex items-center justify-center mb-5 shadow-md">
                  <Share2 className="w-7 h-7 text-white" />
                </div>
                <h3 className="text-lg font-bold mb-3">즉시 공유</h3>
                <p className="text-sm text-muted-foreground text-pretty leading-relaxed">
                  .txt, .md, .json 형식으로 다운로드하고 공유하세요
                </p>
              </div>
            </div>
          </div>
        )}

        {viewState === "recording" && <RecordingView onComplete={handleRecordingComplete} />}
      </main>
    </div>
  )
}
