// Analyst views drawn from the events the viewer has already seen: the momentum of the match,
// every shot sized by its xG, and each team's passing network. Both teams attack to the right
// of the home side's chart, whatever the half.

import { useEffect, useState } from "react";
import type { Lang, MatchEvent, MatchMeta } from "../api";
import { strings } from "../i18n";
import { L, W, markings } from "../views/pitch";

const MARKS = markings();
const VB = { w: L + 4, h: W + 4 };

function Pitch({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <svg className="mini-pitch" viewBox={`-2 -2 ${VB.w} ${VB.h}`} role="img" aria-label={label}>
      <rect x={-2} y={-2} width={VB.w} height={VB.h} fill="#123d22" />
      <g stroke="rgba(255,255,255,.45)" strokeWidth={0.35} fill="none">
        {MARKS.segs.map(([x0, y0, x1, y1], i) => <line key={i} x1={x0} y1={W - y0} x2={x1} y2={W - y1} />)}
        {MARKS.arcs.filter((a) => a.r > 2).map((a, i) => {
          const p = (t: number) => `${a.cx + a.r * Math.cos(t)} ${W - (a.cy + a.r * Math.sin(t))}`;
          const full = a.a1 - a.a0 >= Math.PI * 2 - 1e-6;
          return full ? <circle key={i} cx={a.cx} cy={W - a.cy} r={a.r} />
            : <path key={i} d={`M ${p(a.a0)} A ${a.r} ${a.r} 0 0 0 ${p(a.a1)}`} />;
        })}
      </g>
      {children}
    </svg>
  );
}

/** Puts every event in one frame: the home side attacks right, the away side left. */
function normaliser(meta: MatchMeta) {
  return (e: { period: number; team_id?: string; x?: number; y?: number }, x = e.x ?? 0, y = e.y ?? 0): [number, number] => {
    const d = meta.direction[String(e.period)]?.[e.team_id ?? ""] ?? 1;
    const towardRight = e.team_id === meta.away.team_id ? -d : d;
    return towardRight === 1 ? [x, W - y] : [L - x, y];
  };
}

export function ShotMap({ meta, events, lang }: { meta: MatchMeta; events: MatchEvent[]; lang: Lang }) {
  const s = strings(lang);
  const at = normaliser(meta);
  const shots = events.filter((e) => e.type === "shot" && e.x != null);
  const color = (t?: string) => (t === meta.away.team_id ? meta.away.colors[0] : meta.home.colors[0]);
  return (
    <figure className="chart">
      <figcaption>{s.shotMap}</figcaption>
      <Pitch label={s.shotMap}>
        {shots.map((e) => {
          const [x, y] = at(e);
          const r = 0.9 + 5 * Math.sqrt(e.derived?.xg ?? 0.05);
          const goal = e.outcome === "goal";
          return <circle key={e.event_id} cx={x} cy={y} r={r} fill={goal ? color(e.team_id) : "none"} stroke={color(e.team_id)}
            strokeWidth={goal ? 0.4 : 0.7} opacity={0.9}><title>{`${e.outcome} · xG ${(e.derived?.xg ?? 0).toFixed(2)}`}</title></circle>;
        })}
      </Pitch>
      <div className="chart-key"><span><i className="dot filled" />{s.goalWord}</span><span><i className="dot" />{s.shots}</span><span>{s.sizeIsXg}</span></div>
    </figure>
  );
}

