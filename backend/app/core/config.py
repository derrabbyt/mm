from pathlib import Path
from typing import Self
from urllib.parse import quote_plus

from pydantic import Field, model_validator
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

    # How far ahead and behind a scrape asks each Source for. Deployment
    # settings, unlike a Source's rate limit or its locales - those are
    # knowledge about the site and live in that Source's own spec.
    scrape_days_ahead: int = 60
    scrape_days_back: int = 7
    # The shared read timeout. A Source that needs longer says so in its spec:
    # wien.gv.at server-generates a ~490 KB index and takes ~32s to hand it over.
    scrape_timeout_seconds: float = 30.0
    # The most of one Source's Listings a single run may retire; above it the run
    # retires nothing for that Source. Why that is worth guarding is at the guard
    # itself. 0.6 is the 40% volume-drop rule the scraper alerted on, read from
    # the other side: a run keeping under 40% of what a Source had was already
    # the number somebody was expected to look at.
    #
    # Bounded, because it is a share and the failure is silent in one direction:
    # somebody typing 60 for "60%" would otherwise switch the guard off and
    # nothing would say so until a Source emptied itself.
    scrape_max_retired_share: float = Field(default=0.6, ge=0.0, le=1.0)

    # Where every fetched payload is kept, and for how long. Evidence rather
    # than data: a parser that starts producing nonsense is fixed against the
    # document that broke it, and by the time anyone looks the site has moved
    # on. Local disk on purpose - object storage is the eventual home.
    #
    # Retention is a deployment setting because the disk is. A payload is
    # stored under a digest of itself, so an hourly run costs nothing for a page
    # that did not change and one copy for one that did - a first run of all
    # twenty-one Sources is on the order of 20 MB compressed and a day of
    # re-runs adds only what actually moved. Whoever sized the volume is the one
    # who knows how many days of that fit.
    scrape_archive_dir: Path = BACKEND_DIR / "data" / "archive"
    scrape_archive_retention_days: int = Field(default=14, ge=1)

    # The geocoder, which is the Photon that docker compose runs. Configuration
    # rather than a constant, and with no public default: an address is somebody's
    # whereabouts, and a misconfiguration that quietly sent thousands of them to a
    # third party would look exactly like working software. Self-hosted, so the
    # delay is zero and asking several questions per address is free.
    geocoder_url: str = "http://127.0.0.1:2322/api"
    geocoder_delay_seconds: float = 0.0
    # Off turns the scrape into a pure scrape: addresses are still stored, so
    # nothing has to be re-scraped when it goes back on.
    geocoder_enabled: bool = True

    @property
    def scraper_configured(self) -> bool:
        """Whether the job's connection is something other than the application's.

        Asked of the assembled URL rather than of the password, because either
        can configure it: a deployment that sets `SCRAPER_DATABASE_URL` outright
        is as isolated as one that sets a password, and keying on the password
        would have it warned at every run that it is not.
        """
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
        """Both connections, in one place because the second derives from the
        first - as two validators they would depend on declaration order to be
        correct, which is not something to leave implicit.

        The job's is its own role where a password is configured and the
        application's where none is. Falling back to the application beats
        falling back to a role that cannot log in; the job saying which it got
        is what keeps the fallback from passing for the real thing. Same host,
        port and database either way: this separates who connects, not what
        they connect to, because a foreign key still has to cross from `public`
        to `content`.
        """
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
