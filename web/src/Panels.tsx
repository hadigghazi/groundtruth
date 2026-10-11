import { useState } from "react";
import { api } from "./api";
import type { BoardEntry, Cue, Lang, MatchEvent, MatchMeta, State } from "./api";
import { clock, dir, strings } from "./i18n";

const STAGE_LABEL: Record<string, string> = {
  detected: "Detector flagged it", drafting: "Tactician drafting", drafted: "Tactician drafted",
  rejected: "Verifier rejected", verified: "Verifier approved", scheduled: "Producer scheduled",
  written_en: "Persona (English)", written_es: "Persona (Spanish)", written_ar: "Persona (Arabic)",
  fallback: "Template fallback", on_air: "On air", held: "Held back",
};

/** The insight board, grouped by moment: every hand-off and verdict as it happens. */
export function AgentTrace({ board, lang }: { board: BoardEntry[]; lang: Lang }) {
  const s = strings(lang);
  const groups = new Map<string, BoardEntry[]>();
  for (const e of board) { const g = groups.get(e.moment_id) ?? []; g.push(e); groups.set(e.moment_id, g); }
  const list = [...groups.entries()].reverse();
  if (!list.length) return <p className="muted">{s.noTrace}</p>;
  return (
    <div className="trace">
      {list.map(([id, entries]) => {
        const first = entries[0];
        const t0 = first.at_ms;
        return (
          <section className="trace-moment" key={id}>
            <header>
              <strong>{String(first.team_id ?? "")} · {String(first.rule_cause ?? first.kind ?? "")}</strong>
              {first.score != null && <span className="muted"> score {Number(first.score).toFixed(1)}</span>}
            </header>
            <ol>
              {entries.map((e, i) => (
                <li key={i} className={`stage stage-${e.stage}`}>
                  <span className="stage-t">+{((e.at_ms - t0) / 1000).toFixed(1)}s</span>
                  <span>{STAGE_LABEL[e.stage] ?? e.stage}{e.attempt ? ` (try ${e.attempt})` : ""}</span>
                  {typeof e.headline === "string" && <em> “{e.headline}”</em>}
                  {Array.isArray(e.problems) && <ul className="problems">{(e.problems as string[]).map((p, j) => <li key={j}>{p}</li>)}</ul>}
                </li>
              ))}
            </ol>
          </section>
        );
      })}
    </div>
  );
}

const LABELS = ["high_press", "low_block", "go_direct", "slow_down", "push_wide"] as const;

/** The director console: change a team's hidden tactics live, and see the answer key at full time. */
export function Console({ matchId, meta, log, fullTime, lang }: {
  matchId: string; meta: MatchMeta; log: { team_id: string; label: string; clock_ms: number; period: number }[]; fullTime: boolean; lang: Lang;
}) {
  const s = strings(lang);
  const [busy, setBusy] = useState(false);
  const [truth, setTruth] = useState<{ label: string; team_id: string; period: number; clock_ms: number; source: string }[] | null>(null);
  const send = async (team: string, label: string) => {
    setBusy(true);
    try { await api.console(matchId, team, label); } finally { setBusy(false); }
  };
  return (
    <div className="console" dir={dir(lang)}>
      <p><strong>{s.console}</strong></p>
      <p className="muted">{s.consoleNote}</p>
      {[meta.home, meta.away].map((team) => (
        <div className="console-team" key={team.team_id}>
          <span className="console-name" style={{ borderColor: team.colors[0] }}>{team.short_name}</span>
          <div className="console-buttons">
            {LABELS.map((l) => (
              <button key={l} disabled={busy || fullTime} onClick={() => send(team.team_id, l)}>{s.labels[l]}</button>
            ))}
          </div>
        </div>
      ))}
      {log.length > 0 && (
        <ul className="console-log">
          {log.map((c, i) => <li key={i}>{clock(c.clock_ms, c.period)} · {c.team_id} · {s.labels[c.label as keyof typeof s.labels] ?? c.label}</li>)}
        </ul>
      )}
      <div className="answer-key">
        <strong>{s.revealed}</strong>
        {truth ? (
          <ul>{truth.map((t, i) => <li key={i}>{clock(t.clock_ms, t.period)} · {t.team_id} · {s.labels[t.label as keyof typeof s.labels] ?? t.label} <span className="muted">({t.source})</span></li>)}</ul>
        ) : (
          <button disabled={!fullTime} onClick={async () => setTruth(await api.truth(matchId))}>{fullTime ? s.reveal : s.revealLater}</button>
        )}
      </div>
    </div>
  );
}

