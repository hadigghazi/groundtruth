// The client-side match buffer: everything the stream sends, and a delayed playhead.
//
// Like a live broadcast, the viewer watches a few seconds behind real time. The delay
// gives the agents time to explain a moment, so its verified card usually replaces the
// template before the viewer reaches it. Frames arrive at up to 25 a second; the views
// interpolate.

import type {
  AgentRun, AgentSummary, BoardEntry, Cue, Frame, Lang, MatchEvent, MatchMeta, Mode, Moment, Scorecard, State, StreamMessage,
} from "./api";

export const MIN_READ_S = 5; // a card stays up at least this long in wall time, whatever the replay speed

export interface Viewer { mode: Mode; lang: Lang; club: string | null; player: string | null }

export class MatchBuffer {
  meta: MatchMeta | null = null;
  state: State | null = null;
  frames: Frame[] = [];
  events: MatchEvent[] = [];
  moments: Moment[] = [];
  cues: Cue[] = [];
  board: BoardEntry[] = [];
  runs: AgentRun[] = [];
  summary: AgentSummary | null = null;
  scorecard: Scorecard | null = null;
  console: { team_id: string; label: string; clock_ms: number; period: number }[] = [];
  fullTime = false;
  outage = false;
  speed = 1;
  delayS = 12;
  private listeners = new Set<() => void>();
  private version = 0;
  private eventsById = new Map<string, MatchEvent>();
  private cueIndex = new Map<string, number>();
  /** cue id -> the cues that replace it, so a viewer can swap a template for its upgrade */
  private replacedBy = new Map<string, Cue[]>();

  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  snapshot = () => this.version;
  private bump() { this.version++; this.listeners.forEach((f) => f()); }

  handle(m: StreamMessage) {
    switch (m.type) {
      case "hello":
        this.meta = m.meta; this.state = m.state; this.speed = m.speed; this.delayS = m.delay_s ?? 12;
        this.outage = !!m.outage; this.summary = m.agents ?? null; this.scorecard = m.full_time ?? null;
        this.fullTime = !!m.full_time;
        m.cues.forEach((c) => this.addCue(c));
        if (m.board) this.board = m.board;
        if (m.runs) this.runs = m.runs;
        if (m.moments) this.moments = m.moments;
        for (const e of m.events ?? []) if (!this.eventsById.has(e.event_id)) { this.events.push(e); this.eventsById.set(e.event_id, e); }
        if (m.console) this.console = m.console;
        break;
      case "frame": this.frames.push(m); if (this.frames.length > 24000) this.frames.splice(0, 2000); return; // frames do not re-render React
      case "event": this.events.push(m.event); this.eventsById.set(m.event.event_id, m.event); break;
      case "moment": this.moments.push(m.moment); break;
      case "cue": this.addCue(m.cue); break;
      case "state": this.state = m.state; break;
      case "board": this.board.push(m.entry); break;
      case "agent_run": this.runs.push(m.run); this.summary = m.summary; break;
      case "console": this.console.push(m); break;
      case "outage": this.outage = m.on; break;
      case "speed": this.speed = m.speed; break;
      case "full_time": this.fullTime = true; this.scorecard = m.scorecard; this.summary = m.agents; break;
      default: return;
    }
    this.bump();
  }

  private addCue(c: Cue) {
    const i = this.cueIndex.get(c.cue_id);
    if (i !== undefined) this.cues[i] = c;
    else { this.cueIndex.set(c.cue_id, this.cues.length); this.cues.push(c); }
    for (const id of c.replaces ?? []) {
      const list = this.replacedBy.get(id) ?? [];
      if (!list.some((x) => x.cue_id === c.cue_id)) list.push(c);
      this.replacedBy.set(id, list);
    }
  }

  event(id: string) { return this.eventsById.get(id); }

  get liveT() { return this.frames.length ? this.frames[this.frames.length - 1].t : 0; }
  /** Match-time delay: the broadcast delay in wall seconds, scaled by replay speed. */
  get delayMs() { return this.speed > 0 ? this.delayS * 1000 * this.speed : 0; }

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
    const firstPeriod = this.frames.length ? this.frames[0].period : 1;
    return (period: number, clock: number) => {
      const s = starts.get(period);
      if (s) return s.t + (clock - s.clock);
      return period < firstPeriod ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY; // before we joined: already past
    };
  }

  /** True if this viewer gets an upgrade of `c` instead of `c` itself. */
  private upgraded(c: Cue, v: Viewer) {
    const by = this.replacedBy.get(c.cue_id);
    return !!by && by.some((r) => r.lang === v.lang && (r.profile === "all" || r.profile === v.mode));
  }

  /** Cues on screen at play time t for one viewer, held long enough to read at any replay speed. */
  activeCues(t: number, v: Viewer): Cue[] {
    const hold = MIN_READ_S * 1000 * Math.max(this.speed, 1);
    return this.cues.filter((c) => {
      if (c.lang !== v.lang || t < c.t_in || t >= Math.max(c.t_out, c.t_in + hold)) return false;
      if (c.profile !== "all" && c.profile !== v.mode) return false;
      if (this.upgraded(c, v)) return false;
      if (c.layer === "caption") return true; // the running caption is for everyone
      if (v.mode === "casual") {
        if (c.priority >= 4 && c.layer !== "player_tag") return false; // a casual viewer gets fewer, bigger overlays
        if (c.layer === "player_tag" && v.player && c.anchor_player_id !== v.player) return false;
      }
      if (v.club && c.team_id && c.team_id !== v.club && c.priority >= 3 && c.layer !== "fullscreen") return false;
      return true;
    });
  }

  /** The run that produced a verified cue, for "verified in 3.4 s". */
  runFor(momentId: string | null | undefined) {
    return momentId ? this.runs.find((r) => r.moment_id === momentId) : undefined;
  }
}
