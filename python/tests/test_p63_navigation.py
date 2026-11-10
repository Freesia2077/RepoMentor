import json
from types import SimpleNamespace

import pytest

from repomentor.code_navigation import SymbolIndex, explicit_source_paths, file_role
from repomentor.code_retrieval import CodeRetrievalIndex
from repomentor.contracts import CodeChunk
from repomentor.entry_scope import resolve_entry
from repomentor.retrieval import RetrievalSettings
from repomentor.retrieval_scope import SearchScope


def reader(files):
    return SimpleNamespace(
        paths=list(files),
        snapshot=SimpleNamespace(snapshot_id="static"),
        read=lambda path, maximum: (files[path], False),
    )


@pytest.mark.parametrize(
    "path,source,name",
    [
        ("tests/test_records.py", "class PayloadRecord:\n    pass\n", "PayloadRecord"),
        ("src/z_handler.py", "class Handler:\n    def _finish_request(self): pass\n", "_finish_request"),
    ],
)
def test_named_identifier_precedes_incidental_prose_matches(path, source, name):
    words = ("file", "line", "module", "location", "structure", "context", "method")
    noise = "class Noise:\n" + "".join(f"    def {word}(self): pass\n" for word in words)
    files = {"src/a_noise.py": noise, path: source}
    index = SymbolIndex.build(reader(files), files)
    query = f"Where is {name} defined? " + " ".join(words)
    assert index.lookup(query)["targets"][0]["path"] == path
    assert all(t["path"] != path for t in index.lookup(query, excluded=[path])["targets"])


