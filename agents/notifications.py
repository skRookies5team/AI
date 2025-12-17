from __future__ import annotations

import asyncio
from typing import Any, Dict

# 세션별 알림 큐 (SSE/WebSocket 등에서 소비)
_SESSION_QUEUES: Dict[str, asyncio.Queue] = {}


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


