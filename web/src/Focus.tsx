// Personal overlays for one viewer: the followed player's live numbers, and the one metric they
// chose to watch, charted for both teams. Both read the match as of the viewer's playhead.

import type { Lang, MatchMeta, PlayerLine, State } from "./api";
import { Crest } from "./Crest";
import { strings } from "./i18n";
import type { MatchBuffer } from "./match";

type Get = (st: State, tid: string) => number | null;
const num = (v: unknown) => (v == null ? null : Number(v));
const pct = (v: number) => `${Math.round(100 * v)}%`;

export const METRICS: Record<string, { label: Record<Lang, string>; get: Get; fmt: (v: number) => string }> = {
  possession: {
    label: { en: "Possession", es: "Posesión", ar: "الاستحواذ" }, fmt: pct,
    get: (st, tid) => {
      const all = Object.values(st.teams).reduce((a, t) => a + Number(t.possession_ms ?? 0), 0);
      return all ? Number(st.teams[tid].possession_ms) / all : null;
    },
  },
  xg: { label: { en: "xG", es: "xG", ar: "الأهداف المتوقعة" }, fmt: (v) => v.toFixed(2), get: (st, tid) => num(st.teams[tid].xg) },
  shots: { label: { en: "Shots", es: "Tiros", ar: "التسديدات" }, fmt: (v) => String(v), get: (st, tid) => num(st.teams[tid].shots) },
  pass_accuracy: { label: { en: "Pass accuracy", es: "Precisión de pase", ar: "دقة التمرير" }, fmt: pct,
    get: (st, tid) => num(st.teams[tid].pass_accuracy) },
  high_turnovers: { label: { en: "Pressing: high turnovers", es: "Presión: recuperaciones altas", ar: "الضغط: استعادات في الأمام" },
    fmt: (v) => v.toFixed(1), get: (st, tid) => num(st.recent?.[tid]?.high_turnovers) },
  line_height_m: { label: { en: "Defensive line height", es: "Altura de la línea", ar: "ارتفاع خط الدفاع" },
    fmt: (v) => `${v.toFixed(0)} m`, get: (st, tid) => num(st.recent?.[tid]?.line_height_m) },
  width_adj_m: { label: { en: "Width in possession", es: "Amplitud con balón", ar: "العرض أثناء الاستحواذ" },
    fmt: (v) => `${v.toFixed(0)} m`, get: (st, tid) => num(st.recent?.[tid]?.width_adj_m) },
  time_on_ball_s: { label: { en: "Tempo: time on the ball", es: "Ritmo: tiempo con balón", ar: "الإيقاع: زمن الاحتفاظ بالكرة" },
    fmt: (v) => `${v.toFixed(1)} s`, get: (st, tid) => num(st.recent?.[tid]?.time_on_ball_s) },
};

export function MetricFocus({ buf, meta, metric, upTo, lang }: { buf: MatchBuffer; meta: MatchMeta; metric: string; upTo: number; lang: Lang }) {
  const def = METRICS[metric];
  if (!def) return null;
  const pts = buf.states.filter((s) => s.t <= upTo);
  if (pts.length < 2) return null;
  const teams = [meta.home, meta.away];
  const series = teams.map((t) => pts.map((p) => def.get(p.state, t.team_id)));
  const vals = series.flat().filter((v): v is number => v != null);
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const X = (i: number) => (i / (pts.length - 1)) * 200, Y = (v: number) => 56 - ((v - lo) / span) * 48;
  return (
    <div className="gt-focus" dir="ltr">
      <span className="gt-focus-title">{def.label[lang]}</span>
      <svg viewBox="0 0 200 60" className="gt-focus-chart" aria-hidden="true">
        {series.map((s, k) => (
          <polyline key={k} fill="none" stroke={teams[k].colors[0]} strokeWidth={2} strokeLinejoin="round"
            points={s.map((v, i) => (v == null ? "" : `${X(i)},${Y(v)}`)).filter(Boolean).join(" ")} />
        ))}
      </svg>
      <span className="gt-focus-now">
        {teams.map((t, k) => {
          const v = series[k][series[k].length - 1];
          return <span key={t.team_id} style={{ color: t.colors[0] }}>{t.short_name.slice(0, 3).toUpperCase()} {v == null ? "–" : def.fmt(v)}</span>;
        })}
      </span>
    </div>
  );
}

export function PlayerHud({ meta, line, playerId, lang }: { meta: MatchMeta; line: PlayerLine | undefined; playerId: string; lang: Lang }) {
  const s = strings(lang);
  const team = [meta.home, meta.away].find((t) => [...t.lineup, ...t.bench].some((p) => p.player_id === playerId));
  const p = team && [...team.lineup, ...team.bench].find((x) => x.player_id === playerId);
  if (!team || !p) return null;
  const [km, top, now, passes, done, shots, goals] = line ?? [0, 0, 0, 0, 0, 0, 0];
  return (
    <div className="gt-hud" dir="ltr" style={{ ["--team" as string]: team.colors[0] }}>
      <span className="gt-hud-name"><Crest colors={team.colors} code={team.short_name} size={16} /> <b>{p.number}</b> {p.short_name}</span>
      <span className="gt-hud-grid">
        <span><b>{km.toFixed(1)}</b> km</span>
        <span><b>{now.toFixed(0)}</b> km/h</span>
        <span><b>{top.toFixed(1)}</b> {s.topSpeed}</span>
        <span><b>{done}</b>/{passes} {s.passesWord}</span>
        <span><b>{shots}</b> {s.shots}</span>
        <span><b>{goals}</b> {s.goalsWord}</span>
      </span>
    </div>
  );
}
