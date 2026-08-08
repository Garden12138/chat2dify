import { classifyStudioError } from "../home/core.mjs";
import {
  authorizationPayload,
  isReleaseCenterEnabled,
  mappingRows,
  releaseCenterTone,
  releaseHistoryEvidence,
  releaseIdentity,
  releasePermissions,
  releasePresentation,
  releasePreviewCards,
  reviewPolicyDefaults,
  reviewPresentation,
  shortHash,
  validateReviewAssignment,
} from "./core.mjs";

const config = window.CHAT2DIFY_CONFIG || {};
const basePath = String(config.basePath || "").replace(/\/+$/, "");
const identity = releaseIdentity(window.location.search);
const state = {
  token: "",
  projectId: "",
  principalKey: "",
  center: null,
  detail: null,
  preview: null,
  pendingAction: null,
  confirmTrigger: null,
  reviewPolicyInitialized: false,
};

if (isReleaseCenterEnabled(config, window.location.search)) {
  document.addEventListener("DOMContentLoaded", () => void boot());
}

async function boot() {
  document.body.classList.add("studio-v5", "studio-v5-releases");
  if (identity.embedded) document.body.classList.add("studio-embedded");
  document.querySelector("#legacy-app-frame")?.setAttribute("hidden", "");
  document.querySelector("#studio-root").hidden = false;
  document.querySelector("#studio-content").hidden = true;
  document.querySelector("#studio-build-content").hidden = true;
  document.querySelector("#studio-blueprint-content").hidden = true;
  document.querySelector("#studio-scenario-content").hidden = true;
  document.querySelector("#studio-release-content").hidden = false;
  document.querySelector("#studio-state").hidden = true;
  document.querySelectorAll(".studio-nav-item").forEach(item => {
    item.classList.remove("studio-nav-active");
    item.removeAttribute("aria-current");
  });
  const nav = document.querySelector("#studio-releases-nav");
  nav?.classList.add("studio-nav-active");
  nav?.setAttribute("aria-current", "page");
  bindActions();
  await connect();
}

function bindActions() {
  document.querySelector("#studio-release-refresh").addEventListener("click", () => void loadCenter(true));
  document.querySelector("#studio-release-create-form").addEventListener("submit", event => {
    event.preventDefault();
    void createChangeRequest();
  });
  document.querySelector("#studio-release-comment-form").addEventListener("submit", event => {
    event.preventDefault();
    void comment();
  });
  document.querySelectorAll("[data-review-decision]").forEach(button => {
    button.addEventListener("click", () => void decide(button.dataset.reviewDecision));
  });
  document.querySelector("#studio-logical-app-form").addEventListener("submit", event => {
    event.preventDefault();
    void createLogicalApp();
  });
  document.querySelector("#studio-environment-form").addEventListener("submit", event => {
    event.preventDefault();
    void createEnvironment();
  });
  document.querySelector("#studio-release-environment-select").addEventListener("change", () => {
    state.preview = null;
    renderPreview(null);
    updateReleaseControls();
  });
  document.querySelector("#studio-release-preview").addEventListener("click", () => void loadPreview());
  document.querySelector("#studio-release-save-mapping").addEventListener("click", () => void saveMapping());
  document.querySelector("#studio-release-apply").addEventListener("click", event => openConfirm("apply_draft", event.currentTarget));
  document.querySelector("#studio-release-publish").addEventListener("click", event => openConfirm("publish", event.currentTarget));
  document.querySelector("#studio-release-git-export").addEventListener("click", () => void exportGit());
  document.querySelector("#studio-release-confirm").addEventListener("close", event => {
    const dialog = event.currentTarget;
    const confirmed = dialog.returnValue === "confirm" && state.pendingAction;
    if (confirmed) {
      void authorizeAndExecute(state.pendingAction);
    }
    const trigger = state.confirmTrigger;
    state.pendingAction = null;
    state.confirmTrigger = null;
    if (!confirmed) window.setTimeout(() => trigger?.focus(), 0);
  });
}

