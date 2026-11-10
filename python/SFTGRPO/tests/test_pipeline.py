"""Protocol and framework integration checks; no weights, GPU, API, or target code execution."""
import copy
import json
import runpy
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from sftgrpo.core import ROOT, normalize, read_rows  # noqa: E402
from sftgrpo.runtime import UTILS, JudgeReward, RepoEnvironment  # noqa: E402
from sftgrpo.train import configs, sft_dataset, tokenizer  # noqa: E402


@pytest.fixture(scope="module")
def tok():
    return tokenizer()


def test_repository_alias_and_repeated_call_notice_preserve_source(tmp_path):
    (tmp_path / "xcube").mkdir()
    (tmp_path / "xcube/plugin.py").write_text("registry = 42\n", encoding="utf-8")

    executions = []

    def runner(snapshot, name, arguments):
        executions.append(dict(arguments))
        return (snapshot / arguments["path"].removeprefix("/repo/")).read_text(encoding="utf-8")

    env = RepoEnvironment(runner=runner)
    env.snapshot, env.question, env.tool_turn_limit = tmp_path, "Explain the registry.", 32
    first = json.loads(env.view_codebase("repo/xcube/plugin.py"))
    second = json.loads(env.view_codebase("/repo/xcube/plugin.py"))
    assert first["view_content"] == "registry = 42\n"
    assert "view_content" not in second and second["duplicate_of"] == 1
    assert len(executions) == 1 and not env.events[1]["executed"]
    assert env.events[0]["arguments"]["path"] == "/repo/xcube/plugin.py"
    assert env.events[1]["duplicate_of"] == 1
    assert "identical arguments" in second["runtime_notice"]
    assert env.events[1]["followup_message"]["content"] == second["runtime_notice"]
    for _ in range(18):
        final = json.loads(env.view_codebase("/repo/xcube/plugin.py"))
    assert "20 tool calls" in final["runtime_notice"] and "32 tool rounds" in final["runtime_notice"]
    assert "identical arguments" in final["runtime_notice"]
    assert not final.get("stop_tools")  # Skip duplicate execution without forcing an unlearned early stop.
    assert len(executions) == 1  # Nineteen repeated requests never re-run the tool.
    assert "final answer turn" not in final["runtime_notice"]  # Calls need not equal TRL rounds.
    # A real nested repo directory retains normal relative-path semantics.
    (tmp_path / "repo/xcube").mkdir(parents=True)
    (tmp_path / "repo/xcube/plugin.py").write_text("nested = 7\n", encoding="utf-8")
    assert json.loads(env.view_codebase("repo/xcube/plugin.py"))["view_content"] == "nested = 7\n"
    with pytest.raises(ValueError, match="outside"):
        env.view_codebase("../../outside.py")


def test_shell_observation_uses_upstream_clipping_without_hiding_errors(tmp_path):
    env = RepoEnvironment(runner=lambda *args: "ERROR: " + "x" * 30000)
    env.snapshot, env.question = tmp_path, "Inspect the source."
    result = json.loads(env.execute_readonly_command("grep -r needle /repo"))
    assert result["command_output"].startswith("ERROR: ")
    assert "<response clipped>" in result["command_output"]
    assert len(result["command_output"]) < 6500
    assert env.events[-1]["raw_observation_characters"] == 30007
    assert not env.events[-1]["success"]


@pytest.mark.parametrize(("error", "kind", "hint"), [
    ("ERROR: Command contains a forbidden operator or command: exec", "upstream_command_guard", "quoted search terms"),
    ("ERROR: Command contains a forbidden operator or command: xargs", "upstream_command_guard", "xargs"),
    ("ERROR: Command execution failed\nSTDOUT:\n(no output)\nSTDERR:\n(no error message)", "empty_nonzero_exit", "no match"),
])
def test_upstream_command_failures_preserve_errors_and_offer_existing_tools(tmp_path, error, kind, hint):
    env = RepoEnvironment(runner=lambda *args: error)
    env.snapshot, env.question = tmp_path, "Explain execution."
    result = json.loads(env.execute_readonly_command('grep "execution" /repo/example.py'))
    assert result["command_output"] == error
    assert hint in result["runtime_notice"] and "semantic_search" in result["runtime_notice"]
    assert env.events[-1]["failure_kind"] == kind and not env.events[-1]["success"]


