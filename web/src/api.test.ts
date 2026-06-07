import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createAnalysis, getAnalysis, answerInteraction } from './api';

describe('API Client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('createAnalysis posts to /analysis', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ taskId: '123' })
    } as Response);
    
    const res = await createAnalysis('https://github.com/foo/bar', 'main');
    expect(res.taskId).toBe('123');
    expect(fetch).toHaveBeenCalledWith('/analysis', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repoUrl: 'https://github.com/foo/bar', branch: 'main' })
    }));
  });

  it('createAnalysis throws error on failure', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      text: async () => 'Custom error message'
    } as Response);
    
    await expect(createAnalysis('https://github.com/foo/bar', 'main')).rejects.toThrow('Custom error message');
  });

  it('createAnalysis throws fallback error on empty response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      text: async () => ''
    } as Response);
    
    await expect(createAnalysis('https://github.com/foo/bar', 'main')).rejects.toThrow('Failed to create analysis');
  });

  it('getAnalysis gets from /analysis/:taskId', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'pending' })
    } as Response);
    
    const res = await getAnalysis('123');
    expect((res as any).status).toBe('pending');
    expect(fetch).toHaveBeenCalledWith('/analysis/123');
  });

  it('getAnalysis throws error on failure', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      text: async () => 'Not found'
    } as Response);
    
    await expect(getAnalysis('123')).rejects.toThrow('Not found');
  });

  it('getAnalysis throws fallback error on empty response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      text: async () => ''
    } as Response);
    
    await expect(getAnalysis('123')).rejects.toThrow('Failed to get analysis');
  });

  it('answerInteraction posts to /analysis/:taskId/ask', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true })
    } as Response);
    
    const res = await answerInteraction('123', 'q1', 'my answer');
    expect((res as any).success).toBe(true);
    expect(fetch).toHaveBeenCalledWith('/analysis/123/ask', expect.objectContaining({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questionId: 'q1', answer: 'my answer' })
    }));
  });

  it('answerInteraction throws error on failure', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      text: async () => 'Validation error'
    } as Response);
    
    await expect(answerInteraction('123', 'q1', 'my answer')).rejects.toThrow('Validation error');
  });

  it('answerInteraction throws fallback error on empty response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      text: async () => ''
    } as Response);
    
    await expect(answerInteraction('123', 'q1', 'my answer')).rejects.toThrow('Failed to answer interaction');
  });
});
