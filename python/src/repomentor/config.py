from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import Field, SecretStr

from .contracts import Contract


class ModelSettings(Contract):
    provider: str = "anthropic-compatible"
    apiKey: SecretStr
    baseUrl: str
    model: str
    timeout_seconds: float = Field(default=180, gt=0)
    max_turns: int = Field(default=8, ge=1, le=20)
    max_budget_usd: float = Field(default=1.0, gt=0)

    def public(self):
        return {
            "provider": self.provider,
            "base_url": self.baseUrl,
            "model": self.model,
            "credential_configured": bool(self.apiKey.get_secret_value()),
            "timeout_seconds": self.timeout_seconds,
            "max_turns": self.max_turns,
            "max_budget_usd": self.max_budget_usd,
        }


def load_model_settings(path: Path | None = None):
    if path:
        settings = ModelSettings.model_validate_json(path.read_text(encoding="utf-8"))
    else:
        settings = ModelSettings(
            apiKey=os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
            baseUrl=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            model=os.environ.get("ANTHROPIC_MODEL", ""),
        )
    from urllib.parse import urlparse

    url = urlparse(settings.baseUrl)
    if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password or url.query:
        raise ValueError("model endpoint must be an HTTP(S) origin without embedded credentials/query")
    if not settings.model or not settings.apiKey.get_secret_value():
        raise ValueError("configure an explicit model and API key via --settings or ANTHROPIC_* variables")
    return settings


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