export function PassNetwork({ meta, events, lang }: { meta: MatchMeta; events: MatchEvent[]; lang: Lang }) {
  const s = strings(lang);
  const [team, setTeam] = useState(meta.home.team_id);
  const at = normaliser(meta);
  const side = team === meta.home.team_id ? meta.home : meta.away;
  const passes = events.filter((e) => e.type === "pass" && e.team_id === team && e.outcome === "complete" && e.receiver_id && e.x != null);
  const pos = new Map<string, { x: number; y: number; n: number }>();
  const links = new Map<string, number>();
  for (const e of passes) {
    const [x, y] = at(e);
    const p = pos.get(e.player_id!) ?? { x: 0, y: 0, n: 0 };
    p.x += x; p.y += y; p.n++;
    pos.set(e.player_id!, p);
    const k = [e.player_id, e.receiver_id].sort().join("|");
    links.set(k, (links.get(k) ?? 0) + 1);
  }
  const avg = (id: string) => { const p = pos.get(id); return p ? [p.x / p.n, p.y / p.n] : null; };
  const top = Math.max(1, ...links.values());
  const numbers = new Map([...side.lineup, ...side.bench].map((p) => [p.player_id, p.number]));
  return (
    <figure className="chart">
      <figcaption>
        {s.passNetwork}
        <span className="chart-switch">
          {[meta.home, meta.away].map((t) => (
            <button key={t.team_id} className={team === t.team_id ? "on" : ""} onClick={() => setTeam(t.team_id)}>{t.short_name}</button>
          ))}
        </span>
      </figcaption>
      <Pitch label={s.passNetwork}>
        {[...links.entries()].filter(([, n]) => n >= 3).map(([k, n]) => {
          const [a, b] = k.split("|").map(avg);
          if (!a || !b) return null;
          return <line key={k} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="#c6ff3d" strokeOpacity={0.15 + 0.7 * (n / top)} strokeWidth={0.3 + 1.6 * (n / top)} />;
        })}
        {[...pos.entries()].filter(([, p]) => p.n >= 4).map(([id, p]) => (
          <g key={id}>
            <circle cx={p.x / p.n} cy={p.y / p.n} r={1.2 + Math.sqrt(p.n) * 0.25} fill={side.colors[0]} stroke="#fff" strokeWidth={0.3} />
            <text x={p.x / p.n} y={p.y / p.n + 0.9} textAnchor="middle" fontSize={2.4} fill="#fff" fontWeight={700}>{numbers.get(id) ?? ""}</text>
          </g>
        ))}
      </Pitch>
    </figure>
  );
}

/** Expected threat per minute for each side, as a mirrored area chart, up to the playhead. */
export function Momentum({ matchId, meta, upTo, lang }: { matchId: string; meta: MatchMeta; upTo: number; lang: Lang }) {
  const s = strings(lang);
  const [series, setSeries] = useState<Record<string, [number, number][]>>({});
  useEffect(() => {
    let alive = true;
    const load = () => fetch(`/api/matches/${matchId}/series/threat`).then((r) => (r.ok ? r.json() : {})).then((d) => { if (alive) setSeries(d); }).catch(() => undefined);
    void load();
    const id = setInterval(load, 15000);
    return () => { alive = false; clearInterval(id); };
  }, [matchId]);
  const h = (series[meta.home.team_id] ?? []).filter(([t]) => t <= upTo);
  const a = (series[meta.away.team_id] ?? []).filter(([t]) => t <= upTo);
  const n = Math.max(h.length, a.length);
  if (n < 2) return null;
  const top = Math.max(0.01, ...h.map(([, v]) => v), ...a.map(([, v]) => v));
  const X = (i: number) => (i / Math.max(n - 1, 1)) * 100;
  const area = (pts: [number, number][], up: boolean) =>
    `M 0 30 ${pts.map(([, v], i) => `L ${X(i)} ${30 + (up ? -1 : 1) * (v / top) * 28}`).join(" ")} L ${X(pts.length - 1)} 30 Z`;
  return (
    <figure className="chart">
      <figcaption>{s.momentum}</figcaption>
      <svg viewBox="0 0 100 60" preserveAspectRatio="none" className="momentum" role="img" aria-label={s.momentum}>
        <path d={area(h, true)} fill={meta.home.colors[0]} opacity={0.85} />
        <path d={area(a, false)} fill={meta.away.colors[0]} opacity={0.85} />
        <line x1={0} x2={100} y1={30} y2={30} stroke="rgba(255,255,255,.3)" strokeWidth={0.3} />
      </svg>
      <div className="chart-key"><span style={{ color: meta.home.colors[0] }}>▲ {meta.home.short_name}</span><span style={{ color: meta.away.colors[0] }}>▼ {meta.away.short_name}</span></div>
    </figure>
  );
}
