import { NextRequest, NextResponse } from 'next/server'

/**
 * GET /api/meetings/check
 * 분석이 완료되지 않은 회의 목록 조회 (Polling)
 * 
 * - UPLOADED 또는 PROCESSING 상태인 회의 목록 반환
 * - 최근 1시간 이내 업로드된 회의만 조회
 */
export async function GET(request: NextRequest) {
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
    
    // 백엔드 API 호출
    const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8080/api'
    const response = await fetch(`${backendUrl}/meetings/check`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      return NextResponse.json(
        { message: errorData.message || 'API 요청 실패' },
        { status: response.status }
      )
    }

    const data = await response.json()
    
    // 백엔드 응답 그대로 반환
    return NextResponse.json(data)
    
  } catch (error) {
    console.error('회의 분석 상태 조회 오류:', error)
    return NextResponse.json(
      { message: '서버 오류가 발생했습니다' },
      { status: 500 }
    )
  }
}


