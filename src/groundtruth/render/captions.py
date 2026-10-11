"""Play-by-play captions: one line per notable event, written by code the moment it happens.

The raw events become a running caption in every language with no model in the
loop, so the broadcast always has words on screen, however slow the agents are.
Each caption names its event as evidence. The newest caption always replaces the
one before (see Producer.REPLACE_LAYERS).
"""

from __future__ import annotations

from ..schema import Cue, CueTime, Event
from ..stats.engine import StatsEngine
from .templates import LANGS

LINES: dict[str, dict[str, str]] = {
    "shot_saved": {"en": "{player} shoots. Saved by the keeper", "es": "Dispara {player}. Para el portero",
                   "ar": "{player} يسدد ويتصدى الحارس"},
    "shot_off_target": {"en": "{player} shoots wide", "es": "{player} dispara fuera", "ar": "{player} يسدد خارج المرمى"},
    "shot_blocked": {"en": "{player}'s shot is blocked", "es": "Bloquean el disparo de {player}", "ar": "صدّ تسديدة {player}"},
    "corner": {"en": "Corner to {team}", "es": "Córner para {team}", "ar": "ركلة ركنية لـ{team}"},
    "free_kick": {"en": "Free kick to {team}", "es": "Falta a favor de {team}", "ar": "ركلة حرة لـ{team}"},
    "penalty": {"en": "Penalty to {team}!", "es": "¡Penalti para {team}!", "ar": "ركلة جزاء لـ{team}!"},
    "foul": {"en": "Foul by {player}", "es": "Falta de {player}", "ar": "خطأ من {player}"},
    "kickoff_1": {"en": "Kick-off at {venue}", "es": "Arranca el partido en {venue}", "ar": "انطلاق المباراة في {venue}"},
    "kickoff_2": {"en": "The second half is under way", "es": "Comienza la segunda parte", "ar": "انطلاق الشوط الثاني"},
    "end_1": {"en": "Half-time: {score}", "es": "Descanso: {score}", "ar": "نهاية الشوط الأول: {score}"},
    "end_2": {"en": "Full time: {score}", "es": "Final: {score}", "ar": "نهاية المباراة: {score}"},
}
SECONDS = 5
PRIORITY = 6


class CaptionWriter:
    def __init__(self, stats: StatsEngine, langs: tuple[str, ...] = LANGS):
        self.stats, self.langs = stats, langs
        self._n = 0

    def _line(self, ev: Event) -> str | None:
        if ev.type == "shot" and ev.outcome in ("saved", "off_target", "blocked"):
            return f"shot_{ev.outcome}"
        if ev.type == "restart" and ev.restart in ("corner", "free_kick", "penalty"):
            return ev.restart
        if ev.type == "foul":
            return "foul"
        if ev.type == "period_start":
            return f"kickoff_{min(ev.period, 2)}"
        if ev.type == "period_end":
            return f"end_{min(ev.period, 2)}"
        return None

    def from_event(self, ev: Event) -> list[Cue]:
        key = self._line(ev)
        if key is None:
            return []
        s, meta = self.stats, self.stats.meta
        goals = {tid: ts.goals for tid, ts in s.teams.items()}
        values = {"player": s.name(ev.player_id), "team": s.team_name(ev.team_id), "venue": meta.venue,
                  "score": f"{meta.home.short_name} {goals[meta.home.team_id]}-{goals[meta.away.team_id]} {meta.away.short_name}"}
        t = s.t
        out = []
        for lang in self.langs:
            self._n += 1
            out.append(Cue(
                cue_id=f"{ev.match_id}-p{self._n:05d}", match_id=ev.match_id, t_in=t, t_out=t + SECONDS * 1000,
                start=CueTime(period=ev.period, clock_ms=ev.clock_ms),
                end=CueTime(period=ev.period, clock_ms=ev.clock_ms + SECONDS * 1000),
                layer="caption", template=f"caption_{key}", priority=PRIORITY, profile="all", lang=lang,
                team_id=ev.team_id, headline=LINES[key][lang].format(**values), evidence=[ev.event_id],
                verified=True, path=["ingest", "caption"],
            ))
        return out