@pytest.mark.parametrize(
    "query",
    [
        "The Flask application runner coordinates command execution in isolation.",
        "How should a test plugin enforce registration policies at runtime?",
        "Why does a stream plugin process configuration resource files repeatedly?",
    ],
)
def test_prose_preserves_ranked_evidence_instead_of_incidental_symbols(tmp_path, query):
    files = {
        "src/actual.py": "def load_cli():\n    return 'actual'\n",
        "src/app.py": "class Flask:\n    pass\n",
        "tests/conftest.py": "def runner(): pass\ndef test(): pass\ndef stream(): pass\ndef plugin(): pass\n",
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=len(text.splitlines()),
            content=text.rstrip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": chunks[0].model_dump(), "score": 1}]},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    result = index.search(query)
    assert [r["chunk"]["path"] for r in result["candidates"]] == ["src/actual.py"]
    assert not result["symbol_channel_targets"]
    for explicit in (
        "Where is `runner` defined in this repository?",
        "How does runner() execute this command?",
    ):
        assert index.symbols.lookup(explicit)["targets"][0]["symbol"] == "runner"


@pytest.mark.parametrize(
    "root_path,blocked",
    [
        ("tests/conftest.py", False),
        ("examples/demo.py", False),
        ("src/factory.py", True),
    ],
)
def test_test_fixture_name_does_not_bind_an_application_receiver(root_path, blocked):
    files = {
        "src/core.py": "class Application:\n    def errorhandler(self, code): pass\n",
        root_path: "def app(): return None\n",
    }
    index = SymbolIndex.build(reader(files), files)
    targets = index.lookup("app.errorhandler(404)")["targets"]
    assert any(d["symbol"] == "Application.errorhandler" for d in targets) is not blocked
    scoped = index.lookup("app.errorhandler(404)", path=root_path)["targets"]
    assert all(d["path"] == root_path for d in scoped)


@pytest.mark.parametrize(
    "path,source,query,target",
    [
        (
            "store.py",
            "class Storage:\n    def write(self):\n        return self.flush()\n    def flush(self):\n        return 1\n",
            "Storage.write",
            "Storage.flush",
        ),
        (
            "option.js",
            "class Option { choices() { return this._collectValue(); } _collectValue() { return []; } }",
            "Option.choices",
            "Option._collectValue",
        ),
        (
            "option.ts",
            "class Option { choices(): string[] { return this.collect(); } collect(): string[] { return []; } }",
            "Option.choices",
            "Option.collect",
        ),
    ],
)
def test_members_are_locations_without_claiming_runtime_dispatch(path, source, query, target):
    index = SymbolIndex.build(reader({path: source}), [path])
    result = index.lookup(query, path=path, relation="callees")
    assert [d["symbol"] for d in result["targets"]] == [target]
    assert result["relations"][0]["resolution"] == "lexical_member_candidate"
    assert result["source_facts"] is False


def test_configuration_guard_recalls_implementation_before_same_named_test_callback(tmp_path):
    files = {
        "runner.py": "class Runner:\n    def decide(self, state):\n"
        "        if not state.retry:\n            return state.result\n"
        "        if self.after is not None:\n            self.queue(self.after)\n",
        "tests/test_runner.py": "def test_case():\n    def after(state):\n        return state\n",
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=len(text.splitlines()),
            content=text.rstrip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=1),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": chunks[1].model_dump(), "score": 1}]},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    result = index.initial_sources("after 首次成功也会执行吗？")
    assert result["hits"][0]["unit"]["symbol"] == "Runner.decide"
    assert "if not state.retry:" in result["sources"][0]["content"]
    assert "self.queue(self.after)" in result["sources"][0]["content"]
    target = index.symbols.lookup("after")["targets"][0]
    assert target["match_kind"] == "attribute_guard"
    assert index.symbols.lookup("after")["source_facts"] is False


@pytest.mark.parametrize(
    "body",
    [
        "    @staticmethod\n    def check(self):\n        if self.after: return 1\n",
        "    @builtins.staticmethod\n    def check(self):\n        if self.after: return 1\n",
        "    def check(self):\n        self = other\n        if self.after: return 1\n",
        "    def check(self):\n        def inner(self):\n            if self.after: return 1\n",
        "    def check(self):\n        if any(self.after for self in others): return 1\n",
        "    def check(self):\n        if (lambda self: self.after)(other): return 1\n",
        "    def check(self):\n        text = 'if self.after: return 1'\n        if self.child.after: return 1\n",
    ],
)
def test_guard_recall_does_not_bind_shadowed_or_indirect_receivers(body):
    index = SymbolIndex.build(reader({"runner.py": "class Runner:\n" + body}), ["runner.py"])
    assert not index.lookup("after")["targets"]


def test_guard_recall_respects_owner_paths_and_qualified_unknown_names():
    files = {
        "a.py": "class A:\n    def check(self):\n        if self.after: return 1\n",
        "b.py": "class B:\n    def check(self):\n        while self.after: break\n",
    }
    index = SymbolIndex.build(reader(files), files)
    assert {d["symbol"] for d in index.lookup("A after")["targets"]} == {"A", "A.check"}
    assert {d["symbol"] for d in index.lookup("A after OtherA()")["targets"]} == {"A", "A.check"}
    assert [d["path"] for d in index.lookup("after", path="b.py")["targets"]] == ["b.py"]
    assert [d["path"] for d in index.lookup("after", excluded=["a.py"])["targets"]] == ["b.py"]
    assert not index.lookup("other.after")["targets"]
    assert not index.lookup("A after", excluded=["a.py"])["targets"]
    assert not index.lookup("A.check after", path="b.py")["targets"]


def test_guard_fallback_does_not_treat_policy_constructor_as_field_owner():
    files = {
        "runner.py": "class Runner:\n    def decide(self):\n        if self.after: return 1\n"
        "class Limit:\n    def __init__(self, count): pass\n"
    }
    index = SymbolIndex.build(reader(files), files)
    assert any(d["symbol"] == "Runner.decide" for d in index.lookup("Limit(3), after")["targets"])


def test_guard_fallback_preserves_existing_entry_and_its_dependencies():
    files = {
        "runner.py": "class Runner:\n    def retry_with(self):\n        return self.copy()\n"
        "    def copy(self):\n        return Runner()\n"
        "    def unrelated(self):\n        if self.enabled: return 1\n"
    }
    index = SymbolIndex.build(reader(files), files)
    targets = index.lookup("wrapped.retry_with(enabled=False)")["targets"]
    assert [d["symbol"] for d in targets] == ["Runner.retry_with"]
    assert [d["symbol"] for d in index.lookup(targets[0]["symbol"], relation="callees")["targets"]] == [
        "Runner.copy"
    ]


@pytest.mark.parametrize(
    "path,source,query",
    [
        (
            "store.py",
            "class A:\n    @staticmethod\n    def run(self):\n        return self.flush()\n    def flush(self): pass\n",
            "A.run",
        ),
        (
            "store.py",
            "class A:\n    def run(self):\n        self = other\n        return self.flush()\n    def flush(self): pass\n",
            "A.run",
        ),
        (
            "store.py",
            "class A:\n    def run(self):\n        def inner(self):\n            return self.flush()\n        return inner(other)\n    def flush(self): pass\n",
            "A.run.inner",
        ),
        (
            "store.py",
            "class A:\n    def flush(self): pass\nclass B(A):\n    def run(self):\n        return self.flush()\n",
            "B.run",
        ),
        (
            "store.js",
            "class A { run() { function inner() { return this.flush(); } return inner(); } flush() {} }",
            "A.run",
        ),
        ("store.js", "class A { static run() { return this.flush(); } flush() {} }", "A.run"),
    ],
)
def test_unknown_receivers_and_inherited_or_static_mismatch_stay_unresolved(path, source, query):
    index = SymbolIndex.build(reader({path: source}), [path])
    result = index.lookup(query, path=path, relation="callees")
    assert not any(t["symbol"].endswith(".flush") for t in result["targets"])


@pytest.mark.parametrize(
    "question,expected",
    [
        ("在 non-secure 入口 customAlphabet，不要套用 Node 安全入口的异常规则", "non-secure"),
        ("使用 non-secure，不是 Node", "non-secure"),
        ("不是 Node 而是 browser 入口", "browser"),
        ("non-secure, do not use Node", "non-secure"),
        ("比较 non-secure 与 Node 的不同", None),
        ("Node 和 browser 有什么区别", None),
    ],
)
def test_negated_entry_does_not_disable_explicit_scope(question, expected):
    files = {
        "package.json": json.dumps(
            {
                "exports": {
                    ".": {"node": "./index.js", "browser": "./browser.js"},
                    "./non-secure": "./non-secure/index.js",
                }
            }
        ),
        "index.js": "export function id() {}",
        "browser.js": "export function id() {}",
        "non-secure/index.js": "export function id() {}",
    }
    result = resolve_entry(reader(files), question)
    assert result["label"] == expected
    if expected == "non-secure":
        assert {"index.js", "browser.js"} <= set(result["excluded_paths"])
    if expected is None:
        assert not result["excluded_paths"]


def test_python_import_alias_local_alias_and_dynamic_resolution(tmp_path):
    files = {
        "src/pkg/impl.py": "def target():\n    return 1\n",
        "src/pkg/wrap.py": "from .impl import target as renamed\nalias = renamed\ndef wrapper():\n    return alias()\ndef dynamic(target):\n    return target()\n",
    }
    index = SymbolIndex.build(reader(files), files, tmp_path)
    result = index.lookup("wrapper", relation="callees")
    assert result["targets"][0]["path"] == "src/pkg/impl.py"
    assert result["targets"][0]["symbol"] == "target"
    assert index.lookup("dynamic", relation="callees")["relations"][0]["resolution"] == "unresolved"
    assert index.lookup("target", relation="callers")["targets"][0]["path"] == "src/pkg/wrap.py"
    assert SymbolIndex.build(reader(files), files, tmp_path).record == index.record
    navigation = index.navigation("wrapper", {"excluded_paths": []})
    assert [r["symbol"] for r in navigation["related_definitions"]] == ["target"]
    assert navigation["source_facts"] is False
    assert not index.navigation("wrapper", {"excluded_paths": ["src/pkg/impl.py"]})["related_definitions"]


@pytest.mark.parametrize(
    "alias,shadowed,excluded,expected",
    [
        (True, False, [], True),
        (False, False, [], False),
        (True, True, [], False),
        (True, False, ["pkg/impl.py"], False),
    ],
)
def test_alias_declaration_is_recalled_with_body_within_existing_budget(
    tmp_path, alias, shadowed, excluded, expected
):
    name = "ParserError" if alias else "SemanticError"
    header = "from .impl import SemanticError" + (" as ParserError" if alias else "")
    body = f"    def parse(self{', ' + name if shadowed else ''}):\n        raise {name}('bad')\n"
    files = {
        "pkg/client.py": header + "\nclass Client:\n" + body,
        "pkg/impl.py": "class SemanticError(Exception):\n    pass\n",
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=key,
            path=path,
            start_line=first,
            end_line=last,
            content=content,
            content_hash="fixture",
            symbol=symbol,
        )
        for key, path, first, last, content, symbol in [
            ("header", "pkg/client.py", 1, 1, header, None),
            ("body", "pkg/client.py", 3, 4, body.rstrip(), "Client.parse"),
            ("error", "pkg/impl.py", 1, 2, files["pkg/impl.py"].rstrip(), "SemanticError"),
        ]
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=2),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, scope=None, **kw: {
            "candidates": [
                {"chunk": c.model_dump(), "score": 1}
                for c in chunks[1:]
                if scope is None or scope.matches(c.path)
            ]
        },
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    result = index.initial_sources("Client.parse", excluded=excluded)
    assert any("def parse(" in s["content"] for s in result["sources"])
    assert any(header in s["content"] for s in result["sources"]) is expected
    assert all(s["path"] not in excluded for s in result["sources"])
    assert len(result["hits"]) <= 2
    assert not any("import_binding" in c["channels"] for c in result["retrieval"]["candidates"])
    if expected:
        assert len(result["sources"]) == 3  # Two selected units plus their import dependency.
        assert result["hits"][0]["dependency_sources"][0]["start_line"] == 1
    assert sum(len(s["content"].encode()) for s in result["sources"]) <= 8000


