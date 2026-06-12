const HISTORY_KEY = "chat2dify.workbench.history.v1";
const DATASET_IDS_KEY = "chat2dify.workbench.datasetIds.v1";
const SELECTED_DATASET_IDS_KEY = "chat2dify.workbench.selectedDatasetIds.v1";
const DATASET_SEARCH_KEY = "chat2dify.workbench.datasetSearch.v1";
const SELECTED_TOOLS_KEY = "chat2dify.workbench.selectedTools.v1";
const TOOL_SEARCH_KEY = "chat2dify.workbench.toolSearch.v1";
const TOOL_TYPE_KEY = "chat2dify.workbench.toolType.v1";
const SELECTED_AGENTS_KEY = "chat2dify.workbench.selectedAgents.v1";
const AGENT_SEARCH_KEY = "chat2dify.workbench.agentSearch.v1";
const PLANNER_PROVIDER_KEY = "chat2dify.workbench.plannerProvider.v1";
const PLANNER_MODEL_KEY = "chat2dify.workbench.plannerModel.v1";
const TRIGGER_SELECTION_KEY = "chat2dify.workbench.triggerSelection.v1";
const ACTIVE_TASKS_KEY = "chat2dify.workbench.activeTasks.v1";
const TERMINAL_TASKS_KEY = "chat2dify.workbench.terminalTasks.v1";
const MAX_HISTORY_ITEMS = 12;
const DATASET_PAGE_SIZE = 50;
const DEFAULT_RUN_INPUTS = '{"query":"我要投诉订单配送太慢"}';
const DEFAULT_TOOL_QUERY_TEMPLATE = "{{#start.query#}}";

const state = {
  lastResponse: {},
  history: [],
  activeTab: "changes",
  modifyPreview: null,
  modifyPreviewDirty: false,
  planner: {
    providers: [],
    provider: "",
    model: "",
    defaultProvider: "",
    defaultModel: "",
  },
  datasets: {
    items: [],
    selectedIds: [],
    page: 1,
    hasMore: false,
    total: 0,
    keyword: "",
  },
  tools: {
    items: [],
    selected: [],
    keyword: "",
    providerType: "all",
  },
  agents: {
    items: [],
    selected: [],
    keyword: "",
  },
  pluginTriggers: {
    items: [],
    subscriptions: [],
    keyword: "",
    loaded: false,
    loading: false,
  },
  triggerSelection: null,
  workflowTriggers: [],
  defaultModel: {
    provider: "",
    name: "",
  },
  activeTasks: {},
  terminalTasks: {},
  taskPollTimers: {},
};

const els = {
  healthStatus: document.querySelector("#health-status"),
  refreshHealth: document.querySelector("#refresh-health"),
  plannerForm: document.querySelector("#planner-form"),
  plannerStatus: document.querySelector("#planner-status"),
  plannerProvider: document.querySelector("#planner-provider"),
  plannerModel: document.querySelector("#planner-model"),
  plannerSummary: document.querySelector("#planner-summary"),
  resultJson: document.querySelector("#result-json"),
  summaryGrid: document.querySelector("#summary-grid"),
  resultTabs: document.querySelector("#result-tabs"),
  tabPanels: Array.from(document.querySelectorAll("[data-tab-panel]")),
  createForm: document.querySelector("#create-form"),
  createStatus: document.querySelector("#create-status"),
  createDuration: document.querySelector("#create-duration"),
  createTaskProgress: document.querySelector("#create-task-progress"),
  createTaskMessage: document.querySelector("#create-task-message"),
  createTaskBar: document.querySelector("#create-task-bar"),
  createCancelTask: document.querySelector("#create-cancel-task"),
  knowledgeForm: document.querySelector("#knowledge-form"),
  knowledgeStatus: document.querySelector("#knowledge-status"),
  knowledgeSearch: document.querySelector("#knowledge-search"),
  knowledgeDatasetIds: document.querySelector("#knowledge-dataset-ids"),
  knowledgeDatasetList: document.querySelector("#knowledge-dataset-list"),
  knowledgeSelectedSummary: document.querySelector("#knowledge-selected-summary"),
  loadMoreDatasets: document.querySelector("#load-more-datasets"),
  toolsForm: document.querySelector("#tools-form"),
  toolsStatus: document.querySelector("#tools-status"),
  toolsSearch: document.querySelector("#tools-search"),
  toolsType: document.querySelector("#tools-type"),
  toolsList: document.querySelector("#tools-list"),
  toolsSelectedSummary: document.querySelector("#tools-selected-summary"),
  agentsForm: document.querySelector("#agents-form"),
  agentsStatus: document.querySelector("#agents-status"),
  agentsSearch: document.querySelector("#agents-search"),
  agentsList: document.querySelector("#agents-list"),
  agentsSelectedSummary: document.querySelector("#agents-selected-summary"),
  triggerForm: document.querySelector("#trigger-form"),
  triggerStatus: document.querySelector("#trigger-status"),
  triggerType: document.querySelector("#trigger-type"),
  triggerWebhookFields: document.querySelector("#trigger-webhook-fields"),
  triggerWebhookMethod: document.querySelector("#trigger-webhook-method"),
  triggerWebhookContentType: document.querySelector("#trigger-webhook-content-type"),
  triggerWebhookHeaders: document.querySelector("#trigger-webhook-headers"),
  triggerWebhookQuery: document.querySelector("#trigger-webhook-query"),
  triggerWebhookBody: document.querySelector("#trigger-webhook-body"),
  triggerWebhookStatusCode: document.querySelector("#trigger-webhook-status-code"),
  triggerWebhookTimeout: document.querySelector("#trigger-webhook-timeout"),
  triggerWebhookResponse: document.querySelector("#trigger-webhook-response"),
  triggerPluginFields: document.querySelector("#trigger-plugin-fields"),
  triggerPluginSearch: document.querySelector("#trigger-plugin-search"),
  triggerPluginRefresh: document.querySelector("#refresh-trigger-plugins"),
  triggerPluginEvent: document.querySelector("#trigger-plugin-event"),
  triggerPluginSubscription: document.querySelector("#trigger-plugin-subscription"),
  triggerPluginParameters: document.querySelector("#trigger-plugin-parameters"),
  triggerPluginMessage: document.querySelector("#trigger-plugin-message"),
  triggerScheduleFields: document.querySelector("#trigger-schedule-fields"),
  triggerScheduleMode: document.querySelector("#trigger-schedule-mode"),
  triggerScheduleTimezone: document.querySelector("#trigger-schedule-timezone"),
  triggerScheduleVisual: document.querySelector("#trigger-schedule-visual"),
  triggerScheduleFrequency: document.querySelector("#trigger-schedule-frequency"),
  triggerScheduleTime: document.querySelector("#trigger-schedule-time"),
  triggerScheduleMinute: document.querySelector("#trigger-schedule-minute"),
  triggerScheduleWeekdays: document.querySelector("#trigger-schedule-weekdays"),
  triggerScheduleMonthlyDays: document.querySelector("#trigger-schedule-monthly-days"),
  triggerScheduleCronField: document.querySelector("#trigger-schedule-cron-field"),
  triggerScheduleCron: document.querySelector("#trigger-schedule-cron"),
  modifyForm: document.querySelector("#modify-form"),
  modifyStatus: document.querySelector("#modify-status"),
  modifyDuration: document.querySelector("#modify-duration"),
  modifyTaskProgress: document.querySelector("#modify-task-progress"),
  modifyTaskMessage: document.querySelector("#modify-task-message"),
  modifyTaskBar: document.querySelector("#modify-task-bar"),
  modifyCancelTask: document.querySelector("#modify-cancel-task"),
  runForm: document.querySelector("#run-form"),
  runStatus: document.querySelector("#run-status"),
  runDuration: document.querySelector("#run-duration"),
  runTaskProgress: document.querySelector("#run-task-progress"),
  runTaskMessage: document.querySelector("#run-task-message"),
  runTaskBar: document.querySelector("#run-task-bar"),
  runCancelTask: document.querySelector("#run-cancel-task"),
  publishForm: document.querySelector("#publish-form"),
  publishStatus: document.querySelector("#publish-status"),
  publishDuration: document.querySelector("#publish-duration"),
  publishTaskProgress: document.querySelector("#publish-task-progress"),
  publishTaskMessage: document.querySelector("#publish-task-message"),
  publishTaskBar: document.querySelector("#publish-task-bar"),
  publishCancelTask: document.querySelector("#publish-cancel-task"),
  publishAppId: document.querySelector("#publish-app-id"),
  publishExpectedHash: document.querySelector("#publish-expected-hash"),
  workflowTriggerList: document.querySelector("#workflow-trigger-list"),
  createAppName: document.querySelector("#create-app-name"),
  modifyAppId: document.querySelector("#modify-app-id"),
  modifyExpectedHash: document.querySelector("#modify-expected-hash"),
  runAppId: document.querySelector("#run-app-id"),
  runInputs: document.querySelector("#run-inputs"),
  historyList: document.querySelector("#history-list"),
  openWorkflow: document.querySelector("#open-workflow"),
  copyAppId: document.querySelector("#copy-app-id"),
  copyWorkflowUrl: document.querySelector("#copy-workflow-url"),
  copyHash: document.querySelector("#copy-hash"),
  copyRawJson: document.querySelector("#copy-raw-json"),
};

document.addEventListener("DOMContentLoaded", () => {
  state.history = loadHistory();
  state.datasets.selectedIds = loadSelectedDatasetIds();
  state.datasets.keyword = loadDatasetSearchText();
  state.tools.selected = loadSelectedTools();
  state.tools.keyword = loadToolSearchText();
  state.tools.providerType = loadToolType();
  state.agents.selected = loadSelectedAgents();
  state.agents.keyword = loadAgentSearchText();
  state.planner.provider = loadPlannerProvider();
  state.planner.model = loadPlannerModel();
  state.triggerSelection = loadTriggerSelection();
  state.activeTasks = loadActiveTasks();
  state.terminalTasks = loadTerminalTasks();
  els.knowledgeSearch.value = state.datasets.keyword;
  els.knowledgeDatasetIds.value = loadDatasetIdsText();
  els.toolsSearch.value = state.tools.keyword;
  els.toolsType.value = state.tools.providerType;
  els.agentsSearch.value = state.agents.keyword;
  restoreTriggerForm(state.triggerSelection);
  bindEvents();
  renderHistory();
  renderPlannerModels();
  renderKnowledgeDatasets();
  renderTools();
  renderTriggerForm();
  renderWorkflowTriggers([]);
  refreshHealth();
  loadPlannerProviders();
  loadDatasets({ reset: true });
  loadTools();
  loadAgentStrategies();
  if (state.triggerSelection?.type === "plugin") {
    loadTriggerProviders();
  }
  renderResult({});
  restoreActiveTasks();
  restoreTerminalTasks();
});

function bindEvents() {
  els.refreshHealth.addEventListener("click", refreshHealth);
  els.plannerProvider.addEventListener("change", () => {
    state.planner.provider = els.plannerProvider.value;
    const provider = selectedPlannerProvider();
    state.planner.model = provider?.models?.[0]?.id || "";
    savePlannerSelection();
    renderPlannerModels();
    markModifyPreviewDirty();
  });
  els.plannerModel.addEventListener("change", () => {
    state.planner.model = els.plannerModel.value;
    savePlannerSelection();
    renderPlannerModels();
    markModifyPreviewDirty();
  });
  els.knowledgeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveDatasetSearchText(els.knowledgeSearch.value);
    await loadDatasets({ reset: true });
  });
  els.loadMoreDatasets.addEventListener("click", async () => {
    await loadDatasets({ reset: false });
  });
  els.knowledgeSearch.addEventListener("input", () => {
    saveDatasetSearchText(els.knowledgeSearch.value);
  });
  els.knowledgeDatasetList.addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-dataset-id]");
    if (checkbox) {
      toggleDatasetSelection(checkbox.dataset.datasetId, checkbox.checked);
    }
  });
  els.knowledgeDatasetIds.addEventListener("input", () => {
    saveDatasetIdsText(els.knowledgeDatasetIds.value);
    markModifyPreviewDirty();
  });
  els.toolsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveToolSearchText(els.toolsSearch.value);
    saveToolType(els.toolsType.value);
    await loadTools();
  });
  els.toolsSearch.addEventListener("input", () => {
    saveToolSearchText(els.toolsSearch.value);
  });
  els.toolsType.addEventListener("change", async () => {
    saveToolType(els.toolsType.value);
    await loadTools();
  });
  els.toolsList.addEventListener("change", (event) => {
    const checkbox = event.target.closest("input[type='checkbox'][data-tool-key]");
    if (checkbox) {
      toggleToolSelection(checkbox.dataset.toolKey, checkbox.checked);
      return;
    }
    const field = event.target.closest("[data-tool-config-key]");
    if (field) {
      handleToolConfigInput(field);
    }
  });
  els.toolsList.addEventListener("input", (event) => {
    const field = event.target.closest("[data-tool-config-key]");
    if (field) {
      handleToolConfigInput(field);
    }
  });
  els.agentsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveAgentSearchText(els.agentsSearch.value);
    await loadAgentStrategies();
  });
  els.agentsSearch.addEventListener("input", () => {
    saveAgentSearchText(els.agentsSearch.value);
  });
  els.agentsList.addEventListener("change", (event) => {
    const checkbox = event.target.closest("input[type='checkbox'][data-agent-key]");
    if (checkbox) {
      toggleAgentSelection(checkbox.dataset.agentKey, checkbox.checked);
      return;
    }
    const field = event.target.closest("[data-agent-config-key]");
    if (field) {
      handleAgentConfigInput(field);
    }
  });
  els.agentsList.addEventListener("input", (event) => {
    const field = event.target.closest("[data-agent-config-key]");
    if (field) {
      handleAgentConfigInput(field);
    }
  });
  els.triggerForm.addEventListener("input", handleTriggerFormChange);
  els.triggerForm.addEventListener("change", handleTriggerFormChange);
  els.triggerPluginRefresh.addEventListener("click", async () => {
    await loadTriggerProviders();
  });
  els.triggerPluginSearch.addEventListener("input", () => {
    state.pluginTriggers.keyword = els.triggerPluginSearch.value.trim();
    renderPluginTriggerForm();
  });
  els.triggerPluginEvent.addEventListener("change", async () => {
    const event = selectedPluginTriggerEvent();
    state.triggerSelection = event
      ? {
          type: "plugin",
          provider_id: event.provider_id,
          event_name: event.event_name,
          subscription_id: "",
          event_parameters: {},
        }
      : { type: "plugin" };
    saveTriggerSelection();
    await loadTriggerSubscriptions(event?.provider_id || "");
    renderPluginTriggerForm();
    markModifyPreviewDirty();
  });

  els.createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await handleCreate();
  });
  els.createCancelTask.addEventListener("click", () => handleTaskAction("create"));
  els.modifyCancelTask.addEventListener("click", () => handleTaskAction("modify"));
  els.runCancelTask.addEventListener("click", () => handleTaskAction("run"));
  els.publishCancelTask.addEventListener("click", () => handleTaskAction("publish"));

  document.querySelector("#preview-modify").addEventListener("click", async () => {
    await handleModify("/api/workflows/modify/draft", "preview");
  });

  document.querySelector("#apply-modify").addEventListener("click", async () => {
    await handleReviewedPreviewApply();
  });

  document.querySelector("#load-draft").addEventListener("click", handleLoadDraft);
  ["#modify-app-id", "#modify-message", "#modify-expected-hash", "#modify-allow-destructive"].forEach((selector) => {
    const element = document.querySelector(selector);
    element.addEventListener("input", markModifyPreviewDirty);
    element.addEventListener("change", markModifyPreviewDirty);
  });

  els.runForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await handleRun();
  });
  els.publishForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await handlePublish();
  });
  document.querySelector("#refresh-workflow-triggers").addEventListener("click", async () => {
    await loadWorkflowTriggers();
  });
  els.workflowTriggerList.addEventListener("click", handleWorkflowTriggerAction);

  document.querySelector("#format-inputs").addEventListener("click", formatRunInputs);
  document.querySelector("#reset-inputs").addEventListener("click", resetRunInputs);
  document.querySelector("#clear-result").addEventListener("click", clearResult);
  document.querySelector("#clear-history").addEventListener("click", clearHistory);

  els.resultTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (button) {
      setActiveTab(button.dataset.tab);
    }
  });

  els.historyList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-history-index]");
    if (item) {
      selectHistoryItem(Number(item.dataset.historyIndex));
    }
  });

  els.copyAppId.addEventListener("click", () => copyValue(currentAppId(), els.copyAppId, "Copy app ID"));
  els.copyWorkflowUrl.addEventListener("click", () => copyValue(currentWorkflowUrl(), els.copyWorkflowUrl, "Copy URL"));
  els.copyHash.addEventListener("click", () => copyValue(currentHash(), els.copyHash, "Copy hash"));
  els.copyRawJson.addEventListener("click", () => copyValue(JSON.stringify(state.lastResponse, null, 2), els.copyRawJson, "Copy JSON"));
}

async function refreshHealth() {
  try {
    els.healthStatus.textContent = "Checking";
    els.healthStatus.className = "status-pill status-muted";
    const data = await requestJson("/health");
    const version = data?.dify?.app_dsl_version || "unknown";
    const source = data?.dify?.git_describe || "source";
    const defaultModel = data?.default_model || data?.dify?.default_model || {};
    const modelProvider = defaultModel.provider || "";
    const modelName = defaultModel.name || defaultModel.model || "";
    if (modelProvider || modelName) {
      state.defaultModel = { provider: modelProvider, name: modelName };
      state.agents.selected = uniqueAgents(state.agents.selected);
      saveSelectedAgents();
      renderAgentStrategies();
    }
    const datasetCount = data?.configured_dataset_count ?? data?.dify?.configured_dataset_count;
    const datasetSuffix = datasetCount !== undefined ? ` · datasets ${datasetCount}` : "";
    const planner = data?.planner || {};
    const plannerSuffix = planner.provider && planner.model ? ` · planner ${planner.provider}/${planner.model}` : "";
    els.healthStatus.textContent = `Healthy · DSL ${version} · ${source}${datasetSuffix}${plannerSuffix}`;
    els.healthStatus.className = "status-pill status-ok";
  } catch (error) {
    els.healthStatus.textContent = "Offline";
    els.healthStatus.className = "status-pill status-error";
  }
}

async function loadPlannerProviders() {
  try {
    setPanelStatus(els.plannerStatus, "Loading", "muted");
    const data = await requestJson("/api/planner/providers");
    state.planner.providers = Array.isArray(data.providers) ? data.providers : [];
    state.planner.defaultProvider = data.default_provider || "";
    state.planner.defaultModel = data.default_model || "";
    selectAvailablePlanner();
    renderPlannerModels();
  } catch (error) {
    state.planner.providers = [];
    renderPlannerModels(error.message);
    setPanelStatus(els.plannerStatus, "Unavailable", "error");
  }
}

