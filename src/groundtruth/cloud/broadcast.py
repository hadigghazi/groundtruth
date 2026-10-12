"""Getting live messages to viewers: Azure Web PubSub, or our own WebSocket.

A match is one group. The worker sends each message once; Web PubSub delivers it to every
viewer of that match, so viewers never connect to our servers and personalisation (done in
the browser from the shared stream) costs nothing per viewer. Messages are sent in batches
as {"type": "many", "messages": [...]} to keep the message count, and the bill, down.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Protocol

log = logging.getLogger("groundtruth.broadcast")


def group(match_id: str) -> str:
    return f"match.{match_id}"


class Broadcaster(Protocol):
    local: bool

    async def send(self, match_id: str, messages: list[dict]) -> None: ...
    async def client_url(self, match_id: str) -> str | None: ...
    async def close(self) -> None: ...


class LocalBroadcaster:
    """Fans out to the WebSocket connections this process holds."""

    local = True

    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, match_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=4000)
        self._subs[match_id].add(q)
        return q

    def unsubscribe(self, match_id: str, q: asyncio.Queue) -> None:
        self._subs[match_id].discard(q)

    async def send(self, match_id: str, messages: list[dict]) -> None:
        if not messages:
            return
        packed = {"type": "many", "messages": messages}
        for q in list(self._subs.get(match_id, ())):
            try:
                q.put_nowait(packed)
            except asyncio.QueueFull:  # a slow viewer loses messages, never the match
                pass

    async def client_url(self, match_id: str) -> str | None:
        return None

    async def close(self) -> None:
        return None


class WebPubSubBroadcaster:
    local = False

    def __init__(self, endpoint: str, hub: str = "groundtruth", *, connection_string: str | None = None):
        self.endpoint, self.hub, self.cs = endpoint, hub, connection_string
        self._client = None

    def _c(self):
        if self._client is None:
            from azure.messaging.webpubsubservice.aio import WebPubSubServiceClient

            if self.cs:
                self._client = WebPubSubServiceClient.from_connection_string(self.cs, hub=self.hub)
            else:
                from azure.identity.aio import DefaultAzureCredential

                self._client = WebPubSubServiceClient(self.endpoint, hub=self.hub, credential=DefaultAzureCredential())
        return self._client

    async def send(self, match_id: str, messages: list[dict]) -> None:
        if not messages:
            return
        body = json.dumps({"type": "many", "messages": messages}, separators=(",", ":"))
        try:
            # text/plain: the body is already JSON. With application/json the SDK encodes the string
            # again and viewers receive a quoted string they cannot read.
            await self._c().send_to_group(group(match_id), body, content_type="text/plain")
        except Exception as e:  # a delivery hiccup must not stop the match
            log.warning("web pubsub send failed: %s", e)

    async def client_url(self, match_id: str) -> str | None:
        token = await self._c().get_client_access_token(groups=[group(match_id)], minutes_to_expire=180)
        return token["url"]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
