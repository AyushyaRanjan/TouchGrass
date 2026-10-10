"""Central configuration: env-driven settings plus named threshold constants."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Quality gate thresholds (server/verify/quality.py).
BLUR_THRESHOLD = 50.0        # Laplacian variance below this = too blurry
BRIGHTNESS_THRESHOLD = 40.0  # mean gray below this = too dark
MIN_SHORT_SIDE = 640         # pixels, shorter edge of the submitted photo
QUALITY_LONG_SIDE = 800      # downscale target before measuring blur/brightness

# VLM (server/verify/vlm.py).
VLM_LONG_SIDE = 768          # downscale target before sending to Ollama
EVIDENCE_MIN_WORDS = 4       # evidence must be at least this many words
EVIDENCE_MIN_CHARS = 16      # schema minLength; grammar-enforced by Ollama

# Duplicate detection (server/verify/dedupe.py).
DUPE_DISTANCE = 6  # perceptual-hash distance <= 6 on a different task = rejected


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    API_TOKEN: str = "change-me"
    OLLAMA_URL: str = "http://localhost:11434"
    VLM_MODEL: str = "qwen2.5vl:3b"
    TIMEZONE: str = "Europe/London"
    BLIND_PASS: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