function selectAvailablePlanner() {
  const providers = state.planner.providers;
  let provider = providers.find((item) => item.id === state.planner.provider && item.configured);
  if (!provider) {
    provider = providers.find((item) => item.id === state.planner.defaultProvider && item.configured);
  }
  if (!provider) {
    provider = providers.find((item) => item.configured);
  }
  state.planner.provider = provider?.id || "";
  const models = Array.isArray(provider?.models) ? provider.models : [];
  const selectedModel = models.find((item) => item.id === state.planner.model)
    || models.find((item) => item.id === state.planner.defaultModel)
    || models[0];
  state.planner.model = selectedModel?.id || "";
  savePlannerSelection();
}

function renderPlannerModels(errorMessage = "") {
  const providers = state.planner.providers;
  els.plannerProvider.replaceChildren(
    ...providers.map((provider) => {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = `${provider.label || provider.id}${provider.configured ? "" : " (not configured)"}`;
      option.disabled = !provider.configured;
      option.selected = provider.id === state.planner.provider;
      return option;
    })
  );
  if (!providers.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No planner provider";
    els.plannerProvider.append(option);
  }
  const provider = selectedPlannerProvider();
  const models = Array.isArray(provider?.models) ? provider.models : [];
  els.plannerModel.replaceChildren(
    ...models.map((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.label || model.id;
      option.selected = model.id === state.planner.model;
      return option;
    })
  );
  els.plannerProvider.disabled = !providers.some((item) => item.configured);
  els.plannerModel.disabled = !provider?.configured || !models.length;
  if (errorMessage) {
    els.plannerSummary.textContent = errorMessage;
    return;
  }
  if (!provider) {
    els.plannerSummary.textContent = "No LLM configured · Create uses fallback";
    setPanelStatus(els.plannerStatus, "Fallback", "warning");
    return;
  }
  els.plannerSummary.textContent = `${provider.label || provider.id} · ${state.planner.model}`;
  setPanelStatus(els.plannerStatus, "Configured", "ok");
}

function selectedPlannerProvider() {
  return state.planner.providers.find((item) => item.id === state.planner.provider);
}

async function loadDatasets({ reset }) {
  const nextPage = reset ? 1 : state.datasets.page + 1;
  const params = new URLSearchParams({
    page: String(nextPage),
    limit: String(DATASET_PAGE_SIZE),
    include_all: "true",
  });
  const keyword = els.knowledgeSearch.value.trim();
  if (keyword) {
    params.set("keyword", keyword);
  }

  try {
    setPanelStatus(els.knowledgeStatus, reset ? "Loading" : "Loading more", "muted");
    els.loadMoreDatasets.disabled = true;
    const data = await requestJson(`/api/dify/datasets?${params.toString()}`);
    const incoming = Array.isArray(data.data) ? data.data : [];
    state.datasets.items = reset ? incoming : mergeDatasets(state.datasets.items, incoming);
    state.datasets.page = Number(data.page || nextPage);
    state.datasets.hasMore = Boolean(data.has_more);
    state.datasets.total = Number(data.total || state.datasets.items.length);
    state.datasets.keyword = keyword;
    renderKnowledgeDatasets();
    const count = state.datasets.items.length;
    setPanelStatus(els.knowledgeStatus, `${state.datasets.total || count} found`, "ok");
  } catch (error) {
    state.datasets.hasMore = false;
    renderKnowledgeDatasets(error.message);
    setPanelStatus(els.knowledgeStatus, "List failed", "error");
  } finally {
    els.loadMoreDatasets.disabled = !state.datasets.hasMore;
  }
}

function renderKnowledgeDatasets(errorMessage = "") {
  const selectedCount = currentDatasetIds().length;
  const loadedCount = state.datasets.items.length;
  const total = state.datasets.total || loadedCount;
  els.knowledgeSelectedSummary.textContent = [
    `${selectedCount} selected`,
    loadedCount ? `${loadedCount}/${total} loaded` : "",
  ].filter(Boolean).join(" · ");

  if (errorMessage) {
    els.knowledgeDatasetList.replaceChildren(renderMessageRow({ tone: "error", text: errorMessage }));
    return;
  }
  if (state.datasets.items.length === 0) {
    els.knowledgeDatasetList.replaceChildren(emptyState("No datasets loaded."));
    return;
  }

  const selected = new Set(state.datasets.selectedIds);
  els.knowledgeDatasetList.replaceChildren(
    ...state.datasets.items.map((dataset) => datasetOption(dataset, selected.has(dataset.id)))
  );
}

function datasetOption(dataset, checked) {
  const label = document.createElement("label");
  label.className = `dataset-option${checked ? " is-selected" : ""}`;
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = checked;
  checkbox.dataset.datasetId = dataset.id;

  const body = document.createElement("span");
  body.className = "dataset-option-body";
  const name = document.createElement("span");
  name.className = "dataset-name";
  name.textContent = dataset.name || dataset.id;
  const meta = document.createElement("span");
  meta.className = "dataset-meta";
  meta.textContent = datasetMeta(dataset);
  const description = document.createElement("span");
  description.className = "dataset-description";
  description.textContent = dataset.description || dataset.id;
  body.append(name, meta, description);
  label.append(checkbox, body);
  return label;
}

function datasetMeta(dataset) {
  const documents = dataset.document_count ?? dataset.total_document_count;
  return [
    documents !== undefined && documents !== null ? `${documents} docs` : "",
    dataset.provider,
    dataset.runtime_mode,
    dataset.indexing_technique,
    dataset.embedding_available === false ? "embedding unavailable" : "",
  ].filter(Boolean).join(" · ") || "dataset";
}

function toggleDatasetSelection(datasetId, selected) {
  if (!datasetId) {
    return;
  }
  const ids = new Set(state.datasets.selectedIds);
  if (selected) {
    ids.add(datasetId);
  } else {
    ids.delete(datasetId);
  }
  state.datasets.selectedIds = Array.from(ids);
  saveSelectedDatasetIds();
  renderKnowledgeDatasets();
  markModifyPreviewDirty();
}

function mergeDatasets(current, incoming) {
  const seen = new Set();
  return [...current, ...incoming].filter((dataset) => {
    if (!dataset?.id || seen.has(dataset.id)) {
      return false;
    }
    seen.add(dataset.id);
    return true;
  });
}

async function loadTools() {
  const params = new URLSearchParams({
    provider_type: els.toolsType.value || "all",
  });
  const keyword = els.toolsSearch.value.trim();
  if (keyword) {
    params.set("keyword", keyword);
  }

  try {
    setPanelStatus(els.toolsStatus, "Loading", "muted");
    const data = await requestJson(`/api/dify/tools?${params.toString()}`);
    state.tools.items = Array.isArray(data.data) ? data.data : [];
    hydrateSelectedToolsFromLoaded();
    state.tools.keyword = keyword;
    state.tools.providerType = els.toolsType.value || "all";
    renderTools();
    setPanelStatus(els.toolsStatus, `${data.count ?? state.tools.items.length} found`, "ok");
  } catch (error) {
    state.tools.items = [];
    renderTools(error.message);
    setPanelStatus(els.toolsStatus, "List failed", "error");
  }
}

function renderTools(errorMessage = "") {
  const loadedCount = state.tools.items.length;
  const selectedCount = currentToolSelections().length;
  els.toolsSelectedSummary.textContent = [
    `${selectedCount} selected`,
    loadedCount ? `${loadedCount} loaded` : "",
  ].filter(Boolean).join(" · ");

  if (errorMessage) {
    els.toolsList.replaceChildren(renderMessageRow({ tone: "error", text: errorMessage }));
    return;
  }
  if (state.tools.items.length === 0) {
    if (state.tools.selected.length === 0) {
      els.toolsList.replaceChildren(emptyState("No tools loaded."));
      return;
    }
    els.toolsList.replaceChildren(
      ...state.tools.selected.map((tool) => toolOption(tool, true))
    );
    return;
  }
  const selectedKeys = new Set(state.tools.selected.map(toolKey));
  const renderedTools = uniqueTools([...state.tools.selected, ...state.tools.items]);
  els.toolsList.replaceChildren(
    ...renderedTools.map((tool) => toolOption(tool, selectedKeys.has(toolKey(tool))))
  );
}

async function loadAgentStrategies() {
  const params = new URLSearchParams();
  const keyword = els.agentsSearch.value.trim();
  if (keyword) {
    params.set("keyword", keyword);
  }

  try {
    setPanelStatus(els.agentsStatus, "Loading", "muted");
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const data = await requestJson(`/api/dify/agent-strategies${suffix}`);
    state.agents.items = Array.isArray(data.data) ? data.data : [];
    hydrateSelectedAgentsFromLoaded();
    state.agents.keyword = keyword;
    renderAgentStrategies();
    setPanelStatus(els.agentsStatus, `${data.count ?? state.agents.items.length} found`, "ok");
  } catch (error) {
    state.agents.items = [];
    renderAgentStrategies(error.message);
    setPanelStatus(els.agentsStatus, "List failed", "error");
  }
}

function renderAgentStrategies(errorMessage = "") {
  const loadedCount = state.agents.items.length;
  const selectedCount = currentAgentSelections().length;
  els.agentsSelectedSummary.textContent = [
    `${selectedCount} selected`,
    loadedCount ? `${loadedCount} loaded` : "",
  ].filter(Boolean).join(" · ");

  if (errorMessage) {
    els.agentsList.replaceChildren(renderMessageRow({ tone: "error", text: errorMessage }));
    return;
  }
  if (state.agents.items.length === 0) {
    if (state.agents.selected.length === 0) {
      els.agentsList.replaceChildren(emptyState("No agent strategies loaded."));
      return;
    }
    els.agentsList.replaceChildren(
      renderMessageRow({
        tone: "warning",
        text: "Saved Agent Strategy selections are not available in the current Dify list, so they will not be sent. Refresh strategies or clear the search.",
      }),
      ...state.agents.selected.map((agent) => agentOption(agent, false))
    );
    return;
  }
  const selectedKeys = new Set(state.agents.selected.map(agentKey));
  const renderedAgents = uniqueAgents([...state.agents.selected, ...state.agents.items]);
  els.agentsList.replaceChildren(
    ...renderedAgents.map((agent) => agentOption(agent, selectedKeys.has(agentKey(agent))))
  );
}

function agentOption(agent, checked) {
  const key = agentKey(agent);
  const configuredAgent = checked ? selectedAgentByKey(key) || compactAgentSelection(agent) : compactAgentSelection(agent);
  const label = document.createElement("div");
  label.className = `dataset-option tool-option${checked ? " is-selected" : ""}`;
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = checked;
  checkbox.dataset.agentKey = key;

  const body = document.createElement("span");
  body.className = "dataset-option-body";
  const name = document.createElement("span");
  name.className = "dataset-name";
  name.textContent = agent.agent_strategy_label || agent.agent_strategy_name;
  const meta = document.createElement("span");
  meta.className = "dataset-meta";
  meta.textContent = agentMeta(agent);
  const description = document.createElement("span");
  description.className = "dataset-description";
  description.textContent = agent.description || agent.agent_strategy_provider_name;
  body.append(name, meta, description);
  if (checked) {
    body.append(agentConfigurationPanel(configuredAgent));
  }
  label.append(checkbox, body);
  return label;
}

function agentMeta(agent) {
  const outputProperties = agent.output_schema?.properties && typeof agent.output_schema.properties === "object"
    ? Object.keys(agent.output_schema.properties).length
    : 0;
  return [
    agent.agent_strategy_provider_name,
    Array.isArray(agent.parameters) ? `${agent.parameters.length} params` : "",
    outputProperties ? `${outputProperties} outputs` : "",
    agent.requires_configuration ? "needs config" : "",
  ].filter(Boolean).join(" · ") || "agent";
}

function agentConfigurationPanel(agent) {
  const panel = document.createElement("div");
  panel.className = "tool-config";
  if (agent.requires_configuration) {
    panel.append(renderMessageRow({ tone: "warning", text: "This Agent Strategy has required parameters. Configure them before creating a workflow." }));
  }
  const parameters = Array.isArray(agent.parameters) ? agent.parameters : [];
  const section = document.createElement("div");
  section.className = "tool-config-section";
  const heading = document.createElement("div");
  heading.className = "tool-config-heading";
  heading.textContent = `Agent parameters · ${parameters.length || 0}`;
  section.append(heading);
  if (!parameters.length) {
    section.append(emptyState("No agent parameters."));
  } else {
    section.append(...parameters.map((schema) => agentConfigField(agent, schema)));
  }
  panel.append(section);
  return panel;
}

function agentConfigField(agent, schema) {
  const variable = schemaVariable(schema);
  const key = agentKey(agent);
  const value = agent.agent_parameters?.[variable] || defaultAgentInputForSchema(schema);
  const row = document.createElement("div");
  row.className = `tool-config-field${schema.required && !agentInputHasValue(value, schema) ? " is-missing" : ""}`;

  const label = document.createElement("div");
  label.className = "tool-config-label";
  label.textContent = `${localizedLabel(schema.label) || variable}${schema.required ? " *" : ""}`;
  const meta = document.createElement("div");
  meta.className = "tool-config-meta";
  meta.textContent = [
    variable,
    schema.type || "text-input",
    schema.default !== undefined && schema.default !== null && schema.default !== "" ? `default ${String(schema.default)}` : "",
  ].filter(Boolean).join(" · ");
  const controls = document.createElement("div");
  controls.className = "tool-config-controls";
  if (agentParameterIsToolSelector(schema)) {
    controls.append(agentToolSelectorControl(key, variable, value, schema));
  } else {
    controls.append(agentConfigModeControl(key, variable, value, schema));
    controls.append(agentConfigValueControl(key, variable, value, schema));
  }
  row.append(label, meta, controls);
  return row;
}

function toolOption(tool, checked) {
  const key = toolKey(tool);
  const configuredTool = checked ? selectedToolByKey(key) || compactToolSelection(tool) : compactToolSelection(tool);
  const label = document.createElement("div");
  label.className = `dataset-option tool-option${checked ? " is-selected" : ""}`;
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = checked;
  checkbox.dataset.toolKey = key;

  const body = document.createElement("span");
  body.className = "dataset-option-body";
  const name = document.createElement("span");
  name.className = "dataset-name";
  name.textContent = tool.tool_label || tool.tool_name;
  const meta = document.createElement("span");
  meta.className = "dataset-meta";
  meta.textContent = toolMeta(tool);
  const description = document.createElement("span");
  description.className = "dataset-description";
  description.textContent = tool.description || tool.provider_name || tool.provider_id;
  body.append(name, meta, description);
  if (checked) {
    body.append(toolConfigurationPanel(configuredTool));
  }
  label.append(checkbox, body);
  return label;
}

function toolMeta(tool) {
  return [
    tool.provider_type,
    tool.provider_name || tool.provider_id,
    Array.isArray(tool.parameters) ? `${tool.parameters.length} params` : "",
    tool.requires_configuration ? "needs config" : "",
  ].filter(Boolean).join(" · ") || "tool";
}

function toolConfigurationPanel(tool) {
  const panel = document.createElement("div");
  panel.className = "tool-config";
  if (tool.requires_configuration) {
    panel.append(renderMessageRow({ tone: "warning", text: "This tool may require credentials or provider configuration in Dify." }));
  }
  const schemas = Array.isArray(tool.parameters) ? tool.parameters : [];
  const inputSchemas = schemas.filter((schema) => schemaForm(schema) === "llm");
  const settingSchemas = schemas.filter((schema) => schemaForm(schema) !== "llm");
  panel.append(toolConfigSection(tool, "Input parameters", "tool_parameters", inputSchemas));
  panel.append(toolConfigSection(tool, "Settings", "tool_configurations", settingSchemas));
  return panel;
}

function toolConfigSection(tool, title, group, schemas) {
  const section = document.createElement("div");
  section.className = "tool-config-section";
  const heading = document.createElement("div");
  heading.className = "tool-config-heading";
  heading.textContent = `${title} · ${schemas.length || 0}`;
  section.append(heading);
  if (!schemas.length) {
    section.append(emptyState(group === "tool_parameters" ? "No runtime inputs." : "No settings."));
    return section;
  }
  section.append(...schemas.map((schema) => toolConfigField(tool, group, schema)));
  return section;
}

function toolConfigField(tool, group, schema) {
  const variable = schemaVariable(schema);
  const key = toolKey(tool);
  const map = group === "tool_parameters" ? tool.tool_parameters : tool.tool_configurations;
  const value = map?.[variable] || defaultToolInputForSchema(schema, group) || { type: defaultToolValueType(schema, group), value: "" };
  const row = document.createElement("div");
  row.className = `tool-config-field${schema.required && !toolInputHasValue(value) ? " is-missing" : ""}`;

  const label = document.createElement("div");
  label.className = "tool-config-label";
  label.textContent = `${localizedLabel(schema.label) || variable}${schema.required ? " *" : ""}`;
  const meta = document.createElement("div");
  meta.className = "tool-config-meta";
  meta.textContent = [
    variable,
    schema.type || "string",
    schema.form || "llm",
    schema.default !== undefined && schema.default !== null && schema.default !== "" ? `default ${String(schema.default)}` : "",
  ].filter(Boolean).join(" · ");
  const controls = document.createElement("div");
  controls.className = "tool-config-controls";
  controls.append(toolConfigModeControl(key, group, variable, value, schema));
  controls.append(toolConfigValueControl(key, group, variable, value, schema));
  row.append(label, meta, controls);
  return row;
}

function toolConfigModeControl(key, group, variable, value, schema) {
  const select = document.createElement("select");
  select.className = "tool-config-mode";
  select.dataset.toolConfigKey = key;
  select.dataset.toolParamGroup = group;
  select.dataset.toolParamName = variable;
  select.dataset.toolParamPart = "type";
  select.dataset.toolParamSchemaType = String(schema.type || "string");
  const modes = toolAllowedValueTypes(schema, group);
  for (const mode of modes) {
    const option = document.createElement("option");
    option.value = mode;
    option.textContent = mode;
    select.append(option);
  }
  select.value = modes.includes(value?.type) ? value.type : modes[0];
  return select;
}

function toolConfigValueControl(key, group, variable, value, schema) {
  const type = value?.type || defaultToolValueType(schema, group);
  const schemaType = normalizedSchemaType(schema);
  const options = Array.isArray(schema.options) ? schema.options : [];
  let control;
  if (type === "constant" && schemaType === "boolean") {
    control = document.createElement("select");
    for (const item of [
      ["true", "True"],
      ["false", "False"],
    ]) {
      const option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      control.append(option);
    }
    control.value = String(Boolean(value?.value));
  } else if (type === "constant" && options.length) {
    control = document.createElement("select");
    for (const item of options) {
      const option = document.createElement("option");
      option.value = String(item.value ?? "");
      option.textContent = localizedLabel(item.label) || String(item.value ?? "");
      control.append(option);
    }
    control.value = String(value?.value ?? "");
  } else {
    control = document.createElement("input");
    control.type = type === "constant" && isNumberSchema(schema) ? "number" : "text";
    control.placeholder = toolValuePlaceholder(type, schema, group);
    control.value = toolValueToDisplay(value, schema);
  }
  control.className = "tool-config-value";
  control.dataset.toolConfigKey = key;
  control.dataset.toolParamGroup = group;
  control.dataset.toolParamName = variable;
  control.dataset.toolParamPart = "value";
  control.dataset.toolParamSchemaType = String(schema.type || "string");
  return control;
}