@pytest.mark.parametrize(
    "module_binding,local_binding",
    [
        ("", "        from .other import LocalError as ParserError\n"),
        ("", "        def ParserError():\n            return None\n"),
        ("ParserError = make_error()\n", ""),
        ("def ParserError():\n    return None\n", ""),
        ("ParserError = OtherError\n", ""),
        ("del ParserError\n", ""),
    ],
)
def test_replaced_import_name_is_not_promoted_as_alias_evidence(module_binding, local_binding):
    files = {
        "pkg/client.py": "from .impl import SemanticError as ParserError\n"
        + module_binding
        + "class Client:\n    def parse(self):\n"
        + local_binding
        + "        raise ParserError('bad')\n",
        "pkg/impl.py": "class SemanticError(Exception):\n    pass\n",
        "pkg/other.py": "class LocalError(Exception):\n    pass\n",
    }
    index = SymbolIndex.build(reader(files), files)
    calls = index.lookup("Client.parse", path="pkg/client.py", relation="callees")["relations"]
    assert calls
    assert not any(r.get("import_binding") for r in calls)


@pytest.mark.parametrize("budget,expected", [(8000, True), (1000, False)])
def test_import_split_across_chunks_is_delivered_whole_or_skipped(tmp_path, budget, expected):
    header = "from .impl import (\n" + "    # padding\n" * 100 + "    SemanticError as ParserError,\n)\n"
    header_lines = header.splitlines()
    body = "    def parse(self):\n        raise ParserError('bad')\n"
    first = len(header_lines) + 2
    files = {
        "pkg/client.py": header + "class Client:\n" + body,
        "pkg/impl.py": "class SemanticError(Exception):\n    pass\n",
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=key,
            path=path,
            start_line=start,
            end_line=end,
            content=content,
            content_hash="fixture",
            symbol=symbol,
        )
        for key, path, start, end, content, symbol in [
            ("body", "pkg/client.py", first, first + 1, body.rstrip(), "Client.parse"),
            ("error", "pkg/impl.py", 1, 2, files["pkg/impl.py"].rstrip(), "SemanticError"),
            ("import-head", "pkg/client.py", 1, 50, "\n".join(header_lines[:50]), None),
            ("import-tail", "pkg/client.py", 51, len(header_lines), "\n".join(header_lines[50:]), None),
        ]
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=2, initial_source_bytes=budget),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": c.model_dump(), "score": 1} for c in chunks[:2]]},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    result = index.initial_sources("Client.parse")
    assert not any("import_binding" in c["channels"] for c in result["retrieval"]["candidates"])
    declarations = [s for s in result["sources"] if "from .impl import" in s["content"]]
    assert bool(declarations) is expected
    if expected:
        assert declarations[0]["content"] == header.rstrip()
    assert len(result["hits"]) == 2
    assert sum(len(s["content"].encode()) for s in result["sources"]) <= budget


