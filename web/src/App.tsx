import { useState } from 'react';
import { InputView } from './components/InputView';
import { createAnalysis, answerInteraction } from './api';
import { useAnalysisStream } from './hooks/useAnalysisStream';
import { ProgressUI } from './components/ProgressUI';
import { AnchorNav, ExplorerSection, MentorSection, ContributorSection } from './components/ReportSections';

export default function App() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [repoName, setRepoName] = useState<string>('');
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
      // Basic repo name parsing
      let parsedName = url;
      try {
        if (url.includes('github.com')) {
          const parts = new URL(url.startsWith('http') ? url : `https://${url}`).pathname.split('/').filter(Boolean);
          if (parts.length >= 2) parsedName = `${parts[0]}/${parts[1]}`;
        } else if (url.split('/').length === 2 && !url.includes(' ')) {
          parsedName = url;
        }
      } catch (e) {
        // ignore
      }
      setRepoName(parsedName);

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
      <div style={{marginBottom: '2rem'}}>
        {streamState.status === 'completed' ? (
          <h2 style={{margin: 0}}>Analysis Report: <span className="text-secondary">{repoName}</span></h2>
        ) : (
          <h2 style={{margin: 0}}>Analyzing <span className="text-secondary">{repoName}</span>...</h2>
        )}
      </div>

      {streamState.error && <div style={{color: 'red', marginBottom: '1rem'}}>{streamState.error}</div>}
      
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
        <div style={{marginTop: '2rem'}}>
          <AnchorNav />
          <ExplorerSection data={streamState.result?.explorer} />
          <MentorSection data={streamState.result?.mentor} />
          <ContributorSection data={streamState.result?.contributor} />
        </div>
      )}
    </div>
  );
}
