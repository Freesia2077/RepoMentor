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
            <h4>{mod.path}</h4>
            <p>{mod.responsibility || (mod as any).description}</p>
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
        <ReactMarkdown>{data.architectureOverview || (data as any).architecturePlan}</ReactMarkdown>
      </div>
    </section>
  );
}

export function ContributorSection({ data }: { data?: ContributorOutput }) {
  if (!data) return null;
  return (
    <section className="report-section" id="contribute">
      <h2>Contribution Guide</h2>
      <div className="grid-cards" style={{marginBottom: '2rem'}}>
        {data.contributionSetup && Object.entries(data.contributionSetup).map(([key, step], i) => step && (
          <div key={i} className="card">
            <div className="badge">{key}</div>
            <code style={{display: 'block', marginTop: '1rem'}}>{String(step)}</code>
          </div>
        ))}
        {!(data as any).contributionSetup && (data as any).setupSteps?.map((step: string, i: number) => (
          <div key={i} className="card">
            <div className="badge">Step {i+1}</div>
            <code style={{display: 'block', marginTop: '1rem'}}>{step}</code>
          </div>
        ))}
      </div>
      
      <h3>Implementation Plan</h3>
      <div className="markdown-body">
        <ReactMarkdown>{(data as any).implementationPlan || data.notesForNewcomers?.map(n => n.tip).join('\n') || ''}</ReactMarkdown>
      </div>
    </section>
  );
}
