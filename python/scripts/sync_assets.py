"""Copy only trusted application assets. Never point this at an analyzed repository."""

import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
dest = root / "python/src/repomentor/assets"
records = {}
paths = [
    *root.glob("agents/*/agent.md"),
    *root.glob("skills/*/skill.json"),
    *root.glob("skills/*/SKILL.md"),
    root / ".claude-plugin/plugin.json",
]
for path in paths:
    rel = path.relative_to(root)
    target = dest / (Path("plugin") / rel if rel.parts[0] != "agents" else rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_bytes().replace(b"\r\n", b"\n")
    target.write_bytes(content)
    records[str(rel).replace("\\", "/")] = hashlib.sha256(content).hexdigest()
(dest / "provenance.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
print(f"Copied {len(records)} trusted assets")
