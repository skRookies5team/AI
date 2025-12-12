from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
import html as html_module
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict
import imaplib
import email as email_pkg
from email.header import decode_header as _mime_decode_header
import base64
from datetime import datetime
from email.utils import parsedate_to_datetime
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from agents.notifications import publish_notification

logger = logging.getLogger(__name__)

# Redis 클라이언트 가져오기
try:
	from redis.asyncio import Redis as AsyncRedis
	REDIS_AVAILABLE = True
except ImportError:
	REDIS_AVAILABLE = False
	AsyncRedis = None


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
					# 이미 설정된 환경변수는 덮어쓰지 않음
					if k and k not in os.environ:
						os.environ[k] = v
	except Exception:
		# .env 읽기 실패는 치명적이지 않음
		pass


# 모듈 로드 시 .env 자동 로드
_load_env_file()


@dataclass
class EmailSummary:
	"""단일 이메일 요약 정보.

	Attributes:
		index: 메일 순번(1-based, 검색 결과 내 순서).
		subject: 메일 제목.
		from_addr: 발신자 표기.
		date: 원본 Date 헤더 값.
		snippet: 본문 일부 요약(최대 몇 줄).
	"""

	index: int
	subject: str
	from_addr: str
	date: str
	snippet: str


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
	"""환경 변수 안전 조회."""
	val = os.environ.get(name, default)
	return val


async def _extract_sender_info_with_llm(query: str) -> Dict[str, Optional[str]]:
	"""LLM을 사용하여 사용자 질의에서 발신자 이름과 이메일 주소를 추출.
	
	Args:
		query: 사용자 질의 문자열
		
	Returns:
		{"name": str or None, "email": str or None} 딕셔너리
	"""
	# 쿼리가 비어있으면 즉시 반환
	if not query or not query.strip():
		return {"name": None, "email": None}
	
	try:
		api_key = _get_env("OPENAI_API_KEY")
		if not api_key:
			# API 키가 없으면 정규식으로 fallback
			email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'
			emails = re.findall(email_pattern, query)
			return {
				"name": None,
				"email": emails[0].lower() if emails else None
			}
		
		llm = ChatOpenAI(
			model="gpt-4o-mini",
			temperature=0,
			api_key=api_key
		)
		
		prompt_template = ChatPromptTemplate.from_messages([
			("system", """당신은 이메일 검색 전문가입니다.
사용자의 질의에서 발신자 이름과 이메일 주소를 추출해주세요.

예시:
- "홍길동이 보낸 메일 찾아줘" → name: "홍길동", email: null
- "springriver@kw.ac.kr 이 보낸 메일" → name: null, email: "springriver@kw.ac.kr"
- "홍길동(springriver@kw.ac.kr)이 보낸 메일" → name: "홍길동", email: "springriver@kw.ac.kr"
- "csu030716@naver.com이 보낸 메일" → name: null, email: "csu030716@naver.com"

응답 형식 (JSON):
{{
  "name": "추출된 이름 또는 null",
  "email": "추출된 이메일 주소 또는 null"
}}

이메일 주소가 없으면 "email" 필드는 null로 반환하세요.
이름이 없으면 "name" 필드는 null로 반환하세요."""),
			("user", "다음 질의에서 발신자 이름과 이메일 주소를 추출해주세요:\n{{query}}")
		])
		
		chain = prompt_template | llm
		response = await chain.ainvoke({"query": query})
		
		response_text = response.content.strip()
		
		# JSON 파싱 시도
		try:
			# JSON 블록 추출 (```json ... ``` 또는 {...})
			json_match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
			if json_match:
				response_text = json_match.group(0)
			
			result = json.loads(response_text)
			
			# 결과 검증 및 정규화
			name = result.get("name")
			email = result.get("email")
			
			if name:
				name = name.strip()
				if not name or name.lower() in ["null", "none", ""]:
					name = None
			
			if email:
				email = email.strip().lower()
				if not email or email.lower() in ["null", "none", ""] or "@" not in email:
					email = None
			
			return {
				"name": name,
				"email": email
			}
		except (json.JSONDecodeError, KeyError) as e:
			logger.debug(f"LLM 응답 JSON 파싱 실패: {e}, 응답: {response_text}, 정규식으로 fallback")
			# JSON 파싱 실패 시 정규식으로 fallback
			email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'
			emails = re.findall(email_pattern, query)
			return {
				"name": None,
				"email": emails[0].lower() if emails else None
			}
		
	except Exception as e:
		logger.warning(f"LLM 발신자 정보 추출 실패: {e}, 정규식으로 fallback")
		# LLM 실패 시 정규식으로 fallback
		email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'
		emails = re.findall(email_pattern, query)
		return {
			"name": None,
			"email": emails[0].lower() if emails else None
		}


async def _extract_recipient_info_with_llm(query: str) -> Dict[str, Any]:
	"""LLM을 사용하여 사용자 질의에서 수신자 정보를 추출.
	
	이메일 작성 요청에서 수신자 이름, 이메일 주소, CC, BCC 등을 추출합니다.
	
	Args:
		query: 사용자 질의 문자열
		
	Returns:
		{
			"to_emails": List[str] - 수신자 이메일 주소 리스트,
			"to_names": List[str] - 수신자 이름 리스트 (없으면 빈 리스트),
			"cc": Optional[str] - 참조 이메일 주소,
			"bcc": Optional[str] - 숨은 참조 이메일 주소
		}
	"""
	# 쿼리가 비어있으면 즉시 반환
	if not query or not query.strip():
		return {"to_emails": [], "to_names": [], "cc": None, "bcc": None}

	# ✅ 우선순위 1: 쿼리에 이메일 주소가 있으면 LLM 없이 정규식으로 즉시 추출 (가장 안정적)
	email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'
	emails = re.findall(email_pattern, query)
	if emails:
		return {
			"to_emails": [e.strip().lower() for e in emails if e],
			"to_names": [],
			"cc": None,
			"bcc": None
		}
	
	try:
		api_key = _get_env("OPENAI_API_KEY")
		if not api_key:
			# API 키가 없으면 정규식으로 fallback
			return {
				"to_emails": [e.lower() for e in emails] if emails else [],
				"to_names": [],
				"cc": None,
				"bcc": None
			}
		
		llm = ChatOpenAI(
			model="gpt-4o-mini",
			temperature=0,
			api_key=api_key
		)
		
		prompt_template = ChatPromptTemplate.from_messages([
			("system", """당신은 이메일 작성 어시스턴트입니다.
사용자의 이메일 작성 요청에서 수신자 정보를 추출해주세요.

예시:
- "springriver@kw.ac.kr에게 내일 회의 메일 보내줘" → to_emails: ["springriver@kw.ac.kr"], to_names: []
- "홍길동, 김철수에게 메일 보내줘" → to_emails: [], to_names: ["홍길동", "김철수"] (이메일 주소는 없지만 이름은 추출)
- "csu030716@naver.com, pig121512141@gmail.com한테 내일 3시 회의 있다고 메일 보내줘" → to_emails: ["csu030716@naver.com", "pig121512141@gmail.com"], to_names: []
- "홍길동(springriver@kw.ac.kr), 김철수(kim@example.com)에게 보내줘" → to_emails: ["springriver@kw.ac.kr", "kim@example.com"], to_names: ["홍길동", "김철수"]

응답 형식 (JSON):
{{
  "to_emails": ["이메일1@example.com", "이메일2@example.com"] 또는 [],
  "to_names": ["이름1", "이름2"] 또는 [],
  "cc": "참조@example.com" 또는 null,
  "bcc": "숨은참조@example.com" 또는 null
}}

이메일 주소가 없으면 "to_emails"는 빈 배열로 반환하세요.
이름이 없으면 "to_names"는 빈 배열로 반환하세요."""),
			# ⚠️ 여기서는 {query}가 실제 변수입니다. ({{query}}로 쓰면 리터럴이 되어 LLM이 쿼리를 못 봄)
			("user", "다음 질의에서 수신자 정보를 추출해주세요:\n{query}")
		])
		
		chain = prompt_template | llm
		response = await chain.ainvoke({"query": query})
		
		response_text = response.content.strip()
		
		# JSON 파싱 시도
		try:
			# JSON 블록 추출 (```json ... ``` 또는 {...})
			json_match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
			if json_match:
				response_text = json_match.group(0)
			
			result = json.loads(response_text)
			
			# 결과 검증 및 정규화
			to_emails = result.get("to_emails", [])
			if not isinstance(to_emails, list):
				to_emails = []
			to_emails = [e.strip().lower() for e in to_emails if e and "@" in str(e)]
			
			to_names = result.get("to_names", [])
			if not isinstance(to_names, list):
				to_names = []
			to_names = [n.strip() for n in to_names if n]
			
			cc = result.get("cc")
			if cc:
				cc = cc.strip().lower() if "@" in str(cc) else None
			else:
				cc = None
			
			bcc = result.get("bcc")
			if bcc:
				bcc = bcc.strip().lower() if "@" in str(bcc) else None
			else:
				bcc = None

			# ✅ 안전장치: LLM이 빈 배열을 반환하면 정규식으로 보강 (LLM 파싱 성공 시 fallback이 안 타는 문제 방지)
			if not to_emails:
				emails = re.findall(email_pattern, query)
				to_emails = [e.strip().lower() for e in emails if e]
			
			return {
				"to_emails": to_emails,
				"to_names": to_names,
				"cc": cc,
				"bcc": bcc
			}
		except (json.JSONDecodeError, KeyError) as e:
			logger.debug(f"LLM 응답 JSON 파싱 실패: {e}, 응답: {response_text}, 정규식으로 fallback")
			# JSON 파싱 실패 시 정규식으로 fallback
			email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'
			emails = re.findall(email_pattern, query)
			return {
				"to_emails": [e.lower() for e in emails] if emails else [],
				"to_names": [],
				"cc": None,
				"bcc": None
			}
		
	except Exception as e:
		logger.warning(f"LLM 수신자 정보 추출 실패: {e}, 정규식으로 fallback")
		# LLM 실패 시 정규식으로 fallback
		email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}'
		emails = re.findall(email_pattern, query)
		return {
			"to_emails": [e.lower() for e in emails] if emails else [],
			"to_names": [],
			"cc": None,
			"bcc": None
		}


