"""Wire formats shared by every stage.

Raw events carry only what a data provider would log: who, where, when and
how it ended. They carry no derived numbers (no speeds, xG or difficulty);
those belong to the Interpret stage. The truth log records the engine's
hidden tactical changes and is read only by the evaluation, never by agents.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "period_start",
    "period_end",
    "restart",
    "pass",
    "carry",
    "shot",
    "goal",
    "pressure",
    "tackle",
    "interception",
    "foul",
    "card",
    "substitution",
    "out",
    "recovery",
]
RestartType = Literal["kickoff", "throw_in", "goal_kick", "corner", "free_kick", "penalty"]


class Event(BaseModel):
    event_id: str
    match_id: str
    seq: int
    type: EventType
    period: int
    clock_ms: int
    end_clock_ms: int | None = None
    team_id: str | None = None
    player_id: str | None = None
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    outcome: str | None = None
    receiver_id: str | None = None
    under_pressure: bool = False
    possession_id: int | None = None
    restart: RestartType | None = None
    height: Literal["ground", "high"] | None = None
    body_part: Literal["foot", "head"] | None = None
    card: Literal["yellow", "red"] | None = None
    player_in_id: str | None = None
    related: list[str] = Field(default_factory=list)


class PlayerMeta(BaseModel):
    player_id: str
    name: str
    short_name: str
    number: int
    role: Literal["GK", "DEF", "MID", "FWD"]
    slot: str | None = None


class TeamMeta(BaseModel):
    team_id: str
    name: str
    short_name: str
    colors: list[str]
    formation: str
    lineup: list[PlayerMeta]
    bench: list[PlayerMeta]


class MatchMeta(BaseModel):
    match_id: str
    seed: int
    home: TeamMeta
    away: TeamMeta
    venue: str
    kickoff: str
    weather: str
    referee: str
    # period -> team_id -> +1 when the team attacks towards x = 105, else -1
    direction: dict[str, dict[str, int]]


class TruthChange(BaseModel):
    match_id: str
    period: int
    clock_ms: int
    team_id: str
    kind: Literal["tactics"]
    source: Literal["script", "console"]
    label: str
    before: dict[str, float]
    after: dict[str, float]


class Moment(BaseModel):
    """Something the Interpret stage flagged. Numbers here are computed, never generated."""

    moment_id: str
    match_id: str
    kind: str
    period: int
    clock_ms: int
    t: int  # monotonic play time, ms
    team_id: str | None = None
    player_id: str | None = None
    salience: float
    headline: str  # a plain English template line; agents rewrite it
    metrics: dict = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)  # event ids
