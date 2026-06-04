const HISTORY_KEY = "chat2dify.workbench.history.v1";
const DATASET_IDS_KEY = "chat2dify.workbench.datasetIds.v1";
const SELECTED_DATASET_IDS_KEY = "chat2dify.workbench.selectedDatasetIds.v1";
const DATASET_SEARCH_KEY = "chat2dify.workbench.datasetSearch.v1";
const SELECTED_TOOLS_KEY = "chat2dify.workbench.selectedTools.v1";
const TOOL_SEARCH_KEY = "chat2dify.workbench.toolSearch.v1";
const TOOL_TYPE_KEY = "chat2dify.workbench.toolType.v1";
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
};

const els = {
  healthStatus: document.querySelector("#health-status"),
  refreshHealth: document.querySelector("#refresh-health"),
  resultJson: document.querySelector("#result-json"),
  summaryGrid: document.querySelector("#summary-grid"),
  resultTabs: document.querySelector("#result-tabs"),
  tabPanels: Array.from(document.querySelectorAll("[data-tab-panel]")),
  createForm: document.querySelector("#create-form"),
  createStatus: document.querySelector("#create-status"),
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
  modifyForm: document.querySelector("#modify-form"),
  modifyStatus: document.querySelector("#modify-status"),
  runForm: document.querySelector("#run-form"),
  runStatus: document.querySelector("#run-status"),
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
  els.knowledgeSearch.value = state.datasets.keyword;
  els.knowledgeDatasetIds.value = loadDatasetIdsText();
  els.toolsSearch.value = state.tools.keyword;
  els.toolsType.value = state.tools.providerType;
  bindEvents();
  renderHistory();
  renderKnowledgeDatasets();
  renderTools();
  refreshHealth();
  loadDatasets({ reset: true });
  loadTools();
  renderResult({});
});

function bindEvents() {
  els.refreshHealth.addEventListener("click", refreshHealth);
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

  els.createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await handleCreate();
  });

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
    const datasetCount = data?.configured_dataset_count ?? data?.dify?.configured_dataset_count;
    const datasetSuffix = datasetCount !== undefined ? ` · datasets ${datasetCount}` : "";
    els.healthStatus.textContent = `Healthy · DSL ${version} · ${source}${datasetSuffix}`;
    els.healthStatus.className = "status-pill status-ok";
  } catch (error) {
    els.healthStatus.textContent = "Offline";
    els.healthStatus.className = "status-pill status-error";
  }
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

