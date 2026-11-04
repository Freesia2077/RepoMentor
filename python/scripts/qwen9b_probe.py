"""Isolated Qwen3.5-9B deployment and bounded development diagnostics."""

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import httpx

from repomentor.config import load_chat_settings, write_json
from repomentor.paths import data_root, runtime_dir
from repomentor.runtimes.chat import fingerprint

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/qwen9b-diagnostic"
ORIGIN = "http://127.0.0.1:11435"
SOURCE = "qwen3.5:9b-q4_K_M"
PROFILE = ROOT / "config/model-settings.qwen35-9b-16k.json"
BASELINE = ROOT / "config/model-settings.qwen-local.json"
PREFIX = "qwen9b-diagnostic"
CAP = 72
TASKS = ["history-dev-03", "history-dev-04", "history-dev-05", "history-dev-06",
         "sweqa-requests-001", "sweqa-flask-002"]
REFERENCES = {
    "history-dev-03": [("non-secure/index.js", 19, 29), ("index.js", 133, 138), ("index.js", 177, 177)],
    "history-dev-04": [("src/index.ts", 66, 73), ("test/index_test.ts", 79, 90)],
    "history-dev-05": [("schedule/__init__.py", 122, 133), ("schedule/__init__.py", 864, 868)],
    "history-dev-06": [],
    "sweqa-requests-001": [("src/requests/utils.py", 755, 825), ("tests/test_utils.py", 257, 272)],
    "sweqa-flask-002": [("src/flask/sansio/blueprints.py", 172, 172),
                         ("src/flask/sansio/blueprints.py", 213, 230),
                         ("src/flask/sansio/blueprints.py", 316, 335),
                         ("src/flask/sansio/scaffold.py", 42, 49)],
}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def freeze(path, value):
    if Path(path).exists():
        if load(path) != value:
            raise ValueError(f"frozen diagnostic identity changed: {path}")
    else:
        write_json(Path(path), value)


