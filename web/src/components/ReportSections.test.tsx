import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ExplorerOutput } from '@backend-types/index';
import { EvidenceAppendix, ExplorerSection } from './ReportSections';

const explorerOutput = {
  projectType: { primary: 'library', secondary: [] },
  techStack: { language: 'TypeScript', framework: null, buildTool: 'npm' },
  fileCount: 10,
  entryPoints: [{ file: 'src/index.ts', role: 'public entry' }],
  moduleMap: [],
  directorySummary: 'A small library',
  projectSummary: 'A TypeScript library.',
  evidenceClaims: [{
    claim: 'The package exports its API from src/index.ts.',
    confidence: 'high',
    evidence: [{
      path: 'src/index.ts',
      supports: 'Contains the public export statements.',
    }],
  }],
  evidenceCoverage: {
    examinedFiles: ['src/index.ts'],
    gaps: ['Optional adapters were not inspected.'],
  },
} satisfies ExplorerOutput;

describe('report evidence appendix', () => {
  it('keeps evidence details out of the overview section', () => {
    render(<ExplorerSection data={explorerOutput} />);

    expect(screen.queryByText('Harness verified')).not.toBeInTheDocument();
    expect(screen.queryByText('证据引用与分析边界')).not.toBeInTheDocument();
  });

  it('renders one concise report-level evidence appendix', () => {
    render(<EvidenceAppendix explorer={explorerOutput} />);

    expect(screen.getByText('证据引用与分析边界')).toBeInTheDocument();
    expect(screen.getByText('1 条分析结论 · 1 个已检查文件')).toBeInTheDocument();
    expect(screen.getByText('src/index.ts')).toBeInTheDocument();
    expect(screen.getByText('高置信度')).toBeInTheDocument();
    expect(screen.getByText('尚未覆盖的范围（1）')).toBeInTheDocument();
    expect(screen.queryByText('Harness verified')).not.toBeInTheDocument();
    expect(screen.queryByText(/形式化验证/)).not.toBeInTheDocument();
  });
});
