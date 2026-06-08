import type { CreateAnalysisResponse, GetAnalysisResponse, AskResponse } from '@backend-types/index';

export async function createAnalysis(repoUrl: string, branch: string = 'main'): Promise<CreateAnalysisResponse> {
  const res = await fetch('/api/analysis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repoUrl, branch })
  });
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || 'Failed to create analysis');
  }
  return res.json();
}

export async function getAnalysis(taskId: string): Promise<GetAnalysisResponse> {
  const res = await fetch(`/api/analysis/${taskId}`);
  if (!res.ok) {
    const errText = await res.text();
    throw new Error(errText || 'Failed to get analysis');
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
    const errText = await res.text();
    throw new Error(errText || 'Failed to answer interaction');
  }
  return res.json();
}
