"""MariaDB 백엔드 연동 서비스."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

try:
    from sqlalchemy import text  # type: ignore[import]
    from sqlalchemy.exc import SQLAlchemyError  # type: ignore[import]
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine  # type: ignore[import]
except ImportError as exc:  # pragma: no cover
    raise ImportError("SQLAlchemy 설치가 필요합니다. pyproject.toml 의존성을 확인하세요.") from exc


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수에서 값을 읽고, 공백이면 기본값을 사용한다."""
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped or default


class DatabaseService:
    """MariaDB와 직접 통신하는 스케줄 도메인 서비스."""

    _session_ctx: ContextVar[Optional[AsyncSession]] = ContextVar("schedule_db_session", default=None)

    def __init__(self) -> None:
        """비동기 SQLAlchemy 엔진과 세션 팩토리를 구성한다."""
        username = _get_env("DB_USERNAME", "root")
        password = _get_env("DB_PASSWORD", "")
        host = _get_env("DB_HOST") or _get_env("HOST", "127.0.0.1")
        port = _get_env("DB_PORT", "3306")
        db_name = _get_env("DB_NAME", "ai_agent")

        self._engine: AsyncEngine = create_async_engine(
            f"mysql+asyncmy://{username}:{password}@{host}:{port}/{db_name}",
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
        )

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _session(self, *, transactional: bool = False) -> AsyncSession:
        """현재 트랜잭션이 있으면 재사용하고, 없으면 새 세션을 생성한다."""
        bound = self._session_ctx.get()
        if bound is not None:
            yield bound
            return

        async with self._session_factory() as session:
            if transactional:
                async with session.begin():
                    yield session
            else:
                yield session

    async def _ensure_event_metadata_table(self) -> None:
        """event_metadata 테이블이 없으면 생성한다."""
        create_sql = """
        CREATE TABLE IF NOT EXISTS event_metadata (
            gcal_event_id VARCHAR(128) PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            meeting_room_id INT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
        """
        async with self._session(transactional=True) as session:
            await session.execute(text(create_sql))

    # ------------------------------------------------------------------
    # 일정 관련
    # ------------------------------------------------------------------

    async def get_schedules(
        self,
        employee_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        keyword: Optional[str] = None,
        schedule_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """`schedule` 테이블에서 일정 목록을 조회한다."""
        clauses: List[str] = []
        params: Dict[str, Any] = {}

        if employee_id is not None:
            clauses.append("employee_id = :employee_id")
            params["employee_id"] = employee_id
        if start_date is not None:
            clauses.append("start_time >= :start_time")
            params["start_time"] = start_date
        if end_date is not None:
            clauses.append("end_time <= :end_time")
            params["end_time"] = end_date
        if keyword:
            clauses.append("(title LIKE :keyword OR description LIKE :keyword)")
            params["keyword"] = f"%{keyword}%"
        if schedule_types:
            clauses.append("schedule_type IN :schedule_types")
            params["schedule_types"] = tuple(schedule_types)

        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)

        sql = f"""
        SELECT
            schedule_id,
            employee_id,
            title,
            description,
            start_time,
            end_time,
            schedule_type,
            location,
            alert_time,
            status,
            created_at,
            updated_at
        FROM schedule
        {where_sql}
        ORDER BY start_time ASC;
        """

        async with self._session() as session:
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]

    async def get_event_metadata(self, event_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Google Calendar 이벤트 ID별 메타데이터를 조회한다."""
        ids = list(event_ids)
        if not ids:
            return {}

        await self._ensure_event_metadata_table()

        placeholders = ", ".join(f":id_{idx}" for idx, _ in enumerate(ids))
        params = {f"id_{idx}": value for idx, value in enumerate(ids)}

        sql = f"""
        SELECT gcal_event_id, user_id, meeting_room_id, created_at, updated_at
        FROM event_metadata
        WHERE gcal_event_id IN ({placeholders});
        """

        async with self._session() as session:
            result = await session.execute(text(sql), params)
            return {row["gcal_event_id"]: dict(row) for row in result.mappings().all()}

    async def save_event_metadata(self, metadata: Dict[str, Any]) -> bool:
        """Google Calendar 이벤트 메타데이터를 upsert 한다."""
        gcal_id = metadata.get("gcal_event_id")
        user_id = metadata.get("user_id")
        if not gcal_id or not user_id:
            raise ValueError("gcal_event_id와 user_id는 필수입니다.")

        meeting_room_id = metadata.get("meeting_room_id")
        created_at = metadata.get("created_at") or datetime.utcnow()
        updated_at = datetime.utcnow()

        await self._ensure_event_metadata_table()

        sql = """
        INSERT INTO event_metadata (gcal_event_id, user_id, meeting_room_id, created_at, updated_at)
        VALUES (:gcal_event_id, :user_id, :meeting_room_id, :created_at, :updated_at)
        ON DUPLICATE KEY UPDATE
            user_id = VALUES(user_id),
            meeting_room_id = VALUES(meeting_room_id),
            updated_at = VALUES(updated_at);
        """

        try:
            async with self._session(transactional=True) as session:
                await session.execute(
                    text(sql),
                    {
                        "gcal_event_id": gcal_id,
                        "user_id": str(user_id),
                        "meeting_room_id": meeting_room_id,
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )
            return True
        except SQLAlchemyError as exc:
            print(f"❌ 이벤트 메타데이터 저장 실패: {exc}")
            return False

    # ------------------------------------------------------------------
    # 연차 관련
    # ------------------------------------------------------------------

    async def get_leave_balance(self, employee_id: int) -> Dict[str, Any]:
        """`leave_balance`에서 특정 사원의 잔여 연차 정보를 조회한다."""
        current_year = date.today().year
        select_sql = """
        SELECT id, employee_id, year, total_entitlement_days, carried_over_days,
               used_days, pending_days, remaining_days, created_at, updated_at
        FROM leave_balance
        WHERE employee_id = :employee_id AND year = :year
        LIMIT 1;
        """

        async with self._session() as session:
            result = await session.execute(
                text(select_sql),
                {"employee_id": employee_id, "year": current_year},
            )
            row = result.mappings().first()

        if row:
            return dict(row)

        insert_sql = """
        INSERT INTO leave_balance (
            employee_id, year, total_entitlement_days, carried_over_days,
            used_days, pending_days, remaining_days, created_at, updated_at
        ) VALUES (
            :employee_id, :year, :total_entitlement_days, 0, 0, 0,
            :total_entitlement_days, NOW(), NOW()
        );
        """

        default_total = 15.0
        async with self._session(transactional=True) as session:
            await session.execute(
                text(insert_sql),
                {
                    "employee_id": employee_id,
                    "year": current_year,
                    "total_entitlement_days": default_total,
                },
            )

        return {
            "employee_id": employee_id,
            "year": current_year,
            "total_entitlement_days": default_total,
            "carried_over_days": 0.0,
            "used_days": 0.0,
            "pending_days": 0.0,
            "remaining_days": default_total,
        }

    async def create_leave(self, leave_data: Dict[str, Any]) -> int:
        """연차 신청을 `leave_request` 테이블에 생성한다."""
        request_id = leave_data.get("request_id") or datetime.utcnow().strftime("LR%Y%m%d%H%M%S")
        insert_sql = """
        INSERT INTO leave_request (
            request_id,
            document_id,
            employee_id,
            request_date,
            leave_type,
            start_date,
            end_date,
            total_days,
            status,
            reason,
            created_at,
            updated_at
        ) VALUES (
            :request_id,
            :document_id,
            :employee_id,
            :request_date,
            :leave_type,
            :start_date,
            :end_date,
            :total_days,
            :status,
            :reason,
            NOW(),
            NOW()
        );
        """

        payload = {
            "request_id": request_id,
            "document_id": leave_data.get("document_id"),
            "employee_id": leave_data["employee_id"],
            "request_date": leave_data.get("request_date") or date.today(),
            "leave_type": leave_data.get("leave_type", "연차"),
            "start_date": leave_data.get("start_date"),
            "end_date": leave_data.get("end_date"),
            "total_days": leave_data.get("total_days") or 1.0,
            "status": leave_data.get("status", "대기"),
            "reason": leave_data.get("reason"),
        }

        async with self._session(transactional=True) as session:
            result = await session.execute(text(insert_sql), payload)
            inserted_id = result.lastrowid

        await self._update_leave_pending_days(
            employee_id=leave_data["employee_id"],
            delta=payload["total_days"] if payload["status"] == "대기" else 0.0,
        )

        return int(inserted_id)

    async def _update_leave_pending_days(self, employee_id: int, delta: float) -> None:
        """pending_days 및 remaining_days를 갱신한다."""
        if not delta:
            return

        sql = """
        UPDATE leave_balance
        SET pending_days = pending_days + :delta,
            remaining_days = remaining_days - :delta,
            updated_at = NOW()
        WHERE employee_id = :employee_id AND year = :year;
        """

        async with self._session(transactional=True) as session:
            await session.execute(
                text(sql),
                {
                    "delta": float(delta),
                    "employee_id": employee_id,
                    "year": date.today().year,
                },
            )

    async def get_leave_list(
        self, 
        employee_id: int, 
        leave_type: Optional[str] = None,
        status: Optional[str] = None,
        include_past: bool = True
    ) -> List[Dict[str, Any]]:
        """`leave_request` 테이블에서 신청 내역을 조회한다."""
        clauses = ["employee_id = :employee_id"]
        params: Dict[str, Any] = {"employee_id": employee_id}
        
        if leave_type:
            clauses.append("leave_type = :leave_type")
            params["leave_type"] = leave_type
        
        if status:
            clauses.append("status = :status")
            params["status"] = status
        
        if not include_past:
            clauses.append("start_date >= CURDATE()")

        where_sql = " AND ".join(clauses)
        sql = f"""
        SELECT
            id,
            request_id,
            document_id,
            employee_id,
            request_date,
            leave_type,
            start_date,
            end_date,
            total_days,
            status,
            reason,
            created_at,
            updated_at
        FROM leave_request
        WHERE {where_sql}
        ORDER BY request_date DESC, id DESC;
        """

        async with self._session() as session:
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]

    async def cancel_leave_request(self, request_id: int) -> bool:
        """연차 신청을 취소한다 (status를 '취소'로 변경)."""
        # 1. 현재 상태 조회
        select_sql = """
        SELECT employee_id, total_days, status
        FROM leave_request
        WHERE id = :request_id
        LIMIT 1;
        """
        
        async with self._session() as session:
            result = await session.execute(text(select_sql), {"request_id": request_id})
            row = result.mappings().first()
        
        if not row:
            return False
        
        employee_id = row["employee_id"]
        total_days = float(row["total_days"])
        current_status = row["status"]
        
        # 2. 상태 업데이트
        update_sql = """
        UPDATE leave_request
        SET status = '취소', updated_at = NOW()
        WHERE id = :request_id;
        """
        
        async with self._session(transactional=True) as session:
            await session.execute(text(update_sql), {"request_id": request_id})
        
        # 3. 대기 중이었다면 pending_days 복구
        if current_status == "대기":
            await self._update_leave_pending_days(
                employee_id=employee_id,
                delta=-total_days  # 음수로 복구
            )
        
        return True

    async def update_leave_request(self, request_id: int, update_data: Dict[str, Any]) -> bool:
        """연차 신청 내용을 수정한다."""
        # 1. 현재 정보 조회
        select_sql = """
        SELECT employee_id, total_days, status
        FROM leave_request
        WHERE id = :request_id
        LIMIT 1;
        """
        
        async with self._session() as session:
            result = await session.execute(text(select_sql), {"request_id": request_id})
            row = result.mappings().first()
        
        if not row:
            return False
        
        employee_id = row["employee_id"]
        old_total_days = float(row["total_days"])
        current_status = row["status"]
        
        # 2. 업데이트할 필드 구성
        set_clauses = ["updated_at = NOW()"]
        params: Dict[str, Any] = {"request_id": request_id}
        
        if "start_date" in update_data:
            set_clauses.append("start_date = :start_date")
            params["start_date"] = update_data["start_date"]
        
        if "end_date" in update_data:
            set_clauses.append("end_date = :end_date")
            params["end_date"] = update_data["end_date"]
        
        if "total_days" in update_data:
            set_clauses.append("total_days = :total_days")
            params["total_days"] = update_data["total_days"]
        
        if "reason" in update_data:
            set_clauses.append("reason = :reason")
            params["reason"] = update_data["reason"]
        
        if "leave_type" in update_data:
            set_clauses.append("leave_type = :leave_type")
            params["leave_type"] = update_data["leave_type"]
        
        # 3. 업데이트 실행
        update_sql = f"""
        UPDATE leave_request
        SET {', '.join(set_clauses)}
        WHERE id = :request_id;
        """
        
        async with self._session(transactional=True) as session:
            await session.execute(text(update_sql), params)
        
        # 4. total_days가 변경되었고 대기 중이면 pending_days 조정
        if "total_days" in update_data and current_status == "대기":
            new_total_days = float(update_data["total_days"])
            delta = new_total_days - old_total_days
            if delta != 0:
                await self._update_leave_pending_days(
                    employee_id=employee_id,
                    delta=delta
                )
        
        return True

    # ------------------------------------------------------------------
    # 회의실 관련
    # ------------------------------------------------------------------

    async def get_meeting_rooms(
        self,
        capacity: Optional[int] = None,
        floor: Optional[int] = None,
        has_projector: Optional[bool] = None,
        has_whiteboard: Optional[bool] = None,
        has_video_conf: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """`meeting_room` 정보를 조회한다."""
        clauses: List[str] = []
        params: Dict[str, Any] = {}

        if capacity is not None:
            clauses.append("capacity >= :capacity")
            params["capacity"] = capacity
        if floor is not None:
            clauses.append("floor = :floor")
            params["floor"] = str(floor)
        if has_video_conf is not None:
            clauses.append("has_video = :has_video")
            params["has_video"] = int(has_video_conf)

        where_sql = ""
        if clauses:
            where_sql = "WHERE " + " AND ".join(clauses)

        sql = f"""
        SELECT
            id,
            room_code,
            name,
            building,
            floor,
            capacity,
            has_video,
            created_at,
            updated_at
        FROM meeting_room
        {where_sql}
        ORDER BY capacity DESC;
        """

        async with self._session() as session:
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]

    async def _ensure_meeting_reservation_table(self) -> None:
        """회의실 예약 테이블 스키마 보정 (MariaDB 호환)."""
        # 1. 먼저 컬럼 존재 여부 확인
        check_sql = """
        SELECT COUNT(*) as cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'meeting_room_reservation'
          AND COLUMN_NAME = 'gcal_event_id';
        """
        
        try:
            async with self._session() as session:
                result = await session.execute(text(check_sql))
                row = result.first()
                column_exists = row[0] > 0 if row else False
            
            # 2. 컬럼이 없으면 추가
            if not column_exists:
                alter_sql = """
                ALTER TABLE meeting_room_reservation
                ADD COLUMN gcal_event_id VARCHAR(128) NULL AFTER schedule_id;
                """
                async with self._session(transactional=True) as session:
                    await session.execute(text(alter_sql))
                    print("✅ gcal_event_id 컬럼 추가 완료")
        except SQLAlchemyError as exc:
            if "Duplicate column name" not in str(exc):
                print(f"⚠️ 회의실 예약 테이블 보정 실패: {exc}")

    async def create_room_reservation(self, reservation_data: Dict[str, Any]) -> int:
        """회의실 예약을 `meeting_room_reservation`에 생성한다."""
        await self._ensure_meeting_reservation_table()
        sql = """
        INSERT INTO meeting_room_reservation (
            meeting_room_id,
            employee_id,
            schedule_id,
            gcal_event_id,
            title,
            description,
            start_time,
            end_time,
            participants,
            status,
            created_at,
            updated_at
        ) VALUES (
            :meeting_room_id,
            :employee_id,
            :schedule_id,
            :gcal_event_id,
            :title,
            :description,
            :start_time,
            :end_time,
            :participants,
            :status,
            NOW(),
            NOW()
        );
        """

        payload = {
            "meeting_room_id": reservation_data.get("room_id"),
            "employee_id": reservation_data.get("employee_id"),
            "schedule_id": reservation_data.get("schedule_id"),
            "gcal_event_id": reservation_data.get("gcal_event_id"),
            "title": reservation_data.get("title"),
            "description": reservation_data.get("description"),
            "start_time": reservation_data.get("start_time"),
            "end_time": reservation_data.get("end_time"),
            "participants": reservation_data.get("participants", 0),
            "status": reservation_data.get("status", "PENDING"),
        }

        async with self._session(transactional=True) as session:
            result = await session.execute(text(sql), payload)
            inserted_id = result.lastrowid

        return int(inserted_id)

    async def get_room_reservations_by_user(
        self,
        employee_id: int,
        include_past: bool = False,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """사번 기준 회의실 예약 목록 조회."""
        await self._ensure_meeting_reservation_table()
        clauses = ["employee_id = :employee_id"]
        params: Dict[str, Any] = {"employee_id": employee_id}
        if not include_past:
            clauses.append("end_time >= :now")
            params["now"] = datetime.now()

        where_sql = " AND ".join(clauses)
        sql = f"""
        SELECT
            id,
            meeting_room_id,
            schedule_id,
            gcal_event_id,
            title,
            description,
            start_time,
            end_time,
            participants,
            status,
            created_at,
            updated_at
        FROM meeting_room_reservation
        WHERE {where_sql}
        ORDER BY start_time ASC
        LIMIT :limit;
        """
        params["limit"] = limit

        async with self._session() as session:
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]

    async def find_room_conflicts(
        self,
        meeting_room_id: int,
        start_time: datetime,
        end_time: datetime,
        exclude_reservation_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """해당 시간대에 겹치는 회의실 예약 조회."""
        await self._ensure_meeting_reservation_table()
        clauses = [
            "meeting_room_id = :meeting_room_id",
            "start_time < :end_time",
            "end_time > :start_time",
        ]
        params: Dict[str, Any] = {
            "meeting_room_id": meeting_room_id,
            "start_time": start_time,
            "end_time": end_time,
        }
        if exclude_reservation_id:
            clauses.append("id != :exclude_id")
            params["exclude_id"] = exclude_reservation_id

        where_sql = " AND ".join(clauses)
        sql = f"""
        SELECT
            id,
            meeting_room_id,
            start_time,
            end_time,
            participants,
            status
        FROM meeting_room_reservation
        WHERE {where_sql};
        """

        async with self._session() as session:
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]

    async def find_available_rooms(
        self,
        start_time: datetime,
        end_time: datetime,
        capacity: Optional[int] = None,
        floor: Optional[int] = None,
        has_video_conf: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """특정 시간대에 예약 가능한 회의실 조회.
        
        Args:
            start_time: 시작 시간
            end_time: 종료 시간
            capacity: 최소 수용 인원
            floor: 층수
            has_video_conf: 화상회의 가능 여부
            
        Returns:
            예약 가능한 회의실 목록
        """
        await self._ensure_meeting_reservation_table()
        
        # 1. 조건에 맞는 모든 회의실 조회
        all_rooms = await self.get_meeting_rooms(
            capacity=capacity,
            floor=floor,
            has_video_conf=has_video_conf,
        )
        
        if not all_rooms:
            return []
        
        # 2. 해당 시간대에 예약된 회의실 ID 조회
        sql = """
        SELECT DISTINCT meeting_room_id
        FROM meeting_room_reservation
        WHERE start_time < :end_time
          AND end_time > :start_time
          AND status != 'CANCELLED';
        """
        params = {
            "start_time": start_time,
            "end_time": end_time,
        }
        
        async with self._session() as session:
            result = await session.execute(text(sql), params)
            busy_room_ids = {row[0] for row in result.fetchall()}
        
        # 3. 예약되지 않은 회의실만 반환
        available_rooms = [
            room for room in all_rooms
            if room.get("id") not in busy_room_ids
        ]
        
        return available_rooms

    async def update_room_reservation(
        self,
        reservation_id: int,
        updates: Dict[str, Any]
    ) -> bool:
        """회의실 예약 수정."""
        if not updates:
            return False
        await self._ensure_meeting_reservation_table()
        setters = []
        params: Dict[str, Any] = {"reservation_id": reservation_id}
        for key, value in updates.items():
            setters.append(f"{key} = :{key}")
            params[key] = value
        setters.append("updated_at = NOW()")
        set_clause = ", ".join(setters)

        sql = f"""
        UPDATE meeting_room_reservation
        SET {set_clause}
        WHERE id = :reservation_id;
        """

        async with self._session(transactional=True) as session:
            result = await session.execute(text(sql), params)
            return result.rowcount > 0

    async def update_room_reservation_by_event(
        self,
        gcal_event_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Google 이벤트 ID 기반 회의실 예약 수정."""
        if not updates or not gcal_event_id:
            return False
        await self._ensure_meeting_reservation_table()
        setters = []
        params: Dict[str, Any] = {"gcal_event_id": gcal_event_id}
        for key, value in updates.items():
            setters.append(f"{key} = :{key}")
            params[key] = value
        setters.append("updated_at = NOW()")
        set_clause = ", ".join(setters)

        sql = f"""
        UPDATE meeting_room_reservation
        SET {set_clause}
        WHERE gcal_event_id = :gcal_event_id;
        """

        async with self._session(transactional=True) as session:
            result = await session.execute(text(sql), params)
            return result.rowcount > 0

    async def delete_room_reservation(self, reservation_id: int) -> bool:
        """회의실 예약 삭제."""
        await self._ensure_meeting_reservation_table()
        sql = """
        DELETE FROM meeting_room_reservation
        WHERE id = :reservation_id;
        """
        async with self._session(transactional=True) as session:
            result = await session.execute(text(sql), {"reservation_id": reservation_id})
            return result.rowcount > 0

    async def delete_room_reservation_by_event(self, gcal_event_id: str) -> bool:
        """Google 이벤트 ID로 회의실 예약 삭제."""
        if not gcal_event_id:
            return False
        await self._ensure_meeting_reservation_table()
        sql = """
        DELETE FROM meeting_room_reservation
        WHERE gcal_event_id = :gcal_event_id;
        """
        async with self._session(transactional=True) as session:
            result = await session.execute(text(sql), {"gcal_event_id": gcal_event_id})
            return result.rowcount > 0

    # ------------------------------------------------------------------
    # 트랜잭션
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncSession:
        """하나의 트랜잭션으로 묶어야 할 때 사용하는 컨텍스트 매니저."""
        async with self._session_factory() as session:
            token = self._session_ctx.set(session)
            try:
                async with session.begin():
                    yield session
            finally:
                self._session_ctx.reset(token)