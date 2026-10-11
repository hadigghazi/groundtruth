// Broadcast graphics drawn from timed cues. Every card opens its evidence on click.
//
// The look is a broadcast package, not app UI: condensed display type, team-colour
// slabs, wipes in, and a stamp that draws itself when a card is verified.

import type { AgentRun, Cue, Lang, MatchMeta, Scorecard } from "./api";
import { Crest } from "./Crest";
import { clock, dir, fill, minute, strings } from "./i18n";

interface Props {
  meta: MatchMeta;
  cues: Cue[];
  score: [number, number];
  period: number;
  clockMs: number;
  lang: Lang;
  fullTime: boolean;
  scorecard: Scorecard | null;
  runFor: (momentId?: string | null) => AgentRun | undefined;
  onOpen: (cue: Cue) => void;
}

export const fmtMetric = (key: string, v: number | string) => {
  if (typeof v !== "number") return String(v);
  if (key.endsWith("_share") || key === "possession" || key === "pass_accuracy" || key === "field_tilt") return `${Math.round(v * 100)}%`;
  if (key.endsWith("_m")) return `${v.toFixed(1)} m`;
  if (key.endsWith("_s")) return `${v.toFixed(1)} s`;
  return Math.abs(v) < 10 ? v.toFixed(2) : v.toFixed(1);
};

