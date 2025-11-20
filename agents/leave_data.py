from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Literal, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .schedule_data import ScheduleDatabaseError, get_session

LEAVE_BALANCE_TABLE = os.getenv("LEAVE_BALANCE_TABLE", "leave_balance_v2")
LEAVE_REQUEST_TABLE = os.getenv("LEAVE_REQUEST_TABLE", "leave_request")

# v2 데이터셋 경로
LEAVE_BALANCE_V2_PATH = os.path.join(ROOT_DIR, "leave_balance_v2.csv")
LEAVE_REQUEST_V2_PATH = os.path.join(ROOT_DIR, "leave_request_v2.csv")

def _run(coro):
    """비동기 코루틴을 동기적으로 실행한다. 이벤트 루프가 이미 실행 중이면 별도 스레드에서 실행한다."""
    import concurrent.futures
    import warnings
    import logging
    import sys
    from io import StringIO

    # SQLAlchemy 연결 종료 시 발생하는 경고/오류 억제
    logging.getLogger("sqlalchemy.pool").setLevel(logging.ERROR)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.ERROR)

    # 현재 실행 중인 이벤트 루프 확인
    try:
        loop = asyncio.get_running_loop()
        # 실행 중인 루프가 있으면 별도 스레드에서 실행
        def run_in_thread():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            old_stderr = sys.stderr
            try:
                sys.stderr = StringIO()
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*event loop.*")
                    warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*proactor.*")
                    warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*AttributeError.*")
                    result = new_loop.run_until_complete(coro)
                    return result
            finally:
                sys.stderr = old_stderr
                try:
                    new_loop.close()
                except Exception:
                    pass  # 연결 종료 오류 무시
                asyncio.set_event_loop(None)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            return future.result(timeout=30)  # 30초 타임아웃
    except RuntimeError as e:
        # "no running event loop"는 정상적인 경우이므로 무시하고 asyncio.run 사용
        if "no running event loop" in str(e).lower():
            # 실행 중인 루프가 없으면 asyncio.run 사용
            try:
                old_stderr = sys.stderr
                try:
                    sys.stderr = StringIO()
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*event loop.*")
                        warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*proactor.*")
                        warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*AttributeError.*")
                        result = asyncio.run(coro)
                        return result
                finally:
                    sys.stderr = old_stderr
            except RuntimeError as exc:
                if "event loop" not in str(exc).lower() and "Cannot run" not in str(exc):
                    raise
                # 이벤트 루프 문제인 경우 별도 스레드에서 실행
                def run_in_thread():
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    old_stderr = sys.stderr
                    try:
                        sys.stderr = StringIO()
                        with warnings.catch_warnings():
                            warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*event loop.*")
                            warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*proactor.*")
                            warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*AttributeError.*")
                            result = new_loop.run_until_complete(coro)
                            return result
                    finally:
                        sys.stderr = old_stderr
                        try:
                            new_loop.close()
                        except Exception:
                            pass  # 연결 종료 오류 무시
                        asyncio.set_event_loop(None)

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(run_in_thread)
                    return future.result(timeout=30)
        else:
            # 다른 RuntimeError는 재발생
            raise


def make_request_id(employee_id: int) -> str:
    ts = int(time.time())
    return f"LR{datetime.now().year}-{employee_id}-{ts}"


def _normalize_balance_row(row: dict) -> dict:
    if not row:
        return {}
    normalized = dict(row)
    float_cols = [
        "total_entitlement_days",
        "carried_over_days",
        "used_days",
        "pending_days",
        "remaining_days",
    ]
    for col in float_cols:
        if col in normalized and normalized[col] is not None:
            normalized[col] = float(normalized[col])
    if "year" in normalized and normalized["year"] is not None:
        normalized["year"] = int(normalized["year"])
    if "employee_id" in normalized and normalized["employee_id"] is not None:
        normalized["employee_id"] = int(normalized["employee_id"])
    return normalized


