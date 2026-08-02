import {
  AGENT_EVENT_TYPES,
  CanvasContextChannel,
  EventCursor,
  appModeLabel,
  approvalMatchesVisibleVersion,
  commitBlockReason,
  isAgentWorkbenchSupported,
  isContextNonce,
  parseSse,
  reviewDiffRows,
  resolveAgentAppMode,
  requiresCanvasContext,
  runControlState,
  supportsCanvasContext,
  testPresentation,
  timelinePresentation,
  undoPresentation,
} from "./agent-workbench-core.mjs";

const config = window.CHAT2DIFY_CONFIG || {};
const basePath = normalizeBasePath(config.basePath || "");
const params = new URLSearchParams(window.location.search);
const intent = (params.get("intent") || "").toLowerCase();
const appMode = resolveAgentAppMode(intent, params.get("app_mode"));
const appId = params.get("app_id") || "";
const appName = params.get("app_name") || "";
const embedded = ["1", "true", "yes"].includes(
  (params.get("embed") || "").toLowerCase(),
);
const enabled = isAgentWorkbenchSupported({
  featureEnabled: Boolean(config.agentV4Enabled),
  intent,
  appMode,
  appId,
}) && !(config.studioV5Enabled && params.get("studio") === "build");

const workbenchState = {
  session: null,
  run: null,
  approvals: [],
  canvasContext: null,
  contextChannel: null,
  eventCursor: null,
  eventSource: null,
  reconnectTimer: null,
  reconnectAttempts: 0,
  pollTimer: null,
  contextSyncPromise: Promise.resolve(),
};

const elements = {};

if (enabled) {
  window.CHAT2DIFY_AGENT_WORKBENCH = true;
  document.addEventListener("DOMContentLoaded", () => {
    void bootAgentWorkbench();
  });
}

async function bootAgentWorkbench() {
  bindElements();
  document.querySelector("#legacy-workbench")?.setAttribute("hidden", "");
  elements.root.hidden = false;
  elements.title.textContent = intent === "create"
    ? `新建${appModeLabel(appMode)}`
    : (appName || `修改${appModeLabel(appMode)}`);
  const studioBackLink = document.querySelector("#studio-back-link");
  if (studioBackLink && config.studioV5Enabled) {
    const homeParams = new URLSearchParams();
    if (embedded) {
      homeParams.set("embed", "1");
    }
    const contextNonce = params.get("context_nonce") || "";
    if (isContextNonce(contextNonce)) {
      homeParams.set("context_nonce", contextNonce);
    }
    studioBackLink.href = `?${homeParams.toString()}`;
    studioBackLink.hidden = false;
  }
  bindActions();
  setupCanvasChannel();
  try {
    await restoreOrCreateSession();
    updateComposerAvailability();
  } catch (error) {
    setNotice(errorMessage(error), "danger");
  }
}

function bindElements() {
  Object.assign(elements, {
    root: document.querySelector("#agent-workbench"),
    title: document.querySelector("#agent-title"),
    contextStatus: document.querySelector("#agent-context-status"),
    selection: document.querySelector("#agent-selection"),
    notice: document.querySelector("#agent-notice"),
    timeline: document.querySelector("#agent-timeline"),
    goalPlan: document.querySelector("#agent-goal-plan"),
    diff: document.querySelector("#agent-diff"),
    validation: document.querySelector("#agent-validation"),
    risk: document.querySelector("#agent-risk"),
    approvals: document.querySelector("#agent-approvals"),
    technical: document.querySelector("#agent-technical"),
    testScope: document.querySelector("#agent-test-scope"),
    testInputs: document.querySelector("#agent-test-inputs"),
    testResult: document.querySelector("#agent-test-result"),
    phase: document.querySelector("#agent-phase"),
    form: document.querySelector("#agent-form"),
    input: document.querySelector("#agent-input"),
    submit: document.querySelector("#agent-submit"),
    pause: document.querySelector("#agent-pause"),
    resume: document.querySelector("#agent-resume"),
    undo: document.querySelector("#agent-undo"),
    cancel: document.querySelector("#agent-cancel"),
  });
}

function bindActions() {
  elements.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitGoalOrResume();
  });
  elements.pause.addEventListener("click", () => runAction("pause"));
  elements.resume.addEventListener("click", () => runAction("resume", {}));
  elements.cancel.addEventListener("click", () => runAction("cancel"));
  elements.undo.addEventListener("click", undoVisibleVersion);
}

