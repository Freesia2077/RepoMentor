import copy
from types import SimpleNamespace

from repomentor.contracts import Coverage, MentorOutput
from repomentor.presentation import render_run
from repomentor.report_constraints import constrain_report
from repomentor.report_validation import inspect_report_locations
from repomentor.sdk_report import report_sources
from repomentor.source_windows import source_span


def test_saved_report_view_exposes_failures_and_does_not_imply_semantic_approval():
    text = render_run(
        {
            "report": {
                "status": "failed",
                "stop_reason": "report_grounding",
                "outputs": {
                    "explorer": {
                        "projectSummary": "已有概览",
                        "evidenceClaims": [],
                        "evidenceCoverage": {"gaps": []},
                    }
                },
            },
            "stages": [],
            "raw_untrusted_prompt": "DO_NOT_RENDER",
        }
    )
    assert "已有概览" in text and "report_grounding" in text
    assert "本阶段未发布成功结果" in text and "不代表内容已获语义审核" in text
    assert "DO_NOT_RENDER" not in text


def test_report_seeds_include_small_entry_body_and_respect_byte_bound():
    files = {
        "src/index.ts": "\n".join(f"line {i}" for i in range(1, 124)),
        "package.json": '{"scripts":{"test":"test"}}',
        "README.md": "usage",
    }
    reader = SimpleNamespace(read=lambda path, maximum: (files[path], False))
    profile = {
        "entryCandidates": ["src/index.ts"],
        "manifests": [{"path": "package.json"}],
        "readme": {"path": "README.md"},
        "testCandidates": [],
    }
    spans = report_sources(reader, profile, 2000)
    assert spans[0]["end_line"] == 123
    assert sum(len(s["content"].encode()) for s in spans) <= 2000
    assert spans[0]["content"].splitlines()[-1] == "line 123"


def test_explicit_report_locations_distinguish_bounds_visibility_and_semantics():
    text = "\n".join(f"line {i}" for i in range(1, 124))
    reader = SimpleNamespace(paths=["src/index.ts"], read=lambda path, maximum: (text, False))
    context = [source_span("src/index.ts", text.splitlines(), 1, 50)]
    value = {
        "evidenceClaims": [
            {
                "evidence": [
                    {
                        "path": "src/index.ts",
                        "supports": "L26-27 declaration; L189–202 wrong file; L52 map; L20-L18 invalid; L1 unrelated claim",
                    }
                ]
            }
        ]
    }
    before = copy.deepcopy(value)
    result = inspect_report_locations(reader, value, context)
    assert result["checked_ranges"] == 5
    assert [r["reason"] for r in result["issues"]] == [
        "out_of_bounds",
        "not_visible_to_finalizer",
        "invalid_range",
    ]
    assert result["semantic_correctness"] == "not_scored"
    assert value == before


def test_explicit_report_locations_do_not_read_untracked_paths():
    def read(path, maximum):
        raise AssertionError("must not read an untracked path")

    reader = SimpleNamespace(paths=[], read=read)
    value = {"evidenceClaims": [{"evidence": [{"path": "../private", "supports": "L1"}]}]}
    assert inspect_report_locations(reader, value, [])["issues"][0]["reason"] == "not_in_snapshot"


def test_publication_constraints_preserve_raw_report_and_only_admit_visible_declarations():
    files = {
        "src/a.js": "import {b} from './b.js'\nexport const a = b\n",
        "src/b.js": "export const b = 1\n",
        "src/c.js": "export const c = 2\n",
        "package.json": '{\n  "scripts": {"test": "vitest", "build": "tsc"}\n}',
    }
    reader = SimpleNamespace(paths=list(files), read=lambda path, maximum: (files[path], False))
    context = [
        source_span(p, files[p].splitlines(), 1, len(files[p].splitlines()))
        for p in ("src/a.js", "package.json")
    ]
    raw = {
        "dependencyGraph": {"src/a.js": ["src/b.js", "src/c.js"]},
        "contributionSetup": {
            "devEnv": None,
            "build": "npm run build",
            "test": "npm test",
            "lint": "npm run lint",
        },
        "evidenceCoverage": {"gaps": []},
    }
    before = copy.deepcopy(raw)
    published, changes = constrain_report(reader, raw, context)
    assert raw == before
    assert published["dependencyGraph"] == {"src/a.js": ["src/b.js"]}
    assert published["contributionSetup"]["test"] == "npm test"
    assert published["contributionSetup"]["lint"] is None
    assert len(changes) == 2
    Coverage.model_validate({"examinedFiles": [], "gaps": published["evidenceCoverage"]["gaps"]})
    hidden, _ = constrain_report(reader, raw, [])
    assert hidden["dependencyGraph"] == {}
    assert all(v is None for v in hidden["contributionSetup"].values())


def test_published_report_stays_schema_valid_with_full_gap_capacity():
    reader = SimpleNamespace(paths=["a.js", "b.js"], read=lambda path, maximum: ("export const x = 1", False))
    original = MentorOutput(
        architectureOverview="fixture",
        dependencyGraph={"a.js": ["b.js"]},
        readingPath=[],
        keyPatterns=[],
        codeConventions=[],
        evidenceClaims=[],
        evidenceCoverage={
            "examinedFiles": [],
            "gaps": [
                {
                    "kind": "coverage_limit",
                    "subject": f"gap-{i}",
                    "summary": "original gap",
                    "severity": "low",
                }
                for i in range(8)
            ],
        },
    ).model_dump()
    published, changes = constrain_report(reader, original, [])
    checked = MentorOutput.model_validate(published)
    assert checked.dependencyGraph == {}
    assert len(checked.evidenceCoverage.gaps) == 8
    assert checked.evidenceCoverage.gaps[-1].kind == "missing_evidence"
    assert original["evidenceCoverage"]["gaps"][-1]["subject"] == "gap-7"
    assert any(c["original"] == original["evidenceCoverage"]["gaps"][-1] for c in changes)
