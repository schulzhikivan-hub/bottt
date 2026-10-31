from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: str = ""
    webapp_url: str = "https://example.com"
    database_url: str = "sqlite:///./koreahub.db"
    admin_api_key: str = "change-me"
    admin_ids: str = ""
    upload_dir: str = "uploads"
    notifications_enabled: bool = True
    bot_username: str = ""
    require_photos: bool = True      # housing and goods need at least one photo before publication
    max_photos: int = 10
    listing_ttl_days: int = 30
    new_account_minutes: int = 10    # a newer account may publish only one listing per hour (set 0 to switch off)
    log_level: str = "INFO"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
