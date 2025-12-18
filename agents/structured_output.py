"""Structured Output Parser를 위한 공통 유틸리티."""

from __future__ import annotations

from typing import TypeVar, Type, Any, Optional
from pydantic import BaseModel, Field

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.output_parsers import PydanticOutputParser
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False


T = TypeVar("T", bound=BaseModel)


async def extract_structured_output(
    query: str,
    model_class: Type[T],
    system_prompt: Optional[str] = None,
    temperature: float = 0.0,
    model_name: str = "gpt-4o-mini"
) -> T:
    """LLM을 사용하여 사용자 쿼리에서 구조화된 출력을 추출합니다.
    
    Args:
        query: 사용자 입력 쿼리
        model_class: 추출할 Pydantic 모델 클래스
        system_prompt: 시스템 프롬프트 (선택적)
        temperature: LLM temperature
        model_name: 사용할 LLM 모델 이름
    
    Returns:
        구조화된 데이터 (Pydantic 모델 인스턴스)
    
    Raises:
        ValueError: LLM을 사용할 수 없거나 파싱에 실패한 경우
    """
    if not LLM_AVAILABLE:
        raise ValueError("LangChain OpenAI가 설치되지 않았습니다.")
    
    import os
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다.")
    
    # Pydantic Output Parser 생성
    parser = PydanticOutputParser(pydantic_object=model_class)
    
    # 프롬프트 구성
    format_instructions = parser.get_format_instructions()
    
    if system_prompt:
        prompt_text = f"""{system_prompt}

{format_instructions}

사용자 쿼리: {query}

구조화된 JSON 형식으로 응답하세요:"""
    else:
        prompt_text = f"""사용자 쿼리를 분석하여 다음 구조에 맞게 정보를 추출하세요.

{format_instructions}

사용자 쿼리: {query}

구조화된 JSON 형식으로 응답하세요:"""
    
    # LLM 호출
    llm = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        api_key=api_key
    )
    
    response = await llm.ainvoke(prompt_text)
    
    # 파싱
    try:
        result = parser.parse(response.content)
        return result
    except Exception as e:
        raise ValueError(f"구조화된 출력 파싱 실패: {e}\n응답: {response.content}")


class QueryIntent(BaseModel):
    """사용자 쿼리 의도 분석 결과."""
    
    intent: str = Field(..., description="의도 카테고리 (schedule, document, qa, email, tech)")
    confidence: float = Field(..., description="의도 분류 신뢰도 (0.0 ~ 1.0)", ge=0.0, le=1.0)
    extracted_entities: dict[str, Any] = Field(
        default_factory=dict,
        description="추출된 엔티티들 (날짜, 인물, 장소 등)"
    )


class HistoryCheckIntent(BaseModel):
    """이전 대화 확인 질문 의도 분석 결과.
    
    사용자가 이전에 요청한 작업이나 대화 내용을 확인하는 질문인지 판단합니다.
    
    예 (확인 질문): "내가 이메일에서 육아휴직 관련 내용 찾아달라는 요청을 한적이 있어?"
    예 (새로운 요청): "이메일에서 육아휴직 정보 찾아줘"
    """
    
    is_history_check: bool = Field(
        ..., 
        description=(
            "이전 대화 확인 질문인지 여부. "
            "사용자가 과거에 요청한 작업이나 대화 내용을 확인하는 질문이면 True. "
            "과거형 확인 문구('한적이 있어?', '요청한 적 있어?' 등)가 포함되어야 True. "
            "'찾아줘', '알려줘', '검색해줘' 같은 직접 요청 동사만 있고 확인 문구가 없으면 False."
        )
    )
    check_type: Optional[str] = Field(
        default=None,
        description=(
            "확인하려는 작업 유형 (email_search, patent_search, article_search, leave_request, "
            "document_generation, meeting_reservation, recruitment_search 등). "
            "is_history_check가 True일 때만 의미가 있음. "
            "질문 내용과 일치하는 작업 유형을 선택해야 함. "
            "예: '이메일에서 찾아줘' → email_search (article_search 아님)"
        )
    )
    mentioned_keywords: list[str] = Field(
        default_factory=list,
        description="질문에서 언급된 키워드들 (예: '육아휴직', '특허', '논문' 등)"
    )
    confidence: float = Field(
        ..., 
        description=(
            "분류 신뢰도 (0.0 ~ 1.0). "
            "확실하지 않으면 낮은 신뢰도(0.7 이하)를 설정하세요. "
            "check_type이 질문 내용과 일치하지 않으면 신뢰도를 낮게 설정하세요."
        ), 
        ge=0.0, 
        le=1.0
    )