function setupCanvasChannel() {
  if (!supportsCanvasContext(appMode)) {
    elements.selection.textContent = "使用持久化 Dify 配置";
    setContextStatus("配置应用不使用画布上下文", "ok");
    return;
  }
  if (intent !== "modify") {
    setContextStatus("新建模式不读取现有画布", "ok");
    return;
  }
  if (!requiresCanvasContext({
    appMode,
    intent,
    embedded,
    studioEntry: params.get("studio_entry") || "",
  })) {
    setContextStatus(
      params.get("studio_entry") === "home"
        ? "使用 Dify 已持久化草稿；当前入口不连接画布选择"
        : "使用 URL 应用上下文；未连接画布选择",
      "muted",
    );
    return;
  }
  const nonce = params.get("context_nonce") || "";
  const expectedOrigin = parentOrigin();
  try {
    workbenchState.contextChannel = new CanvasContextChannel({
      expectedOrigin,
      nonce,
      sourceWindow: window.parent,
    });
  } catch (_error) {
    setContextStatus("画布握手参数无效", "error");
    return;
  }
  window.addEventListener("message", (event) => {
    const accepted = workbenchState.contextChannel.accept(event);
    if (!accepted) {
      return;
    }
    workbenchState.canvasContext = accepted.context;
    renderCanvasContext();
    updateComposerAvailability();
    queueCanvasContextSync();
  });
  window.parent.postMessage(
    workbenchState.contextChannel.frameMessage("chat2dify.ready"),
    expectedOrigin,
  );
  setContextStatus("等待 Dify 画布安全握手", "muted");
}

async function restoreOrCreateSession() {
  const storageKey = activeRunStorageKey();
  const requestedRunId = params.get("run_id") || "";
  const storedRunId = sessionStorage.getItem(storageKey);
  for (const candidateRunId of [requestedRunId, storedRunId].filter(Boolean)) {
    try {
      const run = await requestJson(`/api/v4/agent/runs/${encodeURIComponent(candidateRunId)}`);
      const session = await requestJson(
        `/api/v4/agent/sessions/${encodeURIComponent(run.session_id)}`,
      );
      if (intent === "modify" && session.app_id !== appId) {
        throw new Error("Run does not belong to the selected Dify application.");
      }
      workbenchState.run = run;
      workbenchState.session = session;
      sessionStorage.setItem(storageKey, run.id);
      await connectRun(run.id);
      return;
    } catch (_error) {
      if (candidateRunId === storedRunId) {
        sessionStorage.removeItem(storageKey);
      }
    }
  }
  workbenchState.session = await requestJson("/api/v4/agent/sessions", {
    method: "POST",
    body: {
      app_id: intent === "modify" ? appId : undefined,
      app_mode: appMode,
      app_name: appName || undefined,
    },
  });
  setNotice("Workbench 已就绪。描述目标后，Agent 会先生成可审阅的工作区变更。", "neutral");
}

async function submitGoalOrResume() {
  const message = elements.input.value.trim();
  if (!message || !workbenchState.session) {
    return;
  }
  const run = workbenchState.run;
  elements.input.value = "";
  setBusy(true);
  try {
    let nextRun;
    if (run?.phase === "waiting_user") {
      nextRun = await requestJson(`/api/v4/agent/runs/${encodeURIComponent(run.id)}/resume`, {
        method: "POST",
        body: { message },
      });
    } else if (["paused", "interrupted"].includes(run?.phase)) {
      nextRun = await requestJson(`/api/v4/agent/runs/${encodeURIComponent(run.id)}/resume`, {
        method: "POST",
        body: message ? { message } : {},
      });
    } else if (run && !isTerminalRun(run)) {
      throw new Error("当前 Run 仍在执行或等待审批。");
    } else {
      nextRun = await requestJson(
        `/api/v4/agent/sessions/${encodeURIComponent(workbenchState.session.id)}/messages`,
        {
          method: "POST",
          body: {
            message,
            constraints: canvasConstraints(),
          },
        },
      );
    }
    workbenchState.run = nextRun;
    sessionStorage.setItem(activeRunStorageKey(), nextRun.id);
    await connectRun(nextRun.id);
  } catch (error) {
    setNotice(errorMessage(error), "danger");
  } finally {
    setBusy(false);
  }
}

