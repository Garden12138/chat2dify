import {
  buildCommandPayload,
  buildCreatePayload,
  buildIdentity,
  candidateStatus,
  canSelectCandidate,
  comparisonRows,
  conciseValue,
  contextCommandsEnabled,
  isBuildStudioEnabled,
  layoutPresentation,
  selectedCandidate,
} from "./core.mjs";
import {
  CanvasContextChannel,
  isContextNonce,
  supportsCanvasContext,
} from "../../agent-workbench-core.mjs";
import { classifyStudioError } from "../home/core.mjs";

const config = window.CHAT2DIFY_CONFIG || {};
const basePath = normalizeBasePath(config.basePath || "");
const identity = buildIdentity(window.location.search);
const enabled = isBuildStudioEnabled(config, window.location.search);
const state = {
  token: "",
  projectId: "",
  buildId: identity.buildId,
  view: null,
  activeCandidateId: "",
  sourceCandidateIds: new Set(),
  canvasContext: null,
  contextChannel: null,
  pollTimer: null,
  requestSequence: 0,
  busy: false,
};

if (enabled) {
  window.CHAT2DIFY_AGENT_WORKBENCH = true;
  window.CHAT2DIFY_STUDIO_BUILD = true;
  document.addEventListener("DOMContentLoaded", () => {
    void bootBuildStudio();
  });
}

async function bootBuildStudio() {
  document.body.classList.add("studio-v5", "studio-v5-build");
  if (identity.embedded) document.body.classList.add("studio-embedded");
  document.querySelector("#legacy-app-frame")?.setAttribute("hidden", "");
  const shell = document.querySelector("#studio-root");
  shell.hidden = false;
  document.querySelector("#studio-content").hidden = true;
  document.querySelector("#studio-build-content").hidden = false;
  document.querySelector("#studio-state").hidden = true;
  activateBuildNavigation();
  bindActions();
  setupCanvasChannel();
  renderIdentity();
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
    if (state.buildId) {
      document.querySelector("#studio-build-app-mode").disabled = true;
      await refreshBuild({ announce: false });
    } else if (identity.operation === "modify") {
      await ensureBuild();
      await refreshBuild({ announce: false });
    } else {
      setNotice("选择应用类型并描述目标后，将创建 Workspace-only Candidate。", "neutral");
      renderEmpty();
    }
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function activateBuildNavigation() {
  const home = document.querySelector("#studio-home-nav");
  const build = document.querySelector("#studio-build-nav");
  home?.classList.remove("studio-nav-active");
  home?.removeAttribute("aria-current");
  build?.classList.add("studio-nav-active");
  build?.setAttribute("aria-current", "page");
}

function renderIdentity() {
  document.querySelector("#studio-build-title").textContent = identity.operation === "create"
    ? "新建 Dify 应用的安全候选"
    : (identity.appName || "Build Studio");
  document.querySelector("#studio-build-subtitle").textContent = identity.operation === "create"
    ? "Chatbot、Completion、Agent 与 Graph 应用都先进入 Typed Workspace，不创建 Dify 应用。"
    : `${identity.entrySource === "home" ? "从 Dify 已持久化 Draft 打开" : "等待 Dify 画布安全上下文"}；Candidate 选择不会写回 Dify。`;
  const modeWrap = document.querySelector("#studio-build-app-mode-wrap");
  modeWrap.hidden = identity.operation !== "create";
  document.querySelector("#studio-build-app-mode").value = identity.appMode;
}

function bindActions() {
  document.querySelector("#studio-build-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void submitCommand();
  });
  document.querySelector("#studio-build-refresh").addEventListener("click", () => {
    void refreshBuild({ announce: true });
  });
  document.querySelector("#studio-select-candidate").addEventListener("click", () => {
    void selectVisibleCandidate();
  });
  document.querySelector("#studio-cancel-candidate").addEventListener("click", () => {
    void cancelVisibleCandidate();
  });
  document.querySelector("#studio-resume-candidate").addEventListener("click", () => {
    void resumeVisibleCandidate();
  });
  document.querySelector("#studio-focus-changes").addEventListener("click", () => {
    focusLayout(true);
  });
  document.querySelector("#studio-fit-candidate").addEventListener("click", () => {
    focusLayout(false);
  });
  document.querySelectorAll("[data-context-command]").forEach(button => {
    button.addEventListener("click", () => {
      void runContextCommand(button.dataset.contextCommand);
    });
  });
  document.querySelector("#studio-build-mode").addEventListener("change", () => {
    renderCandidateTabs();
  });
}

