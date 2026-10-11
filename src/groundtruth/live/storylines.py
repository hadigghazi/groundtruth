"""Running storylines: the arcs a commentator carries through a match, found by code.

Each minute the keeper looks at the score, the goals, the per-minute snapshots and the
agents' verified insights, and opens, updates or closes storylines:

  comeback          a side that trailed is level or ahead
  pressure          a side creating most of the chances (xG) lately without leading
  against_the_run   a goal by the side that was being outplayed
  pressing_battle   both sides winning the ball high, a lot
  brace             a player with two goals or more
  engine            the player who has run furthest, late in the match
  chess             both sides have changed their approach within twenty minutes

Every storyline carries the numbers behind it, so the narrative, the desk and the recap
can cite them and the verifier can check them.
"""

from __future__ import annotations

from ..stats.engine import MINUTE

TEXT = {
    "comeback": {"en": "{team} fight back from behind", "es": "{team} remonta", "ar": "{team} يعود في النتيجة"},
    "pressure": {"en": "{team} pile on the pressure without reward", "es": "{team} aprieta sin premio",
                 "ar": "{team} يضغط دون أن يُكافأ"},
    "against_the_run": {"en": "{team} score against the run of play", "es": "{team} marca contra corriente",
                        "ar": "{team} يسجل عكس مجرى اللعب"},
    "pressing_battle": {"en": "A pressing battle: both sides winning it high", "es": "Batalla de presión en todo el campo",
                        "ar": "معركة ضغط: الفريقان يستعيدان الكرة عاليًا"},
    "brace": {"en": "{player} has {goals} goals", "es": "{player} suma {goals} goles", "ar": "{player} يسجل {goals} أهداف"},
    "engine": {"en": "{player} has run {km} km, more than anyone", "es": "{player} ha recorrido {km} km, más que nadie",
               "ar": "{player} قطع {km} كم، أكثر من أي لاعب"},
    "chess": {"en": "A tactical game of chess: both sides have changed their approach",
              "es": "Partida de ajedrez táctica: los dos equipos han cambiado su plan",
              "ar": "مباراة شطرنج تكتيكية: الفريقان غيّرا أسلوبهما"},
}


class Storylines:
    def __init__(self, session):
        self.s = session
        self.active: dict[str, dict] = {}
        self.all: list[dict] = []
        self._trailed: set[str] = set()

    def _story(self, key: str, kind: str, *, team_id=None, player_id=None, facts=None) -> dict | None:
        """Opens a storyline, or refreshes its numbers; returns it only when it is new."""
        st = self.s.stats
        facts = facts or {}
        if key in self.active:
            self.active[key]["facts"] = facts
            return None
        values = {"team": st.team_name(team_id), "player": st.name(player_id), **{k: v for k, v in facts.items()}}
        story = {"id": f"story-{self.s.match_id}-{len(self.all) + 1:02d}", "key": key, "kind": kind, "team_id": team_id,
                 "player_id": player_id, "t": st.t, "minute": st.clock_ms // MINUTE + 1, "status": "active",
                 "facts": facts, "headline": {lang: t.format(**values) for lang, t in TEXT[kind].items()}}
        self.active[key] = story
        self.all.append(story)
        return story

    def _close(self, key: str) -> dict | None:
        story = self.active.pop(key, None)
        if story:
            story["status"] = "resolved"
            story["resolved_minute"] = self.s.stats.clock_ms // MINUTE + 1
        return story

    def update(self) -> list[dict]:
        """Called once a minute; returns the storylines that opened or closed."""
        s, st = self.s, self.s.stats
        h, a = st.team_ids
        goals = {tid: st.teams[tid].goals for tid in (h, a)}
        changed = []
        for tid, opp in ((h, a), (a, h)):
            if goals[tid] < goals[opp]:
                self._trailed.add(tid)
            if tid in self._trailed and goals[tid] >= goals[opp] and goals[tid] > 0:
                changed.append(self._story(f"comeback-{tid}", "comeback", team_id=tid,
                                           facts={"goals": goals[tid], "conceded": goals[opp]}))
        # chances over the last 20 minutes, from the minute snapshots
        if len(s.minutes) > 20:
            now, then = s.minutes[-1]["teams"], s.minutes[-21]["teams"]
            xg = {tid: round(now[tid]["xg"] - then[tid]["xg"], 2) for tid in (h, a)}
            total = xg[h] + xg[a]
            for tid, opp in ((h, a), (a, h)):
                share = xg[tid] / total if total > 0 else 0
                key = f"pressure-{tid}"
                if xg[tid] >= 0.8 and share >= 0.65 and goals[tid] <= goals[opp]:
                    changed.append(self._story(key, "pressure", team_id=tid,
                                               facts={"xg_last_20": xg[tid], "xg_share": round(share, 2)}))
                elif key in self.active and (goals[tid] > goals[opp] or share < 0.55):
                    changed.append(self._close(key))
        # both sides winning it high
        recent = s.minutes[-1]["windows"]["recent"] if s.minutes else {}
        ht = {tid: (recent.get(tid) or {}).get("high_turnovers") for tid in (h, a)}
        if all(v is not None and v >= 3 for v in ht.values()):
            changed.append(self._story("pressing", "pressing_battle", facts={"high_turnovers": ht}))
        elif "pressing" in self.active and any(v is not None and v < 2 for v in ht.values()):
            changed.append(self._close("pressing"))
        # players
        scorers: dict[str, int] = {}
        for m in st.moments:
            if m.kind == "goal" and m.player_id:
                scorers[m.player_id] = scorers.get(m.player_id, 0) + 1
        for pid, n in scorers.items():
            if n >= 2:
                story = self._story(f"brace-{pid}", "brace", team_id=st.people[pid][0] if pid in st.people else None,
                                    player_id=pid, facts={"goals": n})
                changed.append(story)
        if st.clock_ms >= 60 * MINUTE and st.players:
            top = max(st.players.values(), key=lambda p: p.km)
            if top.km >= 8:
                changed.append(self._story(f"engine-{top.player_id}", "engine", team_id=top.team_id,
                                           player_id=top.player_id, facts={"km": round(top.km, 1)}))
        # tactics: both sides changed approach within twenty minutes
        recent_shifts = {r["team_id"] for r in s.agent_results.values() if st.t - r["t"] <= 20 * MINUTE}
        if len(recent_shifts) == 2:
            changed.append(self._story("chess", "chess", facts={"shifts": len(recent_shifts)}))
        return [c for c in changed if c]

    def goal(self, team_id: str, recent_xg: dict[str, float]) -> dict | None:
        """At a goal: was it against the run of play?"""
        st = self.s.stats
        opp = next(t for t in st.team_ids if t != team_id)
        if recent_xg.get(opp, 0) >= 0.6 and recent_xg.get(opp, 0) >= 2 * recent_xg.get(team_id, 0):
            key = f"run-{team_id}-{st.t}"
            story = self._story(key, "against_the_run", team_id=team_id,
                                facts={"xg_for": round(recent_xg.get(team_id, 0), 2), "xg_against": round(recent_xg[opp], 2)})
            self._close(key)  # a moment, not a running arc
            return story
        return None
