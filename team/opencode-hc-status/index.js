// OpenCode reports its state and session to the Herdr app (herdr-core).
//
// hc learns an agent's state only from the program's OSC 7501 report, and
// OpenCode writes none. This plugin writes the record to the terminal OpenCode
// runs in and binds the terminal to its OpenCode session.
//
// Installed as a global OpenCode plugin, but active only in a team OpenCode run
// inside an hc terminal: HC_TERMINAL_ID must be set and OPENCODE_CONFIG must
// name the team overlay. Outside hc, or outside the team, it returns no hooks.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";

export const APP = "opencode";
export const PROVIDER = "opencode";
export const BINDING_VERSION = 2;
export const STATES = new Set(["idle", "working", "done", "blocked", "error"]);
export const KINDS = new Set(["permission", "question", "auth"]);
const TEAM_OVERLAY = /(^|[\\/])opencode-trusted-dev\.jsonc$/i;
const TERMINAL = /^[A-Za-z0-9_-]+$/;
const MAX_ANCESTORS = 64;
const WORKING_STATUSES = new Set([
  "active", "busy", "pending", "retry", "running", "streaming", "working",
]);

let tmpSeq = 0;

// The exact record hc parses: ESC ] 7501 ; state=<word>:app=opencode ESC backslash.
export function record(state, kind) {
  if (!STATES.has(state)) return "";
  const detail = kind && KINDS.has(kind) ? `:kind=${kind}` : "";
  return `\x1b]7501;state=${state}:app=${APP}${detail}\x1b\\`;
}

export function statusForSessionStatus(status) {
  const raw = typeof status === "string" ? status : status?.type;
  const word = typeof raw === "string" ? raw.toLowerCase() : "";
  if (word === "idle") return "idle";
  return WORKING_STATUSES.has(word) ? "working" : null;
}

export function stateForEvent(type, properties = {}) {
  switch (type) {
    case "tool.execute.before":
    case "tool.execute.after":
    case "permission.replied":
    case "question.replied":
    case "question.rejected":
    case "session.compacted":
      return { state: "working" };
    case "permission.asked":
      return { state: "blocked", kind: "permission" };
    case "question.asked":
      return { state: "blocked", kind: "question" };
    case "session.error":
      return { state: "error" };
    case "session.idle":
      return { state: "idle" };
    case "session.status": {
      const state = statusForSessionStatus(properties.status);
      return state ? { state } : null;
    }
    default:
      return null;
  }
}

export function enabled(env = {}) {
  return Boolean(env.HC_TERMINAL_ID) && TEAM_OVERLAY.test(env.OPENCODE_CONFIG || "");
}

export function sessionIDFrom(properties = {}, type) {
  const direct = properties.sessionID;
  if (typeof direct === "string" && direct) return direct;
  const id = type === "session.created" || type === "session.updated"
    ? properties.info?.id : type === "message.updated" ? properties.info?.sessionID : null;
  return typeof id === "string" && id ? id : null;
}

export function bindingFor(terminal, sessionID, observed, pid, writtenMs) {
  return {
    v: BINDING_VERSION,
    terminal,
    terminal_created_ms: observed.terminal_created_ms,
    provider: PROVIDER,
    session_id: sessionID,
    provider_pid: Number(pid),
    written_ms: writtenMs,
  };
}

export function bindingPath(stateDir, terminal) {
  return path.join(stateDir, "hc-bindings", `${terminal}.json`);
}

export function defaultHcGet(terminal) {
  const out = execFileSync("hc", ["get", terminal, "--json"], {
    encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "ignore"],
  });
  const data = JSON.parse(out);
  return data && typeof data === "object" ? data : null;
}