def _get_redis_client() -> Optional[AsyncRedis]:
	"""Redis 클라이언트 가져오기 (session_manager와 동일한 방식)."""
	if not REDIS_AVAILABLE:
		return None
	
	try:
		redis_host = _get_env("HOST") or _get_env("REDIS_HOST") or "localhost"
		redis_port_str = _get_env("REDIS_PORT") or "6379"
		redis_password = _get_env("REDIS_PASSWORD")  # _get_env 사용으로 변경
		redis_db_str = _get_env("REDIS_DB") or "0"
		
		try:
			redis_port = int(redis_port_str.strip())
		except (ValueError, AttributeError):
			redis_port = 6379
		
		try:
			redis_db = int(redis_db_str.strip())
		except (ValueError, AttributeError):
			redis_db = 0
		
		# 빈 문자열인 경우 None으로 처리
		if redis_password == "":
			redis_password = None
		
		# 비밀번호가 있으면 URL에 포함
		if redis_password:
			redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}"
		else:
			redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
		
		redis_client = AsyncRedis.from_url(redis_url, decode_responses=False)
		logger.info(f"Redis 클라이언트 초기화 완료: {redis_host}:{redis_port}/{redis_db} (비밀번호: {'설정됨' if redis_password else '없음'})")
		return redis_client
	except Exception as e:
		logger.error(f"Redis 클라이언트 초기화 실패: {e}")
		return None


class EmailRedisStorage:
	"""이메일 메타데이터를 Redis에 저장/조회하는 클래스.
	
	Redis 키 구조:
	- {session_id}_EMAIL:metadata:{email_id} - 메타데이터 해시
	- {session_id}_EMAIL:index:by_date - 날짜별 인덱스 (Sorted Set)
	- {session_id}_EMAIL:index:by_from:{from_email} - 발신자별 인덱스 (Set)
	- {session_id}_EMAIL:index:all - 전체 이메일 ID 목록 (Set)
	- {session_id}_EMAIL:sync:last_check - 마지막 동기화 시간
	"""
	
	def __init__(self, redis_client: AsyncRedis, session_id: str):
		self.redis = redis_client
		self.session_id = session_id
		self.email_key_prefix = f"{session_id}_EMAIL"
		self.metadata_ttl = 2592000  # 30일
	
	def _encode_msg_id(self, msg_id: bytes) -> str:
		"""IMAP 메시지 ID를 Redis 저장용 문자열로 변환."""
		return base64.b64encode(msg_id).decode('utf-8')
	
	def _decode_msg_id(self, encoded: str) -> bytes:
		"""Redis에서 가져온 문자열을 IMAP 메시지 ID로 변환."""
		return base64.b64decode(encoded.encode('utf-8'))
	
	async def save_email_metadata_batch(self, emails: List[Dict[str, Any]]) -> None:
		"""메타데이터 배치 저장 (Pipeline 사용).
		
		Args:
			emails: 메일 메타데이터 리스트 [{"email_id": str, "subject": str, "from_addr": str, "date": str, "snippet": str, "msg_id_bytes": bytes}]
		"""
		if not emails:
			return
		
		pipeline = self.redis.pipeline()
		
		for email_data in emails:
			email_id = email_data["email_id"]
			subject = email_data.get("subject", "")
			from_addr = email_data.get("from_addr", "")
			date = email_data.get("date", "")
			snippet = email_data.get("snippet", "")[:500]  # 최대 500자
			msg_id_bytes = email_data.get("msg_id_bytes", b"")
			
			# 날짜를 timestamp로 변환
			try:
				dt = parsedate_to_datetime(date)
				timestamp = int(dt.timestamp())
			except Exception:
				timestamp = int(datetime.now().timestamp())
			
			# 메타데이터 해시 저장
			metadata_key = f"{self.email_key_prefix}:metadata:{email_id}"
			pipeline.hset(metadata_key, mapping={
				b"subject": subject.encode('utf-8'),
				b"from_addr": from_addr.encode('utf-8'),
				b"date": date.encode('utf-8'),
				b"date_timestamp": str(timestamp).encode('utf-8'),
				b"snippet": snippet.encode('utf-8'),
				b"msg_id": self._encode_msg_id(msg_id_bytes).encode('utf-8'),
			})
			pipeline.expire(metadata_key, self.metadata_ttl)
			
			# 날짜별 인덱스 (Sorted Set)
			pipeline.zadd(f"{self.email_key_prefix}:index:by_date", {email_id: timestamp})
			
			# 발신자별 인덱스 (Set)
			from_index_key = f"{self.email_key_prefix}:index:by_from:{from_addr}"
			pipeline.sadd(from_index_key, email_id)
			pipeline.expire(from_index_key, self.metadata_ttl)
			
			# 전체 목록에 추가
			pipeline.sadd(f"{self.email_key_prefix}:index:all", email_id)
		
		await pipeline.execute()
	
	async def get_email_metadata(self, email_id: str) -> Optional[Dict[str, str]]:
		"""이메일 메타데이터 조회."""
		metadata_key = f"{self.email_key_prefix}:metadata:{email_id}"
		data = await self.redis.hgetall(metadata_key)
		
		if not data:
			return None
		
		# bytes를 문자열로 변환
		result = {}
		for k, v in data.items():
			key = k.decode('utf-8') if isinstance(k, bytes) else k
			val = v.decode('utf-8') if isinstance(v, bytes) else v
			result[key] = val
		
		return result
	
	async def search_emails_by_keyword(self, keyword: str, limit: int = 100) -> List[Dict[str, Any]]:
		"""키워드로 이메일 검색 (Redis에서).
		
		Args:
			keyword: 검색 키워드
			limit: 최대 결과 수
			
		Returns:
			검색된 이메일 메타데이터 리스트
		"""
		results = []
		keyword_lower = keyword.lower()
		
		# 모든 이메일 ID 순회 (날짜 역순, 전체 검색)
		email_ids = await self.redis.zrevrange(
			f"{self.email_key_prefix}:index:by_date",
			0,
			-1  # 전체 가져오기
		)
		
		if not email_ids:
			return []
		
		# Pipeline으로 메타데이터 일괄 조회
		pipeline = self.redis.pipeline()
		for email_id_bytes in email_ids:
			email_id = email_id_bytes.decode('utf-8') if isinstance(email_id_bytes, bytes) else email_id_bytes
			pipeline.hgetall(f"{self.email_key_prefix}:metadata:{email_id}")
		
		metadata_list = await pipeline.execute()
		
		# 결과 파싱 및 필터링
		for email_id_bytes, metadata in zip(email_ids, metadata_list):
			if not metadata:
				continue
			
			email_id = email_id_bytes.decode('utf-8') if isinstance(email_id_bytes, bytes) else email_id_bytes
			
			# bytes를 문자열로 변환
			metadata_dict = {}
			for k, v in metadata.items():
				key = k.decode('utf-8') if isinstance(k, bytes) else k
				val = v.decode('utf-8') if isinstance(v, bytes) else v
				metadata_dict[key] = val
			
			# 키워드 매칭 (제목, 본문 요약, 발신자)
			subject = metadata_dict.get("subject", "").lower()
			snippet = metadata_dict.get("snippet", "").lower()
			from_addr = metadata_dict.get("from_addr", "").lower()
			
			if (keyword_lower in subject or 
				keyword_lower in snippet or 
				keyword_lower in from_addr):
				results.append({
					"id": email_id,
					**metadata_dict
				})
				
				if len(results) >= limit:
					break
		
		return results
	
	async def search_emails_by_from_address(self, email_address: str, limit: int = 100) -> List[Dict[str, Any]]:
		"""발신자 이메일 주소로 검색 (발신자 필드에서만 검색).
		
		Args:
			email_address: 발신자 이메일 주소 (예: "csu030716@naver.com")
			limit: 최대 결과 수
			
		Returns:
			검색된 이메일 메타데이터 리스트
		"""
		results = []
		email_address_lower = email_address.lower()
		
		# 모든 이메일 ID 순회 (날짜 역순, 전체 검색)
		email_ids = await self.redis.zrevrange(
			f"{self.email_key_prefix}:index:by_date",
			0,
			-1  # 전체 가져오기
		)
		
		if not email_ids:
			return []
		
		# Pipeline으로 메타데이터 일괄 조회
		pipeline = self.redis.pipeline()
		for email_id_bytes in email_ids:
			email_id = email_id_bytes.decode('utf-8') if isinstance(email_id_bytes, bytes) else email_id_bytes
			pipeline.hgetall(f"{self.email_key_prefix}:metadata:{email_id}")
		
		metadata_list = await pipeline.execute()
		
		# 결과 파싱 및 필터링 (발신자 필드에서만 검색)
		for email_id_bytes, metadata in zip(email_ids, metadata_list):
			if not metadata:
				continue
			
			email_id = email_id_bytes.decode('utf-8') if isinstance(email_id_bytes, bytes) else email_id_bytes
			
			# bytes를 문자열로 변환
			metadata_dict = {}
			for k, v in metadata.items():
				key = k.decode('utf-8') if isinstance(k, bytes) else k
				val = v.decode('utf-8') if isinstance(v, bytes) else v
				metadata_dict[key] = val
			
			# 발신자 필드에서만 검색 (이메일 주소가 포함되어 있는지 확인)
			from_addr = metadata_dict.get("from_addr", "").lower()
			
			# from_addr에서 이메일 주소 추출하여 비교
			# "홍길동 <csu030716@naver.com>" 같은 형식도 처리
			if email_address_lower in from_addr:
				results.append({
					"id": email_id,
					**metadata_dict
				})
				
				if len(results) >= limit:
					break
		
		return results
	
	async def search_emails_by_sender_name(self, sender_name: str, limit: int = 100) -> List[Dict[str, Any]]:
		"""발신자 이름으로 이메일 검색 (발신자 필드에서만 검색).
		
		Args:
			sender_name: 발신자 이름 (예: "홍길동")
			limit: 최대 결과 수
			
		Returns:
			검색된 이메일 메타데이터 리스트
		"""
		results = []
		sender_name_lower = sender_name.lower()
		
		# 모든 이메일 ID 순회 (날짜 역순, 전체 검색)
		email_ids = await self.redis.zrevrange(
			f"{self.email_key_prefix}:index:by_date",
			0,
			-1  # 전체 가져오기
		)
		
		if not email_ids:
			return []
		
		# Pipeline으로 메타데이터 일괄 조회
		pipeline = self.redis.pipeline()
		for email_id_bytes in email_ids:
			email_id = email_id_bytes.decode('utf-8') if isinstance(email_id_bytes, bytes) else email_id_bytes
			pipeline.hgetall(f"{self.email_key_prefix}:metadata:{email_id}")
		
		metadata_list = await pipeline.execute()
		
		# 결과 파싱 및 필터링 (발신자 필드에서만 검색)
		for email_id_bytes, metadata in zip(email_ids, metadata_list):
			if not metadata:
				continue
			
			email_id = email_id_bytes.decode('utf-8') if isinstance(email_id_bytes, bytes) else email_id_bytes
			
			# bytes를 문자열로 변환
			metadata_dict = {}
			for k, v in metadata.items():
				key = k.decode('utf-8') if isinstance(k, bytes) else k
				val = v.decode('utf-8') if isinstance(v, bytes) else v
				metadata_dict[key] = val
			
			# 발신자 필드에서 이름 검색
			# from_addr 형식: "홍길동 <email@example.com>" 또는 "email@example.com"
			from_addr = metadata_dict.get("from_addr", "").lower()
			
			# 이름 부분 추출 (괄호 앞 또는 < 앞 부분)
			name_part = from_addr.split('<')[0].strip()
			# 특수문자 제거 (괄호, 따옴표 등)
			name_part = re.sub(r'["\'()\[\]{}]', '', name_part).strip()
			
			# 이름이 from_addr에 포함되어 있는지 확인
			if sender_name_lower in from_addr:
				# 정확한 이름 매칭 우선 (공백 단위로 구분)
				if (sender_name_lower == name_part or 
					sender_name_lower in name_part.split() or
					any(part == sender_name_lower for part in name_part.split())):
					results.append({
						"id": email_id,
						**metadata_dict
					})
					
					if len(results) >= limit:
						break
		
		return results
	
	async def get_all_email_ids(self, limit: int = 1000) -> List[str]:
		"""모든 이메일 ID 조회 (날짜 역순)."""
		email_ids = await self.redis.zrevrange(
			f"{self.email_key_prefix}:index:by_date",
			0,
			limit - 1
		)
		return [eid.decode('utf-8') if isinstance(eid, bytes) else eid for eid in email_ids]
	
	async def has_synced_data(self) -> bool:
		"""Redis에 동기화된 데이터가 있는지 확인."""
		count = await self.redis.zcard(f"{self.email_key_prefix}:index:by_date")
		return count > 0


