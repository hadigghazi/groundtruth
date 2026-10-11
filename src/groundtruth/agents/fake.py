"""A scripted chat client: deterministic agent replies for CI and offline demos.

It reads the JSON task in the last user message and answers the way each
agent would, using only numbers from the evidence. `bad_first_draft=True`
makes the Tactician's first attempt quote a wrong number, so tests can watch
the code checks reject it and the retry fix it; `judge_rejects_first=True`
makes the judge turn down the first draft that reaches it, after the writers
have already started on it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any, ClassVar

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    FunctionInvocationLayer,
    Message,
)
from agent_framework.observability import ChatTelemetryLayer

from ..stats.engine import MINUTE, RECENT

HEADLINES = {
    "high_press": "{team} push up and press higher",
    "low_block": "{team} drop deep and sit in",
    "go_direct": "{team} go quicker and more direct",
    "slow_down": "{team} slow the game right down",
    "push_wide": "{team} stretch the pitch wide",
    "unclear": "{team} change their approach",
}
CONSEQUENCE = {
    "high_press": "Winning the ball high gives them chances close to goal, but leaves space behind.",
    "low_block": "They are inviting pressure and looking to hit back on the break.",
    "go_direct": "The game gets faster and more open, with fewer long spells of control.",
    "slow_down": "They are taking the sting out of the game and keeping control.",
    "push_wide": "Spreading the play pulls the defence apart and opens gaps inside.",
    "unclear": "Watch the next few minutes to see which way it settles.",
}
WORD = {"high_turnovers": "high turnovers", "def_action_height_m": "the height of their defending",
        "line_height_m": "their back line", "block_height_adj_m": "their block", "block_height_m": "their block",
        "width_adj_m": "their width in possession", "width_m": "their width in possession",
        "long_pass_share": "their share of long balls", "time_on_ball_s": "their time on the ball",
        "forward_share": "their forward passing", "press_rate": "their pressing", "ppda": "PPDA"}

PERSONA = {
    "en": {"high_press": "{team} turn up the pressure", "low_block": "{team} sit deep",
           "go_direct": "{team} go direct", "slow_down": "{team} slow it down",
           "push_wide": "{team} spread it wide", "unclear": "{team} change things"},
    "es": {"high_press": "{team} adelanta la presión", "low_block": "{team} se repliega",
           "go_direct": "{team} juega más directo", "slow_down": "{team} baja el ritmo",
           "push_wide": "{team} abre el campo", "unclear": "{team} cambia el plan"},
    "ar": {"high_press": "{team} يرفع وتيرة الضغط", "low_block": "{team} يتراجع إلى الخلف",
           "go_direct": "{team} يلعب بشكل مباشر", "slow_down": "{team} يهدّئ اللعب",
           "push_wide": "{team} يوسّع الملعب", "unclear": "{team} يغيّر أسلوبه"},
}
BODY = {
    "en": ("{what}: {before} to {after}.", "A clear change in how they play."),
    "es": ("{what}: de {before} a {after}.", "Un cambio claro en su forma de jugar."),
    "ar": ("{what}: من {before} إلى {after}.", "تغيير واضح في أسلوب لعبهم."),
}


def _fmt(key: str, v: float) -> str:
    return f"{v:.0f}" if key.endswith("_m") else (f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}")


class _ScriptedClient(BaseChatClient):
    OTEL_PROVIDER_NAME: ClassVar[str] = "GroundTruthScripted"

    def __init__(self, *, bad_first_draft: bool = False, judge_rejects_first: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bad_first_draft = bad_first_draft
        self.judge_rejects_first = judge_rejects_first
        self.calls: list[str] = []

    def _inner_get_response(self, *, messages: Sequence[Message], stream: bool = False,
                            options: Mapping[str, Any], **kwargs: Any) -> Awaitable[ChatResponse]:
        task: dict = {}
        for msg in reversed(messages):
            if msg.role == "user" and msg.text:
                try:
                    task = json.loads(msg.text)
                except ValueError:
                    task = {}
                break
        kind = task.get("task", "")
        self.calls.append(kind)
        reply = {"tactician": self._tactician, "judge": self._judge, "persona": self._persona}.get(
            kind, lambda t: {"error": "unknown task"})(task)

        async def respond() -> ChatResponse:
            return ChatResponse(messages=[Message(role="assistant", contents=[json.dumps(reply, ensure_ascii=False)])],
                                model="groundtruth-scripted", response_id=f"scripted-{len(self.calls)}")
        return respond()

    def _tactician(self, task: dict) -> dict:
        ev = task["evidence"]
        m = ev["moment"]
        tid, key = m["team_id"], m["signal"]
        cause = m.get("rule_cause") or "unclear"
        before, after = m["before"], m["after"]
        shown_after = after + 7.3 if self.bad_first_draft and task.get("attempt", 1) == 1 else after
        team = ev["teams"][tid]["name"]
        return {
            "team_id": tid, "cause": cause,
            "headline": HEADLINES.get(cause, HEADLINES["unclear"]).format(team=team)[:70],
            "explanation": (f"Over the last {RECENT // MINUTE} minutes {WORD.get(key, key)} moved from "
                            f"{_fmt(key, before)} to {_fmt(key, shown_after)}."),
            "why_it_matters": CONSEQUENCE.get(cause, CONSEQUENCE["unclear"]),
            "claims": [{"text": "before", "metric": key, "team_id": tid, "value": before},
                       {"text": "after", "metric": key, "team_id": tid, "value": after}],
            "confidence": 0.6,
        }

    def _judge(self, task: dict) -> dict:
        if self.judge_rejects_first and self.calls.count("judge") == 1:
            return {"supported": False, "problems": ["the explanation credits the wrong team with the change"]}
        return {"supported": True, "problems": []}

    def _persona(self, task: dict) -> dict:
        ins, lang = task["insight"], task["lang"]
        team = task["team_name"]
        head = PERSONA[lang].get(ins["cause"], PERSONA[lang]["unclear"]).format(team=team)[:60]
        claims = {c["text"]: c for c in ins.get("claims", [])}
        analyst = BODY[lang][1]
        if "before" in claims and "after" in claims:
            key = claims["after"]["metric"]
            what = task.get("metric_word") or WORD.get(key, key)
            analyst = BODY[lang][0].format(what=what[:1].upper() + what[1:],
                                           before=_fmt(key, claims["before"]["value"]),
                                           after=_fmt(key, claims["after"]["value"]))
        return {"analyst": {"headline": head, "body": analyst}, "casual": {"headline": head, "body": BODY[lang][1]}}


class ScriptedChatClient(  # type: ignore[misc]
    FunctionInvocationLayer, ChatMiddlewareLayer, ChatTelemetryLayer, _ScriptedClient
):
    """The scripted client with the same tool, middleware and telemetry layers as a real one."""

    OTEL_PROVIDER_NAME: ClassVar[str] = "GroundTruthScripted"
