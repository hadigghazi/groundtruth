// The live numbers, as split bars: whole match on top, the detector's recent window below.

import type { Lang, MatchMeta, State } from "../api";
import { dir, fill, strings } from "../i18n";

function Split({ label, a, b, fmt, colors }: { label: string; a: number | null; b: number | null; fmt: (v: number) => string; colors: [string, string] }) {
  const total = (a ?? 0) + (b ?? 0);
  const share = total > 0 ? (100 * (a ?? 0)) / total : 50;
  return (
    <div className="split">
      <span className="split-v">{a == null ? "–" : fmt(a)}</span>
      <span className="split-mid">
        <span className="split-label">{label}</span>
        <span className="split-bar" dir="ltr">
          <span style={{ width: `${share}%`, background: colors[0] }} />
          <span style={{ width: `${100 - share}%`, background: colors[1] }} />
        </span>
      </span>
      <span className="split-v">{b == null ? "–" : fmt(b)}</span>
    </div>
  );
}

const pct = (v: number) => `${Math.round(100 * v)}%`;
const one = (v: number) => v.toFixed(1);

export function StatsPanel({ meta, state, lang }: { meta: MatchMeta; state: State | null; lang: Lang }) {
  const s = strings(lang);
  if (!state) return null;
  const h = meta.home.team_id, a = meta.away.team_id;
  const T = state.teams, R = state.recent ?? {};
  const num = (x: unknown) => (x == null ? null : Number(x));
  const colors: [string, string] = [meta.home.colors[0], meta.away.colors[0]];
  const possTotal = Math.max(1, Number(T[h].possession_ms) + Number(T[a].possession_ms));
  return (
    <div className="stats" dir={dir(lang)}>
      <div className="stats-head" dir="ltr"><span>{meta.home.short_name}</span><span>{meta.away.short_name}</span></div>
      <Split label={s.possession} a={Number(T[h].possession_ms) / possTotal} b={Number(T[a].possession_ms) / possTotal} fmt={pct} colors={colors} />
      <Split label={s.shots} a={num(T[h].shots)} b={num(T[a].shots)} fmt={(v) => String(v)} colors={colors} />
      <Split label={s.xg} a={num(T[h].xg)} b={num(T[a].xg)} fmt={(v) => v.toFixed(2)} colors={colors} />
      <Split label={s.passAccuracy} a={num(T[h].pass_accuracy)} b={num(T[a].pass_accuracy)} fmt={pct} colors={colors} />
      <p className="panel-sub">{fill(s.lastMin, { m: state.recent_min })}</p>
      {([["high_turnovers", one], ["time_on_ball_s", (v: number) => `${v.toFixed(1)} s`], ["long_pass_share", pct],
        ["line_height_m", (v: number) => `${v.toFixed(0)} m`], ["width_adj_m", (v: number) => `${v.toFixed(0)} m`]] as [string, (v: number) => string][]).map(([k, f]) => (
        <Split key={k} label={metricWord(k, lang)} a={R[h]?.[k] ?? null} b={R[a]?.[k] ?? null} fmt={f} colors={colors} />
      ))}
      {state.control_chaos != null && (
        <div className="chaos">
          <span>{s.controlChaos}</span>
          <span className="chaos-track" dir="ltr"><span style={{ insetInlineStart: `${state.control_chaos}%` }} /></span>
        </div>
      )}
    </div>
  );
}

const WORDS: Record<string, Record<Lang, string>> = {
  high_turnovers: { en: "High turnovers", es: "Recuperaciones altas", ar: "استعادات في الأمام" },
  time_on_ball_s: { en: "Time on the ball", es: "Tiempo con balón", ar: "زمن الاحتفاظ بالكرة" },
  long_pass_share: { en: "Long balls", es: "Balones largos", ar: "الكرات الطويلة" },
  line_height_m: { en: "Defensive line", es: "Línea defensiva", ar: "خط الدفاع" },
  width_adj_m: { en: "Width in possession", es: "Amplitud con balón", ar: "العرض أثناء الاستحواذ" },
};
const metricWord = (k: string, lang: Lang) => WORDS[k]?.[lang] ?? k;