def test_import_dependency_is_not_attached_for_a_call_outside_actual_windows(tmp_path):
    body = (
        "    def parse(self):\n"
        + "        # unrelated padding to keep the method large\n" * 200
        + "        raise ParserError('bad')\n"
        + "        # trailing padding\n" * 200
    )
    files = {
        "pkg/client.py": "from .impl import SemanticError as ParserError\nclass Client:\n" + body,
        "pkg/impl.py": "class SemanticError(Exception):\n    pass\n",
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=path,
            path=path,
            start_line=1,
            end_line=len(text.splitlines()),
            content=text.rstrip(),
            content_hash="fixture",
        )
        for path, text in files.items()
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=2),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": c.model_dump(), "score": 1} for c in chunks]},
    )
    result = CodeRetrievalIndex(reader(files), base, tmp_path).initial_sources("Client.parse")
    assert any(h["unit"]["symbol"] == "Client.parse" and h["truncated"] for h in result["hits"])
    assert not any("raise ParserError" in s["content"] for s in result["sources"])
    assert not any("from .impl import" in s["content"] for s in result["sources"])


def test_js_import_calls_and_unresolved_receiver():
    files = {
        "src/helper.ts": "export function helper() { return 1; }",
        "src/main.ts": "import { helper as alias } from './helper';\nexport function run() { alias(); obj.dynamic(); }",
    }
    index = SymbolIndex.build(reader(files), files)
    result = index.lookup("run", relation="callees")
    assert result["targets"][0]["symbol"] == "helper"
    assert any(r["resolution"] == "unresolved" for r in result["relations"])