function Stamp({ cue, lang, run }: { cue: Cue; lang: Lang; run?: AgentRun }) {
  const s = strings(lang);
  if (cue.fallback) return <span className="gt-chip gt-chip-template">{s.template}</span>;
  const agent = cue.template === "insight";
  return (
    <span className={`gt-chip gt-chip-verified ${agent ? "gt-stamp" : ""}`}>
      {agent && (
        <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
          <path d="M3 8.5l3 3 7-7.5" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      {agent && run ? fill(s.verifiedIn, { s: (run.total_ms / 1000).toFixed(1) }) : s.verified}
    </span>
  );
}

const teamColor = (meta: MatchMeta, team?: string | null) =>
  team === meta.away.team_id ? meta.away.colors[0] : team === meta.home.team_id ? meta.home.colors[0] : "#c6ff3d";

export function Overlays({ meta, cues, score, period, clockMs, lang, fullTime, scorecard, runFor, onOpen }: Props) {
  const s = strings(lang);
  const by = (layer: Cue["layer"]) =>
    cues.filter((c) => c.layer === layer).sort((a, b) => a.priority - b.priority || b.t_in - a.t_in)[0];
  const full = by("fullscreen"), lower = by("lower_third"), panel = by("stat_panel"), ticker = by("ticker"), caption = by("caption");
  const h = meta.home, a = meta.away;
  return (
    <div className="gt-overlays" dir={dir(lang)}>
      <div className="gt-bug" dir="ltr">
        <span className="gt-bug-team" style={{ ["--team" as string]: h.colors[0] }}>
          <Crest colors={h.colors} code={h.short_name} size={18} />{h.short_name.slice(0, 3).toUpperCase()}
        </span>
        <span className="gt-bug-score">{score[0]}</span>
        <span className="gt-bug-score">{score[1]}</span>
        <span className="gt-bug-team" style={{ ["--team" as string]: a.colors[0] }}>
          {a.short_name.slice(0, 3).toUpperCase()}<Crest colors={a.colors} code={a.short_name} size={18} />
        </span>
        <span className="gt-bug-clock">{fullTime ? s.fullTime : clock(clockMs, period)}</span>
        {!fullTime && <span className="gt-bug-live">{s.live}</span>}
      </div>

      {fullTime && scorecard ? (
        <AnswerKey meta={meta} card={scorecard} lang={lang} />
      ) : full && (
        <button key={full.cue_id} className="gt-full gt-wipe" style={{ ["--team" as string]: teamColor(meta, full.team_id) }} onClick={() => onOpen(full)}>
          <span className="gt-full-head">{full.headline}</span>
          {full.body && <span className="gt-full-body">{full.body}</span>}
        </button>
      )}

      {lower && (
        <button key={lower.cue_id} className="gt-lower gt-wipe" style={{ ["--team" as string]: teamColor(meta, lower.team_id) }} onClick={() => onOpen(lower)}>
          <span className="gt-lower-slab" />
          <span className="gt-lower-text">
            <strong>{lower.headline}</strong>
            {lower.body && <span>{lower.body}</span>}
          </span>
          <Stamp cue={lower} lang={lang} />
        </button>
      )}

      {panel && (
        <button key={panel.cue_id} className={`gt-panel gt-wipe ${panel.fallback ? "is-template" : "is-verified"}`}
          style={{ ["--team" as string]: teamColor(meta, panel.team_id) }} onClick={() => onOpen(panel)}>
          <span className="gt-panel-kicker">
            <span>{panel.template === "insight" ? s.kickerInsight : s.kickerShift}</span>
            <Stamp cue={panel} lang={lang} run={runFor(panel.moment_id)} />
          </span>
          <span className="gt-panel-head">{panel.headline}</span>
          {panel.body && <span className="gt-panel-body">{panel.body}</span>}
          {panel.metrics.filter((m) => m.before != null).slice(0, 1).map((m, i) => (
            <span className="gt-metric" key={i} dir="ltr">
              <span className="gt-metric-before">{fmtMetric(m.key, m.before!)}</span>
              <span className="gt-metric-arrow" aria-hidden="true" />
              <span className="gt-metric-after">{fmtMetric(m.key, m.value)}</span>
            </span>
          ))}
          <span className="gt-panel-why">{s.why}</span>
        </button>
      )}

      {caption && !full && (
        <button key={caption.cue_id} className="gt-caption" onClick={() => onOpen(caption)}>{caption.headline}</button>
      )}

      {ticker && (
        <button key={ticker.cue_id} className="gt-ticker" onClick={() => onOpen(ticker)}>
          <span className="gt-ticker-tag">{s.live}</span>
          <span className="gt-ticker-text">{ticker.headline}{ticker.body ? ` · ${ticker.body}` : ""}</span>
        </button>
      )}
    </div>
  );
}

/** The full-time graphic: every hidden change, and what the detector and the agents said about it. */
function AnswerKey({ meta, card, lang }: { meta: MatchMeta; card: Scorecard; lang: Lang }) {
  const s = strings(lang);
  const team = (id: string) => (id === meta.home.team_id ? meta.home : meta.away);
  const mark = (ok: boolean, said: string | null, caught: boolean) => (
    <span className={`gt-key-mark ${ok ? "ok" : caught ? "wrong" : "miss"}`}>
      {ok ? "✓" : "✗"} <em>{caught ? s.causes[said as keyof typeof s.causes] ?? said : s.missed}</em>
    </span>
  );
  return (
    <div className="gt-key gt-wipe">
      <div className="gt-key-head">
        <span className="gt-key-title">{s.answerKey}</span>
        <span className="gt-key-sub">{card.changes.length} {s.changes}</span>
      </div>
      {card.changes.length === 0 && <p className="gt-key-empty">{s.noChanges}</p>}
      {card.changes.length > 0 && (
        <table className="gt-key-table">
          <thead><tr><th /><th>{s.hiddenChange}</th><th>{s.detector}</th><th>{s.agentsCol}</th></tr></thead>
          <tbody>
            {card.changes.map((c, i) => {
              const t = team(c.team_id);
              return (
                <tr key={i} style={{ ["--team" as string]: t.colors[0] }}>
                  <td className="gt-key-min">{minute(c.clock_ms)}</td>
                  <td><Crest colors={t.colors} code={t.short_name} size={16} /> {t.short_name} · {s.labels[c.label as keyof typeof s.labels] ?? c.label}
                    <span className="gt-key-src">{c.source === "console" ? s.byYou : s.byScript}</span></td>
                  <td>{mark(c.rule.right_cause, c.rule.said, c.rule.caught)}</td>
                  <td>{mark(c.agents.right_cause, c.agents.said, c.agents.caught)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="gt-key-totals">
        {(["rule", "agents"] as const).map((k) => (
          <span key={k}>
            <strong>{k === "rule" ? s.detector : s.agentsCol}</strong>
            {card[k].right_cause}/{card[k].changes} {s.rightCause} · {card[k].false_alarms} {s.falseAlarms}
          </span>
        ))}
      </div>
    </div>
  );
}
