import assert from "node:assert/strict";
import test from "node:test";

import {
  automationPresentation,
  correlationPresentation,
  errorLabel,
  formatCost,
  formatDuration,
  isRunCenterEnabled,
  repairBuildUrl,
  repairStatusLabel,
  scopedTokenPresentation,
  runCenterQuery,
  runCenterTone,
  runIdentity,
  runMetrics,
  runStatusPresentation,
  trendRows,
} from "../../app/static/studio/runs/core.mjs";

test("Run Center is isolated behind the v5 product route", () => {
  assert.equal(isRunCenterEnabled({ studioV5Enabled: true }, "?studio=runs"), true);
  assert.equal(isRunCenterEnabled({ studioV5Enabled: true }, "?studio=releases"), false);
  assert.equal(isRunCenterEnabled({ studioV5Enabled: false }, "?studio=runs"), false);
});

test("incident handoff is bounded and carries no authority claims", () => {
  assert.deepEqual(runIdentity("?studio=runs&incident_id=incident-1&role=owner&publish=true"), {
    incidentId: "incident-1",
    embedded: false,
  });
});

test("Run Center query exposes only supported filters", () => {
  const query = runCenterQuery("project-1", {
    logicalAppId: "app-1",
    environmentId: "env-1",
    artifactId: "artifact-1",
    status: "failed",
    errorCode: "EXECUTION_HTTP_FAILED",
    startedFrom: "2026-08-08T08:00:00Z",
    role: "owner",
  });
  assert.equal(query, "/api/v5/studio/run-center?project_id=project-1&logical_app_id=app-1&environment_id=env-1&artifact_id=artifact-1&status=failed&error_code=EXECUTION_HTTP_FAILED&started_from=2026-08-08T08%3A00%3A00.000Z");
  assert.equal(query.includes("role"), false);
});

test("status and correlation remain readable without color", () => {
  assert.equal(runStatusPresentation("partial_succeeded").label, "部分成功");
  assert.equal(correlationPresentation("exact").label, "精确关联 Artifact");
  assert.match(correlationPresentation("ambiguous").label, /需对账/);
  assert.equal(errorLabel("EXECUTION_TIMEOUT"), "执行超时");
  assert.equal(runCenterTone("partial_error"), "warning");
});

test("metrics distinguish missing evidence from zero", () => {
  const empty = runMetrics([], []);
  assert.equal(empty[0].value, "证据不足");
  assert.equal(empty[1].value, "证据不足");
  assert.equal(empty[2].value, "证据不足");
  const metrics = runMetrics([
    { status: "succeeded", latency_ms: 1000, estimated_cost_microusd: 1000, correlation_state: "exact" },
    { status: "failed", latency_ms: 3000, estimated_cost_microusd: 3000, correlation_state: "uncorrelated" },
  ], [{ status: "open" }]);
  assert.equal(metrics[0].value, "50%");
  assert.equal(metrics[1].value, "2.0 s");
  assert.equal(metrics[2].value, "$0.0040");
  assert.equal(metrics[3].value, "1/2");
  assert.equal(metrics[4].value, "1");
});

test("trend widths and units are deterministic", () => {
  assert.deepEqual(trendRows([{ bucket: "2026-08-08", succeeded: 3, failed: 1, other: 0 }]), [{
    bucket: "2026-08-08",
    succeeded: 3,
    failed: 1,
    other: 0,
    label: "2026-08-08：成功 3，失败 1，其他 0",
    successWidth: 75,
    failedWidth: 25,
    otherWidth: 0,
  }]);
  assert.equal(formatDuration(250), "250 ms");
  assert.equal(formatDuration(undefined), "证据不足");
  assert.equal(formatCost(undefined), "证据不足");
});

test("repair opens only the normal Build surface", () => {
  const url = repairBuildUrl({ id: "repair-1", build_id: "build-1", status: "draft_build", version: 1, publish: true });
  assert.equal(url, "?studio=build&intent=modify&build_id=build-1&repair_proposal_id=repair-1&repair_proposal_version=1");
  assert.equal(url.includes("publish"), false);
  assert.equal(repairStatusLabel("in_review"), "评审中");
});

test("automation never presents missing adapters as delivered", () => {
  assert.deepEqual(automationPresentation({
    adapter_state: "missing",
    pending_notifications: 2,
    dead_letters: 1,
  }), {
    label: "Adapter 缺失，Outbox 待对账",
    tone: "warning",
    summary: "Adapter 缺失，Outbox 待对账 · 2 条待发送 · 1 条需对账",
  });
  assert.equal(automationPresentation({ adapter_state: "configured" }).tone, "ok");
});

test("scoped token status is explicit and never implies release authority", () => {
  const view = scopedTokenPresentation({
    scopes: ["search:read", "review:read"],
    expires_at: "2026-09-01T00:00:00Z",
  }, Date.parse("2026-08-08T00:00:00Z"));
  assert.equal(view.status, "active");
  assert.equal(view.scopeSummary, "search:read · review:read");
  assert.equal(view.scopeSummary.includes("publish"), false);
  assert.equal(scopedTokenPresentation({ revoked_at: "2026-08-08T00:00:00Z" }).label, "已撤销");
});
