from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .tech_patent_agent import patent_search
from .tech_article_agent import article_search
from .llm_router import classify_intent_with_llm


@dataclass
class TechIntent:
    kind: Literal["patent", "article"]
    keyword: Optional[str] = None
    details: Optional[str] = None


def _extract_keyword(user_text: str) -> str:
    t = user_text.strip()
    # 매우 단순: 따옴표 안, 또는 '관련' 앞 단어를 키워드로 사용
    for quote in ['"', "'"]:
        if t.count(quote) >= 2:
            first = t.find(quote) + 1
            second = t.find(quote, first)
            if second > first:
                return t[first:second].strip()
    if "관련" in t:
        return t.split("관련")[0].strip()
    return t


def parse_tech_intent(user_text: str) -> TechIntent:
    """LLM 기반 의도 분류 (키워드 fallback 포함)."""
    t = user_text.strip()
    
    # LLM 기반 라우팅 시도
    llm_intent = classify_intent_with_llm(t, route_type="tech", fallback_route=None)
    
    if llm_intent == "patent":
        return TechIntent(kind="patent", keyword=_extract_keyword(t), details=t)
    elif llm_intent == "article":
        return TechIntent(kind="article", keyword=_extract_keyword(t), details=t)
    
    # LLM 실패 시 키워드 기반 fallback
    if any(k in t for k in ["특허", "Patent", "patent"]):
        return TechIntent(kind="patent", keyword=_extract_keyword(t), details=t)
    if any(k in t for k in ["논문", "Article", "paper", "학술"]):
        return TechIntent(kind="article", keyword=_extract_keyword(t), details=t)
    # 기본은 특허 조회
    return TechIntent(kind="patent", keyword=_extract_keyword(t), details=t)


def tech_master(user_text: str, session_id: Optional[str] = None) -> dict:
    """Tech 마스터 에이전트: 특허/논문 검색.
    
    Args:
        user_text: 사용자 질의
        session_id: 세션 ID (멀티턴 대화 지원)
    """
    intent = parse_tech_intent(user_text)
    if intent.kind == "patent":
        return patent_search(intent.keyword or user_text, session_id=session_id)
    if intent.kind == "article":
        return article_search(intent.keyword or user_text)
    # 기본은 특허 검색
    return patent_search(intent.keyword or user_text, session_id=session_id)