async def _ensure_uid_mode(storage: "EmailRedisStorage") -> None:
	"""세션 이메일 캐시를 UID 기반으로 운용하도록 마커를 세팅하고, 구버전(시퀀스 번호) 캐시가 있으면 정리.

	- 마커 키: {session_id}_EMAIL:sync:id_mode = "uid"
	- 마커가 없거나 uid가 아니면, prefix 전체를 삭제하고 uid로 마커를 세팅합니다.
	  (sequence-number 기반 캐시가 남아있으면 UID 비교 시 중복 저장/오탐 가능)
	"""
	mode_key = f"{storage.email_key_prefix}:sync:id_mode"
	try:
		existing = await storage.redis.get(mode_key)
		existing_mode = existing.decode("utf-8") if isinstance(existing, (bytes, bytearray)) else (existing or "")
	except Exception:
		existing_mode = ""

	if existing_mode == "uid":
		return

	# 기존 캐시 제거(세션 단위로만)
	try:
		pattern = f"{storage.email_key_prefix}:*"
		cursor = 0
		while True:
			cursor, keys = await storage.redis.scan(cursor, match=pattern, count=500)
			if keys:
				pipe = storage.redis.pipeline()
				for k in keys:
					pipe.delete(k)
				await pipe.execute()
			if cursor == 0:
				break
		print(f"[EMAIL UID] 기존 캐시 정리 완료: prefix={storage.email_key_prefix}")
	except Exception as e:
		print(f"[EMAIL UID] 기존 캐시 정리 실패(무시): {e}")

	try:
		await storage.redis.set(mode_key, "uid")
	except Exception:
		pass
	
	async def update_sync_time(self) -> None:
		"""마지막 동기화 시간 업데이트."""
		timestamp = int(datetime.now().timestamp())
		await self.redis.set(f"{self.email_key_prefix}:sync:last_check", str(timestamp))
	
	async def get_last_sync_time(self) -> Optional[int]:
		"""마지막 동기화 시간 조회."""
		timestamp = await self.redis.get(f"{self.email_key_prefix}:sync:last_check")
		if timestamp:
			return int(timestamp.decode('utf-8') if isinstance(timestamp, bytes) else timestamp)
		return None


def _extract_keywords(query: str) -> str:
	"""사용자 질의에서 핵심 키워드만 추출 (mail_test.py 방식 참고).
	
	이메일 주소가 포함된 경우 이메일 주소를 보존합니다.
	
	Args:
		query: 사용자 질의 전체 문자열. 예) "육아휴직 신청 관련 메일 찾아줘" 또는 "csu030716@naver.com 이 보낸 메일"
		
	Returns:
		핵심 키워드 문자열. 예) "육아휴직" 또는 "csu030716@naver.com"
	"""
	q = query.strip()
	if not q:
		return ""
	
	# 이메일 주소 추출 (우선 처리)
	email_pattern = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?=\s|,|$|[^\w.@-])'
	emails = re.findall(email_pattern, q)
	
	# 불용어 셋
	stopwords = {
		"메일", "이메일", "메일에서", "이메일에서", "관련", "에서", "으로", "로", "을", "를", "은", "는", "이", "가", "에",
		"에게", "의", "도", "및", "그리고", "또는", "search", "find", "찾아", "찾아줘", "찾기", "검색", "해줘", "주세요",
		"관련된", "내용", "안내해줘", "알려줘", "관련해", "관련한", "신청", "안내", "공지",
		"보낸", "보여줘", "보여", "보내", "전송", "작성",
	}
	
	# 이메일 주소가 있으면 특별 처리
	if emails:
		# 이메일 주소는 그대로 보존
		keywords = emails.copy()
		
		# 나머지 부분에서 키워드 추출 (이메일 주소 제외)
		remaining = q
		for email in emails:
			remaining = remaining.replace(email, " ").strip()
		
		# 토큰화 및 정제
		try:
			raw_tokens = [t for t in re.split(r"[\s\W]+", remaining) if t]
		except Exception:
			raw_tokens = remaining.split()
		
		# 불용어 제거 후 2글자 이상인 토큰만 추출
		for tok in raw_tokens:
			tok = tok.strip()
			if tok and tok not in stopwords and len(tok) >= 2:
				keywords.append(tok)
		
		# 이메일 주소만 있거나, 이메일 주소 + 다른 키워드가 있으면 결합
		if keywords:
			return " ".join(keywords)
		else:
			return emails[0] if emails else q
	
	# 이메일 주소가 없는 경우 기존 로직
	try:
		raw_tokens = [t for t in re.split(r"[\s\W]+", q) if t]
	except Exception:
		raw_tokens = q.split()
	
	keywords = [t for t in (tok.strip() for tok in raw_tokens) if t and t not in stopwords and len(t) >= 2]
	if not keywords:
		# 의미 토큰이 없으면 원본 반환 (전체 검색)
		return q
	# 핵심 키워드들을 공백으로 결합 (mail_test.py처럼 단순 매칭을 위해)
	return " ".join(keywords)


