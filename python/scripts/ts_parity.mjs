import fs from 'node:fs/promises';
import { buildEvidenceBundle } from '../../src/lib/repository-profile.ts';
import { RepositoryHarness } from '../../src/services/repository-harness.ts';
import { loadHarnessSkill } from '../../src/services/harness-skills.ts';

const [root, inputPath] = process.argv.slice(2);
const input = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const h = new RepositoryHarness(root);
await h.getRepositoryMap();
h.activateSkillPolicy('explorer', loadHarnessSkill([], 'explorer').policy);
const errors = {};
const action = {tool: 'search_symbols', query: 'logMiddleware', purpose: 'locate'};
await h.executeDiscoveryTool('explorer', action);
try { await h.executeDiscoveryTool('explorer', action); } catch { errors.duplicate_action = true; }
try {
  await h.executeDiscoveryTool('explorer', {tool: 'find_related_tests', paths: ['../secret'], purpose: 'reject'});
} catch { errors.not_tracked = true; }
await h.executeEvidencePlan('explorer', input.plan);
h.completeStage('explorer');
h.activateSkillPolicy('mentor', loadHarnessSkill([], 'mentor').policy);
await h.executeEvidencePlan('mentor', input.mentor, h.getEvidenceBundle().files.map(f => f.path));
const state = h.getContextView();
const projection = {
  budget: state.budget,
  stages: Object.fromEntries(Object.entries(state.stages).map(([k,v]) => [k, {
    status: v.status, examinedPaths: v.examinedPaths, skippedPaths: v.skippedPaths,
    discoveryActionsUsed: v.discoveryActionsUsed, stopReason: v.stopReason,
    selected: v.plan?.files.map(f => f.path.replaceAll('\\', '/').replace(/^\.\/+/, '')) ?? []
  }])),
  ledger: Object.fromEntries(h.getEvidenceBundle().files.map(f => [f.path, f.truncated ? 'partial' : 'available'])),
  errors
};
const bundle = await buildEvidenceBundle(root, input.rawPlan, 'explorer');
projection.rawBundle = {
  files: bundle.files.map(f => ({path: f.path, bytes: Buffer.byteLength(f.content), truncated: f.truncated})),
  omissions: bundle.omissions, totalBytes: bundle.totalBytes
};
process.stdout.write(JSON.stringify(projection));
