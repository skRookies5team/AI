"""이력서 검색 및 인재 추천 에이전트 - 벡터 DB 기반 검색."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from functools import lru_cache
from collections import OrderedDict
from pathlib import Path

try:
    from pymilvus import connections, Collection, utility
    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False
    connections = None
    Collection = None
    utility = None

from agents.embedding_utils import get_embedding_generator

logger = logging.getLogger(__name__)


# ==================== 상수 정의 ====================

# 기본 설정값
DEFAULT_MILVUS_HOST = "175.114.229.152"
DEFAULT_MILVUS_PORT = 19530
DEFAULT_COLLECTION_NAME = "hr_data_embeddings"
DEFAULT_MILVUS_METRIC_TYPE = "L2"
DEFAULT_MILVUS_NPROBE = 10

# 검색 관련 상수
DEFAULT_SEARCH_LIMIT = 5
SEARCH_LIMIT_MULTIPLIER_DIFFERENT_REQUEST = 3  # 다른 인재 요청 시 배수
SEARCH_LIMIT_MULTIPLIER_NORMAL = 2  # 일반 요청 시 배수
MAX_SKILLS_COUNT = 10
MAX_POSITION_LENGTH = 20
MAX_NAME_LENGTH = 5
MIN_SKILL_LENGTH = 2
MAX_SKILL_LENGTH = 30

# 키워드 리스트
DIFFERENT_REQUEST_KEYWORDS = ["다른", "다시", "추가", "더", "another", "different", "more"]
SENIOR_KEYWORDS = ["경력직", "경력", "시니어", "senior", "experienced"]
JUNIOR_KEYWORDS = ["신입", "주니어", "junior", "entry"]
QUERY_CLEANUP_PATTERNS = r'\s*(추천|검색|찾아|해줘|해달라|요청).*$'

# 경력년수 범위 패턴
EXPERIENCE_RANGE_PATTERNS = [
    # "3년 이상", "5년 이상", "10 years or more"
    (r'(\d+\.?\d*)\s*년?\s*(?:이상|이상의|over|more|or\s+more)', 'min'),
    (r'(\d+\.?\d*)\s*years?\s*(?:이상|over|more|or\s+more)', 'min'),
    # "5년 이하", "3년 미만", "under 5 years"
    (r'(\d+\.?\d*)\s*년?\s*(?:이하|미만|under|less)', 'max'),
    (r'(\d+\.?\d*)\s*years?\s*(?:이하|under|less)', 'max'),
    # "3-5년", "3~5년", "3 to 5 years", "3년부터 5년까지"
    (r'(\d+\.?\d*)\s*(?:[-~부터]|to|~)\s*(\d+\.?\d*)\s*년?', 'range'),
    (r'(\d+\.?\d*)\s*years?\s*(?:[-~부터]|to|~)\s*(\d+\.?\d*)\s*years?', 'range'),
    # "3년 이상 5년 이하", "3 to 5 years experience"
    (r'(\d+\.?\d*)\s*년?\s*이상\s*(\d+\.?\d*)\s*년?\s*이하', 'range'),
    (r'(\d+\.?\d*)\s*years?\s*(?:and|to)\s*(\d+\.?\d*)\s*years?', 'range'),
    # "약 3년", "about 3 years"
    (r'약\s*(\d+\.?\d*)\s*년?', 'exact'),
    (r'about\s*(\d+\.?\d*)\s*years?', 'exact'),
    # "4년", ", 4년", "4 년" (콤마와 공백 허용, 정확한 년수)
    (r'[,\s]*(\d+\.?\d*)\s*년', 'exact'),
]

# 필드명 후보 목록
POSSIBLE_NAME_FIELDS = ["name", "이름", "applicant_name", "candidate_name", "person_name"]
POSSIBLE_POSITION_FIELDS = ["position", "직무", "job_title", "직책", "job_position"]
POSSIBLE_EXPERIENCE_FIELDS = [
    "experience", "경력", "career", "경력사항", 
    "career_year", "경력년수", "years_of_experience"
]
POSSIBLE_SKILLS_FIELDS = [
    "skills", "기술", "skill", "기술스택", "tech_stack", "skill_set"
]
POSSIBLE_CONTENT_FIELDS = [
    "content", "내용", "이력서내용", "resume_content", 
    "text", "이력서", "full_content"
]
POSSIBLE_RESUME_ID_FIELDS = ["resume_id", "id"]

# 정규표현식 패턴
NAME_PATTERN_KOREAN = r'^[가-힣]{2,5}$'
NAME_PATTERN_ENGLISH = r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?$'
POSITION_PATTERN = r'([가-힣a-zA-Z\s]+(?:기획자|개발자|디자이너|엔지니어|매니저|관리자|전문|지망|경력|백엔드|프론트엔드|데이터|AI|인공지능))'
EXPERIENCE_PATTERN_SUMMARY = r'(약\s*[\d.]+\s*년간|[\d.]+\s*년간|[\d.]+\s*년\s*경험|[\d.]+\s*년|신입|주니어|시니어)'
EXPERIENCE_PATTERN_CAREER = r'(신입|주니어|시니어|[\d.]+년|경력\s*[\d.]+년|[\d.]+년\s*경력)'
EXPERIENCE_YEARS_PATTERNS = [
    r'약\s*[\d.]+\s*년간',
    r'[\d.]+\s*년간',
    r'[\d.]+\s*년\s*경험',
    r'[\d.]+\s*년\s*재직',
    r'경력\s*[\d.]+\s*년',
    r'[\d.]+\s*년\s*이상',
    r'[\d.]+\s*years?\s*experience',
    r'[\d.]+\s*years?\s*of',
]
JUNIOR_KEYWORD_PATTERNS = [r'신입', r'entry\s*level', r'주니어', r'junior']
SENIOR_KEYWORD_PATTERNS = [
    r'시니어', r'senior', r'경력직', r'experienced', r'[\d.]+년\s*이상'
]
SKILL_EXCLUDE_WORDS = ['년', '년간', '재직', '년도', 'year', 'years', 'of', 'the', 'and', 'or']
SKILL_PATTERN_COMMA = r'\b([A-Z][a-zA-Z0-9#+\-]*(?:\s+[A-Z][a-zA-Z0-9#+\-]*)*)\b'
SKILL_PATTERN_LINE = r'^[A-Za-z#+\-]+'
SKILL_PATTERN_BRACKET = r'\(([A-Za-z,\s]+)\)'

# 기본값
DEFAULT_NAME = "이름 없음"
DEFAULT_POSITION = "포지션 미지정"
DEFAULT_EXPERIENCE = "경력 정보 없음"

# 포지션 키워드 (정확한 포지션명만)
POSITION_KEYWORDS = {
    "백엔드": ["백엔드", "벡엔드", "backend", "back-end"],
    "프론트엔드": ["프론트엔드", "프론트", "frontend", "front-end"],
    "풀스택": ["풀스택", "fullstack", "full-stack"],
    "데이터": ["데이터 분석", "데이터 엔지니어", "data analyst", "data engineer"],
    "AI": ["ai 엔지니어", "인공지능", "머신러닝"],
    "데브옵스": ["devops", "dev-ops", "데브옵스"],
    "모바일": ["모바일", "mobile", "ios", "android", "앱 개발"],
    "QA": ["qa", "테스트"],
    "기획": ["기획자", "pm", "product"],
    "디자인": ["디자이너", "designer"],
}

# 기술 스택 키워드 (일반적인 기술들)
COMMON_TECH_KEYWORDS = [
    "python", "java", "javascript", "typescript", "react", "vue", "angular",
    "node.js", "spring", "django", "flask", "express", "mysql", "postgresql",
    "mongodb", "redis", "docker", "kubernetes", "aws", "gcp", "azure",
    "git", "jenkins", "ci/cd", "graphql", "rest", "microservices"
]

# 임베딩 캐시 설정
EMBEDDING_CACHE_SIZE = 100  # 최대 캐시 크기


# ==================== 유틸리티 함수 ====================

def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    try:
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent.parent.parent
        env_path = project_root / ".env"

        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass


_load_env_file()


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 안전 조회."""
    return os.environ.get(name, default)


