import { describe, it, expect, vi } from 'vitest';
import { createAnalysis } from './api';

global.fetch = vi.fn();

describe('API Client', () => {
  it('createAnalysis posts to /analysis', async () => {
    (fetch as any).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ taskId: '123' })
    });
    
    const res = await createAnalysis('https://github.com/foo/bar', 'main');
    expect(res.taskId).toBe('123');
    expect(fetch).toHaveBeenCalledWith('/analysis', expect.objectContaining({
      method: 'POST'
    }));
  });
});
