from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncGenerator, Optional

from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class ScheduleDatabaseError(RuntimeError):
    """스케줄 DB 접근 시 발생하는 예외."""


@dataclass(slots=True)
class ScheduleFilters:
    """스케줄 조회 필터 조건."""

    employee_id: Optional[int] = None
    employee_ids: Optional[list[int]] = None
    employee_name_keywords: Optional[list[str]] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    keyword: Optional[str] = None
    schedule_types: Optional[list[str]] = None
    limit: int = 20


_ENGINE: Optional[AsyncEngine] = None
_SESSION_FACTORY: Optional[async_sessionmaker[AsyncSession]] = None
_ENGINE_LOCK = asyncio.Lock()
_COLUMNS_LOCK = asyncio.Lock()
_SCHEDULE_COLUMNS: Optional[list[str]] = None


_DEFAULT_DB_NAME = os.getenv("SCHEDULE_DB_NAME", "ai_agent")
_EMPLOYEE_TABLE_NAME = os.getenv("EMPLOYEE_TABLE_NAME", "employee")
_BUSINESS_TRIP_TABLE_NAME = os.getenv("BUSINESS_TRIP_TABLE_NAME", "business_trip")
_EMPLOYEE_COLUMNS_CACHE: Optional[list[str]] = None
_EMPLOYEE_COLUMNS_LOCK = asyncio.Lock()

_DEFAULT_START_CANDIDATES = [
    os.getenv("SCHEDULE_DB_START_COLUMN"),
    "start_at",
    "start_time",
    "start_datetime",
    "start_date",
    "begin_at",
    "begin_time",
]

_DEFAULT_END_CANDIDATES = [
    os.getenv("SCHEDULE_DB_END_COLUMN"),
    "end_at",
    "end_time",
    "end_datetime",
    "end_date",
    "finish_at",
    "finish_time",
]

_DEFAULT_EMPLOYEE_CANDIDATES = [
    os.getenv("SCHEDULE_DB_EMPLOYEE_COLUMN"),
    "employee_id",
    "employee_no",
    "employee",
    "emp_id",
    "user_id",
]

_DEFAULT_TYPE_CANDIDATES = [
    os.getenv("SCHEDULE_DB_TYPE_COLUMN"),
    "schedule_type",
    "type",
    "category",
    "kind",
]

_EMPLOYEE_ID_CANDIDATES = [
    os.getenv("EMPLOYEE_ID_COLUMN"),
    "employee_id",
    "emp_id",
    "id",
]

_EMPLOYEE_NAME_CANDIDATES = [
    os.getenv("EMPLOYEE_NAME_COLUMN"),
    "employee_name",
    "name",
    "emp_name",
]

_ORDER_COLUMN_CANDIDATES = [
    os.getenv("SCHEDULE_DB_ORDER_COLUMN"),
    *[c for c in _DEFAULT_START_CANDIDATES if c],
    "created_at",
    "updated_at",
]


def _build_async_db_url() -> str:
    """환경변수 기반으로 MariaDB 비동기 접속 URL을 생성한다."""
    explicit_url = os.getenv("SCHEDULE_DB_URL")
    if explicit_url:
        return explicit_url

    user = os.getenv("SCHEDULE_DB_USER", "root")
    password = os.getenv("SCHEDULE_DB_PASSWORD", "root")
    host = os.getenv("HOST", "175.120.35.228")
    port = os.getenv("SCHEDULE_DB_PORT", "3306")
    database = os.getenv("SCHEDULE_DB_NAME", "ai_agent")

    return f"mysql+asyncmy://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