# ==================== 전역 변수 ====================

_collection: Optional[Collection] = None
_embedding_model = None

# 임베딩 캐시 (LRU 방식)
_embedding_cache: OrderedDict[str, List[float]] = OrderedDict()


# ==================== 초기화 함수 ====================

def _initialize_milvus() -> None:
    """Milvus 연결 초기화."""
    global _collection
    
    if not MILVUS_AVAILABLE:
        logger.error("pymilvus가 설치되지 않았습니다.")
        _collection = None
        return
    
    try:
        milvus_host = _get_env("HOST") or _get_env("MILVUS_HOST") or DEFAULT_MILVUS_HOST
        milvus_port_str = _get_env("MILVUS_PORT") or str(DEFAULT_MILVUS_PORT)
        milvus_port = int(milvus_port_str.strip()) if milvus_port_str else DEFAULT_MILVUS_PORT
        
        logger.info(f"Milvus 연결 시도: {milvus_host}:{milvus_port}")
        
        try:
            if not connections.has_connection("default"):
                connections.connect("default", host=milvus_host, port=milvus_port)
                logger.info(f"Milvus 연결 성공: {milvus_host}:{milvus_port}")
            else:
                logger.debug("Milvus 이미 연결되어 있음")
        except Exception as e:
            logger.error(f"Milvus 연결 실패: {e}")
            _collection = None
            return
        
        collection_name = _get_env("RECRUITMENT_COLLECTION") or DEFAULT_COLLECTION_NAME
        logger.info(f"컬렉션 이름: {collection_name}")
        
        try:
            if utility.has_collection(collection_name):
                logger.info(f"컬렉션 '{collection_name}' 존재 확인됨")
                
                _collection = Collection(collection_name)
                _collection.load()
                
                num_entities = _collection.num_entities
                logger.info(
                    f"Recruitment 컬렉션 로드 완료: {collection_name} "
                    f"(엔티티 수: {num_entities})"
                )
            else:
                available_collections = utility.list_collections()
                error_msg = (
                    f"컬렉션 '{collection_name}'이 존재하지 않습니다. "
                    f"사용 가능한 컬렉션: {', '.join(available_collections) if available_collections else '없음'}"
                )
                logger.warning(error_msg)
                _collection = None
        except Exception as e:
            logger.error(f"컬렉션 로드 실패: {e}")
            _collection = None
            
    except Exception as e:
        logger.error(f"Milvus 초기화 중 오류 발생: {e}", exc_info=True)
        _collection = None


def _initialize_embedding_model() -> None:
    """임베딩 모델 초기화."""
    global _embedding_model
    
    if _embedding_model is None:
        try:
            _embedding_model = get_embedding_generator()
            logger.info("임베딩 모델 초기화 완료")
        except Exception as e:
            logger.error(f"임베딩 모델 초기화 실패: {e}")
            _embedding_model = None


# ==================== 쿼리 파싱 함수 ====================

def _extract_position_keywords(query: str) -> List[str]:
    """
    쿼리에서 포지션 키워드를 추출합니다.
    
    Args:
        query: 검색 쿼리
        
    Returns:
        추출된 포지션 키워드 리스트 (예: ["백엔드", "프론트엔드"])
    """
    query_lower = query.lower()
    found_positions = []
    
    for position_name, keywords in POSITION_KEYWORDS.items():
        if any(keyword in query_lower for keyword in keywords):
            found_positions.append(position_name)
    
    return found_positions


