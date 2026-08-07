from pathlib import Path
from typing import Self
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")
    log_level: str = "INFO"

    # Every unspecified variable is required in .env
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str

    redis_host: str
    redis_port: int
    redis_db: int
    redis_password: str

    frontend_url: str
    database_url: str = ""
    redis_url: str = ""

    supabase_url: str
    supabase_jwt_audience: str = "authenticated"

    @property
    def supabase_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_issuer}/.well-known/jwks.json"

    @model_validator(mode="after")
    def _assemble_redis_url(self) -> Self:
        if not self.redis_url:
            auth = f":{quote_plus(self.redis_password)}@" if self.redis_password else ""
            self.redis_url = (
                f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"
            )
        return self

    @model_validator(mode="after")
    def _assemble_database_url(self) -> Self:
        if not self.database_url:
            self.database_url = (
                f"postgresql+psycopg2://{self.postgres_user}"
                f":{quote_plus(self.postgres_password)}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self


settings = Settings()