function setupCanvasChannel() {
  const needsCanvas = supportsCanvasContext(identity.appMode)
    && identity.operation === "modify"
    && identity.embedded
    && identity.entrySource === "canvas";
  if (!needsCanvas) {
    setSelection(identity.operation === "create"
      ? "新建模式没有现有画布选区。"
      : "从 Studio Home 打开：使用 Dify 持久化 Draft，不虚构画布选区。");
    return;
  }
  if (!isContextNonce(identity.contextNonce)) {
    setSelection("画布上下文 nonce 无效；为安全起见已禁用构建。", true);
    return;
  }
  try {
    state.contextChannel = new CanvasContextChannel({
      expectedOrigin: parentOrigin(),
      nonce: identity.contextNonce,
      sourceWindow: window.parent,
    });
  } catch (_error) {
    setSelection("无法建立 Dify 画布安全上下文。", true);
    return;
  }
  window.addEventListener("message", event => {
    const accepted = state.contextChannel.accept(event);
    if (!accepted) return;
    state.canvasContext = accepted.context;
    setSelection(
      accepted.context.selected_node_ids.length
        ? `Dify 已选择 ${accepted.context.selected_node_ids.length} 个节点；revision ${accepted.context.revision}`
        : `Dify 画布已连接；revision ${accepted.context.revision}`,
      accepted.context.dirty_state,
    );
    renderContextActionState();
  });
  window.parent.postMessage(
    state.contextChannel.frameMessage("chat2dify.ready"),
    parentOrigin(),
  );
  setSelection("等待 Dify 画布安全握手。", true);
}

async function ensureBuild() {
  if (state.buildId) return state.buildId;
  const appMode = document.querySelector("#studio-build-app-mode").value;
  const build = await requestJson("/api/v5/studio/builds", {
    method: "POST",
    body: buildCreatePayload(identity, state.projectId, appMode),
  });
  state.buildId = build.id;
  updateBlueprintLink();
  document.querySelector("#studio-build-app-mode").disabled = true;
  const url = new URL(window.location.href);
  url.searchParams.set("build_id", build.id);
  if (identity.operation === "create") url.searchParams.set("app_mode", appMode);
  window.history.replaceState({}, "", `${url.pathname}${url.search}`);
  return build.id;
}

