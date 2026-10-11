"""One simulated match, played once and shared by every test module."""

from __future__ import annotations

import pytest

from pitchside.league import build_league
from pitchside.sim.engine import MatchEngine
from pitchside.stats.engine import StatsEngine
from pitchside.stats.replay import run_live


@pytest.fixture(scope="session")
def league():
    return build_league()


@pytest.fixture(scope="session")
def played(league):
    engine = MatchEngine(league["RVM"], league["HLB"], seed=7, match_id="t001")
    stats = StatsEngine(engine.meta())
    events, frames, moments = [], [], []
    for evs, frame, ms in run_live(engine, stats):
        events.extend(evs)
        frames.append(frame)
        moments.extend(ms)
    return {"engine": engine, "stats": stats, "events": events, "frames": frames, "moments": moments}


@pytest.fixture(scope="session")
def match(played):
    return played["engine"], played["events"], played["frames"]