async function connectRun(runId) {
  closeRunConnections();
  elements.timeline.replaceChildren();
  workbenchState.eventCursor = new EventCursor(
    runId,
    Number(sessionStorage.getItem(cursorStorageKey(runId)) || 0),
  );
  try {
    const replay = await fetch(
      apiUrl(`/api/v4/agent/runs/${encodeURIComponent(runId)}/events?follow=false&after_seq=0`),
      { headers: { Accept: "text/event-stream" } },
    );
    if (replay.ok) {
      for (const item of parseSse(await replay.text())) {
        consumeAgentEvent(item.data);
      }
    }
  } catch (_error) {
    setNotice("事件历史暂不可用，已启用状态轮询。", "warning");
  }
  await refreshRun();
  openEventSource();
  schedulePoll();
}

function openEventSource() {
  const run = workbenchState.run;
  if (!run || isTerminalRun(run)) {
    return;
  }
  const after = workbenchState.eventCursor?.sequence || 0;
  const source = new EventSource(
    apiUrl(`/api/v4/agent/runs/${encodeURIComponent(run.id)}/events?after_seq=${after}`),
  );
  workbenchState.eventSource = source;
  for (const eventType of AGENT_EVENT_TYPES) {
    source.addEventListener(eventType, (event) => {
      try {
        consumeAgentEvent(JSON.parse(event.data));
      } catch (_error) {
        setNotice("忽略了一条格式错误的事件；轮询仍在继续。", "warning");
      }
    });
  }
  source.onopen = () => {
    workbenchState.reconnectAttempts = 0;
  };
  source.onerror = () => {
    source.close();
    if (!workbenchState.run || isTerminalRun(workbenchState.run)) {
      return;
    }
    const delay = Math.min(10_000, 500 * (2 ** workbenchState.reconnectAttempts));
    workbenchState.reconnectAttempts += 1;
    window.clearTimeout(workbenchState.reconnectTimer);
    workbenchState.reconnectTimer = window.setTimeout(openEventSource, delay);
  };
}

function consumeAgentEvent(event) {
  if (!workbenchState.eventCursor?.accept(event)) {
    return;
  }
  sessionStorage.setItem(
    cursorStorageKey(event.run_id),
    String(workbenchState.eventCursor.sequence),
  );
  const presentation = timelinePresentation(event);
  const item = document.createElement("li");
  item.className = `timeline-item timeline-${presentation.tone}`;
  item.dataset.eventKey = `${event.run_id}:${event.seq}`;
  const heading = document.createElement("strong");
  heading.textContent = presentation.message;
  const meta = document.createElement("span");
  meta.textContent = `#${event.seq} · ${presentation.phase || presentation.type}`;
  item.append(heading, meta);
  elements.timeline.append(item);
  if (event.type === "goal_plan.created" || event.type === "goal_plan.updated") {
    renderGoalPlan(event.data);
  }
  appendTechnicalEvent(event);
  if (
    event.type === "agent.decision"
    && event.data?.type === "ask_user"
    && event.data.question
  ) {
    setNotice(event.data.question, "warning");
  }
  if (
    event.type === "review.ready"
    || event.type === "approval.required"
    || event.type.startsWith("test.")
    || event.type.startsWith("commit.")
    || event.type.startsWith("agent.")
  ) {
    void refreshRun();
  }
}

async function refreshRun() {
  if (!workbenchState.run) {
    return;
  }
  try {
    const run = await requestJson(`/api/v4/agent/runs/${encodeURIComponent(workbenchState.run.id)}`);
    workbenchState.run = run;
    renderRun(run);
    if (run.review) {
      renderReview(run.review);
    }
    await refreshApprovals();
    if (isTerminalRun(run)) {
      closeRunConnections();
    }
  } catch (error) {
    setNotice(errorMessage(error), "warning");
  }
}

async function refreshApprovals() {
  const run = workbenchState.run;
  if (!run) {
    return;
  }
  try {
    workbenchState.approvals = await requestJson(
      `/api/v4/agent/runs/${encodeURIComponent(run.id)}/approvals`,
    );
    renderApprovals();
  } catch (_error) {
    workbenchState.approvals = [];
    elements.approvals.replaceChildren();
  }
}

