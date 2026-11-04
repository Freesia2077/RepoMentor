import os
import traceback
from pathlib import Path

import pytest

from repomentor import paths
from repomentor.cli import async_main, parser
from repomentor.config import ConfigurationError, load_model_settings, write_json


@pytest.fixture
def home(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("REPOMENTOR_PY_"):
            monkeypatch.delenv(name)
    root = tmp_path / "python"
    monkeypatch.setenv("REPOMENTOR_PY_HOME", str(root))
    return root


def settings_file(path, key="python-key", model="python-model"):
    write_json(
        path,
        {"apiKey": key, "baseUrl": "https://python.example.invalid", "model": model, "timeout_seconds": 17},
    )
    return path


def test_default_settings_and_storage_ignore_cwd_and_ts_config(home, monkeypatch):
    legacy = settings_file(home.parent / "data/model-settings.json", "ts-key", "ts-model")
    original = legacy.read_bytes()
    settings_file(home / "config/model-settings.json")
    for cwd in (home, home.parent, legacy.parent):
        monkeypatch.chdir(cwd)
        assert load_model_settings().apiKey.get_secret_value() == "python-key"
        assert parser().parse_args(["doctor"]).data_dir == home / "artifacts/runtime"
    assert legacy.read_bytes() == original


def test_no_fallback_to_ts_file_or_shared_anthropic_env(home, monkeypatch):
    legacy = settings_file(home.parent / "data/model-settings.json", "ts-key", "ts-model")
    monkeypatch.chdir(legacy.parent)
    for name, value in {
        "ANTHROPIC_API_KEY": "ts-key",
        "ANTHROPIC_AUTH_TOKEN": "ts-token",
        "ANTHROPIC_BASE_URL": "https://ts.example.invalid",
        "ANTHROPIC_MODEL": "ts-model",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError):
        load_model_settings()
    assert not home.exists()


def test_configuration_sources_have_explicit_precedence(home, monkeypatch):
    settings_file(home / "config/model-settings.json", "default-key")
    selected = settings_file(home / "selected.json", "selected-key")
    explicit = settings_file(home / "explicit.json", "explicit-key")
    monkeypatch.setenv("REPOMENTOR_PY_SETTINGS", str(selected))
    monkeypatch.setenv("REPOMENTOR_PY_API_KEY", "env-key")
    monkeypatch.setenv("REPOMENTOR_PY_BASE_URL", "https://env.example.invalid")
    monkeypatch.setenv("REPOMENTOR_PY_MODEL", "env-model")
    assert load_model_settings(explicit).apiKey.get_secret_value() == "explicit-key"
    assert load_model_settings().apiKey.get_secret_value() == "selected-key"
    monkeypatch.delenv("REPOMENTOR_PY_SETTINGS")
    assert load_model_settings().apiKey.get_secret_value() == "env-key"
    for name in ("API_KEY", "BASE_URL", "MODEL"):
        monkeypatch.delenv("REPOMENTOR_PY_" + name)
    assert load_model_settings().apiKey.get_secret_value() == "default-key"


def test_partial_env_never_borrows_default_file_credentials(home, monkeypatch):
    settings_file(home / "config/model-settings.json")
    monkeypatch.setenv("REPOMENTOR_PY_BASE_URL", "https://different.example.invalid")
    with pytest.raises(ConfigurationError):
        load_model_settings()


def test_invalid_selected_file_never_falls_back_or_echoes_secret(home, monkeypatch):
    settings_file(home / "config/model-settings.json")
    selected = home / "broken.json"
    selected.write_text('{"apiKey":"must-not-appear"', encoding="utf-8")
    monkeypatch.setenv("REPOMENTOR_PY_SETTINGS", str(selected))
    with pytest.raises(ConfigurationError) as exc:
        load_model_settings()
    assert "must-not-appear" not in str(exc.value)
    assert "must-not-appear" not in "".join(traceback.format_exception(exc.value))
    monkeypatch.setenv("REPOMENTOR_PY_SETTINGS", str(home / "missing.json"))
    with pytest.raises(ConfigurationError):
        load_model_settings()


def test_paths_are_python_owned_and_overrides_are_explicit(home, monkeypatch):
    custom = home.parent / "custom-data"
    monkeypatch.setenv("REPOMENTOR_PY_DATA_DIR", str(custom))
    assert paths.data_root() == custom
    assert paths.runtime_dir() == custom / "runtime"
    assert parser().parse_args(["--data-dir", str(home / "one-run"), "doctor"]).data_dir == home / "one-run"
    monkeypatch.setenv("REPOMENTOR_PY_DATA_DIR", "relative-folder")
    with pytest.raises(ValueError, match="absolute"):
        paths.data_root()


def test_source_and_wheel_defaults_do_not_search_cwd(home, monkeypatch, tmp_path):
    monkeypatch.delenv("REPOMENTOR_PY_HOME")
    monkeypatch.chdir(tmp_path)
    assert paths.python_home() == Path(paths.__file__).resolve().parents[2]
    monkeypatch.setattr(paths, "__file__", str(tmp_path / "site-packages/repomentor/paths.py"))
    assert paths.python_home() == Path.home() / ".repomentor-python"


async def test_missing_config_fails_before_clone_or_run_creation(home, monkeypatch):
    import repomentor.cli as cli

    def forbidden(*args):
        pytest.fail("repository storage must not be opened before validating model config")

    monkeypatch.setattr(cli, "SnapshotStore", forbidden)
    args = parser().parse_args(["report", "--repository", "developit/mitt"])
    with pytest.raises(ConfigurationError):
        await async_main(args)
    assert not home.exists()


async def test_cli_uses_python_model_config_and_preserves_its_timeout(home, repo, monkeypatch):
    from repomentor.runtimes import claude
    from repomentor.runtimes.stub import StubRuntime

    settings_file(home / "config/model-settings.json")
    captured = []

    def fake_runtime(settings, **kwargs):
        captured.append(settings)
        return StubRuntime()

    monkeypatch.setattr(claude, "ClaudeSdkRuntime", fake_runtime)
    args = parser().parse_args(["report", "--runtime", "claude", "--repository", str(repo)])
    assert await async_main(args) == 0
    assert captured[0].model == "python-model" and captured[0].timeout_seconds == 17
    assert (home / "artifacts/runtime/repomentor-python.sqlite3").is_file()
