"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root (parent of backend/) if present.
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Runtime settings. All values can be overridden via environment variables."""

    # SQLite database file used for the satellite cache and app state.
    database_path: str = field(
        default_factory=lambda: os.getenv("ORBITDESK_DB_PATH", str(_ROOT / "data" / "orbitdesk.db"))
    )
    # Where Skyfield stores its downloaded ephemeris/leap-second files.
    data_dir: str = field(
        default_factory=lambda: os.getenv("ORBITDESK_DATA_DIR", str(_ROOT / "data" / "skyfield"))
    )
    # Seconds between allowed CelesTrak refreshes (rate limiting courtesy).
    cache_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("ORBITDESK_CACHE_TTL_SECONDS", "900"))
    )
    # Network timeout when fetching TLE data from CelesTrak.
    fetch_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("ORBITDESK_FETCH_TIMEOUT_SECONDS", "15"))
    )
    # If true, never touch the network; use bundled demo TLEs instead.
    force_demo_mode: bool = field(default_factory=lambda: _bool("ORBITDESK_FORCE_DEMO_MODE", False))
    # OpenAI-compatible AI assistant. Key stays server-side only.
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    # Log level for the app logger.
    log_level: str = field(default_factory=lambda: os.getenv("ORBITDESK_LOG_LEVEL", "INFO"))

    @property
    def ai_enabled(self) -> bool:
        return bool(self.openai_api_key)


settings = Settings()
