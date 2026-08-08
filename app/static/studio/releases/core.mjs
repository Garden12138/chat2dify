const REVIEW_STATUSES = Object.freeze({
  in_review: { label: "等待评审", tone: "loading" },
  changes_requested: { label: "需要修改", tone: "warning" },
  approved: { label: "已批准精确版本", tone: "ok" },
  rejected: { label: "已拒绝", tone: "danger" },
  superseded: { label: "已被修正版替代", tone: "muted" },
  expired: { label: "已过期", tone: "warning" },
});

export function isReleaseCenterEnabled(config, search = "") {
  return Boolean(config?.studioV5Enabled)
    && new URLSearchParams(search).get("studio") === "releases";
}

export function releaseIdentity(search = "") {
  const params = new URLSearchParams(search);
  return {
    buildId: bounded(params.get("build_id")),
    candidateId: bounded(params.get("candidate_id")),
    scenarioRunId: bounded(params.get("scenario_run_id")),
    changeRequestId: bounded(params.get("change_request_id")),
    repairProposalId: bounded(params.get("repair_proposal_id")),
    repairProposalVersion: Math.max(0, Number.parseInt(params.get("repair_proposal_version") || "0", 10) || 0),
    embedded: ["1", "true", "yes"].includes(String(params.get("embed") || "").toLowerCase()),
  };
}

export function reviewPresentation(status = "in_review") {
  return REVIEW_STATUSES[status] || { label: String(status), tone: "muted" };
}

export function releaseCenterTone(state = "ready") {
  return {
    ready: "ok",
    empty: "warning",
    partial_error: "warning",
    permission_denied: "danger",
    offline: "danger",
  }[state] || "danger";
}

export function reviewPolicyDefaults(members = [], principalKey = "") {
  const reviewers = members.filter(item =>
    ["owner", "admin", "reviewer"].includes(item.role),
  );
  return {
    requireSeparation: reviewers.some(item => item.principal_key !== principalKey),
  };
}

export function validateReviewAssignment({
  assigneeKey = "",
  principalKey = "",
  requireSeparation = false,
} = {}) {
  if (requireSeparation && !assigneeKey) {
    return {
      ok: false,
      message: "启用 Author / Approver Separation 时必须选择另一位 Reviewer。",
    };
  }
  if (requireSeparation && assigneeKey === principalKey) {
    return {
      ok: false,
      message: "职责分离要求 Reviewer 与 Author 不是同一用户。",
    };
  }
  return { ok: true, message: "" };
}

export function releasePermissions(role = "viewer") {
  return {
    canAuthor: ["owner", "admin", "builder"].includes(role),
    canConfigureRelease: ["owner", "admin"].includes(role),
    canRollback: ["owner", "admin", "builder"].includes(role),
  };
}

export function releasePresentation(record = {}) {
  const action = record.action === "publish" ? "Publish" : "Apply Draft";
  const outcome = {
    intent_recorded: "已进入安全队列，等待权威回读",
    succeeded: "成功且已回读",
    failed: "确定失败",
    ambiguous: "结果含糊，禁止自动重试",
    conflicted: "Hash 冲突，未覆盖",
  }[record.outcome] || record.outcome;
  return {
    action,
    outcome,
    tone: record.outcome === "succeeded" ? "ok"
      : record.outcome === "failed" ? "danger"
        : record.outcome === "intent_recorded" ? "loading" : "warning",
  };
}

export function releaseHistoryEvidence(record = {}, center = {}) {
  const request = (center.change_requests || []).find(item => item.id === record.change_request_id);
  const environment = (center.environments || []).find(item => item.id === record.environment_id);
  const scenario = record.details?.scenario || {};
  const evidenceSummary = Number.isFinite(Number(scenario.pass_rate))
    ? `${request?.title || "精确评审证据"} · ${Math.round(Number(scenario.pass_rate) * 100)}% passed · 质量 ${Number(scenario.quality_score || 0).toFixed(1)}`
    : request?.title || "精确评审证据已归档";
  return {
    artifact: shortHash(request?.artifact_hash),
    environment: environment?.name || "环境记录不可用",
    evidence: evidenceSummary,
    receipt: record.receipt_id ? "Receipt 已保存" : "Receipt 待对账",
    hash: shortHash(record.after_hash || record.before_hash),
  };
}

export function releasePreviewCards(preview = {}) {
  return [
    {
      title: "Deployed Base",
      value: shortHash(preview.target_hash),
      detail: preview.target_drift ? "检测到外部 Drift" : "与跟踪 Hash 一致",
      tone: preview.target_drift ? "warning" : "ok",
    },
    {
      title: "Proposed Artifact",
      value: shortHash(preview.proposed_artifact?.artifact_hash),
      detail: `${preview.proposed_artifact?.plan_summary?.node_count ?? 0} nodes · ${preview.proposed_artifact?.plan_summary?.edge_count ?? 0} edges`,
      tone: "ready",
    },
    {
      title: "Scenario Evidence",
      value: `${Math.round(Number(preview.scenario_evidence?.pass_rate || 0) * 100)}% passed`,
      detail: `质量 ${Number(preview.scenario_evidence?.quality_score || 0).toFixed(1)} · Cleanup ${preview.scenario_evidence?.cleanup_verified ? "已验证" : "未完成"}`,
      tone: preview.scenario_evidence?.cleanup_verified ? "ok" : "warning",
    },
    {
      title: "Risk",
      value: String(preview.risk?.risk || "unknown").toUpperCase(),
      detail: `${preview.risk?.issues?.length || 0} 条 Guard 说明`,
      tone: preview.risk?.risk === "high" ? "warning" : "ready",
    },
  ];
}

export function mappingRows(preview = {}, mappingSet = null) {
  const existing = new Map(
    (mappingSet?.mappings || []).map(item => [item.logical_ref, item]),
  );
  return (preview.proposed_artifact?.resource_requirements || []).map(requirement => {
    const mapped = existing.get(requirement.logical_ref);
    return {
      kind: requirement.kind,
      logicalRef: requirement.logical_ref,
      label: requirement.label,
      targetRef: mapped?.target_ref || (requirement.kind === "credential_availability" ? "available" : ""),
      available: mapped?.available !== false,
    };
  });
}

export function authorizationPayload({ projectId, changeRequestId, environmentId, action }) {
  return {
    project_id: projectId,
    change_request_id: changeRequestId,
    environment_id: environmentId,
    action,
    confirmation: action === "publish" ? "PUBLISH" : "APPLY_DRAFT",
    expires_in_seconds: 600,
  };
}

export function shortHash(value) {
  const hash = String(value || "");
  return hash ? hash.slice(0, 12) : "尚无 Hash";
}

function bounded(value) {
  return String(value || "").trim().slice(0, 128);
}
