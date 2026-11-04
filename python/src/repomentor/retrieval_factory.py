"""Optional neural retrieval construction; the base BM25 path needs no ML packages."""

from urllib.parse import urlparse

from .paths import runtime_dir
from .retrieval import BM25Index, RetrievalSettings


def unload_local_generator(profile):
    """Free only the explicitly configured local Ollama model; do not generate anything."""
    import httpx

    origin = profile.baseUrl.rstrip("/").removesuffix("/v1")
    if profile.backend != "ollama" or urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("automatic GPU sharing requires a local Ollama profile; use CPU retrieval otherwise")
    with httpx.Client(base_url=origin, timeout=60, trust_env=False) as client:
        try:
            loaded = client.get("/api/ps")
        except httpx.ConnectError:
            return  # No running generator to unload.
        loaded.raise_for_status()
        if any(row.get("name") == profile.model for row in loaded.json().get("models", [])):
            response = client.post("/api/generate", json={"model": profile.model, "keep_alive": 0})
            response.raise_for_status()


def build_index(
    reader,
    *,
    storage=None,
    chunker="syntax",
    retriever="bm25",
    query_expansion=True,
    retrieval_settings=None,
    device="cpu",
    profile=None,
    token=None,
    code_aware=True,
    top_k=6,
    candidate_k=20,
    chunk_size=768,
    source_bytes=8000,
    max_lines=40,
    overlap_lines=8,
):
    storage = storage or runtime_dir()
    from .retrieval_policy import RetrievalPolicy
    policy = RetrievalPolicy(chunker=chunker, retriever=retriever, device=device, query_expansion=query_expansion,
        code_aware=code_aware, top_k=top_k, candidate_k=candidate_k, chunk_size=chunk_size, source_bytes=source_bytes,
        max_lines=max_lines, overlap_lines=overlap_lines)
    settings = RetrievalSettings(query_expansion=query_expansion, top_k=policy.top_k, candidate_k=policy.candidate_k,
        initial_source_bytes=policy.source_bytes, max_lines=policy.max_lines, overlap_lines=policy.overlap_lines)
    def wrap(base):
        from .code_retrieval import CodeRetrievalIndex
        return CodeRetrievalIndex(reader, base, storage / "symbol-indexes", code_aware=code_aware)

    if chunker == "line" and retriever == "bm25":
        return wrap(BM25Index.build(reader, storage / "indexes", settings, token=token))
    from .chunking import ChunkSettings, build_structural_index
    # All new retrieval conditions use identical source chunks. Byte counting is a
    # conservative source bound, not an LLM token estimate, and needs no ML weights.
    class ByteTokenizer:
        def encode(self, text, **kwargs):
            return text.encode("utf-8")

    lexical = (build_structural_index(reader, storage / "syntax-indexes", ByteTokenizer(),
               {"tokenizer": "utf8-byte-bound-v1"}, chunks=ChunkSettings(max_tokens=policy.chunk_size,
                   min_split_tokens=min(256, policy.chunk_size)), retrieval=settings, token=token)
               if chunker == "syntax" else
               BM25Index.build(reader, storage / "indexes", settings, token=token))
    if retriever == "bm25":
        return wrap(lexical)
    from .hybrid_retrieval import HybridIndex
    from .neural_retrieval import NeuralRetrieval, load_retrieval_models

    neural = NeuralRetrieval(
        load_retrieval_models(retrieval_settings),
        storage / "neural-cache",
        device=device,
        token=token,
        before_load=(lambda: unload_local_generator(profile)) if profile and device == "cuda" else None,
    )
    return wrap(HybridIndex(lexical, neural, storage / "hybrid", strategy=retriever))
