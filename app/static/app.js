const APP_CONFIG = window.CHAT2DIFY_CONFIG || {};
const BASE_PATH = normalizeBasePath(APP_CONFIG.basePath || inferBasePathFromAssets());

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

const PHASE_LABELS = {
  zh: {
    queued: "等待执行",
    starting: "开始执行",
    "loading-config": "加载配置",
    "loading-models": "加载模型配置",
    planning: "生成规划",
    "planner-request": "请求规划模型",
    "planner-provider-fallback": "切换规划模型",
    "validating-plan": "校验规划",
    compiling: "编译 DSL",
    importing: "导入 Dify",
    "loading-draft": "加载草稿",
    publishing: "发布工作流",
    connecting: "连接 Dify",
    "running-workflow": "运行工作流",
    "running-chatflow": "运行对话流",
    "running-agent": "运行智能体",
    "running-chatbot": "运行聊天助手",
    "running-completion": "运行文本生成应用",
    "loading-app": "加载应用",
    decompiling: "读取现有流程",
    "validating-preview": "校验预览",
    "validating-preview-config": "校验配置预览",
    "planning-revision": "生成修改方案",
    "validating-revision": "校验修改方案",
    "validating-change": "校验变更",
    "revising-config": "修改提示词配置",
    syncing: "写回 Dify",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "已中断",
  },
  en: {},
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
  addAssistantMessage("告诉我你想创建、修改或测试运行哪类 Dify 应用。");
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
  const message = taskMessageLabel(record.message);
  if (isTerminalStatus(record.status)) {
    element.textContent = message || statusLabel(record.status) || t("taskSubmitted");
    return;
  }
  const phase = compactTaskParts([
    statusLabel(record.status),
    phaseLabel(record.phase),
    message,
  ]).join(" · ");
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
  if (data.message) {
    return data.message;
  }
  if (!fields) {
    return t("needsMore");
  }
  const colon = state.language === "zh" ? "：" : ": ";
  return `${t("needsMore")}\n${t("stillNeeds")}${colon}${fields}`;
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

function phaseLabel(phase) {
  if (!phase) {
    return "";
  }
  const language = state.language === "en" ? "en" : "zh";
  return PHASE_LABELS[language][phase] || statusLabel(phase) || phase;
}

function taskMessageLabel(message) {
  if (!message) {
    return "";
  }
  if (state.language !== "zh") {
    return message;
  }
  return localizeTaskMessage(message) || message;
}

function localizeTaskMessage(message) {
  const text = String(message || "").trim();
  const normalized = text.toLowerCase();
  const exact = {
    "waiting for a worker.": "等待后台任务开始。",
    "task started.": "任务已开始。",
    "task completed.": "任务已完成。",
    "task failed.": "任务失败。",
    "task cancellation requested.": "任务取消请求已收到。",
    "cancellation requested. waiting for the current operation to stop.": "已请求取消，等待当前操作停止。",
    "service restarted before the task completed.": "服务重启，任务未完成。",
    "loading dify and planner configuration.": "正在加载 Dify 和 Planner 配置。",
    "using the fallback workflow template.": "正在使用备用工作流模板。",
    "compiling the validated plan into dify dsl.": "正在把校验后的方案编译为 Dify DSL。",
    "importing the workflow into dify.": "正在导入工作流到 Dify。",
    "loading and validating the current dify draft.": "正在加载并校验当前 Dify 草稿。",
    "publishing the validated workflow in dify.": "正在发布已校验的工作流。",
    "connecting to the dify draft run stream.": "正在连接 Dify 草稿运行流。",
    "connecting to the dify chatbot chat stream.": "正在连接 Dify 聊天助手运行流。",
    "connecting to the dify completion stream.": "正在连接 Dify 文本生成运行流。",
    "connecting to the dify chatflow draft stream.": "正在连接 Dify 对话流草稿运行流。",
    "connecting to the dify agent chat stream.": "正在连接 Dify 智能体运行流。",
    "loading the current dify app.": "正在加载当前 Dify 应用。",
    "converted the current dify graph into plan ir.": "已将当前 Dify 图转换为 Plan IR。",
    "validating the reviewed preview plan without replanning.": "正在校验已审阅的预览方案。",
    "compiling, validating, and checking change risk.": "正在编译、校验并检查变更风险。",
    "writing the reviewed draft back to dify.": "正在把已审阅的草稿写回 Dify。",
  };
  if (exact[normalized]) {
    return exact[normalized];
  }

  let match = text.match(/^Generating workflow plan, semantic attempt (\d+)\/(\d+)\.$/);
  if (match) {
    return `正在生成工作流方案（第 ${match[1]}/${match[2]} 次语义尝试）。`;
  }
  match = text.match(/^Generating workflow revision, semantic attempt (\d+)\/(\d+)\.$/);
  if (match) {
    return `正在生成工作流修改方案（第 ${match[1]}/${match[2]} 次语义尝试）。`;
  }
  match = text.match(/^Normalizing and validating semantic attempt (\d+)\/(\d+)\.$/);
  if (match) {
    return `正在规范化并校验方案（第 ${match[1]}/${match[2]} 次语义尝试）。`;
  }
  match = text.match(/^Normalizing and validating revision attempt (\d+)\/(\d+)\.$/);
  if (match) {
    return `正在规范化并校验修改方案（第 ${match[1]}/${match[2]} 次尝试）。`;
  }
  match = text.match(/^Calling (.+), network attempt (\d+)\/(\d+)\.$/);
  if (match) {
    return `正在调用 ${match[1]}（第 ${match[2]}/${match[3]} 次网络请求）。`;
  }
  match = text.match(/^(.+) is unavailable; trying (.+)\.$/);
  if (match) {
    return `${match[1]} 暂时不可用，正在尝试 ${match[2]}。`;
  }
  match = text.match(/^Loading Dify model configuration for (Agent|Chatbot|Completion) app\.$/);
  if (match) {
    return `正在加载 ${configuredAppLabel(match[1])} 的 Dify 模型配置。`;
  }
  match = text.match(/^Compiling (Agent|Chatbot|Completion) app configuration into Dify DSL\.$/);
  if (match) {
    return `正在把 ${configuredAppLabel(match[1])} 配置编译为 Dify DSL。`;
  }
  match = text.match(/^Loaded (Agent|Chatbot|Completion) model configuration\.$/);
  if (match) {
    return `已加载 ${configuredAppLabel(match[1])} 模型配置。`;
  }
  match = text.match(/^Using the reviewed (Agent|Chatbot|Completion) configuration preview\.$/);
  if (match) {
    return `正在使用已审阅的 ${configuredAppLabel(match[1])} 配置预览。`;
  }
  match = text.match(/^Revising (Agent|Chatbot|Completion) prompt configuration\.$/);
  if (match) {
    return `正在修改 ${configuredAppLabel(match[1])} 提示词配置。`;
  }
  match = text.match(/^Writing the (Agent|Chatbot|Completion) model configuration back to Dify\.$/);
  if (match) {
    return `正在把 ${configuredAppLabel(match[1])} 模型配置写回 Dify。`;
  }
  match = text.match(/^Dify event (.+); (\d+) nodes finished, (\d+) events received\.$/);
  if (match) {
    return `Dify 事件 ${difyEventLabel(match[1])}；已完成 ${match[2]} 个节点，收到 ${match[3]} 个事件。`;
  }
  match = text.match(/^Dify event (.+); (\d+) nodes finished, (\d+) answer chunks received\.$/);
  if (match) {
    return `Dify 事件 ${difyEventLabel(match[1])}；已完成 ${match[2]} 个节点，收到 ${match[3]} 段回答。`;
  }
  match = text.match(/^Dify event (.+); (\d+) Agent answer chunks received\.$/);
  if (match) {
    return `Dify 事件 ${difyEventLabel(match[1])}；收到 ${match[2]} 段智能体回答。`;
  }
  match = text.match(/^Dify event (.+); (\d+) Chatbot answer chunks received\.$/);
  if (match) {
    return `Dify 事件 ${difyEventLabel(match[1])}；收到 ${match[2]} 段聊天助手回答。`;
  }
  match = text.match(/^Dify event (.+); (\d+) Completion chunks received\.$/);
  if (match) {
    return `Dify 事件 ${difyEventLabel(match[1])}；收到 ${match[2]} 段文本生成结果。`;
  }
  match = text.match(/^Received (\d+) answer chunks\.$/);
  if (match) {
    return `收到 ${match[1]} 段回答。`;
  }
  return localizeErrorDetailText(text);
}

function configuredAppLabel(label) {
  return {
    Agent: "智能体",
    Chatbot: "聊天助手",
    Completion: "文本生成应用",
  }[label] || label;
}

function difyEventLabel(eventType) {
  return {
    workflow_started: "工作流已开始",
    node_started: "节点已开始",
    node_finished: "节点已完成",
    workflow_finished: "工作流已完成",
    workflow_paused: "工作流已暂停",
    message: "回答片段",
    agent_message: "智能体回答片段",
    error: "错误",
  }[eventType] || eventType;
}

function localizedErrorDetail(message) {
  if (!message) {
    return "";
  }
  if (state.language !== "zh") {
    return message;
  }
  return localizeErrorDetailText(message) || localizeTaskMessage(message) || message;
}

function localizeErrorDetailText(message) {
  const text = String(message || "").trim();
  let match = text.match(/^Planner LLM request failed after (\d+) network attempts: (.+)$/);
  if (match) {
    const attempts = Number(match[1]);
    const detail = localizeProviderFailure(match[2]);
    return attempts > 1
      ? `规划模型请求失败（已尝试 ${attempts} 次网络请求）：${detail}`
      : `规划模型请求失败：${detail}`;
  }
  match = text.match(/^Planner LLM request failed: (.+)$/);
  if (match) {
    return `规划模型请求失败：${localizeProviderFailure(match[1])}`;
  }
  match = text.match(/^Planner LLM providers unavailable after fallback attempts: (.+)$/);
  if (match) {
    return `所有规划模型均不可用：${match[1]}`;
  }
  match = text.match(/^At least one Planner provider API key is required: (.+)$/);
  if (match) {
    return `至少需要配置一个 Planner 服务 API Key：${match[1]}`;
  }
  match = text.match(/^Dify request failed: (.+)$/);
  if (match) {
    return `Dify 请求失败：${localizeProviderFailure(match[1])}`;
  }
  if (text === "DIFY_EMAIL and DIFY_PASSWORD are required to create workflows in Dify.") {
    return "需要配置 DIFY_EMAIL 和 DIFY_PASSWORD 后才能在 Dify 中创建应用。";
  }
  return "";
}

function localizeProviderFailure(detail) {
  const text = String(detail || "").trim();
  const match = text.match(/^(\d{3})\s+(.+)$/);
  if (match) {
    return `${match[1]} ${localizeProviderFailureBody(match[2])}`;
  }
  return localizeProviderFailureBody(text);
}

function localizeProviderFailureBody(body) {
  const text = String(body || "").trim();
  const parsed = parseJsonObject(text);
  const message = parsed
    ? parsed.error || parsed.message || parsed.title || parsed.detail || ""
    : "";
  const known = knownProviderError(message || text);
  if (known) {
    return `${known}。`;
  }
  if (message) {
    return message;
  }
  return text;
}

function parseJsonObject(text) {
  if (!text || !text.startsWith("{")) {
    return null;
  }
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (_error) {
    return null;
  }
}

function knownProviderError(message) {
  const normalized = String(message || "").trim().toLowerCase();
  return {
    "key usage limit exceeded": "API Key 用量已超限",
    "too many requests": "请求过于频繁",
    unauthorized: "认证失败",
    forbidden: "没有权限",
    "bad request": "请求参数不合法",
    "internal server error": "服务端错误",
    "service unavailable": "服务暂时不可用",
    "gateway timeout": "网关超时",
  }[normalized] || "";
}

function compactTaskParts(parts) {
  const seen = new Set();
  const result = [];
  for (const part of parts) {
    const value = String(part || "").trim();
    if (!value) {
      continue;
    }
    const key = value.replace(/[。.]+$/, "");
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(value);
  }
  return result;
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
    app_description: "需求描述",
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
    return `${code}${localizedErrorDetail(detail.message)}${extras.length ? `\n${extras.join("\n")}` : ""}`;
  }
  if (typeof detail === "string") {
    return localizedErrorDetail(detail);
  }
  if (record?.error?.type && record?.error?.detail) {
    const detailText = localizedErrorDetail(record.error.detail);
    if (state.language === "zh" && detailText !== record.error.detail) {
      return detailText;
    }
    return `${record.error.type}: ${detailText}`;
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
  const response = await fetch(apiUrl(path), init);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(errorMessageFromPayload(data, response.status));
    error.payload = typeof data === "string" ? { error: data, status: response.status } : data;
    throw error;
  }
  return data;
}

function apiUrl(path) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${BASE_PATH}${normalizedPath}`;
}

function normalizeBasePath(value) {
  if (!value) {
    return "";
  }
  let normalized = String(value).trim();
  if (!normalized || normalized === "/") {
    return "";
  }
  if (!normalized.startsWith("/")) {
    normalized = `/${normalized}`;
  }
  return normalized.replace(/\/+$/, "");
}

function inferBasePathFromAssets() {
  const script = document.querySelector("script[src*='static/app.js']");
  if (!script) {
    return "";
  }
  const src = script.getAttribute("src") || "";
  const url = new URL(src, window.location.href);
  const marker = "/static/app.js";
  if (!url.pathname.endsWith(marker)) {
    return "";
  }
  return url.pathname.slice(0, -marker.length);
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
    return localizedErrorDetail(payload) || `HTTP ${status}`;
  }
  const detail = payload?.detail;
  if (typeof detail === "string") {
    return localizedErrorDetail(detail);
  }
  if (detail?.message) {
    return localizedErrorDetail(detail.message);
  }
  if (payload?.error) {
    return localizedErrorDetail(payload.error);
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
