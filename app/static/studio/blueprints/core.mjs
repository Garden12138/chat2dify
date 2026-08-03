export const BLUEPRINT_APP_MODES = new Set(["workflow", "advanced-chat"]);

export function isBlueprintGalleryEnabled(config, search = "") {
  const params = new URLSearchParams(search);
  return Boolean(config?.studioV5Enabled) && params.get("studio") === "blueprints";
}

export function blueprintIdentity(search = "") {
  const params = new URLSearchParams(search);
  return {
    buildId: String(params.get("build_id") || ""),
    blueprintId: String(params.get("blueprint_id") || ""),
    version: String(params.get("version") || ""),
    candidateId: String(params.get("candidate_id") || ""),
    selectedNodeIds: String(params.get("node_ids") || "")
      .split(",")
      .map(value => value.trim())
      .filter(Boolean)
      .slice(0, 40),
    appMode: BLUEPRINT_APP_MODES.has(params.get("app_mode"))
      ? params.get("app_mode")
      : "",
    embedded: ["1", "true", "yes"].includes(
      String(params.get("embed") || "").toLowerCase(),
    ),
  };
}

export function galleryQuery({
  projectId = "",
  buildId = "",
  search = "",
  category = "",
  appMode = "",
  difyVersion = "",
  risk = "",
  visibility = "",
  resourceAvailable = "",
  compatibleOnly = true,
} = {}) {
  const params = new URLSearchParams();
  params.set("project_id", projectId);
  if (buildId) params.set("build_id", buildId);
  if (String(search).trim()) params.set("search", String(search).trim());
  if (category) params.set("category", category);
  if (appMode) params.set("app_mode", appMode);
  if (String(difyVersion).trim()) params.set("dify_version", String(difyVersion).trim());
  if (risk) params.set("risk", risk);
  if (visibility) params.set("visibility", visibility);
  if (resourceAvailable === true || resourceAvailable === "true") params.set("resource_available", "true");
  if (resourceAvailable === false || resourceAvailable === "false") params.set("resource_available", "false");
  params.set("compatible_only", compatibleOnly ? "true" : "false");
  return `/api/v5/studio/blueprints?${params.toString()}`;
}

export function detailQuery({ projectId, buildId = "", blueprintId, version = "" }) {
  const params = new URLSearchParams({ project_id: projectId });
  if (buildId) params.set("build_id", buildId);
  if (version) params.set("version", version);
  return `/api/v5/studio/blueprints/${encodeURIComponent(blueprintId)}?${params.toString()}`;
}

export function setupPayload({ projectId, buildId, version = "", fields = [], formValues = {} }) {
  return {
    project_id: projectId,
    build_id: buildId,
    version: version || undefined,
    values: fields.map(field => ({
      field_id: field.id,
      kind: field.kind,
      value: normalizeFieldValue(field, formValues[field.id]),
    })),
  };
}

export function normalizeFieldValue(field, value) {
  if (field.multiple) {
    if (Array.isArray(value)) return value.map(String).filter(Boolean);
    return String(value || "").split(",").map(item => item.trim()).filter(Boolean);
  }
  if (typeof field.default === "boolean") {
    return value === true || value === "true" || value === "1";
  }
  if (typeof field.default === "number") {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : field.default;
  }
  return String(value ?? field.default ?? "").trim();
}

export function availabilityPresentation(availability = {}) {
  const reasons = Array.isArray(availability.reasons) ? availability.reasons : [];
  if (reasons.some(item => item?.code === "BLUEPRINT_VERSION_PENDING_REVIEW")) {
    return {
      tone: "warning",
      label: "等待版本评审",
      reasons: reasons.map(item => item.message || item.code).filter(Boolean),
    };
  }
  if (availability.applicable) {
    return { tone: "ok", label: "可用于当前 Build", reasons: [] };
  }
  if (availability.compatible) {
    return {
      tone: "warning",
      label: "兼容，但需要补齐资源",
      reasons: reasons.map(item => item.message || item.code).filter(Boolean),
    };
  }
  return {
    tone: "danger",
    label: "当前不可用",
    reasons: reasons.map(item => item.message || item.code).filter(Boolean),
  };
}

export function previewLayout(preview = {}) {
  const nodes = Array.isArray(preview.nodes) ? preview.nodes : [];
  const edges = Array.isArray(preview.edges) ? preview.edges : [];
  const incoming = new Map(nodes.map(node => [node.ref, 0]));
  for (const edge of edges) {
    incoming.set(edge.target, (incoming.get(edge.target) || 0) + 1);
  }
  const levels = new Map();
  const queue = nodes.filter(node => (incoming.get(node.ref) || 0) === 0)
    .map(node => ({ ref: node.ref, level: 0 }));
  while (queue.length) {
    const current = queue.shift();
    if (levels.has(current.ref) && levels.get(current.ref) >= current.level) continue;
    levels.set(current.ref, current.level);
    for (const edge of edges.filter(item => item.source === current.ref)) {
      queue.push({ ref: edge.target, level: current.level + 1 });
    }
  }
  return nodes.map((node, index) => ({
    ...node,
    column: levels.get(node.ref) ?? index,
    row: nodes.slice(0, index).filter(item => (levels.get(item.ref) ?? 0) === (levels.get(node.ref) ?? index)).length,
  }));
}

export function applyResultPresentation(result = {}) {
  const candidates = Array.isArray(result?.build?.candidates)
    ? result.build.candidates
    : [];
  const candidate = candidates.find(
    item => item?.candidate?.id === result?.application?.candidate_id,
  );
  return {
    ok: Boolean(candidate?.candidate?.status === "valid" && candidate?.reconstructable),
    candidateId: String(result?.application?.candidate_id || ""),
    applicationId: String(result?.application?.id || ""),
    label: String(candidate?.candidate?.label || "Blueprint Candidate"),
    operationCount: Number(result?.patch_operation_count || 0),
    sourceUnchanged: result?.source_head_unchanged === true,
    difyWriteCount: Number(result?.dify_write_count || 0),
  };
}

export function safeBuildReturnUrl(basePath, buildId) {
  const normalized = String(basePath || "").replace(/\/+$/, "");
  if (!buildId) return `${normalized}/?studio=build&intent=create&app_mode=workflow`;
  const params = new URLSearchParams({ studio: "build", build_id: buildId });
  return `${normalized}/?${params.toString()}`;
}