class DocumentGenerationParams(BaseModel):
    """문서 생성 파라미터."""
    
    template_type: str = Field(..., description="템플릿 타입 (예: hr_summary_report, product_comparison)")
    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="템플릿 변수 값들"
    )
    output_format: str = Field(default="html", description="출력 형식 (html, pdf)")


class EmailSearchParams(BaseModel):
    """이메일 검색 파라미터."""
    
    keywords: list[str] = Field(default_factory=list, description="검색 키워드 리스트")
    from_address: Optional[str] = Field(default=None, description="발신자 주소")
    date_range: Optional[dict[str, str]] = Field(default=None, description="날짜 범위 (start, end)")


class ScheduleParams(BaseModel):
    """스케줄 관련 파라미터."""
    
    action: Optional[str] = Field(default=None, description="요청 액션 (조회, 신청, 내역, 추천)")
    employee_id: Optional[int] = Field(default=None, description="사원 번호")
    employee_name: Optional[str] = Field(default=None, description="사원 이름")
    start_date: Optional[str] = Field(default=None, description="시작 날짜 (YYYY-MM-DD 형식)")
    end_date: Optional[str] = Field(default=None, description="종료 날짜 (YYYY-MM-DD 형식)")
    destination: Optional[str] = Field(default=None, description="목적지")
    leave_type: Optional[str] = Field(default=None, description="휴가 타입 (연차, 반차, 병가, 경조사)")
    total_days: Optional[float] = Field(default=None, description="총 일수 (소수점 가능, 반차는 0.5)")
    reason: Optional[str] = Field(default=None, description="사유 (신청인 경우)")


class TechSearchParams(BaseModel):
    """기술 검색 파라미터."""
    
    search_type: str = Field(..., description="검색 타입 (patent, article)")
    keywords: list[str] = Field(default_factory=list, description="검색 키워드")
    date_range: Optional[dict[str, str]] = Field(default=None, description="날짜 범위")


class DocumentUpdateParams(BaseModel):
    """문서 업데이트 파라미터."""
    
    document_title: str = Field(..., description="업데이트할 문서 제목. 사용자가 명시적으로 입력한 제목을 정확히 반영하세요 (예: 복리후생, 식단, 메뉴얼, 이력서)")
    file_path: Optional[str] = Field(default=None, description="업데이트할 파일 경로 (선택적, 없으면 사용자에게 요청)")
    document_type: str = Field(..., description="문서 타입 (반드시 '복리후생', '식단', '메뉴얼', '이력서' 중 하나로 판단)")


class AgentResponse(BaseModel):
    """에이전트 응답 구조."""
    
    success: bool = Field(..., description="작업 성공 여부")
    message: str = Field(..., description="사용자에게 전달할 메시지")
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="추가 데이터 (검색 결과, 생성된 문서 경로 등)"
    )
    next_action: Optional[str] = Field(default=None, description="다음 액션 힌트")


class WorkflowDetectionResult(BaseModel):
    """워크플로우 감지 결과.
    
    사용자 질의를 분석하여 복합 워크플로우 실행 요청인지 판단합니다.
    """
    
    workflow_type: Optional[str] = Field(
        default=None,
        description=(
            "감지된 워크플로우 타입 (예: 'business_trip'). "
            "워크플로우 실행 요청이 아니면 None. "
            "실행 의도가 확실할 때만 값을 설정하세요."
        )
    )
    confidence: float = Field(
        ...,
        description="워크플로우 감지 신뢰도 (0.0 ~ 1.0). 0.8 이상이면 높은 신뢰도",
        ge=0.0,
        le=1.0
    )
    is_workflow_request: bool = Field(
        ...,
        description=(
            "워크플로우 실행 요청인지 여부. "
            "실행 동사('다녀올게', '갈게', '신청해줘' 등)가 명확하면 True, "
            "질문형('알려줘', '방법은?' 등)이면 False"
        )
    )
