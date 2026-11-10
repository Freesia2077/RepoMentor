"""CPU regressions use reference functions even when CUDA-only extras are installed."""
import inspect

import pytest


@pytest.fixture(autouse=True)
def cpu_reference_kernels(monkeypatch):
    from transformers.models.qwen3_5 import modeling_qwen3_5 as qwen
    # Installed causal-conv1d/FLA wrappers otherwise dispatch CPU fixtures to
    # CUDA kernels. Production preflight/training run in separate processes.
    for name in ["causal_conv1d_fn", "causal_conv1d_update",
                 "torch_chunk_gated_delta_rule", "torch_recurrent_gated_delta_rule"]:
        monkeypatch.setattr(qwen, name, inspect.unwrap(getattr(qwen, name)))
