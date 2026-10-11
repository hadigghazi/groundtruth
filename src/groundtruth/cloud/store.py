"""Everything that must outlive a replica: Azure Cosmos DB (serverless), or a dictionary.

One container, partitioned by match id, so a match's documents sit together and every
read the app makes stays inside one partition. Each document has an `id`, a `match_id`
and a `kind`:

  match     the match: clubs, seed, status (live, finished), speed, outage, meta, score
  cue       one overlay cue, as aired (including upgrades and their `replaces`)
  moment    a flagged moment
  board     the insight board for one moment: every agent hand-off with timings
  run       one agent run: outcome, latency per step, tokens
  result    a verified insight's team, cause and text, with the evidence pack it came from
  snapshot  the totals and stats windows once a minute, for answers as of a viewer's minute
  event     goals, cards and shots, for viewers who join late
  console   a change made from the director console
  truth     the truth log and the scorecard, written once, at full time
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Protocol

log = logging.getLogger("groundtruth.store")


class Store(Protocol):
    async def upsert(self, docs: list[dict]) -> None: ...
    async def get(self, match_id: str, doc_id: str) -> dict | None: ...
    async def query(self, match_id: str, kind: str) -> list[dict]: ...
    async def matches(self) -> list[dict]: ...
    async def close(self) -> None: ...


class MemoryStore:
    def __init__(self):
        self._docs: dict[str, dict[str, dict]] = defaultdict(dict)

    async def upsert(self, docs: list[dict]) -> None:
        for d in docs:
            self._docs[d["match_id"]][d["id"]] = d

    async def get(self, match_id: str, doc_id: str) -> dict | None:
        return self._docs.get(match_id, {}).get(doc_id)

    async def query(self, match_id: str, kind: str) -> list[dict]:
        return [d for d in self._docs.get(match_id, {}).values() if d.get("kind") == kind]

    async def matches(self) -> list[dict]:
        return [d for docs in self._docs.values() for d in docs.values() if d.get("kind") == "match"]

    async def close(self) -> None:
        return None


class CosmosStore:
    """Entra ID on Azure (no keys); the emulator's well-known key in CI."""

    def __init__(self, endpoint: str, *, key: str | None = None, database: str = "groundtruth",
                 container: str = "matchdata", concurrency: int = 16):
        self.endpoint, self.key, self.database_name, self.container_name = endpoint, key, database, container
        self._client = self._container = None
        self._sem = asyncio.Semaphore(concurrency)

    async def _c(self):
        if self._container is None:
            from azure.cosmos.aio import CosmosClient

            if self.key:
                self._client = CosmosClient(self.endpoint, credential=self.key, connection_verify=False)
                # the emulator starts empty; on Azure, Bicep creates both
                from azure.cosmos import PartitionKey

                db = await self._client.create_database_if_not_exists(self.database_name)
                self._container = await db.create_container_if_not_exists(self.container_name, PartitionKey("/match_id"))
            else:
                from azure.identity.aio import DefaultAzureCredential

                self._client = CosmosClient(self.endpoint, credential=DefaultAzureCredential())
                self._container = self._client.get_database_client(self.database_name).get_container_client(self.container_name)
        return self._container

    async def upsert(self, docs: list[dict]) -> None:
        c = await self._c()

        async def one(d):
            async with self._sem:
                await c.upsert_item(d)
        results = await asyncio.gather(*(one(d) for d in docs), return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                log.warning("cosmos upsert failed: %s", r)

    async def get(self, match_id: str, doc_id: str) -> dict | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        c = await self._c()
        try:
            return _clean(await c.read_item(doc_id, partition_key=match_id))
        except CosmosResourceNotFoundError:
            return None

    async def query(self, match_id: str, kind: str) -> list[dict]:
        c = await self._c()
        items = c.query_items("SELECT * FROM c WHERE c.kind = @k", parameters=[{"name": "@k", "value": kind}],
                              partition_key=match_id)
        return [_clean(d) async for d in items]

    async def matches(self) -> list[dict]:
        c = await self._c()
        items = c.query_items("SELECT * FROM c WHERE c.kind = 'match'")
        return [_clean(d) async for d in items]

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}
