import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AnalysisView } from './AnalysisView';

// Mock the hook
vi.mock('../hooks/useAnalysisStream', () => ({
  useAnalysisStream: vi.fn()
}));

import { useAnalysisStream } from '../hooks/useAnalysisStream';

describe('AnalysisView', () => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();

  it('renders cloning status and logs', () => {
    // @ts-ignore
    useAnalysisStream.mockReturnValue({
      status: 'cloning',
      logs: ['Cloning repo...']
    });

    render(<AnalysisView taskId="task-123" onAnswer={vi.fn()} />);
    
    expect(screen.getByText('Status: cloning')).toBeInTheDocument();
    expect(screen.getByText('Cloning repo...')).toBeInTheDocument();
  });

  it('shows error state', () => {
    // @ts-ignore
    useAnalysisStream.mockReturnValue({
      status: 'error',
      logs: [],
      error: 'Failed to clone'
    });

    render(<AnalysisView taskId="task-123" onAnswer={vi.fn()} />);
    expect(screen.getByText('Fatal Error: Failed to clone')).toBeInTheDocument();
  });

  it('handles interaction required', async () => {
    // @ts-ignore
    useAnalysisStream.mockReturnValue({
      status: 'interaction_required',
      logs: [],
      question: { id: 'q1', text: 'What do you mean?' }
    });

    const submitSpy = vi.fn();
    render(<AnalysisView taskId="task-123" onAnswer={submitSpy} />);
    
    expect(screen.getByText('Mentor Question')).toBeInTheDocument();
    expect(screen.getByText('What do you mean?')).toBeInTheDocument();

    const textarea = screen.getByPlaceholderText('Type your clarification here...');
    fireEvent.change(textarea, { target: { value: 'I mean this.' } });

    const btn = screen.getByRole('button', { name: 'Submit Answer' });
    fireEvent.click(btn);

    expect(submitSpy).toHaveBeenCalledWith('q1', 'I mean this.');
  });
});
