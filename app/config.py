import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Base directory for the proxy project
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if present
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
else:
    load_dotenv(override=True)


class Settings:
    def __init__(self):
        # API Keys: parsed from GEMINI_API_KEYS (comma-separated) or GEMINI_API_KEY
        raw_keys = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
        self.gemini_api_keys: List[str] = [
            k.strip() for k in raw_keys.split(",") if k.strip()
        ]

        # Cascading fallback models list
        raw_models = os.getenv("GEMINI_MODELS", "")
        if raw_models.strip():
            self.gemini_models: List[str] = [
                m.strip() for m in raw_models.split(",") if m.strip()
            ]
        else:
            self.gemini_models: List[str] = [
                "gemini-3.8-flash",
                "gemini-3.7-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
            ]

        self.proxy_host: str = os.getenv("PROXY_HOST", "127.0.0.1")
        self.proxy_port: int = int(os.getenv("PROXY_PORT", "8080"))
        self.cooldown_seconds: float = float(os.getenv("COOLDOWN_SECONDS", "60.0"))
        self.model_overload_cooldown_seconds: float = float(
            os.getenv("MODEL_OVERLOAD_COOLDOWN_SECONDS", "30.0")
        )
        self.request_timeout: float = float(os.getenv("REQUEST_TIMEOUT", "120.0"))
        self.gemini_base_url: str = os.getenv(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        )

    def reload(self):
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=True)
        raw_keys = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
        self.gemini_api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        raw_models = os.getenv("GEMINI_MODELS", "")
        if raw_models.strip():
            self.gemini_models = [m.strip() for m in raw_models.split(",") if m.strip()]
        self.cooldown_seconds = float(os.getenv("COOLDOWN_SECONDS", "60.0"))
        self.model_overload_cooldown_seconds = float(
            os.getenv("MODEL_OVERLOAD_COOLDOWN_SECONDS", "30.0")
        )


settings = Settings()