async function submitCommand() {
  const message = document.querySelector("#studio-build-input").value.trim();
  if (!message || state.busy) return;
  const canvasRequired = identity.operation === "modify"
    && identity.entrySource === "canvas"
    && identity.embedded
    && supportsCanvasContext(identity.appMode);
  if (canvasRequired && !state.canvasContext) {
    setNotice("请先等待 Dify 画布安全握手完成。", "danger");
    return;
  }
  const modeValue = document.querySelector("#studio-build-mode").value;
  if (modeValue === "synthesize" && state.sourceCandidateIds.size < 2) {
    setNotice("综合方案前请勾选两个或三个有效 Candidate。", "danger");
    return;
  }
  setBusy(true);
  try {
    await ensureBuild();
    const payload = buildCommandPayload({
      projectId: state.projectId,
      modeValue,
      message,
      sourceCandidateIds: [...state.sourceCandidateIds],
      canvasContext: state.canvasContext,
    });
    const view = await requestJson(
      `/api/v5/studio/builds/${encodeURIComponent(state.buildId)}/commands`,
      { method: "POST", body: payload },
    );
    document.querySelector("#studio-build-input").value = "";
    state.view = view;
    if (payload.mode === "synthesize") state.sourceCandidateIds.clear();
    const latest = view.candidates?.[view.candidates.length - 1];
    if (latest) state.activeCandidateId = latest.candidate.id;
    renderView();
    setNotice("Candidate 已进入版本化 Workspace；正在读取权威结果。", "success");
    schedulePoll();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function refreshBuild({ announce }) {
  if (!state.buildId || !state.projectId) return;
  const sequence = ++state.requestSequence;
  if (announce) setNotice("正在刷新 Candidate 权威状态。", "neutral");
  try {
    const view = await requestJson(
      `/api/v5/studio/builds/${encodeURIComponent(state.buildId)}?project_id=${encodeURIComponent(state.projectId)}`,
    );
    if (sequence !== state.requestSequence) return;
    state.view = view;
    renderView();
    schedulePoll();
  } catch (error) {
    if (sequence === state.requestSequence) showError(error);
  }
}

function schedulePoll() {
  window.clearTimeout(state.pollTimer);
  const active = state.view?.candidates?.some(item =>
    ["queued", "building"].includes(item.candidate.status),
  );
  if (active) {
    state.pollTimer = window.setTimeout(() => {
      void refreshBuild({ announce: false });
    }, 1200);
  }
}

function renderView() {
  const view = state.view;
  if (!view) return renderEmpty();
  document.querySelector("#studio-build-base").textContent = view.build.base_fingerprint
    ? `Base ${String(view.build.base_fingerprint).slice(0, 12)}`
    : "Base 正在固定";
  const appMode = document.querySelector("#studio-build-app-mode");
  if (appMode && view.build.app_mode) {
    appMode.value = view.build.app_mode;
    appMode.disabled = true;
  }
  document.querySelector("#studio-candidate-state").textContent = view.candidates.length
    ? `${view.candidates.length} 个 Candidate · Dify 写入 0`
    : "还没有 Candidate";
  const visible = selectedCandidate(view, state.activeCandidateId);
  state.activeCandidateId = visible?.candidate?.id || "";
  updateBlueprintLink();
  renderCandidateTabs();
  renderCandidate(visible);
  renderComparison();
  renderContextActionState();
}

function updateBlueprintLink() {
  const link = document.querySelector("#studio-build-blueprints");
  if (!link) return;
  const params = new URLSearchParams({
    studio: "blueprints",
    app_mode: state.view?.build?.app_mode || identity.appMode,
  });
  if (state.buildId) params.set("build_id", state.buildId);
  const selectedNodes = authoritativeSelection();
  if (state.activeCandidateId && selectedNodes.length) {
    params.set("candidate_id", state.activeCandidateId);
    params.set("node_ids", selectedNodes.join(","));
  }
  link.href = `${basePath || ""}/?${params.toString()}`;
  link.textContent = state.activeCandidateId && selectedNodes.length
    ? "浏览 / 保存 Blueprint"
    : "浏览 Blueprints";
  updateScenarioLink();
}

function updateScenarioLink() {
  const link = document.querySelector("#studio-build-scenarios");
  if (!link) return;
  const candidateIds = (state.view?.candidates || [])
    .filter(item => item?.candidate?.status === "valid" && item.reconstructable)
    .map(item => item.candidate.id)
    .slice(0, 20);
  const params = new URLSearchParams({ studio: "scenarios" });
  if (state.buildId) params.set("build_id", state.buildId);
  if (candidateIds.length) params.set("candidate_ids", candidateIds.join(","));
  if (identity.repairProposalId) {
    params.set("repair_proposal_id", identity.repairProposalId);
    params.set("repair_proposal_version", String(identity.repairProposalVersion || 1));
  }
  link.href = `${basePath || ""}/?${params.toString()}`;
  const available = Boolean(state.buildId && candidateIds.length);
  link.setAttribute("aria-disabled", String(!available));
  link.tabIndex = available ? 0 : -1;
  link.title = available ? "在隔离 Preview 中比较有效 Candidate" : "先生成至少一个有效 Candidate";
}

function renderEmpty() {
  state.view = state.view || { candidates: [], comparison: {} };
  renderCandidateTabs();
  renderCandidate(null);
  renderComparison();
}

function renderCandidateTabs() {
  const container = document.querySelector("#studio-candidate-tabs");
  container.replaceChildren();
  const candidates = state.view?.candidates || [];
  const synthMode = document.querySelector("#studio-build-mode")?.value === "synthesize";
  for (const item of candidates) {
    const wrapper = document.createElement("div");
    wrapper.className = "studio-candidate-tab-wrap";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "studio-candidate-tab";
    button.role = "tab";
    button.dataset.status = item.candidate.status;
    button.setAttribute("aria-selected", String(item.candidate.id === state.activeCandidateId));
    button.tabIndex = item.candidate.id === state.activeCandidateId ? 0 : -1;
    const title = document.createElement("strong");
    title.textContent = item.candidate.label;
    const status = document.createElement("small");
    status.textContent = candidateStatus(item.candidate.status);
    button.append(title, status);
    button.addEventListener("click", () => {
      state.activeCandidateId = item.candidate.id;
      renderView();
    });
    button.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = candidates.findIndex(candidate => candidate.candidate.id === item.candidate.id);
      const next = event.key === "Home"
        ? 0
        : event.key === "End"
          ? candidates.length - 1
          : (current + (event.key === "ArrowRight" ? 1 : -1) + candidates.length) % candidates.length;
      state.activeCandidateId = candidates[next].candidate.id;
      renderView();
      container.querySelector(".studio-candidate-tab[aria-selected='true']")?.focus();
    });
    wrapper.append(button);
    if (synthMode && canSelectCandidate(item)) {
      const label = document.createElement("label");
      label.className = "studio-candidate-source";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.sourceCandidateIds.has(item.candidate.id);
      checkbox.setAttribute("aria-label", `将${item.candidate.label}作为综合来源`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked && state.sourceCandidateIds.size >= 3) {
          checkbox.checked = false;
          setNotice("最多选择三个来源 Candidate。", "danger");
          return;
        }
        if (checkbox.checked) state.sourceCandidateIds.add(item.candidate.id);
        else state.sourceCandidateIds.delete(item.candidate.id);
      });
      label.append(checkbox, document.createTextNode(" 综合来源"));
      wrapper.append(label);
    }
    container.append(wrapper);
  }
}

