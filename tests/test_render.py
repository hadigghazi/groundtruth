"""Stat cues and the screen budget over one full match."""

from __future__ import annotations

import re
from collections import defaultdict

import pytest

from pitchside.render.cues import CueBuilder
from pitchside.render.producer import CAPACITY, Producer
from pitchside.render.templates import LANGS
from pitchside.schema import Cue

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
