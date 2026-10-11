"""The match's read-only tools over MCP (streamable HTTP at /mcp/).

The same functions the desk agents use, so any MCP client (an IDE assistant, another
agent) can ask about a live match. Every tool takes an optional match minute and then
answers as of that minute, exactly as a delayed viewer would see it. The answer key
stays closed until full time here too.
"""

from __future__ import annotations

import contextlib
import logging

from ..stats.engine import MINUTE


def mount_mcp(app, sessions: dict):
    from mcp.server.fastmcp import FastMCP

    from ..agents.desk import brief, desk_functions
    from ..agents.pipeline import AS_OF

    # DNS-rebinding protection guards servers on a user's own machine; this one is a public,
    # read-only server reached under whatever host name it is deployed at, so it is off.
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        security = {"transport_security": TransportSecuritySettings(enable_dns_rebinding_protection=False)}
    except ImportError:  # older SDKs have no such check
        security = {}
    mcp = FastMCP("GroundTruth", stateless_http=True, json_response=True, **security)
    mcp.settings.streamable_http_path = "/"

    def session(match_id: str):
        s = sessions.get(match_id)
        if s is None:
            raise ValueError(f"no match {match_id}; call list_matches")
        return s

    def as_of(s, minute: int | None) -> int:
        if minute is None:
            return s.stats.t
        for snap in s.minutes:
            if snap["clock_ms"] >= minute * MINUTE:
                return snap["t"]
        return s.stats.t

    def call(match_id: str, minute: int | None, name: str, *args):
        s = session(match_id)
        token = AS_OF.set(as_of(s, minute))
        try:
            return desk_functions(s)[name](*args)
        finally:
            AS_OF.reset(token)

    @mcp.tool()
    def list_matches() -> list[dict]:
        """The matches on this server: id, clubs, score, minute and whether it has finished."""
        return [{"match_id": k, "home": s.meta.home.name, "away": s.meta.away.name, "score": s.engine.score,
                 "minute": s.stats.clock_ms // MINUTE, "finished": s.finished} for k, s in sessions.items()]

    @mcp.tool()
    def match_brief(match_id: str, minute: int | None = None) -> dict:
        """Score, team totals, key moments and the agents' verified insights, as of a minute (default: now)."""
        s = session(match_id)
        return brief(s, as_of(s, minute))

    @mcp.tool()
    def team_window(match_id: str, team_id: str, minutes_back_start: int, minutes_back_end: int = 0,
                    minute: int | None = None) -> dict:
        """Pressing, shape, tempo and directness metrics for one team over a stretch counted back from a minute."""
        return call(match_id, minute, "team_window", team_id, minutes_back_start, minutes_back_end)

    @mcp.tool()
    def player_report(match_id: str, team_id: str, minute: int | None = None) -> list[dict]:
        """Each player's distance, top speed, passing and shooting, as of a minute."""
        return call(match_id, minute, "player_report", team_id)

    @mcp.tool()
    def key_moments(match_id: str, kind: str = "", minute: int | None = None) -> list[dict]:
        """Goals, cards, big chances, tactical shifts and substitutions up to a minute; optionally one kind."""
        return call(match_id, minute, "key_moments", kind)

    @mcp.tool()
    def scorecard(match_id: str) -> dict:
        """What the detector and the agents said against the hidden truth log. Only after full time."""
        s = session(match_id)
        if not s.finished:
            raise ValueError("the scorecard opens at full time")
        return s.full_time_card or s.scorecard()

    # A session manager runs once, so each start of the app gets a fresh one behind a proxy.
    current = {"app": None}

    async def proxy(scope, receive, send):
        if current["app"] is None:
            await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"mcp is not running"})
            return
        await current["app"](scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            mcp._session_manager = None
            current["app"] = mcp.streamable_http_app()
            runner = mcp.session_manager.run()
            await runner.__aenter__()
        except Exception:  # MCP must never stop the match server from starting
            logging.getLogger("groundtruth").exception("mcp did not start")
            current["app"], runner = None, None
        try:
            yield
        finally:
            if runner is not None:
                await runner.__aexit__(None, None, None)
            current["app"] = None

    app.router.lifespan_context = lifespan
    app.mount("/mcp", proxy)
    return mcp
