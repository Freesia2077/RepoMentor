import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  buildContributionEvidence,
  buildEvidenceBundle,
  buildRepositoryContributionContext,
  buildRepositoryOverview,
  buildRepositoryProfile,
} from "../../src/lib/repository-profile.js";

const tempDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    tempDirectories.splice(0).map((directory) =>
      fs.rm(directory, { recursive: true, force: true })
    ),
  );
});

async function createRepository(files: Record<string, string | Buffer>): Promise<string> {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "repomentor-snapshot-"));
  tempDirectories.push(directory);

  for (const [relativePath, content] of Object.entries(files)) {
    const absolutePath = path.join(directory, ...relativePath.split("/"));
    await fs.mkdir(path.dirname(absolutePath), { recursive: true });
    await fs.writeFile(absolutePath, content);
  }

  return directory;
}

describe("repository profile and evidence", () => {
  it("collects bounded structural evidence without reading source bodies", async () => {
    const files = {
      "README.md": "# Example SDK\nA small client library.",
      "pyproject.toml": "[project]\nname = \"example-sdk\"",
      "src/example/__init__.py": "SECRET_SOURCE_BODY = true",
      "src/example/client.py": "class Client: pass",
      "tests/test_client.py": "def test_client(): pass",
    };
    const repository = await createRepository(files);

    const snapshot = await buildRepositoryProfile(repository, Object.keys(files));

    expect(snapshot.fileCount).toBe(5);
    expect(snapshot.readme).toMatchObject({
      path: "README.md",
      content: expect.stringContaining("small client library"),
      truncated: false,
    });
    expect(snapshot.manifests).toEqual([
      expect.objectContaining({
        path: "pyproject.toml",
        content: expect.stringContaining("example-sdk"),
      }),
    ]);
    expect(snapshot.topLevelTree).toEqual(expect.arrayContaining([
      "README.md",
      "pyproject.toml",
      "src/",
      "src/example/",
      "tests/",
      "tests/test_client.py",
    ]));
    expect(snapshot.languageStats).toEqual({ Python: 3 });
    expect(snapshot.fileIndex).toEqual(
      Object.keys(files).sort((left, right) => left.localeCompare(right)),
    );
    expect(snapshot.fileIndexTruncated).toBe(false);
    expect(snapshot.directoryStats).toContainEqual({
      path: "src/example",
      files: 2,
      sourceFiles: 2,
      testFiles: 0,
    });
    expect(snapshot.entryCandidates).toContain("src/example/__init__.py");
    expect(snapshot.testCandidates).toContain("tests/test_client.py");
    expect(JSON.stringify(snapshot)).not.toContain("SECRET_SOURCE_BODY");
  });

  it("truncates oversized evidence and skips binary metadata", async () => {
    const files = {
      "README.md": "x".repeat(20_000),
      "package.json": Buffer.from([0, 1, 2, 3]),
    };
    const repository = await createRepository(files);

    const snapshot = await buildRepositoryProfile(repository, Object.keys(files));

    expect(snapshot.readme?.truncated).toBe(true);
    expect(Buffer.byteLength(snapshot.readme?.content ?? "", "utf8")).toBeLessThanOrEqual(10_000);
    expect(snapshot.manifests).toEqual([]);
  });

  it("prioritizes root manifests before workspace manifests", async () => {
    const files = {
      "package.json": "{\"private\":true}",
      "packages/a/package.json": "{\"name\":\"a\"}",
      "packages/b/package.json": "{\"name\":\"b\"}",
      "examples/demo/package.json": "{\"name\":\"demo\"}",
      "test/fixtures/server.crt": "not an entry point",
    };
    const repository = await createRepository(files);

    const snapshot = await buildRepositoryProfile(repository, Object.keys(files));

    expect(snapshot.manifests.map((manifest) => manifest.path)).toEqual([
      "package.json",
      "packages/a/package.json",
      "packages/b/package.json",
    ]);
    expect(snapshot.exampleManifests.map((manifest) => manifest.path)).toEqual([
      "examples/demo/package.json",
    ]);
    expect(snapshot.entryCandidates).toEqual([]);
  });

  it("collects contribution guidance and TODO evidence without retaining source bodies", async () => {
    const files = {
      "CONTRIBUTING.md": "Run npm test before opening a pull request.",
      "src/index.ts": "export function run() {\n  // TODO: add timeout handling\n}\n",
      "src/other.ts": "const secret = 'not included in the snapshot';",
    };
    const repository = await createRepository(files);

    const snapshot = await buildRepositoryProfile(repository, Object.keys(files));

    expect(snapshot.guidanceFiles).toEqual([
      expect.objectContaining({
        path: "CONTRIBUTING.md",
        content: expect.stringContaining("npm test"),
      }),
    ]);
    expect(snapshot.todoMarkers).toEqual([{
      file: "src/index.ts",
      line: 2,
      marker: "TODO",
      excerpt: "// TODO: add timeout handling",
    }]);
    expect(JSON.stringify(snapshot)).not.toContain("not included in the snapshot");
  });

  it("collects engineering configuration and reads only planned tracked evidence", async () => {
    const files = {
      "package.json": "{\"scripts\":{\"test\":\"vitest\"}}",
      "tsconfig.json": "{\"compilerOptions\":{\"strict\":true}}",
      ".github/workflows/ci.yml": "steps:\n  - run: npm test",
      "src/index.ts": "export { run } from './run.js';",
      "src/run.ts": "export function run() { return true; }",
      "tests/run.test.ts": "it('runs', () => {});",
    };
    const repository = await createRepository(files);
    const trackedFiles = Object.keys(files);

    const profile = await buildRepositoryProfile(repository, trackedFiles);
    const bundle = await buildEvidenceBundle(
      repository,
      {
        rationale: "Verify entry and implementation",
        files: [
          { path: "src/index.ts", purpose: "public exports", priority: "high" },
          { path: "src/run.ts", purpose: "core behavior", priority: "high" },
          { path: "missing.ts", purpose: "invalid", priority: "low" },
          { path: "src/index.ts", purpose: "duplicate", priority: "low" },
        ],
      },
      "explorer",
      [],
      trackedFiles,
    );

    expect(profile.configFiles.map((file) => file.path)).toEqual([
      "tsconfig.json",
      ".github/workflows/ci.yml",
    ]);
    expect(profile.testCandidates).toContain("tests/run.test.ts");
    expect(bundle.files).toEqual([
      expect.objectContaining({
        path: "src/index.ts",
        purpose: "public exports",
        phase: "explorer",
        content: expect.stringContaining("export"),
      }),
      expect.objectContaining({
        path: "src/run.ts",
        purpose: "core behavior",
        phase: "explorer",
      }),
    ]);
    expect(bundle.skippedPaths).toEqual(["missing.ts", "src/index.ts"]);
    expect(bundle.totalBytes).toBeGreaterThan(0);
  });

  it("builds a compact Mentor overview without repeating repository file contents", async () => {
    const repository = await createRepository({
      "README.md": "UNIQUE_README_BODY",
      "package.json": "{\"name\":\"example\",\"private\":true}",
      "tsconfig.json": "{\"compilerOptions\":{\"strict\":true}}",
      "src/index.ts": "export const value = true;",
      "tests/index.test.ts": "it('works', () => {});",
    });
    const profile = await buildRepositoryProfile(repository, [
      "README.md",
      "package.json",
      "tsconfig.json",
      "src/index.ts",
      "tests/index.test.ts",
    ]);

    const overview = buildRepositoryOverview(profile);
    const serialized = JSON.stringify(overview);

    expect(overview.projectFiles).toEqual({
      readme: "README.md",
      manifests: ["package.json"],
      exampleManifests: [],
      configFiles: ["tsconfig.json"],
      guidanceFiles: [],
    });
    expect(serialized).not.toContain("UNIQUE_README_BODY");
    expect(serialized).not.toContain("compilerOptions");
    expect(serialized).not.toContain("fileIndex");
  });

  it("builds a focused Contributor context without repeating Explorer source bodies", async () => {
    const repository = await createRepository({
      "package.json": "{\"scripts\":{\"test\":\"vitest\"}}",
      "CONTRIBUTING.md": "Run tests before submitting.",
      "src/index.ts": "export const entry = true;",
      "src/core.ts": "export const core = true;",
    });
    const profile = await buildRepositoryProfile(repository, [
      "package.json",
      "CONTRIBUTING.md",
      "src/index.ts",
      "src/core.ts",
    ]);
    const context = buildRepositoryContributionContext(profile);
    const contributionEvidence = buildContributionEvidence({
      files: [
        {
          path: "src/index.ts",
          content: "UNIQUE_EXPLORER_SOURCE",
          truncated: false,
          purpose: "entry",
          phase: "explorer",
        },
        {
          path: "src/core.ts",
          content: "UNIQUE_MENTOR_SOURCE",
          truncated: false,
          purpose: "core behavior",
          phase: "mentor",
        },
      ],
      skippedPaths: [],
      totalBytes: 42,
    });

    expect(context.manifests[0]?.content).toContain("vitest");
    expect(context.guidanceFiles[0]?.content).toContain("Run tests");
    expect(contributionEvidence.examinedFiles).toHaveLength(2);
    expect(contributionEvidence.focusedFiles.map((file) => file.path)).toEqual([
      "src/core.ts",
    ]);
    expect(JSON.stringify(contributionEvidence)).not.toContain("UNIQUE_EXPLORER_SOURCE");
    expect(JSON.stringify(contributionEvidence)).toContain("UNIQUE_MENTOR_SOURCE");
  });

  it("keeps priority and cross-directory files in a truncated large-repository index", async () => {
    const repository = await createRepository({
      "z-package/src/index.ts": "export const important = true;",
    });
    const trackedFiles = [
      ...Array.from({ length: 3_000 }, (_, index) =>
        `a-generated/chunk-${String(index).padStart(4, "0")}.txt`
      ),
      "z-package/src/index.ts",
    ];

    const profile = await buildRepositoryProfile(repository, trackedFiles);

    expect(profile.fileIndexTruncated).toBe(true);
    expect(profile.fileIndex).toContain("z-package/src/index.ts");
    expect(profile.entryCandidates).toContain("z-package/src/index.ts");
    expect(profile.fileIndex.length).toBeLessThan(trackedFiles.length);
  });
});