async def _fetch_balance_row(session, employee_id: int) -> Optional[dict]:
    """leave_balance_v2에서 잔여 연차를 조회하고 employee 테이블과 JOIN하여 이름 포함."""
    # employee 테이블과 JOIN하여 이름 포함
    result = await session.execute(
        text(
            f"""
            SELECT
                {LEAVE_BALANCE_TABLE}.*,
                employee.name AS employee_name
            FROM {LEAVE_BALANCE_TABLE}
            LEFT JOIN employee ON {LEAVE_BALANCE_TABLE}.employee_id = employee.employee_id
            WHERE {LEAVE_BALANCE_TABLE}.employee_id = :employee_id
            LIMIT 1
            """
        ),
        {"employee_id": employee_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None

    if (bal["employee_id"] == employee_id).any():
        return bal

async def _compute_used_pending(session, employee_id: int) -> Tuple[float, float]:
    query = text(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN status = '승인' THEN total_days ELSE 0 END), 0) AS used_days,
            COALESCE(SUM(CASE WHEN status = '대기' THEN total_days ELSE 0 END), 0) AS pending_days
        FROM {LEAVE_REQUEST_TABLE}
        WHERE employee_id = :employee_id
        """
    )
    result = await session.execute(query, {"employee_id": employee_id})
    row = result.first()
    if not row:
        return 0.0, 0.0
    used = float(row.used_days or 0.0)
    pending = float(row.pending_days or 0.0)
    return used, pending


async def _insert_balance_row(
    session,
    employee_id: int,
    used: float,
    pending: float,
    default_entitlement: float,
) -> None:
    remaining = float(default_entitlement) - used - pending
    await session.execute(
        text(
            f"""
            INSERT INTO {LEAVE_BALANCE_TABLE} (
                employee_id,
                year,
                total_entitlement_days,
                carried_over_days,
                used_days,
                pending_days,
                remaining_days
            )
            VALUES (
                :employee_id,
                :year,
                :total_entitlement_days,
                :carried_over_days,
                :used_days,
                :pending_days,
                :remaining_days
            )
            """
        ),
        {
            "employee_id": employee_id,
            "year": datetime.now().year,
            "total_entitlement_days": float(default_entitlement),
            "carried_over_days": 0.0,
            "used_days": used,
            "pending_days": pending,
            "remaining_days": remaining,
        },
    )


async def _ensure_balance_row_async(
    employee_id: int,
    default_entitlement: float = 15.0,
) -> dict:
    async with get_session() as session:
        try:
            row = await _fetch_balance_row(session, employee_id)
            if row:
                return _normalize_balance_row(row)

            used, pending = await _compute_used_pending(session, employee_id)
            await _insert_balance_row(session, employee_id, used, pending, default_entitlement)
            await session.commit()
            row = await _fetch_balance_row(session, employee_id)
            return _normalize_balance_row(row or {})
        except SQLAlchemyError as exc:
            await session.rollback()
            raise ScheduleDatabaseError(f"휴가 잔여 데이터를 초기화하지 못했습니다: {exc}") from exc


def ensure_balance_row(employee_id: int, default_entitlement: float = 15.0) -> dict:
    return _run(_ensure_balance_row_async(employee_id, default_entitlement))


async def _get_balance_async(employee_id: int) -> Optional[dict]:
    async with get_session() as session:
        try:
            row = await _fetch_balance_row(session, employee_id)
            return _normalize_balance_row(row or {})
        except SQLAlchemyError as exc:
            raise ScheduleDatabaseError(f"휴가 잔여 데이터를 조회하지 못했습니다: {exc}") from exc


def get_balance(employee_id: int) -> Optional[dict]:
    result = ensure_balance_row(employee_id)
    return result or _run(_get_balance_async(employee_id))


async def _update_balance_from_requests_async(employee_id: int) -> dict:
    async with get_session() as session:
        try:
            row = await _fetch_balance_row(session, employee_id)
            if not row:
                row = await _ensure_balance_row_async(employee_id)
                return row

            used, pending = await _compute_used_pending(session, employee_id)
            total = float(row.get("total_entitlement_days") or 0.0)
            remaining = total - used - pending
            await session.execute(
                text(
                    f"""
                    UPDATE {LEAVE_BALANCE_TABLE}
                    SET used_days = :used_days,
                        pending_days = :pending_days,
                        remaining_days = :remaining_days
                    WHERE employee_id = :employee_id
                    """
                ),
                {
                    "used_days": used,
                    "pending_days": pending,
                    "remaining_days": remaining,
                    "employee_id": employee_id,
                },
            )
            await session.commit()
            row = await _fetch_balance_row(session, employee_id)
            return _normalize_balance_row(row or {})
        except SQLAlchemyError as exc:
            await session.rollback()
            raise ScheduleDatabaseError(f"휴가 잔여 데이터를 갱신하지 못했습니다: {exc}") from exc


def update_balance_from_requests(employee_id: int) -> dict:
    return _run(_update_balance_from_requests_async(employee_id))


async def _create_leave_request_async(
    employee_id: int,
    leave_type: Literal["연차", "반차", "병가", "경조사"],
    start_date: str,
    end_date: str,
    total_days: float,
    reason: str,
    status: Literal["대기", "승인", "반려", "취소"],
) -> dict:
    async with get_session() as session:
        try:
            request = {
                "request_id": make_request_id(employee_id),
                "employee_id": int(employee_id),
                "request_date": datetime.now().strftime("%Y-%m-%d"),
                "leave_type": leave_type,
                "start_date": start_date,
                "end_date": end_date,
                "total_days": float(total_days),
                "status": status,
                "reason": reason,
            }
            await session.execute(
                text(
                    f"""
                    INSERT INTO {LEAVE_REQUEST_TABLE} (
                        request_id,
                        employee_id,
                        request_date,
                        leave_type,
                        start_date,
                        end_date,
                        total_days,
                        status,
                        reason
                    ) VALUES (
                        :request_id,
                        :employee_id,
                        :request_date,
                        :leave_type,
                        :start_date,
                        :end_date,
                        :total_days,
                        :status,
                        :reason
                    )
                    """
                ),
                request,
            )
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            raise ScheduleDatabaseError(f"휴가 요청을 저장하지 못했습니다: {exc}") from exc

    balance = await _update_balance_from_requests_async(employee_id)
    return {"request": request, "balance": balance}


def create_leave_request(
    employee_id: int,
    leave_type: Literal["연차", "반차", "병가", "경조사"],
    start_date: str,
    end_date: str,
    total_days: float,
    reason: str = "개인 사정",
    status: Literal["대기", "승인", "반려", "취소"] = "대기",
) -> dict:
    return _run(
        _create_leave_request_async(
            employee_id=employee_id,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            total_days=total_days,
            reason=reason,
            status=status,
        )
    )

