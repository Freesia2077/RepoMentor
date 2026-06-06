import { EventEmitter } from "node:events";
import type { SSEEvent } from "../types/index.js";

export class SSEManager {
  private emitters = new Map<string, EventEmitter>();

  subscribe(taskId: string): EventEmitter {
    const existing = this.emitters.get(taskId);
    if (existing) return existing;

    const emitter = new EventEmitter();
    emitter.setMaxListeners(50);
    this.emitters.set(taskId, emitter);
    return emitter;
  }

  unsubscribe(taskId: string): void {
    const emitter = this.emitters.get(taskId);
    if (emitter) {
      emitter.removeAllListeners();
      this.emitters.delete(taskId);
    }
  }

  emit(taskId: string, event: SSEEvent): void {
    const emitter = this.emitters.get(taskId);
    if (emitter) {
      emitter.emit("event", event);
    }
  }

  hasSubscribers(taskId: string): boolean {
    const emitter = this.emitters.get(taskId);
    return emitter !== undefined && emitter.listenerCount("event") > 0;
  }

  static serialize(event: SSEEvent): string {
    const data = JSON.stringify(event);
    return `event: ${event.type}\ndata: ${data}\n\n`;
  }
}

export const sseManager = new SSEManager();
