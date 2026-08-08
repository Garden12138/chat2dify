import { classifyStudioError } from "../home/core.mjs";
import {
  automationPresentation,
  correlationPresentation,
  errorLabel,
  formatCost,
  formatDuration,
  isRunCenterEnabled,
  repairBuildUrl,
  repairStatusLabel,
  runCenterQuery,
  runCenterTone,
  runIdentity,
  runMetrics,
  runStatusPresentation,
  scopedTokenPresentation,
  shortHash,
  trendRows,
} from "./core.mjs";

const config = window.CHAT2DIFY_CONFIG || {};
const basePath = String(config.basePath || "").replace(/\/+$/, "");
const identity = runIdentity(window.location.search);
const state = {
  token: "",
  projectId: "",
  center: null,
  incident: null,
  automation: null,
  tokens: [],
};

if (isRunCenterEnabled(config, window.location.search)) {
  document.addEventListener("DOMContentLoaded", () => void boot());
}

async function boot() {
  document.body.classList.add("studio-v5", "studio-v5-runs");
  if (identity.embedded) document.body.classList.add("studio-embedded");
  document.querySelector("#legacy-app-frame")?.setAttribute("hidden", "");
  document.querySelector("#studio-root").hidden = false;
  ["#studio-content", "#studio-build-content", "#studio-blueprint-content", "#studio-scenario-content", "#studio-release-content"].forEach(selector => {
    document.querySelector(selector).hidden = true;
  });
  document.querySelector("#studio-run-content").hidden = false;
  document.querySelector("#studio-state").hidden = true;
  document.querySelectorAll(".studio-nav-item").forEach(item => {
    item.classList.remove("studio-nav-active");
    item.removeAttribute("aria-current");
  });
  const nav = document.querySelector("#studio-runs-nav");
  nav?.classList.add("studio-nav-active");
  nav?.setAttribute("aria-current", "page");
  bindActions();
  await connect();
}

function bindActions() {
  document.querySelector("#studio-run-filter-form").addEventListener("submit", event => {
    event.preventDefault();
    void loadCenter(true);
  });
  document.querySelector("#studio-run-clear-filters").addEventListener("click", () => {
    ["#studio-run-logical-app", "#studio-run-environment", "#studio-run-artifact", "#studio-run-status", "#studio-run-error", "#studio-run-started-from", "#studio-run-started-to"].forEach(selector => {
      document.querySelector(selector).value = "";
    });
    void loadCenter(true);
  });
  document.querySelector("#studio-run-refresh-evidence").addEventListener("click", () => void refreshEvidence());
  document.querySelector("#studio-run-create-repair").addEventListener("click", () => void createRepair());
  document.querySelector("#studio-run-alert-form").addEventListener("submit", event => {
    event.preventDefault();
    void saveAlert();
  });
  document.querySelector("#studio-run-schedule-form").addEventListener("submit", event => {
    event.preventDefault();
    void saveSchedule();
  });
  document.querySelector("#studio-run-tick-automation").addEventListener("click", () => void tickAutomation());
  document.querySelector("#studio-run-token-form").addEventListener("submit", event => {
    event.preventDefault();
    void issueToken();
  });
  document.querySelector("#studio-run-token-copy").addEventListener("click", () => void copyIssuedToken());
}

async function connect() {
  setNotice("正在验证 Dify 会话与项目权限。", "loading");
  try {
    const session = await requestJson("/api/v5/studio/session", {
      method: "POST",
      body: { nonce: createNonce() },
      authenticated: false,
    });
    state.token = session.token;
    state.projectId = session.project.id;
    document.querySelector("#studio-project-badge").textContent = session.project.name;
    document.querySelector("#studio-run-context").textContent = `${session.project.name} · ${session.membership.role}`;
    setConnection("Dify 已验证", "ok");
    await loadCenter(false);
    await loadAutomation();
    await loadTokens();
  } catch (error) {
    setConnection("连接失败", "danger");
    setNotice(error.presentation?.message || "无法打开 Run Center。", "danger");
  }
}

function filters() {
  return {
    logicalAppId: value("#studio-run-logical-app"),
    environmentId: value("#studio-run-environment"),
    artifactId: value("#studio-run-artifact"),
    status: value("#studio-run-status"),
    errorCode: value("#studio-run-error"),
    startedFrom: value("#studio-run-started-from"),
    startedTo: value("#studio-run-started-to"),
  };
}

