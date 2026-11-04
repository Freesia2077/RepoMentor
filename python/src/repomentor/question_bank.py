"""Offline catalog of existing questions and attempts; never a mixed training/evaluation export."""

import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

TASK_SOURCES = (
    "p0/validation-seed.jsonl", "p3/tasks.validation-oracle.jsonl", "p4/tasks.jsonl", "p6/tasks.json",
    "p6-d-external/tasks.json", "p63/development.json", "p63/confirmation.json",
    "p63-closeout/confirmation.json", "qwen9b-diagnostic/tasks.json",
    "qwen9b-reliability/development.json", "qwen9b-reliability/confirmation.json",
    "rag-interview/development/tasks.json", "rag-interview/confirmation/tasks.json",
    "rag-expansion/dataset.json", "rag-unified/dataset.json",
)
RECORD_STREAMS = (
    "p3/qwen-q4-oracle.jsonl", "p3/initial-run/qwen-q4-oracle.jsonl",
    "p4/rag-baseline.jsonl", "p4/cache-resume.jsonl", "p4/rag-smoke-baseline.jsonl",
    "p5/retrieval.jsonl", "p5/retrieval-resume.jsonl", "p5/retrieval-parser026-attempt.jsonl",
    "p5/generation.jsonl", "p5/generation-resume.jsonl", "p5/history.jsonl", "p5/history-resume.jsonl",
    "p6/agent.jsonl", "p6/agent-replay.jsonl", "p6/agent-resumed.jsonl", "p6/pilot-cross-map.jsonl",
)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalized(text):
    # Whitespace inside code/string literals may change the question's meaning.
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n")).strip()


def question_id(repository, commit, question, context=None, partition="evaluation"):
    return "q-" + digest([repository.casefold(), commit, normalized(question), context or {}, partition])[:16]