async function connect() {
  setNotice("正在验证 Dify 会话与项目权限。", "loading");
  try {
    const session = await requestJson("/api/v5/studio/session", {
      method: "POST",
      body: { nonce: createNonce() },
      authenticated: false,
    });
    state.token = session.token;
    state.projectId = session.project.id;
    state.principalKey = session.principal.key;
    document.querySelector("#studio-project-badge").textContent = session.project.name;
    document.querySelector("#studio-release-context").textContent = `${session.project.name} · ${session.membership.role}`;
    setConnection("Dify 已验证", "ok");
    await loadCenter(false);
  } catch (error) {
    setConnection("连接失败", "danger");
    setNotice(error.presentation?.message || "无法打开 Review & Release Center。", "danger");
  }
}

async function loadCenter(announce) {
  if (announce) setNotice("正在刷新评审、环境与真实 Release 回执。", "loading");
  try {
    state.center = await requestJson(
      `/api/v5/studio/release-center?project_id=${encodeURIComponent(state.projectId)}`,
    );
    renderCenter();
    const requestedId = identity.changeRequestId || state.detail?.change_request?.id;
    const target = state.center.change_requests.find(item => item.id === requestedId)
      || state.center.change_requests[0];
    if (target) await selectRequest(target.id);
    else clearDetail();
    setNotice(state.center.message, releaseCenterTone(state.center.state));
  } catch (error) {
    setNotice(error.presentation?.message || "Release Center 刷新失败。", "danger");
  }
}

function renderCenter() {
  renderPermissions();
  renderRequests();
  renderCreateHandoff();
  renderReviewers();
  renderLogicalApps();
  renderAvailableApps();
  renderEnvironments();
  renderHistory();
}

function renderRequests() {
  const root = document.querySelector("#studio-release-requests");
  root.replaceChildren();
  for (const item of state.center?.change_requests || []) {
    const presentation = reviewPresentation(item.status);
    const button = element("button", "studio-release-request");
    button.type = "button";
    button.dataset.requestId = item.id;
    button.dataset.tone = presentation.tone;
    button.setAttribute("role", "listitem");
    button.setAttribute("aria-current", String(state.detail?.change_request?.id === item.id));
    button.append(
      element("strong", "", item.title),
      element("small", "", `${presentation.label} · Artifact ${shortHash(item.artifact_hash)}`),
    );
    button.addEventListener("click", () => void selectRequest(item.id));
    root.append(button);
  }
  if (!root.children.length) root.append(empty("还没有 Change Request", "从 cleanup-verified Scenario 结果提交首项评审。"));
}

function renderCreateHandoff() {
  const permissions = releasePermissions(state.center?.membership?.role);
  const ready = permissions.canAuthor
    && identity.buildId
    && identity.candidateId
    && identity.scenarioRunId;
  document.querySelector("#studio-release-create-form").hidden = !ready;
  const emptyState = document.querySelector("#studio-release-handoff-empty");
  emptyState.hidden = Boolean(ready);
  emptyState.textContent = permissions.canAuthor
    ? "请从 cleanup-verified Scenario 结果进入，无需复制 Build、Candidate 或 Run ID。"
    : "当前项目角色为只读评审视图，不能创建 Change Request。";
  if (ready) {
    document.querySelector("#studio-release-create-context").textContent = "已接收服务器可验证的 Candidate 与 Scenario Evidence；表单不会提交 Raw Graph、DSL 或浏览器身份。";
  }
}

function renderReviewers() {
  const select = document.querySelector("#studio-release-create-reviewer");
  const current = select.value;
  select.replaceChildren(option("", "暂不分配 Reviewer"));
  for (const member of (state.center?.members || []).filter(item => ["owner", "admin", "reviewer"].includes(item.role))) {
    select.append(option(member.principal_key, `${memberLabel(member.principal_key)} · ${member.role}`));
  }
  if ([...select.options].some(item => item.value === current)) select.value = current;
  if (!state.reviewPolicyInitialized) {
    const defaults = reviewPolicyDefaults(state.center?.members || [], state.principalKey);
    document.querySelector("#studio-release-create-separation").checked = defaults.requireSeparation;
    state.reviewPolicyInitialized = true;
  }
}

