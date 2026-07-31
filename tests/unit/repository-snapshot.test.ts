import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { buildRepositorySnapshot } from "../../src/lib/repository-snapshot.js";

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

describe("repository snapshot", () => {
  it("collects bounded structural evidence without reading source bodies", async () => {
    const files = {
      "README.md": "# Example SDK\nA small client library.",
      "pyproject.toml": "[project]\nname = \"example-sdk\"",
      "src/example/__init__.py": "SECRET_SOURCE_BODY = true",
      "src/example/client.py": "class Client: pass",
      "tests/test_client.py": "def test_client(): pass",
    };
    const repository = await createRepository(files);

    const snapshot = await buildRepositorySnapshot(repository, Object.keys(files));

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
    expect(snapshot.entryCandidates).toContain("src/example/__init__.py");
    expect(JSON.stringify(snapshot)).not.toContain("SECRET_SOURCE_BODY");
  });

  it("truncates oversized evidence and skips binary metadata", async () => {
    const files = {
      "README.md": "x".repeat(20_000),
      "package.json": Buffer.from([0, 1, 2, 3]),
    };
    const repository = await createRepository(files);

    const snapshot = await buildRepositorySnapshot(repository, Object.keys(files));

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

    const snapshot = await buildRepositorySnapshot(repository, Object.keys(files));

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

    const snapshot = await buildRepositorySnapshot(repository, Object.keys(files));

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
});
