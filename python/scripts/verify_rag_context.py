"""Verify P6.2 C delivery locally; no network or generation calls."""

import json
import re
import zipfile
from xml.etree import ElementTree

from run_rag_context import OUT, ROOT, audit, load, prepare, sha

from repomentor.records import write


def main():
    protocol = prepare()
    audit(protocol)
    replay = load(OUT / "replay.json")
    assert replay["new_model_requests"] == replay["metadata_requests"] == 0
    assert {r["case_id"] for r in replay["receipts"]} == {c["case_id"] for c in protocol["cases"]}
    assert all(r["new_model_requests"] == 0 for r in replay["receipts"])
    review = load(OUT / "semantic-review.json")
    assert {r["case_id"] for r in review["cases"]} == {c["case_id"] for c in protocol["cases"]}
    assert not review["human_approved"] and not review["training_eligible"]
    tests = ElementTree.parse(ROOT / "artifacts/p62c-tests.xml").findall(".//testsuite")
    assert tests and all(int(s.attrib["errors"]) == int(s.attrib["failures"]) == 0 for s in tests)
    wheel = next((ROOT / "artifacts/p62c-wheel").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "repomentor/agent.py" not in archive.namelist()
        for source in (ROOT / "src/repomentor").rglob("*.py"):
            name = source.relative_to(ROOT / "src").as_posix()
            assert archive.read(name).replace(b"\r\n", b"\n") == source.read_bytes().replace(b"\r\n", b"\n"), name
    links = 0
    for path in [ROOT / "README.md", ROOT / "项目文档.md", ROOT / "notes/experiment-index.md",
                 ROOT / "notes/repomentor-python-and-minimind-plan.md", ROOT / "notes/architecture.md"]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if target.startswith(("http", "#")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            assert resolved.exists() or resolved == (OUT / "acceptance.json").resolve(), (path, target)
            links += 1
    record = {"status": "engineering_checks_passed", "model_quality": "not_accepted",
              "local_requests": load(OUT / "audit.json")["local_requests"], "paid_requests": 0,
              "replay_cases": len(replay["receipts"]), "replay_requests": 0,
              "protected_files_unchanged": len(protocol["protected_files"]),
              "offline_tests": sum(int(s.attrib["tests"]) for s in tests),
              "wheel_sha256": sha(wheel), "wheel_matches_sources": True,
              "junit_sha256": sha(ROOT / "artifacts/p62c-tests.xml"),
              "review_sha256": sha(OUT / "semantic-review.json"), "local_doc_links": links,
              "case_sha256": {p.name: sha(p) for p in sorted((OUT / "cases").glob("*.json"))},
              "human_approved": False, "training_eligible": False}
    write(OUT / "acceptance.json", record)
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
