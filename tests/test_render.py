"""Stat cues and the screen budget over one full match."""

from __future__ import annotations

import re
from collections import defaultdict

import pytest

from groundtruth.render.captions import CaptionWriter
from groundtruth.render.cues import CueBuilder
from groundtruth.render.producer import CAPACITY, Producer
from groundtruth.render.templates import LANGS
from groundtruth.schema import Cue

ARABIC = re.compile(r"[؀-ۿ]")


@pytest.fixture(scope="module")
def cues(played):
    builder = CueBuilder(played["stats"])
    return [c for m in played["moments"] for c in builder.from_moment(m)]


def test_every_moment_kind_with_a_placement_yields_one_cue_per_language(played, cues):
    by_moment = defaultdict(set)
    for c in cues:
        by_moment[c.moment_id].add(c.lang)
    assert by_moment and all(langs == set(LANGS) for langs in by_moment.values())


def test_cues_round_trip_as_json(cues):
    for c in cues[:200]:
        assert Cue.model_validate_json(c.model_dump_json()) == c


def test_arabic_cues_are_in_arabic(cues):
    ar = [c for c in cues if c.lang == "ar" and c.template not in ("speed_burst", "distance")]
    assert ar and all(ARABIC.search(c.headline + (c.body or "")) for c in ar)


def test_numbers_on_screen_trace_to_evidence(cues):
    for c in cues:
        if c.template in ("goal", "big_chance", "shot_speed", "pass_quality", "card"):
            assert c.evidence, c.template


def test_tactical_shift_cues_are_marked_as_template_fallbacks(cues):
    shifts = [c for c in cues if c.template == "tactical_shift"]
    assert all(c.fallback for c in shifts)


def test_producer_never_overfills_a_layer(cues):
    producer = Producer()
    for c in sorted(cues, key=lambda c: c.t_in):
        producer.place(c)
    sheet = producer.sheet("all", "en")
    assert sheet
    for c in sheet:
        overlap = [o for o in sheet if o.layer == c.layer and o.t_in <= c.t_in < o.t_out]
        assert len(overlap) <= CAPACITY[c.layer]


def test_goals_are_never_dropped(cues):
    producer = Producer()
    for c in sorted(cues, key=lambda c: c.t_in):
        producer.place(c)
    assert not [c for c in producer.dropped if c.template == "goal"]


def test_captions_cover_shots_and_set_pieces_in_every_language(played):
    writer = CaptionWriter(played["stats"])
    shots = [e for e in played["events"] if e.type == "shot" and e.outcome in ("saved", "off_target", "blocked")]
    corners = [e for e in played["events"] if e.type == "restart" and e.restart == "corner"]
    caps = [c for e in shots + corners for c in writer.from_event(e)]
    assert len(caps) == 3 * (len(shots) + len(corners))
    assert all(c.layer == "caption" and c.evidence for c in caps)
    ar = [c for c in caps if c.lang == "ar"]
    assert ar and all(ARABIC.search(c.headline) for c in ar)


def test_a_new_caption_cuts_the_last_one_short():
    from groundtruth.schema import CueTime
    producer = Producer()
    mk = lambda i, t: Cue(cue_id=f"x{i}", match_id="m", t_in=t, t_out=t + 5000, start=CueTime(period=1, clock_ms=t),
                          end=CueTime(period=1, clock_ms=t + 5000), layer="caption", template="caption", priority=6,
                          profile="all", lang="en", headline=str(i))
    first, second = producer.place(mk(1, 0)), producer.place(mk(2, 2000))
    assert second.t_in == 2000 and first.t_out == 2000
