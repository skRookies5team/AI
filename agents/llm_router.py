# llm_router.py — LLM 기반 의도 분류 및 라우팅 (YAML 기반 프롬프트)
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional, Dict, Any

try:
	from langchain_openai import ChatOpenAI
	from langchain_core.prompts import ChatPromptTemplate
	LLM_AVAILABLE = True
except ImportError:
	LLM_AVAILABLE = False

try:
	import yaml
	YAML_AVAILABLE = True
except ImportError:
	YAML_AVAILABLE = False


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
# YAML 프롬프트 로드
# ───────────────────────────────────────────────────────────
_agent_prompts: Optional[Dict[str, Any]] = None


def load_agent_prompts() -> Dict[str, Any]:
	"""모든 agent별 YAML 파일을 로드하고 병합합니다.
	
	다음 파일들을 순차적으로 로드하여 병합:
	1. agents/common_prompts.yaml (supervisor, orchestrator, orchestrator_config)
	2. agents/master_graphs/{domain}/prompts.yaml (각 도메인별 설정)
	
	Returns:
		병합된 YAML 파일의 내용을 딕셔너리로 반환
	"""
	global _agent_prompts
	
	if _agent_prompts is not None:
		return _agent_prompts
	
	if not YAML_AVAILABLE:
		print("⚠️ YAML 패키지가 설치되지 않았습니다. 기본 프롬프트를 사용합니다.")
		return {}
	
	try:
		current_dir = Path(__file__).parent
		merged_prompts: Dict[str, Any] = {}
		
		# 1. 공통 설정 파일 로드
		common_path = current_dir / "common_prompts.yaml"
		if common_path.exists():
			with open(common_path, "r", encoding="utf-8") as f:
				common_data = yaml.safe_load(f) or {}
				merged_prompts.update(common_data)
				print(f"✅ 공통 프롬프트 로드 완료: {common_path}")
		else:
			print(f"⚠️ common_prompts.yaml 파일을 찾을 수 없습니다: {common_path}")
		
		# 2. 각 도메인별 프롬프트 파일 로드
		domain_names = ["schedule", "document", "qa", "email", "tech", "recruitment"]
		master_graphs_dir = current_dir / "master_graphs"
		
		for domain in domain_names:
			domain_path = master_graphs_dir / domain / "prompts.yaml"
			if domain_path.exists():
				try:
					with open(domain_path, "r", encoding="utf-8") as f:
						domain_data = yaml.safe_load(f) or {}
						# 도메인 이름을 키로 사용하여 병합
						merged_prompts[domain] = domain_data
						print(f"✅ {domain} 도메인 프롬프트 로드 완료: {domain_path}")
				except Exception as e:
					print(f"⚠️ {domain} 도메인 프롬프트 로드 실패: {e}")
			else:
				print(f"⚠️ {domain} 도메인 프롬프트 파일을 찾을 수 없습니다: {domain_path}")
		
		# 3. 하위 호환성: 기존 agent_prompts.yaml 파일이 있으면 경고만 출력
		legacy_path = current_dir / "agent_prompts.yaml"
		if legacy_path.exists():
			print(f"⚠️ 기존 agent_prompts.yaml 파일이 발견되었습니다. 새로운 분리된 구조를 사용합니다.")
		
		_agent_prompts = merged_prompts
		print(f"✅ 총 {len(domain_names)}개 도메인 프롬프트 병합 완료")
		return _agent_prompts
		
	except Exception as e:
		print(f"⚠️ YAML 파일 로드 실패: {e}. 기본 프롬프트를 사용합니다.")
		return {}


def build_prompt_from_yaml(route_type: str, query: str, context_info: str = "") -> str:
	"""YAML에서 프롬프트를 빌드합니다.
	
	Args:
		route_type: 라우팅 타입 ("supervisor", "qa", "schedule", "document", "tech", "email")
		query: 사용자 질의
		context_info: 추가 컨텍스트 정보
	
	Returns:
		생성된 프롬프트 텍스트
	"""
	prompts_data = load_agent_prompts()
	
	if not prompts_data or route_type not in prompts_data:
		# YAML 로드 실패 시 기본 프롬프트 사용
		return _get_default_prompt(route_type, query, context_info)
	
	route_config = prompts_data[route_type]
	
	# 기본 프롬프트 구조 생성
	prompt_parts = []
	
	# 설명 추가
	if "description" in route_config:
		prompt_parts.append(f"{route_config['description']}\n")
	
	# 아키텍처 기능 추가 (supervisor의 경우)
	if "architecture_features" in route_config:
		prompt_parts.append("\n[시스템 아키텍처 기능]")
		prompt_parts.append(route_config["architecture_features"])
	
	# 기능 설명 추가 (각 도메인별)
	if "features" in route_config:
		prompt_parts.append("\n[주요 기능]")
		prompt_parts.append(route_config["features"])
	
	# 분류 기준 추가
	if "classification_criteria" in route_config:
		prompt_parts.append("\n[분류 기준]")
		prompt_parts.append(route_config["classification_criteria"])
	
	# 서브 에이전트 정보 추가 (sub_agents가 있는 경우)
	if "sub_agents" in route_config:
		prompt_parts.append("\n[서브 에이전트 정보]")
		for sub_agent_name, sub_agent_config in route_config["sub_agents"].items():
			prompt_parts.append(f"\n{sub_agent_name}:")
			if "description" in sub_agent_config:
				prompt_parts.append(f"  설명: {sub_agent_config['description']}")
			if "features" in sub_agent_config:
				prompt_parts.append(f"  기능: {sub_agent_config['features']}")
			if "capabilities" in sub_agent_config:
				prompt_parts.append("  가능한 작업:")
				for capability in sub_agent_config["capabilities"]:
					prompt_parts.append(f"    - {capability}")
			if "keywords" in sub_agent_config:
				positive = sub_agent_config["keywords"].get("positive", [])
				if positive:
					prompt_parts.append(f"  키워드: {', '.join(positive)}")
			if "examples" in sub_agent_config:
				prompt_parts.append("  예시:")
				for example in sub_agent_config["examples"]:
					prompt_parts.append(f"    - {example}")
	
	# 예시 추가 (서브 에이전트가 없는 경우 또는 추가 예시)
	if "examples" in route_config:
		prompt_parts.append("\n[예시]")
		for category, examples in route_config["examples"].items():
			if isinstance(examples, list):
				prompt_parts.append(f"\n{category}:")
				for example in examples:
					prompt_parts.append(f"  - {example}")
	
	# 중요 규칙 추가
	if "important_rules" in route_config:
		prompt_parts.append("\n[중요 규칙]")
		for rule in route_config["important_rules"]:
			prompt_parts.append(f"  - {rule}")
	
	# 출력 형식 추가
	if "output_format" in route_config:
		prompt_parts.append(f"\n[출력 형식]")
		prompt_parts.append(route_config["output_format"])
	
	# 사용자 질의 추가
	prompt_parts.append(f"\n[사용자 질의]")
	prompt_parts.append(f"{query}")
	
	# 컨텍스트 정보 추가
	if context_info:
		prompt_parts.append(context_info)
	
	# 최종 프롬프트 조합
	prompt_text = "\n".join(prompt_parts)
	
	return prompt_text


