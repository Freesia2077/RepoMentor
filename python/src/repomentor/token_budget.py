"""Pinned, text-only Qwen 3.5 / Ollama 0.34.3 prompt accounting (thinking disabled)."""

# 根据固定 tokenizer 与服务模板估算完整请求预算，同时预留输出和模板空间。
# 工具路径使用带余量的估计，响应时继续核对服务端实际 prompt 用量。

import hashlib
from functools import lru_cache
from pathlib import Path

from .paths import data_root


# 先确定数据目录再按路径和哈希缓存，防止不同安装目录误用同一 tokenizer 对象。
def load_tokenizer(sha256: str):
    path = data_root() / "tokenizers/qwen35-9b/tokenizer.json"
    return _load_tokenizer(path, sha256)


@lru_cache(maxsize=4)
def _load_tokenizer(path: Path, sha256: str):
    from tokenizers import Tokenizer

    if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
        raise ValueError("tokenizer hash differs from model configuration")
    return Tokenizer.from_file(str(path))


def render_text_messages(messages):
    """Limited renderer; tool/image/thinking paths deliberately use the conservative fallback."""
    if not messages or messages[-1]["role"] != "user":
        raise ValueError("text budget expects a final user message")
    parts = []
    for message in messages:
        if set(message) != {"role", "content"} or message["role"] not in ("system", "user", "assistant"):
            raise ValueError("unsupported text message")
        content = message["content"].strip()
        if message["role"] == "assistant" and "</think>" in content:
            content = content.split("</think>", 1)[1].lstrip("\n")
        parts.append("<|im_start|>" + message["role"] + "\n" + content + "<|im_end|>\n")
    return "".join(parts) + "<|im_start|>assistant\n<think>\n\n</think>\n\n"


# 总预算等于输入估计、最大输出和模板预留；工具路径另留包装余量并核验服务端用量。
def input_budget(settings, messages, tools=None, response_schema=None):
    import json

    byte_count = sum(
        len(json.dumps(value, ensure_ascii=False).encode())
        for value in (messages, tools or [], response_schema or {})
    )
    mode, prompt_tokens = "utf8_upper_bound", byte_count
    if settings.tokenizer_sha256 and tools:
        if (
            settings.backend != "ollama"
            or settings.server_version != "0.34.3"
            or settings.reasoning_effort != "none"
        ):
            raise ValueError("tool budget is calibrated only for pinned Ollama/Qwen with thinking off")
        tokenizer = load_tokenizer(settings.tokenizer_sha256)
        # 对完整请求 JSON 分词，再为服务端工具前言、XML 和消息包装预留余量。
        # 该值用于保守准入；响应后还需核对服务端实际用量，超出预留就显式报错。
        wire = json.dumps([messages, tools], ensure_ascii=False)
        prompt_tokens = len(tokenizer.encode(wire, add_special_tokens=False).ids) + 768 + 64 * len(messages)
        mode = "qwen35_ollama_tools_padded_v1"
    if settings.tokenizer_sha256 and not tools:
        if (
            settings.backend != "ollama"
            or settings.server_version != "0.34.3"
            or settings.reasoning_effort != "none"
        ):
            raise ValueError("pinned text tokenizer requires Ollama 0.34.3 with thinking off")
        tokenizer = load_tokenizer(settings.tokenizer_sha256)
        prompt_tokens = len(tokenizer.encode(render_text_messages(messages), add_special_tokens=False).ids)
        mode = "qwen35_ollama_text_v1"
    # Ollama 用 JSON schema 约束解码，此路径只计算实际文本提示的 token。
    total = prompt_tokens + settings.max_tokens + settings.template_reserve
    return {
        "mode": mode,
        "input_tokens": prompt_tokens,
        "output_allowance": settings.max_tokens,
        "reserve": settings.template_reserve,
        "total": total,
        "context_window": settings.context_window,
        "fits": total <= settings.context_window,
    }
