"""Final offline verification of the separately archived source-retention/schema fix."""

import json
import zipfile
from xml.etree import ElementTree

from run_rag_context_fix import OUT, ROOT, audit, load, prepare, sha

from repomentor.records import write


def main():
    protocol = prepare()
    audit(protocol)
    replay = load(OUT / "replay.json")
    assert replay["new_model_requests"] == replay["metadata_requests"] == 0
    assert {c["case_id"] for c in protocol["cases"]} == {c["case_id"] for c in replay["receipts"]}
    review = load(OUT / "semantic-review.json")
    assert not review["human_approved"] and not review["training_eligible"]
    assert {c["case_id"] for c in review["cases"]} == {c["case_id"] for c in protocol["cases"]}
    follow = load(OUT / "cases/chat-nanoid-2.json")["result"]
    assert any(s["path"] == "index.js" and s["start_line"] <= 136 <= 137 <= s["end_line"]
               for s in follow["shared_input_context"])
    assert any(s["path"] == "index.js" and s["start_line"] <= 136 <= 137 <= s["end_line"]
               for s in follow["final_context"])
    bound_schemas = 0
    for name in ("isolated", "shared"):
        report = load(OUT / f"cases/report-mitt-{name}.json")
        for row in report["stages"]:
            files = sorted({s["path"] for s in row["final_context"]})
            for d in row["decisions"]:
                if d.get("phase") != "finalize" or not d.get("request_sent"):
                    continue
                schema = d["request"]["response_format"]["json_schema"]["schema"]
                assert schema["$defs"]["EvidenceReference"]["properties"]["path"]["enum"] == files
                bound_schemas += 1
    suites = ElementTree.parse(ROOT / "artifacts/p62c-fix-tests.xml").findall(".//testsuite")
    assert suites and all(int(s.attrib["errors"]) == int(s.attrib["failures"]) == 0 for s in suites)
    wheel = next((ROOT / "artifacts/p62c-fix-wheel").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "repomentor/agent.py" not in archive.namelist()
        for source in (ROOT / "src/repomentor").rglob("*.py"):
            path = source.relative_to(ROOT / "src").as_posix()
            assert archive.read(path).replace(b"\r\n", b"\n") == source.read_bytes().replace(b"\r\n", b"\n"), path
    report = {"status": "engineering_checks_passed", "model_quality": "not_accepted",
              "local_requests": load(OUT / "audit.json")["local_requests"], "paid_requests": 0,
              "replayed_cases": len(replay["receipts"]), "replay_new_requests": 0,
              "protected_files_unchanged": len(protocol["protected_files"]),
              "offline_tests": sum(int(s.attrib["tests"]) for s in suites),
              "observed_path_schemas": bound_schemas, "critical_followup_source_retained": True,
              "wheel_sha256": sha(wheel), "wheel_matches_sources": True,
              "junit_sha256": sha(ROOT / "artifacts/p62c-fix-tests.xml"),
              "semantic_review_sha256": sha(OUT / "semantic-review.json"),
              "case_sha256": {p.name: sha(p) for p in sorted((OUT / "cases").glob("*.json"))},
              "human_approved": False, "training_eligible": False}
    write(OUT / "acceptance.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
