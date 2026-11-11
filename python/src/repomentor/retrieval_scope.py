"""Explicit repository scopes applied before retrieval truncation."""

# 把文件、目录、排除路径和文件类型条件封装成统一范围谓词，在召回前筛选候选。

from typing import Literal

from pydantic import Field, field_validator

from .contracts import Contract
from .repository import is_source, is_test, safe_path


class SearchScope(Contract):
    kind: Literal["all", "code", "docs", "tests"] = "all"
    path: str | None = None
    path_prefix: str | None = None
    exclude_paths: list[str] = Field(default_factory=list)

    @field_validator("path", "path_prefix")
    @classmethod
    def relative_path(cls, value, info):
        if value is None:
            return None
        return safe_path(value.rstrip("/") if info.field_name == "path_prefix" else value)

    # 把路径和文件角色条件合并成同一判断，词项与向量两路检索复用这个范围。
    def matches(self, path):
        if path in self.exclude_paths:
            return False
        if self.path is not None and path != self.path:
            return False
        if self.path_prefix is not None and not path.startswith(self.path_prefix + "/"):
            return False
        return (
            self.kind == "all"
            or (self.kind == "code" and is_source(path))
            or (self.kind == "tests" and is_test(path))
            or (self.kind == "docs" and not is_source(path))
        )