async function loadCenter(announce) {
  if (announce) setNotice("正在刷新已保存的脱敏执行证据。", "loading");
  try {
    state.center = await requestJson(runCenterQuery(state.projectId, filters()));
    renderCenter();
    const requested = identity.incidentId || state.incident?.incident?.id;
    const target = state.center.incidents.find(item => item.id === requested);
    if (target) await selectIncident(target.id);
    else clearIncident();
    setNotice(state.center.message, runCenterTone(state.center.state));
  } catch (error) {
    setNotice(error.presentation?.message || "Run Center 刷新失败。", "danger");
  }
}

function renderCenter() {
  renderFilters();
  renderMetrics();
  renderTrend();
  renderAnalysis();
  renderExecutions();
  renderIncidents();
  const refresh = document.querySelector("#studio-run-refresh-evidence");
  refresh.disabled = !state.center.can_refresh;
  refresh.title = state.center.can_refresh ? "读取当前 Dify 生产执行" : "当前角色或 Dify 会话不能刷新运行证据";
  document.querySelector("#studio-run-generated-at").textContent = `证据生成于 ${formatTime(state.center.generated_at)}`;
}

function renderFilters() {
  refillSelect("#studio-run-logical-app", "全部应用", state.center.logical_apps, item => item.name);
  refillSelect("#studio-run-environment", "全部环境", state.center.environments, item => item.name);
  const artifacts = [...new Set((state.center.executions || []).map(item => item.artifact_id).filter(Boolean))];
  refillSelect("#studio-run-artifact", "全部发布版本", artifacts.map(id => ({ id })), item => `Artifact ${shortHash(item.id)}`);
  const codes = [...new Set((state.center.executions || []).map(item => item.stable_error_code).filter(Boolean))];
  refillSelect("#studio-run-error", "全部分类", codes.map(code => ({ id: code })), item => errorLabel(item.id));
}

function refillSelect(selector, firstLabel, items, label) {
  const select = document.querySelector(selector);
  const current = select.value;
  select.replaceChildren(option("", firstLabel));
  for (const item of items) select.append(option(item.id, label(item)));
  if ([...select.options].some(item => item.value === current)) select.value = current;
}

function renderMetrics() {
  const root = document.querySelector("#studio-run-metrics");
  root.replaceChildren();
  for (const metric of runMetrics(state.center.executions, state.center.incidents)) {
    const card = element("article", "studio-run-metric");
    card.append(element("span", "", metric.label), element("strong", "", metric.value), element("small", "", metric.detail));
    root.append(card);
  }
}

function renderTrend() {
  const root = document.querySelector("#studio-run-trend");
  root.replaceChildren();
  for (const point of trendRows(state.center.trend)) {
    const row = element("div", "studio-run-trend-row");
    row.setAttribute("aria-label", point.label);
    const bars = element("div", "studio-run-trend-bars");
    bars.append(bar("studio-run-trend-success", point.successWidth), bar("studio-run-trend-failed", point.failedWidth), bar("studio-run-trend-other", point.otherWidth));
    row.append(element("small", "", point.bucket), bars);
    root.append(row);
  }
  if (!root.children.length) root.append(emptyText("尚无可绘制趋势。"));
}

function bar(className, width) {
  const item = element("span", className);
  item.style.width = `${width}%`;
  return item;
}

function renderAnalysis() {
  renderEvidenceList("#studio-run-releases", [
    ...(state.center.regressions || []).map(item => ({
      title: item.label || "检测到回归",
      detail: item.message || "请核对相关发布与运行证据。",
    })),
    ...(state.center.release_overlays || []).slice(0, 4).map(item => ({
      title: item.action === "publish" ? "显式 Publish" : "Apply Draft",
      detail: `${item.outcome || "未知结果"} · ${formatTime(item.at)}`,
    })),
  ], "尚无发布叠加或回归证据。");
  renderEvidenceList("#studio-run-slow-paths", (state.center.slow_paths || []).map(item => ({
    title: item.title,
    detail: `${item.executions} 次执行 · 平均 ${formatDuration(item.average_latency_ms)}`,
  })), "尚无节点时延证据。");
  renderEvidenceList("#studio-run-costly-paths", (state.center.costly_paths || []).map(item => ({
    title: item.title,
    detail: `${item.executions} 次执行 · 估算 ${formatCost(item.estimated_cost_microusd)}`,
  })), "尚无节点成本证据。");
  const missing = document.querySelector("#studio-run-missing");
  if (state.center.missing_evidence.length) {
    missing.hidden = false;
    missing.textContent = `缺失证据：${state.center.missing_evidence.join("；")}`;
  } else {
    missing.hidden = true;
    missing.textContent = "";
  }
}

