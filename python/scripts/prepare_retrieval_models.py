"""Download pinned public Qwen retrieval assets into Python-owned storage, then verify weights."""

import hashlib
import json
import os
from pathlib import Path

from repomentor.config import write_json
from repomentor.paths import data_root

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.environ["HF_HOME"] = str(data_root() / "hf-cache")
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download

    models = json.loads((ROOT / "experiments/p5/models.json").read_text(encoding="utf-8"))
    manifest = {}
    for role, spec in models.items():
        destination = (
            data_root() / "models" / (spec["repository"].split("/")[-1] + "-" + spec["revision"][:12])
        )
        snapshot_download(
            spec["repository"],
            revision=spec["revision"],
            allow_patterns=spec["files"],
            local_dir=destination,
            token=False,
            max_workers=2,
        )
        hashes = {}
        for name in spec["files"]:
            h = hashlib.sha256()
            with (destination / name).open("rb") as stream:
                for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    h.update(block)
            hashes[name] = h.hexdigest()
        if hashes["model.safetensors"] != spec["expected_weight_sha256"]:
            raise ValueError("downloaded weights differ from frozen upstream LFS hash")
        manifest[role] = {**spec, "sha256": hashes}
        write_json(ROOT / "experiments/p5/model-assets.json", manifest)
        print(json.dumps({"verified": role, "revision": spec["revision"]}), flush=True)
    destination = ROOT / "config/model-settings.retrieval.json"
    if not destination.exists():
        write_json(destination, manifest)


if __name__ == "__main__":
    main()
