import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import type { Frame, MatchMeta } from "../api";
import { L, W, markings } from "./pitch";

export interface Tag { player_id: string; headline: string; body?: string | null }

interface Props {
  meta: MatchMeta;
  getFrame: () => Frame | null;
  tags: React.MutableRefObject<Tag[]>;
  focusPlayer: string | null;
  compact?: boolean; // several views on screen: render cheaper
}

const PX = 14; // texture pixels per metre
const MARGIN = 7; // grass beyond the lines, metres
const LIME = "#c6ff3d";

function pitchTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = (L + 2 * MARGIN) * PX;
  c.height = (W + 2 * MARGIN) * PX;
  const g = c.getContext("2d")!;
  g.fillStyle = "#1b5f33";
  g.fillRect(0, 0, c.width, c.height);
  const X = (x: number) => (MARGIN + x) * PX, Y = (y: number) => (MARGIN + W - y) * PX;
  for (let i = 0; i < 20; i++) { // mowing stripes across the pitch
    g.fillStyle = i % 2 ? "#1d6537" : "#22703e";
    g.fillRect(X((L / 20) * i), 0, (L / 20) * PX + 1, c.height);
  }
  const grain = g.getImageData(0, 0, c.width, c.height); // a little grain, so the grass is not flat plastic
  for (let i = 0; i < grain.data.length; i += 4) {
    const n = (Math.random() - 0.5) * 10;
    grain.data[i] += n; grain.data[i + 1] += n; grain.data[i + 2] += n;
  }
  g.putImageData(grain, 0, 0);
  const m = markings();
  g.strokeStyle = "rgba(255,255,255,0.9)";
  g.lineWidth = 0.13 * PX;
  g.beginPath();
  for (const [x0, y0, x1, y1] of m.segs) { g.moveTo(X(x0), Y(y0)); g.lineTo(X(x1), Y(y1)); }
  g.stroke();
  for (const a of m.arcs) { g.beginPath(); g.arc(X(a.cx), Y(a.cy), a.r * PX, -a.a1, -a.a0); g.stroke(); }
  g.fillStyle = "rgba(255,255,255,0.9)";
  for (const [sx, sy] of m.spots) { g.beginPath(); g.arc(X(sx), Y(sy), 0.22 * PX, 0, Math.PI * 2); g.fill(); }
  const tex = new THREE.CanvasTexture(c);
  tex.anisotropy = 8;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** A crowd: rows of seats filled with shirts in both clubs' colours. */
function crowdTexture(colors: string[]): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 128;
  const g = c.getContext("2d")!;
  g.fillStyle = "#10151b";
  g.fillRect(0, 0, c.width, c.height);
  const palette = [...colors, ...colors, "#d8d8d8", "#2a2f36", "#3b4048", "#c9a27e", "#7a5a44"];
  for (let row = 0; row < 16; row++) {
    for (let x = 0; x < c.width; x += 4) {
      if (Math.random() < 0.12) continue; // empty seats
      g.fillStyle = palette[(Math.random() * palette.length) | 0];
      g.fillRect(x + Math.random(), row * 8 + 1 + Math.random() * 2, 3, 4);
    }
  }
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** Pitch-side LED boards carrying the product's own wordmark. */
function boardTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 1024; c.height = 48;
  const g = c.getContext("2d")!;
  g.fillStyle = "#06090a";
  g.fillRect(0, 0, c.width, c.height);
  g.font = "700 30px 'Barlow Condensed', 'Arial Narrow', sans-serif";
  g.textBaseline = "middle";
  let x = 16;
  while (x < c.width) {
    g.fillStyle = "#e9f1ee"; g.fillText("GROUND", x, 25); x += g.measureText("GROUND").width;
    g.fillStyle = LIME; g.fillText("TRUTH", x, 25); x += g.measureText("TRUTH").width + 24;
    g.fillStyle = "#8fa39b"; g.fillText("MATCH INTELLIGENCE YOU CAN GRADE", x, 25); x += g.measureText("MATCH INTELLIGENCE YOU CAN GRADE").width + 40;
  }
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** Engine metres -> scene units: x along the pitch, the main stand (y = 0) nearest the camera. */
const toScene = (x: number, y: number) => new THREE.Vector3(x - L / 2, 0, W / 2 - y);

