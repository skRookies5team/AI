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
	route_type: Literal["supervisor", "qa", "schedule", "document", "tech"],
	fallback_route: Optional[str] = None,
) -> str:
	"""LLM을 사용하여 사용자 질의의 의도를 분류합니다.
	
	Args:
		query: 사용자 질의
		route_type: 라우팅 타입 ("supervisor", "qa", "schedule", "document", "tech")
		fallback_route: LLM 사용 불가 시 기본 라우트
		
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
- document: 문서 작성, 보고서 작성, 템플릿 생성, 이메일 자동응답 생성 등 문서/이메일 생성 관련
  (중요: "이메일에서 ... 찾아줘", "메일에서 ... 찾아줘" 같은 이메일 검색은 qa로 분류)
- qa: Q&A, 질문, 문의, 메뉴얼, 복리후생 정책, 휴가규정, 근무제도, 급여 외 혜택, 회사 복지, 보험, 수당, 식단, 메뉴, 구내식당, 이메일 검색 관련
  (중요: "휴가·근무제도에 대해 알려줘", "급여 외 혜택에 대해 알려줘" 같은 정책/제도 질문은 qa로 분류)
  (중요: "이메일에서 ... 찾아줘", "메일에서 ... 찾아줘", "이메일 찾아줘" 같은 이메일 검색은 qa로 분류)
- tech: 특허, 논문, KISTI, ScienceON 관련
- unknown: 위 카테고리에 해당하지 않는 경우

질의: {query}

JSON 형식으로 응답하세요: {{"route": "schedule|document|qa|tech|unknown"}}""",
			
			"qa": """사용자 질의를 분석하여 다음 QA 서브 에이전트 중 하나로 분류하세요:
- email: 이메일/메일 검색, 찾기 관련
  (중요: 숫자로 시작하는 질문은 이메일 검색 결과의 상세 요청일 가능성이 높습니다. 예: "355. 제목에 대해 자세히", "355번 상세", "355번에 대해 알려줘")
- benefit: 복리후생, 휴가규정, 근무제도, 급여 외 혜택, 복지, 보험, 수당, 퇴직연금, 회사 복지 정책 관련
  (주의: 숫자로 시작하는 질문이면 이메일 상세 요청일 가능성이 높으므로 email로 분류하세요)
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
		}
		
		prompt_text = prompts.get(route_type, prompts["supervisor"]).format(query=query)
		
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
					if route in ["schedule", "document", "qa", "tech", "unknown"]:
						return route
				else:
					intent = result.get("intent", fallback_route or "unknown")
					return intent
			except json.JSONDecodeError:
				pass
		
		# JSON 파싱 실패 시 텍스트에서 직접 추출 시도
		if route_type == "supervisor":
			for route in ["schedule", "document", "qa", "tech"]:
				if f'"route": "{route}"' in content or f'"route":"{route}"' in content:
					return route
		else:
			# intent 추출
			for intent in ["email", "benefit", "cafeteria", "manual", "leave", "meeting", "calendar", "email_auto_reply", "doc_generate", "patent", "article"]:
				if f'"intent": "{intent}"' in content or f'"intent":"{intent}"' in content:
					return intent
		
		return fallback_route or "unknown"
		
	except Exception as e:
		print(f"⚠️ LLM 라우팅 실패: {e}, fallback 사용")
		return fallback_route or "unknown"

