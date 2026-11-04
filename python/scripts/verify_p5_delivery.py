"""Verify the built wheel, offline test report, documentation links and recorded P5 audit."""

import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from repomentor.evaluation import atomic_write

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/p5"


def main():
    assert sys.flags.no_user_site
    tests_path = ROOT / "artifacts/p5-final-tests.xml"
    suites = list(ET.parse(tests_path).getroot().iter("testsuite"))
    tests = {
        key: sum(int(s.attrib.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    assert tests["tests"] == 108 and not tests["failures"] and not tests["errors"] and not tests["skipped"]
    wheels = list((ROOT / "artifacts/p5-wheel").glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert all(name.startswith(("repomentor/", "repomentor_core-0.1.0.dist-info/")) for name in names)
        assert not any(
            any(
                part in name
                for part in ("model-settings", ".safetensors", ".gguf", ".env", "artifacts/", "experiments/")
            )
            for name in names
        )
        for path in (ROOT / "src/repomentor").rglob("*.py"):
            relative = path.relative_to(ROOT / "src").as_posix()
            assert archive.read(relative).replace(b"\r\n", b"\n") == path.read_bytes().replace(b"\r\n", b"\n")
        assert "repomentor/assets/plugin/.claude-plugin/plugin.json" in names
    checks = {}
    for name, args in {
        "ruff_check": ["ruff", "check", "--no-cache", "src", "scripts", "tests"],
        "ruff_format": ["ruff", "format", "--check", "--no-cache", "src", "scripts", "tests"],
        "pip_check": ["pip", "check"],
    }.items():
        result = subprocess.run(
            [sys.executable, "-s", "-X", "utf8", "-m", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode:
            raise ValueError(f"{name} failed: {result.stdout}\n{result.stderr}")
        checks[name] = result.stdout.strip()
    docs = [ROOT / "README.md", ROOT / "项目文档.md", *sorted((ROOT / "notes").glob("*.md"))]
    checked_links = 0
    for path in docs:
        for link in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            link = link.strip("<>").split("#", 1)[0]
            if not link or re.match(r"^[a-zA-Z]+:", link):
                continue
            destination = (path.parent / unquote(link)).resolve()
            if destination != EXP / "acceptance.json":
                assert destination.exists(), f"broken link: {path.name}: {link}"
            checked_links += 1
    audit = json.loads((EXP / "audit.json").read_text(encoding="utf-8"))
    assert audit["cache_replay"]["generation_new"] == 0 and audit["cli_smoke"]["turns"] == 3
    assert audit["paid_api_calls"] == 0 and audit["protected_files_unchanged"] == 53
    result = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "engineering_status": "accepted",
        "human_semantic_review": "pending",
        "pytest": {
            **tests,
            "deselected_ts_parity": 1,
            "report_sha256": hashlib.sha256(tests_path.read_bytes()).hexdigest(),
        },
        "wheel": {
            "filename": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "source_files_match": True,
            "private_assets_excluded": True,
            "asset_manifest_included": True,
        },
        "checks": checks,
        "documentation_links_checked": checked_links,
        "recorded_results_audit_sha256": hashlib.sha256((EXP / "audit.json").read_bytes()).hexdigest(),
        "current_source_sha256": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for p in sorted((ROOT / "src/repomentor").rglob("*.py"))
        },
        "known_limits": [
            "No aggregate semantic accuracy or human approval claim",
            "Rule navigation memory showed no full-span coverage gain in the two line-BM25 stress scenarios",
            "One Ollama CUDA warmup failure preserved; explicit resume and a subsequent new round succeeded, underlying cause remains unproven",
            "P6 agent loop and MiniMind training are not implemented",
        ],
    }
    atomic_write(EXP / "acceptance.json", json.dumps(result, ensure_ascii=False, indent=2))
    atomic_write(
        EXP / "artifact-hashes.json",
        json.dumps(
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(EXP.iterdir())
                if p.is_file() and p.name != "artifact-hashes.json"
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    print(
        json.dumps(
            {"tests": tests, "wheel": wheel.name, "checks": checks, "links": checked_links},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
