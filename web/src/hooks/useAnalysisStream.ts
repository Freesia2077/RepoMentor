import { useState, useEffect, useCallback } from 'react';
import type { TaskStatus, StageProgress, SSEEvent, AnalysisResult } from '@backend-types/index';
import { getAnalysis } from '../api';
import type { AnalysisLog } from '../types';

export interface StreamState {
  status: TaskStatus;
  stageProgress: StageProgress;
  logs: AnalysisLog[];
  interaction: { id: string; question: string; options: string[] } | null;
  result: Partial<AnalysisResult>;
  error: string | null;
}

const initialState: StreamState = {
  status: 'cloning',
  stageProgress: { explorer: 'pending', mentor: 'pending', contributor: 'pending' },
  logs: [],
  interaction: null,
  result: {},
  error: null
};

export function useAnalysisStream(taskId: string | null) {
  const [state, setState] = useState<StreamState>(initialState);

  const clearInteraction = useCallback(() => {
    setState(s => ({ ...s, interaction: null }));
  }, []);

  useEffect(() => {
    if (!taskId) return;

    let disposed = false;
    setState(initialState);
    const eventSource = new EventSource(`/api/analysis/${taskId}/stream`);

    const hydrateFromSnapshot = async () => {
      try {
        const snapshot = await getAnalysis(taskId);
        if (disposed) return;

        setState(s => ({
          ...s,
          status: mergeStatus(s.status, snapshot.status),
          stageProgress: mergeStageProgress(s.stageProgress, snapshot.stageProgress),
          result: snapshot.result ? { ...s.result, ...snapshot.result } : s.result,
          error: snapshot.error?.message ?? (s.error === 'Connection lost, reconnecting…' ? null : s.error)
        }));

        if (snapshot.status === 'completed' || snapshot.status === 'failed') {
          eventSource.close();
        }
      } catch (err) {
        if (!disposed) {
          setState(s => ({
            ...s,
            error: err instanceof Error ? err.message : 'Failed to restore task state'
          }));
        }
      }
    };

    const handleEvent = (e: Event) => {
      try {
        const messageEvent = e as MessageEvent;
        const event = JSON.parse(messageEvent.data) as SSEEvent;
        
        switch (event.type) {
          case 'task:created':
            setState(s => ({ ...s, status: event.status }));
            break;
          case 'task:error':
            setState(s => ({ ...s, status: 'failed', error: event.error.message }));
            eventSource.close();
            break;
          case 'stage:progress':
            setState(s => ({
              ...s,
              logs: [...s.logs, {
                message: event.message,
                timestamp: event.timestamp ?? new Date().toISOString()
              }]
            }));
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
            setState(s => ({ ...s, status: 'completed', result: event.result }));
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

    eventSource.addEventListener('open', () => {
      setState(s => ({
        ...s,
        error: s.error === 'Connection lost, reconnecting…' ? null : s.error
      }));
    });

    eventSource.addEventListener('error', () => {
      setState(s => ({ ...s, error: 'Connection lost, reconnecting…' }));
      void hydrateFromSnapshot();
    });

    void hydrateFromSnapshot();

    return () => {
      disposed = true;
      eventSource.close();
    };
  }, [taskId]);

  return { state, clearInteraction };
}

const stageRank = { pending: 0, running: 1, done: 2 } as const;

function mergeStageProgress(
  current: StageProgress,
  snapshot: StageProgress | undefined,
): StageProgress {
  if (!snapshot) return current;
  return {
    explorer: stageRank[snapshot.explorer] > stageRank[current.explorer] ? snapshot.explorer : current.explorer,
    mentor: stageRank[snapshot.mentor] > stageRank[current.mentor] ? snapshot.mentor : current.mentor,
    contributor: stageRank[snapshot.contributor] > stageRank[current.contributor] ? snapshot.contributor : current.contributor,
  };
}

function mergeStatus(current: TaskStatus, snapshot: TaskStatus): TaskStatus {
  if (current === 'completed' || current === 'failed') return current;
  return snapshot;
}