def _extract_skill_keywords(query: str) -> List[str]:
    """
    쿼리에서 기술 스택 키워드를 추출합니다.
    
    Args:
        query: 검색 쿼리
        
    Returns:
        추출된 기술 스택 키워드 리스트 (예: ["python", "react"])
    """
    query_lower = query.lower()
    found_skills = []
    
    # 일반적인 기술 스택 키워드 확인
    for tech in COMMON_TECH_KEYWORDS:
        if tech in query_lower:
            found_skills.append(tech)
    
    # 추가 패턴: "Python 할 수 있는", "React 경험자" 등
    skill_patterns = [
        r'([a-zA-Z#+\-]+(?:\.[a-zA-Z]+)?)\s*(?:할\s*수\s*있는|경험자|할줄아는|사용가능)',
        r'([a-zA-Z#+\-]+(?:\.[a-zA-Z]+)?)\s*사용',
        r'([a-zA-Z#+\-]+(?:\.[a-zA-Z]+)?)\s*개발',
    ]
    
    for pattern in skill_patterns:
        matches = re.findall(pattern, query, re.IGNORECASE)
        for match in matches:
            skill = match.lower()
            if skill not in found_skills and len(skill) >= 2:
                found_skills.append(skill)
    
    return found_skills


def _extract_experience_range(query: str) -> Tuple[Optional[float], Optional[float]]:
    """
    쿼리에서 경력년수 범위를 추출합니다.
    
    Args:
        query: 검색 쿼리
        
    Returns:
        (min_years, max_years) - None이면 해당 조건 없음
        예: ("3년 이상" -> (3.0, None), "5-10년" -> (5.0, 10.0))
    """
    min_years: Optional[float] = None
    max_years: Optional[float] = None
    
    for pattern, pattern_type in EXPERIENCE_RANGE_PATTERNS:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            if pattern_type == 'min':
                min_years = float(match.group(1))
                break
            elif pattern_type == 'max':
                max_years = float(match.group(1))
                break
            elif pattern_type == 'range':
                min_years = float(match.group(1))
                max_years = float(match.group(2))
                break
            elif pattern_type == 'exact':
                exact_years = float(match.group(1))
                # N년 = N.0년 ~ (N+1).0년 미만
                # 예: 1년 = 1.0~1.92년 (12~23개월), 4년 = 4.0~4.92년 (48~59개월)
                min_years = exact_years
                max_years = exact_years + 0.92  # N년 11개월까지 (약 N년대)
                break
    
    return min_years, max_years


def _parse_query_filters(
    query: str
) -> Tuple[bool, bool, bool, Optional[float], Optional[float], List[str], List[str]]:
    """
    쿼리에서 필터 조건을 추출합니다.
    
    Args:
        query: 검색 쿼리
        
    Returns:
        (is_different_request, is_senior_only, is_junior_only, min_years, max_years, 
         position_keywords, skill_keywords)
    """
    query_lower = query.lower()
    
    is_different_request = any(
        keyword in query_lower for keyword in DIFFERENT_REQUEST_KEYWORDS
    )
    is_senior_only = any(
        keyword in query_lower for keyword in SENIOR_KEYWORDS
    )
    is_junior_only = any(
        keyword in query_lower for keyword in JUNIOR_KEYWORDS
    )
    
    # "경력직만"과 "신입만"이 동시에 있으면 둘 다 무시
    if is_senior_only and is_junior_only:
        is_senior_only = False
        is_junior_only = False
    
    # 경력년수 범위 추출
    min_years, max_years = _extract_experience_range(query)
    
    # 포지션 키워드 추출
    position_keywords = _extract_position_keywords(query)
    
    # 기술 스택 키워드 추출
    skill_keywords = _extract_skill_keywords(query)
    
    return (
        is_different_request, is_senior_only, is_junior_only, 
        min_years, max_years, position_keywords, skill_keywords
    )


def _restore_previous_query_filters(
    state: Optional[Dict[str, Any]], 
    is_different_request: bool
) -> Tuple[Optional[str], bool, bool, Optional[float], Optional[float], List[str], List[str]]:
    """
    이전 검색 쿼리 및 필터 조건을 복원합니다.
    
    Args:
        state: OrchestratorState
        is_different_request: 다른 인재 요청 여부
        
    Returns:
        (last_query, last_senior_only, last_junior_only, last_min_years, last_max_years,
         last_position_keywords, last_skill_keywords)
    """
    last_query = None
    last_senior_only = False
    last_junior_only = False
    last_min_years: Optional[float] = None
    last_max_years: Optional[float] = None
    last_position_keywords: List[str] = []
    last_skill_keywords: List[str] = []
    
    if is_different_request and state:
        recruitment_context = state.get("recruitment_context", {})
        if isinstance(recruitment_context, dict):
            resume_search = recruitment_context.get("resume_search", {})
            last_query = resume_search.get("last_query", "")
            
            if last_query:
                last_query_lower = last_query.lower()
                last_senior_only = any(
                    keyword in last_query_lower for keyword in SENIOR_KEYWORDS
                )
                last_junior_only = any(
                    keyword in last_query_lower for keyword in JUNIOR_KEYWORDS
                )
                
                if last_senior_only and last_junior_only:
                    last_senior_only = False
                    last_junior_only = False
                
                # 경력년수 범위도 복원
                last_min_years, last_max_years = _extract_experience_range(last_query)
                
                # 포지션 및 기술 스택 키워드도 복원
                last_position_keywords = _extract_position_keywords(last_query)
                last_skill_keywords = _extract_skill_keywords(last_query)
                
                logger.debug(
                    f"이전 쿼리 복원: '{last_query}' "
                    f"(경력직 필터: {last_senior_only}, 신입 필터: {last_junior_only}, "
                    f"경력년수: {last_min_years}~{last_max_years}, "
                    f"포지션: {last_position_keywords}, 기술: {last_skill_keywords})"
                )
    
    return (
        last_query, last_senior_only, last_junior_only, 
        last_min_years, last_max_years, last_position_keywords, last_skill_keywords
    )


