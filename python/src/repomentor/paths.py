"""Python-owned locations; defaults never depend on the caller's working directory."""

import os
from pathlib import Path


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if value is None:
        return None
    path = Path(value).expanduser()
    if not value.strip() or not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path.resolve()


def python_home() -> Path:
    if value := _environment_path("REPOMENTOR_PY_HOME"):
        return value
    # Editable/source installs use python/; wheels use a dedicated user directory.
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file() and (
        source_root / "src/repomentor/paths.py"
    ).resolve() == Path(__file__).resolve():
        return source_root
    return Path.home() / ".repomentor-python"


def data_root() -> Path:
    return _environment_path("REPOMENTOR_PY_DATA_DIR") or python_home() / "artifacts"


def runtime_dir() -> Path:
    return data_root() / "runtime"


def model_settings_path() -> Path:
    return _environment_path("REPOMENTOR_PY_SETTINGS") or python_home() / "config/model-settings.json"