def test_full_index_ambiguity_and_role():
    files = {f"src/{i:03}.py": f"def unrelated_{i}():\n    pass\n" for i in range(90)}
    files.update({"src/zzz.py": "def real():\n    return 1", "examples/app.py": "def real():\n    return 2"})
    index = SymbolIndex.build(reader(files), files)
    found = index.lookup("real")
    assert len(found["targets"]) == 2 and found["targets"][0]["path"] == "src/zzz.py"
    assert found["coverage"]["indexed_files"] == 92
    assert file_role("tests/test_x.py") == "test"


def test_js_local_binding_does_not_resolve_to_module_import():
    files = {
        "helper.js": "export function target() { return 1; }",
        "main.js": "import {target} from './helper';\nfunction run() { const target = other; return target(); }",
    }
    index = SymbolIndex.build(reader(files), files)
    result = index.lookup("run", relation="callees")
    assert not result["targets"]
    assert result["relations"][0]["resolution"] == "unresolved"


def test_shared_initial_retrieval_recalls_callee_without_leaking_excluded_path(tmp_path):
    files = {
        "wrapper.py": "from helper import branch\ndef wrapper():\n    return branch()\n",
        "helper.py": "def branch():\n    return 'actual condition'\n",
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=len(text.splitlines()),
            content=text.rstrip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": chunks[0].model_dump(), "score": 1}]},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    found = index.search("wrapper")
    assert any(
        r["chunk"]["path"] == "helper.py" and "static_callee" in r["channels"] for r in found["candidates"]
    )
    assert any("actual condition" in s["content"] for s in index.initial_sources("wrapper")["sources"])
    excluded = index.search("wrapper", scope=SearchScope(exclude_paths=["helper.py"]))
    assert not excluded["static_callee_targets"]
    assert all(r["chunk"]["path"] != "helper.py" for r in excluded["candidates"])


@pytest.mark.parametrize(
    "question,excluded,ambiguous,code_aware,expected",
    [
        ("Store.erase(ids=[7, 99]) 与 Store.erase()，哪些测试断言支持？", [], False, True, True),
        ("Store.erase(ids=[7, 99])，哪些测试断言支持？", [], False, True, False),
        ("Store.erase() 的实现是什么？", [], False, True, False),
        ("Store.erase()，哪些测试断言支持？", ["tests/test_default.py"], False, True, False),
        ("store.erase()，哪些测试断言支持？", [], True, True, False),
        ("Store.erase()，哪些测试断言支持？", [], False, False, False),
    ],
)
def test_explicit_empty_call_recalls_test_without_widening_scope_or_budget(
    tmp_path,
    question,
    excluded,
    ambiguous,
    code_aware,
    expected,
):
    files = {
        "store.py": "class Store:\n    def erase(self, ids=None):\n"
        "        if ids is None: raise RuntimeError()\n        return ids\n",
        "tests/test_regular.py": "def test_regular():\n    assert store.erase(ids=[7, 99]) == [7]\n",
        "tests/test_default.py": "def test_default():\n    with pytest.raises(RuntimeError):\n        store.erase()\n",
    }
    if ambiguous:
        files["other.py"] = "class Other:\n    def erase(self):\n        return None\n"
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=len(text.splitlines()),
            content=text.rstrip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    # Ordinary lexical recall misses the no-argument boundary. The real literal
    # retrieval still searches all admitted chunks, subject to the same scope.
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=2, candidate_k=3),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {
            "candidates": [
                {"chunk": c.model_dump(), "score": 1}
                for c in chunks[:2]
                if kw.get("scope", SearchScope()).matches(c.path)
            ]
        },
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path, code_aware=code_aware)
    result = index.initial_sources(question, excluded=excluded)
    assert any(s["path"] == "tests/test_default.py" for s in result["sources"]) is expected
    assert len(result["hits"]) <= 2
    assert sum(len(s["content"].encode()) for s in result["sources"]) <= base.settings.initial_source_bytes
    assert not set(excluded) & {s["path"] for s in result["sources"]}


