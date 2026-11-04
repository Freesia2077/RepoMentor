"""Check frozen experiment bytes offline; no model settings, SDK or inference required."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def verify(root=ROOT):
    root = Path(root).resolve()
    manifests = [(root, root / "experiments/p63/protocol.json", "protected_files")]
    for manifest in sorted((root / "experiments").glob("*/completed-files.json")):
        manifests.append((manifest.parent, manifest, "sha256"))
    for manifest in sorted((root / "experiments/workflow-diagnosis").glob("*/manifest.json")):
        manifests.append((manifest.parent, manifest, "sha256"))
    manifest = root / "experiments/improvement-confirmation-design/manifest.json"
    if manifest.is_file():
        manifests.append((manifest.parent, manifest, "sha256"))
    counts = {}
    for base, manifest, key in manifests:
        entries = json.loads(manifest.read_text(encoding="utf-8"))[key]
        for name, expected in entries.items():
            path = (base / name).resolve()
            if not path.is_relative_to(base.resolve()):
                raise ValueError("archive path escapes its root")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"archived result changed: {path.relative_to(root)}")
        counts[str(manifest.relative_to(root))] = len(entries)
    study = root / "experiments/improvement-study"
    report = study / "report/manifest.json"
    if (study / "method-source.zip").is_file() and report.is_file():
        inputs = json.loads(report.read_text(encoding="utf-8"))["inputs_sha256"]
        with ZipFile(study / "method-source.zip") as archive:
            for name, expected in inputs.items():
                path = (root / name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("report input escapes Python root")
                raw = path.read_bytes() if path.is_file() else b""
                if hashlib.sha256(raw).hexdigest() != expected:
                    raw = archive.read(name)
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError(f"archived report input changed: {name}")
        counts[str(report.relative_to(root)) + ":inputs"] = len(inputs)
    result = {"verified_files": sum(counts.values()), "manifests": counts, "model_requests": 0}
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    verify()
