import hashlib
import json

import pytest
from test_chat import settings

from repomentor.evaluation import EvaluationTask, load_tasks, run_evaluation, task_messages
from repomentor.repository import SnapshotStore
from repomentor.runtimes.chat import CompletionResult, fingerprint


def test_atomic_write_retries_transient_destination_lock(tmp_path, monkeypatch):
    from pathlib import Path

    from repomentor.evaluation import atomic_write

    original, calls = Path.replace, []

    def transient(self, target):
        calls.append(target)
        if len(calls) == 1:
            raise PermissionError("transient Windows reader")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", transient)
    path = tmp_path / "checkpoint.json"
    atomic_write(path, "preserved result")
    assert path.read_text() == "preserved result" and len(calls) == 2
    assert not list(tmp_path.glob(".tmp-*"))


@pytest.fixture
def tasks(reader):
    content = reader.read("src/index.ts")[0].splitlines()[0]
    task = EvaluationTask(
        task_id="fixture-1",
        repository=reader.snapshot.source,
        commit_sha=reader.snapshot.commit_sha,
        snapshot_id=reader.snapshot.snapshot_id,
        split="validation",
        context_mode="provided",
        input={"question": "Explain the entry.", "history": []},
        context=[
            {
                "path": "src/index.ts",
                "start_line": 1,
                "end_line": 1,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        ],
    )
    return [task, task.model_copy(update={"task_id": "fixture-2"})]


class FakeRuntime:
    name = "fake-no-model"

    def __init__(self, status="success"):
        self.settings = settings()
        self.calls = 0
        self.verifications = 0
        self.status = status

    async def verify_backend(self):
        self.verifications += 1
        return {"verification": "fixture"}

    async def complete(self, messages, **kwargs):
        self.calls += 1
        return CompletionResult(
            status=self.status,
            finish_reason="stop",
            request_hash=fingerprint(messages),
            request_sent=True,
            content=json.dumps(
                {
                    "answer": "fixture",
                    "insufficient_evidence": False,
                    "citations": [{"path": "src/index.ts", "start_line": 1, "end_line": 1}],
                }
            ),
        )


async def test_resume_reuses_completed_records_without_backend(tasks, tmp_path):
    runtime = FakeRuntime()
    store = SnapshotStore(tmp_path / "snapshots")
    kwargs = {"snapshots": store, "cache_dir": tmp_path / "cache", "output": tmp_path / "results.jsonl"}
    await run_evaluation(tasks[:1], runtime, **kwargs)
    resumed = await run_evaluation(tasks, runtime, **kwargs)
    assert resumed["new_requests"] == 1 and resumed["cache_hits"] == 1 and runtime.calls == 2
    again = await run_evaluation(tasks, runtime, **kwargs)
    assert again["new_requests"] == 0 and again["cache_hits"] == 2
    assert runtime.verifications == 2
    rows = [json.loads(line) for line in kwargs["output"].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[0]["citation_ranges_valid"]


async def test_failed_records_persist_until_explicit_retry(tasks, tmp_path):
    runtime = FakeRuntime("truncated")
    kwargs = {
        "snapshots": SnapshotStore(tmp_path / "snapshots"),
        "cache_dir": tmp_path / "cache",
        "output": tmp_path / "results.jsonl",
    }
    await run_evaluation(tasks[:1], runtime, **kwargs)
    runtime.status = "success"
    cached = await run_evaluation(tasks[:1], runtime, **kwargs)
    assert cached["statuses"] == {"truncated": 1} and runtime.calls == 1
    retried = await run_evaluation(tasks[:1], runtime, retry_failed=True, **kwargs)
    assert retried["statuses"] == {"success": 1} and runtime.calls == 2
    attempts = list((tmp_path / "cache" / runtime.settings.config_id / "attempts").glob("*.json"))
    assert {json.loads(path.read_text(encoding="utf-8"))["result"]["status"] for path in attempts} == {
        "truncated",
        "success",
    }


async def test_modified_question_and_model_config_invalidate_cache(tasks, tmp_path):
    runtime = FakeRuntime()
    kwargs = {
        "snapshots": SnapshotStore(tmp_path / "snapshots"),
        "cache_dir": tmp_path / "cache",
        "output": tmp_path / "results.jsonl",
    }
    await run_evaluation(tasks[:1], runtime, **kwargs)
    tasks[0].input.question = "Different question"
    assert (await run_evaluation(tasks[:1], runtime, **kwargs))["new_requests"] == 1
    runtime.settings.quantization = "BF16"
    assert (await run_evaluation(tasks[:1], runtime, **kwargs))["new_requests"] == 1


def test_gold_and_test_split_never_enter_p3(tasks, tmp_path):
    row = tasks[0].model_dump()
    with pytest.raises(ValueError):
        EvaluationTask.model_validate({**row, "gold": {"answer": "secret"}})
    assert "gold" not in json.dumps(task_messages(tasks[0]))
    path = tmp_path / "sealed.jsonl"
    path.write_text(json.dumps({**row, "split": "test"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        load_tasks(path)


async def test_context_tampering_rejected_before_model(tasks, tmp_path):
    span = tasks[0].context[0]
    # Even a self-consistent replacement hash cannot override the actual Git object.
    tasks[0].context[0] = span.model_copy(
        update={"content": "fake", "content_sha256": hashlib.sha256(b"fake").hexdigest()}
    )
    runtime = FakeRuntime()
    with pytest.raises(ValueError, match="pinned source"):
        await run_evaluation(
            tasks[:1],
            runtime,
            snapshots=SnapshotStore(tmp_path / "snapshots"),
            cache_dir=tmp_path / "cache",
            output=tmp_path / "results.jsonl",
        )
    assert runtime.calls == 0


def test_question_catalog_merges_aliases_without_merging_versions_or_histories(tmp_path):
    from repomentor.question_bank import build_bank, question_id

    task = {
        "task_id": "legacy",
        "repository": "owner/repo",
        "snapshot_id": "snap-a",
        "commit_sha": "commit-a",
        "split": "development",
        "input": {"question": "f('a  b')?"},
    }
    sources = {
        "p6/tasks.json": [task],
        "rag-interview/development/tasks.json": [{**task, "task_id": "recent"}],
        "p63/development.json": [
            {**task, "task_id": "new-version", "snapshot_id": "snap-b", "commit_sha": "commit-b"}
        ],
    }
    for name, value in sources.items():
        path = tmp_path / "experiments" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    cases = tmp_path / "experiments/p6/cases"
    cases.mkdir()
    (cases / "changed.json").write_text(
        json.dumps(
            {"task": {**task, "input": {"question": "Different?"}}, "result": {"answer": {"answer": "saved"}}}
        ),
        encoding="utf-8",
    )
    bank = build_bank(tmp_path)
    assert bank["summary"]["all"]["unique_questions"] == 3
    current = next(q for q in bank["questions"] if q["current"])
    assert {o["task_id"] for o in current["origins"]} == {"legacy", "recent"}
    assert not current["observations"]  # Reused legacy ID with a different question is not attached here.
    base = question_id("owner/repo", "commit-a", "f('a  b')?")
    assert base == question_id("OWNER/repo", "commit-a", " f('a  b')?\r\n")
    assert base != question_id("owner/repo", "commit-a", "f('a b')?")
    assert base != question_id("owner/repo", "commit-a", "f('a  b')?", {"prior_user_questions": ["Earlier?"]})
    assert base != question_id("owner/repo", "commit-a", "f('a  b')?", partition="training")


async def test_question_catalog_separates_retrieval_from_generation_and_verifies_offline(
    tmp_path, capsys, monkeypatch
):
    from repomentor import cli
    from repomentor.question_bank import build_bank

    task = {
        "task_id": "confirmed",
        "repository": "owner/repo",
        "snapshot_id": "snap",
        "commit_sha": "commit",
        "split": "confirmation",
        "input": {"question": "First?"},
    }
    second = {**task, "task_id": "retrieval", "split": "development", "input": {"question": "Second?"}}
    folder = tmp_path / "experiments/rag-expansion"
    folder.mkdir(parents=True)
    (folder / "dataset.json").write_text(
        json.dumps(
            {
                "repositories": [],
                "tasks": [task, second],
                "retrieval": [{"task_id": "confirmed"}, {"task_id": "retrieval"}],
            }
        ),
        encoding="utf-8",
    )
    (folder / "results.jsonl").write_text(
        json.dumps(
            {
                "case_id": "confirm/confirmed/bm25",
                "task_id": "confirmed",
                "result": {"completion": {"status": "truncated", "request_sent": True}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (folder / "summary.json").write_text(
        json.dumps({"reviews": {"confirm/confirmed/bm25": {"solved": False}}}), encoding="utf-8"
    )
    bank = build_bank(tmp_path)
    assert bank["summary"]["current"]["coverage"] == {"retrieval_only": 1, "reviewed": 1}
    failed = next(q for q in bank["questions"] if q["coverage"] == "reviewed")
    assert failed["generation_recorded"] and failed["observations"][0]["status"] == "truncated"
    assert not failed["training_eligible"]
    catalog = tmp_path / "experiments/question-bank.json"
    catalog.write_text(json.dumps(bank), encoding="utf-8")
    monkeypatch.setattr(
        cli, "load_chat_settings", lambda *a, **kw: pytest.fail("offline command loaded model configuration")
    )
    before = set(tmp_path.rglob("*"))
    options = ["--root", str(tmp_path), "--catalog", str(catalog)]
    assert await cli.async_main(cli.parser().parse_args(["questions", "verify", *options])) == 0
    assert '"model_requests": 0' in capsys.readouterr().out
    assert set(tmp_path.rglob("*")) == before
    (folder / "summary.json").write_text('{"reviews": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        await cli.async_main(cli.parser().parse_args(["questions", "verify", *options]))


def test_question_catalog_rejects_sealed_test_import(tmp_path):
    from repomentor.question_bank import build_bank

    path = tmp_path / "experiments/p6/tasks.json"
    path.parent.mkdir(parents=True)
    path.write_text('[{"task_id": "sealed", "split": "test-sealed"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        build_bank(tmp_path)


def test_new_comparison_reuses_question_and_does_not_claim_pending_answers(tmp_path):
    from repomentor.question_bank import build_bank

    task = {
        "task_id": "same",
        "repository": "owner/repo",
        "snapshot_id": "snap",
        "commit_sha": "commit",
        "split": "development",
        "input": {"question": "Implementation?"},
    }
    old = tmp_path / "experiments/p6/tasks.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps([task]), encoding="utf-8")
    new = tmp_path / "experiments/rag-system/dataset.json"
    new.parent.mkdir()
    new.write_text(
        json.dumps(
            {
                "schema": "repomentor.benchmark.v1",
                "tasks": [task],
                "retrieval_checks": [{"task_id": "same", "variant": "bm25"}],
                "cases": [{"task_id": "same", "condition": c} for c in ("none", "bm25", "agentic")],
            }
        ),
        encoding="utf-8",
    )
    bank = build_bank(tmp_path)
    assert bank["summary"]["all"]["unique_questions"] == 1
    assert bank["summary"]["latest_evaluation"]["planned_records"] == 3
    assert bank["summary"]["latest_evaluation"]["completed_records"] == 0
    question = bank["questions"][0]
    assert question["retrieval_checked"] and not question["generation_recorded"]
    assert question["latest_evaluation"]["completed_conditions"] == []
    assert question["latest_evaluation"]["planned_conditions"] == ["none", "bm25", "agentic"]


def test_finalizer_diagnostics_do_not_replace_latest_main_comparison(tmp_path):
    from repomentor.question_bank import build_bank

    task = {
        "task_id": "same",
        "repository": "owner/repo",
        "snapshot_id": "snap",
        "commit_sha": "commit",
        "split": "confirmation",
        "input": {"question": "Implementation?"},
    }
    for name, kind in (("a-main", "main"), ("z-finalizer", "known-question-finalizer-diagnostic")):
        target = tmp_path / "experiments" / name / "dataset.json"
        target.parent.mkdir(parents=True)
        target.write_text(
            json.dumps(
                {
                    "schema": "repomentor.benchmark.v1",
                    "tasks": [task],
                    "protocol": {"evaluation_date": "2026-10-23"},
                    "retrieval_checks": [],
                    "cases": [{"task_id": "same", "condition": name, "kind": kind}],
                }
            ),
            encoding="utf-8",
        )
    bank = build_bank(tmp_path)
    assert bank["summary"]["all"]["unique_questions"] == 1
    assert bank["summary"]["latest_evaluation"]["file"] == "experiments/a-main/dataset.json"
    question = bank["questions"][0]
    assert question["latest_evaluation"]["planned_conditions"] == ["a-main"]
    assert question["diagnostic_evaluations"][0]["planned_conditions"] == ["z-finalizer"]


def test_comparison_followup_uses_declared_history_for_record_identity(tmp_path):
    from repomentor.question_bank import build_bank, digest

    history = [
        {"role": "user", "content": "Earlier condition?"},
        {"role": "assistant", "content": "Please describe the change."},
    ]
    task = {
        "task_id": "followup",
        "repository": "owner/repo",
        "snapshot_id": "snap",
        "commit_sha": "commit",
        "split": "confirmation",
        "input": {"question": "Now what?", "history": history},
    }
    folder = tmp_path / "experiments/new-confirmation"
    folder.mkdir(parents=True)
    (folder / "dataset.json").write_text(
        json.dumps(
            {
                "schema": "repomentor.benchmark.v1",
                "tasks": [task],
                "retrieval_checks": [],
                "cases": [{"task_id": "followup", "condition": "agentic"}],
            }
        ),
        encoding="utf-8",
    )
    rows = [
        {
            "task_id": "followup",
            "case_id": f"answer-{i}",
            "condition": "agentic",
            "result": {
                "answer": {"answer": "saved"},
                "input_messages": [*history, {"role": "user", "content": "Now what?"}],
            },
        }
        for i in range(2)
    ]
    (folder / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (folder / "summary.json").write_text(
        json.dumps({"reviews": {row["case_id"]: {} for row in rows}}), encoding="utf-8"
    )
    bank = build_bank(tmp_path)
    assert bank["summary"]["all"]["unique_questions"] == 1
    assert bank["unresolved_observations"] == []
    question = bank["questions"][0]
    assert question["context"] == {"history_sha256": digest(history)}
    assert len(question["observations"]) == 2
    assert all(row["review"] for row in question["observations"])
