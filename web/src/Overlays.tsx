import type { Cue, Lang, MatchMeta } from "./api";
import { clock, dir, strings } from "./i18n";

interface Props {
  meta: MatchMeta;
  cues: Cue[];
  score: [number, number];
  period: number;
  clockMs: number;
  lang: Lang;
  fullTime: boolean;
  onOpen: (cue: Cue) => void;
}

const fmt = (key: string, v: number | string) => {
  if (typeof v !== "number") return String(v);
  if (key.endsWith("_share")) return `${Math.round(v * 100)}%`;
  if (key.endsWith("_m")) return `${v.toFixed(1)} m`;
  if (key.endsWith("_s")) return `${v.toFixed(1)} s`;
  return Math.abs(v) < 10 ? v.toFixed(2) : v.toFixed(1);
};

function Badge({ cue, lang }: { cue: Cue; lang: Lang }) {
  const s = strings(lang);
  return <span className={`badge ${cue.fallback ? "badge-fallback" : "badge-verified"}`}>{cue.fallback ? s.fallback : s.verified}</span>;
}

/** Broadcast graphics drawn from timed cues. Every graphic opens its evidence on click. */
export function Overlays({ meta, cues, score, period, clockMs, lang, fullTime, onOpen }: Props) {
  const s = strings(lang);
  const by = (layer: Cue["layer"]) => cues.filter((c) => c.layer === layer).sort((a, b) => a.priority - b.priority)[0];
  const full = by("fullscreen"), lower = by("lower_third"), panel = by("stat_panel"), ticker = by("ticker");
  const h = meta.home, a = meta.away;
  return (
    <div className="overlays" dir={dir(lang)}>
      <div className="scorebug">
        <span className="sb-team" style={{ borderColor: h.colors[0] }}>{h.short_name.slice(0, 3).toUpperCase()}</span>
        <span className="sb-score">{score[0]}–{score[1]}</span>
        <span className="sb-team" style={{ borderColor: a.colors[0] }}>{a.short_name.slice(0, 3).toUpperCase()}</span>
        <span className="sb-clock">{fullTime ? s.fullTime : clock(clockMs, period)}</span>
      </div>

      {full && (
        <button className="ov-full" onClick={() => onOpen(full)}>
          <span className="ov-full-head">{full.headline}</span>
          {full.body && <span className="ov-full-body">{full.body}</span>}
        </button>
      )}

      {lower && (
        <button className="ov-lower" onClick={() => onOpen(lower)}>
          <span className="ov-lower-bar" style={{ background: lower.team_id === a.team_id ? a.colors[0] : h.colors[0] }} />
          <span className="ov-lower-text">
            <strong>{lower.headline}</strong>
            {lower.body && <span>{lower.body}</span>}
          </span>
          <Badge cue={lower} lang={lang} />
        </button>
      )}

      {panel && (
        <button className="ov-panel" onClick={() => onOpen(panel)}>
          <span className="ov-panel-head">{panel.headline}</span>
          {panel.body && <span className="ov-panel-body">{panel.body}</span>}
          {panel.metrics.filter((m) => m.before != null || typeof m.value === "number").slice(0, 2).map((m, i) => (
            <span className="ov-metric" key={i}>
              <span className="ov-metric-key">{m.key.replace(/_(m|s|adj_m)$/, "").replace(/_/g, " ")}</span>
              {m.before != null && <span className="ov-metric-before">{fmt(m.key, m.before)}</span>}
              {m.before != null && <span className="ov-arrow">{lang === "ar" ? "←" : "→"}</span>}
              <span className="ov-metric-after">{fmt(m.key, m.value)}</span>
            </span>
          ))}
          <Badge cue={panel} lang={lang} />
        </button>
      )}

      {ticker && (
        <button className="ov-ticker" onClick={() => onOpen(ticker)}>
          <span className="ov-ticker-tag">{s.live}</span>
          <span>{ticker.headline}{ticker.body ? ` · ${ticker.body}` : ""}</span>
        </button>
      )}
    </div>
  );
}
