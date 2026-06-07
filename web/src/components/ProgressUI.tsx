import { useState } from 'react';
import type { StageProgress } from '@backend-types/index';
import './ProgressUI.css';

interface ProgressProps {
  stages: StageProgress;
  logs: string[];
  interaction: { id: string; question: string; options: string[] } | null;
  onAnswer: (id: string, answer: string) => Promise<void>;
  interactionError?: string | null;
}

export function ProgressUI({ stages, logs, interaction, onAnswer, interactionError }: ProgressProps) {
  const [submittingOption, setSubmittingOption] = useState<string | null>(null);
  const stageList: (keyof StageProgress)[] = ['explorer', 'mentor', 'contributor'];
  
  const handleAnswer = async (id: string, answer: string) => {
    setSubmittingOption(answer);
    await onAnswer(id, answer);
    setSubmittingOption(null);
  };

  return (
    <div>
      <div className="stage-indicator">
        {stageList.map(s => (
          <div key={s} className={`stage-card ${stages[s]}`}>
            <h3>{s.charAt(0).toUpperCase() + s.slice(1)}</h3>
            <p className="tag">{stages[s]}</p>
          </div>
        ))}
      </div>
      
      {interaction && (
        <div className="interaction-box">
          <p><strong>Question:</strong> {interaction.question}</p>
          {interactionError && <p style={{color: 'red'}}>{interactionError}</p>}
          <div className="interaction-options">
            {interaction.options.map(opt => (
              <button key={opt} disabled={!!submittingOption} className="btn-primary" onClick={() => handleAnswer(interaction.id, opt)}>
                {submittingOption === opt ? 'Sending...' : opt}
              </button>
            ))}
          </div>
        </div>
      )}

      {logs.length > 0 && (
        <details>
          <summary style={{cursor: 'pointer', margin: '1rem 0', fontFamily: 'var(--font-sans)'}}>
            Detailed Logs ({logs.length})
          </summary>
          <div className="log-panel">
            {logs.slice(-50).map((log, i) => <div key={`${i}-${log.substring(0, 10)}`}>{log}</div>)}
          </div>
        </details>
      )}
    </div>
  );
}
