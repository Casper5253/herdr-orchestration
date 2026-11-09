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

export function sessionIDFrom(properties = {}) {
  const direct = properties.sessionID;
  if (typeof direct === "string" && direct) return direct;
  const id = properties.info?.id;
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
  io.writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n", "utf8");
  io.renameSync(tmp, file);
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
    let boundSession = null;

    const bind = (sessionID) => {
      if (!sessionID || childSessions.has(sessionID)) return false;
      const file = bindingPath(stateDir, terminal);
      const existing = readJson(file, options.fs);
      const startedAt = existing?.session_id === sessionID && existing.started_at
        ? existing.started_at
        : now().toISOString();
      try {
        atomicWriteJson(file, bindingFor(terminal, sessionID, cwd, pid, startedAt),
          options.fs ?? fs);
        return true;
      } catch {
        // Status reporting must never disturb the agent; retry on a later event.
        return false;
      }
    };

    const bindOnce = (sessionID) => {
      if (!sessionID || childSessions.has(sessionID) || boundSession === sessionID) return;
      if (bind(sessionID)) boundSession = sessionID;
    };

    const emit = (state, kind) => writeRecord(stream, record(state, kind));

    return {
      event: async ({ event } = {}) => {
        const type = event?.type;
        const properties = event?.properties ?? {};
        const info = properties.info;
        if (info?.id && info.parentID) childSessions.set(info.id, info.parentID);
        const sessionID = sessionIDFrom(properties);
        if (sessionID && childSessions.has(sessionID)) {
          const mapped = stateForEvent(type, properties);
          if (mapped) emit(mapped.state, mapped.kind);
          return;
        }
        bindOnce(sessionID);
        const mapped = stateForEvent(type, properties);
        if (mapped) emit(mapped.state, mapped.kind);
      },
      "chat.message": async ({ sessionID } = {}) => {
        bindOnce(sessionID);
        emit("working");
      },
      "experimental.chat.system.transform": async (input = {}) => {
        bindOnce(input.sessionID);
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
