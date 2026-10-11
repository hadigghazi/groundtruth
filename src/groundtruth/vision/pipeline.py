"""Auto-eventing from video, end to end, graded against the truth at every step.

1. Render broadcast frames from simulated matches; the engine labels them exactly.
2. Train the keypoint detector on frames from some matches.
3. On held-out matches, played as continuous 10 Hz clips:
   * detect players (by side) and the ball in every frame;
   * put each detection on the pitch through the camera model;
   * track players frame to frame and measure their speed;
   * follow the ball between players and rebuild passes;
   and score each against what really happened: detection precision and recall, metres of
   position error, team accuracy, speed error in km/h, and passes found.

The result is the honest answer to "how much of the event feed and the speed metrics can a
model recover from the broadcast picture alone".
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from ..league import build_league
from ..sim.engine import MatchEngine
from .camera import Director
from .render import BALL, Renderer, kits

W_PX, H_PX = 320, 180
MATCH_M = {"player": 1.5, "ball": 2.0}  # a detection counts if it lands this close on the pitch


def _engine(seed: int, i: int):
    league = list(build_league().values())
    home, away = league[i % len(league)], league[(i + 1) % len(league)]
    return MatchEngine(home, away, seed=seed, match_id=f"v{seed}")


def frames_of(seed: int, i: int, every: int, start_min: float = 0.0, minutes: float | None = None):
    """(frame, camera) pairs from one match: every `every` ticks, the camera panning as on screen."""
    engine = _engine(seed, i)
    director = Director(W_PX, H_PX)
    meta = engine.meta()
    t0, t1 = start_min * 600, (start_min + minutes) * 600 if minutes else math.inf
    for tick, (_events, frame) in enumerate(engine.run()):
        cam = director.step(frame.ball)
        if tick >= t1:
            break
        if tick >= t0 and (tick - int(t0)) % every == 0:
            yield meta, frame, cam


def dataset(seeds: list[int], per_match: int, rng: np.random.Generator):
    renderer = Renderer(W_PX, H_PX)
    imgs, labels = [], []
    every = max(1, 54000 // per_match)
    for i, seed in enumerate(seeds):
        kit = None
        for meta, frame, cam in frames_of(seed, i, every):
            kit = kit or kits(meta)
            img, lab = renderer.render(frame, cam, kit, rng)
            imgs.append(img)
            labels.append(lab)
    return np.stack(imgs), labels


def train(imgs: np.ndarray, labels: list[list[dict]], epochs: int = 18, batch: int = 32, seed: int = 0):
    import torch

    from .detector import Net, loss, targets, to_tensor

    torch.manual_seed(seed)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    net = Net()
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=epochs * math.ceil(len(imgs) / batch))
    tg = [targets(lab, H_PX, W_PX) for lab in labels]
    heat = torch.from_numpy(np.stack([t[0] for t in tg]))
    off = torch.from_numpy(np.stack([t[1] for t in tg]))
    mask = torch.from_numpy(np.stack([t[2] for t in tg]))
    history = []
    for ep in range(epochs):
        net.train()
        order = np.random.default_rng(seed + ep).permutation(len(imgs))
        total = 0.0
        for k in range(0, len(order), batch):
            idx = order[k:k + batch]
            x = to_tensor(imgs[idx])
            if np.random.default_rng(seed * 997 + ep * 31 + k).random() < 0.5:  # brightness jitter
                x = x * float(np.random.default_rng(k + ep).uniform(0.85, 1.15))
            h, o = net(x)
            lv = loss(h, o, heat[idx], off[idx], mask[idx])
            opt.zero_grad()
            lv.backward()
            opt.step()
            sched.step()
            total += float(lv) * len(idx)
        history.append(round(total / len(imgs), 4))
    net.eval()
    return net, history


# ------------------------------------------------------------------ evaluation

def _greedy(a: np.ndarray, b: np.ndarray, limit: float) -> list[tuple[int, int, float]]:
    """Nearest-first one-to-one matching between point sets within `limit` metres."""
    if not len(a) or not len(b):
        return []
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    pairs, used_a, used_b = [], set(), set()
    for flat in np.argsort(d, axis=None):
        i, j = divmod(int(flat), d.shape[1])
        if d[i, j] > limit:
            break
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((i, j, float(d[i, j])))
    return pairs


class Tracker:
    """Frame-to-frame tracks per side, from positions on the pitch; speed over the last half second."""

    def __init__(self, gate_m: float = 2.0, keep: int = 8):
        self.gate, self.keep = gate_m, keep
        self.tracks: dict[int, dict] = {}
        self._next = 0

    def step(self, t: int, side: int, pts: np.ndarray) -> list[int]:
        live = [k for k, tr in self.tracks.items() if tr["side"] == side and t - tr["t"] <= self.keep * 100]
        prev = np.array([self.tracks[k]["xy"][-1] for k in live]) if live else np.zeros((0, 2))
        ids = [-1] * len(pts)
        for i, j, _ in _greedy(pts, prev, self.gate):
            ids[i] = live[j]
        for i, p in enumerate(pts):
            if ids[i] < 0:
                ids[i] = self._next
                self.tracks[self._next] = {"side": side, "xy": [], "ts": []}
                self._next += 1
            tr = self.tracks[ids[i]]
            tr["xy"].append(p)
            tr["ts"].append(t)
            tr["t"] = t
        return ids

    def speed_kmh(self, k: int) -> float | None:
        tr = self.tracks[k]
        if len(tr["ts"]) < 6 or tr["ts"][-1] - tr["ts"][-6] > 700:
            return None
        dist = np.linalg.norm(np.asarray(tr["xy"][-1]) - np.asarray(tr["xy"][-6]))
        return float(dist / ((tr["ts"][-1] - tr["ts"][-6]) / 1000) * 3.6)


def _match_times(found: list[tuple[int, int]], truth: list[tuple[int, int]], within_ms: int = 1000) -> int:
    """Passes found that match a true pass by the same side within a second, one to one."""
    used, hits = set(), 0
    for side, t in found:
        j = next((j for j, (s2, t2) in enumerate(truth) if j not in used and s2 == side and abs(t2 - t) <= within_ms), None)
        if j is not None:
            used.add(j)
            hits += 1
    return hits


def evaluate(net, seeds: list[int], clips_per_match: int = 2, clip_minutes: float = 2.0, demo: Path | None = None) -> dict:
    """Plays held-out clips at 10 Hz through render -> detect -> pitch -> track -> passes, scoring each step."""
    import cv2

    from .detector import detect

    renderer, rng = Renderer(W_PX, H_PX, seed=99), np.random.default_rng(99)
    det = {"tp": 0, "fp": 0, "fn": 0, "err": [], "team_ok": 0, "team_n": 0}
    ball = {"tp": 0, "fp": 0, "fn": 0, "err": []}
    speed_err, ball_speed_err, ms = [], [], []
    pass_true = pass_found = pass_hit = 0
    writer = None
    for i, seed in enumerate(seeds):
        for c in range(clips_per_match):
            start = 10 + c * (80 - clip_minutes) / max(1, clips_per_match - 1)
            clip = list(frames_of(seed, i, 1, start, clip_minutes))
            if not clip:
                continue
            meta = clip[0][0]
            kit = kits(meta)
            tracker = Tracker()
            holder = true_holder = None
            last_ball = last_true_ball = None
            last_ball_t = -10**9
            true_prev: dict[str, tuple[np.ndarray, int]] = {}
            found_passes: list[tuple[int, int]] = []
            true_passes: list[tuple[int, int]] = []
            for n, (_meta, frame, cam) in enumerate(clip):
                img, labels = renderer.render(frame, cam, kit, rng)
                t0 = time.perf_counter()
                found = detect(net, img[None])[0]
                ms.append(1000 * (time.perf_counter() - t0))
                t = n * 100
                # players: onto the pitch through the camera, matched to the truth
                truth = [lab for lab in labels if lab["kind"] < 2]
                tp = np.array([[lab["x"], lab["y"]] for lab in truth]) if truth else np.zeros((0, 2))
                dets = [f for f in found if f[0] < 2]
                if dets:
                    dp = cam.to_ground(np.array([f[1] for f in dets]), np.array([f[2] for f in dets]))
                    keep = ~np.isnan(dp[:, 0])
                    dets, dp = [d for d, k in zip(dets, keep) if k], dp[keep]
                else:
                    dp = np.zeros((0, 2))
                pairs = _greedy(dp, tp, MATCH_M["player"])
                det["tp"] += len(pairs)
                det["fp"] += len(dets) - len(pairs)
                det["fn"] += len(truth) - len(pairs)
                det["err"] += [e for _, _, e in pairs]
                for a, b, _ in pairs:
                    det["team_n"] += 1
                    det["team_ok"] += dets[a][0] == truth[b]["kind"]
                # tracks and speeds, per side, compared with the true speed of the matched player
                det_track: dict[int, int] = {}
                for side in (0, 1):
                    idx = [k for k, d in enumerate(dets) if d[0] == side]
                    if idx:
                        det_track.update(zip(idx, tracker.step(t, side, dp[idx])))
                for a, b, _ in pairs:
                    v = tracker.speed_kmh(det_track[a]) if a in det_track else None
                    pid = truth[b]["player_id"]
                    prev = true_prev.get(pid)
                    if v is not None and prev is not None and 0 < t - prev[1] <= 600:
                        real = float(np.linalg.norm(tp[b] - prev[0]) / ((t - prev[1]) / 1000) * 3.6)
                        if real > 3:
                            speed_err.append(abs(v - real))
                for b, lab in enumerate(truth):
                    prev = true_prev.get(lab["player_id"])
                    if prev is None or t - prev[1] >= 500:
                        true_prev[lab["player_id"]] = (tp[b], t)
                # the ball
                tb = next((lab for lab in labels if lab["kind"] == BALL), None)
                db = next((f for f in found if f[0] == BALL), None)
                bxy = cam.to_ground(np.array([db[1]]), np.array([db[2]]))[0] if db else None
                if bxy is not None and np.isnan(bxy[0]):
                    bxy = None
                if tb is not None and bxy is not None and float(np.hypot(*(bxy - [tb["x"], tb["y"]]))) <= MATCH_M["ball"]:
                    ball["tp"] += 1
                    ball["err"].append(float(np.hypot(*(bxy - [tb["x"], tb["y"]]))))
                    if last_ball is not None and t - last_ball_t <= 300 and tb["z"] < 0.5:
                        dt = (t - last_ball_t) / 1000
                        est = float(np.linalg.norm(bxy - last_ball) / dt * 3.6)
                        real = float(np.linalg.norm(frame.ball[:2] - last_true_ball) / dt * 3.6)
                        if real > 10:
                            ball_speed_err.append(abs(est - real))
                    last_ball, last_ball_t, last_true_ball = bxy, t, frame.ball[:2].copy()
                else:
                    ball["fn"] += tb is not None
                    ball["fp"] += bxy is not None
                # passes: the ball moving from one tracked player to a team-mate, seen in the picture
                if bxy is not None and len(dp):
                    dist = np.linalg.norm(dp - bxy, axis=1)
                    k = int(np.argmin(dist))
                    if dist[k] < 1.2 and k in det_track:
                        cur = (dets[k][0], det_track[k])
                        if holder is not None and cur[0] == holder[0] and cur[1] != holder[1]:
                            found_passes.append((cur[0], t))
                        holder = cur
                # the same rule on the engine's own positions: the passes the picture could have shown
                if tb is not None and truth and tb["z"] < 1.2:
                    dmin, lab = min(((float(np.hypot(lab["x"] - tb["x"], lab["y"] - tb["y"])), lab) for lab in truth),
                                    key=lambda d: d[0])
                    if dmin < 1.0:
                        cur = (lab["kind"], lab["player_id"])
                        if true_holder is not None and cur[0] == true_holder[0] and cur[1] != true_holder[1]:
                            true_passes.append((cur[0], t))
                        true_holder = cur
                if demo is not None and i == 0 and c == 0 and n < 600:
                    if writer is None:
                        demo.parent.mkdir(parents=True, exist_ok=True)
                        writer = cv2.VideoWriter(str(demo), cv2.VideoWriter_fourcc(*"mp4v"), 10, (W_PX * 2, H_PX * 2))
                    vis = cv2.resize(img, (W_PX * 2, H_PX * 2), interpolation=cv2.INTER_NEAREST)
                    for f in found:
                        col = [(80, 200, 255), (255, 140, 80), (60, 255, 200)][f[0]]
                        cv2.circle(vis, (int(f[1] * 2), int(f[2] * 2)), 6 if f[0] < 2 else 4, col, 2, cv2.LINE_AA)
                    cv2.putText(vis, "GroundTruth vision: model detections", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                (200, 255, 60), 1, cv2.LINE_AA)
                    writer.write(vis)
            pass_true += len(true_passes)
            pass_found += len(found_passes)
            pass_hit += _match_times(found_passes, true_passes)
    if writer is not None:
        writer.release()

    def pr(x):
        p = x["tp"] / (x["tp"] + x["fp"]) if x["tp"] + x["fp"] else 0.0
        r = x["tp"] / (x["tp"] + x["fn"]) if x["tp"] + x["fn"] else 0.0
        return round(p, 3), round(r, 3)

    pp, prc = pr(det)
    bp, brc = pr(ball)
    return {
        "players": {"precision": pp, "recall": prc, "median_error_m": round(float(np.median(det["err"])), 2) if det["err"] else None,
                    "team_accuracy": round(det["team_ok"] / det["team_n"], 3) if det["team_n"] else None},
        "ball": {"precision": bp, "recall": brc, "median_error_m": round(float(np.median(ball["err"])), 2) if ball["err"] else None},
        "player_speed_mae_kmh": round(float(np.mean(speed_err)), 2) if speed_err else None,
        "ball_speed_mae_kmh": round(float(np.mean(ball_speed_err)), 2) if ball_speed_err else None,
        "passes": {"in_view": pass_true, "found": pass_found, "matched": pass_hit,
                   "precision": round(pass_hit / pass_found, 3) if pass_found else None,
                   "recall": round(pass_hit / pass_true, 3) if pass_true else None},
        "detector_ms_per_frame": round(float(np.median(ms)), 1) if ms else None,
    }


def run(train_seeds, test_seeds, per_match: int = 900, epochs: int = 18, out: Path = Path("vision-out")) -> dict:
    import torch

    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    t0 = time.perf_counter()
    imgs, labels = dataset(train_seeds, per_match, rng)
    t_data = time.perf_counter() - t0
    net, history = train(imgs, labels, epochs=epochs)
    t_train = time.perf_counter() - t0 - t_data
    torch.save(net.state_dict(), out / "vision.pt")
    import cv2

    for k in range(3):  # a few labelled samples, to see what the model sees
        vis = imgs[k * len(imgs) // 3].copy()
        for lab in labels[k * len(imgs) // 3]:
            cv2.circle(vis, (int(lab["u"]), int(lab["v"])), 3, [(80, 200, 255), (255, 140, 80), (60, 255, 200)][lab["kind"]], 1)
        cv2.imwrite(str(out / f"sample-{k}.png"), cv2.resize(vis, (W_PX * 2, H_PX * 2), interpolation=cv2.INTER_NEAREST))
    report = evaluate(net, test_seeds, demo=out / "demo.mp4")
    report.update({"train_frames": int(len(imgs)), "train_seeds": list(train_seeds), "test_seeds": list(test_seeds),
                   "epochs": epochs, "loss_by_epoch": history, "seconds": {"data": round(t_data), "train": round(t_train)},
                   "frame": [W_PX, H_PX]})
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
