"""Real tiny Qwen3.5 cache checks; no downloaded weights, GPU or external calls."""
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from sftgrpo import core  # noqa: E402,F401 - project-local dependency overlay
from sftgrpo.generation import CachedGRPOTrainer, RolloutCache  # noqa: E402
from sftgrpo.train import configs, tokenizer  # noqa: E402


@pytest.fixture(autouse=True)
def cpu_threads():
    import torch
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def tiny_qwen(vocab_size=64):
    import torch
    from transformers import Qwen3_5ForCausalLM, Qwen3_5TextConfig
    torch.manual_seed(42)
    config = Qwen3_5TextConfig(vocab_size=vocab_size, hidden_size=32, intermediate_size=64,
                              num_hidden_layers=4, num_attention_heads=2, num_key_value_heads=1,
                              head_dim=16, linear_num_key_heads=2, linear_num_value_heads=2,
                              linear_key_head_dim=16, linear_value_head_dim=16,
                              rope_parameters={"rope_type": "default", "rope_theta": 10000000,
                                               "partial_rotary_factor": 1.0, "mrope_section": [2, 2, 4],
                                               "mrope_interleaved": True}, pad_token_id=0, eos_token_id=None)
    model = Qwen3_5ForCausalLM(config).eval()
    model.config.use_cache = False  # Same persistent setting as the training entry point.
    return model


def test_budget_final_turn_blocks_new_tool_call_with_native_generation():
    import torch
    from transformers import GenerationConfig
    model = tiny_qwen()
    trainer = object.__new__(CachedGRPOTrainer)
    trainer.max_tool_calling_iterations = 1
    trainer.processing_class = SimpleNamespace(encode=lambda *args, **kwargs: [11, 12])
    original = GenerationConfig(max_new_tokens=2, do_sample=False, use_cache=True,
                                pad_token_id=0, eos_token_id=None, disable_compile=True,
                                sequence_bias={(11,): 10.0, (11, 12): 100.0})
    trainer.generation_config = original

    def native(*args):
        return model.generate(input_ids=torch.tensor([[3, 4, 5]]),
                              generation_config=trainer.generation_config)[0, 3:].tolist()

    trainer._generate_turn = native
    assert trainer._generate_single_turn([[3, 4, 5]], None, {}, 1) == [11, 12]
    # The policy strongly prefers the tool-call sequence; the final answer turn
    # must actually block it, rather than relying only on a text reminder.
    assert trainer._generate_single_turn([[3, 4, 5]], None, {}, 1) != [11, 12]
    assert trainer.generation_config is original and original.bad_words_ids is None


def test_hybrid_cache_matches_fresh_logits_and_processes_only_suffixes():
    import torch
    from transformers import GenerationConfig
    model = tiny_qwen()
    config = GenerationConfig(max_new_tokens=3, do_sample=False, use_cache=True,
                              pad_token_id=0, eos_token_id=None, disable_compile=True)
    trace = []

    def record(module, args, kwargs, output):
        trace.append((kwargs["input_ids"].shape[-1], output.logits[:, -1].detach().clone()))

    hook = model.register_forward_hook(record, with_kwargs=True)
    pool = RolloutCache()
    prompts = [[11, 12, 13, 14, 15], [21, 22, 23, 24, 25, 26, 27]]
    first = pool.generate(model, prompts, config, model.device)
    assert pool.entries[0][1] is not pool.entries[1][1]
    extended = [p + answer + [31, 32, 33] for p, answer in zip(prompts, first, strict=True)]
    trace.clear()
    cached = pool.generate(model, extended, config, model.device)
    cached_trace = trace[:]
    assert cached_trace[0][0] == cached_trace[3][0] == 4  # Unprocessed final token + 3 observation tokens.
    for i, prompt in enumerate(extended):
        trace.clear()
        fresh = model.generate(input_ids=torch.tensor([prompt]), generation_config=config)
        assert cached[i] == fresh[0, len(prompt):].tolist()
        for (_, actual), (_, expected) in zip(cached_trace[i*3:(i+1)*3], trace, strict=True):
            torch.testing.assert_close(actual, expected, rtol=2e-4, atol=2e-5)
    assert pool.stats["hits"] == 2
    # A changed history must be recomputed, never cropped into a stale DeltaNet state.
    changed = extended[0] + cached[0] + [34]
    changed[0] = 10
    trace.clear()
    pool.generate(model, [changed], config, model.device)
    assert trace[0][0] == len(changed)
    assert pool.stats["hits"] == 2
    hook.remove()