def test_documentation_hook_does_not_override_business_source_unless_file_is_named(tmp_path):
    files = {
        "docs/conf.py": "def setup(app):\n    return 'documentation build'\n",
        "src/guard.py": "def check_registered():\n    if registered:\n        raise RuntimeError('setup is closed')\n",
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=len(text.splitlines()),
            content=text.rstrip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=1),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": chunks[1].model_dump(), "score": 1}]},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    assert (
        index.initial_sources("Why are setup changes blocked after registration?")["sources"][0]["path"]
        == "src/guard.py"
    )
    assert index.initial_sources("Explain setup in docs/conf.py")["sources"][0]["path"] == "docs/conf.py"
    assert file_role("docs/conf.py") == "documentation"
    assert file_role("docs/examples/app.py") == "example" and file_role("docs/tests/test_app.py") == "test"


def test_symbol_owner_precedes_same_named_methods_before_limit():
    source = "\n".join(f"class Other{i}:\n    def fetch(self):\n        return {i}\n" for i in range(12))
    source += "\nclass TargetStore:\n    def fetch(self):\n        return 99\n"
    index = SymbolIndex.build(reader({"store.py": source}), ["store.py"])
    rows = index.lookup("TargetStore的 fetch 如何工作？")["targets"]
    assert rows[0]["symbol"] == "TargetStore.fetch"
    assert len(rows) == 6  # Keep ambiguous alternatives, without losing the requested owner.


def test_qualified_method_supplies_owner_for_other_named_members():
    source = "\n".join(f"class Other{i}:\n    def close(self): pass\n" for i in range(12))
    source += "\nclass Store:\n    def write(self): pass\n    def close(self): pass\n"
    rows = SymbolIndex.build(reader({"store.py": source}), ["store.py"]).lookup("Store.write 和 close")[
        "targets"
    ]
    assert [r["symbol"] for r in rows[:2]] == ["Store.write", "Store.close"]


def test_same_chunk_retains_member_anchors_and_prioritizes_root_dependency(tmp_path):
    files = {
        "store.py": "class Store:\n    def write(self):\n        return self.flush()\n"
        "    def flush(self):\n        return 42\n    def close(self):\n        self.flush()\n"
    }
    files.update({f"other{i}.py": f"class Other{i}:\n    def close(self): pass\n" for i in range(12)})
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=len(text.splitlines()),
            content=text.rstrip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=3),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": chunks[0].model_dump(), "score": 1}]},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    selected = index.initial_sources("Store.write 和 close 如何工作？")
    assert {h["unit"]["symbol"] for h in selected["hits"]} == {"Store.write", "Store.flush", "Store.close"}
    assert {s["path"] for s in selected["sources"]} == {"store.py"}


def test_symbol_scope_is_applied_before_six_target_limit(tmp_path):
    files = {f"src/a{i}.py": "def execute():\n    return 0\n" for i in range(8)}
    files["src/z.py"] = "def execute():\n    return 42\n"
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=2,
            content=text.rstrip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": []},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    result = index.search("execute", scope=SearchScope(path="src/z.py"))
    assert [t["path"] for t in result["symbol_channel_targets"]] == ["src/z.py"]
    assert [c["chunk"]["path"] for c in result["candidates"]] == ["src/z.py"]


def test_module_qualified_symbol_resolves_src_layout_without_suffix_guessing():
    files = {
        "src/pkg/keys.py": "def methodkey(self):\n    return hashkey()\ndef hashkey():\n    return 1\n",
        "other.py": "def methodkey(self):\n    return 2\n",
    }
    index = SymbolIndex.build(reader(files), files)
    result = index.lookup("pkg.keys.methodkey会如何委派 hashkey？")
    assert result["targets"][0]["path"] == "src/pkg/keys.py"
    assert result["targets"][0]["symbol"] == "methodkey"
    assert not index.lookup("unknown.keys.methodkey")["targets"]


@pytest.mark.parametrize("query", ["sig.release()", "sig.release 的行为"])
def test_instance_member_recalls_ambiguous_methods_without_resolving_receiver(query):
    files = {
        "a.py": "class Alpha:\n    def release(self): pass\ndef release(): pass\n",
        "b.py": "class Beta:\n    def release(self): pass\n",
        "tests/test_a.py": "def test_release():\n    def release(): pass\n",
    }
    result = SymbolIndex.build(reader(files), files).lookup(query)
    assert {r["symbol"] for r in result["targets"]} == {"Alpha.release", "Beta.release"}
    assert all(r["match_kind"] == "member_name_only" and r["match_priority"] < 5 for r in result["targets"])
    assert result["source_facts"] is False


