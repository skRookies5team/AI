"""채용 에이전트 - 이력서 검색/추천/요약."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import os
import asyncio
import re
from pathlib import Path

from agents.state import OrchestratorState
from agents.master_graphs.recruitment.recruitment_data import (
    search_resumes_from_vector_db,

    ensure_milvus_collection,
)

# YAML 로드 (선택적)
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None

# 프롬프트 캐시
_recruitment_prompts: Optional[Dict[str, Any]] = None

# 상수 정의
class ResumeConstants:
    """이력서 처리 관련 상수."""
    
    # 정규표현식 패턴
    RESUME_HEADER_PATTERN = r'===\s*RESUME\s*\d+\s*==='
    NAME_PATTERN = r'^([가-힣]{2,4})'
    NAME_IN_FORMATTED_PATTERN = r'\*\*([가-힣]{2,4})\*\*'
    RESUME_NUM_PATTERN = r'(\d+)'
    CONTACT_SEPARATOR_PATTERN = r'[·|]'
    YEAR_RANGE_PATTERN = r'^\d{4}\s*~\s*\d{4}'
    YEAR_CURRENT_PATTERN = r'^\d{4}\s*~\s*현재'
    NUMBERED_LIST_PATTERN = r'^\d+[\.\)]\s+'
    DIVIDER_PATTERN = r'^[-=]{3,}$'
    
    # 섹션 패턴
    SECTION_PATTERNS = {
        '요약': r'^요약',
        '경력': r'^경력',
        '주요\s*경력': r'^주요\s*경력',
        '주요\s*프로젝트': r'^주요\s*프로젝트',
        '프로젝트': r'^프로젝트',
        '기술\s*스택': r'^기술\s*스택',
        '학력': r'^학력',
    }
    
    # 키워드
    STATUS_KEYWORDS = ['구직', '재직']
    ROLE_KEYWORDS = ['역할:', '성과:', '역할', '성과']
    BULLET_POINTS = ['·', '-', '•']
    CONTACT_SEPARATORS = ['·', '|']
    CAREER_SEPARATOR = '–'
    
    # 포맷팅 설정
    MAX_LINE_LENGTH = 100
    MAX_PREVIEW_LENGTH = 200
    MAX_RESUME_TEXT_LENGTH = 10000
    HEADER_LINES_TO_CHECK = 10
    PROJECT_NAME_MIN_LENGTH = 5
    PROJECT_KEYWORD_CHECK_LENGTH = 20
    
    # 메시지 템플릿
    SEARCH_RESULTS_HEADER = "📋 총 {count}건의 이력서를 찾았습니다:\n\n"
    SEARCH_RESULT_ITEM = "{idx}. {title} (유사도: {score:.2f})\n   {preview}...\n\n"
    SEARCH_RESULTS_MORE = "... 외 {count}건 더 있습니다.\n\n"
    SEARCH_RESULTS_FOOTER = "자세한 내용을 보려면 번호를 선택하거나 '요약'이라고 말씀해주세요."
    SEARCH_EMPTY_MESSAGE = (
        "⚠️ 현재 벡터 DB에 등록된 이력서가 없습니다.\n\n"
        "이력서 검색 기능을 사용하려면 먼저 이력서를 업로드해주세요.\n\n"
        "📌 이력서 업로드 방법:\n"
        "1. 웹 UI의 '문서 업로드' 탭에서 이력서 파일(PDF/DOCX)을 업로드하세요.\n"
        "2. 또는 API를 통해 이력서를 벡터 DB에 저장하세요.\n\n"
        "이력서가 업로드되면 키워드 기반으로 검색할 수 있습니다."
    )
    
    RECOMMEND_SUCCESS_MESSAGE = "⭐ 추천 인재 {count}명을 찾았습니다. 아래 카드를 클릭하여 상세 정보를 확인하세요."
    RECOMMEND_EMPTY_MESSAGE = (
        "⚠️ 현재 벡터 DB에 등록된 이력서가 없습니다.\n\n"
        "인재 추천 기능을 사용하려면 먼저 이력서를 업로드해주세요.\n\n"
        "📌 이력서 업로드 방법:\n"
        "1. 웹 UI의 '문서 업로드' 탭에서 이력서 파일(PDF/DOCX)을 업로드하세요.\n"
        "2. 또는 API를 통해 이력서를 벡터 DB에 저장하세요.\n\n"
        "이력서가 업로드되면 키워드 기반으로 인재를 추천해드릴 수 있습니다."
    )
    
    SUMMARY_HEADER = "📋 **{title}** 이력서 핵심 요약:\n\n{summary}"
    
    # 에러 메시지
    ERROR_SEARCH = "이력서 검색 중 오류가 발생했습니다: {error}"
    ERROR_RECOMMEND = "인재 추천 중 오류가 발생했습니다: {error}"
    ERROR_SUMMARY = "이력서 요약 중 오류가 발생했습니다: {error}"
    ERROR_DETAIL = "이력서 상세 정보 조회 중 오류가 발생했습니다: {error}"
    ERROR_CHUNK_FETCH = "청크 조회 오류: {error}"
    
    # 기본 메시지
    MESSAGE_NO_RESUME = "요약할 이력서 정보가 없습니다."
    MESSAGE_NO_RESUME_CONTENT = "이력서 내용을 찾을 수 없습니다."
    MESSAGE_NO_RESUME_INFO = "조회할 이력서 정보가 없습니다."
    
    # 기본값
    DEFAULT_COLLECTION_NAME = "hr_data_embeddings"
    DEFAULT_LIMIT = 5
    DEFAULT_SEARCH_LIMIT = 10
    DEFAULT_MAX_CHUNKS = 1000
    MAX_DISPLAY_RESULTS = 5
    SEARCH_MULTIPLIER = 2  # exclude_titles 사용 시 검색 개수 배수
    
    # LLM 설정
    LLM_MODEL = "gpt-4o-mini"
    LLM_TEMPERATURE = 0.3
    LLM_MAX_TOKENS = 1500


def _handle_error(error: Exception, error_message_template: str, default_message: str = "") -> Dict[str, Any]:
    """에러 처리 헬퍼 함수.
    
    Args:
        error: 발생한 예외
        error_message_template: 에러 메시지 템플릿 (format 사용)
        default_message: 기본 에러 메시지 (템플릿이 없을 때)
    
    Returns:
        에러 응답 딕셔너리
    """
    print(f"❌ 오류: {error}")
    import traceback
    traceback.print_exc()
    
    if error_message_template:
        message = error_message_template.format(error=str(error))
    else:
        message = default_message or f"오류가 발생했습니다: {str(error)}"
    
    return {
        "ok": False,
        "message": message,
    }


async def _collect_full_resume_text(resume: Dict[str, Any]) -> str:
    """이력서 전체 텍스트를 수집합니다 (모든 청크 포함).
    
    Args:
        resume: 이력서 딕셔너리
    
    Returns:
        이력서 전체 텍스트
    """
    document_title = resume.get("document_title", "")
    all_chunks = await get_all_chunks_for_resume(document_title)
    
    if not all_chunks:
        return resume.get("chunk_text", "")
    
    return "\n\n".join(all_chunks)


def _extract_basic_info(lines: List[str]) -> Tuple[Optional[str], List[str], Optional[str]]:
    """이력서 기본 정보 추출 (이름, 연락처, 상태).
    
    Args:
        lines: 이력서 라인 리스트
    
    Returns:
        (이름, 연락처 리스트, 상태) 튜플
    """
    name = None
    contact_info = []
    status = None
    
    for line in lines[:ResumeConstants.HEADER_LINES_TO_CHECK]:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        # 이름 추출
        if not name:
            name_match = re.search(ResumeConstants.NAME_PATTERN, line_stripped)
            if name_match:
                name = name_match.group(1)
        
        # 연락처 정보 추출
        if any(sep in line_stripped for sep in ResumeConstants.CONTACT_SEPARATORS):
            parts = re.split(ResumeConstants.CONTACT_SEPARATOR_PATTERN, line_stripped)
            contact_info = [p.strip() for p in parts if p.strip()]
        
        # 상태 추출
        if any(kw in line_stripped for kw in ResumeConstants.STATUS_KEYWORDS):
            status = line_stripped
    
    return name, contact_info, status


def _format_long_line(line: str) -> List[str]:
    """긴 줄을 적절히 줄바꿈합니다.
    
    Args:
        line: 원본 줄
    
    Returns:
        줄바꿈된 줄 리스트
    """
    if len(line) <= ResumeConstants.MAX_LINE_LENGTH:
        return [line]
    
    words = line.split()
    formatted_lines = []
    current_line = ""
    
    for word in words:
        if len(current_line) + len(word) + 1 > ResumeConstants.MAX_LINE_LENGTH:
            if current_line:
                formatted_lines.append(current_line)
            current_line = word
        else:
            current_line += (" " + word) if current_line else word
    
    if current_line:
        formatted_lines.append(current_line)
    
    return formatted_lines


def _clean_empty_lines(lines: List[str]) -> List[str]:
    """연속된 빈 줄을 정리합니다 (최대 1개).
    
    Args:
        lines: 원본 줄 리스트
    
    Returns:
        정리된 줄 리스트
    """
    cleaned = []
    prev_empty = False
    
    for line in lines:
        is_empty = not line.strip()
        if is_empty:
            if not prev_empty:
                cleaned.append('')
            prev_empty = True
        else:
            cleaned.append(line)
            prev_empty = False
    
    return cleaned


def _format_summary_text(summary_text: str) -> str:
    """요약 텍스트를 구조화된 형식으로 포맷팅.
    
    Args:
        summary_text: LLM이 생성한 원본 요약 텍스트
    
    Returns:
        구조화된 요약 텍스트
    """
    import re
    
    lines = summary_text.split('\n')
    formatted_lines = []
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            if formatted_lines and formatted_lines[-1].strip():
                formatted_lines.append('')
            continue
        
        # 섹션 헤더 감지 (###, ##, **제목**, 제목: 등)
        if re.match(r'^#{1,3}\s+', line_stripped):
            # 마크다운 헤더
            if formatted_lines and formatted_lines[-1].strip():
                formatted_lines.append('')
            formatted_lines.append(line_stripped)
            formatted_lines.append('')
            continue
        
        # 굵은 텍스트로 감싸진 제목 (이미 **제목** 형식)
        if re.match(r'^\*\*[^*]+\*\*', line_stripped) and not line_stripped.endswith('**'):
            if formatted_lines and formatted_lines[-1].strip():
                formatted_lines.append('')
            formatted_lines.append(line_stripped)
            formatted_lines.append('')
            continue
        
        # 섹션 제목 패턴 (예: "#### 지원자 기본 정보", "**주요 경력**", "경력:")
        section_patterns = [
            r'^#{1,6}\s+',
            r'^\*\*[^*]+\*\*$',
            r'^[가-힣\s]+:$',
            r'^[가-힣\s]+:$',
        ]
        
        is_section = False
        for pattern in section_patterns:
            if re.match(pattern, line_stripped):
                is_section = True
                break
        
        # 명시적인 섹션 키워드로 시작하는 경우
        section_keywords = [
            '지원자', '기본 정보', '주요 경력', '경력', '보유 기술', '기술', 
            '학력', '자격증', '주요 프로젝트', '프로젝트', '성과', '역량'
        ]
        
        if any(line_stripped.startswith(kw) or f"**{kw}" in line_stripped for kw in section_keywords):
            is_section = True
        
        if is_section:
            if formatted_lines and formatted_lines[-1].strip():
                formatted_lines.append('')
            # 제목이 **로 감싸져 있지 않으면 감싸기
            if not line_stripped.startswith('**'):
                if line_stripped.endswith(':'):
                    formatted_lines.append(f"**{line_stripped.rstrip(':')}**")
                else:
                    formatted_lines.append(f"**{line_stripped}**")
            else:
                formatted_lines.append(line_stripped)
            formatted_lines.append('')
            continue
        
        # 번호 목록 (1., 2., - 등)
        if re.match(ResumeConstants.NUMBERED_LIST_PATTERN, line_stripped) or \
           any(line_stripped.startswith(bp) for bp in ResumeConstants.BULLET_POINTS):
            formatted_lines.append(f"  {line_stripped}")
            continue
        
        # 일반 텍스트
        formatted_lines.append(line_stripped)
    
    # 빈 줄 정리
    cleaned_lines = _clean_empty_lines(formatted_lines)
    return '\n'.join(cleaned_lines).strip()


def _extract_display_title(document_title: str, formatted_text: str) -> str:
    """표시용 제목을 추출합니다.
    
    Args:
        document_title: 원본 이력서 제목
        formatted_text: 포맷팅된 텍스트
    
    Returns:
        표시용 제목
    """
    resume_num = None
    if document_title.startswith("===") or "RESUME" in document_title.upper():
        resume_num_match = re.search(ResumeConstants.RESUME_NUM_PATTERN, document_title)
        if resume_num_match:
            resume_num = resume_num_match.group(1)
    
    name_match = re.search(
        ResumeConstants.NAME_IN_FORMATTED_PATTERN, 
        formatted_text[:ResumeConstants.MAX_PREVIEW_LENGTH]
    )
    
    if name_match:
        name = name_match.group(1)
        return f"{name} (RESUME {resume_num})" if resume_num else name
    
    return f"RESUME {resume_num}" if resume_num else "이력서"


def _load_recruitment_prompts() -> Dict[str, Any]:
    """recruitment 도메인 프롬프트를 YAML 파일에서 로드합니다.
    
    Returns:
        recruitment 도메인 프롬프트 딕셔너리
    """
    global _recruitment_prompts
    
    if _recruitment_prompts is not None:
        return _recruitment_prompts
    
    if not YAML_AVAILABLE:
        print("⚠️ YAML 패키지가 설치되지 않았습니다. 기본 프롬프트를 사용합니다.")
        return {}
    
    try:
        # agents 디렉토리 기준으로 YAML 파일 경로 찾기
        current_dir = Path(__file__).parent.parent.parent
        yaml_path = current_dir / "agent_prompts.yaml"
        
        if not yaml_path.exists():
            print(f"⚠️ agent_prompts.yaml 파일을 찾을 수 없습니다: {yaml_path}")
            return {}
        
        with open(yaml_path, "r", encoding="utf-8") as f:
            all_prompts = yaml.safe_load(f)
        
        _recruitment_prompts = all_prompts.get("recruitment", {})
        
        if not _recruitment_prompts:
            print("⚠️ agent_prompts.yaml에 'recruitment' 섹션이 없습니다.")
            return {}
        
        return _recruitment_prompts
        
    except Exception as e:
        print(f"⚠️ YAML 파일 로드 실패: {e}. 기본 프롬프트를 사용합니다.")
        return {}


def _get_summarize_resume_prompt(document_title: str, resume_text: str) -> str:
    """이력서 요약 프롬프트를 YAML에서 로드하여 생성합니다.
    
    Args:
        document_title: 이력서 제목
        resume_text: 이력서 텍스트 내용
    
    Returns:
        생성된 프롬프트 문자열
    """
    prompts = _load_recruitment_prompts()
    
    # YAML에서 프롬프트 로드
    prompt_config = prompts.get("prompts", {}).get("summarize_resume", {})
    system_prompt = prompt_config.get("system_prompt", "")
    
    if not system_prompt:
        # 기본 프롬프트 (fallback) - 구조화된 형식으로 출력 요청
        return f"""다음은 '{document_title}' 이력서의 전체 내용입니다. 
