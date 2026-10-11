import { useEffect, useRef } from "react";
import type { Frame, MatchEvent, MatchMeta } from "../api";
import type { Tag } from "./BroadcastView";
import { L, W, markings } from "./pitch";

interface Props {
  meta: MatchMeta;
  getFrame: () => Frame | null;
  focusPlayer: string | null;
  highlight: MatchEvent[];
  tags: React.MutableRefObject<Tag[]>;
}

const MARKS = markings();

/** Top-down pitch on a canvas, redrawn every animation frame from the delayed playhead. */
export function TacticalView({ meta, getFrame, focusPlayer, highlight, tags }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const props = useRef({ focusPlayer, highlight });
  props.current = { focusPlayer, highlight };

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const teamOf = new Map<string, { color: string; alt: string; number: number; gk: boolean }>();
    for (const team of [meta.home, meta.away]) {
      for (const p of [...team.lineup, ...team.bench]) {
        teamOf.set(p.player_id, { color: team.colors[0], alt: team.colors[1], number: p.number, gk: p.role === "GK" });
      }
    }
    let raf = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const dpr = window.devicePixelRatio || 1;
      const cw = canvas.clientWidth, ch = canvas.clientHeight;
      if (canvas.width !== Math.round(cw * dpr)) { canvas.width = Math.round(cw * dpr); canvas.height = Math.round(ch * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const pad = 18;
      const k = Math.min((cw - 2 * pad) / L, (ch - 2 * pad) / W);
      const ox = (cw - k * L) / 2, oy = (ch - k * W) / 2;
      const X = (x: number) => ox + x * k, Y = (y: number) => oy + (W - y) * k;
      // grass, with mowing stripes
      ctx.fillStyle = "#1f6b3a";
      ctx.fillRect(0, 0, cw, ch);
      for (let i = 0; i < 14; i++) {
        if (i % 2) { ctx.fillStyle = "#227240"; ctx.fillRect(X((L / 14) * i), Y(W), (L / 14) * k, W * k); }
      }
      ctx.strokeStyle = "rgba(255,255,255,0.85)";
      ctx.lineWidth = Math.max(1, k * 0.12);
      ctx.beginPath();
      for (const [x0, y0, x1, y1] of MARKS.segs) { ctx.moveTo(X(x0), Y(y0)); ctx.lineTo(X(x1), Y(y1)); }
      ctx.stroke();
      for (const a of MARKS.arcs) {
        ctx.beginPath();
        ctx.arc(X(a.cx), Y(a.cy), a.r * k, -a.a1, -a.a0); // canvas y points down
        ctx.stroke();
      }
      ctx.fillStyle = "rgba(255,255,255,0.85)";
      for (const [sx, sy] of MARKS.spots) { ctx.beginPath(); ctx.arc(X(sx), Y(sy), Math.max(1.5, k * 0.2), 0, Math.PI * 2); ctx.fill(); }

      const f = getFrame();
      if (!f) return;
      const { focusPlayer: focus, highlight: hl } = props.current;
      // evidence: the events behind the open overlay
      for (const e of hl) {
        if (e.x == null || e.y == null) continue;
        ctx.strokeStyle = "#ffd447";
        ctx.fillStyle = "#ffd447";
        ctx.lineWidth = 2;
        if (e.end_x != null && e.end_y != null) {
          ctx.beginPath(); ctx.moveTo(X(e.x), Y(e.y)); ctx.lineTo(X(e.end_x), Y(e.end_y)); ctx.stroke();
        }
        ctx.beginPath(); ctx.arc(X(e.x), Y(e.y), 4, 0, Math.PI * 2); ctx.fill();
      }
      const r = Math.max(5, k * 1.05);
      for (const [pid, x, y] of f.players) {
        const info = teamOf.get(pid);
        if (!info) continue;
        ctx.beginPath();
        ctx.arc(X(x), Y(y), r, 0, Math.PI * 2);
        ctx.fillStyle = info.gk ? info.alt : info.color;
        ctx.fill();
        ctx.lineWidth = pid === focus ? 3 : 1.2;
        ctx.strokeStyle = pid === focus ? "#ffd447" : "rgba(0,0,0,0.55)";
        ctx.stroke();
        ctx.fillStyle = info.gk ? info.color : info.alt;
        ctx.font = `600 ${Math.round(r * 1.05)}px system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(String(info.number), X(x), Y(y) + 0.5);
      }
      const [bx, by, bz] = f.ball;
      ctx.fillStyle = "rgba(0,0,0,0.35)";
      ctx.beginPath(); ctx.arc(X(bx) + bz * k * 0.25, Y(by) + bz * k * 0.25, r * 0.45, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#fff";
      ctx.beginPath(); ctx.arc(X(bx), Y(by) - bz * k * 0.15, r * 0.5 + bz * 0.25, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#222"; ctx.lineWidth = 1; ctx.stroke();
      // player tags: a label above each tagged player
      const where = new Map(f.players.map((p) => [p[0], p]));
      for (const t of tags.current) {
        const p = where.get(t.player_id);
        if (!p) continue;
        const text = t.body ? `${t.headline} · ${t.body}` : t.headline;
        ctx.font = "600 12px system-ui, sans-serif";
        const tw = ctx.measureText(text).width + 12;
        const tx = X(p[1]) - tw / 2, ty = Y(p[2]) - r - 24;
        ctx.fillStyle = "rgba(10,16,24,0.88)";
        ctx.fillRect(tx, ty, tw, 18);
        ctx.fillStyle = "#ffd447";
        ctx.fillRect(tx, ty, 3, 18);
        ctx.fillStyle = "#fff";
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(text, tx + 7, ty + 9.5);
      }
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [meta, getFrame, tags]);

  return <canvas ref={ref} className="view-canvas" aria-label="Top-down view of the pitch" />;
}
