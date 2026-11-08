// Pure helpers, kept apart from main.js so they can be tested outside Herdr.
//
// `hx usage` prints one JSON snapshot. Every provider section carries a tag:
// "measured" | "estimated" | "unknown" (hx/usage.py). The bar shows one entry
// per provider with its 5h % — `~88%` when the section is tagged estimated,
// `?` when the reading is missing or the section is tagged unknown — then one
// entry per live team agent from `hx context decide <name>`.

// The first number inside a value: a bare number, or a known reading field.
export function num(v) {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (v && typeof v === "object") {
    for (const key of ["utilisation", "utilization", "used_percentage", "used_percent", "pct"]) {
      const n = num(v[key]);
      if (n != null) return n;
    }
  }
  return null;
}

// A reading as 0..100. `fraction` says the source is 0..1 (hx's "utilisation"
// fields); otherwise the number is already a percent (hx's "used_percent").
export function pctOf(v, fraction) {
  const n = num(v);
  if (n == null) return null;
  return Math.max(0, Math.min(100, fraction ? n * 100 : n));
}

// The 5-hour window object out of a `windows` map, whatever it is called.
function window5h(windows) {
  if (!windows || typeof windows !== "object") return null;
  if (windows["5h"]) return windows["5h"];
  const key = Object.keys(windows).find((k) => /^5\s*h/i.test(k));
  return key ? windows[key] : null;
}

// One bar entry: { label, value, pct, detail }. `pct` only drives the colour;
// `value` is exactly what shows after the label.
function entry(label, value, pct, detail) {
  return { label, value, pct, detail };
}

function pctEntry(label, v, fraction, estimated, detail) {
  const pct = pctOf(v, fraction);
  return entry(label, pct == null ? "?" : `${estimated ? "~" : ""}${Math.round(pct)}%`, pct, detail);
}

const DETAIL = {
  opencode: (s) => `OpenCode ${s?.plan ?? "shared"} pool, 5h window`,
  codex: () => "Codex primary rate-limit window",
  claude: () => "Claude 5h window",
};

function note(text, section) {
  return [text, section?.error].filter(Boolean).join(" · ") || text;
}

function devin(d) {
  const text = ["Devin", d.status].filter(Boolean).join(" ");
  const tip = note(text, { error: d.reason ?? d.last_error?.message ?? d.error });
  if (d.tag === "unknown" || d.status === "error") return entry("devin", "?", null, tip);
  const max = num(d.max_concurrent);
  const countable = d.status === "available" || (d.status === "busy" && Array.isArray(d.live_sessions));
  if (countable && max != null && max > 0) {
    const used = (num(d.active_runs) ?? 0) + (Array.isArray(d.live_sessions) ? d.live_sessions.length : 0);
    return entry("devin", `${used}/${max}`, (used / max) * 100, tip);
  }
  return entry("devin", d.status ?? "?", null, tip);
}

// Bar entries for the subscription pools in a `hx usage` snapshot.
export function pools(j) {
  const out = [];
  if (!j || typeof j !== "object") return out;

  const oc = j.opencode;
  if (oc && typeof oc === "object") {
    const win = window5h(oc.pool?.windows);
    const estimated = oc.tag === "estimated" || oc.pool?.tag === "estimated";
    out.push(pctEntry("opencode", win?.utilisation, true, estimated, note(DETAIL.opencode(oc), oc)));
  }

  const cx = j.codex;
  if (cx && typeof cx === "object") {
    const rl = cx.rate_limits?.primary;
    out.push(pctEntry("codex", rl?.used_percent ?? rl, false, cx.tag === "estimated", note(DETAIL.codex(), cx)));
  }

  const cl = j.claude;
  if (cl && typeof cl === "object") {
    const rl = cl.rate_limits?.five_hour ?? cl.rate_limits?.["5h"];
    out.push(pctEntry("claude", num(rl), false, cl.tag === "estimated", note(DETAIL.claude(), cl)));
  }

  const dv = j.devin;
  if (dv && typeof dv === "object") out.push(devin(dv));

  // A provider the snapshot adds later still gets an entry when it has a readable window.
  const known = new Set(["timestamp", "opencode", "codex", "claude", "devin", "agents"]);
  for (const [key, section] of Object.entries(j)) {
    if (known.has(key) || !section || typeof section !== "object") continue;
    const win = window5h(section.pool?.windows);
    const rl = section.rate_limits?.primary;
    const fraction = win != null;
    if (num(fraction ? win?.utilisation : (rl?.used_percent ?? rl)) == null) continue;
    out.push(pctEntry(key, fraction ? win?.utilisation : (rl?.used_percent ?? rl), fraction,
      section.tag === "estimated", note(`${key} 5h`, section)));
  }
  return out;
}

// Team agents to ask about, from the snapshot's agents section.
export function agentNames(j) {
  const list = j?.agents?.agents;
  if (!Array.isArray(list)) return [];
  return [...new Set(list.map((a) => a?.name).filter((n) => typeof n === "string" && n))];
}

// One agent's context entry from `hx context decide <name>` output, or from the
// error when the command failed.
export function contextEntry(name, j, error) {
  if (j && typeof j === "object") {
    const pct = pctOf(j.telemetry?.utilisation, true);
    const tip = [name, j.reason ?? j.action].filter(Boolean).join(" · ");
    return entry(name, pct == null ? "?" : `${Math.round(pct)}%`, pct, tip);
  }
  return entry(name, "?", null, [name, error ?? "no answer"].join(" · "));
}