export function defaultAncestors(pid) {
  if (process.platform === "win32") {
    const script = '$rows = Get-CimInstance Win32_Process | ForEach-Object { "$($_.ProcessId),$($_.ParentProcessId)" }; $rows -join ";"';
    let rows;
    try {
      rows = execFileSync("powershell", ["-NoProfile", "-NonInteractive", "-Command", script], {
        encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "ignore"],
      });
    } catch {
      return [];
    }
    const parents = new Map();
    for (const row of rows.trim().split(";")) {
      const [child, parent] = row.split(",").map((value) => Number.parseInt(value, 10));
      if (Number.isInteger(child) && child > 0) parents.set(child, Number.isInteger(parent) ? parent : 0);
    }
    return walkParents(parents, pid);
  }
  const parents = new Map();
  for (let current = pid, depth = 0; depth < MAX_ANCESTORS; depth++) {
    try {
      const stat = fs.readFileSync(`/proc/${current}/stat`, "utf8");
      const rest = stat.slice(stat.lastIndexOf(")") + 2).trim().split(/\s+/);
      parents.set(current, Number.parseInt(rest[1], 10));
    } catch {
      break;
    }
    const parent = parents.get(current);
    if (!Number.isInteger(parent) || parent <= 0) break;
    current = parent;
  }
  return walkParents(parents, pid);
}

function walkParents(parents, pid) {
  const chain = [];
  let current = pid;
  for (let depth = 0; depth < MAX_ANCESTORS; depth++) {
    const parent = parents.get(current);
    if (!Number.isInteger(parent) || parent <= 0 || parent === pid || chain.includes(parent)) break;
    chain.push(parent);
    current = parent;
  }
  return chain;
}

export function terminalInfo(terminal, { hcGet = defaultHcGet, ancestors = defaultAncestors, ownPid = process.pid } = {}) {
  if (typeof terminal !== "string" || !TERMINAL.test(terminal)) return null;
  let data;
  try {
    data = hcGet(terminal);
  } catch {
    return null;
  }
  if (!data || data.state !== "live") return null;
  if (data.address && data.address !== terminal) return null;
  if (!Number.isInteger(data.created_ms)) return null;
  if (!Number.isInteger(data.pid) || data.pid <= 0) return null;
  if (data.pid !== ownPid && !ancestors(ownPid).includes(data.pid)) return null;
  return { terminal_created_ms: data.created_ms };
}