def build_bank(root):
    root = Path(root).resolve()
    source_hashes, questions, aliases, repositories, skipped = {}, {}, defaultdict(set), {}, []
    seen_attempts = set()

    def read(path):
        path = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("question source escapes Python root")
        raw = path.read_bytes()
        name = path.relative_to(root).as_posix()
        source_hashes[name] = hashlib.sha256(raw).hexdigest()
        return ([json.loads(s) for s in raw.decode("utf-8-sig").splitlines() if s.strip()]
                if path.suffix == ".jsonl" else json.loads(raw.decode("utf-8-sig")))

    def repo_meta(task):
        repo = task.get("repository")
        snapshot = task.get("snapshot_id")
        commit = task.get("commit_sha")
        known = repositories.get(repo, {})
        return repo, snapshot or known.get("snapshot_id"), commit or known.get("commit_sha")

    def register(task, path, pointer, *, current=False, context=None):
        if str(task.get("split", "")).casefold().startswith("test"):
            raise ValueError("sealed final-test questions must not be imported")
        repo, snapshot, commit = repo_meta(task)
        question = task.get("input", {}).get("question", task.get("question"))
        if not repo or not snapshot or not commit or not isinstance(question, str) or not question.strip():
            raise ValueError(f"question lacks immutable identity: {path}#{pointer}")
        if repo not in repositories:
            repositories[repo] = {"repository": repo, "snapshot_id": snapshot, "commit_sha": commit,
                                  "role": "historical-evaluation"}
        ctx = context or {}
        history = task.get("input", {}).get("history", [])
        if history:
            ctx = {**ctx, "history_sha256": digest(history)}
        partition = "training" if str(task.get("split", "")).startswith("train") else "evaluation"
        qid = question_id(repo, commit, question, ctx, partition)
        row = questions.setdefault(qid, {"question_id": qid, "repository": repo, "snapshot_id": snapshot,
            "commit_sha": commit, "question": normalized(question), "context": ctx, "partition": partition,
            "current": False, "origins": [], "observations": [], "training_eligible": False})
        if row["snapshot_id"] != snapshot:
            raise ValueError("same repository/commit has inconsistent snapshot identities")
        row["current"] |= current
        origin = {"file": path, "pointer": pointer, "task_id": task.get("task_id"),
                  "split": task.get("split", "historical-development"),
                  "kind": task.get("kind", task.get("task_type", "qa"))}
        if origin not in row["origins"]:
            row["origins"].append(origin)
        if task.get("task_id"):
            aliases[task["task_id"]].add(qid)
        return qid

    p0 = root / "experiments/p0/corpus.json"
    if p0.exists():
        for meta in read(p0)["repositories"]:
            repositories[meta["repository"]] = {k: meta[k] for k in ("repository", "snapshot_id", "commit_sha")}
            repositories[meta["repository"]]["role"] = meta["split"]
    admission = root / "experiments/p6-d-external/admission.json"
    if admission.exists():
        for meta in read(admission)["repositories"]:
            repositories[meta["repository"]] = {k: meta[k] for k in ("repository", "snapshot_id", "commit_sha")}
            repositories[meta["repository"]]["role"] = "external-evaluation"

    expansion = unified = None
    for relative in TASK_SOURCES:
        path = root / "experiments" / relative
        if not path.exists():
            continue
        name = path.relative_to(root).as_posix()
        obj = read(path)
        if relative in ("rag-expansion/dataset.json", "rag-unified/dataset.json"):
            if relative == "rag-expansion/dataset.json":
                expansion = obj
            else:
                unified = obj
            for meta in obj.get("repositories", []):
                repositories[meta["repository"]] = {k: meta[k] for k in ("repository", "snapshot_id", "commit_sha")}
                repositories[meta["repository"]]["role"] = "external-evaluation"
            tasks, prefix = obj["tasks"], "/tasks"
        else:
            tasks, prefix = obj, ""
        local = {t["task_id"]: t for t in tasks}

        def dependency(task, seen=()):
            parent = task.get("depends_on")
            if not parent:
                return {}
            if parent in seen:
                raise ValueError("cyclic question dependency")
            if parent not in local:
                return {"unresolved_dependency": parent}
            previous = local[parent]
            older = dependency(previous, (*seen, parent))
            return {**older, "prior_user_questions": [*older.get("prior_user_questions", []),
                                                       previous["input"]["question"]]}

        for i, task in enumerate(tasks):
            register(task, name, f"{prefix}/{i}", current=relative.startswith(("rag-interview/", "rag-expansion/")),
                     context=dependency(task))

    # Conversation turns are distinct tasks with their required user-history prefix.
    for relative in ("p63/dialogues.json", "qwen9b-reliability/dialogue.json"):
        path = root / "experiments" / relative
        if not path.exists():
            continue
        obj = read(path)
        for i, dialogue in enumerate(obj if isinstance(obj, list) else [obj]):
            did = dialogue.get("dialogue_id", "mitt")
            for turn, question in enumerate(dialogue["questions"], 1):
                task = {**dialogue, "task_id": f"{did}-turn-{turn}", "input": {"question": question},
                        "split": "dialogue-development", "kind": "dialogue"}
                register(task, path.relative_to(root).as_posix(),
                         (f"/{i}" if isinstance(obj, list) else "") + f"/questions/{turn - 1}",
                         context={"prior_user_questions": dialogue["questions"][:turn - 1]} if turn > 1 else {})

    review_sources = {}
    unblinding = root / "experiments/rag-interview/review/unblinding.json"
    if unblinding.exists():
        for cid, meta in read(unblinding)["mapping"].items():
            review_sources[meta["source"]] = {"file": "experiments/rag-interview/judgments.json", "candidate_id": cid}
        read("experiments/rag-interview/judgments.json")

    def resolve(row, result, path, pointer=""):
        task = row.get("task", row)
        tid = task.get("task_id", row.get("task_id"))
        if not tid and task.get("dialogue_id") and task.get("turn"):
            tid = f"{task['dialogue_id']}-turn-{task['turn']}"
        repo = task.get("repository", result.get("repository"))
        snapshot = task.get("snapshot_id", result.get("snapshot_id"))
        commit = task.get("commit_sha", result.get("commit_sha"))
        candidates = [questions[qid] for qid in aliases.get(tid, ())
                      if (not repo or questions[qid]["repository"] == repo)
                      and (not snapshot or questions[qid]["snapshot_id"] == snapshot)
                      and (not commit or questions[qid]["commit_sha"] == commit)]
        question = task.get("input", {}).get("question", task.get("question"))
        result_input = result.get("input", {})
        question = question or result.get("question") or (result_input.get("question") if isinstance(result_input, dict) else None)
        messages = result_input if isinstance(result_input, list) else result.get("input_messages", [])
        users = [m.get("content") for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        context = {"prior_user_questions": users[:-1]} if len(users) > 1 else {}
        if question:
            candidates = [q for q in candidates if q["question"] == normalized(question)]
        declared_input = task.get("input", {})
        if "history" in declared_input:
            # A frozen task's declared history defines its identity. Rendered
            # model messages may also contain source wrappers and instructions.
            history = declared_input["history"]
            context = {"history_sha256": digest(history)} if history else {}
            candidates = [q for q in candidates
                          if q["context"].get("history_sha256") == context.get("history_sha256")]
        elif context:
            candidates = [q for q in candidates if q["context"] == context]
        cohort = Path(path).parts[1]
        same_cohort = [q for q in candidates if any(Path(o["file"]).parts[1] == cohort for o in q["origins"])]
        if same_cohort:
            candidates = same_cohort
        if len(candidates) == 1:
            return candidates[0]["question_id"]
        if candidates:
            skipped.append({"file": path, "task_id": tid, "reason": "ambiguous task alias; not merged"})
            return None
        repo, snapshot, commit = repo_meta({**result, **task})
        if not question or not repo or not snapshot or not commit:
            return None  # Report stages, summaries and trace-only records are not new questions.
        if not context and (task.get("linked_turn", 1) > 1 or task.get("turn", 1) > 1):
            context["unresolved_dialogue_context"] = path
        return register({"task_id": tid, "repository": repo, "snapshot_id": snapshot, "commit_sha": commit,
                         "input": {"question": question}, "split": task.get("split", "historical-development")},
                        path, pointer, context=context)

    def observe(row, path, pointer=""):
        if not isinstance(row, dict):
            return
        result = row.get("result", row)
        if not isinstance(result, dict):
            return
        qid = resolve(row, result, path, pointer)
        if qid is None:
            return
        locator = (path, pointer)
        if locator in seen_attempts:
            return
        seen_attempts.add(locator)
        comp = result.get("completion", result if "finish_reason" in result else {})
        request_sent = comp.get("request_sent")
        if request_sent is None and isinstance(result.get("new_model_requests"), int):
            request_sent = result["new_model_requests"] > 0
        generation = bool(comp or result.get("answer") is not None)
        # A replay is a record of a past answer, not a fresh generation request.
        generated = generation and (request_sent is not False or result.get("answer") is not None)
        questions[qid]["observations"].append({"file": path, "pointer": pointer,
            "cohort": Path(path).parts[1], "condition": row.get("condition", row.get("variant", row.get("mode", result.get("mode")))),
            "status": comp.get("status", result.get("status", result.get("stop_reason", "retrieval_only"))),
            "generation_recorded": generated, "request_sent": request_sent,
            "review": review_sources.get(path), "retrieval_checked": bool("retrieval" in row or result.get("retrieval") or result.get("initial_retrieval")),
            "input_history_stays_in_original_record": True})

    for path in sorted((root / "experiments").rglob("*.json")):
        if "cases" not in path.parts:
            continue
        name = path.relative_to(root).as_posix()
        if "report" in path.parts or "reports" in path.parts:
            continue
        observe(read(path), name)
    for relative in RECORD_STREAMS:
        path = root / "experiments" / relative
        if path.exists():
            for i, row in enumerate(read(path)):
                observe(row, path.relative_to(root).as_posix(), f"/{i}")

    if expansion is not None:
        path = "experiments/rag-expansion/results.jsonl"
        result_path = root / path
        summary_path = root / "experiments/rag-expansion/summary.json"
        reviews = read(summary_path).get("reviews", {}) if summary_path.exists() else {}
        task_map = {t["task_id"]: t for t in expansion["tasks"]}
        for i, row in enumerate(read(result_path) if result_path.exists() else []):
            task = expansion["diagnostic_task"] if row["case_id"].startswith("diagnostic/") else task_map[row["task_id"]]
            observe({**row, "task": task}, path, f"/{i}")
            qid = resolve({"task": task}, {}, path)
            if qid and row["case_id"] in reviews:
                questions[qid]["observations"][-1]["review"] = {"file": "experiments/rag-expansion/summary.json",
                                                                "candidate_id": row["case_id"]}
        checked = {r["task_id"] for r in expansion.get("retrieval", [])}
        for tid in checked:
            for qid in aliases[tid]:
                questions[qid]["retrieval_checked"] = True

    latest = None
    if unified is not None:
        path = "experiments/rag-unified/results.jsonl"
        summary_path = root / "experiments/rag-unified/summary.json"
        reviews = read(summary_path).get("reviews", {}) if summary_path.exists() else {}
        tasks = {t["task_id"]: t for t in unified["tasks"]}
        records = read(path) if (root / path).exists() else []
        for task in tasks.values():
            qid = resolve({"task": task}, {}, path)
            questions[qid]["retrieval_checked"] = True
            questions[qid]["latest_evaluation"] = {"file": "experiments/rag-unified/dataset.json", "completed_conditions": [], "reviewed_conditions": []}
        for i, row in enumerate(records):
            task = tasks[row["task_id"]]
            observe({**row, "task": task}, path, f"/{i}")
            qid = resolve({"task": task}, {}, path)
            questions[qid]["latest_evaluation"]["completed_conditions"].append(row["condition"])
            if row["case_id"] in reviews:
                questions[qid]["observations"][-1]["review"] = {"file": "experiments/rag-unified/summary.json", "candidate_id": row["case_id"]}
                questions[qid]["latest_evaluation"]["reviewed_conditions"].append(row["condition"])
        latest = {"file": "experiments/rag-unified/dataset.json", "planned_questions": len(tasks),
                  "planned_records": len(unified["prepared_inputs"]), "completed_records": len(records), "reviewed_records": len(reviews)}

    # New comparisons reuse the same public question identities, including while answers are pending.
    comparisons = []
    for dataset in sorted((root / "experiments").glob("*/dataset.json")):
        package = json.loads(dataset.read_text(encoding="utf-8"))
        if not isinstance(package, dict) or package.get("schema") != "repomentor.benchmark.v1":
            continue
        comparisons.append((package.get("protocol", {}).get("evaluation_date", ""), dataset))
    for _, dataset in sorted(comparisons):
        package = read(dataset)
        base = dataset.parent.relative_to(root).as_posix()
        tasks = {t["task_id"]: t for t in package["tasks"]}
        summary = dataset.parent / "summary.json"
        reviews = read(summary).get("reviews", {}) if summary.exists() else {}
        stream = dataset.parent / "results.jsonl"
        completed = read(stream) if stream.exists() else []
        diagnostic = bool(package["cases"]) and all(c.get("kind", "main").endswith("diagnostic") for c in package["cases"])
        states = {}
        for i, task in enumerate(package["tasks"]):
            qid = register(task, base + "/dataset.json", f"/tasks/{i}", current=True)
            questions[qid]["retrieval_checked"] = any(r["task_id"] == task["task_id"] for r in package["retrieval_checks"])
            state = {"file": base + "/dataset.json",
                "planned_conditions": [c["condition"] for c in package["cases"] if c["task_id"] == task["task_id"]],
                "completed_conditions": [], "reviewed_conditions": []}
            states[task["task_id"]] = state
            if diagnostic:
                questions[qid].setdefault("diagnostic_evaluations", []).append(state)
            else:
                questions[qid]["latest_evaluation"] = state
        for i, row in enumerate(completed):
            task = tasks[row["task_id"]]
            observe({**row, "task": task}, base + "/results.jsonl", f"/{i}")
            qid = resolve({"task": task}, {}, base + "/results.jsonl")
            states[task["task_id"]]["completed_conditions"].append(row["condition"])
            if row["case_id"] in reviews:
                questions[qid]["observations"][-1]["review"] = {"file": base + "/summary.json", "candidate_id": row["case_id"]}
                states[task["task_id"]]["reviewed_conditions"].append(row["condition"])
        if not diagnostic:
            latest = {"file": base + "/dataset.json", "planned_questions": len(tasks), "planned_records": len(package["cases"]),
                      "completed_records": len(completed), "reviewed_records": len(reviews)}

    for q in questions.values():
        obs = q["observations"]
        q["retrieval_checked"] = q.get("retrieval_checked", False) or any(o["retrieval_checked"] for o in obs)
        q["generation_recorded"] = any(o["generation_recorded"] for o in obs)
        q["semantic_reviewed"] = any(o["review"] for o in obs)
        q["coverage"] = ("reviewed" if q["semantic_reviewed"] else "generated" if q["generation_recorded"]
                         else "retrieval_only" if q["retrieval_checked"] else "registered_only")
        q["origins"].sort(key=lambda o: (o["file"], o["pointer"]))
        q["observations"].sort(key=lambda o: (o["file"], o["pointer"]))
    rows = sorted(questions.values(), key=lambda q: q["question_id"])
    def counts(subset):
        return {"unique_questions": len(subset), "repositories_with_questions": len({q["repository"] for q in subset}),
                "coverage": dict(sorted(Counter(q["coverage"] for q in subset).items())),
                "observation_records": sum(len(q["observations"]) for q in subset)}
    return {"schema": "repomentor.question-bank.v1", "builder_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "policy": {"current": "Union of public questions in the recent RAG packages, including declared follow-ups; latest_evaluation describes only the latest suite, not a pooled score across versions",
                       "dedup": "Exact normalized question + repository commit + prior user questions/declared history + partition; paraphrases and different user histories are not auto-merged; generated assistant history stays in each observation",
                       "attempts": "Observation records include baselines, failures and replays; never sum as unique questions or new model requests",
                       "scores": "Keep model/version/cohort/condition scores in original summaries; no pooled accuracy",
                       "sealed_test_questions_imported": False, "training_eligible": False,
                       "legacy_reviews": "Only recent review bindings are indexed; absence of a binding does not mean no historical review."},
            "summary": {"all": counts(rows), "current": counts([q for q in rows if q["current"]]),
                        "registered_repositories": len(repositories), "latest_evaluation": latest},
            "repositories": sorted(repositories.values(), key=lambda r: r["repository"]),
            "source_files": dict(sorted(source_hashes.items())), "unresolved_observations": skipped, "questions": rows}


