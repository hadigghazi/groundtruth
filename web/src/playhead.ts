// The viewer's playhead: follows the delayed live edge, or pauses, or replays from any point
// already received. It can never pass the delayed edge, so rewinding never spoils anything.

import { useCallback, useEffect, useRef, useState } from "react";
import type { MatchBuffer } from "./match";

export type PlayMode = "live" | "paused" | "replay";

export interface Playhead {
  t: React.MutableRefObject<number>;
  slow: number; // a 4 Hz copy for React-rendered overlays
  mode: PlayMode;
  edge: number; // the delayed live edge, at the same 4 Hz
  pause: () => void;
  play: () => void;
  seek: (t: number) => void;
  live: () => void;
}

export function usePlayhead(buf: MatchBuffer): Playhead {
  const t = useRef(0);
  const mode = useRef<PlayMode>("live");
  const [slow, setSlow] = useState(0);
  const [edge, setEdge] = useState(0);
  const [shownMode, setShownMode] = useState<PlayMode>("live");
  const setMode = useCallback((m: PlayMode) => { mode.current = m; setShownMode(m); }, []);

  useEffect(() => {
    let raf = 0, last = performance.now(), lastSlow = 0;
    const step = (now: number) => {
      raf = requestAnimationFrame(step);
      // after the final whistle the delay plays out to the end instead of holding it back
      const target = buf.fullTime ? buf.liveT : Math.max(0, buf.liveT - buf.delayMs);
      const dt = now - last;
      last = now;
      const rate = dt * Math.max(buf.speed, 1);
      const first = buf.frames.length ? buf.frames[0].t : 0;
      if (mode.current === "live") {
        const snap = t.current < first || (!buf.fullTime && Math.abs(target - t.current) > 4000);
        t.current = snap ? target : Math.min(target, t.current + rate);
      } else if (mode.current === "replay") {
        t.current = Math.min(target, t.current + rate);
        if (t.current >= target) setMode("live");
      }
      if (now - lastSlow > 250) { lastSlow = now; setSlow(t.current); setEdge(target); }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [buf, setMode]);

  const first = () => (buf.frames.length ? buf.frames[0].t : 0);
  return {
    t, slow, edge, mode: shownMode,
    pause: () => setMode("paused"),
    play: () => setMode("replay"),
    seek: (to: number) => {
      t.current = Math.max(first(), Math.min(to, buf.fullTime ? buf.liveT : Math.max(0, buf.liveT - buf.delayMs)));
      setSlow(t.current);
      if (mode.current === "live") setMode("replay");
    },
    live: () => setMode("live"),
  };
}
