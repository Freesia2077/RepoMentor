import type { HarnessTraceEntry } from '@backend-types/index';
import './AnalysisTrace.css';

interface Props {
  traces: HarnessTraceEntry[];
  defaultOpen?: boolean;
}

const kindLabels: Record<HarnessTraceEntry['kind'], string> = {
  plan: '计划',
  tool: '工具',
  evidence: '证据',
  decision: '决策',
};

const stageLabels: Record<HarnessTraceEntry['stage'], string> = {
  repository: '仓库',
  explorer: 'Explorer',
  mentor: 'Mentor',
  contributor: 'Contributor',
};

export function AnalysisTrace({ traces, defaultOpen = true }: Props) {
  return (
    <details className="analysis-trace" open={defaultOpen}>
      <summary className="analysis-trace-summary">
        <span>
          分析轨迹
          <small>{traces.length} 个可核验步骤</small>
        </span>
        <span className="chevron">▼</span>
      </summary>

      <div className="analysis-trace-list">
        {traces.length === 0 ? (
          <p className="analysis-trace-empty">首个仓库分析动作会显示在这里。</p>
        ) : traces.map((trace, index) => (
          <article className={`analysis-trace-entry kind-${trace.kind}`} key={`${trace.timestamp ?? 'trace'}-${index}`}>
            <div className="analysis-trace-marker" aria-hidden="true" />
            <div className="analysis-trace-content">
              <div className="analysis-trace-heading">
                <span className="analysis-trace-kind">{kindLabels[trace.kind]}</span>
                <span className="analysis-trace-stage">{stageLabels[trace.stage]}</span>
                {trace.timestamp && (
                  <time>{new Date(trace.timestamp).toLocaleTimeString()}</time>
                )}
              </div>
              <h4>{trace.title}</h4>
              <p>{trace.summary}</p>
              {trace.tool && <code className="analysis-trace-tool">{trace.tool}</code>}
              {trace.files && trace.files.length > 0 && (
                <div className="analysis-trace-files">
                  {trace.files.slice(0, 8).map((file) => <code key={file}>{file}</code>)}
                  {trace.files.length > 8 && <span>另有 {trace.files.length - 8} 个</span>}
                </div>
              )}
            </div>
          </article>
        ))}
      </div>
    </details>
  );
}
