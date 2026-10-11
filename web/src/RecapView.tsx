// The full-time recap for one viewer: the story in their language and profile, the player of the
// match with the numbers behind the pick, and the highlights rundown, each clip a click away.

import type { Lang, MatchMeta, Mode, RecapPackage } from "./api";
import { Crest } from "./Crest";
import { strings } from "./i18n";

export function RecapView({ meta, recap, lang, mode, onClip }: {
  meta: MatchMeta; recap: RecapPackage; lang: Lang; mode: Mode; onClip: (t: number) => void;
}) {
  const s = strings(lang);
  const text = recap.texts[lang]?.[mode] ?? recap.texts.en[mode];
  const potm = recap.player_of_match;
  const team = (id?: string | null) => (id === meta.away.team_id ? meta.away : meta.home);
  return (
    <div className="gt-key gt-recapview gt-wipe">
      <div className="gt-key-head">
        <span className="gt-key-title">{s.recapTitle}</span>
        <span className={`gt-chip ${recap.written_by[lang] === "agents" ? "gt-chip-verified" : "gt-chip-template"}`}>
          {recap.written_by[lang] === "agents" ? s.byAgents : s.byCode}
        </span>
      </div>
      <div className="rv-grid">
        <div className="rv-story">
          <p className="rv-head">{text.headline}</p>
          <p className="rv-body">{text.body}</p>
          <div className="rv-social">
            <span className="gt-key-src">{s.socialPost}</span>
            <p>{text.social}</p>
            <button onClick={() => void navigator.clipboard?.writeText(text.social)}>{s.copy}</button>
          </div>
        </div>
        <div className="rv-side">
          {potm && (
            <div className="rv-potm" style={{ ["--team" as string]: team(potm.team_id).colors[0] }}>
              <span className="gt-key-src">{s.playerOfMatch}</span>
              <span className="rv-potm-name"><Crest colors={team(potm.team_id).colors} code={team(potm.team_id).short_name} size={18} /> {potm.player}</span>
              <span className="rv-potm-stats" dir="ltr">
                <b>{potm.goals}</b> G · <b>{potm.xg.toFixed(2)}</b> xG · <b>{potm.km.toFixed(1)}</b> km · <b>{potm.top_kmh.toFixed(1)}</b> km/h
              </span>
            </div>
          )}
          <span className="gt-key-src">{s.highlightsTitle}</span>
          <ol className="rv-clips">
            {recap.highlights.map((c) => (
              <li key={c.moment_id}>
                <button onClick={() => onClip(c.t_in)} style={{ ["--team" as string]: team(c.team_id).colors[0] }}>
                  <span className="rv-min">{c.minute}'</span>
                  <span className="rv-label">{c.label[lang] ?? c.label.en}</span>
                  <span className="rv-play" aria-hidden="true">▶</span>
                </button>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
