"""What the agents return. Structured output keeps every claim checkable."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Cause = Literal["high_press", "low_block", "go_direct", "slow_down", "push_wide", "fatigue", "unclear"]

CAUSE_MEANING: dict[str, str] = {
    "high_press": "the team started pressing harder and higher up the pitch",
    "low_block": "the team dropped deeper and stopped pressing high",
    "go_direct": "the team started playing quicker and more direct, with more long balls",
    "slow_down": "the team slowed the game down and kept the ball longer",
    "push_wide": "the team stretched the pitch, playing wider in possession",
    "fatigue": "players are tiring, so intensity is dropping",
    "unclear": "the signals do not point to one clear cause",
}


class Claim(BaseModel):
    text: str = Field(description="One short factual statement the explanation relies on")
    metric: str | None = Field(None, description="The metric key this claim reports, e.g. press_rate")
    team_id: str | None = Field(None, description="Which team the metric belongs to")
    value: float | None = Field(None, description="The number exactly as given in the evidence")


class Insight(BaseModel):
    team_id: str = Field(description="The team whose change this is")
    cause: Cause = Field(description="The most likely cause, from the fixed vocabulary")
    headline: str = Field(description="At most 70 characters, plain words")
    explanation: str = Field(description="One or two sentences: what changed and why")
    why_it_matters: str = Field(description="One sentence: what it means for the match")
    claims: list[Claim] = Field(default_factory=list, description="Every number the text uses")
    confidence: float = Field(ge=0.0, le=1.0)


class Judgement(BaseModel):
    supported: bool = Field(description="True only if every causal claim follows from the evidence")
    problems: list[str] = Field(default_factory=list)


class Rewrite(BaseModel):
    headline: str = Field(description="At most 60 characters")
    body: str = Field(description="One or two short sentences")
