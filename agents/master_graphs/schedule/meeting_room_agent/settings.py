"""회의실 에이전트 환경 설정 모듈."""

from __future__ import annotations

import os
from typing import Final, Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__: list[str] = ["Settings", "settings"]


def _get_env_trimmed(key: str, default: str = "") -> str:
    """환경 변수 조회 (공백 제거)."""
    value = os.getenv(key, default)
    return value.strip() if value and isinstance(value, str) else default


class Settings(BaseSettings):
    """환경 변수 기반 회의실 에이전트 설정 로더.

    .env 파일에서 다음 환경 변수를 읽습니다:
    - HOST 또는 DB_HOST: DB 호스트 주소
    - DB_USERNAME 또는 DB_USER: DB 사용자명
    - DB_PASSWORD: DB 비밀번호
    - DB_NAME: DB 이름
    - DB_PORT: DB 포트
    - TIMEZONE: 시간대
    - OPENAI_API_KEY: OpenAI API 키

    Attributes:
        db_host: DB 호스트 주소 (.env의 HOST 또는 DB_HOST, 기본값: 175.114.229.152)
        db_user: DB 사용자명 (.env의 DB_USERNAME 또는 DB_USER, 기본값: root)
        db_password: DB 비밀번호 (.env의 DB_PASSWORD, 기본값: 5whakfldkelql)
        db_name: DB 이름 (.env의 DB_NAME, 기본값: ai_agent)
        db_port: DB 포트 (.env의 DB_PORT, 기본값: 3306)
        timezone: 시간대 (.env의 TIMEZONE, 기본값: Asia/Seoul)
        openai_api_key: OpenAI API 키 (.env의 OPENAI_API_KEY, 필수)

    Example:
        >>> from meeting_room_agent.settings import settings
        >>> settings.db_host
        '175.114.229.152'
    """

    model_config: Final[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    db_host: str = Field(
        default_factory=lambda: _get_env_trimmed("HOST") or _get_env_trimmed("DB_HOST") or "175.114.229.152"
    )
    db_user: str = Field(
        default_factory=lambda: _get_env_trimmed("DB_USERNAME") or _get_env_trimmed("DB_USER") or "root"
    )
    db_password: str = Field(
        default_factory=lambda: _get_env_trimmed("DB_PASSWORD") or "5whakfldkelql"
    )
    db_name: str = Field(
        default_factory=lambda: _get_env_trimmed("DB_NAME") or "ai_agent"
    )
    db_port: int = Field(
        default_factory=lambda: int(_get_env_trimmed("DB_PORT") or "3306")
    )
    timezone: str = Field(
        default_factory=lambda: _get_env_trimmed("TIMEZONE") or "Asia/Seoul"
    )
    openai_api_key: SecretStr

    @property
    def mariadb_dsn(self) -> str:
        """MariaDB 접속용 DSN 문자열을 반환한다.

        Returns:
            str: `mysql+pymysql://` 형태의 DSN 문자열.
        """

        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()

