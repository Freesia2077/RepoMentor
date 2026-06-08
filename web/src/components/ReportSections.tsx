import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { ExplorerOutput, MentorOutput, ContributorOutput } from '@backend-types/index';
import './ReportSections.css';

export function AnchorNav() {
  const [active, setActive] = useState('overview');

  return (
    <nav className="anchor-nav">
      <a href="#overview" className={active === 'overview' ? 'active' : ''} onClick={() => setActive('overview')}>Overview</a>
      <a href="#architecture" className={active === 'architecture' ? 'active' : ''} onClick={() => setActive('architecture')}>Architecture</a>
      <a href="#contribute" className={active === 'contribute' ? 'active' : ''} onClick={() => setActive('contribute')}>Contribute</a>
    </nav>
  );
}

export function ExplorerSection({ data }: { data?: ExplorerOutput }) {
  if (!data) return null;
  return (
    <section className="report-section" id="overview" style={{paddingTop: '0.5rem'}}>
      <div style={{display: 'flex', gap: '0.5rem', marginBottom: '1rem'}}>
        <span className="tag tag-tech">{data.projectType.primary}</span>
        <span className="tag tag-tech">{data.techStack.language}</span>
        {data.techStack.framework && <span className="tag tag-tech">{data.techStack.framework}</span>}
      </div>
      
      <p style={{fontSize: '1.15rem', marginBottom: '2.5rem'}}>{data.projectSummary}</p>

      <h3>Module Map</h3>
      <div className="grid-cards">
        {data.moduleMap.map((mod, i) => (
          <div key={i} className="card">
            <h4 style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
              {mod.path} 
              <span className="tag tag-status">{mod.importance}</span>
            </h4>
            <p className="text-secondary">{mod.responsibility}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export function MentorSection({ data }: { data?: MentorOutput }) {
  if (!data) return null;
  return (
    <section className="report-section" id="architecture">
      <h2>Architecture</h2>
      <div className="markdown-body">
        <ReactMarkdown>{data.architectureOverview}</ReactMarkdown>
      </div>
      <h3 style={{marginTop: '2rem'}}>Reading Path</h3>
      <ol className="reading-path">
        {data.readingPath.map((step, i) => (
          <li key={i}>
            <strong>{step.file}</strong>
            <p className="text-secondary">{step.why}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ContributorSection({ data }: { data?: ContributorOutput }) {
  if (!data) return null;
  return (
    <section className="report-section" id="contribute">
      <h2>Contribution Guide</h2>
      <div className="grid-cards" style={{marginBottom: '3rem'}}>
        <div className="card" style={{ display: 'flex', alignItems: 'center' }}>
          <span className="tag tag-default" style={{marginRight: '1.5rem'}}>Build</span>
          <code>{data.contributionSetup.build}</code>
        </div>
        <div className="card" style={{ display: 'flex', alignItems: 'center' }}>
          <span className="tag tag-default" style={{marginRight: '1.5rem'}}>Test</span>
          <code>{data.contributionSetup.test}</code>
        </div>
        {data.contributionSetup.lint && (
          <div className="card" style={{ display: 'flex', alignItems: 'center' }}>
            <span className="tag tag-default" style={{marginRight: '1.5rem'}}>Lint</span>
            <code>{data.contributionSetup.lint}</code>
          </div>
        )}
      </div>
      
      <h3>Good First Issues</h3>
      <div className="grid-cards">
        {data.goodFirstIssues.map((issue, i) => (
          <div key={i} className="card">
            <h4 style={{display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start'}}>
              {issue.area} 
              <span className={`tag tag-difficulty-${issue.difficulty}`}>{issue.difficulty}</span>
            </h4>
            <p className="text-secondary">{issue.description}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