function selectedToolByKey(key) {
  return state.tools.selected.find((tool) => toolKey(tool) === key);
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
    if (normalizedSchemaType(schema) === "boolean") {
      return String(value).toLowerCase() === "true" || value === true || value === 1;
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

async function handleCreate() {
  await withBusy(els.createForm, els.createStatus, "Creating", async () => {
    const payload = {
      message: valueOf("#create-message"),
      app_name: optionalValue("#create-app-name"),
      dataset_ids: currentDatasetIds(),
      tool_selections: currentToolSelections(),
    };
    const data = await requestJson("/api/workflows/create", {
      method: "POST",
      body: payload,
    });
    syncAppContext(data);
    rememberApp(data, {
      operation: "create",
      request: payload.message,
      appName: payload.app_name,
    });
    setPanelStatus(els.createStatus, data.status || "Created", "ok");
    renderResult(data, "changes");
  });
}

async function handleModify(path, mode) {
  if (!els.modifyForm.reportValidity()) {
    return;
  }
  await withBusy(els.modifyForm, els.modifyStatus, mode === "apply" ? "Applying" : "Previewing", async () => {
    const payload = {
      app_id: valueOf("#modify-app-id"),
      message: valueOf("#modify-message"),
      expected_hash: optionalValue("#modify-expected-hash"),
      allow_destructive: document.querySelector("#modify-allow-destructive").checked,
      dataset_ids: currentDatasetIds(),
      tool_selections: currentToolSelections(),
    };
    const data = await requestJson(path, {
      method: "POST",
      body: payload,
    });
    syncAppContext(data, payload.app_id);
    if (mode === "preview") {
      storeModifyPreview(data, payload);
    }
    rememberApp(data, {
      operation: mode === "apply" ? "modify apply" : "modify preview",
      request: payload.message,
      appId: payload.app_id,
    });
    const guard = data.guard?.risk ? `Guard ${data.guard.risk}` : "Ready";
    setPanelStatus(els.modifyStatus, mode === "apply" ? "Applied" : guard, guardClass(data.guard));
    renderResult(data, "changes");
  });
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

  await withBusy(els.modifyForm, els.modifyStatus, "Applying preview", async () => {
    const payload = {
      app_id: state.modifyPreview.app_id,
      message: state.modifyPreview.message,
      expected_hash: state.modifyPreview.base_hash,
      allow_destructive: state.modifyPreview.allow_destructive,
      plan: state.modifyPreview.plan,
      dataset_ids: state.modifyPreview.dataset_ids,
      tool_selections: state.modifyPreview.tool_selections,
    };
    const data = await requestJson("/api/workflows/modify/apply", {
      method: "POST",
      body: payload,
    });
    syncAppContext(data, payload.app_id);
    rememberApp(data, {
      operation: "modify apply reviewed preview",
      request: payload.message,
      appId: payload.app_id,
    });
    state.modifyPreview = null;
    state.modifyPreviewDirty = false;
    setPanelStatus(els.modifyStatus, "Applied preview", "ok");
    renderResult(data, "changes");
  });
}

async function handleLoadDraft() {
  const appId = valueOf("#modify-app-id");
  if (!appId) {
    setPanelStatus(els.modifyStatus, "App ID required", "error");
    els.modifyAppId.focus();
    return;
  }
  await withBusy(els.modifyForm, els.modifyStatus, "Loading", async () => {
    const data = await requestJson(`/api/workflows/${encodeURIComponent(appId)}/draft`);
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

async function handleRun() {
  await withBusy(els.runForm, els.runStatus, "Running", async () => {
    const payload = {
      app_id: valueOf("#run-app-id"),
      inputs: parseJsonField("#run-inputs", "Inputs JSON"),
      timeout_seconds: Number(valueOf("#run-timeout") || 120),
    };
    const data = await requestJson("/api/workflows/run/draft", {
      method: "POST",
      body: payload,
    });
    syncAppContext(data, payload.app_id);
    rememberApp(data, {
      operation: "run draft",
      request: JSON.stringify(payload.inputs),
      appId: payload.app_id,
      lastRunStatus: data.status,
    });
    setPanelStatus(els.runStatus, data.status || "Done", runStatusTone(data));
    renderResult(data, "outputs");
  });
}

async function withBusy(container, statusElement, label, action) {
  const buttons = Array.from(container.querySelectorAll("button"));
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
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
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
    ["Fallback", typeof data.planner?.used_fallback === "boolean" ? String(data.planner.used_fallback) : undefined],
    ["Attempts", data.planner?.attempts],
    ["Base hash", data.base_hash],
    ["New hash", data.new_hash],
    ["Sync", data.sync?.result],
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
  const latestHash = data.new_hash || data.base_hash;
  if (latestHash) {
    els.modifyExpectedHash.value = latestHash;
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
    toolSelectionsEqual(payload.tool_selections || [], preview.tool_selections || [])
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

function currentDatasetIds() {
  return uniqueDatasetIds([
    ...state.datasets.selectedIds,
    ...parseDatasetIds(els.knowledgeDatasetIds.value),
  ]);
}

function currentToolSelections() {
  return uniqueTools(state.tools.selected);
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
  setPanelStatus(els.createStatus, "");
  setPanelStatus(els.modifyStatus, "");
  setPanelStatus(els.runStatus, "");
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
