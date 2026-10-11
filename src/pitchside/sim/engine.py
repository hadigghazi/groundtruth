"""Tick-based match engine. One tick is 0.1 s of match time.

The engine is the only place that knows the truth: every player's position,
who has the ball, and each team's hidden tactics. It emits raw events and
10 Hz frames, and writes every tactical change to a truth log that the
downstream stages never read. The same code runs offline to generate datasets
and live, paced by the API, with director-console interventions.

Coordinates are the fixed pitch frame (x along, y across, metres). Decisions
are made in the acting team's own frame, where it always attacks towards
u = 105 (see pitch.py).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

from .. import pitch
from ..league import REFEREES, WEATHER, Club
from ..schema import Event, MatchMeta, PlayerMeta, TeamMeta, TruthChange
from .formations import FORMATIONS, role_of
from .tactics import LABELS, ScheduledChange, Tactics, apply_label, make_script

DT = 0.1
TICK_MS = 100
HALF_MS = 45 * 60_000
L, W = pitch.LENGTH, pitch.WIDTH
LO, HI = np.array([0.8, 0.8]), np.array([L - 0.8, W - 0.8])
ACCEL = 4.5  # m/s^2: the most a player's velocity can change per second


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def true_goal_prob(u: float, v: float, pressured: bool, finishing: float, header: bool = False) -> float:
    """The engine's hidden chance that a shot from (u, v) scores."""
    dist = math.hypot(L - u, W / 2 - v)
    z = -0.6 - 0.11 * dist + 1.5 * pitch.goal_angle(u, v) - 0.4 * pressured + 1.0 * (finishing - 0.5)
    return float(_sigmoid(z - (0.7 if header else 0.0)))


def _value(uv: np.ndarray) -> np.ndarray:
    """How dangerous a spot is for the team in possession (team frame)."""
    u, v = uv[..., 0] / L, uv[..., 1]
    return 0.6 * u**3 + 0.12 * np.exp(-(((v - W / 2) / 16.0) ** 2)) * u**2


@dataclass
class Flight:
    kind: str  # "pass" or "shot"
    start: np.ndarray
    end: np.ndarray
    t0: int
    t1: int
    z_peak: float
    actor: int
    team: int
    outcome: str
    possession_id: int
    receiver: int | None = None  # intended receiver of a pass
    winner: int | None = None    # who ends up with the ball
    height: str | None = None
    under_pressure: bool = False
    restart: str | None = None
    header: bool = False
    cut_gap: float = 99.0  # the winning defender's distance from the passing lane at the kick


@dataclass
class DeadBall:
    kind: str
    team: int  # the restarting team
    spot: np.ndarray
    resume_ms: int
    taker: int | None = None


@dataclass(slots=True)
class Frame:
    period: int
    clock_ms: int
    ball: np.ndarray  # x, y, z
    ids: tuple[str, ...]
    xy: np.ndarray  # (n, 2), same order as ids

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "clock_ms": self.clock_ms,
            "ball": [round(float(c), 2) for c in self.ball],
            "players": [[pid, round(float(x), 2), round(float(y), 2)] for pid, (x, y) in zip(self.ids, self.xy)],
        }


@dataclass
class _Overrides:
    """Per-tick movement orders that replace a player's shape target."""

    orders: dict[int, tuple[np.ndarray, float]] = field(default_factory=dict)

    def set(self, i: int, target, speed: float) -> None:
        self.orders[int(i)] = (np.asarray(target, dtype=float), float(speed))


