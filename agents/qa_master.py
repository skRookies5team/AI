from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .qa_manual_agent import manual_search
from .qa_welfare_agent import welfare_info
from .qa_cafeteria_agent import cafeteria_menu
from .email_search_agent import email_search, has_pending_email_results
from .qa_benefit_agent import benefit_rag
from .llm_router import classify_intent_with_llm


@dataclass
class QaIntent:
    kind: Literal["manual", "welfare", "benefit", "cafeteria", "email"]
    details: Optional[str] = None


def parse_qa_intent(user_text: str) -> QaIntent:
    """LLM 기반 의도 분류 (키워드 fallback 포함)."""
    t = user_text.strip()
    
    # 상세 번호 선택: 최근 이메일 검색 결과가 있고 숫자/상세 패턴이면 이메일로 라우팅
    if has_pending_email_results():
        import re as _re
        if _re.fullmatch(r"\d+", t) or any(k in t for k in ["상세", "선택", "번"]):
            return QaIntent(kind="email", details=t)
    
    # LLM 기반 라우팅 시도
    llm_intent = classify_intent_with_llm(t, route_type="qa", fallback_route=None)
    
    if llm_intent:
        if llm_intent == "benefit":
            return QaIntent(kind="benefit", details=t)
        elif llm_intent == "email":
            return QaIntent(kind="email", details=t)
        elif llm_intent == "cafeteria":
            return QaIntent(kind="cafeteria", details=t)
        elif llm_intent == "manual":
            return QaIntent(kind="manual", details=t)
    
    # LLM 실패 시 키워드 기반 fallback
    # 이메일 검색 의도: 메일/이메일 + 찾기/검색 류 동사
    if any(k in t for k in ["메일", "이메일"]) and any(v in t for v in ["찾아", "찾아줘", "검색", "search", "find"]):
        return QaIntent(kind="email", details=t)
    # '메일에서' / '이메일에서' 문구도 이메일 검색으로 처리
    if any(phrase in t for phrase in ["메일에서", "이메일에서"]):
        return QaIntent(kind="email", details=t)
    
    # 복리후생 관련 키워드 (benefit_rag로 라우팅)
    if any(k in t for k in ["복리후생", "휴가규정", "근무제도", "급여 외 혜택", "회사 복지", "복지", "수당", "보험", "퇴직연금", "휴가", "근무"]):
        return QaIntent(kind="benefit", details=t)
    
    if any(k in t for k in ["식단", "메뉴", "구내식당", "점심", "저녁"]):
        return QaIntent(kind="cafeteria", details=t)
    
    # 기본은 메뉴얼 검색
    return QaIntent(kind="manual", details=t)


def qa_master(user_text: str) -> dict:
    """QA 마스터 에이전트: LLM 기반 라우팅으로 서브 에이전트 호출."""
    intent = parse_qa_intent(user_text)
    
    if intent.kind == "email":
        return email_search(intent.details or user_text)
    if intent.kind == "benefit":
        return benefit_rag(intent.details or user_text)
    if intent.kind == "welfare":
        return welfare_info(intent.details or user_text)
    if intent.kind == "cafeteria":
        return cafeteria_menu(intent.details or user_text)
    return manual_search(intent.details or user_text)

