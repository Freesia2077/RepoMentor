"""Cloud entry points. TRL provides SFT, multi-turn generation, GRPO and evaluation."""
import argparse
import gzip
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
import time
from contextlib import nullcontext
from pathlib import Path

from core import ROOT, data_path, digest, load_json, read_rows, report_path, save_json, supervision_labels

if os.name == "posix":
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from transformers import TrainerCallback


class StabilityCallback(TrainerCallback):
    """Use Trainer hooks for health checks and recoverable first/final checkpoints."""
    def __init__(self, evaluate_final=False, max_train_minutes=None):
        self.evaluate_final = evaluate_final
        self.zero_signal_steps = 0
        self.max_train_seconds = max_train_minutes * 60 if max_train_minutes else None

    def on_train_begin(self, args, state, control, **kwargs):
        self.train_started = time.monotonic()

    def on_pre_optimizer_step(self, args, state, control, model=None, **kwargs):
        import torch
        parameters = [p for p in model.parameters() if p.requires_grad and p.grad is not None]
        if not parameters:
            raise FloatingPointError("No trainable gradients; check LoRA binding and assistant labels")
        # Trainer already clips gradients. Reusing its standard norm helper here catches
        # NaN/Inf before optimizer.step instead of hiding them in averaged logging.
        torch.nn.utils.clip_grad_norm_(parameters, float("inf"), error_if_nonfinite=True)

    def on_step_end(self, args, state, control, **kwargs):
        if state.global_step in {1, state.max_steps}:
            control.should_save = True
        if self.evaluate_final and state.global_step == state.max_steps:
            control.should_evaluate = True
        if ((self.max_train_seconds and time.monotonic() - self.train_started >= self.max_train_seconds)
                or (Path(args.output_dir) / "STOP").exists()):
            control.should_training_stop = control.should_save = True
        return control

    def on_log(self, args, state, control, logs=None, **kwargs):
        logs = logs or {}
        with (Path(args.output_dir) / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"step": state.global_step, **logs}) + "\n")
        for key in ["loss", "grad_norm", "eval_loss"]:
            if key in logs and not math.isfinite(float(logs[key])):
                raise FloatingPointError(f"Non-finite {key}; resume from the previous complete checkpoint")
        if "frac_reward_zero_std" in logs:
            zero = logs["frac_reward_zero_std"] >= 1.0 and logs.get("grad_norm") == 0.0
            self.zero_signal_steps = self.zero_signal_steps + 1 if zero else 0
            if self.zero_signal_steps >= 4:
                raise RuntimeError("Four consecutive GRPO updates had no reward difference or gradient; inspect raw rollouts")


def attention_backend(requested=None):
    requested = requested or load_json(ROOT / "run-config.json")["acceleration"]["attention_backend"]
    if requested != "auto":
        return requested
    from transformers.utils import is_flash_attn_2_available
    return "flash_attention_2" if is_flash_attn_2_available() else "sdpa"


def attention_context(backend):
    if backend != "sdpa":
        return nullcontext()
    from torch.nn.attention import SDPBackend, sdpa_kernel
    # At 32K a silent quadratic math fallback can exhaust memory. The native Flash
    # backend is a supported alternative when the external flash-attn wheel is absent.
    return sdpa_kernel([SDPBackend.FLASH_ATTENTION, SDPBackend.EFFICIENT_ATTENTION])


