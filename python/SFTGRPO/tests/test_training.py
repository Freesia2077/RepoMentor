"""Real tiny-model SFT and interrupted-state recovery; CPU only, no downloaded weights."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import save_json  # noqa: E402
from test_generation import tiny_qwen  # noqa: E402
from train import StabilityCallback, checkpoint_for_resume, configs, tokenizer  # noqa: E402


def test_lora_sft_checkpoint_resume_matches_uninterrupted_training(tmp_path):
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from trl import SFTTrainer
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)

    class StopAfterFirst(StabilityCallback):
        def on_step_end(self, args, state, control, **kwargs):
            super().on_step_end(args, state, control, **kwargs)
            if state.global_step == 1:
                control.should_training_stop = True
            return control

    rows = [{"input_ids": list(range(start, start+12)), "labels": [-100]*8 + list(range(start+8, start+12))}
            for start in [10, 20, 30]]
    data = Dataset.from_list(rows)

    def trainer_at(path, stop=False):
        args, _ = configs(path, cpu=True)
        assert args.num_train_epochs == 2 and args.max_length == 32768
        assert not args.use_cache and not args.logging_nan_inf_filter and not args.save_only_model
        assert args.gradient_checkpointing and args.gradient_checkpointing_kwargs == {"use_reentrant": False}
        args.gradient_accumulation_steps = 1
        args.num_train_epochs = 1  # Three-step CPU recovery fixture, independent of the production two-epoch schedule.
        args.disable_tqdm = True
        return SFTTrainer(model=tiny_qwen(), args=args, processing_class=tokenizer(),
                          train_dataset=data, eval_dataset=data.select([0]),
                          peft_config=LoraConfig(r=2, lora_alpha=4, target_modules="all-linear", task_type="CAUSAL_LM"),
                          callbacks=[StopAfterFirst(True) if stop else StabilityCallback(True)])

    try:
        uninterrupted = trainer_at(tmp_path / "full")
        uninterrupted.train()
        initial = trainer_at(tmp_path / "resumed", stop=True)
        initial.train()
        save_json(tmp_path / "resumed/run.json", {"fixture": True})
        incomplete = tmp_path / "resumed/checkpoint-2"
        incomplete.mkdir()
        (incomplete / "trainer_state.json").write_text("{}")
        checkpoint = checkpoint_for_resume((tmp_path / "resumed").resolve(), "latest")
        assert Path(checkpoint).name == "checkpoint-1"
        for name in ["adapter_model.safetensors", "optimizer.pt", "scheduler.pt", "rng_state.pth", "trainer_state.json"]:
            assert (Path(checkpoint) / name).stat().st_size > 0
        resumed = trainer_at(tmp_path / "resumed")
        resumed.train(resume_from_checkpoint=checkpoint)
        assert resumed.state.global_step == uninterrupted.state.global_step == 3
        assert resumed.state.best_model_checkpoint.endswith("checkpoint-3")
        expected = dict(uninterrupted.model.named_parameters())
        for name, value in resumed.model.named_parameters():
            if value.requires_grad:
                torch.testing.assert_close(value, expected[name], rtol=0, atol=0)
        assert any("grad_norm" in json.loads(line) for line in (tmp_path / "resumed/progress.jsonl").read_text().splitlines())
    finally:
        torch.set_num_threads(previous_threads)


def test_nonfinite_gradient_stops_before_optimizer_update():
    import torch
    model = torch.nn.Linear(1, 1)
    for parameter in model.parameters():
        parameter.grad = torch.full_like(parameter, float("nan"))
    before = {k: v.detach().clone() for k, v in model.named_parameters()}
    with pytest.raises(RuntimeError, match="non-finite"):
        StabilityCallback().on_pre_optimizer_step(None, None, None, model=model)
    for name, parameter in model.named_parameters():
        torch.testing.assert_close(parameter, before[name])


def test_resume_skips_zero_byte_invalid_state_and_unrelated_directories(tmp_path):
    save_json(tmp_path / "run.json", {"fixture": True})
    (tmp_path / "checkpoint-notes").mkdir()
    for step in [8, 9, 10]:
        checkpoint = tmp_path / f"checkpoint-{step}"
        checkpoint.mkdir()
        for name in ["adapter_model.safetensors", "optimizer.pt", "scheduler.pt", "rng_state.pth"]:
            (checkpoint / name).write_bytes(b"fixture")
        save_json(checkpoint / "trainer_state.json", {"global_step": step})
    (tmp_path / "checkpoint-9/optimizer.pt").write_bytes(b"")
    (tmp_path / "checkpoint-10/trainer_state.json").write_text('{"global_step":')
    assert Path(checkpoint_for_resume(tmp_path.resolve(), "latest")).name == "checkpoint-8"
    with pytest.raises(ValueError, match="No complete checkpoint"):
        checkpoint_for_resume(tmp_path.resolve(), str(tmp_path / "checkpoint-9"))
