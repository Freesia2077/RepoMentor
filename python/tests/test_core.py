import asyncio
import json
import subprocess

import pytest

from repomentor.contracts import EvidencePlan, ExplorerOutput
from repomentor.harness import CancellationToken, HarnessError, RepositoryHarness, build_evidence_bundle
from repomentor.report import run_report
from repomentor.repository import RepositoryError, SnapshotStore, is_test, normalize_path, safe_path
from repomentor.runtimes.stub import StubRuntime
from repomentor.skills import load_skill
from repomentor.storage import RunStore


def plan(*paths, actions=()):
    return EvidencePlan(
        goal="Inspect sources",
        rationale="test",
        questions=[],
        actions=list(actions),
        files=[{"path": p, "purpose": "verify", "priority": "high"} for p in paths],
        stopConditions=["done"],
    )


def ready(reader, token=None):
    h = RepositoryHarness(reader, token=token)
    h.get_repository_map()
    h.activate_policy("explorer", load_skill([], "explorer")[0])
    return h


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        "C:\\secret",
        "src/../README.md",
        "src/a:stream",
        "\\\\server\\file",
        ".git/config",
    ],
)
def test_reject_path_escapes(path):
    with pytest.raises(RepositoryError):
        safe_path(path)


def test_path_normalization():
    assert normalize_path("./src\\index.ts") == "src/index.ts"


def test_snapshot_survives_worktree_changes(reader, repo, tmp_path):
    original = reader.read("src/index.ts")
    (repo / "src/index.ts").write_text("changed after freeze")
    (repo / "secret.env").write_text("not tracked")
    assert reader.read("src/index.ts") == original
    reopened = SnapshotStore(tmp_path / "snapshots").open(reader.snapshot.snapshot_id)
    assert reopened.read("src/index.ts") == original
    with pytest.raises(RepositoryError, match="absent"):
        reader.read("secret.env")


def test_symlink_git_object_is_never_followed(repo, tmp_path):
    # Write a symlink object using Git, without needing Windows symlink privileges.
    proc = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"../../secret",
        capture_output=True,
        check=True,
    )
    oid = proc.stdout.decode().strip()
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--add", "--cacheinfo", f"120000,{oid},link"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=test",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-m",
            "symlink",
        ],
        capture_output=True,
        check=True,
    )
    reader = SnapshotStore(tmp_path / "symbolic").import_repository(str(repo))
    with pytest.raises(RepositoryError) as exc:
        reader.read("link")
    assert exc.value.code == "unreadable"


def test_utf8_and_budget_allocation(reader, monkeypatch):
    monkeypatch.setattr(reader, "read", lambda path, limit: ("汉" * 8000, True))
    paths = list(reader.entries)[:4]
    bundle = build_evidence_bundle(reader, plan(*paths), "explorer")
    assert bundle.totalBytes <= 48_000
    assert all("\ufffd" not in f.content for f in bundle.files)
    assert all(f.truncated for f in bundle.files)


def test_omissions_and_ledger_reuse(reader):
    h = ready(reader)
    p = plan("./src\\index.ts", "src/index.ts", "missing.ts")
    h.record_plan("explorer", p)
    bundle = h.read_batch("explorer", p)
    assert [o.reason for o in bundle.omissions] == ["duplicate_request", "not_tracked"]
    assert h.context()["ledger"]["src/index.ts"]["status"] == "available"
    h.complete_stage("explorer")
    h.activate_policy("mentor", load_skill([], "mentor")[0])
    bundle = h.execute_plan("mentor", plan("src/index.ts"))
    assert [o.reason for o in bundle.omissions] == ["already_available"]
    assert h.budget.filesRead == 1
    assert h.context()["ledger"]["src/index.ts"]["attempts"][-1]["status"] == "reused"


def test_discovery_permissions_dedup_and_budget(reader):
    h = ready(reader)
    action = {"tool": "search_symbols", "query": "helper", "purpose": "locate"}
    observed = h.discover("explorer", action)
    assert observed.paths
    with pytest.raises(HarnessError) as exc:
        h.discover("explorer", {**action, "purpose": "same action different purpose"})
    assert exc.value.code == "duplicate_action"
    with pytest.raises(HarnessError) as exc:
        h.discover("explorer", {"tool": "find_related_tests", "paths": ["../outside"], "purpose": "escape"})
    assert exc.value.code == "not_tracked"
    assert h.budget.filesRead == 0
    h.discover("explorer", {**action, "query": "runCore"})
    h.discover("explorer", {**action, "query": "nothing_matches"})
    with pytest.raises(HarnessError) as exc:
        h.discover("explorer", {**action, "query": "other"})
    assert exc.value.code == "action_budget"
    assert h.stages["explorer"].discoveryActionsUsed == 3


