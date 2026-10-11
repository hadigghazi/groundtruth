# GroundTruth

**Match intelligence you can grade.** GroundTruth turns a live stream of synthetic,
football-realistic match data into timed broadcast graphics, captions, spoken commentary and
explanations for every kind of viewer. The match engine knows the true cause of every tactical
change, so GroundTruth measures whether its explanations are right, not just whether they sound right.

Built for the Microsoft *Inside the Game* hackathon. All clubs, players and grounds are
fictional; no real match data is used.

## What makes it different

1. **Explanations with an answer key.** The engine gives each team hidden tactics (pressing,
   line height, directness, tempo, width) that change during the match, by script or from a live
   director console. Nothing downstream can see them. At full time the truth log opens and scores
   the system on screen: which changes it caught, whether it named the right cause, and how often
   it raised a false alarm. The detector's plain rule and the agents are scored side by side.
2. **Challenge it live.** Change a team's tactics from the director console without telling the
   system, or pull the plug on the models with the outage switch, and watch what it does.
3. **Code computes every number; agents only explain.** Pass difficulty, xG, speeds, pressing and
   shape metrics come from a deterministic stats engine. A code verifier rejects any sentence whose
   numbers are not in the evidence at the precision written, any claim about seasons, records or
   what will happen, and any cause its own signals contradict. A Challenger agent argues for a
   different explanation, and code settles the argument with the detector's cause score.
4. **Nothing waits on a model.** Every moment's template card airs the instant it is flagged. The
   agents' verified card then takes over the same slot (`Cue.replaces`). Viewers watch on a short
   broadcast delay, so when the agents are quick the viewer only ever sees the verified card.
5. **Spoiler-safe everywhere.** Rewind, "catch me up", "ask the match", the analyst panels and the
   MCP tools all answer as of the viewer's own minute, never from later play.
6. **Broadcast-ready output.** Overlays are timed cues with a versioned JSON Schema, served as a
   timeline API a graphics system could take as it is.

## What a viewer gets

* A broadcast 3D view (main-camera framing, stands, LED boards) or a tactical top-down view.
* One, two or four viewers side by side, each with their own profile (analyst or casual fan),
  language (English, Spanish, Arabic with full right-to-left layout), club and player to follow.
* Broadcast graphics: scorebug, goal graphics, lower thirds, stat panels with a verified stamp and
  "verified in N s", play-by-play captions written by code the moment each event happens, ticker,
  player tags, and the full-time answer key.
* Spoken commentary: Azure AI Speech neural voices, one per language and profile (browser voices
  when Speech is not configured).
* "Catch me up" (a verified recap up to your minute) and "Ask the match" (questions answered by an
  agent with read-only tools, every number checked).
* Pause, rewind 30 seconds, a timeline marked with goals, chances, cards and tactical shifts, and
  "Replay the moment" on any card's evidence.
* Analyst panels: momentum, shot map sized by xG, passing network, live team stats.
* The agents at work: a timeline per flagged moment showing each agent's run, with the judge,
  the Challenger and the three writers running side by side; a Metrics panel with time to a
  verified card (median and 95th percentile), first-time approval, fallbacks and tokens.

## The pipeline

| Stage | What runs | Where |
| --- | --- | --- |
| Data | Seeded tick-based engine (0.1 s), 22 players + ball at 10 Hz, raw events, truth log | `src/groundtruth/sim` |
| Ingest | Live session paces the engine to the wall clock and streams it; captions from raw events | `src/groundtruth/live`, `render/captions.py` |
| Interpret | Stats engine: metrics, fitted pass-difficulty and xG models, moment detection; events rebuilt from positions alone | `src/groundtruth/stats` |
| Explain | Agent team on Microsoft Agent Framework; the studio desk (recap, Q&A) | `src/groundtruth/agents` |
| Render | Overlay cues in three languages; a Producer with a screen budget and a memory of what aired | `src/groundtruth/render` |
| Personalize | Profile, language, club, player focus, voice, layout | `web/` |

### The agent team

One Agent Framework graph workflow per flagged moment:

```
Tactician --> Checker --pass--> Producer --fan-out--> Judge         --+
    ^           |                                     Challenger      |
    |           |                                     Persona (en)    +--fan-in--> Gate --> verified cards
    |           |                                     Persona (es)    |              |
    |           |                                     Persona (ar)  --+              |
    +--retry----+--------------------------------------------------------------------+
                +--rejected twice--> Fallback (the template stays on air)
```

