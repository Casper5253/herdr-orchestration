// The team's usage and context in the status bar: one entry per subscription
// pool (`hx usage`), then one per live team agent (`hx context decide <name>`).
// Refreshes every 60 s. A failed read shows "?", never takes the bar down.

import { View, div } from "gpui-kit";
import { h_flex } from "gpui-base";
import { exec, theme } from "herdr";
import { pools, agentNames, contextEntry } from "./lib.js";

const REFRESH_MS = 60000;
const TIMEOUT_MS = 45000;
const MAX_AGENTS = 8;

export default class HxUsage extends View {
  init(_props, cx) {
    this.pools = [];
    this.agents = [];
    this.busy = false;
    cx.timer.every(REFRESH_MS, (cx) => this.refresh(cx));
    this.refresh(cx);
  }

  async hx(args) {
    try {
      return await exec("hx", args, { timeout: TIMEOUT_MS });
    } catch (e) {
      if (!/cannot run/.test(String(e?.message ?? e))) throw e;
      // `hx` is a .cmd shim on Windows, which Rust will not spawn directly.
      return exec("cmd", ["/d", "/c", "hx.cmd", ...args], { timeout: TIMEOUT_MS });
    }
  }

  refresh(cx) {
    if (this.busy) return;
    this.busy = true;
    cx.spawn(async (cx) => {
      try {
        const r = await this.hx(["usage"]);
        if (r.code !== 0) throw new Error(r.stderr.trim() || `hx usage exited ${r.code}`);
        const snapshot = JSON.parse(r.stdout);
        this.pools = pools(snapshot);
        const names = agentNames(snapshot).slice(0, MAX_AGENTS);
        this.agents = await Promise.all(names.map(async (name) => {
          try {
            const d = await this.hx(["context", "decide", name]);
            if (d.code !== 0) return contextEntry(name, null, d.stderr.trim() || `exited ${d.code}`);
            return contextEntry(name, JSON.parse(d.stdout));
          } catch (e) {
            return contextEntry(name, null, String(e?.message ?? e));
          }
        }));
      } catch (e) {
        this.pools = [{ label: "hx", value: "?", pct: null, detail: `hx usage · ${e?.message ?? e}` }];
        this.agents = [];
      } finally {
        this.busy = false;
        cx.notify();
      }
    });
  }

  render() {
    const t = theme();
    const colour = (e) => (e.pct == null ? t.dim : e.pct >= 90 ? t.blocked : e.pct >= 70 ? t.working : t.muted);
    const chip = (e, id) => h_flex().id(id).gap(4).items_center().flex_none().tooltip(e.detail)
      .child(div().text_color(t.dim).child(e.label))
      .child(div().text_color(colour(e)).when(e.pct != null && e.pct >= 90, (el) => el.font_semibold()).child(e.value));
    const children = this.pools.map((e) => chip(e, `hx-pool-${e.label}`));
    if (children.length && this.agents.length) children.push(div().id("hx-sep").text_color(t.dim).child("·"));
    children.push(...this.agents.map((e) => chip(e, `hx-agent-${e.label}`)));
    if (!children.length) children.push(div().id("hx-none").text_color(t.dim).tooltip("hx usage has not answered yet").child("hx ?"));
    return h_flex().id("hx-usage").size_full().px(14).gap(10).items_center()
      .bg(t.chrome).border_t(1).border_color(t.border).text_size(11.8).text_color(t.muted)
      .children(children);
  }
}