function agentConfigModeControl(key, variable, value, schema) {
  const select = document.createElement("select");
  select.className = "tool-config-mode";
  select.dataset.agentConfigKey = key;
  select.dataset.agentParamName = variable;
  select.dataset.agentParamPart = "type";
  select.dataset.agentParamSchemaType = String(schema.type || "text-input");
  const modes = agentAllowedValueTypes(schema);
  for (const mode of modes) {
    const option = document.createElement("option");
    option.value = mode;
    option.textContent = mode;
    select.append(option);
  }
  select.value = modes.includes(value?.type) ? value.type : modes[0];
  return select;
}

function agentConfigValueControl(key, variable, value, schema) {
  const type = value?.type || defaultAgentValueType(schema);
  const schemaType = normalizedSchemaType(schema);
  const options = Array.isArray(schema.options) ? schema.options : [];
  let control;
  if (type === "constant" && schemaType === "boolean") {
    control = document.createElement("select");
    for (const item of [
      ["true", "True"],
      ["false", "False"],
    ]) {
      const option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      control.append(option);
    }
    control.value = String(Boolean(value?.value));
  } else if (type === "constant" && options.length) {
    control = document.createElement("select");
    for (const item of options) {
      const option = document.createElement("option");
      option.value = String(item.value ?? "");
      option.textContent = localizedLabel(item.label) || String(item.value ?? "");
      control.append(option);
    }
    control.value = String(value?.value ?? "");
  } else {
    control = document.createElement("input");
    control.type = type === "constant" && isNumberSchema(schema) ? "number" : "text";
    control.placeholder = agentValuePlaceholder(type, schema);
    control.value = toolValueToDisplay(value, schema);
  }
  control.className = "tool-config-value";
  control.dataset.agentConfigKey = key;
  control.dataset.agentParamName = variable;
  control.dataset.agentParamPart = "value";
  control.dataset.agentParamSchemaType = String(schema.type || "text-input");
  return control;
}

function agentToolSelectorControl(key, variable, value, schema) {
  const wrapper = document.createElement("div");
  wrapper.className = "tool-selector-control";
  const selectedTools = currentToolSelections();
  if (!selectedTools.length) {
    wrapper.append(renderMessageRow({ tone: "warning", text: "Select and configure at least one Tool before binding this Agent parameter." }));
    return wrapper;
  }

  if (agentParameterIsMultiToolSelector(schema)) {
    selectedTools.forEach((tool) => {
      const optionLabel = document.createElement("label");
      optionLabel.className = "checkbox-line compact-checkbox";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.agentConfigKey = key;
      checkbox.dataset.agentParamName = variable;
      checkbox.dataset.agentParamPart = "tool";
      checkbox.dataset.agentToolKey = toolKey(tool);
      checkbox.checked = agentToolValueIncludes(value, tool);
      optionLabel.append(checkbox, document.createTextNode(tool.tool_label || tool.tool_name));
      wrapper.append(optionLabel);
    });
    return wrapper;
  }

  const select = document.createElement("select");
  select.className = "tool-config-value";
  select.dataset.agentConfigKey = key;
  select.dataset.agentParamName = variable;
  select.dataset.agentParamPart = "tool";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Select tool";
  select.append(empty);
  for (const tool of selectedTools) {
    const option = document.createElement("option");
    option.value = toolKey(tool);
    option.textContent = tool.tool_label || tool.tool_name;
    select.append(option);
  }
  select.value = agentSingleToolKey(value) || "";
  wrapper.append(select);
  return wrapper;
}

function toggleToolSelection(key, selected) {
  if (!key) {
    return;
  }
  if (selected) {
    const tool = state.tools.items.find((item) => toolKey(item) === key);
    if (tool) {
      state.tools.selected = uniqueTools([...state.tools.selected, compactToolSelection(tool)]);
    }
  } else {
    state.tools.selected = state.tools.selected.filter((item) => toolKey(item) !== key);
    state.agents.selected = state.agents.selected.map((agent) => pruneAgentToolBindings(agent, key));
    saveSelectedAgents();
    renderAgentStrategies();
  }
  saveSelectedTools();
  renderTools();
  markModifyPreviewDirty();
}

function handleToolConfigInput(field) {
  const key = field.dataset.toolConfigKey;
  const group = field.dataset.toolParamGroup;
  const name = field.dataset.toolParamName;
  const part = field.dataset.toolParamPart;
  if (!key || !group || !name || !part) {
    return;
  }
  const tool = selectedToolByKey(key);
  if (!tool) {
    return;
  }
  const schema = (tool.parameters || []).find((item) => schemaVariable(item) === name) || { name, type: field.dataset.toolParamSchemaType };
  const mapName = group === "tool_configurations" ? "tool_configurations" : "tool_parameters";
  const current = tool[mapName]?.[name] || defaultToolInputForSchema(schema, group) || { type: defaultToolValueType(schema, group), value: "" };
  if (!tool[mapName] || typeof tool[mapName] !== "object") {
    tool[mapName] = {};
  }
  if (part === "type") {
    const nextType = field.value;
    tool[mapName][name] = {
      type: nextType,
      value: defaultValueForToolInputType(nextType, schema, group),
    };
    saveSelectedTools();
    renderTools();
    markModifyPreviewDirty();
    return;
  }
  tool[mapName][name] = {
    type: current.type || defaultToolValueType(schema, group),
    value: parseToolConfigValue(field.value, current.type || defaultToolValueType(schema, group), schema),
  };
  saveSelectedTools();
  markModifyPreviewDirty();
}

function toggleAgentSelection(key, selected) {
  if (!key) {
    return;
  }
  if (selected) {
    const agent = state.agents.items.find((item) => agentKey(item) === key);
    if (agent) {
      state.agents.selected = uniqueAgents([...state.agents.selected, compactAgentSelection(agent)]);
    }
  } else {
    state.agents.selected = state.agents.selected.filter((item) => agentKey(item) !== key);
  }
  saveSelectedAgents();
  renderAgentStrategies();
  markModifyPreviewDirty();
}

function handleAgentConfigInput(field) {
  const key = field.dataset.agentConfigKey;
  const name = field.dataset.agentParamName;
  const part = field.dataset.agentParamPart;
  if (!key || !name || !part) {
    return;
  }
  const agent = selectedAgentByKey(key);
  if (!agent) {
    return;
  }
  const schema = (agent.parameters || []).find((item) => schemaVariable(item) === name) || { name, type: field.dataset.agentParamSchemaType };
  if (!agent.agent_parameters || typeof agent.agent_parameters !== "object") {
    agent.agent_parameters = {};
  }
  const current = agent.agent_parameters[name] || defaultAgentInputForSchema(schema) || { type: defaultAgentValueType(schema), value: "" };
  if (part === "type") {
    const nextType = field.value;
    agent.agent_parameters[name] = {
      type: nextType,
      value: defaultValueForToolInputType(nextType, schema, "agent_parameters"),
    };
    saveSelectedAgents();
    renderAgentStrategies();
    markModifyPreviewDirty();
    return;
  }
  if (part === "tool") {
    agent.agent_parameters[name] = nextAgentToolSelectorValue(field, current, schema);
    saveSelectedAgents();
    renderAgentStrategies();
    markModifyPreviewDirty();
    return;
  }
  agent.agent_parameters[name] = {
    type: current.type || defaultAgentValueType(schema),
    value: parseToolConfigValue(field.value, current.type || defaultAgentValueType(schema), schema),
  };
  saveSelectedAgents();
  markModifyPreviewDirty();
}

function hydrateSelectedToolsFromLoaded() {
  if (!state.tools.selected.length || !state.tools.items.length) {
    return;
  }
  state.tools.selected = uniqueTools(state.tools.selected.map((selected) => {
    const latest = state.tools.items.find((item) => toolKey(item) === toolKey(selected));
    if (!latest) {
      return selected;
    }
    return {
      ...latest,
      tool_parameters: selected.tool_parameters || {},
      tool_configurations: selected.tool_configurations || {},
    };
  }));
  saveSelectedTools();
}

function hydrateSelectedAgentsFromLoaded() {
  if (!state.agents.selected.length || !state.agents.items.length) {
    return;
  }
  state.agents.selected = uniqueAgents(state.agents.selected.map((selected) => {
    const latest = state.agents.items.find((item) => agentKey(item) === agentKey(selected));
    if (!latest) {
      return selected;
    }
    return {
      ...latest,
      agent_parameters: selected.agent_parameters || {},
    };
  }));
  saveSelectedAgents();
}

function selectedToolByKey(key) {
  return state.tools.selected.find((tool) => toolKey(tool) === key);
}

function selectedAgentByKey(key) {
  return state.agents.selected.find((agent) => agentKey(agent) === key);
}

function schemaVariable(schema = {}) {
  return String(schema.variable || schema.name || "").trim();
}

function schemaForm(schema = {}) {
  return String(schema.form || "llm").trim() || "llm";
}

function normalizedSchemaType(schema = {}) {
  return String(schema.type || "string").trim().toLowerCase();
}

function localizedLabel(value) {
  if (!value) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "object") {
    return value.zh_Hans || value.en_US || value.zh_CN || value.label || value.text || Object.values(value).find(Boolean) || "";
  }
  return String(value);
}

function toolAllowedValueTypes(schema, group) {
  const type = normalizedSchemaType(schema);
  if (type === "file" || type === "files") {
    return ["variable"];
  }
  if (group === "tool_configurations" && ["boolean", "select", "checkbox", "number", "number-input"].includes(type)) {
    return ["constant", "variable"];
  }
  if (group === "tool_configurations") {
    return ["mixed", "constant", "variable"];
  }
  if (toolSchemaUsesMixedText(schema)) {
    return ["mixed", "variable", "constant"];
  }
  return ["variable", "constant", "mixed"];
}

function defaultToolValueType(schema, group) {
  const type = normalizedSchemaType(schema);
  if (type === "file" || type === "files") {
    return "variable";
  }
  if (group === "tool_configurations" && ["boolean", "select", "checkbox", "number", "number-input"].includes(type)) {
    return "constant";
  }
  if (toolSchemaUsesMixedText(schema)) {
    return "mixed";
  }
  return "variable";
}

function defaultToolInputForSchema(schema, group) {
  const defaultValue = schema.default;
  if (defaultValue !== undefined && defaultValue !== null && defaultValue !== "") {
    return normalizeToolInputValue(defaultValue, schema, group);
  }
  const options = Array.isArray(schema.options) ? schema.options : [];
  if (schema.required && options.length && options[0]?.value !== undefined && options[0]?.value !== "") {
    return normalizeToolInputValue(options[0].value, schema, group);
  }
  if (group === "tool_parameters" && schema.required && isQueryLikeToolParameter(schemaVariable(schema)) && toolSchemaUsesMixedText(schema)) {
    return { type: "mixed", value: DEFAULT_TOOL_QUERY_TEMPLATE };
  }
  return null;
}

function normalizeToolInputValue(value, schema, group) {
  if (value && typeof value === "object" && "type" in value && "value" in value) {
    return {
      type: value.type || defaultToolValueType(schema, group),
      value: value.type === "variable" ? normalizeSelectorValue(value.value) : value.value,
    };
  }
  const type = defaultToolValueType(schema, group);
  return {
    type,
    value: parseToolConfigValue(value, type, schema),
  };
}

function defaultValueForToolInputType(type, schema, group) {
  if (type === "variable") {
    return isQueryLikeToolParameter(schemaVariable(schema)) ? ["start", "query"] : [];
  }
  const configured = defaultToolInputForSchema(schema, group);
  if (configured && configured.type === type) {
    return configured.value;
  }
  if (type === "constant" && normalizedSchemaType(schema) === "boolean") {
    return false;
  }
  return "";
}

function parseToolConfigValue(value, type, schema) {
  if (type === "variable") {
    return normalizeSelectorValue(value);
  }
  if (type === "constant") {
    const schemaType = normalizedSchemaType(schema);
    if (schemaType === "boolean") {
      return String(value).toLowerCase() === "true" || value === true || value === 1;
    }
    if (schemaType === "model-selector" || schemaType === "app-selector") {
      if (value && typeof value === "object") {
        return value;
      }
      try {
        return JSON.parse(String(value || "{}"));
      } catch (error) {
        return value;
      }
    }
    if (isNumberSchema(schema) && value !== "") {
      const parsed = Number(value);
      return Number.isNaN(parsed) ? value : parsed;
    }
    return value;
  }
  return String(value ?? "");
}

function toolValueToDisplay(value, schema) {
  if (!value || typeof value !== "object") {
    return "";
  }
  if (value.type === "variable") {
    return Array.isArray(value.value) ? value.value.join(".") : String(value.value || "");
  }
  if (value.type === "constant" && normalizedSchemaType(schema) === "boolean") {
    return String(Boolean(value.value));
  }
  if (value.value && typeof value.value === "object") {
    return JSON.stringify(value.value);
  }
  return value.value === undefined || value.value === null ? "" : String(value.value);
}

function toolValuePlaceholder(type, schema, group) {
  if (type === "variable") {
    return "start.query";
  }
  if (type === "mixed") {
    return group === "tool_parameters" ? DEFAULT_TOOL_QUERY_TEMPLATE : "constant text or {{#start.query#}}";
  }
  return schema.default !== undefined && schema.default !== null ? String(schema.default) : "constant value";
}

function normalizeSelectorValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  return String(value || "")
    .replace(/^\{\{\s*#?/, "")
    .replace(/#?\s*\}\}$/, "")
    .split(".")
    .map((item) => item.trim())
    .filter(Boolean);
}

function toolSchemaUsesMixedText(schema) {
  return ["", "string", "text-input", "secret-input"].includes(normalizedSchemaType(schema));
}

function isNumberSchema(schema) {
  return ["number", "number-input", "text-number"].includes(normalizedSchemaType(schema));
}

function isQueryLikeToolParameter(name) {
  return ["query", "q", "question", "input", "text", "keyword", "keywords", "url"].includes(
    String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
  );
}

function toolInputHasValue(value) {
  if (!value || typeof value !== "object") {
    return false;
  }
  if (value.value === undefined || value.value === null || value.value === "") {
    return false;
  }
  return !(Array.isArray(value.value) && value.value.length === 0);
}

function agentAllowedValueTypes(schema) {
  const type = normalizedSchemaType(schema);
  if (type === "model-selector" || type === "app-selector") {
    return ["constant"];
  }
  if (type === "any" || type === "file" || type === "files") {
    return ["variable", "constant", "mixed"];
  }
  if (["boolean", "checkbox", "select", "number", "number-input", "text-number"].includes(type)) {
    return ["constant", "variable", "mixed"];
  }
  return ["mixed", "variable", "constant"];
}

function defaultAgentValueType(schema) {
  const type = normalizedSchemaType(schema);
  if (type === "model-selector" || type === "app-selector") {
    return "constant";
  }
  if (type === "any" || type === "file" || type === "files") {
    return "variable";
  }
  if (["boolean", "checkbox", "select", "number", "number-input", "text-number"].includes(type)) {
    return "constant";
  }
  return "constant";
}

function defaultAgentInputForSchema(schema) {
  const variable = schemaVariable(schema);
  const type = normalizedSchemaType(schema);
  if (type === "model-selector" && state.defaultModel.provider && state.defaultModel.name) {
    return {
      type: "constant",
      value: {
        provider: state.defaultModel.provider,
        model: state.defaultModel.name,
        model_type: "llm",
        mode: "chat",
        completion_params: {},
      },
    };
  }
  const defaultValue = schema.default;
  if (defaultValue !== undefined && defaultValue !== null && defaultValue !== "") {
    return normalizeAgentInputValue(defaultValue, schema);
  }
  const options = Array.isArray(schema.options) ? schema.options : [];
  if (schema.required && options.length && options[0]?.value !== undefined && options[0]?.value !== "") {
    return normalizeAgentInputValue(options[0].value, schema);
  }
  if (agentParameterIsToolSelector(schema)) {
    const tools = currentToolSelections();
    if (!tools.length) {
      return { type: "constant", value: agentParameterIsMultiToolSelector(schema) ? [] : null };
    }
    return {
      type: "constant",
      value: agentParameterIsMultiToolSelector(schema)
        ? tools.map(agentToolValueFromSelection)
        : agentToolValueFromSelection(tools[0]),
    };
  }
  if (schema.required && variable === "instruction") {
    return {
      type: "constant",
      value: "你是售后分析智能体。请基于用户输入和已绑定工具的结果进行多步分析，识别售后问题类型、关键信息、风险等级和处理建议；不要编造未查询到的信息；最终给出清晰、可执行、礼貌的售后处理建议。",
    };
  }
  if (schema.required && isQueryLikeToolParameter(variable)) {
    return { type: "constant", value: DEFAULT_TOOL_QUERY_TEMPLATE };
  }
  return null;
}

function normalizeAgentInputValue(value, schema) {
  if (value && typeof value === "object" && "type" in value && "value" in value) {
    return {
      type: value.type || defaultAgentValueType(schema),
      value: value.type === "variable" ? normalizeSelectorValue(value.value) : value.value,
    };
  }
  const type = defaultAgentValueType(schema);
  return {
    type,
    value: parseToolConfigValue(value, type, schema),
  };
}

function agentValuePlaceholder(type, schema) {
  if (normalizedSchemaType(schema) === "model-selector") {
    return state.defaultModel.provider && state.defaultModel.name
      ? JSON.stringify({
          provider: state.defaultModel.provider,
          model: state.defaultModel.name,
          model_type: "llm",
          mode: "chat",
          completion_params: {},
        })
      : '{"provider":"...","model":"...","model_type":"llm","mode":"chat","completion_params":{}}';
  }
  if (type === "variable") {
    return "start.query";
  }
  if (type === "mixed") {
    return isQueryLikeToolParameter(schemaVariable(schema)) ? DEFAULT_TOOL_QUERY_TEMPLATE : "constant text or {{#start.query#}}";
  }
  return schema.default !== undefined && schema.default !== null ? String(schema.default) : "constant value";
}

function agentInputHasValue(value, schema) {
  if (!value || typeof value !== "object") {
    return false;
  }
  if (normalizedSchemaType(schema) === "model-selector") {
    return Boolean(
      value.value
      && typeof value.value === "object"
      && value.value.provider
      && (value.value.model || value.value.name)
    );
  }
  if (agentParameterIsToolSelector(schema)) {
    if (agentParameterIsMultiToolSelector(schema)) {
      return Array.isArray(value.value) && value.value.length > 0;
    }
    return Boolean(value.value && typeof value.value === "object");
  }
  return toolInputHasValue(value);
}

function agentParameterIsToolSelector(schema = {}) {
  return ["tool-selector", "multi-tool-selector", "array[tools]"].includes(normalizedSchemaType(schema));
}

function agentParameterIsMultiToolSelector(schema = {}) {
  return ["multi-tool-selector", "array[tools]"].includes(normalizedSchemaType(schema));
}

function nextAgentToolSelectorValue(field, current, schema) {
  const tools = currentToolSelections();
  if (agentParameterIsMultiToolSelector(schema)) {
    const currentValues = Array.isArray(current?.value) ? current.value : [];
    const byKey = new Map(currentValues.map((tool) => [agentToolKeyFromValue(tool), tool]));
    const selectedTool = tools.find((tool) => toolKey(tool) === field.dataset.agentToolKey);
    if (field.checked && selectedTool) {
      byKey.set(toolKey(selectedTool), agentToolValueFromSelection(selectedTool));
    } else {
      byKey.delete(field.dataset.agentToolKey || "");
    }
    return { type: "constant", value: Array.from(byKey.values()) };
  }
  const selectedTool = tools.find((tool) => toolKey(tool) === field.value);
  return { type: "constant", value: selectedTool ? agentToolValueFromSelection(selectedTool) : null };
}

function agentToolValueFromSelection(tool = {}) {
  const schemas = Array.isArray(tool.parameters) ? tool.parameters : [];
  return {
    enabled: true,
    type: tool.provider_type || "builtin",
    provider_name: tool.provider_id || tool.provider_name || "",
    provider_id: tool.provider_id || "",
    tool_name: tool.tool_name || "",
    tool_label: tool.tool_label || tool.tool_name || "",
    tool_description: tool.description || "",
    plugin_unique_identifier: tool.plugin_unique_identifier || undefined,
    credential_id: tool.credential_id || undefined,
    schemas,
    settings: wrapAgentToolSettings(tool.tool_configurations || {}, schemas),
    parameters: wrapAgentToolParameters(tool.tool_parameters || {}, schemas),
    output_schema: tool.output_schema && typeof tool.output_schema === "object" ? tool.output_schema : {},
  };
}

function wrapAgentToolSettings(values, schemas) {
  const result = {};
  schemas.filter((schema) => schemaForm(schema) !== "llm").forEach((schema) => {
    const variable = schemaVariable(schema);
    const value = values[variable] || defaultToolInputForSchema(schema, "tool_configurations");
    if (value) {
      result[variable] = { value };
    }
  });
  Object.entries(values || {}).forEach(([key, value]) => {
    if (!result[key]) {
      result[key] = { value };
    }
  });
  return result;
}

function wrapAgentToolParameters(values, schemas) {
  const result = {};
  schemas.filter((schema) => schemaForm(schema) === "llm").forEach((schema) => {
    const variable = schemaVariable(schema);
    const value = values[variable] || defaultToolInputForSchema(schema, "tool_parameters");
    if (value) {
      result[variable] = { auto: 0, value };
    }
  });
  Object.entries(values || {}).forEach(([key, value]) => {
    if (!result[key]) {
      result[key] = { auto: 0, value };
    }
  });
  return result;
}

function agentToolValueIncludes(value, tool) {
  if (!value || !Array.isArray(value.value)) {
    return false;
  }
  return value.value.some((item) => agentToolKeyFromValue(item) === toolKey(tool));
}

function agentSingleToolKey(value) {
  return value?.value && typeof value.value === "object" ? agentToolKeyFromValue(value.value) : "";
}

function agentToolKeyFromValue(value = {}) {
  return [value.type || "", value.provider_name || value.provider_id || "", value.tool_name || ""].join("::");
}

function handleTriggerFormChange(event) {
  const changedInsidePluginPanel = Boolean(
    event?.target?.closest && event.target.closest("#trigger-plugin-fields")
  );
  if (!changedInsidePluginPanel) {
    renderTriggerForm();
  }
  try {
    state.triggerSelection = currentTriggerSelection();
    saveTriggerSelection();
    setPanelStatus(
      els.triggerStatus,
      triggerTypeLabel(state.triggerSelection.type),
      state.triggerSelection.type === "user-input" ? "muted" : "ok"
    );
  } catch (error) {
    setPanelStatus(els.triggerStatus, "Invalid", "error");
  }
  markModifyPreviewDirty();
}

function renderTriggerForm() {
  const type = els.triggerType.value || "user-input";
  els.triggerWebhookFields.classList.toggle("is-hidden", type !== "webhook");
  els.triggerPluginFields.classList.toggle("is-hidden", type !== "plugin");
  els.triggerScheduleFields.classList.toggle("is-hidden", type !== "schedule");
  const cronMode = type === "schedule" && els.triggerScheduleMode.value === "cron";
  els.triggerScheduleVisual.classList.toggle("is-hidden", cronMode);
  els.triggerScheduleCronField.classList.toggle("is-hidden", !cronMode);
  if (type === "plugin") {
    renderPluginTriggerForm();
    if (!state.pluginTriggers.loaded && !state.pluginTriggers.loading) {
      loadTriggerProviders();
    }
  }
  setPanelStatus(els.triggerStatus, triggerTypeLabel(type), type === "user-input" ? "muted" : "ok");
}

function currentTriggerSelection() {
  const type = els.triggerType.value || "user-input";
  if (type === "user-input") {
    return { type: "user-input" };
  }
  if (type === "webhook") {
    return {
      type: "webhook",
      method: els.triggerWebhookMethod.value || "POST",
      content_type: els.triggerWebhookContentType.value || "application/json",
      headers: parseTriggerParameters(els.triggerWebhookHeaders.value, "headers"),
      params: parseTriggerParameters(els.triggerWebhookQuery.value, "query parameters"),
      body: parseTriggerParameters(els.triggerWebhookBody.value, "body parameters"),
      status_code: boundedNumber(els.triggerWebhookStatusCode.value, 100, 599, 200),
      response_body: els.triggerWebhookResponse.value || "",
      timeout: boundedNumber(els.triggerWebhookTimeout.value, 1, 300, 30),
    };
  }
  if (type === "plugin") {
    const event = selectedPluginTriggerEvent();
    if (!event) {
      throw new Error("Select a Plugin Trigger provider event.");
    }
    const subscriptionId = els.triggerPluginSubscription.value;
    if (!subscriptionId) {
      throw new Error("Select an existing Dify Trigger subscription.");
    }
    const eventParameters = {};
    for (const schema of event.parameters || []) {
      const name = schemaVariable(schema);
      if (!name) {
        continue;
      }
      const field = els.triggerPluginParameters.querySelector(`[data-trigger-plugin-param="${cssEscape(name)}"]`);
      const value = pluginTriggerFieldValue(field, schema);
      if (schema.required && pluginTriggerValueMissing(value)) {
        throw new Error(`Plugin Trigger parameter "${localizedLabel(schema.label) || name}" is required.`);
      }
      if (!pluginTriggerValueMissing(value)) {
        eventParameters[name] = { type: "constant", value };
      }
    }
    return {
      type: "plugin",
      provider_id: event.provider_id,
      event_name: event.event_name,
      subscription_id: subscriptionId,
      event_parameters: eventParameters,
    };
  }
  const mode = els.triggerScheduleMode.value || "visual";
  const selection = {
    type: "schedule",
    mode,
    timezone: els.triggerScheduleTimezone.value.trim() || "Asia/Shanghai",
  };
  if (mode === "cron") {
    selection.cron_expression = els.triggerScheduleCron.value.trim();
    return selection;
  }
  selection.frequency = els.triggerScheduleFrequency.value || "daily";
  selection.visual_config = {
    time: toTwelveHourTime(els.triggerScheduleTime.value || "09:00"),
    weekdays: splitValues(els.triggerScheduleWeekdays.value).map((item) => item.toLowerCase()),
    on_minute: boundedNumber(els.triggerScheduleMinute.value, 0, 59, 0),
    monthly_days: splitValues(els.triggerScheduleMonthlyDays.value).map((item) => {
      if (item.toLowerCase() === "last") {
        return "last";
      }
      return Number(item);
    }),
  };
  return selection;
}

function parseTriggerParameters(value, label) {
  const seen = new Set();
  return String(value || "")
    .split(/\n|,/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const required = line.endsWith("*");
      const normalized = required ? line.slice(0, -1).trim() : line;
      const separator = normalized.indexOf(":");
      const name = (separator >= 0 ? normalized.slice(0, separator) : normalized).trim();
      const type = (separator >= 0 ? normalized.slice(separator + 1) : "string").trim() || "string";
      if (!/^[A-Za-z_][A-Za-z0-9_-]*$/.test(name)) {
        throw new Error(`${label}: invalid parameter name "${name}".`);
      }
      const outputName = label === "headers" ? name.replaceAll("-", "_") : name;
      if (seen.has(outputName)) {
        throw new Error(`${label}: duplicate parameter "${name}".`);
      }
      seen.add(outputName);
      return { name, type, required };
    });
}

function restoreTriggerForm(selection) {
  const value = selection && typeof selection === "object" ? selection : { type: "user-input" };
  els.triggerType.value = value.type || "user-input";
  els.triggerWebhookMethod.value = value.method || "POST";
  els.triggerWebhookContentType.value = value.content_type || "application/json";
  els.triggerWebhookHeaders.value = formatTriggerParameters(value.headers);
  els.triggerWebhookQuery.value = formatTriggerParameters(value.params);
  els.triggerWebhookBody.value = formatTriggerParameters(value.body);
  els.triggerWebhookStatusCode.value = String(value.status_code || 200);
  els.triggerWebhookTimeout.value = String(value.timeout || 30);
  els.triggerWebhookResponse.value = value.response_body || "";
  els.triggerScheduleMode.value = value.mode || "visual";
  els.triggerScheduleTimezone.value = value.timezone || "Asia/Shanghai";
  els.triggerScheduleFrequency.value = value.frequency || "daily";
  const visual = value.visual_config && typeof value.visual_config === "object" ? value.visual_config : {};
  els.triggerScheduleTime.value = toTwentyFourHourTime(visual.time || "09:00 AM");
  els.triggerScheduleMinute.value = String(visual.on_minute ?? 0);
  els.triggerScheduleWeekdays.value = (visual.weekdays || ["mon"]).join(",");
  els.triggerScheduleMonthlyDays.value = (visual.monthly_days || [1]).join(",");
  els.triggerScheduleCron.value = value.cron_expression || "0 9 * * *";
  if (value.type === "plugin") {
    state.pluginTriggers.keyword = "";
    els.triggerPluginSearch.value = "";
  }
}

function loadTriggerSelection() {
  try {
    const parsed = JSON.parse(localStorage.getItem(TRIGGER_SELECTION_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : { type: "user-input" };
  } catch (error) {
    return { type: "user-input" };
  }
}

function formatTriggerParameters(items) {
  return (Array.isArray(items) ? items : [])
    .map((item) => `${item.name || ""}:${item.type || "string"}${item.required ? "*" : ""}`)
    .filter((item) => !item.startsWith(":"))
    .join("\n");
}

function triggerTypeLabel(type) {
  return {
    "user-input": "User Input",
    webhook: "Webhook",
    plugin: "Plugin Trigger",
    schedule: "Schedule",
  }[type] || type;
}

function saveTriggerSelection() {
  localStorage.setItem(
    TRIGGER_SELECTION_KEY,
    JSON.stringify(state.triggerSelection || { type: "user-input" })
  );
}

async function loadTriggerProviders() {
  const keyword = els.triggerPluginSearch.value.trim();
  const params = new URLSearchParams();
  if (keyword) {
    params.set("keyword", keyword);
  }
  try {
    state.pluginTriggers.loading = true;
    els.triggerPluginRefresh.disabled = true;
    els.triggerPluginMessage.textContent = "Loading installed Trigger Providers...";
    const suffix = params.toString() ? `?${params.toString()}` : "";
    const data = await requestJson(`/api/dify/trigger-providers${suffix}`);
    state.pluginTriggers.items = Array.isArray(data.data) ? data.data : [];
    state.pluginTriggers.loaded = true;
    state.pluginTriggers.keyword = keyword;
    renderPluginTriggerForm();
    const selected = selectedPluginTriggerEvent();
    if (selected) {
      await loadTriggerSubscriptions(selected.provider_id);
    }
    renderPluginTriggerForm();
  } catch (error) {
    state.pluginTriggers.items = [];
    state.pluginTriggers.subscriptions = [];
    state.pluginTriggers.loaded = true;
    renderPluginTriggerForm(error.message);
  } finally {
    state.pluginTriggers.loading = false;
    els.triggerPluginRefresh.disabled = false;
  }
}

async function loadTriggerSubscriptions(providerId) {
  state.pluginTriggers.subscriptions = [];
  if (!providerId) {
    renderPluginTriggerForm();
    return;
  }
  try {
    els.triggerPluginMessage.textContent = "Loading existing subscriptions...";
    const data = await requestJson(
      `/api/dify/trigger-subscriptions?provider_id=${encodeURIComponent(providerId)}`
    );
    state.pluginTriggers.subscriptions = Array.isArray(data.data) ? data.data : [];
  } catch (error) {
    state.pluginTriggers.subscriptions = [];
    renderPluginTriggerForm(error.message);
  }
}

function renderPluginTriggerForm(errorMessage = "") {
  const saved = state.triggerSelection?.type === "plugin" ? state.triggerSelection : {};
  const currentKey = els.triggerPluginEvent.value
    || pluginTriggerEventKey(saved.provider_id, saved.event_name);
  const visibleItems = state.pluginTriggers.items.filter((item) => {
    const needle = els.triggerPluginSearch.value.trim().toLowerCase();
    if (!needle) {
      return true;
    }
    return [
      item.provider_id,
      item.provider_label,
      item.event_name,
      item.event_label,
      item.description,
      item.event_description,
    ].filter(Boolean).join(" ").toLowerCase().includes(needle);
  });
  const options = [new Option("Select an installed trigger event", "")];
  for (const item of visibleItems) {
    options.push(
      new Option(
        `${item.provider_label || item.provider_name || item.provider_id} · ${item.event_label || item.event_name}`,
        pluginTriggerEventKey(item.provider_id, item.event_name)
      )
    );
  }
  els.triggerPluginEvent.replaceChildren(...options);
  if (visibleItems.some((item) => pluginTriggerEventKey(item.provider_id, item.event_name) === currentKey)) {
    els.triggerPluginEvent.value = currentKey;
  }

  const event = selectedPluginTriggerEvent();
  const subscriptionOptions = [new Option("Select a configured subscription", "")];
  for (const item of state.pluginTriggers.subscriptions) {
    subscriptionOptions.push(
      new Option(
        `${item.name || item.id}${item.workflows_in_use ? ` · ${item.workflows_in_use} workflow(s)` : ""}`,
        item.id
      )
    );
  }
  els.triggerPluginSubscription.replaceChildren(...subscriptionOptions);
  const savedSubscription = String(saved.subscription_id || "");
  if (state.pluginTriggers.subscriptions.some((item) => item.id === savedSubscription)) {
    els.triggerPluginSubscription.value = savedSubscription;
  }

  renderPluginTriggerParameters(event, saved);
  if (errorMessage) {
    els.triggerPluginMessage.textContent = errorMessage;
    return;
  }
  if (!state.pluginTriggers.loaded) {
    els.triggerPluginMessage.textContent = "Select Plugin Trigger to load installed providers.";
  } else if (!state.pluginTriggers.items.length) {
    els.triggerPluginMessage.textContent = "No installed Trigger Provider found in Dify.";
  } else if (!event) {
    els.triggerPluginMessage.textContent = `${state.pluginTriggers.items.length} event(s) found.`;
  } else if (!state.pluginTriggers.subscriptions.length) {
    els.triggerPluginMessage.textContent = "No existing subscription for this provider. Create one in Dify first.";
  } else {
    els.triggerPluginMessage.textContent = `${state.pluginTriggers.subscriptions.length} subscription(s) available.`;
  }
}

function renderPluginTriggerParameters(event, saved) {
  if (!event) {
    els.triggerPluginParameters.replaceChildren(emptyState("Select an event to configure its constant parameters."));
    return;
  }
  const schemas = Array.isArray(event.parameters) ? event.parameters : [];
  if (!schemas.length) {
    els.triggerPluginParameters.replaceChildren(emptyState("This event has no parameters."));
    return;
  }
  const savedMatches = saved.provider_id === event.provider_id && saved.event_name === event.event_name;
  const values = savedMatches && saved.event_parameters && typeof saved.event_parameters === "object"
    ? saved.event_parameters
    : {};
  const section = document.createElement("div");
  section.className = "tool-config-section";
  const heading = document.createElement("div");
  heading.className = "tool-config-heading";
  heading.textContent = `EVENT PARAMETERS · ${schemas.length}`;
  section.append(heading);
  for (const schema of schemas) {
    section.append(pluginTriggerParameterField(schema, values[schemaVariable(schema)]));
  }
  els.triggerPluginParameters.replaceChildren(section);
}

function pluginTriggerParameterField(schema, configured) {
  const name = schemaVariable(schema);
  const wrapper = document.createElement("label");
  wrapper.className = "tool-config-field";
  const label = document.createElement("span");
  label.className = "tool-config-label";
  label.textContent = `${localizedLabel(schema.label) || name}${schema.required ? " *" : ""}`;
  const meta = document.createElement("span");
  meta.className = "tool-config-meta";
  meta.textContent = `${name} · ${normalizedSchemaType(schema)} · constant`;
  wrapper.append(label, meta);

  const value = configured && typeof configured === "object" && "value" in configured
    ? configured.value
    : schema.default;
  const type = normalizedSchemaType(schema);
  let field;
  if (type === "boolean" || type === "checkbox") {
    const line = document.createElement("span");
    line.className = "compact-checkbox";
    field = document.createElement("input");
    field.type = "checkbox";
    field.checked = value === true || String(value).toLowerCase() === "true";
    line.append(field, document.createTextNode("Enabled"));
    wrapper.append(line);
  } else if (type === "select" || type === "dynamic-select") {
    field = document.createElement("select");
    const options = Array.isArray(schema.options) ? schema.options : [];
    field.append(new Option("Select a value", ""));
    for (const option of options) {
      field.append(new Option(localizedLabel(option.label) || String(option.value), String(option.value)));
    }
    field.value = value === undefined || value === null ? "" : String(value);
    wrapper.append(field);
  } else {
    field = document.createElement("input");
    field.type = type === "number" ? "number" : "text";
    field.value = value === undefined || value === null
      ? ""
      : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
    field.placeholder = localizedLabel(schema.description) || "Constant value";
    wrapper.append(field);
  }
  field.dataset.triggerPluginParam = name;
  field.dataset.triggerPluginParamType = type;
  return wrapper;
}

function pluginTriggerFieldValue(field, schema) {
  if (!field) {
    return schema.default;
  }
  const type = normalizedSchemaType(schema);
  if (type === "boolean" || type === "checkbox") {
    return Boolean(field.checked);
  }
  const raw = String(field.value ?? "").trim();
  if (!raw) {
    return "";
  }
  if (type === "number") {
    const number = Number(raw);
    return Number.isNaN(number) ? raw : number;
  }
  if (["object", "array", "files"].includes(type)) {
    try {
      return JSON.parse(raw);
    } catch (error) {
      throw new Error(`Plugin Trigger parameter "${schemaVariable(schema)}" must be valid JSON.`);
    }
  }
  return raw;
}

function pluginTriggerValueMissing(value) {
  return value === undefined || value === null || value === ""
    || (Array.isArray(value) && value.length === 0);
}

function selectedPluginTriggerEvent() {
  const [providerId, eventName] = splitPluginTriggerEventKey(els.triggerPluginEvent.value);
  return state.pluginTriggers.items.find(
    (item) => item.provider_id === providerId && item.event_name === eventName
  );
}

function pluginTriggerEventKey(providerId, eventName) {
  return `${String(providerId || "")}::${String(eventName || "")}`;
}

function splitPluginTriggerEventKey(value) {
  const separator = String(value || "").indexOf("::");
  if (separator < 0) {
    return ["", ""];
  }
  return [value.slice(0, separator), value.slice(separator + 2)];
}

function cssEscape(value) {
  return window.CSS?.escape ? window.CSS.escape(String(value)) : String(value).replace(/["\\]/g, "\\$&");
}

function splitValues(value) {
  return String(value || "").split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
}

function boundedNumber(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, Math.trunc(number)));
}

function toTwelveHourTime(value) {
  const [hoursText, minutesText] = String(value || "09:00").split(":");
  const hours = boundedNumber(hoursText, 0, 23, 9);
  const minutes = boundedNumber(minutesText, 0, 59, 0);
  const period = hours >= 12 ? "PM" : "AM";
  const twelveHours = hours % 12 || 12;
  return `${String(twelveHours).padStart(2, "0")}:${String(minutes).padStart(2, "0")} ${period}`;
}

function toTwentyFourHourTime(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i);
  if (!match) {
    return "09:00";
  }
  let hours = Number(match[1]);
  const minutes = Number(match[2]);
  const period = (match[3] || "").toUpperCase();
  if (period === "PM" && hours < 12) {
    hours += 12;
  }
  if (period === "AM" && hours === 12) {
    hours = 0;
  }
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function pruneAgentToolBindings(agent, removedToolKey) {
  if (!agent?.agent_parameters || typeof agent.agent_parameters !== "object") {
    return agent;
  }
  const next = compactAgentSelection(agent);
  for (const [name, value] of Object.entries(next.agent_parameters || {})) {
    if (Array.isArray(value?.value)) {
      next.agent_parameters[name] = {
        ...value,
        value: value.value.filter((item) => agentToolKeyFromValue(item) !== removedToolKey),
      };
    } else if (value?.value && typeof value.value === "object" && agentToolKeyFromValue(value.value) === removedToolKey) {
      next.agent_parameters[name] = { ...value, value: null };
    }
  }
  return next;
}

async function handleCreate() {
  try {
    const payload = {
      message: valueOf("#create-message"),
      app_name: optionalValue("#create-app-name"),
      dataset_ids: currentDatasetIds(),
      tool_selections: currentToolSelections(),
      agent_selections: currentAgentSelections(),
      trigger_selection: currentTriggerSelection(),
      planner: currentPlannerSelection(),
    };
    ensureAgentSelectionReady(payload.message, payload.agent_selections);
    await submitBackgroundTask("create", "/api/tasks/workflows/create", payload, {
      kind: "create",
      payload,
    });
  } catch (error) {
    renderTaskSubmissionError("create", error);
  }
}

async function handleModify(path, mode) {
  if (!els.modifyForm.reportValidity()) {
    return;
  }
  try {
    const payload = {
      app_id: valueOf("#modify-app-id"),
      message: valueOf("#modify-message"),
      expected_hash: optionalValue("#modify-expected-hash"),
      allow_destructive: document.querySelector("#modify-allow-destructive").checked,
      dataset_ids: currentDatasetIds(),
      tool_selections: currentToolSelections(),
      agent_selections: currentAgentSelections(),
      trigger_selection: currentTriggerSelection(),
      planner: currentPlannerSelection(),
    };
    ensureAgentSelectionReady(payload.message, payload.agent_selections);
    const taskPath = mode === "apply"
      ? "/api/tasks/workflows/modify/apply"
      : "/api/tasks/workflows/modify/draft";
    await submitBackgroundTask("modify", taskPath, payload, {
      kind: mode === "apply" ? "modify-apply" : "modify-preview",
      payload,
    });
  } catch (error) {
    renderTaskSubmissionError("modify", error);
  }
}

async function handleReviewedPreviewApply() {
  if (!els.modifyForm.reportValidity()) {
    return;
  }
  const current = currentModifyPayload();
  if (!state.modifyPreview || state.modifyPreviewDirty || !modifyPreviewMatches(current)) {
    setPanelStatus(els.modifyStatus, "Preview required", "warning");
    renderResult(
      {
        error: "Modify Apply uses the reviewed preview plan. Run Preview again before applying.",
        app_id: current.app_id,
        base_hash: current.expected_hash,
      },
      "raw"
    );
    return;
  }

  try {
    const payload = {
      app_id: state.modifyPreview.app_id,
      message: state.modifyPreview.message,
      expected_hash: state.modifyPreview.base_hash,
      allow_destructive: state.modifyPreview.allow_destructive,
      plan: state.modifyPreview.plan,
      dataset_ids: state.modifyPreview.dataset_ids,
      tool_selections: state.modifyPreview.tool_selections,
      agent_selections: state.modifyPreview.agent_selections,
      trigger_selection: state.modifyPreview.trigger_selection,
      planner: state.modifyPreview.planner,
    };
    await submitBackgroundTask("modify", "/api/tasks/workflows/modify/apply", payload, {
      kind: "modify-apply",
      payload,
    });
  } catch (error) {
    renderTaskSubmissionError("modify", error);
  }
}

async function handleLoadDraft() {
  const appId = valueOf("#modify-app-id");
  if (!appId) {
    setPanelStatus(els.modifyStatus, "App ID required", "error");
    els.modifyAppId.focus();
    return;
  }
  await withBusy(els.modifyForm, els.modifyStatus, els.modifyDuration, "Loading", async () => {
    const data = await requestJson(`/api/workflows/${encodeURIComponent(appId)}/draft`);
    const loadedTriggerSelection = triggerSelectionFromPlan(data.plan);
    state.triggerSelection = loadedTriggerSelection;
    restoreTriggerForm(loadedTriggerSelection);
    renderTriggerForm();
    localStorage.setItem(
      TRIGGER_SELECTION_KEY,
      JSON.stringify(loadedTriggerSelection)
    );
    syncAppContext(data, appId);
    rememberApp(data, {
      operation: "load draft",
      appId,
      appName: data.app?.name || data.plan?.name,
    });
    state.modifyPreview = null;
    state.modifyPreviewDirty = false;
    setPanelStatus(els.modifyStatus, "Loaded", "ok");
    renderResult(data, "plan");
  });
}

function triggerSelectionFromPlan(plan) {
  const nodes = Array.isArray(plan?.nodes) ? plan.nodes : [];
  const webhook = nodes.find((node) => node?.type === "trigger-webhook");
  if (webhook) {
    const params = webhook.params && typeof webhook.params === "object" ? webhook.params : {};
    return {
      type: "webhook",
      method: params.method || "POST",
      content_type: params.content_type || "application/json",
      headers: Array.isArray(params.headers) ? params.headers : [],
      params: Array.isArray(params.params) ? params.params : [],
      body: Array.isArray(params.body) ? params.body : [],
      status_code: params.status_code || 200,
      response_body: params.response_body || "",
      timeout: params.timeout || 30,
    };
  }

  const plugin = nodes.find((node) => node?.type === "trigger-plugin");
  if (plugin) {
    const params = plugin.params && typeof plugin.params === "object" ? plugin.params : {};
    const raw = params._raw_data && typeof params._raw_data === "object" ? params._raw_data : params;
    return {
      type: "plugin",
      provider_id: raw.provider_id || "",
      event_name: raw.event_name || "",
      subscription_id: raw.subscription_id || "",
      event_parameters: raw.event_parameters && typeof raw.event_parameters === "object"
        ? raw.event_parameters
        : raw.config && typeof raw.config === "object"
        ? raw.config
        : {},
    };
  }

  const schedule = nodes.find((node) => node?.type === "trigger-schedule");
  if (schedule) {
    const params = schedule.params && typeof schedule.params === "object" ? schedule.params : {};
    if (params.mode === "cron") {
      return {
        type: "schedule",
        mode: "cron",
        cron_expression: params.cron_expression || "",
        timezone: params.timezone || "Asia/Shanghai",
      };
    }
    return {
      type: "schedule",
      mode: "visual",
      frequency: params.frequency || "daily",
      visual_config: params.visual_config && typeof params.visual_config === "object"
        ? params.visual_config
        : {},
      timezone: params.timezone || "Asia/Shanghai",
    };
  }

  return { type: "user-input" };
}

async function handleRun() {
  try {
    const triggerNodes = workflowTriggerNodes(state.lastResponse.plan);
    if (triggerNodes.length && (state.lastResponse.app_id || "") === valueOf("#run-app-id")) {
      throw new Error(
        "This workflow starts from a trigger. Publish it, then invoke its Webhook, schedule, or external plugin event instead of Run Draft inputs."
      );
    }
    const payload = {
      app_id: valueOf("#run-app-id"),
      inputs: parseJsonField("#run-inputs", "Inputs JSON"),
      timeout_seconds: Number(valueOf("#run-timeout") || 120),
    };
    await submitBackgroundTask("run", "/api/tasks/workflows/run/draft", payload, {
      kind: "run",
      payload,
    });
  } catch (error) {
    renderTaskSubmissionError("run", error);
  }
}

async function handlePublish() {
  if (!els.publishForm.reportValidity()) {
    return;
  }
  try {
    const payload = {
      app_id: valueOf("#publish-app-id"),
      expected_hash: optionalValue("#publish-expected-hash"),
      marked_name: optionalValue("#publish-version-name"),
      marked_comment: optionalValue("#publish-version-note"),
    };
    await submitBackgroundTask("publish", "/api/tasks/workflows/publish", payload, {
      kind: "publish",
      payload,
    });
  } catch (error) {
    renderTaskSubmissionError("publish", error);
  }
}

async function loadWorkflowTriggers() {
  const appId = valueOf("#publish-app-id") || valueOf("#modify-app-id");
  if (!appId) {
    setPanelStatus(els.publishStatus, "App ID required", "error");
    els.publishAppId.focus();
    return;
  }
  await withBusy(els.publishForm, els.publishStatus, els.publishDuration, "Loading", async () => {
    const data = await requestJson(`/api/workflows/${encodeURIComponent(appId)}/triggers`);
    const triggers = Array.isArray(data.triggers) ? data.triggers : [];
    const webhooks = [];
    await Promise.all(
      triggers
        .filter((trigger) => trigger.trigger_type === "webhook" || trigger.trigger_type === "trigger-webhook")
        .map(async (trigger) => {
          try {
            const webhook = await requestJson(
              `/api/workflows/${encodeURIComponent(appId)}/triggers/webhook?node_id=${encodeURIComponent(trigger.node_id)}`
            );
            webhooks.push(webhook);
          } catch (error) {
            webhooks.push({ node_id: trigger.node_id, error: error.message });
          }
        })
    );
    updateWorkflowTriggers(triggers, webhooks);
    setPanelStatus(els.publishStatus, triggers.length ? "Loaded" : "No triggers", triggers.length ? "ok" : "muted");
  });
}

async function handleWorkflowTriggerAction(event) {
  const copyButton = event.target.closest("[data-copy-trigger-url]");
  if (copyButton) {
    await copyValue(copyButton.dataset.copyTriggerUrl, copyButton, "Copy URL");
    return;
  }
  const toggleButton = event.target.closest("[data-trigger-id]");
  if (!toggleButton) {
    return;
  }
  const appId = valueOf("#publish-app-id");
  const enabled = toggleButton.dataset.triggerEnabled !== "true";
  toggleButton.disabled = true;
  try {
    await requestJson(
      `/api/workflows/${encodeURIComponent(appId)}/triggers/${encodeURIComponent(toggleButton.dataset.triggerId)}/status`,
      {
        method: "POST",
        body: { enabled },
      }
    );
    await loadWorkflowTriggers();
  } catch (error) {
    toggleButton.disabled = false;
    setPanelStatus(els.publishStatus, "Update failed", "error");
    renderResult(error.payload || { error: error.message }, "raw");
  }
}

function updateWorkflowTriggers(triggers, webhooks = []) {
  const webhookByNode = new Map(
    (Array.isArray(webhooks) ? webhooks : [])
      .filter((item) => item && item.node_id)
      .map((item) => [item.node_id, item])
  );
  state.workflowTriggers = (Array.isArray(triggers) ? triggers : []).map((trigger) => ({
    ...trigger,
    webhook: webhookByNode.get(trigger.node_id) || null,
  }));
  renderWorkflowTriggers(state.workflowTriggers);
}

function renderWorkflowTriggers(triggers) {
  if (!Array.isArray(triggers) || triggers.length === 0) {
    els.workflowTriggerList.replaceChildren(
      emptyState("Publish a trigger workflow, then refresh to manage its status and endpoint.")
    );
    return;
  }
  els.workflowTriggerList.replaceChildren(
    ...triggers.map((trigger) => {
      const card = document.createElement("article");
      card.className = "managed-trigger";
      const heading = document.createElement("div");
      heading.className = "managed-trigger-heading";
      const title = document.createElement("strong");
      title.textContent = trigger.title || trigger.trigger_type || "Trigger";
      const status = document.createElement("span");
      const enabled = triggerStatusEnabled(trigger.status);
      status.className = `panel-status ${enabled ? "status-ok" : "status-warning"}`;
      status.textContent = enabled ? "Enabled" : "Disabled";
      heading.append(title, status);
      const meta = document.createElement("div");
      meta.className = "managed-trigger-meta";
      meta.textContent = [trigger.trigger_type, trigger.node_id].filter(Boolean).join(" · ");
      const actions = document.createElement("div");
      actions.className = "button-row";
      if (trigger.id) {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "secondary";
        toggle.dataset.triggerId = trigger.id;
        toggle.dataset.triggerEnabled = String(enabled);
        toggle.textContent = enabled ? "Disable" : "Enable";
        actions.append(toggle);
      }
      const webhookUrl = trigger.webhook?.webhook_url;
      const debugUrl = trigger.webhook?.webhook_debug_url;
      if (webhookUrl) {
        actions.append(triggerUrlButton("Copy URL", webhookUrl));
      }
      if (debugUrl) {
        actions.append(triggerUrlButton("Copy debug URL", debugUrl));
      }
      card.append(heading, meta);
      if (webhookUrl) {
        const url = document.createElement("a");
        url.className = "managed-trigger-url";
        url.href = webhookUrl;
        url.target = "_blank";
        url.rel = "noreferrer";
        url.textContent = webhookUrl;
        card.append(url);
      }
      if (trigger.webhook?.error) {
        card.append(renderMessageRow({ tone: "warning", text: trigger.webhook.error }));
      }
      card.append(actions);
      return card;
    })
  );
}

function triggerUrlButton(label, url) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary";
  button.dataset.copyTriggerUrl = url;
  button.textContent = label;
  return button;
}

function triggerStatusEnabled(status) {
  if (typeof status === "boolean") {
    return status;
  }
  return ["enabled", "active", "true", "1"].includes(String(status || "").toLowerCase());
}

function workflowTriggerNodes(plan) {
  return (Array.isArray(plan?.nodes) ? plan.nodes : []).filter((node) =>
    ["trigger-webhook", "trigger-plugin", "trigger-schedule"].includes(node.type)
  );
}

async function submitBackgroundTask(panelName, path, payload, metadata) {
  if (state.activeTasks[panelName]) {
    throw new Error("This panel already has an active task.");
  }
  delete state.terminalTasks[panelName];
  saveTerminalTasks();
  const panel = taskPanel(panelName);
  setTaskPanelBusy(panelName, true);
  showTaskProgress(panel, {
    status: "queued",
    phase: "queued",
    progress: 0,
    message: "Submitting background task.",
    created_at: new Date().toISOString(),
  });
  try {
    const record = await requestJson(path, {
      method: "POST",
      body: payload,
    });
    state.activeTasks[panelName] = {
      task_id: record.task_id,
      panel: panelName,
      ...metadata,
    };
    saveActiveTasks();
    await pollBackgroundTask(panelName, true);
  } catch (error) {
    setTaskPanelBusy(panelName, false);
    hideTaskProgress(panel);
    throw error;
  }
}

async function pollBackgroundTask(panelName, immediate = false) {
  window.clearTimeout(state.taskPollTimers[panelName]);
  const active = state.activeTasks[panelName];
  if (!active?.task_id) {
    return;
  }
  const poll = async () => {
    const current = state.activeTasks[panelName];
    if (!current?.task_id) {
      return;
    }
    try {
      const record = await requestJson(`/api/tasks/${encodeURIComponent(current.task_id)}`);
      showTaskProgress(taskPanel(panelName), record);
      if (isTerminalTaskStatus(record.status)) {
        finishBackgroundTask(panelName, record, current);
        return;
      }
    } catch (error) {
      setPanelStatus(taskPanel(panelName).status, "Polling failed", "error");
      taskPanel(panelName).message.textContent = error.message;
    }
    state.taskPollTimers[panelName] = window.setTimeout(poll, 1000);
  };
  if (immediate) {
    await poll();
  } else {
    state.taskPollTimers[panelName] = window.setTimeout(poll, 0);
  }
}

function finishBackgroundTask(panelName, record, metadata) {
  window.clearTimeout(state.taskPollTimers[panelName]);
  delete state.taskPollTimers[panelName];
  delete state.activeTasks[panelName];
  saveActiveTasks();
  setTaskPanelBusy(panelName, false);
  if (["failed", "cancelled", "interrupted"].includes(record.status)) {
    state.terminalTasks[panelName] = {
      record,
      metadata: {
        ...metadata,
        payload: metadata.payload || record.request || {},
      },
    };
  } else {
    delete state.terminalTasks[panelName];
  }
  saveTerminalTasks();
  showTaskProgress(taskPanel(panelName), record);

  if (record.status === "succeeded") {
    completeBackgroundTask(
      {
        ...metadata,
        payload: metadata.payload || record.request || {},
      },
      record.result || {}
    );
    return;
  }
  const tone = record.status === "cancelled" || record.status === "interrupted" ? "warning" : "error";
  setPanelStatus(taskPanel(panelName).status, taskStatusLabel(record.status), tone);
  const errorPayload = record.error || {
    error: record.message || `Task ${record.status}.`,
    status: record.status,
    task_id: record.task_id,
  };
  renderResult(errorPayload, "raw");
}

async function handleTaskAction(panelName) {
  if (state.activeTasks[panelName]) {
    await cancelActiveTask(panelName);
    return;
  }
  await retryTerminalTask(panelName);
}

async function retryTerminalTask(panelName) {
  const terminal = state.terminalTasks[panelName];
  const operation = terminal?.record?.operation;
  const path = backgroundTaskPath(operation);
  const payload = terminal?.record?.request || terminal?.metadata?.payload;
  if (!path || !payload) {
    setPanelStatus(taskPanel(panelName).status, "Retry unavailable", "error");
    return;
  }
  delete state.terminalTasks[panelName];
  saveTerminalTasks();
  try {
    await submitBackgroundTask(panelName, path, payload, {
      kind: terminal.metadata?.kind || backgroundTaskKind(operation),
      payload,
    });
  } catch (error) {
    renderTaskSubmissionError(panelName, error);
  }
}

function completeBackgroundTask(metadata, data) {
  const payload = metadata.payload || {};
  if (metadata.kind === "create") {
    syncAppContext(data);
    rememberApp(data, {
      operation: "create",
      request: payload.message,
      appName: payload.app_name,
    });
    setPanelStatus(els.createStatus, data.status || "Created", "ok");
    renderResult(data, "changes");
    return;
  }
  if (metadata.kind === "modify-preview") {
    syncAppContext(data, payload.app_id);
    storeModifyPreview(data, payload);
    rememberApp(data, {
      operation: "modify preview",
      request: payload.message,
      appId: payload.app_id,
    });
    const guard = data.guard?.risk ? `Guard ${data.guard.risk}` : "Ready";
    setPanelStatus(els.modifyStatus, guard, guardClass(data.guard));
    renderResult(data, "changes");
    return;
  }
  if (metadata.kind === "modify-apply") {
    syncAppContext(data, payload.app_id);
    rememberApp(data, {
      operation: payload.plan ? "modify apply reviewed preview" : "modify apply",
      request: payload.message,
      appId: payload.app_id,
    });
    state.modifyPreview = null;
    state.modifyPreviewDirty = false;
    setPanelStatus(els.modifyStatus, payload.plan ? "Applied preview" : "Applied", "ok");
    renderResult(data, "changes");
    return;
  }
  if (metadata.kind === "run") {
    syncAppContext(data, payload.app_id);
    rememberApp(data, {
      operation: "run draft",
      request: JSON.stringify(payload.inputs),
      appId: payload.app_id,
      lastRunStatus: data.status,
    });
    setPanelStatus(els.runStatus, data.status || "Done", runStatusTone(data));
    renderResult(data, "outputs");
    return;
  }
  if (metadata.kind === "publish") {
    syncAppContext(data, payload.app_id);
    rememberApp(data, {
      operation: "publish",
      appId: payload.app_id,
    });
    updateWorkflowTriggers(data.triggers || [], data.webhooks || []);
    setPanelStatus(els.publishStatus, "Published", "ok");
    renderResult(data, "changes");
  }
}

async function cancelActiveTask(panelName) {
  const active = state.activeTasks[panelName];
  if (!active?.task_id) {
    return;
  }
  const panel = taskPanel(panelName);
  panel.cancel.disabled = true;
  setPanelStatus(panel.status, "Cancelling", "warning");
  try {
    const record = await requestJson(`/api/tasks/${encodeURIComponent(active.task_id)}/cancel`, {
      method: "POST",
    });
    showTaskProgress(panel, record);
    await pollBackgroundTask(panelName, true);
  } catch (error) {
    panel.cancel.disabled = false;
    setPanelStatus(panel.status, "Cancel failed", "error");
    renderResult(error.payload || { error: error.message }, "raw");
  }
}

function restoreActiveTasks() {
  for (const panelName of ["create", "modify", "run", "publish"]) {
    const active = state.activeTasks[panelName];
    if (!active?.task_id) {
      continue;
    }
    setTaskPanelBusy(panelName, true);
    pollBackgroundTask(panelName);
  }
}

function restoreTerminalTasks() {
  for (const panelName of ["create", "modify", "run", "publish"]) {
    if (state.activeTasks[panelName]) {
      continue;
    }
    const terminal = state.terminalTasks[panelName];
    if (!terminal?.task_id) {
      continue;
    }
    requestJson(`/api/tasks/${encodeURIComponent(terminal.task_id)}`)
      .then((record) => {
        if (!isTerminalTaskStatus(record.status) || record.status === "succeeded") {
          delete state.terminalTasks[panelName];
          saveTerminalTasks();
          return;
        }
        state.terminalTasks[panelName] = {
          record,
          metadata: {
            kind: terminal.kind || backgroundTaskKind(record.operation),
            payload: record.request || {},
          },
        };
        showTaskProgress(taskPanel(panelName), record);
      })
      .catch(() => {
        delete state.terminalTasks[panelName];
        saveTerminalTasks();
      });
  }
}

function taskPanel(panelName) {
  const panels = {
    create: {
      form: els.createForm,
      status: els.createStatus,
      duration: els.createDuration,
      progress: els.createTaskProgress,
      message: els.createTaskMessage,
      bar: els.createTaskBar,
      cancel: els.createCancelTask,
    },
    modify: {
      form: els.modifyForm,
      status: els.modifyStatus,
      duration: els.modifyDuration,
      progress: els.modifyTaskProgress,
      message: els.modifyTaskMessage,
      bar: els.modifyTaskBar,
      cancel: els.modifyCancelTask,
    },
    run: {
      form: els.runForm,
      status: els.runStatus,
      duration: els.runDuration,
      progress: els.runTaskProgress,
      message: els.runTaskMessage,
      bar: els.runTaskBar,
      cancel: els.runCancelTask,
    },
    publish: {
      form: els.publishForm,
      status: els.publishStatus,
      duration: els.publishDuration,
      progress: els.publishTaskProgress,
      message: els.publishTaskMessage,
      bar: els.publishTaskBar,
      cancel: els.publishCancelTask,
    },
  };
  return panels[panelName];
}

function setTaskPanelBusy(panelName, busy) {
  const panel = taskPanel(panelName);
  Array.from(panel.form.querySelectorAll("button")).forEach((button) => {
    button.disabled = busy;
  });
  panel.cancel.disabled = !busy;
}

function showTaskProgress(panel, record) {
  panel.progress.classList.remove("is-hidden");
  panel.message.textContent = taskProgressMessage(record);
  if (Number.isFinite(record.progress)) {
    panel.bar.value = Number(record.progress);
  } else {
    panel.bar.removeAttribute("value");
  }
  const active = !isTerminalTaskStatus(record.status);
  const retryable = ["failed", "cancelled", "interrupted"].includes(record.status);
  panel.bar.classList.toggle("is-hidden", !active && record.status !== "succeeded");
  panel.cancel.classList.toggle("is-hidden", !active && !retryable);
  panel.cancel.textContent = active ? "Cancel" : "Retry";
  panel.cancel.title = active ? "Cancel this task" : "Start a new task with the same request";
  panel.cancel.disabled = record.status === "cancel_requested";
  setPanelStatus(panel.status, taskStatusLabel(record.status), taskStatusTone(record.status));
  updateTaskDurationFromRecord(panel.duration, record, active);
}

function hideTaskProgress(panel) {
  panel.progress.classList.add("is-hidden");
  panel.bar.classList.remove("is-hidden");
  panel.cancel.classList.add("is-hidden");
  panel.cancel.textContent = "Cancel";
  panel.cancel.title = "";
}

function updateTaskDurationFromRecord(element, record, running) {
  const startedAt = Date.parse(record.started_at || record.created_at || "");
  const finishedAt = Date.parse(record.finished_at || "");
  if (!Number.isFinite(startedAt)) {
    resetTaskDuration(element);
    return;
  }
  const endAt = Number.isFinite(finishedAt) ? finishedAt : Date.now();
  setTaskDuration(element, Math.max(0, endAt - startedAt), running);
}

function isTerminalTaskStatus(status) {
  return ["succeeded", "failed", "cancelled", "interrupted"].includes(status);
}

function taskStatusLabel(status) {
  return {
    queued: "Queued",
    running: "Running",
    cancel_requested: "Cancelling",
    succeeded: "Completed",
    failed: "Error",
    cancelled: "Cancelled",
    interrupted: "Interrupted",
  }[status] || status || "Working";
}

function taskStatusTone(status) {
  if (status === "succeeded") {
    return "ok";
  }
  if (["cancel_requested", "cancelled", "interrupted"].includes(status)) {
    return "warning";
  }
  if (status === "failed") {
    return "error";
  }
  return "muted";
}

function taskProgressMessage(record) {
  if (record.status === "cancel_requested") {
    return "Cancellation requested · Waiting for the current external call to return.";
  }
  if (record.status === "cancelled") {
    return "Task cancelled · Retry starts a new task from the beginning.";
  }
  if (record.status === "interrupted") {
    return "Interrupted by service restart · Retry starts a new task from the beginning.";
  }
  if (record.status === "failed") {
    return "Task failed · Retry starts a new task with the same request.";
  }
  return [record.phase, record.message].filter(Boolean).join(" · ");
}

function backgroundTaskPath(operation) {
  return {
    "workflow.create": "/api/tasks/workflows/create",
    "workflow.modify.draft": "/api/tasks/workflows/modify/draft",
    "workflow.modify.apply": "/api/tasks/workflows/modify/apply",
    "workflow.run.draft": "/api/tasks/workflows/run/draft",
    "workflow.publish": "/api/tasks/workflows/publish",
  }[operation] || "";
}

function backgroundTaskKind(operation) {
  return {
    "workflow.create": "create",
    "workflow.modify.draft": "modify-preview",
    "workflow.modify.apply": "modify-apply",
    "workflow.run.draft": "run",
    "workflow.publish": "publish",
  }[operation] || "";
}

function renderTaskSubmissionError(panelName, error) {
  setPanelStatus(taskPanel(panelName).status, "Error", "error");
  renderResult(error.payload || { error: error.message }, "raw");
}

async function withBusy(container, statusElement, durationElement, label, action) {
  const buttons = Array.from(container.querySelectorAll("button"));
  const startedAt = performance.now();
  const updateDuration = () => {
    setTaskDuration(durationElement, performance.now() - startedAt, true);
  };
  updateDuration();
  const durationTimer = window.setInterval(updateDuration, 100);
  try {
    buttons.forEach((button) => {
      button.disabled = true;
    });
    setPanelStatus(statusElement, label, "muted");
    await action();
  } catch (error) {
    setPanelStatus(statusElement, "Error", "error");
    renderResult(error.payload || { error: error.message }, "raw");
  } finally {
    window.clearInterval(durationTimer);
    setTaskDuration(durationElement, performance.now() - startedAt, false);
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
}

function setTaskDuration(element, elapsedMilliseconds, running = false) {
  if (!element) {
    return;
  }
  element.textContent = `耗时 ${formatTaskDuration(elapsedMilliseconds)}`;
  element.classList.toggle("is-running", running);
}

function resetTaskDuration(element) {
  if (!element) {
    return;
  }
  element.textContent = "耗时 --";
  element.classList.remove("is-running");
}

function formatTaskDuration(elapsedMilliseconds) {
  const totalSeconds = Math.max(0, elapsedMilliseconds) / 1000;
  if (totalSeconds < 60) {
    return `${totalSeconds.toFixed(1)}秒`;
  }
  const totalWholeSeconds = Math.floor(totalSeconds);
  const hours = Math.floor(totalWholeSeconds / 3600);
  const minutes = Math.floor((totalWholeSeconds % 3600) / 60);
  const seconds = totalWholeSeconds % 60;
  if (hours > 0) {
    return `${hours}小时${minutes}分${seconds}秒`;
  }
  return `${minutes}分${seconds}秒`;
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
    const error = new Error(extractErrorMessage(data, response.status));
    error.payload = typeof data === "string" ? { error: data, status: response.status } : data;
    throw error;
  }
  return data;
}

function renderResult(data, preferredTab = state.activeTab) {
  state.lastResponse = data || {};
  els.resultJson.textContent = JSON.stringify(state.lastResponse, null, 2);
  renderSummary(state.lastResponse);
  renderChangesPanel(state.lastResponse);
  renderValidationPanel(state.lastResponse);
  renderOutputsPanel(state.lastResponse);
  renderPlanPanel(state.lastResponse);
  renderResultActions(state.lastResponse);
  setActiveTab(preferredTab);
}

function renderSummary(data) {
  const items = [
    ["App ID", data.app_id],
    ["App name", data.app?.name || data.plan?.name],
    ["Mode", data.app?.mode || data.app_mode],
    ["Workflow", linkValue(data.workflow_url)],
    ["Status", data.status ?? data.guard?.risk],
    ["Run OK", typeof data.ok === "boolean" ? String(data.ok) : undefined],
    ["Guard", data.guard?.risk],
    ["Planner", data.planner?.mode],
    ["Planner provider", data.planner?.provider],
    ["Planner model", data.planner?.model],
    ["Fallback", typeof data.planner?.used_fallback === "boolean" ? String(data.planner.used_fallback) : undefined],
    ["Attempts", data.planner?.attempts],
    ["Base hash", data.base_hash],
    ["New hash", data.new_hash],
    ["Sync", data.sync?.result],
    ["Published", data.publish?.created_at],
    ["Triggers", Array.isArray(data.triggers) ? data.triggers.length : undefined],
    ["Run ID", data.workflow_run_id],
    ["Task ID", data.task_id],
    ["Tokens", data.total_tokens],
    ["Steps", data.total_steps],
    ["Error", data.error || data.detail?.message || data.detail],
  ].filter((item) => item[1] !== undefined && item[1] !== null && item[1] !== "");

  if (items.length === 0) {
    els.summaryGrid.replaceChildren(emptyState("Run an action to see workflow details here."));
    return;
  }

  els.summaryGrid.replaceChildren(
    ...items.map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "summary-item";
      const labelEl = document.createElement("span");
      labelEl.className = "summary-label";
      labelEl.textContent = label;
      const valueEl = document.createElement("span");
      valueEl.className = "summary-value";
      if (value instanceof Node) {
        valueEl.append(value);
      } else {
        valueEl.textContent = String(value);
      }
      item.append(labelEl, valueEl);
      return item;
    })
  );
}

function renderChangesPanel(data) {
  const panel = panelFor("changes");
  const rows = [];
  if (data.planner?.mode === "preview-plan" && data.sync?.result) {
    rows.push({ tone: "ok", text: "Applied reviewed preview. No second LLM generation was used." });
  }
  if (data.explanation?.summary) {
    rows.push({ tone: "info", text: data.explanation.summary });
  }
  if (Array.isArray(data.explanation?.steps)) {
    rows.push(...data.explanation.steps.map((step) => ({ tone: "info", text: step })));
  }
  if (Array.isArray(data.changes)) {
    rows.push(...data.changes.map((change) => ({ tone: changeTone(change), text: changeMessage(change) })));
  }
  if (data.guard) {
    rows.push({ tone: guardClass(data.guard), text: guardMessage(data.guard) });
  }
  if (Array.isArray(data.guard?.issues)) {
    rows.push(...data.guard.issues.map((issue) => ({ tone: severityTone(issue.severity), text: issueMessage(issue) })));
  }
  if (data.sync?.result) {
    rows.push({ tone: "ok", text: `Sync: ${data.sync.result}${data.sync.updated_at ? ` at ${data.sync.updated_at}` : ""}` });
  }
  if (data.publish?.result) {
    rows.push({ tone: "ok", text: `Publish: ${data.publish.result}${data.publish.created_at ? ` at ${data.publish.created_at}` : ""}` });
  }
  if (data.planner) {
    rows.push({ tone: "muted", text: plannerMessage(data.planner) });
  }

  if (rows.length === 0) {
    panel.replaceChildren(emptyState("No changes or planner diagnostics yet."));
    return;
  }

  panel.replaceChildren(...rows.slice(0, 24).map(renderMessageRow));
}

function renderValidationPanel(data) {
  const panel = panelFor("validation");
  const validation = data.validation || data.detail?.validation;
  const issues = validation?.issues || data.detail?.issues || [];
  const rows = [];
  if (validation) {
    rows.push({ tone: validation.ok ? "ok" : "error", text: `Validation: ${validation.ok ? "ok" : "failed"}` });
  }
  if (Array.isArray(issues)) {
    rows.push(...issues.map((issue) => ({ tone: severityTone(issue.severity), text: issueMessage(issue) })));
  }
  if (data.detail?.code) {
    rows.push({ tone: "error", text: `Code: ${data.detail.code}` });
  }
  if (data.detail?.message) {
    rows.push({ tone: "error", text: data.detail.message });
  }

  if (rows.length === 0) {
    panel.replaceChildren(emptyState("No validation issues reported."));
    return;
  }

  panel.replaceChildren(...rows.map(renderMessageRow));
}

function renderOutputsPanel(data) {
  const panel = panelFor("outputs");
  const sections = [];
  if (data.outputs !== undefined && data.outputs !== null) {
    sections.push(jsonBlock("Outputs", data.outputs));
  }
  if (data.events_summary) {
    sections.push(keyValueGroup("Run summary", data.events_summary));
  }
  if (data.status === "paused") {
    sections.push(renderMessageRow({ tone: "warning", text: "Workflow paused at a human-input node. Complete the human action in Dify UI; chat2dify does not submit or resume forms in this stage." }));
  }
  if (data.final_event) {
    sections.push(jsonBlock("Final event", data.final_event));
  }
  if (data.error) {
    sections.push(renderMessageRow({ tone: "error", text: String(data.error) }));
  }

  if (sections.length === 0) {
    panel.replaceChildren(emptyState("Run a draft workflow to see outputs and SSE summary here."));
    return;
  }

  panel.replaceChildren(...sections);
}

function renderPlanPanel(data) {
  const panel = panelFor("plan");
  const sections = [];
  if (data.before_plan) {
    sections.push(planNodeOverview("Before draft nodes", data.before_plan));
  }
  if (data.plan) {
    sections.push(planSummary(data.plan));
    sections.push(planNodeOverview(data.before_plan ? "After plan nodes" : "Current draft nodes", data.plan));
    sections.push(jsonBlock("Plan IR", data.plan));
  }
  if (data.before_plan) {
    sections.push(jsonBlock("Before plan", data.before_plan));
  }
  if (data.raw_plan) {
    sections.push(jsonBlock("Raw plan", data.raw_plan));
  }

  if (sections.length === 0) {
    panel.replaceChildren(emptyState("Create or modify a workflow to inspect its Plan IR."));
    return;
  }

  panel.replaceChildren(...sections);
}

function planNodeOverview(title, plan) {
  const section = document.createElement("section");
  section.className = "result-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const nodes = Array.isArray(plan?.nodes) ? plan.nodes : [];
  if (nodes.length === 0) {
    section.append(heading, emptyState("No nodes found in this plan."));
    return section;
  }

  const list = document.createElement("div");
  list.className = "node-list";
  nodes.forEach((node) => {
    list.append(renderNodeCard(node));
  });
  section.append(heading, list);
  return section;
}

function renderNodeCard(node) {
  const card = document.createElement("article");
  card.className = "node-card";
  const header = document.createElement("div");
  header.className = "node-card-header";
  const title = document.createElement("span");
  title.className = "node-title";
  title.textContent = node.title || node.id || "Untitled node";
  const meta = document.createElement("span");
  meta.className = "node-meta";
  meta.textContent = [node.type, node.id].filter(Boolean).join(" · ");
  header.append(title, meta);
  card.append(header);

  nodeDetails(node).forEach((detail) => {
    card.append(detail);
  });
  return card;
}

function nodeDetails(node) {
  const params = node.params || {};
  if (node.type === "start") {
    const variables = Array.isArray(params.variables) ? params.variables : [];
    return [nodeLine("Inputs", variables.map((item) => item.name || item.variable).filter(Boolean).join(", ") || "none")];
  }
  if (node.type === "llm") {
    return [
      promptPreview("System", params.system_prompt),
      promptPreview("User", params.user_prompt),
    ];
  }
  if (node.type === "end") {
    const outputs = Array.isArray(params.outputs) ? params.outputs : [];
    return [nodeLine("Outputs", outputs.map((item) => item.variable).filter(Boolean).join(", ") || "none")];
  }
  if (node.type === "if-else") {
    const cases = Array.isArray(params.cases) ? params.cases : [];
    return [nodeLine("Branches", cases.map((item) => item.case_id || item.id).filter(Boolean).join(", ") || "none")];
  }
  if (node.type === "http-request") {
    return [nodeLine("Request", [params.method, params.url].filter(Boolean).join(" ") || "not configured")];
  }
  if (node.type === "template-transform") {
    return [promptPreview("Template", params.template)];
  }
  if (node.type === "question-classifier") {
    const classes = Array.isArray(params.classes) ? params.classes : [];
    return [
      nodeLine("Model", modelLabel(params)),
      nodeLine("Input", selectorLabel(params.query_variable_selector)),
      nodeLine("Classes", classes.map((item) => `${item.name || item.id} (${item.id})`).filter(Boolean).join(", ") || "none"),
      promptPreview("Instruction", params.instruction),
    ];
  }
  if (node.type === "parameter-extractor") {
    const parameters = Array.isArray(params.parameters) ? params.parameters : [];
    return [
      nodeLine("Model", modelLabel(params)),
      nodeLine("Input", selectorLabel(params.query)),
      nodeLine("Parameters", parameters.map((item) => `${item.name}:${item.type || "string"}`).filter(Boolean).join(", ") || "none"),
      promptPreview("Instruction", params.instruction),
    ];
  }
  if (node.type === "variable-aggregator") {
    const variables = Array.isArray(params.variables) ? params.variables : [];
    const groups = params.advanced_settings && Array.isArray(params.advanced_settings.groups)
      ? params.advanced_settings.groups
      : [];
    return [
      nodeLine("Output", params.output_type || "string"),
      nodeLine("Variables", variables.map(selectorLabel).join(", ") || "none"),
      nodeLine("Groups", groups.map((item) => item.group_name || item.groupId).filter(Boolean).join(", ") || "disabled"),
    ];
  }
  if (node.type === "document-extractor") {
    return [
      nodeLine("File input", selectorLabel(params.variable_selector)),
      nodeLine("Array file", params.is_array_file ? "true" : "false"),
      nodeLine("Output", "text"),
    ];
  }
  if (node.type === "assigner") {
    const items = Array.isArray(params.items) ? params.items : [];
    return [
      nodeLine("Version", params.version || "2"),
      nodeLine("Operations", items.map((item) => `${selectorLabel(item.variable_selector)} ${item.operation || "over-write"}`).join(", ") || "none"),
    ];
  }
  if (node.type === "list-operator") {
    return [
      nodeLine("Input", selectorLabel(params.variable)),
      nodeLine("Types", `${params.var_type || "array[string]"} -> ${params.item_var_type || "string"}`),
      nodeLine("Filter", params.filter_by && params.filter_by.enabled ? "enabled" : "disabled"),
      nodeLine("Limit", params.limit && params.limit.enabled ? String(params.limit.size || 10) : "disabled"),
    ];
  }
  if (node.type === "knowledge-retrieval") {
    const datasetIds = Array.isArray(params.dataset_ids) ? params.dataset_ids : [];
    const retrievalConfig = params.multiple_retrieval_config || {};
    return [
      nodeLine("Input", selectorLabel(params.query_variable_selector)),
      nodeLine("Datasets", String(datasetIds.length)),
      nodeLine("Mode", params.retrieval_mode || "multiple"),
      nodeLine("Top K", String(retrievalConfig.top_k || 4)),
    ];
  }
  if (node.type === "human-input") {
    const actions = Array.isArray(params.user_actions) ? params.user_actions : [];
    const inputs = Array.isArray(params.inputs) ? params.inputs : [];
    const methods = Array.isArray(params.delivery_methods) ? params.delivery_methods : [];
    return [
      nodeLine("Actions", actions.map((item) => `${item.title || item.id} (${item.id})`).filter(Boolean).join(", ") || "none"),
      nodeLine("Inputs", inputs.map((item) => `${item.output_variable_name}:${item.type || "paragraph"}`).filter(Boolean).join(", ") || "none"),
      nodeLine("Delivery", methods.map((item) => `${item.type || "webapp"}${item.enabled === false ? " off" : ""}`).join(", ") || "none"),
      nodeLine("Timeout", `${params.timeout || 3} ${params.timeout_unit || "day"}`),
      promptPreview("Form", params.form_content),
    ];
  }
  if (node.type === "iteration") {
    const children = Array.isArray(params.children) ? params.children : [];
    return [
      nodeLine("Iterator", selectorLabel(params.iterator_selector)),
      nodeLine("Output", selectorLabel(params.output_selector)),
      nodeLine("Parallel", params.is_parallel ? `${params.parallel_nums || 10}` : "disabled"),
      nodeLine("Flatten", params.flatten_output === false ? "false" : "true"),
      nodeLine("Children", childSummary(children)),
    ];
  }
  if (node.type === "loop") {
    const children = Array.isArray(params.children) ? params.children : [];
    const conditions = Array.isArray(params.break_conditions) ? params.break_conditions : [];
    const variables = Array.isArray(params.loop_variables) ? params.loop_variables : [];
    return [
      nodeLine("Max count", String(params.loop_count || 3)),
      nodeLine("Break", `${params.logical_operator || "and"} · ${conditions.length} condition${conditions.length === 1 ? "" : "s"}`),
      nodeLine("Variables", variables.map((item) => `${item.label}:${item.var_type || "string"}`).filter(Boolean).join(", ") || "none"),
      nodeLine("Children", childSummary(children)),
    ];
  }
  if (node.type === "trigger-webhook" && !params._raw_data) {
    const headers = Array.isArray(params.headers) ? params.headers : [];
    const query = Array.isArray(params.params) ? params.params : [];
    const body = Array.isArray(params.body) ? params.body : [];
    return [
      nodeLine("Request", `${params.method || "POST"} · ${params.content_type || "application/json"}`),
      nodeLine("Headers", triggerParameterSummary(headers)),
      nodeLine("Query", triggerParameterSummary(query)),
      nodeLine("Body", triggerParameterSummary(body)),
      nodeLine("Response", `${params.status_code || 200} · timeout ${params.timeout || 30}s`),
      promptPreview("Response body", params.response_body),
    ];
  }
  if (node.type === "trigger-schedule" && !params._raw_data) {
    const visual = params.visual_config || {};
    return [
      nodeLine("Mode", params.mode || "visual"),
      nodeLine("Timezone", params.timezone || "Asia/Shanghai"),
      nodeLine(
        "Schedule",
        params.mode === "cron"
          ? params.cron_expression || "not configured"
          : [
              params.frequency,
              visual.time,
              Array.isArray(visual.weekdays) ? visual.weekdays.join(",") : "",
              Array.isArray(visual.monthly_days) ? visual.monthly_days.join(",") : "",
            ].filter(Boolean).join(" · ")
      ),
    ];
  }
  if (node.type === "trigger-plugin" && !params._raw_data) {
    return [
      nodeLine("Provider", [params.provider_name || params.provider_id, params.provider_type].filter(Boolean).join(" / ") || "not configured"),
      nodeLine("Event", params.event_label || params.event_name || "not configured"),
      nodeLine("Subscription", params.subscription_id || "not configured"),
      nodeLine("Parameters", toolBindingSummary(params.event_parameters || {})),
      nodeLine("Outputs", externalOutputSummary(params)),
    ];
  }
  if (node.type === "tool" && !params._raw_data) {
    const schemas = Array.isArray(params.paramSchemas) ? params.paramSchemas : [];
    const toolParameters = params.tool_parameters && typeof params.tool_parameters === "object" ? params.tool_parameters : {};
    const toolConfigurations = params.tool_configurations && typeof params.tool_configurations === "object" ? params.tool_configurations : {};
    return [
      nodeLine("Provider", [params.provider_name || params.provider_id, params.provider_type].filter(Boolean).join(" / ") || "not configured"),
      nodeLine("Tool", params.tool_label || params.tool_name || "not configured"),
      nodeLine("Inputs", toolBindingSummary(toolParameters)),
      nodeLine("Configurations", toolBindingSummary(toolConfigurations)),
      nodeLine("Schema", schemas.map((item) => `${item.name || item.variable}:${item.type || "string"}${item.required ? "*" : ""}`).join(", ") || "none"),
      nodeLine("Outputs", externalOutputSummary(params)),
    ];
  }
  if (node.type === "agent" && !params._raw_data) {
    const agentParameters = params.agent_parameters && typeof params.agent_parameters === "object" ? params.agent_parameters : {};
    const schemas = Array.isArray(params.parameters) ? params.parameters : [];
    return [
      nodeLine("Provider", params.agent_strategy_provider_name || "not configured"),
      nodeLine("Strategy", params.agent_strategy_label || params.agent_strategy_name || "not configured"),
      nodeLine("Parameters", toolBindingSummary(agentParameters)),
      nodeLine("Schema", schemas.map((item) => `${item.name || item.variable}:${item.type || "text-input"}${item.required ? "*" : ""}`).join(", ") || "from Dify strategy"),
      nodeLine("Outputs", externalOutputSummary(params)),
    ];
  }
  if (isExternalDependencyNode(node.type)) {
    const raw = params._raw_data || params;
    return [
      nodeLine("Mode", "preserved external Dify node"),
      nodeLine("Provider", [raw.provider_name, raw.provider_type].filter(Boolean).join(" / ") || raw.plugin_id || "Dify configured"),
      nodeLine("Name", raw.tool_label || raw.tool_name || raw.agent_strategy_label || raw.agent_strategy_name || raw.datasource_label || raw.datasource_name || raw.webhook_url || "configured in Dify"),
      nodeLine("Outputs", externalOutputSummary(raw)),
    ];
  }
  if (node.type === "code") {
    return [promptPreview("Code", params.code)];
  }
  return [];
}

function isExternalDependencyNode(type) {
  return [
    "tool",
    "agent",
    "datasource",
    "datasource-empty",
    "knowledge-index",
    "trigger-webhook",
    "trigger-plugin",
    "trigger-schedule",
  ].includes(type);
}

function externalOutputSummary(raw) {
  if (!raw || typeof raw !== "object")
    return "schema unknown";
  const properties = raw.output_schema && raw.output_schema.properties;
  if (properties && typeof properties === "object")
    return Object.keys(properties).join(", ") || "schema empty";
  const variables = Array.isArray(raw.variables) ? raw.variables : [];
  const names = variables.map((item) => item && (item.name || item.variable)).filter(Boolean);
  return names.join(", ") || "text, files, json / Dify runtime";
}

function triggerParameterSummary(items) {
  if (!Array.isArray(items) || items.length === 0) {
    return "none";
  }
  return items
    .map((item) => `${item.name}:${item.type || "string"}${item.required ? "*" : ""}`)
    .join(", ");
}

function toolBindingSummary(values) {
  const entries = Object.entries(values || {});
  if (!entries.length) {
    return "none";
  }
  return entries.map(([name, value]) => `${name}=${toolBindingValueLabel(value)}`).join(", ");
}

function toolBindingValueLabel(value) {
  if (!value || typeof value !== "object") {
    return String(value ?? "");
  }
  const rawValue = Array.isArray(value.value) ? value.value.join(".") : String(value.value ?? "");
  return `${value.type || "mixed"}:${rawValue}`;
}

function modelLabel(params) {
  const model = params.model || {};
  const provider = model.provider || params.model_provider;
  const name = model.name || params.model_name;
  return [provider, name].filter(Boolean).join(" / ") || "default model";
}

function selectorLabel(selector) {
  return Array.isArray(selector) ? selector.join(".") : selector || "none";
}

function childSummary(children) {
  if (!Array.isArray(children) || children.length === 0) {
    return "none";
  }
  return children.map((child) => `${child.title || child.id || "child"} (${child.type || "unknown"})`).join(", ");
}

function nodeLine(label, value) {
  const row = document.createElement("div");
  row.className = "node-detail";
  const labelEl = document.createElement("span");
  labelEl.className = "node-detail-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("span");
  valueEl.className = "node-detail-value";
  valueEl.textContent = value || "empty";
  row.append(labelEl, valueEl);
  return row;
}

function promptPreview(label, value) {
  const row = nodeLine(label, truncateText(value || "empty", 700));
  row.classList.add("prompt-preview");
  return row;
}

function renderResultActions(data) {
  const appId = data.app_id || "";
  const workflowUrl = data.workflow_url || "";
  const hash = data.new_hash || data.base_hash || "";
  els.copyAppId.disabled = !appId;
  els.copyWorkflowUrl.disabled = !workflowUrl;
  els.copyHash.disabled = !hash;
  if (workflowUrl) {
    els.openWorkflow.href = workflowUrl;
    els.openWorkflow.classList.remove("is-hidden");
  } else {
    els.openWorkflow.href = "#";
    els.openWorkflow.classList.add("is-hidden");
  }
}

function setActiveTab(tabName) {
  state.activeTab = tabName;
  Array.from(els.resultTabs.querySelectorAll("[data-tab]")).forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === tabName);
  });
  els.tabPanels.forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.dataset.tabPanel !== tabName);
  });
}

