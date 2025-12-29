from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Union

# 세션별 알림 큐 (SSE/WebSocket 등에서 소비)
_SESSION_QUEUES: Dict[str, asyncio.Queue] = {}

# employee_id 별 알림 큐 (SSE에서 소비)
_EMPLOYEE_QUEUES: Dict[int, asyncio.Queue] = {}


def get_notification_queue(session_id: str) -> asyncio.Queue:
	"""세션별 알림 큐를 가져옵니다."""
	q = _SESSION_QUEUES.get(session_id)
	if q is None:
		# 무한정 커지지 않게 제한 (가득 차면 오래된 알림을 버림)
		q = asyncio.Queue(maxsize=200)
		_SESSION_QUEUES[session_id] = q
	return q


def publish_notification(session_id: str, payload: Dict[str, Any]) -> None:
	"""세션별 알림 큐에 이벤트를 발행합니다(비동기 작업에서 호출 가능)."""
	q = get_notification_queue(session_id)
	try:
		q.put_nowait(payload)
	except asyncio.QueueFull:
		# 오래된 이벤트 1개를 버리고 최신 이벤트를 넣음
		try:
			_ = q.get_nowait()
		except Exception:
			pass
		try:
			q.put_nowait(payload)
		except Exception:
			# 그래도 실패하면 조용히 무시
			pass


def get_employee_notification_queue(employee_id: int) -> asyncio.Queue:
	"""employee_id 별 알림 큐를 가져옵니다."""
	q = _EMPLOYEE_QUEUES.get(employee_id)
	if q is None:
		q = asyncio.Queue(maxsize=500)
		_EMPLOYEE_QUEUES[employee_id] = q
	return q


async def _get_redis():
	"""SessionManager가 가진 redis 클라이언트를 재사용합니다."""
	try:
		from agents.session_manager import get_session_manager
		sm = await get_session_manager()
		return getattr(sm, "redis", None) if sm else None
	except Exception:
		return None


def _zkey(employee_id: int) -> str:
	return f"notif:emp:{employee_id}:z"


def _hkey(employee_id: int, notif_id: str) -> str:
	return f"notif:emp:{employee_id}:h:{notif_id}"


async def persist_employee_notification(employee_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
	"""알림을 Redis에 영속 저장하고, 저장된 형태(enriched)를 반환합니다."""
	notif_id = uuid.uuid4().hex
	ts_ms = int(time.time() * 1000)
	enriched: Dict[str, Any] = {
		"id": notif_id,
		"employee_id": employee_id,
		"timestamp_ms": ts_ms,
		**payload,
	}

	redis = await _get_redis()
	if redis:
		try:
			pipe = redis.pipeline()
			pipe.zadd(_zkey(employee_id), {notif_id: ts_ms})
			pipe.hset(
				_hkey(employee_id, notif_id),
				mapping={
					"payload": json.dumps(enriched, ensure_ascii=False),
					"read": "0",
					"created_at": str(ts_ms),
				},
			)
			# 최근 200개만 유지
			pipe.zremrangebyrank(_zkey(employee_id), 0, -201)
			await pipe.execute()
		except Exception:
			# 영속 저장 실패는 실시간 큐 전달을 막지 않음
			pass

	return enriched


async def list_employee_notifications(employee_id: int, limit: int = 50, unread_only: bool = False) -> List[Dict[str, Any]]:
	redis = await _get_redis()
	if not redis:
		return []

	out: List[Dict[str, Any]] = []
	try:
		raw_ids = await redis.zrevrange(_zkey(employee_id), 0, max(0, limit - 1))
	except Exception:
		return []

	for raw in raw_ids:
		notif_id = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
		try:
			data = await redis.hgetall(_hkey(employee_id, notif_id))
		except Exception:
			continue
		if not data:
			continue

		# decode_responses=False 환경을 고려
		read_raw = data.get(b"read") if any(isinstance(k, (bytes, bytearray)) for k in data.keys()) else data.get("read")
		read_str = read_raw.decode() if isinstance(read_raw, (bytes, bytearray)) else str(read_raw or "0")
		is_read = read_str == "1"
		if unread_only and is_read:
			continue

		payload_raw = data.get(b"payload") if any(isinstance(k, (bytes, bytearray)) for k in data.keys()) else data.get("payload")
		payload_str = payload_raw.decode() if isinstance(payload_raw, (bytes, bytearray)) else str(payload_raw or "{}")
		try:
			item = json.loads(payload_str)
		except Exception:
			item = {"id": notif_id, "employee_id": employee_id, "raw": payload_str}
		item["read"] = is_read
		out.append(item)

	return out


async def mark_employee_notifications_read(employee_id: int, ids: List[str]) -> int:
	redis = await _get_redis()
	if not redis:
		return 0
	if not ids:
		return 0
	try:
		pipe = redis.pipeline()
		for notif_id in ids:
			pipe.hset(_hkey(employee_id, notif_id), "read", "1")
		await pipe.execute()
		return len(ids)
	except Exception:
		return 0


async def clear_employee_notifications(employee_id: int) -> None:
	redis = await _get_redis()
	if not redis:
		return
	try:
		raw_ids = await redis.zrange(_zkey(employee_id), 0, -1)
		pipe = redis.pipeline()
		pipe.delete(_zkey(employee_id))
		for raw in raw_ids:
			notif_id = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
			pipe.delete(_hkey(employee_id, notif_id))
		await pipe.execute()
	except Exception:
		return


async def publish_employee_notification(employee_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
	"""employee_id 별로 (1) 실시간 큐 전달 + (2) Redis 영속 저장."""
	# 영속 저장(enriched로 id/timestamp 추가)
	enriched = await persist_employee_notification(employee_id, payload)

	# 실시간 큐로 push
	q = get_employee_notification_queue(employee_id)
	try:
		q.put_nowait(enriched)
	except asyncio.QueueFull:
		try:
			_ = q.get_nowait()
		except Exception:
			pass
		try:
			q.put_nowait(enriched)
		except Exception:
			pass
	return enriched


