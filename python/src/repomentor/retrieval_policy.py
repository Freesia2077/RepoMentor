"""One serializable retrieval policy for CLI, reports, and experiment runners."""

# 集中定义检索组件与预算参数，确保 CLI、会话和实验使用同一套合法配置。

from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract


# 候选数量、最终单元数量和切块预算集中校验，防止组合配置出现互相矛盾的上限。
class RetrievalPolicy(Contract):
    chunker: Literal["line", "syntax"] = "syntax"
    retriever: Literal["bm25", "vector", "hybrid", "hybrid-rerank"] = "bm25"
    device: Literal["cpu", "cuda"] = "cpu"
    query_expansion: bool = True
    code_aware: bool = True
    top_k: int = Field(default=6, ge=1, le=30)
    candidate_k: int = Field(default=20, ge=1, le=100)
    chunk_size: int = Field(default=768, ge=128, le=2048)
    source_bytes: int = Field(default=8000, ge=1000, le=48000)
    max_lines: int = Field(default=40, ge=4, le=200)
    overlap_lines: int = Field(default=8, ge=0, le=32)

    @model_validator(mode="after")
    def validate_candidate_limit(self):
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be at least top_k")
        if self.overlap_lines >= self.max_lines:
            raise ValueError("line overlap must be smaller than max_lines")
        return self


# Interactive default; the class defaults remain a lightweight offline baseline.
DEFAULT_RETRIEVAL = RetrievalPolicy()