function panelFor(name) {
  return document.querySelector(`[data-tab-panel="${name}"]`);
}

function renderMessageRow({ tone = "muted", text }) {
  const row = document.createElement("div");
  row.className = `message-row tone-${tone}`;
  row.textContent = text;
  return row;
}

function keyValueGroup(title, values) {
  const section = document.createElement("section");
  section.className = "result-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const grid = document.createElement("div");
  grid.className = "mini-grid";
  Object.entries(values || {}).forEach(([key, value]) => {
    const item = document.createElement("div");
    item.className = "mini-item";
    const label = document.createElement("span");
    label.className = "summary-label";
    label.textContent = key;
    const body = document.createElement("span");
    body.className = "summary-value";
    body.textContent = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value);
    item.append(label, body);
    grid.append(item);
  });
  section.append(heading, grid);
  return section;
}

function jsonBlock(title, value) {
  const section = document.createElement("section");
  section.className = "result-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const pre = document.createElement("pre");
  pre.className = "json-output compact-json";
  pre.textContent = JSON.stringify(value, null, 2);
  section.append(heading, pre);
  return section;
}

function planSummary(plan) {
  const nodes = Array.isArray(plan.nodes) ? plan.nodes : [];
  const edges = Array.isArray(plan.edges) ? plan.edges : [];
  return keyValueGroup("Plan summary", {
    name: plan.name || "Untitled",
    nodes: nodes.length,
    edges: edges.length,
    node_types: [...new Set(nodes.map((node) => node.type).filter(Boolean))].join(", ") || "none",
  });
}

