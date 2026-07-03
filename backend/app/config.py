from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_key: str = ""           # anon key — auth.get_user() checks only
    supabase_service_key: str = ""   # service-role key — all play/data writes
    redis_url: str = "redis://localhost:6379"


settings = Settings()
