// Wire types (mirrors src/groundtruth/schema.py) and the REST/WebSocket client.

export type Lang = "en" | "es" | "ar";
export type Mode = "analyst" | "casual";

export interface PlayerMeta { player_id: string; name: string; short_name: string; number: number; role: string; slot?: string | null }
export interface TeamMeta { team_id: string; name: string; short_name: string; colors: string[]; formation: string; lineup: PlayerMeta[]; bench: PlayerMeta[] }
export interface MatchMeta { match_id: string; seed: number; home: TeamMeta; away: TeamMeta; venue: string; kickoff: string; weather: string; referee: string; direction: Record<string, Record<string, number>> }

export interface Frame { type: "frame"; t: number; period: number; clock_ms: number; ball: [number, number, number]; players: [string, number, number][] }

export interface MatchEvent {
  event_id: string; seq: number; type: string; period: number; clock_ms: number; end_clock_ms?: number;
  team_id?: string; player_id?: string; x?: number; y?: number; end_x?: number; end_y?: number;
  outcome?: string; receiver_id?: string; restart?: string; card?: string; player_in_id?: string;
  derived?: { xg?: number; difficulty?: number; distance_m?: number; shot_speed_kmh?: number } | null;
}

export interface Moment { moment_id: string; kind: string; period: number; clock_ms: number; t: number; team_id?: string | null; player_id?: string | null; salience: number; headline: string; metrics: Record<string, unknown>; evidence: string[] }

export interface CueMetric { key: string; value: number | string; unit?: string | null; team_id?: string | null; before?: number | null }
export interface Cue {
  cue_id: string; moment_id?: string | null; insight_id?: string | null; t_in: number; t_out: number;
  start: { period: number; clock_ms: number }; end: { period: number; clock_ms: number };
  layer: "ticker" | "lower_third" | "player_tag" | "pitch_overlay" | "stat_panel" | "fullscreen" | "caption";
  template: string; priority: number; profile: string; lang: Lang; team_id?: string | null; anchor_player_id?: string | null;
  headline: string; body?: string | null; metrics: CueMetric[]; evidence: string[]; verified: boolean; fallback: boolean; path: string[];
  replaces?: string[];
}

/** km, top km/h, km/h now, passes, completed, shots, goals */
export type PlayerLine = [number, number, number, number, number, number, number];

export interface BoardEntry { moment_id: string; stage: string; at_ms: number; [k: string]: unknown }

export interface TeamWindow { [metric: string]: number | null }
export interface State {
  match_id: string; period: number; clock_ms: number; t: number; score: Record<string, number>;
  teams: Record<string, Record<string, number | null>>; recent: Record<string, TeamWindow>; recent_min: number; control_chaos: number | null;
}

export interface AgentRun {
  moment_id: string; t: number; minute: number; period: number;
  outcome: "verified" | "fallback" | "timeout" | "error" | "guarded" | "outage"; attempts: number; first_pass: boolean;
  total_ms: number; tactician_ms: number; judge_ms: number | null; writers_ms: number | null; rejected_by: string[];
  backup_used: boolean; tokens_in: number; tokens_out: number;
  rule_cause?: string | null; flagged_team?: string | null; cause?: string | null; team_id?: string | null;
}
export interface AgentSummary {
  runs: number; verified: number; first_pass_rate: number | null; fallback_rate: number | null;
  verified_ms: { p50: number | null; p95: number | null; max: number | null };
  stage_p50_ms: { tactician_ms: number | null; judge_ms: number | null; writers_ms: number | null };
  rejections: { code: number; judge: number }; backup_used: number; tokens_per_run: number | null;
  outcomes: Record<string, number>;
}
export interface ScoreSide { changes: number; alerts: number; caught: number; right_cause: number; false_alarms: number; median_delay_min: number | null }
export interface ScoreRow { caught: boolean; right_cause: boolean; said: string | null; delay_min: number | null }
export interface Scorecard {
  rule: ScoreSide; agents: ScoreSide; window_min: number; agents_verified: number;
  changes: { team_id: string; label: string; source: string; period: number; clock_ms: number; rule: ScoreRow; agents: ScoreRow }[];
}

export interface Storyline {
  id: string; kind: string; team_id?: string | null; player_id?: string | null; t: number; minute: number;
  status: "active" | "resolved"; facts: Record<string, unknown>; headline: Record<Lang, string>;
}
export interface RecapText { headline: string; body: string; social: string }
export interface Clip { moment_id: string; kind: string; team_id?: string | null; minute: number; t_in: number; t_out: number; label: Record<Lang, string> }
export interface RecapPackage {
  texts: Record<Lang, { analyst: RecapText; casual: RecapText }>; written_by: Record<Lang, string>;
  player_of_match: { player: string; team: string; team_id: string; goals: number; xg: number; shots: number; passes: number;
    pass_accuracy: number | null; tackles_and_interceptions: number; km: number; top_kmh: number } | null;
  goals: { minute: number; team: string; player: string }[]; highlights: Clip[];
}

