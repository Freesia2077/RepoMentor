"""Read-only checks for frozen P6 records; the historical agent loop was removed."""

import hashlib
import json
from pathlib import Path

from repomentor.paths import data_root
from repomentor.rag import citation_validity, verify_spans
from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p6"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_archive():
    protocol = json.loads((OUT / "protocol.json").read_text(encoding="utf-8"))
    hashes = json.loads((OUT / "artifact-hashes.json").read_text(encoding="utf-8"))
    for name, digest in protocol["protected_files"].items():
        if sha(ROOT / name) != digest:
            raise ValueError(f"protected input changed: {name}")
    for name, digest in hashes.items():
        if sha(OUT / name) != digest:
            raise ValueError(f"archived artifact changed: {name}")
    corpus = SnapshotStore(data_root() / "corpus")
    checked = 0
    for wrapped in read_jsonl(OUT / "agent-resumed.jsonl"):
        row = wrapped["result"]
        reader = corpus.open(row["snapshot_id"])
        assert row["commit_sha"] == reader.snapshot.commit_sha
        verify_spans(reader, row["context"])
        assert row["citation_ranges_valid"] == citation_validity(row["answer"], row["context"])
        assert row["tool_calls"] <= row["limits"]["max_tool_calls"]
        checked += len(row["context"])
    return {"status": "historical_records_verified", "current_runtime_acceptance": False,
            "protected_files": len(protocol["protected_files"]), "archived_files": len(hashes),
            "source_spans": checked, "new_model_requests": 0, "human_approved": False}


def main():
    print(json.dumps(verify_archive()))


if __name__ == "__main__":
    main()
