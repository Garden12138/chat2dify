export const BUILD_APP_MODES = new Set([
  "workflow",
  "advanced-chat",
  "chat",
  "completion",
  "agent-chat",
]);

export function isBuildStudioEnabled(config, search = "") {
  const params = new URLSearchParams(search);
  return Boolean(config?.studioV5Enabled) && params.get("studio") === "build";
}

export function buildIdentity(search = "") {
  const params = new URLSearchParams(search);
  const intent = params.get("intent") === "modify" ? "modify" : "create";
  const appMode = BUILD_APP_MODES.has(params.get("app_mode"))
    ? params.get("app_mode")
    : "workflow";
  return {
    operation: intent,
    entrySource: intent === "create"
      ? "create"
      : (params.get("studio_entry") === "home" ? "home" : "canvas"),
    appId: intent === "modify" ? String(params.get("app_id") || "") : "",
    appMode,
    appName: String(params.get("app_name") || defaultAppName(appMode)),
    buildId: String(params.get("build_id") || ""),
    embedded: ["1", "true", "yes"].includes(
      String(params.get("embed") || "").toLowerCase(),
    ),
    contextNonce: String(params.get("context_nonce") || ""),
  };
}

export function buildCreatePayload(identity, projectId, appModeOverride = "") {
  const appMode = BUILD_APP_MODES.has(appModeOverride)
    ? appModeOverride
    : identity.appMode;
  return {
    project_id: projectId,
    operation: identity.operation,
    entry_source: identity.entrySource,
    app_id: identity.operation === "modify" ? identity.appId : undefined,
    app_mode: appMode,
    app_name: identity.operation === "create"
      ? defaultAppName(appMode)
      : identity.appName,
  };
}

export function buildCommandPayload({
  projectId,
  modeValue,
  message,
  sourceCandidateIds = [],
  canvasContext = null,
}) {
  const alternatives = String(modeValue || "").match(/^alternatives-(2|3)$/);
  const mode = alternatives ? "alternatives" : modeValue;
  return {
    project_id: projectId,
    mode,
    message: String(message || "").trim(),
    candidate_count: alternatives ? Number(alternatives[1]) : 2,
    source_candidate_ids: mode === "synthesize" ? sourceCandidateIds : [],
    canvas_context: normalizeCanvasForApi(canvasContext),
  };
}

export function selectedCandidate(view, preferredId = "") {
  const candidates = Array.isArray(view?.candidates) ? view.candidates : [];
  return candidates.find(item => item?.candidate?.id === preferredId)
    || candidates.find(item => item?.candidate?.id === view?.build?.selected_candidate_id)
    || candidates[0]
    || null;
}

export function comparisonRows(view) {
  const candidates = Array.isArray(view?.candidates) ? view.candidates : [];
  if (!candidates.length) return [];
  const dimensions = [
    ["business_behavior", "Business Behavior"],
    ["nodes_edges", "Node / Edge"],
    ["model_resources", "Model / Resource"],
    ["side_effects", "Side Effect"],
    ["estimated_cost_inputs", "Cost Inputs"],
    ["validation", "Validation"],
    ["unresolved_questions", "Unresolved"],
  ];
  return dimensions.map(([key, label]) => ({
    key,
    label,
    values: candidates.map(candidate => ({
      candidateId: candidate.candidate.id,
      label: candidate.candidate.label,
      value: view?.comparison?.[key]?.[candidate.candidate.id],
    })),
  }));
}

export function candidateStatus(status) {
  return {
    queued: "等待开始",
    building: "生成中",
    waiting_input: "等待补充",
    valid: "校验通过",
    invalid: "未通过",
    cancelled: "已取消",
    interrupted: "可恢复中断",
    conflicted: "Base 冲突",
  }[status] || "未知状态";
}

export function canSelectCandidate(candidate) {
  return Boolean(
    candidate?.candidate?.status === "valid"
    && candidate?.reconstructable
    && candidate?.validation?.ok === true,
  );
}

export function contextCommandsEnabled(candidate, selectedNodeIds = []) {
  const hasCandidate = Boolean(candidate?.candidate?.id);
  const graph = candidate?.technical_detail?.domain === "graph";
  const hasSelection = Array.isArray(selectedNodeIds) && selectedNodeIds.length > 0;
  return {
    explain_selection: hasCandidate && graph && hasSelection,
    explain_variable_flow: hasCandidate && graph && hasSelection,
    safer_fallback: hasCandidate && graph && hasSelection,
    suggest_resources: hasCandidate,
    generate_scenarios: hasCandidate,
  };
}

export function layoutPresentation(layout) {
  const nodes = Array.isArray(layout?.nodes) ? layout.nodes : [];
  if (!nodes.length) return { nodes: [], width: 0, height: 0 };
  const minX = Math.min(...nodes.map(node => finite(node.x)));
  const minY = Math.min(...nodes.map(node => finite(node.y)));
  const normalized = nodes.map(node => ({
    ...node,
    x: finite(node.x) - minX + 30,
    y: finite(node.y) - minY + 30,
  }));
  return {
    nodes: normalized,
    width: Math.max(...normalized.map(node => node.x)) + 190,
    height: Math.max(...normalized.map(node => node.y)) + 105,
  };
}

export function conciseValue(value) {
  if (value == null || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join("；") : "无";
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, item]) => `${key}: ${conciseValue(item)}`)
      .join("；") || "无";
  }
  return String(value);
}

function normalizeCanvasForApi(context) {
  if (!context || typeof context !== "object") return undefined;
  return {
    selected_node_ids: Array.isArray(context.selected_node_ids)
      ? context.selected_node_ids
      : [],
    selected_edge_ids: Array.isArray(context.selected_edge_ids)
      ? context.selected_edge_ids
      : [],
    viewport: context.viewport,
    current_panel: context.current_panel,
    dirty_state: Boolean(context.dirty_state),
    canvas_draft_hash: context.canvas_draft_hash,
    revision: Number.isInteger(context.revision) ? context.revision : 0,
  };
}

function defaultAppName(mode) {
  return {
    workflow: "New Workflow",
    "advanced-chat": "New Chatflow",
    chat: "New Chatbot",
    completion: "New Completion App",
    "agent-chat": "New Dify Agent",
  }[mode] || "New Dify App";
}

function finite(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}
