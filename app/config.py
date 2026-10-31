from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: str = ""
    webapp_url: str = "https://example.com"
    database_url: str = "sqlite:///./koreahub.db"
    admin_api_key: str = "change-me"
    admin_ids: str = ""
    upload_dir: str = "uploads"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