def _build_search_query(
    query: str,
    is_different_request: bool,
    last_query: Optional[str]
) -> str:
    """
    검색 쿼리를 생성합니다 (이전 쿼리 복원 포함).
    
    Args:
        query: 현재 쿼리
        is_different_request: 다른 인재 요청 여부
        last_query: 이전 쿼리
        
    Returns:
        검색에 사용할 쿼리
    """
    if is_different_request and last_query:
        cleaned_last_query = re.sub(
            QUERY_CLEANUP_PATTERNS, 
            '', 
            last_query, 
            flags=re.IGNORECASE
        ).strip()
        
        if cleaned_last_query:
            logger.debug(f"검색 쿼리 복원: '{query}' -> '{cleaned_last_query}'")
            return cleaned_last_query
    
    return query


def _get_excluded_resume_ids(
    state: Optional[Dict[str, Any]],
    is_different_request: bool
) -> set:
    """
    제외할 이력서 ID 목록을 가져옵니다.
    
    Args:
        state: OrchestratorState
        is_different_request: 다른 인재 요청 여부
        
    Returns:
        제외할 resume_id 집합
    """
    excluded_ids = set()
    
    if is_different_request and state:
        recruitment_context = state.get("recruitment_context", {})
        if isinstance(recruitment_context, dict):
            resume_search = recruitment_context.get("resume_search", {})
            last_results = resume_search.get("last_results", [])
            
            for resume in last_results:
                resume_id = resume.get("resume_id") or resume.get("id")
                if resume_id:
                    excluded_ids.add(str(resume_id))
            
            if excluded_ids:
                logger.debug(f"이전 검색 결과 제외: {len(excluded_ids)}개 인재 ID")
    
    return excluded_ids


# ==================== 필드 추출 함수 ====================

def _extract_field_from_entity(
    entity_dict: Dict[str, Any],
    possible_fields: List[str],
    default_value: str
) -> str:
    """
    엔티티에서 필드를 추출합니다.
    
    Args:
        entity_dict: 엔티티 딕셔너리
        possible_fields: 가능한 필드명 목록
        default_value: 기본값
        
    Returns:
        추출된 필드값
    """
    for field in possible_fields:
        value = entity_dict.get(field)
        if value:
            return str(value)
    return default_value


def _parse_name_position_from_title(document_title: str) -> Tuple[Optional[str], Optional[str]]:
    """
    document_title에서 '이름 - 직무 이력서' 형식을 파싱합니다.
    
    Args:
        document_title: 문서 제목 (예: "윤가은 - 데이터 엔지니어 이력서")
        
    Returns:
        (name, position) 튜플 - 파싱 실패 시 (None, None)
    """
    if not document_title:
        return None, None
    
    # '이름 - 직무 이력서' 패턴 매칭
    pattern = r'^(.+?)\s*-\s*(.+?)\s*이력서'
    match = re.match(pattern, document_title.strip(), re.IGNORECASE)
    
    if match:
        name = match.group(1).strip()
        position = match.group(2).strip()
        
        # 이름과 직무가 유효한지 확인
        if name and position and len(name) >= 2:
            return name, position
    
    return None, None


def _extract_name_from_content(content_lines: List[str]) -> str:
    """
    content에서 이름을 추출합니다.
    
    Args:
        content_lines: content를 줄 단위로 분리한 리스트
        
    Returns:
        추출된 이름
    """
    # 먼저 첫 줄에서 "이름 - 직무 이력서" 패턴 확인
    if content_lines:
        first_line = content_lines[0].strip()
        parsed_name, _ = _parse_name_position_from_title(first_line)
        if parsed_name:
            return parsed_name
    
    for i, line in enumerate(content_lines[:10]):
        line = line.strip()
        
        # "===" 헤더 건너뛰기
        if "===" in line or not line:
            continue
        
        # 전화번호나 이메일이 있는 줄은 건너뛰기
        if '@' in line or len(re.findall(r'\d{2,4}', line)) > 2:
            continue
        
        # 이름 패턴 확인
        if re.match(NAME_PATTERN_KOREAN, line):
            return line
        elif re.match(NAME_PATTERN_ENGLISH, line):
            return line
    
    return DEFAULT_NAME


def _extract_position_from_content(content_lines: List[str]) -> str:
    """
    content에서 포지션을 추출합니다.
    
    Args:
        content_lines: content를 줄 단위로 분리한 리스트
        
    Returns:
        추출된 포지션
    """
    # 먼저 첫 줄에서 "이름 - 직무 이력서" 패턴 확인
    if content_lines:
        first_line = content_lines[0].strip()
        _, parsed_position = _parse_name_position_from_title(first_line)
        if parsed_position:
            return parsed_position
    
    # "요약" 섹션에서 찾기
    for i, line in enumerate(content_lines):
        if "요약" in line and i + 1 < len(content_lines):
            next_lines = ' '.join(content_lines[i+1:i+4])
            pos_match = re.search(POSITION_PATTERN, next_lines)
            if pos_match:
                position = pos_match.group(1).strip()
                # "년 차" 같은 패턴 제거
                position = re.sub(r'\s*\d+\s*년\s*차\s*', '', position).strip()
                position = re.sub(r'\s*(지망|의|로서).*$', '', position).strip()
                if len(position) > MAX_POSITION_LENGTH:
                    position = ' '.join(position.split()[:3])
                return position
    
    # "현재 구직 중" 앞뒤에서 찾기
    for i, line in enumerate(content_lines):
        if "구직" in line or "현재" in line:
            search_lines = ' '.join(content_lines[max(0, i-2):i+3])
            pos_match = re.search(POSITION_PATTERN, search_lines)
            if pos_match:
                position = pos_match.group(1).strip()
                # "년 차" 같은 패턴 제거
                position = re.sub(r'\s*\d+\s*년\s*차\s*', '', position).strip()
                position = re.sub(r'\s*(지망|의|로서).*$', '', position).strip()
                if len(position) > MAX_POSITION_LENGTH:
                    position = ' '.join(position.split()[:3])
                return position
    
    return DEFAULT_POSITION


