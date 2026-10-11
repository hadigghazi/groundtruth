// "Ask the match": questions answered by the desk agent from the match up to the viewer's
// minute, every number checked in code before it is shown.

import { Badge, Button, Input, Spinner } from "@fluentui/react-components";
import { Send20Regular } from "@fluentui/react-icons";
import { useState } from "react";
import type { Lang } from "../api";
import { dir, fill, strings } from "../i18n";

interface Turn { q: string; a?: string; minute?: number; ok?: boolean; outOfScope?: boolean; ms?: number; error?: string }

export function AskPanel({ matchId, getT, lang }: { matchId: string; getT: () => number; lang: Lang }) {
  const s = strings(lang);
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const ask = async (question: string) => {
    if (!question.trim() || busy) return;
    setBusy(true);
    setQ("");
    setTurns((t) => [{ q: question }, ...t]);
    let turn: Turn = { q: question };
    try {
      const r = await fetch(`/api/matches/${matchId}/ask`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ question, lang, as_of_t: Math.round(getT()) }),
      });
      if (!r.ok) turn = { ...turn, error: r.status === 503 ? s.deskOffline : (await r.json()).detail ?? String(r.status) };
      else {
        const d = await r.json();
        turn = d.answer ? { ...turn, a: d.answer, minute: d.minute, ok: d.verified, outOfScope: d.out_of_scope, ms: d.ms }
          : { ...turn, error: s.unverified, minute: d.minute };
      }
    } catch (e) {
      turn = { ...turn, error: String(e) };
    }
    setTurns((t) => [turn, ...t.slice(1)]);
    setBusy(false);
  };
  return (
    <div className="ask" dir={dir(lang)}>
      <p className="panel-title">{s.askTitle}</p>
      <p className="muted">{s.askHint}</p>
      <form className="ask-row" onSubmit={(e) => { e.preventDefault(); void ask(q); }}>
        <Input value={q} onChange={(_, d) => setQ(d.value)} placeholder={s.askPlaceholder} className="ask-input" maxLength={300} />
        <Button type="submit" appearance="primary" icon={<Send20Regular />} disabled={busy || !q.trim()} aria-label={s.askSend} />
      </form>
      <div className="ask-suggest">
        {s.suggestions.map((x) => <button key={x} onClick={() => void ask(x)} disabled={busy}>{x}</button>)}
      </div>
      <ol className="ask-turns">
        {turns.map((t, i) => (
          <li key={turns.length - i}>
            <p className="ask-q">{t.q}</p>
            {!t.a && !t.error && <Spinner size="tiny" label={s.thinking} />}
            {t.a && <p className="ask-a">{t.a}</p>}
            {t.error && <p className="ask-err">{t.error}</p>}
            {(t.a || t.error) && (
              <span className="ask-meta">
                {t.minute != null && <span>{fill(s.asOf, { m: t.minute + 1 })}</span>}
                {t.a && <Badge size="small" appearance="tint" color={t.outOfScope ? "warning" : "success"}>
                  {t.outOfScope ? s.outOfScope : s.verified}</Badge>}
                {t.ms != null && <span className="muted">{(t.ms / 1000).toFixed(1)} s</span>}
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
