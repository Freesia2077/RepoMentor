"""Snapshot-pinned static definitions and conservative one-hop call candidates."""

import ast
import hashlib
import json
import re
from bisect import bisect_right
from pathlib import Path, PurePosixPath

from .records import atomic_write
from .repository import is_source, is_test
from .runtimes.chat import fingerprint
from .symbol_navigation import declarations

VERSION = "static-navigation-v4"


def explicit_source_paths(query, paths):
    """Honor tracked paths as whole paths, not index.js inside other/index.js."""
    query = query.replace("\\", "/")
    found = {p for p in paths if re.search(r"(?<![A-Za-z0-9_./-])" + re.escape(p)
                                         + r"(?![A-Za-z0-9_./-])", query)}
    by_name = {}
    for path in paths:
        by_name.setdefault(PurePosixPath(path).name, []).append(path)
    for name, matches in by_name.items():
        if len(matches) == 1 and re.search(r"(?<![A-Za-z0-9_./-])" + re.escape(name)
                                         + r"(?![A-Za-z0-9_./-])", query):
            found.add(matches[0])
    return found


def qualified_names(definition):
    module = definition["module"]
    modules = {module, module.removeprefix("src.")}
    modules |= {m.removesuffix(".__init__") for m in modules}
    return {definition["symbol"], *(m + "." + definition["symbol"] for m in modules)}


def file_role(path):
    parts = {p.casefold() for p in PurePosixPath(path).parts}
    if parts & {"example", "examples", "demo", "demos"}:
        return "example"
    if is_test(path):
        return "test"
    if parts & {"doc", "docs", "documentation"}:
        return "documentation"
    return "implementation" if is_source(path) else "documentation"


def resolve_module(path, module, paths, level=0):
    parent = PurePosixPath(path).parent
    if level:
        for _ in range(level - 1):
            parent = parent.parent
        bases = [parent / module.replace(".", "/")]
    elif module.startswith("."):
        parts = list(parent.parts)
        for part in module.split("/"):
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        bases = [PurePosixPath(*parts)]
    else:
        bases = [PurePosixPath(module.replace(".", "/")), PurePosixPath("src") / module.replace(".", "/")]
    candidates = []
    for base in bases:
        raw = base.as_posix()
        candidates.extend([raw, *(raw + ext for ext in (".py", ".js", ".ts", ".tsx", ".mjs")),
                           *(raw + "/" + suffix for suffix in ("__init__.py", "index.js", "index.ts"))])
    found = list(dict.fromkeys(p for p in candidates if p in paths))
    return found[0] if len(found) == 1 else None


