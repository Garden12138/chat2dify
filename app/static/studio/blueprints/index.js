import {
  applyResultPresentation,
  availabilityPresentation,
  blueprintIdentity,
  detailQuery,
  galleryQuery,
  isBlueprintGalleryEnabled,
  previewLayout,
  safeBuildReturnUrl,
  setupPayload,
} from "./core.mjs";
import { classifyStudioError } from "../home/core.mjs";

const config = window.CHAT2DIFY_CONFIG || {};
const basePath = normalizeBasePath(config.basePath || "");
const identity = blueprintIdentity(window.location.search);
const enabled = isBlueprintGalleryEnabled(config, window.location.search);
const state = {
  token: "",
  projectId: "",
  buildId: identity.buildId,
  gallery: null,
  detail: null,
  selectedId: identity.blueprintId,
  formValues: {},
  validation: null,
  application: null,
  busy: false,
  requestSequence: 0,
  debounce: null,
};

if (enabled) {
  window.CHAT2DIFY_STUDIO_BLUEPRINTS = true;
  document.addEventListener("DOMContentLoaded", () => {
    void bootBlueprintGallery();
  });
}

async function bootBlueprintGallery() {
  document.body.classList.add("studio-v5", "studio-v5-blueprints");
  if (identity.embedded) document.body.classList.add("studio-embedded");
  document.querySelector("#legacy-app-frame")?.setAttribute("hidden", "");
  document.querySelector("#studio-root").hidden = false;
  document.querySelector("#studio-state").hidden = true;
  document.querySelector("#studio-content").hidden = true;
  document.querySelector("#studio-build-content").hidden = true;
  document.querySelector("#studio-blueprint-content").hidden = false;
  activateNavigation();
  bindActions();
  renderContext();
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
    await loadGallery({ announce: false, preserveSelection: true });
  } catch (error) {
    showError(error, { reconnect: true });
  } finally {
    setBusy(false);
  }
}

function activateNavigation() {
  for (const item of document.querySelectorAll(".studio-nav-item")) {
    item.classList.remove("studio-nav-active");
    item.removeAttribute("aria-current");
  }
  const link = document.querySelector("#studio-blueprints-nav");
  link?.classList.add("studio-nav-active");
  link?.setAttribute("aria-current", "page");
}

function bindActions() {
  const filters = document.querySelector("#studio-blueprint-filters");
  filters.addEventListener("submit", event => {
    event.preventDefault();
    void loadGallery({ announce: true, preserveSelection: false });
  });
  document.querySelector("#studio-blueprint-search").addEventListener("input", () => {
    window.clearTimeout(state.debounce);
    state.debounce = window.setTimeout(() => {
      void loadGallery({ announce: false, preserveSelection: false });
    }, 280);
  });
  for (const selector of [
    "#studio-blueprint-category",
    "#studio-blueprint-mode",
    "#studio-blueprint-dify-version",
    "#studio-blueprint-resource",
    "#studio-blueprint-risk",
    "#studio-blueprint-visibility",
    "#studio-blueprint-compatible",
  ]) {
    document.querySelector(selector).addEventListener("change", () => {
      void loadGallery({ announce: true, preserveSelection: false });
    });
  }
  document.querySelector("#studio-blueprint-extract-form").addEventListener("submit", event => {
    event.preventDefault();
    void extractSelection();
  });
}

function renderContext() {
  const context = document.querySelector("#studio-blueprint-context");
  const back = document.querySelector("#studio-blueprint-back");
  back.href = safeBuildReturnUrl(basePath, state.buildId);
  if (state.buildId) {
    context.textContent = "已固定当前 Build 与资源快照";
    setNotice("Gallery 只会创建 Workspace Candidate，不会审批、Apply Draft 或 Publish。", "neutral");
  } else {
    context.textContent = "尚未选择 Build";
    setNotice("可先浏览 Blueprint；要测试资源并应用，请从 Build Studio 进入。", "warning");
  }
  const extract = document.querySelector("#studio-blueprint-extract");
  extract.hidden = !(state.buildId && identity.candidateId && identity.selectedNodeIds.length);
}