def _get_default_prompt(route_type: str, query: str, context_info: str = "") -> str:
	"""기본 프롬프트 (YAML 로드 실패 시 사용)."""
	# 기존 하드코딩된 프롬프트 유지
	base_prompts = {
		"supervisor": """사용자 질의를 분석하여 다음 중 하나로 분류하세요:
- schedule: 연차/반차/병가/경조사 신청, 회의실 예약, 일정 관리, 스케줄 조회 등 구체적인 액션 요청
- document: 문서 작성, 보고서 작성, 템플릿 생성 등 문서 생성 관련
- qa: Q&A, 질문, 문의, 메뉴얼, 복리후생 정책, 휴가규정, 근무제도, 급여 외 혜택, 회사 복지, 보험, 수당, 식단, 메뉴, 구내식당 관련
- email: 이메일 검색, 이메일 답장문 생성 관련
- tech: 특허, 논문, KISTI, ScienceON 관련

질의: {query}

JSON 형식으로 응답하세요: {{"route": "schedule|document|qa|email|tech|unknown"}}""",
		# 기타 기본 프롬프트들...
	}
	
	base_prompt = base_prompts.get(route_type, base_prompts.get("supervisor", ""))
	return base_prompt.format(query=query) + context_info


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
		# 이전 검색 결과 컨텍스트 추가 (Redis 기반 멀티턴 대화 지원)
		context_info = ""
		if route_type in ["qa", "supervisor", "email", "tech", "schedule", "document"]:
			try:
				from agents.master_graphs.email.email_search_agent import has_pending_email_results
				from agents.master_graphs.tech.patent.patent_config import has_pending_patent_results
				from agents.master_graphs.tech.tech_article_agent import has_pending_article_results

				has_email = has_pending_email_results(session_id) if session_id else False
				has_patent = has_pending_patent_results(session_id) if session_id else False
				has_article = has_pending_article_results(session_id) if session_id else False

				if has_email or has_patent or has_article:
					context_info = "\n\n[Redis 기반 이전 대화 컨텍스트]\n"
					context_info += "시스템은 Redis를 통해 이전 대화 내용을 기억하고 있습니다.\n"
					if has_email:
						context_info += "- '더 찾아줘' 같은 요청은 이전 검색의 연속 검색으로 처리해야 합니다.\n"
					if has_patent:
						context_info += "- 이전에 특허 검색을 수행했습니다. '웹 검색', '사내 검색' 같은 단순 응답은 이전 검색의 연속으로 처리해야 합니다.\n"
					if has_article:
						context_info += "- 이전에 논문 검색을 수행했습니다. '웹 검색', '사내 검색' 같은 단순 응답은 이전 검색의 연속으로 처리해야 합니다.\n"
					
					# Human-in-the-loop 관련 컨텍스트
					context_info += "\n[Human-in-the-loop 주의사항]\n"
					context_info += "- '승인', '확인', '네', '예' 같은 응답은 이전에 요청한 작업(연차 신청, 회의실 예약, 결재 요청 등)의 승인일 수 있습니다.\n"
					context_info += "- '취소', '아니오' 같은 응답은 이전 작업의 취소일 수 있습니다.\n"
			except Exception:
				pass

		# YAML 기반 프롬프트 빌드
		prompt_text = build_prompt_from_yaml(route_type, query, context_info)

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
			for intent in ["email", "benefit", "cafeteria", "manual", "leave", "meeting", "schedule", "business_trip", "calendar", "email_auto_reply", "doc_generate", "patent", "article", "search", "auto_reply"]:
				if f'"intent": "{intent}"' in content or f'"intent":"{intent}"' in content:
					return intent
		
		return fallback_route or "unknown"
		
	except Exception as e:
		print(f"⚠️ LLM 라우팅 실패: {e}, fallback 사용")
		return fallback_route or "unknown"

