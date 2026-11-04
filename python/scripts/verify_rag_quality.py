"""Verify P6.2 artifacts without inference, metadata requests, or rewriting old experiments."""

import hashlib
import json
import re
import zipfile
from xml.etree import ElementTree

from run_rag_quality import MODES, OUT, ROOT, load, sha, verify_protected

from repomentor.paths import data_root
from repomentor.rag import citation_validity, verify_spans
from repomentor.records import write
from repomentor.repository import SnapshotStore
from repomentor.sdk_agent import sdk_version


def main():
    protocol = load(OUT / "protocol.json")
    assert protocol["sdk"] == sdk_version()
    verify_protected(protocol)
    corpus = SnapshotStore(data_root() / "corpus")
    expected = {(t, m) for t in protocol["executed_task_ids"] for m in MODES}
    observed, request_hashes = set(), []
    for path in sorted((OUT / "cases").glob("*.json")):
        record = load(path)
        row = record["result"]
        key = (record["task_id"], record["mode"])
        assert key not in observed
        observed.add(key)
        reader = corpus.open(record["snapshot_id"])
        assert reader.snapshot.commit_sha == record["commit_sha"]
        assert row["runtime"] == protocol["sdk"] and row["config"] == protocol["qa_config"]
        assert row["limits"] == protocol["limits"] and record["split"] == "validation"
        for spans in (row["context"], row["final_context"]):
            verify_spans(reader, spans)
        assert citation_validity(row["answer"], row["final_context"])
        assert all(len(t["visible_json"].encode()) <= row["limits"]["max_observation_bytes"] for t in row["tool_trace"])
        request_hashes.extend(d["request_hash"] for d in row["decisions"] if d["request_sent"])
    assert expected == observed
    dispatches = load(OUT / "dispatches.json")
    assert sorted(request_hashes) == sorted(d["request_hash"] for d in dispatches)
    replay = load(OUT / "replay.json")
    assert len(replay["receipts"]) == len(expected) and replay["new_model_requests"] == 0
    review = load(OUT / "semantic-review.json")
    reviewed = {(r["task_id"], mode) for r in review["cases"] for mode in r["modes"]}
    assert reviewed == expected and not review["human_approved"] and not review["training_eligible"]
    tests = ElementTree.parse(ROOT / "artifacts/p62-tests.xml")
    suites = tests.findall(".//testsuite")
    assert suites and all(int(s.attrib["errors"]) == int(s.attrib["failures"]) == 0 for s in suites)
    wheel = next((ROOT / "artifacts/p62-wheel").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "repomentor/agent.py" not in archive.namelist()
        for source in (ROOT / "src/repomentor").rglob("*.py"):
            name = source.relative_to(ROOT / "src").as_posix()
            assert archive.read(name).replace(b"\r\n", b"\n") == source.read_bytes().replace(b"\r\n", b"\n"), name
        metadata = archive.read("repomentor_core-0.1.0.dist-info/METADATA").decode()
        for dependency in ("tree-sitter==", "tree-sitter-python==", "tree-sitter-javascript==", "tree-sitter-typescript=="):
            assert any(dependency in line and "extra ==" not in line for line in metadata.splitlines())
    links = 0
    for path in [ROOT / "README.md", ROOT / "项目文档.md", ROOT / "notes/architecture.md",
                 ROOT / "notes/experiment-index.md", ROOT / "notes/repomentor-python-and-minimind-plan.md"]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if target.startswith(("http", "#")):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
            links += 1
    record = {"status": "engineering_checks_passed", "model_quality": "not_accepted",
              "cases": len(expected), "local_generation_requests": len(dispatches), "paid_requests": 0,
              "cache_replay": "12/12; zero metadata and inference requests", "source_and_citation_ranges_verified": True,
              "protected_files_unchanged": len(protocol["protected_files"]),
              "offline_tests": sum(int(s.attrib["tests"]) for s in suites),
              "wheel_sha256": sha(wheel), "wheel_matches_sources": True, "local_doc_links": links,
              "sdk": sdk_version(), "semantic_review_sha256": sha(OUT / "semantic-review.json"),
              "junit_sha256": hashlib.sha256((ROOT / "artifacts/p62-tests.xml").read_bytes()).hexdigest(),
              "human_approved": False, "training_eligible": False}
    write(OUT / "acceptance.json", record)
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
