import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api, openStream } from "./api";
import type { Club, Cue, Frame, Lang, Mode } from "./api";
import { LANG_LABEL, dir, strings } from "./i18n";
import { MatchBuffer, type Viewer } from "./match";
import { Overlays } from "./Overlays";
import { AgentTrace, Console, Evidence, StatsPanel } from "./Panels";
import { BroadcastView, type Tag } from "./views/BroadcastView";
import { TacticalView } from "./views/TacticalView";

/** A smooth, delayed playhead: advances with the wall clock, re-anchors to the live edge. */
function usePlayhead(buf: MatchBuffer) {
  const t = useRef(0);
  const [slow, setSlow] = useState(0); // a 4 Hz copy for React-rendered overlays
  useEffect(() => {
    let raf = 0, last = performance.now(), lastSlow = 0;
    const step = (now: number) => {
      raf = requestAnimationFrame(step);
      const target = Math.max(0, buf.liveT - buf.delayMs);
      const dt = now - last;
      last = now;
      t.current = Math.abs(target - t.current) > 4000 ? target : Math.min(target, t.current + dt * Math.max(buf.speed, 1));
      if (now - lastSlow > 250) { lastSlow = now; setSlow(t.current); }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [buf]);
  return { t, slow };
}

function ViewerColumn({ buf, matchId, viewer, setViewer, view, getT, slowT }: {
  buf: MatchBuffer; matchId: string; viewer: Viewer; setViewer: (v: Viewer) => void; view: "broadcast" | "tactical";
  getT: () => number; slowT: number;
}) {
  const meta = buf.meta!;
  const s = strings(viewer.lang);
  const [open, setOpen] = useState<Cue | null>(null);
  const tags = useRef<Tag[]>([]);
  const getFrame = useCallback((): Frame | null => buf.frameAt(getT()), [buf, getT]);
  const cues = buf.activeCues(slowT, viewer);
  tags.current = cues.filter((c) => c.layer === "player_tag" && c.anchor_player_id)
    .map((c) => ({ player_id: c.anchor_player_id!, headline: c.headline, body: c.body }));
  if (viewer.player && !tags.current.some((t) => t.player_id === viewer.player)) {
    const p = [...meta.home.lineup, ...meta.home.bench, ...meta.away.lineup, ...meta.away.bench].find((x) => x.player_id === viewer.player);
    if (p) tags.current.push({ player_id: p.player_id, headline: p.short_name });
  }
  const frame = buf.frameAt(slowT);
  const evidence = open ? open.evidence.map((id) => buf.event(id)).filter((e): e is NonNullable<typeof e> => !!e) : [];
  const players = [...meta.home.lineup, ...meta.away.lineup];
  return (
    <div className="viewer" dir={dir(viewer.lang)}>
      <div className="viewer-bar">
        <label>{s.viewer}
          <select value={viewer.mode} onChange={(e) => setViewer({ ...viewer, mode: e.target.value as Mode })}>
            <option value="analyst">{s.analyst}</option><option value="casual">{s.casual}</option>
          </select>
        </label>
        <label>{s.language}
          <select value={viewer.lang} onChange={(e) => setViewer({ ...viewer, lang: e.target.value as Lang })}>
            {(["en", "es", "ar"] as Lang[]).map((l) => <option key={l} value={l}>{LANG_LABEL[l]}</option>)}
          </select>
        </label>
        <label>{s.club}
          <select value={viewer.club ?? ""} onChange={(e) => setViewer({ ...viewer, club: e.target.value || null })}>
            <option value="">{s.anyClub}</option>
            <option value={meta.home.team_id}>{meta.home.short_name}</option>
            <option value={meta.away.team_id}>{meta.away.short_name}</option>
          </select>
        </label>
        <label>{s.player}
          <select value={viewer.player ?? ""} onChange={(e) => setViewer({ ...viewer, player: e.target.value || null })}>
            <option value="">{s.noPlayer}</option>
            {players.map((p) => <option key={p.player_id} value={p.player_id}>{p.number} {p.short_name}</option>)}
          </select>
        </label>
      </div>
      <div className="stage-area" dir="ltr">
        {view === "broadcast"
          ? <BroadcastView meta={meta} getFrame={getFrame} tags={tags} focusPlayer={viewer.player} />
          : <TacticalView meta={meta} getFrame={getFrame} focusPlayer={viewer.player} highlight={evidence} tags={tags} />}
        <Overlays meta={meta} cues={cues} score={buf.scoreAt(slowT)} period={frame?.period ?? 1} clockMs={frame?.clock_ms ?? 0}
          lang={viewer.lang} fullTime={buf.fullTime && slowT >= buf.liveT - 1} onOpen={setOpen} />
        {open && <Evidence cue={open} events={evidence} lang={viewer.lang} onClose={() => setOpen(null)} />}
      </div>
      <p className="delay-note muted">{s.live} · {s.delay.replace("{s}", String(Math.round(buf.delayMs / 1000 / Math.max(buf.speed, 1))))}</p>
      <span hidden>{matchId}</span>
    </div>
  );
}

function MatchScreen({ matchId }: { matchId: string }) {
  const buf = useMemo(() => new MatchBuffer(), [matchId]);
  useSyncExternalStore(buf.subscribe, buf.snapshot);
  const [view, setView] = useState<"broadcast" | "tactical">("broadcast");
  const [twoUp, setTwoUp] = useState(false);
  const [tab, setTab] = useState<"trace" | "console" | "stats">("trace");
  const [left, setLeft] = useState<Viewer>({ mode: "analyst", lang: "en", club: null, player: null });
  const [right, setRight] = useState<Viewer>({ mode: "casual", lang: "ar", club: null, player: null });
  const { t, slow } = usePlayhead(buf);
  const getT = useCallback(() => t.current, [t]);

  useEffect(() => openStream(matchId, (m) => buf.handle(m), () => undefined), [matchId, buf]);
  useEffect(() => {
    if (buf.meta && right.club === null) setRight((r) => ({ ...r, club: buf.meta!.away.team_id }));
  }, [buf.meta]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!buf.meta) return <div className="loading">…</div>;
  const s = strings(left.lang);
  return (
    <div className="match">
      <header className="topbar">
        <a href="#" className="brand">GroundTruth</a>
        <span className="fixture">{buf.meta.home.name} v {buf.meta.away.name} · {buf.meta.venue}</span>
        <div className="seg">
          <button className={view === "broadcast" ? "on" : ""} onClick={() => setView("broadcast")}>{s.broadcast}</button>
          <button className={view === "tactical" ? "on" : ""} onClick={() => setView("tactical")}>{s.tactical}</button>
        </div>
        <label className="toggle"><input type="checkbox" checked={twoUp} onChange={(e) => setTwoUp(e.target.checked)} /> {s.twoUp}</label>
      </header>
      <main className={`match-body ${twoUp ? "two-up" : ""}`}>
        <div className="viewers">
          <ViewerColumn buf={buf} matchId={matchId} viewer={left} setViewer={setLeft} view={view} getT={getT} slowT={slow} />
          {twoUp && <ViewerColumn buf={buf} matchId={matchId} viewer={right} setViewer={setRight} view={view} getT={getT} slowT={slow} />}
        </div>
        <aside className="side">
          <div className="tabs">
            <button className={tab === "trace" ? "on" : ""} onClick={() => setTab("trace")}>{s.trace}</button>
            <button className={tab === "console" ? "on" : ""} onClick={() => setTab("console")}>{s.consoleTab}</button>
            <button className={tab === "stats" ? "on" : ""} onClick={() => setTab("stats")}>{s.stats}</button>
          </div>
          <div className="tab-body">
            {tab === "trace" && <AgentTrace board={buf.board} lang={left.lang} />}
            {tab === "console" && <Console matchId={matchId} meta={buf.meta} log={buf.console} fullTime={buf.fullTime} lang={left.lang} />}
            {tab === "stats" && <StatsPanel meta={buf.meta} state={buf.state} lang={left.lang} />}
          </div>
        </aside>
      </main>
    </div>
  );
}

function Home({ onGo }: { onGo: (id: string) => void }) {
  const s = strings("en");
  const [clubs, setClubs] = useState<Club[]>([]);
  const [live, setLive] = useState<{ match_id: string; home: string; away: string; finished: boolean; score: number[] }[]>([]);
  const [form, setForm] = useState({ home: "RVM", away: "HLB", seed: 7, speed: 4, scripted: true });
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.league().then(setClubs).catch((e) => setErr(String(e))); api.matches().then(setLive).catch(() => undefined); }, []);
  const go = async () => {
    try { const r = await api.create(form); onGo(r.match_id); } catch (e) { setErr(String(e)); }
  };
  return (
    <div className="home">
      <h1>GroundTruth</h1>
      <p className="tagline">{s.tagline}</p>
      <section className="card">
        <h2>{s.newMatch}</h2>
        <div className="form">
          <label>{s.home}<select value={form.home} onChange={(e) => setForm({ ...form, home: e.target.value })}>{clubs.map((c) => <option key={c.club_id} value={c.club_id}>{c.name}</option>)}</select></label>
          <label>{s.away}<select value={form.away} onChange={(e) => setForm({ ...form, away: e.target.value })}>{clubs.map((c) => <option key={c.club_id} value={c.club_id}>{c.name}</option>)}</select></label>
          <label>{s.seed}<input type="number" value={form.seed} onChange={(e) => setForm({ ...form, seed: Number(e.target.value) })} /></label>
          <label>{s.speed}<select value={form.speed} onChange={(e) => setForm({ ...form, speed: Number(e.target.value) })}>{[1, 2, 4, 8].map((v) => <option key={v} value={v}>{v}×</option>)}</select></label>
          <label className="check"><input type="checkbox" checked={form.scripted} onChange={(e) => setForm({ ...form, scripted: e.target.checked })} /> {s.hidden}</label>
          <button className="primary" onClick={go} disabled={form.home === form.away}>{s.start}</button>
        </div>
        {err && <p className="error">{err}</p>}
      </section>
      {live.length > 0 && (
        <section className="card">
          <ul className="live-list">{live.map((m) => (
            <li key={m.match_id}>{m.home} {m.score[0]}–{m.score[1]} {m.away} {m.finished ? `(${s.fullTime})` : `(${s.live})`}
              <button onClick={() => onGo(m.match_id)}>{s.join}</button></li>
          ))}</ul>
        </section>
      )}
    </div>
  );
}

export default function App() {
  const [id, setId] = useState<string | null>(() => new URLSearchParams(location.hash.slice(1)).get("match"));
  useEffect(() => {
    const onHash = () => setId(new URLSearchParams(location.hash.slice(1)).get("match"));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = (m: string) => { location.hash = `match=${m}`; setId(m); };
  return id ? <MatchScreen matchId={id} /> : <Home onGo={go} />;
}
