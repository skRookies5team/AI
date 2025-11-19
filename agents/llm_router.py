# llm_router.py — LLM 기반 의도 분류 및 라우팅
from __future__ import annotations

import os
from typing import Literal, Optional

try:
	from langchain_openai import ChatOpenAI
	from langchain_core.prompts import ChatPromptTemplate
	LLM_AVAILABLE = True
except ImportError:
	LLM_AVAILABLE = False


# ───────────────────────────────────────────────────────────
# .env 파일 로드
# ───────────────────────────────────────────────────────────
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


# ───────────────────────────────────────────────────────────
# LLM 기반 의도 분류
# ───────────────────────────────────────────────────────────
def classify_intent_with_llm(
	query: str,
	route_type: Literal["supervisor", "qa", "schedule", "document", "tech", "email"],
	fallback_route: Optional[str] = None,
	session_id: Optional[str] = None,
) -> str:
	"""LLM을 사용하여 사용자 질의의 의도를 분류합니다.
	
	Args:
		query: 사용자 질의
		route_type: 라우팅 타입 ("supervisor", "qa", "schedule", "document", "tech")
		fallback_route: LLM 사용 불가 시 기본 라우트
		session_id: 세션 ID (이전 검색 결과 확인용)
		
	Returns:
		분류된 라우트 이름
	"""
	if not LLM_AVAILABLE:
		return fallback_route or "unknown"
	
	api_key = _get_env("OPENAI_API_KEY")
	if not api_key:
		return fallback_route or "unknown"
	
	try:
		# 라우팅 타입별 프롬프트 정의
		prompts = {
			"supervisor": """사용자 질의를 분석하여 다음 중 하나로 분류하세요:
- schedule: 연차/반차/병가/경조사 신청, 회의실 예약, 일정 관리, 스케줄 조회 등 구체적인 액션 요청
- document: 문서 작성, 보고서 작성, 템플릿 생성 등 문서 생성 관련
  (중요: "이메일 자동응답", "메일 자동응답" 같은 이메일 답장문 생성은 email로 분류)
- qa: Q&A, 질문, 문의, 메뉴얼, 복리후생 정책, 휴가규정, 근무제도, 급여 외 혜택, 회사 복지, 보험, 수당, 식단, 메뉴, 구내식당 관련
  (중요: "휴가·근무제도에 대해 알려줘", "급여 외 혜택에 대해 알려줘" 같은 정책/제도 질문은 qa로 분류)
  (중요: "이메일에서 ... 찾아줘", "메일에서 ... 찾아줘" 같은 이메일 검색은 email로 분류)
- email: 이메일 검색, 이메일 답장문 생성 관련
  (중요: "이메일에서 ... 찾아줘", "메일에서 ... 찾아줘", "이메일 찾아줘" 같은 이메일 검색은 email로 분류)
  (중요: "이메일 자동응답", "메일 자동응답", "답장 작성" 같은 이메일 답장문 생성은 email로 분류)
  (매우 중요: 숫자로 시작하는 질문은 이메일 검색 결과의 상세 요청일 가능성이 매우 높습니다. 
   예: "938. [고용보험]육아휴직...에 대해 알려줘", "626. 육아휴직 연장 안내의 건에 대해 알려줘" 같은 패턴은 
   복리후생 키워드가 포함되어 있어도 이메일 상세 요청이므로 email로 분류하세요)
- tech: 특허, 논문, KISTI, ScienceON 관련
- unknown: 위 카테고리에 해당하지 않는 경우

질의: {query}

JSON 형식으로 응답하세요: {{"route": "schedule|document|qa|email|tech|unknown"}}""",
			
			"qa": """사용자 질의를 분석하여 다음 QA 서브 에이전트 중 하나로 분류하세요:
- email: 이메일/메일 검색, 찾기 관련
  (매우 중요: 숫자로 시작하는 질문은 이메일 검색 결과의 상세 요청일 가능성이 매우 높습니다. 
   예: "938. [고용보험]육아휴직...에 대해 알려줘", "626. 육아휴직 연장 안내의 건에 대해 알려줘", 
   "355. 제목에 대해 자세히", "355번 상세", "355번에 대해 알려줘" 같은 패턴은 
   복리후생 키워드("보험", "육아휴직" 등)가 포함되어 있어도 이메일 상세 요청이므로 email로 분류하세요)
  (패턴: "숫자. 제목에 대해 알려줘", "숫자번 상세", "숫자번에 대해" 같은 형식은 모두 email로 분류)
- benefit: 복리후생, 휴가규정, 근무제도, 급여 외 혜택, 복지, 보험, 수당, 퇴직연금, 회사 복지 정책 관련
  (주의: 숫자로 시작하는 질문이면 이메일 상세 요청일 가능성이 매우 높으므로 email로 분류하세요)
  (주의: "에 대해 알려줘", "자세히", "상세히" 같은 키워드가 있고 숫자로 시작하면 email로 분류)
- cafeteria: 식단, 메뉴, 구내식당, 점심, 저녁 관련
- manual: 메뉴얼, Q&A, 질문, 문의 관련 (위 카테고리에 해당하지 않는 경우)

질의: {query}

JSON 형식으로 응답하세요: {{"intent": "email|benefit|cafeteria|manual"}}""",
			
			"schedule": """사용자 질의를 분석하여 다음 스케줄 서브 에이전트 중 하나로 분류하세요:
- leave: 연차, 반차, 병가, 경조사, 휴가 관련
- meeting: 회의실, 예약 관련
- calendar: 일정, 캘린더, 스케줄 관련

질의: {query}

JSON 형식으로 응답하세요: {{"intent": "leave|meeting|calendar"}}""",
			
			"document": """사용자 질의를 분석하여 다음 문서 서브 에이전트 중 하나로 분류하세요:
- email_auto_reply: 이메일, 메일, 자동응답 관련
- doc_generate: 문서, 보고서, 작성, 템플릿 관련

질의: {query}

JSON 형식으로 응답하세요: {{"intent": "email_auto_reply|doc_generate"}}""",
			
			"tech": """사용자 질의를 분석하여 다음 기술 서브 에이전트 중 하나로 분류하세요:
- patent: 특허, Patent 관련
- article: 논문, Article, paper, 학술 관련

질의: {query}

JSON 형식으로 응답하세요: {{"intent": "patent|article"}}""",
			
			"email": """사용자 질의를 분석하여 다음 이메일 서브 에이전트 중 하나로 분류하세요:
- search: 이메일/메일 검색, 찾기 관련
  (중요: "이메일에서 ... 찾아줘", "메일에서 ... 찾아줘", "이메일 찾아줘" 같은 패턴은 search로 분류)
  (매우 중요: 숫자로 시작하는 질문은 이메일 검색 결과의 상세 요청일 가능성이 매우 높습니다. 
   예: "938. [고용보험]육아휴직...에 대해 알려줘", "626. 육아휴직 연장 안내의 건에 대해 알려줘", 
   "355. 제목에 대해 자세히", "355번 상세", "355번에 대해 알려줘" 같은 패턴은 모두 search로 분류)
  (패턴: "숫자. 제목에 대해 알려줘", "숫자번 상세", "숫자번에 대해" 같은 형식은 모두 search로 분류)
- auto_reply: 이메일 답장문 생성, 자동응답 생성 관련
  (중요: "이메일 자동응답", "메일 자동응답", "답장 작성", "응답 작성", "회신 작성" 같은 패턴은 auto_reply로 분류)
  (주의: "이메일에서 ... 찾아줘" 같은 검색 의도는 search로 분류하세요)

질의: {query}

JSON 형식으로 응답하세요: {{"intent": "search|auto_reply"}}""",
		}
		
		# 이전 검색 결과 컨텍스트 추가 (qa, supervisor, email의 경우)
		context_info = ""
		if route_type in ["qa", "supervisor", "email"]:
			try:
				from .email_search_agent import has_pending_email_results
				from .tech_patent_agent import has_pending_patent_results
				from .tech_master import has_pending_article_results
				
				has_email = has_pending_email_results(session_id) if session_id else False
				has_patent = has_pending_patent_results(session_id) if session_id else False
				has_article = has_pending_article_results(session_id) if session_id else False
				
				if has_email or has_patent or has_article:
					context_info = "\n\n[중요 컨텍스트 정보]\n"
					if has_email:
						context_info += "- 이전에 이메일 검색을 수행했습니다. 숫자로 시작하는 질문은 이메일 상세 요청일 가능성이 매우 높습니다.\n"
					if has_patent:
						context_info += "- 이전에 특허 검색을 수행했습니다.\n"
					if has_article:
						context_info += "- 이전에 논문 검색을 수행했습니다.\n"
			except Exception:
				pass
		
		prompt_text = prompts.get(route_type, prompts["supervisor"]).format(query=query) + context_info
		
		llm = ChatOpenAI(
			model="gpt-4o-mini",
			temperature=0,
			api_key=api_key,
		)
		
		response = llm.invoke(prompt_text)
		content = response.content.strip()
		
		# JSON 파싱
		import json
		import re
		
		# JSON 부분만 추출 (중첩된 중괄호 지원)
		json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content)
		if json_match:
			try:
				result = json.loads(json_match.group())
				if route_type == "supervisor":
					route = result.get("route", fallback_route or "unknown")
					if route in ["schedule", "document", "qa", "email", "tech", "unknown"]:
						return route
				else:
					intent = result.get("intent", fallback_route or "unknown")
					return intent
			except json.JSONDecodeError:
				pass
		
		# JSON 파싱 실패 시 텍스트에서 직접 추출 시도
		if route_type == "supervisor":
			for route in ["schedule", "document", "qa", "email", "tech"]:
				if f'"route": "{route}"' in content or f'"route":"{route}"' in content:
					return route
		else:
			# intent 추출
			for intent in ["email", "benefit", "cafeteria", "manual", "leave", "meeting", "calendar", "email_auto_reply", "doc_generate", "patent", "article", "search", "auto_reply"]:
				if f'"intent": "{intent}"' in content or f'"intent":"{intent}"' in content:
					return intent
		
		return fallback_route or "unknown"
		
	except Exception as e:
		print(f"⚠️ LLM 라우팅 실패: {e}, fallback 사용")
		return fallback_route or "unknown"

