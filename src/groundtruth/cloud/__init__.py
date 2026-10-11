"""The platform the roles run on: the bus, the store and the broadcaster, chosen by environment.

EVENTHUB_NAMESPACE (or EVENTHUB_CONNECTION_STRING for the emulator)   the bus, else in process
CHECKPOINT_BLOB_URL (or CHECKPOINT_CONNECTION_STRING)                  Event Hubs checkpoints
COSMOS_ENDPOINT (and COSMOS_KEY for the emulator)                      the store, else in memory
WEBPUBSUB_ENDPOINT (or WEBPUBSUB_CONNECTION_STRING)                    delivery, else our WebSocket
GROUNDTRUTH_ROLES   which roles this process runs: simulator, worker, api (default: all three)

With nothing set, one process runs everything in memory: the same code paths as on Azure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .broadcast import Broadcaster, LocalBroadcaster, WebPubSubBroadcaster
from .bus import Bus, EventHubsBus, MemoryBus
from .store import CosmosStore, MemoryStore, Store

ROLES = ("simulator", "worker", "api")


@dataclass
class Platform:
    bus: Bus
    store: Store
    broadcaster: Broadcaster
    roles: tuple[str, ...]

    @property
    def describe(self) -> dict:
        return {"bus": type(self.bus).__name__, "store": type(self.store).__name__,
                "broadcaster": type(self.broadcaster).__name__, "roles": list(self.roles)}

    async def close(self) -> None:
        for part in (self.bus, self.store, self.broadcaster):
            await part.close()


def from_env() -> Platform:
    env = os.environ.get
    if env("EVENTHUB_NAMESPACE") or env("EVENTHUB_CONNECTION_STRING"):
        bus: Bus = EventHubsBus(namespace=env("EVENTHUB_NAMESPACE"), connection_string=env("EVENTHUB_CONNECTION_STRING"),
                                checkpoint_url=env("CHECKPOINT_BLOB_URL"),
                                checkpoint_connection_string=env("CHECKPOINT_CONNECTION_STRING"))
    else:
        bus = MemoryBus()
    store: Store = CosmosStore(env("COSMOS_ENDPOINT"), key=env("COSMOS_KEY")) if env("COSMOS_ENDPOINT") else MemoryStore()
    if env("WEBPUBSUB_ENDPOINT") or env("WEBPUBSUB_CONNECTION_STRING"):
        broadcaster: Broadcaster = WebPubSubBroadcaster(env("WEBPUBSUB_ENDPOINT", ""),
                                                        connection_string=env("WEBPUBSUB_CONNECTION_STRING"))
    else:
        broadcaster = LocalBroadcaster()
    roles = tuple(r.strip() for r in env("GROUNDTRUTH_ROLES", ",".join(ROLES)).split(",") if r.strip())
    return Platform(bus, store, broadcaster, roles)


def local() -> Platform:
    """Everything in this process, in memory."""
    return Platform(MemoryBus(), MemoryStore(), LocalBroadcaster(), ROLES)