def test_completed_discovery_does_not_repeat_during_plan(reader):
    h = ready(reader)
    action = {"tool": "search_symbols", "query": "logMiddleware", "purpose": "locate"}
    h.discover("explorer", action)
    h.execute_plan("explorer", plan("src/index.ts", actions=[action]))
    assert h.stages["explorer"].discoveryActionsUsed == 1
    assert "src/utils/helper.ts" in h.context()["ledger"]


def test_phase_and_cancellation(reader):
    token = CancellationToken()
    h = ready(reader, token)
    with pytest.raises(HarnessError):
        h.complete_stage("explorer")
    with pytest.raises(HarnessError):
        h.record_plan("mentor", plan())
    token.cancel()
    with pytest.raises(HarnessError) as exc:
        h.execute_plan("explorer", plan("src/index.ts"))
    assert exc.value.code == "cancelled"
    assert h.budget.filesRead == 0


async def test_small_repository_pipeline_and_storage(reader, tmp_path):
    store = RunStore(tmp_path / "independent", "fixture-run")
    result = await run_report(reader, StubRuntime(), emit=store.emit, run_id="fixture-run")
    assert result.status == "success", result.error
    assert set(result.outputs) == {"explorer", "mentor", "contributor"}
    assert result.harness["budget"]["usedEvidenceBatches"] == 1
    assert result.harness["stages"]["mentor"]["stopReason"] == "existing_evidence_reused"
    store.save(result)
    assert store.load("fixture-run")["snapshot_id"] == reader.snapshot.snapshot_id
    events = [json.loads(line) for line in store.events_path.read_text(encoding="utf-8").splitlines()]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert all("secret source" not in json.dumps(e) for e in events)


async def test_illegal_citations_downgraded(reader):
    h = ready(reader)
    h.execute_plan("explorer", plan("src/index.ts"))
    output = await StubRuntime().synthesize("explorer", ExplorerOutput, {}, h)
    output.evidenceClaims[0].evidence[0].path = "src/not-read.ts"
    verified = h.verify_output("explorer", output)
    assert verified.evidenceClaims[0].confidence == "low"
    assert verified.evidenceClaims[0].evidence == []
    assert verified.evidenceCoverage.gaps[0].subject == "unsupported_claims"


async def test_local_report_verifies_only_context_actually_sent(reader):
    h = ready(reader)
    h.execute_plan("explorer", plan("src/index.ts"))
    output = await StubRuntime().synthesize("explorer", ExplorerOutput, {}, h)
    output.evidenceClaims[0].evidence[0].path = "src/index.ts"
    verified = h.verify_output("explorer", output, provided_paths=["README.md"])
    assert verified.evidenceCoverage.examinedFiles == ["README.md"]
    assert verified.evidenceClaims[0].evidence == []
    assert verified.evidenceClaims[0].confidence == "low"


async def test_total_timeout_persists_partial_result(reader):
    class Slow(StubRuntime):
        async def synthesize(self, *args, **kwargs):
            await asyncio.sleep(1)

    result = await run_report(reader, Slow(), total_timeout=0.01)
    assert result.status == "timeout"
    assert result.stop_reason == "total_timeout"


def test_data_exports_do_not_contain_credentials():
    from repomentor.config import ModelSettings

    settings = ModelSettings(apiKey="sensitive-example", baseUrl="https://example.invalid", model="model")
    assert "sensitive-example" not in repr(settings)
    assert "sensitive-example" not in json.dumps(settings.public())


def test_repeated_read_cannot_overrun_global_budget(reader):
    h = ready(reader)
    h.execute_plan("explorer", plan("src/index.ts"))
    h.complete_stage("explorer")
    h.activate_policy("mentor", load_skill([], "mentor")[0])
    h.budget.maxEvidenceBytes = h.budget.evidenceBytes
    with pytest.raises(HarnessError) as exc:
        h.execute_plan("mentor", plan("src/utils/helper.ts"))
    assert exc.value.code == "evidence_budget"
    assert h.budget.usedEvidenceBatches == 1
    assert "src/utils/helper.ts" not in h.ledger


def test_later_complete_evidence_replaces_partial_without_losing_history(reader, monkeypatch):
    h = ready(reader)
    original = reader.read
    monkeypatch.setattr(
        reader,
        "read",
        lambda path, limit: ("short", True) if path == "src/index.ts" else original(path, limit),
    )
    h.execute_plan("explorer", plan("src/index.ts"))
    assert h.context()["ledger"]["src/index.ts"]["status"] == "partial"
    h.complete_stage("explorer")
    h.activate_policy("mentor", load_skill([], "mentor")[0])
    monkeypatch.setattr(reader, "read", original)
    h.execute_plan("mentor", plan("src/index.ts"))
    assert h.context()["ledger"]["src/index.ts"]["status"] == "available"
    assert len(h.context()["ledger"]["src/index.ts"]["attempts"]) == 2


