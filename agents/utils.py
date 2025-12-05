"""공통 유틸리티 함수."""

from typing import List, Dict, Any, Optional
from agents.state import Message


def check_detailed_response_in_history(
    conversation_history: List[Message],
    lookback_count: int = 5
) -> bool:
    """대화 히스토리에서 상세한 답변이 있었는지 확인합니다.
    
    Args:
        conversation_history: 대화 히스토리 리스트
        lookback_count: 확인할 최근 메시지 개수
    
    Returns:
        상세한 답변이 있으면 True, 없으면 False
    """
    if not conversation_history:
        return False
    
    # 최근 메시지만 확인
    recent_messages = conversation_history[-lookback_count:]
    
    for msg in reversed(recent_messages):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            
            # 상세한 답변 판단 기준:
            # 1. 200자 이상
            # 2. 구조화된 내용 포함 (번호, 항목, 마커 등)
            if len(content) > 200:
                return True
            
            # 구조화된 내용 마커 확인
            structure_markers = [
                "**",  # 마크다운 볼드
                "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.",  # 번호 목록
                "- ", "• ",  # 불릿 목록
                "##", "###",  # 마크다운 헤더
                ":",  # 설명 형식 (예: "항목: 내용")
            ]
            
            if any(marker in content for marker in structure_markers):
                return True
    
    return False


def get_clarification_message() -> str:
    """숫자만 입력했을 때 명확한 요청을 부탁하는 메시지를 반환합니다.
    
    Returns:
        안내 메시지 문자열
    """
    return """죄송합니다. 숫자만으로는 의도를 정확히 파악하기 어렵습니다. 
좀 더 명확한 요청을 부탁드립니다. 예를 들어:
- '1번 항목에 대해 더 자세히 알려주세요'
- '첫 번째 항목의 상세 정보를 알려주세요'
- '해당 내용에 대해 추가 질문이 있습니다'

원하시는 내용을 구체적으로 말씀해 주시면 더 정확하게 도와드릴 수 있습니다."""