async function loadGallery({ announce, preserveSelection }) {
  if (!state.token) return;
  if (announce) setNotice("正在根据当前 Build 的真实能力与资源重新筛选。", "neutral");
  const requestSequence = ++state.requestSequence;
  const query = galleryQuery({
    projectId: state.projectId,
    buildId: state.buildId,
    search: document.querySelector("#studio-blueprint-search").value,
    category: document.querySelector("#studio-blueprint-category").value,
    appMode: document.querySelector("#studio-blueprint-mode").value || identity.appMode,
    difyVersion: document.querySelector("#studio-blueprint-dify-version").value,
    risk: document.querySelector("#studio-blueprint-risk").value,
    visibility: document.querySelector("#studio-blueprint-visibility").value,
    resourceAvailable: document.querySelector("#studio-blueprint-resource").value,
    compatibleOnly: document.querySelector("#studio-blueprint-compatible").checked,
  });
  try {
    const gallery = await requestJson(query);
    if (requestSequence !== state.requestSequence) return;
    state.gallery = gallery;
    renderCategories(gallery.categories || []);
    renderGallery(gallery);
    if (!preserveSelection && !gallery.items?.some(item => item.blueprint.id === state.selectedId)) {
      state.selectedId = "";
      state.detail = null;
      renderEmptyDetail();
    }
    const preferred = state.selectedId || gallery.items?.[0]?.blueprint?.id || "";
    if (preferred) await selectBlueprint(preferred, identity.version, { focus: false });
    setConnection(gallery.state === "partial_error" ? "部分能力不可用" : "Dify 已验证", gallery.state === "partial_error" ? "warning" : "ok");
    if (announce) setNotice(gallery.message, gallery.state === "empty" ? "warning" : "success");
  } catch (error) {
    if (requestSequence !== state.requestSequence) return;
    renderGalleryFailure(error);
  }
}

function renderCategories(categories) {
  const select = document.querySelector("#studio-blueprint-category");
  const current = select.value;
  select.replaceChildren(option("", "全部分类"));
  for (const category of categories) select.append(option(category, category));
  select.value = categories.includes(current) ? current : "";
}

function renderGallery(gallery) {
  const list = document.querySelector("#studio-blueprint-list");
  const summary = document.querySelector("#studio-blueprint-gallery-state");
  list.replaceChildren();
  summary.textContent = gallery.message || "";
  summary.dataset.tone = gallery.state || "ready";
  if (!gallery.items?.length) {
    list.append(emptyCard(
      "没有匹配的 Blueprint",
      "可关闭“只看兼容”、清除筛选，或回到 Build 补齐资源。",
      "清除筛选",
      clearFilters,
    ));
    return;
  }
  for (const item of gallery.items) list.append(blueprintCard(item));
}

function blueprintCard(item) {
  const blueprint = item.blueprint;
  const availability = availabilityPresentation(item.availability);
  const wrapper = document.createElement("article");
  wrapper.setAttribute("role", "listitem");
  const card = document.createElement("button");
  card.type = "button";
  card.className = "studio-blueprint-card";
  card.dataset.blueprintId = blueprint.id;
  card.setAttribute("aria-current", blueprint.id === state.selectedId ? "true" : "false");
  card.setAttribute("aria-label", `查看 ${blueprint.name}，${availability.label}`);
  card.addEventListener("click", () => void selectBlueprint(blueprint.id, blueprint.version, { focus: true }));

  const top = element("div", "studio-blueprint-card-top");
  top.append(element("span", "studio-blueprint-chip", blueprint.category));
  const status = element("span", "studio-blueprint-availability", availability.label);
  status.dataset.tone = availability.tone;
  top.append(status);
  const title = element("h3", "", blueprint.name);
  const outcome = element("p", "", blueprint.business_outcome);
  const meta = element("div", "studio-blueprint-meta");
  meta.append(
    element("span", "studio-blueprint-chip", `v${blueprint.version}`),
    element("span", "studio-blueprint-chip", versionStatusLabel(item.version_status)),
    element("span", "studio-blueprint-chip", riskLabel(blueprint.risk)),
    element("span", "studio-blueprint-chip", visibilityLabel(blueprint.visibility)),
  );
  card.append(top, title, outcome, meta);
  wrapper.append(card);
  return wrapper;
}

async function selectBlueprint(blueprintId, version = "", { focus = false } = {}) {
  if (!blueprintId) return;
  state.selectedId = blueprintId;
  markSelectedCard();
  renderDetailLoading();
  try {
    const item = await requestJson(detailQuery({
      projectId: state.projectId,
      buildId: state.buildId,
      blueprintId,
      version,
    }));
    if (state.selectedId !== blueprintId) return;
    state.detail = item;
    state.validation = null;
    state.application = null;
    state.formValues = Object.fromEntries(
      (item.blueprint.setup_schema || []).map(field => [field.id, field.default ?? (field.multiple ? [] : "")]),
    );
    renderDetail(item);
    updateSelectedUrl(item.blueprint);
    if (focus) document.querySelector("#studio-blueprint-detail h2")?.focus?.();
  } catch (error) {
    renderDetailFailure(error);
  }
}

