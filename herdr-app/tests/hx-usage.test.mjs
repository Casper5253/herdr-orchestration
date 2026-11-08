import { test } from "node:test";
import assert from "node:assert/strict";
import { pools, agentNames, contextEntry, num, pctOf } from "../mods/hx-usage/lib.js";

const SNAP = {
  timestamp: "2026-11-08T12:00:00+00:00",
  opencode: {
    tag: "estimated",
    plan: "go",
    pool: {
      tag: "estimated",
      windows: {
        "5h": { spend: 20.5, cap: 50, remaining: 29.5, utilisation: 0.41 },
        "7d": { spend: 60, cap: 300, remaining: 240, utilisation: 0.2 },
        "30d": { spend: 100, cap: 1200, remaining: 1100, utilisation: 0.08 },
      },
    },
  },
  codex: {
    tag: "measured",
    rate_limits: {
      primary: { used_percent: 37.5, window_minutes: 300 },
      secondary: { used_percent: 12 },
    },
  },
  claude: {
    tag: "measured",
    context_window: { used_percentage: 22 },
    rate_limits: { five_hour: { utilization: 88, resets_at: "2026-11-08T17:00:00Z" } },
  },
  devin: {
    provider: "devin",
    status: "available",
    reason: "free SWE-2 promotion active",
    tag: "measured",
    active_runs: 1,
    live_sessions: [{ name: "dev-devin" }],
    max_concurrent: 3,
  },
  agents: {
    tag: "measured",
    prefix: "dev-",
    agents: [{ name: "dev-claude" }, { name: "dev-codex", status: "working" }, { name: "dev-devin" }],
  },
};

test("num digs a reading out of numbers and objects", () => {
  assert.equal(num(7), 7);
  assert.equal(num({ utilisation: 0.5 }), 0.5);
  assert.equal(num({ used_percent: 42 }), 42);
  assert.equal(num({ utilization: 88 }), 88);
  assert.equal(num(null), null);
  assert.equal(num({ nope: 1 }), null);
});

test("pctOf scales fractions and clamps percents", () => {
  assert.equal(pctOf(0.41, true), 41);
  assert.equal(pctOf(37.5, false), 37.5);
  assert.equal(pctOf(1.5, false), 1.5);
  assert.equal(pctOf(150, false), 100);
  assert.equal(pctOf(-2, true), 0);
  assert.equal(pctOf(null, true), null);
});

test("pools reads the four provider sections", () => {
  const rows = pools(SNAP);
  assert.deepEqual(rows.map((r) => [r.label, r.value]), [
    ["opencode", "~41%"],
    ["codex", "38%"],
    ["claude", "88%"],
    ["devin", "2/3"],
  ]);
  assert.equal(rows[0].pct, 41);
  assert.equal(rows[3].pct.toFixed(1), "66.7");
});

test("pools tags estimated sections with ~", () => {
  const rows = pools(SNAP);
  assert.equal(rows[0].value.startsWith("~"), true, "opencode is tagged estimated");
  assert.equal(rows[1].value.startsWith("~"), false);
  assert.equal(rows[2].value.startsWith("~"), false);
});

test("unknown sections show ? and keep the error in the tooltip", () => {
  const snap = {
    ...SNAP,
    opencode: { tag: "unknown", pool: { tag: "unknown", windows: { "5h": { utilisation: null } } }, error: "opencode.db not found" },
    codex: { tag: "unknown", rate_limits: null, error: "No Codex rollout JSONL found" },
    claude: { tag: "unknown", rate_limits: null, error: "Claude status file not found" },
  };
  const rows = pools(snap);
  assert.deepEqual(rows.map((r) => r.value), ["?", "?", "?", "2/3"]);
  assert.match(rows[0].detail, /opencode\.db not found/);
});

test("devin shows its status word when slots do not apply", () => {
  const disabled = { ...SNAP.devin, status: "disabled", reason: "policy.devin.enabled is false", active_runs: 0, live_sessions: [], max_concurrent: 1 };
  const [row] = pools({ devin: disabled });
  assert.equal(row.value, "disabled");
  assert.equal(row.pct, null);
  assert.match(row.detail, /enabled is false/);
  const errored = { ...SNAP.devin, status: "error", tag: "unknown", reason: "policy.devin unreadable" };
  assert.equal(pools({ devin: errored })[0].value, "?");
  const full = { ...SNAP.devin, status: "busy", active_runs: 2, live_sessions: [{ name: "dev-devin" }] };
  const [busy] = pools({ devin: full });
  assert.equal(busy.value, "3/3");
  assert.equal(busy.pct, 100);
});

test("a provider added later still gets an entry", () => {
  const snap = { grok: { tag: "measured", rate_limits: { primary: { used_percent: 55 } } } };
  const rows = pools(snap);
  assert.equal(rows.length, 1);
  assert.deepEqual([rows[0].label, rows[0].value], ["grok", "55%"]);
});

test("agentNames reads the team list", () => {
  assert.deepEqual(agentNames(SNAP), ["dev-claude", "dev-codex", "dev-devin"]);
  assert.deepEqual(agentNames({ agents: { tag: "unknown", agents: [] } }), []);
  assert.deepEqual(agentNames({}), []);
});

test("contextEntry reads telemetry.utilisation as a fraction", () => {
  const e = contextEntry("dev-claude", { action: "hold", telemetry: { utilisation: 0.34 }, reason: "below Claude lead threshold" });
  assert.equal(e.value, "34%");
  assert.equal(e.pct, 34);
  assert.match(e.detail, /below Claude lead threshold/);
});

test("contextEntry shows ? on missing telemetry or a failed call", () => {
  assert.equal(contextEntry("dev-claude", { action: "wait", telemetry: { utilisation: null } }).value, "?");
  const failed = contextEntry("dev-claude", null, "hx: agent is outside the configured team");
  assert.equal(failed.value, "?");
  assert.match(failed.detail, /outside the configured team/);
});
