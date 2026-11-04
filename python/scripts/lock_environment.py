"""Lock the actual isolated Conda dependency set, excluding Conda bootstrap packages."""

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

from claude_agent_sdk._cli_version import __cli_version__

assert sys.flags.no_user_site, "run with -s to avoid locking user-site packages"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--phase", choices=("p0", "p3", "p4", "p5", "p6-sdk"), default="p0")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
exclude = {"pip", "setuptools", "wheel", "repomentor-core"}
packages = sorted(
    (d.metadata["Name"], d.version)
    for d in importlib.metadata.distributions()
    if d.metadata["Name"].lower() not in exclude
)
(root / "requirements-win-py312.lock").write_text(
    f"# Exact P0-{args.phase.upper() if args.phase != 'p0' else 'P2'} environment; Windows x64, Python 3.12; generated with user-site disabled.\n"
    + ("--extra-index-url https://download.pytorch.org/whl/cu128\n" if args.phase in ("p5", "p6-sdk") else "")
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
if args.phase in ("p3", "p4", "p5", "p6-sdk"):
    record.pop("python_prefix")
    record.pop("python_sources")
    record.update(
        {
            "python": sys.version.split()[0],
            "user_site_disabled": bool(sys.flags.no_user_site),
            "httpx": importlib.metadata.version("httpx"),
            "jsonschema": importlib.metadata.version("jsonschema"),
        }
    )
    if args.phase in ("p4", "p5", "p6-sdk"):
        record["tokenizers"] = importlib.metadata.version("tokenizers")
    if args.phase in ("p5", "p6-sdk"):
        record["torch_wheel_source"] = "https://download.pytorch.org/whl/cu128"
        record["build_setuptools"] = importlib.metadata.version("setuptools")
        for name in (
            "torch",
            "transformers",
            "faiss-cpu",
            "numpy",
            "tree-sitter",
            "tree-sitter-python",
            "tree-sitter-javascript",
            "tree-sitter-typescript",
        ):
            record[name] = importlib.metadata.version(name)
    if args.phase == "p6-sdk":
        for name in ("openai-agents", "openai", "httpx2"):
            record[name] = importlib.metadata.version(name)
(root / f"experiments/{args.phase}").mkdir(parents=True, exist_ok=True)
(root / f"experiments/{args.phase}/runtime-versions.json").write_text(
    json.dumps(record, indent=2) + "\n", encoding="utf-8"
)
print(f"Locked {len(packages)} distributions; SDK CLI {__cli_version__}")
