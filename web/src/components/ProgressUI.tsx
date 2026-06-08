import { useState } from 'react';
import './ProgressUI.css';

interface Props {
  stages: Record<string, 'pending' | 'running' | 'done'>;
  logs: string[];
  interaction?: { id: string; question: string; options: string[] } | null;
  onAnswer: (id: string, answer: string) => Promise<void>;
  interactionError?: string | null;
}

export function ProgressUI({ stages, logs, interaction, onAnswer, interactionError }: Props) {
  const [submittingOpt, setSubmittingOpt] = useState<string | null>(null);

  const handleAnswer = async (id: string, opt: string) => {
    setSubmittingOpt(opt);
    await onAnswer(id, opt);
    setSubmittingOpt(null);
  };

  const getStatusIcon = (status: 'pending' | 'running' | 'done') => {
    if (status === 'done') return <span className="status-dot done" />;
    if (status === 'running') return <span className="status-dot running" />;
    return <span className="status-dot pending" />;
  };

  const activeLog = logs.length > 0 ? logs[logs.length - 1] : 'Waiting to start...';

  return (
    <div className="progress-ui">
      <div className="stage-indicator">
        {['explorer', 'mentor', 'contributor'].map(stage => {
          const status = stages[stage] || 'pending';
          return (
            <div key={stage} className={`stage-card ${status}`}>
              <div className="stage-header">
                {getStatusIcon(status)}
                <h3 className="stage-title">{stage.charAt(0).toUpperCase() + stage.slice(1)}</h3>
              </div>
              <div className="stage-body">
                {status === 'running' && <p className="stage-log text-secondary">{activeLog}</p>}
                {status === 'done' && <p className="stage-log text-secondary">Analysis complete.</p>}
                {status === 'pending' && <p className="stage-log text-secondary">Waiting in queue...</p>}
              </div>
            </div>
          );
        })}
      </div>

      {interaction && (
        <div className="interaction-box">
          <div className="interaction-header">
            <span className="interaction-icon">💬</span>
            <h4 style={{margin: 0}}>Agent Question</h4>
          </div>
          <p className="interaction-question">{interaction.question}</p>
          <div className="interaction-options">
            {interaction.options.map(opt => (
              <button 
                key={opt} 
                className="btn-ghost"
                onClick={() => handleAnswer(interaction.id, opt)}
                disabled={submittingOpt !== null}
              >
                {submittingOpt === opt ? 'Sending...' : opt}
              </button>
            ))}
          </div>
          {interactionError && <p className="error-text">{interactionError}</p>}
        </div>
      )}

      <details className="logs-accordion">
        <summary className="logs-summary">
          Detailed Logs ({logs.length})
          <span className="chevron">▼</span>
        </summary>
        <div className="log-panel">
          {logs.length === 0 ? <div className="text-secondary">No logs yet...</div> : null}
          {logs.map((l, i) => <div key={i} className="log-line"><span className="log-ts">[{new Date().toLocaleTimeString()}]</span> {l}</div>)}
        </div>
      </details>
    </div>
  );
}
