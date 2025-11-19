"""회의실 에이전트 도메인 서비스 - Spring Boot 연동."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Mapping, Sequence

import httpx
from pymysql.err import IntegrityError

from meeting_room_agent.schemas import ReservationStatus
from meeting_room_agent.tools import DBPool

__all__: Sequence[str] = [
    "is_room_available",
    "reserve_room",
    "cancel_reservation",
    "update_reservation",
    "recommend_meeting_rooms",
    "fetch_reservations",
    "get_reservation",
]

LOGGER = logging.getLogger(__name__)

# 🆕 Spring Boot 서버 URL
SPRING_BOOT_URL = "http://localhost:8080"


# ===== 조회 기능 (Python DB 직접 조회) =====

async def is_room_available(room_id: int, start_time: datetime, end_time: datetime) -> bool:
    """특정 시간대에 회의실 예약 가능 여부를 확인한다."""
    
    if start_time >= end_time:
        raise ValueError("start_time은 end_time보다 빨라야 합니다.")

    query = """
        SELECT COUNT(*) AS cnt
        FROM meeting_room_reservation
        WHERE meeting_room_id = %s
          AND status = %s
          AND (start_time < %s AND end_time > %s)
    """

    rows = await DBPool.fetch(
        query,
        (room_id, ReservationStatus.CONFIRMED.value, end_time, start_time),
    )
    overlap_count = rows[0]["cnt"] if rows else 0
    LOGGER.debug(
        "회의실 예약 가능 여부 조회 (room_id=%d, start=%s, end=%s, overlap=%d)",
        room_id,
        start_time,
        end_time,
        overlap_count,
    )
    return overlap_count == 0


async def recommend_meeting_rooms(
    start_time: datetime,
    end_time: datetime,
    capacity: int,
    require_video: bool = False,
    building: str | None = None,
) -> list[dict[str, Any]]:
    """요구 조건을 기반으로 회의실 후보를 추천한다."""
    
    base_query = """
        SELECT mr.*
        FROM meeting_room AS mr
        WHERE mr.capacity >= %s
          AND (%s = 0 OR mr.has_video = 1)
          {building_clause}
          AND NOT EXISTS (
              SELECT 1
              FROM meeting_room_reservation AS r
              WHERE r.meeting_room_id = mr.id
                AND r.status = %s
                AND (r.start_time < %s AND r.end_time > %s)
          )
        ORDER BY mr.capacity ASC, mr.has_video DESC
        LIMIT 10
    """

    building_clause = ""
    params: list[Any] = [capacity, int(require_video)]
    if building:
        building_clause = "AND mr.building = %s"
        params.append(building)
    params.extend(
        [
            ReservationStatus.CONFIRMED.value,
            end_time,
            start_time,
        ]
    )

    query = base_query.format(building_clause=building_clause)
    rooms = await DBPool.fetch(query, params)
    LOGGER.info(
        "회의실 추천 결과 (start=%s, end=%s, capacity=%d, require_video=%s, building=%s, count=%d)",
        start_time,
        end_time,
        capacity,
        require_video,
        building,
        len(rooms),
    )
    return rooms


async def fetch_reservations(
    meeting_room_id: int | None,
    building: str | None,
    start_time: datetime,
    end_time: datetime,
) -> list[dict[str, Any]]:
    """지정된 조건에 맞는 회의실 예약 목록을 조회한다."""
    
    query = """
        SELECT
            r.id,
            mr.name,
            mr.building,
            r.title,
            r.start_time,
            r.end_time,
            r.status
        FROM meeting_room_reservation AS r
        JOIN meeting_room AS mr ON r.meeting_room_id = mr.id
        WHERE r.status = %s
          AND r.start_time < %s
          AND r.end_time > %s
          {room_clause}
          {building_clause}
        ORDER BY r.start_time ASC
    """

    params: list[Any] = [
        ReservationStatus.CONFIRMED.value,
        end_time,
        start_time,
    ]

    room_clause = ""
    if meeting_room_id is not None:
        room_clause = "AND r.meeting_room_id = %s"
        params.append(meeting_room_id)

    building_clause = ""
    if building:
        building_clause = "AND mr.building = %s"
        params.append(building)

    final_query = query.format(room_clause=room_clause, building_clause=building_clause)
    reservations = await DBPool.fetch(final_query, params)
    LOGGER.debug(
        "예약 조회 (room_id=%s, building=%s, start=%s, end=%s, count=%d)",
        meeting_room_id,
        building,
        start_time,
        end_time,
        len(reservations),
    )
    return reservations


# ===== 🆕 예약/취소 (Spring Boot API 호출) =====

async def reserve_room(payload: Mapping[str, Any]) -> int:
    """
    Spring Boot API를 호출하여 회의실 예약 생성
    
    Args:
        payload: 예약 정보
            - meeting_room_id (int)
            - employee_id (int)
            - title (str)
            - start_time (datetime)
            - end_time (datetime)
            - participants (int, optional)
            - description (str, optional)
    
    Returns:
        int: 생성된 예약 ID
    """
    
    required_keys = {"meeting_room_id", "employee_id", "title", "start_time", "end_time"}
    missing_keys = required_keys - payload.keys()
    if missing_keys:
        raise ValueError(f"필수 예약 정보가 누락되었습니다: {', '.join(sorted(missing_keys))}")

    start_time = payload["start_time"]
    end_time = payload["end_time"]
    
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime):
        raise ValueError("start_time과 end_time은 datetime 인스턴스여야 합니다.")
    if start_time >= end_time:
        raise ValueError("start_time은 end_time보다 빨라야 합니다.")

    # 🆕 Spring Boot API 호출
    request_data = {
        "meetingRoomId": payload["meeting_room_id"],
        "employeeId": payload["employee_id"],
        "title": payload["title"],
        "description": payload.get("description"),
        "startTime": start_time.isoformat(),
        "endTime": end_time.isoformat(),
        "participants": payload.get("participants"),
        "scheduleId": payload.get("schedule_id"),
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{SPRING_BOOT_URL}/api/reservations",
                json=request_data,
                timeout=10.0,
            )
            response.raise_for_status()
            result = response.json()
            
            LOGGER.info(
                "✅ Spring Boot 예약 생성 성공 (id=%s, title=%s)",
                result.get("id"),
                payload["title"],
            )
            return result.get("id", 0)
            
    except httpx.HTTPStatusError as exc:
        error_msg = exc.response.text
        LOGGER.error("❌ Spring Boot 예약 생성 실패: %s", error_msg)
        raise ValueError(f"예약 생성 실패: {error_msg}") from exc
    except Exception as exc:
        LOGGER.exception("❌ Spring Boot 호출 중 오류", exc_info=exc)
        raise ValueError(f"서버 연동 오류: {exc}") from exc


def _ensure_datetime(value: Any, field_name: str) -> datetime:
    """입력 값을 datetime으로 변환한다."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field_name}은 ISO8601 형식이어야 합니다.") from exc
    raise ValueError(f"{field_name}은 datetime 또는 ISO8601 문자열이어야 합니다.")


