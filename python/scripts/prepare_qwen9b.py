"""Verify/install the pinned 9B deployment without generating or rewriting experiments."""

import argparse
import hashlib
import json
from pathlib import Path

import httpx

from repomentor.config import load_chat_settings, write_json
from repomentor.paths import data_root, python_home

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:11435"


def prepare(*, download=False, transport=None):
    profile = load_chat_settings(ROOT / "config/model-settings.example.json")
    destination = python_home() / "config/model-settings.json"
    if destination.exists() and load_chat_settings(destination).public() != profile.public():
        raise ValueError("existing settings differ; refusing to overwrite another deployment")
    source = json.loads((ROOT / "experiments/qwen9b-diagnostic/download.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url=ORIGIN, timeout=600, trust_env=False, transport=transport) as client:
        version = client.get("/api/version").raise_for_status().json()["version"]
        if version != profile.server_version:
            raise ValueError("Ollama version differs from pinned deployment")

        def tags():
            return {m["name"]: m for m in client.get("/api/tags").raise_for_status().json()["models"]}

        models = tags()
        if source["source_model"] not in models:
            if not download:
                raise ValueError("9B weights missing; use --download for a new installation")
            with client.stream(
                "POST", "/api/pull", json={"model": source["source_model"], "stream": True}
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    event = json.loads(line)
                    if event.get("error"):
                        raise ValueError("9B download failed")
            models = tags()
        if models[source["source_model"]]["digest"] != source["source_digest"]:
            raise ValueError("source tag changed; do not silently change model identity")
        if profile.model not in models:
            client.post(
                "/api/create",
                json={
                    "model": profile.model,
                    "from": source["source_model"],
                    "parameters": source["parameters"],
                    "stream": False,
                },
            ).raise_for_status()
            models = tags()
        if models[profile.model]["digest"] != profile.model_revision:
            raise ValueError("9B alias digest differs from pinned configuration")

    record = json.loads((ROOT / "experiments/qwen9b-diagnostic/tokenizer.json").read_text(encoding="utf-8"))
    tokenizer = data_root() / "tokenizers/qwen35-9b/tokenizer.json"
    if tokenizer.exists():
        content = tokenizer.read_bytes()
    elif download:
        with httpx.Client(timeout=180, follow_redirects=True, transport=transport) as client:
            content = client.get(record["url"]).raise_for_status().content
    else:
        raise ValueError("9B tokenizer missing; use --download for a new installation")
    if (
        hashlib.sha256(content).hexdigest() != record["sha256"]
        or record["sha256"] != profile.tokenizer_sha256
    ):
        raise ValueError("9B tokenizer hash mismatch")
    if not tokenizer.exists():
        tokenizer.parent.mkdir(parents=True, exist_ok=True)
        tokenizer.write_bytes(content)
    if not destination.exists():
        write_json(destination, {**profile.public(), "apiKey": ""})
    deployment = {
        "model": profile.model,
        "config_id": profile.config_id,
        "source_digest": source["source_digest"],
        "configuration": profile.public(),
        "generation_requests": 0,
        "paid_requests": 0,
    }
    write_json(data_root() / "deployment/qwen9b.json", deployment)
    print(json.dumps(deployment, ensure_ascii=False))
    return deployment


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="allow missing model/tokenizer downloads")
    prepare(download=parser.parse_args().download)
