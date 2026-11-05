"""Settings from the environment. Every value has a default that works on a laptop."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("SAKUGA_DATA", "data")))
    public_url: str = field(default_factory=lambda: os.environ.get("SAKUGA_PUBLIC_URL", "").rstrip("/"))
    # Sweeps: every 30 minutes. Digest: once a day, of the cels first seen since the last one.
    schedule: str = field(default_factory=lambda: _env("SAKUGA_SCHEDULE", "*/30 * * * *"))
    digest_schedule: str = field(default_factory=lambda: _env("SAKUGA_DIGEST", "0 8 * * *"))
    timezone: str = field(default_factory=lambda: _env("SAKUGA_TZ", "Europe/London"))
    apprise_urls: str = field(default_factory=lambda: os.environ.get("APPRISE_URLS", ""))
    # OpenAI-compatible endpoint (the cluster's LiteLLM proxy). Empty URL: no LLM, regex filter only.
    llm_url: str = field(default_factory=lambda: os.environ.get("SAKUGA_LLM_URL", "").rstrip("/"))
    llm_key: str = field(default_factory=lambda: os.environ.get("SAKUGA_LLM_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("SAKUGA_LLM_MODEL", "claude"))
    tries: int = field(default_factory=lambda: int(_env("SAKUGA_TRIES", "3")))
    headless: bool = field(default_factory=lambda: os.environ.get("SAKUGA_HEADLESS", "1") != "0")
    version: str = field(default_factory=lambda: _env("APP_VERSION", "0.0.0-dev"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sakuga.db"

    @property
    def img_dir(self) -> Path:
        return self.data_dir / "img"

    def link(self, path: str) -> str:
        return f"{self.public_url}{path}" if self.public_url else path


def load() -> Settings:
    s = Settings()
    s.img_dir.mkdir(parents=True, exist_ok=True)
    return s
