"""Turn moments into stat cues: fixed wording, computed numbers, no LLM.

This is the fast lane of the Render stage, and the fallback whenever an
agent's text is late or fails verification.
"""

from __future__ import annotations

from ..schema import Cue, CueMetric, CueTime, Moment
from ..stats.engine import RECENT, MINUTE, StatsEngine
from .templates import LANGS, PLACEMENT, render, word

# Moments whose wording should come from the agents; the template only stands in.
AGENT_KINDS = {"tactical_shift", "momentum_swing", "control_chaos"}


def fmt(key: str, v) -> str:
    if not isinstance(v, (int, float)):
        return str(v)
    if key.endswith("_share") or key in ("possession", "pass_accuracy", "field_tilt"):
        return f"{100 * v:.0f}%"
    if key.endswith("_m"):
        return f"{v:.1f} m"
    if key.endswith("_s"):
        return f"{v:.1f} s"
    if key == "press_rate":
        return f"{v:.2f}"
    if key == "xg":
        return f"{v:.2f}"
    return f"{v:.1f}" if isinstance(v, float) else str(v)


class CueBuilder:
    def __init__(self, stats: StatsEngine, langs: tuple[str, ...] = LANGS):
        self.stats = stats
        self.langs = langs
        self._n = 0

    def _values(self, m: Moment, lang: str) -> dict[str, str]:
        s, mt = self.stats, m.metrics
        v = {"team": s.team_name(m.team_id), "player": s.name(m.player_id)}
        if m.kind == "goal":
            h, a = s.meta.home, s.meta.away
            v["score"] = f"{h.short_name} {mt['score_home']}-{mt['score_away']} {a.short_name}"
        elif m.kind == "card":
            v.update(card_en=word(f"card_{mt['card']}", "en"), card_es=word(f"card_{mt['card']}", "es"),
                     card_ar=word(f"card_{mt['card']}", "ar"))
        elif m.kind == "big_chance":
            v["xg"] = fmt("xg", mt["xg"])
        elif m.kind == "shot_speed":
            v["speed"] = f"{mt['shot_speed_kmh']:.0f}"
        elif m.kind == "pass_quality":
            v.update(difficulty=f"{mt['difficulty']:.0f}", distance=f"{mt['distance_m']:.0f}")
        elif m.kind == "substitution":
            v.update(player_in=s.name(m.player_id), player_out=s.name(mt.get("player_out")))
        elif m.kind == "passing_move":
            v["passes"] = str(mt["passes"])
        elif m.kind == "speed_burst":
            v["speed"] = f"{mt['speed_kmh']:.1f}"
        elif m.kind == "distance_milestone":
            v["km"] = f"{mt['km']:.1f}"
        elif m.kind == "control_chaos":
            v["state"] = word(f"state_{mt['state']}", lang)
        elif m.kind == "tactical_shift":
            key = mt["signal"]
            v.update(signal=word(key, lang), direction=word(mt["direction"], lang),
                     before=fmt(key, mt["before"]), after=fmt(key, mt["after"]), window=str(RECENT // MINUTE))
        return v

    def _metrics(self, m: Moment) -> list[CueMetric]:
        out = []
        if m.kind == "tactical_shift":
            mt = m.metrics
            out.append(CueMetric(key=mt["signal"], value=mt["after"], before=mt["before"], team_id=m.team_id))
            return out
        for k, val in m.metrics.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                out.append(CueMetric(key=k, value=val, team_id=m.team_id))
        return out

    def from_moment(self, m: Moment) -> list[Cue]:
        placement = PLACEMENT.get(m.kind)
        if placement is None:
            return []
        layer, template, secs, priority = placement
        fallback = m.kind in AGENT_KINDS
        cues = []
        for lang in self.langs:
            head, body = render(template, lang, **self._values(m, lang))
            self._n += 1
            cues.append(Cue(
                cue_id=f"{m.match_id}-c{self._n:05d}", match_id=m.match_id, moment_id=m.moment_id,
                t_in=m.t, t_out=m.t + secs * 1000,
                start=CueTime(period=m.period, clock_ms=m.clock_ms),
                end=CueTime(period=m.period, clock_ms=m.clock_ms + secs * 1000),
                layer=layer, template=template, priority=priority, profile="all", lang=lang,
                team_id=m.team_id, anchor_player_id=m.player_id if layer == "player_tag" else None,
                headline=head, body=body, metrics=self._metrics(m), evidence=list(m.evidence),
                verified=True, fallback=fallback, path=["stats", "template"],
            ))
        return cues
