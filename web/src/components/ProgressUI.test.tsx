import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ProgressUI } from './ProgressUI';

describe('ProgressUI', () => {
  it('renders stages and interaction', async () => {
    const handleAnswer = vi.fn();
    render(
      <ProgressUI 
        stages={{ explorer: 'done', mentor: 'running', contributor: 'pending' }}
        logs={[
          { message: 'Log 1', timestamp: '2026-07-30T12:00:00.000Z' },
          { message: 'Log 2', timestamp: '2026-07-30T12:00:01.000Z' }
        ]}
        interaction={{ id: 'q1', question: 'How to proceed?', options: ['Yes', 'No'] }}
        onAnswer={handleAnswer}
      />
    );
    expect(screen.getByText('Explorer')).toBeInTheDocument();
    expect(screen.getByText('How to proceed?')).toBeInTheDocument();
    
    const yesBtn = screen.getByText('Yes');
    await act(async () => {
      fireEvent.click(yesBtn);
    });
    expect(handleAnswer).toHaveBeenCalledWith('q1', 'Yes');
  });

  it('renders the timestamp captured when each log event occurred', () => {
    const timestamp = '2026-07-30T12:34:56.000Z';
    render(
      <ProgressUI
        stages={{ explorer: 'running', mentor: 'pending', contributor: 'pending' }}
        logs={[{ message: 'Reading repository', timestamp }]}
        onAnswer={vi.fn()}
      />
    );

    expect(screen.getByText(`[${new Date(timestamp).toLocaleTimeString()}]`)).toBeInTheDocument();
    expect(screen.getAllByText('Reading repository')).toHaveLength(2);
  });

  it('shows loading state correctly when an option is clicked', async () => {
    let resolvePromise: (value?: unknown) => void;
    const promise = new Promise(resolve => {
      resolvePromise = resolve;
    });
    const handleAnswer = vi.fn().mockReturnValue(promise);

    render(
      <ProgressUI 
        stages={{ explorer: 'done', mentor: 'running', contributor: 'pending' }}
        logs={[]}
        interaction={{ id: 'q1', question: 'How to proceed?', options: ['Yes', 'No'] }}
        onAnswer={handleAnswer}
      />
    );
    
    const yesBtn = screen.getByText('Yes');
    
    await act(async () => {
      fireEvent.click(yesBtn);
    });

    // The clicked option should change to "Sending..."
    const sendingBtn = screen.getByText('Sending...');
    expect(sendingBtn).toBeDisabled();
    
    // The other option should be disabled but retain its original text
    const noBtn = screen.getByText('No');
    expect(noBtn).toBeDisabled();
    
    await act(async () => {
      resolvePromise!(undefined);
    });
    
    // Both options should revert back to original state
    expect(screen.getByText('Yes')).not.toBeDisabled();
    expect(screen.getByText('No')).not.toBeDisabled();
  });
});
