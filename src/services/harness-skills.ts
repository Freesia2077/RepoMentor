import fs from "node:fs";
import path from "node:path";
import { z } from "zod";
import type {
  EvidencePhase,
  HarnessDiscoveryToolName,
  HarnessSkillPolicy,
  LoadedHarnessSkill,
} from "../types/index.js";
import { resolveAppAsset } from "../lib/app-paths.js";

const discoveryToolSchema = z.enum([
  "search_symbols",
  "trace_module_dependencies",
  "find_related_tests",
]);

const skillManifestSchema = z.object({
  name: z.string().min(1),
  matchProjectTypes: z.array(z.string().min(1)).min(1),
  stages: z.array(z.enum(["explorer", "mentor"])).min(1),
  allowedTools: z.array(discoveryToolSchema),
  preferredTools: z.array(discoveryToolSchema),
  recommendedQuestions: z.array(z.string().min(1)).max(8),
  evidenceRequirements: z.array(z.string().min(1)).max(8),
  stopConditions: z.array(z.string().min(1)).max(8),
  maxDiscoveryActions: z.number().int().min(0).max(4),
  maxEvidenceFiles: z.number().int().min(1).max(10),
});

type SkillManifest = z.infer<typeof skillManifestSchema>;

const SKILLS_DIRECTORY = resolveAppAsset("skills");
const GENERIC_SKILL_NAME = "analyze-generic";
const DEFAULT_TOOLS: HarnessDiscoveryToolName[] = [
  "search_symbols",
  "trace_module_dependencies",
  "find_related_tests",
];

export const DEFAULT_HARNESS_SKILL_POLICY: HarnessSkillPolicy = {
  skillNames: [GENERIC_SKILL_NAME],
  allowedTools: [...DEFAULT_TOOLS],
  preferredTools: ["trace_module_dependencies", "find_related_tests"],
  recommendedQuestions: [
    "Where is the public or runtime entry point?",
    "Which implementation and test files prove the main control flow?",
  ],
  evidenceRequirements: [
    "At least one implementation entry or core module",
    "At least one representative verification path when present",
  ],
  stopConditions: [
    "The main entry, core responsibility, and a verification path are supported or recorded as evidence gaps",
  ],
  maxDiscoveryActions: 3,
  maxEvidenceFiles: 8,
};

export function loadHarnessSkill(
  projectTypes: string[],
  stage: EvidencePhase,
  skillsDirectory = SKILLS_DIRECTORY,
): LoadedHarnessSkill {
  const manifests = readSkillManifests(skillsDirectory)
    .filter((item) => item.manifest.stages.includes(stage));
  const normalizedTypes = new Set(
    projectTypes.map((value) => value.trim().toLowerCase()).filter(Boolean),
  );
  const specific = manifests.filter(({ manifest }) =>
    manifest.name !== GENERIC_SKILL_NAME
    && manifest.matchProjectTypes.some((type) => normalizedTypes.has(type.toLowerCase()))
  );
  const selected = specific.length > 0
    ? specific
    : manifests.filter(({ manifest }) =>
      manifest.name === GENERIC_SKILL_NAME
      || manifest.matchProjectTypes.includes("*")
      || manifest.matchProjectTypes.some((type) => normalizedTypes.has(type.toLowerCase()))
    ).slice(0, 1);

  if (selected.length === 0) {
    return {
      policy: clonePolicy(DEFAULT_HARNESS_SKILL_POLICY),
      promptContent: "",
    };
  }

  const allowedTools = unique(selected.flatMap(({ manifest }) => manifest.allowedTools));
  const policy: HarnessSkillPolicy = {
    skillNames: selected.map(({ manifest }) => manifest.name),
    allowedTools,
    preferredTools: unique(
      selected.flatMap(({ manifest }) => manifest.preferredTools),
    ).filter((tool) => allowedTools.includes(tool)),
    recommendedQuestions: unique(
      selected.flatMap(({ manifest }) => manifest.recommendedQuestions),
    ).slice(0, 8),
    evidenceRequirements: unique(
      selected.flatMap(({ manifest }) => manifest.evidenceRequirements),
    ).slice(0, 8),
    stopConditions: unique(
      selected.flatMap(({ manifest }) => manifest.stopConditions),
    ).slice(0, 8),
    maxDiscoveryActions: Math.min(
      ...selected.map(({ manifest }) => manifest.maxDiscoveryActions),
    ),
    maxEvidenceFiles: Math.min(
      ...selected.map(({ manifest }) => manifest.maxEvidenceFiles),
    ),
  };

  return {
    policy,
    promptContent: selected.map(({ markdown }) => markdown).filter(Boolean).join("\n\n---\n\n"),
  };
}

function readSkillManifests(skillsDirectory: string): Array<{
  manifest: SkillManifest;
  markdown: string;
}> {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(skillsDirectory, { withFileTypes: true });
  } catch {
    return [];
  }

  return entries
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) => {
      const directory = path.join(skillsDirectory, entry.name);
      try {
        const manifest = skillManifestSchema.parse(JSON.parse(
          fs.readFileSync(path.join(directory, "skill.json"), "utf8"),
        ));
        const markdown = fs.readFileSync(path.join(directory, "SKILL.md"), "utf8");
        return [{ manifest, markdown }];
      } catch {
        return [];
      }
    });
}

function unique<T>(values: T[]): T[] {
  return [...new Set(values)];
}

function clonePolicy(policy: HarnessSkillPolicy): HarnessSkillPolicy {
  return {
    ...policy,
    skillNames: [...policy.skillNames],
    allowedTools: [...policy.allowedTools],
    preferredTools: [...policy.preferredTools],
    recommendedQuestions: [...policy.recommendedQuestions],
    evidenceRequirements: [...policy.evidenceRequirements],
    stopConditions: [...policy.stopConditions],
  };
}
