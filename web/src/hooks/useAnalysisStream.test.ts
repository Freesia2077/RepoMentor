import { renderHook } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useAnalysisStream } from './useAnalysisStream';

class MockEventSource {
  addEventListener = vi.fn();
  close = vi.fn();
}
global.EventSource = MockEventSource as any;

describe('useAnalysisStream', () => {
  it('initializes default state and exports clearInteraction', () => {
    const { result } = renderHook(() => useAnalysisStream('task-123'));
    expect(result.current.state.status).toBe('cloning');
    expect(typeof result.current.clearInteraction).toBe('function');
  });
});