def test_real_trl_tool_loop_cache_parity_and_cleanup(tmp_path):
    import torch
    from datasets import Dataset
    from transformers import LogitsProcessor, LogitsProcessorList
    tok = tokenizer()
    prompt = [{"role": "user", "content": "Read sample.py, cross-check it, then answer inside finish tags."}]

    def view_codebase(path: str) -> str:
        """Read the source fixture.

        Args:
            path: File path inside the repository.
        """
        return json.dumps({"view_content": "OBS_CANARY needle = 42", "runtime_notice": "BUDGET_CANARY Conclude now."})

    tools = [view_codebase]
    actions = [
        {"role": "assistant", "content": "Read.", "tool_calls": [{"type": "function", "function": {"name": "view_codebase", "arguments": {"path": "/repo/sample.py"}}}]},
        {"role": "assistant", "content": "Cross-check.", "tool_calls": [{"type": "function", "function": {"name": "view_codebase", "arguments": {"path": "/repo/sample.py"}}}]},
        {"role": "assistant", "content": "<finish>needle equals 42.</finish>"},
    ]
    prefix = tok.apply_chat_template(prompt, tools=tools, tokenize=True, return_dict=False,
                                     add_generation_prompt=True, enable_thinking=False)
    scripts = [tok.apply_chat_template(prompt+[action], tools=tools, tokenize=True, return_dict=False)[len(prefix):-1]
               for action in actions]

    class ForceTokens(LogitsProcessor):
        def __init__(self, start, tokens):
            self.start, self.tokens = start, tokens

        def __call__(self, input_ids, scores):
            index = input_ids.shape[-1] - self.start
            scores.fill_(-torch.inf)
            scores[:, self.tokens[index]] = 0
            return scores

    outputs, observations = [], []
    for reuse in [False, True]:
        model = tiny_qwen(len(tok))
        _, args = configs(tmp_path / str(reuse), cpu=True)
        args.beta = 0  # This test covers generation; the SFT-reference adapter has a separate test.
        args.max_tool_calling_iterations = 2
        args.max_completion_length = 1024
        args.generation_kwargs.update(do_sample=False)
        trainer = CachedGRPOTrainer(model=model, args=args, processing_class=tok,
                                    train_dataset=Dataset.from_list([{"prompt": prompt}]), tools=tools,
                                    reward_funcs=lambda completions, **kwargs: [0.0]*len(completions),
                                    reuse_tool_cache=reuse)
        assert trainer.generation_config.use_cache is True
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        native_generate = model.generate
        calls = []

        def scripted_generate(**kwargs):
            ids = kwargs["input_ids"]
            turn = tok.decode(ids[0]).count("\n<tool_response>\n")
            calls.append({"reuse": kwargs.get("past_key_values") is not None,
                          "checkpointing": model.is_gradient_checkpointing,
                          "use_cache": kwargs["generation_config"].use_cache})
            return native_generate(**kwargs, logits_processor=LogitsProcessorList([ForceTokens(ids.shape[-1], scripts[turn])]))

        model.generate = scripted_generate
        trainer._sync_tool_dicts = [{"view_codebase": view_codebase} for _ in range(4)]
        trainer._async_tool_dicts = [{}, {}, {}, {}]
        result = trainer._generate(copy.deepcopy([prompt]*4))
        for ids, mask in zip(result[1], result[2], strict=True):
            masked_text = tok.decode([i for i, m in zip(ids, mask, strict=True) if not m])
            learned_text = tok.decode([i for i, m in zip(ids, mask, strict=True) if m])
            assert masked_text.count("BUDGET_CANARY") == 2  # Each control instruction is rendered once.
            assert "MAX TURNS REACHED" in masked_text
            assert "MAX TURNS REACHED" not in learned_text
            assert "BUDGET_CANARY" not in learned_text
        assert result[3][0][-2]["followup_message"]["content"].startswith("BUDGET_CANARY")
        assert trainer._rollout_cache is None
        assert model.is_gradient_checkpointing
        assert all(call["use_cache"] and not call["checkpointing"] for call in calls)
        outputs.append((result[1], result[2], result[3]))  # Token IDs, observation masks and parsed messages.
        observations.append(calls)
        if not reuse:
            # An OOM after sampling must reuse the exact native batch and RNG,
            # including tool/control masks, without executing tools or judging again.
            model.train()
            trainer.persist_rollout_batch = True
            core.save_json(Path(args.output_dir) / "run.json", {"identity": {"fixture": "native-tool-batch"}})
            batch_inputs = [{"prompt": copy.deepcopy(prompt)} for _ in range(4)]
            batch = trainer._generate_and_score_completions(copy.deepcopy(batch_inputs))
            expected_random = torch.rand(8)
            generated_calls = len(calls)
            torch.manual_seed(777)
            restored = trainer._generate_and_score_completions(copy.deepcopy(batch_inputs))
            assert len(calls) == generated_calls
            torch.testing.assert_close(torch.rand(8), expected_random, rtol=0, atol=0)
            assert restored.keys() == batch.keys()
            for name in batch:
                if isinstance(batch[name], torch.Tensor):
                    torch.testing.assert_close(restored[name], batch[name], rtol=0, atol=0)
                else:
                    assert restored[name] == batch[name]
            assert "tool_mask" in restored and torch.any(restored["tool_mask"] == 0)
            altered = copy.deepcopy(batch_inputs)
            altered[0]["prompt"][-1]["content"] = "Different task must not reuse this batch."
            with pytest.raises(ValueError, match="does not match"):
                trainer._generate_and_score_completions(altered)
            assert len(calls) == generated_calls
        if reuse:
            assert trainer.cache_totals["hits"] == 8
            assert trainer.cache_totals["reused_prefix_tokens"] > 0
            # A second rollout starts fresh even though its initial prompts and policy are identical.
            calls.clear()
            trainer._generate(copy.deepcopy([prompt]*4))
            assert not calls[0]["reuse"] and not calls[1]["reuse"]
    assert outputs[0] == outputs[1]
    assert not any(call["reuse"] for call in observations[0])


