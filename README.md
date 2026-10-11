# Pitchside

Live, explainable match intelligence from synthetic football data, built for
the Microsoft "Inside the Game" hackathon.

Work in progress. So far: a seeded, tick-based match engine (`src/pitchside/sim`)
that streams raw events and 10 Hz positions for a fictional league, and logs
its hidden tactical changes as ground truth.

```bash
docker build --target test -t pitchside-test . && docker run --rm pitchside-test
docker build --target runtime -t pitchside . && docker run --rm pitchside simulate --seed 7
```
