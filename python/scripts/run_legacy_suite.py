"""Bounded offline orchestration of the frozen legacy set; retains each actual model outcome."""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
rows = json.loads((ROOT / "experiments/p0/legacy-regression.json").read_text(encoding="utf-8"))[
    "repositories"
]


def run(row, allow_new_api=False):
    name = row["repository"]
    output = ROOT / "experiments/p2" / (name.replace("/", "--") + "-report.json")
    if not output.exists() and not allow_new_api:
        return {
            "repository": name,
            "commit_sha": row["commit_sha"],
            "status": "not_run",
            "stop_reason": "api_budget_preference",
            "new_api_calls": 0,
        }
    proc = subprocess.run(
        [
            sys.executable,
            "-s",
            "-X",
            "utf8",
            str(ROOT / "scripts/run_live_baseline.py"),
            "--repository",
            name,
            "--legacy",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=700,
    )
    output = ROOT / "experiments/p2" / (name.replace("/", "--") + "-report.json")
    result = (
        json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"status": "launcher_failed"}
    )
    result["launcher_exit_code"] = proc.returncode
    print(name, result["status"], result.get("stop_reason"), flush=True)
    return {
        "repository": name,
        "commit_sha": row["commit_sha"],
        "status": result["status"],
        "stop_reason": result.get("stop_reason"),
        "result_file": str(output.relative_to(ROOT)),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "runtime": result.get("runtime"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reuse recorded runs by default; no new API calls")
    parser.add_argument("--allow-new-api", action="store_true", help="allow missing reports to call the API")
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=2) as pool:
        summary = list(pool.map(lambda row: run(row, args.allow_new_api), rows))
    (ROOT / "experiments/p2/legacy-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
