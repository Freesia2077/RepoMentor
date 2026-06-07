import { useState } from 'react';
import { InputView } from './components/InputView';
import { createAnalysis } from './api';
import { useAnalysisStream } from './hooks/useAnalysisStream';

export default function App() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const { state: streamState } = useAnalysisStream(taskId);

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
      <p>Status: {streamState.status}</p>
      {streamState.error && <div style={{color: 'red'}}>{streamState.error}</div>}
      <pre>{JSON.stringify(streamState.stageProgress, null, 2)}</pre>
    </div>
  );
}
