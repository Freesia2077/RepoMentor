"""Explicit localhost-only probes; preserve outputs and sampled device memory, reuse by default."""

import argparse
import asyncio
import json
import platform
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from repomentor.config import load_chat_settings, write_json
from repomentor.runtimes.chat import ChatCompletionsRuntime

ROOT = Path(__file__).resolve().parents[1]
TEXT = [{"role": "user", "content": "用中文列出阅读陌生代码仓库时的五个步骤，每步一句话。"}]
TOOL_MESSAGES = [
    {"role": "system", "content": "你是仓库学习助手。根据用户要求调用工具，不要自己编造文件内容。"},
    {"role": "user", "content": "请调用 read_file，读取 src/index.ts 的第 1 到 20 行。"},
]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取固定仓库快照中的指定文件行段。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    }
]


def gpu_query(fields):
    result = subprocess.run(
        ["nvidia-smi", "-i", "0", "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return [part.strip() for part in result.stdout.strip().split(",")]


class MemorySampler:
    def __init__(self):
        self.samples = []
        self.errors = []
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                used, utilization = gpu_query("memory.used,utilization.gpu")
                self.samples.append(
                    {
                        "seconds": round(time.monotonic() - self.started, 3),
                        "device_memory_mib": int(used),
                        "gpu_utilization_percent": int(utilization),
                    }
                )
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                self.errors.append(type(exc).__name__)
            self.stop.wait(0.5)


async def benchmark(args):
    settings = load_chat_settings(args.settings)
    origin = settings.baseUrl.rstrip("/").removesuffix("/v1")
    if settings.backend != "ollama" or urlparse(origin).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("resource benchmark only permits local Ollama")
    if args.output.exists() and not args.refresh:
        print(json.dumps({"reused": str(args.output), "new_requests": 0}))
        return
    runtime = ChatCompletionsRuntime(settings)
    verification = await runtime.verify_backend()
    hardware = gpu_query("name,driver_version,memory.total")
    async with httpx.AsyncClient(base_url=origin, timeout=180, trust_env=False) as client:
        # Unload only our named model. OS page cache and CUDA initialization may remain warm.
        (
            await client.post("/api/generate", json={"model": settings.model, "keep_alive": 0})
        ).raise_for_status()
        resident_before = (await client.get("/api/ps")).raise_for_status().json()
        baseline = int(gpu_query("memory.used")[0])
        record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "gpu": dict(zip(("name", "driver", "memory_total_mib"), hardware)),
            "configuration": settings.public(),
            "config_id": settings.config_id,
            "backend_verification": verification,
            "device_memory_before_mib": baseline,
            "resident_before": resident_before,
            "probes": [],
            "notes": [
                "Cold means model unloaded; OS file cache may be warm. No process restart or disk-cold claim.",
                "Device VRAM includes desktop/other processes. Sampled peaks can miss brief spikes.",
                "TTFT is time to the first received nonempty text/tool delta, not GPU decode time.",
                "Client token rate approximates (completion_tokens - 1) / (elapsed - TTFT); includes transport overhead.",
                "Nonstream responses do not expose TTFT. Tools are returned but not executed in P3.",
                "Tool arguments may be buffered by the server; no decode token rate is inferred for tool probes.",
                "Repeated prompts may reuse Ollama prompt cache. These are diagnostic probes, not a workload average.",
            ],
        }
        sampler = MemorySampler()
        sampler.thread.start()
        try:
            probes = [
                ("text_stream_cold", settings, TEXT, None),
                ("text_stream_warm", settings, TEXT, None),
                ("text_nonstream_warm", settings.model_copy(update={"stream": False}), TEXT, None),
                ("tool_stream", settings, TOOL_MESSAGES, TOOLS),
                ("tool_nonstream", settings.model_copy(update={"stream": False}), TOOL_MESSAGES, TOOLS),
                ("output_limit", settings.model_copy(update={"max_tokens": 8}), TEXT, None),
            ]
            for name, profile, messages, tools in probes:
                result = await ChatCompletionsRuntime(profile).complete(messages, tools=tools)
                ttft = result.time_to_first_token_seconds
                tokens = result.usage.get("completion_tokens")
                rate = (
                    round((tokens - 1) / (result.elapsed_seconds - ttft), 2)
                    if not tools and tokens and tokens > 1 and ttft and result.elapsed_seconds > ttft
                    else None
                )
                row = {
                    "probe": name,
                    "config_id": profile.config_id,
                    "configuration": profile.public(),
                    "messages": messages,
                    "tools": tools,
                    "result": result.model_dump(mode="json"),
                    "client_decode_tokens_per_second": rate,
                    "resident_after": (await client.get("/api/ps")).raise_for_status().json(),
                }
                record["probes"].append(row)
                write_json(args.output, record)
                print(
                    json.dumps({"probe": name, "status": result.status, "seconds": result.elapsed_seconds}),
                    flush=True,
                )
        finally:
            sampler.stop.set()
            sampler.thread.join(timeout=12)
            peak = max((row["device_memory_mib"] for row in sampler.samples), default=baseline)
            record.update(
                {
                    "device_memory_peak_mib": peak,
                    "device_memory_delta_mib": peak - baseline,
                    "memory_samples": sampler.samples,
                    "sampling_errors": sampler.errors,
                    "local_generation_requests": len(record["probes"]),
                    "paid_api_calls": 0,
                }
            )
            write_json(args.output, record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=ROOT / "config/model-settings.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/local-benchmark.json")
    parser.add_argument("--refresh", action="store_true")
    asyncio.run(benchmark(parser.parse_args()))
