"""The Verifier's code layer: nothing reaches the screen unless its numbers and cause check out.

Every draft passes these checks before any model is asked anything:
* every number in the text is a number in the evidence (or a difference of two),
  written to the precision it rounds to;
* every claim's value matches the metric it names, for the team it names;
* the cause agrees with the direction the cause's own signals moved;
* the rulebook: nothing the evidence cannot know (seasons, records, history,
  form), no certainty about what happens next, no internal metric keys on screen.
"""

from __future__ import annotations

import re

from .evidence import BASE_KEY, RECENT_KEY, allowed_numbers, is_allowed, numbers_with_places
from .schemas import Insight, Rewrite

ARABIC = re.compile(r"[؀-ۿ]")
SNAKE = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")

# The evidence covers this match up to now. Anything else is invented, however plausible.
RULEBOOK: dict[str, list[tuple[str, re.Pattern]]] = {
    "en": [
        ("claims about seasons, records or history",
         re.compile(r"\b(seasons?|records?|history|historic\w*|all-time|ever|career|streak|unbeaten|"
                    r"last (year|week|season|match|game)|league table)\b", re.I)),
        ("certainty about what happens next",
         re.compile(r"\b(will (win|score|lose|concede)|certain(ly)?|guarantee\w*|definitely|inevitabl\w*|"
                    r"bound to|surely|no doubt)\b", re.I)),
    ],
    "es": [
        ("claims about seasons, records or history",
         re.compile(r"\b(temporadas?|r[ée]cords?|historia|hist[óo]ric\w*|racha|invict[oa]s?|nunca antes|jam[áa]s|"
                    r"el a[ñn]o pasado)\b", re.I)),
        ("certainty about what happens next",
         re.compile(r"\b(ganar[áa]n?|marcar[áa]n?|perder[áa]n?|sin duda|seguro que|garantiz\w*|inevitabl\w*|"
                    r"definitivamente)\b", re.I)),
    ],
    "ar": [  # Arabic attaches prefixes to words, so these match anywhere
        ("claims about seasons, records or history", re.compile(r"موسم|رقم قياسي|التاريخ|تاريخي|سلسلة|العام الماضي")),
        ("certainty about what happens next", re.compile(r"بالتأكيد|حتم|سيفوز|سيسجل|مؤكد|لا محالة|بلا شك")),
    ],
}


def rulebook(text: str, lang: str) -> list[str]:
    problems = []
    for what, pattern in RULEBOOK.get(lang, RULEBOOK["en"]):
        hit = pattern.search(text or "")
        if hit:
            problems.append(f"{what} (\"{hit.group(0)}\") cannot come from this match's evidence")
    key = SNAKE.search(text or "")
    if key:
        problems.append(f"internal metric key {key.group(0)!r} on screen; use plain words")
    return problems


def _numbers(field: str, text: str, allowed: list[float]) -> list[str]:
    return [f"{field} uses {n:g}, which is not in the evidence at that precision"
            for n, places in numbers_with_places(text) if not is_allowed(n, allowed, places)]


def check_insight(ins: Insight, pack: dict, causes: dict[str, list]) -> list[str]:
    problems: list[str] = []
    teams = pack["teams"]
    if ins.team_id not in teams:
        problems.append(f"team_id {ins.team_id!r} is not in this match")
        return problems
    if len(ins.headline) > 80:
        problems.append("headline longer than 80 characters")
    allowed = allowed_numbers(pack) + list(pack.get("tool_results", []))
    for field in ("headline", "explanation", "why_it_matters"):
        problems += _numbers(field, getattr(ins, field), allowed)
        problems += rulebook(getattr(ins, field), "en")
    for c in ins.claims:
        if c.value is None:
            continue
        team = teams.get(c.team_id or ins.team_id)
        if team is None:
            problems.append(f"claim names unknown team {c.team_id!r}")
            continue
        if c.metric:
            actual = [w.get(c.metric) for w in (team.get(RECENT_KEY, {}), team.get(BASE_KEY, {}))]
            actual = [a for a in actual if isinstance(a, (int, float))]
            if not actual:
                problems.append(f"claim uses metric {c.metric!r}, which the evidence does not have")
            elif not is_allowed(c.value, [x for a in actual for x in (a, round(a, 1), round(a, 2), 100 * a,
                                                                       round(100 * a), round(100 * a, 1))]):
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


def check_rewrite(rw: Rewrite, lang: str, allowed: list[float], max_numbers: int,
                  names: tuple[str, ...] = ()) -> list[str]:
    problems: list[str] = []
    text = f"{rw.headline} {rw.body}"
    nums = numbers_with_places(rw.headline) + numbers_with_places(rw.body)
    for n, places in nums:
        if not is_allowed(n, allowed, places):
            problems.append(f"rewrite uses {n:g}, which the verified insight does not have at that precision")
    if len(nums) > max_numbers:
        problems.append(f"rewrite uses {len(nums)} numbers; this profile allows {max_numbers}")
    if len(rw.headline) > 70:
        problems.append("headline longer than 70 characters")
    if lang == "ar" and not ARABIC.search(text):
        problems.append("Arabic rewrite contains no Arabic")
    if names and not any(n.lower() in text.lower() for n in names if n):
        problems.append(f"rewrite does not name the team ({names[0]})")
    problems += rulebook(text, lang)
    return problems