* **Tactician** (larger model, read-only tools; a smaller backup model if it fails) names the team
  that changed, the cause from a fixed vocabulary and why it matters, with every number as a claim.
* **Checker** is code: numbers at the precision written, claim values per metric and team, the
  cause's own signals moving the right way, and the rulebook.
* **Producer** is code: it schedules the slot, then starts everything else at once.
* **Judge** (small model) checks the reasoning. It runs alongside the writers, not before them:
  waiting for it would add a whole model round trip to every card.
* **Challenger** (small model) argues for the strongest different explanation. Code compares the
  detector's cause score for both; the Challenger wins only by a clear margin, and then the
  Tactician must answer it.
* **Persona writers**, one call per language, return the analyst and casual versions together;
  their numbers and wording are checked again.
* **Gate** is code: the writers' text airs only if the judge and the Challenger let it.

Fixed instructions (rules, metric and cause definitions) sit in each agent's system prompt so every
call shares a long cacheable prefix; the per-moment evidence is compact JSON. Every hand-off writes
to an insight board that the app shows live, and every run is traced to Application Insights.

## Evaluation

All numbers come from simulated matches with seeds disjoint from anything used to choose settings.

| Measure | Result | How |
| --- | --- | --- |
| Pass model | held-out log loss 0.358 (base rate 0.475), well calibrated | 16 training, 4 test matches (`groundtruth fit-models`) |
| xG model | held-out log loss 0.273 (base rate 0.341) | same |
| Tactical shifts caught with the right cause (detector rule) | 65% of 34 hidden changes within 12 min (median 9.3 min); 2.5 false alarms per match | 8 noise, 12 selection, 12 test matches (`groundtruth detector-report`) |
| Tactical shifts caught with the right cause (learned detector, in use) | 68% of the same 34 changes (median 9.0 min); 1.9 false alarms per match | softmax over own and opponent drifts; 150 training, 30 noise, the same selection and test matches (`groundtruth learn-detector`, `docs/learned-detector-report.json`) |
| Players found in rendered broadcast video | precision 0.94, recall 0.95, median 0.12 m on the pitch, side right 94% | keypoint detector trained on 2,851 frames from 3 matches, graded on 10 Hz clips from 2 others (`groundtruth vision`, `docs/vision-report.json`) |
| Ball found in video | precision 0.93, recall 0.86, median 0.36 m | same |
| Speeds from video | players within 0.9 km/h, ball within 7.7 km/h (mean error) | tracks on the pitch against the engine's positions |
| Passes from video | precision 0.41, recall 0.66 against passes the picture shows | tracking switches still read as changes of possession |
| Completed passes rebuilt from positions alone | precision 0.86, recall 0.78 (1,133 of 1,459) | `groundtruth autoevent --seeds 7 8` |
| Shots rebuilt from positions alone | precision 1.00, recall 0.49 (blocked shots look like interceptions) | same |
| Agents' cause against the rule, latency, cost | run with a real model: `groundtruth eval-agents --llm foundry` | the detector's 12 test matches, paced live |

`eval-agents` plays each test match live with the agent team, scores the agents' causes and the
rule's against the truth log, reports time to a verified card and tokens per run, and, with an
Azure OpenAI judge deployment set, grades every verified insight with Azure AI Evaluation
(groundedness against the evidence it was written from, relevance, coherence).

## Interfaces

| Interface | Where |
| --- | --- |
| REST API, documented | `/docs` (OpenAPI) |
| Live stream: frames, events, cues, agent trace, scorecard | `WS /api/matches/{id}/stream` |
| Overlay timeline for one viewer profile and language | `GET /api/matches/{id}/overlays?profile=casual&lang=es` |
| Cue JSON Schema, versioned | `GET /api/schema/cue` (also `groundtruth schema`) |
| Catch me up, Ask the match (as of a playhead) | `POST /api/matches/{id}/catchup`, `POST /api/matches/{id}/ask` |
| Agent runs and metrics; outage switch | `GET /api/matches/{id}/agents`, `POST /api/matches/{id}/agents/outage` |
| Full-time scorecard; truth log | `GET /api/matches/{id}/scorecard`, `GET /api/matches/{id}/truth` (both 409 until full time) |
| MCP tools (`list_matches`, `match_brief`, `team_window`, `player_report`, `key_moments`, `scorecard`) | streamable HTTP at `/mcp/` |

