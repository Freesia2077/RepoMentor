"""Language/heading boundaries with exact source lines and a pinned embedding-token limit."""

import hashlib
import importlib.metadata
import re
from bisect import bisect_right
from pathlib import Path

from pydantic import Field

from .contracts import CodeChunk, Contract
from .records import atomic_write
from .repository import RepositoryError
from .retrieval import BM25Index, RetrievalSettings, indexable
from .runtimes.chat import fingerprint

CHUNK_VERSION = "syntax-heading-v1"


class ChunkSettings(Contract):
    max_tokens: int = Field(default=768, ge=128, le=2048)
    min_split_tokens: int = Field(default=256, ge=0, le=2048)
    max_file_bytes: int = 500_000
    max_index_bytes: int = 20_000_000
    max_files: int = 2000


def syntax_units(text, suffix):
    """Return nested declaration ranges; no execution of the target repository."""
    from tree_sitter import Language, Parser

    if suffix == ".py":
        import tree_sitter_python as grammar

        language = grammar.language()
    elif suffix in (".ts", ".tsx"):
        import tree_sitter_typescript as grammar

        language = grammar.language_tsx() if suffix == ".tsx" else grammar.language_typescript()
    else:
        import tree_sitter_javascript as grammar

        language = grammar.language()
    source = text.encode()
    parser = Parser(Language(language))
    tree = parser.parse(source)
    # Derive lines from stable byte offsets. Point access in py-tree-sitter 0.26
    # can corrupt native memory on some grammars; preserve exact source offsets.
    line_starts = [0] + [i + 1 for i, byte in enumerate(source) if byte == 10]
    if tree.root_node.has_error:
        return [], "parse_error_text_fallback"
    units = []
    types = {
        "function_definition",
        "class_definition",
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        "interface_declaration",
        "type_alias_declaration",
    }

    def walk(node, parent_symbol=None):
        name = node.child_by_field_name("name")
        kind, symbol = node.type, None
        if kind in types and name:
            symbol = source[name.start_byte : name.end_byte].decode()
        elif kind == "variable_declarator":
            value = node.child_by_field_name("value")
            if name and value and value.type in ("arrow_function", "function_expression"):
                symbol = source[name.start_byte : name.end_byte].decode()
        elif kind == "call_expression":
            function, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
            if function and args:
                label = source[function.start_byte : function.end_byte].decode()
                if label in ("test", "it", "describe") and args.named_children:
                    first = args.named_children[0]
                    if first.type in ("string", "template_string"):
                        symbol = label + ":" + source[first.start_byte : first.end_byte].decode()[:100]
        full = f"{parent_symbol}.{symbol}" if symbol and parent_symbol else symbol or parent_symbol
        if symbol:
            start = bisect_right(line_starts, node.start_byte)
            if node.parent and node.parent.type == "decorated_definition":
                start = bisect_right(line_starts, node.parent.start_byte)
            end = bisect_right(line_starts, max(node.start_byte, node.end_byte - 1))
            units.append((start, max(start, end), full, kind))
        for child in node.named_children:
            walk(child, full)

    walk(tree.root_node)
    return sorted(set(units), key=lambda u: (u[0], -u[1])), "tree_sitter"


