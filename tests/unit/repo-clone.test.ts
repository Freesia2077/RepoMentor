import { afterEach, describe, expect, it } from "vitest";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { simpleGit } from "simple-git";
import { cloneRepo } from "../../src/lib/repo.js";

const tempDirs: string[] = [];

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

describe("cloneRepo", () => {
  it("checks out the requested branch and refreshes a cached clone", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "repomentor-clone-"));
    tempDirs.push(root);
    const source = path.join(root, "source");
    const target = path.join(root, "cache");
    await mkdir(source);

    const sourceGit = simpleGit(source);
    await sourceGit.init(["--initial-branch=main"]);
    await sourceGit.addConfig("user.name", "RepoMentor Test");
    await sourceGit.addConfig("user.email", "test@example.com");
    await writeFile(path.join(source, "branch.txt"), "main", "utf8");
    await sourceGit.add(".");
    await sourceGit.commit("main");

    await sourceGit.checkoutLocalBranch("feature/test");
    await writeFile(path.join(source, "branch.txt"), "feature-v1", "utf8");
    await sourceGit.add(".");
    await sourceGit.commit("feature v1");

    const first = await cloneRepo(source, target, "feature/test", {
      depth: 1,
      timeoutMs: 10_000,
    });
    expect(first.cached).toBe(false);
    expect((await first.git.branch()).current).toBe("feature/test");
    expect(await readFile(path.join(target, "branch.txt"), "utf8")).toBe("feature-v1");

    await writeFile(path.join(source, "branch.txt"), "feature-v2", "utf8");
    await sourceGit.add(".");
    await sourceGit.commit("feature v2");

    const second = await cloneRepo(source, target, "feature/test", {
      depth: 1,
      timeoutMs: 10_000,
    });
    expect(second.cached).toBe(true);
    expect(second.commitHash).not.toBe(first.commitHash);
    expect(await readFile(path.join(target, "branch.txt"), "utf8")).toBe("feature-v2");
  });
});