function renderCandidate(item) {
  const selectButton = document.querySelector("#studio-select-candidate");
  const cancelButton = document.querySelector("#studio-cancel-candidate");
  const resumeButton = document.querySelector("#studio-resume-candidate");
  if (!item) {
    document.querySelector("#studio-business-preview-title").textContent = "选择一个 Candidate 查看";
    document.querySelector("#studio-business-summary").textContent = "候选生成后，这里会用业务语言说明变化。";
    selectButton.disabled = true;
    cancelButton.disabled = true;
    resumeButton.disabled = true;
    replaceList("#studio-goal-plan", []);
    replaceList("#studio-changed-path", []);
    document.querySelector("#studio-validation-card").textContent = "等待 Candidate";
    document.querySelector("#studio-risk-card").textContent = "等待评估";
    document.querySelector("#studio-build-timeline").replaceChildren();
    document.querySelector("#studio-technical-detail").textContent = "{}";
    renderLayout(null);
    return;
  }
  document.querySelector("#studio-business-preview-title").textContent = item.candidate.label;
  document.querySelector("#studio-business-summary").textContent = item.business_summary;
  selectButton.disabled = !canSelectCandidate(item)
    || state.view?.build?.selected_candidate_id === item.candidate.id;
  selectButton.textContent = state.view?.build?.selected_candidate_id === item.candidate.id
    ? "已选择此方案"
    : "选择此方案";
  cancelButton.disabled = !["queued", "building", "waiting_input", "interrupted"].includes(item.candidate.status);
  resumeButton.disabled = !["waiting_input", "interrupted"].includes(item.candidate.status);
  resumeButton.textContent = item.candidate.status === "waiting_input"
    ? "补充信息并恢复"
    : "恢复当前 Candidate";
  const steps = item.goal_plan?.steps || [];
  replaceList("#studio-goal-plan", steps.map(step => `${step.status} · ${step.description}`));
  replaceList("#studio-changed-path", item.changed_path || []);
  document.querySelector("#studio-validation-card").textContent = item.validation?.ok === true
    ? `通过 · ${item.validation?.issues?.length || 0} 个问题 · 可重建 ${item.reconstructable ? "是" : "否"}`
    : `${candidateStatus(item.candidate.status)} · ${item.validation?.issues?.length || 0} 个问题`;
  document.querySelector("#studio-risk-card").textContent = [
    `风险：${item.risk?.risk || "待评估"}`,
    `副作用：${item.side_effects?.highest_risk || "待评估"}`,
  ].join(" · ");
  renderTimeline(item.timeline || []);
  document.querySelector("#studio-technical-detail").textContent = JSON.stringify(
    item.technical_detail || {}, null, 2,
  );
  renderLayout(item.layout_preview);
}

function renderComparison() {
  const root = document.querySelector("#studio-comparison");
  root.replaceChildren();
  const rows = comparisonRows(state.view);
  if (!rows.length) {
    root.textContent = "生成至少两个 Candidate 后，可在这里比较业务行为、节点、资源、副作用、成本输入与校验。";
    return;
  }
  root.style.setProperty("--candidate-count", String(state.view.candidates.length));
  for (const row of rows) {
    const element = document.createElement("div");
    element.className = "studio-comparison-row";
    const heading = document.createElement("strong");
    heading.textContent = row.label;
    element.append(heading);
    for (const cell of row.values) {
      const value = document.createElement("div");
      value.className = "studio-comparison-cell";
      value.setAttribute("aria-label", `${cell.label}：${row.label}`);
      value.textContent = conciseValue(cell.value);
      element.append(value);
    }
    root.append(element);
  }
}