def test_exception_releases_cache_before_another_policy_step(monkeypatch):
    from trl import GRPOTrainer
    trainer = object.__new__(CachedGRPOTrainer)
    trainer.reuse_tool_cache = True
    trainer.cache_totals = Counter()
    trainer.model = SimpleNamespace(training=True)
    trainer._metrics = defaultdict(lambda: defaultdict(list))
    retained = []

    def failure(self, prompts):
        retained.append(self._rollout_cache)
        self._rollout_cache.stats.update(hits=1)
        raise RuntimeError("tool failure fixture")

    monkeypatch.setattr(GRPOTrainer, "_generate", failure)
    with pytest.raises(RuntimeError, match="tool failure fixture"):
        trainer._generate([])
    assert trainer._rollout_cache is None
    assert trainer.cache_totals["hits"] == 1
    with pytest.raises(RuntimeError):
        trainer._generate([])
    assert retained[0] is not retained[1]


def test_native_loop_reserves_answer_space_before_oversized_observation(tmp_path):
    """Reproduce the pilot's pending-call budget exit, using the actual SDK loop."""
    import torch
    from datasets import Dataset
    from transformers import LogitsProcessor, LogitsProcessorList
    tok = tokenizer()
    prompt = [{"role": "user", "content": "Read the source and answer in finish tags."}]
    executions = []

    def read_source(path: str) -> str:
        """Read a source file.

        Args:
            path: Path to inspect.
        """
        executions.append(path)
        return "OBSERVED_NEEDLE = 42\n" + "source padding\n"*2000 + "UNSEEN_TAIL_CANARY"

    action = {"role": "assistant", "content": "Read.", "tool_calls": [
        {"type": "function", "function": {"name": "read_source", "arguments": {"path": "/repo/source.py"}}}]}
    final = {"role": "assistant", "content": "<finish>OBSERVED_NEEDLE is 42.</finish>"}
    prefix = tok.apply_chat_template(prompt, tools=[read_source], tokenize=True, return_dict=False,
                                     add_generation_prompt=True, enable_thinking=False)
    scripts = [tok.apply_chat_template(prompt+[m], tools=[read_source], tokenize=True, return_dict=False)[len(prefix):-1]
               for m in [action, final]]
    model = tiny_qwen(len(tok))
    _, args = configs(tmp_path / "reserve", cpu=True)
    args.beta, args.max_completion_length, args.max_tool_calling_iterations = 0, 1024, 32
    trainer = CachedGRPOTrainer(model=model, args=args, processing_class=tok,
                                train_dataset=Dataset.from_list([{"prompt": prompt}]), tools=[read_source],
                                reward_funcs=lambda completions, **kwargs: [0.0]*len(completions),
                                reuse_tool_cache=True)
    native = model.generate

    class Script(LogitsProcessor):
        def __init__(self, start, ids):
            self.start, self.ids = start, ids

        def __call__(self, input_ids, scores):
            scores.fill_(-torch.inf)
            scores[:, self.ids[input_ids.shape[-1]-self.start]] = 0
            return scores

    final_flags = []

    def generated(**kwargs):
        is_final = bool(kwargs["generation_config"].bad_words_ids)
        final_flags.append(is_final)
        script = scripts[int(is_final)]
        return native(**kwargs, logits_processor=LogitsProcessorList([Script(kwargs["input_ids"].shape[-1], script)]))

    model.generate = generated
    trainer._sync_tool_dicts, trainer._async_tool_dicts = [{"read_source": read_source}], [{}]
    result = trainer._generate(copy.deepcopy([prompt]))
    ids, mask, messages = result[1][0], result[2][0], result[3][0]
    assert final_flags == [False, True] and executions == ["/repo/source.py"]
    assert len(ids) <= args.max_completion_length and messages[-1]["content"] == final["content"]
    assert messages[-2]["context_truncated"]
    assert "FINAL ANSWER TURN" in messages[-2]["followup_message"]["content"]
    external = tok.decode([i for i, m in zip(ids, mask, strict=True) if not m])
    learned = tok.decode([i for i, m in zip(ids, mask, strict=True) if m])
    assert "OBSERVED_NEEDLE" in external and "UNSEEN_TAIL_CANARY" not in external
    from sftgrpo.rewards import visible_evidence
    delivered = visible_evidence(messages, "", 24000)["text"]
    assert "OBSERVED_NEEDLE" in delivered and "UNSEEN_TAIL_CANARY" not in delivered
    assert "FINAL ANSWER TURN" not in learned and "source padding" not in learned
    assert trainer._tool_budget_context is None and trainer._rollout_cache is None


def test_different_candidates_get_independent_final_turn_budgets():
    from transformers import GenerationConfig
    trainer = object.__new__(CachedGRPOTrainer)
    trainer.max_tool_calling_iterations, trainer.max_completion_length = 32, 1024
    trainer.final_answer_reserve, trainer._rollout_turn = 256, 1
    trainer._initial_prompt_ids = [[1, 2], [3, 4]]
    trainer.model = SimpleNamespace(config=SimpleNamespace(get_text_config=lambda: SimpleNamespace(max_position_embeddings=4096)))
    trainer.processing_class = SimpleNamespace(encode=lambda *args, **kwargs: [11, 12])
    original = GenerationConfig(max_new_tokens=200, use_cache=True)
    trainer.generation_config = original
    seen = []

    def generated(ids, *args):
        seen.append((ids[0][0], bool(trainer.generation_config.bad_words_ids), trainer.generation_config.max_new_tokens))
        return [[p[0]] for p in ids], None, None

    trainer._generate_turn = generated
    result = trainer._generate_single_turn([[1, 2]+[9]*700, [3, 4]+[9]*10], None, {}, 1)
    assert result[0] == [[1], [3]]
    assert seen == [(1, True, 200), (3, False, 200)]
    assert trainer.generation_config is original
