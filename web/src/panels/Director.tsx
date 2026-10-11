// The director console: change a team's hidden tactics live, pull the plug on the models,
// and open the answer key at full time.

import { Button, Switch } from "@fluentui/react-components";
import { useState } from "react";
import { api } from "../api";
import type { Lang, MatchMeta } from "../api";
import { Crest } from "../Crest";
import { clock, dir, strings } from "../i18n";

const LABELS = ["high_press", "low_block", "go_direct", "slow_down", "push_wide"] as const;

export function Director({ matchId, meta, log, fullTime, outage, lang }: {
  matchId: string; meta: MatchMeta; log: { team_id: string; label: string; clock_ms: number; period: number }[];
  fullTime: boolean; outage: boolean; lang: Lang;
}) {
  const s = strings(lang);
  const [busy, setBusy] = useState<string | null>(null);
  const [truth, setTruth] = useState<{ label: string; team_id: string; period: number; clock_ms: number; source: string }[] | null>(null);
  const send = async (team: string, label: string) => {
    setBusy(`${team}:${label}`);
    try { await api.console(matchId, team, label); } finally { setBusy(null); }
  };
  const name = (id: string) => (id === meta.home.team_id ? meta.home.short_name : meta.away.short_name);
  return (
    <div className="director" dir={dir(lang)}>
      <p className="panel-title">{s.console}</p>
      <p className="muted">{s.consoleNote}</p>
      {[meta.home, meta.away].map((team) => (
        <div className="director-team" key={team.team_id} style={{ ["--team" as string]: team.colors[0] }}>
          <span className="director-name"><Crest colors={team.colors} code={team.short_name} size={20} />{team.name}</span>
          <div className="director-buttons">
            {LABELS.map((l) => (
              <Button key={l} size="small" appearance="outline" disabled={!!busy || fullTime}
                onClick={() => send(team.team_id, l)}>{s.labels[l]}</Button>
            ))}
          </div>
        </div>
      ))}
      {log.length > 0 && (
        <>
          <p className="panel-sub">{s.yourChanges}</p>
          <ul className="director-log">
            {log.map((c, i) => <li key={i}><span className="mono">{clock(c.clock_ms, c.period)}</span> {name(c.team_id)} · {s.labels[c.label as keyof typeof s.labels] ?? c.label}</li>)}
          </ul>
        </>
      )}
      <div className="director-outage">
        <Switch checked={outage} disabled={fullTime} label={s.outage}
          onChange={(_, d) => { void api.outage(matchId, d.checked); }} />
        <span className="muted">{s.outageNote}</span>
      </div>
      <div className="director-key">
        <p className="panel-sub">{s.revealed}</p>
        {truth ? (
          <ul className="director-log">{truth.map((t, i) => (
            <li key={i}><span className="mono">{clock(t.clock_ms, t.period)}</span> {name(t.team_id)} · {s.labels[t.label as keyof typeof s.labels] ?? t.label}
              <span className="muted"> ({t.source === "console" ? s.byYou : s.byScript})</span></li>
          ))}</ul>
        ) : (
          <Button appearance="primary" disabled={!fullTime} onClick={async () => setTruth(await api.truth(matchId))}>
            {fullTime ? s.reveal : s.revealLater}
          </Button>
        )}
      </div>
    </div>
  );
}
