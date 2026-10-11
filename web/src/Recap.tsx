// "Catch me up": a recap card for one viewer, as of where their playhead is.

import { Button, Spinner } from "@fluentui/react-components";
import { Dismiss20Regular } from "@fluentui/react-icons";
import type { Lang, Mode } from "./api";
import { dir, fill, strings } from "./i18n";

export interface RecapCard { headline?: string; body?: string; minute?: number; fallback?: boolean; ms?: number; loading?: boolean }

export async function fetchRecap(matchId: string, lang: Lang, mode: Mode, asOf: number): Promise<RecapCard> {
  const r = await fetch(`/api/matches/${matchId}/catchup`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ lang, mode, as_of_t: Math.round(asOf) }),
  });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

export function Recap({ card, lang, onClose }: { card: RecapCard; lang: Lang; onClose: () => void }) {
  const s = strings(lang);
  return (
    <div className="gt-recap gt-wipe" dir={dir(lang)} role="dialog" aria-label={s.catchUp}>
      <div className="gt-recap-top">
        <span className="gt-panel-kicker">{s.catchUp}{card.minute != null ? ` · ${fill(s.asOf, { m: card.minute + 1 })}` : ""}</span>
        <Button size="small" appearance="subtle" icon={<Dismiss20Regular />} aria-label={s.close} onClick={onClose} />
      </div>
      {card.loading ? <Spinner size="small" label={s.thinking} /> : (
        <>
          <span className="gt-recap-head">{card.headline}</span>
          <span className="gt-recap-body">{card.body}</span>
          <span className={`gt-chip ${card.fallback ? "gt-chip-template" : "gt-chip-verified"}`}>
            {card.fallback ? s.template : s.verified}{card.ms ? ` · ${(card.ms / 1000).toFixed(1)} s` : ""}
          </span>
        </>
      )}
    </div>
  );
}