def _decode_header_value(value: Optional[str]) -> str:
	"""헤더 값 디코드 및 정리."""
	if not value:
		return ""
	return value.strip()

def _decode_mime_header(value: Optional[str]) -> str:
	"""MIME 인코딩된 헤더(Subject, From 등)를 안전하게 디코드."""
	if not value:
		return ""
	try:
		parts = _mime_decode_header(value)
	except Exception:
		return value
	result = ""
	for part, enc in parts:
		try:
			if isinstance(part, (bytes, bytearray)):
				if enc:
					result += part.decode(enc, errors="replace")
				else:
					result += part.decode("utf-8", errors="replace")
			else:
				result += str(part)
		except Exception:
			try:
				result += str(part)
			except Exception:
				pass
	return result.strip()

def _html_to_text(html: str) -> str:
	"""아주 단순한 HTML → 텍스트 변환."""
	try:
		# 엔티티 복원
		text = html_module.unescape(html)
		# 스크립트/스타일 제거
		text = re.sub(r"<(script|style)[\\s\\S]*?>[\\s\\S]*?</\\1>", " ", text, flags=re.IGNORECASE)
		# 태그 제거
		text = re.sub(r"<[^>]+>", " ", text)
		# 공백 정리
		text = re.sub(r"[ \\t\\r\\f\\v]+", " ", text)
		return text.strip()
	except Exception:
		return html


def _get_email_body_and_attachments(msg: email_pkg.message.Message) -> Tuple[str, str, List[Dict[str, Any]]]:
	"""이메일 본문 추출 (텍스트 및 HTML) 및 첨부 파일 추출.
	
	Args:
		msg: email.message.Message 객체
		
	Returns:
		(text_body, html_body, attachments) 튜플
	"""
	text_body = ""
	html_body = ""
	attachments = []
	
	if msg.is_multipart():
		for part in msg.walk():
			content_type = part.get_content_type()
			content_disposition = str(part.get("Content-Disposition", ""))
			
			# 첨부 파일 처리
			if "attachment" in content_disposition.lower() or part.get_filename():
				try:
					filename = part.get_filename()
					if filename:
						# 파일명 디코딩
						decoded_filename = _decode_mime_header(filename)
						payload = part.get_payload(decode=True)
						
						if payload:
							attachments.append({
								"filename": decoded_filename,
								"content_type": content_type,
								"data": payload,
								"size": len(payload)
							})
				except Exception:
					pass
				continue
			
			# 본문 처리
			if content_type == "text/plain":
				try:
					payload = part.get_payload(decode=True)
					if payload:
						charset = part.get_content_charset() or 'utf-8'
						text_body += payload.decode(charset, errors='ignore')
				except Exception:
					pass
			elif content_type == "text/html":
				try:
					payload = part.get_payload(decode=True)
					if payload:
						charset = part.get_content_charset() or 'utf-8'
						html_body += payload.decode(charset, errors='ignore')
				except Exception:
					pass
	else:
		# 단일 파트 메시지
		try:
			payload = msg.get_payload(decode=True)
			if payload:
				charset = msg.get_content_charset() or 'utf-8'
				content_type = msg.get_content_type()
				if content_type == "text/html":
					html_body = payload.decode(charset, errors='ignore')
				else:
					text_body = payload.decode(charset, errors='ignore')
		except Exception:
			text_body = str(msg.get_payload())
	
	# HTML 본문이 없으면 텍스트 본문을 HTML로 변환
	if not html_body and text_body:
		html_body = f"<pre style='white-space: pre-wrap; font-family: inherit;'>{html_module.escape(text_body)}</pre>"
	
	return text_body, html_body, attachments


def format_email_html(
	subject: str,
	sender: str,
	date: str,
	text_body: str,
	html_body: str,
	attachments: List[Dict[str, Any]]
) -> str:
	"""이메일을 HTML 형식으로 포맷팅.
	
	Args:
		subject: 제목
		sender: 발신자
		date: 날짜
		text_body: 텍스트 본문
		html_body: HTML 본문
		attachments: 첨부 파일 리스트 [{"filename": str, "content_type": str, "data": bytes, "size": int}]
	
	Returns:
		HTML 형식의 이메일 내용
	"""
	html_content = f"""
	<div style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px;">
		<div style="border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; margin-bottom: 20px;">
			<h2 style="margin: 0; color: #333;">{html_module.escape(subject)}</h2>
		</div>
		
		<div style="margin-bottom: 20px; color: #666;">
			<p style="margin: 5px 0;"><strong>발신자:</strong> {html_module.escape(sender)}</p>
			<p style="margin: 5px 0;"><strong>날짜:</strong> {html_module.escape(date)}</p>
		</div>
	"""
	
	# 첨부 파일 표시
	if attachments:
		html_content += """
		<div style="margin-bottom: 20px; padding: 10px; background-color: #f5f5f5; border-radius: 5px;">
			<p style="margin: 0 0 10px 0; font-weight: bold;">📎 첨부 파일:</p>
			<ul style="margin: 0; padding-left: 20px;">
		"""
		for att in attachments:
			filename = html_module.escape(att.get("filename", "알 수 없음"))
			size_kb = att.get("size", 0) / 1024
			html_content += f'<li>{filename} ({size_kb:.1f} KB)</li>'
		html_content += """
			</ul>
		</div>
		"""
	
	# 본문 표시
	html_content += """
		<div style="margin-bottom: 20px; padding: 15px; background-color: #fafafa; border-radius: 5px;">
	"""
	
	if html_body:
		html_content += html_body
	else:
		html_content += f"<pre style='white-space: pre-wrap; font-family: inherit;'>{html_module.escape(text_body)}</pre>"
	
	html_content += """
		</div>
	</div>
	"""
	
	return html_content


def _message_to_text(msg, max_lines: Optional[int] = 5) -> str:
	"""이메일 메시지에서 text/plain 본문을 추출 (mail_test.py의 extract_text_from_msg 참고).
	
	중복을 방지하기 위해 text/plain을 우선하고, 없으면 text/html을 텍스트로 변환합니다.
	"""
	body_parts: List[str] = []
	
	if msg.is_multipart():
		# multipart 메일: text/plain 우선, 없으면 text/html 사용
		plain_text = None
		html_text = None
		
		for part in msg.walk():
			ct = part.get_content_type()
			disp = str(part.get("Content-Disposition") or "")
			
			# 첨부 파일은 제외
			if "attachment" in disp.lower():
				continue
			
			# text/plain 우선 처리
			if ct == "text/plain" and plain_text is None:
				try:
					payload = part.get_payload(decode=True)
					if payload:
						charset = part.get_content_charset()
						try:
							if charset:
								text = payload.decode(charset, errors="replace")
							else:
								text = payload.decode("utf-8", errors="replace")
							plain_text = text
						except Exception:
							try:
								text = payload.decode("utf-8", errors="replace")
								plain_text = text
							except Exception:
								pass
				except Exception:
					pass
			
			# text/html은 plain이 없을 때만 사용
			elif ct == "text/html" and html_text is None and plain_text is None:
				try:
					payload = part.get_payload(decode=True)
					if payload:
						charset = part.get_content_charset()
						try:
							if charset:
								html = payload.decode(charset, errors="replace")
							else:
								html = payload.decode("utf-8", errors="replace")
							html_text = _html_to_text(html)
						except Exception:
							try:
								html = payload.decode("utf-8", errors="replace")
								html_text = _html_to_text(html)
							except Exception:
								pass
				except Exception:
					pass
		
		# 우선순위: plain_text > html_text
		if plain_text:
			body_parts.append(plain_text)
		elif html_text:
			body_parts.append(html_text)
	else:
		# 단일 파트 메일
		try:
			payload = msg.get_payload(decode=True)
			if payload:
				charset = msg.get_content_charset()
				try:
					if charset:
						text = payload.decode(charset, errors="replace")
					else:
						text = payload.decode("utf-8", errors="replace")
				except Exception:
					text = payload.decode("utf-8", errors="replace")
				
				ct = msg.get_content_type()
				if ct == "text/html":
					body_parts.append(_html_to_text(text))
				else:
					body_parts.append(text)
		except Exception:
			# 페이로드가 문자열인 경우
			try:
				payload = msg.get_payload()
				if isinstance(payload, str):
					ct = msg.get_content_type()
					if ct == "text/html":
						body_parts.append(_html_to_text(payload))
					else:
						body_parts.append(payload)
			except Exception:
				pass
	
	full_text = "\n".join(body_parts).strip()
	if max_lines is None:
		return full_text
	return "\n".join(full_text.splitlines()[: max_lines]).strip()


