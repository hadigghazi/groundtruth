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
}

export interface Moment { moment_id: string; kind: string; period: number; clock_ms: number; t: number; team_id?: string | null; player_id?: string | null; salience: number; headline: string; metrics: Record<string, unknown>; evidence: string[] }

export interface CueMetric { key: string; value: number | string; unit?: string | null; team_id?: string | null; before?: number | null }
export interface Cue {
  cue_id: string; moment_id?: string | null; insight_id?: string | null; t_in: number; t_out: number;
  start: { period: number; clock_ms: number }; end: { period: number; clock_ms: number };
  layer: "ticker" | "lower_third" | "player_tag" | "pitch_overlay" | "stat_panel" | "fullscreen";
  template: string; priority: number; profile: string; lang: Lang; team_id?: string | null; anchor_player_id?: string | null;
  headline: string; body?: string | null; metrics: CueMetric[]; evidence: string[]; verified: boolean; fallback: boolean; path: string[];
}

export interface BoardEntry { moment_id: string; stage: string; at_ms: number; [k: string]: unknown }

export interface TeamWindow { [metric: string]: number | null }
export interface State {
  match_id: string; period: number; clock_ms: number; t: number; score: Record<string, number>;
  teams: Record<string, Record<string, number | null>>; recent: Record<string, TeamWindow>; recent_min: number; control_chaos: number | null;
}

export type StreamMessage =
  | { type: "hello"; meta: MatchMeta; state: State; cues: Cue[]; speed: number }
  | Frame
  | { type: "event"; event: MatchEvent }
  | { type: "moment"; moment: Moment }
  | { type: "cue"; cue: Cue }
  | { type: "state"; state: State }
  | { type: "board"; entry: BoardEntry }
  | { type: "console"; team_id: string; label: string; t: number; period: number; clock_ms: number }
  | { type: "full_time"; score: number[] }
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
  truth: (id: string) => fetch(`/api/matches/${id}/truth`).then((r) => json<{ label: string; team_id: string; period: number; clock_ms: number; source: string }[]>(r)),
};

export function openStream(id: string, onMessage: (m: StreamMessage) => void, onClose: () => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/matches/${id}/stream`);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data) as StreamMessage);
  ws.onclose = onClose;
  return () => ws.close();
}
