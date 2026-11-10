import hashlib
import json
from types import SimpleNamespace

import pytest

from repomentor.answer_audit import (
    audit_answer,
    command_audit,
    dependency_audit,
    line_hints,
    reference_audit,
)


def reader(files):
    return SimpleNamespace(paths=list(files), read=lambda path, limit: (files[path], False))


def span(path, content, start=1):
    return {
        "path": path,
        "content": content,
        "start_line": start,
        "end_line": start + len(content.splitlines()) - 1,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


@pytest.mark.parametrize("text", ["runs 19 tests", "L9 explains 10", "L0", "L9-L2", "", "L2, bad"])
def test_locator_does_not_turn_prose_numbers_into_evidence(text):
    assert line_hints(text) is None


def test_citation_union_gap_and_snapshot_bounds_are_distinct():
    repo = reader({"a.ts": "one\ntwo\nthree\nfour"})
    ref = {"path": "a.ts", "supports": "L1-L2, L4"}
    context = [span("a.ts", "one"), span("a.ts", "two", 2), span("a.ts", "four", 4)]
    assert reference_audit(repo, context, ref)["status"] == "visible_range"
    assert reference_audit(repo, context, {**ref, "supports": "L1-L4"})["status"] == "range_not_visible"
    assert reference_audit(repo, context, {**ref, "supports": "L1-L9"})["status"] == "invalid_line_range"


def test_valid_citation_does_not_score_false_answer_and_tampering_fails_closed():
    repo = reader({"a.ts": "throw Error('negative')"})
    source = span("a.ts", "throw Error('negative')")
    answer = {
        "answer": "Negative sizes return empty strings.",
        "citations": [{"path": "a.ts", "start_line": 1, "end_line": 1}],
    }
    audit = audit_answer(repo, answer, [source])
    assert audit["claims"][0]["references"][0]["status"] == "visible_range"
    assert audit["claims"][0]["support"] == "needs_review"
    assert audit["semantic_correctness"] == "not_scored" and not audit["training_eligible"]
    broken = {**source, "content": "return ''"}
    assert audit_answer(repo, answer, [broken])["source_integrity"] == "invalid"


def test_static_imports_ignore_comments_and_strings_but_keep_true_self_import():
    text = "// import './a.js';\nconst fake = `import './a.js'`;\nexport { b } from './b.js';"
    repo = reader({"a.ts": text, "b.ts": "export const b = 1"})
    rows = dependency_audit(repo, [span("a.ts", text)], {"a.ts": ["a.ts", "b.ts"]})
    assert [r["status"] for r in rows] == ["not_a_static_import", "supported_static_import"]
    assert rows[1]["source_visible_to_model"]
    repo = reader({"a.js": "import './a.js'"})
    assert dependency_audit(repo, [], {"a.js": ["a.js"]})[0]["status"] == "supported_static_import"


@pytest.mark.parametrize("text", ["import(name)", "require('./b')", "import x from '@alias/b'", "import {"])
def test_unresolved_or_invalid_imports_are_not_false_negative_edges(text):
    repo = reader({"a.ts": text, "b.ts": "export const b = 1"})
    assert dependency_audit(repo, [], {"a.ts": ["b.ts"]})[0]["status"] == "unknown"


def test_ambiguous_extensions_directory_nodes_and_unsupported_languages_stay_unknown():
    repo = reader({"a.ts": "import './b'", "b.ts": "", "b.js": "", "c.py": "pass"})
    for graph in ({"a.ts": ["b.ts"]}, {"src": ["b.ts"]}, {"c.py": ["b.ts"]}):
        assert dependency_audit(repo, [], graph)[0]["status"] == "unknown"
    assert dependency_audit(repo, [], {"a.ts": []}) == []


def test_script_declaration_visibility_is_separate_from_execution_and_shell_parsing():
    package = json.dumps({"scripts": {"build": "microbundle", "test": "mocha"}}, indent=2)
    repo = reader({"package.json": package})
    rows = command_audit(repo, [], {"build": "npm run build", "test": "npm test", "lint": "npm run lint"})
    assert [r["status"] for r in rows] == ["declared_script", "declared_script", "not_declared"]
    assert all(r["execution"] == "not_run" and not r["source_visible_to_model"] for r in rows)
    line = rows[0]["start_line"]
    observed = [span("package.json", package.splitlines()[line - 1], line)]
    rows = command_audit(
        repo, observed, {"build": "npm run build", "test": "npm test", "lint": "cd sub && npm run lint"}
    )
    assert rows[0]["source_visible_to_model"] and not rows[1]["source_visible_to_model"]
    assert rows[2]["status"] == "unknown"


@pytest.mark.parametrize("package", ['{"scripts":{"build":"a","build":"b"}}', '{"scripts":[]}', "null"])
def test_malformed_or_duplicate_package_scripts_stay_unknown(package):
    rows = command_audit(reader({"package.json": package}), [], {"build": "npm run build"})
    assert rows[0]["status"] == "unknown"


def test_missing_generation_is_a_preserved_failure_not_an_empty_success():
    result = audit_answer(reader({}), None, [])
    assert result["answer_status"] == "missing" and result["review_required"]
