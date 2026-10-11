import {
  Badge, Button, Dropdown, Field, FluentProvider, Option, SpinButton, Switch, Tab, TabList, ToggleButton, Tooltip,
} from "@fluentui/react-components";
import {
  Bot20Regular, Cube20Regular, DataBarVertical20Regular, Gauge20Regular, Grid20Regular, Map20Regular,
  Settings20Regular, SplitVertical20Regular, Square20Regular, Trophy20Regular,
} from "@fluentui/react-icons";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api, openStream } from "./api";
import type { Club, Cue, Frame, Lang, Mode } from "./api";
import { Crest } from "./Crest";
import { LANG_LABEL, dir, fill, strings } from "./i18n";
import { MatchBuffer, type Viewer } from "./match";
import { Overlays } from "./Overlays";
import { AgentTrace } from "./panels/AgentTrace";
import { Director } from "./panels/Director";
import { Evidence } from "./panels/Evidence";
import { Performance } from "./panels/Performance";
import { ScorecardPanel } from "./panels/Scorecard";
import { StatsPanel } from "./panels/Stats";
import { BroadcastView, type Tag } from "./views/BroadcastView";
import { TacticalView } from "./views/TacticalView";
import { gtTheme } from "./theme";

/** Fluent flips its controls for right-to-left only under a provider that says so. */
function Local({ lang, className, children }: { lang: Lang; className: string; children: React.ReactNode }) {
  return <FluentProvider theme={gtTheme} dir={dir(lang)} className={className}>{children}</FluentProvider>;
}

const LANGS: Lang[] = ["en", "es", "ar"];
const SPEEDS = [1, 2, 4, 8];

function savedLang(): Lang {
  try { const l = localStorage.getItem("gt-lang"); return l === "es" || l === "ar" ? l : "en"; } catch { return "en"; }
}
function saveLang(l: Lang) { try { localStorage.setItem("gt-lang", l); } catch { /* private mode */ } }

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

function ViewerColumn({ buf, viewer, setViewer, view, getT, slowT, compact }: {
  buf: MatchBuffer; viewer: Viewer; setViewer: (v: Viewer) => void; view: "broadcast" | "tactical";
  getT: () => number; slowT: number; compact: boolean;
}) {
  const meta = buf.meta!;
  const s = strings(viewer.lang);
  const [open, setOpen] = useState<Cue | null>(null);
  const tags = useRef<Tag[]>([]);
  const getFrame = useCallback((): Frame | null => buf.frameAt(getT()), [buf, getT]);
  const cues = buf.activeCues(slowT, viewer);
  tags.current = cues.filter((c) => c.layer === "player_tag" && c.anchor_player_id)
    .map((c) => ({ player_id: c.anchor_player_id!, headline: c.headline, body: c.body }));
  const everyone = [...meta.home.lineup, ...meta.home.bench, ...meta.away.lineup, ...meta.away.bench];
  if (viewer.player && !tags.current.some((t) => t.player_id === viewer.player)) {
    const p = everyone.find((x) => x.player_id === viewer.player);
    if (p) tags.current.push({ player_id: p.player_id, headline: p.short_name });
  }
  const frame = buf.frameAt(slowT);
  const evidence = open ? open.evidence.map((id) => buf.event(id)).filter((e): e is NonNullable<typeof e> => !!e) : [];
  const players = [...meta.home.lineup, ...meta.away.lineup];
  const clubName = (id: string | null) => (id === meta.home.team_id ? meta.home.short_name : id === meta.away.team_id ? meta.away.short_name : s.anyClub);
  const playerName = (id: string | null) => { const p = players.find((x) => x.player_id === id); return p ? `${p.number} ${p.short_name}` : s.noPlayer; };
  const langs = [...new Set(buf.cues.map((c) => c.lang))];
  return (
    <Local lang={viewer.lang} className={`viewer ${compact ? "compact" : ""}`}>
      <div className="viewer-bar">
        <Dropdown className="vb-dd" size="small" appearance="filled-darker" aria-label={s.viewer}
          value={viewer.mode === "analyst" ? s.analyst : s.casual} selectedOptions={[viewer.mode]}
          onOptionSelect={(_, d) => setViewer({ ...viewer, mode: d.optionValue as Mode })}>
          <Option value="analyst">{s.analyst}</Option><Option value="casual">{s.casual}</Option>
        </Dropdown>
        <Dropdown className="vb-dd" size="small" appearance="filled-darker" aria-label={s.language}
          value={LANG_LABEL[viewer.lang]} selectedOptions={[viewer.lang]}
          onOptionSelect={(_, d) => setViewer({ ...viewer, lang: d.optionValue as Lang })}>
          {LANGS.map((l) => <Option key={l} value={l}>{LANG_LABEL[l]}</Option>)}
        </Dropdown>
        <Dropdown className="vb-dd" size="small" appearance="filled-darker" aria-label={s.club}
          value={clubName(viewer.club)} selectedOptions={[viewer.club ?? ""]}
          onOptionSelect={(_, d) => setViewer({ ...viewer, club: d.optionValue || null })}>
          <Option value="">{s.anyClub}</Option>
          <Option value={meta.home.team_id}>{meta.home.short_name}</Option>
          <Option value={meta.away.team_id}>{meta.away.short_name}</Option>
        </Dropdown>
        {!compact && (
          <Dropdown className="vb-dd" size="small" appearance="filled-darker" aria-label={s.player}
            value={`${s.player}: ${playerName(viewer.player)}`} selectedOptions={[viewer.player ?? ""]}
            onOptionSelect={(_, d) => setViewer({ ...viewer, player: d.optionValue || null })}>
            <Option value="">{s.noPlayer}</Option>
            {players.map((p) => <Option key={p.player_id} value={p.player_id} text={`${p.number} ${p.short_name}`}>{p.number} {p.short_name}</Option>)}
          </Dropdown>
        )}
      </div>
      <div className="stage" dir="ltr">
        {view === "broadcast"
          ? <BroadcastView meta={meta} getFrame={getFrame} tags={tags} focusPlayer={viewer.player} />
          : <TacticalView meta={meta} getFrame={getFrame} focusPlayer={viewer.player} highlight={evidence} tags={tags} />}
        <Overlays meta={meta} cues={cues} score={buf.scoreAt(slowT)} period={frame?.period ?? 1} clockMs={frame?.clock_ms ?? 0}
          lang={viewer.lang} fullTime={buf.fullTime && slowT >= buf.liveT - 1} scorecard={buf.scorecard}
          runFor={(id) => buf.runFor(id)} onOpen={setOpen} />
        {open && <Evidence cue={open} events={evidence} board={buf.board} langs={langs} lang={viewer.lang} onClose={() => setOpen(null)} />}
      </div>
    </Local>
  );
}

