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
      <h1>RepoMentor</h1>
      <p className="tagline">Deep structural analysis for any codebase.</p>
      <form className="input-form" onSubmit={handleSubmit}>
        <input className="input-url" type="text" placeholder="https://github.com/owner/repo" value={url} onChange={(e) => setUrl(e.target.value)} />
        <input className="input-branch" type="text" placeholder="branch (main)" value={branch} onChange={(e) => setBranch(e.target.value)} />
        <button className="btn-primary" type="submit">Analyze</button>
      </form>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
