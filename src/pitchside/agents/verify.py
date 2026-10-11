"""The Verifier's code layer: nothing reaches the screen unless its numbers and cause check out.

Three checks run on every draft before any model is asked anything:
* every number in the text is a number in the evidence (or a difference of two);
* every claim's value matches the metric it names, for the team it names;
* the cause agrees with the direction the cause's own signals moved.
"""

from __future__ import annotations

import re

from ..stats.engine import BASELINE, MINUTE, RECENT
from .evidence import allowed_numbers, is_allowed, numbers_in
from .schemas import Insight, Rewrite

ARABIC = re.compile(r"[؀-ۿ]")
RECENT_KEY = f"last_{RECENT // MINUTE}_min"
BASE_KEY = f"previous_{BASELINE // MINUTE}_min"


def check_insight(ins: Insight, pack: dict, causes: dict[str, list]) -> list[str]:
    problems: list[str] = []
    teams = pack["teams"]
    if ins.team_id not in teams:
        problems.append(f"team_id {ins.team_id!r} is not in this match")
        return problems
    if len(ins.headline) > 80:
        problems.append("headline longer than 80 characters")
    allowed = allowed_numbers(pack)
    for field in ("headline", "explanation", "why_it_matters"):
        for n in numbers_in(getattr(ins, field)):
            if not is_allowed(n, allowed):
                problems.append(f"{field} uses {n:g}, which is not in the evidence")
    for c in ins.claims:
        if c.value is None:
            continue
        team = teams.get(c.team_id or ins.team_id)
        if team is None:
            problems.append(f"claim names unknown team {c.team_id!r}")
            continue
        if c.metric:
            actual = [w.get(c.metric) for w in (team.get(RECENT_KEY, {}), team.get(BASE_KEY, {}))]
            actual = [a for a in actual if a is not None]
            if not actual:
                problems.append(f"claim uses metric {c.metric!r}, which the evidence does not have")
            elif not is_allowed(c.value, [x for a in actual for x in (a, round(a, 1), round(a, 2), 100 * a)]):
                problems.append(f"claim says {c.metric} = {c.value:g}; evidence has {', '.join(f'{a:g}' for a in actual)}")
        elif not is_allowed(c.value, allowed):
            problems.append(f"claim value {c.value:g} is not in the evidence")
    sigs = causes.get(ins.cause)
    if sigs:
        team = teams[ins.team_id]
        moved = []
        for s, d, *_ in sigs:
            a, b = team.get(BASE_KEY, {}).get(s), team.get(RECENT_KEY, {}).get(s)
            if a is not None and b is not None:
                moved.append((b > a) == (d == "up"))
        if moved and not any(moved):
            problems.append(f"cause {ins.cause!r} needs " + ", ".join(f"{s} {d}" for s, d, *_ in sigs)
                            + " for this team, and none of them moved that way")
    return problems


def check_rewrite(rw: Rewrite, lang: str, allowed: list[float], max_numbers: int) -> list[str]:
    problems: list[str] = []
    nums = numbers_in(rw.headline) + numbers_in(rw.body)
    for n in nums:
        if not is_allowed(n, allowed):
            problems.append(f"rewrite uses {n:g}, which the verified insight does not have")
    if len(nums) > max_numbers:
        problems.append(f"rewrite uses {len(nums)} numbers; this profile allows {max_numbers}")
    if len(rw.headline) > 70:
        problems.append("headline longer than 70 characters")
    if lang == "ar" and not ARABIC.search(rw.headline + rw.body):
        problems.append("Arabic rewrite contains no Arabic")
    return problems
