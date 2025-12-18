"""특허 검색용 키워드 생성 모듈 (특허 Agent 전용).

논문 Agent와 분리하여 충돌을 방지하기 위해 별도 파일로 관리합니다.
"""

import logging
import os
import re
from typing import Set, Optional, Dict, Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

if not OPENAI_API_KEY:
    logging.warning("OPENAI_API_KEY is not set.")

# 기본값 (YAML 로드 실패 시 fallback)
_DEFAULT_STOP_WORDS: Set[str] = {
    "특허", "Patent", "patent", "검색", "찾아", "알려", "줘", "주세요", 
    "관련", "정보", "자료", "요청", "요청한", "검색한", "이전에", "전에"
}
_DEFAULT_KEYWORD_LIMIT: int = 3


def _load_patent_keyword_prompts() -> Dict[str, Any]:
    """YAML에서 키워드 생성 관련 프롬프트 로드."""
    try:
        from agents.llm_router import load_agent_prompts
        prompts_data = load_agent_prompts()
        if not prompts_data or "tech" not in prompts_data:
            return {}
        
        tech_config = prompts_data.get("tech", {})
        patent_config = tech_config.get("sub_agents", {}).get("patent", {})
        return patent_config
    except Exception:
        return {}


def _build_client() -> OpenAI | None:
    """OpenAI 클라이언트를 생성한다.

    Returns:
        Optional[OpenAI]: API 키가 존재하면 OpenAI 클라이언트, 없으면 None.
    """
    if not OPENAI_API_KEY:
        return None

    return OpenAI(api_key=OPENAI_API_KEY)


CLIENT: OpenAI | None = _build_client()


def _clean_keyword_output(raw_output: str, fallback: str, limit: int = _DEFAULT_KEYWORD_LIMIT, stop_words: Optional[Set[str]] = None) -> str:
    """LLM이 생성한 키워드에서 불필요한 토큰을 제거한다.

    Args:
        raw_output (str): LLM이 반환한 원본 문자열.
        fallback (str): 정제 결과가 없을 때 사용할 기본 문자열.
        limit (int): 반환할 최대 키워드 개수.
        stop_words (Optional[Set[str]]): 불용어 집합. None이면 기본값 사용.

    Returns:
        str: 쉼표로 구분된 정제된 키워드 문자열.
    """
    if stop_words is None:
        stop_words = _DEFAULT_STOP_WORDS
    
    candidates = re.split(r"[,/\n]+", raw_output)
    cleaned: list[str] = []
    for candidate in candidates:
        tokens = [
            re.sub(r"[^\w가-힣0-9-]", "", word)
            for word in candidate.strip().split()
            if word.strip()
        ]
        # 숫자만 있는 토큰 필터링 추가
        tokens = [token for token in tokens if token and token not in stop_words and not token.isdigit()]
        if not tokens:
            continue
        keyword = " ".join(tokens)
        if keyword and keyword not in cleaned:
            cleaned.append(keyword)
    if cleaned:
        return ", ".join(cleaned[:limit])

    fallback_tokens = [
        re.sub(r"[^\w가-힣0-9-]", "", word)
        for word in re.split(r"[,\s/]+", fallback)
        if word.strip()
    ]
    filtered_fallback: list[str] = []
    for token in fallback_tokens:
        # 🔧 숫자만 있는 토큰 필터링 추가
        if token and token not in stop_words and not token.isdigit() and token not in filtered_fallback:
            filtered_fallback.append(token)

    if filtered_fallback:
        return ", ".join(filtered_fallback[:limit])

    return fallback


def _normalize_keyword_for_collection(keyword: str) -> str:
    """키워드를 정규화하여 일관된 컬렉션 이름 생성을 보장.
    
    이 함수는 키워드 추출 후 호출되어, 동일한 키워드가 항상 동일한 컬렉션 이름으로 변환되도록 보장합니다.
    
    Args:
        keyword: 정규화할 키워드 (한글 가능, 쉼표로 구분된 여러 키워드 가능)
        
    Returns:
        정규화된 키워드 (첫 번째 키워드만 사용하여 정규화)
    """
    if not keyword or not keyword.strip():
        return "other"
    
    # 순환 참조 방지를 위해 lazy import
    try:
        # patent_milvus_db 모듈에서 _sanitize_collection_name 함수 import
        from . import patent_milvus_db
        
        # 쉼표로 구분된 키워드가 있으면 첫 번째 키워드만 사용
        # 예: "배터리, 충전" -> "배터리" -> "battery"
        first_keyword = keyword.split(",")[0].strip() if keyword else ""
        if not first_keyword:
            return "other"
        
        # 정규화된 키워드 반환 (영문으로 변환)
        normalized = patent_milvus_db._sanitize_collection_name(first_keyword)
        return normalized if normalized else "other"
    except (ImportError, AttributeError) as e:
        logging.warning(f"키워드 정규화 실패 (patent_milvus_db 모듈 로드 실패), 원본 사용: {e}")
        # 정규화 실패 시 원본 키워드의 첫 번째 부분 반환
        first_keyword = keyword.split(",")[0].strip() if keyword else "other"
        return first_keyword
    except Exception as e:
        logging.warning(f"키워드 정규화 실패, 원본 사용: {e}")
        # 정규화 실패 시 원본 키워드의 첫 번째 부분 반환
        first_keyword = keyword.split(",")[0].strip() if keyword else "other"
        return first_keyword


