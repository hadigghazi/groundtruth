// The insight board as a timeline per flagged moment: every agent's work, when it ran,
// how long it took, and every verdict. The judge and the three writers run side by side.

import { Badge } from "@fluentui/react-components";
import type { BoardEntry, Lang, MatchMeta, Moment } from "../api";
import { Crest } from "../Crest";
import { dir, minute, strings } from "../i18n";

type Row = { key: string; label: string; bars: { start: number; end: number; ok: boolean; attempt: number; title: string }[]; ticks: { at: number; ok: boolean; title: string }[] };

function lanes(entries: BoardEntry[], langs: string[], s: ReturnType<typeof strings>): { rows: Row[]; t0: number; span: number } {
  const t0 = entries[0].at_ms;
  const rows: Record<string, Row> = {
    tactician: { key: "tactician", label: s.lane.tactician, bars: [], ticks: [] },
    checker: { key: "checker", label: s.lane.checker, bars: [], ticks: [] },
    judge: { key: "judge", label: s.lane.judge, bars: [], ticks: [] },
    challenger: { key: "challenger", label: s.lane.challenger, bars: [], ticks: [] },
    ...Object.fromEntries(langs.map((l) => [`written_${l}`, { key: `written_${l}`, label: `${s.lane.writer} ${l.toUpperCase()}`, bars: [], ticks: [] }])),
    gate: { key: "gate", label: s.lane.gate, bars: [], ticks: [] },
  };
  for (const e of entries) {
    const at = e.at_ms - t0, ms = Number(e.ms ?? 0), attempt = Number(e.attempt ?? 1);
    if (e.stage === "drafted") rows.tactician.bars.push({ start: at - ms, end: at, ok: !!e.ok, attempt, title: `${ms} ms` });
    else if (e.stage === "judged") rows.judge.bars.push({ start: at - ms, end: at, ok: !!e.ok, attempt, title: `${ms} ms` });
    else if (e.stage === "challenged") rows.challenger.bars.push({ start: at - ms, end: at, ok: !!e.ok, attempt, title: `${ms} ms` });
    else if (e.stage.startsWith("written_") && rows[e.stage]) {
      const failed = Array.isArray(e.failed) && (e.failed as string[]).length > 0;
      rows[e.stage].bars.push({ start: at - ms, end: at, ok: !failed, attempt, title: `${ms} ms` });
    } else if (e.stage === "checked") rows.checker.ticks.push({ at, ok: true, title: "pass" });
    else if (e.stage === "rejected" && e.by === "code") rows.checker.ticks.push({ at, ok: false, title: "rejected" });
    else if (e.stage === "rejected" && e.by === "judge") rows.gate.ticks.push({ at, ok: false, title: "rejected" });
    else if (e.stage === "verified") rows.gate.ticks.push({ at, ok: true, title: "verified" });
  }
  const span = Math.max(1, ...entries.map((e) => e.at_ms - t0));
  return { rows: Object.values(rows).filter((r) => r.bars.length || r.ticks.length), t0, span };
}

function fallbackKind(e: BoardEntry): "outage" | "guarded" | "fallback" {
  const why = String((e.reasons as string[] | undefined)?.[0] ?? "");
  return why.includes("outage") ? "outage" : why.startsWith("usage") ? "guarded" : "fallback";
}

const OUTCOME_COLOR = { verified: "success", fallback: "warning", timeout: "warning", error: "danger", guarded: "informative", outage: "danger" } as const;

export function MomentLanes({ entries, langs, lang }: { entries: BoardEntry[]; langs: string[]; lang: Lang }) {
  const s = strings(lang);
  const { rows, span } = lanes(entries, langs, s);
  if (!rows.length) return null;
  const pct = (ms: number) => `${(100 * Math.max(0, ms)) / span}%`;
  return (
    <div className="lanes" dir="ltr">
      {rows.map((r) => (
        <div className="lane" key={r.key}>
          <span className="lane-label">{r.label}</span>
          <span className="lane-track">
            {r.bars.map((b, i) => (
              <span key={i} className={`lane-bar ${b.ok ? "ok" : "bad"} try-${b.attempt}`} title={b.title}
                style={{ insetInlineStart: pct(b.start), width: `max(3px, ${pct(b.end - b.start)})` }} />
            ))}
            {r.ticks.map((t, i) => (
              <span key={i} className={`lane-tick ${t.ok ? "ok" : "bad"}`} title={t.title} style={{ insetInlineStart: pct(t.at) }} />
            ))}
          </span>
        </div>
      ))}
      <div className="lane lane-axis"><span className="lane-label" /><span className="lane-track"><span>0</span><span>{(span / 1000).toFixed(1)} s</span></span></div>
    </div>
  );
}

