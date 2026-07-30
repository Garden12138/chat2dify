import assert from "node:assert/strict";
import test from "node:test";

import {
  STUDIO_NAVIGATION,
  appModeLabel,
  classifyStudioError,
  homeQuery,
  isContextNonce,
  isStudioHomeEnabled,
  relativeTime,
  runPhaseLabel,
  safeBuildUrl,
} from "../../app/static/studio/home/core.mjs";

test("v5 Home replaces the default surface only while enabled", () => {
  assert.equal(isStudioHomeEnabled({ studioV5Enabled: true }, "?embed=1"), true);
  assert.equal(
    isStudioHomeEnabled({ studioV5Enabled: true }, "?studio=build&intent=modify"),
    false,
  );
  assert.equal(isStudioHomeEnabled({ studioV5Enabled: false }, "?embed=1"), false);
});

test("Studio navigation exposes the complete product map truthfully", () => {
  assert.deepEqual(
    STUDIO_NAVIGATION.map(item => item.id),
    ["home", "build", "blueprints", "scenarios", "releases", "runs"],
  );
  assert.equal(STUDIO_NAVIGATION.find(item => item.id === "home").available, true);
  assert.equal(STUDIO_NAVIGATION.find(item => item.id === "blueprints").available, false);
});

test("Home query carries filters but never user or role claims", () => {
  const query = homeQuery({
    projectId: "project-1",
    search: " support ",
    appMode: "workflow",
    user: "admin",
    role: "owner",
  });
  assert.equal(
    query,
    "/api/v5/studio/home?project_id=project-1&search=support&app_mode=workflow",
  );
  assert.equal(query.includes("user"), false);
  assert.equal(query.includes("role"), false);
});

test("permission offline disabled and generic failures remain distinct", () => {
  assert.deepEqual(
    classifyStudioError(403, {
      error: { code: "STUDIO_PROJECT_ACCESS_DENIED", message: "Denied." },
    }),
    {
      kind: "permission",
      code: "STUDIO_PROJECT_ACCESS_DENIED",
      title: "无法访问这个项目",
      message: "Denied.",
      action: "none",
    },
  );
  assert.equal(classifyStudioError(503, {}).kind, "offline");
  assert.equal(
    classifyStudioError(404, {
      error: { code: "AI_STUDIO_V5_DISABLED" },
    }).kind,
    "disabled",
  );
  assert.equal(classifyStudioError(500, {}).kind, "error");
});

test("labels, time, and build URLs stay business-readable and safe", () => {
  assert.equal(appModeLabel("advanced-chat"), "Chatflow");
  assert.equal(runPhaseLabel("interrupted"), "可恢复中断");
  assert.equal(
    relativeTime("2026-07-30T10:00:00Z", Date.parse("2026-07-30T10:05:00Z")),
    "5 分钟前",
  );
  assert.equal(
    safeBuildUrl(
      "/chat2dify/?studio=build",
      "/chat2dify",
      "context-nonce-1234567890",
    ),
    "/chat2dify/?studio=build&context_nonce=context-nonce-1234567890",
  );
  assert.equal(safeBuildUrl("javascript:alert(1)", "/chat2dify"), "/chat2dify/");
  assert.equal(safeBuildUrl("//evil.example/build", "/chat2dify"), "/chat2dify/");
  assert.equal(safeBuildUrl("/other/build", "/chat2dify"), "/chat2dify/");
  assert.equal(isContextNonce("context-nonce-1234567890"), true);
  assert.equal(isContextNonce("short"), false);
});
