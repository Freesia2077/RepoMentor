"""One version, 48 existing questions, two fixed RAG conditions; compact offline evidence."""

import argparse
import asyncio
import hashlib
import json
import random
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from repomentor.config import load_chat_settings, write_json
from repomentor.evaluation import ANSWER_SCHEMA
from repomentor.experiment_ledger import RequestLedger
from repomentor.interview_baselines import prepare_input, select_sources
from repomentor.paths import data_root, runtime_dir
from repomentor.question_bank import build_bank
from repomentor.rag import citation_validity, rag_version, verify_spans
from repomentor.rag_evaluation import coverage
from repomentor.repository import SnapshotStore
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import ChatCompletionsRuntime, fingerprint
from repomentor.semantic_review import quality_tiers, validate_judgment

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/rag-unified"
PRIVATE = runtime_dir() / "rag-unified"
CONDITIONS = ("lexical", "code_rag")
CAP, PRIOR, COMBINED = 96, 598, 694


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def settings():
    return load_chat_settings(ROOT / "config/model-settings.json")


def implementation():
    return {"rag": rag_version(), **{name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in ("scripts/rag_unified.py", "src/repomentor/interview_baselines.py", "src/repomentor/semantic_review.py")}}


def count_ledger(path):
    if not path.exists():
        return 0
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0]


def prior_count():
    return sum(count_ledger(runtime_dir() / p / "dispatches.sqlite") for p in ("p63", "rag-interview", "rag-expansion"))


def reader_for(task, excluded=()):
    rd = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    if rd.snapshot.commit_sha != task["commit_sha"]:
        raise ValueError("snapshot commit changed")
    if excluded:
        rd = SimpleNamespace(paths=[p for p in rd.paths if p not in excluded], read=rd.read,
                             snapshot=rd.snapshot.model_copy(update={"manifest": [m for m in rd.snapshot.manifest if m.path not in excluded]}))
    return rd


def prepare():
    if (OUT / "dataset.json").exists():
        raise ValueError("dataset already frozen; verify or use a new experiment identity")
    model = settings()
    if prior_count() != PRIOR or (model.context_window, model.max_tokens) != (16384, 2048):
        raise ValueError("unexpected prior budget or model window/output")
    bank = build_bank(ROOT)
    current = [q for q in bank["questions"] if q["current"]]
    if len(current) != 48 or any(q["context"] for q in current):
        raise ValueError("expected exactly 48 standalone current questions")
    expanded = load(ROOT / "experiments/rag-expansion/dataset.json")
    exclusions = {r["repository"]: r.get("excluded_paths", []) for r in expanded["repositories"]}
    source_hashes = {}
    references, old_tasks = {}, {}
    for phase in ("development", "confirmation"):
        for stem in ("tasks", "evaluation-only"):
            name = f"experiments/rag-interview/{phase}/{stem}.json"
            source_hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for row in load(ROOT / name):
                if stem == "tasks":
                    old_tasks[row["task_id"]] = row
                else:
                    references[row["task_id"]] = {"required_points": row["key_points"], "sources": row["sources"], "answerable": bool(row["sources"])}
    source_hashes["experiments/rag-expansion/dataset.json"] = hashlib.sha256((ROOT / "experiments/rag-expansion/dataset.json").read_bytes()).hexdigest()
    original_tasks = {**old_tasks, **{t["task_id"]: t for t in expanded["tasks"]}}
    references.update(expanded["evaluation_only"])
    tasks, inputs, evaluation_only = [], {}, {}
    indices = {}
    for question in current:
        origins = [o for o in question["origins"] if o["file"].startswith(("experiments/rag-interview/", "experiments/rag-expansion/"))]
        origin = next((o for o in origins if o["task_id"] in original_tasks), None)
        if origin is None:
            raise ValueError("current question has no source task")
        original = original_tasks[origin["task_id"]]
        is_old = origin["task_id"] in old_tasks
        cohort = ("older-development" if origin["split"] == "known-development" else "older-confirmation") if is_old else (
            "added-confirmation" if original["split"] == "confirmation" else "added-retrieval-development")
        # Preserve original source split independently of this repeated diagnostic run.
        if is_old and "/development/" in origin["file"]:
            cohort = "older-development"
        task = {**original, "task_id": question["question_id"], "original_task_id": origin["task_id"],
                "original_file": origin["file"], "cohort": cohort, "training_eligible": False}
        tasks.append(task)
        evaluation_only[task["task_id"]] = references[origin["task_id"]]
        repo = task["repository"]
        if repo not in indices:
            rd = reader_for(task, exclusions.get(repo, []))
            indices[repo] = (rd, build_index(rd, storage=PRIVATE, chunker="syntax", retriever="bm25", device="cpu"))
        rd, index = indices[repo]
        for condition in CONDITIONS:
            prepared = prepare_input(model, task["input"]["question"], select_sources(rd, index, task["input"]["question"], condition), condition)
            if not prepared["feasible"]:
                raise ValueError("bounded RAG input infeasible before any generation")
            verify_spans(rd, prepared["context"])
            refs = evaluation_only[task["task_id"]]["sources"]
            inputs[task["task_id"] + "--" + condition] = {"messages": prepared["input_messages"], "context": prepared["context"],
                "budget": prepared["budget"], "reference_coverage": coverage(refs, prepared["context"]) if refs else None}
    order = list(inputs)
    random.Random(20261021).shuffle(order)
    write_json(OUT / "dataset.json", {"protocol": {"version": "rag-unified-v1", "date": "2026-10-21",
        "authorization": "Owner selected all 48 current questions x 2 same-version conditions, up to 96 new local calls; combined limit 694.",
        "prior_reserved": PRIOR, "max_new_requests": CAP, "combined_limit": COMBINED, "settings": model.public(),
        "implementation": implementation(), "source_sha256": source_hashes, "order": order,
        "review": "Nonblind Codex source review; not human or independent approval.",
        "scope": "Repeated selected 48-question diagnostic, not a newly unseen confirmation benchmark; retain all four original cohorts.",
        "retry": "None; interrupted reserved dispatches require manual reconciliation, never automatically retried.",
        "training_eligible": False}, "tasks": tasks, "evaluation_only": evaluation_only, "prepared_inputs": inputs,
        "excluded_paths": exclusions})
    print(json.dumps({"questions": len(tasks), "conditions": len(CONDITIONS), "planned_requests": len(inputs), "model_requests": 0}), flush=True)