def test_instance_member_fallback_respects_qualified_owner_and_scope():
    files = {
        "a.py": "class Alpha:\n    def release(self): pass\n",
        "b.py": "class Beta:\n    def release(self): pass\n    def missing(self): pass\n",
    }
    index = SymbolIndex.build(reader(files), files)
    assert [r["symbol"] for r in index.lookup("Alpha.release")["targets"]] == ["Alpha.release"]
    assert not index.lookup("Alpha.missing")["targets"]
    assert not index.lookup("Alpha.release", path="b.py")["targets"]
    assert [r["symbol"] for r in index.lookup("sig.release", path="b.py")["targets"]] == ["Beta.release"]
    assert [r["symbol"] for r in index.lookup("sig.release", excluded=["b.py"])["targets"]] == [
        "Alpha.release"
    ]
    assert not index.lookup("unknown.pkg.release")["targets"]


def test_instance_entry_and_test_assertion_share_initial_source_budget(tmp_path):
    files = {
        "signal.py": "class Signal:\n    def connected_to(self, receiver):\n"
        "        self.connect(receiver)\n        try:\n            yield\n"
        "        finally:\n            self.disconnect(receiver)\n"
    }
    for i in range(7):
        files[f"tests/test_{i}.py"] = (
            f"def test_cleanup_{i}():\n    seen = []\n"
            "    def receiver(value):\n        seen.append(value)\n"
            "    with sig.connected_to(receiver):\n        sig.send(2)\n"
            "    assert seen == [2]\n    assert not sig.receivers\n"
        )
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=2 if p == "signal.py" else 1,
            end_line=len(text.splitlines()),
            content="\n".join(text.splitlines()[1:] if p == "signal.py" else text.splitlines()),
            symbol="Signal.connected_to" if p == "signal.py" else f"test_cleanup_{i - 1}",
            content_hash="fixture",
        )
        for i, (p, text) in enumerate(files.items())
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=6, initial_source_bytes=8000),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": c.model_dump(), "score": 1} for c in chunks[1:]]},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    found = index.initial_sources("sig.connected_to(receiver) 异常退出后怎么清理，请结合测试")
    assert any(
        s["path"] == "signal.py" and "finally:" in s["content"] and "disconnect(receiver)" in s["content"]
        for s in found["sources"]
    )
    assert any(
        s["path"].startswith("tests/") and "assert not sig.receivers" in s["content"]
        for s in found["sources"]
    )
    assert len(found["hits"]) <= 6
    assert sum(len(s["content"].encode()) for s in found["sources"]) <= 8000
    assert all(
        s["path"] != "signal.py"
        for s in index.initial_sources("sig.connected_to", excluded=["signal.py"])["sources"]
    )


def test_explicit_path_beats_generic_symbols_and_does_not_match_path_suffix(tmp_path):
    files = {
        "src/index.js": "if (process.type === 'renderer') { module.exports = require('./browser'); }\n",
        "src/common.js": "function debug() { return 'not the requested entry'; }\n",
        "other/src/index.js": "function debug() { return 'other'; }\n",
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=1,
            content=text.strip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": chunks[1].model_dump(), "score": 1}]},
    )
    index = CodeRetrievalIndex(reader(files), base, tmp_path)
    result = index.initial_sources("debug 的 src/index.js 如何选择 renderer 入口？")
    assert result["sources"][0]["path"] == "src/index.js"
    assert all(
        not r.get("explicit_path")
        for r in result["retrieval"]["candidates"]
        if r["chunk"]["path"] == "other/src/index.js"
    )
    assert all(
        s["path"] != "src/index.js"
        for s in index.initial_sources("debug 的 src/index.js", excluded=["src/index.js"])["sources"]
    )


def test_initial_top_k_is_respected_for_plain_and_augmented_retrieval(tmp_path):
    files = {f"f{i}.py": f"def task_{i}():\n    return {i}\n" for i in range(7)}
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=p,
            path=p,
            start_line=1,
            end_line=2,
            content=text.strip(),
            content_hash="fixture",
        )
        for p, text in files.items()
    ]
    for enabled in (False, True):
        base = SimpleNamespace(
            settings=RetrievalSettings(top_k=5, initial_source_bytes=8000),
            chunks=chunks,
            record={},
            index_id="base",
            search=lambda *a, **kw: {"candidates": [{"chunk": c.model_dump(), "score": 1} for c in chunks]},
        )
        index = CodeRetrievalIndex(reader(files), base, tmp_path, code_aware=enabled)
        assert len(index.initial_sources("task")["sources"]) == 5


