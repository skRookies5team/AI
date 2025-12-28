"""프롬프트 및 LLM 설정."""

from __future__ import annotations
from typing import Optional, Dict, Any
import logging

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from agents.master_graphs.tech.article_agent_module.state import (
    OPENAI_MODEL,
    OPENAI_TIMEOUT,
)

LOGGER = logging.getLogger(__name__)

# YAML에서 프롬프트 로드 (지연 초기화)
_ARTICLE_PROMPTS: Optional[Dict[str, Any]] = None

def load_article_prompts() -> Dict[str, Any]:
    """prompts.yaml에서 article 프롬프트를 로드 (llm_router 통합 로더 사용).
    
    Returns:
        Article 프롬프트 딕셔너리 (로드 실패 시 빈 딕셔너리)
    """
    global _ARTICLE_PROMPTS
    if _ARTICLE_PROMPTS is None:
        try:
            # llm_router의 통합 로더 사용
            from agents.llm_router import load_agent_prompts
            all_prompts = load_agent_prompts()
            
            tech_prompts = all_prompts.get("tech", {})
            _ARTICLE_PROMPTS = tech_prompts.get('sub_agents', {}).get('article', {}).get('prompts', {})
            
            if not _ARTICLE_PROMPTS:
                LOGGER.warning(
                    "Article 프롬프트를 찾을 수 없습니다. "
                    "agents/master_graphs/tech/prompts.yaml 파일의 sub_agents.article.prompts 섹션을 확인해주세요."
                )
                _ARTICLE_PROMPTS = {}
        except Exception as e:
            LOGGER.warning(f"YAML 파일 로드 실패: {e}. 기본 프롬프트를 사용합니다.")
            _ARTICLE_PROMPTS = {}
    
    return _ARTICLE_PROMPTS

def get_article_prompt(key: str, sub_key: Optional[str] = None) -> str:
    """Article 프롬프트를 가져옵니다.
    
    Args:
        key: 프롬프트 키 (예: 'intent_classification', 'rag_answer')
        sub_key: 하위 키 (예: 'system_prompt', 'human_prompt')
    
    Returns:
        프롬프트 문자열 (없으면 빈 문자열, 경고 로그 출력)
    """
    prompts = load_article_prompts()
    
    result = ""
    if sub_key:
        result = prompts.get(key, {}).get(sub_key, "")
    else:
        result = prompts.get(key, "")
    
    if not result:
        LOGGER.warning(
            f"프롬프트를 찾을 수 없습니다: key={key}, sub_key={sub_key}. "
            f"agents/master_graphs/tech/prompts.yaml 파일의 sub_agents.article.prompts.{key}"
            f"{'.' + sub_key if sub_key else ''} 섹션을 확인해주세요."
        )
    
    return result

# Intent 분류용 LLM (지연 초기화로 변경하여 환경 변수 로드 문제 방지)
_LLM_INSTANCE: Optional[ChatOpenAI] = None

def get_intent_llm() -> ChatOpenAI:
    """Intent 분류용 LLM 인스턴스 반환 (지연 초기화)."""
    global _LLM_INSTANCE
    if _LLM_INSTANCE is None:
        if not OPENAI_MODEL:
            raise ValueError("OPENAI_MODEL 환경 변수가 설정되지 않았습니다. .env 파일에 OPENAI_MODEL을 설정해주세요.")
        _LLM_INSTANCE = ChatOpenAI(
            model=OPENAI_MODEL,
            temperature=0.0,
            max_tokens=512,
            timeout=OPENAI_TIMEOUT,
            max_retries=2,
        )
    return _LLM_INSTANCE

# 하위 호환성을 위한 별칭 (지연 초기화)
# OPENAI_MODEL이 None이면 초기화하지 않고, 사용 시점에 get_intent_llm() 호출
if OPENAI_MODEL:
    try:
        LLM = get_intent_llm()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"LLM 초기화 실패: {e}")
        LLM = None
else:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning("OPENAI_MODEL 환경 변수가 설정되지 않았습니다. LLM은 None입니다.")
    LLM = None

# Intent 분류 프롬프트 (YAML에서 로드, 지연 초기화)
_INTENT_PROMPT_CACHE: Optional[ChatPromptTemplate] = None

def get_intent_prompt() -> ChatPromptTemplate:
    """Intent 분류 프롬프트를 YAML에서 로드하여 반환 (지연 초기화)."""
    global _INTENT_PROMPT_CACHE
    if _INTENT_PROMPT_CACHE is None:
        system_prompt = get_article_prompt('intent_classification', 'system_prompt')
        human_prompt = get_article_prompt('intent_classification', 'human_prompt')
        
        # 프롬프트가 비어있으면 기본값 사용
        if not system_prompt or not human_prompt:
            LOGGER.warning("Intent 분류 프롬프트가 비어있습니다. 기본 프롬프트를 사용합니다.")
            # 기본 프롬프트 (fallback)
            system_prompt = system_prompt or "당신은 ScienceON 논문 검색 챗봇의 의도 분류 전문가입니다."
            human_prompt = human_prompt or "사용자 입력: {user_input}\n위 정보를 바탕으로 의도를 분류하고 JSON을 반환하세요:"
        
        _INTENT_PROMPT_CACHE = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", human_prompt),
        ])
    
    return _INTENT_PROMPT_CACHE

# 하위 호환성을 위한 별칭 (지연 초기화 프록시)
class _IntentPromptProxy:
    """INTENT_PROMPT 접근을 위한 프록시 클래스 (지연 초기화)."""
    
    def format_messages(self, **kwargs):
        """ChatPromptTemplate.format_messages()와 동일한 인터페이스."""
        return get_intent_prompt().format_messages(**kwargs)
    
    def __call__(self):
        """직접 호출 시 ChatPromptTemplate 반환."""
        return get_intent_prompt()

INTENT_PROMPT = _IntentPromptProxy()

