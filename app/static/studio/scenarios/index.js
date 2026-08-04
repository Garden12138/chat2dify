import {
  comparisonRows,
  gatePresentation,
  inputSchemaQuery,
  isScenarioLabEnabled,
  runPresentation,
  safeBuildReturnUrl,
  scenarioIdentity,
  scenarioLabQuery,
  scenarioRunPayload,
  scenarioSource,
  suitePayload,
} from "./core.mjs";
import { classifyStudioError } from "../home/core.mjs";

const config = window.CHAT2DIFY_CONFIG || {};
const basePath = normalizeBasePath(config.basePath || "");
const identity = scenarioIdentity(window.location.search);
const enabled = isScenarioLabEnabled(config, window.location.search);
const state = {
  token: "",
  projectId: "",
  buildId: identity.buildId,
  lab: null,
  schema: null,
  candidateIds: new Set(identity.candidateIds),
  generatedCases: [],
  currentRun: null,
  mappingOrdinal: 0,
  pollTimer: null,
  busy: false,
};

if (enabled) {
  window.CHAT2DIFY_STUDIO_SCENARIOS = true;
  document.addEventListener("DOMContentLoaded", () => void bootScenarioLab());
}

async function bootScenarioLab() {
  document.body.classList.add("studio-v5", "studio-v5-scenarios");
  if (identity.embedded) document.body.classList.add("studio-embedded");
  document.querySelector("#legacy-app-frame")?.setAttribute("hidden", "");
  document.querySelector("#studio-root").hidden = false;
  document.querySelector("#studio-content").hidden = true;
  document.querySelector("#studio-build-content").hidden = true;
  document.querySelector("#studio-blueprint-content").hidden = true;
  document.querySelector("#studio-scenario-content").hidden = false;
  document.querySelector("#studio-state").hidden = true;
  activateNavigation();
  bindActions();
  updateReturnLink();
  if (!state.buildId) {
    setNotice("请从一个包含有效 Candidate 的 Build Studio 进入 Scenario Lab。", "warning");
  }
  await reconnect();
}

function activateNavigation() {
  document.querySelectorAll(".studio-nav-item").forEach(item => {
    item.classList.remove("studio-nav-active");
    item.removeAttribute("aria-current");
  });
  const link = document.querySelector("#studio-scenarios-nav");
  link?.classList.add("studio-nav-active");
  link?.setAttribute("aria-current", "page");
}

function bindActions() {
  document.querySelector("#studio-scenario-refresh").addEventListener("click", () => void loadLab({ announce: true }));
  document.querySelector("#studio-scenario-generate").addEventListener("click", () => void generateCases());
  document.querySelector("#studio-scenario-suite-form").addEventListener("submit", event => {
    event.preventDefault();
    void saveSuite();
  });
  document.querySelector("#studio-scenario-source").addEventListener("change", renderSourceOptions);
  document.querySelector("#studio-scenario-expected-kind").addEventListener("change", renderExpectedControl);
  document.querySelector("#studio-scenario-invariant-kind").addEventListener("change", renderInvariantControl);
  document.querySelector("#studio-scenario-add-mapping").addEventListener("click", () => addMappingRow());
  document.querySelector("#studio-scenario-run").addEventListener("click", () => void runSuite());
  document.querySelector("#studio-scenario-cancel").addEventListener("click", () => void cancelRun());
  document.querySelector("#studio-scenario-save-baseline").addEventListener("click", () => void saveBaseline());
  document.querySelector("#studio-scenario-save-gate").addEventListener("click", () => void saveGate());
  document.querySelector("#studio-scenario-approve-source").addEventListener("click", () => void approveSanitizedSource());
}

