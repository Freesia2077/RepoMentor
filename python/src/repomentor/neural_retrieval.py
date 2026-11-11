"""Pinned local inference and per-input caches for Qwen embedding and yes/no reranking."""

# 加载已核验的本地 embedding / reranker 权重，提供带身份缓存的批量推理。
# 一次仅保留一个活动模型，以便在单卡上与生成器分时使用显存。

import gc
import hashlib
import importlib.metadata
import json
import os
import time
from pathlib import Path

from .paths import data_root, python_home
from .records import atomic_write
from .runtimes.chat import fingerprint

INSTRUCTION = (
    "Given a repository learning question, retrieve source code and documentation that answer the question."
)
PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def load_retrieval_models(path=None):
    path = Path(
        path
        or os.environ.get("REPOMENTOR_PY_RETRIEVAL_SETTINGS")
        or python_home() / "config/model-settings.retrieval.json"
    )
    if not path.is_file():
        raise ValueError("retrieval models are not configured; run scripts/prepare_retrieval_models.py")
    value = json.loads(path.read_text(encoding="utf-8"))
    if set(value) != {"embedding", "reranker"}:
        raise ValueError("retrieval configuration must declare embedding and reranker")
    for spec in value.values():
        if len(spec["revision"]) != 40 or len(spec["sha256"]["model.safetensors"]) != 64:
            raise ValueError("retrieval model requires immutable revision and weight hash")
    return value


def document_text(chunk):
    symbol = f"\nSymbol: {chunk.symbol}" if chunk.symbol else ""
    return f"File: {chunk.path}{symbol}\n{chunk.content}"