/** A compact analyst table from the live state. */
export function StatsPanel({ meta, state, lang }: { meta: MatchMeta; state: State | null; lang: Lang }) {
  const s = strings(lang);
  if (!state) return null;
  const h = meta.home.team_id, a = meta.away.team_id;
  const rows: [string, (t: string) => string][] = [
    [s.possession, (t) => `${Math.round(100 * Number(state.teams[t].possession_ms) / Math.max(1, Number(state.teams[h].possession_ms) + Number(state.teams[a].possession_ms)))}%`],
    [s.passAccuracy, (t) => state.teams[t].pass_accuracy == null ? "–" : `${Math.round(100 * Number(state.teams[t].pass_accuracy))}%`],
    [s.shots, (t) => String(state.teams[t].shots)],
    [s.xg, (t) => Number(state.teams[t].xg).toFixed(2)],
  ];
  const recent = state.recent ?? {};
  const live: [string, string][] = [
    ["high_turnovers", "high turnovers / 10 min"], ["time_on_ball_s", "time on ball (s)"],
    ["long_pass_share", "long balls"], ["line_height_m", "back line (m)"], ["width_adj_m", "width (m)"],
  ];
  return (
    <div className="stats" dir={dir(lang)}>
      <table>
        <thead><tr><th /><th>{meta.home.short_name}</th><th>{meta.away.short_name}</th></tr></thead>
        <tbody>
          {rows.map(([label, f]) => <tr key={label}><td>{label}</td><td>{f(h)}</td><td>{f(a)}</td></tr>)}
          <tr><td colSpan={3} className="muted">last {state.recent_min} min</td></tr>
          {live.map(([k, label]) => (
            <tr key={k}><td>{label}</td>
              {[h, a].map((t) => { const v = recent[t]?.[k]; return <td key={t}>{v == null ? "–" : k.endsWith("_share") ? `${Math.round(v * 100)}%` : v.toFixed(1)}</td>; })}
            </tr>
          ))}
        </tbody>
      </table>
      {state.control_chaos != null && <p>Control ↔ chaos: <strong>{state.control_chaos.toFixed(0)}</strong> / 100</p>}
    </div>
  );
}

/** The evidence drawer: the events and numbers behind one overlay. */
export function Evidence({ cue, events, lang, onClose }: { cue: Cue; events: MatchEvent[]; lang: Lang; onClose: () => void }) {
  const s = strings(lang);
  return (
    <aside className="evidence" dir={dir(lang)}>
      <header><strong>{s.evidence}</strong><button onClick={onClose}>{s.close}</button></header>
      <p className="ev-head">{cue.headline}</p>
      {cue.body && <p>{cue.body}</p>}
      <p className="muted">{cue.fallback ? s.fallback : s.verified} · {cue.path.join(" → ")}</p>
      {cue.metrics.length > 0 && (
        <table><tbody>{cue.metrics.map((m, i) => (
          <tr key={i}><td>{m.key}</td><td>{m.team_id ?? ""}</td><td>{m.before != null ? `${m.before} → ` : ""}{String(m.value)}</td></tr>
        ))}</tbody></table>
      )}
      <p><strong>{s.events}</strong></p>
      <ul className="ev-list">
        {events.map((e) => (
          <li key={e.event_id}><code>{e.event_id}</code> {clock(e.clock_ms, e.period)} {e.type}{e.outcome ? ` (${e.outcome})` : ""} {e.player_id ?? ""}</li>
        ))}
        {!events.length && <li className="muted">—</li>}
      </ul>
    </aside>
  );
}
