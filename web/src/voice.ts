// Spoken commentary for one viewer: goals, verified analysis and the big moments, in the
// viewer's language. Uses the server's neural voices when it has them (Azure AI Speech),
// otherwise the browser's own. Never queues behind itself: late speech is worse than none.

import { useEffect, useRef } from "react";
import type { Cue, Lang, Mode } from "./api";

const LOCALE: Record<Lang, string> = { en: "en-GB", es: "es-ES", ar: "ar-SA" };

let serverVoice: boolean | null = null;
async function hasServerVoice(): Promise<boolean> {
  if (serverVoice !== null) return serverVoice;
  try { serverVoice = !!(await (await fetch("/api/health")).json()).tts; } catch { serverVoice = false; }
  return serverVoice;
}

const speakable = (c: Cue) =>
  c.layer === "fullscreen" || (c.layer === "stat_panel" && !c.fallback) || (c.layer === "lower_third" && c.priority <= 2);

let playing: HTMLAudioElement | null = null;

async function say(text: string, lang: Lang, mode: Mode, urgent: boolean) {
  if (await hasServerVoice()) {
    if (playing && !playing.paused && !urgent) return;
    playing?.pause();
    playing = new Audio(`/api/tts?lang=${lang}&mode=${mode}&text=${encodeURIComponent(text)}`);
    void playing.play().catch(() => undefined);
    return;
  }
  if (typeof speechSynthesis === "undefined") return;
  if (speechSynthesis.speaking && !urgent) return;
  if (urgent) speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = LOCALE[lang];
  const v = speechSynthesis.getVoices().find((x) => x.lang.toLowerCase().startsWith(lang));
  if (v) u.voice = v;
  u.rate = 1.05;
  speechSynthesis.speak(u);
}

export function useVoice(cues: Cue[], lang: Lang, mode: Mode, on: boolean) {
  const said = useRef(new Set<string>());
  useEffect(() => {
    for (const c of cues) {
      if (said.current.has(c.cue_id)) continue;
      said.current.add(c.cue_id); // seen while muted counts as said: switching on never replays a backlog
      if (on && speakable(c)) void say([c.headline, c.body].filter(Boolean).join(". "), lang, mode, c.layer === "fullscreen");
    }
  }, [cues, lang, mode, on]);
  useEffect(() => () => { if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel(); playing?.pause(); }, []);
}
