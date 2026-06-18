const state = {
  context: {
    timeout_seconds: 120,
    recent_apps: {},
  },
  pendingAction: null,
  pendingDraft: "",
  activeTask: null,
  pollTimer: null,
};

const els = {
  healthStatus: document.querySelector("#health-status"),
  refreshHealth: document.querySelector("#refresh-health"),
  chatLog: document.querySelector("#chat-log"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  chatSubmit: document.querySelector("#chat-submit"),
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  addAssistantMessage(
    "告诉我你想创建、修改或测试运行哪类应用。所有信息都可以直接写在这里，比如：创建一个 Agent，名字叫售后助手，帮我分析投诉。"
  );
  refreshHealth();
});

function bindEvents() {
  els.refreshHealth.addEventListener("click", refreshHealth);
  els.chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await handleUserMessage();
  });
}

async function refreshHealth() {
  setHealth("Checking", "muted");
  try {
    const data = await requestJson("/health");
    const model = data.default_model || {};
    setHealth(`${data.status} · ${model.name || "model"}`, "ok");
  } catch (error) {
    setHealth("Error", "error");
    addAssistantMessage(error.message || "Health check failed.");
  }
}

async function handleUserMessage() {
  const text = els.chatInput.value.trim();
  if (!text) {
    return;
  }
  els.chatInput.value = "";
  addUserMessage(text);

  if (isCancelText(text)) {
    state.pendingAction = null;
    state.pendingDraft = "";
    addAssistantMessage("已取消当前待确认操作。");
    return;
  }

  if (isConfirmText(text) && state.pendingAction) {
    await executePendingAction();
    return;
  }

  const planningText = state.pendingDraft ? `${state.pendingDraft}\n${text}` : text;
  await planMessage(planningText, text);
}

async function planMessage(planningText, latestText) {
  setComposerBusy(true);
  addAssistantMessage("我先整理成一个可确认的操作。", { transient: true });
  try {
    const data = await requestJson("/api/assistant/plan", {
        method: "POST",
        body: {
          message: planningText,
          context: assistantContext(),
        },
      });
    removeTransientMessages();
    if (data.status === "pending_action" && data.action) {
      state.pendingAction = data.action;
      state.pendingDraft = "";
      rememberActionContext(data.action);
      addActionCard(data.action);
      return;
    }
    state.pendingAction = null;
    state.pendingDraft = planningText || latestText;
    addAssistantMessage(needsInputText(data));
  } catch (error) {
    removeTransientMessages();
    addAssistantMessage(errorMessage(error));
  } finally {
    setComposerBusy(false);
  }
}

async function executePendingAction() {
  const action = state.pendingAction;
  if (!action) {
    addAssistantMessage("当前没有待确认操作。");
    return;
  }
  if (state.activeTask) {
    addAssistantMessage("已有任务正在执行，等它结束后再确认新的操作。");
    return;
  }
  state.pendingAction = null;
  state.pendingDraft = "";
  setComposerBusy(true);
  const taskMessage = addAssistantMessage(`已确认，正在提交：${action.summary}`);
  try {
    const record = await requestJson("/api/assistant/execute", {
      method: "POST",
      body: { action },
    });
    state.activeTask = {
      task_id: record.task_id,
      operation: record.operation || action.operation,
      action,
      messageElement: taskMessage,
    };
    renderTaskStatus(record, taskMessage);
    await pollTask();
  } catch (error) {
    addAssistantMessage(errorMessage(error));
    setComposerBusy(false);
  }
}

