// The full-time scorecard in the side panel: totals for the detector and the agents.

import type { Lang, MatchMeta, Scorecard } from "../api";
import { Crest } from "../Crest";
import { dir, fill, minute, strings } from "../i18n";

export function ScorecardPanel({ meta, card, lang }: { meta: MatchMeta; card: Scorecard | null; lang: Lang }) {
  const s = strings(lang);
  if (!card) return <p className="muted empty">{s.revealLater}</p>;
  const team = (id: string) => (id === meta.home.team_id ? meta.home : meta.away);
  const cause = (c: string | null) => (c ? s.causes[c as keyof typeof s.causes] ?? c : s.missed);
  return (
    <div className="scorecard" dir={dir(lang)}>
      <div className="score-totals">
        {(["rule", "agents"] as const).map((k) => (
          <div key={k} className={`score-side ${k}`}>
            <span className="score-who">{k === "rule" ? s.detector : s.agentsCol}</span>
            <span className="score-big">{card[k].right_cause}<em>/{card[k].changes}</em></span>
            <span>{s.rightCause}</span>
            <span className="muted">{card[k].caught} {s.caught} · {card[k].false_alarms} {s.falseAlarms}</span>
          </div>
        ))}
      </div>
      {card.changes.length === 0 && <p className="muted">{s.noChanges}</p>}
      <ul className="score-rows">
        {card.changes.map((c, i) => {
          const t = team(c.team_id);
          return (
            <li key={i}>
              <span className="mono">{minute(c.clock_ms)}</span>
              <Crest colors={t.colors} code={t.short_name} size={16} />
              <span className="score-change">{s.labels[c.label as keyof typeof s.labels] ?? c.label}
                <span className="muted"> · {c.source === "console" ? s.byYou : s.byScript}</span></span>
              {(["rule", "agents"] as const).map((k) => (
                <span key={k} className={`score-mark ${c[k].right_cause ? "ok" : c[k].caught ? "wrong" : "miss"}`}
                  title={c[k].delay_min != null ? fill(s.later, { m: c[k].delay_min! }) : ""}>
                  {c[k].right_cause ? "✓" : "✗"} {cause(c[k].said)}
                </span>
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
