import importlib
import json
from pathlib import Path

import pytest

from repomentor.rag_evaluation import coverage


@pytest.mark.parametrize("to_file", [False, True])
async def test_inspect_saved_benchmark_case_without_extracting_files(tmp_path, capsys, to_file):
    from repomentor.cli import async_main, parser
    from repomentor.config import ConfigurationError

    path = tmp_path / "results.jsonl"
    span = {"path": "retry.py", "start_line": 460, "end_line": 466,
            "content": "def post_check():\n    if not should_retry:\n        return result\n\n    if after:\n        after()\n    wait()"}
    hidden = {"path": "retry.py", "start_line": 1, "end_line": 1, "content": "unrelated_source"}
    rows = [{"case_id": name, "result": {"question": "保存的问题", "answer": {"answer": text, "citations": [
             {"path": "retry.py", "start_line": 465, "end_line": 466}] * 2},
             "context": [hidden], "final_context": [span, span, hidden],
             "completion": {"status": "success"}}} for name, text in (("other", "未选答案"), ("chosen", "目标答案"))]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    original = path.read_bytes()
    args = ["inspect-run", str(path), "--case-id", "chosen"]
    output = tmp_path / "answer.md"
    assert await async_main(parser().parse_args(args + (["--output", str(output)] if to_file else []))) == 0
    rendered = output.read_text(encoding="utf-8") if to_file else capsys.readouterr().out
    assert "目标答案" in rendered and "未选答案" not in rendered
    assert "模型引用：retry.py:465-466" in rendered
    assert "461:     if not should_retry:" in rendered and "462:         return result" in rendered
    assert rendered.count("465:         after()") == 1 and "unrelated_source" not in rendered
    assert path.read_bytes() == original
    with pytest.raises(ConfigurationError, match="--case-id"):
        await async_main(parser().parse_args(["inspect-run", str(path)]))


@pytest.mark.parametrize("status,answer,final", [
    ("truncated", None, None), ("context_limit", None, None), ("cancelled", None, []),
    ("success", {"answer": "证据不足，需要补读。", "citations": [], "insufficient_evidence": True}, None),
])
async def test_inspect_uncited_run_keeps_saved_sources_for_followup(tmp_path, capsys, status, answer, final):
    from repomentor.cli import async_main, parser

    path = tmp_path / "run.json"
    source = {"path": "source.py", "start_line": 10, "end_line": 11,
              "content": "if disabled:\n    return []"}
    row = {"answer": answer, "completion": {"status": status, "request_sent": status != "context_limit"},
           "context": [source, source]}
    if final is not None:
        row["final_context"] = final
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    original = path.read_bytes()
    assert await async_main(parser().parse_args(["inspect-run", str(path)])) == 0
    rendered = capsys.readouterr().out
    assert "本轮保存源码（模型未引用，供继续核对）" in rendered
    assert rendered.count("10: if disabled:") == 1
    assert "11:     return []" in rendered
    assert "模型引用：" not in rendered and "已交付上下文" not in rendered
    assert ("本次未发布有效答案" in rendered) == (answer is None)
    assert f"生成状态：{status}" in rendered and path.read_bytes() == original


def test_infeasible_file_is_not_counted_as_source_delivered_to_model(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    delivery = importlib.import_module("rag_interview_delivery")
    reference = [{"path": "main.py", "start_line": 10, "end_line": 20}]
    case = {"result": {"context": reference, "completion": {"status": "context_limit", "request_sent": False}}}
    assert coverage(reference, case["result"]["context"]) == 1
    assert coverage(reference, delivery.submitted_sources(case)) == 0
    assert case["result"]["context"] == reference
    case["result"]["completion"].update(status="success", request_sent=True)
    assert coverage(reference, delivery.submitted_sources(case)) == 1


async def test_unified_run_refuses_to_retry_an_ambiguous_dispatch(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    runner = importlib.import_module("rag_unified")
    monkeypatch.setattr(runner, "PRIVATE", tmp_path)
    monkeypatch.setattr(runner, "check", lambda: {})
    monkeypatch.setattr(runner, "results", lambda: [])

    class ReservedLedger:
        def rows(self):
            return [{"case": "question--lexical"}]

    monkeypatch.setattr(runner, "RequestLedger", lambda *a, **kw: ReservedLedger())
    monkeypatch.setattr(runner, "settings", lambda: pytest.fail("retry reached model setup"))
    with pytest.raises(ValueError, match="ambiguous"):
        await runner.run()


def test_unified_summary_keeps_all_cohorts_and_does_not_treat_success_as_correct(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    runner = importlib.import_module("rag_unified")
    tasks = [{"task_id": "a", "cohort": "older-development"}, {"task_id": "b", "cohort": "added-retrieval-development"}]
    data = {"tasks": tasks, "evaluation_only": {"a": {"answerable": True}, "b": {"answerable": False}},
            "protocol": {"scope": "selected repeated diagnostic"}}
    rows = [{"case_id": t["task_id"] + "--" + c, "task_id": t["task_id"], "condition": c,
             "completion": {"status": "success"}} for t in tasks for c in ("lexical", "code_rag")]
    monkeypatch.setattr(runner, "OUT", tmp_path)
    monkeypatch.setattr(runner, "check", lambda: data)
    monkeypatch.setattr(runner, "results", lambda: rows)
    monkeypatch.setattr(runner, "count_ledger", lambda path: 4)
    runner.summary()
    value = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert len(value["reviews"]) == 0 and not value["human_approved"]
    assert value["groups"]["all/lexical"] == {"records": 2, "success": 2}
    assert value["groups"]["answerable/code_rag"]["records"] == 1
    assert value["groups"]["unanswerable/code_rag"]["records"] == 1
    assert value["groups"]["added-retrieval-development/lexical"]["records"] == 1
