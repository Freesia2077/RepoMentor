import hashlib
import json
from pathlib import Path

from repomentor.paths import data_root
from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_splits_and_licenses():
    rows = json.loads((ROOT / "experiments/p0/corpus.json").read_text(encoding="utf-8"))["repositories"]
    assert {s: sum(r["split"] == s for r in rows) for s in ("train", "validation", "test")} == {
        "train": 12,
        "validation": 4,
        "test": 4,
    }
    for split, count in (("train", 6), ("validation", 2), ("test", 2)):
        assert sum(r["split"] == split and r["language_group"] == "Python" for r in rows) == count
    families = {}
    for r in rows:
        assert len(r["commit_sha"]) == 40 and r["license_review"]["resolved_expression"]
        assert not r["fork"] and r["readme_paths"] and r["test_paths"]
        assert families.setdefault(r["family"], r["split"]) == r["split"]


def test_seed_contract_and_no_gold_in_input():
    tasks = [
        json.loads(line)
        for line in (ROOT / "experiments/p0/validation-seed.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(tasks) == len({t["task_id"] for t in tasks}) == 60
    for task in tasks:
        assert task["split"] == "validation" and set(task["input"]) == {"question", "history"}
        assert task["review"]["source_reviewed"] and not task["review"]["human_approved"]
        assert task["gold"]["required_facts"] and task["gold"]["evidence"]


def test_pinned_seed_evidence_hashes():
    # Cache-optional integration test: portable unit suite must not download external repositories.
    import pytest

    if not (data_root() / "corpus/snapshots").exists():
        pytest.skip("downloaded P0 snapshots not available; run freeze_corpus first")
    store = SnapshotStore(data_root() / "corpus")
    tasks = [
        json.loads(line)
        for line in (ROOT / "experiments/p0/validation-seed.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for task in tasks:
        reader = store.open(task["snapshot_id"])
        for evidence in task["gold"]["evidence"]:
            text, truncated = reader.read(evidence["path"], 500_000)
            assert not truncated
            snippet = "\n".join(text.splitlines()[evidence["start_line"] - 1 : evidence["end_line"]])
            assert hashlib.sha256(snippet.encode()).hexdigest() == evidence["content_sha256"]
