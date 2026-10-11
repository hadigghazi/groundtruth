import {
  Badge, Button, Dropdown, Field, FluentProvider, Option, SpinButton, Switch, Tab, TabList, ToggleButton, Tooltip,
} from "@fluentui/react-components";
import {
  Bot20Regular, Chat20Regular, Cube20Regular, DataBarVertical20Regular, Gauge20Regular, Grid20Regular, History20Regular, Map20Regular,
  Camera20Regular, Settings20Regular, Speaker220Regular, SpeakerMute20Regular, SplitVertical20Regular, Square20Regular, Trophy20Regular,
} from "@fluentui/react-icons";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api, openStream } from "./api";
import type { Club, Cue, Frame, Lang, Mode } from "./api";
import { Crest } from "./Crest";
import { METRICS, MetricFocus, PlayerHud } from "./Focus";
import { LANG_LABEL, dir, fill, strings } from "./i18n";
import { MatchBuffer, type Viewer } from "./match";
import { usePlayhead } from "./playhead";
import { Transport } from "./Transport";
import { Overlays } from "./Overlays";
import { AgentTrace } from "./panels/AgentTrace";
import { AskPanel } from "./panels/Ask";
import { Recap, fetchRecap, type RecapCard } from "./Recap";
import { Director } from "./panels/Director";
import { Evidence } from "./panels/Evidence";
import { Performance } from "./panels/Performance";
import { ScorecardPanel } from "./panels/Scorecard";
import { StatsPanel } from "./panels/Stats";
import { BroadcastView, type Tag } from "./views/BroadcastView";
import { TacticalView } from "./views/TacticalView";
import { gtTheme } from "./theme";
import { useVoice } from "./voice";

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

function ViewerColumn({ buf, viewer, setViewer, view, getT, slowT, compact, seek }: {
  buf: MatchBuffer; viewer: Viewer; setViewer: (v: Viewer) => void; view: "broadcast" | "tactical";
  getT: () => number; slowT: number; compact: boolean; seek: (t: number) => void;
}) {
  const meta = buf.meta!;
  const s = strings(viewer.lang);
  const [open, setOpen] = useState<Cue | null>(null);
  const [voice, setVoice] = useState(false);
  const [recap, setRecap] = useState<RecapCard | null>(null);
  const catchUp = async () => {
    setRecap({ loading: true });
    try { setRecap(await fetchRecap(meta.match_id, viewer.lang, viewer.mode, getT())); } catch { setRecap(null); }
  };
  const tags = useRef<Tag[]>([]);
  const getFrame = useCallback((): Frame | null => buf.frameAt(getT()), [buf, getT]);
  const cues = buf.activeCues(slowT, viewer);
  useVoice(cues, viewer.lang, viewer.mode, voice);
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
        <Tooltip content={s.catchUp} relationship="label">
          <Button size="small" appearance="subtle" icon={<History20Regular />} onClick={() => void catchUp()}>{compact ? "" : s.catchUp}</Button>
        </Tooltip>
        <Tooltip content={s.voice} relationship="label">
          <ToggleButton size="small" appearance="subtle" checked={voice} onClick={() => setVoice(!voice)}
            icon={voice ? <Speaker220Regular /> : <SpeakerMute20Regular />} />
        </Tooltip>
        {!compact && (
          <Dropdown className="vb-dd" size="small" appearance="filled-darker" aria-label={s.metricWord}
            value={`${s.metricWord}: ${viewer.metric ? METRICS[viewer.metric].label[viewer.lang] : s.noMetric}`}
            selectedOptions={[viewer.metric ?? ""]} onOptionSelect={(_, d) => setViewer({ ...viewer, metric: d.optionValue || null })}>
            <Option value="">{s.noMetric}</Option>
            {Object.entries(METRICS).map(([k, m]) => <Option key={k} value={k}>{m.label[viewer.lang]}</Option>)}
          </Dropdown>
        )}
        {viewer.player && view === "broadcast" && (
          <Tooltip content={s.playerCam} relationship="label">
            <ToggleButton size="small" appearance="subtle" checked={!!viewer.cam} icon={<Camera20Regular />}
              onClick={() => setViewer({ ...viewer, cam: !viewer.cam })} />
          </Tooltip>
        )}
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
        <div className="frame">
          {view === "broadcast"
            ? <BroadcastView meta={meta} getFrame={getFrame} tags={tags} focusPlayer={viewer.player} compact={compact}
                followPlayer={viewer.cam ? viewer.player : null} />
            : <TacticalView meta={meta} getFrame={getFrame} focusPlayer={viewer.player} highlight={evidence} tags={tags} />}
          <Overlays meta={meta} cues={cues} score={buf.scoreAt(slowT)} period={frame?.period ?? 1} clockMs={frame?.clock_ms ?? 0}
            lang={viewer.lang} fullTime={buf.fullTime && slowT >= buf.liveT - 1} scorecard={buf.scorecard}
            runFor={(id) => buf.runFor(id)} onOpen={setOpen} recap={buf.recap} mode={viewer.mode} onClip={seek} />
          {recap && <Recap card={recap} lang={viewer.lang} onClose={() => setRecap(null)} />}
          {viewer.metric && !buf.fullTime && <MetricFocus buf={buf} meta={meta} metric={viewer.metric} upTo={slowT} lang={viewer.lang} />}
          {viewer.player && !compact && <PlayerHud meta={meta} line={buf.stateAt(slowT)?.players?.[viewer.player]}
            playerId={viewer.player} lang={viewer.lang} />}
        </div>
        {open && <Evidence cue={open} events={evidence} board={buf.board} langs={langs} lang={viewer.lang} onClose={() => setOpen(null)}
          onReplay={() => { seek(open.t_in - 15_000); setOpen(null); }} />}
      </div>
    </Local>
  );
}