export type StreamMessage =
  | { type: "hello"; meta: MatchMeta; state: State; cues: Cue[]; speed: number; delay_s: number; outage: boolean;
      board: BoardEntry[]; console: { team_id: string; label: string; t: number; period: number; clock_ms: number }[];
      agents: AgentSummary; runs: AgentRun[]; full_time: Scorecard | null; events?: MatchEvent[]; moments?: Moment[];
      storylines?: Storyline[]; recap?: RecapPackage | null }
  | { type: "storyline"; storyline: Storyline }
  | { type: "recap"; recap: RecapPackage }
  | Frame
  | { type: "event"; event: MatchEvent; derived?: MatchEvent["derived"] }
  | { type: "moment"; moment: Moment }
  | { type: "cue"; cue: Cue }
  | { type: "state"; state: State; players?: Record<string, PlayerLine> }
  | { type: "board"; entry: BoardEntry }
  | { type: "console"; team_id: string; label: string; t: number; period: number; clock_ms: number }
  | { type: "full_time"; score: number[]; scorecard: Scorecard; agents: AgentSummary }
  | { type: "agent_run"; run: AgentRun; summary: AgentSummary }
  | { type: "outage"; on: boolean }
  | { type: "speed"; speed: number }
  | { type: "ping" };

export interface Club { club_id: string; name: string; short_name: string; ground: string; colors: string[]; formation: string; players: { player_id: string; name: string; number: number; role: string }[] }

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  league: () => fetch("/api/league").then((r) => json<Club[]>(r)),
  matches: () => fetch("/api/matches").then((r) => json<{ match_id: string; home: string; away: string; finished: boolean; score: number[] }[]>(r)),
  create: (body: { home: string; away: string; seed: number; speed: number; scripted: boolean }) =>
    fetch("/api/matches", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
      .then((r) => json<{ match_id: string; meta: MatchMeta }>(r)),
  console: (id: string, team_id: string, label: string) =>
    fetch(`/api/matches/${id}/console`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ team_id, label }) })
      .then((r) => json<{ ok: boolean }>(r)),
  speed: (id: string, speed: number) =>
    fetch(`/api/matches/${id}/speed`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ speed }) })
      .then((r) => json<{ ok: boolean; speed: number }>(r)),
  outage: (id: string, on: boolean) =>
    fetch(`/api/matches/${id}/agents/outage`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ on }) })
      .then((r) => json<{ ok: boolean; outage: boolean }>(r)),
  agents: (id: string) => fetch(`/api/matches/${id}/agents`).then((r) => json<{ runs: AgentRun[]; summary: AgentSummary }>(r)),
  truth: (id: string) => fetch(`/api/matches/${id}/truth`).then((r) => json<{ label: string; team_id: string; period: number; clock_ms: number; source: string }[]>(r)),
};

/**
 * The live stream for one match. On Azure the server hands out a Web PubSub URL for the
 * match's group (with the hello snapshot); locally, this server streams it, hello first.
 * Messages arrive in batches ({"type": "many"}) to keep the message count down.
 */
export function openStream(id: string, onMessage: (m: StreamMessage) => void, onClose: () => void): () => void {
  let ws: WebSocket | null = null, closed = false, timer = 0;
  const unpack = (e: MessageEvent) => {
    let m = JSON.parse(e.data);
    if (typeof m === "string") m = JSON.parse(m); // a message encoded twice on the way
    if (m.type === "many") for (const x of m.messages) onMessage(x as StreamMessage);
    else onMessage(m as StreamMessage);
  };
  const go = async () => {
    if (closed) return;
    let url: string | null = null;
    try {
      const c = await fetch(`/api/matches/${id}/connect`).then((r) => r.json());
      if (c.url) {
        if (!c.hello) { timer = window.setTimeout(go, 800); return; } // not kicked off yet
        onMessage(c.hello as StreamMessage);
        url = c.url;
      }
    } catch { /* fall back to this server's stream */ }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(url ?? `${proto}://${location.host}/api/matches/${id}/stream`);
    ws.onmessage = unpack;
    ws.onclose = onClose;
  };
  void go();
  return () => { closed = true; window.clearTimeout(timer); ws?.close(); };
}
