import { useState } from 'react';
import './InputView.css';

interface Props {
  onSubmit: (url: string, branch: string) => void;
  error?: string | null;
}

export function InputView({ onSubmit, error }: Props) {
  const [url, setUrl] = useState('');
  const [branch, setBranch] = useState('main');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (url.trim()) onSubmit(url.trim(), branch.trim() || 'main');
  };

  return (
    <div className="input-view">
      <div className="hero-section">
        <h1 className="hero-title">RepoMentor</h1>
        <p className="subtitle hero-subtitle">Deep structural analysis for any codebase.</p>
      </div>

      <form className="input-form" onSubmit={handleSubmit}>
        <div className="input-group">
          <input className="input-url" type="text" placeholder="https://github.com/owner/repo" value={url} onChange={(e) => setUrl(e.target.value)} />
          <div className="branch-container">
            <span className="branch-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="6" y1="3" x2="6" y2="15"></line><circle cx="18" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M18 9a9 9 0 0 1-9 9"></path></svg>
            </span>
            <input className="input-branch" type="text" placeholder="main" value={branch} onChange={(e) => setBranch(e.target.value)} />
          </div>
        </div>
        <button className="btn-primary" type="submit">Analyze</button>
      </form>
      
      {error && <p className="error-text">{error}</p>}

      <div className="example-repos">
        <p className="text-secondary" style={{fontSize: '0.9rem', marginBottom: '0.8rem'}}>Try an example repository:</p>
        <div className="example-links">
          <button type="button" className="example-btn" onClick={() => setUrl('anthropic/anthropic-sdk-python')}>anthropic/anthropic-sdk-python</button>
          <button type="button" className="example-btn" onClick={() => setUrl('facebook/react')}>facebook/react</button>
        </div>
      </div>
    </div>
  );
}
