import json
import subprocess
from pathlib import Path

import pytest
from test_core import plan, ready

from repomentor.harness import HarnessError, build_evidence_bundle
from repomentor.skills import load_skill


@pytest.mark.ts_parity
def test_ts_python_harness_semantics(repo, reader, tmp_path):
    action = {"tool": "search_symbols", "query": "logMiddleware", "purpose": "locate"}
    p = plan("./src\\index.ts", "src/core.ts", actions=[action])
    mentor = plan("src/index.ts")
    raw = plan("./src\\index.ts", "src/index.ts", "missing.ts")
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps({"plan": p.model_dump(), "mentor": mentor.model_dump(), "rawPlan": raw.model_dump()})
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["node", "--import", "tsx", "python/scripts/ts_parity.mjs", str(repo), str(scenario)],
        cwd=root,
        capture_output=True,
        check=True,
        encoding="utf-8",
        timeout=30,
    )
    expected = json.loads(result.stdout)
    h = ready(reader)
    h.discover("explorer", action)
    errors = {}
    try:
        h.discover("explorer", action)
    except HarnessError as exc:
        errors[exc.code] = True
    try:
        h.discover("explorer", {"tool": "find_related_tests", "paths": ["../secret"], "purpose": "reject"})
    except HarnessError as exc:
        errors[exc.code] = True
    h.execute_plan("explorer", p)
    h.complete_stage("explorer")
    h.activate_policy("mentor", load_skill([], "mentor")[0])
    h.execute_plan("mentor", mentor)
    state = h.context()
    actual = {
        "budget": state["budget"],
        "stages": {
            k: {
                "status": v["status"],
                "examinedPaths": v["examinedPaths"],
                "skippedPaths": v["skippedPaths"],
                "discoveryActionsUsed": v["discoveryActionsUsed"],
                "stopReason": v["stopReason"],
                "selected": [f["path"] for f in v["plan"]["files"]] if v["plan"] else [],
            }
            for k, v in state["stages"].items()
        },
        "ledger": {p: row["status"] for p, row in state["ledger"].items()},
        "errors": errors,
    }
    bundle = build_evidence_bundle(reader, raw, "explorer")
    actual["rawBundle"] = {
        "files": [
            {"path": f.path, "bytes": len(f.content.encode()), "truncated": f.truncated} for f in bundle.files
        ],
        "omissions": [o.model_dump() for o in bundle.omissions],
        "totalBytes": bundle.totalBytes,
    }
    assert actual == expected
