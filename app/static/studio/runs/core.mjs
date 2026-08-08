const STATUS = Object.freeze({
  running: { label: "运行中", tone: "loading" },
  succeeded: { label: "成功", tone: "ok" },
  failed: { label: "失败", tone: "danger" },
  stopped: { label: "已停止", tone: "warning" },
  partial_succeeded: { label: "部分成功", tone: "warning" },
  unknown: { label: "证据不足", tone: "muted" },
});

const CORRELATION = Object.freeze({
  exact: { label: "精确关联 Artifact", tone: "ok" },
  uncorrelated: { label: "未关联发布版本", tone: "warning" },
  ambiguous: { label: "关联含糊，需对账", tone: "danger" },
  unsupported: { label: "Dify 未提供版本证据", tone: "muted" },
});

const ERRORS = Object.freeze({
  EXECUTION_VARIABLE_REFERENCE_INVALID: "变量引用失效",
  EXECUTION_HTTP_FAILED: "外部请求失败",
  EXECUTION_TOOL_FAILED: "工具调用失败",
  EXECUTION_MODEL_FAILED: "模型调用失败",
  EXECUTION_TIMEOUT: "执行超时",
  EXECUTION_CANCELLED: "执行已停止",
  EXECUTION_ERROR_UNKNOWN: "执行失败（证据不足）",
});

export function isRunCenterEnabled(config, search = "") {
  return Boolean(config?.studioV5Enabled)
    && new URLSearchParams(search).get("studio") === "runs";
}

export function runIdentity(search = "") {
  const params = new URLSearchParams(search);
  return {
    incidentId: bounded(params.get("incident_id")),
    embedded: ["1", "true", "yes"].includes(String(params.get("embed") || "").toLowerCase()),
  };
}

export function runCenterTone(state = "ready") {
  return {
    ready: "ok",
    empty: "warning",
    partial_error: "warning",
    permission_denied: "danger",
    offline: "danger",
  }[state] || "danger";
}

export function runStatusPresentation(status = "unknown") {
  return STATUS[status] || { label: String(status), tone: "muted" };
}

export function correlationPresentation(state = "unsupported") {
  return CORRELATION[state] || { label: String(state), tone: "muted" };
}

export function errorLabel(code = "") {
  return ERRORS[code] || code || "无稳定错误分类";
}

export function runCenterQuery(projectId, filters = {}) {
  const params = new URLSearchParams({ project_id: projectId });
  for (const [key, value] of Object.entries({
    logical_app_id: filters.logicalAppId,
    environment_id: filters.environmentId,
    artifact_id: filters.artifactId,
    status: filters.status,
    error_code: filters.errorCode,
    started_from: isoDate(filters.startedFrom),
    started_to: isoDate(filters.startedTo),
  })) {
    if (String(value || "").trim()) params.set(key, String(value).trim());
  }
  return `/api/v5/studio/run-center?${params.toString()}`;
}

export function runMetrics(executions = [], incidents = []) {
  const finished = executions.filter(item => item.status !== "running");
  const succeeded = finished.filter(item => item.status === "succeeded").length;
  const latency = executions.map(item => Number(item.latency_ms)).filter(Number.isFinite);
  const cost = executions.map(item => Number(item.estimated_cost_microusd)).filter(Number.isFinite);
  const exact = executions.filter(item => item.correlation_state === "exact").length;
  return [
    {
      label: "成功率",
      value: finished.length ? `${Math.round((succeeded / finished.length) * 100)}%` : "证据不足",
      detail: `${succeeded}/${finished.length} 次已完成执行`,
    },
    {
      label: "平均时延",
      value: latency.length ? formatDuration(Math.round(latency.reduce((a, b) => a + b, 0) / latency.length)) : "证据不足",
      detail: `${latency.length} 次包含时延证据`,
    },
    {
      label: "估算成本",
      value: cost.length ? formatCost(cost.reduce((a, b) => a + b, 0)) : "证据不足",
      detail: "固定费率估算，不冒充账单",
    },
    {
      label: "Artifact 关联",
      value: executions.length ? `${exact}/${executions.length}` : "尚无执行",
      detail: "仅接受执行版本与 Publish Receipt 精确匹配",
    },
    {
      label: "开放事件",
      value: String(incidents.filter(item => item.status !== "resolved").length),
      detail: "按稳定错误与节点聚类",
    },
  ];
}

export function trendRows(points = []) {
  const max = Math.max(1, ...points.map(item => item.succeeded + item.failed + item.other));
  return points.map(item => ({
    ...item,
    label: `${item.bucket}：成功 ${item.succeeded}，失败 ${item.failed}，其他 ${item.other}`,
    successWidth: Math.round((item.succeeded / max) * 100),
    failedWidth: Math.round((item.failed / max) * 100),
    otherWidth: Math.round((item.other / max) * 100),
  }));
}

export function repairBuildUrl(repair = {}) {
  const buildId = bounded(repair.build_id);
  if (!buildId) return "?studio=runs";
  const params = new URLSearchParams({
    studio: "build",
    intent: "modify",
    build_id: buildId,
    repair_proposal_id: bounded(repair.id),
    repair_proposal_version: String(Number.isInteger(repair.version) ? repair.version : 1),
  });
  return `?${params.toString()}`;
}

export function repairStatusLabel(status = "draft_build") {
  return {
    draft_build: "等待 Build 修改",
    candidate_ready: "等待 Scenario",
    scenario_ready: "等待 Review",
    in_review: "评审中",
    released: "已完成显式发布",
    closed: "已关闭",
  }[status] || status;
}

export function automationPresentation(view = {}) {
  const state = {
    configured: { label: "通知 Adapter 已配置", tone: "ok" },
    missing: { label: "Adapter 缺失，Outbox 待对账", tone: "warning" },
    disabled: { label: "告警未启用", tone: "muted" },
  }[view.adapter_state] || { label: "自动化状态未知", tone: "warning" };
  return {
    ...state,
    summary: `${state.label} · ${Number(view.pending_notifications || 0)} 条待发送 · ${Number(view.dead_letters || 0)} 条需对账`,
  };
}

export function scopedTokenPresentation(record = {}, now = Date.now()) {
  const revoked = Boolean(record.revoked_at);
  const expired = Number.isFinite(new Date(record.expires_at).getTime())
    && new Date(record.expires_at).getTime() <= now;
  return {
    status: revoked ? "revoked" : expired ? "expired" : "active",
    label: revoked ? "已撤销" : expired ? "已过期" : "有效",
    tone: revoked || expired ? "warning" : "ok",
    scopeSummary: (record.scopes || []).join(" · ") || "没有 Scope",
  };
}

export function formatDuration(milliseconds) {
  const value = Number(milliseconds);
  if (!Number.isFinite(value)) return "证据不足";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.max(0, Math.round(value))} ms`;
}

export function formatCost(microusd) {
  const value = Number(microusd);
  if (!Number.isFinite(value)) return "证据不足";
  return `$${(value / 1_000_000).toFixed(4)}`;
}

export function shortHash(value) {
  const raw = String(value || "");
  return raw ? raw.slice(0, 12) : "无精确 Artifact";
}

function bounded(value) {
  return String(value || "").trim().slice(0, 128);
}

function isoDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : "";
}
