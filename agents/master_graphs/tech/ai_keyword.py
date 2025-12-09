import logging
import os
import re
from typing import Dict, List, Optional, Sequence, Set

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

if not OPENAI_API_KEY:
    logging.warning("OPENAI_API_KEY is not set.")

_STOP_WORDS: Set[str] = {"논문", "연구", "관련", "자료", "정보", "소개", "분석"}
KEYWORD_LIMIT: int = 3


def _build_client() -> Optional[OpenAI]:
    """OpenAI 클라이언트를 생성한다.

    Returns:
        Optional[OpenAI]: API 키가 존재하면 OpenAI 클라이언트, 없으면 None.
    """
    if not OPENAI_API_KEY:
        return None

    return OpenAI(api_key=OPENAI_API_KEY)


CLIENT: Optional[OpenAI] = _build_client()


def _clean_keyword_output(raw_output: str, fallback: str, limit: int = KEYWORD_LIMIT) -> str:
    """LLM이 생성한 키워드에서 불필요한 토큰을 제거한다.

    Args:
        raw_output (str): LLM이 반환한 원본 문자열.
        fallback (str): 정제 결과가 없을 때 사용할 기본 문자열.
        limit (int): 반환할 최대 키워드 개수.

    Returns:
        str: 쉼표로 구분된 정제된 키워드 문자열.
    """
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


def generate_search_keyword(user_input: str, max_tokens: int = 32) -> str:
    """사용자 질의를 학술 검색 키워드로 변환한다.

    Args:
        user_input (str): 사용자의 자연어 질의.
        max_tokens (int): 생성할 키워드의 최대 토큰 수.

    Returns:
        str: 검색에 활용할 간결한 키워드. OpenAI API를 사용할 수 없으면 원본 질의를 반환.
    """
    if CLIENT is None:
        return _clean_keyword_output(user_input, user_input, limit=KEYWORD_LIMIT)

    prompt = (
        "다음 사용자 질의를 학술 논문 검색용 핵심 키워드로 요약해줘.\n"
        "- 출력 형식: 쉼표로 구분된 최대 3개의 키워드만 반환해.\n"
        "- 모든 키워드는 무조건 사용자 입력에 존재하는 단어로만 선택해.\n"
        "- 각 키워드는 1~2개의 핵심 단어로 구성하고, 질문에 등장한 핵심 개념에서만 선택해.\n"
        "- '논문', '연구', '관련', '자료' 같은 일반 단어와 조사는 모두 제거해.\n"
        "- 번호, 설명, 불릿, 접두어 같은 추가 문장은 절대 포함하지 마.\n"
        "- 예시 입력: '다음으로 인공지능 관련 논문은?' -> 예시 출력: 인공지능, 머신러닝, 딥러닝\n"
        f"입력: {user_input}\n출력:"
    )

    try:
        response = CLIENT.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0,
        )
        raw_output = response.choices[0].message.content.strip()
        return _clean_keyword_output(raw_output, user_input, limit=KEYWORD_LIMIT)
    except Exception:
        logging.exception("OpenAI keyword generation failed.")
        return _clean_keyword_output(user_input, user_input, limit=KEYWORD_LIMIT)


def generate_summary(
    user_query: str,
    keyword: str,
    results: Sequence[Dict[str, str]],
) -> str:
    """검색 결과를 요약하여 사용자에게 전달할 응답을 생성한다.

    Args:
        user_query (str): 사용자의 원래 질의.
        keyword (str): 생성된 검색 키워드.
        results (Sequence[Dict[str, str]]): 논문 제목과 원문 링크 정보.

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
            f"- {item.get('title', '제목 없음')} ({item.get('pdf_link', '링크 없음')})"
            for item in results
        )
        return f"사용자 질의: {user_query}\n검색 키워드: {keyword}\n{lines}"

    bullet_list = "\n".join(
        f"- 제목: {item.get('title', '제목 없음')}\n  링크: {item.get('pdf_link', '링크 없음')}"
        for item in results
    ) or "검색 결과가 없습니다."

    prompt = (
        "다음은 사용자의 질문과 ScienceON 검색 결과 목록입니다.\n"
        "사용자가 빠르게 이해할 수 있도록 한국어로 친절하게 요약 답변을 작성해 주세요.\n"
        "각 항목의 논문 제목만 순서대로 알려주고 원문 바로가기 링크는 답변에 포함하지 마세요.\n"
        "만약 결과가 없다면, 재검색 팁과 가능한 이유를 안내해 주세요.\n\n"
        f"[사용자 질문]\n{user_query}\n\n"
        f"[검색 키워드]\n{keyword}\n\n"
        f"[검색 결과]\n{bullet_list}\n"
    )

    try:
        response = CLIENT.chat.completions.create(
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
            f"- {item.get('title', '제목 없음')} ({item.get('pdf_link', '링크 없음')})"
            for item in results
        )
        return (
            f"사용자 질의: {user_query}\n검색 키워드: {keyword}\n"
            "아래는 AI 요약 없이 원문 링크만 제공합니다:\n"
            f"{lines}"
        )