def check():
    data = load(OUT / "dataset.json")
    p = data["protocol"]
    if p["implementation"] != implementation() or p["settings"] != settings().public() or prior_count() != PRIOR:
        raise ValueError("frozen implementation/settings/prior budget changed")
    for name, sha in p["source_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha:
            raise ValueError("source task/reference bytes changed")
    return data


def results():
    path = OUT / "results.jsonl"
    rows = [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()] if path.exists() else []
    if len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate result IDs")
    return rows


async def run():
    data = check()
    ledger = RequestLedger(PRIVATE / "dispatches.sqlite", limit=CAP, experiment=fingerprint(data), context_windows=(16384,))
    done = {r["case_id"] for r in results()}
    reserved = {r["case"] for r in ledger.rows()}
    if reserved - done:
        raise ValueError("ambiguous interrupted dispatch; no automatic retry")
    metadata = await ChatCompletionsRuntime(settings()).verify_backend()
    for cid in data["protocol"]["order"]:
        if cid in done:
            continue
        prepared = data["prepared_inputs"][cid]

        def before(body, budget):
            if prior_count() + len(ledger.rows()) >= COMBINED:
                raise ValueError("combined cap reached")
            ledger.reserve(settings(), cid, body, budget)

        runtime = ChatCompletionsRuntime(settings(), before_dispatch=before)
        started = time.perf_counter()
        comp = await runtime.complete(prepared["messages"], response_schema=ANSWER_SCHEMA)
        answer = json.loads(comp.content) if comp.status == "success" else None
        tid, condition = cid.rsplit("--", 1)
        record = {"case_id": cid, "task_id": tid, "condition": condition, "input_sha256": fingerprint(prepared["messages"]),
            "completion": comp.model_dump(), "answer": answer, "backend": metadata,
            "citation_ranges_valid": citation_validity(answer, prepared["context"]), "elapsed_seconds": time.perf_counter() - started,
            "training_eligible": False}
        with (OUT / "results.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps({"completed": len(results()), "case": cid, "status": comp.status, "combined_reserved": PRIOR + len(ledger.rows())}), flush=True)


def verify():
    data = check()
    records = results()
    tasks = {t["task_id"]: t for t in data["tasks"]}
    for row in records:
        source = data["prepared_inputs"][row["case_id"]]
        runtime = ChatCompletionsRuntime(settings())
        if row["input_sha256"] != fingerprint(source["messages"]) or row["completion"]["request_hash"] != fingerprint(runtime.request_body(source["messages"], response_schema=ANSWER_SCHEMA)):
            raise ValueError("input/request identity changed")
        verify_spans(reader_for(tasks[row["task_id"]]), source["context"])
        if set(json.loads(source["messages"][-1]["content"])) != {"question", "context"}:
            raise ValueError("evaluation-only data leaked into generation")
    if PRIOR + count_ledger(PRIVATE / "dispatches.sqlite") > COMBINED:
        raise ValueError("combined request budget exceeded")
    print(json.dumps({"verified_records": len(records), "model_requests": 0}), flush=True)


def summary(supplied=None):
    data = check()
    previous = load(OUT / "summary.json") if (OUT / "summary.json").exists() else {}
    pending = previous.get("reviews", {})
    if supplied:
        pending.update(supplied)
    tasks = {t["task_id"]: t for t in data["tasks"]}
    reviews, groups = {}, defaultdict(Counter)
    fields = ("candidate_id", "correctness", "citation_support", "completeness", "abstention", "entry_scope", "consistency", "claims", "notes")
    for row in results():
        cid, tid = row["case_id"], row["task_id"]
        condition, task = row["condition"], tasks[tid]
        if cid in pending:
            value = {k: pending[cid][k] for k in fields}
            value["claims"] = [{k: c[k] for k in ("claim", "status", "rationale", "evidence")} for c in value["claims"]]
            candidate = {"candidate_id": cid, "answer": row["answer"], "observed_context": data["prepared_inputs"][cid]["context"]}
            reviews[cid] = validate_judgment(value, candidate, reader_for(task))
            reviews[cid]["result_sha256"] = fingerprint(row)
        answerability = "answerable" if data["evaluation_only"][tid]["answerable"] else "unanswerable"
        for key in ("all/" + condition, task["cohort"] + "/" + condition, answerability + "/" + condition):
            group = groups[key]
            group["records"] += 1
            group[row["completion"]["status"]] += 1
            if cid in reviews:
                group["reviewed"] += 1
                group["factually_correct"] += reviews[cid]["correctness"] == "correct"
                group.update({k: int(v) for k, v in quality_tiers(reviews[cid], row["completion"]["status"]).items()})
    count = count_ledger(PRIVATE / "dispatches.sqlite")
    write_json(OUT / "summary.json", {"questions": len(tasks), "records": len(results()), "reviews": reviews,
        "groups": {k: dict(v) for k, v in groups.items()}, "new_requests": count, "combined_requests": PRIOR + count,
        "paid_requests": 0, "checks": previous.get("checks", {}), "training_eligible": False, "human_approved": False,
        "independent_review_completed": False, "scope": data["protocol"]["scope"]})
    print(json.dumps({"records": len(results()), "reviewed": len(reviews), "groups": {k: dict(v) for k, v in groups.items()}}), flush=True)


def finalize():
    verify()
    summary()
    if len(results()) != CAP or len(load(OUT / "summary.json")["reviews"]) != CAP:
        raise ValueError("96 planned records and explicit reviews required")
    hashes = {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest() for name in ("dataset.json", "results.jsonl", "summary.json")}
    manifest = OUT / "completed-files.json"
    if manifest.exists() and load(manifest)["sha256"] != hashes:
        raise ValueError("sealed outputs changed")
    write_json(manifest, {"sha256": hashes, "training_eligible": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "verify", "review", "summary", "finalize"))
    action = parser.parse_args().action
    if action == "run":
        asyncio.run(run())
    elif action == "review":
        summary(json.load(sys.stdin))
    elif action == "verify":
        import httpx
        import httpx2

        def forbidden(*args, **kwargs):
            raise AssertionError("offline verification attempted HTTP")

        with patch.object(httpx.Client, "send", forbidden), patch.object(httpx.AsyncClient, "send", forbidden), patch.object(httpx2.Client, "send", forbidden), patch.object(httpx2.AsyncClient, "send", forbidden):
            verify()
    else:
        globals()[action]()


if __name__ == "__main__":
    main()
