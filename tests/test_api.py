"""The API end to end: start a match, stream it, steer it from the director console."""

from __future__ import annotations

from fastapi.testclient import TestClient

from pitchside.api.app import app


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
                kinds.add(ws.receive_json()["type"])
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