function renderRun(run) {
  elements.phase.textContent = `${run.status} · ${run.phase}`;
  renderGoalPlan(run.goal_plan);
  const controls = runControlState(run);
  elements.pause.hidden = !controls.canPause;
  elements.resume.hidden = !controls.canResume;
  elements.undo.hidden = !controls.canUndo;
  elements.cancel.hidden = !controls.canCancel;
  if (run.error?.message) {
    setNotice(`${run.error.code || "AGENT_ERROR"}: ${run.error.message}`, "danger");
  }
  updateComposerAvailability();
}

function renderCanvasContext() {
  const context = workbenchState.canvasContext;
  if (!context) {
    return;
  }
  const selected = [
    `${context.selected_node_ids.length} 个节点`,
    `${context.selected_edge_ids.length} 条连线`,
  ].join(" · ");
  elements.selection.textContent = selected;
  if (context.dirty_state) {
    setContextStatus("画布有未同步变更", "error");
  } else {
    setContextStatus(`画布已同步 · ${context.canvas_draft_hash || "无 Hash"}`, "ok");
  }
}

function renderGoalPlan(plan) {
  elements.goalPlan.replaceChildren();
  for (const step of plan?.steps || []) {
    const item = document.createElement("li");
    item.className = `goal-step goal-${step.status}`;
    const title = document.createElement("strong");
    title.textContent = step.description;
    const status = document.createElement("span");
    status.textContent = step.status;
    item.append(title, status);
    if (step.evidence?.length) {
      const evidence = document.createElement("small");
      evidence.textContent = step.evidence.join("；");
      item.append(evidence);
    }
    elements.goalPlan.append(item);
  }
}

function renderReview(review) {
  elements.diff.replaceChildren();
  for (const change of reviewDiffRows(review)) {
    const row = document.createElement("li");
    row.className = `diff-row diff-${change.type || "changed"}`;
    row.textContent = change.message || change.type || "changed";
    elements.diff.append(row);
  }
  elements.validation.textContent = review.validation?.ok
    ? "确定性校验通过"
    : `校验未通过：${(review.validation?.issues || []).map(item => item.message).join("；")}`;
  elements.risk.textContent = `风险：${review.risk?.risk || "unknown"}${review.risk?.ok === false ? "（需要额外确认）" : ""}`;
  renderTestResult(review);
  elements.technical.textContent = JSON.stringify(review, null, 2);
}

function renderTestResult(review) {
  const presentation = testPresentation(review);
  elements.testScope.textContent = presentation.scope;
  elements.testInputs.textContent = JSON.stringify(presentation.inputs, null, 2);
  elements.testResult.textContent = presentation.result;
}

function renderApprovals() {
  elements.approvals.replaceChildren();
  const run = workbenchState.run;
  if (!run?.head_version_id) {
    return;
  }
  for (const approval of workbenchState.approvals) {
    if (
      approval.action !== "draft_run"
      && !approvalMatchesVisibleVersion(approval, run.head_version_id)
    ) {
      continue;
    }
    const card = document.createElement("article");
    card.className = `approval-card approval-${approval.action}`;
    const title = document.createElement("strong");
    title.textContent = approvalLabel(approval.action);
    const meta = document.createElement("span");
    meta.textContent = `${approval.status} · 版本 ${approval.workspace_version_id}`;
    card.append(title, meta);
    if (approval.status === "pending") {
      let draftOptions = null;
      if (approval.action === "draft_run") {
        const scope = document.createElement("pre");
        scope.className = "test-input-preview";
        scope.textContent = JSON.stringify({
          side_effects: approval.scope?.side_effects,
          input_preview: approval.scope?.input_preview,
          requested_test_runs: approval.scope?.requested_test_runs,
        }, null, 2);
        const count = document.createElement("input");
        count.type = "number";
        count.min = "1";
        count.max = String(approval.scope?.requested_test_runs || 1);
        count.value = String(approval.scope?.requested_test_runs || 1);
        count.setAttribute("aria-label", "批准的 Draft Test 次数");
        const inputs = document.createElement("textarea");
        inputs.rows = 4;
        inputs.value = JSON.stringify(approval.scope?.input_preview?.inputs || {}, null, 2);
        inputs.setAttribute("aria-label", "Draft Test 输入覆盖");
        card.append(scope, count, inputs);
        draftOptions = { count, inputs };
      }
      card.append(
        actionButton(
          "批准",
          () => resolveApproval(approval.id, true, draftOptions),
        ),
        actionButton("拒绝", () => resolveApproval(approval.id, false), "secondary"),
      );
    }
    if (approval.action === "draft_run" && approval.status !== "pending") {
      const remaining = document.createElement("small");
      remaining.textContent = `剩余测试次数：${approval.scope?.remaining_test_runs || 0}`;
      card.append(remaining);
    }
    if (approval.status === "approved" && approval.action === "commit") {
      const reason = commitBlockReason(run, workbenchState.canvasContext);
      const button = actionButton("提交到 Dify", () => commitApprovedVersion(approval));
      button.disabled = Boolean(reason);
      card.append(button);
      if (reason) {
        const warning = document.createElement("small");
        warning.textContent = reason;
        card.append(warning);
      }
    }
    elements.approvals.append(card);
  }
}

