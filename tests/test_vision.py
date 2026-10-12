"""The rule that decides who has the ball, used for both the detections and the truth."""

from __future__ import annotations

import numpy as np

from groundtruth.vision.pipeline import HOLD_FRAMES, MIN_PASS_M, Possession

A, B, OPPONENT = (0, "a"), (0, "b"), (1, "c")


def _run(ball_track: list[tuple[float, float]], players) -> int:
    """Plays a sequence of ball positions past the rule and counts the passes it declares."""
    p = Possession()
    return sum(p.step(np.array(xy), players) for xy in ball_track)


def test_a_ball_carried_then_played_to_a_team_mate_is_one_pass():
    players = [(A, np.array([10.0, 10.0])), (B, np.array([20.0, 10.0]))]
    track = [(10.0, 10.0)] * HOLD_FRAMES + [(20.0, 10.0)] * HOLD_FRAMES
    assert _run(track, players) == 1


def test_the_nearest_player_flickering_is_not_a_pass():
    """Two players standing together: the ball barely moves, so neither guard lets it through."""
    players = [(A, np.array([10.0, 10.0])), (B, np.array([10.8, 10.0]))]
    brief = [(10.0, 10.0)] * 5 + [(10.5, 10.0)] * (HOLD_FRAMES - 1) + [(10.0, 10.0)] * 3
    assert _run(brief, players) == 0  # the flicker never lasts long enough
    held = [(10.0, 10.0)] * 5 + [(10.5, 10.0)] * (HOLD_FRAMES + 2)
    assert _run(held, players) == 0  # it lasts, but the ball moved under MIN_PASS_M


def test_losing_the_ball_to_the_other_side_is_not_a_pass():
    players = [(A, np.array([10.0, 10.0])), (OPPONENT, np.array([20.0, 10.0]))]
    track = [(10.0, 10.0)] * HOLD_FRAMES + [(20.0, 10.0)] * HOLD_FRAMES
    assert _run(track, players) == 0


def test_the_ball_out_of_everyone_s_reach_holds_the_possession():
    """A pass in flight must not reset who had it, or the pass it completes is lost."""
    players = [(A, np.array([10.0, 10.0])), (B, np.array([40.0, 10.0]))]
    flight = [(x, 10.0) for x in range(15, 36, 5)]  # nobody within reach while it travels
    track = [(10.0, 10.0)] * HOLD_FRAMES + flight + [(40.0, 10.0)] * HOLD_FRAMES
    assert _run(track, players) == 1


def test_the_travel_guard_measures_from_where_the_last_holder_had_it():
    p = Possession()
    for _ in range(HOLD_FRAMES):
        p.step(np.array([10.0, 10.0]), [(A, np.array([10.0, 10.0]))])
    assert p.holder == A and np.allclose(p.held_at, [10.0, 10.0])
    near = [(A, np.array([10.0, 10.0])), (B, np.array([10.0 + MIN_PASS_M - 0.5, 10.0]))]
    assert sum(p.step(np.array(near[1][1]), near) for _ in range(HOLD_FRAMES)) == 0
