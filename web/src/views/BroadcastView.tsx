import { useEffect, useRef } from "react";
import * as THREE from "three";
import type { Frame, MatchMeta } from "../api";
import { L, W, markings } from "./pitch";

export interface Tag { player_id: string; headline: string; body?: string | null }

interface Props {
  meta: MatchMeta;
  getFrame: () => Frame | null;
  tags: React.MutableRefObject<Tag[]>;
  focusPlayer: string | null;
}

const PX = 12; // texture pixels per metre
const MARGIN = 6; // grass beyond the lines, metres

function pitchTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = (L + 2 * MARGIN) * PX;
  c.height = (W + 2 * MARGIN) * PX;
  const g = c.getContext("2d")!;
  g.fillStyle = "#1d6636";
  g.fillRect(0, 0, c.width, c.height);
  for (let i = 0; i < 18; i++) {
    if (i % 2) continue;
    g.fillStyle = "#217441";
    g.fillRect((MARGIN + (L / 18) * i) * PX, 0, (L / 18) * PX, c.height);
  }
  const X = (x: number) => (MARGIN + x) * PX, Y = (y: number) => (MARGIN + W - y) * PX;
  const m = markings();
  g.strokeStyle = "rgba(255,255,255,0.92)";
  g.lineWidth = 0.14 * PX;
  g.beginPath();
  for (const [x0, y0, x1, y1] of m.segs) { g.moveTo(X(x0), Y(y0)); g.lineTo(X(x1), Y(y1)); }
  g.stroke();
  for (const a of m.arcs) { g.beginPath(); g.arc(X(a.cx), Y(a.cy), a.r * PX, -a.a1, -a.a0); g.stroke(); }
  g.fillStyle = "rgba(255,255,255,0.92)";
  for (const [sx, sy] of m.spots) { g.beginPath(); g.arc(X(sx), Y(sy), 0.22 * PX, 0, Math.PI * 2); g.fill(); }
  const tex = new THREE.CanvasTexture(c);
  tex.anisotropy = 8;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** Engine metres -> scene units: x along the pitch, the main stand (y = 0) nearest the camera. */
const toScene = (x: number, y: number) => new THREE.Vector3(x - L / 2, 0, W / 2 - y);