function renderDetail(item) {
  const root = document.querySelector("#studio-blueprint-detail");
  const blueprint = item.blueprint;
  const availability = availabilityPresentation(item.availability);
  root.replaceChildren();

  const overview = element("section", "studio-build-card");
  const titleWrap = element("div", "studio-blueprint-detail-title");
  const copy = element("div");
  copy.append(
    element("p", "studio-eyebrow", `${blueprint.category} · v${blueprint.version}`),
    focusableHeading(blueprint.name),
    element("p", "", blueprint.business_outcome),
  );
  const availabilityBadge = element("span", "studio-blueprint-availability", availability.label);
  availabilityBadge.dataset.tone = availability.tone;
  titleWrap.append(copy, availabilityBadge);
  const description = element("p", "", blueprint.description);
  const versionRow = element("div", "studio-blueprint-version-row");
  versionRow.append(
    element("span", "studio-blueprint-chip", blueprint.supported_app_modes.map(appModeLabel).join(" / ")),
    element("span", "studio-blueprint-chip", `Dify ${blueprint.dify_version_range}`),
    element("span", "studio-blueprint-chip", `成本 ${costLabel(blueprint.estimated_cost)}`),
    element("span", "studio-blueprint-chip", visibilityLabel(blueprint.visibility)),
    element("span", "studio-blueprint-chip", versionStatusLabel(item.version_status)),
  );
  overview.append(titleWrap, description, versionRow);
  if (availability.reasons.length) overview.append(listBlock("当前约束", availability.reasons, "studio-blueprint-reasons"));
  if (blueprint.deprecated) overview.append(element("p", "studio-blueprint-field-result", blueprint.deprecation_message || "此版本已弃用。"));

  const preview = element("section", "studio-build-card");
  preview.append(sectionHeading("PREVIEW", "结构与预期行为"), renderPreview(blueprint.preview));

  const setup = element("section", "studio-build-card");
  setup.append(sectionHeading("TYPED SETUP", "映射当前项目资源"));
  const form = element("form", "studio-blueprint-setup");
  form.id = "studio-blueprint-setup-form";
  for (const field of blueprint.setup_schema || []) form.append(renderSetupField(field, item.availability));
  if (!(blueprint.setup_schema || []).length) {
    form.append(element("p", "studio-blueprint-field-result", "此 Blueprint 不需要额外映射。"));
  }
  const fieldResult = element("div");
  fieldResult.id = "studio-blueprint-validation";
  const actions = element("div", "studio-blueprint-detail-actions");
  const validate = actionButton("测试映射并预览", "studio-secondary-action", () => void validateSetup());
  validate.id = "studio-blueprint-validate";
  validate.disabled = !state.buildId || !item.availability.compatible;
  validate.dataset.permanentlyDisabled = String(validate.disabled);
  const apply = actionButton("应用为 Candidate", "studio-primary-action", () => void applyBlueprint());
  apply.id = "studio-blueprint-apply";
  apply.disabled = !state.buildId || !item.availability.applicable;
  apply.dataset.permanentlyDisabled = String(apply.disabled);
  actions.append(validate, apply);
  form.append(fieldResult, actions);
  form.addEventListener("submit", event => {
    event.preventDefault();
    void validateSetup();
  });
  setup.append(form);
  if (!state.buildId) setup.append(element("p", "studio-blueprint-field-result", "请先从 Build Studio 打开 Gallery，应用按钮才会启用。"));

  const evidence = element("section", "studio-build-card");
  evidence.append(sectionHeading("EVIDENCE", "风险、场景与来源"));
  const grid = element("div", "studio-blueprint-evidence-grid");
  grid.append(
    evidenceBlock("业务用例", blueprint.use_cases),
    evidenceBlock("风险与副作用", blueprint.risk_reasons?.length ? blueprint.risk_reasons : ["没有声明额外风险。"]),
    evidenceBlock("确定性校验", blueprint.validators?.length ? blueprint.validators : ["标准 Workflow 校验链"]),
    evidenceBlock("建议场景", (blueprint.scenarios || []).map(scenario => `${scenario.name}：${scenario.expected}`)),
    evidenceBlock("来源", [`${blueprint.provenance.source} · ${blueprint.provenance.author}`, "元数据按不可信输入处理"]),
    evidenceBlock("版本说明", blueprint.upgrade_notes?.length ? blueprint.upgrade_notes : ["当前版本没有额外升级说明。"]),
  );
  evidence.append(grid);
  root.append(overview, preview, setup, evidence);
  const governance = renderVersionGovernance(item);
  if (governance) root.append(governance);
}

