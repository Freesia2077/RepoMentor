import path from "node:path";
import { describe, expect, it } from "vitest";

import { ClaudeAgentRuntime } from "../../src/services/claude-agent-runtime.js";
import { DEFAULT_HARNESS_SKILL_POLICY } from "../../src/services/harness-skills.js";
import { requireModelSettings } from "../../src/services/model-settings.js";
import { RepositoryHarness } from "../../src/services/repository-harness.js";

describe.skipIf(process.env.RUN_AGENT_SDK_LIVE !== "1")(
  "Claude Agent SDK live bounded-MCP contract",
  () => {
    it("calls a RepoMentor MCP tool without exposing raw file or shell tools", async () => {
      const fixture = path.join(process.cwd(), "tests", "fixtures", "mini-repo");
      const harness = new RepositoryHarness(fixture);
      const { profile } = await harness.getRepositoryMap();
      harness.activateSkillPolicy("explorer", DEFAULT_HARNESS_SKILL_POLICY);
      const runtime = new ClaudeAgentRuntime(requireModelSettings());
      const result = await runtime.planEvidence({
        phase: "explorer",
        repositoryProfile: profile,
        skillPolicy: DEFAULT_HARNESS_SKILL_POLICY,
        sdkSkillNames: ["repomentor:analyze-generic"],
        harnessState: harness.getContextView(),
        existingEvidencePaths: [],
      }, harness, new AbortController());

      expect(Object.values(result.toolCalls).reduce((sum, count) => sum + count, 0)).toBeGreaterThan(0);
      expect(result.evidencePlan.actions).toEqual([]);
      expect(result.evidencePlan.files.every((file) => profile.fileIndex.includes(file.path))).toBe(true);
    }, 180_000);
  },
);