def run_command(args):
    root = Path(args.root).resolve()
    target = Path(args.catalog)
    if args.action == "rebuild":
        value = build_bank(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(value["summary"], ensure_ascii=False))
        return 0
    value = json.loads(target.read_text(encoding="utf-8"))
    if args.action == "verify":
        if build_bank(root) != value:
            raise ValueError("question catalog differs from its sources; rebuild the derived catalog")
        print(json.dumps({"verified_questions": len(value["questions"]), "source_files": len(value["source_files"]), "model_requests": 0}))
        return 0
    rows = [q for q in value["questions"] if (args.scope == "all" or q["current"])
            and (not args.repository or q["repository"] == args.repository)
            and (not args.status or q["coverage"] == args.status)]
    if args.action == "show":
        found = [q for q in rows if q["question_id"] == args.question_id
                 or any(o["task_id"] == args.question_id for o in q["origins"])]
        if len(found) != 1:
            raise ValueError("question ID missing or ambiguous; use the canonical q- ID from list")
        print(json.dumps(found[0], ensure_ascii=False, indent=2))
    elif args.action == "list":
        for q in rows:
            print(f"{q['question_id']}\t{q['repository']}\t{q['coverage']}\t{q['question']}")
    else:
        print(json.dumps({"catalog": str(target), "summary": value["summary"], "selected_questions": len(rows)}, ensure_ascii=False, indent=2))
    return 0
