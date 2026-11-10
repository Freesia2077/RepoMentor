"""Rollout-scoped cache reuse for TRL's existing text-only tool loop (single GPU)."""
import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import torch
from trl import GRPOTrainer
from trl.extras.profiling import profiling_context
from trl.models import unwrap_model_for_generation


class RolloutCache:
    """Keep native KV/DeltaNet states only while an unchanged policy extends its histories.

    Each candidate owns one unpadded cache. Consume each previous entry at most once,
    so divergent candidates never mutate a shared recurrent state. Never crop hybrid
    states: a changed prefix is recomputed. The trainer clears everything before loss.
    """
    def __init__(self):
        self.entries = []
        self.stats = Counter()

    @torch.no_grad()
    def generate(self, model, prompts, config, device):
        if not config.use_cache:
            raise ValueError("Rollout cache reuse requires generation_kwargs.use_cache=True")
        if (config.num_beams or 1) != 1 or (config.num_return_sequences or 1) != 1:
            raise ValueError("Rollout cache reuse supports one sampled continuation per candidate")
        if config.cache_implementation not in (None, "dynamic"):
            raise ValueError("Use the model's native dynamic cache for multi-turn reuse")
        config = deepcopy(config)
        config.return_dict_in_generate = True
        config.output_scores = config.output_logits = False
        config.output_hidden_states = config.output_attentions = False
        previous = [(prefix, cache) for prefix, cache in self.entries
                    if any(len(prefix) < len(ids) and ids[:len(prefix)] == prefix for ids in prompts)]
        self.entries = []  # Drop caches belonging to finished or changed histories before allocating new states.
        completions = []
        for ids in prompts:
            matches = [i for i, (prefix, _) in enumerate(previous)
                       if len(prefix) < len(ids) and ids[:len(prefix)] == prefix]
            chosen = max(matches, key=lambda i: len(previous[i][0])) if matches else None
            prefix, cache = previous.pop(chosen) if chosen is not None else ([], None)
            inputs = torch.tensor([ids], dtype=torch.long, device=device)
            output = model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                                    past_key_values=cache, generation_config=config)
            sequence = output.sequences[0].tolist()
            cache = output.past_key_values
            if cache is None:
                raise RuntimeError("Generation did not return a cache despite use_cache=True")
            length = int(cache.get_seq_length())
            if sequence[:len(ids)] != ids or not 0 < length <= len(sequence):
                raise RuntimeError("Unexpected generated sequence/cache boundary")
            # generate normally leaves its last sampled token (including EOS) unprocessed.
            # Cache only the actually processed prefix; the next turn processes the rest.
            self.entries.append((sequence[:length], cache))
            completions.append(sequence[len(ids):])
            self.stats.update(calls=1, hits=int(chosen is not None),
                              reused_prefix_tokens=len(prefix), prefill_tokens=len(ids)-len(prefix))
        return completions


