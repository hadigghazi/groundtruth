"""The screen budget: what is on screen, where, and for how long.

Each (profile, language) feed has its own schedule. A layer holds a fixed
number of cues at once. A more important cue cuts a less important one
short; otherwise it waits for the layer to clear, and it is dropped if it
would wait longer than `max_wait_ms` (a stale overlay is worse than none).
"""

from __future__ import annotations

from collections import defaultdict

from ..schema import Cue

CAPACITY = {"fullscreen": 1, "lower_third": 1, "stat_panel": 1, "ticker": 1, "player_tag": 2, "pitch_overlay": 1}


class Producer:
    def __init__(self, max_wait_ms: int = 20_000):
        self.max_wait_ms = max_wait_ms
        self.schedule: dict[tuple[str, str], list[Cue]] = defaultdict(list)
        self.dropped: list[Cue] = []

    def place(self, cue: Cue) -> Cue | None:
        """Schedule a cue; returns it (possibly moved later) or None if it was dropped."""
        feed = self.schedule[(cue.profile, cue.lang)]
        on_layer = [c for c in feed if c.layer == cue.layer and c.t_out > cue.t_in]
        if len(on_layer) >= CAPACITY[cue.layer]:
            weaker = [c for c in on_layer if c.priority > cue.priority]
            if weaker:
                cut = max(weaker, key=lambda c: (c.priority, c.t_in))
                shortened = cut.t_out - cue.t_in
                cut.t_out = cue.t_in
                cut.end = cut.end.model_copy(update={"clock_ms": cut.end.clock_ms - shortened})
            else:
                free_at = sorted(c.t_out for c in on_layer)[len(on_layer) - CAPACITY[cue.layer]]
                wait = free_at - cue.t_in
                if wait > self.max_wait_ms:
                    self.dropped.append(cue)
                    return None
                cue = cue.model_copy(update={
                    "t_in": cue.t_in + wait, "t_out": cue.t_out + wait,
                    "start": cue.start.model_copy(update={"clock_ms": cue.start.clock_ms + wait}),
                    "end": cue.end.model_copy(update={"clock_ms": cue.end.clock_ms + wait}),
                })
        feed.append(cue)
        return cue

    def sheet(self, profile: str = "all", lang: str = "en") -> list[Cue]:
        return sorted(self.schedule[(profile, lang)], key=lambda c: c.t_in)
