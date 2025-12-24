"""키워드 생성 및 요약 생성 모듈."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL")

if not OPENAI_API_KEY:
    logging.warning("OPENAI_API_KEY is not set.")

_STOP_WORDS: Set[str] = {
    "논문", "연구", "관련", "자료", "정보", "소개", "분석",
    "웹", "인터넷", "온라인", "web", "internet",
    "사내", "내부", "서버", "internal", "server"
}
KEYWORD_LIMIT: int = 2


def _build_client() -> Optional[AsyncOpenAI]:
    """OpenAI 클라이언트를 생성한다.

    Returns:
        Optional[AsyncOpenAI]: API 키가 존재하면 AsyncOpenAI 클라이언트, 없으면 None.
    """
    if not OPENAI_API_KEY:
        return None

    return AsyncOpenAI(api_key=OPENAI_API_KEY)


CLIENT: Optional[AsyncOpenAI] = _build_client()


def _clean_keyword_output(raw_output: str, fallback: str, limit: int = KEYWORD_LIMIT) -> str:
    """LLM이 생성한 키워드에서 불필요한 토큰을 제거한다.

    Args:
        raw_output (str): LLM이 반환한 원본 문자열.
        fallback (str): 정제 결과가 없을 때 사용할 기본 문자열.
        limit (int): 반환할 최대 키워드 개수.

    Returns:
        str: 쉼표로 구분된 정제된 키워드 문자열.
    """
    empty_indicators = ["빈 문자열", "빈문자열", "없음", "none", "n/a", "없습니다", "없어요", "없다", 
                        "(빈 문자열)", "(빈문자열)", "()", "빈", "empty", 
                        "None", "NONE", "null", "NULL", "Null"]
    raw_output_stripped = raw_output.strip()
    raw_output_lower = raw_output_stripped.lower()
    raw_output_no_brackets = re.sub(r'[()\[\]]', '', raw_output_stripped).strip()
    
    if (not raw_output_stripped or 
        raw_output_lower in [ind.lower() for ind in empty_indicators] or 
        raw_output_no_brackets.lower() in [ind.lower() for ind in empty_indicators]):
        return ""
    
    candidates = re.split(r"[,/\n]+", raw_output)
    cleaned: List[str] = []
    for candidate in candidates:
        tokens = [
            re.sub(r"[^\w가-힣0-9-]", "", word)
            for word in candidate.strip().split()
            if word.strip()
        ]
        tokens = [token for token in tokens if token and token not in _STOP_WORDS]
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
    filtered_fallback: List[str] = []
    for token in fallback_tokens:
        if token and token not in _STOP_WORDS and token not in filtered_fallback:
            filtered_fallback.append(token)

    if filtered_fallback:
        return ", ".join(filtered_fallback[:limit])

    return fallback


async def generate_search_keyword(user_input: str, max_tokens: int = 32) -> Optional[str]:
    """사용자 질의를 학술 검색 키워드로 변환한다.

    Args:
        user_input (str): 사용자의 자연어 질의.
        max_tokens (int): 생성할 키워드의 최대 토큰 수.

    Returns:
        str: 검색에 활용할 간결한 키워드. 실제 검색어가 없으면 빈 문자열 반환.
    """
    # 검색 소스 키워드 제거 (전처리)
    search_source_keywords = ["웹", "인터넷", "온라인", "web", "internet", 
                              "사내", "내부", "서버", "internal", "server"]
    cleaned_input = user_input
    for kw in search_source_keywords:
        # 한글 조사와 공백을 고려한 패턴
        # "웹에서", "웹으로", "웹 ", " 웹", "웹" 등 모든 경우 처리
        patterns = [
            rf'\s*{re.escape(kw)}\s*(에서|으로|로|에|의|을|를|은|는|이|가|도|와|과|하고|랑)\s*',  # "웹에서" -> 제거
            rf'(에서|으로|로|에|의|을|를|은|는|이|가|도|와|과|하고|랑)\s*{re.escape(kw)}\s*',  # "에서웹" -> 제거
            rf'^\s*{re.escape(kw)}\s+',  # 시작 부분
            rf'\s+{re.escape(kw)}\s+',  # 중간 부분
            rf'\s+{re.escape(kw)}\s*$',  # 끝 부분
            rf'^\s*{re.escape(kw)}\s*$',  # 전체가 키워드인 경우
        ]
        for pattern in patterns:
            cleaned_input = re.sub(pattern, ' ', cleaned_input, flags=re.IGNORECASE)
    
    # 도메인 키워드 제거 (논문, paper, 학술 등)
    domain_keywords = ["논문", "paper", "학술", "ScienceON", "검색", "찾아", "알려", "줘", "주세요", "해줘", "해주세요", "찾아줄래", "찾아줄래?"]
    for dk in domain_keywords:
        pattern = rf'\b{re.escape(dk)}\b'
        cleaned_input = re.sub(pattern, '', cleaned_input, flags=re.IGNORECASE)
    
    cleaned_input = " ".join(cleaned_input.split())  # 연속된 공백 제거
    
    # 도메인 키워드 제거 후 실제 검색어가 없으면 빈 문자열 반환
    if not cleaned_input or len(cleaned_input.strip()) == 0:
        return None
    
    if CLIENT is None:
        return _clean_keyword_output(cleaned_input, cleaned_input, limit=KEYWORD_LIMIT)

    prompt = (
        "다음 사용자 질의를 학술 논문 검색용 핵심 키워드로 요약해줘.\n"
        "- 입력에 오타나 맞춤법 오류가 있으면 자동으로 올바른 단어로 수정한 후 키워드를 추출해줘.\n"
        "- 예: '베터리' -> '배터리', '인공지능' -> '인공지능' (이미 올바름)\n"
        "- 출력 형식: 사용자가 명시한 각 핵심 키워드를 쉼표로 구분해서 반환해.\n"
        "  * 사용자가 조사(예시: '랑', '와', '과', '하고', ',')로 여러 키워드를 명시했으면 → 각각을 쉼표로 구분해서 반환\n"
        "    예: '배터리랑 디스플레이' -> '배터리, 디스플레이'\n"
        "    예: '배터리, 디스플레이' -> '배터리, 디스플레이'\n"
        "  * 사용자가 단순히 띄어쓰기만 있는 경우(예: '전기차 배터리') → 하나의 키워드로 합쳐서 반환\n"
        "    예: '전기차 배터리' -> '전기차배터리'\n"
        "- 오타가 수정된 키워드를 반환해도 됩니다 (사용자 입력에 정확히 존재하지 않아도 수정된 올바른 단어 사용).\n"
        "- 각 키워드는 1~2개의 핵심 단어로 구성하고, 질문에 등장한 핵심 개념에서만 선택해.\n"
        "- '논문', 'paper', '학술', 'ScienceON', '연구', '관련', '자료', '웹', '사내', '서버', '검색', '찾아', '알려', '줘', '주세요' 같은 일반 단어와 조사는 모두 제거해.\n"
        "- ⚠️⚠️⚠️ 매우 중요: 실제 검색어가 없으면(예: '논문 찾아줘', '논문 좀 찾아줄래', '논문 검색해줘' 같이 도메인 키워드만 있는 경우) 반드시 아무것도 출력하지 말고 완전히 빈 문자열을 반환하세요.\n"
        "- ⚠️⚠️⚠️ 절대 'None', 'none', 'Null', 'null', '없음', '빈 문자열', '(빈 문자열)', '()' 같은 텍스트를 출력하지 마세요. 아무것도 출력하지 않는 것이 정답입니다.\n"
        "- 번호, 설명, 불릿, 접두어 같은 추가 문장은 절대 포함하지 마.\n"
        "- 예시 입력: '배터리 관련 논문 필요해' -> 예시 출력: 배터리\n"
        "- 예시 입력: '배터리랑 디스플레이 관련 논문 필요해' -> 예시 출력: 배터리, 디스플레이\n"
        "- 예시 입력: '전기차 배터리 관련 논문' -> 예시 출력: 전기차배터리\n"
        "- 예시 입력: '전기차배터리 관련 논문' -> 예시 출력: 전기차배터리\n"
        "- 예시 입력: '논문 찾아줘' -> 예시 출력: (아무것도 출력하지 않음)\n"
        "- 예시 입력: '논문 좀 찾아줄래' -> 예시 출력: (아무것도 출력하지 않음)\n"
        "- 예시 입력: '논문 검색해줘' -> 예시 출력: (아무것도 출력하지 않음)\n"
        "- 예시 입력: '기술' -> 예시 출력: 기술\n"
        f"입력: {cleaned_input}\n출력:"
    )

    try:
        response = await CLIENT.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0,
        )
        raw_output = response.choices[0].message.content.strip()
        result = _clean_keyword_output(raw_output, cleaned_input, limit=KEYWORD_LIMIT)
        return result if result and result.strip() else None
    except Exception:
        logging.exception("OpenAI keyword generation failed.")
        result = _clean_keyword_output(cleaned_input, cleaned_input, limit=KEYWORD_LIMIT)
        return result if result and result.strip() else None


async def generate_summary(
    user_query: str,
    keyword: str,
    results: Sequence[Dict[str, str]],
    search_source: str = "웹",
) -> str:
    """검색 결과를 요약하여 사용자에게 전달할 응답을 생성한다.

    Args:
        user_query (str): 사용자의 원래 질의.
        keyword (str): 생성된 검색 키워드.
        results (Sequence[Dict[str, str]]): 논문 제목과 원문 링크 정보.
        search_source (str): 검색 소스 ("웹" 또는 "사내 서버"). 기본값은 "웹".

    Returns:
        str: 사용자에게 보여줄 요약 응답. OpenAI API 사용 불가 시 기본 문자열 반환.
    """
    if CLIENT is None:
        if not results:
            return (
                f"사용자 질의: {user_query}\n"
                "검색 결과를 찾지 못했습니다. 키워드를 변경하거나 다시 시도해 주세요."
            )

        lines = "\n".join(
            f"{idx+1}. {item.get('title', '제목 없음')} ({item.get('pdf_link', '링크 없음')})"
            for idx, item in enumerate(results)
        )
        return f"사용자 질의: {user_query}\n검색 키워드: {keyword}\n{lines}"

    bullet_list = "\n".join(
        f"{idx+1}. 제목: {item.get('title', '제목 없음')}\n  링크: {item.get('pdf_link', '링크 없음')}"
        for idx, item in enumerate(results)
    ) or "검색 결과가 없습니다."

    # 검색 소스에 따른 명확한 프롬프트
    source_description = "웹에서 검색한" if search_source == "웹" else "사내 서버에서 검색한"
    
    prompt = (
        f"다음은 사용자의 질문과 {source_description} ScienceON 검색 결과입니다.\n"
        "사용자가 빠르게 이해할 수 있도록 한국어로 친절하게 요약 답변을 작성해 주세요.\n"
        "⚠️ 논문 제목 목록을 포함하지 말고, 검색 결과 요약만 제공하세요.\n"
        "만약 결과가 없다면, 재검색 팁과 가능한 이유를 안내해 주세요.\n\n"
        "답변 마지막에 '추가적인 정보가 필요하시면 논문 번호(예: 1번)나 제목과 함께 질문해 주세요.'라는 안내 문구를 포함해 주세요.\n\n"
        f"[사용자 질문]\n{user_query}\n\n"
        f"[검색 키워드]\n{keyword}\n\n"
        f"[검색 소스]\n{search_source}\n\n"
        f"[검색 결과 개수]\n{len(results)}건\n"
    )

    try:
        response = await CLIENT.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logging.exception("OpenAI summary generation failed.")
        if not results:
            return (
                f"사용자 질의: {user_query}\n"
                "검색 결과를 찾지 못했습니다. 잠시 후 다시 시도하거나 다른 키워드를 입력해 주세요."
            )

        lines = "\n".join(
            f"{idx+1}. {item.get('title', '제목 없음')} ({item.get('pdf_link', '링크 없음')})"
            for idx, item in enumerate(results)
        )
        return (
            f"사용자 질의: {user_query}\n검색 키워드: {keyword}\n"
            "아래는 AI 요약 없이 원문 링크만 제공합니다:\n"
            f"{lines}"
        )


# 캐시: (원본 키워드) -> (정규화된 한글, 영문 변환)
_keyword_translation_cache: Dict[str, Tuple[str, str]] = {}
_CACHE_MAX_SIZE = 1000


def _get_fallback_keyword_map() -> Dict[str, str]:
    """기존 keyword_map을 반환 (fallback용)."""
    return {
        "디스플레이": "display",
        "인공지능": "ai",
        "배터리": "battery",
        # "베터리": "battery",  # 오타 대응
        "반도체": "semiconductor",
        "센서": "sensor",
        "전자": "electronics",
        "통신": "communication",
        "소프트웨어": "software",
        "하드웨어": "hardware",
        "네트워크": "network",
        "보안": "security",
        "데이터": "data",
        "알고리즘": "algorithm",
        "머신러닝": "machine_learning",
        "딥러닝": "deep_learning",
        "인터넷": "internet",
        "컴퓨터": "computer",
        "프로그램": "program",
        "애플리케이션": "application",
        "시스템": "system",
        "장치": "device",
        "방법": "method",
        "제어": "control",
        "처리": "process",
        "저장": "storage",
        "전송": "transmission",
        "수신": "reception",
        "디지털": "digital",
        "아날로그": "analog",
        "신호": "signal",
        "파일": "file",
        "이미지": "image",
        "비디오": "video",
        "오디오": "audio",
        "텍스트": "text",
        "음성": "voice",
        "화면": "screen",
        "터치": "touch",
        "입력": "input",
        "출력": "output",
        "전기": "electricity",
    }


def _sanitize_with_fallback(keyword: str) -> str:
    """Fallback 방식으로 키워드를 영문으로 변환 (기존 keyword_map 사용)."""
    keyword_map = _get_fallback_keyword_map()
    normalized = keyword.strip()
    normalized_no_space = normalized.replace(' ', '').replace('_', '')
    
    # keyword_map에서 매칭 시도
    if normalized in keyword_map:
        return keyword_map[normalized]
    elif normalized_no_space in keyword_map:
        return keyword_map[normalized_no_space]
    
    # 부분 일치 시도
    for korean_key, english_value in keyword_map.items():
        if korean_key in normalized:
            return english_value
    
    # 해시 기반 변환
    has_korean = any('가' <= char <= '힣' for char in normalized)
    english_only = re.sub(r'[^a-zA-Z0-9_]', '_', normalized)
    english_only = re.sub(r'_+', '_', english_only).strip('_')
    
    if has_korean:
        hash_value = hashlib.md5(normalized.encode('utf-8')).hexdigest()[:12]
        if english_only and len(english_only) >= 2:
            return f"{english_only}_{hash_value}"
        else:
            return f"kw_{hash_value}"
    elif english_only:
        if len(english_only) > 240:
            english_only = english_only[:240]
        return english_only.lower()
    else:
        hash_value = hashlib.md5(normalized.encode('utf-8')).hexdigest()[:12]
        return f"kw_{hash_value}"


async def normalize_and_translate_keyword(
    keyword: str, 
    use_llm: bool = True,
    use_cache: bool = True
) -> str:
    """키워드의 오타를 수정하고 영문으로 변환한다 (한 번의 LLM 호출로 통합).
    
    Args:
        keyword: 원본 키워드 (오타 가능)
        use_llm: LLM 사용 여부 (기본값: True)
        use_cache: 캐시 사용 여부 (기본값: True)
        
    Returns:
        영문 키워드 (Milvus 컬렉션 이름용)
    """
    original_keyword = keyword.strip()
    
    # 캐시 확인
    if use_cache and original_keyword in _keyword_translation_cache:
        return _keyword_translation_cache[original_keyword][1]  # 영문 변환 결과 반환
    
    # 1단계: Fallback keyword_map 확인 (빠른 매칭, LLM 호출 전에 체크)
    keyword_map = _get_fallback_keyword_map()
    normalized_no_space = original_keyword.replace(' ', '').replace('_', '')
    
    if original_keyword in keyword_map:
        english_keyword = keyword_map[original_keyword]
        normalized_korean = original_keyword
    elif normalized_no_space in keyword_map:
        english_keyword = keyword_map[normalized_no_space]
        normalized_korean = normalized_no_space
    else:
        english_keyword = None
        normalized_korean = original_keyword
        
        # 2단계: LLM으로 오타 수정 + 영문 변환 (keyword_map에 정확히 일치하는 항목이 없을 때)
        if use_llm and CLIENT:
            try:
                # 한 번의 LLM 호출로 오타 수정 + 영문 변환 동시 수행
                prompt = (
                    "다음 키워드를 처리해줘:\n"
                    "1. 맞춤법이 틀리거나 오타가 있으면 수정해줘 (예: '베터리' -> '배터리')\n"
                    "2. 수정된 한글 키워드를 기술/과학 용어의 영문 키워드로 변환해줘\n\n"
                    "출력 형식:\n"
                    "한글: [수정된 한글 키워드]\n"
                    "영문: [영문 키워드]\n\n"
                    "- 복합어는 언더스코어로 연결 (예: machine_learning, electric_vehicle)\n"
                    "- 소문자로 변환\n"
                    "- 설명 없이 형식만 지켜줘\n\n"
                    f"입력: {original_keyword}\n출력:"
                )
                
                response = await CLIENT.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=64,
                    temperature=0,
                )
                
                output = response.choices[0].message.content.strip()
                
                # 파싱: "한글: 배터리\n영문: battery" 형식
                korean_match = re.search(r'한글[:\s]+([^\n]+)', output, re.IGNORECASE)
                english_match = re.search(r'영문[:\s]+([^\n]+)', output, re.IGNORECASE)
                
                if korean_match and english_match:
                    normalized_korean = korean_match.group(1).strip()
                    english_keyword_raw = english_match.group(1).strip()
                    # 영문자, 숫자, 언더스코어만 추출
                    english_keyword = re.sub(r'[^a-zA-Z0-9_]', '', english_keyword_raw)
                    english_keyword = re.sub(r'_+', '_', english_keyword).strip('_')
                    english_keyword = english_keyword.lower() if english_keyword else None
                else:
                    # 파싱 실패 시 fallback
                    english_keyword = None
                    normalized_korean = original_keyword
                    
            except Exception:
                logging.warning(f"LLM translation failed for '{original_keyword}', using fallback.")
                english_keyword = None
                normalized_korean = original_keyword
        
        # 3단계: Fallback 방식 사용 (LLM 실패 또는 사용 안 함)
        if english_keyword is None:
            english_keyword = _sanitize_with_fallback(original_keyword)
            normalized_korean = original_keyword
    
    # 캐시에 저장 (크기 제한)
    if use_cache:
        if len(_keyword_translation_cache) >= _CACHE_MAX_SIZE:
            # 가장 오래된 항목 제거 (FIFO 방식)
            oldest_key = next(iter(_keyword_translation_cache))
            del _keyword_translation_cache[oldest_key]
        _keyword_translation_cache[original_keyword] = (normalized_korean, english_keyword)
    
    return english_keyword
