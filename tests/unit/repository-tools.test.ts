import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  findRepositoryRelatedTests,
  searchRepositorySymbols,
  traceRepositoryDependencies,
} from "../../src/lib/repository-tools.js";
import type { RepositoryProfile } from "../../src/types/index.js";

const repository = path.resolve("tests/fixtures/mini-repo");
const profile = {
  fileIndex: [
    "src/index.ts",
    "src/utils/helper.ts",
    "tests/utils/helper.test.ts",
  ],
} as RepositoryProfile;

describe("repository domain tools", () => {
  it("finds symbol evidence within bounded source files", async () => {
    const results = await searchRepositorySymbols(
      repository,
      profile,
      "logMiddleware",
    );
    expect(results).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: "src/index.ts" }),
      expect.objectContaining({ path: "src/utils/helper.ts" }),
    ]));
  });

  it("resolves TypeScript dependencies imported with a JavaScript suffix", async () => {
    const result = await traceRepositoryDependencies(
      repository,
      profile,
      ["src/index.ts"],
    );
    expect(result.edges).toContainEqual({
      from: "src/index.ts",
      to: "src/utils/helper.ts",
    });
  });

  it("locates related tests from the repository index", () => {
    expect(findRepositoryRelatedTests(
      profile,
      ["src/utils/helper.ts"],
    )).toEqual(["tests/utils/helper.test.ts"]);
  });
});
