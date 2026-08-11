import fs from "node:fs";
import path from "node:path";

if (process.env.RUN_AGENT_EVAL !== "1") {
  console.error("Agent evaluation is opt-in. Set RUN_AGENT_EVAL=1 after reviewing its model cost.");
  process.exit(2);
}

const root = process.cwd();
const benchmark = JSON.parse(fs.readFileSync(
  path.join(root, "eval", "agentic-benchmark.json"),
  "utf8",
));
const resultsPath = process.env.AGENT_EVAL_RESULTS
  ? path.resolve(process.env.AGENT_EVAL_RESULTS)
  : path.join(root, "eval", "results.json");
if (!fs.existsSync(resultsPath)) {
  console.error(`Missing evaluation results: ${resultsPath}`);
  process.exit(2);
}
if (benchmark.repositories.some((item) => !/^[a-f0-9]{40}$/i.test(item.commitSha ?? ""))) {
  console.error("Pin all eight commitSha values during the first baseline before scoring results.");
  process.exit(2);
}

const results = JSON.parse(fs.readFileSync(resultsPath, "utf8"));
const byMode = results.runs.reduce((grouped, run) => {
  (grouped[run.mode] ??= []).push(run);
  return grouped;
}, {});
const workflow = requireMode(byMode, "workflow", 8);
const mentor = requireMode(byMode, "mentor-multi-agent", 8);
requireMode(byMode, "bounded-agent", 8);
const gates = benchmark.gates;
const checks = [
  check("8/8 Mentor runs succeed with valid Schema", mentor.every((run) => run.success && run.schemaValid)),
  check(
    "Mean gold evidence recall improves by at least 10 percentage points",
    mean(mentor.map((run) => run.goldRecall)) - mean(workflow.map((run) => run.goldRecall))
      >= gates.minimumRecallImprovementPercentagePoints / 100,
  ),
  check(
    "Unsupported claims do not increase",
    sum(mentor.map((run) => run.unsupportedClaims)) <= sum(workflow.map((run) => run.unsupportedClaims)),
  ),
  check("Legal evidence citation rate remains 100%", mentor.every((run) => run.legalCitationRate === 1)),
  check("Every run stays within Harness budgets", results.runs.every((run) =>
    run.evidenceBatches <= gates.budgets.evidenceBatches
    && run.filesRead <= gates.budgets.files
    && run.evidenceBytes <= gates.budgets.bytes
    && run.actionsWithinSkillBudget === true
  )),
  check(
    "Median token/cost ratio is at most 1.5x Workflow",
    median(mentor.map((run) => run.tokenCost))
      <= median(workflow.map((run) => run.tokenCost)) * gates.maximumMedianTokenCostRatio,
  ),
  check(
    "P95 latency ratio is at most 1.5x Workflow",
    percentile(mentor.map((run) => run.latencyMs), 0.95)
      <= percentile(workflow.map((run) => run.latencyMs), 0.95) * gates.maximumP95LatencyRatio,
  ),
];

for (const item of checks) console.log(`${item.passed ? "PASS" : "FAIL"}  ${item.name}`);
process.exit(checks.every((item) => item.passed) ? 0 : 1);

function requireMode(grouped, mode, count) {
  const rows = grouped[mode] ?? [];
  if (rows.length !== count) throw new Error(`Expected ${count} ${mode} runs, received ${rows.length}`);
  return rows;
}

function check(name, passed) {
  return { name, passed };
}

function mean(values) {
  return sum(values) / values.length;
}

function sum(values) {
  return values.reduce((total, value) => total + value, 0);
}

function median(values) {
  return percentile(values, 0.5);
}

function percentile(values, quantile) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.ceil(sorted.length * quantile) - 1] ?? 0;
}
