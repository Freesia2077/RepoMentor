import { useState } from 'react';
import { InputView } from './components/InputView';
import { createAnalysis, answerInteraction } from './api';
import { useAnalysisStream } from './hooks/useAnalysisStream';
import { ProgressUI } from './components/ProgressUI';

export default function App() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [interactionError, setInteractionError] = useState<string | null>(null);
  const { state: streamState, clearInteraction } = useAnalysisStream(taskId);

  const handleAnswer = async (id: string, answer: string) => {
    if (!taskId) return;
    setInteractionError(null);
    try {
      await answerInteraction(taskId, id, answer);
      clearInteraction(); // Optimistically clear interaction after successful send
    } catch (err) {
      console.error(err);
      setInteractionError(err instanceof Error ? err.message : 'Failed to send answer');
    }
  };


  const handleAnalyze = async (url: string, branch: string) => {
    setSubmitError(null);
    try {
      const res = await createAnalysis(url, branch);
      setTaskId(res.taskId);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Failed to start analysis');
    }
  };

  if (!taskId) {
    return (
      <div className="container">
        <InputView onSubmit={handleAnalyze} error={submitError} />
      </div>
    );
  }

  return (
    <div className="container">
      <h2>Analysis Task: {taskId}</h2>
      {streamState.error && <div style={{color: 'red'}}>{streamState.error}</div>}
      
      {streamState.status !== 'completed' && (
        <ProgressUI 
          stages={streamState.stageProgress}
          logs={streamState.logs}
          interaction={streamState.interaction}
          onAnswer={handleAnswer}
          interactionError={interactionError}
        />
      )}

      {streamState.status === 'completed' && (
        <div style={{marginTop: '3rem'}}>
          <h3>Analysis Complete</h3>
        </div>
      )}
    </div>
  );
}