function emptyState(message) {
  const item = document.createElement("div");
  item.className = "empty-state";
  item.textContent = message;
  return item;
}

function linkValue(url) {
  if (!url) {
    return undefined;
  }
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = url;
  return link;
}

function syncAppContext(data, fallbackAppId = "") {
  const appId = data.app_id || fallbackAppId;
  if (!appId) {
    return;
  }
  els.modifyAppId.value = appId;
  els.runAppId.value = appId;
  els.publishAppId.value = appId;
  const latestHash = data.new_hash || data.base_hash;
  if (latestHash) {
    els.modifyExpectedHash.value = latestHash;
    els.publishExpectedHash.value = latestHash;
  }
  if (Array.isArray(data.triggers)) {
    updateWorkflowTriggers(data.triggers, data.webhooks || []);
  } else if (Array.isArray(data.webhooks) && data.webhooks.length) {
    const planTriggers = workflowTriggerNodes(data.plan);
    updateWorkflowTriggers(
      planTriggers.map((node) => ({
        id: "",
        trigger_type: node.type.replace("trigger-", ""),
        title: node.title,
        node_id: node.id,
        status: "draft",
      })),
      data.webhooks
    );
  }
}

function currentModifyPayload() {
  return {
    app_id: valueOf("#modify-app-id"),
    message: valueOf("#modify-message"),
    expected_hash: optionalValue("#modify-expected-hash"),
    allow_destructive: document.querySelector("#modify-allow-destructive").checked,
    dataset_ids: currentDatasetIds(),
    tool_selections: currentToolSelections(),
    agent_selections: currentAgentSelections(),
    trigger_selection: currentTriggerSelection(),
    planner: currentPlannerSelection(),
  };
}