type SideTab = "agents" | "perf" | "director" | "stats" | "ask" | "score";

/** Events the viewer has already seen: nothing past the playhead reaches the analyst panels. */
function seenEvents(buf: MatchBuffer, t: number) {
  const at = buf.timeOfClock();
  return buf.events.filter((e) => at(e.period, e.clock_ms) <= t);
}

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
  const ph = usePlayhead(buf);
  const { t, slow } = ph;
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
        <a href="#" className="wordmark" dir="ltr" aria-label="GroundTruth home"><Mark /><span>Ground<b>Truth</b></span></a>
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
      <Transport buf={buf} ph={ph} lang={lead} />
      <main className="match-body">
        <div className={`viewers n${layout}`}>
          {shown.map((v, i) => (
            <ViewerColumn key={i} buf={buf} viewer={v} setViewer={setViewer(i)} view={view} getT={getT} slowT={slow} compact={layout === 4} seek={ph.seek} />
          ))}
        </div>
        <aside className="side">
          <TabList size="small" selectedValue={tab} onTabSelect={(_, d) => setTab(d.value as SideTab)} className="side-tabs">
            <Tab value="agents" icon={<Bot20Regular />}>{s.agents}</Tab>
            <Tab value="perf" icon={<Gauge20Regular />}>{s.performance}</Tab>
            <Tab value="director" icon={<Settings20Regular />}>{s.director}</Tab>
            <Tab value="stats" icon={<DataBarVertical20Regular />}>{s.stats}</Tab>
            <Tab value="ask" icon={<Chat20Regular />}>{s.ask}</Tab>
            <Tab value="score" icon={<Trophy20Regular />}>{s.scorecard}</Tab>
          </TabList>
          <div className="side-body">
            {tab === "agents" && <AgentTrace board={buf.board} meta={meta} moments={buf.moments} langs={langs} lang={lead} />}
            {tab === "perf" && <Performance summary={buf.summary} runs={buf.runs} lang={lead} />}
            {tab === "director" && <Director matchId={matchId} meta={meta} log={buf.console} fullTime={buf.fullTime} outage={buf.outage} lang={lead} />}
            {tab === "stats" && <StatsPanel matchId={matchId} meta={meta} state={buf.stateAt(slow)?.state ?? null} lang={lead} upTo={slow}
              events={seenEvents(buf, slow)} storylines={buf.storylines.filter((x) => x.t <= slow)} />}
            {tab === "ask" && <AskPanel matchId={matchId} getT={getT} lang={lead} />}
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
        <span className="wordmark" dir="ltr"><Mark /><span>Ground<b>Truth</b></span></span>
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
