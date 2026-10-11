"""The API end to end: start a match, stream it, steer it from the director console."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from groundtruth.api.app import app


def _kicked_off(client, mid, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if client.get(f"/api/matches/{mid}").json().get("meta"):
            return
        time.sleep(0.05)
    raise AssertionError(f"{mid} never kicked off")


def test_health_and_league():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"] is True
        clubs = client.get("/api/league").json()
        assert {c["club_id"] for c in clubs} == {"RVM", "HLB", "STM", "KBY"}


def test_live_match_streams_and_takes_console_changes():
    with TestClient(app) as client:
        r = client.post("/api/matches", json={"home": "RVM", "away": "HLB", "seed": 3, "speed": 0})
        assert r.status_code == 201
        mid = r.json()["match_id"]
        with client.websocket_connect(f"/api/matches/{mid}/stream") as ws:
            assert ws.receive_json()["type"] == "hello"
            kinds = set()
            for _ in range(5000):
                msg = ws.receive_json()
                kinds |= {m["type"] for m in msg.get("messages", [msg])}  # the stream arrives in batches
                if {"frame", "event", "state"} <= kinds:
                    break
            assert {"frame", "event", "state"} <= kinds
        r = client.post(f"/api/matches/{mid}/console", json={"team_id": "HLB", "label": "high_press"})
        if r.status_code == 200:  # the match may already be over at full speed
            assert r.json()["console"]["label"] == "high_press"
        else:
            assert r.status_code == 409


def test_console_rejects_unknown_changes_and_teams():
    with TestClient(app) as client:
        mid = client.post("/api/matches", json={"home": "STM", "away": "KBY", "seed": 4, "speed": 1}).json()["match_id"]
        assert client.post(f"/api/matches/{mid}/console", json={"team_id": "STM", "label": "park_the_bus"}).status_code == 422
        assert client.post(f"/api/matches/{mid}/console", json={"team_id": "RVM", "label": "high_press"}).status_code == 422


def test_truth_stays_hidden_until_full_time():
    with TestClient(app) as client:
        mid = client.post("/api/matches", json={"home": "KBY", "away": "RVM", "seed": 5, "speed": 1}).json()["match_id"]
        assert client.get(f"/api/matches/{mid}/truth").status_code == 409


def test_cue_schema_is_versioned_and_stable():
    with TestClient(app) as client:
        schema = client.get("/api/schema/cue").json()
    assert schema["x-schema-version"] == "1.2"
    # A graphics system depends on these; change them only with a version bump.
    assert set(schema["properties"]) == {
        "cue_id", "match_id", "moment_id", "insight_id", "t_in", "t_out", "start", "end", "layer", "template",
        "priority", "profile", "lang", "team_id", "anchor_player_id", "headline", "body", "metrics", "evidence",
        "verified", "fallback", "path", "replaces"}


def test_overlays_scorecard_and_agent_metrics():
    with TestClient(app) as client:
        mid = client.post("/api/matches", json={"home": "HLB", "away": "STM", "seed": 6, "speed": 1}).json()["match_id"]
        _kicked_off(client, mid)
        tl = client.get(f"/api/matches/{mid}/overlays", params={"profile": "casual", "lang": "es"}).json()
        assert tl["schema_version"] == "1.2" and tl["profile"] == "casual" and isinstance(tl["cues"], list)
        assert client.get(f"/api/matches/{mid}/scorecard").status_code == 409
        agents = client.get(f"/api/matches/{mid}/agents").json()
        assert agents["outage"] is False and "summary" in agents
        recap = client.post(f"/api/matches/{mid}/catchup", json={"lang": "es"}).json()
        assert recap["headline"] and recap["verified"]
        assert client.post(f"/api/matches/{mid}/agents/outage", json={"on": True}).json()["outage"] is True
        assert client.post(f"/api/matches/{mid}/catchup", json={"lang": "en"}).json()["fallback"] is True
        assert client.post(f"/api/matches/{mid}/ask", json={"question": "Who leads?"}).status_code == 503
        assert client.get("/api/agents").json()["matches"] >= 1


def test_mcp_lists_the_match_tools():
    with TestClient(app) as client:
        r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        headers={"accept": "application/json, text/event-stream"})
        assert r.status_code == 200, r.text
        for name in ("list_matches", "match_brief", "team_window", "player_report", "key_moments", "scorecard"):
            assert name in r.text


def test_the_api_reports_its_platform_and_streams_from_the_store():
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["platform"]["bus"] == "MemoryBus" and set(health["platform"]["roles"]) == {"simulator", "worker", "api"}
        mid = client.post("/api/matches", json={"home": "KBY", "away": "STM", "seed": 9, "speed": 2}).json()["match_id"]
        _kicked_off(client, mid)
        c = client.get(f"/api/matches/{mid}/connect").json()
        assert c["url"] is None and c["hello"]["meta"]["match_id"] == mid  # local mode: stream from this server
        assert client.get("/api/matches/nope/connect").status_code == 404
