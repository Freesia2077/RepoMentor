"""Check frozen SDK collaboration artifacts offline; never call a model or rewrite history."""

import json
import re
import zipfile
from collections import Counter
from xml.etree import ElementTree

from run_sdk_collaboration import OUT, ROOT, load, protected, sha

from repomentor.paths import data_root
from repomentor.rag import citation_validity, verify_spans
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.sdk_agent import sdk_version


def main():
    protocol = load(OUT / "protocol.json")
    assert protocol["sdk"] == sdk_version()
    assert protocol["script_sha256"] == sha(ROOT / "scripts/run_sdk_collaboration.py")
    protected(protocol)
    corpus = SnapshotStore(data_root() / "corpus")
    expected = {c["case_id"]: c for c in protocol["cases"]}
    records, sent, attempts = {}, [], set()
    workflow_success = Counter()
    for path in sorted((OUT / "cases").glob("*.json")):
        record = load(path)
        case_id = record["case_id"]
        assert case_id not in records and case_id in expected
        records[case_id] = record
        assert all(record[k] == v for k, v in expected[case_id].items())
        assert record["split"] == "validation"
        reader = corpus.open(record["snapshot_id"])
        assert reader.snapshot.commit_sha == record["commit_sha"]
        report = record["kind"] == "report"
        rows = record["stages"] if report else [record["result"]]
        if all(r["completion"]["status"] == "success" for r in rows):
            assert not report or len(rows) == 3
            workflow_success[record["kind"]] += 1
        for row in rows:
            assert row["runtime"] == protocol["sdk"]
            assert row["config"] == protocol["report_config" if report else "qa_config"]
            assert row["limits"] == protocol["report_limits" if report else "qa_limits"]
            assert len(row["decisions"]) <= row["limits"]["max_decisions"]
            assert row["tool_calls"] <= row["limits"]["max_tool_calls"]
            assert row["source_bytes"] <= row["limits"]["max_source_bytes"]
            excluded = row["entry_focus"]["excluded_paths"]
            contexts = [row["context"], row["final_context"], row["shared_input_context"],
                        *row["visible_context"].values()]
            for spans in contexts:
                verify_spans(reader, spans)
                assert all(s["path"] not in excluded for s in spans)
            for item in row["tool_trace"]:
                assert len(item["visible_json"].encode()) <= row["limits"]["max_observation_bytes"]
            for delegation in row["delegations"]:
                payload = json.loads(delegation["visible_json"])
                assert len(delegation["visible_json"].encode()) <= row["limits"]["max_observation_bytes"]
                verify_spans(reader, payload["spans"])
                assert all(s in row["visible_context"]["main"] for s in payload["spans"])
                assert payload["notes_not_evidence"]
            if row["completion"]["status"] == "success":
                if report:
                    assert row["report_validation"]["passed"]
                else:
                    assert citation_validity(row["answer"], row["final_context"])
            if row["attempt_id"] not in attempts:
                sent.extend(d for d in row["decisions"] if d["request_sent"])
                attempts.add(row["attempt_id"])
    assert records.keys() == expected.keys()
    dispatches = load(OUT / "dispatches.json")
    assert Counter((d["request_hash"], d["actor"]) for d in sent) == Counter(
        (d["request_hash"], d["actor"]) for d in dispatches)
    assert len(dispatches) <= protocol["max_generation_requests"]
    replay = load(OUT / "replay.json")
    assert replay["new_model_requests"] == replay["metadata_requests"] == 0
    assert {r["case_id"] for r in replay["receipts"]} == expected.keys()
    assert all(r["new_model_requests"] == 0 for r in replay["receipts"])
    review = load(OUT / "semantic-review.json")
    assert Counter(c for r in review["cases"] for c in r["case_ids"]) == Counter(expected.keys())
    assert not review["human_approved"] and not review["training_eligible"]
    chat = [records[f"chat-nanoid-{n}"]["result"] for n in (1, 2, 3)]
    assert [r["entry_focus"]["label"] for r in chat] == ["node", "node", "non-secure"]
    assert chat[1]["shared_input_context"] and chat[1]["tool_calls"] == 0
    assert not chat[2]["shared_input_context"]
    assert all(s["path"] == "non-secure/index.js" for s in chat[2]["context"])
    assert len(records["expert-demo"]["result"]["delegations"]) == 1
    report_rows = [records[f"report-mitt-{mode}"]["stages"] for mode in ("isolated", "shared")]
    assert report_rows[0][0]["attempt_id"] == report_rows[1][0]["attempt_id"]
    assert report_rows[1][0]["new_model_requests"] == 0
    assert not report_rows[0][1]["shared_input_context"] and report_rows[1][1]["shared_input_context"]
    assert report_rows[0][1]["stop_reason"] == "context_limit"
    assert not report_rows[0][1]["decisions"][-1]["request_sent"]
    assert report_rows[1][1]["stop_reason"] == "report_grounding"
    tests = ElementTree.parse(ROOT / "artifacts/p62b-tests.xml").findall(".//testsuite")
    assert tests and all(int(s.attrib["errors"]) == int(s.attrib["failures"]) == 0 for s in tests)
    wheel = next((ROOT / "artifacts/p62b-wheel").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "repomentor/agent.py" not in archive.namelist()
        for source in (ROOT / "src/repomentor").rglob("*.py"):
            name = source.relative_to(ROOT / "src").as_posix()
            assert archive.read(name).replace(b"\r\n", b"\n") == source.read_bytes().replace(b"\r\n", b"\n"), name
    links = 0
    for path in [ROOT / "README.md", ROOT / "项目文档.md", ROOT / "notes/architecture.md",
                 ROOT / "notes/experiment-index.md", ROOT / "notes/repomentor-python-and-minimind-plan.md"]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if target.startswith(("http", "#")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            assert resolved.exists() or resolved == (OUT / "acceptance.json").resolve(), (path, target)
            links += 1
    acceptance = {
        "status": "engineering_checks_passed", "model_quality": "not_accepted",
        "cases": len(records), "workflow_success_by_kind": dict(workflow_success),
        "local_generation_requests": len(dispatches), "paid_requests": 0,
        "actor_requests": dict(Counter(d["actor"] for d in sent)),
        "cache_replay": "12/12; zero metadata and inference requests",
        "source_provenance_and_qa_citation_ranges_verified": True,
        "global_budgets_verified": True, "old_files_unchanged": len(protocol["protected_files"]),
        "offline_full_suite_tests": sum(int(s.attrib["tests"]) for s in tests),
        "wheel_sha256": sha(wheel), "wheel_matches_sources": True,
        "semantic_review_sha256": sha(OUT / "semantic-review.json"),
        "case_sha256": {p.name: sha(p) for p in sorted((OUT / "cases").glob("*.json"))},
        "junit_sha256": sha(ROOT / "artifacts/p62b-tests.xml"), "local_doc_links": links,
        "sdk": sdk_version(), "human_approved": False, "training_eligible": False,
    }
    write(OUT / "acceptance.json", acceptance)
    print(json.dumps(acceptance, ensure_ascii=False))


if __name__ == "__main__":
    main()
