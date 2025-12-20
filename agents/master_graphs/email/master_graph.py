"""이메일 마스터그래프 - 이메일 검색/작성/분석 (AI-021~AI-027)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio
import re
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

from langgraph.graph import StateGraph, END

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.email.email_search_agent import email_search, _extract_recipient_info_with_llm
from agents.master_graphs.email.email_compose_agent import compose_and_send_email
from agents.structured_output import extract_structured_output, EmailSearchParams, AgentResponse

# Email search param extraction prompt (YAML) cache
_EMAIL_SEARCH_PARAMS_YAML_CACHE: Optional[Dict[str, Any]] = None


def _load_email_search_params_yaml() -> Dict[str, Any]:
    """Load YAML config for email search param extraction (cached)."""
    global _EMAIL_SEARCH_PARAMS_YAML_CACHE
    if _EMAIL_SEARCH_PARAMS_YAML_CACHE is not None:
        return _EMAIL_SEARCH_PARAMS_YAML_CACHE

    try:
        import yaml  # type: ignore
    except Exception:
        _EMAIL_SEARCH_PARAMS_YAML_CACHE = {}
        return _EMAIL_SEARCH_PARAMS_YAML_CACHE

    try:
        yaml_path = Path(__file__).parent / "search_params.yaml"
        if not yaml_path.exists():
            _EMAIL_SEARCH_PARAMS_YAML_CACHE = {}
            return _EMAIL_SEARCH_PARAMS_YAML_CACHE
        with open(yaml_path, "r", encoding="utf-8") as f:
            _EMAIL_SEARCH_PARAMS_YAML_CACHE = yaml.safe_load(f) or {}
        return _EMAIL_SEARCH_PARAMS_YAML_CACHE
    except Exception:
        _EMAIL_SEARCH_PARAMS_YAML_CACHE = {}
        return _EMAIL_SEARCH_PARAMS_YAML_CACHE


def _token_replace(s: str, mapping: Dict[str, str]) -> str:
    """Replace token placeholders like __CURRENT_DATE_ISO__ without interfering with braces."""
    out = s
    for k, v in mapping.items():
        out = out.replace(k, v)
    return out


def _build_email_search_params_system_prompt(
    *,
    context: str,
    current_date: str,
    current_date_iso: str,
    current_weekday: str,
    current_datetime: str,
    yesterday_iso: str,
    last_week_start_iso: str,
    last_week_end_iso: str,
) -> str:
    cfg = _load_email_search_params_yaml()
    template = (cfg.get("system_prompt_template") or "").strip()

    # Fallback to existing minimal prompt if YAML is missing
    if not template:
        return f"""사용자의 이메일 검색 요청을 분석하여 다음 정보를 추출하세요:
- keywords: 검색할 핵심 키워드 리스트 (불용어 제거)
- from_address: 특정 발신자 주소 (있는 경우)
- date_range: 날짜 범위 (시작일, 종료일이 있는 경우)

현재 날짜: {current_date} ({current_date_iso})
현재 요일: {current_weekday}
현재 날짜/시간 (ISO): {current_datetime}