function renderVersionGovernance(item) {
  if (item.blueprint.visibility === "builtin") return null;
  const section = element("section", "studio-build-card");
  section.append(sectionHeading("VERSION GOVERNANCE", "团队版本与独立评审"));
  if (item.version_status === "pending_review") {
    if (!item.can_review) {
      section.append(element("p", "studio-blueprint-field-result", "版本正在等待另一位 Owner、Admin 或 Reviewer 评审；作者不能自批。"));
      return section;
    }
    const label = element("label");
    label.append(element("span", "", "评审说明 *"));
    const note = document.createElement("textarea");
    note.id = "studio-blueprint-review-note";
    note.rows = 3;
    note.required = true;
    note.maxLength = 2000;
    note.placeholder = "记录 typed interface、Secret Scan 与升级 Diff 的评审结论";
    label.append(note);
    const actions = element("div", "studio-blueprint-detail-actions");
    actions.append(
      actionButton("批准版本", "studio-primary-action", () => void reviewVersion(true)),
      actionButton("拒绝版本", "danger-button", () => void reviewVersion(false)),
    );
    section.append(label, actions);
    return section;
  }
  if (item.can_propose) {
    const form = element("form", "studio-blueprint-setup");
    const versionLabel = element("label");
    versionLabel.append(element("span", "", "新语义版本 *"));
    const version = document.createElement("input");
    version.id = "studio-blueprint-next-version";
    version.required = true;
    version.pattern = "\\d+\\.\\d+\\.\\d+";
    version.placeholder = nextPatchVersion(item.blueprint.version);
    versionLabel.append(version);
    const notesLabel = element("label");
    notesLabel.append(element("span", "", "升级说明（每行一项） *"));
    const notes = document.createElement("textarea");
    notes.id = "studio-blueprint-upgrade-notes";
    notes.required = true;
    notes.rows = 3;
    notes.maxLength = 4000;
    notesLabel.append(notes);
    const submit = actionButton("提交新版本评审", "studio-secondary-action", () => void proposeVersion());
    form.append(versionLabel, notesLabel, submit);
    form.addEventListener("submit", event => {
      event.preventDefault();
      void proposeVersion();
    });
    section.append(form);
    return section;
  }
  section.append(element("p", "studio-blueprint-field-result", "只有 Blueprint 作者或项目管理员可以提议新版本。"));
  return section;
}