async function resolveApproval(approvalId, approved, draftOptions = null) {
  const run = workbenchState.run;
  if (!run) {
    return;
  }
  try {
    let testInputs;
    if (draftOptions?.inputs) {
      try {
        testInputs = JSON.parse(draftOptions.inputs.value || "{}");
      } catch (_error) {
        throw new Error("Draft Test 输入必须是 JSON 对象。");
      }
    }
    await requestJson(
      `/api/v4/agent/runs/${encodeURIComponent(run.id)}/approvals/${encodeURIComponent(approvalId)}`,
      {
        method: "POST",
        body: {
          approved,
          allowed_test_runs: draftOptions?.count
            ? Number(draftOptions.count.value)
            : undefined,
          test_inputs: testInputs,
        },
      },
    );
    await refreshRun();
  } catch (error) {
    setNotice(errorMessage(error), "danger");
  }
}

async function commitApprovedVersion(approval) {
  const run = workbenchState.run;
  if (!run || !approvalMatchesVisibleVersion(approval, run.head_version_id)) {
    setNotice("可见审批与当前工作区版本不一致。", "danger");
    return;
  }
  const reason = commitBlockReason(run, workbenchState.canvasContext);
  if (reason) {
    setNotice(reason, "danger");
    return;
  }
  try {
    await requestJson(`/api/v4/agent/runs/${encodeURIComponent(run.id)}/commit`, {
      method: "POST",
      body: {
        workspace_version_id: run.head_version_id,
        approval_id: approval.id,
      },
    });
    await refreshRun();
  } catch (error) {
    setNotice(errorMessage(error), "danger");
  }
}

async function undoVisibleVersion() {
  const run = workbenchState.run;
  if (!run?.head_version_id) {
    return;
  }
  try {
    const result = await requestJson(`/api/v4/agent/runs/${encodeURIComponent(run.id)}/undo`, {
      method: "POST",
      body: { workspace_version_id: run.head_version_id },
    });
    const presentation = undoPresentation(result);
    workbenchState.run = presentation.run;
    sessionStorage.setItem(activeRunStorageKey(), presentation.run.id);
    setNotice(presentation.message, "success");
    await connectRun(presentation.run.id);
  } catch (error) {
    setNotice(errorMessage(error), "danger");
  }
}

async function runAction(action, body = undefined) {
  const run = workbenchState.run;
  if (!run) {
    return;
  }
  try {
    workbenchState.run = await requestJson(
      `/api/v4/agent/runs/${encodeURIComponent(run.id)}/${action}`,
      { method: "POST", body },
    );
    await refreshRun();
    if (action === "resume") {
      await connectRun(run.id);
    }
  } catch (error) {
    setNotice(errorMessage(error), "danger");
  }
}

function queueCanvasContextSync() {
  const run = workbenchState.run;
  const context = workbenchState.canvasContext;
  if (!run || !context || isTerminalRun(run) || run.phase === "committing") {
    return;
  }
  workbenchState.contextSyncPromise = workbenchState.contextSyncPromise
    .catch(() => undefined)
    .then(() => requestJson(`/api/v4/agent/runs/${encodeURIComponent(run.id)}/context`, {
      method: "POST",
      body: context,
    }))
    .catch((error) => {
      if (!String(errorMessage(error)).includes("stale")) {
        setNotice(errorMessage(error), "warning");
      }
    });
}

