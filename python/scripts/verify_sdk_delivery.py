"""Offline acceptance of SDK migration/testing scope, explicitly excluding model quality approval."""

import importlib.metadata
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from urllib.parse import unquote

from p6_archive import ROOT, sha, verify_archive

from repomentor.records import write
from repomentor.sdk_agent import sdk_version


def main():
    initial = ROOT / "experiments/p6-sdk-e2e"
    current = ROOT / "experiments/p6-sdk-e2e-budget-fix"
    def load(path):
        return json.loads(path.read_text(encoding="utf-8"))
    protocol = load(current / "protocol.json")
    assert protocol["sdk"] == sdk_version(), "current implementation differs from the targeted regression"
    assert not (ROOT / "src/repomentor/agent.py").exists()
    for path in (ROOT / "src/repomentor").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"from\s+(?:repomentor\.agent|\.agent)\s+import|class\s+AgentEngine\b", source)
    test_report = ROOT / "artifacts/sdk-e2e-tests.xml"
    suites = list(ET.parse(test_report).getroot().iter("testsuite"))
    assert suites and all(int(s.attrib["errors"]) == int(s.attrib["failures"]) == 0 for s in suites)
    passed = sum(int(s.attrib["tests"]) - int(s.attrib["skipped"]) for s in suites)
    wheel = next((ROOT / "artifacts/sdk-e2e-wheel").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "repomentor/agent.py" not in names
        assert {"repomentor/sdk_agent.py", "repomentor/sdk_report.py", "repomentor/agent_tools.py",
                "repomentor/agent_prompts.py", "repomentor/records.py"} <= names
        assert not any(n.startswith(("config/", "artifacts/", "experiments/")) or n.endswith(".safetensors")
                       for n in names)
        assert any("assets/plugin/.claude-plugin/" in name for name in names)
        for name in names:
            if name.endswith(".py"):
                assert archive.read(name).replace(b"\r\n", b"\n") == (ROOT / "src" / name).read_bytes().replace(
                    b"\r\n", b"\n"), name
    links = 0
    for path in (ROOT / "README.md", ROOT / "项目文档.md", *sorted((ROOT / "notes").glob("*.md"))):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            assert (path.parent / unquote(target.split("#")[0])).resolve().exists(), (path.name, target)
            links += 1
    audits = [load(folder / "audit.json") for folder in (initial, current)]
    for folder, count in ((initial, 30), (current, 4)):
        replay = load(folder / "replay.json")
        assert replay["network_disabled"] and replay["new_model_requests"] == 0
        assert len(replay["cases"]) == count
    cli = load(current / "cli-cache-replay.json")
    assert cli["mode"] == "sdk" and cli["cache_hit"] and cli["new_model_requests"] == 0
    value = {
        "schema_version": "repomentor.sdk.delivery.v1", "verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": "SDK-only agent runtime and expanded local end-to-end validation",
        "engineering_checks": "passed", "model_quality": "not_accepted", "human_approved": False,
        "sdk": sdk_version(), "python": sys.version, "offline_tests_passed": passed,
        "tests_sha256": sha(test_report), "ts_tests_run": False,
        "historical_records": verify_archive(), "initial_cases": 30, "targeted_regression_cases": 4,
        "new_local_requests": sum(a["new_local_requests"] for a in audits), "paid_requests": 0,
        "replay_new_model_requests": 0, "documentation_links_checked": links,
        "wheel": {"name": wheel.name, "sha256": sha(wheel), "bytes": wheel.stat().st_size},
        "versions": {p: importlib.metadata.version(p) for p in ("openai-agents", "openai", "httpx2", "pydantic")},
        "source_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted((ROOT / "src").rglob("*.py"))},
        "limits": ["Original report failures remain in the first suite; targeted retries use a separate directory.",
                   "Workflow/schema success is not correctness; both latest reports still have serious content gaps.",
                   "Nonblind source diagnoses are not human approval and validation trajectories cannot train MiniMind."]}
    write(current / "acceptance.json", value)
    for folder in (initial, current):
        write(folder / "artifact-hashes.json", {p.relative_to(folder).as_posix(): sha(p)
              for p in sorted(folder.rglob("*.json")) if p.name != "artifact-hashes.json"})
    print(json.dumps({k: value[k] for k in ("engineering_checks", "model_quality", "offline_tests_passed",
                                          "new_local_requests", "paid_requests", "documentation_links_checked")}))


if __name__ == "__main__":
    main()
