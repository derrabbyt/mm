from pydantic_settings import BaseSettings, SettingsConfigDict
from redis import Redis


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Origins allowed to make cross-origin browser requests. In development the
    # frontend is served by the Angular dev server and proxies /api to us, so
    # requests are same-origin and CORS is not exercised. This matters when the
    # frontend talks to this app directly (e.g. a deployed frontend on another host).
    cors_allow_origins: list[str] = ["http://localhost:4200"]
    # Applied to the root logger by core.logging.setup_logging. The only
    # difference between a dev and a prod run: DEBUG locally, INFO deployed.
    log_level: str = "INFO"
    # unified redis connection, not used by the sdk
    redis: Redis = Redis(host="localhost", port=6379, db=0)


settings = Settings()