async function createChangeRequest() {
  const reviewer = value("#studio-release-create-reviewer");
  const requireSeparation = document.querySelector("#studio-release-create-separation").checked;
  const assignment = validateReviewAssignment({
    assigneeKey: reviewer,
    principalKey: state.principalKey,
    requireSeparation,
  });
  if (!assignment.ok) return setNotice(assignment.message, "danger");
  try {
    const detail = await requestJson("/api/v5/studio/reviews", {
      method: "POST",
      body: {
        project_id: state.projectId,
        build_id: identity.buildId,
        candidate_id: identity.candidateId,
        scenario_run_id: identity.scenarioRunId,
        title: value("#studio-release-create-title"),
        release_note: value("#studio-release-create-note"),
        assignee_key: reviewer || null,
        require_author_approver_separation: requireSeparation,
        expires_in_seconds: 604800,
        repair_proposal_id: identity.repairProposalId || null,
        repair_proposal_version: identity.repairProposalVersion || null,
      },
    });
    state.detail = detail;
    await loadCenter(false);
    await selectRequest(detail.change_request.id);
    setNotice("Change Request 已绑定精确 Artifact 与 Scenario Evidence。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "无法创建 Change Request。", "danger");
  }
}

async function selectRequest(id) {
  try {
    state.detail = await requestJson(
      `/api/v5/studio/reviews/${encodeURIComponent(id)}?project_id=${encodeURIComponent(state.projectId)}`,
    );
    state.preview = null;
    renderRequests();
    renderDetail();
    renderPreview(null);
    updateReleaseControls();
  } catch (error) {
    setNotice(error.presentation?.message || "无法读取这项评审。", "danger");
  }
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) return clearDetail();
  const request = detail.change_request;
  const presentation = reviewPresentation(request.status);
  const status = document.querySelector("#studio-release-review-status");
  status.textContent = presentation.label;
  status.dataset.tone = presentation.tone;
  const evidence = detail.artifact.scenario_evidence || detail.artifact.payload?.scenario_evidence || {};
  const root = document.querySelector("#studio-release-detail");
  root.replaceChildren(
    element("h3", "", request.title),
    element("p", "", request.release_note),
  );
  const proofs = element("div", "studio-release-proof-grid");
  proofs.append(
    proof("Artifact", shortHash(request.artifact_hash), "Canonical · immutable · secrets-free"),
    proof("Scenario", `${Math.round(Number(evidence.pass_rate || 0) * 100)}% passed`, `质量 ${Number(evidence.quality_score || 0).toFixed(1)} · Cleanup ${evidence.cleanup_verified ? "已验证" : "未完成"}`),
    proof("Author", memberLabel(request.author_key), request.policy.require_author_approver_separation ? "要求职责分离" : "允许策略内审批"),
    proof("Reviewer", request.assignee_key ? memberLabel(request.assignee_key) : "未分配", `版本 ${request.version}`),
  );
  root.append(proofs);
  if (detail.stale_reasons?.length) {
    const stale = element("ul", "studio-release-blockers");
    detail.stale_reasons.forEach(reason => stale.append(element("li", "", reason)));
    root.append(stale);
  }
  renderAssignment(root);
  const permissions = releasePermissions(state.center?.membership?.role);
  const canSupersede = request.author_key === state.principalKey
    || permissions.canConfigureRelease;
  if (canSupersede && request.status === "changes_requested" && identity.buildId && identity.candidateId && identity.scenarioRunId) {
    const button = element("button", "studio-secondary-action", "用当前已测试修正版 Supersede");
    button.type = "button";
    button.addEventListener("click", () => void supersede());
    root.append(button);
  }
  const events = document.querySelector("#studio-release-events");
  events.replaceChildren();
  for (const item of detail.events || []) {
    const row = element("li");
    row.append(
      element("strong", "", `${eventLabel(item.kind)} · ${memberLabel(item.actor_key)}`),
      element("p", "", item.body || "无附加说明"),
      element("time", "", new Date(item.created_at).toLocaleString("zh-CN")),
    );
    events.append(row);
  }
  document.querySelector("#studio-release-comment-form").hidden = !detail.can_comment;
  document.querySelector("#studio-release-decisions").hidden = !detail.can_decide;
}

