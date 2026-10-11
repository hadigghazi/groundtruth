# Pitchside

**Live match intelligence you can check.** Pitchside turns a live stream of synthetic,
football-realistic match data into timed broadcast overlays and explanations for every
kind of viewer, and every number on screen traces back to the events behind it.

Built for the Microsoft *Inside the Game* hackathon. All clubs, players and grounds are
fictional; no real match data is used.

## What makes it different

1. **Code computes every number; agents only explain.** Pass difficulty, xG, speeds,
   distances, pressing and shape metrics come from a deterministic stats engine. The
   agents read them through tools and a Verifier rejects any sentence whose numbers
   are not in the evidence.
2. **Explanations with an answer key.** The match engine gives each team hidden
   tactics (pressing, line height, directness, tempo, width) that change during the
   match, by script or from a live director console. The truth log lets us measure
   whether the system spotted a change and named the right cause, not just whether its
   commentary sounds plausible.
3. **Broadcast-ready output.** Overlays are timed, machine-readable cues (JSON) a
   graphics system could take as they are, played behind a short broadcast delay so
   explanations land with the moment they describe.

## The pipeline

| Stage | What runs | Where |
| --- | --- | --- |
| Data | Seeded tick-based engine (0.1 s), 22 players + ball at 10 Hz, raw events, truth log | `src/pitchside/sim` |
| Ingest | Live session paces the engine to the wall clock and streams it | `src/pitchside/live` |
| Interpret | Stats engine: metrics, fitted pass-difficulty and xG models, moment detection | `src/pitchside/stats` |
| Explain | Agent team on Microsoft Agent Framework: Tactician, Verifier, Producer, Persona writers | `src/pitchside/agents` |
| Render | Overlay cues in English, Spanish and Arabic; a screen budget per viewer | `src/pitchside/render` |
| Personalize | Analyst or casual viewer, language, favourite club, player focus | `web/` |

### The agent team

One graph workflow per flagged moment:

```
Tactician ──▶ Verifier ──approved──▶ Producer ──fan-out──▶ Persona (en) ┐
    ▲            │                                          Persona (es) ├─fan-in─▶ Collector
    └──retry─────┤                                          Persona (ar) ┘
                 └──rejected twice──▶ Fallback (template cue)
```

* **Tactician** (larger model, read-only tools) names the team that changed, the cause from
  a fixed vocabulary, and why it matters, with every number listed as a claim.
* **Verifier** checks in code first: each number must be in the evidence, each claim must
  match its metric, and the cause must agree with the direction its signals moved. Then a
  small model judges the reasoning. One retry with the problems listed; after that the
  template cue airs, marked as a fallback.
* **Producer** is deliberately code, not a model: it decides what reaches the screen.
* **Persona writers** (one per language, in parallel) rewrite the verified insight for
  analyst and casual viewers; their numbers are checked again.

Every hand-off writes to a shared insight board that the web app shows live.

## Evaluation

All numbers come from simulated matches with seeds disjoint from anything used to
choose settings.

| Measure | Result | How |
| --- | --- | --- |
| Pass model | held-out log loss 0.358 (base rate 0.475), well calibrated | 16 training, 4 test matches (`pitchside fit-models`) |
| xG model | held-out log loss 0.273 (base rate 0.341) | same |
| Tactical shifts caught with the right cause | 65% of 34 hidden changes within 12 min (median 9.3 min); 2.5 false alarms per match | 8 noise, 12 selection, 12 test matches (`pitchside detector-report`, `docs/detector-report.json`) |

The detector picks its own signals: for each kind of change it keeps the signals that
move most relative to their noise on training matches, combines them into a score, and
sets the threshold at the score's 99th percentile on matches with no hidden change.

## Run it

Everything runs in Docker; CI builds and tests every push.

```bash
docker build --target test -t pitchside-test . && docker run --rm pitchside-test
docker build --target app -t pitchside . && docker run --rm -p 8000:8000 pitchside   # then open http://localhost:8000
```

Agents use a deterministic scripted client unless a model is configured:

| Variable | Meaning |
| --- | --- |
| `PITCHSIDE_LLM` | `scripted` (default), `openai` (any OpenAI-compatible endpoint) or `foundry` |
| `PITCHSIDE_MODEL_LARGE`, `PITCHSIDE_MODEL_SMALL` | model or deployment names for the Tactician and for the judge and writers |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | for `openai` |
| `FOUNDRY_PROJECT_ENDPOINT` | for `foundry` (managed identity or Azure CLI login) |

Command line: `pitchside simulate`, `pitchside batch`, `pitchside calibrate`,
`pitchside fit-models`, `pitchside detector-report`.

## Known limitations

* The engine is a model of football, tuned so totals fall in typical top-flight ranges.
  Very direct sides against a high line can produce high-scoring games.
* For an incomplete pass the event's end point is where the ball was won, so the pass
  model's "space at the end" feature partly reflects the outcome, as in real event data.
* Auto-eventing from video is out of scope; the ingest schema is source-agnostic.

## Licence

MIT
