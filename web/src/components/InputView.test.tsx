import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { InputView } from './InputView';

describe('InputView', () => {
  it('calls onSubmit and shows inline error if passed', () => {
    const submitSpy = vi.fn();
    render(<InputView onSubmit={submitSpy} error="Failed to connect" />);
    
    expect(screen.getByText('Failed to connect')).toBeInTheDocument();
    
    const urlInput = screen.getByPlaceholderText(/https:\/\/github.com/);
    fireEvent.change(urlInput, { target: { value: 'test/repo' } });
    
    const btn = screen.getByRole('button', { name: /Analyze/i });
    fireEvent.click(btn);
    
    expect(submitSpy).toHaveBeenCalledWith('test/repo', 'main');
  });
});
