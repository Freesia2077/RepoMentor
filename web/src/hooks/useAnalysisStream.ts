import { useState, useEffect } from 'react';

export type AnalysisStatus = 'cloning' | 'analyzing' | 'interaction_required' | 'completed' | 'error';

export interface AnalysisState {
  status: AnalysisStatus;
  logs: string[];
  question?: { id: string; text: string };
  error?: string;
  result?: any;
}

export function useAnalysisStream(taskId: string | null) {
  const [state, setState] = useState<AnalysisState>({ status: 'cloning', logs: [] });

  useEffect(() => {
    if (!taskId) return;
    
    // Reset state when taskId changes
    setState({ status: 'cloning', logs: [] });
    
    const eventSource = new EventSource(`/analysis/${taskId}/stream`);

    eventSource.addEventListener('task:created', (e: any) => {
      const data = JSON.parse(e.data);
      setState(s => ({ ...s, status: data.status }));
    });

    eventSource.addEventListener('task:log', (e: any) => {
      const data = JSON.parse(e.data);
      setState(s => ({ ...s, logs: [...s.logs, data.message] }));
    });

    eventSource.addEventListener('task:interaction_required', (e: any) => {
      const data = JSON.parse(e.data);
      setState(s => ({ ...s, status: 'interaction_required', question: { id: data.questionId, text: data.question } }));
    });

    eventSource.addEventListener('task:completed', (e: any) => {
      const data = JSON.parse(e.data);
      setState(s => ({ ...s, status: 'completed', result: data.result }));
      eventSource.close();
    });

    eventSource.addEventListener('task:error', (e: any) => {
      const data = JSON.parse(e.data);
      setState(s => ({ ...s, status: 'error', error: data.error }));
      eventSource.close();
    });

    return () => {
      eventSource.close();
    };
  }, [taskId]);

  return state;
}
