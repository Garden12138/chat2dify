import assert from "node:assert/strict";
import test from "node:test";

import {
  authorizationPayload,
  isReleaseCenterEnabled,
  mappingRows,
  releaseCenterTone,
  releaseIdentity,
  releasePermissions,
  releaseHistoryEvidence,
  releasePresentation,
  releasePreviewCards,
  reviewPolicyDefaults,
  reviewPresentation,
  shortHash,
  validateReviewAssignment,
} from "../../app/static/studio/releases/core.mjs";

test("Release Center is isolated behind the v5 product route", () => {
  assert.equal(isReleaseCenterEnabled({ studioV5Enabled: true }, "?studio=releases"), true);
  assert.equal(isReleaseCenterEnabled({ studioV5Enabled: true }, "?studio=scenarios"), false);
  assert.equal(isReleaseCenterEnabled({ studioV5Enabled: false }, "?studio=releases"), false);
});

test("Scenario handoff carries bounded record context and no authority", () => {
  const identity = releaseIdentity(
    "?studio=releases&build_id=build-1&candidate_id=candidate-1&scenario_run_id=run-1&role=owner&publish=true&raw_graph=secret",
  );
  assert.deepEqual(identity, {
    buildId: "build-1",
    candidateId: "candidate-1",
    scenarioRunId: "run-1",
    changeRequestId: "",
    repairProposalId: "",
    repairProposalVersion: 0,
    embedded: false,
  });
  assert.equal("role" in identity, false);
  assert.equal("publish" in identity, false);
  assert.equal("raw_graph" in identity, false);
});

test("review status is business-readable without relying on color", () => {
  assert.deepEqual(reviewPresentation("changes_requested"), {
    label: "需要修改",
    tone: "warning",
  });
  assert.equal(reviewPresentation("approved").label, "已批准精确版本");
  assert.equal(reviewPresentation("superseded").label, "已被修正版替代");
});

test("Release Center distinguishes ready, partial, permission, and offline states", () => {
  assert.equal(releaseCenterTone("ready"), "ok");
  assert.equal(releaseCenterTone("empty"), "warning");
  assert.equal(releaseCenterTone("partial_error"), "warning");
  assert.equal(releaseCenterTone("permission_denied"), "danger");
  assert.equal(releaseCenterTone("offline"), "danger");
});

test("single-owner projects keep reviewer separation optional", () => {
  const owner = { principal_key: "owner", role: "owner" };
  assert.deepEqual(reviewPolicyDefaults([owner], "owner"), {
    requireSeparation: false,
  });
  assert.deepEqual(
    validateReviewAssignment({ assigneeKey: "", requireSeparation: false }),
    { ok: true, message: "" },
  );
  assert.equal(
    validateReviewAssignment({ assigneeKey: "", requireSeparation: true }).ok,
    false,
  );
  assert.equal(
    validateReviewAssignment({
      assigneeKey: "owner",
      principalKey: "owner",
      requireSeparation: true,
    }).ok,
    false,
  );
  assert.equal(
    reviewPolicyDefaults(
      [owner, { principal_key: "reviewer", role: "reviewer" }],
      "owner",
    ).requireSeparation,
    true,
  );
});

test("release controls reflect server roles instead of offering forbidden writes", () => {
  assert.deepEqual(releasePermissions("viewer"), {
    canAuthor: false,
    canConfigureRelease: false,
    canRollback: false,
  });
  assert.equal(releasePermissions("reviewer").canConfigureRelease, false);
  assert.equal(releasePermissions("builder").canAuthor, true);
  assert.equal(releasePermissions("builder").canRollback, true);
  assert.equal(releasePermissions("builder").canConfigureRelease, false);
  assert.equal(releasePermissions("admin").canConfigureRelease, true);
  assert.equal(releasePermissions("owner").canConfigureRelease, true);
});

test("Release Preview reports drift evidence and risk truthfully", () => {
  const cards = releasePreviewCards({
    target_hash: "a".repeat(64),
    target_drift: true,
    proposed_artifact: {
      artifact_hash: "b".repeat(64),
      plan_summary: { node_count: 4, edge_count: 3 },
    },
    scenario_evidence: {
      pass_rate: 1,
      quality_score: 94,
      cleanup_verified: true,
    },
    risk: { risk: "high", issues: [{ code: "START_CHANGED" }] },
  });
  assert.equal(cards[0].detail, "检测到外部 Drift");
  assert.equal(cards[0].tone, "warning");
  assert.equal(cards[1].detail, "4 nodes · 3 edges");
  assert.match(cards[2].detail, /Cleanup 已验证/);
  assert.equal(cards[3].value, "HIGH");
});

test("opaque mappings include credential availability but never credential values", () => {
  const preview = {
    proposed_artifact: {
      resource_requirements: [
        { kind: "model", logical_ref: "model:abc", label: "Answer 模型" },
        {
          kind: "credential_availability",
          logical_ref: "credential_availability:abc",
          label: "Answer 模型凭据可用性",
        },
      ],
    },
  };
  const rows = mappingRows(preview, {
    mappings: [{
      kind: "model",
      logical_ref: "model:abc",
      target_ref: "provider::model",
      available: true,
    }],
  });
  assert.equal(rows[0].targetRef, "provider::model");
  assert.equal(rows[1].targetRef, "available");
  assert.equal(JSON.stringify(rows).includes("credential_value"), false);
  assert.equal(JSON.stringify(rows).includes("api_key"), false);
});

test("Apply Draft and Publish create distinct authorization payloads", () => {
  const base = {
    projectId: "project-1",
    changeRequestId: "review-1",
    environmentId: "staging-1",
  };
  const apply = authorizationPayload({ ...base, action: "apply_draft" });
  const publish = authorizationPayload({ ...base, action: "publish" });
  assert.equal(apply.confirmation, "APPLY_DRAFT");
  assert.equal(publish.confirmation, "PUBLISH");
  assert.notDeepEqual(apply, publish);
  assert.equal("user" in apply, false);
  assert.equal("role" in apply, false);
});

test("ambiguous release outcome never looks successful or retryable", () => {
  const view = releasePresentation({ action: "publish", outcome: "ambiguous" });
  assert.equal(view.action, "Publish");
  assert.equal(view.tone, "warning");
  assert.match(view.outcome, /禁止自动重试/);
  assert.equal(shortHash("a".repeat(64)), "aaaaaaaaaaaa");
});

test("Release History exposes business evidence without copied record IDs", () => {
  const evidence = releaseHistoryEvidence(
    {
      change_request_id: "review-internal-id",
      environment_id: "environment-internal-id",
      receipt_id: "receipt-internal-id",
      before_hash: "a".repeat(64),
      details: { scenario: { pass_rate: 1, quality_score: 96.5 } },
    },
    {
      change_requests: [{
        id: "review-internal-id",
        title: "售后兜底评审",
        artifact_hash: "b".repeat(64),
      }],
      environments: [{ id: "environment-internal-id", name: "Staging" }],
    },
  );
  assert.deepEqual(evidence, {
    artifact: "bbbbbbbbbbbb",
    environment: "Staging",
    evidence: "售后兜底评审 · 100% passed · 质量 96.5",
    receipt: "Receipt 已保存",
    hash: "aaaaaaaaaaaa",
  });
  assert.equal(JSON.stringify(evidence).includes("internal-id"), false);
});
