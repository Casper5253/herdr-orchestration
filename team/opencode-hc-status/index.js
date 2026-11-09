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
import { randomUUID } from "node:crypto";

export const APP = "opencode";
export const STATES = new Set(["idle", "working", "done", "blocked", "error"]);
export const KINDS = new Set(["permission", "question", "auth"]);
const TEAM_OVERLAY = /(^|[\\/])opencode-trusted-dev\.jsonc$/i;
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

export function bindingFor(terminal, sessionID, cwd, pid, startedAt) {
  return { terminal, session_id: sessionID, cwd: cwd || "", pid: Number(pid), started_at: startedAt };
}

export function bindingPath(stateDir, terminal) {
  return path.join(stateDir, "hc-bindings", `${terminal}.json`);
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

export function readBinding(file, { terminal, core, cwd, isAlive = (pid) => {
  try { process.kill(pid, 0); return true; } catch { return false; }
} }, io = fs) {
  const data = readJson(file, io);
  const claim = readJson(`${file}.owner`, io);
  return data?.terminal === terminal && data.core === core && data.cwd === cwd &&
    typeof data.owner === "string" && data.owner === claim?.owner &&
    data.pid === claim.pid && data.terminal === claim.terminal && data.core === claim.core &&
    data.cwd === claim.cwd && typeof data.session_id === "string" && Boolean(data.session_id) &&
    Number.isInteger(data.pid) && data.pid > 0 && isAlive(data.pid) ? data : null;
}

export function createPlugin(options = {}) {
  return async (input = {}) => {
    const env = options.env ?? process.env;
    if (!enabled(env)) return {};

    const terminal = env.HC_TERMINAL_ID;
    const stateDir = options.stateDir ?? env.HX_STATE_DIR ?? path.join(os.homedir(), ".herdr-team", "state");
    const cwd = input.directory || options.cwd || process.cwd();
    const pid = options.pid ?? process.pid;
    const now = options.now ?? (() => new Date());
    const stdout = options.stdout ?? process.stdout;
    const stderr = options.stderr ?? process.stderr;
    const stream = stdout?.isTTY ? stdout : (stderr?.isTTY ? stderr : null);
    const childSessions = new Map();
    const prompts = new Map();
    const io = options.fs ?? fs;
    const file = bindingPath(stateDir, terminal);
    const claimFile = `${file}.owner`;
    const owner = randomUUID();
    const core = JSON.stringify([env.HC_SESSION || "default", env.HC_SOCKET || ""]);
    const initial = JSON.stringify(readJson(claimFile, io));
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
          atomicWriteJson(claimFile, {...bindingFor(terminal, null, cwd, pid, startedAt), owner, core}, io);
          claimed = true;
        }
        atomicWriteJson(file, {...bindingFor(terminal, activeRoot, cwd, pid, startedAt), owner, core}, io);
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
