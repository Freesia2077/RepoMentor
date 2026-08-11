import { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import type {
  ExplorerOutput,
  MentorOutput,
  ContributorOutput,
  EvidenceGap,
} from '@backend-types/index';
import './ReportSections.css';

export function AnchorNav() {
  const [active, setActive] = useState('overview');

  useEffect(() => {
    const sections = ['overview', 'architecture', 'contribute'];
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setActive(entry.target.id);
          }
        });
      },
      { rootMargin: '-10% 0px -85% 0px' }
    );

    sections.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });

    return () => observer.disconnect();
  }, []);

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
              <span className={`tag tag-importance tag-importance-${mod.importance}`}>
                {mod.importance}
              </span>
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

interface EvidenceAppendixProps {
  explorer?: ExplorerOutput;
  mentor?: MentorOutput;
  contributor?: ContributorOutput;
}

const confidenceLabels = {
  high: '高置信度',
  medium: '中置信度',
  low: '低置信度',
} as const;

function normalizeReportGap(value: EvidenceGap | string): EvidenceGap {
  if (typeof value !== 'string') return value;
  return {
    kind: 'missing_evidence',
    subject: value.split(':', 1)[0]?.trim() || value,
    summary: value,
    severity: 'medium',
  };
}

export function EvidenceAppendix({ explorer, mentor, contributor }: EvidenceAppendixProps) {
  const sections = [
    { key: 'explorer', label: '项目概览', data: explorer },
    { key: 'mentor', label: '架构分析', data: mentor },
    { key: 'contributor', label: '贡献指南', data: contributor },
  ].filter((section) => section.data
    && (section.data.evidenceClaims.length > 0 || section.data.evidenceCoverage.gaps.length > 0));

  if (sections.length === 0) return null;

  const examinedFiles = new Set(
    sections.flatMap(({ data }) => data?.evidenceCoverage.examinedFiles ?? []),
  );
  const claimCount = sections.reduce(
    (total, { data }) => total + (data?.evidenceClaims.length ?? 0),
    0,
  );
  const gaps = [...sections
    .flatMap(({ data }) => (data?.evidenceCoverage.gaps ?? []) as Array<EvidenceGap | string>)
    .map(normalizeReportGap)
    .filter((gap) => gap.kind !== 'out_of_scope' && gap.severity !== 'low')
    .reduce((deduplicated, gap) => {
      const key = gap.subject.trim().toLowerCase();
      if (!deduplicated.has(key)) deduplicated.set(key, gap);
      return deduplicated;
    }, new Map<string, EvidenceGap>())
    .values()].slice(0, 8);

  return (
    <details className="evidence-appendix" id="evidence">
      <summary className="evidence-appendix-summary">
        <div>
          <span className="evidence-eyebrow">分析依据</span>
          <strong>证据引用与分析边界</strong>
          <small>{claimCount} 条分析结论 · {examinedFiles.size} 个已检查文件</small>
        </div>
        <span className="evidence-appendix-chevron" aria-hidden="true">▼</span>
      </summary>

      <div className="evidence-appendix-content">
        {sections
          .filter(({ data }) => data && data.evidenceClaims.length > 0)
          .map(({ key, label, data }) => data && (
          <section className="evidence-stage" key={key}>
            <h3>{label}</h3>
            <div className="evidence-claims">
              {data.evidenceClaims.map((claim, index) => (
                <article className="evidence-claim" key={`${claim.claim}-${index}`}>
                  <div className="evidence-claim-title">
                    <strong>{claim.claim}</strong>
                    <span className={`evidence-confidence confidence-${claim.confidence}`}>
                      {confidenceLabels[claim.confidence]}
                    </span>
                  </div>
                  {claim.evidence.length > 0 ? (
                    <ul>
                      {claim.evidence.map((reference) => (
                        <li key={`${reference.path}-${reference.supports}`}>
                          <code>{reference.path}</code>
                          <span>{reference.supports}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="evidence-unsupported">该结论未附带文件引用，属于报告级解释。</p>
                  )}
                </article>
              ))}
            </div>
          </section>
        ))}

        {gaps.length > 0 && (
          <section className="coverage-gaps">
            <h3>尚未覆盖的范围（{gaps.length}）</h3>
            <ul>{gaps.map((gap) => (
              <li key={`${gap.kind}:${gap.subject}`}>{gap.summary}</li>
            ))}</ul>
          </section>
        )}
      </div>
    </details>
  );
}