def _extract_experience_from_content(content_lines: List[str]) -> str:
    """
    content에서 경력을 추출합니다.
    
    Args:
        content_lines: content를 줄 단위로 분리한 리스트
        
    Returns:
        추출된 경력 정보
    """
    # "요약" 섹션에서 찾기
    for i, line in enumerate(content_lines):
        if "요약" in line or "summary" in line.lower():
            summary_lines = ' '.join(content_lines[i:i+5])
            exp_match = re.search(EXPERIENCE_PATTERN_SUMMARY, summary_lines, re.IGNORECASE)
            if exp_match:
                exp_text = exp_match.group(1).strip()
                if re.search(r'\d+', exp_text):
                    return exp_text
                elif "신입" in exp_text.lower() or "junior" in exp_text.lower():
                    return "신입"
                break
    
    # "주요 경력" 섹션에서 찾기
    for i, line in enumerate(content_lines):
        if "경력" in line or "career" in line.lower():
            exp_match = re.search(EXPERIENCE_PATTERN_CAREER, line, re.IGNORECASE)
            if exp_match:
                return exp_match.group(1).strip()
            
            # 다음 줄 확인
            if i + 1 < len(content_lines):
                next_line = content_lines[i+1]
                exp_match = re.search(EXPERIENCE_PATTERN_CAREER, next_line, re.IGNORECASE)
                if exp_match:
                    return exp_match.group(1).strip()
    
    return DEFAULT_EXPERIENCE


def _extract_skills_from_content(content_lines: List[str]) -> List[str]:
    """
    content에서 기술 스택을 추출합니다.
    
    Args:
        content_lines: content를 줄 단위로 분리한 리스트
        
    Returns:
        추출된 기술 스택 리스트
    """
    for i, line in enumerate(content_lines):
        if any(keyword in line.lower() for keyword in ["기술", "스택", "skill", "tech"]):
            skill_lines = '\n'.join(content_lines[i:i+10])
            
            # 쉼표로 구분된 기술들
            comma_skills = re.findall(SKILL_PATTERN_COMMA, skill_lines)
            
            # 줄바꿈으로 구분된 기술들
            line_skills = []
            for skill_line in content_lines[i+1:i+15]:
                skill_line = skill_line.strip()
                if (skill_line and 
                    not re.match(r'^[\d\s\-~년]+$', skill_line) and 
                    len(skill_line) < MAX_SKILL_LENGTH):
                    if re.match(SKILL_PATTERN_LINE, skill_line):
                        line_skills.append(skill_line)
            
            # 괄호 안의 기술들
            bracket_skills = re.findall(SKILL_PATTERN_BRACKET, skill_lines)
            
            # 모든 기술 합치기
            all_skills = []
            for skill_list in [comma_skills, line_skills, bracket_skills]:
                for skill in skill_list:
                    if ',' in skill:
                        all_skills.extend([s.strip() for s in skill.split(',')])
                    else:
                        all_skills.append(skill.strip())
            
            # 필터링
            filtered_skills = []
            for skill in all_skills:
                skill = skill.strip()
                if (len(skill) >= MIN_SKILL_LENGTH and
                    not re.match(r'^[\d\s\-~.]+$', skill) and
                    skill.lower() not in SKILL_EXCLUDE_WORDS and
                    not skill.isdigit()):
                    filtered_skills.append(skill)
            
            if filtered_skills:
                # 중복 제거 및 최대 개수 제한
                seen = set()
                unique_skills = []
                for skill in filtered_skills:
                    skill_lower = skill.lower()
                    if skill_lower not in seen and len(unique_skills) < MAX_SKILLS_COUNT:
                        seen.add(skill_lower)
                        unique_skills.append(skill)
                return unique_skills
    
    return []


# ==================== 필터링 함수 ====================

def _extract_years_from_experience(experience: str, content: str) -> Optional[float]:
    """
    경력 정보에서 경력년수 숫자값을 추출합니다.
    
    Args:
        experience: 경력 정보 문자열
        content: 이력서 내용 (fallback)
        
    Returns:
        추출된 경력년수 (None이면 추출 실패)
    """
    # experience 필드에서 숫자 추출
    year_match = re.search(r'(\d+\.?\d*)\s*년', experience, re.IGNORECASE)
    if year_match:
        try:
            return float(year_match.group(1))
        except ValueError:
            pass
    
    # content에서 경력년수 추출
    if content:
        for pattern in EXPERIENCE_YEARS_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                # 숫자 부분 추출
                number_match = re.search(r'(\d+\.?\d*)', match.group(0))
                if number_match:
                    try:
                        return float(number_match.group(1))
                    except ValueError:
                        continue
    
    return None


def _check_experience_in_content(content: str) -> Tuple[bool, bool, bool, Optional[float]]:
    """
    content에서 경력 정보를 확인합니다.
    
    Args:
        content: 이력서 내용
        
    Returns:
        (has_experience_years, has_junior_keyword, has_senior_keyword, extracted_years)
    """
    has_experience_years = False
    has_junior_keyword = False
    has_senior_keyword = False
    extracted_years: Optional[float] = None
    
    if not content:
        return has_experience_years, has_junior_keyword, has_senior_keyword, extracted_years
    
    # 경력년수 패턴 확인 및 숫자 추출
    for pattern in EXPERIENCE_YEARS_PATTERNS:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            has_experience_years = True
            # 숫자 부분 추출
            number_match = re.search(r'(\d+\.?\d*)', match.group(0))
            if number_match:
                try:
                    extracted_years = float(number_match.group(1))
                    break
                except ValueError:
                    continue
    
    # 신입/주니어 키워드 확인
    for pattern in JUNIOR_KEYWORD_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            has_junior_keyword = True
            break
    
    # 시니어 키워드 확인
    for pattern in SENIOR_KEYWORD_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            has_senior_keyword = True
            break
    
    return has_experience_years, has_junior_keyword, has_senior_keyword, extracted_years


