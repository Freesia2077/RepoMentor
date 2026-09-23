"""Explicit live reports; model settings are read locally and never copied into results."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from repomentor.config import load_model_settings, write_json
from repomentor.harness import RepositoryHarness
from repomentor.report import run_report
from repomentor.repository import SnapshotStore
from repomentor.runtimes.claude import ClaudeSdkRuntime
from repomentor.skills import load_skill
from repomentor.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default="developit/mitt")
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--stub", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="explicitly replace an existing recorded run")
    args = parser.parse_args()
    manifest = ROOT / "experiments/p0" / ("legacy-regression.json" if args.legacy else "corpus.json")
    rows = json.loads(manifest.read_text(encoding="utf-8"))["repositories"]
    row = next(r for r in rows if r["repository"] == args.repository)
    suffix = "discovery" if args.discovery_only else "stub" if args.stub else "report"
    output = ROOT / "experiments/p2" / (args.repository.replace("/", "--") + "-" + suffix + ".json")
    if output.exists() and not args.refresh:
        existing = json.loads(output.read_text(encoding="utf-8"))
        if existing.get("commit_sha") != row["commit_sha"]:
            raise ValueError("saved result belongs to a different SHA; inspect before using --refresh")
        print(
            json.dumps(
                {
                    "output": str(output),
                    "reused": True,
                    "status": existing.get("status", "success"),
                    "new_api_calls": 0,
                }
            )
        )
        return
    reader = SnapshotStore(ROOT / "artifacts/corpus").open(row["snapshot_id"])
    run_id = uuid4().hex
    store = RunStore(ROOT / "artifacts/live", run_id)
    if args.stub:
        from repomentor.runtimes.stub import StubRuntime

        runtime = StubRuntime()
        if args.discovery_only:
            parser.error("--discovery-only requires the SDK runtime")
    else:
        settings = load_model_settings(ROOT.parent / "data/model-settings.json")
        runtime = ClaudeSdkRuntime(settings, emit=store.emit)
    if args.discovery_only:
        harness = RepositoryHarness(reader, store.emit)
        harness.get_repository_map()
        policy, text = load_skill([], "explorer")
        harness.activate_policy("explorer", policy)
        # Dedicated MCP capability test requires a real call; normal report planning remains adaptive.
        text += "\nFor this integration test, first call search_symbols with query 'emit' before submitting the plan."
        result = await runtime.plan("explorer", harness, text)
        record = {
            "repository": args.repository,
            "commit_sha": row["commit_sha"],
            "runtime_versions": runtime.versions,
            "model": settings.model,
            "plan": result.model_dump(),
            "harness": harness.context(),
            "usage": runtime.usage,
            "actual_tool_calls": harness.stages["explorer"].discoveryActionsUsed,
        }
        assert record["actual_tool_calls"] > 0, "live MCP tool was not exercised"
    else:
        result = await run_report(reader, runtime, emit=store.emit, run_id=run_id)
        store.save(result)
        record = result.model_dump()
        record["repository"] = args.repository
        record["commit_sha"] = row["commit_sha"]
    write_json(output, record)
    print(
        json.dumps(
            {
                "output": str(output),
                "status": record.get("status", "success"),
                "reason": record.get("stop_reason"),
                "actual_tool_calls": record.get("actual_tool_calls"),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
