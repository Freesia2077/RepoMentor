"""Lock the actual isolated Conda dependency set, excluding Conda bootstrap packages."""

import importlib.metadata
import json
import sys
from pathlib import Path

from claude_agent_sdk._cli_version import __cli_version__

assert sys.flags.no_user_site, "run with -s to avoid locking user-site packages"
root = Path(__file__).resolve().parents[1]
exclude = {"pip", "setuptools", "wheel", "repomentor-core"}
packages = sorted(
    (d.metadata["Name"], d.version)
    for d in importlib.metadata.distributions()
    if d.metadata["Name"].lower() not in exclude
)
(root / "requirements-win-py312.lock").write_text(
    "# Exact P0-P2 environment; Windows x64, Python 3.12; generated with user-site disabled.\n"
    + "\n".join(f"{name}=={version}" for name, version in packages)
    + "\n",
    encoding="utf-8",
)
record = {
    "sdk": importlib.metadata.version("claude-agent-sdk"),
    "bundled_cli": __cli_version__,
    "python_prefix": sys.prefix,
    "package_version": importlib.metadata.version("repomentor-core"),
    "python_sources": [
        str(d.locate_file(""))
        for d in importlib.metadata.distributions()
        if d.metadata["Name"].lower() in ("pydantic", "claude-agent-sdk", "pytest")
    ],
}
(root / "experiments/p0/runtime-versions.json").write_text(
    json.dumps(record, indent=2) + "\n", encoding="utf-8"
)
print(f"Locked {len(packages)} distributions; SDK CLI {__cli_version__}")