def prepare():
    """Pull weights once; keep the owner's active model configuration unchanged."""
    baseline = load_chat_settings(BASELINE)
    if (baseline.backend, baseline.context_window, baseline.max_tokens) != ("ollama", 16384, 1536):
        raise ValueError("expected the frozen local 4B / 16K baseline")
    with httpx.Client(base_url=ORIGIN, timeout=600, trust_env=False) as client:
        version = client.get("/api/version").raise_for_status().json()["version"]
        if version != baseline.server_version:
            raise ValueError("server version changed")
        tags = client.get("/api/tags").raise_for_status().json()["models"]
        source = next((m for m in tags if m["name"] == SOURCE), None)
        if source is None:
            last = None
            with client.stream("POST", "/api/pull", json={"model": SOURCE, "stream": True}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    row = json.loads(line)
                    if row.get("error"):
                        raise RuntimeError(row["error"])
                    percent = int(10 * row.get("completed", 0) / row["total"]) * 10 if row.get("total") else None
                    progress = (row.get("status"), percent)
                    if progress != last:
                        print(json.dumps({"download": progress}), flush=True)
                        last = progress
            tags = client.get("/api/tags").raise_for_status().json()["models"]
            source = next(m for m in tags if m["name"] == SOURCE)
        previous = OUT / "deployment.json"
        if previous.exists() and load(previous)["source_digest"] != source["digest"]:
            raise ValueError("download tag changed; preserve the previous deployment")
        show = client.post("/api/show", json={"model": SOURCE}).raise_for_status().json()
        if show["details"]["quantization_level"] != "Q4_K_M":
            raise ValueError("unexpected quantization")
        alias = "repomentor-qwen35-9b-16k-" + source["digest"].removeprefix("sha256:")[:12] + ":latest"
        parameters = {"num_ctx": 16384, "num_predict": 1536, "temperature": baseline.temperature,
                      "top_p": baseline.top_p, "seed": baseline.seed, "presence_penalty": 0}
        if not any(m["name"] == alias for m in tags):
            created = client.post("/api/create", json={"model": alias, "from": SOURCE,
                "parameters": parameters, "stream": False}).raise_for_status().json()
            if created.get("status") != "success":
                raise ValueError("candidate alias was not created")
        tags = client.get("/api/tags").raise_for_status().json()["models"]
        candidate = next(m for m in tags if m["name"] == alias)
        freeze(OUT / "download.json", {"source_model": SOURCE, "source_digest": source["digest"],
            "source_size_bytes": source["size"], "alias": alias, "alias_digest": candidate["digest"],
            "server_version": version, "details": show["details"], "parameters": parameters,
            "capabilities": show.get("capabilities"), "model_info": show.get("model_info", {}),
            "template_sha256": hashlib.sha256(show.get("template", "").encode()).hexdigest(),
            "generation_requests": 0})
        print(json.dumps({"download_complete": True, "alias": alias}), flush=True)
    token_record = prepare_tokenizer(baseline)
    profile = baseline.model_copy(update={"model": alias, "model_revision": candidate["digest"]})
    if PROFILE.exists() and load_chat_settings(PROFILE).public() != profile.public():
        raise ValueError("candidate settings changed")
    if not PROFILE.exists():
        write_json(PROFILE, profile.public())
    freeze(OUT / "deployment.json", {"source_model": SOURCE, "source_digest": source["digest"],
        "source_size_bytes": source["size"], "configuration": profile.public(), "config_id": profile.config_id,
        "tokenizer": token_record, "tokenizer_identical_to_baseline": True,
        "baseline_config_id": baseline.config_id, "baseline_public_sha256": fingerprint(baseline.public()),
        "promoted": False, "paid_requests": 0, "training_eligible": False})
    print(json.dumps({"prepared": True, "config_id": profile.config_id, "promoted": False}), flush=True)


def prepare_tokenizer(baseline=None):
    """Reuse calibration only after checking the 9B publisher's pinned tokenizer."""
    baseline = baseline or load_chat_settings(BASELINE)
    tokenizer_file = data_root() / "tokenizers/qwen35-9b/tokenizer.json"
    tokenizer_record = OUT / "tokenizer.json"
    if tokenizer_record.exists():
        token_record = load(tokenizer_record)
        if not tokenizer_file.exists():
            cached = data_root() / "tokenizers/qwen35-4b/tokenizer.json"
            content = cached.read_bytes()
            if hashlib.sha256(content).hexdigest() != token_record["sha256"]:
                raise ValueError("shared tokenizer does not match the 9B publisher's recorded hash")
            tokenizer_file.parent.mkdir(parents=True, exist_ok=True)
            tokenizer_file.write_bytes(content)
        if hashlib.sha256(tokenizer_file.read_bytes()).hexdigest() != token_record["sha256"]:
            raise ValueError("9B tokenizer cache changed")
    else:
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            metadata = client.get("https://huggingface.co/api/models/Qwen/Qwen3.5-9B").raise_for_status().json()
            revision = metadata["sha"]
            url = f"https://huggingface.co/Qwen/Qwen3.5-9B/resolve/{revision}/tokenizer.json"
            content = client.get(url).raise_for_status().content
        tokenizer_file.parent.mkdir(parents=True, exist_ok=True)
        tokenizer_file.write_bytes(content)
        token_record = {"repository": "Qwen/Qwen3.5-9B", "revision": revision,
                        "sha256": hashlib.sha256(content).hexdigest(), "url": url}
        freeze(tokenizer_record, token_record)
    if token_record["sha256"] != baseline.tokenizer_sha256:
        raise ValueError("9B tokenizer differs; do not reuse the calibrated 4B tokenizer implicitly")
    print(json.dumps({"tokenizer_verified": True, "sha256": token_record["sha256"]}), flush=True)
    return token_record


def profiles():
    return {"4b": load_chat_settings(BASELINE), "9b": load_chat_settings(PROFILE)}


def book():
    from repomentor.experiment_ledger import RequestLedger

    return RequestLedger(runtime_dir() / "p63/dispatches.sqlite", limit=600,
        experiment=fingerprint(load(ROOT / "experiments/p63/protocol.json")), context_windows=(16384,),
        batch_prefix=PREFIX, batch_limit=CAP)


def limits():
    from repomentor.agent_tools import AgentLimits

    return AgentLimits(max_decisions=4, max_tool_calls=4, timeout_seconds=240)


def open_task(task):
    from repomentor.repository import SnapshotStore

    reader = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    if reader.snapshot.commit_sha != task["commit_sha"]:
        raise ValueError("snapshot revision changed")
    return reader


def open_index(reader):
    from repomentor.retrieval_factory import build_index

    return build_index(reader, chunker="syntax", retriever="bm25", device="cpu", storage=runtime_dir())


def source_spans(reader, refs):
    from repomentor.rag import verify_spans

    rows = []
    for path, start, end in refs:
        text, truncated = reader.read(path, 500_000)
        if truncated:
            raise ValueError("reference source was truncated")
        content = "\n".join(text.splitlines()[start - 1:end])
        rows.append({"path": path, "start_line": start, "end_line": end, "content": content,
                     "content_sha256": hashlib.sha256(content.encode()).hexdigest()})
    verify_spans(reader, rows)
    return rows


def protocol():
    from repomentor.entry_scope import resolve_entry
    from repomentor.sdk_agent import sdk_version

    chosen = {t["task_id"]: t for t in load(ROOT / "experiments/p63/development.json")}
    tasks = [chosen[name] for name in TASKS]
    settings = profiles()
    other = {k: v for k, v in settings["4b"].public().items() if k not in {"model", "model_revision"}}
    if other != {k: v for k, v in settings["9b"].public().items() if k not in {"model", "model_revision"}}:
        raise ValueError("model-only comparison requires otherwise identical profiles")
    initial, oracle, indexes = {}, {}, {}
    for task in tasks:
        reader = open_task(task)
        index = open_index(reader)
        query = task["input"]["question"]
        focus = resolve_entry(reader, query)
        initial[task["task_id"]] = index.initial_sources(query, excluded=focus["excluded_paths"])
        oracle[task["task_id"]] = source_spans(reader, REFERENCES[task["task_id"]])
        indexes[task["task_id"]] = index.index_id
    freeze(OUT / "tasks.json", tasks)
    freeze(OUT / "initial-retrieval.json", initial)
    freeze(OUT / "evaluation-only/oracle.json", oracle)
    checklist = load(OUT / "evaluation-only/answer-checklist.json")
    value = {"version": "qwen9b-diagnostic-v1", "sdk": sdk_version(),
        "profiles": {k: v.public() for k, v in settings.items()}, "limits": limits().model_dump(),
        "retriever": "bm25", "chunker": "syntax", "device": "cpu", "index_ids": indexes,
        "tasks_sha256": fingerprint(tasks), "initial_sha256": fingerprint(initial),
        "oracle_sha256": fingerprint(oracle), "conditions": ["sdk", "oracle"],
        "review_checklist_sha256": fingerprint(checklist),
        "smoke": "SDK read_code with no initial source; checks tools and configured 16K residency",
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "batch_cap": CAP, "global_cap": 600, "paid_requests": 0, "automatic_retry": False,
        "training_eligible": False, "split": "selected development", "human_approved": False,
        "purpose": "Model capacity and evidence diagnostics, not a fresh confirmation or a pure parameter-count ablation",
        "limits_note": "Both evidence conditions use the same SDK workflow, tools and budgets; oracle replaces only "
                       "the initial source snippets. CPU BM25 is shared across both models; do not pool with historical hybrid/CUDA results."}
    freeze(OUT / "protocol.json", value)
    return value


class FrozenInitialIndex:
    """Both models start from identical saved retrieval; later tools still use the real CPU index."""

    def __init__(self, base, initial):
        self.base, self.initial = base, initial

    def __getattr__(self, name):
        return getattr(self.base, name)

    def initial_sources(self, question, *, excluded=()):
        sources = self.initial["sources"]
        if any(s["path"] in excluded for s in sources):
            raise ValueError("frozen initial retrieval violates entry scope")
        return copy.deepcopy(self.initial)


def make_runtime(profile, name, condition, replay=False):
    from repomentor.runtimes.openai_sdk import OpenAISdkRuntime

    def before(body, budget):
        if replay:
            raise AssertionError("offline replay forbids generation")
        book().reserve(profile, PREFIX + "/" + name, body, budget)

    class Sdk(OpenAISdkRuntime):
        async def verify_backend(self):
            if replay:
                raise AssertionError("offline replay forbids metadata")
            return await super().verify_backend()

    return Sdk(profile, before_dispatch=before)


def save_case(name, row):
    from repomentor.presentation import render_run

    freeze(OUT / "cases" / (name + ".json"), row)
    target = OUT / "markdown" / (name + ".md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_run(row), encoding="utf-8")
    print(json.dumps({"case": name, "status": row["result"]["completion"]["status"],
        "requests": row["result"]["new_model_requests"], "tools": row["result"].get("tool_calls", 0)}), flush=True)


async def smoke():
    from benchmark_local_qwen import MemorySampler

    from repomentor.sdk_agent import SdkAgentEngine

    protocol()
    path = OUT / "smoke.json"
    if path.exists():
        print(json.dumps({"smoke": "saved", "accepted": load(path)["accepted"]}), flush=True)
        return
    task = next(t for t in load(OUT / "tasks.json") if t["task_id"] == "history-dev-04")
    reader = open_task(task)
    index = FrozenInitialIndex(open_index(reader), {"sources": [], "retrieval": {}, "hits": []})
    question = "先调用 read_code 读取 src/index.ts 第 76–92 行，再说明 off 不传 handler 是否删除 Map 的键。请给出实际行号。"
    profile = profiles()["9b"]
    engine = SdkAgentEngine(reader, index, make_runtime(profile, "smoke", "sdk"),
                            runtime_dir() / PREFIX / "sdk-cache", limits())
    sampler = MemorySampler()
    sampler.thread.start()
    try:
        result = await engine.answer(question)
        async with httpx.AsyncClient(base_url=ORIGIN, trust_env=False, timeout=30) as client:
            resident = (await client.get("/api/ps")).raise_for_status().json()
    finally:
        sampler.stop.set()
        sampler.thread.join(timeout=12)
    found = next((m for m in resident.get("models", []) if m["name"] == profile.model), {})
    successful_reads = [step for step in result["tool_trace"] if step["action"]["tool"] == "read_code"
                        and step["observation"]["status"] == "ok" and step["source_spans"]]
    row = {"result": result, "resident": resident, "memory_samples": sampler.samples,
        "memory_sample_errors": sampler.errors, "context_verified": found.get("context_length") == 16384,
        "peak_device_mib": max((s["device_memory_mib"] for s in sampler.samples), default=None),
        "accepted": result["completion"]["status"] == "success" and bool(successful_reads)
                    and found.get("context_length") == 16384,
        "semantic_correctness": "not_scored", "full_window_quality_claim": False,
        "note": "Short real tool episode with a 16K allocated context; not a 16K semantic benchmark."}
    freeze(path, row)
    save_case("smoke", {"snapshot_id": task["snapshot_id"], "question": question, "result": result,
                        "training_eligible": False})
    book().export(OUT / "dispatches.json")
    print(json.dumps({"accepted": row["accepted"], "resident": found,
                      "peak_device_mib": row["peak_device_mib"]}), flush=True)


async def run(args):
    from repomentor.sdk_agent import SdkAgentEngine

    protocol()
    if not load(OUT / "smoke.json")["accepted"]:
        raise ValueError("9B load/tool smoke check failed; preserve failure and stop")
    initial, oracle = load(OUT / "initial-retrieval.json"), load(OUT / "evaluation-only/oracle.json")
    receipts = []
    for model, profile in profiles().items():
        if args.model != "both" and args.model != model:
            continue
        for task in load(OUT / "tasks.json"):
            reader = open_task(task)
            base = open_index(reader)
            for condition in ("sdk", "oracle"):
                name = model + "--" + task["task_id"] + "--" + condition
                path = OUT / "cases" / (name + ".json")
                if path.exists() and not args.replay:
                    print(json.dumps({"case": name, "saved": True}), flush=True)
                    continue
                runtime = make_runtime(profile, name, condition, args.replay)
                seeds = initial[task["task_id"]] if condition == "sdk" else {
                    "sources": oracle[task["task_id"]], "retrieval": {}, "hits": []}
                index = FrozenInitialIndex(base, seeds)
                engine = SdkAgentEngine(reader, index, runtime, runtime_dir() / PREFIX / "sdk-cache", limits())
                result = await engine.answer(task["input"]["question"])
                row = {**task, "model": model, "condition": condition, "mode": condition,
                       "training_eligible": False, "result": result}
                if args.replay:
                    previous = load(path)["result"]
                    if result["new_model_requests"] != 0 or result["attempt_id"] != previous["attempt_id"]:
                        raise AssertionError("replay did not recover the original attempt")
                    receipts.append(name)
                else:
                    save_case(name, row)
                    book().export(OUT / "dispatches.json")
    if args.replay:
        write_json(OUT / "replay.json", {"cases": receipts, "generation_requests": 0, "metadata_requests": 0})


async def offline_replay(args):
    from unittest.mock import patch

    import httpx2

    attempts = []

    def refuse(*a, **kw):
        attempts.append("sync")
        raise AssertionError("strict diagnostic replay forbids HTTP")

    async def refuse_async(*a, **kw):
        attempts.append("async")
        raise AssertionError("strict diagnostic replay forbids HTTP")

    with (patch.object(httpx.Client, "send", refuse), patch.object(httpx.AsyncClient, "send", refuse_async),
          patch.object(httpx2.Client, "send", refuse), patch.object(httpx2.AsyncClient, "send", refuse_async)):
        await run(args)
    if attempts:
        raise AssertionError("replay attempted network access")
    write_json(OUT / "network-free-replay.json", {**load(OUT / "replay.json"), "http_send_attempts": 0})


def export_review():
    candidates = []
    for path in sorted((OUT / "cases").glob("*.json")):
        case = load(path)
        if path.stem == "smoke":
            continue
        result = case["result"]
        candidates.append({"candidate_id": path.stem, "source_file": path.relative_to(ROOT).as_posix(),
            "snapshot_id": case["snapshot_id"], "task_id": case["task_id"], "model": case["model"],
            "condition": case["condition"], "question": result["question"], "answer": result["answer"],
            "status": result["completion"]["status"], "record_sha256": fingerprint(result),
            "observed_context": result.get("final_context", result["context"])})
    write_json(OUT / "review-candidates.json", candidates)
    print(json.dumps({"review_candidates": len(candidates)}))


def summarize():
    from collections import Counter, defaultdict

    from repomentor.semantic_review import validate_judgment

    decisions, groups, checked = load(OUT / "review-decisions.json"), defaultdict(list), []
    candidates = load(OUT / "review-candidates.json")
    if set(decisions) != {c["candidate_id"] for c in candidates}:
        raise ValueError("review coverage differs from generated candidates")
    for candidate in candidates:
        case = load(ROOT / candidate["source_file"])
        result = case["result"]
        if fingerprint(result) != candidate["record_sha256"]:
            raise ValueError("answer changed after review export")
        judgment = validate_judgment({"candidate_id": candidate["candidate_id"],
            **decisions[candidate["candidate_id"]]}, candidate, open_task(case))
        judgment["solved"] = (candidate["status"] == "success" and judgment["correctness"] == "correct"
            and judgment["completeness"] == "adequate" and judgment["citation_support"] in {"supported", "not_applicable"}
            and judgment["abstention"] != "inappropriate" and judgment["consistency"] == "consistent")
        checked.append(judgment)
        groups[candidate["model"] + "/" + candidate["condition"]].append((candidate, judgment, result))
    stats = {}
    for group, rows in groups.items():
        stats[group] = {"tasks": len(rows), "solved": sum(j["solved"] for _, j, _ in rows),
            "correctness": dict(Counter(j["correctness"] for _, j, _ in rows)),
            "citation_support": dict(Counter(j["citation_support"] for _, j, _ in rows)),
            "completeness": dict(Counter(j["completeness"] for _, j, _ in rows)),
            "model_requests": sum(r.get("total_model_requests", r["new_model_requests"]) for _, _, r in rows),
            "cases_using_tools": sum(r.get("tool_calls", 0) > 0 for _, _, r in rows),
            "recorded_seconds": sum(r.get("elapsed_seconds", r["completion"]["elapsed_seconds"]) for _, _, r in rows)}
    write_json(OUT / "judgments.json", checked)
    write_json(OUT / "summary.json", {"conditions": stats, "reviewer": "Codex nonblind model review",
        "human_approved": False, "training_eligible": False, "paid_requests": 0,
        "limitations": "Six selected development tasks; CPU BM25; not confirmation or an unbiased accuracy estimate. "
                       "Both conditions use SDK; oracle provides manually selected source, not reference answer prose. "
                       "Timings are not pure decode speeds."})
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def audit():
    from repomentor.rag import verify_spans
    from repomentor.sdk_agent import sdk_version

    identity = load(OUT / "protocol.json")
    if sdk_version() != identity["sdk"] or {k: p.public() for k, p in profiles().items()} != identity["profiles"]:
        raise ValueError("diagnostic model or runtime changed")
    if hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest() != identity["runner_sha256"]:
        raise ValueError("diagnostic runner changed")
    cases = [load(p) for p in sorted((OUT / "cases").glob("*.json")) if p.stem != "smoke"]
    for case in cases:
        result, reader = case["result"], open_task(case)
        for spans in (result["context"], result.get("final_context", [])):
            verify_spans(reader, spans)
        calls = result.get("decisions") or [result["completion"]]
        for call in calls:
            if call.get("request_sent") and not (call["input_budget"]["fits"] and call["input_budget"]["total"] <= 16384):
                raise ValueError("generation exceeded the total context budget")
        if case["condition"] == "sdk" and result["initial_retrieval"] != load(OUT / "initial-retrieval.json")[case["task_id"]]:
            raise ValueError("paired models received different initial retrieval")
        if case["condition"] == "oracle" and result["initial_retrieval"]["sources"] != load(OUT / "evaluation-only/oracle.json")[case["task_id"]]:
            raise ValueError("paired models received different oracle source")
    frozen_inputs = {"tasks.json": "tasks_sha256", "initial-retrieval.json": "initial_sha256",
                     "evaluation-only/oracle.json": "oracle_sha256",
                     "evaluation-only/answer-checklist.json": "review_checklist_sha256"}
    for path, key in frozen_inputs.items():
        if fingerprint(load(OUT / path)) != identity[key]:
            raise ValueError("frozen diagnostic input changed: " + path)
    protected = load(ROOT / "experiments/p63-closeout/completed-files.json")["sha256"]
    for name, expected in protected.items():
        if hashlib.sha256((ROOT / "experiments/p63-closeout" / name).read_bytes()).hexdigest() != expected:
            raise ValueError("historical closeout artifact changed: " + name)
    original = load(ROOT / "experiments/p63/protocol.json")["protected_files"]
    for name, expected in original.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("historical experiment artifact changed: " + name)
    frozen_p63 = load(ROOT / "experiments/p63/completed-files.json")["sha256"]
    for name, expected in frozen_p63.items():
        if hashlib.sha256((ROOT / "experiments/p63" / name).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen P6.3 artifact changed: " + name)
    count = sum(r["case"].startswith(PREFIX + "/") for r in book().rows())
    row = {"cases": len(cases), "initial_contexts_equal": True, "source_and_budget_checks_passed": True,
        "protected_closeout_files": len(protected), "reserved_requests": count,
        "protected_historical_files": len(original), "protected_p63_files": len(frozen_p63),
        "all_planned_cases_present": len(cases) == 24,
        "global_reserved_requests": len(book().rows()), "cap": CAP, "paid_requests": 0,
        "default_model_unchanged": True, "semantic_quality_claim": False}
    write_json(OUT / "audit.json", row)
    book().export(OUT / "dispatches.json")
    print(json.dumps(row))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "tokenizer", "smoke", "run", "export-review", "summarize", "audit", "replay"])
    parser.add_argument("--model", choices=["both", "4b", "9b"], default="both")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "tokenizer":
        prepare_tokenizer()
    elif args.action == "smoke":
        asyncio.run(smoke())
    elif args.action in {"run", "replay"}:
        args.replay = args.action == "replay"
        asyncio.run(offline_replay(args) if args.replay else run(args))
    else:
        {"export-review": export_review, "summarize": summarize, "audit": audit}[args.action]()


if __name__ == "__main__":
    main()