def accelerator_probe(backend):
    """Exercise real optimized forward, backward and hybrid cache, without 2B weights."""
    import torch
    from transformers import AutoConfig, Qwen3_5ForCausalLM
    from transformers.models.qwen3_5 import modeling_qwen3_5 as qwen

    bindings = {}
    for name in ["torch_chunk_gated_delta_rule", "torch_recurrent_gated_delta_rule", "causal_conv1d_fn", "causal_conv1d_update"]:
        function = getattr(qwen, name)
        while callable(function):
            closure = inspect.getclosurevars(function).nonlocals
            implementation = closure.get("implementation")
            if implementation is not None:
                bindings[name] = implementation.__module__
                break
            function = getattr(function, "__wrapped__", None)
        if name not in bindings:
            raise RuntimeError(f"Cannot identify active kernel: {name}")
    if load_json(ROOT / "run-config.json")["acceleration"]["require_fast_linear_attention"]:
        if not all(module.startswith(("fla.", "causal_conv1d.")) for module in bindings.values()):
            raise RuntimeError(f"32K requires optimized DeltaNet/conv kernels; install requirements-cuda.txt: {bindings}")
    config = AutoConfig.from_pretrained(ROOT / ".cache/tokenizer", local_files_only=True).get_text_config()
    config.hidden_size, config.intermediate_size, config.vocab_size = 256, 512, 1024
    config.num_hidden_layers, config.num_attention_heads, config.num_key_value_heads = 4, 1, 1
    config.linear_num_key_heads = config.linear_num_value_heads = 1
    config.layer_types = ["linear_attention"]*3 + ["full_attention"]
    config.eos_token_id, config.pad_token_id = None, 0
    config._attn_implementation = backend
    model = Qwen3_5ForCausalLM(config).to(device="cuda", dtype=torch.bfloat16)
    ids = torch.randint(10, 1000, (1, 128), device="cuda")
    with attention_context(backend):
        model.train()
        loss = model(input_ids=ids, labels=ids, use_cache=False).loss
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
        model.eval()
        with torch.no_grad():
            generated = model.generate(input_ids=ids, max_new_tokens=2, do_sample=False, use_cache=True,
                                       return_dict_in_generate=True, disable_compile=True)
    if not torch.isfinite(loss) or generated.past_key_values is None:
        raise RuntimeError("Optimized forward/backward/cache probe failed")
    result = {"attention_backend": backend, "linear_kernel_bindings": bindings,
              "probe_loss": loss.item(), "probe_grad_norm": norm.item(), "hybrid_cache": True,
              "scope": "Small random Qwen3.5, 128 tokens; kernel/cache presence probe only. Real-model measurements are saved in training outputs."}
    del model, generated, loss, ids
    torch.cuda.empty_cache()
    return result


def checkpoint_for_resume(output, requested):
    """Ignore interrupted saves; resume only a complete Trainer state in this output."""
    from transformers.trainer_utils import get_last_checkpoint
    if requested is None:
        if output.exists():
            raise ValueError("Output exists; use --resume latest or choose a new directory")
        return None
    if not (output / "run.json").is_file():
        raise ValueError("Resume requires an existing run.json")
    latest = get_last_checkpoint(str(output))
    candidates = (sorted((p for p in output.glob("checkpoint-*") if p.is_dir() and p.name[11:].isdecimal()),
                         key=lambda p: int(p.name[11:]), reverse=True)
                  if requested == "latest" else [(ROOT / requested).resolve()])
    for path in candidates:
        if path.parent != output:
            raise ValueError("Checkpoint must belong to this output directory")
        required = ["trainer_state.json", "optimizer.pt", "scheduler.pt", "rng_state.pth", "adapter_model.safetensors"]
        if not all((path / name).is_file() and (path / name).stat().st_size > 0 for name in required):
            continue
        try:
            state = load_json(path / "trainer_state.json")
        except (ValueError, OSError):
            continue
        if isinstance(state, dict) and state.get("global_step") == int(path.name[11:]):
            return str(path)
    raise ValueError(f"No complete checkpoint to resume (last reported: {latest})")


def tokenizer():
    from transformers import AutoTokenizer
    from trl.chat_template_utils import add_response_schema
    tok = AutoTokenizer.from_pretrained(ROOT / ".cache/tokenizer", local_files_only=True, padding_side="left")
    tok = add_response_schema(tok)  # Recognize the original template before selecting its training variant.
    tok.chat_template = (ROOT / ".cache/training-template.jinja").read_text(encoding="utf-8")
    if digest(tok.chat_template) != load_json(report_path("preparation.json"))["training_template_sha256"]:
        raise ValueError("Prepared labels and runtime template differ")
    return tok


