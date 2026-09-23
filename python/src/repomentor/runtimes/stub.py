"""Deterministic fixture runtime. Its results are never reported as model quality."""

from ..contracts import EvidencePlan


class StubRuntime:
    name = "stub-no-model"
    versions = {"stub": "1"}

    def __init__(self):
        self.usage = []

    async def plan(self, phase, harness, skill_text):
        profile = harness.profile
        candidates = list(dict.fromkeys(profile["entryCandidates"] + profile["testCandidates"]))
        return EvidencePlan(
            goal="Fixture report",
            rationale="Deterministic harness verification",
            questions=[],
            actions=[],
            files=[{"path": p, "purpose": "Inspect fixture", "priority": "high"} for p in candidates[:8]],
            stopConditions=["Fixture read"],
        )

    async def synthesize(self, stage, model, payload, harness, skill_text=""):
        paths = [f.path for f in harness.evidence().files]
        shared = {
            "evidenceClaims": [
                {
                    "claim": "已通过 Harness 读取此文件；此输出来自测试 stub。",
                    "confidence": "high",
                    "evidence": [{"path": paths[0], "supports": "文件读取成功"}],
                }
            ]
            if paths
            else [],
            "evidenceCoverage": {"examinedFiles": paths, "gaps": []},
        }
        if stage == "explorer":
            shared.update(
                projectType={"primary": "library", "secondary": []},
                techStack={"language": None, "framework": None, "buildTool": None},
                fileCount=harness.profile["fileCount"],
                entryPoints=[{"file": p, "role": "候选入口（stub）"} for p in paths[:2]],
                moduleMap=[],
                directorySummary="Deterministic fixture output",
                projectSummary="测试输出，不用于模型评测。",
            )
        elif stage == "mentor":
            shared.update(
                architectureOverview="Stub: inspect the listed sources.",
                dependencyGraph={},
                readingPath=[
                    {"step": i + 1, "file": p, "why": "已读取的候选文件（stub）"}
                    for i, p in enumerate(paths[:5])
                ],
                keyPatterns=[],
                codeConventions=[],
            )
        else:
            shared.update(
                goodFirstIssues=[],
                contributionSetup={"devEnv": None, "build": None, "test": None},
                entryFiles=[],
                notesForNewcomers=[{"tip": "Stub does not infer contribution opportunities."}],
            )
        return model.model_validate(shared)
