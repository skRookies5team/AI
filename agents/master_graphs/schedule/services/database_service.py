"""MariaDB 백엔드 연동 서비스."""

from __future__ import annotations

import json
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


def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입.
    
    여러 경로에서 .env 파일을 찾습니다.
    """
    import sys
    from pathlib import Path
    
    # 가능한 .env 파일 경로들
    possible_paths = [
        os.path.join(os.getcwd(), ".env"),  # 현재 작업 디렉토리
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),  # 프로젝트 루트
        os.path.join(os.path.dirname(__file__), ".env"),  # 현재 파일 디렉토리
    ]
    
    # 스크립트 파일이 있는 경우 그 디렉토리도 확인
    if hasattr(sys, 'argv') and sys.argv:
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        possible_paths.append(os.path.join(script_dir, ".env"))
        possible_paths.append(os.path.join(script_dir, "..", ".env"))
    
    env_path = None
    for path in possible_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            env_path = abs_path
            break
    
    if not env_path:
        print("[WARNING] .env 파일을 찾을 수 없습니다.")
        return
    
    try:
        loaded_vars = []
        with open(env_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                original_line = line
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                # 따옴표 제거 (단, 값이 비어있지 않은 경우만)
                if v:
                    v = v.strip("'").strip('"')
                
                # 기존 환경 변수가 있으면 덮어쓰지 않음 (환경 변수 우선)
                if k:
                    if k not in os.environ:
                        os.environ[k] = v
                        loaded_vars.append(k)
        
        # 로드된 변수 확인
        db_password = os.getenv("DB_PASSWORD", "")
        print(f"[INFO] DatabaseService .env 파일 로드 완료: {env_path}")
        print(f"[DEBUG] DB_PASSWORD 존재 여부: {'있음' if db_password else '없음'}, 길이: {len(db_password)}")
    except Exception as e:
        print(f"[WARNING] .env 파일 로드 실패 ({env_path}): {e}")
        import traceback
        traceback.print_exc()


# 모듈 로드 시 .env 파일 로드
_load_env_file()


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
        # .env 파일이 로드되었는지 확인하고, 없으면 다시 로드 시도
        if not os.getenv("DB_PASSWORD") and not os.getenv("DB_USERNAME"):
            _load_env_file()
        
        username = _get_env("DB_USERNAME", "root")
        password = _get_env("DB_PASSWORD", "")
        host = _get_env("DB_HOST") or _get_env("HOST", "127.0.0.1")
        port = _get_env("DB_PORT", "3306")
        db_name = _get_env("DB_NAME", "ai_agent")
        
        # DB 연결 정보 디버깅 (비밀번호는 마스킹)
        password_display = "***" if password else "(없음)"
        print(f"[INFO] DatabaseService 초기화: host={host}, port={port}, db={db_name}, user={username}, password={password_display}")
        
        # 비밀번호가 없으면 경고
        if not password:
            print("[WARNING] DB_PASSWORD가 설정되지 않았습니다. DB 연결이 실패할 수 있습니다.")
            print("[WARNING] .env 파일에 DB_PASSWORD=your_password 형식으로 설정해주세요.")

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
            # ✅ interviewer_ids가 NULL이면 일반 일정, 값이 있으면 면접 일정
            clauses.append("""
                (
                    -- 일반 개인 일정: interviewer_ids가 NULL이고 employee_id가 매칭
                    (interviewer_ids IS NULL AND employee_id = :employee_id)
                    OR
                    -- 면접 일정: interviewer_ids에 현재 사용자가 포함되어 있는지 확인
                    (interviewer_ids IS NOT NULL AND JSON_CONTAINS(interviewer_ids, CAST(:employee_id AS JSON)))
                )
            """)
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
            interviewer_ids,
            created_at,
            updated_at
        FROM schedule
        {where_sql}
        ORDER BY start_time ASC;
        """

        async with self._session() as session:
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]

    async def create_schedule(
        self,
        schedule_data: Dict[str, Any]
    ) -> int:
        """일정을 `schedule` 테이블에 생성한다.
        
        Args:
            schedule_data: 일정 데이터
                - employee_id: int (필수)
                - title: str
                - description: Optional[str]
                - start_time: datetime (필수)
                - end_time: datetime (필수)
                - schedule_type: str (필수) - 'meeting_room', 'general', 'business_trip', 'leave'
                - location: Optional[str]
                - gcal_event_id: Optional[str] - Google Calendar 이벤트 ID
                - alert_time: Optional[datetime]
                - status: Optional[str] (기본값: 'active')
        
        Returns:
            생성된 schedule_id
        """
        insert_sql = """
        INSERT INTO schedule (
            employee_id,
            title,
            description,
            start_time,
            end_time,
            schedule_type,
            location,
            gcal_event_id,
            alert_time,
            status,
            created_at,
            updated_at
        ) VALUES (
            :employee_id,
            :title,
            :description,
            :start_time,
            :end_time,
            :schedule_type,
            :location,
            :gcal_event_id,
            :alert_time,
            :status,
            NOW(),
            NOW()
        );
        """
        
        payload = {
            "employee_id": schedule_data["employee_id"],
            "title": schedule_data.get("title", ""),
            "description": schedule_data.get("description"),
            "start_time": schedule_data["start_time"],
            "end_time": schedule_data["end_time"],
            "schedule_type": schedule_data["schedule_type"],
            "location": schedule_data.get("location"),
            "gcal_event_id": schedule_data.get("gcal_event_id"),
            "alert_time": schedule_data.get("alert_time"),
            "status": schedule_data.get("status", "active"),
        }
        
        async with self._session(transactional=True) as session:
            result = await session.execute(text(insert_sql), payload)
            inserted_id = result.lastrowid
        
        return int(inserted_id)

    async def get_event_metadata(self, event_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """Google Calendar 이벤트 ID별 메타데이터를 조회한다."""
        ids = list(event_ids)
        if not ids:
            return {}

        await self._ensure_event_metadata_table()

        placeholders = ", ".join(f":id_{idx}" for idx, _ in enumerate(ids))
        params = {f"id_{idx}": value for idx, value in enumerate(ids)}

        sql = f"""
        SELECT gcal_event_id, user_id, meeting_room_id, schedule_type, created_at, updated_at
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
        schedule_type = metadata.get("schedule_type")
        created_at = metadata.get("created_at") or datetime.utcnow()
        updated_at = datetime.utcnow()

        await self._ensure_event_metadata_table()

        sql = """
        INSERT INTO event_metadata (gcal_event_id, user_id, meeting_room_id, schedule_type, created_at, updated_at)
        VALUES (:gcal_event_id, :user_id, :meeting_room_id, :schedule_type, :created_at, :updated_at)
        ON DUPLICATE KEY UPDATE
            user_id = VALUES(user_id),
            meeting_room_id = VALUES(meeting_room_id),
            schedule_type = VALUES(schedule_type),
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
                        "schedule_type": schedule_type,
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
            lr.id,
            lr.request_id,
            lr.document_id,
            lr.employee_id,
            lr.request_date,
            lr.leave_type,
            lr.start_date,
            lr.end_date,
            lr.total_days,
            lr.status,
            lr.reason,
            lr.created_at,
            lr.updated_at,
            s.gcal_event_id
        FROM leave_request lr
        LEFT JOIN schedule s ON s.employee_id = lr.employee_id 
            AND s.schedule_type = 'leave'
            AND DATE(s.start_time) = lr.start_date
            AND DATE(s.end_time) = lr.end_date
        WHERE {where_sql}
        ORDER BY lr.request_date DESC, lr.id DESC;
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

    async def get_room_reservations_by_date(
        self,
        target_date: date
    ) -> List[Dict[str, Any]]:
        """특정 날짜의 모든 회의실 예약 조회 (회의실 정보 및 예약자 포함) - 최적화됨."""
        # 날짜 범위 계산 (인덱스 활용을 위해 BETWEEN 사용)
        from datetime import datetime, timedelta
        start_datetime = datetime.combine(target_date, datetime.min.time())
        end_datetime = datetime.combine(target_date, datetime.max.time())
        
        sql = """
        SELECT 
            mrr.id,
            mrr.meeting_room_id as room_id,
            mr.name as room_name,
            mr.floor,
            mrr.start_time,
            mrr.end_time,
            mrr.title,
            e.name as applicant_name,
            mrr.participants,
            mrr.description
        FROM meeting_room_reservation mrr
        INNER JOIN meeting_room mr ON mrr.meeting_room_id = mr.id
        LEFT JOIN employee e ON mrr.employee_id = e.employee_id
        WHERE mrr.start_time BETWEEN :start_dt AND :end_dt
        AND mrr.status IN ('CONFIRMED', 'PENDING')
        ORDER BY mrr.start_time ASC
        """
        
        async with self._session() as session:
            result = await session.execute(
                text(sql),
                {"start_dt": start_datetime, "end_dt": end_datetime}
            )
            return [dict(row) for row in result.mappings().all()]
    async def get_all_schedules_for_calendar(
        self,
        employee_id: int,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict[str, Any]]:
        """캘린더용 통합 일정 조회 (연차, 회의, 출장, 면접 등).
        
        Google Calendar API 의존성을 제거하고 DB 데이터를 직접 조회하여 병합합니다.
        
        Returns:
            통합된 일정 리스트 (날짜순 정렬)
        """
        # 1. 연차/반차 (승인, 대기, 확정)
        # 2. 회의실 예약 (CONFIRMED, PENDING)
        # 3. 출장 (active, approved, pending) - status가 컬럼에 있는지 확인 필요 (없으면 조건 제외)
        # 4. 일반/면접 일정 (schedule 테이블)
        
        # 날짜 포맷팅 (YYYY-MM-DD)
        # start_date, end_date는 datetime 객체
        
        # 먼저 business_trip 테이블에 status 컬럼이 있는지 확인 (없으면 제외)
        # 여기서는 안전하게 status 컬럼이 존재한다고 가정하거나 단순화
        
        # JSON_CONTAINS를 위한 employee_id (문자열 아님, JSON 숫자)
        emp_id_json = json.dumps(employee_id)
        
        sql = """
        SELECT * FROM (
            -- 1. 연차/반차
            SELECT 
                CONCAT('leave_', id) as id,
                start_date as date,
                CONCAT(leave_type, CASE WHEN reason IS NOT NULL AND reason != '' 
                    THEN CONCAT(' - ', LEFT(reason, 20)) ELSE '' END) as title,
                'leave' as schedule_type,
                CAST(CONCAT(start_date, ' 00:00:00') AS DATETIME) as start_time,
                CAST(CONCAT(end_date, ' 23:59:59') AS DATETIME) as end_time,
                NULL as location,
                NULL as meeting_room_name,
                status,
                reason as description
            FROM leave_request
            WHERE employee_id = :employee_id
              AND start_date <= :end_date_date
              AND end_date >= :start_date_date
              AND status IN ('approved', '승인', 'pending', '대기', 'confirmed', '확정')

            UNION ALL

            -- 2. 회의실 예약
            SELECT 
                CONCAT('room_', mrr.id) as id,
                DATE(mrr.start_time) as date,
                mrr.title,
                'meeting_room' as schedule_type,
                mrr.start_time,
                mrr.end_time,
                CONCAT(mr.name, ' (', mr.floor, '층)') as location,
                mr.name as meeting_room_name,
                mrr.status,
                mrr.description
            FROM meeting_room_reservation mrr
            JOIN meeting_room mr ON mrr.meeting_room_id = mr.id
            WHERE mrr.employee_id = :employee_id
              AND mrr.start_time < :end_date
              AND mrr.end_time > :start_date
              AND mrr.status IN ('CONFIRMED', 'PENDING')

            UNION ALL

            -- 3. 출장
            SELECT 
                CONCAT('trip_', trip_id) as id,
                DATE(start_time) as date,
                trip_title as title,
                'business_trip' as schedule_type,
                start_time,
                end_time,
                destination as location,
                NULL as meeting_room_name,
                'active' as status,
                trip_purpose as description
            FROM business_trip
            WHERE employee_id = :employee_id
              AND start_time <= :end_date
              AND end_time >= :start_date

            UNION ALL

            -- 4. 일반 스케줄 (schedule 테이블)
            -- (1) 일반 일정: interviewer_ids IS NULL AND employee_id 매칭
            SELECT 
                CONCAT('sched_', schedule_id) as id,
                DATE(start_time) as date,
                title,
                schedule_type,
                start_time,
                end_time,
                location,
                NULL as meeting_room_name,
                status,
                description
            FROM schedule
            WHERE employee_id = :employee_id
              AND start_time <= :end_date
              AND end_time >= :start_date
              AND interviewer_ids IS NULL
              AND status IN ('active', '활성')
              
            UNION ALL
            
            -- (2) 면접 일정: JSON_CONTAINS(interviewer_ids)
            SELECT 
                CONCAT('interview_', schedule_id) as id,
                DATE(start_time) as date,
                title,
                'interview' as schedule_type,
                start_time,
                end_time,
                location,
                NULL as meeting_room_name,
                status,
                description
            FROM schedule
            WHERE schedule_type = 'interview'
              AND start_time <= :end_date
              AND end_time >= :start_date
              AND status IN ('active', '활성')
              AND interviewer_ids IS NOT NULL
              AND JSON_CONTAINS(interviewer_ids, :emp_id_json)
        ) AS unified_schedule
        ORDER BY start_time ASC
        """
        
        params = {
            "employee_id": employee_id,
            "start_date": start_date,
            "end_date": end_date,
            "start_date_date": start_date.date(),
            "end_date_date": end_date.date(),
            "emp_id_json": emp_id_json
        }

        async with self._session() as session:
            try:
                result = await session.execute(text(sql), params)
                rows = [dict(row) for row in result.mappings().all()]
                
                # datetime 객체를 ISO 문자열로 변환 (JSON 직렬화 위해)
                for row in rows:
                    if isinstance(row.get("date"), date):
                        row["date"] = row["date"].isoformat()
                    if isinstance(row.get("start_time"), datetime):
                        row["start_time"] = row["start_time"].isoformat()
                    if isinstance(row.get("end_time"), datetime):
                        row["end_time"] = row["end_time"].isoformat()
                
                return rows
            except SQLAlchemyError as e:
                print(f"❌ 통합 일정 조회 중 오류: {e}")
                import traceback
                traceback.print_exc()
                return []

    async def create_business_trip_request(
        self,
        trip_data: Dict[str, Any]
    ) -> int:
        """출장 신청을 `business_trip` 테이블에 생성한다.
        
        Args:
            trip_data: 출장 데이터 딕셔너리
                - employee_id: int (필수)
                - trip_title: Optional[str]
                - trip_purpose: Optional[str]
                - destination: Optional[str]
                - start_time: datetime (필수)
                - end_time: datetime (필수)
                - document_id: Optional[int]
                - alert_time: Optional[datetime]
        
        Returns:
            생성된 trip_id
        """
        insert_sql = """
        INSERT INTO business_trip (
            document_id,
            employee_id,
            trip_title,
            trip_purpose,
            destination,
            start_time,
            end_time,
            alert_time,
            created_at
        ) VALUES (
            :document_id,
            :employee_id,
            :trip_title,
            :trip_purpose,
            :destination,
            :start_time,
            :end_time,
            :alert_time,
            NOW()
        );
        """
        
        payload = {
            "document_id": trip_data.get("document_id"),
            "employee_id": trip_data["employee_id"],
            "trip_title": trip_data.get("trip_title"),
            "trip_purpose": trip_data.get("trip_purpose"),
            "destination": trip_data.get("destination"),
            "start_time": trip_data["start_time"],
            "end_time": trip_data["end_time"],
            "alert_time": trip_data.get("alert_time"),
        }
        
        async with self._session(transactional=True) as session:
            result = await session.execute(text(insert_sql), payload)
            inserted_id = result.lastrowid
        
        return int(inserted_id)
    
    async def get_business_trip_list(
        self,
        employee_id: int,
        include_past: bool = True
    ) -> List[Dict[str, Any]]:
        """`business_trip` 테이블에서 출장 내역을 조회한다.
        
        Args:
            employee_id: 사원 ID
            include_past: 과거 출장 포함 여부
        
        Returns:
            출장 내역 리스트
        """
        clauses = ["employee_id = :employee_id"]
        params: Dict[str, Any] = {"employee_id": employee_id}
        
        if not include_past:
            clauses.append("start_time >= NOW()")
        
        where_sql = " AND ".join(clauses)
        sql = f"""
        SELECT
            trip_id,
            document_id,
            employee_id,
            trip_title,
            trip_purpose,
            destination,
            start_time,
            end_time,
            alert_time,
            created_at
        FROM business_trip
        WHERE {where_sql}
        ORDER BY start_time DESC, trip_id DESC;
        """
        
        async with self._session() as session:
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]
    
    async def get_business_trip_by_id(self, trip_id: int, employee_id: int) -> Optional[Dict[str, Any]]:
        """출장 ID로 출장 정보를 조회한다.
        
        Args:
            trip_id: 출장 ID
            employee_id: 사원 ID (본인 확인용)
        
        Returns:
            출장 정보 또는 None
        """
        sql = """
        SELECT
            trip_id,
            document_id,
            employee_id,
            trip_title,
            trip_purpose,
            destination,
            start_time,
            end_time,
            alert_time,
            created_at
        FROM business_trip
        WHERE trip_id = :trip_id AND employee_id = :employee_id;
        """
        
        async with self._session() as session:
            result = await session.execute(text(sql), {"trip_id": trip_id, "employee_id": employee_id})
            row = result.mappings().first()
            return dict(row) if row else None
    
    async def find_business_trip_by_date(
        self,
        employee_id: int,
        start_time: datetime,
        end_time: datetime,
        destination: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """날짜 범위와 목적지로 출장을 찾는다.
        
        Args:
            employee_id: 사원 ID
            start_time: 시작 시간
            end_time: 종료 시간
            destination: 목적지 (선택)
        
        Returns:
            출장 정보 또는 None
        """
        clauses = [
            "employee_id = :employee_id",
            "start_time >= :start_time",
            "end_time <= :end_time"
        ]
        params: Dict[str, Any] = {
            "employee_id": employee_id,
            "start_time": start_time,
            "end_time": end_time
        }
        
        if destination:
            clauses.append("destination = :destination")
            params["destination"] = destination
        
        where_sql = " AND ".join(clauses)
        sql = f"""
        SELECT
            trip_id,
            document_id,
            employee_id,
            trip_title,
            trip_purpose,
            destination,
            start_time,
            end_time,
            alert_time,
            created_at
        FROM business_trip
        WHERE {where_sql}
        ORDER BY start_time DESC
        LIMIT 1;
        """
        
        async with self._session() as session:
            result = await session.execute(text(sql), params)
            row = result.mappings().first()
            return dict(row) if row else None
    
    async def cancel_business_trip(self, trip_id: int, employee_id: int) -> bool:
        """출장을 취소한다 (status를 '취소'로 변경하거나 삭제).
        
        Args:
            trip_id: 출장 ID
            employee_id: 사원 ID (본인 확인용)
        
        Returns:
            취소 성공 여부
        """
        # 먼저 출장이 존재하고 본인 것인지 확인
        trip = await self.get_business_trip_by_id(trip_id, employee_id)
        if not trip:
            return False
        
        # status 컬럼이 있으면 업데이트, 없으면 삭제
        # 일단 삭제로 처리 (나중에 status 컬럼이 추가되면 수정 가능)
        sql = """
        DELETE FROM business_trip
        WHERE trip_id = :trip_id AND employee_id = :employee_id;
        """
        
        try:
            async with self._session(transactional=True) as session:
                await session.execute(text(sql), {"trip_id": trip_id, "employee_id": employee_id})
            return True
        except Exception as e:
            print(f"❌ 출장 취소 실패: {e}")
            return False
    
    async def get_gcal_event_id_by_trip_id(self, trip_id: int) -> Optional[str]:
        """출장 ID로 Google Calendar 이벤트 ID를 조회한다.
        
        Args:
            trip_id: 출장 ID
        
        Returns:
            Google Calendar 이벤트 ID 또는 None
        """
        # schedule 테이블에서 schedule_type='business_trip'이고 gcal_event_id가 있는 것 찾기
        # 또는 event_metadata에서 reference_id=trip_id인 것 찾기
        # 일단 schedule 테이블에서 찾기 (더 확실함)
        sql = """
        SELECT s.gcal_event_id
        FROM schedule s
        INNER JOIN business_trip bt ON (
            s.employee_id = bt.employee_id
            AND s.start_time = bt.start_time
            AND s.end_time = bt.end_time
            AND s.schedule_type = 'business_trip'
        )
        WHERE bt.trip_id = :trip_id
        LIMIT 1;
        """
        
        async with self._session() as session:
            result = await session.execute(text(sql), {"trip_id": trip_id})
            row = result.mappings().first()
            return row["gcal_event_id"] if row and row.get("gcal_event_id") else None
    
    async def delete_schedule_by_gcal_event_id(self, gcal_event_id: str) -> bool:
        """gcal_event_id로 schedule 테이블에서 일정을 삭제한다.
        
        Args:
            gcal_event_id: Google Calendar 이벤트 ID
        
        Returns:
            삭제 성공 여부
        """
        sql = """
        DELETE FROM schedule
        WHERE gcal_event_id = :gcal_event_id;
        """
        
        try:
            async with self._session(transactional=True) as session:
                await session.execute(text(sql), {"gcal_event_id": gcal_event_id})
            return True
        except Exception as e:
            print(f"⚠️ schedule 테이블 삭제 실패: {e}")
            return False
    
    async def get_team_member_emails(
        self,
        employee_id: int
    ) -> List[str]:
        """같은 부서 계열의 팀원 이메일 목록을 조회한다.
        
        부서명에서 기본 부서명을 추출하여 같은 부서 계열의 모든 팀원에게 알림을 보냅니다.
        예: "경영지원팀1팀" → "경영지원팀본부", "경영지원팀1팀", "경영지원팀2팀" 모두 조회
        
        Args:
            employee_id: 사원 ID
        
        Returns:
            팀원 이메일 리스트 (본인 제외)
        """
        import re
        
        # 먼저 해당 사원의 부서 조회
        dept_sql = """
        SELECT dept
        FROM employee
        WHERE employee_id = :employee_id
        LIMIT 1;
        """
        
        async with self._session() as session:
            result = await session.execute(text(dept_sql), {"employee_id": employee_id})
            row = result.mappings().first()
            
            if not row:
                return []
            
            dept = row.get("dept")
            if not dept:
                return []
            
            # 기본 부서명 추출 (패턴 매칭)
            # 예: "경영지원팀1팀" → "경영지원팀", "경영지원팀본부" → "경영지원팀"
            base_dept = dept
            
            # 숫자 + "팀" 패턴 제거 (예: "1팀", "2팀", "10팀")
            base_dept = re.sub(r'\d+팀$', '', base_dept)
            
            # "본부" 제거
            base_dept = re.sub(r'본부$', '', base_dept)
            
            # 공백 제거
            base_dept = base_dept.strip()
            
            if not base_dept:
                # 기본 부서명 추출 실패 시 원본 부서명 사용
                base_dept = dept
            
            # 같은 부서 계열의 다른 사원 이메일 조회 (LIKE 패턴 사용)
            email_sql = """
            SELECT email
            FROM employee
            WHERE dept LIKE :dept_pattern
              AND employee_id != :employee_id
              AND email IS NOT NULL
              AND email != ''
            ORDER BY employee_id;
            """
            
            # LIKE 패턴: "경영지원팀%" 형태
            dept_pattern = f"{base_dept}%"
            
            result = await session.execute(
                text(email_sql),
                {"dept_pattern": dept_pattern, "employee_id": employee_id}
            )
            rows = result.mappings().all()
            
            emails = [row.get("email") for row in rows if row.get("email")]
            return emails

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

    async def get_interviewer_schedules(
        self,
        interviewer_ids: List[int],
        start_date: datetime,
        end_date: datetime
    ) -> Dict[int, List[Dict[str, Any]]]:
        """면접관들의 일정을 조회합니다.
        
        연차/반차, 회의실 예약, 출장 일정을 모두 조인하여 조회합니다.
        
        Args:
            interviewer_ids: 면접관 employee_id 리스트
            start_date: 조회 시작일
            end_date: 조회 종료일
        
        Returns:
            {employee_id: [일정 리스트]} 형식의 딕셔너리
        """
        if not interviewer_ids:
            return {}
        
        # 면접관 ID를 IN 절에 사용할 수 있도록 처리
        placeholders = ",".join([f":id_{i}" for i in range(len(interviewer_ids))])
        params = {f"id_{i}": interviewer_ids[i] for i in range(len(interviewer_ids))}
        params["start_date"] = start_date
        params["end_date"] = end_date
        
        sql = f"""
        SELECT 
            employee_id,
            lr.start_date as start_time,
            lr.end_date as end_time,
            'leave' as schedule_type,
            lr.leave_type as title,
            lr.reason as description
        FROM leave_request lr
        WHERE lr.employee_id IN ({placeholders})
            AND lr.status IN ('대기', '승인')
            AND lr.start_date <= :end_date
            AND lr.end_date >= :start_date
        
        UNION ALL
        
        SELECT 
            mrr.employee_id,
            mrr.start_time,
            mrr.end_time,
            'meeting_room' as schedule_type,
            mrr.title,
            mrr.description
        FROM meeting_room_reservation mrr
        WHERE mrr.employee_id IN ({placeholders})
            AND mrr.status = 'CONFIRMED'
            AND mrr.start_time < :end_date
            AND mrr.end_time > :start_date
        
        UNION ALL
        
        SELECT 
            bt.employee_id,
            bt.start_time,
            bt.end_time,
            'business_trip' as schedule_type,
            bt.trip_title as title,
            bt.trip_purpose as description
        FROM business_trip bt
        WHERE bt.employee_id IN ({placeholders})
            AND bt.status IN ('대기', '승인')
            AND bt.start_time <= :end_date
            AND bt.end_time >= :start_date
        
        ORDER BY employee_id, start_time
        """
        
        async with self._session() as session:
            result = await session.execute(text(sql), params)
            rows = result.mappings().all()
            
            # ✅ 면접 일정 별도 조회 (각 면접관별로)
            # schedule 테이블에서 interviewer_ids에 포함된 면접 일정 조회
            interview_sql = """
            SELECT 
                s.employee_id,
                s.start_time,
                s.end_time,
                'interview' as schedule_type,
                s.title,
                s.description
            FROM schedule s
            WHERE s.schedule_type = 'interview'
                AND s.start_time <= :end_date
                AND s.end_time >= :start_date
                AND s.status = 'active'
                AND s.interviewer_ids IS NOT NULL
                AND JSON_CONTAINS(s.interviewer_ids, :emp_id_json)
            """
            
            # 각 면접관별로 면접 일정 조회
            for emp_id in interviewer_ids:
                # ✅ emp_id를 JSON 숫자로 변환 (예: 4001 -> JSON "4001")
                # json.dumps()를 사용하면 숫자는 JSON 숫자로 변환됨
                emp_id_json = json.dumps(emp_id)
                
                interview_params = {
                    "emp_id_json": emp_id_json,  # ✅ JSON 문자열로 변환된 값 사용
                    "start_date": start_date,
                    "end_date": end_date
                }
                try:
                    interview_result = await session.execute(text(interview_sql), interview_params)
                    interview_rows = interview_result.mappings().all()
                    
                    # 각 면접 일정을 해당 면접관의 일정으로 추가
                    for interview_row in interview_rows:
                        interview_data = dict(interview_row)
                        # employee_id를 현재 면접관 ID로 설정 (interviewer_ids에 포함된 면접관이므로)
                        interview_data["employee_id"] = emp_id
                        rows.append(interview_data)
                except Exception as e:
                    # JSON_CONTAINS 오류 시 무시 (interviewer_ids가 NULL이거나 잘못된 형식인 경우)
                    print(f"⚠️ 면접 일정 조회 중 오류 (employee_id={emp_id}): {e}")
                    continue
        
        # employee_id별로 그룹화
        schedules_by_interviewer: Dict[int, List[Dict[str, Any]]] = {}
        for row in rows:
            emp_id = row["employee_id"]
            if emp_id not in schedules_by_interviewer:
                schedules_by_interviewer[emp_id] = []
            schedules_by_interviewer[emp_id].append(dict(row))
        
        return schedules_by_interviewer