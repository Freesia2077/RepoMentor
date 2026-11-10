"""Production-plan regressions: target isolation, evidence gating and reward failure safety."""
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import (  # noqa: E402
    ROOT,
    data_path,
    load_json,
    read_rows,
    report_path,
    supervision_labels,
    trajectory_audit,
)
from rewards import parse_grounding, shape_reward, visible_evidence  # noqa: E402
from runtime import WEIGHTS, JudgeReward  # noqa: E402
from train import configs, sft_dataset  # noqa: E402


def policy(**kwargs):
    return {**load_json(ROOT / "run-config.json")["reward"], **kwargs}


def source_completion(answer="a.py:L2 returns the input"):
    return [{"role": "tool", "name": "view_codebase", "content": json.dumps({
        "view_content": "File view: /repo/a.py\n     1 # raise ValueError()\n     2 return data\n"})},
        {"role": "assistant", "content": f"<finish>{answer}</finish>"}]


def supported():
    return {"grounding": "supported", "critical_error": False, "reason": "The visible return supports the claim."}


def test_sft_conditions_preserve_identical_inputs_and_only_change_targets(tmp_path, monkeypatch):
    import train
    path = tmp_path / "tokens.gz"
    labels = [-100, 12, 13, -100, 21, 22, -100]
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for split in ["train", "dev"]:
            stream.write(json.dumps({"split": split, "input_ids": list(range(7)), "labels": labels}) + "\n")
    monkeypatch.setattr(train, "data_path", lambda name: path)
    all_data = sft_dataset(32768, supervision="all_assistant")
    last_data = sft_dataset(32768, supervision="last_assistant")
    for split in ["train", "dev"]:
        assert all_data[split][0]["input_ids"] == last_data[split][0]["input_ids"]
        assert all_data[split][0]["labels"] == labels
        assert last_data[split][0]["labels"] == [-100] * 4 + [21, 22, -100]
    with pytest.raises(ValueError, match="Missing assistant"):
        supervision_labels([-100, -100], "last_assistant")


def test_fixed_training_and_source_disjoint_128_task_plan(tmp_path):
    sft, grpo = configs(tmp_path, cpu=True)
    assert sft.num_train_epochs == 2 and sft.gradient_accumulation_steps == 8
    assert sft.get_warmup_steps(242) == 13
    assert grpo.num_generations == grpo.gradient_accumulation_steps == 4
    assert grpo.loss_type == "dapo" and grpo.importance_sampling_level == "token"
    assert grpo.num_iterations == 1
    train, dev, final = [read_rows(data_path(f"{name}.jsonl")) for name in ["rl-train", "rl-dev", "eval"]]
    assert (len(train), len(dev), len(final)) == (128, 8, 30)
    assert len({row["repo"] for row in train}) == 128
    assert not {row["basename"] for row in train} & {row["basename"] for row in dev + final}
    report = load_json(report_path("preparation.json"))
    assert report["counts"]["sft_train"] == 963 and report["counts"]["sft_dev"] == 32
    assert report["counts"]["train_last_assistant_tokens"] < report["counts"]["train_all_assistant_tokens"]


def test_teacher_behavior_flags_retain_recovery_and_overlap():
    call = {"function": {"name": "semantic_search", "arguments": {"term": "needle"}}}
    messages = [{"role": "assistant", "content": "search", "tool_calls": [call]},
                {"role": "tool", "content": "ERROR: no match"},
                {"role": "assistant", "content": "retry", "tool_calls": [call]},
                {"role": "user", "content": "MAX TURNS REACHED"}]
    result = trajectory_audit(messages)
    assert result["duplicate_requests"] == 1 and result["external_finalization"]
    assert result["error_observations"] == 1 and result["tool_rounds"] == 2


def test_citation_identity_does_not_prove_semantic_support():
    completion = source_completion("a.py:L1 raises ValueError")
    evidence = visible_evidence(completion, "a.py:L1 raises ValueError", 24000)
    assert evidence["citations"][0]["visible"]  # The line exists but is a comment.
    result = shape_reward(dict.fromkeys(WEIGHTS, 9),
                          {"grounding": "contradicted", "critical_error": True}, evidence, [], policy(), WEIGHTS)
    assert result["reward"] == .2 and not result["quality_eligible"]


