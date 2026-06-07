import { useState, useRef, useEffect } from 'react';
import { useAnalysisStream } from '../hooks/useAnalysisStream';
import ReactMarkdown from 'react-markdown';
import './AnalysisView.css';

interface Props {
  taskId: string;
  onAnswer: (questionId: string, answer: string) => Promise<void>;
}

export function AnalysisView({ taskId, onAnswer }: Props) {
  const state = useAnalysisStream(taskId);
  const [answerText, setAnswerText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [state.logs]);

  const handleAnswerSubmit = async () => {
    if (!state.question || !answerText.trim()) return;
    setSubmitting(true);
    try {
      await onAnswer(state.question.id, answerText);
      setAnswerText('');
    } catch (e) {
      console.error(e);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="analysis-view">
      <div className="status-badge">Status: {state.status}</div>

      <div className="log-container">
        {state.logs.map((log, i) => (
          <div key={i} className="log-entry">{log}</div>
        ))}
        <div ref={logEndRef} />
      </div>

      {state.status === 'error' && (
        <div className="error-text">Fatal Error: {state.error}</div>
      )}

      {state.status === 'interaction_required' && state.question && (
        <div className="interaction-box">
          <h3>Mentor Question</h3>
          <p>{state.question.text}</p>
          <textarea 
            value={answerText}
            onChange={e => setAnswerText(e.target.value)}
            placeholder="Type your clarification here..."
            disabled={submitting}
          />
          <button 
            className="btn-primary" 
            onClick={handleAnswerSubmit}
            disabled={submitting || !answerText.trim()}
          >
            {submitting ? 'Submitting...' : 'Submit Answer'}
          </button>
        </div>
      )}

      {state.status === 'completed' && state.result && (
        <div className="result-box">
          <h2>Analysis Complete</h2>
          <ReactMarkdown>{state.result.implementationPlan}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}
