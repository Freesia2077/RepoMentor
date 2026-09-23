"""Metadata-only compatibility audit, no model weights or training packages downloaded."""

import json
import platform
import urllib.request
from pathlib import Path

from packaging.specifiers import SpecifierSet

root = Path(__file__).resolve().parents[1]
results = []
for name in ("torch", "transformers", "sentence-transformers", "faiss-cpu"):
    with urllib.request.urlopen("https://pypi.org/pypi/" + name + "/json", timeout=30) as response:
        data = json.load(response)
    requires = data["info"]["requires_python"] or ""
    results.append(
        {
            "package": name,
            "version_audited": data["info"]["version"],
            "requires_python": requires,
            "interpreter_eligible": platform.python_version() in SpecifierSet(requires),
            "matching_wheels": [
                f["filename"]
                for f in data["urls"]
                if "py3-none-any" in f["filename"]
                or (
                    "win_amd64" in f["filename"] and any(t in f["filename"] for t in ("cp312", "abi3", "py3"))
                )
            ],
            "installed": False,
            "runtime_compatibility_tested": False,
        }
    )
(root / "experiments/p0/deferred-dependency-compatibility.json").write_text(
    json.dumps(
        {
            "scope": "metadata check only; CUDA kernels and joint dependency resolution deferred to P3/P5/P7",
            "packages": results,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
print("Metadata audit saved; no training/retrieval package installed")
