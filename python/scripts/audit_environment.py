"""Record only software/hardware facts. Never dump environment variables or secrets."""

import argparse
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path


def command(*args):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return proc.stdout.strip() if proc.returncode == 0 else {"unavailable": proc.returncode}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"unavailable": type(exc).__name__}


def audit():
    packages = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
    return {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "python": sys.version,
        "platform": platform.platform(),
        "packages": dict(sorted(packages.items())),
        "git": command("git", "--version"),
        "node": command("node", "--version"),
        "gpu": command("nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"),
        "ts_baseline": command("git", "rev-parse", "HEAD"),
        "scope": "P0-P2: SDK/core only; torch, CUDA kernels, retrieval and training are not installed or tested",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
