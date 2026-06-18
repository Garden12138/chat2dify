const state = {
  context: {
    timeout_seconds: 120,
    recent_apps: {},
  },
  pendingAction: null,
  pendingDraft: "",
  activeTask: null,
  pollTimer: null,
  language: "zh",
};

const UI_TEXT = {
  zh: {
    checking: "检查中",
    healthError: "异常",
    healthFailed: "健康检查失败。",
    loading: "加载中",
    plannerUnavailable: "Planner 不可用",
    noPlannerKey: "未配置 Planner Key",
    defaultSuffix: "（默认）",
    planning: "我先整理成一个可确认的操作。",
    noPendingAction: "当前没有待确认操作。",
    taskRunning: "已有任务正在执行，等它结束后再确认新的操作。",
    confirmedSubmitting: "已确认，正在提交",
    cancelled: "已取消当前待确认操作。",
    operation: "操作",
    mode: "类型",
    appId: "应用 ID",
    payloadJson: "参数详情（JSON）",
    rawJson: "原始 JSON",
    cancel: "取消",
    create: "创建",
    preview: "生成预览",
    apply: "应用",
    run: "运行",
    confirm: "确认",
    previewReady: "修改预览已生成，尚未写回 Dify。确认下面的“应用”操作后才会真正写回。",
    taskSubmitted: "任务已提交。",
    result: "结果",
    modifyPreviewTitle: "修改预览（尚未应用）",
    modifyAppliedTitle: "修改已应用",
    url: "链接",
    answer: "回答",
    conversation: "会话",
    newHash: "新版本哈希",
    baseHash: "基准哈希",
    needsMore: "还需要补充信息。",
    stillNeeds: "还需要",
    requestFailed: "请求失败。",
  },
  en: {
    checking: "Checking",
    healthError: "Error",
    healthFailed: "Health check failed.",
    loading: "Loading",
    plannerUnavailable: "Planner unavailable",
    noPlannerKey: "No planner key",
    defaultSuffix: " (default)",
    planning: "I will turn that into a confirmable action first.",
    noPendingAction: "There is no pending action to confirm.",
    taskRunning: "A task is already running. Please wait for it to finish before confirming another action.",
    confirmedSubmitting: "Confirmed, submitting",
    cancelled: "Cancelled the pending action.",
    operation: "Operation",
    mode: "Mode",
    appId: "App ID",
    payloadJson: "Payload JSON",
    rawJson: "Raw JSON",
    cancel: "Cancel",
    create: "Create",
    preview: "Preview",
    apply: "Apply",
    run: "Run",
    confirm: "Confirm",
    previewReady: "The modification preview is ready and has not been written back to Dify. Confirm the Apply action below to apply it.",
    taskSubmitted: "Task submitted.",
    result: "Result",
    modifyPreviewTitle: "Modification preview (not applied)",
    modifyAppliedTitle: "Modification applied",
    url: "URL",
    answer: "Answer",
    conversation: "Conversation",
    newHash: "New Hash",
    baseHash: "Base Hash",
    needsMore: "More information is needed.",
    stillNeeds: "Still needs",
    requestFailed: "Request failed.",
  },
};

const APP_MODE_LABELS = {
  zh: {
    workflow: "工作流",
    "advanced-chat": "对话流",
    chat: "聊天助手",
    "agent-chat": "智能体",
    completion: "文本生成应用",
  },
  en: {
    workflow: "Workflow",
    "advanced-chat": "Chatflow",
    chat: "Chatbot",
    "agent-chat": "Agent",
    completion: "Text generation app",
  },
};

const OPERATION_LABELS = {
  zh: {
    "workflow.create": "创建应用",
    "workflow.modify.draft": "生成修改预览",
    "workflow.modify.apply": "应用修改",
    "workflow.run.draft": "测试运行工作流",
    "chatflow.run.draft": "测试运行对话流",
    "chatbot.run.draft": "测试运行聊天助手",
    "completion.run.draft": "测试运行文本生成应用",
    "agent.run.draft": "测试运行智能体",
  },
  en: {
    "workflow.create": "Create app",
    "workflow.modify.draft": "Preview modification",
    "workflow.modify.apply": "Apply modification",
    "workflow.run.draft": "Run workflow draft",
    "chatflow.run.draft": "Run Chatflow",
    "chatbot.run.draft": "Run Chatbot",
    "completion.run.draft": "Run text generation app",
    "agent.run.draft": "Run Agent",
  },
};

