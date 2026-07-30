import { EventEmitter } from "node:events";
import type { SSEEvent } from "../types/index.js";

export interface StoredSSEEvent {
  id: number;
  event: SSEEvent;
}

export class SSEManager {
  private emitters = new Map<string, EventEmitter>();
  private history = new Map<string, StoredSSEEvent[]>();
  private sequence = new Map<string, number>();
  private readonly maxHistory = 200;

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
    const id = (this.sequence.get(taskId) ?? 0) + 1;
    this.sequence.set(taskId, id);
    const stored = { id, event };
    const events = this.history.get(taskId) ?? [];
    events.push(stored);
    if (events.length > this.maxHistory) events.shift();
    this.history.set(taskId, events);

    const emitter = this.emitters.get(taskId);
    if (emitter) {
      emitter.emit("event", stored);
    }
  }

  getEventsAfter(taskId: string, lastEventId = 0): StoredSSEEvent[] {
    return (this.history.get(taskId) ?? []).filter((item) => item.id > lastEventId);
  }

  hasSubscribers(taskId: string): boolean {
    const emitter = this.emitters.get(taskId);
    return emitter !== undefined && emitter.listenerCount("event") > 0;
  }

  static serialize(stored: StoredSSEEvent): string {
    const data = JSON.stringify(stored.event);
    return `id: ${stored.id}\nevent: ${stored.event.type}\ndata: ${data}\n\n`;
  }
}

export const sseManager = new SSEManager();
