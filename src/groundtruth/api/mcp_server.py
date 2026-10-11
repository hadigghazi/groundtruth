"""The match's read-only tools over MCP (streamable HTTP at /mcp/).

The same functions the desk agents use, read from the store, so any MCP client (an IDE
assistant, another agent) can ask about a live match. Every tool takes an optional match
minute and answers as of that minute, exactly as a delayed viewer would see it. The
answer key stays closed until full time here too.
"""

from __future__ import annotations

import contextlib
import logging

from ..stats.engine import MINUTE

log = logging.getLogger("groundtruth.mcp")


class McpMount:
    def __init__(self, mcp):
        self.mcp = mcp
        self.app = None

    async def asgi(self, scope, receive, send):
        if self.app is None:
            await send({"type": "http.response.start", "status": 503, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"mcp is not running"})
            return
        await self.app(scope, receive, send)

    @contextlib.asynccontextmanager
    async def running(self):
        """A session manager runs once, so each start of the app gets a fresh one."""
        runner = None
        try:
            self.mcp._session_manager = None
            self.app = self.mcp.streamable_http_app()
            runner = self.mcp.session_manager.run()
            await runner.__aenter__()
        except Exception:  # MCP must never stop the match server from starting
            log.exception("mcp did not start")
            self.app, runner = None, None
        try:
            yield
        finally:
            if runner is not None:
                await runner.__aexit__(None, None, None)
            self.app = None


def mount_mcp(app, platform) -> McpMount:
    """`platform` is a callable returning the running Platform."""
    from mcp.server.fastmcp import FastMCP

    from ..agents.desk import brief, desk_functions
    from ..agents.pipeline import AS_OF
    from ..live.view import StoreView

    # DNS-rebinding protection guards servers on a user's own machine; this one is a public,
    # read-only server reached under whatever host name it is deployed at, so it is off.
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        security = {"transport_security": TransportSecuritySettings(enable_dns_rebinding_protection=False)}
    except ImportError:  # older SDKs have no such check
        security = {}
    mcp = FastMCP("GroundTruth", stateless_http=True, json_response=True, **security)
    mcp.settings.streamable_http_path = "/"

    async def view(match_id: str) -> StoreView:
        v = await StoreView.load(platform().store, match_id)
        if v is None:
            raise ValueError(f"no match {match_id} in play; call list_matches")
        return v

    def as_of(v: StoreView, minute: int | None) -> int:
        if minute is None:
            return v.t
        for snap in v.minutes:
            if snap["clock_ms"] >= minute * MINUTE:
                return snap["t"]
        return v.t

    async def call(match_id: str, minute: int | None, name: str, *args):
        v = await view(match_id)
        token = AS_OF.set(as_of(v, minute))
        try:
            return desk_functions(v)[name](*args)
        finally:
            AS_OF.reset(token)

    @mcp.tool()
    async def list_matches() -> list[dict]:
        """The matches on this server: id, clubs, score and status (starting, live, finished)."""
        return [{"match_id": m["match_id"], "home": m["home"], "away": m["away"], "score": m.get("score", [0, 0]),
                 "status": m.get("status")} for m in await platform().store.matches()]

    @mcp.tool()
    async def match_brief(match_id: str, minute: int | None = None) -> dict:
        """Score, team totals, key moments and the agents' verified insights, as of a minute (default: now)."""
        v = await view(match_id)
        return brief(v, as_of(v, minute))

    @mcp.tool()
    async def team_window(match_id: str, team_id: str, minutes_before: int = 0, minute: int | None = None) -> dict:
        """One team's pressing, shape, tempo and directness: the last 10 minutes and the 20 before them."""
        return await call(match_id, minute, "team_window", team_id, minutes_before)

    @mcp.tool()
    async def player_report(match_id: str, team_id: str, minute: int | None = None) -> list[dict]:
        """Each player's distance, top speed, passing and shooting, as of a minute."""
        return await call(match_id, minute, "player_report", team_id)

    @mcp.tool()
    async def key_moments(match_id: str, kind: str = "", minute: int | None = None) -> list[dict]:
        """Goals, cards, big chances, tactical shifts and substitutions up to a minute; optionally one kind."""
        return await call(match_id, minute, "key_moments", kind)

    @mcp.tool()
    async def scorecard(match_id: str) -> dict:
        """What the detector and the agents said against the hidden truth log. Only after full time."""
        truth = await platform().store.get(match_id, f"truth-{match_id}")
        if truth is None:
            raise ValueError("the scorecard opens at full time")
        return truth["scorecard"]

    mount = McpMount(mcp)
    app.mount("/mcp", mount.asgi)
    return mount
