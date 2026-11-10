"""9B defaults and archive checks stay usable after removing all 4B assets."""

import hashlib
import importlib.util
import os
from pathlib import Path

import httpx
import pytest
from tokenizers import Tokenizer, models

from repomentor import token_budget
from repomentor.config import load_chat_settings, write_json

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tokenizer_needs_only_9b_and_verifies_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOMENTOR_PY_DATA_DIR", str(tmp_path))
    content = Tokenizer(models.WordLevel({"[UNK]": 0, "repo": 1}, unk_token="[UNK]")).to_str().encode()
    path = tmp_path / "tokenizers/qwen35-9b/tokenizer.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    assert token_budget.load_tokenizer(digest).encode("repo").ids == [1]
    assert not (tmp_path / "tokenizers/qwen35-4b").exists()
    with pytest.raises(ValueError, match="hash"):
        token_budget.load_tokenizer("0" * 64)
    # Identical hashes in another data root must not hide a missing/corrupted file.
    monkeypatch.setenv("REPOMENTOR_PY_DATA_DIR", str(tmp_path / "other"))
    with pytest.raises(FileNotFoundError):
        token_budget.load_tokenizer(digest)


@pytest.mark.parametrize("command", ["ask", "chat", "report"])
@pytest.mark.parametrize(
    "source",
    ["default", "environment_file", "model_environment", "explicit_file", "invalid_environment_file"],
)
async def test_cli_respects_shared_configuration_precedence(tmp_path, monkeypatch, reader, command, source):
    import repomentor.cli as cli
    from repomentor.config import ConfigurationError
    from repomentor.runtimes.chat import ChatSettings

    for name in list(os.environ):
        if name.startswith("REPOMENTOR_PY_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("REPOMENTOR_PY_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "open_reader", lambda *a, **kw: reader)
    profile = ChatSettings(
        baseUrl="http://127.0.0.1:11435/v1",
        model="default-fixture",
        model_revision="fixture-rev",
        quantization="fixture",
        server_version="fixture",
        context_window=16384,
        max_tokens=2048,
    ).public()
    write_json(tmp_path / "config/model-settings.json", profile)
    selected, explicit = tmp_path / "selected.json", tmp_path / "explicit.json"
    write_json(selected, {**profile, "model": "selected-fixture"})
    write_json(explicit, {**profile, "model": "explicit-fixture"})
    if source != "default":
        for name, value in {
            "API_KEY": "fixture-key",
            "BASE_URL": profile["baseUrl"],
            "MODEL": "environment-fixture",
            "MODEL_REVISION": "fixture-rev",
            "QUANTIZATION": "fixture",
            "SERVER_VERSION": "fixture",
        }.items():
            monkeypatch.setenv("REPOMENTOR_PY_" + name, value)
    if source in ("environment_file", "explicit_file", "invalid_environment_file"):
        monkeypatch.setenv(
            "REPOMENTOR_PY_SETTINGS",
            str(tmp_path / "missing.json" if source == "invalid_environment_file" else selected),
        )
    expected = {
        "default": "default-fixture",
        "environment_file": "selected-fixture",
        "model_environment": "environment-fixture",
        "explicit_file": "explicit-fixture",
    }

    class Selected(Exception):
        pass

    def capture(path=None):
        loaded = load_chat_settings(path)
        assert loaded.model == expected[source]
        if source != "model_environment":
            assert loaded.context_window == 16384 and loaded.max_tokens == 2048
        raise Selected

    monkeypatch.setattr(cli, "load_chat_settings", capture)
    args = [command, "--snapshot-id", reader.snapshot.snapshot_id]
    args += (
        ["--question", "入口？"] if command == "ask" else ["--runtime", "sdk"] if command == "report" else []
    )
    if source == "explicit_file":
        args += ["--settings", str(explicit)]
    with pytest.raises(ConfigurationError if source == "invalid_environment_file" else Selected):
        await cli.async_main(cli.parser().parse_args(args))


def test_9b_setup_reuses_verified_files_and_never_generates(tmp_path, monkeypatch):
    setup = script("prepare_qwen9b")
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setenv("REPOMENTOR_PY_HOME", str(tmp_path))
    profile = load_chat_settings(ROOT / "config/model-settings.example.json").public()
    content = b"test-tokenizer"
    profile["tokenizer_sha256"] = hashlib.sha256(content).hexdigest()
    write_json(tmp_path / "config/model-settings.example.json", profile)
    source = {"source_model": "qwen3.5:9b-q4_K_M", "source_digest": "source-digest"}
    write_json(tmp_path / "experiments/qwen9b-diagnostic/download.json", source)
    write_json(
        tmp_path / "experiments/qwen9b-diagnostic/tokenizer.json", {"sha256": profile["tokenizer_sha256"]}
    )
    tokenizer = tmp_path / "artifacts/tokenizers/qwen35-9b/tokenizer.json"
    tokenizer.parent.mkdir(parents=True)
    tokenizer.write_bytes(content)
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": profile["server_version"]})
        assert request.method == "GET" and request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": source["source_model"], "digest": source["source_digest"]},
                    {"name": profile["model"], "digest": profile["model_revision"]},
                ]
            },
        )

    result = setup.prepare(transport=httpx.MockTransport(handler))
    assert result["generation_requests"] == 0 and len(calls) == 2
    assert load_chat_settings(tmp_path / "config/model-settings.json").public() == profile


def test_archive_verification_rejects_changed_bytes_without_settings(tmp_path):
    verifier = script("verify_archives")
    with pytest.raises(ValueError, match="No frozen experiment manifests"):
        verifier.verify(tmp_path)
    folder = tmp_path / "experiments/sweqa-50-2026-11-03"
    write_json(folder / "completed-files.json", {"sha256": {}})
    evidence = folder / "case.json"
    evidence.write_bytes(b"original failure")
    write_json(
        evidence.parent / "completed-files.json",
        {"sha256": {"case.json": hashlib.sha256(evidence.read_bytes()).hexdigest()}},
    )
    assert verifier.verify(tmp_path)["verified_files"] == 1
    evidence.write_bytes(b"rewritten success")
    with pytest.raises(ValueError, match="changed"):
        verifier.verify(tmp_path)
