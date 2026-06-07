import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ProgressUI } from './ProgressUI';

describe('ProgressUI', () => {
  it('renders stages and interaction', async () => {
    const handleAnswer = vi.fn();
    render(
      <ProgressUI 
        stages={{ explorer: 'done', mentor: 'running', contributor: 'pending' }}
        logs={['Log 1', 'Log 2']}
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
});