function renderLayout(layout) {
  const root = document.querySelector("#studio-layout-preview");
  root.replaceChildren();
  const presentation = layoutPresentation(layout);
  if (!presentation.nodes.length) {
    root.textContent = "配置型应用或尚未初始化的 Candidate 没有 Graph 布局预览。";
    return;
  }
  const surface = document.createElement("div");
  surface.className = "studio-layout-surface";
  surface.style.width = `${Math.max(100, presentation.width)}px`;
  surface.style.height = `${Math.max(260, presentation.height)}px`;
  root.append(surface);
  for (const node of presentation.nodes) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "studio-layout-node";
    element.dataset.nodeId = node.id;
    element.dataset.changed = String(Boolean(node.changed));
    element.style.left = `${node.x}px`;
    element.style.top = `${node.y}px`;
    element.setAttribute("aria-label", `${node.title || node.type}，${node.changed ? "已变化" : "位置保留"}`);
    const title = document.createElement("strong");
    title.textContent = node.title || node.type;
    const type = document.createElement("small");
    type.textContent = `${node.type} · ${node.preserved ? "原位置" : "预览位置"}`;
    element.append(title, type);
    surface.append(element);
  }
}

function focusLayout(changesOnly) {
  const root = document.querySelector("#studio-layout-preview");
  const selector = changesOnly
    ? ".studio-layout-node[data-changed='true']"
    : ".studio-layout-node";
  const target = root.querySelector(selector);
  if (!target) {
    setNotice(changesOnly ? "当前 Candidate 没有可聚焦的变化节点。" : "没有布局节点。", "neutral");
    return;
  }
  target.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  target.focus({ preventScroll: true });
}

function renderTimeline(events) {
  const list = document.querySelector("#studio-build-timeline");
  list.replaceChildren();
  for (const event of events.slice(-40)) {
    const item = document.createElement("li");
    item.textContent = `${event.phase || ""} · ${event.message || event.type}`;
    list.append(item);
  }
  if (!events.length) {
    const item = document.createElement("li");
    item.textContent = "等待 Runtime 事件。";
    list.append(item);
  }
}

function renderContextActionState() {
  const item = selectedCandidate(state.view, state.activeCandidateId);
  const selectedNodeIds = authoritativeSelection();
  const enabledMap = contextCommandsEnabled(item, selectedNodeIds);
  document.querySelectorAll("[data-context-command]").forEach(button => {
    button.disabled = !enabledMap[button.dataset.contextCommand];
  });
  if (state.canvasContext) {
    setSelection(state.canvasContext.selected_node_ids.length
      ? `Dify 已选择：${state.canvasContext.selected_node_ids.join("、")}`
      : "Dify 画布已连接，当前没有选中节点。");
  } else if (state.view?.selected_context?.domain === "config") {
    setSelection("配置型应用不使用画布上下文。", false);
  }
}

