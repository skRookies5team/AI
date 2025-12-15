# qa_benefit_agent.py — 복리후생 RAG 에이전트 (LangGraph 기반)
from __future__ import annotations

import os
import re
import time
from typing import TypedDict, List, Dict, Optional, Any
from collections import Counter

try:
	from pymilvus import connections, Collection
	MILVUS_AVAILABLE = True
except ImportError:
	MILVUS_AVAILABLE = False

try:
	from sentence_transformers import SentenceTransformer
	from agents.embedding_utils import get_embedding_generator
	SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
	SENTENCE_TRANSFORMERS_AVAILABLE = False

try:
	from langchain_openai import ChatOpenAI
	from langchain_core.prompts import ChatPromptTemplate
	LLM_AVAILABLE = True
except ImportError:
	LLM_AVAILABLE = False

from langgraph.graph import StateGraph, END
from pathlib import Path
import yaml


# ───────────────────────────────────────────────────────────
# YAML 설정 로드
# ───────────────────────────────────────────────────────────
_qa_benefit_config = None

def _load_qa_benefit_config() -> Dict[str, Any]:
	"""QA Benefit Agent 설정을 YAML 파일에서 로드합니다."""
	global _qa_benefit_config
	
	if _qa_benefit_config is not None:
		return _qa_benefit_config
	
	try:
		# llm_router의 통합 로더 사용
		from agents.llm_router import load_agent_prompts
		all_config = load_agent_prompts()
		# qa 도메인 아래의 qa_benefit 설정 로드
		qa_config = all_config.get("qa", {})
		_qa_benefit_config = qa_config.get("qa_benefit", {})
	except Exception as e:
		print(f"[WARNING] QA Benefit 설정 로드 실패: {e}, 기본값 사용")
		_qa_benefit_config = {}
	
	return _qa_benefit_config

