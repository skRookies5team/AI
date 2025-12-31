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
from agents.master_graphs.email.email_compose_agent import generate_email_draft, send_email_via_smtp

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


class EmailComposeToolInput(BaseModel):
    """이메일 작성 Tool 입력 스키마.
    
    Attributes:
        to_email: 수신자 이메일 주소
        user_message: 사용자 메시지 또는 요청사항
        original_subject: 원본 이메일 제목 (답장인 경우)
        original_sender: 원본 발신자 (답장인 경우)
        original_body: 원본 이메일 본문 (답장인 경우)
        cc: 참조 (선택)
        bcc: 숨은 참조 (선택)
    """
    
    to_email: str = Field(description="수신자 이메일 주소")
    user_message: str = Field(description="사용자 메시지 또는 요청사항")
    original_subject: Optional[str] = Field(default=None, description="원본 이메일 제목 (답장인 경우)")
    original_sender: Optional[str] = Field(default=None, description="원본 발신자 (답장인 경우)")
    original_body: Optional[str] = Field(default=None, description="원본 이메일 본문 (답장인 경우)")
    cc: Optional[str] = Field(default=None, description="참조")
    bcc: Optional[str] = Field(default=None, description="숨은 참조")


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


async def _email_compose_tool_impl(
    to_email: str,
    user_message: str,
    original_subject: Optional[str] = None,
    original_sender: Optional[str] = None,
    original_body: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
) -> str:
    """이메일 작성 Tool 구현.
    
    Args:
        to_email: 수신자 이메일 주소
        user_message: 사용자 메시지 또는 요청사항
        original_subject: 원본 이메일 제목 (답장인 경우)
        original_sender: 원본 발신자 (답장인 경우)
        original_body: 원본 이메일 본문 (답장인 경우)
        cc: 참조
        bcc: 숨은 참조
        
    Returns:
        생성된 이메일 초안 정보
    """
    try:
        logger.info(
            f"[Email Compose Tool] 초안 생성: to={to_email}, "
            f"message={user_message[:50]}..., is_reply={bool(original_subject)}"
        )
        
        # generate_email_draft 함수 사용
        subject, body = await generate_email_draft(
            to_email=to_email,
            user_message=user_message,
            original_subject=original_subject,
            original_sender=original_sender,
            original_body=original_body,
            is_revision=False,
            feedback=None
        )
        
        # NOTE: 프론트에서 email_draft 카드(UI)로 제목/수신자/본문을 보여주므로,
        # Tool message에는 레거시 "본문 미리보기" 등을 포함하지 않습니다.
        message = "이메일 초안이 생성되었습니다. 아래 초안 카드에서 전송/수정/취소를 진행해주세요."
        
        logger.info("[Email Compose Tool] 초안 생성 완료")
        
        return message
        
    except Exception as e:
        error_msg = f"이메일 초안 생성 중 오류가 발생했습니다: {str(e)}"
        logger.error(f"[Email Compose Tool] 오류: {e}", exc_info=True)
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


email_compose_tool = StructuredTool.from_function(
    func=_email_compose_tool_impl,
    name="email_compose",
    description=(
        "이메일 초안을 생성합니다. 새 메일 작성 또는 답장 생성이 가능합니다. "
        "원본 이메일 정보를 제공하면 답장을 생성하고, 제공하지 않으면 새 메일을 작성합니다. "
        "예: 'user@example.com에게 내일 회의 시간 변경 요청 메일 작성', "
        "'답장: 내일 가능합니다'"
    ),
    args_schema=EmailComposeToolInput,
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
        email_compose_tool,
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