async function runContextCommand(command) {
  const item = selectedCandidate(state.view, state.activeCandidateId);
  if (!item || state.busy) return;
  setBusy(true);
  try {
    const result = await requestJson(
      `/api/v5/studio/builds/${encodeURIComponent(state.buildId)}/context`,
      {
        method: "POST",
        body: {
          project_id: state.projectId,
          candidate_id: item.candidate.id,
          command,
          selected_node_ids: authoritativeSelection(),
        },
      },
    );
    renderContextResult(result);
    if (result.kind === "candidate_started") {
      setNotice("已从原始 Base 启动一个新的安全兜底 Candidate。", "success");
      await refreshBuild({ announce: false });
    }
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function renderContextResult(result) {
  const root = document.querySelector("#studio-context-result");
  root.replaceChildren();
  const summary = document.createElement("p");
  summary.textContent = result?.summary || "上下文命令已完成。";
  root.append(summary);
  for (const item of result?.items || []) {
    const card = document.createElement("article");
    card.textContent = conciseValue(item);
    root.append(card);
  }
}

async function selectVisibleCandidate() {
  const item = selectedCandidate(state.view, state.activeCandidateId);
  if (!item || !canSelectCandidate(item)) return;
  setBusy(true);
  try {
    state.view = await requestJson(
      `/api/v5/studio/builds/${encodeURIComponent(state.buildId)}/select`,
      {
        method: "POST",
        body: { project_id: state.projectId, candidate_id: item.candidate.id },
      },
    );
    renderView();
    setNotice("已选择 Candidate；Dify 仍未发生写入。", "success");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function cancelVisibleCandidate() {
  const item = selectedCandidate(state.view, state.activeCandidateId);
  if (!item) return;
  setBusy(true);
  try {
    state.view = await requestJson(
      `/api/v5/studio/builds/${encodeURIComponent(state.buildId)}/cancel`,
      {
        method: "POST",
        body: { project_id: state.projectId, candidate_id: item.candidate.id },
      },
    );
    renderView();
    setNotice("Candidate 已取消；已持久化版本保留，未重放任何副作用。", "success");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function resumeVisibleCandidate() {
  const item = selectedCandidate(state.view, state.activeCandidateId);
  if (!item || !["waiting_input", "interrupted"].includes(item.candidate.status)) return;
  const message = document.querySelector("#studio-build-input").value.trim();
  if (item.candidate.status === "waiting_input" && !message) {
    setNotice("请在目标输入框补充 Builder 请求的信息后再恢复。", "danger");
    document.querySelector("#studio-build-input").focus();
    return;
  }
  setBusy(true);
  try {
    state.view = await requestJson(
      `/api/v5/studio/builds/${encodeURIComponent(state.buildId)}/resume`,
      {
        method: "POST",
        body: {
          project_id: state.projectId,
          candidate_id: item.candidate.id,
          message: message || null,
        },
      },
    );
    document.querySelector("#studio-build-input").value = "";
    renderView();
    setNotice("Candidate 已显式恢复；不会重放重启前的外部副作用。", "success");
    schedulePoll();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function authoritativeSelection() {
  if (state.canvasContext?.selected_node_ids) {
    return state.canvasContext.selected_node_ids;
  }
  return state.view?.selected_context?.selected_node_ids || [];
}

function replaceList(selector, items) {
  const root = document.querySelector(selector);
  root.replaceChildren();
  for (const value of items) {
    const item = document.createElement("li");
    item.textContent = value;
    root.append(item);
  }
  if (!items.length) {
    const item = document.createElement("li");
    item.textContent = "暂无";
    root.append(item);
  }
}

function setBusy(busy) {
  state.busy = busy;
  document.querySelector("#studio-build-submit").disabled = busy;
  document.querySelector("#studio-build-refresh").disabled = busy;
}

function setNotice(message, tone = "neutral") {
  const notice = document.querySelector("#studio-build-notice");
  notice.textContent = message;
  notice.dataset.tone = tone;
}

function setSelection(message, danger = false) {
  document.querySelector("#studio-selection-summary").textContent = message;
  if (danger) setNotice(message, "danger");
}

function setConnection(message, tone) {
  const element = document.querySelector("#studio-connection");
  element.textContent = message;
  element.dataset.tone = tone;
}

function showError(error) {
  const presentation = error?.presentation || {
    title: "Build Studio 请求失败",
    message: error?.message || "发生了未预期的错误。",
  };
  setNotice(`${presentation.title}：${presentation.message}`, "danger");
  setConnection(presentation.kind === "offline" ? "Studio 离线" : "需要处理", "danger");
}

async function requestJson(path, options = {}) {
  const headers = { Accept: "application/json" };
  if (state.token && options.authenticated !== false) {
    headers.Authorization = `Bearer ${state.token}`;
  }
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
  try {
    response = await fetch(apiUrl(path), init);
  } catch (_error) {
    const error = new Error("网络连接失败。");
    error.presentation = classifyStudioError(503, {
      error: {
        code: "STUDIO_NETWORK_OFFLINE",
        message: "无法连接 Chat2Dify，请检查网络或服务状态。",
        retryable: true,
      },
    });
    throw error;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
    error.code = payload?.error?.code;
    error.presentation = classifyStudioError(response.status, payload);
    throw error;
  }
  return payload;
}

function createNonce() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error("浏览器不支持安全随机数，无法建立 Studio 会话。");
  }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(24));
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
}

function parentOrigin() {
  try {
    return document.referrer ? new URL(document.referrer).origin : window.location.origin;
  } catch (_error) {
    return window.location.origin;
  }
}

function apiUrl(path) {
  return `${basePath}${path.startsWith("/") ? path : `/${path}`}`;
}

function normalizeBasePath(value) {
  const normalized = String(value || "").trim();
  if (!normalized || normalized === "/") return "";
  return `${normalized.startsWith("/") ? "" : "/"}${normalized}`.replace(/\/+$/, "");
}