async function pollTask() {
  window.clearTimeout(state.pollTimer);
  const task = state.activeTask;
  if (!task?.task_id) {
    setComposerBusy(false);
    return;
  }
  try {
    const record = await requestJson(`/api/tasks/${encodeURIComponent(task.task_id)}`);
    renderTaskStatus(record, task.messageElement);
    if (isTerminalStatus(record.status)) {
      const active = state.activeTask;
      state.activeTask = null;
      setComposerBusy(false);
      if (record.status === "succeeded") {
        updateContextFromResult(record.result || {}, active.action);
        addResultMessage(record.result || {}, active.action);
        if (active.action?.operation === "workflow.modify.draft") {
          await prepareApplyActionFromPreview();
        }
      } else {
        addAssistantMessage(taskFailureMessage(record));
      }
      return;
    }
    state.pollTimer = window.setTimeout(pollTask, 1200);
  } catch (error) {
    state.activeTask = null;
    setComposerBusy(false);
    addAssistantMessage(errorMessage(error));
  }
}

function addActionCard(action) {
  const wrapper = addAssistantMessage("", { card: true });
  const title = document.createElement("strong");
  title.textContent = action.summary;
  const meta = document.createElement("div");
  meta.className = "action-meta";
  const payload = action.payload || {};
  const mode = action.app_mode || payload.app_mode;
  meta.textContent = [
    `Operation: ${action.operation}`,
    mode ? `Mode: ${mode}` : "",
    payload.app_id ? `App ID: ${payload.app_id}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Payload JSON";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(
    {
      operation: action.operation,
      app_mode: action.app_mode,
      payload: action.payload,
    },
    null,
    2
  );
  details.append(summary, pre);
  const actions = document.createElement("div");
  actions.className = "message-actions";
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.textContent = actionButtonLabel(action);
  confirm.addEventListener("click", executePendingAction);
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "secondary";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => {
    state.pendingAction = null;
    wrapper.remove();
    addAssistantMessage("已取消当前待确认操作。");
  });
  actions.append(confirm, cancel);
  wrapper.replaceChildren(title, meta, details, actions);
  scrollToLatest();
}

function actionButtonLabel(action) {
  if (action?.operation === "workflow.modify.draft") {
    return "Preview";
  }
  if (action?.operation === "workflow.modify.apply") {
    return "Apply";
  }
  if (action?.operation?.endsWith(".run.draft")) {
    return "Run";
  }
  if (action?.operation === "workflow.create") {
    return "Create";
  }
  return "Confirm";
}

async function prepareApplyActionFromPreview() {
  if (!state.context.modify_preview) {
    return;
  }
  setComposerBusy(true);
  try {
    const data = await requestJson("/api/assistant/plan", {
      method: "POST",
      body: {
        message: "应用修改",
        context: assistantContext(),
      },
    });
    if (data.status === "pending_action" && data.action) {
      state.pendingAction = data.action;
      state.pendingDraft = "";
      rememberActionContext(data.action);
      addAssistantMessage("修改预览已生成，尚未写回 Dify。确认下面的 Apply 操作后才会应用到工作流。");
      addActionCard(data.action);
      return;
    }
    addAssistantMessage(needsInputText(data));
  } catch (error) {
    addAssistantMessage(errorMessage(error));
  } finally {
    setComposerBusy(false);
  }
}

function addResultMessage(result, action = null) {
  const wrapper = addAssistantMessage("", { card: true });
  const title = document.createElement("strong");
  title.textContent = resultTitle(result, action);
  const rows = document.createElement("div");
  rows.className = "result-rows";
  for (const [label, value] of resultRows(result)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    const row = document.createElement("div");
    row.textContent = `${label}: ${String(value)}`;
    rows.append(row);
  }
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Raw JSON";
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(result, null, 2);
  details.append(summary, pre);
  wrapper.replaceChildren(title, rows, details);
  scrollToLatest();
}

function resultTitle(result, action = null) {
  if (action?.operation === "workflow.modify.draft") {
    return "修改预览（尚未应用）";
  }
  if (action?.operation === "workflow.modify.apply") {
    return "修改已应用";
  }
  return result.status || result.app_mode || "Result";
}

function resultRows(result) {
  const rows = [
    ["App ID", result.app_id],
    ["Mode", result.app_mode || result.app?.mode || result.plan?.app_mode],
    ["URL", result.workflow_url],
    ["Answer", result.answer],
    ["Conversation", result.conversation_id],
  ];
  if (result.new_hash) {
    rows.splice(3, 0, ["New Hash", result.new_hash]);
  } else if (result.base_hash) {
    rows.splice(3, 0, ["Base Hash", result.base_hash]);
  }
  if (result.base_hash && result.new_hash) {
    rows.splice(4, 0, ["Base Hash", result.base_hash]);
  }
  return rows;
}

function addUserMessage(text) {
  return addMessage("user", text);
}

function addAssistantMessage(text, options = {}) {
  return addMessage("assistant", text, options);
}

function addMessage(role, text, options = {}) {
  const message = document.createElement("article");
  message.className = `message message-${role}`;
  if (options.transient) {
    message.dataset.transient = "true";
  }
  if (options.card) {
    message.classList.add("message-card");
  } else {
    message.textContent = text;
  }
  els.chatLog.append(message);
  scrollToLatest();
  return message;
}

function removeTransientMessages() {
  els.chatLog.querySelectorAll("[data-transient='true']").forEach((item) => item.remove());
}

function renderTaskStatus(record, element) {
  const phase = [record.status, record.phase, record.message].filter(Boolean).join(" · ");
  element.textContent = phase || "任务已提交。";
}

function assistantContext() {
  return state.context;
}

function rememberActionContext(action) {
  const payload = action.payload || {};
  if (payload.app_id) {
    rememberAppReference({
      app_id: payload.app_id,
      app_mode: action.app_mode || payload.app_mode,
      app_name: payload.app_name,
      expected_hash: payload.expected_hash,
    });
  }
  if (payload.inputs) {
    state.context.run_inputs = payload.inputs;
  }
  if (payload.query) {
    state.context.run_query = payload.query;
  }
}

function updateContextFromResult(result, action) {
  rememberActionContext(action || {});
  const reference = appReferenceFromResult(result, action);
  rememberAppReference(reference);
  updateConversationContext(reference);

  if (action?.operation === "workflow.modify.draft") {
    const preview = modifyPreviewFromResult(result, action, reference);
    if (preview) {
      state.context.modify_preview = preview;
    }
  } else if (action?.operation === "workflow.modify.apply") {
    state.context.modify_preview = null;
  }
}

function appReferenceFromResult(result, action) {
  const payload = action?.payload || {};
  return {
    app_id: result.app_id || result.app?.id || payload.app_id,
    app_mode: result.app_mode || result.app?.mode || result.plan?.app_mode || action?.app_mode || payload.app_mode,
    app_name: result.app?.name || result.plan?.name || payload.app_name,
    expected_hash: result.new_hash || result.base_hash || payload.expected_hash,
    conversation_id: result.conversation_id,
    parent_message_id: result.message_id,
  };
}

function rememberAppReference(reference = {}) {
  if (!reference.app_id && !reference.app_mode) {
    return;
  }
  const current = state.context.active_app || {};
  const app = compactPayload({
    ...current,
    ...reference,
  });
  state.context.active_app = app;
  if (app.app_id) {
    state.context.app_id = app.app_id;
  }
  if (app.app_mode) {
    state.context.app_mode = app.app_mode;
    state.context.recent_apps = state.context.recent_apps || {};
    state.context.recent_apps[app.app_mode] = app;
  }
  if (app.app_name) {
    state.context.app_name = app.app_name;
  }
  if (app.expected_hash) {
    state.context.expected_hash = app.expected_hash;
  }
}

function updateConversationContext(reference = {}) {
  if (reference.conversation_id) {
    state.context.conversation_id = reference.conversation_id;
  }
  if (reference.parent_message_id) {
    state.context.parent_message_id = reference.parent_message_id;
  }
  if (state.context.active_app) {
    state.context.active_app = compactPayload({
      ...state.context.active_app,
      conversation_id: state.context.conversation_id,
      parent_message_id: state.context.parent_message_id,
    });
    const mode = state.context.active_app.app_mode;
    if (mode) {
      state.context.recent_apps = state.context.recent_apps || {};
      state.context.recent_apps[mode] = state.context.active_app;
    }
  }
}

function modifyPreviewFromResult(result, action, reference) {
  if (!result.plan && !result.model_config) {
    return null;
  }
  const payload = action.payload || {};
  const preview = {
    app_id: reference.app_id,
    app_mode: reference.app_mode,
    message: payload.message,
    base_hash: result.base_hash,
    allow_destructive: payload.allow_destructive || false,
    dataset_ids: payload.dataset_ids,
    tool_selections: payload.tool_selections,
    agent_selections: payload.agent_selections,
    model_selections: payload.model_selections,
    trigger_selection: payload.trigger_selection,
    planner: payload.planner,
  };
  if (result.plan) {
    preview.plan = result.plan;
  }
  if (result.model_config) {
    preview.configured_model_config = result.model_config;
    preview.configured_model_config_changes = result.changes || [];
  }
  return compactPayload(preview);
}

function needsInputText(data) {
  const labels = {
    app_mode: "应用类型",
    app_id: "App ID",
    inputs: "测试输入",
    query: "测试问题",
    expected_hash: "当前版本 hash",
    modify_preview: "修改预览",
    operation: "操作",
    message: "需求描述",
  };
  const fields = (data.missing_fields || []).map((field) => labels[field] || field).join(", ");
  if (!fields) {
    return data.message || "还需要补充信息。";
  }
  return `${data.message || "还需要补充信息。"}\n还需要：${fields}`;
}

function isConfirmText(text) {
  const value = text.trim().toLowerCase();
  return ["确认", "执行", "提交", "确定", "应用", "应用修改", "确认应用", "confirm", "yes", "ok", "apply"].includes(value);
}

function isCancelText(text) {
  const value = text.trim().toLowerCase();
  return ["取消", "不要", "算了", "cancel", "stop"].includes(value);
}

function isTerminalStatus(status) {
  return ["succeeded", "failed", "cancelled", "interrupted"].includes(status);
}

function taskFailureMessage(record) {
  const detail = record?.error?.detail;
  if (detail?.message) {
    const code = detail.code ? `${detail.code}: ` : "";
    const extras = [];
    if (detail.expected_hash) {
      extras.push(`expected=${detail.expected_hash}`);
    }
    if (detail.current_hash) {
      extras.push(`current=${detail.current_hash}`);
    }
    return `${code}${detail.message}${extras.length ? `\n${extras.join("\n")}` : ""}`;
  }
  if (typeof detail === "string") {
    return detail;
  }
  if (record?.error?.type && record?.error?.detail) {
    return `${record.error.type}: ${record.error.detail}`;
  }
  return record?.message || `任务 ${record?.status || "failed"}`;
}

async function requestJson(path, options = {}) {
  const init = {
    method: options.method || "GET",
    headers: {},
  };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(compactPayload(options.body));
  }
  const response = await fetch(path, init);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(errorMessageFromPayload(data, response.status));
    error.payload = typeof data === "string" ? { error: data, status: response.status } : data;
    throw error;
  }
  return data;
}

function compactPayload(value) {
  if (Array.isArray(value)) {
    return value.map(compactPayload);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === undefined || item === null || item === "") {
      continue;
    }
    result[key] = compactPayload(item);
  }
  return result;
}

function errorMessage(error) {
  if (error?.payload) {
    return errorMessageFromPayload(error.payload, error.payload.status);
  }
  return error?.message || "请求失败。";
}

function errorMessageFromPayload(payload, status) {
  if (typeof payload === "string") {
    return payload || `HTTP ${status}`;
  }
  const detail = payload?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (detail?.message) {
    return detail.message;
  }
  if (payload?.error) {
    return payload.error;
  }
  return `HTTP ${status}`;
}

function setComposerBusy(busy) {
  els.chatSubmit.disabled = busy;
  els.chatInput.disabled = busy;
}

function setHealth(text, tone) {
  els.healthStatus.textContent = text;
  els.healthStatus.className = `status-pill status-${tone || "muted"}`;
}

function scrollToLatest() {
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}
