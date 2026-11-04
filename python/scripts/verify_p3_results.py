"""Offline audit and descriptive statistics of P3 artifacts; never calls a model."""

import json
import statistics
from collections import Counter
from pathlib import Path

import jsonschema

from repomentor.config import write_json
from repomentor.evaluation import ANSWER_SCHEMA, citation_check, load_tasks, task_messages
from repomentor.runtimes.chat import ChatSettings, CompletionResult, fingerprint

ROOT = Path(__file__).resolve().parents[1] / "experiments/p3"


def main():
    tasks = load_tasks(ROOT / "tasks.validation-oracle.jsonl")
    rows = [
        json.loads(line) for line in (ROOT / "qwen-q4-oracle.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["task_id"] for row in rows] == [task.task_id for task in tasks]
    for row, task in zip(rows, tasks, strict=True):
        profile = ChatSettings.model_validate(row["configuration"])
        result = CompletionResult.model_validate(row["result"])
        assert profile.config_id == row["config_id"]
        assert fingerprint(task.model_dump()) == row["task_hash"]
        assert row["input_messages"] == task_messages(task)
        assert row["commit_sha"] == task.commit_sha and row["split"] == "validation"
        assert row["answer_correctness"] == "not_scored"
        if result.status == "success":
            jsonschema.validate(json.loads(result.content), ANSWER_SCHEMA)
        assert citation_check(task, result) == row["citation_ranges_valid"]
    summary = json.loads((ROOT / "qwen-q4-oracle.summary.json").read_text(encoding="utf-8"))
    assert summary["statuses"] == dict(Counter(row["result"]["status"] for row in rows))
    resume = json.loads((ROOT / "cache-resume.summary.json").read_text(encoding="utf-8"))
    assert resume["config_id"] == summary["config_id"]
    assert resume["implementation_hash"] == summary["implementation_hash"]
    assert resume["new_requests"] == 0 and resume["cache_hits"] == len(tasks)
    probes = json.loads((ROOT / "local-benchmark.json").read_text(encoding="utf-8"))["probes"]
    assert len(probes) == 6
    for probe in probes:
        result = CompletionResult.model_validate(probe["result"])
        assert result.status == ("truncated" if probe["probe"] == "output_limit" else "success")
        assert not result.reasoning_observed
        if probe["tools"]:
            assert len(result.tool_calls) == 1
            call = result.tool_calls[0]
            assert call.call_id and call.tool == "read_file"
            assert call.arguments == {"path": "src/index.ts", "start_line": 1, "end_line": 20}
        assert probe["resident_after"]["models"][0]["context_length"] == 4096
    latencies = [row["result"]["elapsed_seconds"] for row in rows]
    rates = []
    for row in rows:
        result = row["result"]
        ttft, count = result["time_to_first_token_seconds"], result["usage"]["completion_tokens"]
        if ttft is not None and count and count > 1 and result["elapsed_seconds"] > ttft:
            rates.append((count - 1) / (result["elapsed_seconds"] - ttft))
    record = {
        "offline_verification": "passed",
        "new_model_calls_for_verification": 0,
        "tasks": len(tasks),
        "statuses": summary["statuses"],
        "citation_ranges_valid": sum(row["citation_ranges_valid"] is True for row in rows),
        "reasoning_observed": sum(row["result"]["reasoning_observed"] for row in rows),
        "prompt_tokens_range": [
            min(row["result"]["usage"]["prompt_tokens"] for row in rows),
            max(row["result"]["usage"]["prompt_tokens"] for row in rows),
        ],
        "completion_tokens_total": sum(row["result"]["usage"]["completion_tokens"] for row in rows),
        "latency_seconds_median": round(statistics.median(latencies), 3),
        "latency_seconds_total": round(sum(latencies), 3),
        "client_decode_tokens_per_second_median": round(statistics.median(rates), 2) if rates else None,
        "cache_resume_new_requests": resume["new_requests"],
        "answer_correctness": "not_scored",
        "human_approved": False,
        "notes": "Structural validation only. Citation ranges do not prove the cited line supports the answer. Oracle source spans and short-task selection preclude retrieval/overall accuracy claims. Rates are client approximations.",
    }
    write_json(ROOT / "verification.json", record)
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