class CachedGRPOTrainer(GRPOTrainer):
    """Adapt generation only; TRL retains rollout, tool execution, masks and GRPO loss."""
    def __init__(self, *args, reuse_tool_cache=True, generation_micro_batch_size=2,
                 activation_offloading=False, persist_rollout_batch=False,
                 final_answer_reserve=2048, **kwargs):
        super().__init__(*args, **kwargs)
        self.reuse_tool_cache = reuse_tool_cache
        self.generation_micro_batch_size = generation_micro_batch_size
        self.cache_totals = Counter()
        self._rollout_cache = None
        self.activation_offloading = activation_offloading
        self._activation_context = None
        self.persist_rollout_batch = persist_rollout_batch
        self.final_answer_reserve = final_answer_reserve
        if reuse_tool_cache and (self.use_vllm or self.use_transformers_continuous_batching
                                 or self.accelerator.num_processes != 1):
            raise ValueError("This cache adapter targets the single-process Transformers rollout backend")

    def _generate_and_score_completions(self, inputs):
        if not self.persist_rollout_batch or not self.model.training:
            return super()._generate_and_score_completions(inputs)
        if self.accelerator.num_processes != 1:
            raise ValueError("Durable rollout batches currently support one training process")
        from .core import digest, load_json, save_json
        directory = Path(self.args.output_dir) / "rollout-batch"
        metadata = directory / "ready.json"
        policy = hashlib.sha256()
        for name, parameter in sorted(self.model.named_parameters()):
            if parameter.requires_grad or ".ref." in name:
                value = parameter.detach().cpu().contiguous()
                policy.update(name.encode())
                policy.update(str(value.dtype).encode())
                policy.update(value.view(torch.uint8).numpy().tobytes())
        key = {"step": self.state.global_step, "policy_sha256": policy.hexdigest(),
               "inputs_sha256": digest(json.dumps(inputs, sort_keys=True, ensure_ascii=False)),
               "identity": load_json(Path(self.args.output_dir) / "run.json")["identity"]}
        if metadata.exists():
            saved = load_json(metadata)
            if saved["key"]["step"] == self.state.global_step:
                if saved["key"] != key:
                    raise ValueError("Saved rollout batch does not match restored policy, task order or run identity")
                for name, expected in saved["files"].items():
                    if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
                        raise ValueError(f"Incomplete or changed rollout recovery file: {name}")
                batch = torch.load(directory / "batch.pt", map_location="cpu", weights_only=True)
                self._load_rng_state(str(directory))  # Native Trainer restores Python/NumPy/Torch/CUDA.
                for name, values in saved["metrics"].items():
                    self._metrics["train"][name].extend(values)
                print(f"ROLLOUT_BATCH_REUSED step={self.state.global_step}; no generation or judge calls", flush=True)
                return {k: v.to(self.accelerator.device) if isinstance(v, torch.Tensor) else v
                        for k, v in batch.items()}
        batch = super()._generate_and_score_completions(inputs)
        directory.mkdir(parents=True, exist_ok=True)
        # Publish the ready marker last. An interrupted write must never look reusable.
        metadata.unlink(missing_ok=True)
        torch.save({k: v.detach().cpu() if isinstance(v, torch.Tensor) else v for k, v in batch.items()},
                   directory / "batch.pt")
        self._save_rng_state(str(directory))
        record = {"key": key, "files": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                                        for name in ["batch.pt", "rng_state.pth"]},
                  "metrics": dict(self._metrics["train"])}
        save_json(directory / "ready.tmp", record)
        (directory / "ready.tmp").replace(metadata)
        print(f"ROLLOUT_BATCH_SAVED step={self.state.global_step}; exact tokens, masks, advantages and RNG", flush=True)
        return batch

    def training_step(self, *args, **kwargs):
        if not self.activation_offloading:
            return super().training_step(*args, **kwargs)
        if self._activation_context is None:
            from trl.models import get_act_offloading_ctx_manager
            self._activation_context = get_act_offloading_ctx_manager(self.model, max_fwd_stash_size=1)
        with self._activation_context:
            return super().training_step(*args, **kwargs)

    def _load_from_checkpoint(self, resume_from_checkpoint, model=None):
        super()._load_from_checkpoint(resume_from_checkpoint, model)
        policy = model if model is not None else self.model
        checkpoint = Path(resume_from_checkpoint)
        # PEFT puts default at the root and ref in ref/. Transformers' subdir
        # branch restores only ref, silently leaving the policy at its initializer.
        if ((checkpoint / "adapter_model.safetensors").is_file()
                and (checkpoint / "ref/adapter_model.safetensors").is_file()
                and "default" in getattr(policy, "peft_config", {})):
            policy.load_adapter(str(checkpoint), "default", is_trainable=True)
            policy.set_adapter("default")

    def _tool_call_loop(self, prompts, prompt_ids, completion_ids, completions, *args, **kwargs):
        # Observe native TRL's mutable batch state; keep its dispatch, rollback,
        # parsing and masks intact. No separate agent/tool loop is introduced.
        self._tool_budget_context = (prompt_ids, completion_ids, completions)
        try:
            return super()._tool_call_loop(prompts, prompt_ids, completion_ids, completions, *args, **kwargs)
        finally:
            self._tool_budget_context = None

    def _answer_reserve(self):
        return min(getattr(self, "final_answer_reserve", 0), self.max_completion_length // 4)

    def _get_tool_suffix_ids(self, tool_messages):
        # A tool observation alone is not a control instruction. Render the
        # recorded budget notice as a follow-up turn; TRL masks the whole suffix.
        notices = []
        for message in tool_messages:
            try:
                value = json.loads(message["content"])
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("runtime_notice"):
                if value["runtime_notice"] not in notices:
                    notices.append(value["runtime_notice"])
                # Render control text once as a user turn, rather than twice
                # inside both a potentially large tool observation and a user turn.
                message["content"] = json.dumps({k: v for k, v in value.items() if k != "runtime_notice"}, ensure_ascii=False)
        final_notice = (
                "FINAL ANSWER TURN. This is the final allowed turn. You MUST NOT call any tools. "
                "Keep A. Reasoning to at most two sentences; do not repeat the investigation or enumerate findings again. "
                "Do not emit tool XML or tool arguments. Provide B. Final Answer now inside <finish>...</finish> using only observed evidence. "
                "State uncertainty briefly if evidence is incomplete."
        )
        if getattr(self, "_rollout_turn", 0) >= self.max_tool_calling_iterations:
            notices.append("MAX TURNS REACHED. " + final_notice)

        def render():
            if notices:
                followup = {"role": "user", "content": "\n".join(notices)}
                tool_messages[-1]["followup_message"] = followup
                return super(CachedGRPOTrainer, self)._get_tool_suffix_ids(tool_messages + [followup])
            return super(CachedGRPOTrainer, self)._get_tool_suffix_ids(tool_messages)

        suffix = render()
        context = getattr(self, "_tool_budget_context", None)
        reserve = self._answer_reserve()
        if context is not None and reserve:
            initial, generated, parsed = context
            index = next(i for i, messages in enumerate(parsed) if messages[-1] is tool_messages[-1])
            remaining = self.max_completion_length - len(generated[index])
            config = self.model.config.get_text_config()
            remaining = min(remaining, config.max_position_embeddings-1-len(initial[index])-len(generated[index]))
            if len(suffix) + 2*reserve + 256 >= remaining:
                notices.append(final_notice)
                suffix = render()
            # A large tool return must not consume the answer reserve or be
            # silently rolled back by TRL. Keep raw returns in environment events.
            available = remaining - reserve
            while len(suffix) > available:
                message = max(tool_messages, key=lambda m: len(m["content"]))
                ids = self.processing_class.encode(message["content"], add_special_tokens=False)
                keep = max(0, len(ids) - (len(suffix)-available) - 32)
                if not ids or keep >= len(ids):
                    raise ValueError("Tool framing cannot fit with the final answer reserve")
                message.setdefault("original_content_tokens", len(ids))
                message["context_truncated"] = True
                clipped = self.processing_class.decode(ids[:keep]) + "\n[Tool output truncated to reserve final-answer space.]"
                if clipped == message["content"]:
                    raise ValueError("Tool framing cannot fit with the final answer reserve")
                message["content"] = clipped
                suffix = render()
        return suffix

    def _generate(self, prompts):
        self._rollout_turn = 0
        if not self.reuse_tool_cache:
            return super()._generate(prompts)
        self._rollout_cache = RolloutCache()
        try:
            return super()._generate(prompts)
        finally:
            # Includes normal exits, length-budget exits and exceptions. No cache may reach
            # the optimizer, reference-adapter forward, or the next rollout generation.
            self.cache_totals.update(self._rollout_cache.stats)
            mode = "train" if self.model.training else "eval"
            for key, value in self._rollout_cache.stats.items():
                self._metrics[mode][f"cache/{key}"].append(float(value))
            self._rollout_cache = None

    def _generate_single_turn(self, prompt_ids, images, multimodal_fields, num_generations, has_tool_images=False):
        turn = getattr(self, "_rollout_turn", 0)
        self._rollout_turn = turn + 1
        original = self.generation_config
        if turn == 0:
            self._initial_prompt_ids = [ids[:] for ids in prompt_ids]
        reserve = self._answer_reserve() if hasattr(self, "max_completion_length") else 0
        plans = []
        for ids in prompt_ids:
            initial_length = next(len(p) for p in self._initial_prompt_ids if ids[:len(p)] == p)
            remaining = self.max_completion_length - (len(ids)-initial_length) if reserve else None
            final = bool(self.max_tool_calling_iterations and turn >= self.max_tool_calling_iterations)
            if remaining is not None:
                remaining = min(remaining, self.model.config.get_text_config().max_position_embeddings-1-len(ids))
                final |= remaining <= 2*reserve + 256
            limit = min(original.max_new_tokens or self.max_completion_length,
                        remaining if final else remaining-reserve-256) if remaining is not None else original.max_new_tokens
            plans.append((final, limit))
        try:
            if len(set(plans)) > 1:
                # Different candidates can exhaust their budgets at different times.
                # Split only these boundary turns, preserving the original batch order.
                if images is not None or multimodal_fields or has_tool_images:
                    raise ValueError("Per-candidate answer reserves require text-only repository tasks")
                result = [None] * len(prompt_ids)
                for plan in dict.fromkeys(plans):
                    indices = [i for i, p in enumerate(plans) if p == plan]
                    self.generation_config = self._turn_config(original, *plan)
                    values, logprobs, mask = self._generate_turn([prompt_ids[i] for i in indices], None, {}, num_generations)
                    assert logprobs is None and mask is None
                    for i, value in zip(indices, values, strict=True):
                        result[i] = value
                return result, None, None
            self.generation_config = self._turn_config(original, *plans[0])
            return self._generate_turn(prompt_ids, images, multimodal_fields, num_generations, has_tool_images)
        finally:
            self.generation_config = original

    def _turn_config(self, original, final, limit):
        config = deepcopy(original)
        if limit is not None:
            if limit < 1:
                raise ValueError("No space left for a model-generated final answer")
            config.max_new_tokens = limit
        if final:
            # This blocks native calls, not every malformed textual imitation.
            # The terminal reward must still require a closed finish answer.
            config.bad_words_ids = list(original.bad_words_ids or []) + [self.processing_class.encode("<tool_call>", add_special_tokens=False)]
        return config

    def _generate_turn(self, prompt_ids, images, multimodal_fields, num_generations, has_tool_images=False):
        if self._rollout_cache is None:
            if len(prompt_ids) > self.generation_micro_batch_size:
                if images is not None or multimodal_fields or has_tool_images:
                    raise ValueError("Repository rollout micro-batches must be text-only")
                completions = []
                for start in range(0, len(prompt_ids), self.generation_micro_batch_size):
                    ids, logprobs, sampling_mask = super()._generate_single_turn(
                        prompt_ids[start:start+self.generation_micro_batch_size], None, {}, num_generations)
                    assert logprobs is None and sampling_mask is None  # Native Transformers backend.
                    completions.extend(ids)
                return completions, None, None
            return super()._generate_single_turn(prompt_ids, images, multimodal_fields, num_generations, has_tool_images)
        if images is not None or multimodal_fields or has_tool_images:
            raise ValueError("Repository rollouts must be text-only for this cache adapter")
        with (
            profiling_context(self, "transformers.generate_cached"),
            unwrap_model_for_generation(self.model_wrapped, self.accelerator,
                                        gather_deepspeed3_params=self.args.ds3_gather_for_generation,
                                        generation_kwargs=self.generation_kwargs,
                                        gradient_checkpointing_kwargs=self.args.gradient_checkpointing_kwargs) as model,
            self._dist.summon_full_params(self.model_wrapped, recurse=False),
        ):
            was_training = model.training
            model.eval()
            try:
                ids = self._rollout_cache.generate(model, prompt_ids, self.generation_config, self.accelerator.device)
            finally:
                model.train(was_training)
        return ids, None, None
