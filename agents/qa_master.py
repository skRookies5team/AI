from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .qa_manual_agent import manual_search
from .qa_cafeteria_agent import cafeteria_menu
from .email_search_agent import email_search, has_pending_email_results
from .qa_benefit_agent import benefit_rag
from .llm_router import classify_intent_with_llm


@dataclass
class QaIntent:
    kind: Literal["manual", "benefit", "cafeteria", "email"]
    details: Optional[str] = None


def parse_qa_intent(user_text: str, session_id: Optional[str] = None) -> QaIntent:
    """LLM 기반 의도 분류 (키워드 fallback 포함).

    Args:
        user_text: 사용자 질의
        session_id: 세션 ID (멀티턴 대화 지원)
    """
    t = user_text.strip()
    import re as _re

    # 이메일 검색 연속 요청 체크 (이전 검색 결과가 있는 경우)
    # "더 찾아줘", "계속 검색", "더 검색" 등의 요청은 이전 이메일 검색의 연속
    if has_pending_email_results(session_id) and any(k in t.lower() for k in ["더", "계속", "추가", "더 찾", "계속 검색", "더 검색", "더 찾아줘", "계속 찾아줘"]):
        return QaIntent(kind="email", details=t)

    # 이메일 상세 요청 우선 체크 (복리후생 키워드보다 우선)
    # 숫자로 시작 + 제목 형식이 있으면 이메일 상세 요청으로 간주
    # 예: "938. [고용보험]육아휴직...에 대해 알려줘"
    if has_pending_email_results(session_id):
        # 숫자로 시작 + 제목 형식 (예: "355. 육아휴직 연장 안내의 건")
        # 단, 날짜 표현이나 다른 명확한 의도가 아닐 때만
        if _re.match(r"^\d+\.?\s*[^0-9]", t):
            # 날짜 표현(예: "17일")이나 다른 명확한 의도가 아닐 때만 이메일로
            if not any(k in t for k in ["일", "월", "년", "시", "분", "점심", "저녁", "메뉴", "식단", "구내식당"]):
                return QaIntent(kind="email", details=t)

    # 명확한 다른 의도 키워드가 있으면 우선 처리 (이메일 검색 결과가 있어도)
    # 식단/메뉴 관련 키워드 우선 체크
    if any(k in t for k in ["식단", "메뉴", "구내식당", "점심", "저녁", "아침", "식사", "밥"]):
        return QaIntent(kind="cafeteria", details=t)

    # 복리후생 관련 키워드 체크
    # 주의: 숫자로 시작하는 패턴은 이미 위에서 처리되었으므로 여기서는 제외
    # "에 대해 알려줘" 같은 패턴이 있으면 이메일 상세 요청일 가능성이 높음
    if not _re.match(r"^\d+", t) and not any(k in t for k in ["에 대해", "에대해", "자세히", "상세히", "알려줘"]) and \
       any(k in t for k in ["복리후생", "휴가규정", "근무제도", "급여 외 혜택", "회사 복지", "복지", "수당", "보험", "퇴직연금", "휴가", "근무"]):
        return QaIntent(kind="benefit", details=t)

    # 상세 번호 선택: 최근 이메일 검색 결과가 있고 숫자/상세 패턴이면 이메일로 라우팅
    # 단, 명확한 다른 의도가 없는 경우에만
    if has_pending_email_results(session_id):
        # 숫자만 입력된 경우 (예: "355")
        if _re.fullmatch(r"\d+", t):
            return QaIntent(kind="email", details=t)
        # 숫자로 시작 + 상세 요청 키워드 (예: "355번", "355 상세", "355번 상세")
        # 단, 이메일 관련 키워드가 없으면 다른 의도일 수 있으므로 더 정확히 체크
        if any(k in t for k in ["상세", "선택", "번"]) and not any(k in t for k in ["점심", "저녁", "메뉴", "식단", "구내식당"]):
            return QaIntent(kind="email", details=t)
        # 숫자로 시작 + "에 대해 자세히" 패턴 (예: "355. 제목에 대해 자세히 알려줘")
        # 주의: 특허/논문 관련 키워드가 있으면 tech로 라우팅되어야 하므로 제외
        if _re.match(r"^\d+\.?\s*", t) and any(k in t for k in ["에 대해", "에대해", "자세히", "상세히", "알려줘", "알려"]):
            # 다른 명확한 의도 키워드가 없을 때만 이메일로
            # 특허/논문 관련 키워드가 있으면 tech로 라우팅되어야 함
            if not any(k in t for k in ["점심", "저녁", "메뉴", "식단", "구내식당", "식사", "밥",
                                        "특허", "논문", "paper", "patent", "KISTI", "ScienceON", "학술"]):
                return QaIntent(kind="email", details=t)
        # 숫자로 시작 + 제목 형식 (예: "355. 육아휴직 연장 안내의 건")
        # 단, 날짜 표현이나 다른 명확한 의도가 아닐 때만
        if _re.match(r"^\d+\.?\s*[^0-9]", t):
            # 날짜 표현(예: "17일")이나 다른 명확한 의도가 아닐 때만 이메일로
            if not any(k in t for k in ["일", "월", "년", "시", "분", "점심", "저녁", "메뉴", "식단", "구내식당"]):
                return QaIntent(kind="email", details=t)

    # LLM 기반 라우팅 시도
    llm_intent = classify_intent_with_llm(t, route_type="qa", fallback_route=None, session_id=session_id)

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
    # 단, 숫자로 시작하는 패턴이 있으면 이메일 상세 요청이므로 제외
    if not _re.match(r"^\d+", t) and any(k in t for k in ["복리후생", "휴가규정", "근무제도", "급여 외 혜택", "회사 복지", "복지", "수당", "보험", "퇴직연금", "휴가", "근무"]):
        return QaIntent(kind="benefit", details=t)
    
    if any(k in t for k in ["식단", "메뉴", "구내식당", "점심", "저녁"]):
        return QaIntent(kind="cafeteria", details=t)
    
    # 기본은 메뉴얼 검색
    return QaIntent(kind="manual", details=t)


def qa_master(user_text: str, session_id: Optional[str] = None) -> dict:
    """QA 마스터 에이전트: LLM 기반 라우팅으로 서브 에이전트 호출.

    Args:
        user_text: 사용자 질의
        session_id: 세션 ID (멀티턴 대화 지원)
    """
    intent = parse_qa_intent(user_text, session_id=session_id)

    if intent.kind == "email":
        return email_search(intent.details or user_text, session_id=session_id)
    if intent.kind == "benefit":
        return benefit_rag(intent.details or user_text)
    if intent.kind == "cafeteria":
        return cafeteria_menu(intent.details or user_text)
    return manual_search(intent.details or user_text)

