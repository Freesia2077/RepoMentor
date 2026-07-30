import { describe, expect, it, vi } from "vitest";
import { SSEManager } from "../../src/lib/sse.js";

describe("SSEManager", () => {
  it("stores events emitted before a subscriber connects", () => {
    const manager = new SSEManager();
    manager.emit("task-1", {
      type: "stage:start",
      stage: "explorer",
    });

    expect(manager.getEventsAfter("task-1")).toEqual([
      {
        id: 1,
        event: {
          type: "stage:start",
          stage: "explorer",
          timestamp: expect.any(String),
        },
      },
    ]);
  });

  it("supports resume after a Last-Event-ID", () => {
    const manager = new SSEManager();
    manager.emit("task-1", { type: "stage:start", stage: "explorer" });
    manager.emit("task-1", {
      type: "stage:progress",
      stage: "explorer",
      message: "reading",
    });

    expect(manager.getEventsAfter("task-1", 1)).toHaveLength(1);
    expect(SSEManager.serialize(manager.getEventsAfter("task-1", 1)[0]!)).toContain(
      "id: 2",
    );
  });

  it("publishes stored events to active subscribers", () => {
    const manager = new SSEManager();
    const listener = vi.fn();
    manager.subscribe("task-1").on("event", listener);

    manager.emit("task-1", { type: "stage:start", stage: "mentor" });

    expect(listener).toHaveBeenCalledWith({
      id: 1,
      event: {
        type: "stage:start",
        stage: "mentor",
        timestamp: expect.any(String),
      },
    });
  });

  it("preserves an explicitly supplied event timestamp", () => {
    const manager = new SSEManager();
    manager.emit("task-1", {
      type: "stage:start",
      stage: "explorer",
      timestamp: "2026-07-30T12:00:00.000Z",
    });

    expect(manager.getEventsAfter("task-1")[0]?.event.timestamp).toBe(
      "2026-07-30T12:00:00.000Z",
    );
  });
});