def test_transient_tool_exception_is_retried_not_memoized(tmp_path):
    calls = []

    def runner(snapshot, name, arguments):
        calls.append(dict(arguments))
        if len(calls) == 1:
            raise TimeoutError("temporary worker failure")
        return "needle = 42"

    env = RepoEnvironment(runner=runner)
    env.snapshot, env.question = tmp_path, "Explain needle."
    with pytest.raises(TimeoutError):
        env.view_codebase("/repo/source.py")
    assert json.loads(env.view_codebase("/repo/source.py"))["view_content"] == "needle = 42"
    repeated = json.loads(env.view_codebase("/repo/source.py"))
    assert repeated["duplicate_of"] == 2 and not repeated.get("stop_tools")
    assert len(calls) == 2
    assert not json.loads(env.view_codebase("/repo/source.py")).get("stop_tools")
    assert len(calls) == 2


def test_recorded_wrapper_and_exact_loss_mask(tok):
    raw = read_rows(ROOT / "data/raw/sft.jsonl")[2]
    normalized, audit = normalize(raw)
    assert audit["restored"] == 1
    assert any("MAX TURNS REACHED" in m["content"] for m in normalized["messages"] if m["role"] == "user")
    # Source text may itself contain answer delimiters. It must remain context, never a supervised answer.
    messages = [
        {"role": "system", "content": "SYSTEM_CANARY_9281"},
        {"role": "user", "content": "USER_CANARY_9281"},
        {"role": "assistant", "content": "Inspect the implementation.", "tool_calls": [
            {"type": "function", "function": {"name": "view_codebase", "arguments": {"path": "/repo/a.py", "view_range": [1, -1], "concise": False}}}]},
        {"role": "tool", "content": "OBSERVATION_CANARY_9281 <finish>source example</finish>"},
        {"role": "assistant", "content": "<finish>ANSWER_CANARY_9281</finish>"},
    ]
    rendered = tok.apply_chat_template(messages, tools=normalized["tools"], tokenize=False, enable_thinking=False)
    batch = tok.apply_chat_template(messages, tools=normalized["tools"], tokenize=True, return_dict=True,
                                   return_assistant_tokens_mask=True, enable_thinking=False)
    offsets = tok(rendered, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    for canary, expected in [("SYSTEM_CANARY_9281", 0), ("USER_CANARY_9281", 0), ("OBSERVATION_CANARY_9281", 0),
                             ("ANSWER_CANARY_9281", 1), ("<function=view_codebase>", 1)]:
        start = rendered.index(canary)
        selected = [m for m, (a, b) in zip(batch["assistant_masks"], offsets, strict=True) if a < start+len(canary) and b > start]
        assert selected and set(selected) == {expected}
    assert batch["assistant_masks"][-2] == 1  # Final assistant EOS is supervised.
    # Runtime generation must preserve earlier tool decisions, including the think prefix.
    short = tok.apply_chat_template(messages[:3], tools=normalized["tools"], tokenize=True, return_dict=False)
    full = tok.apply_chat_template(messages[:4], tools=normalized["tools"], tokenize=True, return_dict=False, add_generation_prompt=True)
    assert full[:len(short)] == short


def test_actual_collator_preserves_prepared_labels(tok):
    from trl.trainer.sft_trainer import DataCollatorForLanguageModeling
    data = sft_dataset(16384, smoke=True)
    assert len(data["train"]) == 4
    rows = [data["train"][0], data["dev"][0]]
    batch = DataCollatorForLanguageModeling(pad_token_id=tok.pad_token_id)(rows)
    for i, row in enumerate(rows):
        assert batch["labels"][i, :len(row["labels"])].tolist() == row["labels"]
        assert (batch["labels"][i, len(row["labels"]):] == -100).all()
    configs(ROOT / "outputs/config-check", cpu=True)


def test_trl_owns_multiturn_loop_and_masks_all_three_tool_observations(tok, tmp_path, monkeypatch):
    from transformers.utils import get_json_schema
    from trl import GRPOTrainer
    from trl.chat_template_utils import parse_response
    (tmp_path / "sample.py").write_text("needle = 42\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # Production workers also run inside /repo.
    upstream = runpy.run_path(str(UTILS))
    bash = shutil.which("bash") if sys.platform != "win32" else r"D:\Git\bin\bash.exe"

    def run_command(command, cwd=None):
        # Closed scripted fixture commands only. Production execution always uses Linux bubblewrap.
        return subprocess.run([bash, "-c", command], cwd=cwd, capture_output=True, text=True)

    upstream["execute_readonly_command"].__globals__["run_command"] = run_command

    def runner(snapshot, name, arguments):
        arguments = dict(arguments)
        if "path" in arguments:
            arguments["path"] = str(tmp_path / arguments["path"].removeprefix("/repo/"))
        if name == "execute_readonly_command":
            arguments["cwd"] = tmp_path
        return upstream[name](**arguments)

    env = RepoEnvironment(runner=runner)
    env.snapshot = tmp_path
    tools = [env.view_codebase, env.semantic_search, env.execute_readonly_command]
    # Exercise Qwen's typed parser: array, bool and integer arguments must survive native XML.
    prompt = [{"role": "user", "content": "Investigate sample.py and explain needle."}]
    actions = [
        {"role": "assistant", "content": "Read.", "tool_calls": [{"type": "function", "function": {"name": "view_codebase", "arguments": {"path": "/repo/sample.py", "view_range": [1, -1], "concise": False}}}]},
        {"role": "assistant", "content": "Search.", "tool_calls": [{"type": "function", "function": {"name": "semantic_search", "arguments": {"term": "needle", "path": "/repo/sample.py", "max_files": 2, "include_lines": True}}}]},
        {"role": "assistant", "content": "Cross-check.", "tool_calls": [{"type": "function", "function": {"name": "execute_readonly_command", "arguments": {"command": "grep -n needle sample.py"}}}]},
        {"role": "assistant", "content": "<finish>needle equals 42.</finish>"},
    ]

    def action_ids(action):
        whole = tok.apply_chat_template(prompt + [action], tools=tools, tokenize=True, return_dict=False)
        prefix = tok.apply_chat_template(prompt, tools=tools, tokenize=True, return_dict=False, add_generation_prompt=True, enable_thinking=False)
        assert whole[:len(prefix)] == prefix
        return whole[len(prefix):-1]  # Generated turn ends at EOS, before formatting newline.

    ids = [action_ids(action) for action in actions]
    prompts = [copy.deepcopy(prompt)]
    prompt_ids = [tok.apply_chat_template(prompt, tools=tools, tokenize=True, return_dict=False, add_generation_prompt=True, enable_thinking=False)]
    trainer = object.__new__(GRPOTrainer)
    trainer.processing_class = trainer._tokenizer = tok
    trainer.chat_template = None
    trainer.chat_template_kwargs = {"enable_thinking": False}
    trainer._is_vlm = trainer.use_vllm = False
    trainer.model = SimpleNamespace(config=SimpleNamespace(max_position_embeddings=32768))
    trainer.max_completion_length = 8192
    trainer.max_tool_calling_iterations = 25
    trainer._sync_tool_dicts = [{f.__name__: f for f in tools}]
    trainer._async_tool_dicts = [{}]
    trainer.environment_factories = None
    trainer.tools = tools
    next_ids = iter(ids[1:])
    trainer._generate_single_turn = lambda *a, **kw: ([next(next_ids)], None, None)
    parsed = parse_response(tok, ids[0], prefix=prompt_ids[0], tools=tools)
    assert parsed["tool_calls"][0]["function"]["arguments"]["view_range"] == [1, -1]
    assert parsed["tool_calls"][0]["function"]["arguments"]["concise"] is False
    result = trainer._tool_call_loop(prompts, prompt_ids, [ids[0]], [[parsed]], None, None, None, {})
    masks, completions, completion_ids, _, _, call_count, failed_count, _ = result
    assert call_count == 3 and failed_count == 0 and len(env.events) == 3
    assert all("42" in event["observation"] for event in env.events)
    assert [m["role"] for m in completions[0]] == ["assistant", "tool", "assistant", "tool", "assistant", "tool", "assistant"]
    assert len(masks[0]) == len(completion_ids[0])
    assert set(masks[0]) == {0, 1}
    learned_text = tok.decode([t for t, m in zip(completion_ids[0], masks[0], strict=True) if m])
    assert all(name in learned_text for name in ["view_codebase", "semantic_search", "execute_readonly_command"])
    assert "<finish>needle equals 42.</finish>" in learned_text
    assert "view_content" not in learned_text and "command_output" not in learned_text
    assert all(get_json_schema(tool)["function"]["name"] == tool.__name__ for tool in tools)


def test_judge_preserves_failures_and_does_not_score_tool_text(tmp_path):
    responses = iter([8, 6])

    def create(**kwargs):
        score = next(responses)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            {k: score for k in ["correctness", "completeness", "relevance", "clarity", "reasoning"]})))])

    reward = JudgeReward(tmp_path / "rewards.jsonl", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
                         model="offline-fixture", policy={"mode": "quality"})
    completions = [[{"role": "assistant", "content": "<finish>A</finish>"}],
                   [{"role": "tool", "content": "<finish>source example</finish>"}, {"role": "assistant", "content": "No finish"}],
                   [{"role": "assistant", "content": "<finish>B</finish>"}]]
    values = reward(completions=completions, task_id=["a", "a", "b"], question=["Q"]*3, reference=["R"]*3)
    assert values == pytest.approx([.8, 0.0, .6])
    rows = read_rows(tmp_path / "rewards.jsonl")
    assert rows[1]["missing_finish"] and not rows[1]["reward_imputed"] and rows[1]["scores"] is None