async function proposeVersion() {
  if (!state.detail?.can_propose || state.busy) return;
  const version = document.querySelector("#studio-blueprint-next-version")?.value.trim();
  const notes = document.querySelector("#studio-blueprint-upgrade-notes")?.value
    .split("\n").map(value => value.trim()).filter(Boolean);
  if (!version || !notes?.length) {
    setNotice("请填写有效的新版本号和至少一条升级说明。", "danger");
    return;
  }
  setBusy(true);
  try {
    const blueprint = state.detail.blueprint;
    const record = await requestJson(`/api/v5/studio/blueprints/${encodeURIComponent(blueprint.id)}/versions`, {
      method: "POST",
      body: { project_id: state.projectId, version, upgrade_notes: notes },
    });
    setNotice(`v${record.version} 已提交独立评审，当前应用不会自动升级。`, "success");
    await loadGallery({ announce: false, preserveSelection: false });
    await selectBlueprint(record.blueprint_id, record.version, { focus: true });
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function reviewVersion(approved) {
  if (!state.detail?.can_review || state.busy) return;
  const note = document.querySelector("#studio-blueprint-review-note")?.value.trim();
  if (!note) {
    setNotice("请先填写评审说明。", "danger");
    document.querySelector("#studio-blueprint-review-note")?.focus();
    return;
  }
  setBusy(true);
  try {
    const blueprint = state.detail.blueprint;
    const record = await requestJson(
      `/api/v5/studio/blueprints/${encodeURIComponent(blueprint.id)}/versions/${encodeURIComponent(blueprint.version)}/review`,
      {
        method: "POST",
        body: { project_id: state.projectId, approved, note },
      },
    );
    setNotice(approved ? `v${record.version} 已评审发布。` : `v${record.version} 已拒绝；现有已发布版本不受影响。`, approved ? "success" : "warning");
    state.selectedId = approved ? record.blueprint_id : "";
    await loadGallery({ announce: false, preserveSelection: approved });
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function renderSetupField(field, availability) {
  const label = element("label");
  const labelText = element("span", "", `${field.label}${field.required ? " *" : ""}`);
  const resourceOptions = availability?.available_resources?.[field.id] || [];
  const options = resourceOptions.length ? resourceOptions : field.options || [];
  let control;
  if (options.length) {
    control = document.createElement("select");
    if (!field.required) control.append(option("", "不设置"));
    for (const item of options) control.append(option(item.value ?? item.id ?? "", item.label ?? item.name ?? item.value ?? item.id ?? ""));
    control.value = String(state.formValues[field.id] ?? "");
  } else if (["prompt", "variable"].includes(field.kind)) {
    control = document.createElement("textarea");
    control.rows = field.kind === "prompt" ? 4 : 2;
    control.value = formatFieldValue(state.formValues[field.id]);
  } else if (typeof field.default === "boolean") {
    control = document.createElement("select");
    control.append(option("true", "是"), option("false", "否"));
    control.value = String(state.formValues[field.id] ?? field.default);
  } else {
    control = document.createElement("input");
    control.type = "text";
    control.value = formatFieldValue(state.formValues[field.id]);
  }
  control.id = `studio-blueprint-field-${field.id}`;
  control.name = field.id;
  control.required = field.required;
  control.setAttribute("autocomplete", "off");
  control.addEventListener("input", () => {
    state.formValues[field.id] = control.value;
    state.validation = null;
    document.querySelector("#studio-blueprint-validation")?.replaceChildren();
  });
  const help = element("small", "", `${field.help_text || "Typed Setup 字段"} · ${setupKindLabel(field.kind)}${field.multiple ? " · 可多选（逗号分隔）" : ""}`);
  label.append(labelText, control, help);
  return label;
}

async function validateSetup() {
  if (!state.detail || !state.buildId || state.busy) return;
  const blueprint = state.detail.blueprint;
  collectFormValues(blueprint.setup_schema);
  setBusy(true);
  setNotice("正在用固定的 Capability Manifest 与资源快照校验映射。", "neutral");
  try {
    const validation = await requestJson(`/api/v5/studio/blueprints/${encodeURIComponent(blueprint.id)}/validate`, {
      method: "POST",
      body: setupPayload({
        projectId: state.projectId,
        buildId: state.buildId,
        version: blueprint.version,
        fields: blueprint.setup_schema,
        formValues: state.formValues,
      }),
    });
    state.validation = validation;
    renderValidation(validation);
    setNotice("映射与预期行为已通过确定性校验；仍未发生 Dify 写入。", "success");
  } catch (error) {
    renderValidationError(error);
  } finally {
    setBusy(false);
  }
}

function renderValidation(validation) {
  const root = document.querySelector("#studio-blueprint-validation");
  root.replaceChildren();
  const summary = element("p", "studio-blueprint-field-result", validation.ok ? "✓ 映射有效，可生成一个独立 Candidate。" : "映射尚未通过。" );
  summary.dataset.ok = String(Boolean(validation.ok));
  root.append(summary);
  for (const result of validation.field_results || []) {
    const line = element("p", "studio-blueprint-field-result", `${result.label || result.field_id || "字段"}：${result.message || (result.ok ? "有效" : "无效")}`);
    line.dataset.ok = String(result.ok !== false);
    root.append(line);
  }
  if (validation.preview) root.append(renderPreview(validation.preview));
}

async function applyBlueprint() {
  if (!state.detail || !state.buildId || state.busy) return;
  const blueprint = state.detail.blueprint;
  collectFormValues(blueprint.setup_schema);
  setBusy(true);
  setNotice("正在把 Blueprint 展开为一个普通 Typed Patch 事务。", "neutral");
  try {
    const result = await requestJson(`/api/v5/studio/blueprints/${encodeURIComponent(blueprint.id)}/apply`, {
      method: "POST",
      body: setupPayload({
        projectId: state.projectId,
        buildId: state.buildId,
        version: blueprint.version,
        fields: blueprint.setup_schema,
        formValues: state.formValues,
      }),
    });
    state.application = result;
    renderApplication(result);
    setNotice("Candidate 已创建并权威回读；源 Candidate 未变，Dify 写入为 0。", "success");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

function renderApplication(result) {
  const root = document.querySelector("#studio-blueprint-validation");
  root.replaceChildren();
  const view = applyResultPresentation(result);
  const card = element("div", "studio-blueprint-success");
  card.append(
    element("strong", "", view.ok ? `${view.label} 已就绪` : "Candidate 需要检查"),
    element("p", "", `${view.operationCount} 个操作组成 1 个事务 · Source 未变：${yesNo(view.sourceUnchanged)} · Dify 写入：${view.difyWriteCount}`),
  );
  const actions = element("div", "studio-blueprint-detail-actions");
  const back = document.createElement("a");
  back.className = "studio-primary-action";
  back.href = safeBuildReturnUrl(basePath, state.buildId);
  back.textContent = "回到 Build 查看 Candidate";
  const upgrade = actionButton("检查新版（不自动升级）", "studio-secondary-action", () => void previewUpgrade(view.applicationId));
  actions.append(back, upgrade);
  card.append(actions);
  root.append(card);
}

async function previewUpgrade(applicationId) {
  if (!applicationId || state.busy) return;
  setBusy(true);
  try {
    const params = new URLSearchParams({ project_id: state.projectId });
    const preview = await requestJson(`/api/v5/studio/blueprint-applications/${encodeURIComponent(applicationId)}/upgrade?${params.toString()}`);
    const root = document.querySelector("#studio-blueprint-validation");
    const card = element("div", "studio-blueprint-upgrade");
    card.append(
      element("strong", "", preview.source.version === preview.target.version ? "当前已是最新版本" : `可显式升级到 v${preview.target.version}`),
      element("p", "", "不会自动修改现有 Candidate；升级必须应用为一个新的 Candidate。"),
      listBlock("变更", (preview.changes || []).map(change => change.message || `${change.kind || "change"}: ${change.field || ""}`)),
    );
    root.append(card);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function extractSelection() {
  if (!(state.buildId && identity.candidateId && identity.selectedNodeIds.length) || state.busy) return;
  setBusy(true);
  const output = document.querySelector("#studio-blueprint-extract-result");
  output.textContent = "正在执行节点边界校验、环境 ID 清理与 Secret Scan。";
  try {
    const record = await requestJson("/api/v5/studio/blueprints/extract", {
      method: "POST",
      body: {
        project_id: state.projectId,
        build_id: state.buildId,
        candidate_id: identity.candidateId,
        selected_node_ids: identity.selectedNodeIds,
        name: document.querySelector("#studio-blueprint-extract-name").value.trim(),
        business_outcome: document.querySelector("#studio-blueprint-extract-outcome").value.trim(),
        category: document.querySelector("#studio-blueprint-extract-category").value.trim(),
        visibility: document.querySelector("#studio-blueprint-extract-visibility").value,
        typed_interface: {
          inputs: [interfaceField(document.querySelector("#studio-blueprint-interface-input").value, "选区业务输入")],
          outputs: [interfaceField(document.querySelector("#studio-blueprint-interface-output").value, "选区业务输出")],
          resources: [],
        },
      },
    });
    output.textContent = record.status === "pending_review"
      ? "已通过 Secret Scan 并提交 Team 版本评审；评审通过前不会发布。"
      : "已通过 Secret Scan，Private Blueprint 已可在当前项目使用。";
    document.querySelector("#studio-blueprint-visibility").value = record.definition.visibility;
    await loadGallery({ announce: false, preserveSelection: false });
    await selectBlueprint(record.blueprint_id, record.version, { focus: true });
  } catch (error) {
    output.textContent = error.presentation?.message || error.message || "选区无法安全提取。";
    output.dataset.tone = "danger";
  } finally {
    setBusy(false);
  }
}

function interfaceField(name, description) {
  return { name: String(name || "").trim(), value_type: "string", description, required: true };
}

function renderPreview(preview) {
  const viewport = element("div", "studio-blueprint-preview");
  const surface = element("div", "studio-blueprint-preview-surface");
  const positioned = previewLayout(preview);
  const byRef = new Map(positioned.map(node => [node.ref, node]));
  for (const edge of preview.edges || []) {
    const source = byRef.get(edge.source);
    const target = byRef.get(edge.target);
    if (!source || !target) continue;
    const start = nodePoint(source, true);
    const end = nodePoint(target, false);
    const width = Math.max(20, Math.hypot(end.x - start.x, end.y - start.y));
    const line = element("div", "studio-blueprint-preview-edge");
    line.style.left = `${start.x}px`;
    line.style.top = `${start.y}px`;
    line.style.width = `${width}px`;
    line.style.transform = `rotate(${Math.atan2(end.y - start.y, end.x - start.x)}rad)`;
    if (edge.label) line.append(element("span", "", edge.label));
    surface.append(line);
  }
  for (const node of positioned) {
    const card = element("div", "studio-blueprint-preview-node", node.label);
    card.dataset.tone = node.tone || "neutral";
    card.style.left = `${30 + node.column * 175}px`;
    card.style.top = `${28 + node.row * 82}px`;
    card.setAttribute("aria-label", `${node.kind}：${node.label}`);
    surface.append(card);
  }
  const maxColumn = Math.max(0, ...positioned.map(node => node.column));
  const maxRow = Math.max(0, ...positioned.map(node => node.row));
  surface.style.width = `${Math.max(640, 210 + maxColumn * 175)}px`;
  surface.style.height = `${Math.max(190, 118 + maxRow * 82)}px`;
  viewport.append(surface);
  return viewport;
}

function nodePoint(node, outgoing) {
  return {
    x: 30 + node.column * 175 + (outgoing ? 138 : 0),
    y: 28 + node.row * 82 + 32,
  };
}

function collectFormValues(fields) {
  for (const field of fields || []) {
    const control = document.querySelector(`#studio-blueprint-field-${CSS.escape(field.id)}`);
    if (control) state.formValues[field.id] = control.value;
  }
}

function renderDetailLoading() {
  const root = document.querySelector("#studio-blueprint-detail");
  root.replaceChildren(emptyCard("正在加载 Blueprint", "正在读取版本、兼容性与资源要求。"));
}

function renderEmptyDetail() {
  const root = document.querySelector("#studio-blueprint-detail");
  root.replaceChildren(emptyCard("选择一个 Blueprint", "这里会显示业务结果、兼容性、资源映射、预览图、风险和场景。"));
}

function renderDetailFailure(error) {
  const root = document.querySelector("#studio-blueprint-detail");
  root.replaceChildren(emptyCard(
    error.presentation?.title || "无法读取 Blueprint",
    error.presentation?.message || error.message || "请稍后重试。",
    "重试",
    () => void selectBlueprint(state.selectedId, identity.version, { focus: false }),
  ));
}

function renderGalleryFailure(error) {
  const list = document.querySelector("#studio-blueprint-list");
  const presentation = error.presentation || {};
  list.replaceChildren(emptyCard(
    presentation.title || "Gallery 暂时不可用",
    presentation.message || error.message || "请检查服务状态。",
    presentation.action === "reconnect" ? "重新连接" : "重试",
    () => presentation.action === "reconnect" ? void reconnect() : void loadGallery({ announce: true, preserveSelection: true }),
  ));
  document.querySelector("#studio-blueprint-gallery-state").textContent = presentation.code || "STUDIO_REQUEST_FAILED";
  setConnection(presentation.kind === "offline" ? "Studio 离线" : "需要处理", "danger");
}

function renderValidationError(error) {
  const root = document.querySelector("#studio-blueprint-validation");
  root.replaceChildren();
  const line = element("p", "studio-blueprint-field-result", error.presentation?.message || error.message || "映射校验失败。" );
  line.dataset.ok = "false";
  root.append(line);
  showError(error);
}

function showError(error, { reconnect = false } = {}) {
  const presentation = error.presentation || { title: "Blueprint 请求失败", message: error.message || "发生未预期错误。", kind: "failure" };
  setNotice(`${presentation.title}：${presentation.message}`, "danger");
  setConnection(presentation.kind === "offline" ? "Studio 离线" : "需要处理", "danger");
  if (reconnect || presentation.action === "reconnect") {
    const list = document.querySelector("#studio-blueprint-list");
    list.replaceChildren(emptyCard(presentation.title, presentation.message, "重新连接", () => void reconnect()));
  }
}

async function reconnect() {
  window.location.reload();
}

function setBusy(value) {
  state.busy = value;
  for (const selector of [
    "#studio-blueprint-refresh",
    "#studio-blueprint-validate",
    "#studio-blueprint-apply",
    "#studio-blueprint-extract-form button[type='submit']",
  ]) {
    const control = document.querySelector(selector);
    if (control) control.disabled = value || control.dataset.permanentlyDisabled === "true";
  }
}

function setNotice(message, tone = "neutral") {
  const notice = document.querySelector("#studio-blueprint-notice");
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
  try {
    response = await fetch(apiUrl(path), init);
  } catch (_error) {
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
  return payload;
}

function clearFilters() {
  document.querySelector("#studio-blueprint-search").value = "";
  document.querySelector("#studio-blueprint-category").value = "";
  document.querySelector("#studio-blueprint-mode").value = "";
  document.querySelector("#studio-blueprint-dify-version").value = "";
  document.querySelector("#studio-blueprint-resource").value = "";
  document.querySelector("#studio-blueprint-risk").value = "";
  document.querySelector("#studio-blueprint-visibility").value = "";
  document.querySelector("#studio-blueprint-compatible").checked = true;
  void loadGallery({ announce: true, preserveSelection: false });
}

function markSelectedCard() {
  for (const card of document.querySelectorAll(".studio-blueprint-card")) {
    card.setAttribute("aria-current", card.dataset.blueprintId === state.selectedId ? "true" : "false");
  }
}

function updateSelectedUrl(blueprint) {
  const url = new URL(window.location.href);
  url.searchParams.set("blueprint_id", blueprint.id);
  url.searchParams.set("version", blueprint.version);
  window.history.replaceState({}, "", `${url.pathname}${url.search}`);
}

function emptyCard(title, message, actionLabel = "", handler = null) {
  const card = element("section", "studio-build-card studio-blueprint-empty");
  card.append(element("p", "studio-eyebrow", "BLUEPRINT GALLERY"), element("h2", "", title), element("p", "", message));
  if (actionLabel && handler) card.append(actionButton(actionLabel, "studio-secondary-action", handler));
  return card;
}

function sectionHeading(eyebrow, title) {
  const wrapper = element("div", "studio-section-heading");
  const copy = element("div");
  copy.append(element("p", "studio-eyebrow", eyebrow), element("h2", "", title));
  wrapper.append(copy);
  return wrapper;
}

function focusableHeading(text) {
  const heading = element("h2", "", text);
  heading.tabIndex = -1;
  return heading;
}

function evidenceBlock(title, values) {
  const section = element("section");
  section.append(element("h3", "", title), list(values || []));
  return section;
}

function list(values) {
  const root = element("ul");
  const items = values?.length ? values : ["暂无"];
  for (const value of items) root.append(element("li", "", String(value)));
  return root;
}

function listBlock(title, values, className = "") {
  const wrapper = element("div", className);
  wrapper.append(element("strong", "", title), list(values || []));
  return wrapper;
}

function actionButton(label, className, handler) {
  const button = element("button", className, label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function option(value, label) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  return item;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

function formatFieldValue(value) {
  return Array.isArray(value) ? value.join(", ") : String(value ?? "");
}

function riskLabel(risk) {
  return { low: "低风险", medium: "中风险", high: "高风险" }[risk] || risk;
}

function costLabel(cost) {
  return { none: "无", low: "低", medium: "中", high: "高", variable: "取决于用量" }[cost] || cost;
}

function visibilityLabel(visibility) {
  return { builtin: "内置", private: "Private", team: "Team" }[visibility] || visibility;
}

function versionStatusLabel(status) {
  return {
    published: "已发布",
    pending_review: "待评审",
    rejected: "已拒绝",
    deprecated: "已弃用",
  }[status] || status;
}

function nextPatchVersion(version) {
  const parts = String(version || "0.0.0").split(".").map(Number);
  if (parts.length !== 3 || parts.some(value => !Number.isInteger(value) || value < 0)) return "1.0.1";
  return `${parts[0]}.${parts[1]}.${parts[2] + 1}`;
}

function setupKindLabel(kind) {
  return { model: "模型", dataset: "知识库", tool: "工具", trigger: "触发器", prompt: "Prompt", variable: "变量", policy: "策略" }[kind] || kind;
}

function appModeLabel(mode) {
  return { workflow: "Workflow", "advanced-chat": "Chatflow", chat: "Chatbot", completion: "文本生成", "agent-chat": "Dify Agent" }[mode] || mode;
}

function yesNo(value) {
  return value ? "是" : "否";
}

function createNonce() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (!globalThis.crypto?.getRandomValues) throw new Error("浏览器不支持安全随机数，无法建立 Studio 会话。");
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(24));
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
}

function apiUrl(path) {
  return `${basePath}${path.startsWith("/") ? path : `/${path}`}`;
}

function normalizeBasePath(value) {
  const normalized = String(value || "").trim();
  if (!normalized || normalized === "/") return "";
  return `${normalized.startsWith("/") ? "" : "/"}${normalized}`.replace(/\/+$/, "");
}
