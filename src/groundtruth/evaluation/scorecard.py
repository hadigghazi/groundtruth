"""The full-time scorecard for one match: what the system said against the answer key.

Two sets of alerts are scored against the same truth log:
* the rule: the detector's own cause for each flagged moment;
* the agents: the verified insight's team and cause for the same moments.

An alert counts for a change if it names the same team within 12 minutes of it.
The agents can name the other team (the one that really changed), so their
alerts are matched by the team they name, not the team the detector flagged.
"""

from __future__ import annotations

import statistics

from ..stats.engine import MINUTE

WINDOW = 12 * MINUTE


def score(changes: list[dict], alerts: list[dict], window: int = WINDOW) -> dict:
    """changes: {t, team_id, label}; alerts: {t, team_id, cause}. Returns totals and one row per change."""
    rows, delays = [], []
    for ch in changes:
        near = sorted((a for a in alerts if a["team_id"] == ch["team_id"] and 0 <= a["t"] - ch["t"] <= window),
                      key=lambda a: a["t"])
        hit = next((a for a in near if a["cause"] == ch["label"]), None)
        if hit:
            delays.append((hit["t"] - ch["t"]) / MINUTE)
        rows.append({"caught": bool(near), "right_cause": bool(hit),
                     "said": (hit or near[0])["cause"] if near else None,
                     "delay_min": round((hit["t"] - ch["t"]) / MINUTE, 1) if hit else None})
    false_alarms = sum(not any(a["team_id"] == ch["team_id"] and 0 <= a["t"] - ch["t"] <= window for ch in changes)
                       for a in alerts)
    n = len(changes)
    return {
        "changes": n, "alerts": len(alerts),
        "caught": sum(r["caught"] for r in rows),
        "right_cause": sum(r["right_cause"] for r in rows),
        "false_alarms": false_alarms,
        "median_delay_min": round(statistics.median(delays), 1) if delays else None,
        "rows": rows,
    }


def scorecard(changes: list[dict], moments: list, agent_results: dict[str, dict]) -> dict:
    """changes carry t, team_id, label, source, period, clock_ms; moments are the stats engine's."""
    shifts = [m for m in moments if m.kind == "tactical_shift"]
    rule = score(changes, [{"t": m.t, "team_id": m.team_id, "cause": m.metrics.get("rule_cause")} for m in shifts])
    agents = score(changes, [{"t": r["t"], "team_id": r["team_id"], "cause": r["cause"]}
                             for r in agent_results.values() if r.get("cause") and r["cause"] != "unclear"])
    rows = []
    for ch, r, a in zip(changes, rule.pop("rows"), agents.pop("rows")):
        rows.append({**{k: ch[k] for k in ("team_id", "label", "source", "period", "clock_ms")},
                     "rule": r, "agents": a})
    return {"rule": rule, "agents": agents, "changes": rows, "window_min": WINDOW // MINUTE,
            "agents_verified": len(agent_results)}