export function AgentTrace({ board, meta, moments, langs, lang }: {
  board: BoardEntry[]; meta: MatchMeta; moments: Moment[]; langs: string[]; lang: Lang;
}) {
  const s = strings(lang);
  const groups = new Map<string, BoardEntry[]>();
  for (const e of board) { const g = groups.get(e.moment_id) ?? []; g.push(e); groups.set(e.moment_id, g); }
  const list = [...groups.entries()].reverse();
  if (!list.length) return <p className="muted empty">{s.noTrace}</p>;
  const team = (id: unknown) => (id === meta.away.team_id ? meta.away : id === meta.home.team_id ? meta.home : null);
  const cause = (c: unknown) => (typeof c === "string" ? s.causes[c as keyof typeof s.causes] ?? c : "–");
  return (
    <div className="trace" dir={dir(lang)}>
      {list.map(([id, entries]) => {
        const detected = entries.find((e) => e.stage === "detected") ?? entries[0];
        const verified = entries.find((e) => e.stage === "verified");
        const upgraded = entries.find((e) => e.stage === "upgraded");
        const fellBack = entries.find((e) => e.stage === "fallback");
        const draft = [...entries].reverse().find((e) => e.stage === "drafted" && e.ok);
        const m = moments.find((x) => x.moment_id === id);
        const flagged = team(detected.team_id), named = team(verified?.team_id);
        const outcome = upgraded ? "verified" : fellBack ? fallbackKind(fellBack) : null;
        const rejections = entries.filter((e) => e.stage === "rejected");
        return (
          <section className="trace-moment" key={id}>
            <header>
              <span className="trace-min">{m ? minute(m.clock_ms) : ""}</span>
              {flagged && <Crest colors={flagged.colors} code={flagged.short_name} size={16} />}
              <span className="trace-what">
                <span className="muted">{s.detector}</span> {cause(detected.rule_cause)}
                {verified && <> <span className="arrow">→</span> <span className="muted">{s.agentsCol}</span> {cause(verified.cause)}{named && named !== flagged ? ` · ${named.short_name}` : ""}</>}
              </span>
              {outcome ? (
                <Badge size="small" appearance="tint" color={OUTCOME_COLOR[outcome]}>
                  {s.outcome[outcome]}{upgraded ? ` · ${(Number(upgraded.ms) / 1000).toFixed(1)} s` : ""}
                </Badge>
              ) : <Badge size="small" appearance="outline" color="brand" className="working">…</Badge>}
            </header>
            <MomentLanes entries={entries} langs={langs} lang={lang} />
            {typeof draft?.headline === "string" && <p className="trace-quote">“{draft.headline}”</p>}
            {entries.filter((e) => e.stage === "challenged" && e.alt_cause && (e.alt_cause !== verified?.cause || e.alt_team !== verified?.team_id)).map((e, i) => (
              <p key={i} className={`trace-challenge ${e.ok ? "held" : "won"}`}>
                <strong>{s.lane.challenger}</strong> {team(e.alt_team)?.short_name ?? ""} · {cause(e.alt_cause)}
                <span className="muted"> ({String(e.alt_score ?? "–")} v {String(e.score ?? "–")}) · {e.ok ? s.challengeHeld : s.challengeWon}</span>
              </p>
            ))}
            {rejections.map((r, i) => (
              <div className="trace-reject" key={i}>
                <strong>{r.by === "judge" ? s.lane.judge : s.lane.checker}</strong>
                <ul>{((r.problems as string[]) ?? []).map((p, j) => <li key={j}>{p}</li>)}</ul>
              </div>
            ))}
            {fellBack && !upgraded && Array.isArray(fellBack.reasons) && (
              <p className="trace-note">{(fellBack.reasons as string[]).join(" · ")}</p>
            )}
          </section>
        );
      })}
    </div>
  );
}
