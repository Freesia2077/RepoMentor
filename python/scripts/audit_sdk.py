"""Audit SDK recordings offline; archive pilots and count unique inference attempts honestly."""

import hashlib
import json
from pathlib import Path

from repomentor.paths import data_root
from repomentor.rag import citation_validity, verify_spans
from repomentor.records import write
from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p6-sdk"


def rows(value):
    if isinstance(value, dict):
        if value.get("schema_version") == "repomentor.agent.sdk.v1":
            yield value
        else:
            for child in value.values():
                yield from rows(child)
    elif isinstance(value, list):
        for child in value:
            yield from rows(child)


# Failed pilots are evidence too. Copy named outputs without overwriting earlier archives.
for name in ("pilot-strict", "pilot-sdk-validation", "pilot-sdk-validation-online"):
    source = data_root() / "p6-sdk" / (name + ".json")
    target = OUT / "pilots" / source.name
    if source.exists() and not target.exists():
        write(target, json.loads(source.read_text(encoding="utf-8")))

# One early conversation script stopped at its failed replay check, after its attempt was saved.
source_root = data_root() / "runtime/sdk-validation-conversations"
if source_root.exists():
    for session in source_root.glob("*.json"):
        state = json.loads(session.read_text(encoding="utf-8"))
        pending_id = (state.get("pending") or {}).get("last_attempt_id")
        if pending_id:
            sources = list((data_root() / "runtime/sdk/cache").glob(f"*/attempts/{pending_id}.json"))
            target = OUT / "pilots" / ("pending-" + pending_id + ".json")
            if sources and not target.exists():
                write(target, json.loads(sources[0].read_text(encoding="utf-8")))

attempts, files = {}, {}
for path in sorted(OUT.rglob("*.json")):
    if path.name in ("audit.json", "runtime-versions.json"):
        continue
    value = json.loads(path.read_text(encoding="utf-8"))
    found = list(rows(value))
    if not found:
        continue
    files[path.relative_to(OUT).as_posix()] = {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "stops": [r["stop_reason"] for r in found],
        "new_local_requests": sum(r["new_model_requests"] for r in found),
    }
    for row in found:
        previous = attempts.get(row["attempt_id"])
        if previous is None or row["new_model_requests"] > previous["new_model_requests"]:
            attempts[row["attempt_id"]] = row
        reader = SnapshotStore(data_root() / "corpus").open(row["snapshot_id"])
        verify_spans(reader, row["context"])
        if row["completion"]["status"] == "success":
            assert citation_validity(row["answer"], row["context"])
        for decision in row["decisions"]:
            if decision.get("request_sent") and "request" in decision:
                assert decision["input_budget"]["fits"]
                assert not decision["request"].get("stream", False)
                assert "apiKey" not in row["config"]
            if "usage" in decision and row["config"].get("tokenizer_sha256"):
                assert decision["usage"]["prompt_tokens"] <= decision["input_budget"]["input_tokens"]

protected = json.loads((ROOT / "experiments/p6/protocol.json").read_text(encoding="utf-8"))["protected_files"]
assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in protected.items())
p6_files = json.loads((ROOT / "experiments/p6/artifact-hashes.json").read_text(encoding="utf-8"))
assert all(hashlib.sha256((ROOT / "experiments/p6" / name).read_bytes()).hexdigest() == digest
           for name, digest in p6_files.items())
record = {
    "schema_version": "repomentor.sdk.audit.v1", "files": files,
    "unique_attempts": len(attempts), "new_local_requests": sum(r["new_model_requests"] for r in attempts.values()),
    "paid_requests": 0, "protected_p2_p5_files_unchanged": len(protected),
    "protected_p6_files_unchanged": len(p6_files),
    "split": "validation", "human_approved": False, "semantic_correctness": "not_scored",
    "notes": ["Counts include failed pilots and exclude duplicate cache replays.",
              "Compatibility smoke tests are not a model-quality benchmark.",
              "Discarded JSON/tool-choice pilots and the final two-stage SDK workflow have different runtime hashes."],
}
write(OUT / "audit.json", record)
print(json.dumps({k: v for k, v in record.items() if k != "files"}, ensure_ascii=False))