function renderAssignment(root) {
  const request = state.detail.change_request;
  const canAssign = state.center.membership.role === "owner"
    || state.center.membership.role === "admin"
    || request.author_key === state.principalKey;
  if (!canAssign || !["in_review", "changes_requested"].includes(request.status)) return;
  const wrap = element("div", "studio-inline-actions");
  const select = element("select");
  select.setAttribute("aria-label", "重新分配 Reviewer");
  const eligible = state.center.members.filter(item =>
    ["owner", "admin", "reviewer"].includes(item.role)
      && (!request.policy.require_author_approver_separation
        || item.principal_key !== request.author_key),
  );
  for (const member of eligible) {
    select.append(option(member.principal_key, `${memberLabel(member.principal_key)} · ${member.role}`));
  }
  select.value = request.assignee_key || "";
  const button = element("button", "studio-secondary-action", "更新 Assignment");
  button.type = "button";
  button.addEventListener("click", () => void assign(select.value));
  wrap.append(select, button);
  root.append(wrap);
}

function renderPermissions() {
  const permissions = releasePermissions(state.center?.membership?.role);
  for (const formId of ["#studio-logical-app-form", "#studio-environment-form"]) {
    document.querySelectorAll(`${formId} input, ${formId} select, ${formId} button`).forEach(control => {
      control.disabled = !permissions.canConfigureRelease;
    });
  }
}