async def _get_engine() -> AsyncEngine:
    """싱글톤 패턴으로 AsyncEngine을 생성한다."""
    import logging
    
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is not None:
        return _ENGINE

    async with _ENGINE_LOCK:
        if _ENGINE is None:
            # SQLAlchemy 연결 종료 시 발생하는 경고/오류 억제
            sqlalchemy_loggers = [
                "sqlalchemy.pool",
                "sqlalchemy.engine",
                "sqlalchemy.dialects",
                "sqlalchemy.connectors",
                "sqlalchemy.util",
                "sqlalchemy",
            ]
            for logger_name in sqlalchemy_loggers:
                logger = logging.getLogger(logger_name)
                logger.setLevel(logging.CRITICAL)
                logger.handlers = []
                logger.propagate = False
            
            _ENGINE = create_async_engine(
                _build_async_db_url(),
                echo=os.getenv("SCHEDULE_DB_ECHO", "false").lower() == "true",
                pool_pre_ping=True,
                pool_reset_on_return='commit',  # 연결 종료 시 더 안전하게 처리
            )
            _SESSION_FACTORY = async_sessionmaker(_ENGINE, expire_on_commit=False)
    return _ENGINE


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """비동기 DB 세션 context manager."""
    await _get_engine()
    if _SESSION_FACTORY is None:
        raise ScheduleDatabaseError("세션 팩토리 초기화에 실패했습니다.")
    session = _SESSION_FACTORY()
    try:
        yield session
    finally:
        try:
            await session.close()
        except (AttributeError, RuntimeError, Exception) as e:
            # 연결 종료 시 발생하는 오류는 무시 (이벤트 루프 종료 관련)
            # 'NoneType' object has no attribute 'send' 등의 오류는 무시
            error_str = str(e).lower()
            if any(keyword in error_str for keyword in [
                "none", "attribute", "send", "proactor", "event loop", 
                "terminate", "closed", "connection"
            ]):
                pass  # 예상된 오류는 무시
            else:
                # 예상치 못한 오류는 로깅 (선택적)
                import logging
                logger = logging.getLogger(__name__)
                logger.debug(f"세션 종료 중 예상치 못한 오류 (무시됨): {e}")


async def _get_schedule_columns(session: AsyncSession) -> list[str]:
    """schedule 테이블의 컬럼 목록을 조회 및 캐시한다."""
    global _SCHEDULE_COLUMNS
    if _SCHEDULE_COLUMNS is not None:
        return _SCHEDULE_COLUMNS

    async with _COLUMNS_LOCK:
        if _SCHEDULE_COLUMNS is not None:
            return _SCHEDULE_COLUMNS
        result = await session.execute(text("SHOW COLUMNS FROM schedule"))
        rows = result.fetchall()
        _SCHEDULE_COLUMNS = [row[0] for row in rows]
    return _SCHEDULE_COLUMNS


def _match_column(columns: list[str], candidates: list[Optional[str]]) -> Optional[str]:
    """컬럼 후보 목록과 실제 컬럼 목록을 비교해 가장 잘 맞는 이름을 찾는다."""
    normalized_candidates = [c for c in candidates if c]
    for candidate in normalized_candidates:
        if candidate in columns:
            return candidate
    for candidate in normalized_candidates:
        lowered_candidate = candidate.lower()
        for column in columns:
            if lowered_candidate in column.lower():
                return column
    return None


def _select_keyword_columns(columns: list[str]) -> list[str]:
    """키워드 검색에 사용할 적절한 컬럼 목록을 선택한다."""
    env_columns = os.getenv("SCHEDULE_DB_KEYWORD_COLUMNS")
    if env_columns:
        explicit = [col.strip() for col in env_columns.split(",") if col.strip()]
        return [col for col in explicit if col in columns]

    preferred = ["title", "description", "location", "subject", "content", "memo", "details"]
    matched = [col for col in preferred if col in columns]
    if matched:
        return matched

    token_candidates = ["title", "desc", "subject", "content", "memo", "note"]
    fallback = []
    for column in columns:
        lower = column.lower()
        if any(token in lower for token in token_candidates):
            fallback.append(column)
    return fallback


async def _get_employee_columns(session: AsyncSession) -> list[str]:
    """employee 테이블의 컬럼 목록을 조회한다."""
    global _EMPLOYEE_COLUMNS_CACHE
    if _EMPLOYEE_COLUMNS_CACHE is not None:
        return _EMPLOYEE_COLUMNS_CACHE

    async with _EMPLOYEE_COLUMNS_LOCK:
        if _EMPLOYEE_COLUMNS_CACHE is not None:
            return _EMPLOYEE_COLUMNS_CACHE
        result = await session.execute(text(f"SHOW COLUMNS FROM {_EMPLOYEE_TABLE_NAME}"))
        rows = result.fetchall()
        _EMPLOYEE_COLUMNS_CACHE = [row[0] for row in rows]
    return _EMPLOYEE_COLUMNS_CACHE