export function BroadcastView({ meta, getFrame, tags, focusPlayer, compact = false }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const tagLayer = useRef<HTMLDivElement>(null);
  const focus = useRef(focusPlayer);
  focus.current = focusPlayer;
  const [lost, setLost] = useState(false);

  useEffect(() => {
    const el = host.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: !compact, powerPreference: "high-performance" });
    } catch {
      setLost(true);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, compact ? 1 : 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    el.appendChild(renderer.domElement);
    const onLost = (e: Event) => { e.preventDefault(); setLost(true); };
    renderer.domElement.addEventListener("webglcontextlost", onLost);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#05080a");
    scene.fog = new THREE.Fog("#05080a", 140, 300);
    scene.add(new THREE.HemisphereLight("#e8f0ff", "#203018", 0.9));
    const flood = new THREE.DirectionalLight("#ffffff", 1.6);
    flood.position.set(-30, 90, 50);
    scene.add(flood);
    const rim = new THREE.DirectionalLight("#cfe3ff", 0.5);
    rim.position.set(40, 60, -80);
    scene.add(rim);

    const ground = new THREE.Mesh(new THREE.PlaneGeometry(L + 2 * MARGIN, W + 2 * MARGIN),
      new THREE.MeshLambertMaterial({ map: pitchTexture() }));
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);
    const apron = new THREE.Mesh(new THREE.PlaneGeometry(L + 2 * MARGIN + 40, W + 2 * MARGIN + 40),
      new THREE.MeshLambertMaterial({ color: "#123d22" }));
    apron.rotation.x = -Math.PI / 2;
    apron.position.y = -0.02;
    scene.add(apron);

    // stands: stepped tiers with a crowd, a roof over the far side; none in front of the camera
    const crowd = crowdTexture([meta.home.colors[0], meta.away.colors[0], meta.home.colors[1]]);
    const tierMat = (len: number) => {
      const t = crowd.clone();
      t.needsUpdate = true;
      t.repeat.set(len / 24, 1);
      return new THREE.MeshLambertMaterial({ map: t });
    };
    const concrete = new THREE.MeshLambertMaterial({ color: "#1a2027" });
    const stand = (len: number, tiers: number, along: "x" | "z", sign: number, start: number) => {
      for (let i = 0; i < tiers; i++) {
        const depth = 5, rise = 2.6;
        const geo = along === "x" ? new THREE.BoxGeometry(len, rise, depth) : new THREE.BoxGeometry(depth, rise, len);
        const mats = [concrete, concrete, concrete, concrete, concrete, concrete];
        // the face towards the pitch shows the crowd
        const face = along === "x" ? (sign > 0 ? 4 : 5) : (sign > 0 ? 0 : 1);
        mats[face] = tierMat(len);
        mats[2] = tierMat(len); // the top, seen from the camera
        const b = new THREE.Mesh(geo, mats);
        const d = start + i * depth + depth / 2;
        b.position.set(along === "x" ? 0 : -sign * d, rise / 2 + i * rise, along === "x" ? -sign * d : 0);
        scene.add(b);
      }
    };
    stand(L + 30, 5, "x", 1, W / 2 + MARGIN + 3); // the far stand, tall
    stand(W + 2 * MARGIN + 6, 3, "z", 1, L / 2 + MARGIN + 3);
    stand(W + 2 * MARGIN + 6, 3, "z", -1, L / 2 + MARGIN + 3);
    const roof = new THREE.Mesh(new THREE.BoxGeometry(L + 34, 0.6, 18), new THREE.MeshLambertMaterial({ color: "#0d1116" }));
    roof.position.set(0, 17, -(W / 2 + MARGIN + 3 + 12));
    scene.add(roof);

    // LED boards along both touchlines and behind the goals
    const boards = boardTexture();
    const boardMat = (len: number) => { const t = boards.clone(); t.needsUpdate = true; t.repeat.set(len / 40, 1); return new THREE.MeshBasicMaterial({ map: t }); };
    for (const [x, z, len, rot] of [
      [0, -(W / 2 + 3.5), L + 6, 0], [0, W / 2 + 3.5, L + 6, Math.PI],
      [-(L / 2 + 4), 0, 30, Math.PI / 2], [L / 2 + 4, 0, 30, -Math.PI / 2],
    ] as const) {
      const b = new THREE.Mesh(new THREE.PlaneGeometry(len, 0.9), boardMat(len));
      b.position.set(x, 0.45, z);
      b.rotation.y = rot;
      scene.add(b);
    }

    // goals with nets
    const postMat = new THREE.MeshLambertMaterial({ color: "#f4f4f4" });
    const netMat = new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.12, side: THREE.DoubleSide });
    for (const gx of [-L / 2, L / 2]) {
      const out = Math.sign(gx);
      for (const dz of [-3.66, 3.66]) {
        const post = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 2.44), postMat);
        post.position.set(gx, 1.22, dz);
        scene.add(post);
      }
      const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 7.32), postMat);
      bar.rotation.x = Math.PI / 2;
      bar.position.set(gx, 2.44, 0);
      scene.add(bar);
      const back = new THREE.Mesh(new THREE.PlaneGeometry(7.32, 2.44), netMat);
      back.position.set(gx + out * 1.6, 1.22, 0);
      back.rotation.y = Math.PI / 2;
      scene.add(back);
      const top = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 7.32), netMat);
      top.position.set(gx + out * 0.8, 2.44, 0);
      top.rotation.x = Math.PI / 2;
      scene.add(top);
    }

    // players: shirt, shorts, head; a ring under the followed player
    const torso = new THREE.CapsuleGeometry(0.3, 0.55, 4, 10);
    const legs = new THREE.CylinderGeometry(0.24, 0.2, 0.85, 10);
    const headGeo = new THREE.SphereGeometry(0.17, 12, 10);
    const skins = ["#f1c7a5", "#d9a77f", "#b07a52", "#8a5a3b", "#5e3d2a"].map((c) => new THREE.MeshLambertMaterial({ color: c }));
    const shadowGeo = new THREE.CircleGeometry(0.55, 16);
    const shadowMat = new THREE.MeshBasicMaterial({ color: "#000000", transparent: true, opacity: 0.4 });
    const ringMat = new THREE.MeshBasicMaterial({ color: LIME });
    const ringGeo = new THREE.RingGeometry(0.7, 0.95, 28);
    const shirt = new Map<string, THREE.MeshLambertMaterial>();
    const shorts = new Map<string, THREE.MeshLambertMaterial>();
    const playerTeam = new Map<string, { team: string; gk: boolean; n: number }>();
    for (const team of [meta.home, meta.away]) {
      shirt.set(team.team_id, new THREE.MeshLambertMaterial({ color: team.colors[0] }));
      shirt.set(team.team_id + ":gk", new THREE.MeshLambertMaterial({ color: team.colors[1] }));
      shorts.set(team.team_id, new THREE.MeshLambertMaterial({ color: new THREE.Color(team.colors[1]).multiplyScalar(0.8) }));
      for (const p of [...team.lineup, ...team.bench]) playerTeam.set(p.player_id, { team: team.team_id, gk: p.role === "GK", n: p.number });
    }
    const meshes = new Map<string, { g: THREE.Group; last: THREE.Vector3; phase: number }>();
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.24, 18, 14), new THREE.MeshLambertMaterial({ color: "#ffffff", emissive: "#222222" }));
    const ballShadow = new THREE.Mesh(shadowGeo, shadowMat);
    ballShadow.rotation.x = -Math.PI / 2;
    ballShadow.scale.setScalar(0.45);
    scene.add(ball, ballShadow);

    // the main camera: high in the stand on the halfway line, about 50 m of pitch across
    const camera = new THREE.PerspectiveCamera(24, 16 / 9, 1, 600);
    const look = new THREE.Vector3(0, 0, 0);
    const tagEls = new Map<string, HTMLDivElement>();
    const minDt = compact ? 1000 / 30 : 0;
    let raf = 0, lastDraw = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (now - lastDraw < minDt) return;
      lastDraw = now;
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      if (renderer.domElement.width !== Math.round(w * renderer.getPixelRatio())) {
        renderer.setSize(w, h, false);
        renderer.domElement.style.width = "100%";
        renderer.domElement.style.height = "100%";
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
      const f = getFrame();
      if (f) {
        const seen = new Set<string>();
        for (const [pid, x, y] of f.players) {
          seen.add(pid);
          let rec = meshes.get(pid);
          if (!rec) {
            const info = playerTeam.get(pid);
            const team = info?.team ?? meta.home.team_id;
            const g = new THREE.Group();
            const body = new THREE.Mesh(torso, shirt.get(team + (info?.gk ? ":gk" : ""))!);
            body.position.y = 1.2;
            const lower = new THREE.Mesh(legs, shorts.get(team)!);
            lower.position.y = 0.45;
            const head = new THREE.Mesh(headGeo, skins[(info?.n ?? 0) % skins.length]);
            head.position.y = 1.78;
            const s = new THREE.Mesh(shadowGeo, shadowMat);
            s.rotation.x = -Math.PI / 2;
            s.position.y = 0.02;
            const ring = new THREE.Mesh(ringGeo, ringMat);
            ring.rotation.x = -Math.PI / 2;
            ring.position.y = 0.03;
            ring.name = "ring";
            g.add(lower, body, head, s, ring);
            scene.add(g);
            rec = { g, last: toScene(x, y), phase: Math.random() * 6 };
            meshes.set(pid, rec);
          }
          const p = toScene(x, y);
          const moved = p.distanceTo(rec.last);
          rec.phase += Math.min(moved, 0.5) * 7;
          rec.last.copy(p);
          rec.g.position.set(p.x, Math.abs(Math.sin(rec.phase)) * Math.min(moved * 0.6, 0.06), p.z); // a running bob
          rec.g.getObjectByName("ring")!.visible = pid === focus.current;
        }
        for (const [pid, rec] of meshes) if (!seen.has(pid)) { scene.remove(rec.g); meshes.delete(pid); }
        const bp = toScene(f.ball[0], f.ball[1]);
        ball.position.set(bp.x, 0.24 + f.ball[2], bp.z);
        ballShadow.position.set(bp.x, 0.02, bp.z);
        const target = new THREE.Vector3(THREE.MathUtils.clamp(bp.x, -L / 2 + 20, L / 2 - 20), 0, bp.z * 0.45);
        look.lerp(target, 0.05);
        camera.position.set(look.x * 0.9, 19, W / 2 + 32);
        camera.lookAt(look);
      }
      renderer.render(scene, camera);

      // HTML player tags, projected above each tagged player's head
      const layer = tagLayer.current;
      if (layer) {
        const want = new Map(tags.current.map((t) => [t.player_id, t]));
        for (const [pid, elTag] of tagEls) if (!want.has(pid)) { elTag.remove(); tagEls.delete(pid); }
        for (const [pid, t] of want) {
          const rec = meshes.get(pid);
          if (!rec) continue;
          let elTag = tagEls.get(pid);
          if (!elTag) { elTag = document.createElement("div"); elTag.className = "player-tag"; layer.appendChild(elTag); tagEls.set(pid, elTag); }
          const text = t.headline + (t.body ? `|${t.body}` : "");
          if (elTag.dataset.text !== text) { // rebuild only when the words change
            elTag.dataset.text = text;
            elTag.innerHTML = "";
            const n = document.createElement("strong"); n.textContent = t.headline; elTag.appendChild(n);
            if (t.body) { const b = document.createElement("span"); b.textContent = t.body; elTag.appendChild(b); }
          }
          const p = rec.g.position.clone().setY(2.3).project(camera);
          elTag.style.transform = `translate(-50%, -100%) translate(${((p.x + 1) / 2) * w}px, ${((1 - p.y) / 2) * h}px)`;
        }
      }
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      renderer.domElement.removeEventListener("webglcontextlost", onLost);
      renderer.dispose();
      renderer.forceContextLoss();
      el.removeChild(renderer.domElement);
      tagEls.forEach((t) => t.remove());
    };
  }, [meta, getFrame, tags, compact]);

  return (
    <div className="view-3d" ref={host}>
      <div className="tag-layer" ref={tagLayer} />
      {lost && <div className="view-lost">3D view unavailable on this device. Switch to Tactical.</div>}
    </div>
  );
}
