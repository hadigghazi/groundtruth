// "Why this card?": the path that produced it, the numbers it rests on, the events behind
// them, and for an agent card, the run that wrote it.

import { Button } from "@fluentui/react-components";
import { Dismiss20Regular } from "@fluentui/react-icons";
import type { BoardEntry, Cue, Lang, MatchEvent } from "../api";
import { fmtMetric } from "../Overlays";
import { clock, dir, strings } from "../i18n";
import { MomentLanes } from "./AgentTrace";

export function Evidence({ cue, events, board, langs, lang, onClose }: {
  cue: Cue; events: MatchEvent[]; board: BoardEntry[]; langs: string[]; lang: Lang; onClose: () => void;
}) {
  const s = strings(lang);
  const run = cue.moment_id ? board.filter((e) => e.moment_id === cue.moment_id) : [];
  return (
    <aside className="evidence" dir={dir(lang)} aria-label={s.evidence}>
      <header>
        <span className="panel-title">{s.evidence}</span>
        <Button size="small" appearance="subtle" icon={<Dismiss20Regular />} aria-label={s.close} onClick={onClose} />
      </header>
      <p className="ev-head">{cue.headline}</p>
      {cue.body && <p className="ev-body">{cue.body}</p>}
      <ol className="ev-path" dir="ltr">
        {cue.path.map((p, i) => <li key={i} className={p === "template" ? "tmpl" : ""}>{p.replace(/_/g, " ")}</li>)}
      </ol>
      {run.length > 0 && <MomentLanes entries={run} langs={langs} lang={lang} />}
      {cue.metrics.length > 0 && (
        <table className="ev-metrics" dir="ltr"><tbody>{cue.metrics.map((m, i) => (
          <tr key={i}><td className="mono">{m.key}</td><td>{m.team_id ?? ""}</td>
            <td>{m.before != null ? `${fmtMetric(m.key, m.before)} → ` : ""}{fmtMetric(m.key, m.value)}</td></tr>
        ))}</tbody></table>
      )}
      <p className="panel-sub">{s.events}</p>
      <ul className="ev-list" dir="ltr">
        {events.map((e) => (
          <li key={e.event_id}><span className="mono">{clock(e.clock_ms, e.period)}</span> {e.type}{e.outcome ? ` · ${e.outcome}` : ""}
            <span className="muted"> {e.player_id ?? ""}</span></li>
        ))}
        {!events.length && <li className="muted">—</li>}
      </ul>
    </aside>
  );
}