type SideTab = "agents" | "perf" | "director" | "stats" | "score";

function MatchScreen({ matchId }: { matchId: string }) {
  const buf = useMemo(() => new MatchBuffer(), [matchId]);
  useSyncExternalStore(buf.subscribe, buf.snapshot);
  const [view, setView] = useState<"broadcast" | "tactical">("broadcast");
  const [layout, setLayout] = useState<1 | 2 | 4>(1);
  const [tab, setTab] = useState<SideTab>("agents");
  const [viewers, setViewers] = useState<Viewer[]>(() => {
    const l = savedLang();
    return [
      { mode: "analyst", lang: l, club: null, player: null },
      { mode: "casual", lang: l === "ar" ? "en" : "ar", club: null, player: null },
      { mode: "casual", lang: "es", club: null, player: null },
      { mode: "analyst", lang: l === "en" ? "es" : "en", club: null, player: null },
    ];
  });
  const { t, slow } = usePlayhead(buf);
  const getT = useCallback(() => t.current, [t]);

  useEffect(() => openStream(matchId, (m) => buf.handle(m), () => undefined), [matchId, buf]);
  useEffect(() => { // the second and third viewers each support one club
    if (!buf.meta) return;
    setViewers((vs) => vs.map((v, i) => (i === 1 && !v.club ? { ...v, club: buf.meta!.away.team_id } : i === 2 && !v.club ? { ...v, club: buf.meta!.home.team_id } : v)));
  }, [buf.meta]);
  const sawFullTime = useRef(false);
  useEffect(() => { if (buf.scorecard && !sawFullTime.current) { sawFullTime.current = true; setTab("score"); } }, [buf.scorecard]);

  if (!buf.meta) return <div className="loading"><span className="loading-mark" />GroundTruth</div>;
  const meta = buf.meta;
  const lead = viewers[0].lang;
  const s = strings(lead);
  const setViewer = (i: number) => (v: Viewer) => setViewers((vs) => vs.map((x, j) => (j === i ? v : x)));
  const shown = viewers.slice(0, layout);
  const langs = [...new Set(buf.cues.map((c) => c.lang))];
  return (
    <Local lang={lead} className="match">
      <header className="topbar">
        <a href="#" className="wordmark" aria-label="GroundTruth home"><Mark />Ground<span>Truth</span></a>
        <span className="fixture" dir="ltr">
          <Crest colors={meta.home.colors} code={meta.home.short_name} size={20} />
          <strong>{meta.home.name}</strong><span className="muted">v</span><strong>{meta.away.name}</strong>
          <Crest colors={meta.away.colors} code={meta.away.short_name} size={20} />
          <span className="muted venue">{meta.venue}</span>
        </span>
        {buf.fullTime
          ? <Badge appearance="filled" color="informative">{s.fullTime}</Badge>
          : <Badge appearance="filled" color="danger" className="live-badge">{s.live}</Badge>}
        <span className="muted delay">{fill(s.delay, { s: buf.delayS })}</span>
        <span className="spacer" />
        <TabList size="small" selectedValue={view} onTabSelect={(_, d) => setView(d.value as typeof view)}>
          <Tab value="broadcast" icon={<Cube20Regular />}>{s.broadcast}</Tab>
          <Tab value="tactical" icon={<Map20Regular />}>{s.tactical}</Tab>
        </TabList>
        <div className="seg" role="group" aria-label={s.layout}>
          {([[1, <Square20Regular key="1" />], [2, <SplitVertical20Regular key="2" />], [4, <Grid20Regular key="4" />]] as const).map(([n, icon]) => (
            <Tooltip key={n} content={`${n} ${s.layout.toLowerCase()}`} relationship="label">
              <ToggleButton size="small" appearance="subtle" icon={icon} checked={layout === n} onClick={() => setLayout(n)} />
            </Tooltip>
          ))}
        </div>
        <div className="seg" role="group" aria-label={s.speed}>
          {SPEEDS.map((v) => (
            <ToggleButton key={v} size="small" appearance="subtle" checked={buf.speed === v} disabled={buf.fullTime}
              onClick={() => { void api.speed(matchId, v); }}>{v}×</ToggleButton>
          ))}
        </div>
      </header>
      <main className="match-body">
        <div className={`viewers n${layout}`}>
          {shown.map((v, i) => (
            <ViewerColumn key={i} buf={buf} viewer={v} setViewer={setViewer(i)} view={view} getT={getT} slowT={slow} compact={layout === 4} />
          ))}
        </div>
        <aside className="side">
          <TabList size="small" selectedValue={tab} onTabSelect={(_, d) => setTab(d.value as SideTab)} className="side-tabs">
            <Tab value="agents" icon={<Bot20Regular />}>{s.agents}</Tab>
            <Tab value="perf" icon={<Gauge20Regular />}>{s.performance}</Tab>
            <Tab value="director" icon={<Settings20Regular />}>{s.director}</Tab>
            <Tab value="stats" icon={<DataBarVertical20Regular />}>{s.stats}</Tab>
            <Tab value="score" icon={<Trophy20Regular />}>{s.scorecard}</Tab>
          </TabList>
          <div className="side-body">
            {tab === "agents" && <AgentTrace board={buf.board} meta={meta} moments={buf.moments} langs={langs} lang={lead} />}
            {tab === "perf" && <Performance summary={buf.summary} runs={buf.runs} lang={lead} />}
            {tab === "director" && <Director matchId={matchId} meta={meta} log={buf.console} fullTime={buf.fullTime} outage={buf.outage} lang={lead} />}
            {tab === "stats" && <StatsPanel meta={meta} state={buf.state} lang={lead} />}
            {tab === "score" && <ScorecardPanel meta={meta} card={buf.scorecard} lang={lead} />}
          </div>
        </aside>
      </main>
    </Local>
  );
}