def load_qa_benefit_config() -> Dict[str, Any]:
	"""QA Benefit Agent 설정을 반환합니다."""
	return _load_qa_benefit_config()


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
# Milvus 연결 설정 (.env 파일에서 읽기)
# ───────────────────────────────────────────────────────────
def _get_env_trimmed(key: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 조회 (공백 제거)."""
    value = os.environ.get(key, default)
    return value.strip() if value and isinstance(value, str) else (default if value is None else value)

MILVUS_HOST = _get_env_trimmed("HOST") or _get_env_trimmed("MILVUS_HOST") or "175.114.229.152"
MILVUS_PORT_STR = _get_env_trimmed("MILVUS_PORT") or "19530"
MILVUS_PORT = int(MILVUS_PORT_STR.strip()) if MILVUS_PORT_STR else 19530
COLLECTION_NAME = (
    _get_env_trimmed("BENEFIT_COLLECTION") or
    _get_env_trimmed("MILVUS_COLLECTION_NAME") or
    "Employee_Benefits_embeddings"
)

# 전역 변수 (지연 초기화)
_collection: Optional[Collection] = None
_embedding_model = None  # OpenAIEmbeddingGenerator 인스턴스
_rag_graph = None


# ───────────────────────────────────────────────────────────
# 초기화 함수
# ───────────────────────────────────────────────────────────
def _initialize_connections() -> None:
	"""Milvus 연결 및 모델 초기화 (지연 초기화)."""
	global _collection, _embedding_model
	
	if not MILVUS_AVAILABLE:
		raise ImportError("pymilvus가 설치되지 않았습니다. pip install pymilvus")
	
	if _collection is None:
		try:
			# Milvus 서버 연결
			connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
			
			# 컬렉션 존재 여부 확인
			from pymilvus import utility
			if not utility.has_collection(COLLECTION_NAME):
				# 사용 가능한 컬렉션 목록 조회
				available_collections = utility.list_collections()
				error_msg = (
					f"컬렉션 '{COLLECTION_NAME}'이 존재하지 않습니다.\n"
					f"사용 가능한 컬렉션: {', '.join(available_collections) if available_collections else '없음'}\n"
					f"환경변수 MILVUS_COLLECTION_NAME을 확인하거나 컬렉션을 생성해주세요."
				)
				raise RuntimeError(error_msg)
			
			_collection = Collection(COLLECTION_NAME)
			_collection.load()
		except RuntimeError:
			# 이미 포맷된 에러 메시지는 그대로 전달
			raise
		except Exception as e:
			error_msg = f"Milvus 연결 실패 (호스트: {MILVUS_HOST}:{MILVUS_PORT}, 컬렉션: {COLLECTION_NAME}): {str(e)}"
			raise RuntimeError(error_msg)
	
	if _embedding_model is None:
		try:
			_embedding_model = get_embedding_generator()
		except Exception as e:
			raise RuntimeError(f"임베딩 모델 로딩 실패: {e}")


# ───────────────────────────────────────────────────────────
# LangGraph State 정의
# ───────────────────────────────────────────────────────────
class BenefitState(TypedDict, total=False):
	"""복리후생 RAG 상태."""
	question: str
	query_embedding: List[float]
	query_keywords: List[str]
	vector_search_results: List[Dict]
	keyword_search_results: List[Dict]
	retrieved_contexts: List[str]
	answer: str
	timings: Dict[str, float]
	conversation_history: Optional[List[Dict[str, str]]]  # 이전 대화 히스토리 (Redis 숏텀 메모리)


# ───────────────────────────────────────────────────────────
# 키워드 추출 함수
# ───────────────────────────────────────────────────────────
def extract_keywords(text: str, min_length: Optional[int] = None, top_count: Optional[int] = None) -> List[str]:
	"""텍스트에서 키워드 추출 (한글, 영문, 숫자).
	
	Args:
		text: 추출할 텍스트
		min_length: 최소 키워드 길이 (None이면 YAML에서 로드)
		top_count: 상위 키워드 개수 (None이면 YAML에서 로드)
	"""
	# 키워드 추출 파라미터 (YAML에서 로드)
	if min_length is None or top_count is None:
		config = load_qa_benefit_config()
		search_params = config.get("search_params", {})
		if min_length is None:
			min_length = search_params.get("keyword_min_length", 2)
		if top_count is None:
			top_count = search_params.get("keyword_top_count", 10)
	
	pattern = r'[가-힣a-zA-Z0-9]+'
	keywords = re.findall(pattern, text.lower())
	keywords = [kw for kw in keywords if len(kw) >= min_length]
	keyword_counts = Counter(keywords)
	return [kw for kw, _ in keyword_counts.most_common(top_count)]


# ───────────────────────────────────────────────────────────
# LangGraph 노드들
# ───────────────────────────────────────────────────────────
def analyze_query(state: BenefitState) -> BenefitState:
	"""사용자 질문을 임베딩 벡터로 변환하고 키워드 추출."""
	start_time = time.time()
	question = state["question"]
	
	_initialize_connections()
	
	# 임베딩 생성
	query_embedding = _embedding_model.embed(question)
	
	# 키워드 추출
	query_keywords = extract_keywords(question)
	
	elapsed_time = time.time() - start_time
	timings = state.get("timings", {})
	timings["analyze_query"] = elapsed_time
	
	return {
		"query_embedding": query_embedding,
		"query_keywords": query_keywords,
		"timings": timings
	}


def calculate_keyword_score(text: str, keywords: List[str]) -> float:
	"""문서에서 키워드 매칭 점수 계산.
	
	Args:
		text: 문서 텍스트
		keywords: 검색 키워드 리스트
	"""
	# 키워드 점수 가중치 (YAML에서 로드)
	config = load_qa_benefit_config()
	search_params = config.get("search_params", {})
	exact_match_score = search_params.get("keyword_exact_match_score", 2.0)
	count_multiplier = search_params.get("keyword_count_multiplier", 0.1)
	substring_score = search_params.get("keyword_substring_score", 0.1)
	
	text_lower = text.lower()
	score = 0.0
	
	for keyword in keywords:
		if keyword in text_lower:
			score += exact_match_score
			score += text_lower.count(keyword) * count_multiplier
		
		if len(keyword) > 3:
			for i in range(len(keyword) - 2):
				substring = keyword[i:i+3]
				if substring in text_lower:
					score += substring_score
	
	return score


def vector_search(state: BenefitState) -> BenefitState:
	"""Milvus에서 벡터 기반 유사 문서 검색."""
	start_time = time.time()
	query_embedding = state["query_embedding"]
	
	# 검색 파라미터 (YAML에서 로드)
	config = load_qa_benefit_config()
	search_params = config.get("search_params", {})
	vector_search_limit = search_params.get("vector_search_limit", 15)
	milvus_metric_type = search_params.get("milvus_metric_type", "L2")
	milvus_nprobe = search_params.get("milvus_nprobe", 10)
	
	search_results = _collection.search(
		data=[query_embedding],
		anns_field="embedding",
		param={"metric_type": milvus_metric_type, "params": {"nprobe": milvus_nprobe}},
		limit=vector_search_limit,
		output_fields=["content"]
	)
	
	vector_results = []
	for hit in search_results[0]:
		content = hit.entity.get('content')
		distance = hit.distance
		score = 1.0 / (1.0 + distance)
		vector_results.append({
			"content": content,
			"score": score,
			"distance": distance,
			"source": "vector"
		})
	
	elapsed_time = time.time() - start_time
	timings = state.get("timings", {})
	timings["vector_search"] = elapsed_time
	
	return {
		"vector_search_results": vector_results,
		"timings": timings
	}


def keyword_search(state: BenefitState) -> BenefitState:
	"""키워드 기반 문서 검색."""
	start_time = time.time()
	query_keywords = state["query_keywords"]
	vector_results = state.get("vector_search_results", [])
	
	if not query_keywords or not vector_results:
		elapsed_time = time.time() - start_time
		timings = state.get("timings", {})
		timings["keyword_search"] = elapsed_time
		return {
			"keyword_search_results": [],
			"timings": timings
		}
	
	keyword_results = []
	for result in vector_results:
		content = result["content"]
		keyword_score = calculate_keyword_score(content, query_keywords)
		keyword_results.append({
			"content": content,
			"score": keyword_score,
			"source": "keyword"
		})
	
	keyword_results.sort(key=lambda x: x["score"], reverse=True)
	
	elapsed_time = time.time() - start_time
	timings = state.get("timings", {})
	timings["keyword_search"] = elapsed_time
	
	return {
		"keyword_search_results": keyword_results,
		"timings": timings
	}


def hybrid_merge(state: BenefitState) -> BenefitState:
	"""벡터 검색과 키워드 검색 결과를 병합하여 최종 결과 생성."""
	start_time = time.time()
	vector_results = state.get("vector_search_results", [])
	keyword_results = state.get("keyword_search_results", [])
	
	merged_results: Dict[str, Dict] = {}
	
	for result in vector_results:
		content = result["content"]
		if content not in merged_results:
			merged_results[content] = {
				"content": content,
				"vector_score": result["score"],
				"keyword_score": 0.0,
				"source": "vector"
			}
		else:
			merged_results[content]["vector_score"] = result["score"]
	
	for result in keyword_results:
		content = result["content"]
		if content not in merged_results:
			merged_results[content] = {
				"content": content,
				"vector_score": 0.0,
				"keyword_score": result["score"],
				"source": "keyword"
			}
		else:
			merged_results[content]["keyword_score"] = result["score"]
			merged_results[content]["source"] = "hybrid"
	
	# 하이브리드 검색 가중치 (YAML에서 로드)
	config = load_qa_benefit_config()
	hybrid_search = config.get("hybrid_search", {})
	VECTOR_WEIGHT = hybrid_search.get("vector_weight", 0.6)
	KEYWORD_WEIGHT = hybrid_search.get("keyword_weight", 0.4)
	
	if vector_results:
		max_vector_score = max(r["score"] for r in vector_results)
		min_vector_score = min(r["score"] for r in vector_results)
		vector_range = max_vector_score - min_vector_score if max_vector_score > min_vector_score else 1.0
	else:
		vector_range = 1.0
	
	if keyword_results:
		max_keyword_score = max(r["score"] for r in keyword_results)
		min_keyword_score = min(r["score"] for r in keyword_results)
		keyword_range = max_keyword_score - min_keyword_score if max_keyword_score > min_keyword_score else 1.0
	else:
		keyword_range = 1.0
	
	final_results = []
	for content, result in merged_results.items():
		normalized_vector = (result["vector_score"] - (min_vector_score if vector_results else 0)) / vector_range if vector_range > 0 else 0
		normalized_keyword = (result["keyword_score"] - (min_keyword_score if keyword_results else 0)) / keyword_range if keyword_range > 0 else 0
		hybrid_score = (normalized_vector * VECTOR_WEIGHT) + (normalized_keyword * KEYWORD_WEIGHT)
		
		final_results.append({
			"content": content,
			"hybrid_score": hybrid_score,
			"vector_score": normalized_vector,
			"keyword_score": normalized_keyword,
			"source": result["source"]
		})
	
	final_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
	# 상위 결과 개수 (YAML에서 로드)
	config = load_qa_benefit_config()
	search_params = config.get("search_params", {})
	top_results_count = search_params.get("top_results_count", 7)
	top_results = final_results[:top_results_count]
	retrieved_contexts = [r["content"] for r in top_results]
	
	elapsed_time = time.time() - start_time
	timings = state.get("timings", {})
	timings["hybrid_merge"] = elapsed_time
	
	return {
		"retrieved_contexts": retrieved_contexts,
		"timings": timings
	}


def generate_answer(state: BenefitState) -> BenefitState:
	"""검색된 컨텍스트를 바탕으로 GPT가 답변 생성."""
	start_time = time.time()
	question = state["question"]
	contexts = state["retrieved_contexts"]
	
	if not LLM_AVAILABLE:
		answer = "LLM이 사용 불가능합니다. langchain-openai를 설치해주세요."
		return {
			"answer": answer,
			"timings": state.get("timings", {})
		}
	
	api_key = _get_env("OPENAI_API_KEY")
	if not api_key:
		answer = "OpenAI API 키가 설정되지 않았습니다."
		return {
			"answer": answer,
			"timings": state.get("timings", {})
		}
	
	# 검색 결과가 없으면 명확한 메시지 반환
	if not contexts or len(contexts) == 0:
		answer = "복리후생 관련 정보를 찾을 수 없습니다. 질문을 다시 확인해 주시거나 다른 키워드로 검색해 주세요."
		return {
			"answer": answer,
			"timings": state.get("timings", {})
		}
	
	context_text = "\n\n".join([f"[문서 {i+1}]\n{ctx}" for i, ctx in enumerate(contexts)])
	conversation_history = state.get("conversation_history")
	
	# 프롬프트 및 설정 (YAML에서 로드)
	config = load_qa_benefit_config()
	prompts = config.get("prompts", {})
	
	# YAML에서 system_prompt 로드 (YAML에 정의되어 있어야 함)
	system_prompt = prompts.get("system_prompt")
	if not system_prompt:
		raise ValueError(
			"system_prompt가 YAML 설정 파일에 정의되어 있지 않습니다. "
			"agents/master_graphs/qa/prompts.yaml 파일의 qa_benefit.prompts.system_prompt를 확인하세요."
		)
	
	# 이전 대화 히스토리 구성
	messages = [
		("system", system_prompt),
	]
	
	# 이전 대화 히스토리 추가 (YAML 설정 사용)
	search_params = config.get("search_params", {})
	conversation_history_max = search_params.get("conversation_history_max_messages", 5)
	if conversation_history:
		for msg in conversation_history[-conversation_history_max:]:
			role = msg.get("role", "")
			content = msg.get("content", "")
			if role and content:
				messages.append((role, content))
	
	# YAML에서 user_prompt 로드 (YAML에 정의되어 있어야 함)
	user_prompt = prompts.get("user_prompt")
	if not user_prompt:
		raise ValueError(
			"user_prompt가 YAML 설정 파일에 정의되어 있지 않습니다. "
			"agents/master_graphs/qa/prompts.yaml 파일의 qa_benefit.prompts.user_prompt를 확인하세요."
		)
	messages.append(("user", user_prompt))
	
	prompt_template = ChatPromptTemplate.from_messages(messages)
	
	# LLM 설정 (YAML에서 로드)
	llm_config = config.get("llm", {})
	llm_model = llm_config.get("model", "gpt-4o-mini")
	llm_temperature = llm_config.get("temperature", 0)
	
	llm = ChatOpenAI(
		model=llm_model,
		temperature=llm_temperature,
		api_key=api_key
	)
	
	chain = prompt_template | llm
	response = chain.invoke({
		"context": context_text,
		"question": question
	})
	
	answer = response.content
	elapsed_time = time.time() - start_time
	timings = state.get("timings", {})
	timings["generate_answer"] = elapsed_time
	
	return {
		"answer": answer,
		"timings": timings
	}


# ───────────────────────────────────────────────────────────
# LangGraph 그래프 구성
# ───────────────────────────────────────────────────────────
def _create_rag_graph():
	"""하이브리드 RAG 그래프 생성."""
	workflow = StateGraph(BenefitState)
	
	workflow.add_node("analyze", analyze_query)
	workflow.add_node("vector_search", vector_search)
	workflow.add_node("keyword_search", keyword_search)
	workflow.add_node("hybrid_merge", hybrid_merge)
	workflow.add_node("generate", generate_answer)
	
	workflow.set_entry_point("analyze")
	workflow.add_edge("analyze", "vector_search")
	workflow.add_edge("vector_search", "keyword_search")
	workflow.add_edge("keyword_search", "hybrid_merge")
	workflow.add_edge("hybrid_merge", "generate")
	workflow.add_edge("generate", END)
	
	return workflow.compile()


# ───────────────────────────────────────────────────────────
# 메인 함수
# ───────────────────────────────────────────────────────────
async def benefit_rag(
	user_query: str,
	orchestrator_state: Optional[Dict[str, Any]] = None,
) -> Dict:
	"""복리후생 RAG 에이전트 메인 함수.
	
	Args:
		user_query: 사용자 질의
		orchestrator_state: OrchestratorState 딕셔너리 (Redis 숏텀 메모리용)
		
	Returns:
		에이전트 실행 결과 딕셔너리
	"""
	try:
		# 그래프 생성 (캐싱)
		global _rag_graph
		if _rag_graph is None:
			_rag_graph = _create_rag_graph()
		
		# conversation_history 추출
		conversation_history = None
		if orchestrator_state:
			conv_history = orchestrator_state.get("conversation_history", [])
			if conv_history:
				# Message 객체를 dict로 변환
				conversation_history = [
					{
						"role": msg.get("role", ""),
						"content": msg.get("content", ""),
					}
					for msg in conv_history
					if isinstance(msg, dict) and msg.get("role") and msg.get("content")
				]
		
		# 초기 상태 설정
		initial_state = {
			"question": user_query,
			"query_embedding": [],
			"query_keywords": [],
			"vector_search_results": [],
			"keyword_search_results": [],
			"retrieved_contexts": [],
			"answer": "",
			"timings": {},
			"conversation_history": conversation_history,
		}
		
		# 그래프 실행
		result = await _rag_graph.ainvoke(initial_state)
		
		# 답변 텍스트 추출
		answer_text = result.get("answer", "답변을 생성할 수 없습니다.")
		
		# Redis에 검색 컨텍스트 및 답변 저장
		if orchestrator_state is not None:
			# qa_search_context 초기화 (None 체크 포함)
			if "qa_search_context" not in orchestrator_state or orchestrator_state["qa_search_context"] is None:
				orchestrator_state["qa_search_context"] = {}
			if not isinstance(orchestrator_state.get("qa_search_context"), dict):
				orchestrator_state["qa_search_context"] = {}
			
			# 검색 컨텍스트 저장
			orchestrator_state["qa_search_context"]["benefit"] = {
				"last_query": user_query,
				"last_answer": answer_text,
				"retrieved_contexts": result.get("retrieved_contexts", []),
			}
			
			# conversation_history에 답변 추가 (Redis 저장용)
			if "conversation_history" not in orchestrator_state:
				orchestrator_state["conversation_history"] = []
			if not isinstance(orchestrator_state.get("conversation_history"), list):
				orchestrator_state["conversation_history"] = []
			
			# 사용자 메시지가 없으면 추가
			conversation_history = orchestrator_state["conversation_history"]
			if not conversation_history or conversation_history[-1].get("role") != "user":
				from datetime import datetime
				conversation_history.append({
					"role": "user",
					"content": user_query,
					"timestamp": datetime.now().isoformat()
				})
			
			# Assistant 답변 추가
			from datetime import datetime
			conversation_history.append({
				"role": "assistant",
				"content": answer_text,
				"timestamp": datetime.now().isoformat()
			})
		
		# 결과 반환
		return {
			"ok": True,
			"message": answer_text,
			"contexts": result.get("retrieved_contexts", []),
			"timings": result.get("timings", {})
		}
		
	except ImportError as e:
		# 의존성 누락
		return {
			"ok": False,
			"error": f"필수 라이브러리가 설치되지 않았습니다: {str(e)}"
		}
	except RuntimeError as e:
		# Milvus 연결 실패 등 런타임 오류
		error_msg = str(e)
		if "컬렉션" in error_msg or "Collection" in error_msg:
			return {
				"ok": False,
				"error": f"복리후생 데이터베이스 연결 실패: {error_msg}\n\n"
				         f"해결 방법:\n"
				         f"1. .env 파일에 MILVUS_HOST, MILVUS_PORT, MILVUS_COLLECTION_NAME을 확인하세요.\n"
				         f"2. Milvus 서버가 실행 중인지 확인하세요.\n"
				         f"3. 컬렉션이 생성되어 있는지 확인하세요."
			}
		return {
			"ok": False,
			"error": f"복리후생 RAG 처리 중 오류 발생: {error_msg}"
		}
	except Exception as e:
		return {
			"ok": False,
			"error": f"복리후생 RAG 처리 중 예상치 못한 오류 발생: {str(e)}"
		}

