"""Constrain report path vocabulary through the SDK schema; semantics still need review."""

# 依据当前可见源码约束结构化报告的引用字段，给 SDK 输出校验提供动态 schema。

import copy
from pathlib import PurePosixPath

from agents import AgentOutputSchema


# 从已可见的文件与范围生成输出约束，使结构化引用只能指向允许来源。
def grounded_report_schema(schema, context):
    schema = copy.deepcopy(schema)
    if "evidenceCoverage" not in schema.get("properties", {}):
        return schema
    files = sorted({s["path"] for s in context})
    modules = sorted(set(files) | {str(p) for f in files for p in PurePosixPath(f).parents if str(p) != "."})
    definitions = schema.get("$defs", {})
    if not files:
        # Keep an explicit gap-only response representable without an invalid empty enum.
        for name in (
            "entryPoints",
            "moduleMap",
            "readingPath",
            "entryFiles",
            "evidenceClaims",
            "keyPatterns",
            "codeConventions",
            "goodFirstIssues",
        ):
            if name in schema["properties"]:
                schema["properties"][name]["maxItems"] = 0
        if "Coverage" in definitions:
            definitions["Coverage"]["properties"]["examinedFiles"]["maxItems"] = 0
    else:
        for name, field, allowed in (
            ("EntryPoint", "file", files),
            ("ReadingStep", "file", files),
            ("EntryFile", "file", files),
            ("EvidenceReference", "path", files),
            ("ModuleInfo", "path", modules),
        ):
            if name in definitions:
                definitions[name]["properties"][field]["enum"] = allowed
        definitions["Coverage"]["properties"]["examinedFiles"]["items"]["enum"] = files
    if "dependencyGraph" in schema["properties"]:
        # Enumerated optional keys permit {}, without depending on provider propertyNames support.
        schema["properties"]["dependencyGraph"] = {
            "type": "object",
            "properties": {
                p: {"type": "array", "items": {"type": "string", "enum": modules}} for p in modules
            },
            "additionalProperties": False,
        }
    return schema


class GroundedReportSchema(AgentOutputSchema):
    def __init__(self, output_type, context):
        super().__init__(output_type, strict_json_schema=False)
        self.grounded = grounded_report_schema(super().json_schema(), context)

    def json_schema(self):
        return self.grounded
