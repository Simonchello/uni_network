from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jwt_secret: str = "dev-secret-change-me"
    jwt_expires_hours: int = 24
    admin_username: str = "admin"
    admin_password_hash: str = ""

    mock_stats: bool = False
    poll_interval_sec: float = 2.0

    bot_dir: Path = Path(__file__).parent.parent / "bot"
    host: str = "127.0.0.1"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