@pytest.mark.parametrize("citation", ["a.py:L3", "a.py:L1-L3", "missing.py:L2", "../a.py:L2"])
def test_unseen_lines_and_paths_cannot_be_used_as_visible_citations(citation):
    evidence = visible_evidence(source_completion(citation), citation, 24000)
    assert evidence["citations"] and not evidence["citations"][0]["visible"]


def test_clipped_tool_json_does_not_restore_unseen_tail_or_partial_line():
    clipped = '{"view_content": "File view: /repo/a.py\\n     1 return data\\n     2 partial'
    completion = [{"role": "tool", "content": clipped + "\n[Tool output truncated]", "context_truncated": True}]
    evidence = visible_evidence(completion, "a.py:L1 a.py:L2", 24000)
    assert [c["visible"] for c in evidence["citations"]] == [True, False]


def test_cost_penalty_is_bounded_and_requires_quality_and_evidence():
    evidence = visible_evidence(source_completion(), "a.py:L2", 24000)
    events = [{"duplicate_of": 1}] * 50
    strong = shape_reward(dict.fromkeys(WEIGHTS, 8), supported(), evidence, events, policy(), WEIGHTS)
    assert strong["reward"] == pytest.approx(.75) and strong["redundancy_penalty"] == .05
    weak_scores = {**dict.fromkeys(WEIGHTS, 9), "correctness": 2}
    weak = shape_reward(weak_scores, supported(), evidence, events, policy(), WEIGHTS)
    assert weak["reward"] == .2 and weak["redundancy_penalty"] == 0
    necessary = shape_reward(dict.fromkeys(WEIGHTS, 8), supported(), evidence, [{}] * 50, policy(), WEIGHTS)
    assert necessary["reward"] == pytest.approx(.8)  # No reward for merely reducing necessary reads.
    no_source = visible_evidence([], "", 24000)
    premature = shape_reward(dict.fromkeys(WEIGHTS, 9), supported(), no_source, [], policy(), WEIGHTS)
    assert premature["reward"] == .2 and "no_visible_evidence" in premature["gate_reasons"]


def test_grounded_judge_sees_delivered_source_not_raw_environment_tail(tmp_path):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        content = {**dict.fromkeys(WEIGHTS, 8), **supported()}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reward = JudgeReward(tmp_path / "rollouts.jsonl", client=client, model="fixture")
    env = SimpleNamespace(events=[{"observation": "HIDDEN_RAW_TAIL", "duplicate_of": 1}])
    values = reward([source_completion()], task_id=["one"], question=["Explain return"], reference=["returns data"], environments=[env])
    text = json.dumps(requests, ensure_ascii=False)
    assert "return data" in text and "HIDDEN_RAW_TAIL" not in text
    assert "critical_error" in text and "VISIBLE_SOURCE_DATA" in text
    assert values == pytest.approx([.79])
    record = read_rows(tmp_path / "rollouts.jsonl")[0]
    assert record["grounding"]["grounding"] == "supported" and record["reward_audit"]["duplicate_requests"] == 1


def test_grounded_group_never_optimizes_imputed_semantic_scores(tmp_path):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        content = {**dict.fromkeys(WEIGHTS, 8), **supported()} if len(calls) == 1 else {"grounding": "missing_scores"}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reward = JudgeReward(tmp_path / "rollouts.jsonl", client=client, model="fixture")
    with pytest.raises(RuntimeError, match="complete grounded group"):
        reward([source_completion(), source_completion()], task_id=["one"] * 2, question=["Q"] * 2, reference=["R"] * 2)
    records = read_rows(tmp_path / "rollouts.jsonl")
    assert records[1]["judge_failed"] and records[1]["reward"] is None and not records[1]["reward_imputed"]
    assert records[0]["reward"] is not None  # Retained diagnostics; the group raises before any update.
    assert not any(row["group_update_eligible"] for row in records)


@pytest.mark.parametrize("content", ['{}', '{"grounding":"supported","critical_error":0,"reason":"x"}'])
def test_judge_grounding_fields_require_explicit_valid_decisions(content):
    with pytest.raises(ValueError):
        parse_grounding(content)