def test_unique_filename_resolves_without_ambiguous_suffixes():
    paths = {"tests/options.custom.test.js", "src/index.js", "other/index.js"}
    assert explicit_source_paths("读options.custom.test.js", paths) == {"tests/options.custom.test.js"}
    assert not explicit_source_paths("读index.js", paths)
    assert explicit_source_paths("读src/index.js", paths) == {"src/index.js"}


def test_dependency_cannot_displace_other_explicit_entry_points(tmp_path):
    files = {
        "app.py": "class Parser:\n    def dict(self):\n        return self.parse()\n"
        "    def parse(self):\n        return 1\ndef load_values():\n    return 2\ndef load_env():\n    return 3\n"
    }
    chunk = CodeChunk(
        snapshot_id="static",
        chunk_id="one",
        path="app.py",
        start_line=1,
        end_line=9,
        content=files["app.py"].strip(),
        content_hash="fixture",
    )
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=4),
        chunks=[chunk],
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": []},
    )
    found = CodeRetrievalIndex(reader(files), base, tmp_path).initial_sources(
        "Parser.dict、parse、load_values、load_env"
    )
    assert {h["unit"]["symbol"] for h in found["hits"]} == {
        "Parser.dict",
        "Parser.parse",
        "load_values",
        "load_env",
    }


def test_exact_test_description_precedes_similar_handler_test(tmp_path):
    files = {
        "test/a.js": "it('should invoke handler', () => { expect(1); });\n"
        "it('should invoke * handlers', () => { expect(2); });\n"
    }
    chunks = [
        CodeChunk(
            snapshot_id="static",
            chunk_id=str(i),
            path="test/a.js",
            start_line=i,
            end_line=i,
            content=line,
            symbol="it:'" + title + "'",
            content_hash="fixture",
        )
        for i, line, title in (
            (1, files["test/a.js"].splitlines()[0], "should invoke handler"),
            (2, files["test/a.js"].splitlines()[1], "should invoke * handlers"),
        )
    ]
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=1),
        chunks=chunks,
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": [{"chunk": chunks[0].model_dump(), "score": 1}]},
    )
    result = CodeRetrievalIndex(reader(files), base, tmp_path).initial_sources("should invoke * handlers测试")
    assert result["sources"][0]["start_line"] == 2


def test_initial_source_unit_keeps_head_and_tail_with_same_budget(tmp_path):
    from test_chat import settings

    from repomentor.rag import RagEngine

    text = (
        "def long():\n"
        + "".join(f"    item_{i} = 'padding_padding_padding_padding'\n" for i in range(120))
        + "    return 42\n"
    )
    files = {"long.py": text}
    chunk = CodeChunk(
        snapshot_id="static",
        chunk_id="long",
        path="long.py",
        start_line=1,
        end_line=len(text.splitlines()),
        content=text.rstrip(),
        content_hash="fixture",
    )
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=1, initial_source_bytes=3000),
        chunks=[chunk],
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": []},
    )
    source_reader = reader(files)
    index = CodeRetrievalIndex(source_reader, base, tmp_path)
    prepared = RagEngine(
        source_reader, index, SimpleNamespace(settings=settings(context_window=16384)), tmp_path / "cache"
    ).prepare("long")
    assert len(prepared["context"]) == 2
    assert prepared["context"][0]["start_line"] == 1
    assert prepared["context"][1]["end_line"] == len(text.splitlines())
    assert sum(len(s["content"].encode()) for s in prepared["context"]) <= 3000
    assert prepared["initial_retrieval"]["hits"][0]["uncovered_ranges"]


def test_large_implementation_class_keeps_constructor_defaults(tmp_path):
    text = (
        "class Settings {\n  constructor() {\n"
        + "".join(f"    // constructor comment {i} with some padding\n" for i in range(25))
        + "    this.enabled = false;\n  }\n"
        + "\n".join(f"  method{i}() {{ return {i}; }}" for i in range(210))
        + "\n}\n"
    )
    chunk = CodeChunk(
        snapshot_id="static",
        chunk_id="settings",
        path="settings.js",
        start_line=1,
        end_line=2,
        content="class Settings {\n  constructor() {",
        content_hash="fixture",
    )
    base = SimpleNamespace(
        settings=RetrievalSettings(top_k=1),
        chunks=[chunk],
        record={},
        index_id="base",
        search=lambda *a, **kw: {"candidates": []},
    )
    result = CodeRetrievalIndex(reader({"settings.js": text}), base, tmp_path).initial_sources("Settings")
    assert any("this.enabled = false;" in s["content"] for s in result["sources"])
    assert result["hits"][0]["uncovered_ranges"]
    assert sum(len(s["content"].encode()) for s in result["sources"]) <= 8000