이 이력서의 핵심 내용을 한국어로 구조화된 형식으로 요약해 주세요.

다음 형식을 따라주세요:

**지원자 기본 정보**
- 이름: [이름]
- 연락처: [연락처]
- 이메일: [이메일]
- 현재 상태: [구직 중/재직 중 등]

**주요 경력 및 경험**
[회사명] – [직책]
[기간]
- [주요 업무 1]
- [주요 업무 2]

**보유 기술 및 역량**
- 프로그래밍 언어: [언어 목록]
- 프레임워크: [프레임워크 목록]
- 도구: [도구 목록]

**학력 및 자격증**
[학교명] – [학위]
[설명]

**주요 프로젝트**
[프로젝트명]
- 역할: [역할 설명]
- 성과: [성과 설명]

이력서 내용:
{resume_text[:ResumeConstants.MAX_RESUME_TEXT_LENGTH]}

핵심 요약:"""
    
    # 플레이스홀더 치환
    document_title_placeholder = prompt_config.get("document_title_placeholder", "{document_title}")
    resume_text_placeholder = prompt_config.get("resume_text_placeholder", "{resume_text}")
    max_length = prompt_config.get("max_resume_text_length", ResumeConstants.MAX_RESUME_TEXT_LENGTH)
    
    # 텍스트 길이 제한
    truncated_resume_text = resume_text[:max_length]
    
    # 프롬프트 생성
    prompt = system_prompt.replace(document_title_placeholder, document_title)
    prompt = prompt.replace(resume_text_placeholder, truncated_resume_text)
    
    return prompt


async def search_resumes(
    query: str,
    state: OrchestratorState,
    limit: int = 10
) -> Dict[str, Any]:
    """벡터 DB에서 이력서 검색.
    
    Args:
        query: 사용자 쿼리 (예: "Python 개발자", "데이터 분석 경력 5년")
        state: 오케스트레이터 상태
        limit: 검색 결과 개수
    
    Returns:
        검색 결과 딕셔너리
    """
    print(f"🔍 이력서 검색: {query}")
    
    try:
        # 벡터 DB에서 이력서 검색
        resumes = await search_resumes_from_vector_db(
            query=query,
            limit=limit
        )
        
        if resumes:
            message = ResumeConstants.SEARCH_RESULTS_HEADER.format(count=len(resumes))
            for idx, resume in enumerate(resumes[:ResumeConstants.MAX_DISPLAY_RESULTS], 1):
                title = resume.get("document_title", "이력서 제목 없음")
                score = resume.get("score", 0.0)
                chunk_preview = resume.get("chunk_text", "")[:ResumeConstants.MAX_PREVIEW_LENGTH]
                message += ResumeConstants.SEARCH_RESULT_ITEM.format(
                    idx=idx, title=title, score=score, preview=chunk_preview
                )
            
            if len(resumes) > ResumeConstants.MAX_DISPLAY_RESULTS:
                message += ResumeConstants.SEARCH_RESULTS_MORE.format(
                    count=len(resumes) - ResumeConstants.MAX_DISPLAY_RESULTS
                )
            message += ResumeConstants.SEARCH_RESULTS_FOOTER
        else:
            message = ResumeConstants.SEARCH_EMPTY_MESSAGE
        
        return {
            "ok": True,
            "message": message,
            "resumes": resumes,
        }
    except Exception as e:
        error_result = _handle_error(e, ResumeConstants.ERROR_SEARCH)
        error_result["resumes"] = []
        return error_result


async def recommend_talents(
    query: str,
    state: OrchestratorState,
    limit: int = 5,
    exclude_titles: Optional[set] = None
) -> Dict[str, Any]:
    """인재 추천.
    
    Args:
        query: 사용자 쿼리 (예: "백엔드 개발자 추천", "데이터 사이언티스트")
        state: 오케스트레이터 상태
        limit: 추천 개수
        exclude_titles: 제외할 이력서 제목 집합 (중복 추천 방지용)
    
    Returns:
        추천 결과 딕셔너리
    """
    print(f"⭐ 인재 추천: {query}")
    if exclude_titles:
        print(f"  → 제외할 인재: {len(exclude_titles)}명")
    
    try:
        # 쿼리에서 추천 조건 추출 (예: "Python 개발자 추천" -> "Python 개발자")
        recommendation_query = query.replace("추천", "").replace("추천해줘", "").replace("추천해", "").strip()
        if not recommendation_query:
            recommendation_query = query  # 추천 키워드만 있으면 원본 쿼리 사용
        
        # 제외할 인재가 있으면 더 많이 검색 후 필터링
        search_limit = limit * ResumeConstants.SEARCH_MULTIPLIER if exclude_titles else limit
        
        # 벡터 DB에서 이력서 검색 (추천은 상위 결과만)
        all_resumes = await search_resumes_from_vector_db(
            query=recommendation_query,
            limit=search_limit
        )
        
        # 제외할 인재 필터링
        if exclude_titles:
            resumes = [r for r in all_resumes if r.get("document_title", "") not in exclude_titles]
            resumes = resumes[:limit]  # 필요한 개수만큼만
            print(f"  → 필터링 후: {len(resumes)}명 (제외: {len(all_resumes) - len(resumes)}명)")
        else:
            resumes = all_resumes
        
        if resumes:
            message = ResumeConstants.RECOMMEND_SUCCESS_MESSAGE.format(count=len(resumes))
        else:
            message = ResumeConstants.RECOMMEND_EMPTY_MESSAGE
        
        return {
            "ok": True,
            "message": message,
            "resumes": resumes,
        }
    except Exception as e:
        error_result = _handle_error(e, ResumeConstants.ERROR_RECOMMEND)
        error_result["resumes"] = []
        return error_result


async def summarize_resume(
    query: str,
    resume: Optional[Dict[str, Any]],
    state: OrchestratorState
) -> Dict[str, Any]:
    """이력서 핵심 요약.
    
    Args:
        query: 사용자 쿼리
        resume: 요약할 이력서 딕셔너리 (document_title, chunk_text 등 포함)
        state: 오케스트레이터 상태
    
    Returns:
        요약 결과 딕셔너리
    """
    print(f"📝 이력서 요약")
    
    if not resume:
        return {
            "ok": False,
            "message": ResumeConstants.MESSAGE_NO_RESUME,
            "summary": "",
        }
    
    try:
        from langchain_openai import ChatOpenAI
        
        # 이력서 전체 텍스트 수집
        resume_text = await _collect_full_resume_text(resume)
        
        if not resume_text:
            return {
                "ok": False,
                "message": ResumeConstants.MESSAGE_NO_RESUME_CONTENT,
                "summary": "",
            }
        
        document_title = resume.get("document_title", "")
        
        # LLM으로 요약 생성
        llm = ChatOpenAI(
            model=ResumeConstants.LLM_MODEL,
            temperature=ResumeConstants.LLM_TEMPERATURE,
            max_tokens=ResumeConstants.LLM_MAX_TOKENS,
        )
        
        # YAML에서 프롬프트 로드
        summary_prompt = _get_summarize_resume_prompt(document_title, resume_text)
        
        response = await asyncio.to_thread(llm.invoke, summary_prompt)
        summary_text = response.content if hasattr(response, 'content') else str(response)
        
        # 요약 텍스트를 구조화된 형식으로 포맷팅
        formatted_summary = _format_summary_text(summary_text)
        
        # 표시용 제목 추출
        display_title = _extract_display_title(document_title, formatted_summary)
        
        message = f"📋 **{display_title}** 이력서 핵심 요약:\n\n{formatted_summary}"
        
        return {
            "ok": True,
            "message": message,
            "summary": summary_text,
        }
    except Exception as e:
        error_result = _handle_error(e, ResumeConstants.ERROR_SUMMARY)
        error_result["summary"] = ""
        return error_result


def _format_resume_text(resume_text: str, document_title: str) -> str:
    """이력서 텍스트를 구조화된 형식으로 포맷팅.
    
    Args:
        resume_text: 원본 이력서 텍스트
        document_title: 이력서 제목
    
    Returns:
        포맷팅된 이력서 텍스트
    """
    import re
    
    # "=== RESUME XX ===" 패턴 제거
    resume_text = re.sub(ResumeConstants.RESUME_HEADER_PATTERN, '', resume_text, flags=re.IGNORECASE)
    
    lines = resume_text.split('\n')
    formatted_lines = []
    
    # 기본 정보 추출 및 헤더 구성
    name, contact_info, status = _extract_basic_info(lines)
    
    if name:
        formatted_lines.extend([f"**{name}**", ''])
    
    if contact_info:
        formatted_lines.extend([" | ".join(contact_info), ''])
    
    if status:
        formatted_lines.extend([status, ''])
    
    # 섹션별 파싱
    current_section = None
    in_project = False
    in_career = False
    project_name = None
    career_company = None
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            if formatted_lines and formatted_lines[-1].strip():
                formatted_lines.append('')
            continue
        
        # 섹션 헤더 감지
        section_found = False
        for section_name, pattern in ResumeConstants.SECTION_PATTERNS.items():
            if re.match(pattern, line_stripped, re.IGNORECASE):
                if formatted_lines and formatted_lines[-1].strip():
                    formatted_lines.append('')
                formatted_lines.append(f"**{line_stripped}**")
                formatted_lines.append('')
                current_section = section_name
                section_found = True
                in_project = '프로젝트' in section_name
                in_career = '경력' in section_name
                break
        
        if section_found:
            continue
        
        # 구분선 제거
        if re.match(ResumeConstants.DIVIDER_PATTERN, line_stripped):
            continue
        
        # 프로젝트명 감지 (대시나 특수문자로 시작하는 경우)
        if in_project and (line_stripped.startswith('-') or 
                          (len(line_stripped) > ResumeConstants.PROJECT_NAME_MIN_LENGTH and 
                           not any(line_stripped.startswith(kw) for kw in ResumeConstants.ROLE_KEYWORDS) and 
                           not ':' in line_stripped[:ResumeConstants.PROJECT_KEYWORD_CHECK_LENGTH])):
            # 이전 프로젝트가 있으면 빈 줄 추가
            if project_name and formatted_lines and formatted_lines[-1].strip():
                formatted_lines.append('')
            project_name = line_stripped.lstrip('- ').strip()
            formatted_lines.append(f"**{project_name}**")
            formatted_lines.append('')
            continue
        
        # 경력 회사명 감지
        if in_career and ResumeConstants.CAREER_SEPARATOR in line_stripped and not career_company:
            # "B 소프트 – 프론트엔드 개발자" 형식
            parts = line_stripped.split(ResumeConstants.CAREER_SEPARATOR)
            if len(parts) >= 2:
                career_company = parts[0].strip()
                role = parts[1].strip()
                formatted_lines.append(f"**{career_company}** {ResumeConstants.CAREER_SEPARATOR} {role}")
                formatted_lines.append('')
                continue
        
        # 기간 정보 (년도 패턴)
        if re.match(ResumeConstants.YEAR_RANGE_PATTERN, line_stripped) or \
           re.match(ResumeConstants.YEAR_CURRENT_PATTERN, line_stripped):
            formatted_lines.append(line_stripped)
            formatted_lines.append('')
            continue
        
        # 역할, 성과 등 키워드로 시작하는 줄
        if any(line_stripped.startswith(kw) for kw in ResumeConstants.ROLE_KEYWORDS):
            formatted_lines.append(line_stripped)
            continue
        
        # 기술 스택 카테고리 (콜론으로 끝나거나 특정 패턴)
        if current_section == '기술 스택' and ':' in line_stripped:
            formatted_lines.append(line_stripped)
            continue
        
        # 일반 텍스트 처리
        # 들여쓰기가 필요한 경우 (역할, 성과 하위 항목)
        if any(line_stripped.startswith(bp) for bp in ResumeConstants.BULLET_POINTS):
            formatted_lines.append(f"  {line_stripped}")
        elif re.match(ResumeConstants.NUMBERED_LIST_PATTERN, line_stripped):
            formatted_lines.append(f"  {line_stripped}")
        else:
            # 긴 줄은 적절히 줄바꿈
            formatted_lines.extend(_format_long_line(line_stripped))
    
    # 최종 정리: 연속된 빈 줄 제거
    cleaned_lines = _clean_empty_lines(formatted_lines)
    return '\n'.join(cleaned_lines).strip()


async def get_resume_detail(
    resume: Dict[str, Any],
    state: OrchestratorState
) -> Dict[str, Any]:
    """이력서 상세 정보 조회 (요약 없이 전체 내용 표시).
    
    Args:
        resume: 이력서 딕셔너리 (document_title, chunk_text 등 포함)
        state: 오케스트레이터 상태
    
    Returns:
        상세 정보 딕셔너리
    """
    print(f"📄 이력서 상세 정보 조회")
    
    if not resume:
        return {
            "ok": False,
            "message": ResumeConstants.MESSAGE_NO_RESUME_INFO,
        }
    
    try:
        # 이력서 전체 텍스트 수집
        resume_text = await _collect_full_resume_text(resume)
        
        if not resume_text:
            return {
                "ok": False,
                "message": ResumeConstants.MESSAGE_NO_RESUME_CONTENT,
            }
        
        document_title = resume.get("document_title", "")
        
        # 상세 정보를 깔끔하게 포맷팅
        formatted_text = _format_resume_text(resume_text, document_title)
        
        # 표시용 제목 추출
        display_title = _extract_display_title(document_title, formatted_text)
        
        message = f"**{display_title}**\n\n{formatted_text}"
        
        return {
            "ok": True,
            "message": message,
        }
    except Exception as e:
        return _handle_error(e, ResumeConstants.ERROR_DETAIL)


async def get_all_chunks_for_resume(document_title: str) -> List[str]:
    """특정 이력서의 모든 청크를 가져옵니다.
    
    Args:
        document_title: 이력서 제목 (document_title 필드)
    
    Returns:
        청크 텍스트 리스트
    """
    try:
        from pymilvus import Collection
        
        # Milvus 컬렉션 가져오기
        collection_name = os.getenv("RECRUITMENT_COLLECTION") or ResumeConstants.DEFAULT_COLLECTION_NAME
        collection = ensure_milvus_collection(collection_name)
        
        # document_title로 필터링하여 모든 청크 조회
        results = collection.query(
            expr=f'document_title == "{document_title}"',
            output_fields=["chunk_text"],
            limit=ResumeConstants.DEFAULT_MAX_CHUNKS
        )
        
        chunks = [result.get("chunk_text", "") for result in results if result.get("chunk_text")]
        return chunks
    except Exception as e:
        print(f"⚠️ {ResumeConstants.ERROR_CHUNK_FETCH.format(error=e)}")
        return []