function renderEvidenceList(selector, items, fallback) {
  const root = document.querySelector(selector);
  root.replaceChildren();
  for (const item of items) {
    const row = element("div", "studio-run-evidence-item");
    row.append(element("strong", "", item.title), element("small", "", item.detail));
    root.append(row);
  }
  if (!root.children.length) root.append(emptyText(fallback));
}

function renderExecutions() {
  const root = document.querySelector("#studio-run-execution-list");
  root.replaceChildren();
  const apps = new Map(state.center.logical_apps.map(item => [item.id, item.name]));
  const environments = new Map(state.center.environments.map(item => [item.id, item.name]));
  for (const run of state.center.executions) {
    const status = runStatusPresentation(run.status);
    const correlation = correlationPresentation(run.correlation_state);
    const row = element("article", "studio-run-execution");
    const head = element("div", "studio-run-row-head");
    const badges = element("div", "studio-run-badges");
    badges.append(badge(status.label, status.tone), badge(correlation.label, correlation.tone));
    head.append(element("strong", "", apps.get(run.logical_app_id) || "可访问应用"), badges);
    row.append(
      head,
      element("p", "", run.safe_message || (run.status === "succeeded" ? "执行已完成。" : "没有保存可展示的错误内容。")),
      element("small", "", `${environments.get(run.environment_id) || "环境不可用"} · ${formatTime(run.started_at)} · ${formatDuration(run.latency_ms)} · ${formatCost(run.estimated_cost_microusd)}`),
      element("small", "", `${errorLabel(run.stable_error_code)} · Artifact ${shortHash(run.artifact_id)}`),
    );
    root.append(row);
  }
  if (!root.children.length) root.append(empty("尚无生产执行", "刷新已配置环境，或调整筛选条件。"));
}

function renderIncidents() {
  const root = document.querySelector("#studio-run-incident-list");
  root.replaceChildren();
  for (const incident of state.center.incidents) {
    const row = element("button", "studio-run-incident");
    row.type = "button";
    row.setAttribute("role", "listitem");
    row.setAttribute("aria-current", String(state.incident?.incident?.id === incident.id));
    const head = element("div", "studio-run-row-head");
    head.append(element("strong", "", incident.title), badge(severityLabel(incident.severity), incident.severity === "critical" ? "danger" : "warning"));
    row.append(head, element("p", "", incident.business_cause), element("small", "", `${errorLabel(incident.stable_error_code)} · ${formatTime(incident.last_seen_at)}`));
    row.addEventListener("click", () => void selectIncident(incident.id));
    root.append(row);
  }
  if (!root.children.length) root.append(empty("没有待处理事件", "失败执行出现后会按稳定错误与节点聚类。"));
}

async function selectIncident(id) {
  try {
    state.incident = await requestJson(`/api/v5/studio/run-incidents/${encodeURIComponent(id)}?project_id=${encodeURIComponent(state.projectId)}`);
    renderIncidents();
    renderIncidentDetail();
  } catch (error) {
    setNotice(error.presentation?.message || "无法读取事件详情。", "danger");
  }
}

