"use client"

import { Card } from "@/components/ui/card"
import { Loader2 } from "lucide-react"

export function ProcessingView() {
  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div className="text-center space-y-2">
        <h2 className="text-3xl font-bold">회의록 생성 중</h2>
        <p className="text-muted-foreground">AI가 회의 내용을 분석하고 있습니다</p>
      </div>

      <Card className="p-12 text-center space-y-8">
        <div className="flex justify-center">
          <Loader2 className="w-16 h-16 text-primary animate-spin" />
        </div>

        <div className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">음성을 텍스트로 변환 중...</span>
              <span className="text-primary font-medium">진행 중</span>
            </div>
            <div className="h-2 bg-secondary rounded-full overflow-hidden">
              <div className="h-full bg-primary rounded-full animate-pulse w-2/3" />
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">회의 내용 요약 중...</span>
              <span className="text-muted-foreground font-medium">대기 중</span>
            </div>
            <div className="h-2 bg-secondary rounded-full" />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">액션아이템 추출 중...</span>
              <span className="text-muted-foreground font-medium">대기 중</span>
            </div>
            <div className="h-2 bg-secondary rounded-full" />
          </div>
        </div>

        <p className="text-sm text-muted-foreground">평균 처리 시간: 30초 ~ 1분</p>
      </Card>
    </div>
  )
}