def test_reward_requires_terminal_answer_without_pending_tools(tmp_path):
    reward = JudgeReward(tmp_path / "rollouts.jsonl", collect_only=True)
    prior = {"role": "assistant", "content": "<finish>Earlier speculation.</finish>"}
    action = {"role": "assistant", "content": "<finish>Still inspecting.</finish>",
              "tool_calls": [{"type": "function", "function": {"name": "view_codebase", "arguments": {"path": "/repo"}}}]}
    cases = [[prior, {"role": "assistant", "content": "Truncated final answer"}],
             [prior, {"role": "tool", "content": "source"}], [action],
             [prior, {"role": "assistant", "content": "<finish>Final answer.</finish>"}]]
    reward(completions=cases, task_id=["one"]*4, question=["Q"]*4, reference=["R"]*4)
    rows = read_rows(tmp_path / "rollouts.jsonl")
    assert [r["answer"] for r in rows] == [None, None, None, "Final answer."]


def test_grpo_reference_adapter_is_frozen_sft_copy(tmp_path):
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import GPT2Config, GPT2LMHeadModel
    config = GPT2Config(n_layer=1, n_head=2, n_embd=16, vocab_size=32)
    model = get_peft_model(GPT2LMHeadModel(config), LoraConfig(r=2, target_modules=["c_attn"], task_type="CAUSAL_LM"))
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            if "lora_B" in name:
                parameter.fill_(.25)
    model.save_pretrained(tmp_path / "sft")
    policy = PeftModel.from_pretrained(GPT2LMHeadModel(config), tmp_path / "sft", is_trainable=True)
    from datasets import Dataset

    from sftgrpo.generation import CachedGRPOTrainer
    from sftgrpo.train import configs
    _, args = configs(tmp_path / "grpo", cpu=True)
    trainer = CachedGRPOTrainer(model=policy, args=args, processing_class=tokenizer(),
                                train_dataset=Dataset.from_list([{"prompt": [{"role": "user", "content": "Q"}]}]),
                                reward_funcs=lambda completions, **kwargs: [0.0]*len(completions))
    assert "ref" in trainer.model.peft_config
    parameters = dict(trainer.model.named_parameters())
    for name, parameter in parameters.items():
        if ".ref." in name:
            assert not parameter.requires_grad
            assert torch.equal(parameter, parameters[name.replace(".ref.", ".default.")])
    # PEFT saves the default adapter at the root and ref in a subdirectory.
    # Native Trainer's adapter_subdirs branch must not silently skip the policy.
    with torch.no_grad():
        for name, parameter in parameters.items():
            if ".default." in name:
                parameter.add_(.5)
    saved = {name: parameter.detach().clone() for name, parameter in parameters.items() if ".default." in name or ".ref." in name}
    trainer.save_model(str(tmp_path / "checkpoint"))
    with torch.no_grad():
        for name, parameter in parameters.items():
            if name in saved:
                parameter.zero_()
    trainer._load_from_checkpoint(str(tmp_path / "checkpoint"))
    for name, parameter in trainer.model.named_parameters():
        if name in saved:
            torch.testing.assert_close(parameter, saved[name], rtol=0, atol=0)
            assert parameter.requires_grad == (".default." in name)