function renderIncidentDetail() {
  const detail = state.incident;
  const card = document.querySelector("#studio-run-incident-detail-card");
  card.hidden = !detail;
  if (!detail) return;
  document.querySelector("#studio-run-incident-detail-title").textContent = detail.incident.title;
  const severity = document.querySelector("#studio-run-incident-severity");
  severity.textContent = severityLabel(detail.incident.severity);
  severity.dataset.tone = detail.incident.severity === "critical" ? "danger" : "warning";
  const root = document.querySelector("#studio-run-incident-detail");
  root.replaceChildren();
  const grid = element("div", "studio-run-incident-detail-grid");
  grid.append(
    panel("业务解释", [detail.known_error.title, detail.known_error.cause, detail.known_error.next_step]),
    panel("精确发布证据", [
      detail.artifact_summary ? `Artifact ${shortHash(detail.artifact_summary.artifact_hash)}` : "没有精确 Artifact 关联",
      detail.release_summary ? `发布结果：${detail.release_summary.outcome}` : detail.execution.correlation_reason,
      detail.scenario_coverage?.summary || "Scenario 覆盖证据不可用",
    ]),
    panel("受影响路径", [], detail.affected_path),
    panel("安全 Release Diff", (detail.release_diff || []).length
      ? detail.release_diff.map(item => item.message || `${item.type || "变更"} · ${item.title || item.target || "工作流"}`)
      : ["没有可安全展示的业务 Diff；不会暴露 Raw DSL。"]),
  );
  root.append(grid);
  const create = document.querySelector("#studio-run-create-repair");
  create.hidden = Boolean(detail.repair);
  create.disabled = !detail.can_create_repair;
  const open = document.querySelector("#studio-run-open-repair");
  open.hidden = !detail.repair;
  if (detail.repair) {
    open.href = repairBuildUrl(detail.repair);
    open.textContent = `${repairStatusLabel(detail.repair.status)} · 进入 Build Studio`;
  }
}

function panel(title, lines = [], path = []) {
  const section = element("section", "studio-run-incident-panel");
  section.append(element("h3", "", title));
  for (const line of lines.filter(Boolean)) section.append(element("p", "", String(line)));
  if (path.length) {
    const list = element("ol", "studio-run-path");
    path.forEach(item => list.append(element("li", "", `${item.title || item.node_type || "节点"} · ${runStatusPresentation(item.status).label} · ${formatDuration(item.elapsed_ms)}`)));
    section.append(list);
  }
  return section;
}

function clearIncident() {
  state.incident = null;
  document.querySelector("#studio-run-incident-detail-card").hidden = true;
  renderIncidents();
}

