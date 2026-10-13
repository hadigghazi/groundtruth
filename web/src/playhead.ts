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
        // Frames arrive in batches a few times a second. Holding a small cushion behind the newest
        // one, and easing the pace instead of snapping, keeps the picture moving between batches.
        const s = Math.max(buf.speed, 1);
        const cushion = 700 * s;
        const lag = target - t.current;
        if (t.current < first || (!buf.fullTime && lag > 12_000 * s)) {
          t.current = Math.max(first, target - cushion); // far behind (a background tab): rejoin
        } else {
          const ease = lag > 2 * cushion ? 1.15 : lag < cushion ? Math.max(0.4, lag / cushion) : 1;
          t.current = Math.min(target, t.current + rate * ease);
        }
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
