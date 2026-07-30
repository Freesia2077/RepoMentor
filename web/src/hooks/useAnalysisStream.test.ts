import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useAnalysisStream } from './useAnalysisStream';
import { getAnalysis } from '../api';

vi.mock('../api', () => ({
  getAnalysis: vi.fn()
}));

class MockEventSource {
  listeners: Record<string, Function[]> = {};
  addEventListener = vi.fn((event: string, cb: Function) => {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(cb);
  });
  close = vi.fn();
  
  emit(type: string, data: any) {
    const event = new Event(type) as MessageEvent;
    // Inject type into data payload to match backend SSEEvent structure
    const payload = { type, ...data };
    (event as any).data = JSON.stringify(payload);
    this.listeners[type]?.forEach(cb => cb(event));
  }

  emitError() {
    const event = new Event('error');
    this.listeners['error']?.forEach(cb => cb(event));
  }
}

let mockEventSourceInstance: MockEventSource;

(globalThis as any).EventSource = class {
  constructor() {
    mockEventSourceInstance = new MockEventSource();
    return mockEventSourceInstance;
  }
} as any;

describe('useAnalysisStream', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAnalysis).mockReturnValue(new Promise(() => {}));
  });

  it('initializes default state and exports clearInteraction', () => {
    const { result } = renderHook(() => useAnalysisStream('task-123'));
    expect(result.current.state.status).toBe('cloning');
    expect(typeof result.current.clearInteraction).toBe('function');
  });

  it('handles incoming events and updates state accordingly', () => {
    const { result } = renderHook(() => useAnalysisStream('task-123'));
    
    act(() => {
      mockEventSourceInstance.emit('task:created', { type: 'task:created', status: 'running' });
    });
    expect(result.current.state.status).toBe('running');

    act(() => {
      mockEventSourceInstance.emit('stage:start', { type: 'stage:start', stage: 'explorer' });
    });
    expect(result.current.state.stageProgress.explorer).toBe('running');

    act(() => {
      mockEventSourceInstance.emit('stage:progress', {
        type: 'stage:progress',
        message: 'Analyzing files...',
        timestamp: '2026-07-30T12:00:00.000Z'
      });
    });
    expect(result.current.state.logs).toContainEqual({
      message: 'Analyzing files...',
      timestamp: '2026-07-30T12:00:00.000Z'
    });

    act(() => {
      mockEventSourceInstance.emit('stage:done', { type: 'stage:done', stage: 'explorer', output: { summary: 'done' } });
    });
    expect(result.current.state.stageProgress.explorer).toBe('done');
    expect(result.current.state.result.explorer).toEqual({ summary: 'done' });
    
    act(() => {
      mockEventSourceInstance.emit('interact:ask', { type: 'interact:ask', questionId: 'q1', question: 'Continue?', options: ['Yes', 'No'] });
    });
    expect(result.current.state.interaction).toEqual({ id: 'q1', question: 'Continue?', options: ['Yes', 'No'] });

    act(() => {
      result.current.clearInteraction();
    });
    expect(result.current.state.interaction).toBeNull();
  });

  it('handles task error event', () => {
    const { result } = renderHook(() => useAnalysisStream('task-123'));
    act(() => {
      // Use the correct structure that matches the implementation's expectation
      mockEventSourceInstance.emit('task:error', { type: 'task:error', error: { message: 'Failed to analyze' } });
    });
    expect(result.current.state.error).toBe('Failed to analyze');
  });

  it('handles EventSource error', () => {
    const { result } = renderHook(() => useAnalysisStream('task-123'));
    act(() => {
      mockEventSourceInstance.emitError();
    });
    expect(result.current.state.error).toBe('Connection lost, reconnecting…');
    expect(mockEventSourceInstance.close).not.toHaveBeenCalled();
  });

  it('hydrates a completed task that finished before SSE connected', async () => {
    const analysisResult = {
      explorer: { projectSummary: 'cached result' },
      mentor: {},
      contributor: {}
    };
    vi.mocked(getAnalysis).mockResolvedValueOnce({
      taskId: 'task-123',
      status: 'completed',
      stageProgress: { explorer: 'done', mentor: 'done', contributor: 'done' },
      result: analysisResult as never,
      createdAt: '2026-07-30T00:00:00.000Z',
      cached: true
    });

    const { result } = renderHook(() => useAnalysisStream('task-123'));

    await waitFor(() => expect(result.current.state.status).toBe('completed'));
    expect(result.current.state.result).toEqual(analysisResult);
    expect(mockEventSourceInstance.close).toHaveBeenCalled();
  });
});
