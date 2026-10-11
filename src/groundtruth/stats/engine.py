"""The Interpret stage: raw events and 10 Hz frames in, match state and moments out.

Everything is computed here, in code: pass difficulty, xG, ball and shot
speed, distance and sprints, pressing intensity (PPDA), field tilt, threat,
the control-vs-chaos index and the team-shape signals the moment detector
watches. Agents never produce a number; they read this state through tools.

Time: `t` is monotonic play time in ms, advanced by frames (the match clock
restarts at 45:00 in the second half, so it cannot be used for windows).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from importlib import resources

import numpy as np

from .. import pitch
from ..schema import Event, MatchMeta, Moment
from ..sim.engine import Frame
from .features import PASS_FEATURES, SHOT_FEATURES, pass_features, shot_features, team_xy
from .models import Logistic, load_models

MINUTE = 60_000
SPRINT_KMH = 25.2
HSR_KMH = 19.8
TAG_KMH = 31.0
RECENT, BASELINE = 10 * MINUTE, 20 * MINUTE  # detector windows: last 10 min against the 20 before


@dataclass
class PlayerStats:
    player_id: str
    team_id: str
    name: str
    passes: int = 0
    passes_complete: int = 0
    pass_m: float = 0.0
    hardest_complete: float = 0.0
    shots: int = 0
    on_target: int = 0
    goals: int = 0
    xg: float = 0.0
    tackles_won: int = 0
    interceptions: int = 0
    pressures: int = 0
    km: float = 0.0
    top_kmh: float = 0.0
    sprints: int = 0
    hsr_m: float = 0.0
    speed_kmh: float = 0.0

    def view(self) -> dict:
        d = dict(self.__dict__)
        d["pass_accuracy"] = round(self.passes_complete / self.passes, 3) if self.passes else None
        for k in ("pass_m", "xg", "km", "top_kmh", "hsr_m", "speed_kmh", "hardest_complete"):
            d[k] = round(d[k], 3 if k == "xg" else 2)
        return d


@dataclass
class TeamStats:
    team_id: str
    goals: int = 0
    passes: int = 0
    passes_complete: int = 0
    shots: int = 0
    on_target: int = 0
    xg: float = 0.0
    corners: int = 0
    fouls: int = 0
    possession_ms: int = 0

    def view(self) -> dict:
        d = dict(self.__dict__)
        d["pass_accuracy"] = round(self.passes_complete / self.passes, 3) if self.passes else None
        d["xg"] = round(self.xg, 3)
        return d


def _at_ball(samples: list[dict], key: str, at: float, min_n: int = 60) -> float | None:
    """`key` as it would read with the ball at `at` metres: a straight line fitted to ball position."""
    pts = [(s["ball_u"], s[key]) for s in samples if 10.0 <= s["ball_u"] <= 95.0]
    if len(pts) < min_n:
        return None
    x, y = np.array(pts).T
    if x.std() < 3.0:
        return round(float(y.mean()), 2)
    slope, intercept = np.polyfit(x, y, 1)
    return round(float(intercept + slope * at), 2)


@dataclass
class _Window:
    """Timestamped samples, trimmed to the longest window any metric needs."""

    keep_ms: int
    items: deque = field(default_factory=deque)

    def add(self, t: int, item) -> None:
        self.items.append((t, item))
        while self.items and self.items[0][0] < t - self.keep_ms:
            self.items.popleft()

    def between(self, lo: int, hi: int):
        return [it for ts, it in self.items if lo < ts <= hi]


DEFAULT_THRESHOLDS = {"press_rate": 0.15, "block_height_m": 4.0, "width_m": 3.0,
                      "long_pass_share": 0.08, "time_on_ball_s": 0.5}


def load_detector_config() -> dict:
    """The detector settings chosen by `groundtruth detector-report` (see evaluation/detector.py), and the
    learned detector from `groundtruth learn-detector` (evaluation/learned.py) when it has been trained."""
    files = resources.files("groundtruth.stats")
    try:
        cfg = json.loads(files.joinpath("detector.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        cfg = {"thresholds": dict(DEFAULT_THRESHOLDS)}
    try:
        cfg["learned"] = json.loads(files.joinpath("learned.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    return cfg


class StatsEngine:
    def __init__(self, meta: MatchMeta, models: dict[str, Logistic] | None = None,
                 thresholds: dict[str, float] | None = None, detector: dict | None = None):
        self.meta = meta
        self.match_id = meta.match_id
        self.models = models or load_models()
        cfg = detector or load_detector_config()
        self.cause_map: dict[str, str] = dict(cfg.get("cause_map", {}))
        # cause mode: a score per cause from the standardized moves of its signals
        self.causes = {c: [tuple(s[:2]) for s in sigs] for c, sigs in cfg.get("causes", {}).items()}
        self.signal_sd: dict[str, float] = dict(cfg.get("sd", {}))
        self.score_thresholds: dict[str, float] = dict(cfg.get("score_thresholds", {}))
        # the learned detector replaces the rule when it is there; the rule's signals still back the verifier
        self.learned = cfg.get("learned")
        self._learned_W = np.array(self.learned["W"]) if self.learned else None
        self._learned_pending: dict[str, int] = {}
        self._learned_last: dict[str, int] = {}
        self.thresholds = thresholds or dict(cfg.get("thresholds", {}))
        self.team_ids = (meta.home.team_id, meta.away.team_id)
        self.direction = {int(p): d for p, d in meta.direction.items()}
        self.people = {p.player_id: (team.team_id, p) for team in (meta.home, meta.away) for p in team.lineup + team.bench}
        self.players = {pid: PlayerStats(pid, tid, p.short_name) for pid, (tid, p) in self.people.items()}
        self.teams = {tid: TeamStats(tid) for tid in self.team_ids}
        self.t = 0
        self.period = 1
        self.clock_ms = 0
        self._frames: dict[tuple[int, int], Frame] = {}
        self._frame_order: deque = deque()
        self._last_ids: tuple | None = None
        self._prev_xy: np.ndarray | None = None
        self._steps: deque = deque(maxlen=10)
        self.poss_team: str | None = None
        self.poss_id: int | None = None
        self._poss_since = 0
        self._streak = (None, 0, [])  # team, completed passes in a row, their event ids
        keep = RECENT + BASELINE + MINUTE
        self.w = {k: _Window(keep) for k in ("pass", "def", "shot", "box", "poss", "shape", "zone", "action",
                                             "press", "tob", "won", "dact")}
        self._last_reception: tuple[str, str, int, int] | None = None  # team, player, clock_ms, period
        self.series: dict[str, dict[str, list[tuple[int, float]]]] = defaultdict(lambda: defaultdict(list))
        self.derived: dict[str, dict[str, float]] = {}
        self.moments: list[Moment] = []
        self._next_minute = MINUTE
        self._last_shift: dict[tuple[str, str], int] = {}
        self._pending_shift: dict[tuple[str, str], int] = {}
        self._tagged: dict[str, int] = {}
        self._km_marks: dict[str, int] = defaultdict(int)
        self._momentum_sign: list[int] = []
        self._chaos_state: list[int] = []
        self._record_shot_kmh = 0.0

    # ---------------------------------------------------------------- helpers

    def _dir(self, team_id: str, period: int | None = None) -> int:
        return self.direction[period or self.period][team_id]

    def _moment(self, kind: str, salience: float, headline: str, *, team_id=None, player_id=None,
                metrics=None, evidence=None, clock_ms=None) -> Moment:
        m = Moment(
            moment_id=f"{self.match_id}-k{len(self.moments) + 1:04d}", match_id=self.match_id, kind=kind,
            period=self.period, clock_ms=self.clock_ms if clock_ms is None else clock_ms, t=self.t,
            team_id=team_id, player_id=player_id, salience=round(salience, 2), headline=headline,
            metrics=metrics or {}, evidence=evidence or [],
        )
        self.moments.append(m)
        return m

    def name(self, pid: str | None) -> str:
        return self.people[pid][1].short_name if pid in self.people else (pid or "")

    def team_name(self, tid: str | None) -> str:
        if tid == self.meta.home.team_id:
            return self.meta.home.short_name
        if tid == self.meta.away.team_id:
            return self.meta.away.short_name
        return tid or ""

    def other(self, tid: str) -> str:
        return self.team_ids[1] if tid == self.team_ids[0] else self.team_ids[0]

    def _opp_xy(self, ev: Event) -> np.ndarray:
        """Opponent positions in the frame when the event started (empty if that frame is gone)."""
        f = self._frames.get((ev.period, ev.clock_ms))
        if f is None:
            return np.zeros((0, 2))
        mask = np.array([self.people.get(pid, (None,))[0] not in (ev.team_id, None) for pid in f.ids])
        return f.xy[mask].astype(float)

    # ----------------------------------------------------------------- frames

    def feed_frame(self, f: Frame) -> list[Moment]:
        before = len(self.moments)
        self.period, self.clock_ms = f.period, f.clock_ms
        key = (f.period, f.clock_ms)
        self._frames[key] = f
        self._frame_order.append(key)
        while len(self._frame_order) > 150:  # 15 s: longer than any pass or shot flight
            self._frames.pop(self._frame_order.popleft(), None)
        if f.ids != self._last_ids:  # line-up changed: restart the speed window
            self._last_ids = f.ids
            self._prev_xy = None
            self._steps.clear()
        xy = f.xy.astype(float)
        prev, self._prev_xy = self._prev_xy, xy
        if prev is not None:
            step = np.linalg.norm(xy - prev, axis=1)
            step = np.where(step < 1.5, step, 0.0)  # a jump is a kickoff or half-time reset, not running
            self._steps.append(step)
            kmh = np.mean(self._steps, axis=0) * 36.0  # mean speed over the last second
            settled = len(self._steps) == self._steps.maxlen
            for pid, s, v in zip(f.ids, step, kmh):
                ps = self.players.get(pid)
                if ps is None:
                    continue
                ps.km += s / 1000.0
                was_sprinting = ps.speed_kmh >= SPRINT_KMH
                ps.speed_kmh = float(v)
                if settled and v > ps.top_kmh:
                    ps.top_kmh = float(v)
                if v >= HSR_KMH:
                    ps.hsr_m += s
                if v >= SPRINT_KMH and not was_sprinting:
                    ps.sprints += 1
                self._player_tags(ps)
        if self.poss_team:
            self.teams[self.poss_team].possession_ms += 100
        self.t += 100
        if self.t % 1000 == 0:
            self._sample_shape(f)
        if self.t >= self._next_minute:
            self._next_minute += MINUTE
            self._minute()
        return self.moments[before:]

    def _player_tags(self, ps: PlayerStats) -> None:
        if ps.speed_kmh >= TAG_KMH and self.t - self._tagged.get(ps.player_id, -10 * MINUTE) > 5 * MINUTE:
            self._tagged[ps.player_id] = self.t
            self._moment("speed_burst", 0.3, f"{ps.name} hits {ps.speed_kmh:.1f} km/h",
                         team_id=ps.team_id, player_id=ps.player_id,
                         metrics={"speed_kmh": round(ps.speed_kmh, 1), "top_kmh": round(ps.top_kmh, 1)})
        for mark in (5, 8, 10):
            if ps.km >= mark > self._km_marks[ps.player_id]:
                self._km_marks[ps.player_id] = mark
                self._moment("distance_milestone", 0.25, f"{ps.name} passes {mark} km",
                             team_id=ps.team_id, player_id=ps.player_id, metrics={"km": round(ps.km, 2)})

    def _sample_shape(self, f: Frame) -> None:
        by_team: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for pid, (x, y) in zip(f.ids, f.xy):
            tid, p = self.people.get(pid, (None, None))
            if tid is None or p.role == "GK":
                continue
            by_team[tid].append(team_xy(float(x), float(y), self._dir(tid)))
        sample = {}
        for tid, pts in by_team.items():
            uv = np.array(pts)
            ball_u = team_xy(float(f.ball[0]), float(f.ball[1]), self._dir(tid))[0]
            sample[tid] = {"height": float(uv[:, 0].mean()), "ball_u": ball_u,
                           "line": float(np.sort(uv[:, 0])[:4].mean()),  # the four deepest outfield players
                           "width": float(np.percentile(uv[:, 1], 90) - np.percentile(uv[:, 1], 10))}
        self.w["shape"].add(self.t, (self.poss_team, sample))
        bx, by = float(f.ball[0]), float(f.ball[1])
        self.w["zone"].add(self.t, int(min(bx // 17.5, 5)) * 4 + int(min(by // 17.0, 3)))

    # ----------------------------------------------------------------- events

    def feed_event(self, ev: Event) -> list[Moment]:
        before = len(self.moments)
        self.period = ev.period
        if ev.possession_id is not None and ev.possession_id != self.poss_id and ev.type not in (
                "pressure", "tackle", "foul", "card", "period_start", "period_end", "substitution", "out"):
            self._possession_change(ev)
        handler = getattr(self, f"_on_{ev.type}", None)
        if handler:
            handler(ev)
        return self.moments[before:]

    def _possession_change(self, ev: Event) -> None:
        team, n, ids = self._streak
        if n >= 12:
            self._moment("passing_move", 0.4, f"{self.team_name(team)} string {n} passes together",
                         team_id=team, metrics={"passes": n}, evidence=ids[-8:])
        self._streak = (ev.team_id, 0, [])
        if self.poss_team != ev.team_id and ev.team_id:
            self.w["poss"].add(self.t, ev.team_id)
            if ev.x is not None and ev.type != "restart":
                u, _ = team_xy(ev.x, ev.y, self._dir(ev.team_id, ev.period))
                self.w["won"].add(self.t, {"team": ev.team_id, "u": u})
        self.poss_id = ev.possession_id
        self.poss_team = ev.team_id or self.poss_team

    def _on_pass(self, ev: Event) -> None:
        self._time_on_ball(ev)
        if ev.outcome == "complete":
            self._last_reception = (ev.team_id, ev.receiver_id, ev.end_clock_ms, ev.period)
        tid, ps, ts = ev.team_id, self.players[ev.player_id], self.teams[ev.team_id]
        d = self._dir(tid, ev.period)
        feats = pass_features(ev, d, self._opp_xy(ev))
        p = self.models["pass"].prob(feats)
        difficulty = round(100.0 * (1.0 - p), 1)
        secs = max((ev.end_clock_ms - ev.clock_ms) / 1000.0, 0.1)
        self.derived[ev.event_id] = {"distance_m": round(feats["dist"], 1), "difficulty": difficulty,
                                     "ball_speed_kmh": round(feats["dist"] / secs * 3.6, 1)}
        done = ev.outcome == "complete"
        ps.passes += 1
        ts.passes += 1
        ps.pass_m += feats["dist"]
        if done:
            ps.passes_complete += 1
            ts.passes_complete += 1
            if difficulty > ps.hardest_complete:
                ps.hardest_complete = difficulty
            if ps.passes_complete == 50:
                self._moment("milestone", 0.3, f"{ps.name} completes a 50th pass", team_id=tid,
                             player_id=ps.player_id, metrics={"passes_complete": 50}, evidence=[ev.event_id])
            if difficulty >= 75 and feats["dist"] > 15:
                self._moment("pass_quality", 0.35, f"{ps.name} lands a difficulty-{difficulty:.0f} pass",
                             team_id=tid, player_id=ps.player_id, evidence=[ev.event_id],
                             metrics={"difficulty": difficulty, **self.derived[ev.event_id]})
        u0, _ = team_xy(ev.x, ev.y, d)
        u1, v1 = team_xy(ev.end_x, ev.end_y, d)
        self.w["pass"].add(self.t, {"team": tid, "own60": u0 < 63.0, "final_third": u0 > 70.0, "fwd": u1 - u0,
                                    "long": feats["dist"] > 30.0, "complete": done, "id": ev.event_id})
        self.w["action"].add(self.t, ev.under_pressure)
        if done and pitch.in_box(u1, v1) and not pitch.in_box(u0, team_xy(ev.x, ev.y, d)[1]):
            self.w["box"].add(self.t, tid)
        team, n, ids = self._streak
        if done and team == tid:
            self._streak = (tid, n + 1, ids + [ev.event_id])
        elif done:
            self._streak = (tid, 1, [ev.event_id])

    def _on_carry(self, ev: Event) -> None:
        d = self._dir(ev.team_id, ev.period)
        u0, v0 = team_xy(ev.x, ev.y, d)
        u1, v1 = team_xy(ev.end_x, ev.end_y, d)
        self.w["action"].add(self.t, ev.under_pressure)
        if pitch.in_box(u1, v1) and not pitch.in_box(u0, v0):
            self.w["box"].add(self.t, ev.team_id)

    def _on_shot(self, ev: Event) -> None:
        self._time_on_ball(ev)
        tid, ps, ts = ev.team_id, self.players[ev.player_id], self.teams[ev.team_id]
        if ev.restart == "penalty":
            xg = 0.76
        else:
            xg = self.models["shot"].prob(shot_features(ev, self._dir(tid, ev.period), self._opp_xy(ev)))
        secs = max((ev.end_clock_ms - ev.clock_ms) / 1000.0, 0.1)
        dist = math.hypot(ev.end_x - ev.x, ev.end_y - ev.y)
        kmh = dist / secs * 3.6
        self.derived[ev.event_id] = {"xg": round(xg, 3), "distance_m": round(dist, 1)}
        if dist >= 5.0:  # a shot blocked at point-blank range has no meaningful speed
            self.derived[ev.event_id]["shot_speed_kmh"] = round(kmh, 1)
        else:
            kmh = 0.0
        ps.shots += 1
        ts.shots += 1
        ps.xg += xg
        ts.xg += xg
        if ev.outcome in ("goal", "saved"):
            ps.on_target += 1
            ts.on_target += 1
        self.w["shot"].add(self.t, (tid, xg))
        if ev.outcome != "goal" and xg >= 0.3:
            self._moment("big_chance", 0.7, f"Big chance for {ps.name} ({xg:.2f} xG)", team_id=tid,
                         player_id=ps.player_id, evidence=[ev.event_id], clock_ms=ev.clock_ms,
                         metrics={"xg": round(xg, 3), "outcome": ev.outcome})
        if kmh > max(self._record_shot_kmh, 105.0) and ev.body_part != "head":
            self._moment("shot_speed", 0.4, f"{ps.name} strikes it at {kmh:.0f} km/h", team_id=tid,
                         player_id=ps.player_id, evidence=[ev.event_id], clock_ms=ev.clock_ms,
                         metrics={"shot_speed_kmh": round(kmh, 1), "previous_best_kmh": round(self._record_shot_kmh, 1)})
        self._record_shot_kmh = max(self._record_shot_kmh, kmh)

    def _on_goal(self, ev: Event) -> None:
        tid = ev.team_id
        self.teams[tid].goals += 1
        self.players[ev.player_id].goals += 1
        shot = ev.related[0] if ev.related else None
        score = {t: self.teams[t].goals for t in self.team_ids}
        self._moment("goal", 1.0, f"GOAL {self.team_name(tid)}: {self.name(ev.player_id)}", team_id=tid,
                     player_id=ev.player_id, evidence=[e for e in (shot, ev.event_id) if e],
                     metrics={"xg": self.derived.get(shot, {}).get("xg", 0.0),
                              "score_home": score[self.team_ids[0]], "score_away": score[self.team_ids[1]]})

    def _on_tackle(self, ev: Event) -> None:
        self._defensive_action(ev)
        if ev.outcome == "won":
            self.players[ev.player_id].tackles_won += 1

    def _on_interception(self, ev: Event) -> None:
        self._defensive_action(ev)
        self.players[ev.player_id].interceptions += 1

    def _on_foul(self, ev: Event) -> None:
        self._defensive_action(ev)
        self.teams[ev.team_id].fouls += 1

    def _defensive_action(self, ev: Event) -> None:
        u, _ = team_xy(ev.x, ev.y, self._dir(ev.team_id, ev.period))
        self.w["def"].add(self.t, {"team": ev.team_id, "high": u > 42.0, "id": ev.event_id})
        self.w["dact"].add(self.t, (ev.team_id, u))

    def _on_recovery(self, ev: Event) -> None:
        u, _ = team_xy(ev.x, ev.y, self._dir(ev.team_id, ev.period))
        self.w["dact"].add(self.t, (ev.team_id, u))

    def _on_pressure(self, ev: Event) -> None:
        self.players[ev.player_id].pressures += 1
        u, _ = team_xy(ev.x, ev.y, self._dir(ev.team_id, ev.period))
        self.w["press"].add(self.t, {"team": ev.team_id, "high": u > 42.0})
        self.w["dact"].add(self.t, (ev.team_id, u))

    def _time_on_ball(self, ev: Event) -> None:
        """Seconds between receiving a pass and playing the next one: the purest read of tempo."""
        last = self._last_reception
        if last and last[1] == ev.player_id and last[3] == ev.period:
            dt = (ev.clock_ms - last[2]) / 1000.0
            if 0.0 < dt < 15.0:
                self.w["tob"].add(self.t, (ev.team_id, dt))
        self._last_reception = None

    def _on_restart(self, ev: Event) -> None:
        if ev.restart == "corner":
            self.teams[ev.team_id].corners += 1

    def _on_card(self, ev: Event) -> None:
        red = ev.card == "red"
        self._moment("card", 0.8 if red else 0.35, f"{ev.card.title()} card: {self.name(ev.player_id)}",
                     team_id=ev.team_id, player_id=ev.player_id, evidence=[ev.event_id], metrics={"card": ev.card})

    def _on_substitution(self, ev: Event) -> None:
        self._moment("substitution", 0.3, f"{self.name(ev.player_in_id)} on for {self.name(ev.player_id)}",
                     team_id=ev.team_id, player_id=ev.player_in_id, evidence=[ev.event_id],
                     metrics={"player_out": ev.player_id})

    # ---------------------------------------------------------- rolling metrics

    def window_metrics(self, lo: int, hi: int) -> dict[str, dict[str, float | None]]:
        """Team metrics over play time (lo, hi], in ms."""
        passes = self.w["pass"].between(lo, hi)
        defs = self.w["def"].between(lo, hi)
        shapes = self.w["shape"].between(lo, hi)
        shots = self.w["shot"].between(lo, hi)
        boxes = self.w["box"].between(lo, hi)
        presses = self.w["press"].between(lo, hi)
        tob = self.w["tob"].between(lo, hi)
        dacts = self.w["dact"].between(lo, hi)
        won = self.w["won"].between(lo, hi)
        per10 = 10.0 * MINUTE / max(hi - lo, 1)
        out = {}
        ft_total = sum(p["final_third"] for p in passes) or None
        midfield = lambda s, tid: tid in s and 35.0 <= s[tid]["ball_u"] <= 70.0  # noqa: E731
        for tid in self.team_ids:
            opp = self.other(tid)
            mine = [p for p in passes if p["team"] == tid]
            opp_build = sum(p["own60"] for p in passes if p["team"] == opp)
            my_high = sum(d["high"] for d in defs if d["team"] == tid)
            my_press = sum(p["high"] for p in presses if p["team"] == tid)
            poss_s = sum(1 for pt, _ in shapes if pt == tid)
            # Shape is read only with the ball in midfield, so where the ball is does not swamp the signal.
            heights = [s[tid]["height"] for pt, s in shapes if pt == opp and midfield(s, tid)]
            widths = [s[tid]["width"] for pt, s in shapes if pt == tid and midfield(s, tid)]
            touches = [dt for team, dt in tob if team == tid]
            off_ball = [s[tid] for pt, s in shapes if pt == opp and tid in s]
            on_ball = [s[tid] for pt, s in shapes if pt == tid and tid in s]
            my_dacts = [u for team, u in dacts if team == tid]
            out[tid] = {
                # shape, adjusted for where the ball is (a line fitted to ball position, read at a fixed point)
                "line_height_m": _at_ball(off_ball, "line", 50.0),
                "block_height_adj_m": _at_ball(off_ball, "height", 50.0),
                "width_adj_m": _at_ball(on_ball, "width", 60.0),
                "def_action_height_m": round(float(np.mean(my_dacts)), 2) if len(my_dacts) >= 15 else None,
                "high_turnovers": round(sum(w["u"] > 65.0 for w in won if w["team"] == tid) * per10, 2),
                "forward_share": round(sum(p["fwd"] > 10.0 for p in mine) / len(mine), 3) if len(mine) >= 15 else None,
                "ppda": round(opp_build / max(my_high, 1), 2) if opp_build else None,
                "press_rate": round(my_press / opp_build, 3) if opp_build >= 10 else None,
                "block_height_m": round(float(np.mean(heights)), 2) if len(heights) >= 30 else None,
                "width_m": round(float(np.mean(widths)), 2) if len(widths) >= 30 else None,
                "long_pass_share": round(sum(p["long"] for p in mine) / len(mine), 3) if len(mine) >= 15 else None,
                "time_on_ball_s": round(float(np.mean(touches)), 2) if len(touches) >= 10 else None,
                "pass_accuracy": round(sum(p["complete"] for p in mine) / len(mine), 3) if mine else None,
                "field_tilt": round(sum(p["final_third"] for p in mine) / ft_total, 3) if ft_total else None,
                "threat": round(sum(x for t, x in shots if t == tid) + 0.05 * sum(b == tid for b in boxes), 3),
                "possession": round(poss_s / max(len(shapes), 1), 3) if shapes else None,
            }
        return out

    def control_chaos(self, lo: int, hi: int) -> float | None:
        """0 = calm control, 100 = chaos. Mean of four parts, each scaled to 0..1."""
        minutes = (hi - lo) / MINUTE
        changes = len(self.w["poss"].between(lo, hi))
        passes = self.w["pass"].between(lo, hi)
        acts = self.w["action"].between(lo, hi)
        zones = self.w["zone"].between(lo, hi)
        if minutes <= 0 or not passes or not zones:
            return None
        turnover = min(max((changes / minutes - 1.0) / 2.5, 0.0), 1.0)
        per_poss = len(passes) / max(changes, 1)
        short_poss = min(max(1.0 - (per_poss - 1.5) / 5.0, 0.0), 1.0)
        pressure = min(sum(acts) / max(len(acts), 1) / 0.5, 1.0)
        counts = np.bincount(zones, minlength=24) / len(zones)
        nz = counts[counts > 0]
        spread = float(-(nz * np.log(nz)).sum() / math.log(24))
        return round(100.0 * (turnover + short_poss + pressure + spread) / 4.0, 1)

    def _minute(self) -> None:
        now = self.t
        cur = self.window_metrics(now - RECENT, now)
        for tid, m in cur.items():
            for k, v in m.items():
                if v is not None:
                    self.series[k][tid].append((now, v))
        cc = self.control_chaos(now - RECENT, now)
        if cc is not None:
            self.series["control_chaos"]["match"].append((now, cc))
        self._detect_shifts(cur)
        self._detect_momentum(cur)
        self._detect_chaos(cc)

    # The signals the detector watches. Their thresholds come from `groundtruth detector-report
    # --write-thresholds`, which measures how far each one drifts on matches with no hidden change.
    SIGNALS = ("press_rate", "ppda", "def_action_height_m", "high_turnovers", "line_height_m", "block_height_m",
               "block_height_adj_m", "width_m", "width_adj_m", "long_pass_share", "forward_share", "time_on_ball_s")

    def _detect_shifts(self, cur: dict) -> None:
        now = self.t
        if now < RECENT + BASELINE:
            return
        base = self.window_metrics(now - RECENT - BASELINE, now - RECENT)
        if self.learned:
            self._detect_learned(base, cur)
            return
        if self.causes:
            self._detect_causes(base, cur)
            return
        for tid in self.team_ids:
            for key, threshold in self.thresholds.items():  # only the signals the config selects
                a, b = base[tid].get(key), cur[tid].get(key)
                if a is None or b is None:
                    continue
                delta = b - a
                big = abs(delta) >= threshold
                k = (tid, key)
                if not big:
                    self._pending_shift.pop(k, None)
                    continue
                # must hold for two consecutive checks, and not repeat within 10 minutes
                if self._pending_shift.get(k) is None:
                    self._pending_shift[k] = now
                    continue
                if now - self._last_shift.get(k, -10**9) < 10 * MINUTE:
                    continue
                self._last_shift[k] = now
                self._pending_shift.pop(k, None)
                self._shift_moment(tid, key, a, b, base, cur)

    def _score(self, cause: str, tid: str, base: dict, cur: dict) -> tuple[float, list] | None:
        """A cause's score: the sum of its signals' standardized moves (in its expected direction) / sqrt(n)."""
        parts = []
        for s, d in self.causes[cause]:
            a, b = base[tid].get(s), cur[tid].get(s)
            sd = self.signal_sd.get(s)
            if a is None or b is None or not sd:
                continue
            parts.append(((1.0 if d == "up" else -1.0) * (b - a) / sd, s, a, b))
        if not parts:
            return None
        return sum(p[0] for p in parts) / math.sqrt(len(parts)), parts

    def _detect_causes(self, base: dict, cur: dict) -> None:
        now = self.t
        for tid in self.team_ids:
            for cause in self.causes:
                scored = self._score(cause, tid, base, cur)
                k = (tid, cause)
                if scored is None or scored[0] < self.score_thresholds.get(cause, 3.0):
                    self._pending_shift.pop(k, None)
                    continue
                # must hold for two consecutive checks, and not repeat within 10 minutes
                if self._pending_shift.get(k) is None:
                    self._pending_shift[k] = now
                    continue
                if now - self._last_shift.get(k, -10**9) < 10 * MINUTE:
                    continue
                self._last_shift[k] = now
                self._pending_shift.pop(k, None)
                score, parts = scored
                _, s, a, b = max(parts)
                self._shift_moment(tid, s, a, b, base, cur, cause=cause, score=score,
                                   contributions={p[1]: round(p[0], 2) for p in parts})

    def _detect_learned(self, base: dict, cur: dict) -> None:
        """One cause most likely, above the threshold, for two checks running; once per team per gap."""
        from .learned import drifts, features, probs

        L, W, now = self.learned, self._learned_W, self.t
        classes, signals = L["classes"], L["signals"]
        h, a = self.team_ids
        for tid, opp in ((h, a), (a, h)):
            x = features(drifts(base, cur, tid, opp, signals), L["sd"], signals)
            p = probs(W, x)
            c = int(np.argmax(p[1:])) + 1
            if p[c] < L["tau"]:
                self._learned_pending.pop(tid, None)
                continue
            if self._learned_pending.get(tid) != c:
                self._learned_pending[tid] = c
                continue
            if now - self._learned_last.get(tid, -10**9) < L.get("gap_min", 10) * MINUTE:
                continue
            self._learned_last[tid] = now
            self._learned_pending.pop(tid, None)
            # the team's own signal that pushed hardest towards this cause carries the template card
            pushes = sorted(((W[c, 1 + j] * x[1 + j], s) for j, s in enumerate(signals)), reverse=True)
            key = next((s for _, s in pushes if base[tid].get(s) is not None and cur[tid].get(s) is not None), None)
            if key is None:
                continue
            self._shift_moment(tid, key, base[tid][key], cur[tid][key], base, cur, cause=classes[c], score=float(p[c]),
                               probabilities={classes[i]: round(float(p[i]), 3) for i in range(len(classes))})

    def _shift_moment(self, tid: str, key: str, a: float, b: float, base: dict, cur: dict,
                      cause: str | None = None, score: float | None = None,
                      contributions: dict | None = None, probabilities: dict | None = None) -> None:
        now = self.t
        signals = {}
        for t in self.team_ids:
            for k in self.SIGNALS:
                if base[t].get(k) is not None and cur[t].get(k) is not None:
                    signals[f"{t}.{k}"] = {"before": base[t][k], "after": cur[t][k]}
        if key == "ppda":
            ev = [d["id"] for d in self.w["def"].between(now - RECENT, now) if d["team"] == tid and d["high"]]
        else:
            ev = [p["id"] for p in self.w["pass"].between(now - RECENT, now) if p["team"] == tid]
        direction = "up" if b > a else "down"
        self._moment("tactical_shift", 0.8, f"{self.team_name(tid)}: {key.replace('_', ' ')} {direction}",
                     team_id=tid, evidence=ev[-10:],
                     metrics={"signal": key, "direction": direction, "before": a, "after": b,
                              "rule_cause": cause or self.cause_map.get(f"{key}|{direction}"),
                              "score": round(score, 2) if score is not None else None,
                              "contributions": contributions or {},
                              **({"probabilities": probabilities} if probabilities else {}),
                              "window_min": RECENT // MINUTE, "baseline_min": BASELINE // MINUTE,
                              "signals": signals})

    def _detect_momentum(self, cur: dict) -> None:
        h, a = self.team_ids
        diff = cur[h]["threat"] - cur[a]["threat"]
        sign = 0 if abs(diff) < 0.15 else (1 if diff > 0 else -1)
        self._momentum_sign.append(sign)
        hist = self._momentum_sign
        if len(hist) >= 4 and sign != 0 and hist[-3:] == [sign] * 3 and -sign in hist[-10:-3]:
            if hist[-4] != sign:
                tid = h if sign > 0 else a
                self._moment("momentum_swing", 0.7, f"Momentum swings to {self.team_name(tid)}", team_id=tid,
                             metrics={"threat_home": cur[h]["threat"], "threat_away": cur[a]["threat"]})

    def _detect_chaos(self, cc: float | None) -> None:
        if cc is None:
            return
        state = 1 if cc >= 55 else (0 if cc <= 45 else (self._chaos_state[-1] if self._chaos_state else 0))
        self._chaos_state.append(state)
        s = self._chaos_state
        if len(s) >= 4 and s[-3:] == [state] * 3 and s[-4] != state:
            label = "chaos" if state else "control"
            self._moment("control_chaos", 0.55, f"The match tips into {label}", metrics={"index": cc, "state": label})

    # ----------------------------------------------------------------- state

    def snapshot(self) -> dict:
        now = self.t
        return {
            "match_id": self.match_id,
            "period": self.period,
            "clock_ms": self.clock_ms,
            "t": now,
            "score": {t: self.teams[t].goals for t in self.team_ids},
            "teams": {t: self.teams[t].view() for t in self.team_ids},
            "recent": self.window_metrics(now - RECENT, now),
            "recent_min": RECENT // MINUTE,
            "control_chaos": self.control_chaos(now - RECENT, now),
        }