const STATUS_LABELS = {
  zh: {
    pending: "等待中",
    queued: "排队中",
    running: "运行中",
    succeeded: "成功",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "已中断",
  },
  en: {
    pending: "pending",
    queued: "queued",
    running: "running",
    succeeded: "succeeded",
    completed: "completed",
    failed: "failed",
    cancelled: "cancelled",
    interrupted: "interrupted",
  },
};

const els = {
  healthStatus: document.querySelector("#health-status"),
  refreshHealth: document.querySelector("#refresh-health"),
  plannerSelect: document.querySelector("#planner-select"),
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
  refreshHeader();
});

function bindEvents() {
  els.refreshHealth.addEventListener("click", refreshHeader);
  els.plannerSelect.addEventListener("change", updatePlannerSelection);
  els.chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await handleUserMessage();
  });
}

function refreshHeader() {
  refreshHealth();
  refreshPlannerProviders();
}

async function refreshHealth() {
  setHealth(t("checking"), "muted");
  try {
    const data = await requestJson("/health");
    setHealth(data.status || "ok", "ok");
  } catch (error) {
    setHealth(t("healthError"), "error");
    addAssistantMessage(error.message || t("healthFailed"));
  }
}

async function refreshPlannerProviders() {
  setPlannerOptions([{ label: t("loading"), disabled: true }]);
  try {
    const data = await requestJson("/api/planner/providers");
    renderPlannerProviders(data);
  } catch (error) {
    state.context.planner = null;
    setPlannerOptions([{ label: t("plannerUnavailable"), disabled: true }]);
  }
}

function renderPlannerProviders(data) {
  const defaultProvider = data.default_provider || "";
  const defaultModel = data.default_model || "";
  const options = [];
  for (const provider of data.providers || []) {
    if (!provider.configured) {
      continue;
    }
    for (const model of provider.models || []) {
      const isDefault = provider.id === defaultProvider && model.id === defaultModel;
      options.push({
        provider: provider.id,
        model: model.id,
        label: plannerOptionLabel(provider, model, isDefault),
      });
    }
  }
  if (!options.length) {
    state.context.planner = null;
    setPlannerOptions([{ label: t("noPlannerKey"), disabled: true }]);
    return;
  }

  const current = state.context.planner;
  const selected =
    options.find((item) => item.provider === current?.provider && item.model === current?.model) ||
    options.find((item) => item.provider === defaultProvider && item.model === defaultModel) ||
    options[0];
  setPlannerOptions(options, selected);
  state.context.planner = { provider: selected.provider, model: selected.model };
}

function plannerOptionLabel(provider, model, isDefault) {
  const providerLabel = provider.label || provider.id;
  const modelLabel = model.label || model.id;
  return `${providerLabel} · ${modelLabel}${isDefault ? t("defaultSuffix") : ""}`;
}

function setPlannerOptions(options, selected = null) {
  els.plannerSelect.replaceChildren(
    ...options.map((item) => {
      const option = document.createElement("option");
      option.textContent = item.label;
      option.disabled = Boolean(item.disabled);
      if (item.provider && item.model) {
        option.value = `${item.provider}::${item.model}`;
        option.dataset.provider = item.provider;
        option.dataset.model = item.model;
      }
      if (selected && item.provider === selected.provider && item.model === selected.model) {
        option.selected = true;
      }
      return option;
    })
  );
  els.plannerSelect.disabled = options.every((item) => item.disabled);
}

function updatePlannerSelection() {
  const option = els.plannerSelect.selectedOptions[0];
  if (!option?.dataset.provider || !option?.dataset.model) {
    state.context.planner = null;
    return;
  }
  state.context.planner = {
    provider: option.dataset.provider,
    model: option.dataset.model,
  };
}

