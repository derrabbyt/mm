from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Origins allowed to make cross-origin browser requests. In development the
    # frontend is served by the Angular dev server and proxies /api to us, so
    # requests are same-origin and CORS is not exercised. This matters when the
    # frontend talks to this app directly (e.g. a deployed frontend on another host).
    cors_allow_origins: list[str] = ["http://localhost:4200"]


settings = Settings()
