"""Keeps a public demo inside its budget, and keeps score of how the agents are doing.

The guard is shared by every match on the server. When it says no, the moment
keeps its template card (already on air) and the trace says why; nothing else
changes for the viewer.

GROUNDTRUTH_AGENT_RUNS_PER_HOUR   agent runs allowed per rolling hour (default 240)
GROUNDTRUTH_TOKENS_PER_DAY        model tokens allowed per rolling day (default 4,000,000)
"""

from __future__ import annotations

import math
import os
import time
from collections import deque

HOUR, DAY = 3600.0, 86400.0


class UsageGuard:
    def __init__(self, runs_per_hour: int = 240, tokens_per_day: int = 4_000_000):
        self.runs_per_hour, self.tokens_per_day = runs_per_hour, tokens_per_day
        self._runs: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self.refused = 0

    @classmethod
    def from_env(cls) -> UsageGuard:
        return cls(int(os.environ.get("GROUNDTRUTH_AGENT_RUNS_PER_HOUR", "240")),
                   int(os.environ.get("GROUNDTRUTH_TOKENS_PER_DAY", "4000000")))

    def _prune(self, now: float) -> None:
        while self._runs and now - self._runs[0] > HOUR:
            self._runs.popleft()
        while self._tokens and now - self._tokens[0][0] > DAY:
            self._tokens.popleft()

    def admit(self) -> str | None:
        """Records a run and returns None, or returns why the run is refused."""
        now = time.time()
        self._prune(now)
        if len(self._runs) >= self.runs_per_hour:
            self.refused += 1
            return f"usage guard: {self.runs_per_hour} agent runs in the last hour"
        if sum(n for _, n in self._tokens) >= self.tokens_per_day:
            self.refused += 1
            return "usage guard: today's token budget is spent"
        self._runs.append(now)
        return None

    def spend(self, tokens: int) -> None:
        if tokens:
            self._tokens.append((time.time(), tokens))

    def view(self) -> dict:
        self._prune(time.time())
        return {"runs_last_hour": len(self._runs), "runs_per_hour": self.runs_per_hour,
                "tokens_last_day": sum(n for _, n in self._tokens), "tokens_per_day": self.tokens_per_day,
                "refused": self.refused}


GUARD = UsageGuard.from_env()


def percentile(xs: list[float], p: float) -> float | None:
    """Nearest-rank percentile: an actual observed value, honest for small samples."""
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def run_record(m, board_entries: list[dict], outcome: str, total_ms: int, attempts: int,
               insight=None) -> dict:
    """One agent run, from its board entries: where the time and the tokens went."""
    last = max((e.get("attempt", 1) for e in board_entries), default=1)
    of = lambda stage, a=None: [e for e in board_entries if e["stage"] == stage and (a is None or e.get("attempt", 1) == a)]
    written = [e for e in board_entries if e["stage"].startswith("written_") and e.get("attempt", 1) == last]
    return {
        "moment_id": m.moment_id, "t": m.t, "minute": m.clock_ms // 60_000, "period": m.period,
        "outcome": outcome, "attempts": attempts, "first_pass": outcome == "verified" and attempts == 1,
        "total_ms": total_ms,
        "tactician_ms": sum(e.get("ms", 0) for e in of("drafted")),
        "judge_ms": max((e.get("ms", 0) for e in of("judged", last)), default=None),
        "writers_ms": max((e.get("ms", 0) for e in written), default=None),
        "rejected_by": [e.get("by") for e in of("rejected")],
        "backup_used": any(e.get("model") == "backup" for e in of("drafted")),
        "tokens_in": sum(e.get("tokens_in", 0) for e in board_entries),
        "tokens_out": sum(e.get("tokens_out", 0) for e in board_entries),
        "rule_cause": m.metrics.get("rule_cause"), "flagged_team": m.team_id,
        "cause": insight.cause if insight else None, "team_id": insight.team_id if insight else None,
    }


def summarize(runs: list[dict]) -> dict:
    """The numbers to beat: time from detection to a verified card, approval rates, cost."""
    n = len(runs)
    ok = [r for r in runs if r["outcome"] == "verified"]
    total = [r["total_ms"] for r in ok]
    stage = lambda k: [r[k] for r in ok if r.get(k) is not None]
    return {
        "runs": n,
        "verified": len(ok),
        "first_pass_rate": round(sum(r["first_pass"] for r in runs) / n, 3) if n else None,
        "fallback_rate": round((n - len(ok)) / n, 3) if n else None,
        "verified_ms": {"p50": percentile(total, 50), "p95": percentile(total, 95), "max": max(total, default=None)},
        "stage_p50_ms": {k: percentile(stage(k), 50) for k in ("tactician_ms", "judge_ms", "writers_ms")},
        "rejections": {"code": sum(r["rejected_by"].count("code") for r in runs),
                       "judge": sum(r["rejected_by"].count("judge") for r in runs)},
        "backup_used": sum(r["backup_used"] for r in runs),
        "tokens_per_run": round(sum(r["tokens_in"] + r["tokens_out"] for r in runs) / n) if n else None,
        "outcomes": {k: sum(r["outcome"] == k for r in runs) for k in sorted({r["outcome"] for r in runs})},
    }
