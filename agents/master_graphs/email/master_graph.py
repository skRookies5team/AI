"""이메일 마스터그래프 - 이메일 검색/작성/분석 (AI-021~AI-027)."""

from __future__ import annotations

from typing import Any, Dict
import asyncio
import json
import os
import re
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from agents.state import OrchestratorState
from agents.llm_router import classify_intent_with_llm
from agents.master_graphs.base import BaseMasterGraph
from agents.master_graphs.email.email_search_agent import email_search, _extract_recipient_info_with_llm
from agents.master_graphs.email.email_compose_agent import compose_and_send_email
from agents.master_graphs.email.workflow_email_composer import generate_workflow_email_draft
from agents.structured_output import extract_structured_output, EmailSearchParams, AgentResponse

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

def _strip_html_for_summary(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fallback_two_lines(text: str) -> tuple[str, str]:
    cleaned = _strip_html_for_summary(text)
    if not cleaned:
        return "(요약 없음)", ""
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        one = re.sub(r"\s+", " ", cleaned).strip()
        return (one[:90] if one else "(요약 없음)"), ""
    if len(lines) == 1:
        return lines[0][:90], ""
    return lines[0][:90], lines[1][:90]


async def _summarize_email_search_result(result: Dict[str, Any], max_items: int = 10) -> dict[str, dict[str, str]]:
    """email_search 결과에서 메일별 2줄 요약 생성.

    Returns:
        { "1": {"line1": "...", "line2": "..."}, ... }
    """
    data = result.get("data")
    if not isinstance(data, dict) or not data:
        return {}

    top_key = next(iter(data.keys()))
    email_data = data.get(top_key)
    if not isinstance(email_data, dict) or not email_data:
        return {}

    items: list[dict[str, str]] = []
    for k in sorted(email_data.keys(), key=lambda x: int(x) if str(x).isdigit() else 10**9):
        if len(items) >= max_items:
            break
        info = email_data.get(k) or {}
        subject = str(info.get("subject") or "(제목 없음)").strip()[:120]
        content = str(info.get("content") or "")
        excerpt = _strip_html_for_summary(content)[:2500]
        items.append({"index": str(k), "subject": subject, "excerpt": excerpt})

    if not items:
        return {}

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        by_idx: dict[str, dict[str, str]] = {}
        for it in items:
            l1, l2 = _fallback_two_lines(it.get("excerpt", ""))
            by_idx[it["index"]] = {"line1": l1, "line2": l2}
        return by_idx

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=api_key)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "너는 이메일 요약기다. 각 메일을 2줄로 요약해라.\n"
                "- 한국어\n"
                "- 인사말/맺음말/서명/회사정보/AI 자동 안내 문구는 제외\n"
                "- 각 메일 요약은 정확히 2줄(줄바꿈 포함)\n"
                "- 출력은 반드시 JSON 배열만: "
                '[{{"index":"1","line1":"...","line2":"..."}}]',
            ),
            ("user", "다음 이메일들을 각각 요약해줘:\n\n{items_json}"),
        ]
    )

    chain = prompt | llm
    resp = await chain.ainvoke({"items_json": json.dumps(items, ensure_ascii=False)})

    by_idx: dict[str, dict[str, str]] = {}
    try:
        raw = (resp.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
        arr = json.loads(raw)
        if isinstance(arr, list):
            for row in arr:
                if not isinstance(row, dict):
                    continue
                idx = str(row.get("index", "")).strip()
                l1 = str(row.get("line1", "")).strip()
                l2 = str(row.get("line2", "")).strip()
                if idx:
                    by_idx[idx] = {"line1": l1, "line2": l2}
    except Exception:
        by_idx = {}

    # 누락된 항목은 fallback으로 채움
    for it in items:
        idx = it["index"]
        got = by_idx.get(idx) or {"line1": "", "line2": ""}
        if not got.get("line1"):
            l1, l2 = _fallback_two_lines(it.get("excerpt", ""))
            by_idx[idx] = {"line1": l1, "line2": l2}

    return by_idx

_email_search_params_cache: dict[str, Any] | None = None


def _load_email_search_params_yaml() -> dict[str, Any]:
    """이메일 검색 파라미터 추출용 YAML 로드(캐시)."""
    global _email_search_params_cache
    if _email_search_params_cache is not None:
        return _email_search_params_cache
    yaml_path = Path(__file__).parent / "search_params.yaml"
    if not yaml_path.exists():
        _email_search_params_cache = {}
        return _email_search_params_cache
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            _email_search_params_cache = yaml.safe_load(f) or {}
    except Exception:
        _email_search_params_cache = {}
    return _email_search_params_cache


def _render_email_search_few_shots(examples: list[dict[str, Any]], fmt: dict[str, str]) -> str:
    """YAML few_shot_examples를 문자열로 렌더링."""
    lines: list[str] = []
    for ex in examples or []:
        user_in = str(ex.get("input", "")).strip()
        out = ex.get("output", {}) or {}
        kw = out.get("keywords", [])
        frm = out.get("from_address", None)
        dr = out.get("date_range", None)
        # date_range 안의 {current_date_iso} 같은 템플릿 치환
        dr_str = json.dumps(dr, ensure_ascii=False)
        try:
            dr_str = dr_str.format(**fmt)
        except Exception:
            pass
        lines.append(f'- "{user_in}" -> keywords: {kw}, from_address: {frm}, date_range: {dr_str}')
    return "\n".join(lines).strip()


def _postfilter_keywords(keywords: list[str], stopwords: set[str]) -> list[str]:
    """불용어 제거 + 공백 정리 + 중복 제거(순서 유지)."""
    out: list[str] = []
    seen: set[str] = set()
    for k in keywords or []:
        k2 = str(k).strip()
        if not k2:
            continue
        if k2 in stopwords:
            continue
        if k2 in seen:
            continue
        seen.add(k2)
        out.append(k2)
    return out


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
        
        # ✅ 워크플로우 모드 확인 (최우선 처리)
        workflow_in_progress = state.get("workflow_in_progress", False)
        workflow_type = state.get("workflow_type")
        workflow_temp_data = state.get("workflow_temp_data") or {}
        
        # 워크플로우 모드이고 business_trip 워크플로우인 경우 자동으로 compose로 라우팅
        if workflow_in_progress and workflow_type == "business_trip":
            # workflow_temp_data에 출장 정보가 있으면 compose로 라우팅
            if workflow_temp_data.get("start_time") and workflow_temp_data.get("end_time"):
                print(f"✅ 워크플로우 모드 감지: {workflow_type} → compose로 자동 라우팅")
                step_elapsed = (time.time() - step_start) * 1000
                print(f"✅ Email 의도 결정: compose (워크플로우 모드, {step_elapsed/1000:.2f}초)")
                return {"intent": "compose"}
        
        # ✅ 수신자 정보가 있으면 자동으로 compose로 라우팅
        email_compose_context = state.get("email_compose_context") or {}
        to_emails = email_compose_context.get("to_emails", [])
        to_names = email_compose_context.get("to_names", [])
        pending_selection = email_compose_context.get("pending_recipient_selection", {})

        # ✅ 작성 취소는 generate_reply로 보내서 state 초기화 로직이 실행되도록 함
        cancel_keywords = ["작성 취소", "취소", "중단", "그만", "cancel", "stop"]
        if email_compose_context and any(k in query.lower() for k in cancel_keywords):
            print("✅ 이메일 작성 취소 감지 → compose로 라우팅(상태 초기화 실행)")
            return {"intent": "compose"}
        
        # 수신자 정보가 있거나 동명이인 선택 중이면 compose로 라우팅
        if to_emails or (to_names and not pending_selection) or pending_selection:
            print(f"✅ 수신자 정보 확인: to_emails={to_emails}, to_names={to_names}, pending_selection={bool(pending_selection)} → compose로 라우팅")
            return {"intent": "compose"}
        
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

                # ✅ 이메일 검색 파라미터 추출 프롬프트는 YAML로 관리
                search_yaml = _load_email_search_params_yaml()
                stopwords = set(search_yaml.get("stopwords", []) or [])
                few_shot_examples = search_yaml.get("few_shot_examples", []) or []
                tmpl = search_yaml.get("system_prompt_template") or ""

                yesterday_date = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
                last_week_start = (now_dt - timedelta(days=now_dt.weekday() + 7)).strftime("%Y-%m-%d")
                last_week_end = (now_dt - timedelta(days=now_dt.weekday() + 1)).strftime("%Y-%m-%d")

                fmt = {
                    "current_date": current_date,
                    "current_date_iso": current_date_iso,
                    "current_weekday": current_weekday,
                    "current_datetime": current_datetime,
                    "current_year": str(current_year),
                    "current_month": str(current_month),
                    "current_day": str(current_day),
                    "yesterday_date": yesterday_date,
                    "last_week_start": last_week_start,
                    "last_week_end": last_week_end,
                }

                few_shots_str = _render_email_search_few_shots(few_shot_examples, fmt)

                if tmpl:
                    system_prompt = tmpl.format(
                        **fmt,
                        few_shots=few_shots_str,
                    )
                else:
                    # YAML이 없으면 기존 프롬프트 유지(하위 호환)
                    system_prompt = f"""사용자의 이메일 검색 요청을 분석하여 다음 정보를 추출하세요:
- keywords: 검색할 핵심 키워드 리스트 (불용어 제거)
- from_address: 특정 발신자 주소 (있는 경우)
- date_range: 날짜 범위 (시작일, 종료일이 있는 경우)

**중요: 현재 날짜 정보를 반드시 참조하세요!**
- 현재 날짜: {current_date} ({current_date_iso})
- 현재 요일: {current_weekday}
- 현재 날짜/시간 (ISO): {current_datetime}
- 현재 연도: {current_year}년
- 현재 월: {current_month}월
- 현재 일: {current_day}일

{context}"""
                
                params = await extract_structured_output(
                    query=query,
                    model_class=EmailSearchParams,
                    system_prompt=system_prompt
                )

                # ✅ stopwords 후처리(LLM이 불용어를 넣어도 제거)
                filtered_keywords = _postfilter_keywords(params.keywords or [], stopwords)
                
                print(f"✅ 구조화된 검색 파라미터 추출 완료:")
                print(f"   - 키워드: {filtered_keywords}")
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
                    keywords=filtered_keywords,  # 불용어 제거된 키워드만 전달
                )

                # ✅ 검색 결과를 rows로 제공 + 각 row에 2줄 요약 포함
                rows = result.get("rows", []) or []
                summaries_by_index = await _summarize_email_search_result(result, max_items=10)
                for r in rows:
                    idx = str(r.get("index") or "").strip()
                    summ = summaries_by_index.get(idx) or {}
                    line1 = (summ.get("line1") or "").strip()
                    line2 = (summ.get("line2") or "").strip()
                    if line1 or line2:
                        r["summary_line1"] = line1
                        r["summary_line2"] = line2
                        r["summary"] = (line1 + ("\n" + line2 if line2 else "")).strip()

                message = f"📧 이메일 검색 결과 {len(rows)}개"

                # Structured Output을 사용한 응답 생성
                response = AgentResponse(
                    success=True,
                    message=message,
                    data={
                        "keywords": filtered_keywords,
                        "from_address": params.from_address,
                        "date_range": params.date_range,
                        "search_results": rows
                    },
                    next_action="더 자세한 정보가 필요하신가요?"
                )
                
                # email_search에서 업데이트된 state를 반영하여 Redis에 저장
                # email_search 내부에서 state.update()를 통해 email_search_context가 업데이트됨
                # 업데이트된 state를 반환하여 Redis에 저장되도록 함
                return {
                    "final_response": response.message,
                    "messages": [response.message],
                    "search_results": rows,
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

                rows = result.get("rows", []) or []
                summaries_by_index = await _summarize_email_search_result(result, max_items=10)
                for r in rows:
                    idx = str(r.get("index") or "").strip()
                    summ = summaries_by_index.get(idx) or {}
                    line1 = (summ.get("line1") or "").strip()
                    line2 = (summ.get("line2") or "").strip()
                    if line1 or line2:
                        r["summary_line1"] = line1
                        r["summary_line2"] = line2
                        r["summary"] = (line1 + ("\n" + line2 if line2 else "")).strip()

                message = f"📧 이메일 검색 결과 {len(rows)}개"

                return {
                    "final_response": message,
                    "messages": [message],
                    "search_results": rows,
                    # email_search_context는 state에 이미 업데이트되어 있으므로
                    # state를 반환하면 Redis에 저장됨
                    "email_search_context": state.get("email_search_context"),
                    "email_ids": result.get("email_ids", []),  # ID 리스트 추가
                    "last_search_keyword": result.get("last_search_keyword")  # 마지막 검색 키워드 추가
                }
    
    async def generate_reply(self, state: OrchestratorState) -> Dict[str, Any]:
        """답장/새 메일 초안 생성 및 발송 워크플로우 (AI-026, AI-027).
        
        워크플로우 모드 지원:
        - workflow_temp_data에서 출장 정보 가져오기
        - 부재중 자동 응답 초안 생성
        - 초안 컨펌 요청 (휴먼 인 더 루프)
        """
        print("--- [Email Master] Generating Reply/Compose Email ---")
        query = state.get("query", "")
        session_id = state.get("session_id")

        # ✅ 작성 취소: 이메일 작성 state 초기화
        # - 초안/수신자 선택/피드백 등 email_compose_context가 존재할 때만 초기화
        cancel_keywords = ["작성 취소", "취소", "중단", "그만", "cancel", "stop"]
        is_cancel = any(k in query.lower() for k in cancel_keywords)
        email_compose_context_now = state.get("email_compose_context") or {}
        if is_cancel and email_compose_context_now:
            cancel_msg = "이메일 작성이 취소되었습니다. (이메일 상태를 초기화했습니다)"
            return {
                "final_response": cancel_msg,
                "messages": [cancel_msg],
                "email_compose_context": {},  # ✅ 컨텍스트 초기화
                "requires_input": False,
            }
        
        # 워크플로우 모드 확인
        workflow_in_progress = state.get("workflow_in_progress", False)
        workflow_type = state.get("workflow_type")
        workflow_temp_data = state.get("workflow_temp_data") or {}
        
        # 워크플로우 모드이고 business_trip 워크플로우인 경우
        if workflow_in_progress and workflow_type == "business_trip":
            # 저장된 초안 컨텍스트 확인 (발송/수정 명령 처리용)
            email_compose_context = state.get("email_compose_context") or {}
            stored_draft_subject = email_compose_context.get("email_draft_subject")
            stored_draft_body = email_compose_context.get("email_draft_body")
            stored_to_email = email_compose_context.get("to_email", "")
            
            # "발송" 또는 "수정" 명령 확인
            is_send_command = any(k in query.lower() for k in ["발송", "보내", "전송", "send", "승인", "ok", "좋아"])
            is_revision_command = any(k in query.lower() for k in ["수정", "다시", "revision", "추가", "변경"])
            
            # "발송" 명령이 있고 초안이 있으면 발송 처리
            if is_send_command and stored_draft_subject and stored_draft_body and stored_to_email:
                # ✅ 워크플로우 모드: 팀원들에게 이메일 발송
                try:
                    result = await compose_and_send_email(
                        to_email=stored_to_email,  # 팀원 이메일 주소
                        user_message=query,
                        cc=None,
                        bcc=None,
                        original_subject=None,
                        original_sender=None,
                        original_body=None,
                        approval_status="approved",  # 발송 명령이므로 "approved"로 설정
                        email_draft_subject=stored_draft_subject,
                        email_draft_body=stored_draft_body,
                    )
                    
                    if result.get("ok"):
                        # ✅ 발송 완료 - 워크플로우 완료 처리
                        current_step_index = state.get("current_workflow_step_index", 0)
                        workflow_steps = state.get("workflow_steps", [])
                        
                        # 마지막 단계인지 확인
                        if current_step_index >= len(workflow_steps) - 1:
                            # 워크플로우 완료
                            from agents.orchestrator_v3 import load_workflows_config
                            workflows = load_workflows_config()
                            workflow_config = workflows.get("workflows", {}).get(workflow_type, {})
                            completion_message = workflow_config.get("completion_message", "워크플로우가 완료되었습니다.")
                            
                            # ✅ 이메일 발송 완료 및 워크플로우 종료 안내 메시지 (요청 형식)
                            raw_send_msg = result.get("message", "이메일이 성공적으로 발송되었습니다!")
                            # 예: "이메일이 성공적으로 발송되었습니다! (수신자: ...)" -> 줄바꿈 형태로 정리
                            send_msg = raw_send_msg.replace("! (수신자:", "!\n(수신자:").replace(") ---", ")")
                            if " (수신자:" in send_msg and "\n(수신자:" not in send_msg:
                                send_msg = send_msg.replace(" (수신자:", "\n(수신자:")

                            email_sent_msg = (
                                f"{send_msg}\n\n"
                                f"완료된 작업:\n\n"
                                f"{completion_message}\n\n"
                                f"출장 신청 워크플로우가 종료되었습니다."
                            )
                            
                            return {
                                "final_response": email_sent_msg,
                                "messages": [email_sent_msg],
                                "email_compose_context": {},  # 컨텍스트 초기화
                                "workflow_temp_data": {},  # 초기화
                                "workflow_type": None,
                                "workflow_steps": None,
                                "current_workflow_step_index": None,
                                "workflow_completed_steps": None,
                                "workflow_in_progress": False,
                                "next_domain": None,
                                "requires_input": False,
                            }
                        else:
                            # 다음 단계로 진행 (이미 마지막 단계이므로 여기 도달하지 않음)
                            raw_send_msg = result.get("message", "이메일이 성공적으로 발송되었습니다!")
                            send_msg = raw_send_msg.replace("! (수신자:", "!\n(수신자:").replace(") ---", ")")
                            if " (수신자:" in send_msg and "\n(수신자:" not in send_msg:
                                send_msg = send_msg.replace(" (수신자:", "\n(수신자:")
                            email_sent_msg = send_msg
                            
                            return {
                                "final_response": email_sent_msg,
                                "messages": [email_sent_msg],
                                "email_compose_context": {},  # 컨텍스트 초기화
                                "workflow_temp_data": workflow_temp_data,
                                "requires_input": False,
                            }
                    else:
                        return {
                            "final_response": result.get("message", "이메일 발송에 실패했습니다."),
                            "messages": [result.get("message", "")],
                            "email_compose_context": email_compose_context,
                            "workflow_temp_data": workflow_temp_data,
                            "requires_input": True,
                        }
                except Exception as e:
                    print(f"⚠️ 이메일 발송 실패: {e}")
                    return {
                        "final_response": f"이메일 발송 중 오류가 발생했습니다: {str(e)}",
                        "messages": [f"오류: {str(e)}"],
                        "email_compose_context": email_compose_context,
                        "workflow_temp_data": workflow_temp_data,
                        "requires_input": True,
                    }
            
            # "수정" 명령이면 사용자에게 수정 요청
            if is_revision_command:
                message = "초안을 수정하시겠습니까? 수정할 내용을 알려주세요.\n\n예: '제목을 변경해줘', '본문에 연락처 추가해줘' 등"
                return {
                    "final_response": message,
                    "messages": [message],
                    "email_compose_context": email_compose_context,
                    "workflow_temp_data": workflow_temp_data,
                    "requires_input": True,
                }
            
            # 초안이 이미 있고 명령이 없으면 초안 표시
            if stored_draft_subject and stored_draft_body and stored_to_email:
                message = f"📧 **팀원 알림 이메일 초안**\n\n"
                message += f"**제목:** {stored_draft_subject}\n\n"
                message += f"**수신자:** {stored_to_email}\n\n"
                message += f"**본문:**\n{stored_draft_body}\n\n"
                message += "이 초안을 사용하시겠습니까?\n\n[발송] 또는 [수정]을 선택해주세요."
                
                return {
                    "final_response": message,
                    "messages": [message],
                    "email_compose_context": email_compose_context,
                    "workflow_temp_data": workflow_temp_data,
                    "requires_input": True,
                }
            
            # ✅ 초안 생성 (팀원들에게 출장 알림) - workflow_email_composer 사용
            employee_id = state.get("employee_id")
            
            # user_dept 조회 (없으면 자동 조회)
            user_dept = None
            if employee_id:
                try:
                    from agents.master_graphs.schedule.services.database_service import DatabaseService
                    from sqlalchemy import text
                    db = DatabaseService()
                    async with db._session() as session:
                        sql = "SELECT dept FROM ai_agent.employee WHERE employee_id = :employee_id"
                        result = await session.execute(text(sql), {"employee_id": employee_id})
                        row = result.fetchone()
                        if row:
                            user_dept = row.dept
                except Exception as e:
                    print(f"⚠️ 팀원 정보 조회 실패: {e}")
            
            # 워크플로우 이메일 초안 생성
            draft_result = await generate_workflow_email_draft(
                workflow_temp_data=workflow_temp_data,
                employee_id=employee_id,
                user_dept=user_dept
            )
            
            if draft_result.get("error"):
                return {
                    "final_response": f"❌ {draft_result['error']}",
                    "messages": [draft_result["error"]],
                    "email_compose_context": email_compose_context,
                    "workflow_temp_data": workflow_temp_data,
                    "requires_input": True,
                }
            
            to_email = draft_result.get("to_email", "")
            absence_subject = draft_result.get("subject", "")
            absence_body = draft_result.get("body", "")
            
            # 현재 단계 번호 계산
            current_step_index = state.get("current_workflow_step_index", 0)
            current_step_number = current_step_index + 1
            step_complete_msg = f"✅ [Step {current_step_number} 완료] 팀원 알림 이메일 초안이 생성되었습니다.\n\n"
            
            # 초안 컨펌 요청
            # 🔥 프론트엔드 React 버튼으로 대체 (주석처리 - 필요시 복구 가능)
            # buttons_html = """
# <div style="margin-top: 15px; display: flex; gap: 10px; flex-wrap: wrap;">
#     <button onclick="sendMessageFromButton('발송')" style="padding: 10px 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(102, 126, 234, 0.3);">
#         📧 발송
#     </button>
#     <button onclick="sendMessageFromButton('수정')" style="padding: 10px 20px; background: #e0e0e0; color: #333; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.3s; box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);">
#         ✏️ 수정
#     </button>
# </div>
# """
            message = f"📧 **팀원 알림 이메일 초안**\n\n"
            message += f"**제목:** {absence_subject}\n\n"
            message += f"**수신자:** {to_email}\n\n"
            message += f"**본문:**\n{absence_body}\n\n"
            message += "이 초안을 사용하시겠습니까?"  # + buttons_html (프론트에서 React 버튼으로 표시)
            message = step_complete_msg + message
            
            # email_compose_context에 초안 저장
            email_compose_context["email_draft_subject"] = absence_subject
            email_compose_context["email_draft_body"] = absence_body
            email_compose_context["to_email"] = to_email  # 수신자 정보도 저장
            email_compose_context["approval_status"] = "pending"
            
            return {
                "final_response": message,
                "messages": [message],
                "email_compose_context": email_compose_context,
                "workflow_temp_data": workflow_temp_data,
                "requires_input": True,
            }
        
        # 일반 모드 (기존 로직)
        # 사용자 쿼리에서 이메일 주소, 메시지 추출 시도 (LLM 사용)
        # 예: "user@example.com에게 내일 회의 시간 변경 요청 메일 보내줘"
        #     "답장해줘: 내일 가능해요"
        
        # 저장된 초안 컨텍스트 확인 (발송/수정 명령 처리용)
        # NOTE: Redis/세션에서 null(None)로 들어오는 경우가 있어 .get() 사용 전에 정규화 필요
        email_compose_context = state.get("email_compose_context") or {}
        
        # ✅ 동명이인 선택 처리 (사용자가 "1번", "2번" 입력한 경우)
        pending_selection = email_compose_context.get("pending_recipient_selection", {})
        if pending_selection:
            pick_pattern = re.compile(r'^\s*(\d+)\s*번?\s*$')
            match = pick_pattern.match(query.strip())
            
            if match:
                selection_num = int(match.group(1))
                current_name = email_compose_context.get("current_recipient_name")
                
                if current_name and current_name in pending_selection:
                    candidates = pending_selection[current_name]
                    if 1 <= selection_num <= len(candidates):
                        selected = candidates[selection_num - 1]
                        selected_email = selected["email"]
                        
                        # to_emails에 추가
                        to_emails = email_compose_context.get("to_emails", [])
                        if selected_email not in to_emails:
                            to_emails.append(selected_email)
                        
                        # 현재 이름 처리 완료
                        pending_selection.pop(current_name, None)
                        
                        # 다음 동명이인 처리
                        if pending_selection:
                            # 다음 이름 선택 요청
                            next_name = list(pending_selection.keys())[0]
                            email_compose_context["current_recipient_name"] = next_name
                            email_compose_context["to_emails"] = to_emails
                            email_compose_context["pending_recipient_selection"] = pending_selection
                            
                            candidates = pending_selection[next_name]
                            selection_prompt = f"**{next_name}**님의 이메일을 선택해주세요:\n\n"
                            for idx, candidate in enumerate(candidates, 1):
                                dept = candidate.get("dept", "부서 정보 없음")
                                selection_prompt += f"{idx}. {candidate['email']} ({dept})\n"
                            selection_prompt += "\n번호를 입력해주세요 (예: 1번, 2번)"
                            
                            return {
                                "final_response": selection_prompt,
                                "messages": [selection_prompt],
                                "email_compose_context": email_compose_context,
                                "requires_input": True
                            }
                        else:
                            # 모든 동명이인 처리 완료
                            email_compose_context["current_recipient_name"] = None
                            email_compose_context["to_emails"] = to_emails
                            email_compose_context["pending_recipient_selection"] = {}
                            
                            # 원래 쿼리 복원 (동명이인 선택 완료 후 이메일 작성 시 사용)
                            original_query = email_compose_context.get("original_query", query)
                            query = original_query  # query를 원래 쿼리로 업데이트
                            print(f"[DEBUG] 모든 동명이인 처리 완료: {to_emails}, 원래 쿼리 복원: {original_query}")
                            
                            # 이메일 작성 진행 (아래 로직 계속)
                            to_email = ", ".join(to_emails)
                            # 저장된 이메일을 사용하여 계속 진행
                            
                            # 이제 이메일 작성 로직으로 계속 진행하기 위해
                            # recipient_info를 업데이트하고 나머지 로직 계속
                            recipient_info = {"to_emails": to_emails, "to_names": email_compose_context.get("to_names", []), "cc": None, "bcc": None}
                            to_names = email_compose_context.get("to_names", [])  # 원래 이름들 유지
                            
                            # 나머지 로직을 계속 진행하기 위해 변수 설정
                            # (아래 코드에서 to_emails와 to_email을 사용)
                            # 이제 아래의 LLM 추출 로직을 건너뛰고 바로 이메일 작성으로 진행
                            skip_recipient_extraction = True
                    else:
                        return {
                            "final_response": f"잘못된 번호입니다. 1번부터 {len(candidates)}번까지 선택할 수 있습니다.",
                            "messages": [f"잘못된 번호입니다. 1번부터 {len(candidates)}번까지 선택할 수 있습니다."],
                            "email_compose_context": email_compose_context,
                            "requires_input": True
                        }
        
        # LLM을 사용하여 수신자 정보 추출 (팀 처리 포함)
        employee_id = state.get("employee_id")
        user_dept = None
        print(f"[DEBUG] [Email Master] employee_id: {employee_id}")
        
        if employee_id:
            # 사용자 부서 정보 조회 (우리팀 처리용)
            try:
                from agents.master_graphs.schedule.services.database_service import DatabaseService
                from sqlalchemy import text
                db = DatabaseService()
                async with db._session() as session:
                    sql = "SELECT dept FROM ai_agent.employee WHERE employee_id = :employee_id"
                    result = await session.execute(text(sql), {"employee_id": employee_id})
                    row = result.fetchone()
                    if row:
                        user_dept = row.dept
                        print(f"[DEBUG] [Email Master] user_dept 조회 성공: {user_dept}")
                    else:
                        print(f"[DEBUG] [Email Master] employee_id {employee_id}에 해당하는 부서 정보가 DB에 없습니다.")
            except Exception as e:
                print(f"⚠️ 사용자 부서 정보 조회 실패: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"[DEBUG] [Email Master] employee_id가 state에 없습니다.")
        
        print(f"[DEBUG] [Email Master] 최종 - employee_id: {employee_id}, user_dept: {user_dept}")
        
        # 동명이인 선택 처리 후에는 recipient_info를 다시 추출하지 않음
        if "skip_recipient_extraction" not in locals() or not skip_recipient_extraction:
            recipient_info = await _extract_recipient_info_with_llm(query, employee_id=employee_id, user_dept=user_dept)
            to_emails = recipient_info.get("to_emails", [])
            to_names = recipient_info.get("to_names", [])
        
        # ✅ 이름만 있고 이메일이 없으면 DB에서 조회
        if not to_emails and to_names:
            try:
                from agents.master_graphs.schedule.services.database_service import DatabaseService
                from sqlalchemy import text
                
                db = DatabaseService()
                async with db._session() as session:
                    # 각 이름별로 조회 (동명이인 처리)
                    name_to_candidates = {}
                    
                    for name in to_names:
                        sql = """
                            SELECT email, name, dept, employee_id
                            FROM ai_agent.employee
                            WHERE name = :name
                              AND email IS NOT NULL
                              AND email != ''
                        """
                        result = await session.execute(text(sql), {"name": name})
                        rows = result.fetchall()
                        
                        candidates = [
                            {
                                "email": row.email,
                                "name": row.name,
                                "dept": row.dept,
                                "employee_id": row.employee_id
                            }
                            for row in rows
                        ]
                        
                        name_to_candidates[name] = candidates
                    
                    # 동명이인 처리
                    pending_selection = {}
                    resolved_emails = []
                    unresolved_names = []
                    
                    for name in to_names:
                        candidates = name_to_candidates.get(name, [])
                        
                        if len(candidates) == 0:
                            # 매칭 없음
                            print(f"[DEBUG] '{name}'에 해당하는 이메일을 찾을 수 없습니다.")
                            unresolved_names.append(name)
                        elif len(candidates) == 1:
                            # 단일 매칭 - 바로 사용
                            resolved_emails.append(candidates[0]["email"])
                            print(f"[DEBUG] '{name}' → {candidates[0]['email']} (단일 매칭)")
                        else:
                            # 동명이인 - 사용자 선택 필요
                            print(f"[DEBUG] '{name}'에 {len(candidates)}명의 동명이인이 있습니다.")
                            pending_selection[name] = candidates
                    
                    # 동명이인이 있으면 사용자에게 선택 요청
                    if pending_selection:
                        # 첫 번째 이름부터 선택 요청
                        first_name = list(pending_selection.keys())[0]
                        candidates = pending_selection[first_name]
                        
                        selection_prompt = f"**{first_name}**님의 이메일을 선택해주세요:\n\n"
                        for idx, candidate in enumerate(candidates, 1):
                            dept = candidate.get("dept", "부서 정보 없음")
                            selection_prompt += f"{idx}. {candidate['email']} ({dept})\n"
                        selection_prompt += "\n번호를 입력해주세요 (예: 1번, 2번)"
                        
                        # email_compose_context에 저장
                        email_compose_context["to_names"] = to_names
                        email_compose_context["to_emails"] = resolved_emails  # 이미 확정된 이메일들
                        email_compose_context["pending_recipient_selection"] = pending_selection
                        email_compose_context["current_recipient_name"] = first_name
                        email_compose_context["original_query"] = query  # 원래 쿼리 저장
                        
                        return {
                            "final_response": selection_prompt,
                            "messages": [selection_prompt],
                            "email_compose_context": email_compose_context,
                            "requires_input": True
                        }
                    elif unresolved_names:
                        # 매칭 없는 이름이 있으면 오류 메시지
                        return {
                            "final_response": f"다음 이름에 해당하는 이메일을 찾을 수 없습니다: {', '.join(unresolved_names)}",
                            "messages": [f"다음 이름에 해당하는 이메일을 찾을 수 없습니다: {', '.join(unresolved_names)}"],
                            "email_compose_context": email_compose_context
                        }
                    else:
                        # 모든 이름이 단일 매칭
                        to_emails = resolved_emails
                        if to_emails:
                            print(f"[DEBUG] 이름으로 이메일 조회 성공: {to_names} → {to_emails}")
                        else:
                            print(f"[DEBUG] 이름으로 이메일 조회 실패: {to_names}")
                            
            except Exception as e:
                print(f"⚠️ 이름으로 이메일 조회 실패: {e}")
                import traceback
                traceback.print_exc()
        
        # 이미 확정된 이메일이 있으면 사용
        if not to_emails:
            to_emails = email_compose_context.get("to_emails", [])
        
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
        # 저장된 초안이 있을 때만 "보내"를 발송 명령으로 인식
        has_stored_draft = bool(stored_draft_subject and stored_draft_body)
        
        if has_stored_draft:
            # 저장된 초안이 있으면 "보내"도 발송 명령으로 인식
            is_approval_command = any(k in query.lower() for k in ["발송", "보내", "전송", "send", "승인", "ok", "좋아"])
        else:
            # 저장된 초안이 없으면 "보내"는 새 작성 요청으로 처리 (제외)
            is_approval_command = any(k in query.lower() for k in ["발송", "전송", "send", "승인", "ok", "좋아"])
        
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
            # 원래 쿼리를 우선 사용 (동명이인 선택 완료 후에도 원래 요청 내용 사용)
            original_query = email_compose_context.get("original_query")
            if original_query:
                # 원래 쿼리에서 이메일 주소와 불필요한 단어 제거
                final_user_message = original_query
                if to_email:
                    final_user_message = final_user_message.replace(to_email, "").strip()
                final_user_message = re.sub(r'\s*(에게|에게\s+보내|보내줘|보내|전송|작성|작성해줘)\s*', '', final_user_message).strip()
            else:
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
                employee_id=employee_id,
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
                    "to_emails": to_emails if to_emails else email_compose_context.get("to_emails", []),
                    "to_names": to_names if to_names else email_compose_context.get("to_names", []),
                    "user_message": user_message or email_compose_context.get("user_message") or query,
                    "email_draft_subject": result.get("draft", {}).get("subject") if result.get("draft") else None,
                    "email_draft_body": result.get("draft", {}).get("body") if result.get("draft") else None,
                    "original_subject": original_subject or email_compose_context.get("original_subject"),
                    "original_sender": original_sender or email_compose_context.get("original_sender"),
                    "original_body": original_body or email_compose_context.get("original_body"),
                    "user_feedback": user_feedback,
                    "cc": state.get("cc") or email_compose_context.get("cc"),
                    "bcc": state.get("bcc") or email_compose_context.get("bcc"),
                    # 동명이인 선택 관련 정보도 유지
                    "pending_recipient_selection": email_compose_context.get("pending_recipient_selection", {}),
                    "current_recipient_name": email_compose_context.get("current_recipient_name"),
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
                        "email_draft": result.get("draft"),  # ✅ pending이어도 초안은 내려줘야 카드가 뜸
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

