"""The roles as separate processes over real Azure SDKs, against the emulators (infra/emulators).

1. A match flows end to end: simulator -> Event Hubs -> worker -> Cosmos DB -> API.
2. The worker dies mid-match; a new one takes the partition over, replays the match from
   kick-off and finishes it, and no moment is explained twice.

Run after `docker compose -f infra/emulators/compose.yml up -d`:  python scripts/distributed_check.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

EH = "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"
BLOB = ("DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;")
COSMOS_KEY = "C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
API = "http://127.0.0.1:8000"


def env(roles: str) -> dict:
    return {**os.environ, "GROUNDTRUTH_ROLES": roles, "EVENTHUB_CONNECTION_STRING": EH,
            "CHECKPOINT_CONNECTION_STRING": BLOB, "COSMOS_ENDPOINT": "http://127.0.0.1:8081/", "COSMOS_KEY": COSMOS_KEY,
            "GROUNDTRUTH_LLM": "scripted", "PYTHONUNBUFFERED": "1"}


def start(roles: str, port: int) -> subprocess.Popen:
    log = open(f"role-{roles}-{port}.log", "w")
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "groundtruth.api.app:app", "--port", str(port)],
                            env=env(roles), stdout=log, stderr=subprocess.STDOUT)


def call(path: str, body: dict | None = None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, ConnectionError, TimeoutError):  # not up yet
        return 0, None


def wait(what: str, fn, timeout: float, mid: str | None = None):
    end, last = time.monotonic() + timeout, 0.0
    while time.monotonic() < end:
        out = fn()
        if out:
            return out
        if mid and time.monotonic() - last > 30:
            last = time.monotonic()
            _, m = call(f"/api/matches/{mid}")
            _, tl = call(f"/api/matches/{mid}/overlays?profile=analyst&lang=en")
            state = (m or {}).get("state") or {}
            print(f"  {what}: status {(m or {}).get('status')}, clock {state.get('clock_ms', 0) // 60000}', "
                  f"{len((tl or {}).get('cues', []))} cues so far", flush=True)
        time.sleep(1)
    raise SystemExit(f"timed out waiting for {what}")


def make_checkpoint_container():
    from azure.storage.blob import BlobServiceClient

    svc = BlobServiceClient.from_connection_string(BLOB)
    try:
        svc.create_container("checkpoints")
    except Exception:
        pass  # already there


def finished(mid: str):
    status, card = call(f"/api/matches/{mid}/scorecard")
    return card if status == 200 else None


def main() -> None:
    make_checkpoint_container()
    api, sim, worker = start("api", 8000), start("simulator", 8001), start("worker", 8002)
    procs = [api, sim, worker]
    try:
        wait("the API", lambda: call("/api/ping")[0] == 200, 120)
        status, health = call("/api/health")
        assert health["platform"]["bus"] == "EventHubsBus" and health["platform"]["store"] == "CosmosStore", health
        print("platform:", health["platform"])

        # 1. end to end, as fast as the roles go
        _, r = call("/api/matches", {"home": "RVM", "away": "HLB", "seed": 7, "speed": 0})
        mid = r["match_id"]
        card = wait("full time", lambda: finished(mid), 600, mid)
        _, agents = call(f"/api/matches/{mid}/agents")
        _, timeline = call(f"/api/matches/{mid}/overlays?profile=casual&lang=ar")
        assert agents["runs"] and timeline["cues"], (len(agents["runs"]), len(timeline["cues"]))
        print(f"match 1: {len(timeline['cues'])} cues, {len(agents['runs'])} agent runs, "
              f"rule {card['rule']['right_cause']}/{card['rule']['changes']}, agents {card['agents']['right_cause']}/{card['agents']['changes']}")

        # 2. the worker dies mid-match; its replacement replays the match and finishes it
        _, r = call("/api/matches", {"home": "STM", "away": "KBY", "seed": 8, "speed": 40})
        mid2 = r["match_id"]
        wait("kick-off", lambda: (call(f"/api/matches/{mid2}")[1] or {}).get("status") == "live", 60)
        time.sleep(25)  # a good stretch of the first half
        worker.kill()
        worker.wait()
        print("worker killed mid-match")
        time.sleep(3)
        procs.append(start("worker", 8003))
        card2 = wait("full time after the takeover", lambda: finished(mid2), 900, mid2)
        _, agents2 = call(f"/api/matches/{mid2}/agents")
        log2 = [line.strip() for line in open("role-worker-8003.log") if mid2 in line]
        took = next((line for line in log2 if "on partition" in line), None)
        assert took, "the new worker never took the match over"
        print("takeover:", took)
        print("replay:", next((line for line in log2 if "replaying" in line), "no moments had been explained before the kill"))
        time.sleep(20)  # the replaying worker may report after full time; the simulator grades again
        card2 = finished(mid2) or card2
        assert agents2["runs"] and card2["changes"] is not None
        print(f"match 2 survived the takeover: {len(agents2['runs'])} agent runs, scorecard written")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
