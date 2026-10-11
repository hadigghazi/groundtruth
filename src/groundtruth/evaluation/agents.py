"""Score the agent team on held-out matches against the truth log.

Each match is played live and paced (so the agents work under broadcast conditions,
not with the whole match already known), with the agent team explaining every
flagged shift. At full time the scorecard compares the agents' causes with the
detector's own rule on the same moments, and the runs give latency and token use.

With GROUNDTRUTH_EVAL_DEPLOYMENT (and AZURE_OPENAI_ENDPOINT) set, every verified
insight is also graded by Azure AI Evaluation: groundedness against the evidence
pack it was written from, relevance and coherence.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os

from ..agents.guard import UsageGuard, summarize
from ..agents.hook import attach_agents
from ..league import build_league
from ..live.session import MatchSession

TEST_SEEDS = list(range(400, 412))  # the detector's held-out seeds: never used to choose anything


def _fixtures(seeds: list[int]) -> list[tuple[str, str, int]]:
    pairs = itertools.cycle(itertools.permutations(build_league(), 2))
    return [(*next(pairs), s) for s in seeds]


async def _play(home: str, away: str, seed: int, speed: float, kind: str | None, budget_s: float) -> dict:
    league = build_league()
    s = MatchSession(league[home], league[away], seed=seed, match_id=f"ae{seed}", speed=speed)
    attach_agents(s, kind=kind, budget_s=budget_s, guard=UsageGuard(10_000, 10**9))
    await s.run()
    # the pack each insight was written from, kept at the time: the stats windows move on
    insights = [{"moment_id": mid, "cause": r["cause"], "text": r["text"], "evidence": r["pack"]}
                for mid, r in s.agent_results.items() if r.get("text") and r.get("pack")]
    return {"seed": seed, "home": home, "away": away, "scorecard": s.scorecard(), "runs": s.agent_runs,
            "insights": insights}


def _grade(insights: list[dict]) -> dict:
    """Azure AI Evaluation scores, 1 to 5, averaged; skipped when no judge deployment is configured."""
    dep = os.environ.get("GROUNDTRUTH_EVAL_DEPLOYMENT")
    if not dep or not insights:
        return {"ran": False, "why": "set GROUNDTRUTH_EVAL_DEPLOYMENT and AZURE_OPENAI_ENDPOINT to grade"}
    try:
        from azure.ai.evaluation import CoherenceEvaluator, GroundednessEvaluator, RelevanceEvaluator
        from azure.identity import DefaultAzureCredential

        config = {"azure_endpoint": os.environ["AZURE_OPENAI_ENDPOINT"], "azure_deployment": dep,
                  "api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")}
        cred = DefaultAzureCredential()
        evaluators = {"groundedness": GroundednessEvaluator(config, credential=cred),
                      "relevance": RelevanceEvaluator(config, credential=cred),
                      "coherence": CoherenceEvaluator(config, credential=cred)}
    except Exception as e:  # the evaluation SDK is optional
        return {"ran": False, "why": f"{type(e).__name__}: {e}"}
    query = "Explain the tactical change the detector flagged, using only this match's evidence."
    scores: dict[str, list[float]] = {k: [] for k in evaluators}
    for ins in insights:
        context = json.dumps(ins["evidence"], separators=(",", ":"))
        for name, ev in evaluators.items():
            try:
                out = ev(query=query, response=ins["text"], context=context) if name == "groundedness" \
                    else ev(query=query, response=ins["text"])
                v = out.get(name)
                if isinstance(v, (int, float)):
                    scores[name].append(float(v))
            except Exception:
                continue
    return {"ran": True, "n": len(insights),
            **{k: round(sum(v) / len(v), 2) if v else None for k, v in scores.items()}}


def evaluate(seeds: list[int] | None = None, speed: float = 8.0, kind: str | None = None,
             budget_s: float = 20.0, concurrency: int = 4) -> dict:
    fixtures = _fixtures(seeds or TEST_SEEDS)

    async def run_all():
        sem = asyncio.Semaphore(concurrency)

        async def one(f):
            async with sem:
                return await _play(*f, speed=speed, kind=kind, budget_s=budget_s)
        return await asyncio.gather(*(one(f) for f in fixtures))

    matches = asyncio.run(run_all())
    total: dict[str, dict[str, int]] = {}
    for side in ("rule", "agents"):
        total[side] = {k: sum(m["scorecard"][side][k] for m in matches)
                       for k in ("changes", "alerts", "caught", "right_cause", "false_alarms")}
    runs = [r for m in matches for r in m["runs"]]
    insights = [i for m in matches for i in m["insights"]]
    return {
        "matches": len(matches), "seeds": [m["seed"] for m in matches], "speed": speed,
        "model": {"kind": kind or os.environ.get("GROUNDTRUTH_LLM", "scripted"),
                  "large": os.environ.get("GROUNDTRUTH_MODEL_LARGE"), "small": os.environ.get("GROUNDTRUTH_MODEL_SMALL")},
        "right_cause": {side: f"{t['right_cause']}/{t['changes']}" for side, t in total.items()},
        "totals": total,
        "latency": summarize(runs),
        "quality": _grade(insights),
        "per_match": [{"seed": m["seed"], "fixture": f"{m['home']} v {m['away']}",
                       "rule": {k: m["scorecard"]["rule"][k] for k in ("right_cause", "changes", "false_alarms")},
                       "agents": {k: m["scorecard"]["agents"][k] for k in ("right_cause", "changes", "false_alarms")}}
                      for m in matches],
    }