def test_judge_outage_stops_training_and_preserves_raw_candidate(tmp_path):
    def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="invalid JSON"))])
    reward = JudgeReward(tmp_path / "rollouts.jsonl", model="offline-fixture",
                         client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    with pytest.raises(RuntimeError, match="No usable GRPO quality scores"):
        reward(completions=[[{"role": "assistant", "content": "<finish>A</finish>"}]],
               task_id=["a"], question=["Q"], reference=["R"])
    row = read_rows(tmp_path / "rollouts.jsonl")[0]
    assert row["answer"] == "A" and row["judge_failed"] and row["scores"] is None
    assert len(row["judge_requests"]) == 3


def test_collection_never_calls_judge_or_invents_zero_score(tmp_path):
    from sftgrpo.train import summarize_evaluation
    def forbidden(**kwargs):
        raise AssertionError("Evaluation must not call a paid API")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)))
    reward = JudgeReward(tmp_path / "rollouts.jsonl", client=client, collect_only=True)
    values = reward(completions=[[{"role": "assistant", "content": "<finish>answer</finish>"}]],
                    task_id=["one"], question=["Q"], reference=["R"])
    assert values == [0.0]  # Only the evaluate hook needs a numeric placeholder; no optimizer update.
    row = read_rows(tmp_path / "rollouts.jsonl")[0]
    assert row["reward"] is None and row["scores"] is None and row["review_pending"]
    assert not row["judge_failed"] and not row["reward_imputed"] and not row["judge_requests"]
    summarize_evaluation(tmp_path, [{"task_id": "one"}])
    summary = json.loads((tmp_path / "evaluation-summary.json").read_text())
    assert summary["five_dimension_mean_0_to_100"] is None and summary["review_pending"] == 1
    from sftgrpo.core import digest
    review = {"task_id": "one", "answer_sha256": digest(row["answer"]),
              "scores": {k: 8 for k in ["correctness", "completeness", "relevance", "clarity", "reasoning"]},
              "reviewer": "codex_session_assistant", "human_approved": False}
    (tmp_path / "reviews.jsonl").write_text(json.dumps(review)+'\n')
    summarize_evaluation(tmp_path, [{"task_id": "one"}])
    summary = json.loads((tmp_path / "evaluation-summary.json").read_text())
    assert summary["five_dimension_mean_0_to_100"] == 80 and not summary["review_pending"]
    assert read_rows(tmp_path / "rollouts.jsonl")[0]["scores"] is None  # Original generation stays unchanged.
    review["answer_sha256"] = "wrong answer"
    (tmp_path / "reviews.jsonl").write_text(json.dumps(review)+'\n')
    with pytest.raises(ValueError, match="exact completed answer"):
        summarize_evaluation(tmp_path, [{"task_id": "one"}])


def test_budget_persists_failed_reservations_and_stops_before_request(tmp_path):
    from sftgrpo.runtime import JudgeBudget
    ledger = tmp_path / "budget.json"
    first = JudgeBudget(ledger, limit=1.0)
    ticket = first.reserve([{"role": "user", "content": "test"}])
    first.settle(ticket, {"prompt_tokens": 100, "completion_tokens": 25}, "fixture")
    first.reserve([{"role": "user", "content": "transport failure keeps reservation"}])
    recorded = json.loads(ledger.read_text())
    assert recorded["accounted_upper_cny"] > .0004
    assert recorded["requests"][1]["accounted_upper_cny"] == recorded["requests"][1]["reserved_cny"]
    calls = []
    def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("No request allowed after budget exhausted")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)))
    reward = JudgeReward(tmp_path / "stopped.jsonl", client=client, model="fixture",
                         budget=JudgeBudget(ledger, limit=recorded["accounted_upper_cny"]))
    with pytest.raises(RuntimeError, match="stopped at judge budget"):
        reward(completions=[[{"role": "assistant", "content": "<finish>A</finish>"}]],
               task_id=["one"], question=["Q"], reference=["R"])
    assert not calls and len(read_rows(tmp_path / "stopped.jsonl")) == 1
