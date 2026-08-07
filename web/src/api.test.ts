import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  answerInteraction,
  createAnalysis,
  getAnalysis,
  getModelSettings,
  saveModelSettings,
} from './api';

describe('API Client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('createAnalysis posts to /api/analysis', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ taskId: '123' })
    } as Response);
    
    const res = await createAnalysis('https://github.com/foo/bar', 'main');
    expect(res.taskId).toBe('123');
    expect(fetch).toHaveBeenCalledWith('/api/analysis', expect.objectContaining({
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

  it('getAnalysis gets from /api/analysis/:taskId', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'pending' })
    } as Response);
    
    const res = await getAnalysis('123');
    expect((res as any).status).toBe('pending');
    expect(fetch).toHaveBeenCalledWith('/api/analysis/123');
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

  it('answerInteraction posts to /api/analysis/:taskId/ask', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ success: true })
    } as Response);
    
    const res = await answerInteraction('123', 'q1', 'my answer');
    expect((res as any).success).toBe(true);
    expect(fetch).toHaveBeenCalledWith('/api/analysis/123/ask', expect.objectContaining({
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

  it('loads public model settings without exposing an API key', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        provider: 'anthropic-compatible',
        baseUrl: 'https://example.com',
        model: 'example-model',
        hasApiKey: true,
        source: 'file',
      }),
    } as Response);

    const settings = await getModelSettings();
    expect(settings.hasApiKey).toBe(true);
    expect(fetch).toHaveBeenCalledWith('/api/settings/model', { cache: 'no-store' });
  });

  it('saves model settings locally through the backend', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        provider: 'openai-compatible',
        baseUrl: 'https://api.openai.com/v1',
        model: 'example-model',
        hasApiKey: true,
        source: 'file',
      }),
    } as Response);

    await saveModelSettings({
      provider: 'openai-compatible',
      baseUrl: 'https://api.openai.com/v1',
      model: 'example-model',
      apiKey: 'secret',
    });

    expect(fetch).toHaveBeenCalledWith('/api/settings/model', expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify({
        provider: 'openai-compatible',
        baseUrl: 'https://api.openai.com/v1',
        model: 'example-model',
        apiKey: 'secret',
      }),
    }));
  });

  it('uses a structured backend error message when available', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      text: async () => JSON.stringify({ message: 'Configure a model first' }),
    } as Response);

    await expect(createAnalysis('foo/bar')).rejects.toThrow('Configure a model first');
  });
});
