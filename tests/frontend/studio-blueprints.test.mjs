import assert from "node:assert/strict";
import test from "node:test";

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
} from "../../app/static/studio/blueprints/core.mjs";

test("Blueprint Gallery is isolated behind its v5 product route", () => {
  assert.equal(isBlueprintGalleryEnabled({ studioV5Enabled: true }, "?studio=blueprints"), true);
  assert.equal(isBlueprintGalleryEnabled({ studioV5Enabled: false }, "?studio=blueprints"), false);
  assert.equal(isBlueprintGalleryEnabled({ studioV5Enabled: true }, "?studio=build"), false);
});

test("Gallery identity keeps only bounded product context", () => {
  const ids = Array.from({ length: 48 }, (_, index) => `node-${index}`).join(",");
  const identity = blueprintIdentity(
    `?studio=blueprints&build_id=build-1&candidate_id=candidate-1&node_ids=${ids}&app_mode=workflow&role=owner&raw_graph=secret`,
  );
  assert.equal(identity.buildId, "build-1");
  assert.equal(identity.candidateId, "candidate-1");
  assert.equal(identity.selectedNodeIds.length, 40);
  assert.equal(identity.appMode, "workflow");
  assert.equal("role" in identity, false);
  assert.equal("raw_graph" in identity, false);
});

test("discovery queries preserve filters without inventing authorization", () => {
  const query = new URL(galleryQuery({
    projectId: "project-1",
    buildId: "build-1",
    search: " knowledge fallback ",
    category: "Knowledge & Grounding",
    appMode: "workflow",
    difyVersion: " 1.14.2 ",
    risk: "medium",
    visibility: "team",
    resourceAvailable: true,
    compatibleOnly: true,
  }), "https://studio.example");
  assert.equal(query.pathname, "/api/v5/studio/blueprints");
  assert.equal(query.searchParams.get("search"), "knowledge fallback");
  assert.equal(query.searchParams.get("compatible_only"), "true");
  assert.equal(query.searchParams.get("visibility"), "team");
  assert.equal(query.searchParams.get("dify_version"), "1.14.2");
  assert.equal(query.searchParams.get("resource_available"), "true");
  assert.equal(query.searchParams.has("role"), false);
  assert.match(detailQuery({ projectId: "project-1", buildId: "build-1", blueprintId: "a/b", version: "1.0.0" }), /a%2Fb/);
});

test("guided setup emits only declared typed fields and normalizes values", () => {
  const payload = setupPayload({
    projectId: "project-1",
    buildId: "build-1",
    version: "1.0.0",
    fields: [
      { id: "datasets", kind: "dataset", multiple: true, default: [] },
      { id: "strict", kind: "policy", default: true },
      { id: "retries", kind: "policy", default: 2 },
      { id: "prompt", kind: "prompt", default: "safe" },
    ],
    formValues: {
      datasets: "a, b, ",
      strict: "false",
      retries: "3",
      prompt: " reviewed prompt ",
      permission: "admin",
      raw_dsl: { injected: true },
    },
  });
  assert.deepEqual(payload.values, [
    { field_id: "datasets", kind: "dataset", value: ["a", "b"] },
    { field_id: "strict", kind: "policy", value: false },
    { field_id: "retries", kind: "policy", value: 3 },
    { field_id: "prompt", kind: "prompt", value: "reviewed prompt" },
  ]);
  assert.equal(JSON.stringify(payload).includes("permission"), false);
  assert.equal(JSON.stringify(payload).includes("raw_dsl"), false);
});

test("availability and previews communicate compatibility without mutating input", () => {
  assert.deepEqual(availabilityPresentation({ applicable: true }), {
    tone: "ok",
    label: "可用于当前 Build",
    reasons: [],
  });
  assert.deepEqual(
    availabilityPresentation({ compatible: true, applicable: false, reasons: [{ message: "缺少 Dataset" }] }),
    { tone: "warning", label: "兼容，但需要补齐资源", reasons: ["缺少 Dataset"] },
  );
  assert.deepEqual(
    availabilityPresentation({
      compatible: true,
      applicable: false,
      reasons: [{ code: "BLUEPRINT_VERSION_PENDING_REVIEW", message: "等待独立评审" }],
    }),
    { tone: "warning", label: "等待版本评审", reasons: ["等待独立评审"] },
  );
  const preview = {
    nodes: [
      { ref: "start", label: "Start" },
      { ref: "lookup", label: "Lookup" },
      { ref: "end", label: "End" },
    ],
    edges: [
      { source: "start", target: "lookup" },
      { source: "lookup", target: "end" },
    ],
  };
  assert.deepEqual(previewLayout(preview).map(node => node.column), [0, 1, 2]);
  assert.equal("column" in preview.nodes[0], false);
});

test("authoritative apply readback keeps zero-write and explicit return semantics", () => {
  const result = applyResultPresentation({
    application: { id: "application-1", candidate_id: "candidate-1" },
    build: {
      candidates: [{ candidate: { id: "candidate-1", label: "Knowledge Candidate", status: "valid" }, reconstructable: true }],
    },
    patch_operation_count: 7,
    source_head_unchanged: true,
    dify_write_count: 0,
  });
  assert.deepEqual(result, {
    ok: true,
    candidateId: "candidate-1",
    applicationId: "application-1",
    label: "Knowledge Candidate",
    operationCount: 7,
    sourceUnchanged: true,
    difyWriteCount: 0,
  });
  assert.equal(safeBuildReturnUrl("/chat2dify/", "build-1"), "/chat2dify/?studio=build&build_id=build-1");
  assert.equal(safeBuildReturnUrl("", ""), "/?studio=build&intent=create&app_mode=workflow");
});
