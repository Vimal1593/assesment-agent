from functools import lru_cache
from pathlib import Path
import os


class Settings:
    def __init__(self) -> None:
        self.app_env = os.getenv("APP_ENV", "development")
        self.app_host = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port = int(os.getenv("APP_PORT", "8000"))
        self.app_secret_key = os.getenv("APP_SECRET_KEY", "")
        self.app_admin_password = os.getenv("APP_ADMIN_PASSWORD", "")
        self.llm_provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b-instruct")
        self.ollama_timeout_seconds = max(10.0, float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120.0")))
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.data_dir = Path(os.getenv("DATA_DIR", "./data")).expanduser()
        self.max_upload_mb = max(1, int(os.getenv("MAX_UPLOAD_MB", "15")))
        self.max_source_chars = max(1000, int(os.getenv("MAX_SOURCE_CHARS", "100000")))
        self.assessment_token_ttl_hours = max(1, int(os.getenv("ASSESSMENT_TOKEN_TTL_HOURS", "168")))
        self.session_https_only = os.getenv("SESSION_HTTPS_ONLY", "false").lower() in {"1", "true", "yes"}

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'assessment.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