// Write the terminal's binding through a sibling temp file, then rename it.
// The rename is the atomic step, so a reader never sees half a JSON document.
export function atomicWriteJson(file, data, io = fs) {
  io.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${Date.now()}.${tmpSeq++}.tmp`;
  try {
    io.writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n", "utf8");
    io.renameSync(tmp, file);
  } finally {
    try { io.unlinkSync(tmp); } catch {}
  }
  return file;
}

function readJson(file, io = fs) {
  try {
    return JSON.parse(io.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function writeRecord(stream, text) {
  if (!text || !stream || !stream.isTTY) return false;
  try {
    stream.write(text);
    return true;
  } catch {
    return false;
  }
}

function writeDiagnostic(stream, text) {
  if (!text || !stream || typeof stream.write !== "function") return false;
  try {
    stream.write(text + "\n");
    return true;
  } catch {
    return false;
  }
}

export function readBinding(file, { terminal, createdMs, provider = PROVIDER, isAlive = (pid) => {
  try { process.kill(pid, 0); return true; } catch { return false; }
} }, io = fs) {
  const data = readJson(file, io);
  return data?.v === BINDING_VERSION && data.terminal === terminal &&
    data.provider === provider && data.terminal_created_ms === createdMs &&
    typeof data.session_id === "string" && Boolean(data.session_id) &&
    Number.isInteger(data.provider_pid) && data.provider_pid > 0 && isAlive(data.provider_pid)
    ? data : null;
}

export function createPlugin(options = {}) {
  return async () => {
    const env = options.env ?? process.env;
    if (TEAM_OVERLAY.test(env.OPENCODE_CONFIG || "") && !env.HC_TERMINAL_ID) {
      writeDiagnostic(options.stderr ?? process.stderr, "hc-binding: refusing: HC_TERMINAL_ID is not set");
      return {};
    }
    if (!enabled(env)) return {};

    const terminal = env.HC_TERMINAL_ID;
    const stateDir = options.stateDir ?? env.HX_STATE_DIR ?? path.join(os.homedir(), ".herdr-team", "state");
    const pid = options.pid ?? process.pid;
    const now = options.now ?? (() => new Date());
    const stdout = options.stdout ?? process.stdout;
    const stderr = options.stderr ?? process.stderr;
    const stream = stdout?.isTTY ? stdout : (stderr?.isTTY ? stderr : null);
    const childSessions = new Map();
    const prompts = new Map();
    const io = options.fs ?? fs;
    const observe = options.terminalInfo ?? ((name) => terminalInfo(name, {
      hcGet: options.hcGet, ancestors: options.ancestors, ownPid: pid,
    }));
    const observed = observe(terminal);
    if (!observed) {
      writeDiagnostic(stderr, `hc-binding: refusing ${terminal}: terminal is not live or not this process's ancestor`);
      return {};
    }
    const file = bindingPath(stateDir, terminal);
    const claimFile = `${file}.owner`;
    const owner = randomUUID();
    const initial = JSON.stringify(readJson(claimFile, io));
    const writtenMs = options.writtenMs ?? (() => Date.now());
    let claimed = false;
    let activeRoot = null;
    let startedAt = now().toISOString();
    let rootStatus = {state: "idle"};

    const bind = () => {
      const existing = readJson(claimFile, io);
      if (claimed ? existing?.owner !== owner : JSON.stringify(existing) !== initial) return false;
      try {
        if (!claimed) {
          // Only startup replaces the ownership claim. A late binding rename cannot reclaim it.
          atomicWriteJson(claimFile, {terminal, owner, provider_pid: pid, started_at: startedAt}, io);
          claimed = true;
        }
        atomicWriteJson(file, bindingFor(terminal, activeRoot, observed, pid, writtenMs()), io);
        return true;
      } catch {
        // Status reporting must never disturb the agent; retry on a later event.
        return false;
      }
    };

    bind();
    const ownsTerminal = () => claimed && readJson(claimFile, io)?.owner === owner;

    const select = (sessionID) => {
      if (typeof sessionID !== "string" || !sessionID || childSessions.has(sessionID)) return false;
      if (activeRoot !== sessionID) {
        activeRoot = sessionID;
        startedAt = now().toISOString();
        prompts.clear();
      }
      bind();
      return ownsTerminal();
    };
    const belongs = (sessionID) => {
      const seen = new Set();
      while (sessionID && !seen.has(sessionID)) {
        if (sessionID === activeRoot) return true;
        seen.add(sessionID);
        sessionID = childSessions.get(sessionID);
      }
      return false;
    };
    const emit = () => {
      if (!ownsTerminal()) return;
      const prompt = prompts.values().next().value;
      const status = prompt ? {state: "blocked", kind: prompt.kind} : rootStatus;
      writeRecord(stream, record(status.state, status.kind));
    };

    return {
      event: async ({ event } = {}) => {
        const type = event?.type;
        const properties = event?.properties ?? {};
        const info = properties.info;
        if (type === "session.created" || type === "session.updated") {
          if (info?.id && info.parentID) childSessions.set(info.id, info.parentID);
          if (!activeRoot && info?.id && !info.parentID) select(info.id);
        }
        const sessionID = sessionIDFrom(properties, type);
        if (!activeRoot || !belongs(sessionID)) return;
        bind();
        if (!ownsTerminal()) return;
        const mapped = stateForEvent(type, properties);
        if (!mapped) return;
        const match = /^(permission|question)\.(asked|replied|rejected)$/.exec(type);
        if (match) {
          const [, kind, action] = match;
          const key = JSON.stringify([sessionID, kind, properties.requestID ?? properties.id ?? ""]);
          if (action === "asked") prompts.set(key, {kind});
          else prompts.delete(key);
        } else if (sessionID !== activeRoot) return;
        if (sessionID === activeRoot) rootStatus = mapped;
        emit();
      },
      "chat.message": async ({ sessionID } = {}) => {
        if (!select(sessionID)) return;
        rootStatus = {state: "working"};
        emit();
      },
      "experimental.chat.system.transform": async (input = {}) => {
        if (!activeRoot) select(input.sessionID);
      },
    };
  };
}

export const OpenCodeHcStatusPlugin = createPlugin();

export default {
  id: "herdr.hc-status",
  server: OpenCodeHcStatusPlugin,
  setup() {},
};