function storeModifyPreview(data, payload) {
  if (!data.plan || !data.base_hash) {
    state.modifyPreview = null;
    state.modifyPreviewDirty = false;
    return;
  }
  state.modifyPreview = {
    app_id: data.app_id || payload.app_id,
    message: payload.message,
    base_hash: data.base_hash,
    allow_destructive: payload.allow_destructive,
    plan: data.plan,
    dataset_ids: payload.dataset_ids || [],
    tool_selections: payload.tool_selections || [],
    agent_selections: payload.agent_selections || [],
    trigger_selection: payload.trigger_selection || { type: "user-input" },
    planner: payload.planner || null,
  };
  state.modifyPreviewDirty = false;
}

function modifyPreviewMatches(payload) {
  const preview = state.modifyPreview;
  if (!preview) {
    return false;
  }
  return (
    payload.app_id === preview.app_id &&
    payload.message === preview.message &&
    payload.expected_hash === preview.base_hash &&
    payload.allow_destructive === preview.allow_destructive &&
    datasetIdsEqual(payload.dataset_ids || [], preview.dataset_ids || []) &&
    toolSelectionsEqual(payload.tool_selections || [], preview.tool_selections || []) &&
    agentSelectionsEqual(payload.agent_selections || [], preview.agent_selections || []) &&
    triggerSelectionsEqual(payload.trigger_selection, preview.trigger_selection) &&
    plannerSelectionsEqual(payload.planner, preview.planner)
  );
}

function markModifyPreviewDirty() {
  if (!state.modifyPreview) {
    return;
  }
  state.modifyPreviewDirty = !modifyPreviewMatches(currentModifyPayload());
  if (state.modifyPreviewDirty) {
    setPanelStatus(els.modifyStatus, "Preview stale", "warning");
  }
}

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