async def _resolve_employee_ids_by_names(session: AsyncSession, keywords: list[str]) -> list[int]:
    """사원 이름 목록을 employee_id 목록으로 변환한다."""
    columns = await _get_employee_columns(session)
    if not columns or not keywords:
        return []

    id_column = _match_column(columns, _EMPLOYEE_ID_CANDIDATES)
    name_column = _match_column(columns, _EMPLOYEE_NAME_CANDIDATES)
    if not id_column or not name_column:
        return []

    params: dict[str, Any] = {}
    clauses: list[str] = []
    for idx, keyword in enumerate(keywords):
        key = f"name_{idx}"
        params[key] = f"%{keyword}%"
        clauses.append(f"{name_column} LIKE :{key}")

    if not clauses:
        return []

    query = text(
        f"SELECT DISTINCT {id_column} FROM {_EMPLOYEE_TABLE_NAME} "
        f"WHERE {' OR '.join(clauses)} LIMIT 100"
    )

    result = await session.execute(query, params)
    ids: list[int] = []
    for row in result.fetchall():
        value = row[0]
        if value is None:
            continue
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _derive_order_candidates(columns: list[str], preferred: Optional[str]) -> list[Optional[str]]:
    """정렬에 사용할 컬럼 후보를 정한다."""
    candidates: list[Optional[str]] = []

    for candidate in [preferred, *_ORDER_COLUMN_CANDIDATES]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for column in columns:
        if column not in candidates and any(token in column.lower() for token in ["start", "begin", "date", "created"]):
            candidates.append(column)

    if None not in candidates:
        candidates.append(None)
    return candidates


def _build_query(
    filters: ScheduleFilters, 
    columns: list[str], 
    employee_columns: Optional[list[str]] = None
) -> tuple[str, dict[str, Any], Optional[str]]:
    """필터 조건을 반영한 SQL 쿼리 문자열과 파라미터, 기본 정렬 컬럼을 생성한다.
    
    Args:
        filters: 조회 필터
        columns: schedule 테이블 컬럼 목록
        employee_columns: employee 테이블 컬럼 목록 (JOIN 시 사용)
    """
    # employee 테이블과 JOIN하여 이름 포함
    employee_id_col = None
    employee_name_col = None
    schedule_employee_col = _match_column(columns, _DEFAULT_EMPLOYEE_CANDIDATES)
    
    if employee_columns and schedule_employee_col:
        employee_id_col = _match_column(employee_columns, _EMPLOYEE_ID_CANDIDATES)
        employee_name_col = _match_column(employee_columns, _EMPLOYEE_NAME_CANDIDATES)
    
    if employee_id_col and employee_name_col and schedule_employee_col:
        # JOIN 쿼리: employee 이름 포함
        query = (
            f"SELECT schedule.*, employee.{employee_name_col} AS employee_name "
            f"FROM schedule "
            f"LEFT JOIN {_EMPLOYEE_TABLE_NAME} employee "
            f"ON schedule.{schedule_employee_col} = employee.{employee_id_col} "
            f"WHERE 1=1"
        )
    else:
        # JOIN 불가 시 기본 쿼리
        query = "SELECT * FROM schedule WHERE 1=1"
    
    params: dict[str, Any] = {}

    # WHERE 절에서 employee_id 필터링
    employee_column = schedule_employee_col or _match_column(columns, _DEFAULT_EMPLOYEE_CANDIDATES)
    table_prefix = "schedule." if employee_id_col and employee_name_col and schedule_employee_col else ""
    
    if filters.employee_ids and employee_column:
        placeholders: list[str] = []
        unique_ids = [int(v) for v in filters.employee_ids if v is not None]
        for idx, value in enumerate(unique_ids):
            key = f"employee_id_{idx}"
            placeholders.append(f":{key}")
            params[key] = value
        if placeholders:
            in_clause = ", ".join(placeholders)
            query += f" AND {table_prefix}{employee_column} IN ({in_clause})"
    elif filters.employee_id is not None and employee_column:
        query += f" AND {table_prefix}{employee_column} = :employee_id"
        params["employee_id"] = filters.employee_id

    start_column = _match_column(columns, _DEFAULT_START_CANDIDATES)
    if filters.start_at is not None and start_column:
        query += f" AND {table_prefix}{start_column} >= :start_at"
        params["start_at"] = filters.start_at

    end_column = _match_column(columns, _DEFAULT_END_CANDIDATES)
    if filters.end_at is not None and end_column:
        query += f" AND {table_prefix}{end_column} <= :end_at"
        params["end_at"] = filters.end_at

    if filters.keyword:
        keyword_columns = _select_keyword_columns(columns)
        if keyword_columns:
            like_clauses = " OR ".join(f"{table_prefix}{col} LIKE :keyword" for col in keyword_columns)
            query += f" AND ({like_clauses})"
            params["keyword"] = f"%{filters.keyword}%"

    if filters.schedule_types:
        schedule_type_column = _match_column(columns, _DEFAULT_TYPE_CANDIDATES)
        title_column = _match_column(columns, ["title", "subject", "name"])
        
        if schedule_type_column or title_column:
            valid_types = [value for value in filters.schedule_types if value]
            if valid_types:
                # IN 절과 LIKE 패턴을 모두 사용하여 더 유연한 매칭
                in_placeholders: list[str] = []
                like_clauses: list[str] = []
                title_like_clauses: list[str] = []
                
                for index, value in enumerate(valid_types):
                    param_key = f"type_{index}"
                    # 정확한 매칭을 위한 IN 절 (schedule_type 컬럼이 있는 경우만)
                    if schedule_type_column:
                        in_placeholders.append(f":{param_key}")
                        params[param_key] = value
                        
                        # 부분 매칭을 위한 LIKE 절 (예: "반차"가 "반차(오전)"과 매칭)
                        like_key = f"type_like_{index}"
                        like_clauses.append(f"{table_prefix}{schedule_type_column} LIKE :{like_key}")
                        # 공백을 %로 변환하여 유연한 매칭 (예: "반차 오전" -> "%반차%오전%")
                        # 단, 공백이 앞이나 뒤에 있으면 제거
                        like_pattern = value.strip().replace(" ", "%")
                        params[like_key] = f"%{like_pattern}%"
                    
                    # title 컬럼도 함께 검색 (반차/병가가 title에 저장되어 있을 수 있음)
                    if title_column:
                        title_like_key = f"title_like_{index}"
                        title_like_clauses.append(f"{table_prefix}{title_column} LIKE :{title_like_key}")
                        title_like_pattern = value.strip().replace(" ", "%")
                        params[title_like_key] = f"%{title_like_pattern}%"
                
                # IN 절과 LIKE 절을 OR로 결합
                conditions = []
                if in_placeholders:
                    in_clause = ", ".join(in_placeholders)
                    conditions.append(f"{table_prefix}{schedule_type_column} IN ({in_clause})")
                if like_clauses:
                    conditions.extend(like_clauses)
                if title_like_clauses:
                    conditions.extend(title_like_clauses)
                
                if conditions:
                    query += f" AND ({' OR '.join(conditions)})"

    return query, params, start_column, table_prefix