def sft_dataset(max_length, smoke=False, supervision="all_assistant"):
    from datasets import Dataset
    rows = {"train": [], "dev": []}
    with gzip.open(data_path("sft-tokens.jsonl.gz"), "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if len(row["input_ids"]) <= max_length:
                rows[row["split"]].append({"input_ids": row["input_ids"],
                                           "labels": supervision_labels(row["labels"], supervision)})
    if smoke:
        # Exercise the longest intact examples, rather than proving that only short examples fit.
        rows = {k: sorted(v, key=lambda r: len(r["input_ids"]), reverse=True)[:4] for k, v in rows.items()}
    if not all(rows.values()):
        raise ValueError("No complete train/dev trajectories fit the chosen length")
    return {k: Dataset.from_list(v) for k, v in rows.items()}


def configs(output, max_length=32768, smoke=False, cpu=False):
    from trl import GRPOConfig, SFTConfig
    run = load_json(ROOT / "run-config.json")
    common = dict(output_dir=str(output), seed=run["seed"], data_seed=run["seed"], report_to="none",
                  bf16=not cpu, use_cpu=cpu, per_device_train_batch_size=1, per_device_eval_batch_size=1,
                  gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
                  logging_steps=1, logging_nan_inf_filter=False, dataloader_num_workers=0,
                  save_strategy="steps", save_only_model=False,
                  max_grad_norm=1.0, use_cache=False, torch_compile=False,
                  optim="adamw_torch" if cpu else "adamw_torch_fused",
                  tf32=not cpu and run["acceleration"]["tf32"],
                  dataloader_pin_memory=not cpu, include_num_input_tokens_seen="non_padding")
    sft = SFTConfig(**common, max_length=max_length, packing=False,
                    dataset_kwargs={"skip_prepare_dataset": True},
                    gradient_accumulation_steps=1 if smoke else run["sft"]["gradient_accumulation_steps"],
                    learning_rate=run["sft"]["learning_rate"], num_train_epochs=run["sft"]["epochs"],
                    max_steps=2 if smoke else -1, warmup_steps=.05, lr_scheduler_type="cosine",
                    train_sampling_strategy="group_by_length", eval_strategy="steps",
                    save_steps=run["sft"]["save_steps"], eval_steps=run["sft"]["eval_steps"],
                    save_total_limit=run["sft"]["save_total_limit"],
                    load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False)
    grpo = GRPOConfig(**common, gradient_accumulation_steps=run["grpo"]["num_generations"],
                     num_generations=run["grpo"]["num_generations"], num_generations_eval=1,
                     learning_rate=run["grpo"]["learning_rate"], beta=run["grpo"]["beta"],
                     num_train_epochs=run["grpo"]["epochs"], max_steps=1 if smoke else -1,
                     save_steps=run["grpo"]["save_steps"], save_total_limit=run["grpo"]["save_total_limit"],
                     use_vllm=False, loss_type="dapo", importance_sampling_level="token",
                     scale_rewards="group", num_iterations=1,
                     chat_template_kwargs={"enable_thinking": False}, **run["rollout"])
    return sft, grpo


def preflight(gpu=False, tools=False, requested_attention=None, online_split=None):
    import torch
    from runtime import RepoEnvironment, _judge_class
    if report_path("bundle-manifest.json").exists():
        for entry in load_json(report_path("bundle-manifest.json")):
            if Path(entry["path"]).name in {"cpu-preflight.json", "gpu-preflight.json"}:
                continue  # Environment reports change on a new machine; data and code remain frozen.
            if hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError(f"Changed handoff input: {entry['path']}")
    from trl import GRPOTrainer, SFTTrainer  # noqa: F401 - validate imports, not only package metadata
    if gpu and (not torch.cuda.is_available() or not torch.cuda.is_bf16_supported()):
        raise RuntimeError("A CUDA GPU with BF16 support is required")
    tok = tokenizer()
    preparation = load_json(report_path("preparation.json"))
    if preparation.get("data_policy_sha256") != hashlib.sha256((ROOT / "data-policy.json").read_bytes()).hexdigest():
        raise ValueError("Prepared data policy is stale; run prepare.py data before training")
    for name, expected in preparation.get("prepared_files", {}).items():
        if hashlib.sha256(data_path(name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Prepared input changed or was interrupted: {name}; run prepare.py data")
    if not preparation.get("prepared_files"):
        raise ValueError("Missing prepared input identities; run prepare.py data")
    sft_config, grpo_config = configs(ROOT / "outputs/config-check", cpu=not gpu)
    datasets = sft_dataset(32768)
    if any(len(datasets[name]) != preparation["counts"][f"sft_{name}"] for name in ["train", "dev"]):
        raise ValueError("Prepared trajectory counts differ from the preparation report")
    splits = [online_split] if online_split else ["rl-train", "rl-dev", "eval"]
    tasks = [r for name in splits for r in read_rows(data_path(f"{name}.jsonl"))]
    missing_archives = []
    for row in tasks:
        path = ROOT / ".cache/archives" / (row["repo"].replace("/", "--") + "--" + row["commit"] + ".tar.gz")
        if not path.is_file():
            missing_archives.append(row["repo"])
        if path.is_file() and gpu and tools:
            RepoEnvironment().reset(**row)
    if tools and missing_archives:
        raise ValueError(f"Missing {len(missing_archives)} canonical archives; run prepare.py snapshots")
    result = {"mode": "gpu" if gpu else "cpu", "cuda_available": torch.cuda.is_available(),
              "versions": {p: importlib.metadata.version(p) for p in ["torch", "transformers", "trl", "peft", "datasets", "accelerate"]},
              "sft_rows": {k: len(v) for k, v in datasets.items()}, "online_tasks": len(tasks),
              "missing_online_archives": missing_archives,
              "judge_methods_loaded": _judge_class().__name__, "template_sha256": digest(tok.chat_template),
              "cache_policy": {"training_use_cache": sft_config.use_cache,
                               "rollout_use_cache": grpo_config.generation_kwargs["use_cache"],
                               "reuse_tool_cache": load_json(ROOT / "run-config.json")["grpo"]["reuse_tool_cache"]}}
    result["sft_training_policy"] = {"max_length": sft_config.max_length, "epochs": sft_config.num_train_epochs,
                                     "gradient_accumulation": sft_config.gradient_accumulation_steps,
                                     "gradient_checkpointing": sft_config.gradient_checkpointing_kwargs,
                                     "optimizer": sft_config.optim.value, "save_steps": sft_config.save_steps,
                                     "eval_steps": sft_config.eval_steps, "save_only_model": sft_config.save_only_model,
                                     "allocator": os.environ.get("PYTORCH_CUDA_ALLOC_CONF")}
    if gpu:
        torch.set_float32_matmul_precision("high")
        result["acceleration"] = accelerator_probe(attention_backend(requested_attention))
        if tools:
            env = RepoEnvironment()
            env.reset(**tasks[0])
        for name, arguments in ([("view_codebase", {"path": "/repo"}),
                                ("semantic_search", {"term": "import", "max_files": 2}),
                                ("execute_readonly_command", {"command": "find . -maxdepth 1 -type f | head -n 3"})] if tools else []):
            observation = getattr(env, name)(**arguments)
            if not env.events[-1]["success"]:
                raise RuntimeError(f"Cloud tool preflight failed: {name}: {observation[:500]}")
        result.update(gpu=torch.cuda.get_device_name(), memory_gib=torch.cuda.get_device_properties(0).total_memory/2**30)
        result["tools_checked"] = tools
        result["tool_sandbox"] = os.environ.get("REPOMENTOR_PY_SFTGRPO_SANDBOX", "bubblewrap") if tools else None
        result["kernel_versions"] = {}
        for package in ["flash-attn", "flash-linear-attention", "causal-conv1d", "triton"]:
            try:
                result["kernel_versions"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                result["kernel_versions"][package] = None
    save_json(report_path(f"{'gpu' if gpu else 'cpu'}-preflight.json"), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run(args):
    started = time.monotonic()
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel
    from runtime import JudgeReward, RepoEnvironment
    from transformers import AutoModelForCausalLM
    from trl import SFTTrainer

    output = (ROOT / args.output).resolve()
    if args.action == "sft" and args.adapter:
        raise ValueError("Both SFT conditions must start from the same base; do not pass --adapter")
    if args.action != "sft" and args.supervision is not None:
        raise ValueError("--supervision selects SFT labels only")
    if args.action != "grpo" and args.reward_mode is not None:
        raise ValueError("--reward-mode selects a GRPO reward ablation only")
    checkpoint = checkpoint_for_resume(output, args.resume)
    if args.action == "evaluate" and checkpoint:
        raise ValueError("Evaluation outputs cannot be resumed as training checkpoints")
    if args.train_minutes is not None and (args.action != "grpo" or args.train_minutes <= 0):
        raise ValueError("--train-minutes is a positive GRPO wall-time budget")
    if args.action == "grpo":
        for key in ["REPOMENTOR_PY_SFTGRPO_JUDGE_API_KEY", "REPOMENTOR_PY_SFTGRPO_JUDGE_MODEL"]:
            if not os.environ.get(key):
                raise ValueError(f"Missing {key}")
    if args.action == "grpo" and not args.adapter:
        raise ValueError("GRPO requires an SFT adapter")
    if args.train_tasks is not None and (args.action != "grpo" or not 0 < args.train_tasks <= len(read_rows(data_path("rl-train.jsonl")))):
        raise ValueError("--train-tasks must select a positive prefix of the prepared GRPO training set")
    environment = preflight(gpu=True, tools=args.action != "sft", requested_attention=args.attn_implementation,
                            online_split="rl-train" if args.action == "grpo" else args.split)
    lock, settings = load_json(ROOT / "sources.lock.json"), load_json(ROOT / "run-config.json")
    backend = environment["acceleration"]["attention_backend"]
    identity = {"action": args.action, "max_length": args.max_length, "smoke": args.smoke,
                "supervision": args.supervision or settings["sft"]["supervision"] if args.action == "sft" else None,
                "reward_mode": args.reward_mode or settings["reward"]["mode"] if args.action == "grpo" else None,
                "adapter": args.adapter, "settings": settings, "model_revision": lock["model_revision"],
                "training_template": load_json(report_path("preparation.json"))["training_template_sha256"],
                "data_sha256": hashlib.sha256(data_path("sft-tokens.jsonl.gz").read_bytes()).hexdigest(),
                "attention_backend": backend, "judge_model": os.environ.get("REPOMENTOR_PY_SFTGRPO_JUDGE_MODEL") if args.action == "grpo" else None}
    if args.adapter:
        identity["adapter_sha256"] = hashlib.sha256((ROOT / args.adapter / "adapter_model.safetensors").read_bytes()).hexdigest()
    identity["code_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                               for name in ["core.py", "train.py", "runtime.py", "generation.py", "rewards.py"]}
    if args.action != "sft":
        identity["train_tasks"] = args.train_tasks
        identity["online_data_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                          for name in [str(data_path("rl-train.jsonl")), str(data_path(f"{args.split}.jsonl")),
                                                       str(data_path("system.txt")), "tool-schema.json"]}
    if checkpoint and load_json(output / "run.json").get("identity") != identity:
        raise ValueError("Resume data/model/settings differ from the original run")
    tok = tokenizer()
    model = AutoModelForCausalLM.from_pretrained(lock["model"], revision=lock["model_revision"],
                                              dtype=torch.bfloat16, attn_implementation=backend, trust_remote_code=False)
    model.config.use_cache = False
    if args.adapter:
        model = PeftModel.from_pretrained(model, str((ROOT / args.adapter).resolve()), is_trainable=args.action == "grpo")
    sft_args, grpo_args = configs(output, args.max_length, args.smoke)
    output.mkdir(parents=True, exist_ok=bool(checkpoint))
    record_path = output / f"resume-{time.time_ns()}.json" if checkpoint else output / "run.json"
    save_json(record_path, {"action": args.action, "arguments": vars(args), "settings": settings,
                                    "model_revision": lock["model_revision"], "data": load_json(report_path("preparation.json")),
                                    "judge_model": os.environ.get("REPOMENTOR_PY_SFTGRPO_JUDGE_MODEL") if args.action == "grpo" else None,
                                    "evaluation_review": "assistant_review_pending" if args.action == "evaluate" else None, "human_approved": False,
                                    "identity": identity, "environment": environment, "resume_checkpoint": checkpoint})
    torch.cuda.reset_peak_memory_stats()
    if args.action == "sft":
        data = sft_dataset(args.max_length, args.smoke, identity["supervision"])
        lora = settings["sft"]
        trainer = SFTTrainer(model=model, args=sft_args, processing_class=tok, train_dataset=data["train"],
                             eval_dataset=data["dev"], peft_config=LoraConfig(r=lora["lora_rank"], lora_alpha=lora["lora_alpha"],
                             target_modules="all-linear", lora_dropout=0, bias="none", task_type="CAUSAL_LM"),
                             callbacks=[StabilityCallback(evaluate_final=True)])
        trainer.model.print_trainable_parameters()
        with attention_context(backend):
            metrics = trainer.train(resume_from_checkpoint=checkpoint).metrics
        metrics.update(best_eval_loss=trainer.state.best_metric, best_checkpoint=trainer.state.best_model_checkpoint,
                       train_trajectories=len(data["train"]), dev_trajectories=len(data["dev"]),
                       supervision=identity["supervision"],
                       train_target_tokens=sum(sum(v != -100 for v in row["labels"]) for row in data["train"]),
                       trainable_parameters=sum(p.numel() for p in trainer.model.parameters() if p.requires_grad))
    else:
        from generation import CachedGRPOTrainer
        train_rows = read_rows(data_path("rl-train.jsonl"))
        eval_rows = read_rows(data_path(f"{args.split}.jsonl"))
        if args.train_tasks is not None:
            if args.action != "grpo" or not 0 < args.train_tasks <= len(train_rows):
                raise ValueError("--train-tasks must select a positive prefix of the prepared GRPO training set")
            train_rows = train_rows[:args.train_tasks]
        if args.smoke:
            train_rows, eval_rows = train_rows[:8], eval_rows[:2]
        reward = JudgeReward(output / "rollouts.jsonl", collect_only=args.action == "evaluate",
                             policy={**settings["reward"], "mode": identity["reward_mode"] or settings["reward"]["mode"]})
        if args.action == "evaluate":
            for name in ["temperature", "top_p", "top_k"]:
                setattr(grpo_args, name, settings["evaluation"][name])
            grpo_args.per_device_eval_batch_size = settings["evaluation"].get("batch_size", 1)
            grpo_args.beta = 0.0  # Collect answers without a reference-model pass or policy updates.
        trainer = CachedGRPOTrainer(model=model, args=grpo_args, processing_class=tok,
                              train_dataset=Dataset.from_list(train_rows), eval_dataset=Dataset.from_list(eval_rows),
                              environment_factory=RepoEnvironment, reward_funcs=reward,
                              reuse_tool_cache=settings["grpo"]["reuse_tool_cache"],
                              generation_micro_batch_size=settings["grpo"].get("generation_micro_batch_size", 2),
                              activation_offloading=settings["grpo"].get("activation_offloading", False),
                              persist_rollout_batch=settings["grpo"].get("persist_rollout_batch", False),
                              callbacks=[StabilityCallback(max_train_minutes=args.train_minutes)])
        if args.action == "grpo":
            # TRL copies the loaded SFT adapter into its own reference adapter.
            # Adding it before Trainer construction would create a duplicate.
            assert "ref" in trainer.model.peft_config
            assert not any(p.requires_grad for n, p in trainer.model.named_parameters() if ".ref." in n)
        with attention_context(backend):
            metrics = trainer.train(resume_from_checkpoint=checkpoint).metrics if args.action == "grpo" else trainer.evaluate()
        if args.action == "grpo":
            recorded = read_rows(output / "rollouts.jsonl")
            metrics.update(rollouts=len(recorded), unique_tasks=len({r["task_id"] for r in recorded}),
                           optimizer_updates=trainer.state.global_step, reward_mode=identity["reward_mode"],
                           update_ineligible_candidates=sum(not r.get("group_update_eligible", True) for r in recorded),
                           prepared_training_tasks=len(train_rows), wall_time_budget_minutes=args.train_minutes)
    if args.action != "evaluate":
        trainer.save_model(str(output / "adapter"))
        tok.save_pretrained(output / "adapter")
    metrics.update(wall_seconds=time.monotonic()-started, peak_allocated_gib=torch.cuda.max_memory_allocated()/2**30,
                   peak_reserved_gib=torch.cuda.max_memory_reserved()/2**30)
    if hasattr(trainer, "cache_totals"):
        metrics["generation_cache"] = dict(trainer.cache_totals)
    save_json(output / "metrics.json", metrics)
    if args.action == "evaluate":
        metrics["answer_score_status"] = "pending_assistant_review; Trainer rewards are placeholders"
        save_json(output / "metrics.json", metrics)
        summarize_evaluation(output, eval_rows)
    print(json.dumps(metrics, indent=2))


def summarize_evaluation(output, expected):
    from runtime import WEIGHTS
    rows = read_rows(output / "rollouts.jsonl")
    ids = [row["task_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != {row["task_id"] for row in expected}:
        raise ValueError("Evaluation must contain exactly one attempt per frozen task")
    reviews = read_rows(output / "reviews.jsonl") if (output / "reviews.jsonl").exists() else []
    by_id = {row["task_id"]: row for row in rows}
    seen = set()
    for review in reviews:
        task = review["task_id"]
        if task in seen or task not in by_id:
            raise ValueError("Duplicate or unknown reviewed task")
        seen.add(task)
        row = by_id[task]
        if row["answer"] is None or review.get("answer_sha256") != digest(row["answer"]):
            raise ValueError("Review does not bind to this exact completed answer")
        scores = review["scores"]
        if set(scores) != set(WEIGHTS) or not all(type(v) is int and 1 <= v <= 10 for v in scores.values()):
            raise ValueError("Review must contain five integer scores in [1,10]")
        if review.get("reviewer") != "codex_session_assistant" or review.get("human_approved") is not False:
            raise ValueError("This evaluation uses disclosed assistant review, not human approval")
        row.update(scores=scores, review_pending=False, judge_failed=False)
    valid = [r for r in rows if r["scores"] is not None]
    missing, failed = sum(r["missing_finish"] for r in rows), sum(r["judge_failed"] for r in rows)
    pending = sum(r.get("review_pending", False) for r in rows)
    score_sum = sum(sum(r["scores"].values()) / 5 * 10 for r in valid)
    result = {"tasks": len(rows), "scored": len(valid), "missing_finish": missing, "judge_failed": failed, "review_pending": pending,
              "valid_only_dimension_means_1_to_10": {k: sum(r["scores"][k] for r in valid)/len(valid) if valid else None for k in WEIGHTS},
              "five_dimension_mean_0_to_100": score_sum/len(rows) if not (failed or pending) else None,
              "score_policy": "Missing finish receives zero in the evaluation aggregate; judge errors leave the aggregate unknown. Official training reward imputation is not evaluation correctness.",
              "assistant_turns": sum(sum(m["role"] == "assistant" for m in r["completion"]) for r in rows),
              "tool_requests": sum(len(r["tools"]) for r in rows),
              "mean_tool_requests": sum(len(r["tools"]) for r in rows) / len(rows),
              "executed_tools": sum(event.get("executed", True) for r in rows for event in r["tools"]),
              "duplicate_requests": sum("duplicate_of" in event for r in rows for event in r["tools"]),
              "tool_errors": sum(not event["success"] for r in rows for event in r["tools"]),
              "judge_requests": sum(len(r["judge_requests"]) for r in rows), "human_approved": False}
    result["review_method"] = "reference-guided Codex session assistant review" if reviews else "pending"
    save_json(output / "evaluation-summary.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight", "sft", "grpo", "evaluate", "summarize"])
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--tools", action="store_true", help="Also verify online tool isolation (not needed for offline SFT)")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--supervision", choices=["all_assistant", "last_assistant"], help="SFT target range; input and sample order stay identical")
    parser.add_argument("--reward-mode", choices=["quality", "grounded"], help="Original quality reward or evidence/quality-gated redundancy shaping")
    parser.add_argument("--train-tasks", type=int, help="Use a fixed prefix of prepared RL training tasks within the rental budget")
    parser.add_argument("--train-minutes", type=float, help="Stop GRPO after a complete update and save its full checkpoint when this wall-time budget is reached")
    parser.add_argument("--max-length", type=int, choices=[16384, 24576, 32768], default=32768)
    parser.add_argument("--adapter", help="SFT or GRPO adapter path, relative to SFTGRPO")
    parser.add_argument("--split", choices=["rl-dev", "eval"], default="rl-dev")
    parser.add_argument("--output", default="outputs/run")
    parser.add_argument("--resume", help="latest or a complete checkpoint path relative to SFTGRPO")
    parser.add_argument("--attn-implementation", choices=["auto", "flash_attention_2", "sdpa"])
    args = parser.parse_args()
    if args.action == "preflight":
        preflight(args.gpu, args.tools, args.attn_implementation)
    elif args.action == "summarize":
        summarize_evaluation((ROOT / args.output).resolve(), read_rows(data_path(f"{args.split}.jsonl")))
    else:
        run(args)
