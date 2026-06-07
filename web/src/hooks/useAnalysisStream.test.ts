import { renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useAnalysisStream } from './useAnalysisStream';

describe('useAnalysisStream', () => {
  let mockEventSource: any;

  beforeEach(() => {
    mockEventSource = {
      addEventListener: vi.fn(),
      close: vi.fn()
    };
    global.EventSource = vi.fn(function() { return mockEventSource; }) as any;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('initializes and cleans up EventSource', () => {
    const { unmount } = renderHook(() => useAnalysisStream('task-123'));
    
    expect(global.EventSource).toHaveBeenCalledWith('/analysis/task-123/stream');
    expect(mockEventSource.addEventListener).toHaveBeenCalledWith('task:created', expect.any(Function));
    expect(mockEventSource.addEventListener).toHaveBeenCalledWith('task:log', expect.any(Function));
    
    unmount();
    expect(mockEventSource.close).toHaveBeenCalled();
  });

  it('does nothing if taskId is null', () => {
    renderHook(() => useAnalysisStream(null));
    expect(global.EventSource).not.toHaveBeenCalled();
  });
});
