"""The message bus between the roles: Azure Event Hubs, or an in-process stand-in.

Topics:
  events   the simulator's feed. One "meta" per match, then "tick"s (positions at 10 Hz and the
           events that resolved in that tick), then "end". The partition key is the match id,
           so a match's messages stay in order on one partition and one worker owns the match.
  control  commands: start, console, speed, outage, and agents_done from the workers.

The truth log is never on the bus: it stays inside the simulator until full time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

log = logging.getLogger("groundtruth.bus")

TOPICS = ("events", "control")
ENVELOPE_BYTES = 200_000  # messages travel packed: one event carries many ticks, well under the 1 MB limit
RETRY_FOR_S = 90.0        # how long a throttled send keeps retrying before it gives up


class Delivery(Protocol):
    partition: str

    async def checkpoint(self) -> None: ...


Handler = Callable[[dict, Delivery], Awaitable[None]]


class Bus(Protocol):
    async def publish(self, topic: str, key: str, messages: list[dict]) -> None: ...
    async def consume(self, topic: str, group: str, handler: Handler) -> None: ...  # runs until cancelled
    async def close(self) -> None: ...


# ------------------------------------------------------------------ in process

class _MemoryDelivery:
    partition = "0"

    async def checkpoint(self) -> None:
        return None


class MemoryBus:
    """Every consumer group gets every message, in order. Bounded queues push back on a fast producer."""

    def __init__(self, maxsize: int = 4000):
        self.maxsize = maxsize
        self._groups: dict[str, dict[str, asyncio.Queue]] = {t: {} for t in TOPICS}
        self._ready = {t: asyncio.Event() for t in TOPICS}

    def _queue(self, topic: str, group: str) -> asyncio.Queue:
        q = self._groups[topic].get(group)
        if q is None:
            q = self._groups[topic][group] = asyncio.Queue(self.maxsize)
            self._ready[topic].set()
        return q

    async def publish(self, topic: str, key: str, messages: list[dict]) -> None:
        if not self._groups[topic]:  # nobody listens yet: wait briefly for the first consumer
            try:
                await asyncio.wait_for(self._ready[topic].wait(), timeout=5)
            except asyncio.TimeoutError:
                log.warning("no consumer on %s; %d messages dropped", topic, len(messages))
                return
        for q in list(self._groups[topic].values()):
            for m in messages:
                await q.put(m)

    async def consume(self, topic: str, group: str, handler: Handler) -> None:
        q, d = self._queue(topic, group), _MemoryDelivery()
        while True:
            msg = await q.get()
            try:
                await handler(msg, d)
            except Exception:  # one bad message must never stop the consumer
                log.exception("handler failed on %s", topic)

    async def close(self) -> None:
        return None


# ------------------------------------------------------------------ Azure Event Hubs

class _HubDelivery:
    def __init__(self, ctx, event):
        self._ctx, self._event = ctx, event
        self.partition = ctx.partition_id

    async def checkpoint(self) -> None:
        await self._ctx.update_checkpoint(self._event)


class EventHubsBus:
    """One Event Hub per topic. Managed identity on Azure; a connection string for the emulator."""

    def __init__(self, *, namespace: str | None = None, connection_string: str | None = None,
                 checkpoint_url: str | None = None, checkpoint_connection_string: str | None = None,
                 checkpoint_container: str = "checkpoints", hubs: dict[str, str] | None = None):
        self.namespace, self.connection_string = namespace, connection_string
        self.checkpoint_url, self.checkpoint_cs = checkpoint_url, checkpoint_connection_string
        self.container = checkpoint_container
        self.hubs = hubs or {"events": "match-events", "control": "match-control"}
        self._producers: dict[str, object] = {}
        self._cred = None

    def _credential(self):
        if self._cred is None:
            from azure.identity.aio import DefaultAzureCredential

            self._cred = DefaultAzureCredential()
        return self._cred

    def _producer(self, topic: str):
        p = self._producers.get(topic)
        if p is None:
            from azure.eventhub.aio import EventHubProducerClient

            hub = self.hubs[topic]
            p = (EventHubProducerClient.from_connection_string(self.connection_string, eventhub_name=hub)
                 if self.connection_string else
                 EventHubProducerClient(self.namespace, hub, self._credential()))
            self._producers[topic] = p
        return p

    @staticmethod
    def _envelopes(messages: list[dict]) -> list[str]:
        """Packs messages into as few events as fit: a throughput unit counts events, not messages."""
        out, part, size = [], [], 0
        for m in messages:
            text = json.dumps(m, separators=(",", ":"))
            if part and size + len(text) > ENVELOPE_BYTES:
                out.append('{"many":[' + ",".join(part) + "]}")
                part, size = [], 0
            part.append(text)
            size += len(text) + 1
        if part:
            out.append('{"many":[' + ",".join(part) + "]}")
        return out

    async def _send(self, producer, batch) -> None:
        """Sends, backing off while the hub is throttling; a long match must not die of a busy minute."""
        deadline, wait = time.monotonic() + RETRY_FOR_S, 1.0
        while True:
            try:
                await producer.send_batch(batch)
                return
            except Exception as e:
                busy = "server-busy" in str(e) or "throttl" in str(e).lower() or "timeout" in type(e).__name__.lower()
                if not busy or time.monotonic() + wait > deadline:
                    raise
                log.warning("event hub busy; retrying in %.0f s", wait)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 15.0)

    async def publish(self, topic: str, key: str, messages: list[dict]) -> None:
        from azure.eventhub import EventData

        producer = self._producer(topic)
        batch = await producer.create_batch(partition_key=key)
        for env in self._envelopes(messages):
            data = EventData(env)
            try:
                batch.add(data)
            except ValueError:  # the batch is full: send it and start another
                await self._send(producer, batch)
                batch = await producer.create_batch(partition_key=key)
                batch.add(data)
        if len(batch):
            await self._send(producer, batch)

    def _checkpoints(self):
        from azure.eventhub.extensions.checkpointstoreblobaio import BlobCheckpointStore

        if self.checkpoint_cs:
            return BlobCheckpointStore.from_connection_string(self.checkpoint_cs, self.container)
        if self.checkpoint_url:
            return BlobCheckpointStore(self.checkpoint_url, self.container, credential=self._credential())
        return None

    async def consume(self, topic: str, group: str, handler: Handler) -> None:
        from azure.eventhub.aio import EventHubConsumerClient

        hub, store = self.hubs[topic], self._checkpoints()
        client = (EventHubConsumerClient.from_connection_string(self.connection_string, group, eventhub_name=hub,
                                                                checkpoint_store=store)
                  if self.connection_string else
                  EventHubConsumerClient(self.namespace, hub, group, self._credential(), checkpoint_store=store))

        async def on_batch(ctx, events):
            for e in events:
                body = json.loads(e.body_as_str())
                d = _HubDelivery(ctx, e)
                for msg in body.get("many", [body]):
                    try:
                        await handler(msg, d)
                    except Exception:
                        log.exception("handler failed on %s", topic)

        async with client:
            # with a checkpoint store, replicas share the partitions between them; a new owner
            # resumes from the last checkpoint, so a match in progress is replayed, not lost
            await client.receive_batch(on_event_batch=on_batch, starting_position="-1",
                                       max_batch_size=300, max_wait_time=0.25)

    async def close(self) -> None:
        for p in self._producers.values():
            await p.close()
        if self._cred is not None:
            await self._cred.close()