function Mark() {
  return (
    <svg className="mark" viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
      <path d="M8 23V9h15" stroke="currentColor" strokeWidth="3" fill="none" />
      <path d="M12.5 18l3.5 3.5 7-8" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ClubPicker({ clubs, value, other, onPick, label }: { clubs: Club[]; value: string; other: string; onPick: (id: string) => void; label: string }) {
  return (
    <div className="club-picker" role="radiogroup" aria-label={label}>
      <span className="club-picker-label">{label}</span>
      {clubs.map((c) => (
        <button key={c.club_id} role="radio" aria-checked={value === c.club_id} disabled={other === c.club_id}
          className={`club ${value === c.club_id ? "on" : ""}`} style={{ ["--team" as string]: c.colors[0] }} onClick={() => onPick(c.club_id)}>
          <Crest colors={c.colors} code={c.short_name} size={30} />
          <span className="club-name">{c.name}</span>
          <span className="club-meta">{c.formation}</span>
        </button>
      ))}
    </div>
  );
}

function Home({ onGo }: { onGo: (id: string) => void }) {
  const [lang, setLang] = useState<Lang>(savedLang);
  const s = strings(lang);
  const [clubs, setClubs] = useState<Club[]>([]);
  const [live, setLive] = useState<{ match_id: string; home: string; away: string; finished: boolean; score: number[] }[]>([]);
  const [form, setForm] = useState({ home: "RVM", away: "HLB", seed: 7, speed: 4, scripted: true });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.league().then(setClubs).catch((e) => setErr(String(e))); api.matches().then(setLive).catch(() => undefined); }, []);
  const go = async () => {
    setBusy(true);
    try { const r = await api.create(form); onGo(r.match_id); } catch (e) { setErr(String(e)); setBusy(false); }
  };
  const club = (id: string) => clubs.find((c) => c.club_id === id);
  return (
    <Local lang={lang} className="home">
      <header className="home-top">
        <span className="wordmark"><Mark />Ground<span>Truth</span></span>
        <Dropdown className="vb-dd" size="small" appearance="filled-darker" aria-label={s.language} value={LANG_LABEL[lang]}
          selectedOptions={[lang]} onOptionSelect={(_, d) => { const l = d.optionValue as Lang; setLang(l); saveLang(l); }}>
          {LANGS.map((l) => <Option key={l} value={l}>{LANG_LABEL[l]}</Option>)}
        </Dropdown>
      </header>
      <section className="hero">
        <div className="hero-copy">
          <h1>{s.tagline}</h1>
          <p className="lede">{s.lede}</p>
          <ol className="steps">
            <li><span className="step-n">1</span><strong>{s.step1}</strong><span>{s.step1Body}</span></li>
            <li><span className="step-n">2</span><strong>{s.step2}</strong><span>{s.step2Body}</span></li>
            <li><span className="step-n">3</span><strong>{s.step3}</strong><span>{s.step3Body}</span></li>
          </ol>
        </div>
        <div className="kickoff">
          <h2>{s.newMatch}</h2>
          <ClubPicker clubs={clubs} value={form.home} other={form.away} label={s.home} onPick={(id) => setForm({ ...form, home: id })} />
          <ClubPicker clubs={clubs} value={form.away} other={form.home} label={s.away} onPick={(id) => setForm({ ...form, away: id })} />
          <div className="kickoff-row">
            <Field label={s.seed} size="small">
              <SpinButton size="small" value={form.seed} min={0} max={99999}
                onChange={(_, d) => setForm({ ...form, seed: d.value ?? (Number(d.displayValue) || 0) })} />
            </Field>
            <Field label={s.speed} size="small">
              <div className="seg">
                {SPEEDS.map((v) => (
                  <ToggleButton key={v} size="small" checked={form.speed === v} onClick={() => setForm({ ...form, speed: v })}>{v}×</ToggleButton>
                ))}
              </div>
            </Field>
          </div>
          <Switch checked={form.scripted} label={s.hidden} onChange={(_, d) => setForm({ ...form, scripted: d.checked })} />
          <p className="muted small">{s.hiddenNote}</p>
          <Button appearance="primary" size="large" className="kickoff-go" disabled={busy || form.home === form.away} onClick={go}>
            {s.start}{club(form.home) && club(form.away) ? ` · ${club(form.home)!.short_name} v ${club(form.away)!.short_name}` : ""}
          </Button>
          {err && <p className="error">{err}</p>}
        </div>
      </section>
      {live.length > 0 && (
        <section className="live-now">
          <h2>{s.liveNow}</h2>
          <ul>{live.map((m) => {
            const h = club(m.home), a = club(m.away);
            return (
              <li key={m.match_id}>
                {h && <Crest colors={h.colors} code={h.short_name} size={20} />}
                <span dir="ltr" className="live-score">{h?.short_name ?? m.home} {m.score[0]}–{m.score[1]} {a?.short_name ?? m.away}</span>
                {a && <Crest colors={a.colors} code={a.short_name} size={20} />}
                <Badge appearance="tint" color={m.finished ? "informative" : "danger"}>{m.finished ? s.fullTime : s.live}</Badge>
                <Button size="small" appearance="secondary" onClick={() => onGo(m.match_id)}>{s.join}</Button>
              </li>
            );
          })}</ul>
        </section>
      )}
    </Local>
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
