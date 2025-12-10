"use client"

import type React from "react"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Mic } from "lucide-react"
import Link from "next/link"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { useAuth } from "@/contexts/AuthContext"
import { apiClient } from "@/lib/api"

export default function SignupPage() {
  const router = useRouter()
  const { signup } = useAuth()
  const [nickname, setNickname] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState("")
  const [nicknameError, setNicknameError] = useState("")
  const [isCheckingNickname, setIsCheckingNickname] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")

    if (password !== confirmPassword) {
      setError("비밀번호가 일치하지 않습니다")
      return
    }

    if (password.length < 8) {
      setError("비밀번호는 8자 이상이어야 합니다")
      return
    }

    if (nickname.length < 2) {
      setError("닉네임은 2자 이상이어야 합니다")
      return
    }

    if (nicknameError) {
      setError("닉네임을 확인해주세요")
      return
    }

    setIsLoading(true)

    try {
      await signup({
        email,
        password,
        nickname
      })
      router.push("/")
    } catch (error: any) {
      setError(error.message || "회원가입에 실패했습니다")
    } finally {
      setIsLoading(false)
    }
  }

  // 닉네임 중복 확인
  const handleNicknameChange = async (value: string): Promise<void> => {
    setNickname(value)
    setNicknameError("")
    
    if (value.length >= 2) {
      setIsCheckingNickname(true)
      try {
        const result = await apiClient.checkNickname(value)
        if (!result.available) {
          setNicknameError("이미 사용 중인 닉네임입니다")
        }
      } catch (error) {
        console.error('닉네임 중복 확인 실패:', error)
      } finally {
        setIsCheckingNickname(false)
      }
    }
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md space-y-8">
        {/* Logo */}
        <div className="text-center space-y-2">
          <div className="flex justify-center">
            <div className="w-16 h-16 rounded-xl bg-primary flex items-center justify-center">
              <Mic className="w-8 h-8 text-primary-foreground" />
            </div>
          </div>
          <h1 className="text-2xl font-bold">meeting one-line</h1>
          <p className="text-sm text-muted-foreground">회의를 자동으로 기록하고 정리합니다</p>
        </div>

        {/* Signup Form */}
        <Card>
          <CardHeader>
            <CardTitle>회원가입</CardTitle>
            <CardDescription>새 계정을 만들어 시작하세요</CardDescription>
          </CardHeader>
          <form onSubmit={handleSubmit}>
            <CardContent className="space-y-4">
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <div className="space-y-2">
                <Label htmlFor="nickname">닉네임</Label>
                <div className="relative">
                  <Input
                    id="nickname"
                    type="text"
                    placeholder="홍길동"
                    value={nickname}
                    onChange={(e) => handleNicknameChange(e.target.value)}
                    required
                    className={nicknameError ? "border-red-500" : ""}
                  />
                  {isCheckingNickname && (
                    <div className="absolute right-3 top-1/2 transform -translate-y-1/2">
                      <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-primary"></div>
                    </div>
                  )}
                </div>
                {nicknameError && (
                  <p className="text-sm text-red-500">{nicknameError}</p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">이메일</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="name@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">비밀번호</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirm-password">비밀번호 확인</Label>
                <Input
                  id="confirm-password"
                  type="password"
                  placeholder="••••••••"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  required
                />
              </div>
            </CardContent>
            <CardFooter className="flex flex-col gap-4">
              <Button type="submit" className="w-full" disabled={isLoading}>
                {isLoading ? "가입 중..." : "회원가입"}
              </Button>
              <p className="text-sm text-center text-muted-foreground">
                이미 계정이 있으신가요?{" "}
                <Link href="/login" className="text-primary hover:underline font-medium">
                  로그인
                </Link>
              </p>
            </CardFooter>
          </form>
        </Card>
      </div>
    </div>
  )
}
