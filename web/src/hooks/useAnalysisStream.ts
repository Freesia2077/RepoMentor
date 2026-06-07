import { useState, useEffect, useCallback } from 'react';
import type { TaskStatus, StageProgress, SSEEvent, TaskError, AnalysisResult } from '@backend-types/index';

export interface StreamState {
  status: TaskStatus;
  stageProgress: StageProgress;
  logs: string[];
  interaction: { id: string; question: string; options: string[] } | null;
  result: Partial<AnalysisResult>;
  error: string | null;
}

export function useAnalysisStream(taskId: string | null) {
  const [state, setState] = useState<StreamState>({
    status: 'cloning',
    stageProgress: { explorer: 'pending', mentor: 'pending', contributor: 'pending' },
    logs: [],
    interaction: null,
    result: {},
    error: null
  });

  const clearInteraction = useCallback(() => {
    setState(s => ({ ...s, interaction: null }));
  }, []);

  useEffect(() => {
    if (!taskId) return;

    const eventSource = new EventSource(`/analysis/${taskId}/stream`);

    const handleEvent = (e: Event) => {
      try {
        const messageEvent = e as MessageEvent;
        const event = JSON.parse(messageEvent.data) as SSEEvent;
        
        switch (messageEvent.type) {
          case 'task:created':
            setState(s => ({ ...s, status: event.status }));
            break;
          case 'task:error':
            setState(s => ({ ...s, error: (event as any).error.message }));
            eventSource.close();
            break;
          case 'stage:progress':
            setState(s => ({ ...s, logs: [...s.logs, event.message] }));
            break;
          case 'stage:start':
            setState(s => ({ ...s, stageProgress: { ...s.stageProgress, [event.stage]: 'running' } }));
            break;
          case 'stage:done':
            setState(s => ({ 
              ...s, 
              stageProgress: { ...s.stageProgress, [event.stage]: 'done' },
              result: { ...s.result, [event.stage]: event.output }
            }));
            break;
          case 'interact:ask':
            setState(s => ({ ...s, interaction: { id: event.questionId, question: event.question, options: event.options || [] } }));
            break;
          case 'interact:timeout':
            setState(s => ({ ...s, interaction: null }));
            break;
          case 'task:completed':
            setState(s => ({ ...s, status: 'completed' }));
            eventSource.close();
            break;
        }
      } catch (err) {
        console.error('SSE parse error:', err);
      }
    };

    // The backend only sends named events. We bind to specific event names.
    const eventTypes = ['task:created', 'task:error', 'stage:progress', 'stage:start', 'stage:done', 'interact:ask', 'interact:timeout', 'task:completed'];
    eventTypes.forEach(type => eventSource.addEventListener(type, handleEvent as EventListener));

    eventSource.addEventListener('error', () => {
      setState(s => ({ ...s, error: 'Connection lost' }));
      eventSource.close();
    });

    return () => eventSource.close();
  }, [taskId]);

  return { state, clearInteraction };
}
