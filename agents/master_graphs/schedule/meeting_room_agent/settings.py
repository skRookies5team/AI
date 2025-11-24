"""회의실 에이전트 환경 설정 모듈."""

from __future__ import annotations

from typing import Final

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__: list[str] = ["Settings", "settings"]


class Settings(BaseSettings):
    """환경 변수 기반 회의실 에이전트 설정 로더.

    Attributes:
        db_host: 175.120.35.228
        db_user: root
        db_password: root
        db_name: ai_agent
        db_port: 3306
        timezone: Asia/Seoul
        openai_api_key: 

    Example:
        >>> from meeting_room_agent.settings import settings
        >>> settings.db_host
        '175.120.35.228'
    """

    model_config: Final[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    db_host: str = "175.114.229.152"
    db_user: str = "root"
    db_password: str = "5whakfldkelql"
    db_name: str = "ai_agent"
    db_port: int = 3306
    timezone: str = "Asia/Seoul"
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

