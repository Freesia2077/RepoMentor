"""Validate recorded model results offline. No API calls; no semantic-quality claims."""

import json
from pathlib import Path

from repomentor.config import write_json
from repomentor.contracts import ContributorOutput, ExplorerOutput, MentorOutput
from repomentor.paths import data_root

ROOT = Path(__file__).resolve().parents[1]
legacy = json.loads((ROOT / "experiments/p0/legacy-regression.json").read_text(encoding="utf-8"))
gold = {r["repository"]: r for r in legacy["repositories"]}
records = []
for path in sorted((ROOT / "experiments/p2").glob("*-report.json")):
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["status"] == "success", path.name
    for stage, model in (
        ("explorer", ExplorerOutput),
        ("mentor", MentorOutput),
        ("contributor", ContributorOutput),
    ):
        model.model_validate(result["outputs"][stage])
        assert result["harness"]["stages"][stage]["status"] == "completed"
    budget = result["harness"]["budget"]
    assert (
        budget["usedEvidenceBatches"] <= 2 and budget["filesRead"] <= 18 and budget["evidenceBytes"] <= 88_000
    )
    read = {p for p, entry in result["harness"]["ledger"].items() if entry["status"] != "missing"}
    citations = [
        ref["path"]
        for value in result["outputs"].values()
        for claim in value["evidenceClaims"]
        for ref in claim["evidence"]
    ]
    event_path = data_root() / "live/runs" / result["run_id"] / "events.jsonl"
    events = (
        [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        if event_path.exists()
        else []
    )
    row = {
        "repository": result["repository"],
        "commit_sha": result["commit_sha"],
        "schema_and_budget_passed": True,
        "elapsed_seconds": result["elapsed_seconds"],
        "budget": budget,
        "sdk_sessions": len(result["usage"]),
        "discovery_actions": sum(s["discoveryActionsUsed"] for s in result["harness"]["stages"].values()),
        "citation_count": len(citations),
        "event_log_available": event_path.exists(),
        "references_removed_by_harness": sum(
            e["data"].get("removed_references", 0) for e in events if e["kind"] == "verification"
        )
        if event_path.exists()
        else None,
        "semantic_correctness": "not scored; documented source-reviewed failure examples exist",
    }
    if result["repository"] in gold:
        ref = gold[result["repository"]]
        assert ref["commit_sha"] == result["commit_sha"]
        paths = {p for values in ref["goldEvidence"].values() for p in values}
        row["legacy_gold_path_recall"] = len(read & paths) / len(paths)
        row["legacy_gold_read"] = sorted(read & paths)
        row["legacy_gold_missing"] = sorted(paths - read)
    records.append(row)
write_json(
    ROOT / "experiments/p2/offline-verification.json",
    {
        "new_api_calls": 0,
        "records": records,
        "notes": "Snapshot/path/schema/budget verification. Legal citations do not establish semantic truth. "
        "Gold path recall uses the corrected legacy gold and is not comparable with old invalid paths.",
    },
)
print(
    json.dumps(
        {
            "verified_reports": len(records),
            "new_api_calls": 0,
            "legacy_reports": sum("legacy_gold_path_recall" in r for r in records),
        }
    )
)