{context}"""

    mapping = {
        "__CURRENT_DATE__": current_date,
        "__CURRENT_DATE_ISO__": current_date_iso,
        "__CURRENT_WEEKDAY__": current_weekday,
        "__CURRENT_DATETIME_ISO__": current_datetime,
        "__YESTERDAY_ISO__": yesterday_iso,
        "__LAST_WEEK_START_ISO__": last_week_start_iso,
        "__LAST_WEEK_END_ISO__": last_week_end_iso,
    }
    prompt = _token_replace(template, mapping)

    examples = cfg.get("few_shot_examples") or []
    if isinstance(examples, list) and examples:
        lines: List[str] = []
        lines.append("\n\n[예시]\n")
        for ex in examples:
            if not isinstance(ex, dict):
                continue
            inp = str(ex.get("input") or "").strip()
            out = ex.get("output")
            if not inp or not out:
                continue
            # Render output in a predictable, copyable format
            if isinstance(out, dict):
                # keep order-ish: keywords/from_address/date_range
                keywords = out.get("keywords")
                from_address = out.get("from_address")
                date_range = out.get("date_range")
                rendered = (
                    "keywords: " + str(keywords) + "\n"
                    "from_address: " + str(from_address) + "\n"
                    "date_range: " + str(date_range)
                )
            else:
                rendered = str(out)

            rendered = _token_replace(rendered, mapping)
            lines.append(f'- 입력: "{inp}"')
            lines.append("  기대 출력:")
            lines.append(rendered)
            lines.append("")

        prompt = prompt + "\n" + "\n".join(lines).strip()

    # Attach conversation context at the end (keeps rules/examples stable)
    if context:
        prompt = prompt + "\n\n" + context
    return prompt


def _filter_email_search_keywords(keywords: List[str], stopwords: List[str]) -> List[str]:
    """Server-side safety net to remove stopwords / very short tokens from extracted keywords."""
    sw = {s.strip().lower() for s in stopwords if isinstance(s, str) and s.strip()}
    cleaned: List[str] = []
    seen = set()
    for k in keywords or []:
        if not isinstance(k, str):
            continue
        token = k.strip()
        if not token:
            continue
        token_l = token.lower()
        # drop exact stopwords
        if token_l in sw:
            continue
        # drop 1-char korean tokens and very short noise
        # keep email-like / alnum keywords
        if len(token) < 2 and not re.search(r"[A-Za-z0-9]", token):
            continue
        if token_l in seen:
            continue
        seen.add(token_l)
        cleaned.append(token)
    return cleaned

# Email 전용 로거 설정 (email_compose_agent와 동일한 로그 파일 사용)
# email_compose_agent에서 이미 로거를 설정했으므로, 여기서는 같은 로거를 가져와서 사용
def _setup_email_logger() -> logging.Logger:
    """이메일 관련 로그를 파일에 저장하는 로거 설정."""
    # email_compose_agent에서 설정한 같은 로거 사용
    logger = logging.getLogger("email_compose")
    
    # 로거가 이미 설정되어 있으면 그대로 사용
    # 없으면 기본 설정 (하지만 email_compose_agent가 먼저 로드되므로 보통 이미 설정되어 있음)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # logs 디렉토리 생성
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent.parent.parent
        log_dir = project_root / "logs"
        log_dir.mkdir(exist_ok=True)
        
        # 로그 파일 경로 (날짜별로 생성)
        log_file = log_dir / f"email_compose_{datetime.now().strftime('%Y%m%d')}.log"
        
        # 파일 핸들러 생성
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8", mode='a')
            file_handler.setLevel(logging.INFO)
            
            # 포맷 설정: 시간 | 레벨 | 메시지
            formatter = logging.Formatter(
                '%(asctime)s | %(levelname)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(formatter)
            
            logger.addHandler(file_handler)
            logger.propagate = False  # 상위 로거로 전파하지 않음
        except Exception as e:
            import sys
            print(f"⚠️ Email 로거 설정 실패: {e}", file=sys.stderr)
    
    return logger

logger = _setup_email_logger()


class EmailMasterGraph(BaseMasterGraph):
    """이메일 도메인 마스터그래프.
    
    요구사항:
    - AI-021: 이메일 라우팅
    - AI-022: 이메일 검색
    - AI-023: 답변 생성 (RAG)
    - AI-024: 에이전트 추천
    - AI-025: 수신 감지
    - AI-026: 답장 초안 생성
    - AI-027: 자동 발송
    """
    
    def get_domain_name(self) -> str:
        return "email"
    
    def get_graph(self) -> StateGraph:
        """이메일 마스터그래프 생성."""
        workflow = StateGraph(OrchestratorState)
        
        # 노드 추가
        workflow.add_node("route_intent", self.route_intent)
        workflow.add_node("search_email", self.search_email)
        workflow.add_node("generate_reply", self.generate_reply)
        workflow.add_node("detect_reception", self.detect_reception)
        
        # 엣지 설정
        workflow.set_entry_point("route_intent")
        
        # 조건부 라우팅
        workflow.add_conditional_edges(
            "route_intent",
            self.route_to_subagent,
            {
                "search": "search_email",
                "reply": "generate_reply",
                "compose": "generate_reply",  # 새 메일 작성도 generate_reply로 처리
                "detect": "detect_reception",
            }
        )
        
        workflow.add_edge("search_email", END)
        workflow.add_edge("generate_reply", END)  # compose_and_send_email 내부에서 HITL 처리
        workflow.add_edge("detect_reception", END)
        
        return workflow.compile()
    
    async def route_intent(self, state: OrchestratorState) -> Dict[str, Any]:
        """이메일 도메인 내부 의도 분류."""
        print("--- [Email Master] Routing Intent ---")
        step_start = time.time()
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # llm_router를 사용한 LLM 기반 분류
        intent = await asyncio.to_thread(
            classify_intent_with_llm,
            query=query,
            route_type="email",
            fallback_route="search",
            session_id=session_id
        )
        
        # llm_router는 "search" 또는 "auto_reply"를 반환하지만,
        # 우리 그래프는 "search", "reply", "compose", "detect"를 사용
        if intent == "auto_reply":
            intent = "reply"
        elif intent not in ["search", "reply", "compose", "detect"]:
            # 키워드 기반 fallback
            if any(k in query for k in ["찾아", "검색", "search", "find"]):
                intent = "search"
            elif any(k in query for k in ["답장", "응답", "자동응답", "reply"]):
                intent = "reply"
            elif any(k in query for k in ["작성", "보내", "전송", "메일 보내", "이메일 작성", "메일 작성", "compose", "send"]):
                intent = "compose"
            elif any(k in query for k in ["수신", "새 메일", "확인"]):
                intent = "detect"
            else:
                intent = "search"  # 기본값
        
        step_elapsed = (time.time() - step_start) * 1000
        print(f"✅ Email 의도 결정: {intent} ({step_elapsed/1000:.2f}초)")
        return {"intent": intent}
    
    def route_to_subagent(self, state: OrchestratorState) -> str:
        """route_intent 결과에 따라 하위 에이전트로 라우팅."""
        intent = state.get("intent", "search")
        # "compose"는 "reply"와 동일하게 처리 (generate_reply 노드에서 구분)
        if intent == "compose":
            return "reply"
        return intent
    
    async def search_email(self, state: OrchestratorState) -> Dict[str, Any]:
        """이메일 검색 (AI-022).
        
        Structured Output Parser를 사용하여 사용자 쿼리에서 
        검색 파라미터를 구조적으로 추출합니다.
        """
        print("--- [Email Master] Searching Email ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        conversation_history = state.get("conversation_history", [])
        email_search_context = state.get("email_search_context", {})
        
        # 이전 요청 확인 질문인지 감지 ("요청한적있어?", "요청한 적 있어?", "요청했어?" 등)
        is_history_check = any(k in query for k in [
            "요청한적", "요청한 적", "요청했어", "요청했었어", "요청한적있어", "요청한 적 있어",
            "한적", "한 적", "했어?", "했었어?", "했나?", "했었나?",
            "찾아달라고 요청", "검색 요청한적", "검색한적", "검색한 적", "검색했어",
            "찾아달라는 요청", "요청했던", "요청했었던"
        ])
        
        if is_history_check:
            print(f"✅ 이전 요청 확인 질문 감지: '{query}' - 대화 히스토리 확인")
            
            # 대화 히스토리에서 이전 이메일 검색 요청 확인
            found_previous_request = False
            search_keywords = []
            
            # 1. email_search_context에서 이전 검색 키워드 확인
            if email_search_context and email_search_context.get("last_keyword"):
                found_previous_request = True
                search_keywords.append(email_search_context.get("last_keyword"))
            
            # 2. conversation_history에서 이전 검색 요청 확인
            if conversation_history:
                for msg in conversation_history:
                    content = msg.get("content", "")
                    role = msg.get("role", "")
                    
                    # 사용자 메시지에서 이메일 검색 요청 찾기
                    if role == "user" and any(k in content for k in ["이메일", "메일", "메일함", "이메일에서", "메일에서"]):
                        if any(k in content for k in ["찾아", "검색", "찾아줘", "검색해줘", "찾아달라", "검색해달라"]):
                            found_previous_request = True
                            # 키워드 추출 시도
                            import re as regex_module
                            # 간단한 키워드 추출 (예: "육아휴직 관련" -> "육아휴직")
                            keyword_match = regex_module.search(r'([가-힣a-zA-Z]+)\s*(?:관련|관해서|에 대해|에 대한)', content)
                            if keyword_match:
                                search_keywords.append(keyword_match.group(1))
            
            # 대화식 답변 생성
            if found_previous_request:
                if search_keywords:
                    keywords_str = ", ".join(set(search_keywords))
                    response = f"네, 이전에 {keywords_str} 관련 이메일을 검색해드린 적이 있습니다."
                else:
                    response = "네, 이전에 이메일 검색을 요청해주신 적이 있습니다."
            else:
                response = "아니요, 이전에 이메일 검색 요청은 없었습니다. 검색을 원하시면 말씀해주세요."
            
            return {
                "final_response": response,
                "messages": [response],
            }

        else:
            # 대화 히스토리 컨텍스트 구성
            context = ""
            if conversation_history:
                recent_history = conversation_history[-3:]  # 최근 3개 메시지
                context = "\n\n이전 대화 내용:\n"
                for msg in recent_history:
                    context += f"{msg.get('role', 'unknown')}: {msg.get('content', '')}\n"
            
            # Structured Output Parser를 사용하여 검색 파라미터 추출
            try:
                # 현재 날짜 정보 가져오기 (orchestrator가 제공하거나 직접 계산)
                KST = timezone(timedelta(hours=9))

                current_date = state.get('current_date', '')
                current_datetime = state.get('current_datetime', '')
                current_weekday = state.get('current_weekday', '')

                # 날짜 정보가 없거나 비어있으면 직접 계산 (KST 기준)
                if not current_date or not current_datetime:
                    now_dt = datetime.now(KST)
                    weekday_names = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
                    current_date = now_dt.strftime("%Y년 %m월 %d일")
                    current_datetime = now_dt.isoformat()
                    current_weekday = weekday_names[now_dt.weekday()]

                # 현재 날짜를 YYYY-MM-DD 형식으로도 제공 (예시 계산용)
                now_dt = datetime.now(KST)
                current_date_iso = now_dt.strftime("%Y-%m-%d")
                current_year = now_dt.year
                current_month = now_dt.month
                current_day = now_dt.day

                # YAML 기반 프롬프트(캐시) 사용: 예시/불용어 규칙은 search_params.yaml에서 관리
                yesterday_iso = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
                # 지난주(월~일) 계산
                last_week_start = (now_dt - timedelta(days=now_dt.weekday() + 7)).strftime("%Y-%m-%d")
                last_week_end = (now_dt - timedelta(days=now_dt.weekday() + 1)).strftime("%Y-%m-%d")

                system_prompt = _build_email_search_params_system_prompt(
                    context=context,
                    current_date=current_date,
                    current_date_iso=current_date_iso,
                    current_weekday=current_weekday,
                    current_datetime=current_datetime,
                    yesterday_iso=yesterday_iso,
                    last_week_start_iso=last_week_start,
                    last_week_end_iso=last_week_end,
                )
                
                params = await extract_structured_output(
                    query=query,
                    model_class=EmailSearchParams,
                    system_prompt=system_prompt
                )
                
                print(f"✅ 구조화된 검색 파라미터 추출 완료:")
                # 서버 후처리: 불용어 제거(LLM이 실수해도 방어)
                cfg = _load_email_search_params_yaml()
                stopwords = cfg.get("stopwords") or []
                filtered_keywords = _filter_email_search_keywords(
                    keywords=list(params.keywords or []),
                    stopwords=list(stopwords) if isinstance(stopwords, list) else [],
                )
                print(f"   - 키워드(원본): {params.keywords}")
                print(f"   - 키워드(필터): {filtered_keywords}")
                print(f"   - 발신자: {params.from_address}")
                print(f"   - 날짜 범위: {params.date_range}")
                
                # 추출된 파라미터로 검색
                # - email_search는 마스터가 준 파라미터(from/date/keywords)만 사용해 검색합니다.
                # - keywords가 비어있으면 user_text는 빈 문자열로 전달(원문 query를 SEARCH에 섞지 않음: 한글 ASCII 인코딩 오류 방지)
                search_query = " ".join(filtered_keywords) if filtered_keywords else ""
                
                # 기존 email_search_agent 사용 (Redis 숏텀 메모리 state 전달)
                # email_search는 이제 비동기 함수이므로 await 사용
                # master_graph에서 추출한 정보 전달
                result = await email_search(
                    search_query,
                    session_id=session_id,
                    state=state,
                    from_address=params.from_address,  # 추출한 발신자 주소 전달
                    date_range=params.date_range,  # 추출한 날짜 범위 전달
                    keywords=filtered_keywords,  # 추출한 키워드 리스트 전달 (불용어 필터 적용)
                )
                
                # Structured Output을 사용한 응답 생성
                response = AgentResponse(
                    success=True,
                    message=result.get("message", "이메일 검색 결과를 찾을 수 없습니다."),
                    data={
                        "keywords": filtered_keywords,
                        "from_address": params.from_address,
                        "date_range": params.date_range,
                        "search_results": result.get("rows", [])
                    },
                    next_action="더 자세한 정보가 필요하신가요?"
                )
                
                # email_search에서 업데이트된 state를 반영하여 Redis에 저장
                # email_search 내부에서 state.update()를 통해 email_search_context가 업데이트됨
                # 업데이트된 state를 반환하여 Redis에 저장되도록 함
                return {
                    "final_response": response.message,
                    "messages": [response.message],
                    "search_results": result.get("rows", []),
                    # email_search_context는 state에 이미 업데이트되어 있으므로
                    # state를 반환하면 Redis에 저장됨
                    "email_search_context": state.get("email_search_context"),
                    "email_ids": result.get("email_ids", []),  # ID 리스트 추가
                    "last_search_keyword": result.get("last_search_keyword")  # 마지막 검색 키워드 추가
                }
            except Exception as e:
                print(f"⚠️ Structured Output 파싱 실패: {e}, 기본 로직 사용")
                # Fallback: 기존 로직 사용 (Redis 숏텀 메모리 state 전달)
                result = await email_search(query, session_id=session_id, state=state)
                
                return {
                    "final_response": result.get("message", "이메일 검색 결과를 찾을 수 없습니다."),
                    "messages": [result.get("message", "")],
                    "search_results": result.get("rows", []),
                    # email_search_context는 state에 이미 업데이트되어 있으므로
                    # state를 반환하면 Redis에 저장됨
                    "email_search_context": state.get("email_search_context"),
                    "email_ids": result.get("email_ids", []),  # ID 리스트 추가
                    "last_search_keyword": result.get("last_search_keyword")  # 마지막 검색 키워드 추가
                }
    
    async def generate_reply(self, state: OrchestratorState) -> Dict[str, Any]:
        """답장/새 메일 초안 생성 및 발송 워크플로우 (AI-026, AI-027)."""
        print("--- [Email Master] Generating Reply/Compose Email ---")
        query = state.get("query", "")
        session_id = state.get("session_id")
        
        # 사용자 쿼리에서 이메일 주소, 메시지 추출 시도 (LLM 사용)
        # 예: "user@example.com에게 내일 회의 시간 변경 요청 메일 보내줘"
        #     "답장해줘: 내일 가능해요"
        
        # 저장된 초안 컨텍스트 확인 (발송/수정 명령 처리용)
        # NOTE: Redis/세션에서 null(None)로 들어오는 경우가 있어 .get() 사용 전에 정규화 필요
        email_compose_context = state.get("email_compose_context") or {}
        
        # LLM을 사용하여 수신자 정보 추출
        recipient_info = await _extract_recipient_info_with_llm(query)
        to_emails = recipient_info.get("to_emails", [])
        to_email = ", ".join(to_emails) if to_emails else ""
        cc = recipient_info.get("cc") or state.get("cc") or email_compose_context.get("cc")
        bcc = recipient_info.get("bcc") or state.get("bcc") or email_compose_context.get("bcc")
        
        # 답장인지 새 메일인지 확인
        is_reply = any(k in query for k in ["답장", "응답", "reply", "회신"])
        original_subject = None
        original_sender = None
        original_body = None
        
        # 답장인 경우, email_search_context에서 최근 검색한 메일 정보 가져오기
        if is_reply:
            email_search_context = state.get("email_search_context", {})
            items = email_search_context.get("items", [])
            if items:
                # 가장 최근 검색한 메일 정보 사용
                latest_item = items[0]
                original_subject = latest_item.get("title", "")
                original_sender = latest_item.get("from", "")
                # original_body는 필요시 email_search_agent에서 가져와야 함
        stored_draft_subject = email_compose_context.get("email_draft_subject")
        stored_draft_body = email_compose_context.get("email_draft_body")
        stored_to_email = email_compose_context.get("to_email")
        
        # 디버그: 저장된 초안 컨텍스트 확인
        print(f"[EMAIL COMPOSE DEBUG] 저장된 초안 컨텍스트 확인:")
        print(f"  - email_compose_context 존재: {bool(email_compose_context)}")
        print(f"  - stored_draft_subject: {stored_draft_subject[:50] if stored_draft_subject else None}...")
        print(f"  - stored_draft_body 길이: {len(stored_draft_body) if stored_draft_body else 0}")
        print(f"  - stored_to_email: {stored_to_email}")
        print(f"  - 전체 email_compose_context 키: {list(email_compose_context.keys())}")
        
        # 사용자 쿼리에서 승인/거부/피드백 감지
        is_approval_command = any(k in query.lower() for k in ["발송", "보내", "전송", "send", "승인", "ok", "좋아"])
        is_reject_command = any(k in query.lower() for k in ["거부", "안 보내", "취소", "reject", "cancel"])
        is_revision_command = any(k in query.lower() for k in ["수정", "다시", "revision", "추가", "변경"])
        
        # 디버그: 명령 감지
        print(f"[EMAIL COMPOSE DEBUG] 명령 감지:")
        print(f"  - query: {query}")
        print(f"  - is_approval_command: {is_approval_command}")
        print(f"  - is_reject_command: {is_reject_command}")
        print(f"  - is_revision_command: {is_revision_command}")
        
        # 저장된 초안이 있고 "발송" 명령이면 approval_status를 "approved"로 설정
        approval_status = None
        user_feedback = None
        
        if is_approval_command and stored_draft_subject and stored_draft_body:
            approval_status = "approved"
            to_email = stored_to_email or to_email
            print(f"[EMAIL COMPOSE DEBUG] ✅ 발송 명령 감지! approval_status='approved' 설정")
            # 저장된 초안 정보를 사용하여 발송
            print(f"--- [Email Master] Using stored draft for approval: subject={stored_draft_subject[:50]}...")
        elif is_revision_command:
            approval_status = "rejected"
            # 피드백 추출 (저장된 피드백보다 새로운 쿼리에서 추출한 피드백 우선)
            feedback_keywords = ["수정", "다시", "추가", "변경", "더"]
            new_feedback = None
            for keyword in feedback_keywords:
                if keyword in query.lower():
                    idx = query.lower().find(keyword.lower())
                    new_feedback = query[idx + len(keyword):].strip()
                    if new_feedback.startswith(":"):
                        new_feedback = new_feedback[1:].strip()
                    break
            # 새로운 피드백이 있으면 사용, 없으면 이전 피드백 유지
            if new_feedback:
                user_feedback = new_feedback
                print(f"[EMAIL COMPOSE DEBUG] 새로운 피드백 추출: {user_feedback}")
            elif not user_feedback:
                # 피드백이 없으면 저장된 초안이 있다는 것을 알림
                user_feedback = email_compose_context.get("user_feedback")
        elif is_reject_command:
            approval_status = "rejected"
        
        # 디버그: 최종 설정
        print(f"[EMAIL COMPOSE DEBUG] 최종 설정:")
        print(f"  - approval_status: {approval_status}")
        print(f"  - to_email: {to_email}")
        print(f"  - user_feedback: {user_feedback}")
        
        # 사용자 메시지 추출 (이메일 주소 제외)
        user_message = query
        if to_email:
            user_message = user_message.replace(to_email, "").strip()
        # "에게", "에게 보내", "보내줘" 등 제거
        user_message = re.sub(r'\s*(에게|에게\s+보내|보내줘|보내|전송|작성|작성해줘)\s*', '', user_message).strip()
        
        # email_compose_agent의 compose_and_send_email 호출 (LangGraph 사용)
        try:
            final_to_email = to_email or stored_to_email or state.get("to_email", "")
            final_user_message = user_message or email_compose_context.get("user_message") or query
            
            # 디버그: compose_and_send_email 호출 파라미터
            print(f"[EMAIL COMPOSE DEBUG] compose_and_send_email 호출 파라미터:")
            print(f"  - to_email: {final_to_email}")
            print(f"  - approval_status: {approval_status}")
            print(f"  - email_draft_subject 전달: {stored_draft_subject[:50] if (stored_draft_subject if (is_approval_command or is_revision_command) else None) else None}...")
            print(f"  - email_draft_body 전달: {'있음' if (stored_draft_body if (is_approval_command or is_revision_command) else None) else '없음'}")
            
            result = await compose_and_send_email(
                to_email=final_to_email,
                user_message=final_user_message,
                original_subject=original_subject or email_compose_context.get("original_subject") or state.get("original_subject"),
                original_sender=original_sender or email_compose_context.get("original_sender") or state.get("original_sender"),
                original_body=original_body or email_compose_context.get("original_body") or state.get("original_body"),
                cc=cc,
                bcc=bcc,
                session_id=session_id,
                approval_status=approval_status,
                user_feedback=user_feedback if user_feedback is not None else email_compose_context.get("user_feedback"),
                email_draft_subject=stored_draft_subject if (is_approval_command or is_revision_command) else None,  # 저장된 초안 전달 (발송/수정 명령 시)
                email_draft_body=stored_draft_body if (is_approval_command or is_revision_command) else None,  # 저장된 초안 전달 (발송/수정 명령 시)
            )
            
            # 디버그: compose_and_send_email 결과
            print(f"[EMAIL COMPOSE DEBUG] compose_and_send_email 결과:")
            print(f"  - ok: {result.get('ok')}")
            print(f"  - message: {result.get('message', '')[:100]}...")
            print(f"  - draft 존재: {bool(result.get('draft'))}")
            
            # 발송 완료되면 email_compose_context 초기화
            if result.get("ok") and is_approval_command:
                print(f"[EMAIL COMPOSE DEBUG] ✅ 발송 완료, 컨텍스트 초기화")
                return {
                    "final_response": result.get("message", "이메일 처리가 완료되었습니다."),
                    "messages": [result.get("message", "")],
                    "email_draft": result.get("draft"),
                    "email_compose_context": {},  # 컨텍스트 초기화
                }
            elif result.get("ok"):
                # 초안 생성 완료 시 컨텍스트에 저장
                compose_context = {
                    "to_email": to_email or stored_to_email or state.get("to_email", ""),
                    "user_message": user_message or email_compose_context.get("user_message") or query,
                    "email_draft_subject": result.get("draft", {}).get("subject") if result.get("draft") else None,
                    "email_draft_body": result.get("draft", {}).get("body") if result.get("draft") else None,
                    "original_subject": original_subject or email_compose_context.get("original_subject"),
                    "original_sender": original_sender or email_compose_context.get("original_sender"),
                    "original_body": original_body or email_compose_context.get("original_body"),
                    "user_feedback": user_feedback,
                    "cc": state.get("cc") or email_compose_context.get("cc"),
                    "bcc": state.get("bcc") or email_compose_context.get("bcc"),
                }
                
                # 디버그: 초안 생성 완료, 컨텍스트 저장
                print(f"[EMAIL COMPOSE DEBUG] ✅ 초안 생성 완료, 컨텍스트 저장:")
                print(f"  - email_draft_subject: {compose_context.get('email_draft_subject')[:50] if compose_context.get('email_draft_subject') else None}...")
                print(f"  - email_draft_body 길이: {len(compose_context.get('email_draft_body', ''))}")
                print(f"  - to_email: {compose_context.get('to_email')}")
                print(f"  - 전체 compose_context 키: {list(compose_context.keys())}")
                
                # state에도 직접 저장 (LangGraph가 state 업데이트를 인식하도록)
                return {
                    "final_response": result.get("message", "이메일 처리가 완료되었습니다."),
                    "messages": [result.get("message", "")],
                    "email_draft": result.get("draft"),
                    "email_compose_context": compose_context,  # 컨텍스트 저장
                }
            else:
                # ok가 False여도 draft가 있으면 저장해야 함 (pending/rejected 상태)
                if result.get("draft"):
                    compose_context = {
                        "to_email": to_email or stored_to_email or state.get("to_email", ""),
                        "user_message": user_message or email_compose_context.get("user_message") or query,
                        "email_draft_subject": result.get("draft", {}).get("subject") if result.get("draft") else None,
                        "email_draft_body": result.get("draft", {}).get("body") if result.get("draft") else None,
                        "original_subject": original_subject or email_compose_context.get("original_subject"),
                        "original_sender": original_sender or email_compose_context.get("original_sender"),
                        "original_body": original_body or email_compose_context.get("original_body"),
                        "user_feedback": user_feedback,
                        "cc": state.get("cc") or email_compose_context.get("cc"),
                        "bcc": state.get("bcc") or email_compose_context.get("bcc"),
                    }
                    
                    print(f"[EMAIL COMPOSE DEBUG] ✅ 초안 저장 (ok=False, pending/rejected 상태):")
                    print(f"  - email_draft_subject: {compose_context.get('email_draft_subject')[:50] if compose_context.get('email_draft_subject') else None}...")
                    print(f"  - email_draft_body 길이: {len(compose_context.get('email_draft_body', ''))}")
                    
                    return {
                        "final_response": result.get("message", result.get("error", "이메일 처리 중 오류가 발생했습니다.")),
                        "messages": [result.get("message", result.get("error", ""))],
                        "email_compose_context": compose_context,  # 컨텍스트 저장
                    }
                else:
                    # draft가 없으면 오류로 처리
                    return {
                        "final_response": result.get("message", result.get("error", "이메일 처리 중 오류가 발생했습니다.")),
                        "messages": [result.get("message", result.get("error", ""))],
                    }
        except Exception as e:
            print(f"[EMAIL COMPOSE DEBUG] ❌ 이메일 작성/발송 실패: {e}")
            import traceback
            traceback.print_exc()
        return {
                "final_response": f"이메일 처리 중 오류가 발생했습니다: {str(e)}",
                "messages": [f"오류: {str(e)}"],
        }
    
    async def detect_reception(self, state: OrchestratorState) -> Dict[str, Any]:
        """수신 감지 (AI-025)."""
        print("--- [Email Master] Detecting Reception ---")
        
        # 수신 감지 로직 (실제 구현 필요)
        # 여기서는 메시지만 반환
        return {
            "final_response": "새로운 이메일이 없습니다.",
            "messages": ["이메일 수신 확인이 완료되었습니다."],
        }


def get_email_master_graph() -> StateGraph:
    """이메일 마스터그래프 인스턴스 반환."""
    master = EmailMasterGraph()
    return master.get_graph()

