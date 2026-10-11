"""Command line: `pitchside simulate`, `pitchside batch`, `pitchside league`."""

from __future__ import annotations

import argparse
import itertools
import json
import time
from pathlib import Path

from .league import build_league
from .sim.io import simulate, write_match
from .sim.summary import check, player_running, team_totals


def report(engine, events, frames) -> dict:
    totals = team_totals(events, engine.team_ids)
    running = player_running(frames)
    gk_ids = {p.player_id for c in engine.clubs for p in c.players if p.role == "GK"}
    full = {pid: r for pid, r in running.items() if r["minutes"] > 88 and pid not in gk_ids}
    kms = [r["km"] for r in full.values()]
    return {
        "match_id": engine.match_id,
        "score": f"{engine.team_ids[0]} {engine.score[0]}-{engine.score[1]} {engine.team_ids[1]}",
        "totals": {t: {k: round(v, 3) for k, v in s.items()} for t, s in totals.items()},
        "outfield_km_mean": round(sum(kms) / max(len(kms), 1), 2),
        "outfield_km_range": [round(min(kms, default=0), 2), round(max(kms, default=0), 2)],
        "top_speed_kmh_max": round(max((r["top_kmh"] for r in full.values()), default=0), 1),
        "truth": [f"{t.period}H {t.clock_ms // 60000}' {t.team_id} {t.label}" for t in engine.truth],
        "out_of_range": check(totals),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pitchside")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("simulate", help="simulate one match")
    s.add_argument("--home", default="RVM")
    s.add_argument("--away", default="HLB")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--match-id", default=None)
    s.add_argument("--out", type=Path, default=None, help="write the match to this directory")
    s.add_argument("--no-script", action="store_true", help="no hidden tactical changes")

    b = sub.add_parser("batch", help="simulate a round-robin of matches")
    b.add_argument("--n", type=int, default=12)
    b.add_argument("--seed0", type=int, default=100)
    b.add_argument("--out", type=Path, required=True)

    c = sub.add_parser("calibrate", help="simulate several matches and print compact totals")
    c.add_argument("--seeds", type=int, nargs="+", default=[7, 8, 9, 10])

    f = sub.add_parser("fit-models", help="fit pass-difficulty and xG models on simulated matches")
    f.add_argument("--train", type=int, default=16)
    f.add_argument("--test", type=int, default=4)
    f.add_argument("--seed0", type=int, default=1000)
    f.add_argument("--workers", type=int, default=4)
    f.add_argument("--write", type=Path, default=None, help="write coefficients JSON here")

    sub.add_parser("league", help="print the fictional league")
    args = ap.parse_args(argv)
    league = build_league()

    if args.cmd == "fit-models":
        from .stats.fit import fit_models
        result = fit_models(args.train, args.test, args.seed0, workers=args.workers, write=args.write)
        print(json.dumps(result["report"], indent=2))
        return

    if args.cmd == "calibrate":
        pairs = itertools.cycle(itertools.permutations(league, 2))
        for seed in args.seeds:
            home, away = next(pairs)
            t0 = time.perf_counter()
            engine, events, frames = simulate(league[home], league[away], seed=seed, match_id=f"c{seed}")
            r = report(engine, events, frames)
            keys = ("passes", "pass_completion", "shots", "shots_on_target", "goals", "corners", "fouls",
                    "tackles", "interceptions", "pressures", "carries", "pass_share")
            print(f"seed {seed}: {r['score']}  {time.perf_counter() - t0:.1f}s  {len(events)} events  "
                  f"km {r['outfield_km_mean']} {r['outfield_km_range']}  top {r['top_speed_kmh_max']} km/h")
            for tid, s in r["totals"].items():
                print("   ", tid, "  ".join(f"{k}={s.get(k, 0):.3g}" for k in keys))
            print("    truth:", "; ".join(r["truth"]) or "none")
            print("    out of range:", "; ".join(r["out_of_range"]) or "none")
        return

    if args.cmd == "league":
        for club in league.values():
            print(f"{club.club_id}  {club.name}  ({club.formation}, {club.ground})")
            for p in club.players:
                print(f"    {p.number:>2}  {p.role:<3}  {p.name}")
        return

    if args.cmd == "simulate":
        t0 = time.perf_counter()
        mid = args.match_id or f"{args.home}-{args.away}-s{args.seed}"
        engine, events, frames = simulate(league[args.home], league[args.away], seed=args.seed,
                                          match_id=mid, scripted=not args.no_script)
        out = report(engine, events, frames)
        out["seconds"] = round(time.perf_counter() - t0, 1)
        out["events"] = len(events)
        if args.out:
            out["written_to"] = str(write_match(args.out, engine, events, frames))
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    if args.cmd == "batch":
        pairs = itertools.cycle(itertools.permutations(league, 2))
        for i in range(args.n):
            home, away = next(pairs)
            mid = f"m{i + 1:03d}"
            engine, events, frames = simulate(league[home], league[away], seed=args.seed0 + i, match_id=mid)
            d = write_match(args.out, engine, events, frames)
            print(f"{mid} {home} {engine.score[0]}-{engine.score[1]} {away} -> {d}")


if __name__ == "__main__":
    main()