def _check_position_match(
    position: str,
    content: str,
    required_positions: List[str]
) -> bool:
    """
    이력서의 포지션이 요구 포지션과 일치하는지 확인합니다.
    
    Args:
        position: 이력서 포지션
        content: 이력서 내용 (사용 안 함)
        required_positions: 요구 포지션 키워드 리스트
        
    Returns:
        일치하면 True
    """
    if not required_positions:
        return True  # 필터 조건 없으면 통과
    
    position_lower = position.lower()
    
    for req_position in required_positions:
        # 포지션 키워드 매칭 (position 필드에서만 정확히 확인)
        position_keywords = POSITION_KEYWORDS.get(req_position, [])
        if any(keyword in position_lower for keyword in position_keywords):
            return True
    
    return False


def _check_skills_match(
    skills: List[str],
    content: str,
    required_skills: List[str]
) -> bool:
    """
    이력서의 기술 스택이 요구 기술과 일치하는지 확인합니다.
    
    Args:
        skills: 이력서 기술 스택 리스트
        content: 이력서 내용
        required_skills: 요구 기술 키워드 리스트
        
    Returns:
        일치하면 True
    """
    if not required_skills:
        return True  # 필터 조건 없으면 통과
    
    skills_lower = [s.lower() for s in skills if s]
    content_lower = content.lower() if content else ""
    
    for req_skill in required_skills:
        req_skill_lower = req_skill.lower()
        
        # skills 필드에서 확인
        if any(req_skill_lower in skill_lower for skill_lower in skills_lower):
            return True
        
        # content에서 확인 (대소문자 구분 없이)
        if req_skill_lower in content_lower:
            return True
    
    return False


def _should_filter_resume(
    resume_id: str,
    experience: str,
    position: str,
    skills: List[str],
    content: str,
    excluded_ids: set,
    is_different_request: bool,
    is_senior_only: bool,
    is_junior_only: bool,
    min_years: Optional[float] = None,
    max_years: Optional[float] = None,
    required_positions: Optional[List[str]] = None,
    required_skills: Optional[List[str]] = None
) -> bool:
    """
    이력서를 필터링해야 하는지 확인합니다.
    
    Args:
        resume_id: 이력서 ID
        experience: 경력 정보
        position: 포지션
        skills: 기술 스택 리스트
        content: 이력서 내용
        excluded_ids: 제외할 ID 집합
        is_different_request: 다른 인재 요청 여부
        is_senior_only: 경력직만 요청 여부
        is_junior_only: 신입만 요청 여부
        min_years: 최소 경력년수
        max_years: 최대 경력년수
        required_positions: 요구 포지션 키워드 리스트
        required_skills: 요구 기술 키워드 리스트
        
    Returns:
        필터링해야 하면 True
    """
    # 다른 인재 요청 시 이전 결과 제외
    if is_different_request and str(resume_id) in excluded_ids:
        return True
    
    # 포지션 필터링
    if required_positions:
        if not _check_position_match(position, content, required_positions):
            return True
    
    # 기술 스택 필터링
    if required_skills:
        if not _check_skills_match(skills, content, required_skills):
            return True
    
    # content에서 경력 정보 확인
    has_exp_years, has_junior, has_senior, extracted_years = _check_experience_in_content(content)
    experience_lower = experience.lower() if experience else ""
    
    # 경력년수 추출
    resume_years = extracted_years
    if resume_years is None:
        resume_years = _extract_years_from_experience(experience, content)
    
    # 경력년수 범위 필터링
    if min_years is not None or max_years is not None:
        if resume_years is None:
            # 경력년수를 추출할 수 없으면 제외
            return True
        
        if min_years is not None and resume_years < min_years:
            return True
        
        if max_years is not None and resume_years > max_years:
            return True
    
    # 경력직만 요청
    if is_senior_only:
        # 신입 키워드 확인
        if any(kw in experience_lower for kw in ["신입", "junior", "entry"]):
            if not re.search(r'\d+\s*년', experience) and not re.search(r'\d+\s*year', experience_lower):
                return True
        
        # content에서 신입/주니어 키워드 발견 시 제외
        if has_junior and not has_exp_years:
            return True
        
        # 경력년수가 없는 경우 제외 (단, 시니어 키워드가 있으면 포함)
        if not has_exp_years and not has_senior:
            if not re.search(r'\d+\s*년', experience) and not re.search(r'\d+\s*year', experience_lower):
                return True
    
    # 신입만 요청
    if is_junior_only:
        has_experience_years = (
            re.search(r'\d+\s*년', experience) or
            re.search(r'\d+\s*year', experience_lower) or
            has_exp_years
        )
        
        # 경력년수가 있으면 제외
        if has_experience_years:
            return True
        
        # 경력직/시니어 키워드 확인
        if any(kw in experience_lower for kw in ["경력직", "시니어", "senior", "experienced"]):
            return True
        
        # content에서 시니어 키워드 발견 시 제외
        if has_senior:
            return True
        
        # 주니어는 경력직으로 간주 (경력년수가 있으면)
        if any(kw in experience_lower for kw in ["주니어", "junior"]) and has_experience_years:
            return True
    
    return False


# ==================== 임베딩 캐싱 함수 ====================