def markdown_units(text):
    lines = text.splitlines()
    headings, stack, fence = [], [], None
    for i, line in enumerate(lines, 1):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            token = match[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        match = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match and fence is None:
            level = len(match[1])
            stack = [(n, s) for n, s in stack if n < level] + [(level, match[2])]
            headings.append((i, " / ".join(s for _, s in stack)))
    return [
        (start, headings[j + 1][0] - 1 if j + 1 < len(headings) else len(lines), symbol, "heading")
        for j, (start, symbol) in enumerate(headings)
    ]


def structural_chunks(snapshot_id, path, text, tokenizer, settings=None):
    settings = settings or ChunkSettings()
    lines = text.splitlines()

    def count(content):
        return len(tokenizer.encode(content, add_special_tokens=False))

    suffix = Path(path).suffix.lower()
    if suffix in (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        units, parser = syntax_units(text, suffix)
    elif suffix == ".md":
        units, parser = markdown_units(text), "markdown_headings"
    else:
        units, parser = [], "bounded_text_fallback"
    output, omitted, metadata = [], [], {}

    def add(start, end, symbol, kind):
        content = "\n".join(lines[start - 1 : end])
        if not content.strip():
            return
        digest = hashlib.sha256(content.encode()).hexdigest()
        cid = fingerprint([snapshot_id, path, start, end, symbol, digest, CHUNK_VERSION])[:24]
        output.append(
            CodeChunk(
                snapshot_id=snapshot_id,
                chunk_id=cid,
                path=path,
                start_line=start,
                end_line=end,
                content_hash=digest,
                content=content,
                symbol=symbol,
            )
        )
        metadata[cid] = {
            "parser": parser,
            "kind": kind,
            "parent_symbol": symbol,
            "embedding_tokens": count(content),
        }

    def split(start, end, symbol, kind):
        # Prefer blank/statement boundaries above the target minimum; retain every nonblank source line.
        while start <= end:
            stop, preferred = start - 1, None
            for current in range(start, end + 1):
                n = count("\n".join(lines[start - 1 : current]))
                if n > settings.max_tokens:
                    break
                stop = current
                if n >= settings.min_split_tokens and (
                    not lines[current - 1].strip() or re.search(r"[;}\]]\s*$", lines[current - 1])
                ):
                    preferred = current
            if stop < start:
                omitted.append({"path": path, "start_line": start, "reason": "oversized_line_tokens"})
                start += 1
                continue
            if stop < end and preferred:
                stop = preferred
            add(start, stop, symbol, kind if start == stop == end else kind + "_bounded")
            start = stop + 1

    def visit(start, end, candidates, parent=None):
        cursor = start
        for a, b, symbol, kind in candidates:
            if a < cursor or b > end:
                continue
            split(cursor, a - 1, parent, "context")
            children = [u for u in candidates if a <= u[0] and u[1] <= b and (u[0], u[1]) != (a, b)]
            if count("\n".join(lines[a - 1 : b])) <= settings.max_tokens:
                add(a, b, symbol, kind)
            elif children:
                visit(a, b, children, symbol)
            else:
                split(a, b, symbol, kind)
            cursor = b + 1
        split(cursor, end, parent, "context")

    visit(1, len(lines), units)
    return output, omitted, metadata


def build_structural_index(
    reader, cache_dir, tokenizer, tokenizer_identity, *, chunks=None, retrieval=None, token=None
):
    import json

    chunks, retrieval = chunks or ChunkSettings(), retrieval or RetrievalSettings()
    parser_versions = {
        name: importlib.metadata.version(name)
        for name in ("tree-sitter", "tree-sitter-python", "tree-sitter-javascript", "tree-sitter-typescript")
    }
    identity = {
        "snapshot": reader.snapshot.model_dump(),
        "chunks": chunks.model_dump(),
        "retrieval": retrieval.model_dump(),
        "version": CHUNK_VERSION,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "tokenizer": tokenizer_identity,
        "parsers": parser_versions,
    }
    key = fingerprint(identity)[:24]
    path = Path(cache_dir) / (key + ".json")
    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["identity"] != identity:
            raise ValueError("structural index identity mismatch")
        for row in record["chunks"]:
            if hashlib.sha256(row["content"].encode()).hexdigest() != row["content_hash"]:
                raise ValueError("structural index content mismatch")
        return BM25Index(record, retrieval)
    rows, omissions, meta, used, files = [], [], {}, 0, 0
    for relative in sorted(reader.paths):
        if token:
            token.check()
        if not indexable(relative):
            continue
        if used >= chunks.max_index_bytes or files >= chunks.max_files:
            omissions.append({"path": relative, "reason": "index_budget"})
            continue
        try:
            text, truncated = reader.read(relative, chunks.max_file_bytes)
        except RepositoryError as exc:
            omissions.append({"path": relative, "reason": exc.code})
            continue
        if truncated or used + len(text.encode()) > chunks.max_index_bytes:
            omissions.append({"path": relative, "reason": "file_or_index_budget"})
            continue
        block, skipped, metadata = structural_chunks(
            reader.snapshot.snapshot_id, relative, text, tokenizer, chunks
        )
        rows.extend(c.model_dump() for c in block)
        omissions.extend(skipped)
        meta.update(metadata)
        used += len(text.encode())
        files += 1
    record = {
        "index_id": key,
        "snapshot_id": reader.snapshot.snapshot_id,
        "commit_sha": reader.snapshot.commit_sha,
        "version": CHUNK_VERSION,
        "identity": identity,
        "settings": retrieval.model_dump(),
        "files": files,
        "source_bytes": used,
        "omissions": omissions,
        "chunks": rows,
        "chunk_metadata": meta,
    }
    atomic_write(path, json.dumps(record, ensure_ascii=False))
    return BM25Index(record, retrieval)
