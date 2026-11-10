"""The cloud handoff preserves an independent src package and its data root."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile

from sftgrpo import core, prepare


def test_bundle_runs_from_an_unrelated_directory_without_rag_sources(tmp_path, monkeypatch):
    project = tmp_path / "project"
    root = project / "SFTGRPO"
    source = project / "src/sftgrpo"
    source.mkdir(parents=True)
    for path in core.SOURCE_ROOT.glob("*.py"):
        shutil.copy2(path, source / path.name)
    root.mkdir()
    core.save_json(root / "run-config.json", {"data": {
        "directory": "data/current", "reports_directory": "reports/current"}})
    core.save_json(root / "sources.lock.json", {"files": []})
    for name in ["data-policy.json", "tool-schema.json"]:
        core.save_json(root / name, {})
    for name in ["README.md", ".gitignore", "requirements.txt", "requirements-cuda.txt"]:
        (root / name).write_text("fixture\n", encoding="utf-8")
    for name in [".cache/training-template.jinja", "data/current/system.txt"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    (root / ".env").write_text("PRIVATE_FIXTURE=must_stay_local\n", encoding="utf-8")
    rag = project / "src/repomentor"
    rag.mkdir()
    (rag / "__init__.py").write_text("raise RuntimeError('RAG must stay out of the handoff')\n", encoding="utf-8")
    monkeypatch.setattr(core, "ROOT", root)
    monkeypatch.setattr(prepare, "ROOT", root)
    monkeypatch.setattr(prepare, "SOURCE_ROOT", source)
    monkeypatch.setattr(prepare, "PROJECT_ROOT", project)
    prepare.bundle()

    manifest = core.load_json(root / "reports/current/bundle-manifest.json")
    assert "src/sftgrpo/train.py" in {entry["path"] for entry in manifest}
    for entry in manifest:
        assert hashlib.sha256((project / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    restored = tmp_path / "restored"
    with tarfile.open(root / ".cache/rent-ready.tar.gz") as archive:
        names = archive.getnames()
        assert all(name.startswith(("src/sftgrpo/", "SFTGRPO/")) for name in names)
        assert not any("repomentor" in name or name.endswith(".env") for name in names)
        archive.extractall(restored, filter="data")
    environment = {**os.environ, "PYTHONPATH": str(restored / "src"), "PYTHONNOUSERSITE": "1"}
    result = subprocess.run(
        [sys.executable, "-s", "-X", "utf8", "-m", "sftgrpo.prepare", "--help"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=True,
    )
    assert "snapshots" in result.stdout
    result = subprocess.run(
        [sys.executable, "-s", "-X", "utf8", "-c",
         "import json, sys; from sftgrpo.core import ROOT; "
         "print(json.dumps({'root': str(ROOT), 'rag_loaded': any("
         "n == 'repomentor' or n.startswith('repomentor.') for n in sys.modules)}))"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == {"root": str(restored / "SFTGRPO"), "rag_loaded": False}
