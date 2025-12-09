"""이메일 도메인 Tool 정의.

LangChain StructuredTool을 사용하여 이메일 관련 기능을 Tool로 정의합니다.
이를 통해 표준화된 인터페이스, 자동 문서화, 추적 가능한 실행을 제공합니다.
"""

from __future__ import annotations

from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool
from datetime import datetime
import logging

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from agents.master_graphs.email.email_search_agent import email_search

logger = logging.getLogger(__name__)


# ============================================================================
# Tool Input Schemas
# ============================================================================

class EmailSearchToolInput(BaseModel):
    """이메일 검색 Tool 입력 스키마.
    
    Attributes:
        query: 검색할 키워드 또는 내용
        from_address: 발신자 이메일 주소 (선택)
        date_range: 날짜 범위 (예: '2024-01-01,2024-12-31') (선택)
        session_id: 세션 ID (선택)
    """
    
    query: str = Field(description="검색할 키워드 또는 내용")
    from_address: Optional[str] = Field(default=None, description="발신자 이메일 주소")
    date_range: Optional[str] = Field(default=None, description="날짜 범위 (예: '2024-01-01,2024-12-31')")
    session_id: Optional[str] = Field(default=None, description="세션 ID")


class EmailAutoReplyToolInput(BaseModel):
    """이메일 자동 답장 생성 Tool 입력 스키마.
    
    Attributes:
        query: 답장을 생성할 이메일 내용 또는 요청사항
        original_email: 원본 이메일 내용 (선택)
    """
    
    query: str = Field(description="답장을 생성할 이메일 내용 또는 요청사항")
    original_email: Optional[str] = Field(default=None, description="원본 이메일 내용")


class EmailDetectReceptionToolInput(BaseModel):
    """이메일 수신 감지 Tool 입력 스키마.
    
    Attributes:
        session_id: 세션 ID (선택)
    """
    
    session_id: Optional[str] = Field(default=None, description="세션 ID")


# ============================================================================
# Tool Implementation Functions
# ============================================================================

async def _email_search_tool_impl(
    query: str,
    from_address: Optional[str] = None,
    date_range: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """이메일 검색 Tool 구현.
    
    Args:
        query: 검색할 키워드
        from_address: 발신자 이메일 주소
        date_range: 날짜 범위
        session_id: 세션 ID
        
    Returns:
        검색 결과 메시지
    """
    try:
        logger.info(
            f"[Email Search Tool] 검색 실행: query={query}, "
            f"from={from_address}, date_range={date_range}, session={session_id}"
        )
        
        # 기존 email_search_agent 사용 (이제 비동기 함수)
        result = await email_search(query, session_id=session_id)
        
        message = result.get("message", "이메일 검색 결과를 찾을 수 없습니다.")
        rows = result.get("rows", [])
        
        logger.info(f"[Email Search Tool] 검색 완료: {len(rows)}개 결과")
        
        return message
        
    except Exception as e:
        error_msg = f"이메일 검색 중 오류가 발생했습니다: {str(e)}"
        logger.error(f"[Email Search Tool] 오류: {e}", exc_info=True)
        return error_msg


def _email_auto_reply_tool_impl(
    query: str,
    original_email: Optional[str] = None,
) -> str:
    """이메일 자동 답장 생성 Tool 구현.
    
    Args:
        query: 답장을 생성할 이메일 내용 또는 요청사항
        original_email: 원본 이메일 내용
        
    Returns:
        생성된 답장 초안
    """
    try:
        logger.info(f"[Email Auto Reply Tool] 답장 생성: query={query[:50]}...")
        
        # 원본 이메일이 있으면 함께 전달
        full_query = query
        if original_email:
            full_query = f"원본 이메일:\n{original_email}\n\n답장 요청:\n{query}"
        
        # 기존 email_auto_reply_agent 사용
        result = email_auto_reply(full_query)
        
        message = result.get("message", "답장 초안을 생성할 수 없습니다.")
        
        logger.info("[Email Auto Reply Tool] 답장 생성 완료")
        
        return message
        
    except Exception as e:
        error_msg = f"답장 생성 중 오류가 발생했습니다: {str(e)}"
        logger.error(f"[Email Auto Reply Tool] 오류: {e}", exc_info=True)
        return error_msg


def _email_detect_reception_tool_impl(
    session_id: Optional[str] = None,
) -> str:
    """이메일 수신 감지 Tool 구현.
    
    Args:
        session_id: 세션 ID
        
    Returns:
        수신 감지 결과 메시지
    """
    try:
        logger.info(f"[Email Detect Reception Tool] 수신 감지 실행: session={session_id}")
        
        # TODO: 실제 이메일 수신 감지 로직 구현
        # 현재는 더미 구현
        message = "새로운 이메일이 없습니다."
        
        logger.info("[Email Detect Reception Tool] 수신 감지 완료")
        
        return message
        
    except Exception as e:
        error_msg = f"이메일 수신 감지 중 오류가 발생했습니다: {str(e)}"
        logger.error(f"[Email Detect Reception Tool] 오류: {e}", exc_info=True)
        return error_msg


# ============================================================================
# Tool Definitions
# ============================================================================

email_search_tool = StructuredTool.from_function(
    func=_email_search_tool_impl,
    name="email_search",
    description=(
        "이메일을 검색합니다. 키워드, 발신자, 날짜 범위로 검색할 수 있습니다. "
        "예: '프로젝트 관련 이메일 찾아줘', '김철수씨가 보낸 이메일', '지난주 이메일'"
    ),
    args_schema=EmailSearchToolInput,
    return_direct=False,
)


email_auto_reply_tool = StructuredTool.from_function(
    func=_email_auto_reply_tool_impl,
    name="email_auto_reply",
    description=(
        "이메일 답장 초안을 자동으로 생성합니다. "
        "원본 이메일 내용과 답장 요청사항을 입력받아 적절한 답장을 생성합니다. "
        "예: '회의 일정 확인했습니다 답장해줘', '프로젝트 진행 상황 확인 답장'"
    ),
    args_schema=EmailAutoReplyToolInput,
    return_direct=False,
)


email_detect_reception_tool = StructuredTool.from_function(
    func=_email_detect_reception_tool_impl,
    name="email_detect_reception",
    description=(
        "새로운 이메일 수신 여부를 확인합니다. "
        "최근 수신된 이메일이 있는지 확인하고 알림을 제공합니다."
    ),
    args_schema=EmailDetectReceptionToolInput,
    return_direct=False,
)


# ============================================================================
# Tool Registry
# ============================================================================

def get_email_tools() -> List[StructuredTool]:
    """이메일 도메인의 모든 Tool을 반환합니다.
    
    Returns:
        이메일 관련 Tool 리스트
    """
    return [
        email_search_tool,
        email_auto_reply_tool,
        email_detect_reception_tool,
    ]


def get_email_tool_by_name(tool_name: str) -> Optional["StructuredTool"]:
    """Tool 이름으로 Tool을 찾습니다.
    
    Args:
        tool_name: Tool 이름 (예: 'email_search')
        
    Returns:
        찾은 Tool 또는 None
    """
    tools = get_email_tools()
    for tool in tools:
        if tool.name == tool_name:
            return tool
    return None

