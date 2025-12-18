"""특허 검색 관련 환경 설정 및 전역 변수."""

import os
import json
import re
import logging
from pathlib import Path
from typing import Optional, Dict, List, Literal, Callable, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    try:
        env_path = os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_path):
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


# KIPRIS API 설정
KIPRIS_API_KEY = _get_env("KIPRIS_API_KEY")
OPENAI_API_KEY = _get_env("OPENAI_API_KEY")
OPENAI_MODEL = _get_env("OPENAI_MODEL")

# 특허 데이터 디렉토리
def _get_base_dir() -> Path:
    """기본 디렉토리 경로 반환."""
    return Path(__file__).resolve().parent.parent.parent.parent


PATENT_DATA_DIR = _get_base_dir() / "data"

# ───────────────────────────────────────────────────────────
# 전역 상태 관리 (멀티턴 대화 지원) - Redis 우선, 로컬 캐시는 하위 호환성
# ───────────────────────────────────────────────────────────
_PATENT_SEARCH_CACHE: Dict[str, List[Dict]] = {}  # 세션별 검색 결과 캐시 (하위 호환성)
_PATENT_SESSION_ID: Optional[str] = None
_WAITING_FOR_SEARCH_MODE: Dict[str, bool] = {}  # 세션별 검색 모드 선택 대기 상태
_PENDING_SEARCH_KEYWORD: Dict[str, str] = {}  # 세션별 검색 모드 선택 대기 중인 검색어 저장
_SESSION_SEARCH_MODE: Dict[str, Optional[Literal["server", "web"]]] = {}  # 세션별 검색 모드 저장
_WAITING_FOR_WEB_SEARCH: Dict[str, bool] = {}  # 세션별 웹 검색 여부 대기 상태
_ORIGINAL_SEARCH_KEYWORD: Dict[str, str] = {}  # 세션별 원래 검색어 저장 (웹 검색용)

# ───────────────────────────────────────────────────────────
# 진행 상황 콜백 및 골든셋 관리
# ───────────────────────────────────────────────────────────
_PROGRESS_CALLBACK: Optional[Callable[[str], None]] = None  # 진행 상황 콜백 함수
_GOLDEN_SET: Dict[str, str] = {}  # 골든셋 (키: 질문, 값: 답변)

# ───────────────────────────────────────────────────────────
# 전역 변수: 노드 함수에서 orchestrator_state 접근용
# ───────────────────────────────────────────────────────────
_current_orchestrator_state_for_node: Optional[Dict[str, Any]] = None


def set_progress_callback(callback: Optional[Callable[[str], None]]) -> None:
    """진행 상황 콜백 함수 설정."""
    global _PROGRESS_CALLBACK
    _PROGRESS_CALLBACK = callback


def _notify_progress(message: str) -> None:
    """진행 상황 알림."""
    if _PROGRESS_CALLBACK:
        try:
            _PROGRESS_CALLBACK(message)
        except Exception:
            pass


def load_golden_set(file_path: Optional[str] = None) -> None:
    """골든셋 로드 (JSON 파일)."""
    global _GOLDEN_SET
    if file_path is None:
        file_path = PATENT_DATA_DIR / "golden_set.json"

    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                _GOLDEN_SET = json.load(f)
    except Exception:
        _GOLDEN_SET = {}


def get_golden_set() -> Dict[str, str]:
    """골든셋 반환."""
    return _GOLDEN_SET


def _set_session_id(session_id: Optional[str] = None) -> str:
    """세션 ID 설정."""
    global _PATENT_SESSION_ID
    if session_id:
        _PATENT_SESSION_ID = session_id
    elif not _PATENT_SESSION_ID:
        import uuid
        _PATENT_SESSION_ID = str(uuid.uuid4())
    return _PATENT_SESSION_ID