## Design

The app chrome is Fluent 2 (`@fluentui/react-components`) with a GroundTruth theme: a night-pitch
studio, green-black neutrals and one floodlight-lime brand colour for anything verified or live.
Fluent supplies the controls, their states, keyboard support and right-to-left behaviour. The
overlays are a separate broadcast package: Barlow Condensed (Cairo for Arabic), team-colour slabs,
wipes in, and a stamp that draws itself when a card is verified. Motion respects reduced-motion
settings.

## Run it

Everything runs in Docker. A push runs the Python tests (`ci.yml`) or the web build (`web.yml`),
whichever changed; the heavier checks run on demand, for example
`gh workflow run ci.yml -f ui=true` (screenshots of every screen, English and Arabic, desktop and
phone) or `-f distributed=true` (the roles over the Azure emulators).

```bash
docker build --target test -t groundtruth-test . && docker run --rm groundtruth-test
docker build --target app -t groundtruth . && docker run --rm -p 8000:8000 groundtruth   # then open http://localhost:8000
```

Or open the repository in GitHub Codespaces (`.devcontainer/`): the API runs with
`uvicorn groundtruth.api.app:app --reload` and the web app with `cd web && npm run dev`.

Agents use a deterministic scripted client unless a model is configured:

| Variable | Meaning |
| --- | --- |
| `GROUNDTRUTH_LLM` | `scripted` (default), `openai` (any OpenAI-compatible endpoint) or `foundry` |
| `GROUNDTRUTH_MODEL_LARGE`, `GROUNDTRUTH_MODEL_SMALL` | the Tactician's model; the judge, Challenger, writers and desk |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | for `openai` |
| `FOUNDRY_PROJECT_ENDPOINT` | for `foundry` (managed identity or Azure CLI login) |
| `GROUNDTRUTH_SPEECH_REGION`, `GROUNDTRUTH_SPEECH_RESOURCE_ID` | Azure AI Speech for the spoken commentary |
| `GROUNDTRUTH_AGENT_RUNS_PER_HOUR`, `GROUNDTRUTH_TOKENS_PER_DAY`, `GROUNDTRUTH_TTS_CHARS_PER_HOUR` | the public demo's usage guard |

### Deploy to Azure

`infra/main.bicep` describes the whole deployment: a Container Apps app (API, agents, web), a
Microsoft Foundry account and project with two model deployments (the account also serves Speech),
Event Hubs, a container registry, Log Analytics with Application Insights, and a budget. The app
reaches Foundry, Speech, Event Hubs and the registry with a managed identity; there are no keys.

1. In Azure Cloud Shell (Bash), run `infra/bootstrap.sh`. It creates the resource group and an
   identity GitHub Actions signs in as through OpenID Connect, then prints three values.
2. Set them as repository secrets (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`).
3. Set the repository variable `AZURE_DEPLOY` to `true` (the script prints the command). From then on
   every push to `main` runs `deploy.yml`: it provisions, pushes the image, rolls out and smoke-tests; the job summary
   shows the URL.

Command line: `groundtruth simulate`, `batch`, `calibrate`, `fit-models`, `detector-report`,
`autoevent`, `eval-agents`, `schema`.

## Known limitations

* The engine is a model of football, tuned so totals fall in typical top-flight ranges. Very direct
  sides against a high line can produce high-scoring games.
* For an incomplete pass the event's end point is where the ball was won, so the pass model's
  "space at the end" feature partly reflects the outcome, as in real event data.
* From positions alone, blocked shots are indistinguishable from interceptions, so shot recall
  from tracking is about half.
* The video is rendered by the project itself: cleaner than a real broadcast (no replays, cuts or
  crowds of players at a corner). The detector runs at 11 ms a frame on one CPU core.
* The learned detector's gain over the rule is within noise on 34 test changes; it is better on
  all three measures and gives the agents calibrated probabilities, so it is the one in use.
* A live match lives in one server process, so the app runs as a single replica.

## Licence

MIT
