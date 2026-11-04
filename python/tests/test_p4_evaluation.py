import hashlib
import json

import pytest
from test_rag import FakeChat

from repomentor.rag import citation_validity
from repomentor.rag_evaluation import coverage, load_rag_tasks, run_rag_evaluation
from repomentor.repository import SnapshotStore


def test_span_coverage_and_citation_union():
    gold = [{"path": "a", "start_line": 2, "end_line": 6}]
    spans = [{"path": "a", "start_line": 1, "end_line": 3}, {"path": "a", "start_line": 4, "end_line": 8}]
    assert coverage(gold, spans) == 1
    assert coverage(gold, spans[:1]) == 0
    assert coverage([], spans) is None
    assert citation_validity({"citations": gold, "insufficient_evidence": False}, spans)
    assert not citation_validity(
        {"citations": [{**gold[0], "end_line": 10**12}], "insufficient_evidence": False}, spans
    )


def test_final_test_tasks_remain_sealed(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text(json.dumps({"task_id": "x", "split": "test"}))
    with pytest.raises(ValueError, match="sealed"):
        load_rag_tasks(path)


async def test_eval_gold_is_scoring_only_and_resume_has_zero_calls(repo, tmp_path):
    snapshots = SnapshotStore(tmp_path / "snapshots")
    reader = snapshots.import_repository(str(repo))
    text, _ = reader.read("src/index.ts", 10000)
    content = text.splitlines()[0]
    task = {
        "task_id": "fixture",
        "repository": reader.snapshot.source,
        "split": "validation",
        "task_type": "code",
        "snapshot_id": reader.snapshot.snapshot_id,
        "commit_sha": reader.snapshot.commit_sha,
        "input": {"question": "UNIQUE_NO_LEXICAL_MATCH"},
        "gold": {
            "answer": "PRIVATE_GOLD_ANSWER",
            "evidence": [
                {
                    "path": "src/index.ts",
                    "start_line": 1,
                    "end_line": 1,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            ],
        },
    }
    runtime = FakeChat()
    output = tmp_path / "eval.jsonl"
    await run_rag_evaluation([task], runtime, snapshots, tmp_path / "runtime", output, emit=lambda _: None)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert runtime.calls == 3
    assert all("PRIVATE_GOLD_ANSWER" not in str(r["result"]["input_messages"]) for r in rows)
    assert not rows[0]["result"]["context"] and not rows[1]["result"]["context"]
    assert rows[2]["result"]["context"][0]["content"] == content
    result = await run_rag_evaluation(
        [task], runtime, snapshots, tmp_path / "runtime", output, emit=lambda _: None
    )
    assert runtime.calls == 3 and all(r["cache_hits"] == 1 for r in result["modes"].values())