async def update_reservation(reservation_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    """
    Spring Boot API를 호출하여 예약 정보를 수정한다.
    
    Args:
        reservation_id: 수정 대상 예약 ID
        payload: 수정할 필드가 포함된 딕셔너리
    
    Returns:
        dict[str, Any]: 수정된 예약 응답
    """

    if reservation_id <= 0:
        raise ValueError("유효한 예약 ID를 입력해 주세요.")

    if not payload:
        raise ValueError("수정할 필드를 최소 한 개 이상 제공해 주세요.")

    mapping = {
        "meeting_room_id": "meetingRoomId",
        "employee_id": "employeeId",
        "schedule_id": "scheduleId",
        "title": "title",
        "description": "description",
        "participants": "participants",
        "status": "status",
    }

    request_data: dict[str, Any] = {}

    for source_key, target_key in mapping.items():
        if source_key in payload and payload[source_key] is not None:
            value = payload[source_key]
            if target_key == "status" and hasattr(value, "value"):
                value = value.value  # ReservationStatus Enum 대응
            request_data[target_key] = value

    # 시간 필드는 별도로 처리
    for source_key, target_key in (("start_time", "startTime"), ("end_time", "endTime")):
        if source_key in payload and payload[source_key] is not None:
            coerced = _ensure_datetime(payload[source_key], source_key)
            request_data[target_key] = coerced.isoformat()

    if not request_data:
        raise ValueError("전송할 수정 데이터가 없습니다.")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{SPRING_BOOT_URL}/api/reservations/{reservation_id}",
                json=request_data,
                timeout=10.0,
            )
            response.raise_for_status()
            result = response.json()
            LOGGER.info("✅ Spring Boot 예약 수정 성공 (id=%d)", reservation_id)
            return result

    except httpx.HTTPStatusError as exc:
        error_msg = exc.response.text
        LOGGER.error("❌ Spring Boot 예약 수정 실패: %s", error_msg)
        raise ValueError(f"예약 수정 실패: {error_msg}") from exc
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("❌ Spring Boot 호출 중 오류", exc_info=exc)
        raise ValueError(f"서버 연동 오류: {exc}") from exc


async def cancel_reservation(reservation_id: int, reason: str | None = None) -> int:
    """
    Spring Boot API를 호출하여 예약 취소
    
    Args:
        reservation_id: 취소할 예약 ID
        reason: 취소 사유
    
    Returns:
        int: 1 (성공), 0 (실패)
    """
    
    request_data = {"reason": reason} if reason else {}
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{SPRING_BOOT_URL}/api/reservations/{reservation_id}/cancel",
                json=request_data,
                timeout=10.0,
            )
            response.raise_for_status()
            
            LOGGER.info("✅ Spring Boot 예약 취소 성공 (id=%d)", reservation_id)
            return 1
            
    except httpx.HTTPStatusError as exc:
        error_msg = exc.response.text
        LOGGER.error("❌ Spring Boot 예약 취소 실패: %s", error_msg)
        
        # 404: 없는 예약, 409: 이미 취소된 예약
        if exc.response.status_code in (404, 409):
            return 0
        
        raise ValueError(f"예약 취소 실패: {error_msg}") from exc
    except Exception as exc:
        LOGGER.exception("❌ Spring Boot 호출 중 오류", exc_info=exc)
        raise ValueError(f"서버 연동 오류: {exc}") from exc


async def get_reservation(reservation_id: int) -> dict[str, Any]:
    """Spring Boot API를 통해 예약 단건을 조회한다."""

    if reservation_id <= 0:
        raise ValueError("유효한 예약 ID를 입력해 주세요.")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{SPRING_BOOT_URL}/api/reservations/{reservation_id}",
                timeout=10.0,
            )
            response.raise_for_status()
            result = response.json()
            LOGGER.info("✅ 예약 상세 조회 성공 (id=%d)", reservation_id)
            return result
    except httpx.HTTPStatusError as exc:
        error_msg = exc.response.text
        LOGGER.error("❌ 예약 상세 조회 실패: %s", error_msg)
        if exc.response.status_code == 404:
            raise ValueError("해당 예약을 찾을 수 없습니다.") from exc
        raise ValueError(f"예약 조회 실패: {error_msg}") from exc
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("❌ 예약 상세 조회 중 오류", exc_info=exc)
        raise ValueError(f"예약 조회 중 오류: {exc}") from exc