def generate_patent_keyword(user_input: str, max_tokens: int = 32) -> str:
    """사용자 질의를 특허 검색 키워드로 변환한다.
    
    키워드 추출 후 자동으로 정규화하여 일관된 컬렉션 이름 생성을 보장합니다.

    Args:
        user_input (str): 사용자의 자연어 질의.
        max_tokens (int): 생성할 키워드의 최대 토큰 수.

    Returns:
        str: 검색에 활용할 간결한 키워드 (정규화됨). OpenAI API를 사용할 수 없으면 원본 질의를 반환.
    """
    # YAML에서 프롬프트 및 설정 로드
    patent_config = _load_patent_keyword_prompts()
    keyword_config = patent_config.get("keyword_generation", {})
    
    # 프롬프트 템플릿 로드 (fallback 포함)
    prompt_template = keyword_config.get("prompt", "")
    if not prompt_template:
        # Fallback: 하드코딩된 기본 프롬프트
        prompt_template = (
            "다음 사용자 질의를 특허 검색용 핵심 키워드로 요약해줘.\n"
            "- 출력 형식: 쉼표로 구분된 최대 3개의 키워드만 반환해.\n"
            "- ⚠️ 중요: 붙어있는 키워드는 절대 분리하지 마세요. 공백으로 구분된 키워드만 쉼표로 구분하세요.\n"
            "- 모든 키워드는 무조건 사용자 입력에 존재하는 단어로만 선택해.\n"
            "- 각 키워드는 1~2개의 핵심 단어로 구성하고, 질문에 등장한 핵심 개념에서만 선택해.\n"
            "- '특허', 'Patent', '검색', '찾아', '알려', '줘', '주세요', '관련', '정보' 같은 일반 단어와 조사는 모두 제거해.\n"
            "- 번호, 설명, 불릿, 접두어 같은 추가 문장은 절대 포함하지 마.\n"
            "- 예시 입력: '배터리 충전 관련 특허 찾아줘' -> 예시 출력: 배터리, 충전 (공백으로 구분되어 있음)\n"
            "- 예시 입력: '전기차베터리 특허 좀 찾아줘' -> 예시 출력: 전기차베터리 (붙어있으므로 분리하지 않음)\n"
            "- 예시 입력: 'AI 딥러닝 특허' -> 예시 출력: 인공지능, 딥러닝\n"
            "입력: {user_input}\n출력:"
        )
    
    # 불용어 및 제한 로드
    stop_words_list = keyword_config.get("stop_words", list(_DEFAULT_STOP_WORDS))
    stop_words = set(stop_words_list) if stop_words_list else _DEFAULT_STOP_WORDS
    keyword_limit = keyword_config.get("keyword_limit", _DEFAULT_KEYWORD_LIMIT)
    
    # 프롬프트에 user_input 치환
    prompt = prompt_template.replace("{user_input}", user_input)
    
    if CLIENT is None:
        raw_keyword = _clean_keyword_output(user_input, user_input, limit=keyword_limit, stop_words=stop_words)
    else:
        try:
            response = CLIENT.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0,
            )
            raw_output = response.choices[0].message.content.strip()
            raw_keyword = _clean_keyword_output(raw_output, user_input, limit=keyword_limit, stop_words=stop_words)
        except Exception:
            logging.exception("OpenAI patent keyword generation failed.")
            raw_keyword = _clean_keyword_output(user_input, user_input, limit=keyword_limit, stop_words=stop_words)
    
    # 🔧 키워드 정규화: 일관된 컬렉션 이름 생성을 위해 정규화 수행
    normalized_keyword = _normalize_keyword_for_collection(raw_keyword)
    
    return normalized_keyword


