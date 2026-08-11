import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  DEFAULT_HARNESS_SKILL_POLICY,
  loadHarnessSkill,
} from "../../src/services/harness-skills.js";

const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe("Harness Skills", () => {
  it("loads a project-specific capability pack instead of broadening it with generic defaults", () => {
    const skills = createSkillsDirectory();
    writeSkill(skills, "generic", {
      name: "analyze-generic",
      matchProjectTypes: ["*"],
      stages: ["mentor"],
      allowedTools: ["search_symbols", "trace_module_dependencies"],
      preferredTools: ["search_symbols"],
      recommendedQuestions: ["generic question"],
      evidenceRequirements: ["generic evidence"],
      stopConditions: ["generic stop"],
      maxDiscoveryActions: 3,
      maxEvidenceFiles: 8,
    });
    writeSkill(skills, "cli", {
      name: "analyze-cli-tool",
      matchProjectTypes: ["cli"],
      stages: ["mentor"],
      allowedTools: ["find_related_tests"],
      preferredTools: ["find_related_tests"],
      recommendedQuestions: ["trace command dispatch"],
      evidenceRequirements: ["CLI entry"],
      stopConditions: ["dispatch is evidenced"],
      maxDiscoveryActions: 1,
      maxEvidenceFiles: 4,
    });

    const loaded = loadHarnessSkill(["cli"], "mentor", skills);

    expect(loaded.policy).toMatchObject({
      skillNames: ["analyze-cli-tool"],
      allowedTools: ["find_related_tests"],
      maxDiscoveryActions: 1,
      maxEvidenceFiles: 4,
    });
    expect(loaded.promptContent).toContain("analyze-cli-tool");
    expect(loaded.promptContent).not.toContain("analyze-generic");
    expect(loaded.sdkSkillNames).toEqual(["repomentor:analyze-cli-tool"]);
  });

  it("merges multiple matching packs by keeping the strictest execution limits", () => {
    const skills = createSkillsDirectory();
    writeSkill(skills, "cli", {
      name: "cli-pack",
      matchProjectTypes: ["cli"],
      stages: ["mentor"],
      allowedTools: ["find_related_tests"],
      preferredTools: ["find_related_tests"],
      recommendedQuestions: ["CLI question"],
      evidenceRequirements: ["CLI evidence"],
      stopConditions: ["CLI stop"],
      maxDiscoveryActions: 2,
      maxEvidenceFiles: 6,
    });
    writeSkill(skills, "mono", {
      name: "monorepo-pack",
      matchProjectTypes: ["monorepo"],
      stages: ["mentor"],
      allowedTools: ["trace_module_dependencies"],
      preferredTools: ["trace_module_dependencies"],
      recommendedQuestions: ["Package question"],
      evidenceRequirements: ["Workspace evidence"],
      stopConditions: ["Package stop"],
      maxDiscoveryActions: 3,
      maxEvidenceFiles: 5,
    });

    const loaded = loadHarnessSkill(["cli", "monorepo"], "mentor", skills);

    expect(loaded.policy.skillNames).toEqual(["cli-pack", "monorepo-pack"]);
    expect(loaded.policy.allowedTools).toEqual([
      "find_related_tests",
      "trace_module_dependencies",
    ]);
    expect(loaded.policy.maxDiscoveryActions).toBe(2);
    expect(loaded.policy.maxEvidenceFiles).toBe(5);
    expect(loaded.sdkSkillNames).toEqual([
      "repomentor:cli-pack",
      "repomentor:monorepo-pack",
    ]);
  });

  it("falls back to safe built-in constraints when manifests are missing or invalid", () => {
    const skills = createSkillsDirectory();
    fs.mkdirSync(path.join(skills, "invalid"));
    fs.writeFileSync(path.join(skills, "invalid", "skill.json"), "{broken");

    const loaded = loadHarnessSkill(["unknown"], "explorer", skills);

    expect(loaded.policy).toEqual(DEFAULT_HARNESS_SKILL_POLICY);
    expect(loaded.promptContent).toBe("");
    expect(loaded.sdkSkillNames).toEqual(["repomentor:analyze-generic"]);
  });

  it("publishes the trusted SDK plugin, skills, and prompt assets", () => {
    const root = process.cwd();
    const manifest = JSON.parse(fs.readFileSync(
      path.join(root, ".claude-plugin", "plugin.json"),
      "utf8",
    ));
    const packageJson = JSON.parse(fs.readFileSync(
      path.join(root, "package.json"),
      "utf8",
    ));

    expect(manifest.name).toBe("repomentor");
    expect(packageJson.files).toEqual(expect.arrayContaining([
      ".claude-plugin",
      "agents",
      "skills",
    ]));

    for (const skill of fs.readdirSync(path.join(root, "skills"))) {
      expect(fs.existsSync(path.join(root, "skills", skill, "skill.json"))).toBe(true);
      expect(fs.existsSync(path.join(root, "skills", skill, "SKILL.md"))).toBe(true);
    }
    for (const agent of ["orchestrator", "explorer", "mentor", "contributor"]) {
      const prompt = fs.readFileSync(path.join(root, "agents", agent, "agent.md"), "utf8");
      const frontmatter = prompt.match(/^---\s*\n([\s\S]*?)\n---/)?.[1] ?? "";
      expect(frontmatter).not.toMatch(/^tools:/m);
      expect(frontmatter).not.toMatch(/^model:/m);
    }
  });
});

function createSkillsDirectory(): string {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "repomentor-skills-"));
  temporaryDirectories.push(directory);
  return directory;
}

function writeSkill(
  root: string,
  directoryName: string,
  manifest: Record<string, unknown>,
): void {
  const directory = path.join(root, directoryName);
  fs.mkdirSync(directory);
  fs.writeFileSync(path.join(directory, "skill.json"), JSON.stringify(manifest));
  fs.writeFileSync(path.join(directory, "SKILL.md"), `# ${String(manifest.name)}`);
}