#
# IMAP 구현
#
# 세션별 이메일 검색 결과 캐시 (멀티턴 대화 지원)
# DEPRECATED: 이제 Redis 숏텀 메모리(OrchestratorState.email_search_context)를 사용합니다.
# 하위 호환성을 위해 유지되지만, 새 코드에서는 사용하지 마세요.
# {session_id: {"ids": [], "items": [], "index_map": {}, "last_keyword": ""}}
_EMAIL_SEARCH_CACHE: Dict[str, Dict[str, Any]] = {}
_EMAIL_SESSION_ID: Optional[str] = None


def _set_email_session_id(session_id: Optional[str] = None) -> str:
	"""이메일 검색 세션 ID 설정."""
	global _EMAIL_SESSION_ID
	if session_id:
		_EMAIL_SESSION_ID = session_id
	elif not _EMAIL_SESSION_ID:
		import uuid
		_EMAIL_SESSION_ID = str(uuid.uuid4())
	return _EMAIL_SESSION_ID


def _get_email_cache(session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	"""세션별 이메일 검색 결과 캐시 가져오기.
	
	Redis 숏텀 메모리(OrchestratorState)를 우선 사용하고,
	없으면 로컬 캐시를 사용합니다 (하위 호환성).
	
	Args:
		session_id: 세션 ID
		state: OrchestratorState (선택적, Redis에서 가져온 state)
	
	Returns:
		이메일 검색 캐시 딕셔너리
	"""
	# Redis 숏텀 메모리(OrchestratorState)에서 가져오기 (우선)
	if state and "email_search_context" in state and state["email_search_context"]:
		return state["email_search_context"]
	
	# 하위 호환성: 로컬 캐시 사용 (deprecated)
	sid = _set_email_session_id(session_id)
	if sid not in _EMAIL_SEARCH_CACHE:
		_EMAIL_SEARCH_CACHE[sid] = {
			"ids": [],
			"items": [],
			"index_map": {},
			"last_keyword": ""
		}
	return _EMAIL_SEARCH_CACHE[sid]


def _save_email_cache(cache_data: Dict[str, Any], session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	"""세션별 이메일 검색 결과 캐시 저장.
	
	Redis 숏텀 메모리(OrchestratorState)에 저장할 수 있도록
	업데이트된 state를 반환합니다.
	
	Args:
		cache_data: 저장할 캐시 데이터
		session_id: 세션 ID
		state: OrchestratorState (선택적, Redis에 저장할 state)
	
	Returns:
		업데이트된 state (state가 전달된 경우) 또는 None
	"""
	# Redis 숏텀 메모리(OrchestratorState)에 저장 (우선)
	if state is not None:
		updated_state = state.copy()
		updated_state["email_search_context"] = cache_data
		return updated_state
	
	# 하위 호환성: 로컬 캐시 사용 (deprecated)
	sid = _set_email_session_id(session_id)
	_EMAIL_SEARCH_CACHE[sid] = cache_data
	return None

def _imap_connect() -> imaplib.IMAP4_SSL:
	"""IMAP 서버에 연결 (mail_test.py 방식 참고)."""
	# mail_test.py: host = "imap.naver.com", port = 993
	host = _get_env("IMAP_HOST") or "imap.naver.com"
	port = int(_get_env("IMAP_PORT") or "993")
	user = _get_env("NAVER_EMAIL") or ""
	if not user:
		nid = _get_env("NAVER_ID") or ""
		if nid:
			user = f"{nid}@naver.com"
	pwd = _get_env("NAVER_APP_PASSWORD") or ""
	if not user or not pwd:
		raise RuntimeError(
			"IMAP 접속 정보가 필요합니다. .env 파일에 NAVER_EMAIL, NAVER_APP_PASSWORD를 설정해 주세요."
		)
	m = imaplib.IMAP4_SSL(host, port)
	m.login(user, pwd)
	m.select("INBOX")
	return m

def _imap_fetch_msg(m: imaplib.IMAP4_SSL, msg_id: bytes) -> email_pkg.message.Message:
	st, msg_data = m.fetch(msg_id, "(RFC822)")
	if st != "OK" or not msg_data or not msg_data[0]:
		raise RuntimeError("메일을 가져오지 못했습니다.")
	return email_pkg.message_from_bytes(msg_data[0][1])

def _extract_summary_from_msg(idx: int, msg: email_pkg.message.Message) -> EmailSummary:
	subject = _decode_mime_header(msg.get("Subject"))
	from_addr = _decode_mime_header(msg.get("From"))
	date = _decode_header_value(msg.get("Date"))
	snippet = _message_to_text(msg, max_lines=5)
	return EmailSummary(index=idx, subject=subject or "(제목 없음)", from_addr=from_addr, date=date, snippet=snippet)


async def sync_emails_to_redis(
	session_id: str,
	limit: int = 1000
) -> Dict[str, Any]:
	"""IMAP에서 최근 메일 메타데이터를 가져와 Redis에 저장.
	
	Args:
		session_id: 세션 ID
		limit: 가져올 최대 메일 개수 (기본 1000)
	
	Returns:
		동기화 결과 {"ok": bool, "count": int, "message": str}
	"""
	redis_client = _get_redis_client()
	if not redis_client:
		return {"ok": False, "count": 0, "message": "Redis 클라이언트를 가져올 수 없습니다."}
	
	storage = EmailRedisStorage(redis_client, session_id)

	# UID 모드 보장(구버전 캐시 정리)
	await _ensure_uid_mode(storage)

	# 이미 동기화된 데이터가 있는지 확인 (UID 모드에서만 의미 있음)
	if await storage.has_synced_data():
		count = await redis_client.zcard(f"{session_id}_EMAIL:index:by_date")
		return {"ok": True, "count": count, "message": f"이미 {count}개의 메일이 동기화되어 있습니다."}
	
	# IMAP 연결
	host = _get_env("IMAP_HOST") or "imap.naver.com"
	port = int(_get_env("IMAP_PORT") or "993")
	user = _get_env("NAVER_EMAIL") or ""
	if not user:
		nid = _get_env("NAVER_ID") or ""
		if nid:
			user = f"{nid}@naver.com"
	pwd = _get_env("NAVER_APP_PASSWORD") or ""
	
	if not user or not pwd:
		return {"ok": False, "count": 0, "message": "IMAP 접속 정보가 필요합니다."}
	
	mail = imaplib.IMAP4_SSL(host, port)
	try:
		mail.login(user, pwd)
		mail.select("INBOX")
		
		# UID 기반으로 최근 메일 UID 목록 가져오기
		status, data = mail.uid("search", None, "ALL")
		if status != "OK" or not data or not data[0]:
			return {"ok": False, "count": 0, "message": "메일 목록을 가져올 수 없습니다."}
		
		mail_ids_bytes = data[0].split()  # UID bytes
		if not mail_ids_bytes:
			return {"ok": False, "count": 0, "message": "메일함이 비어있습니다."}
		
		# 최신 메일부터 (역순)
		mail_ids_bytes.reverse()
		mail_ids_bytes = mail_ids_bytes[:limit]
		
		# 배치 단위로 헤더만 가져오기 (100개씩)
		batch_size = 100
		all_emails = []
		
		for batch_start in range(0, len(mail_ids_bytes), batch_size):
			batch_ids = mail_ids_bytes[batch_start:batch_start + batch_size]
			
			# UID 기반으로 헤더만 FETCH (BODY.PEEK[HEADER])
			for msg_id_bytes in batch_ids:
				try:
					uid_str = msg_id_bytes.decode("utf-8") if isinstance(msg_id_bytes, (bytes, bytearray)) else str(msg_id_bytes)
					status, msg_data = mail.uid("fetch", uid_str, "(BODY.PEEK[HEADER])")
					if status != "OK" or not msg_data or not msg_data[0]:
						continue
					
					# 헤더 파싱
					header_data = msg_data[0][1]
					if isinstance(header_data, bytes):
						# 헤더만으로 메시지 객체 생성
						msg = email_pkg.message_from_bytes(header_data)
					else:
						continue
					
					# 메타데이터 추출
					subject = _decode_mime_header(msg.get("Subject", ""))
					from_addr = _decode_mime_header(msg.get("From", ""))
					date = _decode_header_value(msg.get("Date", ""))
					
					# 본문 요약은 헤더만으로는 불가능하므로 제목과 발신자만 사용
					snippet = f"{subject[:200]}" if subject else ""
					
					# 이메일 ID는 UID 사용
					email_id = uid_str
					
					all_emails.append({
						"email_id": email_id,
						"subject": subject or "(제목 없음)",
						"from_addr": from_addr or "(발신자 없음)",
						"date": date or "",
						"snippet": snippet,
						# msg_id_bytes에도 UID bytes 저장 (향후 UID FETCH에 사용)
						"msg_id_bytes": msg_id_bytes
					})
				except Exception as e:
					logger.debug(f"메일 헤더 파싱 중 오류 (ID: {msg_id_bytes}): {e}")
					continue
		
		# Redis에 배치 저장
		if all_emails:
			await storage.save_email_metadata_batch(all_emails)
			await storage.update_sync_time()
		
		return {"ok": True, "count": len(all_emails), "message": f"{len(all_emails)}개의 메일 메타데이터를 동기화했습니다."}
	
	except Exception as e:
		logger.error(f"이메일 동기화 실패: {e}")
		return {"ok": False, "count": 0, "message": f"동기화 실패: {str(e)}"}
	
	finally:
		try:
			mail.logout()
		except Exception:
			pass


# =========================
# Email reception detection
# =========================

# 세션별 백그라운드 이메일 체커(중복 실행 방지)
_ACTIVE_EMAIL_CHECKERS: Dict[str, asyncio.Task] = {}


async def _check_new_emails_for_session(session_id: str, recent_limit: int = 100) -> List[Dict[str, Any]]:
	"""IMAP 최신 recent_limit개 vs Redis(해당 세션) 최신 recent_limit개 비교 → 새 메일 메타데이터 반환."""
	redis_client = _get_redis_client()
	if not redis_client:
		logger.debug(f"[EMAIL CHECK][{session_id}] Redis 연결 실패")
		return []

	storage = EmailRedisStorage(redis_client, session_id)
	redis_by_date_key = f"{storage.email_key_prefix}:index:by_date"

	# Redis 최신 recent_limit개
	if not await storage.redis.exists(redis_by_date_key):
		logger.debug(f"[EMAIL CHECK][{session_id}] Redis 동기화 데이터 없음 ({redis_by_date_key})")
		return []

	redis_recent_ids_raw = await storage.redis.zrevrange(redis_by_date_key, 0, recent_limit - 1)
	redis_recent_ids = {
		(x.decode("utf-8") if isinstance(x, (bytes, bytearray)) else str(x))
		for x in redis_recent_ids_raw
	}

	# IMAP 연결
	host = _get_env("IMAP_HOST") or "imap.naver.com"
	port = int(_get_env("IMAP_PORT") or "993")
	user = _get_env("NAVER_EMAIL") or ""
	if not user:
		nid = _get_env("NAVER_ID") or ""
		if nid:
			user = f"{nid}@naver.com"
	pwd = _get_env("NAVER_APP_PASSWORD") or ""
	if not user or not pwd:
		logger.debug(f"[EMAIL CHECK][{session_id}] IMAP 접속 정보 없음")
		return []

	mail = imaplib.IMAP4_SSL(host, port)
	try:
		mail.login(user, pwd)
		mail.select("INBOX")

		# UID 기반 검색
		status, data = mail.uid("search", None, "ALL")
		if status != "OK" or not data or not data[0]:
			return []

		ids = data[0].split()
		if not ids:
			return []

		ids.reverse()
		ids = ids[:recent_limit]

		new_pairs: List[Tuple[str, bytes]] = []
		for uid_bytes in ids:
			email_id = uid_bytes.decode("utf-8") if isinstance(uid_bytes, (bytes, bytearray)) else str(uid_bytes)
			if email_id not in redis_recent_ids:
				new_pairs.append((email_id, uid_bytes))

		if not new_pairs:
			return []

		new_emails: List[Dict[str, Any]] = []
		for email_id, uid_bytes in new_pairs:
			try:
				st, msg_data = mail.uid("fetch", email_id, "(BODY.PEEK[HEADER])")
				if st != "OK" or not msg_data or not msg_data[0]:
					continue

				header_data = msg_data[0][1]
				if not isinstance(header_data, (bytes, bytearray)):
					continue

				msg = email_pkg.message_from_bytes(header_data)

				subject = _decode_mime_header(msg.get("Subject", ""))
				from_addr = _decode_mime_header(msg.get("From", ""))
				date = _decode_header_value(msg.get("Date", ""))

				new_emails.append(
					{
						"email_id": email_id,
						"subject": subject or "(제목 없음)",
						"from_addr": from_addr or "(발신자 없음)",
						"date": date or "",
						"msg_id_bytes": uid_bytes,
					}
				)
			except Exception as e:
				logger.debug(f"[EMAIL CHECK][{session_id}] 새 메일 파싱 실패: {e}")
				continue

		return new_emails
	except Exception as e:
		logger.error(f"[EMAIL CHECK][{session_id}] 체크 실패: {e}")
		# 콘솔 스팸 방지: 에러는 logger로만 남김
		return []
	finally:
		try:
			mail.logout()
		except Exception:
			pass


async def _background_email_checker_for_session(session_id: str, check_interval: int = 30) -> None:
	"""현재 세션(session_id)만 주기 체크 + 카운트다운 + 새 메일 Redis 저장."""
	# 첫 실행에서 Redis에 1000개 동기화가 없으면 동기화(기존 기능 활용)
	try:
		redis_client = _get_redis_client()
		if redis_client:
			storage = EmailRedisStorage(redis_client, session_id)
			if not await storage.has_synced_data():
				sync_result = await sync_emails_to_redis(session_id, limit=1000)
	except Exception as e:
		logger.debug(f"[EMAIL CHECK][{session_id}] 초기 동기화 중 오류(무시): {e}")

	while True:
		try:
			for remaining in range(check_interval, 0, -1):
				print(f"\r[EMAIL CHECK][{session_id}] 다음 체크까지 {remaining}초", end="", flush=True)
				await asyncio.sleep(1)
			print()
			print(f"[EMAIL CHECK][{session_id}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - IMAP 최신 100개 체크 시작")

			new_emails = await _check_new_emails_for_session(session_id, recent_limit=100)
			if not new_emails:
				continue

			# 새 메일은 프론트로만 발행 (콘솔 출력 X)
			email_data: Dict[str, Dict[str, str]] = {}

			# IMAP에서 본문까지 가져와 content 채우기 (UID 기반)
			host = _get_env("IMAP_HOST") or "imap.naver.com"
			port = int(_get_env("IMAP_PORT") or "993")
			user = _get_env("NAVER_EMAIL") or ""
			if not user:
				nid = _get_env("NAVER_ID") or ""
				if nid:
					user = f"{nid}@naver.com"
			pwd = _get_env("NAVER_APP_PASSWORD") or ""

			mail = None
			if user and pwd:
				try:
					mail = imaplib.IMAP4_SSL(host, port)
					mail.login(user, pwd)
					mail.select("INBOX")
				except Exception as e:
					logger.error(f"[EMAIL CHECK][{session_id}] IMAP 연결 실패: {e}")
					mail = None

			try:
				for idx, e in enumerate(new_emails, 1):
					email_info: Dict[str, str] = {
						"subject": e.get("subject", "(제목 없음)"),
						"from": e.get("from_addr", "(발신자 없음)"),
						"date": e.get("date", ""),
						"content": "",
					}

					if mail:
						try:
							uid_str = str(e.get("email_id") or "")
							st, msg_data = mail.uid("fetch", uid_str, "(RFC822)")
							if st == "OK" and msg_data and msg_data[0]:
								msg = email_pkg.message_from_bytes(msg_data[0][1])
								text_body, html_body, attachments = _get_email_body_and_attachments(msg)
								if html_body:
									email_info["content"] = html_body
								elif text_body:
									email_info["content"] = text_body
								else:
									email_info["content"] = e.get("subject", "")
							else:
								email_info["content"] = e.get("subject", "")
						except Exception as fetch_err:
							logger.error(f"[EMAIL CHECK][{session_id}] 본문 fetch 실패 (uid={e.get('email_id')}): {fetch_err}")
							email_info["content"] = e.get("subject", "")
					else:
						email_info["content"] = e.get("subject", "")

					email_data[str(idx)] = email_info
			finally:
				if mail:
					try:
						mail.logout()
					except Exception:
						pass

			summary_message = f"📧 새 메일 도착: {len(new_emails)}개"
			json_result = {summary_message: email_data}
			# 프론트로 알림 발행
			publish_notification(
				session_id,
				{
					"type": "email_new",
					"session_id": session_id,
					"data": json_result,
					"timestamp": datetime.now().isoformat(),
				},
			)

			redis_client = _get_redis_client()
			if redis_client:
				storage = EmailRedisStorage(redis_client, session_id)
				await storage.save_email_metadata_batch(
					[
						{
							"email_id": e["email_id"],
							"subject": e["subject"],
							"from_addr": e["from_addr"],
							"date": e["date"],
							"snippet": (e["subject"] or "")[:200],
							"msg_id_bytes": e["msg_id_bytes"],
						}
						for e in new_emails
					]
				)
		except asyncio.CancelledError:
			# 종료 시에는 1줄만 남기고 깔끔하게 종료
			print(f"[EMAIL CHECK][{session_id}] 체커 종료(cancelled)")
			raise
		except Exception as e:
			logger.error(f"[EMAIL CHECK][{session_id}] 백그라운드 오류: {e}")
			# 콘솔 스팸 방지: 에러는 logger로만 남김
			await asyncio.sleep(60)


def start_email_checker_for_session(session_id: str, check_interval: int = 30) -> None:
	"""첫 채팅에서 알아낸 '현재 세션'만 이메일 체커 시작(중복 실행 방지)."""
	task = _ACTIVE_EMAIL_CHECKERS.get(session_id)
	if task and not task.done():
		return
	_ACTIVE_EMAIL_CHECKERS[session_id] = asyncio.create_task(
		_background_email_checker_for_session(session_id, check_interval=check_interval)
	)
	print(f"📧 [EMAIL CHECK][{session_id}] 체커 시작 (interval={check_interval}s)")


async def stop_all_email_checkers() -> None:
	"""서버 종료 시 모든 이메일 체커 태스크를 취소(cancel)하고 정리합니다."""
	if not _ACTIVE_EMAIL_CHECKERS:
		return

	tasks = []
	for sid, task in list(_ACTIVE_EMAIL_CHECKERS.items()):
		if task and not task.done():
			task.cancel()
			tasks.append(task)

	# 태스크 종료 대기 (예외는 무시)
	if tasks:
		try:
			await asyncio.gather(*tasks, return_exceptions=True)
		except Exception:
			pass

	_ACTIVE_EMAIL_CHECKERS.clear()

def has_pending_email_results(session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> bool:
	"""최근 검색 결과가 캐시에 남아 있는지 여부 (세션별).

	Args:
		session_id: 세션 ID (없으면 현재 세션 사용)
		state: OrchestratorState (선택적, Redis에서 가져온 state)
	"""
	cache = _get_email_cache(session_id, state)
	# 결과(ids)가 있거나, 이전 검색 키워드(last_keyword)가 있으면 연속 검색 가능
	return bool(cache.get("ids") or cache.get("last_keyword"))


async def email_search(
	user_text: str, 
	session_id: Optional[str] = None, 
	state: Optional[Dict[str, Any]] = None,
	from_address: Optional[str] = None,  # master_graph에서 추출한 발신자 주소
	date_range: Optional[Dict[str, str]] = None  # master_graph에서 추출한 날짜 범위 {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
) -> Dict[str, Any]:
	"""사용자 질의로 IMAP 메일함을 검색하여 관련 메일 요약/상세를 반환합니다.

	Args:
		user_text: 사용자 질의 (예: "육아휴직 신청 관련 메일을 찾아줘")
		session_id: 세션 ID (멀티턴 대화 지원)
		state: OrchestratorState (선택적, Redis 숏텀 메모리에서 가져온 state)
		from_address: master_graph에서 추출한 발신자 주소 (있으면 우선 사용)
		date_range: master_graph에서 추출한 날짜 범위 (있으면 우선 사용)

	.env 지원:
		현재 작업 디렉터리의 .env 파일을 자동으로 읽어 환경변수에 주입하려 시도합니다(존재 시).
		이미 설정된 환경변수는 .env로 덮어쓰지 않습니다.

	환경 변수:
		IMAP_HOST (기본 imap.naver.com), IMAP_PORT (기본 993)
		EMAIL_USERNAME 또는 NAVER_EMAIL
		EMAIL_PASSWORD 또는 NAVER_APP_PASSWORD
		IMAP_MAX_FETCH: 최근 몇 개 메시지 범위를 검색할지 (기본 1000)

	Args:
		user_text: 예) "육아휴직 신청 관련 메일을 찾아줘"

	Returns:
		표준화된 dict 결과. format_result와 호환되는 구조.
	
	Note:
		.env 파일의 NAVER_EMAIL, NAVER_APP_PASSWORD는 모듈 로드 시 자동으로 환경변수에 주입됩니다.
	"""
	# 로깅 설정 (EMAIL_DEBUG=true 시 활성화)
	def _mask_user(u: str) -> str:
		if not u:
			return ""
		if "@" in u:
			local, domain = u.split("@", 1)
			if len(local) <= 2:
				local_mask = local[:1] + "*" * max(0, len(local) - 1)
			else:
				local_mask = local[:2] + "*" * (len(local) - 2)
			return f"{local_mask}@{domain}"
		return u[:2] + "*" * max(0, len(u) - 2)

	email_debug = (_get_env("EMAIL_DEBUG", "false") or "false").lower() in {"1", "true", "yes", "y"}
	if email_debug:
		if not logger.handlers:
			handler = logging.StreamHandler()
			formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
			handler.setFormatter(formatter)
			logger.addHandler(handler)
		logger.setLevel(logging.DEBUG)

	query = user_text.strip()

	if email_debug:
		# 질의 토큰 간단 추출(로그용)
		try:
			q_tokens = [t for t in re.split(r"[\\s\\W]+", query) if t]
		except Exception:
			q_tokens = query.split()
		logger.info(
			"email_search(IMAP) start: tokens=%s imap_limit=%s",
			q_tokens,
			_get_env("IMAP_MAX_FETCH", "1000") or "1000",
		)

	# Redis 기반 검색으로 변경
	if not session_id:
		return {"ok": False, "error": "세션 ID가 필요합니다."}
	
	redis_client = _get_redis_client()
	if not redis_client:
		return {"ok": False, "error": "Redis 클라이언트를 가져올 수 없습니다."}
	
	storage = EmailRedisStorage(redis_client, session_id)
	
	# 초기 동기화 확인 (Redis에 메타데이터가 없으면 동기화)
	if not await storage.has_synced_data():
		print(f"[EMAIL SEARCH] 초기 동기화 시작 (세션: {session_id})")
		sync_result = await sync_emails_to_redis(session_id, limit=1000)
		if not sync_result.get("ok"):
			return {"ok": False, "error": f"이메일 동기화 실패: {sync_result.get('message', '알 수 없는 오류')}"}
		print(f"[EMAIL SEARCH] 초기 동기화 완료: {sync_result.get('count', 0)}개 메일")
	
	# 발신자 정보 추출 (master_graph에서 전달받은 정보 우선 사용)
	sender_name = None
	sender_email = None
	
	if from_address:
		# master_graph에서 추출한 발신자 주소 사용
		print(f"[EMAIL SEARCH] master_graph에서 추출한 발신자 주소 사용: {from_address}")
		sender_email = from_address.lower().strip()
	else:
		# master_graph에서 정보가 없으면 LLM으로 추출
		sender_info = await _extract_sender_info_with_llm(query)
		sender_name = sender_info.get("name")
		sender_email = sender_info.get("email")
	
	# 날짜 범위 처리 (master_graph에서 전달받은 정보 우선 사용)
	date_range_timestamps = None
	if date_range:
		# master_graph에서 추출한 날짜 범위를 timestamp로 변환
		start_date_str = date_range.get("start")
		end_date_str = date_range.get("end")
		if start_date_str and end_date_str:
			try:
				# "YYYY-MM-DD" 형식을 datetime으로 변환
				start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
				end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
				# 하루의 끝 시간으로 설정 (23:59:59)
				end_dt = end_dt.replace(hour=23, minute=59, second=59)
				
				date_range_timestamps = {
					"start_timestamp": int(start_dt.timestamp()),
					"end_timestamp": int(end_dt.timestamp())
				}
				print(f"[EMAIL SEARCH] master_graph에서 추출한 날짜 범위 사용: {start_date_str} ~ {end_date_str}")
			except ValueError as e:
				logger.warning(f"날짜 범위 파싱 실패: {e}")
	
	# 검색 로직 (날짜 범위 우선, 그 다음 발신자, 마지막 키워드)
	if date_range_timestamps:
		# 날짜 범위 검색
		start_ts = date_range_timestamps["start_timestamp"]
		end_ts = date_range_timestamps["end_timestamp"]
		
		# 날짜 범위로 검색 (Redis Sorted Set 사용)
		email_ids = await storage.redis.zrangebyscore(
			f"{storage.email_key_prefix}:index:by_date",
			min=start_ts,
			max=end_ts,
			withscores=False
		)
		
		if not email_ids:
			return {
				"ok": True,
				"message": f"📭 해당 날짜 범위({date_range.get('start')} ~ {date_range.get('end')})에 메일을 찾지 못했습니다.",
				"rows": [],
				"items": [],
				"email_ids": [],
				"last_search_keyword": None
			}
		
		# Pipeline으로 메타데이터 일괄 조회
		pipeline = storage.redis.pipeline()
		for email_id_bytes in email_ids:
			email_id = email_id_bytes.decode('utf-8') if isinstance(email_id_bytes, bytes) else email_id_bytes
			pipeline.hgetall(f"{storage.email_key_prefix}:metadata:{email_id}")
		
		metadata_list = await pipeline.execute()
		
		# 결과 파싱
		date_results = []
		for email_id_bytes, metadata in zip(email_ids, metadata_list):
			if not metadata:
				continue
			
			email_id = email_id_bytes.decode('utf-8') if isinstance(email_id_bytes, bytes) else email_id_bytes
			metadata_dict = {}
			for k, v in metadata.items():
				key = k.decode('utf-8') if isinstance(k, bytes) else k
				val = v.decode('utf-8') if isinstance(v, bytes) else v
				metadata_dict[key] = val
			
			date_results.append({
				"id": email_id,
				**metadata_dict
			})
		
		# 발신자로 추가 필터링 (있는 경우)
		if sender_email:
			results = [r for r in date_results if sender_email.lower() in r.get("from_addr", "").lower()]
			search_keyword = f"{sender_email} ({date_range.get('start')} ~ {date_range.get('end')})"
		elif sender_name:
			results = [r for r in date_results if sender_name.lower() in r.get("from_addr", "").lower()]
			search_keyword = f"{sender_name} ({date_range.get('start')} ~ {date_range.get('end')})"
		else:
			# 날짜 범위 검색인 경우, 날짜 관련 키워드는 필터링하지 않음
			# "어제", "오늘", "저번주" 등은 날짜를 나타내는 것이지 검색 키워드가 아님
			
			# 키워드 추출
			extracted_keyword = _extract_keywords(query)
			
			# 날짜 관련 키워드 목록
			date_related_terms = {"어제", "오늘", "내일", "저번주", "지난주", "이번주", "다음주", "온", "날", "일에", "보여줘", "보여", "알려줘", "찾아줘", "메일", "이메일"}
			
			# 추출된 키워드가 날짜 관련 용어뿐인지 확인
			if not extracted_keyword:
				# 키워드가 없으면 모든 날짜 범위 결과 반환
				results = date_results
				search_keyword = f"{date_range.get('start')} ~ {date_range.get('end')}"
			else:
				keyword_lower = extracted_keyword.lower()
				keyword_tokens = keyword_lower.split()
				
				# 키워드가 날짜 관련 용어만 포함하는지 확인
				is_only_date_query = all(
					any(date_term in token for date_term in date_related_terms)
					for token in keyword_tokens
				) or any(date_term in keyword_lower for date_term in date_related_terms if len(date_term) >= 2)
				
				if is_only_date_query:
					# 날짜만 요청한 경우: 모든 날짜 범위 결과 반환
					results = date_results
					search_keyword = f"{date_range.get('start')} ~ {date_range.get('end')}"
				else:
					# 실제 검색 키워드가 있는 경우: 필터링 (예: "어제 프로젝트 관련 메일")
					results = []
					for r in date_results:
						subject = r.get("subject", "").lower()
						snippet = r.get("snippet", "").lower()
						from_addr = r.get("from_addr", "").lower()
						if (keyword_lower in subject or keyword_lower in snippet or keyword_lower in from_addr):
							results.append(r)
					search_keyword = extracted_keyword
	elif sender_email:
		# 이메일 주소가 있는 경우: 발신자 이메일로 검색
		print(f"[EMAIL SEARCH] 발신자 이메일 검색: {sender_email}")
		results = await storage.search_emails_by_from_address(sender_email, limit=100)
		search_keyword = sender_email  # 메시지 표시용
	elif sender_name:
		# 이름만 있는 경우: 발신자 이름으로 검색
		print(f"[EMAIL SEARCH] 발신자 이름 검색: {sender_name}")
		results = await storage.search_emails_by_sender_name(sender_name, limit=100)
		search_keyword = sender_name  # 메시지 표시용
	else:
		# 이름/이메일이 없는 경우: 일반 키워드 검색
		search_keyword = _extract_keywords(query)
		if not search_keyword:
			search_keyword = query
		print(f"[EMAIL SEARCH] 키워드 검색: {search_keyword}")
		results = await storage.search_emails_by_keyword(search_keyword, limit=100)
	
	if not results:
		return {
			"ok": True,
			"message": f"📭 '{search_keyword}' 관련 메일을 찾지 못했습니다.\n\n💡 다른 키워드로 검색해보세요.",
			"rows": [],
			"items": [],
			"email_ids": [],
			"last_search_keyword": search_keyword
		}
	
	# 검색 결과 포맷팅 및 상세 내용 가져오기
	items = []
	email_data = {}  # JSON 형식으로 저장할 데이터
	
	# IMAP 연결 준비
	host = _get_env("IMAP_HOST") or "imap.naver.com"
	port = int(_get_env("IMAP_PORT") or "993")
	user = _get_env("NAVER_EMAIL") or ""
	if not user:
		nid = _get_env("NAVER_ID") or ""
		if nid:
			user = f"{nid}@naver.com"
	pwd = _get_env("NAVER_APP_PASSWORD") or ""
	
	mail = None
	if user and pwd:
		try:
			mail = imaplib.IMAP4_SSL(host, port)
			mail.login(user, pwd)
			mail.select("INBOX")
		except Exception as e:
			logger.error(f"IMAP 연결 실패: {e}")
			mail = None
	
	# 각 검색 결과에 대해 상세 내용 가져오기
	for idx, result in enumerate(results, 1):
			email_id = result.get("id")
			items.append({
				"title": result.get("subject", "(제목 없음)"),
				"from": result.get("from_addr", "(발신자 없음)"),
				"date": result.get("date", ""),
				"snippet": result.get("snippet", ""),
				"index": str(idx),
				"email_id": email_id,
				"actual_index": idx - 1
			})
			
			# 기본 메타데이터
			email_info = {
				"subject": result.get("subject", "(제목 없음)"),
				"from": result.get("from_addr", "(발신자 없음)"),
				"date": result.get("date", ""),
				"content": ""  # 본문 내용
			}
			
			# IMAP에서 전체 본문 가져오기
			if mail:
				try:
					# msg_id 디코딩
					msg_id_encoded = result.get("msg_id", "")
					if msg_id_encoded:
						msg_id_bytes = storage._decode_msg_id(msg_id_encoded)

						# 전체 메일 가져오기 (UID 기반)
						uid_str = msg_id_bytes.decode("utf-8") if isinstance(msg_id_bytes, (bytes, bytearray)) else str(msg_id_bytes)
						status, msg_data = mail.uid("fetch", uid_str, "(RFC822)")
						if status == "OK" and msg_data and msg_data[0]:
							msg = email_pkg.message_from_bytes(msg_data[0][1])
							
							# 본문 및 첨부 파일 추출
							text_body, html_body, attachments = _get_email_body_and_attachments(msg)
							
							# 본문 내용 설정 (HTML이 있으면 HTML, 없으면 텍스트)
							if html_body:
								email_info["content"] = html_body
							elif text_body:
								email_info["content"] = text_body
							else:
								email_info["content"] = result.get("snippet", "")
						else:
							# 메일을 가져올 수 없는 경우 스니펫 사용
							email_info["content"] = result.get("snippet", "")
				except Exception as e:
					logger.error(f"메일 상세 내용 가져오기 실패 (ID: {email_id}): {e}")
					email_info["content"] = result.get("snippet", "")
			else:
				# IMAP 연결 실패 시 스니펫 사용
				email_info["content"] = result.get("snippet", "")
			
			# 번호를 키로 사용하여 저장
			email_data[str(idx)] = email_info
	
	# IMAP 연결 종료
	if mail:
		try:
			mail.logout()
		except Exception:
			pass
	
	# 기존 메시지 형식으로 최상위 키 생성
	summary_message = f"📧 검색 결과: '{search_keyword}' 관련 메일 {len(results)}개"
	
	# JSON 형식 결과 생성
	json_result = {
		summary_message: email_data  # 기존 메시지를 최상위 키로 사용
	}
	
	# 검색 결과를 email_search_context에 저장
	cache = _get_email_cache(session_id, state)
	cache["ids"] = [item["email_id"] for item in items]
	cache["items"] = items
	index_map = {idx + 1: idx for idx in range(len(items))}
	cache["index_map"] = index_map
	cache["last_keyword"] = query
	updated_state = _save_email_cache(cache, session_id, state)
	if updated_state and state:
		state.update(updated_state)
	
	# email_search_context에서 마지막 검색 키워드 가져오기
	cache = _get_email_cache(session_id, state)
	last_search_keyword = cache.get("last_keyword", search_keyword)
	
	return {
		"ok": True,
		"message": json.dumps(json_result, ensure_ascii=False, indent=2),  # JSON 문자열로 변환
		"json": True,  # JSON 형식임을 표시
		"data": json_result,  # 파싱된 JSON 데이터
		"rows": items,
		"items": items,
		"email_ids": [item.get("email_id") for item in items if item.get("email_id")],
		"last_search_keyword": last_search_keyword
	}


async def email_search_async(user_text: str, session_id: Optional[str] = None, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	"""비동기 래퍼: email_search는 이미 비동기 함수이므로 직접 호출."""
	return await email_search(user_text, session_id=session_id, state=state)


