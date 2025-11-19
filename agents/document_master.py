from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .doc_generate_agent import doc_generate
from .email_auto_reply_agent import email_auto_reply
from .llm_router import classify_intent_with_llm


@dataclass
class DocumentIntent:
    kind: Literal["doc_generate", "email_auto_reply"]
    details: Optional[str] = None


def parse_document_intent(user_text: str) -> DocumentIntent:
    """LLM 기반 의도 분류 (키워드 fallback 포함)."""
    t = user_text.strip()
    
    # LLM 기반 라우팅 시도
    llm_intent = classify_intent_with_llm(t, route_type="document", fallback_route=None)
    
    if llm_intent == "email_auto_reply":
        return DocumentIntent(kind="email_auto_reply", details=t)
    elif llm_intent == "doc_generate":
        return DocumentIntent(kind="doc_generate", details=t)
    
    # LLM 실패 시 키워드 기반 fallback
    if any(k in t for k in ["이메일", "메일", "자동응답", "auto-reply", "auto reply"]):
        return DocumentIntent(kind="email_auto_reply", details=t)
    # 기본은 문서 자동 작성
    return DocumentIntent(kind="doc_generate", details=t)


def document_master(user_text: str) -> dict:
    intent = parse_document_intent(user_text)
    if intent.kind == "email_auto_reply":
        return email_auto_reply(intent.details or user_text)
    return doc_generate(intent.details or user_text)

