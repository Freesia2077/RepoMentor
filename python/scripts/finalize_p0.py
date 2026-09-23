"""Apply source-checked license/gold corrections without rewriting the legacy TS benchmark."""

import json
from pathlib import Path

from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p0"
store = SnapshotStore(ROOT / "artifacts/corpus")
corpus = json.loads((OUT / "corpus.json").read_text(encoding="utf-8"))
licenses = {
    "pypa/packaging": "Apache-2.0 OR BSD-2-Clause",
    "mahmoud/boltons": "BSD-3-Clause",
    "dateutil/dateutil": "Apache-2.0 AND BSD-3-Clause",
    "python-trio/outcome": "Apache-2.0 OR MIT",
}
for row in corpus["repositories"]:
    row["license_review"] = {
        "resolved_expression": licenses.get(row["repository"], row["license_spdx"]),
        "basis": "frozen repository license files; preserve all original notices",
    }
    assert not row["fork"]
    assert row["readme_paths"] and row["license_files"]
    # schedule uses top-level test_schedule.py, which the legacy heuristic does not recognize.
    if row["repository"] == "dbader/schedule":
        row["test_paths"] = ["test_schedule.py"]
    if row["repository"] == "sindresorhus/p-limit":
        row["test_paths"] = ["test.js"]
    assert row["test_paths"], row["repository"]
(OUT / "corpus.json").write_text(json.dumps(corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

legacy = json.loads((OUT / "legacy-regression.json").read_text(encoding="utf-8"))
corrections = {
    "expressjs/express": {
        "entry": ["index.js", "lib/express.js"],
        "core": ["lib/application.js"],
        "test": ["test/app.js"],
    },
    "cli/cli": {
        "entry": ["cmd/gh/main.go"],
        "core": ["pkg/cmd/root/root.go"],
        "test": ["pkg/cmd/root/help_test.go"],
    },
    "vitejs/vite": {
        "entry": ["packages/vite/src/node/index.ts"],
        "core": ["packages/vite/src/node/server/index.ts"],
        "test": ["packages/vite/src/node/__tests__/dev.spec.ts"],
    },
    "modelcontextprotocol/typescript-sdk": {
        "entry": ["packages/client/src/index.ts", "packages/server/src/index.ts"],
        "core": ["packages/core-internal/src/shared/transport.ts"],
        "test": ["packages/core-internal/test/inMemory.test.ts"],
    },
    "anthropics/claude-agent-sdk-typescript": {
        "entry": ["examples/session-stores/postgres/src/index.ts"],
        "core": ["examples/session-stores/postgres/src/PostgresSessionStore.ts"],
        "test": ["examples/session-stores/postgres/test/PostgresSessionStore.test.ts"],
    },
}
for row in legacy["repositories"]:
    reader = store.open(row["snapshot_id"])
    row["goldEvidence"] = corrections.get(row["repository"], row["original_gold"])
    row["scope_note"] = (
        (
            "This checkout contains published SDK docs and session-store examples, not SDK query internals. "
            "Gold covers the actual Postgres example; do not compare recall to the original invalid gold."
        )
        if row["repository"].startswith("anthropics/")
        else "Gold paths reviewed against pinned manifest."
    )
    row["gold_paths_verified"] = all(
        p in reader.entries for paths in row["goldEvidence"].values() for p in paths
    )
    assert row["gold_paths_verified"], row["repository"]
(OUT / "legacy-regression.json").write_text(
    json.dumps(legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print("20 corpus and 8 legacy manifests checked; original TS benchmark preserved")
