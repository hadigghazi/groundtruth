// How the agents are doing, live: time from detection to a verified card, approval
// rates, fallbacks and cost. The target line is the number to beat.

import type { AgentRun, AgentSummary, Lang } from "../api";
import { dir, fill, strings } from "../i18n";

const TARGET_MS = 5000;
const sec = (ms: number | null | undefined) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)} s`);
const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(100 * x)}%`);

export function Performance({ summary, runs, lang }: { summary: AgentSummary | null; runs: AgentRun[]; lang: Lang }) {
  const s = strings(lang);
  if (!summary || !summary.runs) return <p className="muted empty">{s.noRuns}</p>;
  const p50 = summary.verified_ms.p50;
  const top = Math.max(TARGET_MS * 2, ...runs.map((r) => r.total_ms));
  const stages: [string, number | null][] = [
    [s.lane.tactician, summary.stage_p50_ms.tactician_ms],
    [s.lane.judge, summary.stage_p50_ms.judge_ms],
    [s.lane.writer, summary.stage_p50_ms.writers_ms],
  ];
  const stageMax = Math.max(1, ...stages.map(([, v]) => v ?? 0));
  return (
    <div className="perf" dir={dir(lang)}>
      <div className="perf-hero">
        <span className={`perf-big ${p50 != null && p50 <= TARGET_MS ? "good" : "slow"}`}>{sec(p50)}</span>
        <span className="perf-label">{s.medianTime}</span>
        <span className="muted">{s.p95} {sec(summary.verified_ms.p95)} · {s.target}</span>
      </div>
      <div className="perf-tiles">
        <div><strong>{pct(summary.first_pass_rate)}</strong><span>{s.firstPass}</span></div>
        <div><strong>{pct(summary.fallback_rate)}</strong><span>{s.fallbacks}</span></div>
        <div><strong>{summary.tokens_per_run ?? "–"}</strong><span>{s.tokens}</span></div>
        <div><strong>{summary.runs}</strong><span>{s.runs}</span></div>
      </div>
      <div className="perf-runs" dir="ltr" aria-label={s.runs}>
        <span className="perf-target" style={{ bottom: `${(100 * TARGET_MS) / top}%` }}><em>5 s</em></span>
        {runs.map((r) => (
          <span key={r.moment_id} className={`perf-run ${r.outcome}`} title={`${r.minute + 1}' · ${s.outcome[r.outcome]} · ${sec(r.total_ms)}`}
            style={{ height: `${Math.max(4, (100 * (r.outcome === "verified" ? r.total_ms : top)) / top)}%` }} />
        ))}
      </div>
      <p className="perf-sub">{s.perStage}</p>
      <div className="perf-stages">
        {stages.map(([label, v]) => (
          <div key={label} className="perf-stage">
            <span>{label}</span>
            <span className="perf-stage-bar"><span style={{ width: `${(100 * (v ?? 0)) / stageMax}%` }} /></span>
            <span className="perf-stage-v">{sec(v)}</span>
          </div>
        ))}
      </div>
      <p className="muted">{fill(s.rejections, { c: summary.rejections.code, j: summary.rejections.judge })}</p>
    </div>
  );
}