async function reconnect() {
  setConnection("正在连接 Dify", "loading");
  setBusy(true);
  try {
    const session = await requestJson("/api/v5/studio/session", {
      method: "POST",
      body: { nonce: createNonce() },
      authenticated: false,
    });
    state.token = session.token;
    state.projectId = session.project.id;
    document.querySelector("#studio-project-badge").textContent = session.project.name;
    setConnection("Dify 已验证", "ok");
    if (state.buildId) await loadLab({ announce: false });
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function loadLab({ announce }) {
  if (!state.buildId || !state.projectId) return;
  if (announce) setNotice("正在读取 Scenario、Candidate 与清理状态。", "neutral");
  try {
    const lab = await requestJson(scenarioLabQuery(state.projectId, state.buildId));
    state.lab = lab;
    const valid = validCandidates(lab);
    const known = new Set(valid.map(item => item.candidate.id));
    state.candidateIds = new Set([...state.candidateIds].filter(id => known.has(id)));
    if (!state.candidateIds.size) valid.forEach(item => state.candidateIds.add(item.candidate.id));
    state.currentRun = lab.runs?.[0] || state.currentRun;
    renderLab();
    if (state.candidateIds.size) await discoverSchema();
    schedulePoll();
  } catch (error) {
    showError(error);
  }
}

function renderLab() {
  const lab = state.lab;
  if (!lab) return;
  document.querySelector("#studio-scenario-context").textContent = lab.build?.build?.app_name || "Scenario Build";
  renderCandidates();
  renderEnvironment();
  renderSuites();
  renderSourceOptions();
  renderRun(state.currentRun);
  setNotice(lab.message || "Scenario Lab 已就绪。", lab.state === "ready" ? "success" : "warning");
}

function validCandidates(lab = state.lab) {
  return (lab?.build?.candidates || []).filter(item =>
    item?.candidate?.status === "valid" && item.reconstructable === true,
  );
}

function renderCandidates() {
  const root = document.querySelector("#studio-scenario-candidates");
  root.querySelectorAll("label, p").forEach(item => item.remove());
  const candidates = validCandidates();
  if (!candidates.length) {
    root.append(element("p", "studio-scenario-boundary", "当前 Build 没有有效且可重建的 Candidate。"));
    return;
  }
  for (const item of candidates) {
    const label = element("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = item.candidate.id;
    input.checked = state.candidateIds.has(item.candidate.id);
    input.setAttribute("aria-label", `选择 ${item.candidate.label} 参与 Scenario 对比`);
    input.addEventListener("change", async () => {
      if (input.checked) state.candidateIds.add(item.candidate.id);
      else state.candidateIds.delete(item.candidate.id);
      await discoverSchema();
    });
    label.append(input, document.createTextNode(` ${item.candidate.label}`));
    root.append(label);
  }
}

async function discoverSchema() {
  const candidateIds = [...state.candidateIds];
  if (!candidateIds.length) {
    state.schema = null;
    renderSchema();
    return;
  }
  try {
    state.schema = await requestJson(inputSchemaQuery(
      state.projectId,
      state.buildId,
      candidateIds,
    ));
    renderSchema();
  } catch (error) {
    state.schema = null;
    renderSchema();
    showError(error);
  }
}

function renderSchema() {
  const root = document.querySelector("#studio-scenario-schema");
  const inputs = document.querySelector("#studio-scenario-inputs");
  root.replaceChildren();
  inputs.replaceChildren();
  if (!state.schema) {
    root.append(element("p", "studio-scenario-boundary", "先选择至少一个共享同一输入 Schema 的 Candidate。"));
    return;
  }
  root.append(
    element("strong", "", `${state.schema.app_mode === "workflow" ? "Workflow" : "Chatflow"} 输入 Schema`),
    element("span", "studio-scenario-hash", `Schema ${state.schema.schema_hash.slice(0, 12)}`),
  );
  for (const field of state.schema.fields || []) {
    root.append(element("span", "studio-scenario-field", `${field.label} · ${field.value_type}${field.required ? " · 必填" : ""}`));
    if (["file", "file-list"].includes(field.value_type)) {
      const label = element("label", "studio-scenario-file-input");
      label.append(document.createTextNode(`${field.label}（已批准 Fixture）`));
      const select = document.createElement("select");
      select.dataset.schemaField = field.name;
      select.dataset.valueType = field.value_type;
      select.append(option("", "选择已批准文件"));
      for (const fixture of state.lab?.file_fixtures || []) {
        const item = option(fixture.id, `${fixture.name} · ${fixture.media_type}`);
        item.dataset.fixture = JSON.stringify(fixture);
        select.append(item);
      }
      label.append(select);
      inputs.append(label);
    } else {
      const label = element("label");
      label.append(document.createTextNode(field.label));
      const control = field.value_type === "paragraph" ? document.createElement("textarea") : document.createElement("input");
      control.dataset.schemaField = field.name;
      control.dataset.valueType = field.value_type;
      control.required = field.required;
      if (control.tagName === "TEXTAREA") control.rows = 2;
      if (field.value_type === "number") control.type = "number";
      if (field.value_type === "boolean") control.type = "checkbox";
      label.append(control);
      inputs.append(label);
    }
  }
}

async function generateCases() {
  if (!state.schema || !state.candidateIds.size) {
    setNotice("必须先完成确定性 Schema 发现。", "danger");
    return;
  }
  setBusy(true);
  try {
    state.generatedCases = await requestJson("/api/v5/studio/scenario-suites/generate-edge-cases", {
      method: "POST",
      body: {
        project_id: state.projectId,
        build_id: state.buildId,
        candidate_ids: [...state.candidateIds],
        input_schema_hash: state.schema.schema_hash,
      },
    });
    const root = document.querySelector("#studio-scenario-generated-cases");
    root.replaceChildren(...state.generatedCases.map(item => {
      const card = element("article");
      card.append(element("strong", "", item.name), element("p", "", item.expected_behavior));
      return card;
    }));
    setNotice(`已按当前 Schema 生成 ${state.generatedCases.length} 个确定性边界用例。`, "success");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function saveSuite() {
  if (!state.schema || !state.candidateIds.size) {
    setNotice("请先选择 Candidate 并发现 Schema。", "danger");
    return;
  }
  const draft = readCase();
  if (!draft) return;
  const payload = suitePayload({
    projectId: state.projectId,
    buildId: state.buildId,
    candidateIds: [...state.candidateIds],
    schemaHash: state.schema.schema_hash,
    name: value("#studio-scenario-suite-name"),
    description: value("#studio-scenario-suite-description"),
    retentionDays: value("#studio-scenario-retention"),
    version: value("#studio-scenario-suite-version"),
    cases: [draft, ...state.generatedCases],
  });
  setBusy(true);
  try {
    const suite = await requestJson("/api/v5/studio/scenario-suites", { method: "POST", body: payload });
    await loadLab({ announce: false });
    document.querySelector("#studio-scenario-suite-select").value = suite.id;
    setNotice(`Scenario Suite ${suite.name} ${suite.semantic_version} 已保存；所有内容均标记为不可信数据。`, "success");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function readCase() {
  const inputs = {};
  const files = [];
  for (const control of document.querySelectorAll("#studio-scenario-inputs [data-schema-field]")) {
    const field = control.dataset.schemaField;
    const kind = control.dataset.valueType;
    if (["file", "file-list"].includes(kind)) {
      const selected = control.selectedOptions?.[0];
      if (!selected?.value) continue;
      const fixture = JSON.parse(selected.dataset.fixture || "{}");
      files.push({
        field_name: field,
        source: "approved_fixture",
        opaque_ref: fixture.opaque_ref,
        name: fixture.name,
        media_type: fixture.media_type,
        size_bytes: fixture.size_bytes,
        fixture_id: fixture.id,
      });
      continue;
    }
    if (kind === "boolean") inputs[field] = control.checked;
    else if (kind === "number") inputs[field] = Number(control.value);
    else if (kind === "json") {
      try { inputs[field] = JSON.parse(control.value); }
      catch (_error) {
        setNotice(`${field} 必须是有效的业务 JSON 值。`, "danger");
        return null;
      }
    } else inputs[field] = control.value;
  }
  const sourceKind = value("#studio-scenario-source");
  const reference = value("#studio-scenario-source-reference");
  const selectedSource = document.querySelector("#studio-scenario-source-reference")?.selectedOptions?.[0];
  const evidenceHash = selectedSource?.dataset.evidenceHash || "";
  const expectedKind = value("#studio-scenario-expected-kind");
  const invariantKind = value("#studio-scenario-invariant-kind");
  return {
    name: value("#studio-scenario-case-name"),
    source: scenarioSource(sourceKind, { schemaHash: state.schema.schema_hash, reference, evidenceHash }),
    inputs,
    files,
    expected_output: { kind: expectedKind, value: typedValue(expectedKind, value("#studio-scenario-expected-value")) },
    expected_behavior: value("#studio-scenario-expected-behavior"),
    invariants: [{
      kind: invariantKind,
      target: typedInvariant(invariantKind, value("#studio-scenario-invariant-target")),
      description: "用户定义的确定性业务约束。",
    }],
    rubric: [{ name: "业务约束", description: "预期输出与确定性约束均通过。", weight: 100, invariant_indexes: [0] }],
    tags: value("#studio-scenario-tags").split(",").map(item => item.trim()).filter(Boolean),
  };
}

function renderSourceOptions() {
  const kind = value("#studio-scenario-source");
  const wrap = document.querySelector("#studio-scenario-source-reference-wrap");
  const select = document.querySelector("#studio-scenario-source-reference");
  select.replaceChildren();
  wrap.hidden = !["fixture", "approved_sanitized_run"].includes(kind);
  if (kind === "fixture") {
    select.append(option("", "选择已批准 Fixture"));
    for (const fixture of state.lab?.file_fixtures || []) select.append(option(fixture.id, fixture.name));
  } else if (kind === "approved_sanitized_run") {
    select.append(option("", "选择已批准脱敏 Run"));
    for (const source of state.lab?.sanitized_run_sources || []) {
      const item = option(source.source_run_id, `Run ${source.source_run_id.slice(0, 8)} · 已脱敏`);
      item.dataset.evidenceHash = source.evidence_hash;
      select.append(item);
    }
  }
}

function renderExpectedControl() {
  const kind = value("#studio-scenario-expected-kind");
  const input = document.querySelector("#studio-scenario-expected-value");
  input.value = kind === "status" ? "succeeded" : kind === "human_escalation" ? "true" : "已受理";
}

function renderInvariantControl() {
  const kind = value("#studio-scenario-invariant-kind");
  document.querySelector("#studio-scenario-invariant-target").value = {
    status_is: "succeeded",
    max_latency_ms: "5000",
    max_tokens: "2000",
    human_escalation_is: "true",
  }[kind] || "";
}

function renderEnvironment() {
  const badge = document.querySelector("#studio-scenario-environment");
  const environment = state.lab?.environment;
  badge.textContent = environment ? `${environment.name} · 非生产` : "Preview 未配置";
  badge.dataset.tone = environment ? "ok" : "warning";
  document.querySelector("#studio-scenario-run").disabled = !environment || state.busy;
}

function renderSuites() {
  const select = document.querySelector("#studio-scenario-suite-select");
  const selected = select.value;
  select.replaceChildren(option("", "选择一个 Scenario Suite"));
  for (const suite of state.lab?.suites || []) {
    select.append(option(suite.id, `${suite.name} · ${suite.semantic_version} · ${suite.cases.length} 例`));
  }
  if ([...select.options].some(item => item.value === selected)) select.value = selected;
}

function addMappingRow(initial = {}) {
  const row = element("div", "studio-scenario-mapping-row");
  row.dataset.mappingId = String(++state.mappingOrdinal);
  const kind = document.createElement("select");
  kind.setAttribute("aria-label", "测试资源类型");
  for (const item of [["model", "模型"], ["dataset", "Dataset"], ["tool", "Tool"], ["trigger", "Trigger"]]) kind.append(option(item[0], item[1]));
  kind.value = initial.kind || "model";
  const logical = document.createElement("input");
  logical.placeholder = "Candidate 资源，例如 provider::model";
  logical.setAttribute("aria-label", "Candidate 资源引用");
  logical.value = initial.logical_ref || "";
  const target = document.createElement("input");
  target.placeholder = "Preview 测试资源";
  target.setAttribute("aria-label", "Preview 测试资源引用");
  target.value = initial.target_ref || "";
  const remove = element("button", "studio-secondary-action", "移除");
  remove.type = "button";
  remove.addEventListener("click", () => row.remove());
  row.append(kind, logical, target, remove);
  document.querySelector("#studio-scenario-mappings").append(row);
}

function readMappings() {
  return [...document.querySelectorAll(".studio-scenario-mapping-row")].map(row => ({
    kind: row.querySelector("select").value,
    logical_ref: row.querySelectorAll("input")[0].value,
    target_ref: row.querySelectorAll("input")[1].value,
  }));
}

function currentPolicy() {
  return scenarioRunPayload({
    projectId: state.projectId,
    buildId: state.buildId,
    suiteId: value("#studio-scenario-suite-select"),
    environmentId: state.lab?.environment?.id || "",
    candidateIds: [...state.candidateIds],
    mappings: readMappings(),
    allowedSideEffects: [...document.querySelectorAll("[data-preview-effect]:checked")].map(item => item.value),
    sideEffectsConfirmed: document.querySelector("#studio-scenario-side-effects-confirmed").checked,
    timeoutSeconds: value("#studio-scenario-timeout"),
    maxCases: value("#studio-scenario-max-cases"),
    maxTotalTokens: value("#studio-scenario-max-tokens"),
    maxEstimatedCostMicrousd: value("#studio-scenario-max-cost"),
  });
}

async function runSuite() {
  const payload = currentPolicy();
  if (!payload.suite_id || !payload.candidate_ids.length || !payload.environment_id) {
    setNotice("请选择 Suite、Candidate，并配置显式非生产 Preview。", "danger");
    return;
  }
  setBusy(true);
  try {
    state.currentRun = await requestJson("/api/v5/studio/scenario-runs", { method: "POST", body: payload });
    renderRun(state.currentRun);
    setNotice("Scenario Run 已持久化；外部动作将逐项记录 Intent、Receipt 和 Cleanup。", "success");
    schedulePoll();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function schedulePoll() {
  window.clearTimeout(state.pollTimer);
  const run = state.currentRun;
  if (!run || !["pending", "running"].includes(run.status)) return;
  state.pollTimer = window.setTimeout(() => void pollRun(), 900);
}

async function pollRun() {
  if (!state.currentRun) return;
  try {
    state.currentRun = await requestJson(
      `/api/v5/studio/scenario-runs/${encodeURIComponent(state.currentRun.id)}?project_id=${encodeURIComponent(state.projectId)}`,
    );
    renderRun(state.currentRun);
    schedulePoll();
  } catch (error) {
    showError(error);
  }
}

async function cancelRun() {
  if (!state.currentRun || !["pending", "running"].includes(state.currentRun.status)) return;
  try {
    state.currentRun = await requestJson(
      `/api/v5/studio/scenario-runs/${encodeURIComponent(state.currentRun.id)}/cancel`,
      { method: "POST", body: { project_id: state.projectId } },
    );
    renderRun(state.currentRun);
    setNotice("已请求取消；不会自动重放 Preview 或清理动作。", "warning");
  } catch (error) {
    showError(error);
  }
}

function renderRun(run) {
  const status = document.querySelector("#studio-scenario-run-state");
  const comparison = document.querySelector("#studio-scenario-comparison");
  const baselineSelect = document.querySelector("#studio-scenario-baseline-candidate");
  baselineSelect.replaceChildren(option("", "运行后选择"));
  comparison.replaceChildren();
  if (!run) {
    status.textContent = "尚未运行";
    comparison.append(emptyCard("还没有可比较证据", "运行至少一个 Candidate 后，这里才会显示真实指标。"));
    setEvidenceActions(false);
    return;
  }
  const presentation = runPresentation(run);
  status.textContent = presentation.label;
  status.dataset.tone = presentation.tone;
  document.querySelector("#studio-scenario-cancel").disabled = !["pending", "running"].includes(run.status);
  const rows = comparisonRows(run);
  if (rows.length) {
    comparison.append(comparisonTable(rows));
    for (const row of rows) baselineSelect.append(option(row.candidateId, row.label));
  } else {
    comparison.append(emptyCard(
      presentation.label,
      run.failure?.message || "等待 Candidate Evidence 与 Cleanup Readback。",
    ));
  }
  const gate = gatePresentation(run.comparison);
  document.querySelector("#studio-scenario-limitations").textContent =
    `${gate.label}。成本是基于固定 Token 单价的估算；Dify 未返回的指标会明确标为缺失，不会推测。`;
  document.querySelector("#studio-scenario-cleanup").textContent = presentation.cleanupMessage;
  document.querySelector("#studio-scenario-cleanup").dataset.ok = String(presentation.releaseEligible);
  setEvidenceActions(presentation.releaseEligible && rows.length > 0);
}

function comparisonTable(rows) {
  const table = element("table");
  const caption = element("caption", "studio-visually-hidden", "Candidate Scenario 指标对比");
  const thead = element("thead");
  const header = element("tr");
  for (const label of ["Candidate", "通过率", "质量", "时延", "Tokens", "成本估算", "人工接管", "回归 / Gate", "Cleanup"]) header.append(element("th", "", label));
  thead.append(header);
  const tbody = element("tbody");
  for (const row of rows) {
    const tr = element("tr");
    const issues = [...row.regressions, ...row.gateFailures];
    const values = [
      row.label,
      `${Math.round(row.passRate * 100)}%`,
      row.quality.toFixed(1),
      row.latencyMs === null ? "缺失" : `${row.latencyMs} ms`,
      row.tokens === null ? "缺失" : String(row.tokens),
      row.costMicrousd === null ? "缺失" : `${row.costMicrousd} µUSD`,
      String(row.humanEscalations),
      issues.length ? issues.join("、") : "无",
      row.cleanupVerified ? "已验证不存在" : "待处理",
    ];
    for (const value of values) tr.append(element("td", "", value));
    tbody.append(tr);
  }
  table.append(caption, thead, tbody);
  return table;
}

function setEvidenceActions(enabledValue) {
  document.querySelector("#studio-scenario-save-baseline").disabled = !enabledValue;
  document.querySelector("#studio-scenario-save-gate").disabled = !enabledValue || !value("#studio-scenario-suite-select");
  document.querySelector("#studio-scenario-approve-source").disabled = !enabledValue;
}

async function saveBaseline() {
  const candidateId = value("#studio-scenario-baseline-candidate");
  if (!state.currentRun || !candidateId) {
    setNotice("请选择一个 Candidate Report 作为 Baseline。", "danger");
    return;
  }
  try {
    await requestJson(`/api/v5/studio/scenario-runs/${encodeURIComponent(state.currentRun.id)}/baseline`, {
      method: "POST",
      body: { project_id: state.projectId, candidate_id: candidateId },
    });
    await loadLab({ announce: false });
    setNotice("Baseline 已绑定精确 Candidate、Mapping、Suite、Policy、Environment 与有效期。", "success");
  } catch (error) { showError(error); }
}

async function saveGate() {
  const policy = currentPolicy().policy;
  const suiteId = value("#studio-scenario-suite-select");
  if (!suiteId) return;
  try {
    await requestJson("/api/v5/studio/regression-gates", {
      method: "PUT",
      body: {
        project_id: state.projectId,
        build_id: state.buildId,
        suite_id: suiteId,
        min_pass_rate: Number(value("#studio-scenario-gate-pass")),
        min_quality_score: Number(value("#studio-scenario-gate-quality")),
        max_latency_regression_percent: Number(value("#studio-scenario-gate-latency")),
        max_cost_regression_percent: Number(value("#studio-scenario-gate-cost")),
        evidence_ttl_seconds: 604800,
        required_policy: policy,
      },
    });
    await loadLab({ announce: false });
    setNotice("Regression Gate 已保存；将从下一次精确绑定的 Run 起生效，Suite、Policy 或证据有效期变化会使它失效。", "success");
  } catch (error) { showError(error); }
}

async function approveSanitizedSource() {
  if (!state.currentRun) return;
  try {
    await requestJson(
      `/api/v5/studio/scenario-runs/${encodeURIComponent(state.currentRun.id)}/approve-sanitized-source`,
      { method: "POST", body: { project_id: state.projectId, ttl_seconds: 604800 } },
    );
    await loadLab({ announce: false });
    setNotice("当前 Report 已显式批准为限时、脱敏、不可信的数据源。", "success");
  } catch (error) { showError(error); }
}

function updateReturnLink() {
  document.querySelector("#studio-scenario-back").href = safeBuildReturnUrl(basePath, state.buildId);
}

function setBusy(busy) {
  state.busy = busy;
  for (const selector of [
    "#studio-scenario-refresh",
    "#studio-scenario-generate",
    "#studio-scenario-save-suite",
    "#studio-scenario-run",
  ]) {
    const control = document.querySelector(selector);
    if (control) control.disabled = busy;
  }
  renderEnvironment();
}

function showError(error) {
  const presentation = error.presentation || classifyStudioError(500, {});
  setNotice(`${presentation.title}：${presentation.message}`, "danger");
  setConnection(presentation.kind === "offline" ? "Studio 离线" : "需要处理", "danger");
}

function setNotice(message, tone = "neutral") {
  const notice = document.querySelector("#studio-scenario-notice");
  notice.textContent = message;
  notice.dataset.tone = tone;
}

function setConnection(message, tone) {
  const connection = document.querySelector("#studio-connection");
  connection.textContent = message;
  connection.dataset.tone = tone;
}

async function requestJson(path, options = {}) {
  const headers = { Accept: "application/json" };
  if (state.token && options.authenticated !== false) headers.Authorization = `Bearer ${state.token}`;
  const init = {
    method: options.method || "GET",
    headers,
    credentials: "same-origin",
    referrerPolicy: "strict-origin",
  };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  let response;
  try { response = await fetch(apiUrl(path), init); }
  catch (_error) {
    const error = new Error("网络连接失败。");
    error.presentation = classifyStudioError(503, { error: { code: "STUDIO_NETWORK_OFFLINE", message: "无法连接 Chat2Dify，请检查网络或服务状态。", retryable: true } });
    throw error;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
    error.code = payload?.error?.code;
    error.presentation = classifyStudioError(response.status, payload);
    throw error;
  }
  setConnection("Dify 已验证", "ok");
  return payload;
}

function typedValue(kind, raw) {
  if (kind === "human_escalation") return raw === "true";
  return raw;
}

function typedInvariant(kind, raw) {
  if (["max_latency_ms", "max_tokens"].includes(kind)) return Number(raw);
  if (kind === "human_escalation_is") return raw === "true";
  return raw;
}

function value(selector) {
  return String(document.querySelector(selector)?.value || "").trim();
}

function option(valueText, label) {
  const item = document.createElement("option");
  item.value = valueText;
  item.textContent = label;
  return item;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

function emptyCard(title, message) {
  const card = element("section", "studio-scenario-empty");
  card.append(element("h3", "", title), element("p", "", message));
  return card;
}

function createNonce() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (!globalThis.crypto?.getRandomValues) throw new Error("浏览器不支持安全随机数，无法建立 Studio 会话。");
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(24));
  return Array.from(bytes, item => item.toString(16).padStart(2, "0")).join("");
}

function apiUrl(path) {
  return `${basePath}${path.startsWith("/") ? path : `/${path}`}`;
}

function normalizeBasePath(raw) {
  const normalized = String(raw || "").trim();
  if (!normalized || normalized === "/") return "";
  return `${normalized.startsWith("/") ? "" : "/"}${normalized}`.replace(/\/+$/, "");
}
