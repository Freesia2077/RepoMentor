"""Check frozen experiment bytes offline; no model settings, SDK or inference required."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(root=ROOT):
    root = Path(root).resolve()
    manifests = sorted((root / "experiments").glob("*/completed-files.json"))
    if not manifests:
        raise ValueError("No frozen experiment manifests found")
    counts = {}
    for manifest in manifests:
        base = manifest.parent
        entries = json.loads(manifest.read_text(encoding="utf-8"))["sha256"]
        for name, expected in entries.items():
            path = (base / name).resolve()
            if not path.is_relative_to(base.resolve()):
                raise ValueError("archive path escapes its root")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"archived result changed: {path.relative_to(root)}")
        counts[str(manifest.relative_to(root))] = len(entries)
    result = {"verified_files": sum(counts.values()), "manifests": counts, "model_requests": 0}
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    verify()
