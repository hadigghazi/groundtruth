// The client-side match buffer: everything the stream sends, and a delayed playhead.
//
// Like a live broadcast, the viewer watches a few seconds behind real time. The delay
// gives the agents time to explain a moment, so its overlay lands with the moment
// instead of after it. Frames arrive at up to 25 a second; the views interpolate.

import type { BoardEntry, Cue, Frame, Lang, MatchEvent, MatchMeta, Mode, Moment, State, StreamMessage } from "./api";

export const AGENT_BUDGET_S = 12; // must match the server's agent budget

export interface Viewer { mode: Mode; lang: Lang; club: string | null; player: string | null }

export class MatchBuffer {
  meta: MatchMeta | null = null;
  state: State | null = null;
  frames: Frame[] = [];
  events: MatchEvent[] = [];
  moments: Moment[] = [];
  cues: Cue[] = [];
  board: BoardEntry[] = [];
  console: { team_id: string; label: string; clock_ms: number; period: number }[] = [];
  fullTime = false;
  speed = 1;
  private listeners = new Set<() => void>();
  private version = 0;
  private eventsById = new Map<string, MatchEvent>();

  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  snapshot = () => this.version;
  private bump() { this.version++; this.listeners.forEach((f) => f()); }

  handle(m: StreamMessage) {
    switch (m.type) {
      case "hello": this.meta = m.meta; this.state = m.state; this.speed = m.speed; m.cues.forEach((c) => this.addCue(c)); break;
      case "frame": this.frames.push(m); if (this.frames.length > 6000) this.frames.splice(0, 1000); return; // frames do not re-render React
      case "event": this.events.push(m.event); this.eventsById.set(m.event.event_id, m.event); break;
      case "moment": this.moments.push(m.moment); break;
      case "cue": this.addCue(m.cue); break;
      case "state": this.state = m.state; break;
      case "board": this.board.push(m.entry); break;
      case "console": this.console.push(m); break;
      case "full_time": this.fullTime = true; break;
      default: return;
    }
    this.bump();
  }

  private addCue(c: Cue) {
    const i = this.cues.findIndex((x) => x.cue_id === c.cue_id);
    if (i >= 0) this.cues[i] = c; else this.cues.push(c);
  }

  event(id: string) { return this.eventsById.get(id); }

  get liveT() { return this.frames.length ? this.frames[this.frames.length - 1].t : 0; }
  /** Match-time delay: the agents' wall-clock budget, scaled by replay speed. */
  get delayMs() { return this.speed > 0 ? AGENT_BUDGET_S * 1000 * this.speed : 0; }

  /** The frame state at play time t, interpolated between the two nearest frames. */
  frameAt(t: number): Frame | null {
    const f = this.frames;
    if (!f.length) return null;
    if (t <= f[0].t) return f[0];
    if (t >= f[f.length - 1].t) return f[f.length - 1];
    let lo = 0, hi = f.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (f[mid].t <= t) lo = mid; else hi = mid; }
    const a = f[lo], b = f[hi];
    if (a.period !== b.period || b.t - a.t > 3000) return a;
    const w = (t - a.t) / (b.t - a.t);
    const pos = new Map(b.players.map((p) => [p[0], p]));
    return {
      ...a,
      t, clock_ms: Math.round(a.clock_ms + w * (b.clock_ms - a.clock_ms)),
      ball: [a.ball[0] + w * (b.ball[0] - a.ball[0]), a.ball[1] + w * (b.ball[1] - a.ball[1]), a.ball[2] + w * (b.ball[2] - a.ball[2])],
      players: a.players.map((p) => {
        const q = pos.get(p[0]);
        if (!q || Math.hypot(q[1] - p[1], q[2] - p[2]) > 4) return p; // a teleport (kick-off reset): no smear
        return [p[0], p[1] + w * (q[1] - p[1]), p[2] + w * (q[2] - p[2])] as [string, number, number];
      }),
    };
  }

  /** Goals scored up to play time t (the scorebug follows the playhead, not the live edge). */
  scoreAt(t: number): [number, number] {
    if (!this.meta) return [0, 0];
    const tAt = this.timeOfClock();
    let h = 0, a = 0;
    for (const e of this.events) {
      if (e.type !== "goal") continue;
      if (tAt(e.period, e.clock_ms) > t) continue;
      if (e.team_id === this.meta.home.team_id) h++; else a++;
    }
    return [h, a];
  }

  /** Maps (period, clock) to play time using the frames we have seen. */
  timeOfClock() {
    const starts = new Map<number, { t: number; clock: number }>();
    for (const f of this.frames) if (!starts.has(f.period)) starts.set(f.period, { t: f.t, clock: f.clock_ms });
    return (period: number, clock: number) => {
      const s = starts.get(period);
      return s ? s.t + (clock - s.clock) : Number.POSITIVE_INFINITY;
    };
  }

  /** Cues on screen at play time t for one viewer. */
  activeCues(t: number, v: Viewer): Cue[] {
    return this.cues.filter((c) => {
      if (c.lang !== v.lang || t < c.t_in || t >= c.t_out) return false;
      if (c.profile !== "all" && c.profile !== v.mode) return false;
      if (v.mode === "casual") {
        if (c.priority >= 4 && c.layer !== "player_tag") return false; // a casual viewer gets fewer, bigger overlays
        if (c.layer === "player_tag" && v.player && c.anchor_player_id !== v.player) return false;
      }
      if (v.club && c.team_id && c.team_id !== v.club && c.priority >= 3 && c.layer !== "fullscreen") return false;
      return true;
    });
  }
}
