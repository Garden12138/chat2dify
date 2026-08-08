const MAPPING_KINDS = new Set(["model", "dataset", "tool", "trigger"]);
const EFFECTS = new Set([
  "model_cost",
  "http",
  "tool",
  "trigger",
  "notification",
  "human_escalation",
]);

export function isScenarioLabEnabled(config, search = "") {
  const params = new URLSearchParams(search);
  return Boolean(config?.studioV5Enabled) && params.get("studio") === "scenarios";
}

export function scenarioIdentity(search = "") {
  const params = new URLSearchParams(search);
  return {
    buildId: String(params.get("build_id") || "").slice(0, 128),
    candidateIds: String(params.get("candidate_ids") || params.get("candidate_id") || "")
      .split(",")
      .map(value => value.trim())
      .filter(Boolean)
      .slice(0, 20),
    repairProposalId: String(params.get("repair_proposal_id") || "").slice(0, 128),
    repairProposalVersion: Math.max(0, Number.parseInt(params.get("repair_proposal_version") || "0", 10) || 0),
    embedded: ["1", "true", "yes"].includes(
      String(params.get("embed") || "").toLowerCase(),
    ),
  };
}

export function scenarioLabQuery(projectId, buildId) {
  const params = new URLSearchParams({ project_id: projectId, build_id: buildId });
  return `/api/v5/studio/scenario-lab?${params.toString()}`;
}

export function inputSchemaQuery(projectId, buildId, candidateIds = []) {
  const params = new URLSearchParams({ project_id: projectId, build_id: buildId });
  for (const candidateId of candidateIds.slice(0, 20)) {
    params.append("candidate_ids", candidateId);
  }
  return `/api/v5/studio/scenario-lab/input-schema?${params.toString()}`;
}

export function scenarioSource(kind, {
  schemaHash = "",
  reference = "",
  evidenceHash = "",
} = {}) {
  if (kind === "generated") {
    return { kind, input_schema_hash: schemaHash };
  }
  if (kind === "fixture") {
    return { kind, fixture_id: reference };
  }
  if (kind === "approved_sanitized_run") {
    return { kind, source_run_id: reference, evidence_hash: evidenceHash };
  }
  return { kind: "manual" };
}

export function suitePayload({
  projectId,
  buildId,
  candidateIds,
  schemaHash,
  name,
  description,
  retentionDays,
  version,
  cases,
}) {
  return {
    project_id: projectId,
    build_id: buildId,
    candidate_ids: [...candidateIds].slice(0, 20),
    name: String(name || "").trim(),
    description: String(description || "").trim(),
    retention_days: finiteInteger(retentionDays, 30),
    semantic_version: String(version || "1.0.0").trim(),
    input_schema_hash: schemaHash,
    cases: cases.map(item => ({
      name: String(item.name || "").trim(),
      source: normalizeSourceRequest(item.source),
      inputs: item.inputs || {},
      files: Array.isArray(item.files) ? item.files : [],
      expected_output: item.expected_output,
      expected_behavior: String(item.expected_behavior || "").trim(),
      invariants: Array.isArray(item.invariants) ? item.invariants : [],
      rubric: Array.isArray(item.rubric) ? item.rubric : [],
      tags: Array.isArray(item.tags) ? item.tags.map(String).filter(Boolean) : [],
    })),
  };
}

export function scenarioRunPayload({
  projectId,
  buildId,
  suiteId,
  environmentId,
  candidateIds,
  mappings = [],
  allowedSideEffects = [],
  sideEffectsConfirmed = false,
  timeoutSeconds = 120,
  maxCases = 20,
  maxTotalTokens = 100000,
  maxEstimatedCostMicrousd = 5000000,
  tokenCostMicrousdPer1k = 5000,
}) {
  const safeMappings = mappings
    .filter(item => MAPPING_KINDS.has(item.kind))
    .map(item => ({
      kind: item.kind,
      logical_ref: String(item.logical_ref || "").trim(),
      target_ref: String(item.target_ref || "").trim(),
    }))
    .filter(item => item.logical_ref && item.target_ref);
  return {
    project_id: projectId,
    build_id: buildId,
    suite_id: suiteId,
    environment_id: environmentId,
    candidate_ids: [...candidateIds].slice(0, 20),
    mappings: safeMappings,
    policy: {
      timeout_seconds: finiteInteger(timeoutSeconds, 120),
      max_cases: finiteInteger(maxCases, 20),
      max_total_tokens: finiteInteger(maxTotalTokens, 100000),
      max_estimated_cost_microusd: finiteInteger(maxEstimatedCostMicrousd, 5000000),
      token_cost_microusd_per_1k: finiteInteger(tokenCostMicrousdPer1k, 5000),
      allowed_side_effects: [...new Set(allowedSideEffects.filter(item => EFFECTS.has(item)))],
      external_side_effects_confirmed: Boolean(sideEffectsConfirmed),
    },
  };
}

