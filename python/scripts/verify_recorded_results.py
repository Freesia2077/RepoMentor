"""Check recorded experiment bytes without requiring legacy pruned files."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def _manifests(root: Path):
    manifests = [(root, root / "experiments/p63/protocol.json", "protected_files")]
    manifests += [
        (p.parent, p, "sha256") for p in sorted((root / "experiments").glob("*/completed-files.json"))
    ]
    manifests += [
        (p.parent, p, "sha256")
        for p in sorted((root / "experiments/workflow-diagnosis").glob("*/manifest.json"))
    ]
    manifest = root / "experiments/improvement-confirmation-design/manifest.json"
    if manifest.is_file():
        manifests.append((manifest.parent, manifest, "sha256"))
    return manifests


def verify(root=ROOT):
    root = Path(root).resolve()
    counts, missing = {}, []
    for base, manifest, key in _manifests(root):
        entries = json.loads(manifest.read_text(encoding="utf-8"))[key]
        verified = 0
        for name, expected in entries.items():
            path = (base / name).resolve()
            if not path.is_relative_to(base.resolve()):
                raise ValueError("archive path escapes its root")
            if not path.exists():
                missing.append(str(path.relative_to(root)))
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"archived result changed: {path.relative_to(root)}")
            verified += 1
        counts[str(manifest.relative_to(root))] = verified

    study = root / "experiments/improvement-study"
    report = study / "report/manifest.json"
    if (study / "method-source.zip").is_file() and report.is_file():
        inputs = json.loads(report.read_text(encoding="utf-8"))["inputs_sha256"]
        verified_inputs = 0
        with ZipFile(study / "method-source.zip") as archive:
            for name, expected in inputs.items():
                path = (root / name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("report input escapes Python root")
                if path.is_file():
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() == expected:
                        verified_inputs += 1
                        continue
                try:
                    raw = archive.read(name)
                except KeyError:
                    missing.append(name)
                    continue
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError(f"archived report input changed: {name}")
                verified_inputs += 1
        counts[str(report.relative_to(root)) + ":inputs"] = verified_inputs

    result = {
        "verified_files": sum(counts.values()),
        "missing_files": sorted(set(missing)),
        "manifests": counts,
        "model_requests": 0,
    }
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    verify()
