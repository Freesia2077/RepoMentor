"""Offline source, budget, split, replay and evaluation audit. Does not score semantic accuracy."""

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import jsonschema
from run_p5_retrieval import protocol, write

from repomentor.evaluation import ANSWER_SCHEMA, atomic_write
from repomentor.paths import data_root
from repomentor.rag import citation_validity, messages_for, verify_spans
from repomentor.rag_evaluation import retrieval_metrics
from repomentor.repository import SnapshotStore
from repomentor.runtimes.chat import ChatSettings, CompletionResult
from repomentor.token_budget import input_budget

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/p5"


def jsonl(name):
    return [json.loads(line) for line in (EXP / name).read_text(encoding="utf-8").splitlines()]


def main():
    task_rows, frozen = protocol()  # Also checks every protected P2/P4 file hash.
    tasks = {t["task_id"]: t for t in task_rows}
    matrix, generated, history = jsonl("retrieval.jsonl"), jsonl("generation.jsonl"), jsonl("history.jsonl")
    assert len(matrix) == 396 and len(generated) == 72 and len(history) == 36
    assert {(r["task_id"], r["variant"]) for r in matrix} == {
        (t, v) for t in tasks for v in frozen["variants"]
    }
    assert {(r["task_id"], r["mode"]) for r in generated} == {
        (t, v) for t in frozen["generation_task_ids"] for v in frozen["generation_variants"]
    }
    corpus = json.loads((ROOT / "experiments/p0/corpus.json").read_text(encoding="utf-8"))
    splits = {r["snapshot_id"]: r["split"] for r in corpus["repositories"]}
    snapshots, readers, checked = SnapshotStore(data_root() / "corpus"), {}, set()

    def verify_context(sid, context):
        assert splits[sid] == "validation"
        if sid not in readers:
            readers[sid] = snapshots.open(sid)
        for span in context:
            key = (sid, span["path"], span["start_line"], span["end_line"], span["content_sha256"])
            if key not in checked:
                verify_spans(readers[sid], [span])
                checked.add(key)

    for row in matrix:
        task = tasks[row["task_id"]]
        assert row["split"] == task["split"] == "validation"
        verify_context(row["snapshot_id"], row["context"])
        assert row["commit_sha"] == readers[row["snapshot_id"]].snapshot.commit_sha == task["commit_sha"]
        assert row["metrics"] == retrieval_metrics(task["gold"]["evidence"], row)
        assert row["input_messages"] == messages_for(row["question"], [], row["context"])
        assert row["budget"]["fits"]

    lookup = {(r["task_id"], r["variant"]): r for r in matrix}

    def verify_result(result):
        verify_context(result["snapshot_id"], result["context"])
        profile = ChatSettings.model_validate(result["configuration"])
        assert profile.backend == "ollama" and urlparse(profile.baseUrl).hostname in (
            "localhost",
            "127.0.0.1",
            "::1",
        )
        assert profile.context_window == 8192
        assert result["config_id"] == profile.config_id
        completion = CompletionResult.model_validate(result["completion"])
        budget = input_budget(profile, result["input_messages"], response_schema=ANSWER_SCHEMA)
        assert budget["fits"]
        assert result["citation_ranges_valid"] == citation_validity(result["answer"], result["context"])
        if completion.status == "success":
            jsonschema.validate(result["answer"], ANSWER_SCHEMA)
            assert result["answer"] == json.loads(completion.content)
            assert budget == completion.input_budget
            assert completion.usage["prompt_tokens"] == budget["input_tokens"]

    for row in generated:
        record, result = lookup[(row["task_id"], row["mode"])], row["result"]
        assert row["metrics"] == record["metrics"]
        assert result["context"] == record["context"] and result["input_messages"] == record["input_messages"]
        verify_result(result)

    trajectories, history_summary = defaultdict(list), {}
    history_protocol = json.loads((EXP / "history-protocol.json").read_text(encoding="utf-8"))
    scenarios = {s["repository"]: s for s in history_protocol["scenarios"]}
    assert {(r["repository"], r["condition"], r["turn"]) for r in history} == {
        (repo, "/".join(condition), turn)
        for repo in scenarios
        for condition in history_protocol["conditions"]
        for turn in range(1, 7)
    }
    for row in history:
        result = row["result"]
        scenario = scenarios[row["repository"]]
        assert result["snapshot_id"] == scenario["snapshot_id"]
        assert row["question"] == scenario["questions"][row["turn"] - 1]
        assert row["gold_reference"] == [
            {"path": scenario["path"], "start_line": a, "end_line": b}
            for a, b in scenario["gold_ranges"][row["turn"] - 1]
        ]
        verify_result(result)
        key = row["repository"], row["condition"]
        previous = trajectories[key]
        actual_history = result["input_messages"][1:-1]
        expected_history = [
            message
            for r in previous
            for message in [
                {"role": "user", "content": r["question"]},
                {"role": "assistant", "content": r["result"]["answer"]["answer"]},
            ]
        ]
        assert len(actual_history) <= 8 and (
            not actual_history or actual_history == expected_history[-len(actual_history) :]
        )
        assert result["input_messages"] == messages_for(
            row["question"], actual_history, result["context"], memory=result["navigation_memory"]
        )
        assert row["metrics"] == retrieval_metrics(row["gold_reference"], result)
        previous.append(row)
    for (repo, condition), group in trajectories.items():
        history_summary[f"{repo}/{condition}"] = {
            "turns": len(group),
            "statuses": dict(Counter(r["result"]["completion"]["status"] for r in group)),
            "coverage_by_turn": [r["metrics"]["gold_span_selected_coverage"] for r in group],
            "final_retrieval_query": group[-1]["retrieval_query"],
            "final_memory": group[-1]["result"]["navigation_memory"],
            "new_model_requests": sum(r["new_model_requests"] for r in group),
        }
    write(EXP / "history.summary.json", history_summary)

    packet = []
    for row in generated:
        task, result = tasks[row["task_id"]], row["result"]
        packet.append(
            {
                "task_id": row["task_id"],
                "condition": row["mode"],
                "attempt_id": result["attempt_id"],
                "question": task["input"]["question"],
                "gold": task["gold"],
                "answer": result["answer"],
                "context": result["context"],
                "human_approved": False,
                "ratings": {
                    key: None
                    for key in (
                        "factual_correctness",
                        "citation_entailment",
                        "completeness",
                        "abstention",
                        "learning_value",
                    )
                },
            }
        )
    # Do not overwrite a packet once a reviewer has entered ratings.
    packet_path = EXP / "review-packet.jsonl"
    packet_text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in packet)
    if packet_path.exists():
        saved_packet = jsonl("review-packet.jsonl")

        def immutable_review_fields(row):
            return {
                k: v
                for k, v in row.items()
                if k not in ("ratings", "human_approved", "reviewer", "review_notes")
            }

        if [immutable_review_fields(r) for r in saved_packet] != [immutable_review_fields(r) for r in packet]:
            raise ValueError("review packet source changed; preserve reviewer work")
    if not packet_path.exists():
        atomic_write(packet_path, packet_text)

    summary = {
        "retrieval_rows": len(matrix),
        "generation_rows": len(generated),
        "history_turns": len(history),
        "source_spans_verified": len(checked),
        "protected_files_unchanged": len(frozen["protected_files"]),
        "statuses": dict(Counter(r["result"]["completion"]["status"] for r in generated)),
        "citation_ranges_valid": sum(r["result"]["citation_ranges_valid"] is True for r in generated),
        "generation_new_requests": sum(r["result"]["new_model_requests"] for r in generated),
        "history_new_requests": sum(r["new_model_requests"] for r in history),
        "paid_api_calls": 0,
        "human_approved": False,
        "semantic_accuracy": None,
        "notes": "Only deterministic source/protocol checks. Retrieval coverage and legal citation ranges do not prove answers correct. Review packet is pending human review.",
    }
    by_variant = {}
    for variant in frozen["generation_variants"]:
        group = [r for r in generated if r["mode"] == variant]
        unavailable = [r for r in group if not tasks[r["task_id"]]["gold"]["evidence"]]
        answerable = [r for r in group if tasks[r["task_id"]]["gold"]["evidence"]]
        by_variant[variant] = {
            "unavailable_abstentions": sum(
                bool(r["result"]["answer"] and r["result"]["answer"]["insufficient_evidence"])
                for r in unavailable
            ),
            "unavailable_tasks": len(unavailable),
            "answerable_abstentions": sum(
                bool(r["result"]["answer"] and r["result"]["answer"]["insufficient_evidence"])
                for r in answerable
            ),
            "answerable_tasks": len(answerable),
            "median_prompt_tokens": statistics.median(
                r["result"]["completion"]["input_budget"]["input_tokens"] for r in group
            ),
            "prompt_tokens": sum(r["result"]["completion"]["usage"].get("prompt_tokens", 0) for r in group),
            "completion_tokens": sum(
                r["result"]["completion"]["usage"].get("completion_tokens", 0) for r in group
            ),
        }
    summary["generation_dimensions"] = by_variant
    # Compare saved attempts and actual inputs, rather than trusting a summary's claimed cache-hit rate.
    retrieval_replay = jsonl("retrieval-resume.jsonl")
    assert len(retrieval_replay) == len(matrix)
    search_hits = 0
    for old, new in zip(matrix, retrieval_replay, strict=True):
        assert (old["task_id"], old["variant"]) == (new["task_id"], new["variant"])
        assert old["metrics"] == new["metrics"] and old["context"] == new["context"]
        assert old["input_messages"] == new["input_messages"]
        assert [r["chunk"] for r in old["retrieval"]["candidates"]] == [
            r["chunk"] for r in new["retrieval"]["candidates"]
        ]
        if old["variant"] != "line_bm25":
            assert new["retrieval"]["cache_hit"]
            assert all(n == 0 for n in new["retrieval"]["neural_work"].values())
            search_hits += 1
    resources = json.loads((EXP / "retrieval-resume.resources.json").read_text(encoding="utf-8"))
    assert resources["neural_work"]["embedding_new"] == resources["neural_work"]["rerank_new"] == 0
    generation_replay, history_replay = jsonl("generation-resume.jsonl"), jsonl("history-resume.jsonl")
    for original, replay, keys in (
        (generated, generation_replay, ("task_id", "mode")),
        (history, history_replay, ("repository", "condition", "turn")),
    ):
        assert len(original) == len(replay)
        for old, new in zip(original, replay, strict=True):
            assert all(old[key] == new[key] for key in keys)
            assert old["result"]["attempt_id"] == new["result"]["attempt_id"]
            assert old["result"]["input_messages"] == new["result"]["input_messages"]
            assert old["result"]["completion"] == new["result"]["completion"]
            assert new.get("new_model_requests", new["result"]["new_model_requests"]) == 0
    assert all(r["result"]["cache_hit"] for r in generation_replay)
    assert all(r["replayed"] for r in history_replay)
    summary["cache_replay"] = {
        "search_cache_hits": search_hits,
        "lexical_recomputations": 66,
        "generation_hits": len(generation_replay),
        "conversation_replays": len(history_replay),
        "embedding_new": 0,
        "rerank_new": 0,
        "generation_new": 0,
    }

    cli_rows = [
        json.loads((EXP / name).read_text(encoding="utf-8"))
        for name in ("cli-chat-turn1.json", "cli-chat-turn2-recovered.json", "cli-chat-turn3.json")
    ]
    for row in cli_rows:
        verify_result(row["result"])
        assert row["result"]["retrieval"]["strategy"] == "hybrid-rerank"
        assert row["result"]["completion"]["status"] == "success"
    assert cli_rows[1]["retrieval_query"].endswith("追问对象：off")
    assert cli_rows[1]["result"]["input_messages"][1:3] == [
        {"role": "user", "content": cli_rows[0]["question"]},
        {"role": "assistant", "content": cli_rows[0]["result"]["answer"]["answer"]},
    ]
    cli_replay = json.loads((EXP / "cli-chat-replay.json").read_text(encoding="utf-8"))
    assert cli_replay["replayed"] and cli_replay["new_model_requests"] == 0
    assert cli_replay["result"]["attempt_id"] == cli_rows[-1]["result"]["attempt_id"]
    cli_failure = json.loads((EXP / "cli-chat-turn2.json").read_text(encoding="utf-8"))
    assert cli_failure["result"]["completion"]["status"] == "failed"
    assert cli_failure["result"]["completion"]["error_code"] == "provider_unavailable"
    assert cli_failure["request_id"] == cli_rows[1]["request_id"]
    assert cli_failure["result"]["attempt_id"] != cli_rows[1]["result"]["attempt_id"]
    assert cli_failure["result"]["input_messages"] == cli_rows[1]["result"]["input_messages"]
    summary["cli_smoke"] = {
        "turns": len(cli_rows),
        "statuses": [r["result"]["completion"]["status"] for r in cli_rows],
        "new_model_requests": sum(r["new_model_requests"] for r in cli_rows)
        + cli_failure["new_model_requests"],
        "preserved_failures": 1,
        "failure_reason": "Ollama CUDA shared object initialization failed; recovered by explicit resume; root cause not established",
        "replay_new_model_requests": 0,
    }
    summary["total_local_generation_requests"] = (
        summary["generation_new_requests"]
        + summary["history_new_requests"]
        + summary["cli_smoke"]["new_model_requests"]
    )
    summary["source_spans_verified"] = len(checked)
    write(EXP / "audit.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    write(
        EXP / "artifact-hashes.json",
        {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(EXP.iterdir())
            if p.is_file() and p.name != "artifact-hashes.json"
        },
    )


if __name__ == "__main__":
    main()