async function comment() {
  const body = value("#studio-release-comment");
  if (!body || !state.detail) return;
  try {
    state.detail = await requestJson(
      `/api/v5/studio/reviews/${encodeURIComponent(state.detail.change_request.id)}/comments`,
      { method: "POST", body: { project_id: state.projectId, body } },
    );
    document.querySelector("#studio-release-comment").value = "";
    renderDetail();
    setNotice("评论已追加到项目审计记录。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "评论保存失败。", "danger");
  }
}

async function assign(assigneeKey) {
  if (!assigneeKey || !state.detail) return;
  try {
    state.detail = await requestJson(
      `/api/v5/studio/reviews/${encodeURIComponent(state.detail.change_request.id)}/assignment`,
      {
        method: "POST",
        body: {
          project_id: state.projectId,
          assignee_key: assigneeKey,
          expected_version: state.detail.change_request.version,
        },
      },
    );
    renderDetail();
    setNotice("Reviewer Assignment 已更新。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "Assignment 更新失败。", "danger");
  }
}

async function decide(decision) {
  if (!state.detail) return;
  const defaultBody = {
    request_changes: "请修正后重新运行 Scenario，并用新证据 Supersede。",
    approve: "批准当前精确 Artifact 与 Scenario Evidence。",
    reject: "拒绝当前发布提案。",
  }[decision];
  const body = value("#studio-release-comment") || defaultBody;
  try {
    state.detail = await requestJson(
      `/api/v5/studio/reviews/${encodeURIComponent(state.detail.change_request.id)}/decision`,
      {
        method: "POST",
        body: {
          project_id: state.projectId,
          decision,
          body,
          expected_version: state.detail.change_request.version,
          expected_binding_hash: state.detail.change_request.binding_hash,
        },
      },
    );
    document.querySelector("#studio-release-comment").value = "";
    await loadCenter(false);
    setNotice(`评审决定已记录：${reviewPresentation(state.detail.change_request.status).label}。`, "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "评审决定失败；请刷新精确绑定。", "danger");
  }
}

async function supersede() {
  const request = state.detail?.change_request;
  if (!request) return;
  try {
    state.detail = await requestJson(
      `/api/v5/studio/reviews/${encodeURIComponent(request.id)}/supersede`,
      {
        method: "POST",
        body: {
          project_id: state.projectId,
          expected_version: request.version,
          build_id: identity.buildId,
          candidate_id: identity.candidateId,
          scenario_run_id: identity.scenarioRunId,
          title: `${request.title} · 修正版`,
          release_note: value("#studio-release-create-note") || request.release_note,
          expires_in_seconds: 604800,
        },
      },
    );
    await loadCenter(false);
    setNotice("修正版已 Supersede 旧提案；旧决定没有被继承。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "无法 Supersede 当前提案。", "danger");
  }
}

function clearDetail() {
  state.detail = null;
  document.querySelector("#studio-release-review-status").textContent = "选择一项评审";
  document.querySelector("#studio-release-detail").replaceChildren(empty("等待选择", "选择 Change Request 查看评论、证据与决定。"));
  document.querySelector("#studio-release-events").replaceChildren();
  document.querySelector("#studio-release-comment-form").hidden = true;
  document.querySelector("#studio-release-decisions").hidden = true;
  updateReleaseControls();
}

function renderLogicalApps() {
  const select = document.querySelector("#studio-environment-logical");
  const current = select.value;
  select.replaceChildren(option("", "选择 Logical App"));
  for (const app of state.center?.logical_apps || []) select.append(option(app.id, `${app.name} · ${app.app_mode}`));
  if ([...select.options].some(item => item.value === current)) select.value = current;
}

function renderAvailableApps() {
  const select = document.querySelector("#studio-environment-target");
  const current = select.value;
  select.replaceChildren(option("", "选择当前账号可访问的 App"));
  for (const app of state.center?.available_apps || []) select.append(option(app.id, `${app.name} · ${app.mode}`));
  if ([...select.options].some(item => item.value === current)) select.value = current;
}

function renderEnvironments() {
  const select = document.querySelector("#studio-release-environment-select");
  const current = select.value;
  select.replaceChildren(option("", "选择发布环境"));
  for (const env of state.center?.environments || []) select.append(option(env.id, `${env.name} · ${env.classification}`));
  if ([...select.options].some(item => item.value === current)) select.value = current;
  else if (select.options.length > 1) select.selectedIndex = 1;
  updateReleaseControls();
}

async function createLogicalApp() {
  try {
    await requestJson("/api/v5/studio/logical-apps", {
      method: "POST",
      body: {
        project_id: state.projectId,
        name: value("#studio-logical-app-name"),
        app_mode: value("#studio-logical-app-mode"),
      },
    });
    await loadCenter(false);
    setNotice("Logical App 已创建；它不包含 Dify 凭据。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "Logical App 创建失败。", "danger");
  }
}

async function createEnvironment() {
  try {
    await requestJson("/api/v5/studio/release-environments", {
      method: "POST",
      body: {
        project_id: state.projectId,
        logical_app_id: value("#studio-environment-logical"),
        name: value("#studio-environment-name"),
        classification: value("#studio-environment-class"),
        target_app_ref: value("#studio-environment-target"),
      },
    });
    await loadCenter(false);
    setNotice("环境已绑定，并由服务器读取当前 Dify Draft Hash 作为跟踪基线。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "环境绑定失败。", "danger");
  }
}

function updateReleaseControls() {
  const permissions = releasePermissions(state.center?.membership?.role);
  const ready = Boolean(state.detail && value("#studio-release-environment-select"));
  document.querySelector("#studio-release-preview").disabled = !ready;
  document.querySelector("#studio-release-save-mapping").disabled = !state.preview
    || !permissions.canConfigureRelease;
  document.querySelector("#studio-release-apply").disabled = !permissions.canConfigureRelease;
  document.querySelector("#studio-release-publish").disabled = !permissions.canConfigureRelease;
}

async function loadPreview() {
  if (!state.detail) return;
  try {
    state.preview = await requestJson("/api/v5/studio/release-preview", {
      method: "POST",
      body: {
        project_id: state.projectId,
        change_request_id: state.detail.change_request.id,
        environment_id: value("#studio-release-environment-select"),
      },
    });
    renderPreview(state.preview);
    setNotice(
      state.preview.blockers.length ? "Release Preview 已生成，但存在阻塞项。" : "Release Preview 与当前 Dify Hash 已精确绑定。",
      state.preview.blockers.length ? "warning" : "ok",
    );
  } catch (error) {
    setNotice(error.presentation?.message || "Release Preview 生成失败。", "danger");
  }
}

function renderPreview(preview) {
  const root = document.querySelector("#studio-release-preview-result");
  const mappingRoot = document.querySelector("#studio-release-mapping");
  root.replaceChildren();
  mappingRoot.replaceChildren();
  document.querySelector("#studio-release-actions").hidden = true;
  if (!preview) {
    root.append(empty("尚未生成 Release Preview", "选择已批准 Change Request 与环境后读取实时 Dify 状态。"));
    updateReleaseControls();
    return;
  }
  const mappingSet = state.center.mappings.find(item => item.environment_id === preview.environment_id);
  for (const row of mappingRows(preview, mappingSet)) mappingRoot.append(mappingEditor(row));
  if (!mappingRoot.children.length) mappingRoot.append(element("p", "studio-scenario-boundary", "这个 Artifact 没有环境资源引用；Mapping 为空也是一个精确 Hash。"));
  const cards = element("div", "studio-release-preview-cards");
  for (const item of releasePreviewCards(preview)) {
    const card = element("article", "studio-release-preview-card");
    card.dataset.tone = item.tone;
    card.append(element("span", "", item.title), element("strong", "", item.value), element("small", "", item.detail));
    cards.append(card);
  }
  root.append(cards, element("p", "studio-scenario-boundary", `Release Note：${preview.release_note}`));
  if (preview.blockers.length) {
    const blockers = element("ul", "studio-release-blockers");
    preview.blockers.forEach(item => blockers.append(element("li", "", item.message)));
    root.append(blockers);
  }
  document.querySelector("#studio-release-actions").hidden = preview.blockers.length > 0;
  updateReleaseControls();
}

function mappingEditor(row) {
  const permissions = releasePermissions(state.center?.membership?.role);
  const wrap = element("div", "studio-release-mapping-row");
  wrap.dataset.logicalRef = row.logicalRef;
  wrap.dataset.kind = row.kind;
  const source = element("div");
  source.append(element("strong", "", row.label), element("small", "studio-release-mapping-kind", row.kind));
  const label = element("label");
  label.append(element("span", "", row.kind === "credential_availability" ? "可用性（不接受值）" : "目标 Opaque Ref"));
  const input = element("input");
  input.value = row.targetRef;
  input.required = true;
  input.readOnly = row.kind === "credential_availability"
    || !permissions.canConfigureRelease;
  input.setAttribute("aria-label", `${row.label} 目标映射`);
  label.append(input);
  const availability = element("label", "studio-release-check");
  const check = element("input");
  check.type = "checkbox";
  check.checked = row.available;
  if (row.kind === "credential_availability" || !permissions.canConfigureRelease) check.disabled = true;
  availability.append(check, element("span", "", "Available"));
  wrap.append(source, label, availability);
  return wrap;
}

async function saveMapping() {
  const environmentId = value("#studio-release-environment-select");
  const current = state.center.mappings.find(item => item.environment_id === environmentId);
  const mappings = [...document.querySelectorAll(".studio-release-mapping-row")].map(row => ({
    kind: row.dataset.kind,
    logical_ref: row.dataset.logicalRef,
    target_ref: row.querySelector("input:not([type='checkbox'])").value.trim(),
    available: row.querySelector("input[type='checkbox']").checked,
    secret: false,
  }));
  try {
    await requestJson(`/api/v5/studio/release-environments/${encodeURIComponent(environmentId)}/mappings`, {
      method: "PUT",
      body: {
        project_id: state.projectId,
        mappings,
        expected_version: current?.version ?? null,
      },
    });
    await loadCenter(false);
    await loadPreview();
    setNotice("Opaque Mapping 已保存；Credential 仅记录 availability。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "Mapping 保存失败。", "danger");
  }
}

function openConfirm(action, trigger) {
  if (!state.preview || state.preview.blockers.length) return;
  state.pendingAction = action;
  state.confirmTrigger = trigger;
  const publish = action === "publish";
  document.querySelector("#studio-release-confirm-title").textContent = publish ? "独立确认 Publish" : "确认 Apply Draft";
  document.querySelector("#studio-release-confirm-body").textContent = publish
    ? "Publish 是与 Apply Draft 分离的高风险动作。它会为当前已应用 Artifact 创建新的显式授权。"
    : "Apply Draft 只写入当前环境的 Dify Draft，不会自动 Publish。";
  document.querySelector("#studio-release-confirm-action").textContent = publish ? "确认 Publish" : "确认 Apply Draft";
  document.querySelector("#studio-release-confirm").showModal();
}

async function authorizeAndExecute(action) {
  try {
    const authorization = await requestJson("/api/v5/studio/release-authorizations", {
      method: "POST",
      body: authorizationPayload({
        projectId: state.projectId,
        changeRequestId: state.detail.change_request.id,
        environmentId: value("#studio-release-environment-select"),
        action,
      }),
    });
    const intent = await requestJson("/api/v5/studio/release-executions", {
      method: "POST",
      body: {
        project_id: state.projectId,
        authorization_id: authorization.id,
        idempotency_key: `${action}-${randomId()}`,
      },
    });
    const record = await waitForRelease(intent);
    await loadCenter(false);
    await selectRequest(state.detail.change_request.id);
    if (record.outcome === "succeeded") {
      setNotice(action === "publish" ? "Publish 成功，回执已保存。" : "Apply Draft 成功并完成权威 Hash 回读。", "ok");
    } else {
      setNotice(releasePresentation(record).outcome, record.outcome === "failed" ? "danger" : "warning");
    }
  } catch (error) {
    setNotice(error.presentation?.message || "高风险动作未完成；不会自动重试。", "danger");
  } finally {
    document.querySelector("#studio-release-notice")?.focus();
  }
}

async function waitForRelease(record) {
  if (record.outcome !== "intent_recorded") return record;
  let current = record;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise(resolve => window.setTimeout(resolve, 300));
    const center = await requestJson(`/api/v5/studio/release-center?project_id=${encodeURIComponent(state.projectId)}`);
    current = (center.releases || []).find(item => item.id === record.id) || current;
    if (current.outcome !== "intent_recorded") return current;
  }
  return current;
}

async function exportGit() {
  if (!state.detail) return;
  try {
    const bundle = await requestJson(
      `/api/v5/studio/artifacts/${encodeURIComponent(state.detail.artifact.id)}/git?project_id=${encodeURIComponent(state.projectId)}`,
    );
    const blob = new Blob([JSON.stringify(bundle.files, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `workflow-artifact-${bundle.content_hash.slice(0, 12)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    setNotice("已导出 deterministic、secrets-free Git 文件；没有自动 push 或 merge。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "Git serialization 导出失败。", "danger");
  }
}

function renderHistory() {
  const permissions = releasePermissions(state.center?.membership?.role);
  const root = document.querySelector("#studio-release-history");
  root.replaceChildren();
  for (const record of state.center?.releases || []) {
    const presentation = releasePresentation(record);
    const evidence = releaseHistoryEvidence(record, state.center);
    const row = element("article", "studio-release-history-item");
    const status = element("span", "studio-project-badge", presentation.outcome);
    status.dataset.tone = presentation.tone;
    const body = element("div");
    body.append(
      element("strong", "", presentation.action),
      element("p", "", record.release_note),
      element("small", "", `Artifact ${evidence.artifact} · ${evidence.environment} · Dify Hash ${evidence.hash}`),
      element("small", "", `Actor ${memberLabel(record.actor_key)} · ${evidence.receipt}`),
      element("small", "", `Evidence：${evidence.evidence}`),
    );
    const rollback = element("button", "studio-secondary-action", "Propose Rollback");
    rollback.type = "button";
    rollback.disabled = record.outcome !== "succeeded" || !permissions.canRollback;
    rollback.addEventListener("click", () => void proposeRollback(record.artifact_id));
    row.append(status, body, rollback);
    root.append(row);
  }
  if (!root.children.length) root.append(empty("还没有 Release Receipt", "Apply Draft 与 Publish 的真实结果会分别显示在这里。"));
}

async function proposeRollback(artifactId) {
  const reviewer = value("#studio-release-create-reviewer") || null;
  try {
    const detail = await requestJson("/api/v5/studio/rollback-proposals", {
      method: "POST",
      body: {
        project_id: state.projectId,
        artifact_id: artifactId,
        title: "Rollback Proposal",
        release_note: "回退到先前 Artifact；仍需重新 Review、Apply 与显式 Publish。",
        assignee_key: reviewer,
        require_author_approver_separation: Boolean(reviewer),
        expires_in_seconds: 604800,
      },
    });
    await loadCenter(false);
    await selectRequest(detail.change_request.id);
    setNotice("Rollback 已创建为新的 Change Request；没有覆盖当前 Dify Drift。", "ok");
  } catch (error) {
    setNotice(error.presentation?.message || "Rollback Proposal 创建失败。", "danger");
  }
}

async function requestJson(path, { method = "GET", body, authenticated = true } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (authenticated && state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(`${basePath}${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = {};
  try { payload = await response.json(); } catch (_error) { payload = {}; }
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
    error.presentation = classifyStudioError(response.status, payload);
    throw error;
  }
  return payload;
}

function setNotice(message, tone = "ready") {
  const notice = document.querySelector("#studio-release-notice");
  notice.textContent = message;
  notice.dataset.tone = tone;
  if (["danger", "warning"].includes(tone)) notice.focus();
}

function setConnection(message, tone) {
  const connection = document.querySelector("#studio-connection");
  connection.textContent = message;
  connection.dataset.tone = tone;
}

function proof(title, valueText, detail) {
  const card = element("article", "studio-release-proof");
  card.append(element("span", "", title), element("strong", "", valueText), element("small", "", detail));
  return card;
}

function empty(title, body) {
  const card = element("article", "studio-scenario-empty");
  card.append(element("h3", "", title), element("p", "", body));
  return card;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function option(valueText, label) {
  const item = document.createElement("option");
  item.value = valueText;
  item.textContent = label;
  return item;
}

function value(selector) {
  return String(document.querySelector(selector)?.value || "").trim();
}

function memberLabel(key) {
  if (key === "system:expiry") return "System Expiry";
  return String(key || "未指定").split(":").pop();
}

function eventLabel(kind) {
  return {
    created: "Created",
    assigned: "Assigned",
    commented: "Comment",
    changes_requested: "Request Changes",
    approved: "Approved",
    rejected: "Rejected",
    superseded: "Superseded",
    expired: "Expired",
    rollback_proposed: "Rollback Proposed",
    git_pull_created: "Git Pull",
  }[kind] || kind;
}

function createNonce() {
  const bytes = new Uint8Array(24);
  window.crypto.getRandomValues(bytes);
  return [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
}

function randomId() {
  return window.crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
