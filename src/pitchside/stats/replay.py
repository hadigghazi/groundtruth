"""Drive the Interpret stage from the engine, in the order a live feed delivers data."""

from __future__ import annotations

from collections.abc import Iterator

from ..schema import Event, Moment
from ..sim.engine import Frame, MatchEngine
from .engine import StatsEngine


def run_live(engine: MatchEngine, stats: StatsEngine) -> Iterator[tuple[list[Event], Frame, list[Moment]]]:
    """Each tick: positions first, then the events that resolved during that tick."""
    for events, frame in engine.run():
        moments = stats.feed_frame(frame)
        for ev in events:
            moments += stats.feed_event(ev)
        yield events, frame, moments