class MatchEngine:
    def __init__(
        self,
        home: Club,
        away: Club,
        *,
        seed: int,
        match_id: str = "m001",
        script: list[ScheduledChange] | None = None,
        scripted: bool = True,
        kickoff: str = "2026-10-24T15:00:00Z",
    ):
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.match_id = match_id
        self.kickoff = kickoff
        self.clubs = (home, away)
        self.team_ids = (home.club_id, away.club_id)
        self.tactics: list[Tactics] = [home.style, away.style]
        if script is None:
            script = make_script(self.rng, self.tactics) if scripted else []
        self.script = sorted(script, key=lambda c: (c.period, c.clock_ms))
        self.truth: list[TruthChange] = []
        self._console: list[tuple[str, int, str]] = []
        self._seq = 0
        self._out: list[Event] = []
        self.period = 1
        self.clock_ms = 0
        self.added_min = (int(self.rng.integers(1, 4)), int(self.rng.integers(2, 7)))
        self.score = [0, 0]
        self.finished = False
        self.dir = [1, -1]
        self._init_players()
        self.sub_due = [sorted(int(m * 60_000) for m in self.rng.uniform(55, 86, size=3)) for _ in range(2)]
        self.subs_used = [0, 0]
        self.possession_team = 0
        self.possession_id = 1
        self.ball = np.array([L / 2, W / 2, 0.0])
        self.state = "dead"
        self.owner: int | None = None
        self.flight: Flight | None = None
        self.dead: DeadBall | None = None
        self.restart_kind: str | None = None
        self.carry_start: tuple[np.ndarray, int] | None = None
        self.pressured_by: set[int] = set()
        self.received_ms = 0
        self.next_decision_ms = 0
        self.carry_dir = np.array([1.0, 0.0])
        self.header_ready = False
        self._ids: tuple[str, ...] = ()
        self._teleport(self._kickoff_targets(0))
        self._emit("period_start")
        self._set_dead("kickoff", 0, (L / 2, W / 2), 0.0)

    # ------------------------------------------------------------------ setup

    def _init_players(self) -> None:
        self.players, self.slot, uv, team = [], [], [], []
        for t, club in enumerate(self.clubs):
            for (slot, u, v), p in zip(FORMATIONS[club.formation], club.players[:11]):
                self.players.append(p)
                self.slot.append(slot)
                uv.append((u, v))
                team.append(t)
        self.N = len(self.players)
        self.slot_uv = np.array(uv, dtype=float)
        self.team = np.array(team)
        self.is_gk = np.array([s == "GK" for s in self.slot])
        self.bench = [list(c.players[11:]) for c in self.clubs]
        self._load_attrs()
        self.pos = np.zeros((self.N, 2))
        self.vel = np.zeros((self.N, 2))
        self.noise = np.zeros((self.N, 2))
        self.stamina = np.ones(self.N)
        self.active = np.ones(self.N, dtype=bool)
        self.run_until = np.zeros(self.N)
        self.run_target = np.zeros((self.N, 2))
        self.beaten_until = np.zeros(self.N)
        self.yellow = np.zeros(self.N, dtype=bool)

    def _load_attrs(self) -> None:
        self.pid = [p.player_id for p in self.players]
        for a in ("pace", "passing", "dribbling", "tackling", "finishing", "endurance"):
            setattr(self, a, np.array([getattr(p, a) for p in self.players], dtype=float))
        self.top_speed = 7.4 + 2.0 * self.pace  # m/s, about 27 to 34 km/h

    def meta(self) -> MatchMeta:
        def pm(p, slot=None):
            return PlayerMeta(player_id=p.player_id, name=p.name, short_name=p.short_name,
                              number=p.number, role=p.role, slot=slot)

        teams = []
        for club in self.clubs:
            slots = [s for s, _, _ in FORMATIONS[club.formation]]
            teams.append(TeamMeta(
                team_id=club.club_id, name=club.name, short_name=club.short_name, colors=list(club.colors),
                formation=club.formation,
                lineup=[pm(p, s) for p, s in zip(club.players[:11], slots)],
                bench=[pm(p) for p in club.players[11:]],
            ))
        h, a = self.team_ids
        rng = np.random.default_rng(self.seed + 7919)  # a separate stream, so it never perturbs the match
        return MatchMeta(
            match_id=self.match_id, seed=self.seed, home=teams[0], away=teams[1],
            venue=self.clubs[0].ground, kickoff=self.kickoff,
            weather=str(rng.choice(WEATHER)), referee=str(rng.choice(REFEREES)),
            direction={"1": {h: 1, a: -1}, "2": {h: -1, a: 1}},
        )

    # ------------------------------------------------------------- public API

    def intervene(self, team_id: str, label: str) -> None:
        """Queue a director-console tactics change; it applies on the next tick."""
        if label not in LABELS:
            raise ValueError(f"unknown change {label!r}; expected one of {sorted(LABELS)}")
        self._console.append(("tactics", self.team_ids.index(team_id), label))

    def step(self) -> tuple[list[Event], Frame | None]:
        if self.finished:
            return [], None
        now = self.clock_ms
        self._apply_changes(now)
        ov = _Overrides()
        if self.state == "dead":
            self._tick_dead(now, ov)
        if self.state == "owned":
            self._tick_owned(now, ov)
        elif self.state == "flight":
            self._tick_flight(now, ov)
        self._move(ov)
        frame = self._frame()
        self.clock_ms += TICK_MS
        self._check_period_end()
        events, self._out = self._out, []
        return events, frame

    def run(self) -> Iterator[tuple[list[Event], Frame]]:
        while not self.finished:
            events, frame = self.step()
            if frame is not None:
                yield events, frame

    # ---------------------------------------------------------------- helpers

    def _emit(self, type_: str, **fields) -> Event:
        self._seq += 1
        fields.setdefault("clock_ms", self.clock_ms)
        fields.setdefault("possession_id", self.possession_id)
        ev = Event(event_id=f"{self.match_id}-e{self._seq:05d}", match_id=self.match_id, seq=self._seq,
                   type=type_, period=self.period, **fields)
        self._out.append(ev)
        return ev

    @staticmethod
    def _xy(p, prefix: str = "") -> dict[str, float]:
        return {f"{prefix}x": round(float(p[0]), 2), f"{prefix}y": round(float(p[1]), 2)}

    def _uv(self, i: int) -> np.ndarray:
        return pitch.to_team(self.pos[i], self.dir[self.team[i]])

    def _fatigue(self, i):
        return 0.86 + 0.14 * self.stamina[i]

    def _members(self, t: int, outfield: bool = False) -> np.ndarray:
        mask = (self.team == t) & self.active
        if outfield:
            mask &= ~self.is_gk
        return np.flatnonzero(mask)

    def _gk(self, t: int) -> int:
        return int(np.flatnonzero((self.team == t) & self.is_gk)[0])

    def _teleport(self, targets: np.ndarray) -> None:
        act = self.active
        self.pos[act] = targets[act]
        self.vel[:] = 0.0

    # ------------------------------------------------------- tactical changes

    def _apply_changes(self, now: int) -> None:
        while self.script and (self.script[0].period, self.script[0].clock_ms) <= (self.period, now):
            ch = self.script.pop(0)
            self._change_tactics(ch.team, ch.label, "script")
        while self._console:
            _, team, label = self._console.pop(0)
            self._change_tactics(team, label, "console")

    def _change_tactics(self, team: int, label: str, source: str) -> TruthChange:
        before = self.tactics[team]
        after = apply_label(before, label)
        self.tactics[team] = after
        rec = TruthChange(match_id=self.match_id, period=self.period, clock_ms=self.clock_ms,
                          team_id=self.team_ids[team], kind="tactics", source=source, label=label,
                          before=before.as_dict(), after=after.as_dict())
        self.truth.append(rec)
        return rec

    # --------------------------------------------------------------- shapes

    def _offside_line(self, t: int, ball_u: float) -> float:
        opp = self._members(1 - t)
        ou = np.sort(pitch.to_team(self.pos[opp], self.dir[t])[:, 0])
        last_defender = ou[-2] if len(ou) >= 2 else L / 2
        return max(last_defender, ball_u, L / 2)

    def _shape(self, t: int, in_poss: bool) -> tuple[np.ndarray, np.ndarray]:
        idx = self._members(t)
        d = self.dir[t]
        bu, bv = pitch.to_team(self.ball[:2], d)
        tac = self.tactics[t]
        u0, v0 = self.slot_uv[idx, 0], self.slot_uv[idx, 1]
        if in_poss:
            shift = 0.5 * (bu - 45.0) + 4.0 + 6.0 * tac.line_height
            depth, width, pull = 1.15, 0.95 + 0.25 * tac.width, 0.25
        else:
            shift = 0.45 * (bu - 50.0) - 4.0 + 10.0 * (tac.line_height - 0.5)
            depth, width, pull = 0.72, 0.72, 0.40
        u = 22.0 + (u0 - 22.0) * depth + shift
        v = W / 2 + (v0 - W / 2) * width + pull * (bv - W / 2)
        gk = self.is_gk[idx]
        # Out of possession the back line holds near the box edge until the ball is close.
        floor = 5.0 if in_poss else float(np.clip(bu - 8.0, 11.0, 20.0))
        u = np.where(gk, np.clip(3.0 + 0.06 * bu, 2.0, 14.0), np.clip(u, floor, 101.0))
        v = np.where(gk, W / 2 + 0.2 * (bv - W / 2), np.clip(v, 2.0, W - 2.0))
        if in_poss:
            u = np.where(gk, u, np.minimum(u, self._offside_line(t, bu)))
        return idx, pitch.from_team(np.stack([u, v], axis=1), d)

    def _zonal(self, tgt: np.ndarray, o: int) -> None:
        """Defenders lean from their zone towards a goal-side spot on the nearest attacker."""
        D = self._members(o, outfield=True)
        A = np.array([i for i in self._members(1 - o, outfield=True) if i != self.owner])
        if len(D) == 0 or len(A) == 0:
            return
        goal = pitch.from_team(np.array([0.0, W / 2]), self.dir[o])
        gap = np.linalg.norm(tgt[D][:, None, :] - self.pos[A][None, :, :], axis=2)
        near = gap.argmin(axis=1)
        dmin = gap[np.arange(len(D)), near]
        att = self.pos[A][near]
        to_goal = goal - att
        spot = att + to_goal / np.maximum(np.linalg.norm(to_goal, axis=1), 1e-6)[:, None] * 2.0
        w = np.where(dmin < 12.0, 0.5, 0.0)[:, None]
        tgt[D] = (1.0 - w) * tgt[D] + w * spot

    def _kickoff_targets(self, kicking: int) -> np.ndarray:
        tgt = np.zeros((self.N, 2))
        for t in (0, 1):
            idx = self._members(t)
            u = np.minimum(self.slot_uv[idx, 0] * 0.72, 48.0)
            v = self.slot_uv[idx, 1].copy()
            if t == kicking:
                k = int(np.argmax(np.where(self.is_gk[idx], -1.0, self.slot_uv[idx, 0])))
                u[k], v[k] = L / 2 - 0.4, W / 2
            tgt[idx] = pitch.from_team(np.stack([u, v], axis=1), self.dir[t])
        return tgt

    def _corner_shape(self, tgt: np.ndarray, attacking: int, taker: int | None) -> None:
        box_attack = [(97, 30), (98, 37), (95, 41), (100, 34), (92, 34)]
        box_defend = [(2, 30), (2, 38), (5, 34), (6, 27), (6, 41), (9, 31), (9, 37), (12, 34)]
        att = [int(i) for i in self._members(attacking, outfield=True) if i != taker]
        # forwards first, then the tallest-standing defenders, then midfielders
        order = {"FWD": 0, "DEF": 1, "MID": 2}
        att.sort(key=lambda i: order[role_of(self.slot[i])])
        for i, uv in zip(att, box_attack):
            tgt[i] = pitch.from_team(np.array(uv, float), self.dir[attacking])
        for i, uv in zip(self._members(1 - attacking, outfield=True), box_defend):
            tgt[i] = pitch.from_team(np.array(uv, float), self.dir[1 - attacking])

    # ------------------------------------------------------------- movement

    def _move(self, ov: _Overrides) -> None:
        tgt = np.zeros((self.N, 2))
        if self.state == "dead" and self.dead.kind == "kickoff":
            tgt = self._kickoff_targets(self.dead.team)
        else:
            poss = self.dead.team if self.state == "dead" else self.possession_team
            for t in (0, 1):
                idx, xy = self._shape(t, in_poss=(t == poss))
                tgt[idx] = xy
            if self.state == "dead" and self.dead.kind == "corner":
                self._corner_shape(tgt, self.dead.team, self.dead.taker)
            elif self.state != "dead":
                self._zonal(tgt, 1 - self.possession_team)
        # slow wandering, so nobody stands still
        tau, sigma = 5.0, 1.0
        self.noise += -self.noise * DT / tau + sigma * math.sqrt(2 * DT / tau) * self.rng.normal(size=self.noise.shape)
        tgt += self.noise
        dist = np.linalg.norm(tgt - self.pos, axis=1)
        cap = np.minimum(self.top_speed * 0.8 * self._fatigue(slice(None)), 1.0 + 0.2 * dist)
        for i, (p, s) in ov.orders.items():
            tgt[i], cap[i] = p, s
        delta = tgt - self.pos
        dist = np.linalg.norm(delta, axis=1)
        want = np.minimum(cap, 0.9 * dist)
        desired = delta / np.maximum(dist, 1e-6)[:, None] * want[:, None]
        dv = desired - self.vel
        dvn = np.linalg.norm(dv, axis=1)
        dv *= np.minimum(1.0, ACCEL * DT / np.maximum(dvn, 1e-9))[:, None]
        self.vel += dv
        self.pos += self.vel * DT
        self.pos = np.clip(self.pos, [-1.5, -1.5], [L + 1.5, W + 1.5])
        self.vel[~self.active] = 0.0
        self.pos[~self.active] = (-5.0, -5.0)
        speed = np.linalg.norm(self.vel, axis=1)
        drain = (0.00004 + 0.000006 * np.maximum(speed - 4.0, 0.0) ** 2) * (1.3 - 0.6 * self.endurance)
        recover = np.where(speed < 2.0, 0.00001, 0.0)
        self.stamina = np.clip(self.stamina - (drain - recover) * DT * 10.0, 0.35, 1.0)
        if self.state == "owned":
            c = self.owner
            s = speed[c]
            lead = self.vel[c] / s * 0.5 if s > 0.3 else 0.0
            self.ball[:2] = self.pos[c] + lead
            self.ball[2] = 0.0

    # ------------------------------------------------------------ dead ball

    def _set_dead(self, kind: str, team: int, spot, delay_s: float) -> None:
        self.state = "dead"
        self.owner = None
        self.flight = None
        spot = np.asarray(spot, dtype=float)
        self.dead = DeadBall(kind, team, spot, self.clock_ms + int(delay_s * 1000))
        self.ball[:] = (spot[0], spot[1], 0.0)

    def _choose_taker(self, dd: DeadBall) -> int:
        if dd.kind == "goal_kick":
            return self._gk(dd.team)
        out = self._members(dd.team, outfield=True)
        if dd.kind == "penalty":
            return int(out[np.argmax(self.finishing[out])])
        return int(out[np.argmin(np.linalg.norm(self.pos[out] - dd.spot, axis=1))])

    def _tick_dead(self, now: int, ov: _Overrides) -> None:
        dd = self.dead
        self._due_substitutions(now)
        if dd.taker is None or not self.active[dd.taker]:
            dd.taker = self._choose_taker(dd)
        k = dd.taker
        ov.set(k, dd.spot, 5.5)
        if now < dd.resume_ms:
            return
        if np.linalg.norm(self.pos[k] - dd.spot) < 1.5 or now >= dd.resume_ms + 6000:
            self.pos[k] = dd.spot
            self.vel[k] = 0.0
            self._restart(dd, k, now)

    def _restart(self, dd: DeadBall, k: int, now: int) -> None:
        self.dead = None
        if self.possession_team != dd.team:
            self.possession_team = dd.team
            self.possession_id += 1
        self._emit("restart", team_id=self.team_ids[dd.team], player_id=self.pid[k],
                   restart=dd.kind, **self._xy(dd.spot))
        self.state = "owned"
        self.owner = k
        self.restart_kind = dd.kind
        self.carry_start = None
        self.pressured_by = set()
        self.received_ms = now
        self.next_decision_ms = now
        self.header_ready = False
        if dd.kind == "penalty":
            self._shoot(k, now, pressured=False, penalty=True)

    def _due_substitutions(self, now: int) -> None:
        if self.period != 2:
            return
        for t in (0, 1):
            while self.sub_due[t] and self.sub_due[t][0] <= now:
                self.sub_due[t].pop(0)
                if self.subs_used[t] < 3 and self.bench[t]:
                    self._substitute(t)

    def _substitute(self, t: int) -> None:
        idx = self._members(t, outfield=True)
        i = int(idx[np.argmin(self.stamina[idx])])
        group = role_of(self.slot[i])
        pool = [p for p in self.bench[t] if p.role == group] or [p for p in self.bench[t] if p.role != "GK"]
        if not pool:
            return
        p_in = pool[0]
        self.bench[t].remove(p_in)
        out_id = self.pid[i]
        self.players[i] = p_in
        self._load_attrs()
        self.stamina[i] = 1.0
        self.yellow[i] = False
        self.subs_used[t] += 1
        self._emit("substitution", team_id=self.team_ids[t], player_id=out_id, player_in_id=p_in.player_id)

    # ---------------------------------------------------------- on the ball

    def _give_ball(self, j: int, now: int, reason: str) -> None:
        tj = int(self.team[j])
        if self.possession_team != tj:
            self.possession_team = tj
            self.possession_id += 1
        self.state = "owned"
        self.owner = int(j)
        self.flight = None
        self.restart_kind = None
        self.received_ms = now
        self.carry_start = (self.pos[j].copy(), now)
        self.pressured_by = set()
        hold = self.rng.uniform(3.0, 7.0) if reason == "save" else self.rng.uniform(0.25, 0.6)
        self.next_decision_ms = now + int(hold * 1000)
        self.carry_dir = self._carry_dir(j)
        self.ball[:2] = self.pos[j]
        self.ball[2] = 0.0

    def _carry_dir(self, c: int) -> np.ndarray:
        u, v = self._uv(c)
        ang = 0.5 * math.atan2(W / 2 - v, max(L - u, 1.0)) + self.rng.normal(0.0, 0.45)
        d = np.array([math.cos(ang), math.sin(ang)])
        return d if self.dir[self.team[c]] == 1 else -d

    def _end_carry(self, c: int, now: int) -> None:
        if self.carry_start is None:
            return
        start, t0 = self.carry_start
        self.carry_start = None
        if np.linalg.norm(self.pos[c] - start) >= 3.0 and now - t0 >= 800:
            self._emit("carry", clock_ms=t0, end_clock_ms=now, team_id=self.team_ids[self.team[c]],
                       player_id=self.pid[c], **self._xy(start), **self._xy(self.pos[c], "end_"))

    def _tick_owned(self, now: int, ov: _Overrides) -> None:
        c = self.owner
        t, o = int(self.team[c]), 1 - int(self.team[c])
        P = self.pos[c]
        opp = self._members(o)
        d_opp = np.linalg.norm(self.pos[opp] - P, axis=1)
        gk_safe = bool(self.is_gk[c]) and self._uv(c)[0] < pitch.BOX_DEPTH
        pressured = bool((d_opp < 3.0).any()) and not gk_safe
        if gk_safe or self.restart_kind is not None:
            ov.set(c, P, 0.0)
        else:
            speed = (3.6 + 2.6 * self.dribbling[c]) * self._fatigue(c) * (0.85 if pressured else 1.0)
            ov.set(c, np.clip(P + self.carry_dir * 5.0, LO, HI), speed)
            self._press(o, c, opp, d_opp, now, ov)
            self._mark(o, c, ov)
        self._runs(t, now, ov)
        if gk_safe or self.restart_kind is not None:
            if now >= self.next_decision_ms:
                self._decide(c, now, pressured)
            return
        for j, dj in zip(opp, d_opp):
            if dj < 2.5 and j not in self.pressured_by:
                self.pressured_by.add(int(j))
                self._emit("pressure", team_id=self.team_ids[o], player_id=self.pid[j], **self._xy(self.pos[j]))
        for j, dj in zip(opp, d_opp):
            if dj < 1.8 and now >= self.beaten_until[j]:
                rate = 0.35 + 0.8 * self.tactics[o].press_intensity  # challenges per second at close range
                if self.rng.random() < rate * DT and self._tackle(int(j), c, now):
                    return
        if now >= self.next_decision_ms:
            self._decide(c, now, pressured)

    def _press(self, o: int, c: int, opp: np.ndarray, d_opp: np.ndarray, now: int, ov: _Overrides) -> None:
        tac = self.tactics[o]
        ball_u_def = pitch.to_team(self.pos[c], self.dir[o])[0]  # metres from the defenders' own goal line
        reach = 40.0 + 60.0 * tac.press_intensity
        k = 1 + int(tac.press_intensity > 0.4) + int(tac.press_intensity > 0.75)
        n = 0
        for rank in np.argsort(d_opp):
            j = int(opp[rank])
            if self.is_gk[j]:
                continue
            if ball_u_def <= reach or (n == 0 and d_opp[rank] < 12.0):
                speed = self.top_speed[j] * (0.75 + 0.25 * tac.press_intensity) * self._fatigue(j)
                if now < self.beaten_until[j]:
                    speed *= 0.5
                ov.set(j, self.pos[c] + self.vel[c] * 0.4, speed)
            n += 1
            if n >= k:
                break

    def _mark(self, o: int, c: int, ov: _Overrides) -> None:
        """Goal-side marking of dangerous attackers once the ball is in the defenders' own 40 m."""
        d = self.dir[o]
        if pitch.to_team(self.pos[c], d)[0] > 40.0:
            return
        attackers = [int(i) for i in self._members(1 - o, outfield=True) if i != c]
        a_uv = {i: pitch.to_team(self.pos[i], d) for i in attackers}
        danger = sorted((i for i in attackers if a_uv[i][0] < 30.0), key=lambda i: a_uv[i][0])
        free = [int(j) for j in self._members(o, outfield=True)
                if j not in ov.orders and self.slot_uv[j, 0] < 45.0]
        goal = pitch.from_team(np.array([0.0, W / 2]), d)
        for i in danger:
            if not free:
                break
            dists = [float(np.linalg.norm(self.pos[j] - self.pos[i])) for j in free]
            k = int(np.argmin(dists))
            if dists[k] > 18.0:
                continue
            j = free.pop(k)
            to_goal = goal - self.pos[i]
            spot = self.pos[i] + to_goal / max(float(np.linalg.norm(to_goal)), 1e-6) * 1.5
            ov.set(j, spot, self.top_speed[j] * 0.85 * self._fatigue(j))

    def _runs(self, t: int, now: int, ov: _Overrides) -> None:
        tac = self.tactics[t]
        rate = 0.05 + 0.10 * tac.directness  # runs started per second, per eligible player
        d = self.dir[t]
        ball_u = pitch.to_team(self.ball[:2], d)[0]
        line = None
        for i in self._members(t, outfield=True):
            if i == self.owner:
                continue
            if now < self.run_until[i]:
                ov.set(i, self.run_target[i], self.top_speed[i] * self._fatigue(i))
                continue
            if self.slot_uv[i, 0] < 45.0:
                continue
            u, v = self._uv(i)
            if u > 40.0 and self.rng.random() < rate * DT:
                if line is None:
                    line = self._offside_line(t, ball_u)
                tu = min(u + self.rng.uniform(12.0, 22.0), line)
                tv = float(np.clip(v + self.rng.uniform(-8.0, 8.0), 3.0, W - 3.0))
                self.run_target[i] = pitch.from_team(np.array([tu, tv]), d)
                self.run_until[i] = now + self.rng.uniform(2500, 4000)

    def _tackle(self, j: int, c: int, now: int) -> bool:
        """A tackle attempt by j on the carrier c. True when play changes hands or stops."""
        o = int(self.team[j])
        loc = self.pos[c].copy()
        if self.rng.random() < 0.12 + 0.08 * self.tactics[o].press_intensity:
            self._foul(j, c, now, loc)
            return True
        p_win = float(np.clip(0.45 + 0.35 * (self.tackling[j] - self.dribbling[c]), 0.2, 0.75))
        won = self.rng.random() < p_win
        self._emit("tackle", team_id=self.team_ids[o], player_id=self.pid[j],
                   outcome="won" if won else "lost", **self._xy(loc))
        if won:
            self._end_carry(c, now)
            self._give_ball(j, now, "tackle")
            return True
        self.beaten_until[j] = now + 1200
        return False

    def _foul(self, j: int, c: int, now: int, loc: np.ndarray) -> None:
        o, t = int(self.team[j]), int(self.team[c])
        self._end_carry(c, now)
        self._emit("foul", team_id=self.team_ids[o], player_id=self.pid[j], **self._xy(loc))
        r = self.rng.random()
        if r < 0.004 or (r < 0.134 and self.yellow[j]):
            self._emit("card", team_id=self.team_ids[o], player_id=self.pid[j], card="red", **self._xy(loc))
            self.active[j] = False
            self.pos[j] = (-5.0, -5.0)
        elif r < 0.134:
            self.yellow[j] = True
            self._emit("card", team_id=self.team_ids[o], player_id=self.pid[j], card="yellow", **self._xy(loc))
        u, v = pitch.to_team(loc, self.dir[t])
        if pitch.in_box(u, v) and self.rng.random() < 0.5:
            spot = pitch.from_team(np.array([L - 11.0, W / 2]), self.dir[t])
            self._set_dead("penalty", t, spot, self.rng.uniform(40, 70))
        else:
            self._set_dead("free_kick", t, loc, self.rng.uniform(12, 28))

    def _space_ahead(self, c: int) -> float:
        t = int(self.team[c])
        u, v = self._uv(c)
        ouv = pitch.to_team(self.pos[self._members(1 - t)], self.dir[t])
        du, dv = ouv[:, 0] - u, np.abs(ouv[:, 1] - v)
        ahead = (du > 0) & (dv < du + 2.0)
        if not ahead.any():
            return 12.0
        return float(min(np.hypot(du[ahead], dv[ahead]).min(), 12.0))

    def _decision_delay(self, c: int, pressured: bool) -> int:
        tempo = self.tactics[self.team[c]].tempo
        s = self.rng.uniform(0.8, 2.4) * (1.25 - 0.5 * tempo) * (0.6 if pressured else 1.0)
        return int(s * 1000)

    def _decide(self, c: int, now: int, pressured: bool) -> None:
        rk = self.restart_kind
        u, v = self._uv(c)
        if rk in (None, "free_kick") and not self.is_gk[c]:
            dg = math.hypot(L - u, W / 2 - v)
            if dg < 30.0:
                header = self.header_ready
                q = true_goal_prob(u, v, pressured, self.finishing[c], header=header)
                if rk == "free_kick":
                    p_shoot = 0.35 if dg < 28.0 and abs(v - W / 2) < 15.0 else 0.0
                    q *= 0.55
                elif header:
                    p_shoot = 0.6
                else:
                    blockers = len(self._defenders_in_cone(c))
                    p_shoot = float(np.clip(0.9 * q - 0.02, 0.0, 0.45)) * (0.5 if blockers >= 2 else 1.0)
                if self.rng.random() < p_shoot:
                    self._shoot(c, now, pressured, q=q, header=header)
                    return
        self.header_ready = False
        opts = self._pass_options(c, pressured, rk)
        held = (now - self.received_ms) / 1000.0
        can_carry = rk is None and not self.is_gk[c]
        carry_u = 0.015 + 0.004 * self._space_ahead(c) - 0.035 * pressured - 0.012 * held
        utils = np.append(opts["util"], carry_u if can_carry else -np.inf)
        w = np.exp((utils - utils.max()) / 0.03)
        k = int(self.rng.choice(len(utils), p=w / w.sum()))
        if k == len(utils) - 1:
            self.carry_dir = self._carry_dir(c)
            self.next_decision_ms = now + self._decision_delay(c, pressured)
            return
        self._pass(c, opts, k, now, pressured)

    def _pass_options(self, c: int, pressured: bool, rk: str | None) -> dict[str, np.ndarray]:
        t = int(self.team[c])
        d = self.dir[t]
        tac = self.tactics[t]
        P = self.pos[c]
        u_c, v_c = pitch.to_team(P, d)
        mates = self._members(t)
        mates = mates[mates != c]
        if rk != "goal_kick" and not (pressured and u_c < 35.0):
            mates = mates[~self.is_gk[mates]]
        R = np.clip(self.pos[mates] + self.vel[mates] * 0.6, [1.0, 1.0], [L - 1.0, W - 1.0])
        seg = R - P
        dist = np.linalg.norm(seg, axis=1)
        long_ok = tac.directness > 0.6 or rk == "goal_kick" or bool(self.is_gk[c])
        max_d = {"throw_in": 25.0, "kickoff": 22.0}.get(rk, 62.0 if long_ok else 55.0)
        keep = (dist >= 4.0) & (dist <= max_d)
        uvR = pitch.to_team(R, d)
        if rk == "corner":
            in_box = (uvR[:, 0] >= L - pitch.BOX_DEPTH) & (np.abs(uvR[:, 1] - W / 2) <= pitch.BOX_HALF_WIDTH)
            if (keep & in_box).any():
                keep &= in_box
        if not keep.any():
            keep = dist == dist.min()
        mates, R, seg, dist, uvR = mates[keep], R[keep], seg[keep], dist[keep], uvR[keep]
        O = self.pos[self._members(1 - t)]
        len2 = np.maximum((seg**2).sum(axis=1), 1e-6)
        rel = O[None, :, :] - P[None, None, :]
        tt = np.clip((rel * seg[:, None, :]).sum(axis=2) / len2[:, None], 0.0, 1.0)
        closest = P[None, None, :] + tt[..., None] * seg[:, None, :]
        gap = np.linalg.norm(O[None, :, :] - closest, axis=2)
        gap = np.where(tt < 0.12, np.inf, gap)
        lane = np.minimum(gap.min(axis=1), 8.0)
        recv = np.minimum(np.linalg.norm(O[None, :, :] - R[:, None, :], axis=2).min(axis=1), 10.0)
        fwd = uvR[:, 0] - u_c
        high = (dist > 32.0) | ((lane < 2.0) & (dist > 18.0)) | (rk == "corner")
        skill = self.passing[c] - 0.5
        lg = 2.4 - 0.055 * dist + 0.18 * lane + 0.12 * recv - 0.8 * pressured + 1.2 * skill - 0.01 * np.maximum(fwd, 0)
        lh = 1.3 - 0.045 * dist + 0.20 * recv - 0.6 * pressured + 1.0 * skill
        p = _sigmoid(np.where(high, lh, lg))
        if rk == "corner":
            p = p * 0.45
        gain = _value(uvR) - _value(np.array([u_c, v_c]))
        loss = 0.05 + 0.3 * (1.0 - u_c / L) ** 2
        util = p * (gain + 0.04) - (1.0 - p) * loss + tac.directness * 0.003 * fwd
        return {"idx": mates, "R": R, "dist": dist, "high": high, "p": p, "util": util}

    def _pass(self, c: int, opts: dict, k: int, now: int, pressured: bool) -> None:
        t = int(self.team[c])
        self._end_carry(c, now)
        P = self.pos[c].copy()
        r = int(opts["idx"][k])
        R = opts["R"][k].copy()
        high = bool(opts["high"][k])
        cut_gap = 99.0
        if self.rng.random() < opts["p"][k]:
            outcome, winner, end = "complete", r, R
        else:
            opp = self._members(1 - t)
            seg = R - P
            len2 = max(float(seg @ seg), 1e-6)
            tt = np.clip(((self.pos[opp] - P) @ seg) / len2, 0.35, 1.0)
            closest = P + tt[:, None] * seg
            gaps = np.linalg.norm(self.pos[opp] - closest, axis=1)
            j = int(np.argmin(gaps))
            if self.rng.random() < (0.22 if high else 0.08):
                outcome, winner = "out", None
                end, _ = pitch.exit_point(P, seg / math.sqrt(len2))
            else:
                outcome, winner, end = "incomplete", int(opp[j]), closest[j]
                cut_gap = float(gaps[j])
        dist = float(np.linalg.norm(end - P))
        speed = min(12.0 + 0.2 * dist, 25.0) if high else min(9.0 + 0.25 * dist, 22.0)
        steps = max(int(round(dist / speed / DT)), 2)
        self.flight = Flight(
            kind="pass", start=P, end=np.asarray(end, dtype=float), t0=now, t1=now + steps * TICK_MS,
            z_peak=min(2.0 + 0.12 * dist, 12.0) if high else 0.0, actor=c, team=t, outcome=outcome,
            possession_id=self.possession_id, receiver=r, winner=winner,
            height="high" if high else "ground", under_pressure=pressured, restart=self.restart_kind,
            cut_gap=cut_gap,
        )
        self.state, self.owner, self.restart_kind = "flight", None, None

    def _defenders_in_cone(self, c: int) -> list[int]:
        t = int(self.team[c])
        u, v = self._uv(c)
        out = []
        for j in self._members(1 - t, outfield=True):
            uj, vj = pitch.to_team(self.pos[j], self.dir[t])
            du = uj - u
            if du <= 0.5 or du >= L - u:
                continue
            f = du / (L - u)
            lo = v + (pitch.POST_LOW - v) * f - 1.0
            hi = v + (pitch.POST_HIGH - v) * f + 1.0
            if lo <= vj <= hi:
                out.append(int(j))
        return out

    def _shoot(self, c: int, now: int, pressured: bool, q: float | None = None,
               header: bool = False, penalty: bool = False) -> None:
        t = int(self.team[c])
        d = self.dir[t]
        self._end_carry(c, now)
        P = self.pos[c].copy()
        u, v = pitch.to_team(P, d)
        if penalty:
            q = float(np.clip(0.76 + 0.12 * (self.finishing[c] - 0.5), 0.6, 0.9))
        elif q is None:
            q = true_goal_prob(u, v, pressured, self.finishing[c], header=header)
        rng = self.rng
        speed = rng.uniform(11.0, 16.0) if header else float(np.clip(rng.normal(25.5, 3.5), 15.0, 36.0))
        cone = [] if penalty else self._defenders_in_cone(c)
        winner = None
        z_peak = 1.2 if header else 0.6
        if cone and rng.random() < min(0.45, 0.06 + 0.09 * len(cone)):
            outcome = "blocked"
            winner = min(cone, key=lambda j: float(np.linalg.norm(self.pos[j] - P)))
            end = self.pos[winner].copy()
        else:
            p_goal = min(0.97, q / 0.88)
            r = rng.random()
            gy = rng.uniform(pitch.POST_LOW + 0.3, pitch.POST_HIGH - 0.3)
            if r < p_goal:
                outcome, end_uv = "goal", (L, gy)
            elif r < p_goal + (1.0 - p_goal) * 0.42:
                outcome, end_uv = "saved", (L - 0.6, gy)
                winner = self._gk(1 - t)
            else:
                outcome = "off_target"
                if rng.random() < 0.35:
                    end_uv, z_peak = (L, gy), 3.2  # over the bar
                else:
                    wide = rng.uniform(0.3, 4.0)
                    end_uv = (L, pitch.POST_LOW - wide if rng.random() < 0.5 else pitch.POST_HIGH + wide)
            end = pitch.from_team(np.array(end_uv, dtype=float), d)
        dist = float(np.linalg.norm(end - P))
        steps = max(int(round(dist / speed / DT)), 1)
        self.flight = Flight(
            kind="shot", start=P, end=end, t0=now, t1=now + steps * TICK_MS, z_peak=z_peak, actor=c, team=t,
            outcome=outcome, possession_id=self.possession_id, winner=winner, under_pressure=pressured,
            restart=self.restart_kind, header=header,
        )
        self.state, self.owner, self.restart_kind, self.header_ready = "flight", None, None, False

    # ------------------------------------------------------------- in flight

    def _tick_flight(self, now: int, ov: _Overrides) -> None:
        f = self.flight
        a = min((now - f.t0) / max(f.t1 - f.t0, 1), 1.0)
        self.ball[:2] = f.start + (f.end - f.start) * a
        self.ball[2] = 4.0 * f.z_peak * a * (1.0 - a)
        if f.kind == "pass":
            if f.winner is not None:
                ov.set(f.winner, f.end, self.top_speed[f.winner] * 0.9)
            if f.receiver is not None and f.receiver != f.winner:
                ov.set(f.receiver, f.end, self.top_speed[f.receiver] * 0.85)
        if now >= f.t1:
            self._resolve(f, now)

    def _resolve(self, f: Flight, now: int) -> None:
        tid = self.team_ids[f.team]
        if f.kind == "pass":
            ev = self._emit(
                "pass", clock_ms=f.t0, end_clock_ms=f.t1, team_id=tid, player_id=self.pid[f.actor],
                **self._xy(f.start), **self._xy(f.end, "end_"), outcome=f.outcome,
                receiver_id=self.pid[f.winner] if f.outcome == "complete" else None,
                under_pressure=f.under_pressure, height=f.height, restart=f.restart,
                possession_id=f.possession_id,
            )
            if f.outcome == "complete":
                self._give_ball(f.winner, now, "pass")
                u, v = self._uv(f.winner)
                self.header_ready = f.height == "high" and pitch.in_box(u, v)
                if self.header_ready:
                    self.next_decision_ms = now + 100
            elif f.outcome == "incomplete":
                # A defender already in the lane cuts it out (interception); otherwise the
                # ball runs loose and is picked up (recovery).
                kind = "interception" if f.cut_gap < 1.2 else "recovery"
                self._emit(kind, team_id=self.team_ids[self.team[f.winner]],
                           player_id=self.pid[f.winner], **self._xy(f.end), related=[ev.event_id])
                self._give_ball(f.winner, now, kind)
            else:
                self._ball_out(f.end, f.team)
            return
        ev = self._emit(
            "shot", clock_ms=f.t0, end_clock_ms=f.t1, team_id=tid, player_id=self.pid[f.actor],
            **self._xy(f.start), **self._xy(f.end, "end_"), outcome=f.outcome,
            under_pressure=f.under_pressure, restart=f.restart, body_part="head" if f.header else "foot",
            possession_id=f.possession_id,
        )
        other = 1 - f.team
        if f.outcome == "goal":
            self.score[f.team] += 1
            self._emit("goal", team_id=tid, player_id=self.pid[f.actor], **self._xy(f.end), related=[ev.event_id])
            self._set_dead("kickoff", other, (L / 2, W / 2), self.rng.uniform(45, 70))
        elif f.outcome == "saved":
            if self.rng.random() < 0.7:
                self._give_ball(f.winner, now, "save")
            else:
                self._set_dead("corner", f.team, self._corner_spot(f.team, f.end), self.rng.uniform(18, 35))
        elif f.outcome == "blocked":
            if self.rng.random() < 0.3:
                self._set_dead("corner", f.team, self._corner_spot(f.team, f.end), self.rng.uniform(18, 35))
            else:
                self._give_ball(f.winner, now, "block")
        else:
            self._ball_out(f.end, f.team)

    def _corner_spot(self, attacking: int, near: np.ndarray) -> np.ndarray:
        """The corner flag on the goal line `attacking` attacks, on the side nearest `near`."""
        x = L if self.dir[attacking] == 1 else 0.0
        return np.array([x, 0.0 if near[1] < W / 2 else W])

    def _ball_out(self, point: np.ndarray, last_team: int) -> None:
        self._emit("out", team_id=self.team_ids[last_team], **self._xy(point))
        other = 1 - last_team
        x, y = float(point[0]), float(point[1])
        if 0.05 < x < L - 0.05:  # crossed a touchline
            self._set_dead("throw_in", other, (float(np.clip(x, 0.5, L - 0.5)), 0.0 if y < W / 2 else W),
                           self.rng.uniform(7, 15))
            return
        # The goal line at this end belongs to whichever team defends it.
        high_end = x > L / 2
        defender = 0 if (self.dir[0] == -1) == high_end else 1
        if last_team == defender:
            self._set_dead("corner", other, np.array([L if high_end else 0.0, 0.0 if y < W / 2 else W]),
                           self.rng.uniform(18, 35))
        else:
            spot = pitch.from_team(np.array([5.5, W / 2]), self.dir[defender])
            self._set_dead("goal_kick", defender, spot, self.rng.uniform(14, 25))

    # ---------------------------------------------------------- periods

    def _check_period_end(self) -> None:
        limit = (45 + self.added_min[0]) * 60_000 if self.period == 1 else (90 + self.added_min[1]) * 60_000
        if self.clock_ms < limit or self.state == "flight":
            return
        if self.state == "owned":
            self._end_carry(self.owner, self.clock_ms)
        self._emit("period_end")
        if self.period == 2:
            self.finished = True
            return
        self.period = 2
        self.clock_ms = HALF_MS
        self.dir = [-1, 1]
        self.stamina = np.minimum(1.0, self.stamina + 0.08)  # half-time recovery
        self.run_until[:] = 0.0
        self.possession_team = 1
        self.possession_id += 1
        self._teleport(self._kickoff_targets(1))
        self._emit("period_start")
        self._set_dead("kickoff", 1, (L / 2, W / 2), 0.0)

    def _frame(self) -> Frame:
        act = np.flatnonzero(self.active)
        ids = tuple(self.pid[i] for i in act)
        if ids != self._ids:  # one shared tuple until the line-up changes
            self._ids = ids
        return Frame(self.period, self.clock_ms, self.ball.astype(np.float32), self._ids,
                     self.pos[act].astype(np.float32))