def generate_patent_keyword_from_text(text: str, max_tokens: int = 32) -> str:
    """특허 제목/초록 텍스트에서 주요 키워드를 추출한다.
    
    키워드 추출 후 자동으로 정규화하여 일관된 컬렉션 이름 생성을 보장합니다.
    
    Args:
        text (str): 특허 제목 또는 초록 텍스트.
        max_tokens (int): 생성할 키워드의 최대 토큰 수.
        
    Returns:
        str: 추출된 핵심 키워드 (정규화됨).
    """
    # YAML에서 프롬프트 및 설정 로드
    patent_config = _load_patent_keyword_prompts()
    extraction_config = patent_config.get("keyword_extraction", {})
    
    # 프롬프트 템플릿 로드 (fallback 포함)
    prompt_template = extraction_config.get("prompt", "")
    if not prompt_template:
        # Fallback: 하드코딩된 기본 프롬프트
        prompt_template = (
            "다음 특허 제목/초록 텍스트에서 핵심 기술 키워드를 추출해줘.\n"
            "- 출력 형식: 쉼표로 구분된 최대 3개의 키워드만 반환해.\n"
            "- 핵심 기술 용어만 선택하고, 일반적인 단어는 제거해.\n"
            "- 예시 입력: '배터리 충전 장치 및 방법' -> 예시 출력: 배터리, 충전\n"
            "입력: {text}\n출력:"
        )
    
    # 불용어 및 제한 로드
    stop_words_list = extraction_config.get("stop_words", list(_DEFAULT_STOP_WORDS))
    stop_words = set(stop_words_list) if stop_words_list else _DEFAULT_STOP_WORDS
    keyword_limit = extraction_config.get("keyword_limit", _DEFAULT_KEYWORD_LIMIT)
    
    # 프롬프트에 text 치환
    prompt = prompt_template.replace("{text}", text)
    
    if CLIENT is None:
        # OpenAI API를 사용할 수 없으면 텍스트에서 주요 단어만 추출
        words = re.findall(r'\b\w+\b', text)
        # 한글과 영문 단어만 선택, stop words 제거
        keywords = [w for w in words if len(w) > 1 and w not in stop_words]
        raw_keyword = ", ".join(keywords[:keyword_limit]) if keywords else "기타"
    else:
        try:
            response = CLIENT.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0,
            )
            raw_output = response.choices[0].message.content.strip()
            raw_keyword = _clean_keyword_output(raw_output, text, limit=keyword_limit, stop_words=stop_words)
        except Exception:
            logging.exception("OpenAI patent keyword extraction from text failed.")
            words = re.findall(r'\b\w+\b', text)
            keywords = [w for w in words if len(w) > 1 and w not in stop_words]
            raw_keyword = ", ".join(keywords[:keyword_limit]) if keywords else "기타"
    
    # 🔧 키워드 정규화: 일관된 컬렉션 이름 생성을 위해 정규화 수행
    normalized_keyword = _normalize_keyword_for_collection(raw_keyword)
    
    return normalized_keyword


def generate_patent_search_phrase(user_input: str, max_tokens: int = 50) -> str:
    """사용자 질의를 특허 검색용 핵심 구문으로 변환.
    
    키워드를 분리하지 않고, 하나의 의미 있는 구문으로 추출합니다.
    
    예시:
    - 입력: "전기차 베터리 관련해서 특허좀 찾아줘"
    - 출력: "전기차 배터리 기술"
    
    Args:
        user_input: 사용자의 자연어 질의
        max_tokens: 최대 토큰 수
    
    Returns:
        검색에 활용할 핵심 구문
    """
    # YAML에서 프롬프트 및 설정 로드
    patent_config = _load_patent_keyword_prompts()
    phrase_config = patent_config.get("search_phrase_generation", {})
    
    # 프롬프트 템플릿 로드 (YAML에서 로드 또는 기본값)
    prompt_template = phrase_config.get("prompt", "")
    if not prompt_template:
        # Fallback: 하드코딩된 기본 프롬프트
        prompt_template = """다음 사용자 질의를 특허 검색용 핵심 검색 구문 하나로 변환해줘.
- 키워드를 분리하지 말고, 하나의 의미 있는 기술 개념 구문으로 생성해줘.
- 불필요한 단어("특허", "찾아줘", "검색" 등)는 제거하고 핵심 기술 용어만 남겨줘.
- 출력은 최대 10단어 이내의 자연스러운 구문이어야 해.

예시:
- 입력: "전기차 베터리 관련해서 특허좀 찾아줘" → 출력: "전기차 배터리 기술"
- 입력: "AI 딥러닝 특허 검색" → 출력: "인공지능 딥러닝"
- 입력: "초전도체 기술 현황" → 출력: "초전도체 기술"

입력: {user_input}
출력:"""
    
    prompt = prompt_template.replace("{user_input}", user_input)
    
    if CLIENT is None:
        # Fallback: 기본 파싱 (불필요한 단어 제거)
        cleaned = re.sub(r'\s*(특허|patent|검색|찾아|찾아줘|검색해줘|관련해서|관련|정보|자료|요청|알려|줘|주세요)\s*', ' ', user_input, flags=re.IGNORECASE).strip()
        return cleaned if cleaned else user_input
    
    try:
        response = CLIENT.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0,
        )
        output = response.choices[0].message.content.strip()
        # 불필요한 설명 제거 (구문만 추출)
        # "출력:", "답변:" 같은 접두어 제거
        output = re.sub(r'^(출력|답변|결과)[:\s]*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'[^\w가-힣\s-]', '', output).strip()
        return output if output else user_input
    except Exception:
        logging.exception("OpenAI search phrase generation failed.")
        # Fallback: 기본 파싱
        cleaned = re.sub(r'\s*(특허|patent|검색|찾아|찾아줘|검색해줘|관련해서|관련|정보|자료|요청|알려|줘|주세요)\s*', ' ', user_input, flags=re.IGNORECASE).strip()
        return cleaned if cleaned else user_input
