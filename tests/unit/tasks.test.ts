import { describe, expect, it } from "vitest";
import { createMemoryDb } from "../../src/db/index.js";
import * as taskRepo from "../../src/db/repositories/tasks.js";
import type { TaskRecord } from "../../src/types/index.js";

function makeTask(overrides: Partial<TaskRecord> = {}): TaskRecord {
  return {
    taskId: "task_test",
    repoUrl: "https://github.com/facebook/react.git",
    branch: "main",
    status: "cloning",
    currentStage: null,
    stageProgress: { explorer: "pending", mentor: "pending", contributor: "pending" },
    result: null,
    error: null,
    commitHash: null,
    cached: false,
    createdAt: "2026-07-30T00:00:00.000Z",
    completedAt: null,
    ...overrides,
  };
}

describe("task repository", () => {
  it("persists and updates task state", () => {
    const db = createMemoryDb();
    const task = makeTask();
    taskRepo.save(db, task);

    task.status = "analyzing";
    task.currentStage = "mentor";
    task.stageProgress.explorer = "done";
    task.commitHash = "abc123";
    taskRepo.save(db, task);

    expect(taskRepo.findById(db, task.taskId)).toEqual(task);
    db.close();
  });

  it("marks interrupted tasks as retryable failures", () => {
    const db = createMemoryDb();
    taskRepo.save(db, makeTask());

    expect(taskRepo.markInterruptedAsFailed(db)).toBe(1);
    const restored = taskRepo.findById(db, "task_test");
    expect(restored?.status).toBe("failed");
    expect(restored?.error?.retryable).toBe(true);
    db.close();
  });
});