async function handleUserMessage() {
  const text = els.chatInput.value.trim();
  if (!text) {
    return;
  }
  els.chatInput.value = "";
  addUserMessage(text);
  setLanguageFromText(text);

  if (isCancelText(text)) {
    state.pendingAction = null;
    state.pendingDraft = "";
    addAssistantMessage(t("cancelled"));
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
  addAssistantMessage(t("planning"), { transient: true });
  try {
    const data = await requestJson("/api/assistant/plan", {
        method: "POST",
        body: {
          message: planningText,
          context: assistantContext(),
        },
      });
    removeTransientMessages();
    if (data.language) {
      state.language = data.language;
    }
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
  const action = actionWithCurrentPlanner(state.pendingAction);
  if (!action) {
    addAssistantMessage(t("noPendingAction"));
    return;
  }
  if (state.activeTask) {
    addAssistantMessage(t("taskRunning"));
    return;
  }
  state.pendingAction = null;
  state.pendingDraft = "";
  setComposerBusy(true);
  const taskMessage = addAssistantMessage(`${t("confirmedSubmitting")}：${localizedActionSummary(action)}`);
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

function actionWithCurrentPlanner(action) {
  if (!action) {
    return null;
  }
  if (!["workflow.create", "workflow.modify.draft", "workflow.modify.apply"].includes(action.operation)) {
    return action;
  }
  const payload = { ...(action.payload || {}) };
  if (state.context.planner) {
    payload.planner = state.context.planner;
  } else {
    delete payload.planner;
  }
  return { ...action, payload };
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
  title.textContent = localizedActionSummary(action);
  const meta = document.createElement("div");
  meta.className = "action-meta";
  const payload = action.payload || {};
  const mode = action.app_mode || payload.app_mode;
  meta.textContent = [
    `${t("operation")}: ${operationLabel(action.operation)}`,
    mode ? `${t("mode")}: ${appModeLabel(mode)}` : "",
    payload.app_id ? `${t("appId")}: ${payload.app_id}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = t("payloadJson");
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
  cancel.textContent = t("cancel");
  cancel.addEventListener("click", () => {
    state.pendingAction = null;
    wrapper.remove();
    addAssistantMessage(t("cancelled"));
  });
  actions.append(confirm, cancel);
  wrapper.replaceChildren(title, meta, details, actions);
  scrollToLatest();
}

function actionButtonLabel(action) {
  if (action?.operation === "workflow.modify.draft") {
    return t("preview");
  }
  if (action?.operation === "workflow.modify.apply") {
    return t("apply");
  }
  if (action?.operation?.endsWith(".run.draft")) {
    return t("run");
  }
  if (action?.operation === "workflow.create") {
    return t("create");
  }
  return t("confirm");
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
    if (data.language) {
      state.language = data.language;
    }
    if (data.status === "pending_action" && data.action) {
      state.pendingAction = data.action;
      state.pendingDraft = "";
      rememberActionContext(data.action);
      addAssistantMessage(t("previewReady"));
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
  summary.textContent = t("rawJson");
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(result, null, 2);
  details.append(summary, pre);
  wrapper.replaceChildren(title, rows, details);
  scrollToLatest();
}

function resultTitle(result, action = null) {
  if (action?.operation === "workflow.modify.draft") {
    return t("modifyPreviewTitle");
  }
  if (action?.operation === "workflow.modify.apply") {
    return t("modifyAppliedTitle");
  }
  return statusLabel(result.status) || appModeLabel(result.app_mode) || t("result");
}

function resultRows(result) {
  const rows = [
    [t("appId"), result.app_id],
    [t("mode"), appModeLabel(result.app_mode || result.app?.mode || result.plan?.app_mode)],
    [t("url"), result.workflow_url],
    [t("answer"), result.answer],
    [t("conversation"), result.conversation_id],
  ];
  if (result.new_hash) {
    rows.splice(3, 0, [t("newHash"), result.new_hash]);
  } else if (result.base_hash) {
    rows.splice(3, 0, [t("baseHash"), result.base_hash]);
  }
  if (result.base_hash && result.new_hash) {
    rows.splice(4, 0, [t("baseHash"), result.base_hash]);
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
  const phase = [
    statusLabel(record.status),
    statusLabel(record.phase),
    taskMessageLabel(record.message),
  ].filter(Boolean).join(" · ");
  element.textContent = phase || t("taskSubmitted");
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
  const switchingApp = Boolean(reference.app_id && current.app_id && reference.app_id !== current.app_id);
  if (switchingApp) {
    clearAppScopedContext();
  }
  const app = compactPayload({
    ...(switchingApp ? {} : current),
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

function clearAppScopedContext() {
  for (const key of [
    "run_inputs",
    "run_query",
    "conversation_id",
    "parent_message_id",
    "modify_preview",
    "expected_hash",
  ]) {
    delete state.context[key];
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
  const labels = fieldLabels();
  const separator = state.language === "zh" ? "、" : ", ";
  const fields = (data.missing_fields || []).map((field) => labels[field] || field).join(separator);
  if (!fields) {
    return data.message || t("needsMore");
  }
  return `${data.message || t("needsMore")}\n${t("stillNeeds")}：${fields}`;
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

function t(key) {
  const language = state.language === "en" ? "en" : "zh";
  return UI_TEXT[language][key] || UI_TEXT.en[key] || key;
}

function setLanguageFromText(text) {
  state.language = primaryLanguage(text);
}

function primaryLanguage(text) {
  const cleaned = String(text || "").replace(
    /\b(?:agent-chat|app_id|chatbot|chatflow|completion|workflow|agent|dify|hash|json|app)\b/gi,
    " "
  );
  const chinese = (cleaned.match(/[\u4e00-\u9fff]/g) || []).length;
  const latin = (cleaned.match(/[A-Za-z]+/g) || []).reduce((count, word) => count + word.length, 0);
  if (chinese && !latin) {
    return "zh";
  }
  if (latin && !chinese) {
    return "en";
  }
  if (!chinese && !latin) {
    return state.language || "zh";
  }
  return chinese * 2 >= latin ? "zh" : "en";
}

function appModeLabel(mode) {
  if (!mode) {
    return "";
  }
  const language = state.language === "en" ? "en" : "zh";
  return APP_MODE_LABELS[language][mode] || mode;
}

function operationLabel(operation) {
  const language = state.language === "en" ? "en" : "zh";
  return OPERATION_LABELS[language][operation] || operation || "";
}

function statusLabel(status) {
  if (!status) {
    return "";
  }
  const language = state.language === "en" ? "en" : "zh";
  return STATUS_LABELS[language][status] || status;
}

function taskMessageLabel(message) {
  if (!message) {
    return "";
  }
  if (state.language !== "zh") {
    return message;
  }
  const normalized = String(message).trim().toLowerCase();
  if (normalized === "task completed.") {
    return "任务已完成。";
  }
  if (normalized === "task failed.") {
    return "任务失败。";
  }
  return message;
}

function localizedActionSummary(action) {
  if (state.language === "en") {
    return action?.summary || operationLabel(action?.operation);
  }
  const mode = appModeLabel(action?.app_mode || action?.payload?.app_mode);
  switch (action?.operation) {
    case "workflow.create":
      return `确认后创建${mode || "应用"}。`;
    case "workflow.modify.draft":
      return "确认后生成修改预览。";
    case "workflow.modify.apply":
      return "确认后应用已审阅的修改预览。";
    case "workflow.run.draft":
      return "确认后测试运行工作流草稿。";
    case "chatflow.run.draft":
    case "chatbot.run.draft":
    case "completion.run.draft":
    case "agent.run.draft":
      return `确认后测试运行${mode || "应用"}。`;
    default:
      return action?.summary || operationLabel(action?.operation);
  }
}

function fieldLabels() {
  if (state.language === "en") {
    return {
      app_mode: "app type",
      app_id: "app ID",
      inputs: "test input",
      query: "test query",
      expected_hash: "current hash",
      modify_preview: "modify preview",
      operation: "operation",
      message: "request description",
      app_name: "app name",
      app_description: "app description",
    };
  }
  return {
    app_mode: "应用类型",
    app_id: "应用 ID",
    inputs: "测试输入",
    query: "测试问题",
    expected_hash: "当前版本哈希",
    modify_preview: "修改预览",
    operation: "操作",
    message: "需求描述",
    app_name: "应用名称",
    app_description: "应用描述",
  };
}

function taskFailureMessage(record) {
  const detail = record?.error?.detail;
  if (detail?.message) {
    const code = detail.code ? `${detail.code}: ` : "";
    const extras = [];
    if (detail.expected_hash) {
      extras.push(`${state.language === "zh" ? "期望哈希" : "expected"}=${detail.expected_hash}`);
    }
    if (detail.current_hash) {
      extras.push(`${state.language === "zh" ? "当前哈希" : "current"}=${detail.current_hash}`);
    }
    return `${code}${detail.message}${extras.length ? `\n${extras.join("\n")}` : ""}`;
  }
  if (typeof detail === "string") {
    return detail;
  }
  if (record?.error?.type && record?.error?.detail) {
    return `${record.error.type}: ${record.error.detail}`;
  }
  return taskMessageLabel(record?.message) || `${state.language === "zh" ? "任务" : "Task"} ${statusLabel(record?.status || "failed")}`;
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
  return error?.message || t("requestFailed");
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
