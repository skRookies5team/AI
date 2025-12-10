"""특허 검색 관련 유틸리티 함수들."""

import re
from typing import Optional, List

from .patent_keyword import generate_patent_keyword


def _extract_keyword(user_text: str) -> str:
    """사용자 질문에서 키워드 추출 (LLM 기반).
    
    특허 전용 키워드 생성 모듈을 사용하여 동적으로 키워드를 생성합니다.
    """
    try:
        keyword = generate_patent_keyword(user_text)
        return keyword if keyword else user_text
    except Exception as e:
        # LLM 실패 시 기본 파싱 로직 사용
        t = user_text.strip()
        
        # "관련" 앞 단어 추출
        if "관련" in t:
            keyword = t.split("관련")[0].strip()
            keyword = re.sub(r"\s*(검색|찾아|찾아줘|검색해줘|검색해|찾아서).*$", "", keyword).strip()
            return keyword
        
        # 따옴표 안 내용 추출
        for quote in ['"', "'"]:
            if t.count(quote) >= 2:
                first = t.find(quote) + 1
                second = t.find(quote, first)
                if second > first:
                    return t[first:second].strip()
        
        # 기본: "특허" 키워드 제거 후 나머지
        keyword = re.sub(r"(특허|patent|Patent)\s*", "", t, flags=re.IGNORECASE).strip()
        keyword = re.sub(r"\s*(검색|찾아|찾아줘|검색해줘|검색해|찾아서).*$", "", keyword).strip()
        
        return keyword if keyword else t


def _parse_selection_number(user_text: str) -> Optional[int]:
    """사용자 입력에서 선택 번호 파싱 (예: "1번", "7번", "7 상세")."""
    t = user_text.strip()
    
    # 숫자만 입력된 경우
    if re.fullmatch(r"\d+", t):
        return int(t) - 1
    
    # 숫자 + "번", "상세", "선택" 등
    match = re.match(r"^(\d+)", t)
    if match:
        num = int(match.group(1))
        if any(k in t for k in ["번", "상세", "선택", "자세히"]):
            return num - 1
    
    return None


def _parse_selection_numbers(user_text: str) -> Optional[List[int]]:
    """사용자 입력에서 여러 선택 번호 파싱 (예: "1,2,5", "1번,2번,5번", "1 2 5").

    Returns:
        파싱된 번호 리스트 (0-based 인덱스), None이면 단일 번호 파싱 시도
    """
    t = user_text.strip()

    # 쉼표로 구분된 번호들 (예: "1,2,5,8,10" 또는 "1번,2번,5번")
    if "," in t:
        numbers = []
        parts = [p.strip() for p in t.split(",")]
        for part in parts:
            # "1번" -> 1, "2" -> 2
            match = re.match(r"^(\d+)", part)
            if match:
                num = int(match.group(1))
                numbers.append(num - 1)  # 0-based 인덱스로 변환
        if numbers:
            return numbers

    # 공백으로 구분된 번호들 (예: "1 2 5 8 10")
    if re.search(r"\d+\s+\d+", t):
        numbers = []
        matches = re.findall(r"\d+", t)
        for match in matches:
            num = int(match)
            numbers.append(num - 1)  # 0-based 인덱스로 변환
        if len(numbers) > 1:  # 여러 개가 있을 때만 반환
            return numbers

    return None