async def fetch_business_trip_rows(
    employee_id: Optional[int] = None,
    employee_ids: Optional[list[int]] = None,
    employee_name_keywords: Optional[list[str]] = None,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    keyword: Optional[str] = None,
    destination: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """business_trip 테이블에서 출장 데이터를 조회한다.
    
    Args:
        employee_id: 직원 ID (단일)
        employee_ids: 직원 ID 목록
        employee_name_keywords: 직원 이름 키워드 목록
        start_at: 시작 날짜/시간 필터
        end_at: 종료 날짜/시간 필터
        keyword: 제목/목적 검색 키워드
        destination: 목적지 필터
        limit: 최대 조회 개수
        
    Returns:
        출장 데이터 리스트
    """
    async with get_session() as session:
        try:
            # employee 테이블과 JOIN하여 이름 포함
            employee_columns = await _get_employee_columns(session)
            employee_id_col = _match_column(employee_columns, _EMPLOYEE_ID_CANDIDATES)
            employee_name_col = _match_column(employee_columns, _EMPLOYEE_NAME_CANDIDATES)
            
            if employee_id_col and employee_name_col:
                query = (
                    f"SELECT bt.*, employee.{employee_name_col} AS employee_name "
                    f"FROM {_BUSINESS_TRIP_TABLE_NAME} bt "
                    f"LEFT JOIN {_EMPLOYEE_TABLE_NAME} employee "
                    f"ON bt.employee_id = employee.{employee_id_col} "
                    f"WHERE 1=1"
                )
                table_prefix = "bt."
            else:
                query = f"SELECT * FROM {_BUSINESS_TRIP_TABLE_NAME} bt WHERE 1=1"
                table_prefix = "bt."
            
            params: dict[str, Any] = {}
            
            # employee_id 필터
            if employee_ids:
                placeholders: list[str] = []
                unique_ids = [int(v) for v in employee_ids if v is not None]
                for idx, value in enumerate(unique_ids):
                    key = f"employee_id_{idx}"
                    placeholders.append(f":{key}")
                    params[key] = value
                if placeholders:
                    in_clause = ", ".join(placeholders)
                    query += f" AND {table_prefix}employee_id IN ({in_clause})"
            elif employee_id is not None:
                query += f" AND {table_prefix}employee_id = :employee_id"
                params["employee_id"] = employee_id
            
            # employee_name_keywords 필터 (JOIN이 가능한 경우)
            # employee_id가 없고 employee_name_keywords가 있을 때만 이름으로 필터링
            if employee_name_keywords and employee_name_col and employee_id is None:
                name_clauses: list[str] = []
                for idx, keyword in enumerate(employee_name_keywords):
                    key = f"name_{idx}"
                    params[key] = f"%{keyword}%"
                    name_clauses.append(f"employee.{employee_name_col} LIKE :{key}")
                if name_clauses:
                    query += f" AND ({' OR '.join(name_clauses)})"
            
            # 날짜 필터
            if start_at is not None:
                query += f" AND {table_prefix}start_time >= :start_at"
                params["start_at"] = start_at
            
            if end_at is not None:
                query += f" AND {table_prefix}end_time <= :end_at"
                params["end_at"] = end_at
            
            # 키워드 검색 (제목, 목적)
            if keyword:
                query += f" AND ({table_prefix}trip_title LIKE :keyword OR {table_prefix}trip_purpose LIKE :keyword)"
                params["keyword"] = f"%{keyword}%"
            
            # 목적지 필터
            if destination:
                query += f" AND {table_prefix}destination LIKE :destination"
                params["destination"] = f"%{destination}%"
            
            # 정렬 및 제한
            query += f" ORDER BY {table_prefix}start_time DESC LIMIT :limit"
            params["limit"] = limit
            
            result = await session.execute(text(query), params)
            rows = result.mappings().all()
            
            # 딕셔너리로 변환
            trips = []
            for row in rows:
                trip = dict(row)
                # 날짜 형식 정규화
                for date_col in ["start_time", "end_time", "created_at", "alert_time"]:
                    if date_col in trip and trip[date_col] is not None:
                        if isinstance(trip[date_col], str):
                            trip[date_col] = trip[date_col]
                        else:
                            trip[date_col] = str(trip[date_col])
                # 숫자 형식 정규화
                if "trip_id" in trip and trip["trip_id"] is not None:
                    trip["trip_id"] = int(trip["trip_id"])
                if "employee_id" in trip and trip["employee_id"] is not None:
                    trip["employee_id"] = int(trip["employee_id"])
                trips.append(trip)
            
            return trips
        except SQLAlchemyError as exc:
            raise ScheduleDatabaseError(f"출장 데이터를 조회하지 못했습니다: {exc}") from exc


async def fetch_schedule_rows(filters: ScheduleFilters) -> list[dict[str, Any]]:
    """스케줄 테이블에서 조건에 맞는 행을 조회한다.

    Args:
        filters: 조회 조건

    Returns:
        스케줄 행들의 리스트

    Raises:
        ScheduleDatabaseError: DB 접속 혹은 쿼리 실행 실패
    """
    limit_value = max(1, min(filters.limit, 200))

    try:
        async with get_session() as session:
            # 이름 검색은 load_schedule에서 이미 수행되었으므로 여기서는 중복 검색하지 않음
            # employee_name_keywords가 있지만 employee_ids가 없는 경우는 이미 상위에서 처리됨

            columns = await _get_schedule_columns(session)
            employee_columns = await _get_employee_columns(session)
            query, params, preferred_order, table_prefix = _build_query(filters, columns, employee_columns)
            params["limit_value"] = limit_value
            order_candidates = _derive_order_candidates(columns, preferred_order)
            
            last_error: Optional[Exception] = None
            for order_column in order_candidates:
                if order_column:
                    order_clause = f" ORDER BY {table_prefix}{order_column} ASC"
                else:
                    order_clause = ""
                final_query = f"{query}{order_clause} LIMIT :limit_value"
                try:
                    result = await session.execute(text(final_query), params)
                    return [dict(row) for row in result.mappings().all()]
                except OperationalError as exc:
                    message = str(exc.orig if hasattr(exc, "orig") else exc)
                    if order_column and "Unknown column" in message:
                        last_error = exc
                        continue
                    raise ScheduleDatabaseError(f"스케줄 데이터를 조회하지 못했습니다: {exc}") from exc
                except SQLAlchemyError as exc:
                    raise ScheduleDatabaseError(f"스케줄 데이터를 조회하지 못했습니다: {exc}") from exc
            if last_error:
                raise ScheduleDatabaseError(
                    "스케줄 테이블에 사용 가능한 정렬 컬럼이 없습니다. 환경 변수를 확인해 주세요."
                ) from last_error
            return []
    except SQLAlchemyError as exc:
        raise ScheduleDatabaseError(f"스케줄 데이터를 조회하지 못했습니다: {exc}") from exc
    except ConnectionError as exc:  # pragma: no cover - defensive
        raise ScheduleDatabaseError(f"DB 연결에 실패했습니다: {exc}") from exc
    return []


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """ISO 포맷 문자열을 datetime 객체로 변환한다."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ScheduleDatabaseError(f"유효하지 않은 날짜 형식입니다: {value}") from exc