def get_search_results(session_id: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> List[Dict]:
    """캐시된 검색 결과 가져오기 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에서 가져오기 (우선)
    if orchestrator_state and "tech_search_context" in orchestrator_state:
        tech_context = orchestrator_state.get("tech_search_context") or {}
        patent_context = tech_context.get("patent", {})
        if patent_context.get("results"):
            return patent_context.get("results", [])
    
    # 하위 호환성: 로컬 캐시 사용
    sid = _set_session_id(session_id)
    return _PATENT_SEARCH_CACHE.get(sid, [])


def save_search_results(results: List[Dict], session_id: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> None:
    """검색 결과 캐시에 저장 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에 저장 (우선)
    if orchestrator_state is not None:
        if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
            orchestrator_state["tech_search_context"] = {}
        if "patent" not in orchestrator_state["tech_search_context"]:
            orchestrator_state["tech_search_context"]["patent"] = {}
        
        orchestrator_state["tech_search_context"]["patent"]["results"] = results
    
    # 하위 호환성: 로컬 캐시에도 저장
    sid = _set_session_id(session_id)
    _PATENT_SEARCH_CACHE[sid] = results


def has_pending_patent_results(session_id: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> bool:
    """대기 중인 특허 검색 결과가 있는지 확인 (Redis 숏텀 메모리 우선)."""
    return len(get_search_results(session_id, orchestrator_state)) > 0


def is_waiting_for_search_mode(session_id: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> bool:
    """검색 모드 선택 대기 중인지 확인 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에서 확인 (우선)
    if orchestrator_state and "tech_search_context" in orchestrator_state:
        tech_context = orchestrator_state.get("tech_search_context") or {}
        patent_context = tech_context.get("patent", {})
        if "waiting_for_search_mode" in patent_context:
            return patent_context["waiting_for_search_mode"]
    
    # 하위 호환성: 로컬 캐시 확인
    sid = _set_session_id(session_id)
    return _WAITING_FOR_SEARCH_MODE.get(sid, False)


def set_waiting_for_search_mode(session_id: Optional[str] = None, waiting: bool = True, keyword: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> None:
    """검색 모드 선택 대기 상태 설정 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에 저장 (우선)
    if orchestrator_state is not None:
        if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
            orchestrator_state["tech_search_context"] = {}
        if "patent" not in orchestrator_state["tech_search_context"]:
            orchestrator_state["tech_search_context"]["patent"] = {}
        
        orchestrator_state["tech_search_context"]["patent"]["waiting_for_search_mode"] = waiting
        if keyword and waiting:
            orchestrator_state["tech_search_context"]["patent"]["pending_search_keyword"] = keyword
        elif not waiting:
            # waiting=False일 때는 keyword를 삭제하지 않음 (검색 모드 선택 후 사용하기 위해)
            # 하지만 Redis에서는 명시적으로 유지하므로 여기서는 삭제하지 않음
            pass
    
    # 하위 호환성: 로컬 캐시에도 저장
    sid = _set_session_id(session_id)
    _WAITING_FOR_SEARCH_MODE[sid] = waiting
    if keyword and waiting:
        _PENDING_SEARCH_KEYWORD[sid] = keyword


def get_pending_search_keyword(session_id: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """대기 중인 검색어 가져오기 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에서 확인 (우선)
    if orchestrator_state and "tech_search_context" in orchestrator_state:
        tech_context = orchestrator_state.get("tech_search_context") or {}
        patent_context = tech_context.get("patent", {})
        if "pending_search_keyword" in patent_context:
            return patent_context["pending_search_keyword"]
    
    # 하위 호환성: 로컬 캐시 확인
    sid = _set_session_id(session_id)
    return _PENDING_SEARCH_KEYWORD.get(sid)


def get_session_search_mode(session_id: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> Optional[Literal["server", "web"]]:
    """세션별 저장된 검색 모드 가져오기 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에서 확인 (우선)
    if orchestrator_state and "tech_search_context" in orchestrator_state:
        tech_context = orchestrator_state.get("tech_search_context") or {}
        patent_context = tech_context.get("patent", {})
        if "session_search_mode" in patent_context:
            return patent_context["session_search_mode"]
    
    # 하위 호환성: 로컬 캐시 확인
    sid = _set_session_id(session_id)
    return _SESSION_SEARCH_MODE.get(sid)


def set_session_search_mode(session_id: Optional[str] = None, mode: Optional[Literal["server", "web"]] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> None:
    """세션별 검색 모드 저장 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에 저장 (우선)
    if orchestrator_state is not None:
        if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
            orchestrator_state["tech_search_context"] = {}
        if "patent" not in orchestrator_state["tech_search_context"]:
            orchestrator_state["tech_search_context"]["patent"] = {}
        
        if mode:
            orchestrator_state["tech_search_context"]["patent"]["session_search_mode"] = mode
        else:
            orchestrator_state["tech_search_context"]["patent"].pop("session_search_mode", None)
    
    # 하위 호환성: 로컬 캐시에도 저장
    sid = _set_session_id(session_id)
    if mode:
        _SESSION_SEARCH_MODE[sid] = mode
    else:
        _SESSION_SEARCH_MODE.pop(sid, None)


def is_waiting_for_web_search(session_id: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> bool:
    """웹 검색 대기 중인지 확인 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에서 확인 (우선)
    if orchestrator_state and "tech_search_context" in orchestrator_state:
        tech_context = orchestrator_state.get("tech_search_context") or {}
        patent_context = tech_context.get("patent", {})
        if "waiting_for_web_search" in patent_context:
            return patent_context["waiting_for_web_search"]
    
    # 하위 호환성: 로컬 캐시 확인
    sid = _set_session_id(session_id)
    return _WAITING_FOR_WEB_SEARCH.get(sid, False)


def set_waiting_for_web_search(session_id: Optional[str] = None, waiting: bool = True, orchestrator_state: Optional[Dict[str, Any]] = None) -> None:
    """웹 검색 대기 상태 설정 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에 저장 (우선)
    if orchestrator_state is not None:
        if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
            orchestrator_state["tech_search_context"] = {}
        if "patent" not in orchestrator_state["tech_search_context"]:
            orchestrator_state["tech_search_context"]["patent"] = {}
        
        orchestrator_state["tech_search_context"]["patent"]["waiting_for_web_search"] = waiting
    
    # 하위 호환성: 로컬 캐시에도 저장
    sid = _set_session_id(session_id)
    if waiting:
        _WAITING_FOR_WEB_SEARCH[sid] = waiting
    else:
        _WAITING_FOR_WEB_SEARCH.pop(sid, None)


def get_original_search_keyword(session_id: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """원본 검색어 가져오기 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에서 확인 (우선)
    if orchestrator_state and "tech_search_context" in orchestrator_state:
        tech_context = orchestrator_state.get("tech_search_context") or {}
        patent_context = tech_context.get("patent")
        if patent_context and isinstance(patent_context, dict) and "original_search_keyword" in patent_context:
            return patent_context["original_search_keyword"]
    
    # 하위 호환성: 로컬 캐시 확인
    sid = _set_session_id(session_id)
    return _ORIGINAL_SEARCH_KEYWORD.get(sid)


def set_original_search_keyword(session_id: Optional[str] = None, keyword: Optional[str] = None, orchestrator_state: Optional[Dict[str, Any]] = None) -> None:
    """원본 검색어 저장 (Redis 숏텀 메모리 우선)."""
    # Redis 숏텀 메모리에 저장 (우선)
    if orchestrator_state is not None:
        if "tech_search_context" not in orchestrator_state or orchestrator_state["tech_search_context"] is None:
            orchestrator_state["tech_search_context"] = {}
        if "patent" not in orchestrator_state["tech_search_context"]:
            orchestrator_state["tech_search_context"]["patent"] = {}
        
        if keyword:
            orchestrator_state["tech_search_context"]["patent"]["original_search_keyword"] = keyword
        else:
            orchestrator_state["tech_search_context"]["patent"].pop("original_search_keyword", None)
    
    # 하위 호환성: 로컬 캐시에도 저장
    sid = _set_session_id(session_id)
    if keyword:
        _ORIGINAL_SEARCH_KEYWORD[sid] = keyword
    else:
        _ORIGINAL_SEARCH_KEYWORD.pop(sid, None)


def _load_patent_prompts() -> Dict[str, Any]:
    """특허 프롬프트 로드 (YAML에서)."""
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


async def parse_search_mode_with_llm(
    user_input: str,
    session_id: Optional[str] = None
) -> Optional[Literal["server", "web"]]:
    """LLM 기반 검색 모드 파싱 (완전 LLM 처리, 하드코딩 없음)."""
    api_key = _get_env("OPENAI_API_KEY")
    if not api_key:
        # API 키가 없으면 None 반환 (하드코딩 fallback 제거)
        logging.warning("OPENAI_API_KEY가 없어 검색 모드 파싱을 수행할 수 없습니다.")
        return None
    
    try:
        from langchain_openai import ChatOpenAI
        
        patent_config = _load_patent_prompts()
        classification_criteria = patent_config.get("search_mode_classification_criteria", "")
        
        # 1차 LLM 시도
        prompt_parts = []
        prompt_parts.append("사용자의 검색 모드 선택을 분석하여 분류하세요.\n")
        
        if classification_criteria:
            prompt_parts.append(classification_criteria)
        else:
            prompt_parts.append("""
            - server: 서버 기반 검색, 벡터DB 검색, 내부 데이터베이스 검색
            - web: 웹 검색, KIPRIS 검색, 인터넷 검색
            """)
        
        prompt_parts.append(f"\n사용자 입력: \"{user_input}\"")
        prompt_parts.append('\nJSON 형식으로만 응답: {"mode": "server" 또는 "web"}')
        
        prompt_text = "\n".join(prompt_parts)
        
        llm = ChatOpenAI(
            model=OPENAI_MODEL,
            temperature=0,
            api_key=api_key
        )
        
        response = llm.invoke(prompt_text)
        content = response.content.strip()
        
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
        if json_match:
            try:
                result = json.loads(json_match.group())
                mode = result.get("mode", "").lower()
                if mode in ["server", "web"]:
                    return mode
            except json.JSONDecodeError:
                pass
        
        # 2차 LLM 재시도 (더 강한 프롬프트, 자연어 표현 포함)
        retry_prompt = f"""사용자의 검색 모드 선택을 정확히 분석하세요.

사용자 입력: "{user_input}"

다음 중 하나로 분류:
- server: 내부 데이터, 사내 서버, 벡터DB, 내부 데이터베이스 관련 표현
  예: "내부 데이터에서 찾아볼래", "사내 서버에서 검색", "내부 DB에서", "벡터DB에서 찾아봐"
- web: 인터넷, 웹, KIPRIS, 온라인 검색 관련 표현
  예: "인터넷에서 찾아볼래", "웹에서 검색해줘", "온라인에서 찾아", "KIPRIS에서 검색할래"

JSON 형식으로만 응답: {{"mode": "server" 또는 "web"}}"""
        
        try:
            retry_response = llm.invoke(retry_prompt)
            retry_content = retry_response.content.strip()
            retry_json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', retry_content)
            if retry_json_match:
                try:
                    retry_result = json.loads(retry_json_match.group())
                    retry_mode = retry_result.get("mode", "").lower()
                    if retry_mode in ["server", "web"]:
                        return retry_mode
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            logging.exception(f"LLM 재시도 실패: {e}")
        
        # LLM 실패 시 None 반환 (하드코딩 fallback 제거)
        logging.warning(f"검색 모드 파싱 실패: user_input='{user_input}'")
        return None
        
    except Exception as e:
        logging.exception(f"LLM 검색 모드 파싱 오류: {e}")
        return None


def _parse_search_mode_keywords_fallback(user_input: str) -> Optional[Literal["server", "web"]]:
    """검색 모드 fallback (하드코딩 최소화, 이 함수는 더 이상 사용되지 않음)."""
    # 이 함수는 더 이상 사용되지 않지만 하위 호환성을 위해 유지
    # 최소한의 명확한 입력만 체크
    t = user_input.strip().lower()
    if t == "1" or t.startswith("1번") or t.startswith("1 "):
        return "server"
    if t == "2" or t.startswith("2번") or t.startswith("2 "):
        return "web"
    return None


def generate_web_search_question(keyword: str) -> str:
    """LLM 기반 웹 검색 질문 생성 (서버 검색 결과 없을 때).
    
    Args:
        keyword: 사용자 검색 키워드
        
    Returns:
        자연스러운 웹 검색 제안 질문
    """
    try:
        from langchain_openai import ChatOpenAI
        
        api_key = _get_env("OPENAI_API_KEY")
        if not api_key:
            # LLM 실패 시 기본 질문 반환
            return f"서버에 '{keyword}' 관련 특허 자료가 저장되어 있지 않습니다. 웹에서 찾아드릴까요?"
        
        patent_config = _load_patent_prompts()
        prompt_template = patent_config.get("web_search_question_prompt", "")
        
        if not prompt_template:
            # YAML에 프롬프트가 없으면 기본 질문 반환
            return f"서버에 '{keyword}' 관련 특허 자료가 저장되어 있지 않습니다. 웹에서 찾아드릴까요?"
        
        # 프롬프트에 키워드 치환
        prompt = prompt_template.replace("{keyword}", keyword)
        
        llm = ChatOpenAI(
            model=OPENAI_MODEL,
            temperature=0.7,  # 자연스러운 질문을 위해 약간 높은 temperature
            api_key=api_key
        )
        
        response = llm.invoke(prompt)
        question = response.content.strip()
        
        # 질문이 너무 길면 기본 질문으로 fallback
        if len(question) > 200:
            return f"서버에 '{keyword}' 관련 특허 자료가 저장되어 있지 않습니다. 웹에서 찾아드릴까요?"
        
        return question
        
    except Exception as e:
        # 오류 발생 시 기본 질문 반환
        return f"서버에 '{keyword}' 관련 특허 자료가 저장되어 있지 않습니다. 웹에서 찾아드릴까요?"


def parse_search_mode_from_input(user_input: str) -> Optional[Literal["server", "web"]]:
    """사용자 입력에서 검색 모드 파싱 (하위 호환성, 하드코딩 최소화)."""
    # 명확한 숫자 입력만 체크 (하드코딩 최소화)
    t = user_input.strip().lower()
    
    if t == "1" or t.startswith("1번") or t.startswith("1 "):
        return "server"
    if t == "2" or t.startswith("2번") or t.startswith("2 "):
        return "web"
    
    # 나머지는 LLM으로 처리해야 하므로 None 반환
    # 호출하는 쪽에서 parse_search_mode_with_llm()을 사용해야 함
    return None


async def parse_yes_no_with_llm(
    user_input: str,
    context: str = "추가 작업을 진행할까요?"
) -> Optional[bool]:
    """LLM 기반 예/아니오 파싱 (하드코딩 최소화)."""
    api_key = _get_env("OPENAI_API_KEY")
    if not api_key:
        # API 키가 없으면 숫자만 체크 (하드코딩 최소화)
        t = user_input.strip().lower()
        if t in ["1", "네", "예", "yes", "y"]:
            return True
        if t in ["0", "아니오", "아니", "no", "n"]:
            return False
        return None
    
    try:
        from langchain_openai import ChatOpenAI
        
        patent_config = _load_patent_prompts()
        classification_criteria = patent_config.get("yes_no_classification_criteria", "")
        
        # 1차 LLM 시도
        prompt_parts = []
        prompt_parts.append("다음 사용자 답변을 분석하여 예/아니오로 분류하세요.\n")
        
        if classification_criteria:
            prompt_parts.append(classification_criteria)
        else:
            prompt_parts.append("""
            - yes (예): 긍정적인 답변, 동의, 요청 표현 ("네", "예", "좋아", "해줘", "찾아줄래", "검색해줘" 등)
            - no (아니오): 부정적인 답변, 거절 표현 ("아니오", "아니", "안해", "필요없어" 등)
            """)
        
        prompt_parts.append(f"\n질문: {context}")
        prompt_parts.append(f"사용자 답변: \"{user_input}\"")
        prompt_parts.append('\nJSON 형식으로만 응답: {"answer": true 또는 false}')
        
        prompt_text = "\n".join(prompt_parts)
        
        llm = ChatOpenAI(
            model=OPENAI_MODEL,
            temperature=0,
            api_key=api_key
        )
        
        response = llm.invoke(prompt_text)
        content = response.content.strip()
        
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
        if json_match:
            try:
                result = json.loads(json_match.group())
                answer = result.get("answer")
                if isinstance(answer, bool):
                    return answer
            except json.JSONDecodeError:
                pass
        
        # 2차 LLM 재시도 (더 강한 프롬프트)
        retry_prompt = f"""사용자의 답변을 분석하여 예/아니오로 분류하세요.

질문: {context}
사용자 답변: "{user_input}"

다음 중 하나로 분류하세요:
- 긍정/동의/요청 표현 → true
- 부정/거절 표현 → false

JSON 형식으로만 응답: {{"answer": true 또는 false}}"""
        
        try:
            retry_response = llm.invoke(retry_prompt)
            retry_content = retry_response.content.strip()
            retry_json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', retry_content)
            if retry_json_match:
                try:
                    retry_result = json.loads(retry_json_match.group())
                    retry_answer = retry_result.get("answer")
                    if isinstance(retry_answer, bool):
                        return retry_answer
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        
        # 최후의 수단: 명확한 숫자/단어만 체크 (하드코딩 최소화)
        t = user_input.strip().lower()
        if t in ["1", "네", "예", "yes", "y", "좋아", "해줘", "찾아줄래", "찾아줘", "검색해줘"]:
            return True
        if t in ["0", "아니오", "아니", "no", "n", "안해", "필요없어"]:
            return False
        
        return None
        
    except Exception:
        # 예외 발생 시 명확한 숫자/단어만 체크
        t = user_input.strip().lower()
        if t in ["1", "네", "예", "yes", "y"]:
            return True
        if t in ["0", "아니오", "아니", "no", "n"]:
            return False
        return None


def _parse_yes_no_keywords_fallback(user_input: str) -> Optional[bool]:
    """예/아니오 fallback (하드코딩 최소화, 명확한 입력만 체크)."""
    # 이 함수는 더 이상 사용되지 않지만 하위 호환성을 위해 유지
    # 최소한의 명확한 입력만 체크
    t = user_input.strip().lower()
    if t in ["1", "네", "예", "yes", "y"]:
        return True
    if t in ["0", "아니오", "아니", "no", "n"]:
        return False
    return None
