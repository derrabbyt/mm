from pathlib import Path
from typing import Self
from urllib.parse import quote_plus

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")
    log_level: str = "INFO"

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

    # The scraping job connects as its own role, which owns the `content`
    # schema and has no grant at all on the application's tables - so a bug in
    # scraping cannot reach an account or a meetup. This is what ADR 0001 chose
    # a separate schema for, and it is not delivered until the job uses it.
    #
    # The password has no default on purpose. Left unset, the migration still
    # creates the role and its grants but leaves it unable to log in, and the
    # job falls back to the application's connection saying loudly that it has -
    # a deployment that has not been given the credential is better off scraping
    # than silently not scraping, but it should not be able to think it is
    # isolated when it is not.
    scraper_postgres_user: str = "mm_scraper"
    scraper_postgres_password: str = ""
    scraper_database_url: str = ""

    supabase_url: str
    supabase_jwt_audience: str = "authenticated"

    dataset_dir: Path = BACKEND_DIR / "app" / "data" / "output_dev" / "vienna_v2_h3r9"

    matrix_osm_path: Path = BACKEND_DIR / "data" / "geodata" / "vienna.osm.pbf"
    matrix_gtfs_path: Path = BACKEND_DIR / "data" / "geodata" / "wl.zip"

    matrix_min_transit_coverage_pct: float = Field(default=10.0, ge=0.0, le=100.0)

    scrape_days_ahead: int = 60
    scrape_days_back: int = 7
    scrape_timeout_seconds: float = 30.0
    scrape_max_retired_share: float = Field(default=0.6, ge=0.0, le=1.0)

    scrape_archive_dir: Path = BACKEND_DIR / "data" / "archive"
    scrape_archive_retention_days: int = Field(default=14, ge=1)

    # The geocoder, which is the Photon that docker compose runs. Configuration
    # rather than a constant, and with no public default: an address is somebody's
    # whereabouts, and a misconfiguration that quietly sent thousands of them to a
    # third party would look exactly like working software. Self-hosted, so the
    # delay is zero and asking several questions per address is free.
    geocoder_url: str = "http://127.0.0.1:2322/api"
    geocoder_delay_seconds: float = 0.0
    geocoder_enabled: bool = True

    @property
    def app_database_url(self) -> str:
        return self._dsn(self.postgres_user, self.postgres_password)

    @property
    def scraper_configured(self) -> bool:
        return self.scraper_database_url != self.database_url

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
    def _assemble_database_urls(self) -> Self:
        if not self.database_url:
            self.database_url = self._dsn(self.postgres_user, self.postgres_password)
        if not self.scraper_database_url:
            self.scraper_database_url = (
                self._dsn(self.scraper_postgres_user, self.scraper_postgres_password)
                if self.scraper_postgres_password
                else self.database_url
            )
        return self

    def _dsn(self, user: str, password: str) -> str:
        return (
            f"postgresql+psycopg2://{user}:{quote_plus(password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
