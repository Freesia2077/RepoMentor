"""Explicit live cancellation/cleanup contract check, separate from report quality results."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from repomentor.config import load_model_settings, write_json
from repomentor.harness import RepositoryHarness
from repomentor.paths import data_root
from repomentor.repository import SnapshotStore
from repomentor.runtimes.claude import ClaudeSdkRuntime
from repomentor.skills import load_skill
from repomentor.storage import RunStore

ROOT = Path(__file__).resolve().parents[1]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, help="Python model settings override")
    args = parser.parse_args()
    settings = load_model_settings(args.settings)
    rows = json.loads((ROOT / "experiments/p0/corpus.json").read_text(encoding="utf-8"))["repositories"]
    row = next(r for r in rows if r["repository"] == "developit/mitt")
    reader = SnapshotStore(data_root() / "corpus").open(row["snapshot_id"])
    store = RunStore(data_root() / "live", uuid4().hex)
    h = RepositoryHarness(reader, store.emit)
    h.get_repository_map()
    h.activate_policy("explorer", load_skill([], "explorer")[0])
    runtime = ClaudeSdkRuntime(settings, emit=store.emit)
    cwd_paths = []
    original = runtime.make_options

    def options(*args, **kwargs):
        result = original(*args, **kwargs)
        cwd_paths.append(result.cwd)
        return result

    runtime.make_options = options
    asyncio.get_running_loop().call_later(3, h.token.cancel)
    code = "unexpected_success"
    try:
        await runtime.plan("explorer", h, "Inspect implementation and tests.")
    except Exception as error:
        code = getattr(error, "code", type(error).__name__)
    record = {
        "code": code,
        "temporary_cwds_removed": all(not Path(p).exists() for p in cwd_paths),
        "tool_actions_committed": h.stages["explorer"].discoveryActionsUsed,
        "versions": runtime.versions,
    }
    write_json(ROOT / "experiments/p2/live-cancellation.json", record)
    print(json.dumps(record))
    assert code == "cancelled" and record["temporary_cwds_removed"]


if __name__ == "__main__":
    asyncio.run(main())
