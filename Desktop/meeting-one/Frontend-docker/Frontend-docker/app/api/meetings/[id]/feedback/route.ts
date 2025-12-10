import { NextRequest, NextResponse } from 'next/server'

/**
 * GET /api/meetings/[id]/feedback
 * 회의 피드백 조회 (액션아이템, 주제 분류, 후속 질문)
 */
export async function GET(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  try {
    // Authorization 헤더에서 토큰 추출
    const authHeader = request.headers.get('authorization')
    
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return NextResponse.json(
        { message: '인증 토큰이 필요합니다' },
        { status: 401 }
      )
    }

    const token = authHeader.substring(7)
    const meetingId = params.id
    
    // 백엔드 API 호출
    const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080/api'
    const url = `${backendUrl}/meetings/${meetingId}/feedback`
    
    console.log('🔍 피드백 API 호출:', url)
    
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    })

    console.log('📡 백엔드 응답 상태:', response.status)

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      console.error('❌ 백엔드 에러:', errorData)
      return NextResponse.json(
        { message: errorData.message || 'API 요청 실패' },
        { status: response.status }
      )
    }

    const data = await response.json()
    console.log('✅ 피드백 데이터:', data)
    
    // 백엔드 응답 그대로 반환
    return NextResponse.json(data)
    
  } catch (error) {
    console.error('💥 피드백 조회 오류:', error)
    return NextResponse.json(
      { message: '서버 오류가 발생했습니다' },
      { status: 500 }
    )
  }
}