function loadActiveTasks() {
  try {
    const raw = localStorage.getItem(ACTIVE_TASKS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (error) {
    return {};
  }
}

function saveActiveTasks() {
  const persisted = Object.fromEntries(
    Object.entries(state.activeTasks).map(([panelName, task]) => [
      panelName,
      {
        task_id: task.task_id,
        panel: task.panel,
        kind: task.kind,
      },
    ])
  );
  try {
    localStorage.setItem(ACTIVE_TASKS_KEY, JSON.stringify(persisted));
  } catch (error) {
    // The in-memory task remains usable even when browser storage is unavailable.
  }
}

function loadTerminalTasks() {
  try {
    const raw = localStorage.getItem(TERMINAL_TASKS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (error) {
    return {};
  }
}

function saveTerminalTasks() {
  const persisted = Object.fromEntries(
    Object.entries(state.terminalTasks)
      .map(([panelName, task]) => [
        panelName,
        {
          task_id: task?.record?.task_id || task?.task_id,
          kind: task?.metadata?.kind || task?.kind || "",
        },
      ])
      .filter(([, task]) => task.task_id)
  );
  try {
    localStorage.setItem(TERMINAL_TASKS_KEY, JSON.stringify(persisted));
  } catch (error) {
    // Retry remains available in memory when browser storage is unavailable.
  }
}

function saveHistory() {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(0, MAX_HISTORY_ITEMS)));
}

function loadDatasetIdsText() {
  try {
    return localStorage.getItem(DATASET_IDS_KEY) || "";
  } catch (error) {
    return "";
  }
}

function saveDatasetIdsText(value) {
  localStorage.setItem(DATASET_IDS_KEY, value || "");
  renderKnowledgeDatasets();
}

function loadSelectedDatasetIds() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SELECTED_DATASET_IDS_KEY) || "[]");
    return Array.isArray(parsed) ? uniqueDatasetIds(parsed) : [];
  } catch (error) {
    return [];
  }
}

function saveSelectedDatasetIds() {
  localStorage.setItem(SELECTED_DATASET_IDS_KEY, JSON.stringify(uniqueDatasetIds(state.datasets.selectedIds)));
}

function loadDatasetSearchText() {
  try {
    return localStorage.getItem(DATASET_SEARCH_KEY) || "";
  } catch (error) {
    return "";
  }
}

function saveDatasetSearchText(value) {
  localStorage.setItem(DATASET_SEARCH_KEY, value || "");
}

function loadSelectedTools() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SELECTED_TOOLS_KEY) || "[]");
    return Array.isArray(parsed) ? uniqueTools(parsed) : [];
  } catch (error) {
    return [];
  }
}

function saveSelectedTools() {
  localStorage.setItem(SELECTED_TOOLS_KEY, JSON.stringify(uniqueTools(state.tools.selected)));
}

function loadToolSearchText() {
  try {
    return localStorage.getItem(TOOL_SEARCH_KEY) || "";
  } catch (error) {
    return "";
  }
}

function saveToolSearchText(value) {
  localStorage.setItem(TOOL_SEARCH_KEY, value || "");
}

function loadToolType() {
  try {
    return localStorage.getItem(TOOL_TYPE_KEY) || "all";
  } catch (error) {
    return "all";
  }
}

function saveToolType(value) {
  localStorage.setItem(TOOL_TYPE_KEY, value || "all");
}

function loadSelectedAgents() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SELECTED_AGENTS_KEY) || "[]");
    return Array.isArray(parsed) ? uniqueAgents(parsed) : [];
  } catch (error) {
    return [];
  }
}

function saveSelectedAgents() {
  localStorage.setItem(SELECTED_AGENTS_KEY, JSON.stringify(uniqueAgents(state.agents.selected)));
}

function loadAgentSearchText() {
  try {
    return localStorage.getItem(AGENT_SEARCH_KEY) || "";
  } catch (error) {
    return "";
  }
}

function saveAgentSearchText(value) {
  localStorage.setItem(AGENT_SEARCH_KEY, value || "");
}

function currentDatasetIds() {
  return uniqueDatasetIds([
    ...state.datasets.selectedIds,
    ...parseDatasetIds(els.knowledgeDatasetIds.value),
  ]);
}

function currentToolSelections() {
  return uniqueTools(state.tools.selected);
}

function currentAgentSelections() {
  const selected = uniqueAgents(state.agents.selected);
  if (!state.agents.items.length) {
    return [];
  }
  const loadedKeys = new Set(state.agents.items.map(agentKey));
  return selected.filter((agent) => loadedKeys.has(agentKey(agent)));
}

function currentPlannerSelection() {
  const provider = selectedPlannerProvider();
  if (!provider?.configured || !state.planner.model) {
    return null;
  }
  return {
    provider: provider.id,
    model: state.planner.model,
  };
}

function loadPlannerProvider() {
  return localStorage.getItem(PLANNER_PROVIDER_KEY) || "";
}

function loadPlannerModel() {
  return localStorage.getItem(PLANNER_MODEL_KEY) || "";
}

function savePlannerSelection() {
  localStorage.setItem(PLANNER_PROVIDER_KEY, state.planner.provider || "");
  localStorage.setItem(PLANNER_MODEL_KEY, state.planner.model || "");
}

function plannerSelectionsEqual(left, right) {
  return JSON.stringify(left || null) === JSON.stringify(right || null);
}

function ensureAgentSelectionReady(message, agentSelections) {
  if (!messageRequestsAgentStrategy(message) || agentSelections.length) {
    return;
  }
  throw new Error(
    "This request asks for Agent/智能体, but no Dify Agent Strategy is selected. The Agent Strategies panel lists strategy plugins, not Dify Agent apps. Select an installed strategy plugin first, or rewrite the request to use LLM/Tool nodes."
  );
}

function messageRequestsAgentStrategy(message) {
  const text = String(message || "").toLowerCase().replaceAll("user agent", "");
  return (
    text.includes("智能体") ||
    text.includes("agent strategy") ||
    text.includes("agent策略") ||
    text.includes("agent 节点") ||
    text.includes("agent节点") ||
    text.includes("自主规划") ||
    text.includes("多步执行") ||
    /\bagent\b/.test(text)
  );
}

function compactToolSelection(tool = {}) {
  const item = tool || {};
  const parameters = Array.isArray(item.parameters) ? item.parameters : [];
  return {
    provider_id: item.provider_id,
    provider_type: item.provider_type,
    provider_name: item.provider_name,
    tool_name: item.tool_name,
    tool_label: item.tool_label,
    description: item.description,
    parameters,
    output_schema: item.output_schema && typeof item.output_schema === "object" ? item.output_schema : {},
    plugin_id: item.plugin_id || undefined,
    plugin_unique_identifier: item.plugin_unique_identifier || undefined,
    tool_parameters: configuredToolValues(parameters, item.tool_parameters, "tool_parameters"),
    tool_configurations: configuredToolValues(parameters, item.tool_configurations, "tool_configurations"),
    is_team_authorization: item.is_team_authorization,
    requires_configuration: Boolean(item.requires_configuration),
  };
}

function compactAgentSelection(agent = {}) {
  const item = agent || {};
  const parameters = Array.isArray(item.parameters) ? item.parameters : [];
  return {
    agent_strategy_provider_name: item.agent_strategy_provider_name,
    agent_strategy_name: item.agent_strategy_name,
    agent_strategy_label: item.agent_strategy_label,
    description: item.description,
    parameters,
    features: Array.isArray(item.features) ? item.features : [],
    output_schema: item.output_schema && typeof item.output_schema === "object" ? item.output_schema : {},
    plugin_id: item.plugin_id || undefined,
    plugin_unique_identifier: item.plugin_unique_identifier || undefined,
    meta: item.meta && typeof item.meta === "object" ? item.meta : undefined,
    agent_parameters: configuredAgentValues(parameters, item.agent_parameters),
    requires_configuration: Boolean(item.requires_configuration),
  };
}

function configuredAgentValues(parameters, current) {
  const configured = current && typeof current === "object" ? { ...current } : {};
  for (const schema of parameters) {
    const variable = schemaVariable(schema);
    if (!variable) {
      continue;
    }
    if (normalizedSchemaType(schema) === "model-selector" && configured[variable]?.value) {
      const model = configured[variable].value;
      if (model && typeof model === "object" && model.provider && (model.model || model.name)) {
        configured[variable] = {
          type: "constant",
          value: {
            ...model,
            model: model.model || model.name,
            model_type: model.model_type || "llm",
            mode: model.mode || "chat",
            completion_params: model.completion_params && typeof model.completion_params === "object"
              ? model.completion_params
              : {},
          },
        };
      }
    }
    if (toolSchemaUsesMixedText(schema) && configured[variable]?.type === "variable") {
      const selector = normalizeSelectorValue(configured[variable].value);
      configured[variable] = {
        type: "constant",
        value: selector.length ? `{{#${selector.join(".")}#}}` : DEFAULT_TOOL_QUERY_TEMPLATE,
      };
    }
    if (toolSchemaUsesMixedText(schema) && configured[variable]?.type === "mixed") {
      configured[variable] = {
        type: "constant",
        value: configured[variable].value,
      };
    }
    if (configured[variable] && agentInputHasValue(configured[variable], schema)) {
      continue;
    }
    const value = defaultAgentInputForSchema(schema);
    if (value) {
      configured[variable] = value;
    }
  }
  return configured;
}

function configuredToolValues(parameters, current, group) {
  const configured = current && typeof current === "object" ? { ...current } : {};
  for (const schema of parameters) {
    if (schemaForm(schema) === "llm" && group !== "tool_parameters") {
      continue;
    }
    if (schemaForm(schema) !== "llm" && group !== "tool_configurations") {
      continue;
    }
    const variable = schemaVariable(schema);
    if (!variable || configured[variable]) {
      continue;
    }
    const value = defaultToolInputForSchema(schema, group);
    if (value) {
      configured[variable] = value;
    }
  }
  return configured;
}

function uniqueTools(tools) {
  const seen = new Set();
  return (tools || [])
    .map(compactToolSelection)
    .filter((tool) => {
      const key = toolKey(tool);
      if (!tool.provider_id || !tool.tool_name || seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function toolKey(tool) {
  return [tool?.provider_type || "", tool?.provider_id || "", tool?.tool_name || ""].join("::");
}

function uniqueAgents(agents) {
  const seen = new Set();
  return (agents || [])
    .map(compactAgentSelection)
    .filter((agent) => {
      const key = agentKey(agent);
      if (!agent.agent_strategy_provider_name || !agent.agent_strategy_name || seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
}

function agentKey(agent) {
  return [agent?.agent_strategy_provider_name || "", agent?.agent_strategy_name || ""].join("::");
}

function parseDatasetIds(value) {
  return uniqueDatasetIds(String(value || "")
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean));
}

function uniqueDatasetIds(ids) {
  const seen = new Set();
  return (ids || []).filter((id) => {
    const text = String(id || "").trim();
    if (!text || seen.has(text)) {
      return false;
    }
    seen.add(text);
    return true;
  }).map((id) => String(id).trim());
}

function datasetIdsEqual(left, right) {
  return JSON.stringify(left || []) === JSON.stringify(right || []);
}

function toolSelectionsEqual(left, right) {
  return JSON.stringify(uniqueTools(left || [])) === JSON.stringify(uniqueTools(right || []));
}

function agentSelectionsEqual(left, right) {
  return JSON.stringify(uniqueAgents(left || [])) === JSON.stringify(uniqueAgents(right || []));
}

function triggerSelectionsEqual(left, right) {
  return JSON.stringify(left || { type: "user-input" }) === JSON.stringify(right || { type: "user-input" });
}

function rememberApp(data, meta = {}) {
  const appId = data.app_id || meta.appId;
  if (!appId) {
    return;
  }
  const previous = state.history.find((item) => item.app_id === appId) || {};
  const record = {
    ...previous,
    app_id: appId,
    app_name: meta.appName || data.plan?.name || previous.app_name || "",
    workflow_url: data.workflow_url || previous.workflow_url || "",
    base_hash: data.base_hash || previous.base_hash || "",
    new_hash: data.new_hash || previous.new_hash || "",
    last_request: meta.request || previous.last_request || "",
    last_operation: meta.operation || previous.last_operation || "",
    last_run_status: meta.lastRunStatus || (typeof data.ok === "boolean" ? data.status : previous.last_run_status || ""),
    updated_at: new Date().toISOString(),
  };
  state.history = [record, ...state.history.filter((item) => item.app_id !== appId)].slice(0, MAX_HISTORY_ITEMS);
  saveHistory();
  renderHistory();
}

function renderHistory() {
  if (state.history.length === 0) {
    els.historyList.replaceChildren(emptyState("Created and modified apps will appear here."));
    return;
  }

  const items = state.history.map((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.dataset.historyIndex = String(index);
    const title = document.createElement("span");
    title.className = "history-title";
    title.textContent = item.app_name || item.app_id;
    const id = document.createElement("span");
    id.className = "history-id";
    id.textContent = item.app_id;
    const meta = document.createElement("span");
    meta.className = "history-meta";
    meta.textContent = historyMeta(item);
    const request = document.createElement("span");
    request.className = "history-request";
    request.textContent = item.last_request || "No recent request";
    button.append(title, id, meta, request);
    return button;
  });
  els.historyList.replaceChildren(...items);
}

function historyMeta(item) {
  const parts = [item.last_operation, item.last_run_status, item.new_hash || item.base_hash ? "hash saved" : ""].filter(Boolean);
  if (item.updated_at) {
    parts.push(new Date(item.updated_at).toLocaleString());
  }
  return parts.join(" · ");
}

function selectHistoryItem(index) {
  const item = state.history[index];
  if (!item) {
    return;
  }
  syncAppContext(
    {
      app_id: item.app_id,
      workflow_url: item.workflow_url,
      base_hash: item.base_hash,
      new_hash: item.new_hash,
    },
    item.app_id
  );
  if (item.app_name) {
    els.createAppName.value = item.app_name;
  }
  renderResult(
    {
      app_id: item.app_id,
      workflow_url: item.workflow_url,
      base_hash: item.base_hash,
      new_hash: item.new_hash,
      status: item.last_run_status || item.last_operation,
    },
    "changes"
  );
}

function clearHistory() {
  state.history = [];
  saveHistory();
  renderHistory();
}

function clearResult() {
  renderResult({});
  if (!state.activeTasks.create) {
    setPanelStatus(els.createStatus, "");
    resetTaskDuration(els.createDuration);
    hideTaskProgress(taskPanel("create"));
  }
  if (!state.activeTasks.modify) {
    setPanelStatus(els.modifyStatus, "");
    resetTaskDuration(els.modifyDuration);
    hideTaskProgress(taskPanel("modify"));
  }
  if (!state.activeTasks.run) {
    setPanelStatus(els.runStatus, "");
    resetTaskDuration(els.runDuration);
    hideTaskProgress(taskPanel("run"));
  }
  if (!state.activeTasks.publish) {
    setPanelStatus(els.publishStatus, "");
    resetTaskDuration(els.publishDuration);
    hideTaskProgress(taskPanel("publish"));
  }
}

function formatRunInputs() {
  try {
    const value = parseJsonField("#run-inputs", "Inputs JSON");
    els.runInputs.value = JSON.stringify(value, null, 2);
    setPanelStatus(els.runStatus, "Formatted", "ok");
  } catch (error) {
    setPanelStatus(els.runStatus, error.message, "error");
    renderResult({ error: error.message }, "raw");
  }
}

function resetRunInputs() {
  els.runInputs.value = DEFAULT_RUN_INPUTS;
  setPanelStatus(els.runStatus, "Reset", "muted");
}

async function copyValue(value, button, originalLabel) {
  if (!value) {
    return;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.append(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    flashButton(button, "Copied", originalLabel);
  } catch (error) {
    flashButton(button, "Copy failed", originalLabel);
  }
}

function flashButton(button, text, originalLabel) {
  button.textContent = text;
  setTimeout(() => {
    button.textContent = originalLabel;
  }, 1200);
}

function currentAppId() {
  return state.lastResponse.app_id || "";
}

function currentWorkflowUrl() {
  return state.lastResponse.workflow_url || "";
}

function currentHash() {
  return state.lastResponse.new_hash || state.lastResponse.base_hash || "";
}

function valueOf(selector) {
  return document.querySelector(selector).value.trim();
}

function optionalValue(selector) {
  const value = valueOf(selector);
  return value || undefined;
}

function truncateText(value, maxLength) {
  const text = String(value || "");
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength)}...`;
}

function parseJsonField(selector, label) {
  const raw = valueOf(selector);
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error(`${label} is not valid JSON.`);
  }
}

function compactPayload(payload) {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined && value !== ""));
}

function setPanelStatus(element, text, tone = "muted") {
  element.textContent = text;
  if (!text) {
    element.className = "panel-status";
    return;
  }
  const className = toneClass(tone);
  element.className = `panel-status ${className}`;
}

function toneClass(tone) {
  if (tone === "ok") {
    return "status-ok";
  }
  if (tone === "error") {
    return "status-error";
  }
  if (tone === "warning") {
    return "status-warning";
  }
  return "status-muted";
}

function runStatusTone(data) {
  if (data?.ok) {
    return "ok";
  }
  if (data?.status === "paused") {
    return "warning";
  }
  return "error";
}

function guardClass(guard) {
  if (!guard) {
    return "muted";
  }
  if (guard.risk === "high") {
    return "error";
  }
  if (guard.risk === "medium") {
    return "warning";
  }
  return "ok";
}

function severityTone(severity) {
  if (severity === "error" || severity === "high") {
    return "error";
  }
  if (severity === "warning" || severity === "medium") {
    return "warning";
  }
  return "muted";
}

function changeTone(change) {
  if (change?.type === "deleted") {
    return "warning";
  }
  if (change?.type === "added") {
    return "ok";
  }
  return "info";
}

function changeMessage(change) {
  if (typeof change === "string") {
    return change;
  }
  return change?.message || [change?.type, change?.target].filter(Boolean).join(": ") || JSON.stringify(change);
}

function guardMessage(guard) {
  if (!guard) {
    return "Guard: not reported";
  }
  const parts = [`Guard: ${guard.risk || "unknown"}`];
  if (guard.no_op) {
    parts.push("no-op");
  }
  if (guard.ok === false) {
    parts.push("blocked");
  }
  return parts.join(" · ");
}

function plannerMessage(planner) {
  const parts = [`Planner: ${planner.mode || "unknown"}`];
  if (planner.provider || planner.model) {
    parts.push([planner.provider, planner.model].filter(Boolean).join("/"));
  }
  if (planner.attempts !== undefined) {
    parts.push(`${planner.attempts} attempt${planner.attempts === 1 ? "" : "s"}`);
  }
  if (planner.used_fallback) {
    parts.push("fallback");
  }
  if (planner.repaired) {
    parts.push("self-repaired");
  }
  return parts.join(" · ");
}

function issueMessage(issue) {
  if (typeof issue === "string") {
    return issue;
  }
  const parts = [];
  if (issue.severity) {
    parts.push(issue.severity);
  }
  if (issue.path) {
    parts.push(issue.path);
  }
  if (issue.message) {
    parts.push(issue.message);
  }
  if (issue.suggestion) {
    parts.push(`Suggestion: ${issue.suggestion}`);
  }
  return parts.join(" · ") || JSON.stringify(issue);
}

function extractErrorMessage(data, status) {
  if (typeof data === "string") {
    return data || `Request failed with ${status}`;
  }
  if (typeof data?.detail === "string") {
    return data.detail;
  }
  if (data?.detail?.message) {
    return data.detail.message;
  }
  if (data?.error) {
    return data.error;
  }
  return `Request failed with ${status}`;
}