def test_notebook_strips_outputs(reader, monkeypatch):
    raw = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["print(1)"],
                    "outputs": [{"data": {"image/png": "huge secret output"}}],
                }
            ]
        }
    ).encode()
    monkeypatch.setattr(reader, "raw", lambda path: raw)
    text, truncated = reader.read("demo.ipynb", 1000)
    assert "print(1)" in text and "huge secret" not in text and not truncated


@pytest.mark.parametrize("path", ["test_schedule.py", "pkg/test_main.py", "pkg/main_test.py", "test.js"])
def test_root_and_python_test_file_discovery(path):
    assert is_test(path)
    assert not is_test("src/test_helpers.py.txt")
    assert not is_test("src/testing.py")


async def test_reusing_runtime_does_not_duplicate_usage(reader):
    class MeteredStub(StubRuntime):
        async def synthesize(self, stage, *args, **kwargs):
            self.usage.append({"stage": stage, "call": len(self.usage) + 1})
            return await super().synthesize(stage, *args, **kwargs)

    runtime = MeteredStub()
    first = await run_report(reader, runtime)
    second = await run_report(reader, runtime)
    assert first.status == second.status == "success"
    assert [u["call"] for u in first.usage] == [1, 2, 3]
    assert [u["call"] for u in second.usage] == [4, 5, 6]


async def test_export_does_not_create_phantom_runs(reader, tmp_path):
    from repomentor.cli import async_main, parser

    root = tmp_path / "existing"
    assert RunStore.load_saved(root, "unknown") is None
    assert not root.exists()
    store = RunStore(root, "existing-run")
    result = await run_report(reader, StubRuntime(), run_id="existing-run")
    store.save(result)
    before = set(root.rglob("*"))
    output = tmp_path / "export.json"
    args = parser().parse_args(["--data-dir", str(root), "export", "existing-run", "--output", str(output)])
    assert await async_main(args) == 0
    assert set(root.rglob("*")) == before
    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == "existing-run"


def test_store_reopen_continues_event_sequence_and_rejects_escape(tmp_path):
    root = tmp_path / "runs"
    with pytest.raises(ValueError, match="invalid run ID"):
        RunStore(root, "../outside")
    assert not root.exists()
    first = RunStore(root, "existing")
    first.emit("report", "started", {})
    reopened = RunStore(root, "existing")
    assert reopened.emit("report", "finished", {}).sequence == 2


def test_python_relative_and_src_layout_import_discovery(reader, monkeypatch):
    from repomentor.tools import trace_dependencies

    sources = {
        "src/pkg/main.py": "from . import helpers\nfrom .sub import utils\nfrom pkg import shared\n",
        "src/pkg/sub/utils.py": "from .. import helpers\n",
        "src/pkg/helpers.py": "",
        "src/pkg/shared.py": "",
    }
    monkeypatch.setattr(reader, "read", lambda path, limit: (sources[path], False))
    paths, _ = trace_dependencies(reader, list(sources), ["src/pkg/main.py", "src/pkg/sub/utils.py"])
    assert set(paths) == {"src/pkg/helpers.py", "src/pkg/sub/utils.py", "src/pkg/shared.py"}


def test_bundled_asset_provenance_survives_platform_line_endings():
    import hashlib

    from repomentor.skills import assets_path

    root = assets_path()
    records = json.loads(root.joinpath("provenance.json").read_text(encoding="utf-8"))
    for source, expected in records.items():
        path = source if source.startswith("agents/") else "plugin/" + source
        content = root.joinpath(path).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(content).hexdigest() == expected, source


@pytest.mark.parametrize(
    "status,error,reason,hint",
    [
        ("truncated", "output_limit", "length", "不要补齐 JSON"),
        ("context_limit", "context_window", None, "缩小相关源码范围"),
    ],
)
def test_failed_run_export_displays_failure_without_publishing_partial_draft(status, error, reason, hint):
    from repomentor.presentation import render_run

    record = {
        "result": {
            "question": "Question?",
            "answer": None,
            "completion": {
                "status": status,
                "error_code": error,
                "finish_reason": reason,
                "content": '{"answer":"unfinished',
            },
        }
    }
    original = json.dumps(record)
    rendered = render_run(record)
    assert error in rendered and hint in rendered and "未发布有效答案" in rendered
    assert "unfinished" not in rendered and json.dumps(record) == original
