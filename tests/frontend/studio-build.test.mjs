import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCommandPayload,
  buildCreatePayload,
  buildIdentity,
  candidateStatus,
  canSelectCandidate,
  comparisonRows,
  contextCommandsEnabled,
  isBuildStudioEnabled,
  layoutPresentation,
  selectedCandidate,
} from "../../app/static/studio/build/core.mjs";

test("Build Studio is isolated behind the v5 build route", () => {
  assert.equal(isBuildStudioEnabled({ studioV5Enabled: true }, "?studio=build"), true);
  assert.equal(isBuildStudioEnabled({ studioV5Enabled: false }, "?studio=build"), false);
  assert.equal(isBuildStudioEnabled({ studioV5Enabled: true }, "?studio=home"), false);
});

test("create and modify identities remain honest about their source", () => {
  const created = buildIdentity("?studio=build&intent=create&app_mode=agent-chat");
  assert.deepEqual(buildCreatePayload(created, "project-1"), {
    project_id: "project-1",
    operation: "create",
    entry_source: "create",
    app_id: undefined,
    app_mode: "agent-chat",
    app_name: "New Dify Agent",
  });

  const modified = buildIdentity(
    "?studio=build&intent=modify&studio_entry=home&app_id=app-1&app_mode=workflow&app_name=Support",
  );
  assert.equal(modified.entrySource, "home");
  assert.equal(modified.appId, "app-1");
  assert.equal(buildCreatePayload(modified, "project-1").app_name, "Support");
});

test("candidate commands expose only bounded fields", () => {
  const canvas = {
    selected_node_ids: ["classify"],
    selected_edge_ids: [],
    viewport: { x: 0, y: 0, zoom: 1 },
    dirty_state: false,
    canvas_draft_hash: "hash-1",
    revision: 4,
    raw_graph: { injected: true },
    role: "owner",
  };
  const alternatives = buildCommandPayload({
    projectId: "project-1",
    modeValue: "alternatives-3",
    message: "  three options  ",
    canvasContext: canvas,
  });
  assert.equal(alternatives.mode, "alternatives");
  assert.equal(alternatives.candidate_count, 3);
  assert.equal(alternatives.message, "three options");
  assert.equal("raw_graph" in alternatives.canvas_context, false);
  assert.equal("role" in alternatives.canvas_context, false);

  const synthesis = buildCommandPayload({
    projectId: "project-1",
    modeValue: "synthesize",
    message: "combine",
    sourceCandidateIds: ["a", "b"],
  });
  assert.deepEqual(synthesis.source_candidate_ids, ["a", "b"]);
});

test("selection, comparison, and contextual actions follow authoritative state", () => {
  const valid = {
    candidate: { id: "valid", label: "Safe", status: "valid" },
    reconstructable: true,
    validation: { ok: true },
    technical_detail: { domain: "graph" },
  };
  const waiting = {
    candidate: { id: "waiting", label: "Ask", status: "waiting_input" },
    reconstructable: false,
    validation: {},
    technical_detail: { domain: "graph" },
  };
  const view = {
    build: { selected_candidate_id: "valid" },
    candidates: [waiting, valid],
    comparison: {
      business_behavior: { valid: "safe", waiting: "needs input" },
    },
  };
  assert.equal(selectedCandidate(view).candidate.id, "valid");
  assert.equal(selectedCandidate(view, "waiting").candidate.id, "waiting");
  assert.equal(canSelectCandidate(valid), true);
  assert.equal(canSelectCandidate(waiting), false);
  assert.equal(comparisonRows(view).length, 7);
  assert.equal(candidateStatus("interrupted"), "可恢复中断");
  assert.deepEqual(contextCommandsEnabled(valid, []), {
    explain_selection: false,
    explain_variable_flow: false,
    safer_fallback: false,
    suggest_resources: true,
    generate_scenarios: true,
  });
  assert.equal(contextCommandsEnabled(valid, ["classify"]).safer_fallback, true);
});

test("layout preview preserves relative positions without mutating source data", () => {
  const source = {
    nodes: [
      { id: "start", x: -200, y: -50, preserved: true },
      { id: "new", x: 160, y: 70, changed: true },
    ],
  };
  const rendered = layoutPresentation(source);
  assert.deepEqual(rendered.nodes.map(node => [node.id, node.x, node.y]), [
    ["start", 30, 30],
    ["new", 390, 150],
  ]);
  assert.equal(source.nodes[0].x, -200);
  assert.equal(rendered.width, 580);
});