export function comparisonRows(run = {}) {
  const reports = Array.isArray(run.reports) ? run.reports : [];
  const regressions = run.comparison?.regressions || {};
  const missing = run.comparison?.missing_evidence || {};
  const gateFailures = run.comparison?.gate_failures || {};
  return reports.map(report => ({
    candidateId: report.candidate_id,
    label: report.candidate_label,
    passRate: Number(report.pass_rate || 0),
    quality: Number(report.quality_score || 0),
    latencyMs: nullableNumber(report.latency_ms),
    tokens: nullableNumber(report.total_tokens),
    costMicrousd: nullableNumber(report.estimated_cost_microusd),
    humanEscalations: Number(report.human_escalations || 0),
    sideEffects: Array.isArray(report.side_effects) ? report.side_effects : [],
    failures: Array.isArray(report.failure_clusters) ? report.failure_clusters : [],
    regressions: regressions[report.candidate_id] || [],
    missing: missing[report.candidate_id] || [],
    gateFailures: gateFailures[report.candidate_id] || [],
    cleanupVerified: report.cleanup_verified === true,
    bindingHash: String(report.binding?.binding_hash || ""),
  }));
}

export function runPresentation(run = {}) {
  const labels = {
    pending: "等待运行",
    running: "正在隔离 Preview 运行",
    completed: "证据完整且清理已验证",
    failed: "运行失败",
    cancelled: "已取消",
    interrupted: "服务重启，等待显式处理",
    reconciliation_required: "导入结果含糊，需要人工对账",
    cleanup_failed: "证据已生成，但清理尚未验证",
  };
  const status = String(run.status || "pending");
  return {
    status,
    label: labels[status] || status,
    tone: status === "completed" ? "ok"
      : ["pending", "running"].includes(status) ? "loading"
        : ["cleanup_failed", "reconciliation_required", "interrupted"].includes(status) ? "warning"
          : "danger",
    releaseEligible: status === "completed" && run.cleanup_verified === true,
    cleanupMessage: run.cleanup_verified === true
      ? "所有临时 Preview App 均已独立确认不存在。"
      : "临时 Preview App 尚未全部完成独立缺失验证。",
  };
}

export function gatePresentation(comparison = null) {
  const status = comparison?.gate_status || "unconfigured";
  return {
    status,
    label: {
      unconfigured: "尚未配置 Gate",
      passed: "Regression Gate 通过",
      failed: "Regression Gate 未通过",
      stale: "Gate 与当前 Suite / Policy 不再一致",
    }[status] || status,
  };
}

export function safeBuildReturnUrl(basePath, buildId) {
  const normalized = String(basePath || "").replace(/\/+$/, "");
  if (!buildId) return `${normalized}/?studio=build&intent=create&app_mode=workflow`;
  const params = new URLSearchParams({ studio: "build", build_id: buildId });
  return `${normalized}/?${params.toString()}`;
}

function finiteInteger(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.round(numeric) : fallback;
}

function nullableNumber(value) {
  return value === null || value === undefined ? null : Number(value);
}

function normalizeSourceRequest(source = {}) {
  const kind = String(source.kind || "manual");
  if (kind === "generated") {
    return { kind, input_schema_hash: String(source.input_schema_hash || "") };
  }
  if (kind === "fixture") {
    return { kind, fixture_id: String(source.fixture_id || "") };
  }
  if (kind === "approved_sanitized_run") {
    return {
      kind,
      source_run_id: String(source.source_run_id || ""),
      evidence_hash: String(source.evidence_hash || ""),
    };
  }
  return { kind: "manual" };
}