def parsed_calls(text, path, *, guarded_attributes=None):
    """Imports are bindings; calls retain their lexical owner and shadowed parameters."""
    imports, aliases, calls = {}, {}, []
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return imports, aliases, calls
        # File-wide and conservative: a replaced name is not safe alias evidence.
        # Keep existing call resolution separate from this new recall channel.
        binding_counts = {}
        for ref in ast.walk(tree):
            if isinstance(ref, (ast.Import, ast.ImportFrom)):
                for name in ref.names:
                    key = name.asname or name.name.split(".")[0]
                    binding_counts[key] = binding_counts.get(key, 0) + 1
        replaced_imports = {name for name, count in binding_counts.items() if count > 1}
        replaced_imports.update(
            ref.id if isinstance(ref, ast.Name) else ref.name for ref in ast.walk(tree)
            if (isinstance(ref, ast.Name) and isinstance(ref.ctx, (ast.Store, ast.Del)))
            or isinstance(ref, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for name in node.names:
                    imports[name.asname or name.name] = (node.module or "", name.name, node.level,
                                                        node.lineno, node.end_lineno)
            elif isinstance(node, ast.Import):
                for name in node.names:
                    imports[name.asname or name.name.split(".")[0]] = (name.name, None, 0,
                                                                        node.lineno, node.end_lineno)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = node.value.id

        def walk(node, owner="", blocked=frozenset(), class_owner="", receiver=None):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parent_owner = owner
                owner = owner + "." + node.name if owner else node.name
                if isinstance(node, ast.ClassDef):
                    class_owner, receiver = owner, None
                else:
                    args = node.args
                    positional = [*args.posonlyargs, *args.args]
                    parameters = {a.arg for a in [*positional, *args.kwonlyargs]}
                    parameters |= {a.arg for a in (args.vararg, args.kwarg) if a}
                    stores = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                              and isinstance(n.ctx, (ast.Store, ast.Del))}
                    if parent_owner == class_owner and class_owner:
                        first = positional[0].arg if positional else None
                        static = any((isinstance(d, ast.Name) and d.id == "staticmethod")
                                     or (isinstance(d, ast.Attribute) and d.attr == "staticmethod") for d in node.decorator_list)
                        receiver = (first, class_owner) if first in ("self", "cls") and not static else None
                    elif receiver and receiver[0] in parameters:
                        receiver = None  # A nested function can shadow the method receiver.
                    if receiver and receiver[0] in stores:
                        receiver = None
                    blocked = blocked | parameters | stores
            if guarded_attributes is not None and receiver and isinstance(node, (ast.If, ast.While)):
                condition = list(ast.walk(node.test))
                # Conditional scopes with their own bindings are not receiver evidence.
                if not any(isinstance(n, (ast.Lambda, ast.comprehension)) for n in condition):
                    for ref in condition:
                        if (isinstance(ref, ast.Attribute) and isinstance(ref.ctx, ast.Load)
                                and isinstance(ref.value, ast.Name) and ref.value.id == receiver[0]):
                            guarded_attributes.setdefault(owner, set()).add(ref.attr)
            if isinstance(node, ast.Call):
                expr = ast.unparse(node.func)
                calls.append({"expression": expr, "line": node.lineno, "owner": owner,
                              "shadowed": expr.split(".")[0] in blocked,
                              "import_replaced": expr.split(".")[0] in replaced_imports,
                              "receiver_class": receiver[1] if receiver and expr.startswith(receiver[0] + ".") else None})
            for child in ast.iter_child_nodes(node):
                child_receiver = receiver
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and child not in node.body:
                    child_receiver = None  # Decorators and defaults run outside the method body.
                walk(child, owner, blocked, class_owner, child_receiver)
        walk(tree)
    else:
        from tree_sitter import Language, Parser

        if path.endswith((".ts", ".tsx")):
            import tree_sitter_typescript as grammar
            language = grammar.language_tsx() if path.endswith(".tsx") else grammar.language_typescript()
        else:
            import tree_sitter_javascript as grammar
            language = grammar.language()
        source = text.encode()
        line_starts = [0] + [i + 1 for i, byte in enumerate(source) if byte == 10]
        tree = Parser(Language(language)).parse(source)
        defs = declarations(text, path)
        for match in re.finditer(r"import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]", text):
            clause, module = match.groups()
            if clause.startswith("{"):
                for binding in clause.strip("{} \n").split(","):
                    names = re.split(r"\s+as\s+", binding.strip())
                    if names[0]:
                        imports[names[-1]] = (module, names[0], 0)
            elif clause.startswith("*"):
                imports[clause.split()[-1]] = (module, None, 0)
            elif re.fullmatch(r"[\w$]+", clause):
                imports[clause] = (module, "default", 0)
        for match in re.finditer(r"(?:const|let|var)\s+([\w$]+)\s*=\s*([\w$]+)\s*[;\n]", text):
            aliases[match[1]] = match[2]
        for match in re.finditer(r"export\s*\{([^}]+)\}", text):
            for binding in match[1].split(","):
                names = re.split(r"\s+as\s+", binding.strip())
                if len(names) == 2:
                    aliases[names[1]] = names[0]

        def walk(node, owner=""):
            name = node.child_by_field_name("name")
            named = node.type in {"function_declaration", "generator_function_declaration", "class_declaration",
                                  "method_definition", "interface_declaration", "type_alias_declaration"}
            value = node.child_by_field_name("value") if node.type == "variable_declarator" else None
            if name and (named or value and value.type in ("arrow_function", "function_expression")):
                symbol = source[name.start_byte:name.end_byte].decode()
                owner = owner + "." + symbol if owner else symbol
            if node.type == "call_expression":
                fn = node.child_by_field_name("function")
                if fn:
                    expr = source[fn.start_byte:fn.end_byte].decode()
                    # Byte offsets avoid the Windows binding's Point conversion crash
                    # on deeply nested JavaScript; same convention as declarations().
                    line = bisect_right(line_starts, node.start_byte)
                    parents = [d for d in defs if d["start_line"] <= line <= d["end_line"]
                               and d["kind"] != "factory_binding_candidate"]
                    call_owner = owner or (min(parents, key=lambda d: d["end_line"]-d["start_line"])["symbol"] if parents else "")
                    factories = [d for d in defs if d["kind"] == "factory_binding_candidate"
                                 and d["start_line"] <= line <= d["end_line"]]
                    if not call_owner and len(factories) == 1:
                        call_owner = factories[0]["symbol"]
                    ancestor, blocked = node.parent, set()
                    receiver_class, receiver_static, receiver_blocked = None, False, False
                    while ancestor:
                        if ancestor.type in {"function_expression", "function_declaration", "generator_function"}:
                            receiver_blocked = True  # Traditional nested functions have their own `this`.
                        if ancestor.type == "method_definition" and receiver_class is None and not receiver_blocked:
                            container = ancestor.parent
                            if container and container.type == "class_body":
                                classes = [d for d in defs if "class" in d["kind"]
                                           and d["start_line"] <= line <= d["end_line"]
                                           and call_owner.startswith(d["symbol"] + ".")]
                                if classes:
                                    receiver_class = min(classes, key=lambda d: d["end_line"]-d["start_line"])["symbol"]
                                    receiver_static = source[ancestor.start_byte:ancestor.end_byte].lstrip().startswith(b"static ")
                        parameters = ancestor.child_by_field_name("parameters")
                        if parameters:
                            blocked.update(re.findall(r"[\w$]+", source[parameters.start_byte:parameters.end_byte].decode()))
                        if ancestor.type in {"statement_block", "function_declaration", "arrow_function", "function_expression"}:
                            # Conservative scope check: local declarations and assignments
                            # may hide a module binding. Do not pretend to resolve their values.
                            scope_text = source[ancestor.start_byte:ancestor.end_byte].decode()
                            blocked.update(re.findall(r"\b(?:const|let|var)\s+([\w$]+)", scope_text))
                            blocked.update(re.findall(r"(?<![.\w$])([\w$]+)\s*=(?!=|>)", scope_text))
                        ancestor = ancestor.parent
                    calls.append({"expression": expr, "line": line, "owner": call_owner,
                                  "shadowed": expr.split(".")[0] in blocked,
                                  "receiver_class": receiver_class if expr.startswith("this.") else None,
                                  "receiver_static": receiver_static})
            for child in node.named_children:
                walk(child, owner)
        walk(tree.root_node)
    return imports, aliases, calls


class SymbolIndex:
    def __init__(self, record):
        self.record, self.definitions, self.relations = record, record["definitions"], record["relations"]
        self.index_id = record["index_id"]

    @classmethod
    def build(cls, reader, indexed_paths, cache_dir=None):
        paths = sorted(set(indexed_paths))
        identity = {"version": VERSION, "snapshot": reader.snapshot.snapshot_id, "paths": paths,
                    "implementation": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()}
        key = fingerprint(identity)[:24]
        cache = cache_dir / (key + ".json") if cache_dir else None
        if cache and cache.exists():
            record = json.loads(cache.read_text(encoding="utf-8"))
            payload = {k: v for k, v in record.items() if k != "sha256"}
            if record["index_id"] != key or fingerprint(payload) != record["sha256"]:
                raise ValueError("symbol index integrity mismatch")
            return cls(record)
        definitions, parsed, omitted = [], {}, []
        for path in paths:
            if not path.endswith((".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")):
                omitted.append({"path": path, "reason": "not_supported_source_language"})
                continue
            text, truncated = reader.read(path, 500_000)
            if truncated:
                omitted.append({"path": path, "reason": "file_budget"})
                continue
            rows = declarations(text, path)
            guards = {}
            parsed[path] = parsed_calls(text, path, guarded_attributes=guards)
            digest = hashlib.sha256(text.encode()).hexdigest()
            for row in rows:
                definitions.append({**row, "path": path, "role": file_role(path), "file_sha256": digest,
                    "module": path.rsplit(".", 1)[0].replace("/", "."),
                    "signature": text.splitlines()[row["start_line"] - 1].strip()[:240],
                    **({"guarded_attributes": sorted(guards[row["symbol"]])} if row["symbol"] in guards else {}),
                    "id": fingerprint([key, path, row["symbol"], row["start_line"]])[:20]})
        by_symbol = {}
        for definition in definitions:
            by_symbol.setdefault((definition["path"], definition["symbol"]), []).append(definition)
        path_set = set(paths)
        relations = []
        for path, (imports, aliases, calls) in parsed.items():
            for raw_call in calls:
                call = dict(raw_call)
                import_replaced = call.pop("import_replaced", False)
                expr = aliases.get(call["expression"], call["expression"])
                target_path, target_name = path, expr
                binding = imports.get(expr) or imports.get(expr.split(".")[0])
                binding_range = ()
                if binding:
                    module, imported, level, *binding_range = binding
                    target_path = resolve_module(path, module, path_set, level)
                    target_name = imported or expr.partition(".")[2]
                    if target_path in parsed:
                        target_name = parsed[target_path][1].get(target_name, target_name)
                elif "." not in expr and call["owner"]:
                    # Prefer a nested lexical declaration, then a module-level definition.
                    owner = call["owner"].rsplit(".", 1)[0] if "." in call["owner"] else ""
                    nested = owner + "." + expr if owner else expr
                    if (path, nested) in by_symbol:
                        target_name = nested
                candidates = by_symbol.get((target_path, target_name), [])
                resolved = not call["shadowed"] and len(candidates) == 1
                member = re.fullmatch(r"(?:self|cls|this)\.([A-Za-z_$][A-Za-z_0-9$]*)", expr)
                member_class = call.get("receiver_class")
                member_candidates = [d for d in by_symbol.get((path, member_class + "." + member[1]), [])
                    if path.endswith(".py") or d["signature"].startswith("static ") == call.get("receiver_static", False)
                    ] if member and member_class else []
                member_candidate = member_candidates[0] if len(member_candidates) == 1 else None
                relations.append({"path": path, **call, "relation": "call_candidate",
                    "target_id": candidates[0]["id"] if resolved else member_candidate["id"] if member_candidate else None,
                    "resolution": "static_binding" if resolved else "lexical_member_candidate" if member_candidate else "unresolved",
                    **({"import_binding": {"path": path, "start_line": binding_range[0], "end_line": binding_range[1]}}
                       if resolved and not import_replaced and binding_range and imported != expr and "." not in expr
                       and expr == call["expression"] else {}),
                    "limits": "Static location candidate, not executed dispatch. Member candidates do not resolve inheritance, overrides or monkeypatching."})
        record = {**identity, "index_id": key, "definitions": definitions, "relations": relations,
                  "indexed_files": len(parsed), "omissions": omitted,
                  "source_files_not_admitted": sorted(p for p in reader.paths if is_source(p) and p not in paths),
                  "coverage": "Only files admitted by the source index; no per-question 80-file cutoff."}
        record["sha256"] = fingerprint(record)
        if cache:
            atomic_write(cache, json.dumps(record, ensure_ascii=False))
        return cls(record)

    def lookup(self, query, *, path=None, relation="definitions", excluded=()):
        terms = set(re.findall(r"[A-Za-z_$][A-Za-z_0-9$]*(?:\.[A-Za-z_$][A-Za-z_0-9$]*)*", query))
        # Prose words are not exact code mentions. Keep short navigation queries
        # and identifiers marked by spelling, backticks or call syntax.
        if len(terms) > 3:
            marked = re.findall(r"`([A-Za-z_$][A-Za-z_0-9$.]*)`|([A-Za-z_$][A-Za-z_0-9$.]*)\s*\(", query)
            marked = {name for pair in marked for name in pair if name}
            terms = {t for t in terms if re.search(r"[_.A-Z$]", t) or t in marked}
        folded = {t.casefold() for t in terms}
        # Unknown instance names recall class members, without binding a receiver
        # or reinterpreting an explicit class/module-qualified name after scoping.
        known = {name for d in self.definitions if d["role"] == "implementation" or "class" in d["kind"]
                 for name in qualified_names(d)}
        classes = {(d["path"], d["symbol"]) for d in self.definitions if "class" in d["kind"]}
        member_names = {t.rsplit(".", 1)[1] for t in terms if t.count(".") == 1
                        and t not in known and t.split(".", 1)[0] not in known}
        eligible = [d for d in self.definitions if d["path"] not in excluded
                    and (path is None or d["path"] == path)]
        paths = explicit_source_paths(query, {d["path"] for d in eligible})
        owners = {(d["path"], d["symbol"]) for d in eligible
                  if "class" in d["kind"] and qualified_names(d) & terms}
        # A qualified method also establishes its lexical class for other named
        # members in the question; unrelated classes must not win the six slots.
        qualified_members = [d for d in self.definitions if any("." in t and t in qualified_names(d) for t in terms)]
        owners.update((d["path"], d["symbol"]) for d in eligible if "class" in d["kind"] and any(
            m["path"] == d["path"] and m["symbol"].startswith(d["symbol"] + ".") for m in qualified_members))
        matches = []
        for definition in eligible:
            symbol, names = definition["symbol"], qualified_names(definition)
            leaf = symbol.rsplit(".", 1)[-1]
            owner_match = any(p == definition["path"] and symbol.startswith(owner + ".") for p, owner in owners)
            priority = (10 if any("." in t and t in names for t in terms) else
                        8 if owner_match and leaf in terms else
                        6 if leaf in terms and re.search(r"_|[a-z][A-Z]", leaf) else
                        5 if symbol in terms and "class" not in definition["kind"] else
                        4 if leaf in member_names and (definition["path"], symbol.rpartition(".")[0]) in classes else
                        3 if symbol in terms else
                        2 if leaf in terms else
                        1 if {n.casefold() for n in names} & folded or leaf.casefold() in folded else 0)
            if priority:
                matches.append({**definition, "match_priority": priority, "explicit_path": definition["path"] in paths,
                                **({"match_kind": "member_name_only"} if priority == 4 else {})})
        # A field use is a fallback, never a substitute for an identified entry.
        if not any(m["role"] == "implementation" and m["match_priority"] >= 4 for m in matches):
            guard_owners = {(d["path"], d["symbol"]) for d in self.definitions if "class" in d["kind"] and (
                any(name in terms and re.search(r"(?<![A-Za-z_0-9$.])" + re.escape(name)
                    + r"(?![A-Za-z_0-9$.]|\s*\()", query) for name in qualified_names(d))
                or any(m["path"] == d["path"] and m["symbol"].startswith(d["symbol"] + ".") for m in qualified_members))}
            for definition in eligible:
                owner_match = any(p == definition["path"] and definition["symbol"].startswith(owner + ".")
                                  for p, owner in guard_owners)
                if (definition["role"] == "implementation" and (not guard_owners or owner_match)
                        and set(definition.get("guarded_attributes", [])) & terms):
                    matches = [m for m in matches if m["id"] != definition["id"]]
                    matches.append({**definition, "match_priority": 4, "match_kind": "attribute_guard",
                                    "explicit_path": definition["path"] in paths})
        matches.sort(key=lambda d: (not d["explicit_path"], -d["match_priority"],
                                    d["role"] != "implementation", d["path"], d["start_line"]))
        edges, targets = [], matches
        if relation != "definitions":
            ids = {d["id"] for d in matches}
            edges = [r for r in self.relations if r["path"] not in excluded and (
                r["target_id"] in ids if relation == "callers" else
                any(d["path"] == r["path"] and d["symbol"] == r["owner"] for d in matches))]
            target_ids = {r["target_id"] for r in edges if r["target_id"]}
            targets = [d for d in self.definitions if d["id"] in target_ids and d["path"] not in excluded]
            if relation == "callers":
                targets = [{"path": r["path"], "symbol": r["owner"], "start_line": r["line"],
                            "end_line": r["line"], "kind": "call_site"} for r in edges]
        return {"schema_version": VERSION, "targets": targets[:6], "relations": edges[:6],
                "omitted_targets": max(0, len(targets)-6), "source_facts": False,
                "coverage": {"indexed_files": self.record["indexed_files"], "omissions": self.record["omissions"][:6]},
                "scope": "Locations and static candidates only; read source before claims. One hop, six targets."}

    def navigation(self, question, focus, previous=None):
        query = question + (" " + previous if previous and re.search(r"它|那么|继续|\bit\b", question, re.I) else "")
        result = self.lookup(query, excluded=focus.get("excluded_paths", []))
        # Supply bounded, confirmed static locations without pretending the bodies were read.
        # The SDK agent still chooses whether to read these one-hop definitions.
        related = []
        for target in result["targets"]:
            if target.get("role") != "implementation":
                continue
            callees = self.lookup(target["symbol"], path=target["path"], relation="callees",
                                  excluded=focus.get("excluded_paths", []))
            for row in callees["targets"]:
                if row not in related and row not in result["targets"]:
                    related.append(row)
        result["related_definitions"] = related[:6]
        symbols = {r["symbol"] for r in result["targets"]}
        return {**result, "policy": VERSION, "current_symbol": next(iter(symbols)) if len(symbols) == 1 else None}