export function BroadcastView({ meta, getFrame, tags, focusPlayer }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const tagLayer = useRef<HTMLDivElement>(null);
  const focus = useRef(focusPlayer);
  focus.current = focusPlayer;

  useEffect(() => {
    const el = host.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#0b1a12");
    scene.fog = new THREE.Fog("#0b1a12", 120, 260);
    scene.add(new THREE.HemisphereLight("#dfe9ff", "#2a3a20", 1.0));
    const sun = new THREE.DirectionalLight("#ffffff", 1.4);
    sun.position.set(-40, 80, 60);
    scene.add(sun);

    const ground = new THREE.Mesh(new THREE.PlaneGeometry(L + 2 * MARGIN, W + 2 * MARGIN),
      new THREE.MeshLambertMaterial({ map: pitchTexture() }));
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);
    // stands: low dark terraces around the pitch, enough to read as a stadium
    const standMat = new THREE.MeshLambertMaterial({ color: "#1c2733" });
    const stands: [number, number, number, number, number][] = [
      [0, -(W / 2 + MARGIN + 9), L + 40, 14, 18], [0, W / 2 + MARGIN + 9, L + 40, 6, 18],
      [-(L / 2 + MARGIN + 9), 0, 18, 10, W + 2 * MARGIN], [L / 2 + MARGIN + 9, 0, 18, 10, W + 2 * MARGIN],
    ];
    for (const [x, z, sx, sy, sz] of stands) {
      const b = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), standMat);
      b.position.set(x, sy / 2 - 1, z);
      scene.add(b);
    }
    // goals
    const postMat = new THREE.MeshLambertMaterial({ color: "#f4f4f4" });
    for (const gx of [-L / 2, L / 2]) {
      for (const dz of [-3.66, 3.66]) {
        const post = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 2.44), postMat);
        post.position.set(gx, 1.22, dz);
        scene.add(post);
      }
      const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 7.32), postMat);
      bar.rotation.x = Math.PI / 2;
      bar.position.set(gx, 2.44, 0);
      scene.add(bar);
    }

    const body = new THREE.CapsuleGeometry(0.34, 1.15, 4, 10);
    const shadowGeo = new THREE.CircleGeometry(0.55, 16);
    const shadowMat = new THREE.MeshBasicMaterial({ color: "#000000", transparent: true, opacity: 0.35 });
    const ringMat = new THREE.MeshBasicMaterial({ color: "#ffd447" });
    const ringGeo = new THREE.RingGeometry(0.7, 0.95, 24);
    const mats = new Map<string, THREE.MeshLambertMaterial>();
    const playerTeam = new Map<string, { team: string; gk: boolean }>();
    for (const team of [meta.home, meta.away]) {
      mats.set(team.team_id, new THREE.MeshLambertMaterial({ color: team.colors[0] }));
      mats.set(team.team_id + ":gk", new THREE.MeshLambertMaterial({ color: team.colors[1] }));
      for (const p of [...team.lineup, ...team.bench]) playerTeam.set(p.player_id, { team: team.team_id, gk: p.role === "GK" });
    }
    const meshes = new Map<string, THREE.Group>();
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.26, 16, 12), new THREE.MeshLambertMaterial({ color: "#ffffff" }));
    const ballShadow = new THREE.Mesh(shadowGeo, shadowMat);
    ballShadow.rotation.x = -Math.PI / 2;
    ballShadow.scale.setScalar(0.45);
    scene.add(ball, ballShadow);

    const camera = new THREE.PerspectiveCamera(30, 16 / 9, 1, 500);
    const look = new THREE.Vector3(0, 0, 0);
    const tagEls = new Map<string, HTMLDivElement>();

    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const w = el.clientWidth, h = el.clientHeight;
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
          let g = meshes.get(pid);
          if (!g) {
            const info = playerTeam.get(pid);
            g = new THREE.Group();
            const m = new THREE.Mesh(body, mats.get(info ? info.team + (info.gk ? ":gk" : "") : meta.home.team_id)!);
            m.position.y = 0.92;
            const s = new THREE.Mesh(shadowGeo, shadowMat);
            s.rotation.x = -Math.PI / 2;
            s.position.y = 0.02;
            const ring = new THREE.Mesh(ringGeo, ringMat);
            ring.rotation.x = -Math.PI / 2;
            ring.position.y = 0.03;
            ring.name = "ring";
            g.add(m, s, ring);
            scene.add(g);
            meshes.set(pid, g);
          }
          g.position.copy(toScene(x, y));
          g.getObjectByName("ring")!.visible = pid === focus.current;
        }
        for (const [pid, g] of meshes) if (!seen.has(pid)) { scene.remove(g); meshes.delete(pid); }
        const bp = toScene(f.ball[0], f.ball[1]);
        ball.position.set(bp.x, 0.26 + f.ball[2], bp.z);
        ballShadow.position.set(bp.x, 0.02, bp.z);
        // the main camera: high on the halfway line side, panning with the ball
        const target = new THREE.Vector3(THREE.MathUtils.clamp(bp.x, -L / 2 + 12, L / 2 - 12), 0, bp.z * 0.35);
        look.lerp(target, 0.06);
        camera.position.set(look.x * 0.85, 26, W / 2 + 46);
        camera.lookAt(look);
      }
      renderer.render(scene, camera);

      // HTML player tags, projected above each tagged player's head
      const layer = tagLayer.current;
      if (layer) {
        const want = new Map(tags.current.map((t) => [t.player_id, t]));
        for (const [pid, elTag] of tagEls) if (!want.has(pid)) { elTag.remove(); tagEls.delete(pid); }
        for (const [pid, t] of want) {
          const g = meshes.get(pid);
          if (!g) continue;
          let elTag = tagEls.get(pid);
          if (!elTag) { elTag = document.createElement("div"); elTag.className = "player-tag"; layer.appendChild(elTag); tagEls.set(pid, elTag); }
          elTag.innerHTML = "";
          const n = document.createElement("strong"); n.textContent = t.headline; elTag.appendChild(n);
          if (t.body) { const b = document.createElement("span"); b.textContent = t.body; elTag.appendChild(b); }
          const p = g.position.clone().setY(2.6).project(camera);
          elTag.style.transform = `translate(-50%, -100%) translate(${((p.x + 1) / 2) * w}px, ${((1 - p.y) / 2) * h}px)`;
        }
      }
    };
    tick();
    return () => {
      cancelAnimationFrame(raf);
      renderer.dispose();
      el.removeChild(renderer.domElement);
      tagEls.forEach((t) => t.remove());
    };
  }, [meta, getFrame, tags]);

  return (
    <div className="view-3d" ref={host}>
      <div className="tag-layer" ref={tagLayer} />
    </div>
  );
}
