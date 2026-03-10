"""
app/config.py
─────────────
Centralised settings via pydantic-settings.
All values are read from environment variables (or .env file).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: str = "development"
    log_level: str = "DEBUG"

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://adrc_user:adrc_pass@localhost:5432/adrc_db"
    )
    sync_database_url: str = (
        "postgresql+psycopg2://adrc_user:adrc_pass@localhost:5432/adrc_db"
    )

    # ── Twilio ────────────────────────────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"



    # ── Azure AI Content Safety ────────────────────────────────────────────────
    azure_content_safety_endpoint: str = ""
    azure_content_safety_key: str = ""

    # ── Azure AI Search ───────────────────────────────────────────────────────
    azure_search_endpoint: str = ""
    azure_search_key: str = ""
    azure_search_index: str = "ndma-sops"

    # ── Clustering Thresholds ─────────────────────────────────────────────────
    cluster_radius_meters: int = 500
    cluster_time_window_minutes: int = 30
    cluster_min_reports: int = 3


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton settings instance."""
    return Settings()
