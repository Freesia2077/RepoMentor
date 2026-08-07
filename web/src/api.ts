import type {
  AskResponse,
  CreateAnalysisResponse,
  GetAnalysisResponse,
  PublicModelSettings,
  UpdateModelSettingsRequest,
} from '@backend-types/index';

export async function createAnalysis(repoUrl: string, branch: string = 'main'): Promise<CreateAnalysisResponse> {
  const res = await fetch('/api/analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repoUrl, branch })
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, 'Failed to create analysis'));
  }
  return res.json();
}

export async function getAnalysis(taskId: string): Promise<GetAnalysisResponse> {
  const res = await fetch(`/api/analysis/${taskId}`);
  if (!res.ok) {
    throw new Error(await readApiError(res, 'Failed to get analysis'));
  }
  return res.json();
}

export async function answerInteraction(taskId: string, questionId: string, answer: string): Promise<AskResponse> {
  const res = await fetch(`/api/analysis/${taskId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ questionId, answer })
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, 'Failed to answer interaction'));
  }
  return res.json();
}

export async function getModelSettings(): Promise<PublicModelSettings> {
  const res = await fetch('/api/settings/model', { cache: 'no-store' });
  if (!res.ok) {
    throw new Error(await readApiError(res, 'Failed to load model settings'));
  }
  return res.json();
}

export async function saveModelSettings(
  settings: UpdateModelSettingsRequest,
): Promise<PublicModelSettings> {
  const res = await fetch('/api/settings/model', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!res.ok) {
    throw new Error(await readApiError(res, 'Failed to save model settings'));
  }
  return res.json();
}

async function readApiError(response: Response, fallback: string): Promise<string> {
  const text = await response.text();
  if (!text) return fallback;
  try {
    const payload = JSON.parse(text) as { message?: unknown };
    if (typeof payload.message === 'string' && payload.message) return payload.message;
  } catch {
    // Plain-text backend errors are returned as-is.
  }
  return text;
}