function canvasConstraints() {
  const context = workbenchState.canvasContext;
  if (!supportsCanvasContext(appMode) || intent !== "modify" || !context) {
    return {};
  }
  return {
    selected_node_ids: context.selected_node_ids,
    selected_edge_ids: context.selected_edge_ids,
    viewport: context.viewport,
    current_panel: context.current_panel,
    canvas_draft_hash: context.canvas_draft_hash,
    dirty_state: context.dirty_state,
    canvas_context_revision: context.revision,
  };
}

function updateComposerAvailability() {
  const needsHandshake = requiresCanvasContext({
    appMode,
    intent,
    embedded,
    studioEntry: params.get("studio_entry") || "",
  });
  const hasContext = Boolean(workbenchState.canvasContext);
  const run = workbenchState.run;
  const blockedByRun = run && !isTerminalRun(run)
    && !["waiting_user", "paused", "interrupted"].includes(run.phase);
  const disabled = (needsHandshake && !hasContext) || Boolean(blockedByRun);
  elements.input.disabled = disabled;
  elements.submit.disabled = disabled;
  elements.input.placeholder = run?.phase === "waiting_user"
    ? "补充 Agent 请求的信息，并继续同一个 Run"
    : "描述修改目标，例如：把选中的 LLM Prompt 改得更专业，并增加 JSON 输出约束";
}

function schedulePoll() {
  window.clearTimeout(workbenchState.pollTimer);
  if (!workbenchState.run || isTerminalRun(workbenchState.run)) {
    return;
  }
  workbenchState.pollTimer = window.setTimeout(async () => {
    await refreshRun();
    schedulePoll();
  }, 2500);
}

function closeRunConnections() {
  workbenchState.eventSource?.close();
  workbenchState.eventSource = null;
  window.clearTimeout(workbenchState.reconnectTimer);
  window.clearTimeout(workbenchState.pollTimer);
}

function appendTechnicalEvent(event) {
  const existing = elements.technical.textContent.trim();
  const entries = existing ? JSON.parse(existing) : [];
  const normalized = Array.isArray(entries) ? entries : [entries];
  normalized.push(event);
  elements.technical.textContent = JSON.stringify(normalized.slice(-100), null, 2);
}

function actionButton(label, handler, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.className = className;
  button.addEventListener("click", handler);
  return button;
}

function approvalLabel(action) {
  return {
    commit: "提交审批",
    destructive_change: "破坏性变更审批",
    draft_run: "草稿运行审批",
  }[action] || action;
}

function setBusy(busy) {
  elements.submit.disabled = busy;
  if (!busy) {
    updateComposerAvailability();
  }
}

function setNotice(message, tone) {
  elements.notice.textContent = message || "";
  elements.notice.dataset.tone = tone || "neutral";
}

function setContextStatus(message, tone) {
  elements.contextStatus.textContent = message;
  elements.contextStatus.className = `status-pill status-${tone || "muted"}`;
}

function isTerminalRun(run) {
  return ["completed", "conflicted", "cancelled", "failed"].includes(run?.phase);
}

function activeRunStorageKey() {
  return `chat2dify.agent.v4.active.${intent}.${appId || appMode}`;
}

function cursorStorageKey(runId) {
  return `chat2dify.agent.v4.cursor.${runId}`;
}

function parentOrigin() {
  try {
    return document.referrer ? new URL(document.referrer).origin : window.location.origin;
  } catch (_error) {
    return window.location.origin;
  }
}

async function requestJson(path, options = {}) {
  const init = {
    method: options.method || "GET",
    headers: { Accept: "application/json" },
  };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(apiUrl(path), init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data?.detail || data;
    const error = new Error(detail?.message || `HTTP ${response.status}`);
    error.code = detail?.code;
    throw error;
  }
  return data;
}

function errorMessage(error) {
  return `${error?.code ? `${error.code}: ` : ""}${error?.message || "请求失败。"}`;
}

function apiUrl(path) {
  return `${basePath}${path.startsWith("/") ? path : `/${path}`}`;
}

function normalizeBasePath(value) {
  const normalized = String(value || "").trim();
  if (!normalized || normalized === "/") {
    return "";
  }
  return `${normalized.startsWith("/") ? "" : "/"}${normalized}`.replace(/\/+$/, "");
}