def _get_cached_embedding(query: str) -> Optional[List[float]]:
    """
    캐시된 임베딩을 가져옵니다.
    
    Args:
        query: 검색 쿼리
        
    Returns:
        캐시된 임베딩 벡터 (없으면 None)
    """
    if query in _embedding_cache:
        # LRU: 사용된 항목을 맨 뒤로 이동
        _embedding_cache.move_to_end(query)
        logger.debug(f"임베딩 캐시 히트: '{query}'")
        return _embedding_cache[query]
    return None


def _cache_embedding(query: str, embedding: List[float]) -> None:
    """
    임베딩을 캐시에 저장합니다.
    
    Args:
        query: 검색 쿼리
        embedding: 임베딩 벡터
    """
    global _embedding_cache
    
    # 캐시 크기 제한 (LRU)
    if len(_embedding_cache) >= EMBEDDING_CACHE_SIZE:
        # 가장 오래된 항목 제거
        _embedding_cache.popitem(last=False)
    
    _embedding_cache[query] = embedding
    _embedding_cache.move_to_end(query)
    logger.debug(f"임베딩 캐시 저장: '{query}' (캐시 크기: {len(_embedding_cache)})")


# ==================== 메인 검색 함수 ====================

async def search_resumes(
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    session_id: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    벡터 DB에서 이력서를 검색하여 인재를 추천합니다.
    
    Args:
        query: 검색 쿼리 (예: "Python 개발자 경력 5년 이상")
        limit: 반환할 최대 결과 수
        session_id: 세션 ID
        state: OrchestratorState
        
    Returns:
        {
            "message": str,  # 사용자에게 보여줄 메시지
            "resumes": List[Dict],  # 검색된 이력서 목록
            "total_count": int
        }
    """
    try:
        # Milvus 및 임베딩 모델 초기화
        _initialize_milvus()
        _initialize_embedding_model()
        
        if _collection is None:
            return {
                "message": "이력서 데이터베이스에 연결할 수 없습니다. 관리자에게 문의하세요.",
                "resumes": [],
                "total_count": 0,
                "ok": False,
            }
        
        if _embedding_model is None:
            return {
                "message": "임베딩 모델을 초기화할 수 없습니다. 관리자에게 문의하세요.",
                "resumes": [],
                "total_count": 0,
                "ok": False,
            }
        
        # 쿼리 파싱
        (
            is_different_request, is_senior_only, is_junior_only, 
            min_years, max_years, position_keywords, skill_keywords
        ) = _parse_query_filters(query)
        
        # 디버그: 필터 조건 추출 로그
        logger.info(
            f"🔍 [필터 추출] query='{query}' | "
            f"position={position_keywords} | min_years={min_years} | max_years={max_years} | "
            f"skills={skill_keywords} | senior={is_senior_only} | junior={is_junior_only}"
        )
        
        # 이전 쿼리 및 필터 복원
        (
            last_query, last_senior_only, last_junior_only, 
            last_min_years, last_max_years, last_position_keywords, last_skill_keywords
        ) = _restore_previous_query_filters(state, is_different_request)
        
        # 필터 조건 적용 (이전 필터 복원)
        if is_different_request and last_query:
            if not is_senior_only and not is_junior_only:
                is_senior_only = last_senior_only
                is_junior_only = last_junior_only
            
            # 경력년수 범위도 복원 (현재 쿼리에 범위가 없으면)
            if min_years is None and max_years is None:
                min_years = last_min_years
                max_years = last_max_years
            
            # 포지션 및 기술 스택 키워드도 복원
            if not position_keywords:
                position_keywords = last_position_keywords
            if not skill_keywords:
                skill_keywords = last_skill_keywords
            
            logger.debug(
                f"이전 필터 조건 복원: 경력직={is_senior_only}, 신입={is_junior_only}, "
                f"경력년수={min_years}~{max_years}, 포지션={position_keywords}, 기술={skill_keywords}"
            )
        
        # position_keywords와 skill_keywords가 None인 경우 빈 리스트로 초기화
        if position_keywords is None:
            position_keywords = []
        if skill_keywords is None:
            skill_keywords = []
        
        # 검색 쿼리 생성
        search_query = _build_search_query(query, is_different_request, last_query)
        
        # 제외할 이력서 ID 가져오기
        excluded_ids = _get_excluded_resume_ids(state, is_different_request)
        
        # 쿼리 임베딩 생성 (캐시 확인)
        cached_embedding = _get_cached_embedding(search_query)
        if cached_embedding:
            query_embedding = cached_embedding
            logger.debug(f"임베딩 캐시에서 사용: '{search_query}'")
        else:
            query_embedding = await asyncio.to_thread(_embedding_model.embed, search_query)
            _cache_embedding(search_query, query_embedding)
        
        # 검색 파라미터
        search_limit_multiplier = (
            SEARCH_LIMIT_MULTIPLIER_DIFFERENT_REQUEST 
            if is_different_request 
            else SEARCH_LIMIT_MULTIPLIER_NORMAL
        )
        search_limit = limit * search_limit_multiplier
        
        milvus_metric_type = _get_env("MILVUS_METRIC_TYPE", DEFAULT_MILVUS_METRIC_TYPE)
        milvus_nprobe = int(_get_env("MILVUS_NPROBE", str(DEFAULT_MILVUS_NPROBE)))
        
        # 컬렉션 스키마 확인
        schema = _collection.schema
        field_names = [field.name for field in schema.fields]
        logger.debug(f"컬렉션 스키마 필드: {field_names}")
        
        # 출력 필드 결정 (embedding 제외)
        output_fields = [f for f in field_names if f != "embedding"]
        if not output_fields:
            output_fields = field_names
        
        logger.debug(f"사용할 출력 필드: {output_fields}")
        
        # 벡터 검색 실행
        search_params = {
            "metric_type": milvus_metric_type,
            "params": {"nprobe": milvus_nprobe},
        }
        
        search_results = await asyncio.to_thread(
            _collection.search,
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=search_limit,
            output_fields=output_fields if output_fields else None,
        )
        
        # 검색 결과 처리
        resumes = []
        filtered_count = 0
        
        for idx, hit in enumerate(search_results[0], 1):
            resume_data = hit.entity
            
            # Entity를 딕셔너리로 변환
            entity_dict = {}
            try:
                if hasattr(resume_data, 'get'):
                    for field in output_fields:
                        try:
                            val = resume_data.get(field)
                            if val is not None:
                                entity_dict[field] = val
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Entity 딕셔너리 변환 실패: {e}")
            
            # 필드 추출
            resume_id = (
                _extract_field_from_entity(entity_dict, POSSIBLE_RESUME_ID_FIELDS, "") or
                str(hit.id) if hasattr(hit, 'id') else str(idx)
            )
            
            # content와 chunk_text 가져오기 (먼저 확인)
            content = _extract_field_from_entity(
                entity_dict, POSSIBLE_CONTENT_FIELDS, ""
            )
            chunk_text = entity_dict.get("chunk_text") or ""
            if not content and chunk_text:
                content = chunk_text
            
            # document_title에서 먼저 이름과 직무 추출 시도 (새로운 데이터 구조)
            document_title = entity_dict.get("document_title") or entity_dict.get("document_name") or ""
            parsed_name, parsed_position = _parse_name_position_from_title(document_title)
            
            # document_title에서 파싱 실패 시 content의 첫 줄에서도 확인
            if not parsed_name and content:
                first_line = content.split('\n')[0].strip() if content else ""
                parsed_name, parsed_position = _parse_name_position_from_title(first_line)
            
            # 이름 추출 (document_title/content 첫 줄 우선, 없으면 기존 로직)
            if parsed_name:
                name = parsed_name
            else:
                name = _extract_field_from_entity(entity_dict, POSSIBLE_NAME_FIELDS, DEFAULT_NAME)
            
            # 직무 추출 (document_title/content 첫 줄 우선, 없으면 기존 로직)
            if parsed_position:
                position = parsed_position
            else:
                position = _extract_field_from_entity(
                    entity_dict, POSSIBLE_POSITION_FIELDS, DEFAULT_POSITION
                )
            
            experience = _extract_field_from_entity(
                entity_dict, POSSIBLE_EXPERIENCE_FIELDS, DEFAULT_EXPERIENCE
            )
            
            skills = entity_dict.get("skills") or []
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.split(",") if s.strip()]
            
            # content에서 정보 추출 (이름과 직무가 아직 기본값인 경우에만)
            if content:
                content_lines = content.split('\n')
                
                if name == DEFAULT_NAME:
                    name = _extract_name_from_content(content_lines)
                
                if position == DEFAULT_POSITION:
                    position = _extract_position_from_content(content_lines)
                
                if experience == DEFAULT_EXPERIENCE:
                    experience = _extract_experience_from_content(content_lines)
                
                if not skills:
                    skills = _extract_skills_from_content(content_lines)
            
            # 필터링
            if _should_filter_resume(
                resume_id, experience, position, skills, content, excluded_ids,
                is_different_request, is_senior_only, is_junior_only,
                min_years, max_years, position_keywords, skill_keywords
            ):
                filtered_count += 1
                continue
            
            resumes.append({
                "index": str(len(resumes) + 1),
                "resume_id": resume_id,
                "name": name,
                "position": position,
                "experience": experience,
                "skills": skills if isinstance(skills, list) else [],
                "content": content,
            })
            
            # limit 개수만큼 수집하면 중단
            if len(resumes) >= limit:
                break
        
        # 사용자 메시지 생성
        filter_info = []
        if is_different_request:
            filter_info.append("다른 인재")
        if is_senior_only:
            filter_info.append("경력직")
        elif is_junior_only:
            filter_info.append("신입")
        
        # 경력년수 범위 정보 추가
        if min_years is not None or max_years is not None:
            if min_years is not None and max_years is not None:
                filter_info.append(f"{min_years:.1f}~{max_years:.1f}년")
            elif min_years is not None:
                filter_info.append(f"{min_years:.1f}년 이상")
            elif max_years is not None:
                filter_info.append(f"{max_years:.1f}년 이하")
        
        # 포지션 정보 추가
        if position_keywords:
            filter_info.append(f"포지션: {', '.join(position_keywords)}")
        
        # 기술 스택 정보 추가 (최대 3개만 표시)
        if skill_keywords:
            skills_display = ', '.join(skill_keywords[:3])
            if len(skill_keywords) > 3:
                skills_display += f" 외 {len(skill_keywords) - 3}개"
            filter_info.append(f"기술: {skills_display}")
        
        filter_msg = f" ({', '.join(filter_info)} 필터 적용)" if filter_info else ""
        
        if resumes:
            if filtered_count > 0:
                message = (
                    f"검색 결과 {len(resumes)}명의 인재를 찾았습니다{filter_msg}. "
                    f"(필터링: {filtered_count}개 제외)"
                )
            else:
                message = (
                    f"검색 결과 {len(resumes)}명의 인재를 찾았습니다{filter_msg}. "
                    f"카드를 클릭하면 상세 정보를 확인할 수 있습니다."
                )
        else:
            filter_msg = f" ({', '.join(filter_info)} 필터)" if filter_info else ""
            message = f"'{query}' 조건{filter_msg}에 맞는 인재를 찾을 수 없습니다."
        
        logger.debug(
            f"검색 완료: {len(resumes)}개 결과, "
            f"{filtered_count}개 필터링됨"
        )
        
        return {
            "message": message,
            "resumes": resumes,
            "total_count": len(resumes),
            "ok": True,
            "display_type": "cards",
        }
        
    except Exception as e:
        logger.error(f"이력서 검색 실패: {e}", exc_info=True)
        return {
            "message": f"이력서 검색 중 오류가 발생했습니다: {str(e)}",
            "resumes": [],
            "total_count": 0,
            "ok": False,
        }