class NeuralRetrieval:
    """One active model at a time. No implicit downloads, API fallback or precision changes."""

    def __init__(self, specs, cache_dir, *, device="cuda", batch_size=4, token=None, before_load=None):
        if device not in ("cpu", "cuda"):
            raise ValueError("device must be cpu or cuda")
        self.specs, self.cache_dir, self.device = specs, Path(cache_dir), device
        self.batch_size, self.token = batch_size, token
        self.before_load = before_load
        if not 1 <= batch_size <= 16:
            raise ValueError("invalid retrieval batch size")
        self.model, self.role = None, None
        self.tokenizers, self.verified = {}, set()
        self.stats = {"embedding_hits": 0, "embedding_new": 0, "rerank_hits": 0, "rerank_new": 0}
        self.identities = {
            role: {
                "model": spec,
                "device": device,
                "dtype": "float16" if device == "cuda" else "float32",
                "torch": importlib.metadata.version("torch"),
                "transformers": importlib.metadata.version("transformers"),
                "tokenizers": importlib.metadata.version("tokenizers"),
                "instruction": INSTRUCTION,
                "implementation": hashlib.sha256(
                    Path(__file__).read_bytes().replace(b"\r\n", b"\n")
                ).hexdigest(),
                "protocol": "last-token-l2-v1" if role == "embedding" else "qwen-yes-no-softmax-v1",
            }
            for role, spec in specs.items()
        }

    def identity(self, role):
        return self.identities[role]

    def model_path(self, role):
        spec = self.specs[role]
        return data_root() / "models" / (spec["repository"].split("/")[-1] + "-" + spec["revision"][:12])

    def tokenizer(self, role="embedding"):
        if role not in self.tokenizers:
            from transformers import AutoTokenizer

            for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
                if (
                    hashlib.sha256((self.model_path(role) / name).read_bytes()).hexdigest()
                    != self.specs[role]["sha256"][name]
                ):
                    raise ValueError("retrieval tokenizer/config hash mismatch")
            self.tokenizers[role] = AutoTokenizer.from_pretrained(
                self.model_path(role), local_files_only=True, trust_remote_code=False, padding_side="left"
            )
        return self.tokenizers[role]

    def _check(self):
        if self.token:
            self.token.check()

    def close(self):
        self.model, self.role = None, None
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # 切换 embedding 与 reranker 时先释放旧模型，权重只从已核验的本地路径加载。
    def _load(self, role):
        import torch
        from transformers import AutoModel, AutoModelForCausalLM

        self._check()
        if self.role == role:
            return self.model
        self.close()
        if self.before_load:
            self.before_load()
        if self.device == "cuda" and not torch.cuda.is_available():
            raise ValueError("configured CUDA retrieval is unavailable; explicitly select cpu to run on CPU")
        if role not in self.verified:
            digest = hashlib.sha256()
            with (self.model_path(role) / "model.safetensors").open("rb") as stream:
                for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != self.specs[role]["sha256"]["model.safetensors"]:
                raise ValueError("retrieval weights hash mismatch")
            self.verified.add(role)
        torch.manual_seed(42)
        loader = AutoModel if role == "embedding" else AutoModelForCausalLM
        self.model = (
            loader.from_pretrained(
                self.model_path(role),
                local_files_only=True,
                trust_remote_code=False,
                dtype=torch.float16 if self.device == "cuda" else torch.float32,
                attn_implementation="sdpa",
            )
            .to(self.device)
            .eval()
        )
        self.role = role
        return self.model

    def _cache_path(self, role, payload):
        key = fingerprint({"identity": self.identity(role), "input": payload})
        return self.cache_dir / role / (key + ".json"), key

    # 用查询指令编码问题，取最后一个有效位置的表示并做 L2 归一化，超限输入要求重新切块。
    def embed(self, texts, *, query=False):
        import numpy as np
        import torch

        inputs = [f"Instruct: {INSTRUCTION}\nQuery: {t}" if query else t for t in texts]
        results, missing = [None] * len(inputs), []
        tokenizer = self.tokenizer()
        for i, text in enumerate(inputs):
            self._check()
            path, key = self._cache_path("embedding", text)
            if path.exists():
                value = json.loads(path.read_text(encoding="utf-8"))
                if value["cache_key"] != key:
                    raise ValueError("embedding cache identity mismatch")
                results[i] = value["vector"]
                self.stats["embedding_hits"] += 1
            else:
                ids = tokenizer.encode(text, add_special_tokens=False)
                if len(ids) > 1100:
                    raise ValueError("embedding input exceeds 1100 tokens; rechunk instead of truncating")
                missing.append((i, path, key, ids))
        for start in range(0, len(missing), self.batch_size):
            self._check()
            batch = missing[start : start + self.batch_size]
            model = self._load("embedding")
            encoded = tokenizer.pad(
                {"input_ids": [row[3] for row in batch]}, padding=True, return_tensors="pt"
            ).to(self.device)
            begun = time.perf_counter()
            with torch.inference_mode():
                states = model(**encoded, use_cache=False).last_hidden_state[:, -1].float()
                # 归一化使不同文本向量处于同一尺度，后续 FAISS 可直接使用内积。
                vectors = torch.nn.functional.normalize(states, p=2, dim=1).cpu().numpy()
            elapsed = time.perf_counter() - begun
            for row, vector in zip(batch, vectors, strict=True):
                i, path, key, ids = row
                results[i] = vector.tolist()
                atomic_write(
                    path,
                    json.dumps(
                        {
                            "cache_key": key,
                            "vector": results[i],
                            "tokens": len(ids),
                            "batch_elapsed_seconds": elapsed,
                        }
                    ),
                )
                self.stats["embedding_new"] += 1
        matrix = np.asarray(results, dtype="float32")
        if len(inputs) and (
            matrix.shape != (len(inputs), 1024)
            or not np.isfinite(matrix).all()
            or not np.allclose(np.linalg.norm(matrix, axis=1), 1, atol=1e-4)
        ):
            raise ValueError("invalid normalized embedding")
        return matrix

    # 对问题和候选原文联合编码，取 yes/no 两个词的归一化概率作为相关性分数。
    def rerank(self, query, documents):
        import torch

        tokenizer = self.tokenizer("reranker")
        results, missing = [None] * len(documents), []
        for i, document in enumerate(documents):
            self._check()
            text = PREFIX + f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {document}" + SUFFIX
            path, key = self._cache_path("reranker", text)
            if path.exists():
                value = json.loads(path.read_text(encoding="utf-8"))
                if value["cache_key"] != key or not 0 <= value["score"] <= 1:
                    raise ValueError("reranker cache invalid")
                results[i] = value["score"]
                self.stats["rerank_hits"] += 1
            else:
                ids = tokenizer.encode(text, add_special_tokens=False)
                if len(ids) > 2048:
                    raise ValueError("reranker pair exceeds 2048 tokens; no silent truncation")
                missing.append((i, path, key, ids))
        for start in range(0, len(missing), self.batch_size):
            self._check()
            batch = missing[start : start + self.batch_size]
            model = self._load("reranker")
            encoded = tokenizer.pad(
                {"input_ids": [row[3] for row in batch]}, padding=True, return_tensors="pt"
            ).to(self.device)
            with torch.inference_mode():
                logits = model(**encoded, use_cache=False, logits_to_keep=1).logits[:, -1].float()
                values = logits[
                    :, [tokenizer.convert_tokens_to_ids("no"), tokenizer.convert_tokens_to_ids("yes")]
                ]
                # 只在 no / yes 两个候选词之间归一化，取 yes 的概率作为成对相关性。
                scores = torch.softmax(values, dim=1)[:, 1].cpu().tolist()
            if not all(0 <= score <= 1 for score in scores):
                raise ValueError("invalid reranker probability")
            for row, score in zip(batch, scores, strict=True):
                i, path, key, ids = row
                results[i] = score
                atomic_write(path, json.dumps({"cache_key": key, "score": score, "tokens": len(ids)}))
                self.stats["rerank_new"] += 1
        return results
