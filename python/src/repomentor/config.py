from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import Field, SecretStr

from .contracts import Contract
from .paths import model_settings_path


class ConfigurationError(ValueError):
    code = "model_configuration"


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


def load_model_settings(path: Path | None = None, *, _model=ModelSettings):
    """Whole-source precedence: explicit file > PY_SETTINGS > PY model env > default file.

    Sources are not merged: a key from one service must not silently use another endpoint.
    Generic ANTHROPIC_* variables and target-repository settings are never inputs.
    """
    fields = {
        "REPOMENTOR_PY_API_KEY": "apiKey",
        "REPOMENTOR_PY_BASE_URL": "baseUrl",
        "REPOMENTOR_PY_MODEL": "model",
        "REPOMENTOR_PY_PROVIDER": "provider",
    }
    if _model is not ModelSettings:
        fields.update(
            {
                "REPOMENTOR_PY_MODEL_REVISION": "model_revision",
                "REPOMENTOR_PY_QUANTIZATION": "quantization",
                "REPOMENTOR_PY_SERVER_VERSION": "server_version",
            }
        )
    try:
        if path is not None or "REPOMENTOR_PY_SETTINGS" in os.environ:
            selected = Path(path).expanduser() if path is not None else model_settings_path()
            settings = _model.model_validate_json(selected.read_text(encoding="utf-8-sig"))
        elif any(name in os.environ for name in fields):
            values = {field: os.environ[name] for name, field in fields.items() if name in os.environ}
            # Require endpoint together with credentials: no implicit provider fallback.
            settings = _model.model_validate(values)
        else:
            settings = _model.model_validate_json(model_settings_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ConfigurationError(
            "Configure Python's config/model-settings.json, --settings, or the complete "
            "REPOMENTOR_PY_API_KEY / REPOMENTOR_PY_BASE_URL / REPOMENTOR_PY_MODEL group. "
            "Check the selected file exists and has valid fields."
        ) from None  # Pydantic/JSON tracebacks may include credential-bearing input.
    from urllib.parse import urlparse

    url = urlparse(settings.baseUrl)
    if (
        url.scheme not in ("http", "https")
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ConfigurationError("model endpoint must be HTTP(S) without embedded credentials/query/fragment")
    if not settings.model.strip() or (
        _model is ModelSettings and not settings.apiKey.get_secret_value().strip()
    ):
        raise ConfigurationError(
            "Fill in the model and API key in Python's own configuration before a live run"
        )
    return settings


def load_chat_settings(path: Path | None = None):
    from .runtimes.chat import ChatSettings

    return load_model_settings(path, _model=ChatSettings)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
