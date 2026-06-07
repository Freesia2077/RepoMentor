import ReactMarkdown from 'react-markdown';
import type { ExplorerOutput, MentorOutput, ContributorOutput } from '@backend-types/index';
import './ReportSections.css';

export function AnchorNav() {
  return (
    <nav className="anchor-nav">
      <a href="#overview">Overview</a>
      <a href="#architecture">Architecture</a>
      <a href="#contribute">Contribute</a>
    </nav>
  );
}

export function ExplorerSection({ data }: { data?: ExplorerOutput }) {
  if (!data) return null;
  return (
    <section className="report-section" id="overview">
      <h2>Overview</h2>
      <p style={{fontSize: '1.2rem'}}>{data.projectSummary}</p>
      
      <div style={{margin: '2rem 0'}}>
        <span className="badge">{data.projectType.primary}</span>
        <span className="badge">{data.techStack.language}</span>
        {data.techStack.framework && <span className="badge">{data.techStack.framework}</span>}
      </div>

      <h3>Module Map</h3>
      <div className="grid-cards">
        {data.moduleMap.map((mod, i) => (
          <div key={i} className="card">
            <h4>{mod.path} <span className="badge">{mod.importance}</span></h4>
            <p>{mod.responsibility}</p>
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
      <h2>Architecture & Design</h2>
      <div className="markdown-body">
        <ReactMarkdown>{data.architectureOverview}</ReactMarkdown>
      </div>
      <h3>Reading Path</h3>
      <ol>
        {data.readingPath.map((step, i) => (
          <li key={i}><strong>{step.file}</strong>: {step.why}</li>
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
      <div className="grid-cards" style={{marginBottom: '2rem'}}>
        <div className="card">
          <div className="badge">Build</div>
          <code>{data.contributionSetup.build}</code>
        </div>
        <div className="card">
          <div className="badge">Test</div>
          <code>{data.contributionSetup.test}</code>
        </div>
        {data.contributionSetup.lint && (
          <div className="card">
            <div className="badge">Lint</div>
            <code>{data.contributionSetup.lint}</code>
          </div>
        )}
      </div>
      
      <h3>Good First Issues</h3>
      <div className="grid-cards">
        {data.goodFirstIssues.map((issue, i) => (
          <div key={i} className="card">
            <h4>{issue.area} <span className="badge">{issue.difficulty}</span></h4>
            <p>{issue.description}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