async function refreshEvidence() {
  const button = document.querySelector("#studio-run-refresh-evidence");
  button.disabled = true;
  setNotice("正在从可访问的 Dify 环境读取生产执行；不会重放任何运行。", "loading");
  try {
    const result = await requestJson("/api/v5/studio/run-center/refresh", {
      method: "POST",
      body: {
        project_id: state.projectId,
        environment_id: value("#studio-run-environment") || null,
        limit_per_environment: 100,
      },
    });
    await loadCenter(false);
    await loadAutomation();
    const message = `已读取 ${result.environments_scanned} 个环境、${result.executions_observed} 次执行，新增 ${result.incidents_opened} 个事件；${result.uncorrelated} 次执行未精确关联。`;
    setNotice(result.errors.length ? `${message} ${result.errors.length} 个环境返回安全错误。` : message, result.errors.length ? "warning" : "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "生产执行刷新失败；没有自动重试。", "danger");
  } finally {
    button.disabled = !state.center?.can_refresh;
  }
}

async function loadAutomation() {
  try {
    state.automation = await requestJson(
      `/api/v5/studio/run-automation?project_id=${encodeURIComponent(state.projectId)}`,
    );
    renderAutomation();
  } catch (error) {
    state.automation = null;
    const node = document.querySelector("#studio-run-automation-state");
    node.textContent = error.presentation?.message || "无法读取告警与计划状态。";
    node.dataset.tone = "danger";
  }
}

async function loadTokens() {
  try {
    state.tokens = await requestJson(
      `/api/v5/studio/scoped-tokens?project_id=${encodeURIComponent(state.projectId)}`,
    );
    renderTokens();
  } catch (error) {
    state.tokens = [];
    renderTokens(error.presentation?.message || "无法读取 Scoped Token。" );
  }
}

function renderTokens(failure = "") {
  const canConfigure = Boolean(state.automation?.can_configure);
  document.querySelectorAll("#studio-run-token-form input, #studio-run-token-form select, #studio-run-token-form button").forEach(control => {
    control.disabled = !canConfigure;
  });
  const root = document.querySelector("#studio-run-token-list");
  root.replaceChildren();
  if (failure) {
    root.append(emptyText(failure));
    return;
  }
  for (const record of state.tokens) {
    const presentation = scopedTokenPresentation(record);
    const row = element("div", "studio-run-evidence-item");
    row.append(
      element("strong", "", `${record.name} · ${presentation.label}`),
      element("small", "", `${record.token_prefix}… · ${presentation.scopeSummary}`),
      element("small", "", `到期 ${formatTime(record.expires_at)} · ${record.rate_limit_per_minute}/分钟`),
    );
    const actions = element("div", "studio-run-token-actions");
    const rotate = element("button", "studio-secondary-action", "轮换");
    rotate.type = "button";
    rotate.disabled = presentation.status !== "active" || !canConfigure;
    rotate.addEventListener("click", () => void rotateToken(record));
    const revoke = element("button", "danger-button", "撤销");
    revoke.type = "button";
    revoke.disabled = presentation.status !== "active" || !canConfigure;
    revoke.addEventListener("click", () => void revokeToken(record));
    actions.append(rotate, revoke);
    row.append(actions);
    root.append(row);
  }
  if (!root.children.length) root.append(emptyText("尚未创建 Scoped Token。"));
}

async function issueToken() {
  const scopes = [...document.querySelectorAll("[data-mcp-scope]:checked")].map(item => item.value);
  if (!scopes.length) {
    setNotice("至少选择一个安全 Scope。", "warning");
    return;
  }
  setNotice("正在创建只显示一次的 Scoped Token。", "loading");
  try {
    const issued = await requestJson("/api/v5/studio/scoped-tokens", {
      method: "POST",
      body: {
        project_id: state.projectId,
        name: value("#studio-run-token-name"),
        scopes,
        expires_in_seconds: Number(value("#studio-run-token-expiry")),
        rate_limit_per_minute: Number(value("#studio-run-token-rate")),
      },
    });
    showIssuedToken(issued.token);
    await loadTokens();
    setNotice("Scoped Token 已创建；数据库只保存哈希。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "Scoped Token 创建失败。", "danger");
  }
}

async function rotateToken(record) {
  setNotice("正在原子轮换 Token；旧 Token 会立即撤销。", "loading");
  try {
    const issued = await requestJson(`/api/v5/studio/scoped-tokens/${encodeURIComponent(record.id)}/rotate`, {
      method: "POST",
      body: {
        project_id: state.projectId,
        expected_version: record.version,
        expires_in_seconds: Number(value("#studio-run-token-expiry")),
      },
    });
    showIssuedToken(issued.token);
    await loadTokens();
    setNotice("Token 已轮换；旧 Token 已撤销。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "Token 轮换失败。", "danger");
  }
}

async function revokeToken(record) {
  setNotice("正在撤销 Scoped Token。", "loading");
  try {
    await requestJson(`/api/v5/studio/scoped-tokens/${encodeURIComponent(record.id)}/revoke`, {
      method: "POST",
      body: { project_id: state.projectId, expected_version: record.version },
    });
    await loadTokens();
    setNotice("Scoped Token 已撤销，后续请求将失败关闭。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "Token 撤销失败。", "danger");
  }
}

function showIssuedToken(token) {
  const secret = document.querySelector("#studio-run-token-secret");
  document.querySelector("#studio-run-token-value").textContent = token;
  secret.hidden = false;
  secret.focus();
}

async function copyIssuedToken() {
  const token = document.querySelector("#studio-run-token-value").textContent;
  try {
    await navigator.clipboard.writeText(token);
    setNotice("Token 已复制；离开页面后无法再次查看。", "ok");
  } catch (_error) {
    setNotice("浏览器未允许复制，请手动选择仅显示一次的 Token。", "warning");
  }
}

function renderAutomation() {
  const automation = state.automation;
  if (!automation) return;
  const presentation = automationPresentation(automation);
  const status = document.querySelector("#studio-run-automation-state");
  status.textContent = `${presentation.summary}。${automation.message}`;
  status.dataset.tone = presentation.tone;
  const canConfigure = automation.can_configure;
  for (const form of ["#studio-run-alert-form", "#studio-run-schedule-form"]) {
    document.querySelectorAll(`${form} input, ${form} select, ${form} button`).forEach(control => {
      control.disabled = !canConfigure;
    });
  }
  document.querySelector("#studio-run-tick-automation").disabled = !canConfigure;
  refillSelect(
    "#studio-run-alert-environment",
    "全部已授权环境",
    state.center?.environments || [],
    item => item.name,
  );
  const codes = [...new Set((state.center?.executions || []).map(item => item.stable_error_code).filter(Boolean))];
  refillSelect("#studio-run-alert-error", "全部失败", codes.map(code => ({ id: code })), item => errorLabel(item.id));
  const targetSelect = document.querySelector("#studio-run-schedule-target");
  const currentTarget = targetSelect.value;
  targetSelect.replaceChildren(option("", "没有可调度的已发布 Artifact"));
  for (const target of automation.schedule_targets || []) {
    const item = option(`${target.artifact_id}|${target.suite_id}`, `${target.release_note} · Artifact ${shortHash(target.artifact_hash)} · ${target.suite_name} ${target.suite_version}`);
    targetSelect.append(item);
  }
  if ([...targetSelect.options].some(item => item.value === currentTarget)) targetSelect.value = currentTarget;
  targetSelect.required = true;
  renderEvidenceList("#studio-run-alert-list", (automation.alert_rules || []).map(rule => ({
    title: `${rule.enabled ? "已启用" : "已停用"} · ${rule.name}`,
    detail: `${rule.error_count_threshold} 次 / ${formatWindow(rule.window_seconds)} · Adapter ${rule.adapter_ref}`,
  })), "尚未保存告警规则。");
  renderEvidenceList("#studio-run-schedule-list", (automation.scheduled_regressions || []).map(schedule => ({
    title: schedule.enabled ? "隔离回归计划已启用" : "隔离回归计划已停用",
    detail: `${formatWindow(schedule.interval_seconds)} · 下次 ${formatTime(schedule.next_run_at)}`,
  })), "尚未保存定时回归计划。");
  renderDurableWork();
}

function renderDurableWork() {
  const root = document.querySelector("#studio-run-work-list");
  root.replaceChildren();
  for (const work of state.automation?.durable_work || []) {
    const row = element("div", "studio-run-evidence-item studio-run-work-item");
    const body = element("div");
    body.append(
      element("strong", "", `${workLabel(work.kind)} · ${workStatusLabel(work.status)}`),
      element("small", "", `尝试 ${work.attempts}/${work.max_attempts} · ${formatTime(work.updated_at)}`),
    );
    const cancel = element("button", "studio-secondary-action", "请求取消");
    cancel.type = "button";
    cancel.disabled = !work.can_cancel || !state.automation.can_configure;
    cancel.addEventListener("click", () => void cancelWork(work));
    row.append(body, cancel);
    root.append(row);
  }
  if (!root.children.length) root.append(emptyText("尚无持久化后台工作。"));
}

async function cancelWork(work) {
  setNotice("正在记录取消请求；Worker 会在下一安全边界停止。", "loading");
  try {
    state.automation = await requestJson("/api/v5/studio/durable-work/cancel", {
      method: "POST",
      body: {
        project_id: state.projectId,
        entity_type: work.entity_type,
        entity_id: work.id,
        reason: "Operator requested cancellation from Run Center.",
      },
    });
    renderAutomation();
    setNotice("取消请求已持久化；已经完成或结果含糊的外部动作不会被回滚或重放。", "warning");
  } catch (error) {
    setNotice(error.presentation?.message || "无法取消这项后台工作。", "danger");
  }
}

async function saveAlert() {
  setNotice("正在保存显式告警配置。", "loading");
  try {
    await requestJson("/api/v5/studio/run-alerts", {
      method: "POST",
      body: {
        project_id: state.projectId,
        name: value("#studio-run-alert-name"),
        environment_id: value("#studio-run-alert-environment") || null,
        stable_error_code: value("#studio-run-alert-error") || null,
        error_count_threshold: Number(value("#studio-run-alert-count")),
        failure_rate_threshold: value("#studio-run-alert-rate") === "" ? null : Number(value("#studio-run-alert-rate")),
        window_seconds: Number(value("#studio-run-alert-window")),
        adapter_ref: value("#studio-run-alert-adapter"),
        enabled: document.querySelector("#studio-run-alert-enabled").checked,
      },
    });
    await loadAutomation();
    setNotice("告警规则已保存；达到阈值时只会生成脱敏 Outbox 工作。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "告警规则保存失败。", "danger");
  }
}

async function saveSchedule() {
  const [artifactId, suiteId] = value("#studio-run-schedule-target").split("|");
  if (!artifactId || !suiteId) {
    setNotice("当前没有可调度的已发布 Artifact 与 Scenario Suite。", "warning");
    return;
  }
  setNotice("正在保存隔离回归计划。", "loading");
  try {
    await requestJson("/api/v5/studio/scheduled-regressions", {
      method: "POST",
      body: {
        project_id: state.projectId,
        artifact_id: artifactId,
        suite_id: suiteId,
        interval_seconds: Number(value("#studio-run-schedule-interval")),
        enabled: document.querySelector("#studio-run-schedule-enabled").checked,
      },
    });
    await loadAutomation();
    setNotice("定时回归已绑定已发布 Artifact；只会在隔离 Preview 队列运行。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "定时回归保存失败。", "danger");
  }
}

async function tickAutomation() {
  const button = document.querySelector("#studio-run-tick-automation");
  button.disabled = true;
  setNotice("正在检查阈值和到期计划；不会执行生产写入。", "loading");
  try {
    const result = await requestJson("/api/v5/studio/run-automation/tick", {
      method: "POST",
      body: { project_id: state.projectId },
    });
    await loadAutomation();
    setNotice(`已生成 ${result.alerts_enqueued} 条新告警和 ${result.schedules_enqueued} 个到期回归任务。`, "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "自动化检查失败；没有盲目重试。", "danger");
  } finally {
    button.disabled = !state.automation?.can_configure;
  }
}

async function createRepair() {
  if (!state.incident) return;
  const button = document.querySelector("#studio-run-create-repair");
  button.disabled = true;
  setNotice("正在创建只绑定当前脱敏证据的新 Build。", "loading");
  try {
    const repair = await requestJson(`/api/v5/studio/run-incidents/${encodeURIComponent(state.incident.incident.id)}/repair-proposals`, {
      method: "POST",
      body: { project_id: state.projectId, title: `修复：${state.incident.incident.title}` },
    });
    await selectIncident(state.incident.incident.id);
    const open = document.querySelector("#studio-run-open-repair");
    open.href = repairBuildUrl(repair);
    open.focus();
    setNotice("修复 Build 已创建；生产环境没有被修改。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "无法创建受控修复方案。", "danger");
    button.disabled = !state.incident?.can_create_repair;
  }
}

async function requestJson(path, { method = "GET", body, authenticated = true } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (authenticated && state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(`${basePath}${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = {};
  try { payload = await response.json(); } catch (_error) { payload = {}; }
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
    error.presentation = classifyStudioError(response.status, payload);
    throw error;
  }
  return payload;
}

function setNotice(message, tone = "ready") {
  const notice = document.querySelector("#studio-run-notice");
  notice.textContent = message;
  notice.dataset.tone = tone;
  if (["danger", "warning"].includes(tone)) notice.focus();
}

function setConnection(message, tone) {
  const connection = document.querySelector("#studio-connection");
  connection.textContent = message;
  connection.dataset.tone = tone;
}

function badge(text, tone) {
  const item = element("span", "studio-run-badge", text);
  item.dataset.tone = tone;
  return item;
}

function empty(title, body) {
  const card = element("article", "studio-scenario-empty");
  card.append(element("h3", "", title), element("p", "", body));
  return card;
}

function emptyText(text) { return element("p", "studio-scenario-boundary", text); }

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function option(valueText, label) {
  const item = document.createElement("option");
  item.value = valueText;
  item.textContent = label;
  return item;
}

function value(selector) { return String(document.querySelector(selector)?.value || "").trim(); }

function formatTime(value) {
  if (!value) return "时间证据不可用";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "时间证据不可用";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function severityLabel(value) {
  return { critical: "严重", warning: "警告", info: "提示" }[value] || value;
}

function formatWindow(seconds) {
  const value = Number(seconds);
  if (value % 604800 === 0) return `每 ${value / 604800} 周`;
  if (value % 86400 === 0) return `每 ${value / 86400} 天`;
  if (value % 3600 === 0) return `每 ${value / 3600} 小时`;
  return `${Math.round(value / 60)} 分钟`;
}

function workLabel(kind) {
  return {
    "scenario.scheduled_regression": "定时 Scenario 回归",
    "notification.run_alert": "运行告警通知",
  }[kind] || kind;
}

function workStatusLabel(status) {
  return {
    pending: "等待 Worker",
    leased: "执行中（租约已持有）",
    completed: "已完成且有回执",
    failed: "确定失败",
    ambiguous: "结果含糊，需对账",
    cancelled: "已取消",
    dead_letter: "已进入 Dead Letter",
  }[status] || status;
}

function createNonce() {
  const bytes = new Uint8Array(24);
  window.crypto.getRandomValues(bytes);
  return [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
}
