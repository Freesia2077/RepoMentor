"""Offline D acceptance: provenance, splits, actual budgets, review evidence and failures."""

import json
from collections import Counter
from xml.etree import ElementTree

from finalize_d_review import GOLD, KEY_POINTS
from prepare_semantic_review import OUT, ROOT, load
from run_rag_quality import frozen, sha

from repomentor.paths import data_root
from repomentor.rag import verify_spans
from repomentor.repository import SOURCE_EXTENSIONS, SnapshotStore
from repomentor.sdk_agent import sdk_version
from repomentor.semantic_review import candidate_digest, validate_judgment

EXTERNAL = ROOT / "experiments/p6-d-external"


def main():
    protocol = load(EXTERNAL / "protocol.json")
    assert protocol["sdk"] == sdk_version()
    assert protocol["runner_sha256"] == sha(ROOT / "scripts/run_external_qa.py")
    assert protocol["admission_sha256"] == sha(EXTERNAL / "admission.json")
    for name, digest in protocol["protected_files"].items():
        assert sha(ROOT / name) == digest, name
    tasks = load(EXTERNAL / "tasks.json")
    assert tasks == protocol["tasks"] and len(tasks) == 6
    assert len({t["question_sha256"] for t in tasks}) == len(tasks)
    assert all(t["split"] == "external-development" and set(t["input"]) == {"question"} for t in tasks)
    assert not load(EXTERNAL / "admission.json")["training_eligible"]
    corpus = load(ROOT / "experiments/p0/corpus.json")
    assert not {t["repository"].casefold() for t in tasks} & {r["repository"].casefold() for r in corpus["repositories"]}
    store = SnapshotStore(data_root() / "corpus")
    historical_blobs = {}
    for row in corpus["repositories"]:
        snapshot = store.open(row["snapshot_id"]).snapshot
        # Blob metadata only: sealed source and answers are not opened.
        for entry in snapshot.manifest:
            if entry.size >= 200 and any(entry.path.endswith(ext) for ext in SOURCE_EXTENSIONS):
                historical_blobs.setdefault(entry.oid, set()).add(row["repository"])
    overlap, checked = [], set()
    actual_requests, statuses, inputs = 0, Counter(), []
    references = load(EXTERNAL / "evaluation-only/references.json")
    for task in tasks:
        reader = store.open(task["snapshot_id"])
        assert reader.snapshot.commit_sha == task["commit_sha"]
        for ref in GOLD[task["task_id"]]:
            content, truncated = reader.read(ref["path"], 500_000)
            assert not truncated and 1 <= ref["start_line"] <= ref["end_line"] <= len(content.splitlines())
        if task["snapshot_id"] not in checked:
            checked.add(task["snapshot_id"])
            overlap.extend({"external_repository": task["repository"], "path": e.path,
                            "local_repositories": sorted(historical_blobs[e.oid])}
                           for e in reader.snapshot.manifest if e.oid in historical_blobs and e.size >= 200
                           and any(e.path.endswith(ext) for ext in SOURCE_EXTENSIONS))
        indexes = []
        for mode in protocol["modes"]:
            record = load(EXTERNAL / "cases" / f"{task['task_id']}--{mode}.json")["result"]
            assert record["snapshot_id"] == task["snapshot_id"] and record["commit_sha"] == task["commit_sha"]
            context = record.get("final_context", record["context"])
            verify_spans(reader, context)
            statuses[record["completion"]["status"]] += 1
            actual_requests += record["new_model_requests"]
            if mode == "sdk-agent":
                indexes.append(record["index_id"])
                sent = [d for d in record["decisions"] if d["request_sent"]]
                assert len(sent) == record["new_model_requests"] <= protocol["limits"]["max_decisions"]
                assert record["tool_calls"] <= protocol["limits"]["max_tool_calls"]
                assert all(d["input_budget"]["fits"] and d["input_budget"]["total"] <= 8192 for d in sent)
                inputs.extend(json.dumps(d["request"], ensure_ascii=False) for d in sent)
            else:
                indexes.append(record["retrieval"]["index_id"])
                assert record["completion"]["input_budget"]["fits"]
                inputs.append(json.dumps(record["input_messages"], ensure_ascii=False))
        assert len(set(indexes)) == 1, "paired index differs"
    # Full reference strings and explicit grading field names must not occur in request bodies.
    for body in inputs:
        assert '"reference_answer"' not in body and '"key_points"' not in body
        for ref in references.values():
            assert ref["reference_answer"] not in body
    replay = load(EXTERNAL / "replay.json")
    assert len(replay["receipts"]) == 12 and replay["new_model_requests"] == replay["metadata_requests"] == 0
    assert actual_requests == len(load(EXTERNAL / "dispatches.json")) == 34 <= protocol["max_local_requests"]
    candidates = {c["candidate_id"]: c for c in load(OUT / "candidates.json")}
    identities = load(OUT / "identity-map.json")
    reviews = load(OUT / "judgments-final.json")
    assert len(reviews) == len(candidates) == 31 and {r["candidate_id"] for r in reviews} == set(candidates)
    for review in reviews:
        cid = review["candidate_id"]
        assert review["candidate_sha256"] == candidate_digest(candidates[cid])
        identity = identities[cid]
        for appearance in identity["appearances"]:
            assert sha(ROOT / appearance["source_file"]) == appearance["sha256"]
        assert identity["split"] in ("validation", "external-development")
        raw = {k: review[k] for k in ("candidate_id", "correctness", "citation_support", "completeness", "abstention",
                                     "entry_scope", "consistency", "claims", "notes", "key_points")}
        raw["claims"] = [{k: v for k, v in c.items() if k != "verified_evidence"} for c in raw["claims"]]
        assert validate_judgment(raw, candidates[cid], store.open(identity["snapshot_id"])) == review
        if review["key_points"] is not None:
            assert set(review["key_points"]) == set(KEY_POINTS[identity["task_id"]])
    suites = ElementTree.parse(ROOT / "artifacts/p62d-complete-tests.xml").findall(".//testsuite")
    assert suites and all(int(s.attrib["errors"]) == int(s.attrib["failures"]) == 0 for s in suites)
    supplement = load(OUT / "reference-and-dialogue-review.json")
    assert not supplement["human_approved"] and not supplement["training_eligible"]
    supplemental_sources = []
    for finding in supplement["reference_findings"] + supplement["dialogue_findings"]:
        reader = store.open(finding["snapshot_id"])
        for ref in finding["evidence"]:
            content, truncated = reader.read(ref["path"], 500_000)
            assert not truncated and 1 <= ref["start_line"] <= ref["end_line"] <= len(content.splitlines())
            supplemental_sources.append({"snapshot_id": finding["snapshot_id"], **ref})
        if "reference_marker" in finding:
            assert finding["reference_marker"] in references[finding["task_id"]]["reference_answer"]
    result = {"status": "D_deliverables_completed_with_owner_selected_Codex_review", "model_quality": "not_accepted",
        "review_type": "model_assisted_nonblind", "human_approved": False, "training_eligible": False,
        "unique_reviewed_candidates": len(reviews), "reviewed_claims": sum(len(r["claims"]) for r in reviews),
        "appearances": sum(len(i["appearances"]) for i in identities.values()),
        "external_repositories": 2, "external_tasks": 6, "paired_executions": 12,
        "workflow_statuses": dict(statuses), "local_model_requests": actual_requests, "paid_requests": 0,
        "DeepSeek_requests": 0, "review_API_requests": 0, "replay_requests": 0,
        "historical_files_unchanged": len(protocol["protected_files"]), "paired_indexes_identical": True,
        "full_reference_strings_absent_from_requests": True, "SDK_identity_unchanged": True,
        "exact_source_blob_overlap_over_200_bytes": overlap,
        "overlap_limits": "Metadata exact-blob scan only; not semantic clone or pretraining contamination detection.",
        "offline_tests": sum(int(s.attrib["tests"]) for s in suites),
        "tests_sha256": sha(ROOT / "artifacts/p62d-complete-tests.xml"),
        "review_sha256": sha(OUT / "judgments-final.json"), "metrics_sha256": sha(OUT / "external-metrics.json"),
        "supplement_sha256": sha(OUT / "reference-and-dialogue-review.json"),
        "supplemental_source_ranges_verified": len(supplemental_sources),
        "explicit_review_adjudications": len(load(OUT / "adjudications.json")["changes"])}
    frozen(OUT / "acceptance.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
