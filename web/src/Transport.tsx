// Pause, rewind and a timeline of what has happened so far, marked with goals, chances,
// cards and the tactical shifts the agents explained. Nothing past the delayed edge exists here.

import { Button, Tooltip } from "@fluentui/react-components";
import { ArrowCounterclockwise20Regular, Pause20Filled, Play20Filled } from "@fluentui/react-icons";
import type { Lang } from "./api";
import { fill, minute, strings } from "./i18n";
import type { MatchBuffer } from "./match";
import type { Playhead } from "./playhead";

const MARKED: Record<string, string> = { goal: "goal", tactical_shift: "shift", big_chance: "chance", card: "card" };

export function Transport({ buf, ph, lang }: { buf: MatchBuffer; ph: Playhead; lang: Lang }) {
  const s = strings(lang);
  const start = buf.frames.length ? buf.frames[0].t : 0;
  const end = Math.max(start + 1, ph.edge);
  const at = (t: number) => `${(100 * (Math.min(Math.max(t, start), end) - start)) / (end - start)}%`;
  const behind = Math.max(0, ph.edge - ph.slow);
  const marks = buf.moments.filter((m) => MARKED[m.kind] && m.t <= ph.edge && m.t >= start);
  return (
    <div className="transport" dir="ltr">
      <Tooltip content={ph.mode === "paused" ? s.play : s.pause} relationship="label">
        <Button size="small" appearance="subtle" icon={ph.mode === "paused" ? <Play20Filled /> : <Pause20Filled />}
          onClick={() => (ph.mode === "paused" ? ph.play() : ph.pause())} />
      </Tooltip>
      <Tooltip content={s.back30} relationship="label">
        <Button size="small" appearance="subtle" icon={<ArrowCounterclockwise20Regular />} onClick={() => ph.seek(ph.t.current - 30_000)} />
      </Tooltip>
      <div className="tl" role="slider" aria-label={s.timeline} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round((100 * (ph.slow - start)) / (end - start))} tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") ph.seek(ph.t.current - 10_000);
          if (e.key === "ArrowRight") ph.seek(ph.t.current + 10_000);
        }}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          ph.seek(start + ((e.clientX - r.left) / r.width) * (end - start));
        }}>
        <span className="tl-fill" style={{ width: at(ph.slow) }} />
        {marks.map((m) => (
          <span key={m.moment_id} className={`tl-mark ${MARKED[m.kind]}`} style={{ left: at(m.t) }} title={`${minute(m.clock_ms)} ${m.headline}`} />
        ))}
        <span className="tl-head" style={{ left: at(ph.slow) }} />
      </div>
      <Button size="small" appearance={ph.mode === "live" ? "primary" : "outline"} onClick={ph.live} className="tl-live">
        {ph.mode === "live" ? s.live : fill(s.behind, { m: `${Math.floor(behind / 60000)}:${String(Math.floor((behind % 60000) / 1000)).padStart(2, "0")}` })}
      </Button>
    </div>
  );
}
