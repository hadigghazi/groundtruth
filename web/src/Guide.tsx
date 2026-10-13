// What is happening now, in one line, and a short how-to-watch. The product explains itself as
// the match goes: before the first flag, while the agents work on one, and at full time.

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, DialogTrigger, Tooltip,
} from "@fluentui/react-components";
import { ArrowRight16Regular, Question20Regular } from "@fluentui/react-icons";
import { useEffect, useState } from "react";
import type { Lang, MatchMeta } from "./api";
import { fill, minute, strings } from "./i18n";
import type { MatchBuffer } from "./match";

type Tab = "agents" | "score";

/** The tactical shifts the viewer has reached, each with whether its verified card has arrived. */
export function shiftsSeen(buf: MatchBuffer, t: number) {
  return buf.moments
    .filter((m) => m.kind === "tactical_shift" && m.t <= t)
    .map((m) => ({ m, verified: buf.cues.some((c) => c.moment_id === m.moment_id && c.verified) }));
}

export function Guide({ buf, meta, t, lang, onTab }: {
  buf: MatchBuffer; meta: MatchMeta; t: number; lang: Lang; onTab: (tab: Tab) => void;
}) {
  const s = strings(lang);
  const g = s.guide;
  const seen = shiftsSeen(buf, t);
  const team = (id?: string | null) => (id === meta.home.team_id ? meta.home.name : id === meta.away.team_id ? meta.away.name : "");
  let text: string, action: { label: string; tab: Tab } | null = null;
  if (buf.fullTime && t >= buf.liveT - 1) {
    text = g.fullTime;
    action = { label: g.openScore, tab: "score" };
  } else if (!seen.length) {
    text = g.watching;
  } else {
    const last = seen[seen.length - 1];
    const cause = String(last.m.metrics.rule_cause ?? "unclear") as keyof typeof s.causes;
    text = fill(last.verified ? g.flaggedVerified : g.flaggedWorking, {
      m: minute(last.m.clock_ms), team: team(last.m.team_id),
      cause: (s.causes[cause] ?? s.causes.unclear).toLowerCase(), n: seen.length,
    });
    action = { label: g.seeAgents, tab: "agents" };
  }
  return (
    <div className="guide" role="status" aria-live="polite">
      <span className={`guide-dot ${seen.length && !seen[seen.length - 1].verified && !buf.fullTime ? "busy" : ""}`} />
      <span className="guide-text">{text}</span>
      {action && (
        <Button size="small" appearance="transparent" icon={<ArrowRight16Regular />} iconPosition="after"
          onClick={() => onTab(action!.tab)}>{action.label}</Button>
      )}
    </div>
  );
}

const SEEN_KEY = "gt-howto-seen";

/** Opens by itself on a viewer's first match; the ? button brings it back. */
export function HowTo({ lang }: { lang: Lang }) {
  const s = strings(lang);
  const g = s.guide;
  const [open, setOpen] = useState(false);
  useEffect(() => {
    try { if (!localStorage.getItem(SEEN_KEY)) setOpen(true); } catch { /* private mode: no auto-open */ }
  }, []);
  const close = () => {
    setOpen(false);
    try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* private mode */ }
  };
  return (
    <Dialog open={open} onOpenChange={(_, d) => (d.open ? setOpen(true) : close())}>
      <DialogTrigger disableButtonEnhancement>
        <Tooltip content={g.howTitle} relationship="label">
          <Button size="small" appearance="subtle" icon={<Question20Regular />} aria-label={g.howTitle} />
        </Tooltip>
      </DialogTrigger>
      <DialogSurface className="howto">
        <DialogBody>
          <DialogTitle>{g.howTitle}</DialogTitle>
          <DialogContent>
            <ol className="howto-steps">
              {g.how.map(([title, body], i) => (
                <li key={i}><span className="step-n">{i + 1}</span><div><strong>{title}</strong><p>{body}</p></div></li>
              ))}
            </ol>
            <p className="muted howto-tip">{g.tip}</p>
          </DialogContent>
          <DialogActions>
            <Button appearance="primary" onClick={close}>{g.gotIt}</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
