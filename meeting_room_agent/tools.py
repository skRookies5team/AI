"""MariaDB 비동기 유틸리티 모듈."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Mapping, Sequence

import aiomysql
from aiomysql.pool import Pool

from meeting_room_agent.settings import settings

__all__: list[str] = ["DBPool"]

LOGGER = logging.getLogger(__name__)


class DBPool:
    """MariaDB 비동기 커넥션 풀 관리 헬퍼."""

    pool: ClassVar[Pool | None] = None

    @classmethod
    async def init_pool(cls) -> None:
        """커넥션 풀을 초기화한다."""

        if cls.pool is not None:
            LOGGER.debug("DB 커넥션 풀이 이미 초기화되었습니다.")
            return

        cls.pool = await aiomysql.create_pool(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            db=settings.db_name,
            autocommit=True,
            charset="utf8mb4",
        )
        LOGGER.info("MariaDB 커넥션 풀 초기화 완료 (host=%s, db=%s)", settings.db_host, settings.db_name)

    @classmethod
    async def close_pool(cls) -> None:
        """커넥션 풀을 종료한다."""

        if cls.pool is None:
            LOGGER.debug("종료할 커넥션 풀이 없습니다.")
            return

        cls.pool.close()
        await cls.pool.wait_closed()
        cls.pool = None
        LOGGER.info("MariaDB 커넥션 풀 종료 완료")

    @classmethod
    async def fetch(
        cls,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """쿼리를 실행하고 결과를 반환한다."""

        pool = cls._require_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params or ())
                rows = await cur.fetchall()
                LOGGER.debug("쿼리 실행 완료 (rows=%d)", len(rows))
                return rows

    @classmethod
    async def execute(
        cls,
        sql: str,
        params: Sequence[Any] | Mapping[str, Any] | None = None,
    ) -> int:
        """INSERT/UPDATE/DELETE 쿼리를 실행한다."""

        pool = cls._require_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                result = await cur.execute(sql, params or ())
                await conn.commit()
                LOGGER.debug("변경 쿼리 실행 완료 (affected=%d)", cur.rowcount)
                return result

    @classmethod
    def _require_pool(cls) -> Pool:
        """초기화된 커넥션 풀을 반환한다.

        Raises:
            RuntimeError: 커넥션 풀이 초기화되지 않은 경우.
        """

        if cls.pool is None:
            raise RuntimeError("DBPool.init_pool()을 먼저 호출해야 합니다.")
        return cls.pool

