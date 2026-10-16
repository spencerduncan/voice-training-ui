import { useEffect, useRef, useState } from "react";
import type { Recording, ReferenceVoice } from "./types";
import { loadRecordings, loadReferences, type Unreadable } from "./data";
import {
  PITCH_ZONES,
  F2_ZONES,
  LOUD_ZONES,
  HNR_ZONES,
  JITTER_ZONES,
  WEIGHT_ZONES,
  WEIGHT_DIRECTION,
  MELODY_ZONES,
  zoneOf,
  goalOf,
  fmt,
} from "./zones";
import { StatCard } from "./components/StatCard";
import { MetricModal } from "./components/MetricModal";
import { METRICS, type MetricKey } from "./metrics";
import { ResonanceCard } from "./components/ResonanceCard";
import { LineChart, type Point, type ChartBand } from "./components/LineChart";
import { RecordingCard } from "./components/RecordingCard";
import { CheatSheet } from "./components/CheatSheet";
import { VoiceCareNote } from "./components/VoiceCareNote";
import { RegisterSection } from "./components/RegisterSection";
import {
  AnnotationsProvider,
  Note,
  Region,
} from "./annotations/AnnotationsProvider";
import {
  TulipIcon,
  BowIcon,
  SparkleIcon,
  ContourIcon,
  InsightIcon,
  TrendsIcon,
  CardsIcon,
  BulbIcon,
} from "./components/icons";

// The selected take lives in the URL hash (#take=N), so a reload or a shared
// link opens the same take. Anything else in the hash is ignored.
function takeFromHash(): number | null {
  const m = /(?:^#|&)take=(\d+)/.exec(window.location.hash);
  return m ? Number(m[1]) : null;
}

const prefersReducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

type Load =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; recordings: Recording[]; unreadable: Unreadable[] };

export function App() {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [selectedId, setSelectedId] = useState<number | null>(takeFromHash);
  const [references, setReferences] = useState<ReferenceVoice[]>([]);
  // which metric's reference modal is open + the card rect it grew from
  const [modal, setModal] = useState<{ key: MetricKey; rect: DOMRect } | null>(
    null,
  );
  // the "This take" heading — selection from the cards below scrolls back here
  const takeHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let alive = true;
    loadRecordings()
      .then((d) => {
        if (alive) setLoad({ status: "ready", ...d });
      })
      .catch((e: unknown) => {
        if (alive)
          setLoad({
            status: "error",
            message: e instanceof Error ? e.message : String(e),
          });
      });
    return () => {
      alive = false;
    };
  }, []);

  // reference voices (real men/women) — degrade gracefully if missing.
  useEffect(() => {
    let alive = true;
    loadReferences().then((refs) => {
      if (alive) setReferences(refs);
    });
    return () => {
      alive = false;
    };
  }, []);

  // follow hand-edited hashes and back/forward between #take= links
  useEffect(() => {
    const onHash = () => setSelectedId(takeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const select = (id: number, scrollToTake = false) => {
    setSelectedId(id);
    // replaceState: switching takes shouldn't pile up history entries, and it
    // doesn't fire hashchange (so no loop with the listener above)
    window.history.replaceState(null, "", `#take=${id}`);
    if (scrollToTake) {
      const h = takeHeading.current;
      h?.focus({ preventScroll: true });
      h?.scrollIntoView({
        behavior: prefersReducedMotion() ? "auto" : "smooth",
        block: "start",
      });
    }
  };

  const openModal = (key: MetricKey, rect: DOMRect) => setModal({ key, rect });

  const R = load.status === "ready" ? load.recordings : [];
  const unreadable = load.status === "ready" ? load.unreadable : [];
  const latest = R.length ? R[R.length - 1] : null;
  // the recording currently in focus (defaults to the latest; a #take= for a
  // take that doesn't exist falls back to the latest too)
  const active =
    (selectedId != null ? R.find((r) => r.id === selectedId) : null) ?? latest;
  const pz = active ? zoneOf(PITCH_ZONES, active.pitch.mean_hz) : null;
  const pitchGoal = goalOf(PITCH_ZONES);
  // Pitch alone doesn't settle how a voice reads — f0 and formants are read
  // together (Hillenbrand & Clark 2009), so the pitch note is conditioned on
  // where F2 sits before it ever says "reads feminine".
  const f2z = active ? zoneOf(F2_ZONES, active.formants.f2_hz) : null;
  const isLatest = active && latest && active.id === latest.id;

  const mk = (sel: (r: Recording) => number | null): Point[] =>
    R.map((r) => ({ id: r.id, date: r.date, y: sel(r) }));
  const activeId = active?.id ?? null;

  return (
    <div className="wrap">
      <header className="hero">
        <h1>
          <TulipIcon /> Voice Garden
        </h1>
        <p>a cozy place to watch your voice bloom 🌱✨</p>
        {active && (
          <div className="latest-banner">
            🌸 <b>#{active.id}</b> &middot; {active.label} &middot;{" "}
            <span style={{ color: "#756384" }}>{active.date}</span>
          </div>
        )}
      </header>

      {load.status === "loading" && (
        <div className="empty">gathering your takes… 🌱</div>
      )}

      {load.status === "error" && (
        <div className="empty empty-error" role="alert">
          couldn't load your recordings 🌧️
          <br />
          {load.message}
          <br />
          <span className="empty-hint">
            your takes are safe — check that the dashboard is running from{" "}
            <code>dashboard-react/</code> and that <code>recordings.json</code>{" "}
            is intact, then refresh.
          </span>
        </div>
      )}

      {unreadable.length > 0 && (
        <div className="data-note" role="status">
          🌧️{" "}
          {unreadable.map((u, i) => (
            <span key={i}>
              {i > 0 && " · "}
              <b>{u.label}</b> couldn't be read ({u.reason})
            </span>
          ))}
          {R.length > 0 && (
            <> — {unreadable.length === 1 ? "it's" : "they're"} left out below</>
          )}
          . Re-running the analyzer on {unreadable.length === 1 ? "it" : "them"}{" "}
          usually fixes this.
        </div>
      )}

      {load.status === "ready" && R.length === 0 && unreadable.length === 0 && (
        <WelcomeCard />
      )}

      {/* recording switcher */}
      {R.length > 1 && (
        <div className="picker">
          <span className="picker-label">viewing:</span>
          {[...R].reverse().map((r) => (
            <button
              key={r.id}
              className={active && r.id === active.id ? "active" : ""}
              aria-current={active && r.id === active.id ? "true" : undefined}
              onClick={() => select(r.id)}
            >
              #{r.id} · {r.label}
              {latest && r.id === latest.id ? " 🌟" : ""}
            </button>
          ))}
        </div>
      )}

      {active && (
        <AnnotationsProvider recording={active} recordings={R}>
          <Region id="region.top" />

          {/* 🎀 This take */}
          <section>
            <h2 className="section-title" ref={takeHeading} tabIndex={-1}>
              <BowIcon /> {isLatest ? "Latest take" : `Take #${active.id}`}
            </h2>
            <p className="take-hint">
              🔍 tap any card with a colored bar to see its full scale, with
              all your takes on it
            </p>
            <div className="stat-grid">
              <StatCard
                title="Pitch (avg)"
                value={active.pitch.mean_hz}
                unit="Hz"
                zones={PITCH_ZONES}
                lo={100}
                hi={260}
                metricKey="pitch"
                onExpand={openModal}
                sub={
                  <Note id="note.pitch">
                    {!pz ? (
                      ""
                    ) : pz.name === "fem" && f2z?.name === "bright" ? (
                      <>
                        you're in the <b>fem</b> zone with bright resonance to
                        match — that pairing is what ears actually go on 💕
                      </>
                    ) : pz.name === "fem" ? (
                      <>
                        you're in the <b>fem</b> zone
                        {pitchGoal && <> — {pitchGoal.from} Hz is the top of the
                        in-between range</>}, and resonance takes it from there 🌱
                      </>
                    ) : (
                      <>
                        you're in the <b>{pz.name}</b> zone
                        {pitchGoal && <> — {pitchGoal.from} Hz is where the
                        in-between range tops out</>}, and pitch &amp; resonance
                        decide together from there 🌱
                      </>
                    )}
                  </Note>
                }
              />
              {/* The typical span (5th–95th percentile), not the extremes:
                  one octave blip or a creaky ending used to stretch this to
                  hundreds of Hz. It's a description of where the voice
                  travelled, not a score, so there's no "wider = better". */}
              <StatCard
                title="Pitch range"
                value={active.pitch.range_hz}
                unit="Hz"
                sub={
                  active.pitch.p5_hz !== undefined ? (
                    <>
                      {fmt(active.pitch.p5_hz)}–{fmt(active.pitch.p95_hz)} Hz ·
                      your typical span, 5th to 95th percentile, so a stray blip
                      or a creaky ending doesn't stretch it
                    </>
                  ) : (
                    <>
                      {fmt(active.pitch.min_hz)}–{fmt(active.pitch.max_hz)} Hz ·
                      lowest to highest (an older analysis — re-run it for the
                      typical span)
                    </>
                  )
                }
              />
              <StatCard
                title="Loudness"
                value={active.intensity.mean_db}
                unit="dB"
                zones={LOUD_ZONES}
                lo={45}
                hi={78}
                metricKey="loudness"
                onExpand={openModal}
                sub={
                  <Note id="note.loudness">
                    louder = more present & confident 📣
                  </Note>
                }
              />
              {/* Hz variability is informational only — no zones, no verdict.
                  Hz SD rises with mean pitch, so it can't tell melody apart from
                  simply speaking higher. See the note in zones.ts. */}
              <StatCard
                title="Pitch variability"
                value={active.pitch.sd_hz}
                unit="Hz"
                sub="the same movement measured in Hz · this one grows as your pitch rises, so it's a number to watch, not a score"
              />
              <StatCard
                title="Clarity (HNR)"
                value={active.voice_quality.hnr_db}
                unit="dB"
                zones={HNR_ZONES}
                lo={0}
                hi={30}
                metricKey="hnr"
                onExpand={openModal}
                sub="higher = clearer, lower = breathier · runs lower on full passages than a held vowel"
              />
              <StatCard
                title="Steadiness (jitter)"
                value={active.voice_quality.jitter_pct}
                unit="%"
                zones={JITTER_ZONES}
                lo={0}
                hi={3}
                metricKey="jitter"
                onExpand={openModal}
                sub={
                  <>
                    lower = steadier · shimmer{" "}
                    {fmt(active.voice_quality.shimmer_pct)}% — tracked, not
                    scored
                  </>
                }
              />
              <StatCard
                title="Weight"
                value={active.weight?.h1a3c_db ?? null}
                unit="dB"
                zones={WEIGHT_ZONES}
                lo={0}
                hi={20}
                metricKey="weight"
                onExpand={openModal}
                sub={
                  <Note id="note.weight">
                    source spectral tilt (corrected H1*–A3*) — the <i>thickness</i>{" "}
                    of the voice itself, separate from pitch &amp; resonance ·{" "}
                    {WEIGHT_DIRECTION}. The measure is still being validated, so
                    it isn't compared with anyone else's voice.
                  </Note>
                }
              />
            </div>
          </section>

          <Region id="region.afterLatest" />

          {/* ✨ Resonance */}
          <section>
            <h2 className="section-title">
              <SparkleIcon /> Resonance{" "}
              <span style={{ fontSize: 12, fontWeight: 400, color: "var(--ink-soft)" }}>
                · the "size" your voice sounds, separate from pitch
              </span>
            </h2>
            <ResonanceCard r={active} onExpand={openModal} references={references} />
          </section>

          <Region id="region.afterResonance" />

          {/* 🎚️ Register & phrasing */}
          <section>
            <h2 className="section-title">
              <ContourIcon /> Register &amp; phrasing{" "}
              <span style={{ fontSize: 12, fontWeight: 400, color: "var(--ink-soft)" }}>
                · where your voice holds vs. falls out of register
              </span>
            </h2>
            <RegisterSection />
          </section>

          <Region id="region.afterRegister" />

          {/* 🔍 Insights for this take */}
          <section>
            <h2 className="section-title">
              <InsightIcon /> Insights for this take{" "}
              <span style={{ fontSize: 12, fontWeight: 400, color: "var(--ink-soft)" }}>
                · custom analysis of what to work on next
              </span>
            </h2>
            <Region
              id="region.insights"
              empty={
                <div className="insight-placeholder">
                  ✍️ no custom insight written for take #{active.id} yet.
                  <br />
                  ask Claude to "analyze this recording" — it'll design one right
                  here.
                </div>
              }
              loading={
                <div className="insight-placeholder">
                  opening take #{active.id}'s insight… 🌱
                </div>
              }
              failed={
                <div className="insight-placeholder">
                  🌧️ this take's insight failed to load — the rest of the page is
                  fine.
                  <br />
                  the browser console says what went wrong in its entry file.
                </div>
              }
            />
          </section>

          <Region id="region.bottom" />
        </AnnotationsProvider>
      )}

      {/* 📈 Trends over time (across all recordings) — hidden until there's
          at least one take; the welcome card covers the empty garden */}
      {R.length > 0 && (
      <section>
        <h2 className="section-title">
          <TrendsIcon /> Trends over time
        </h2>
        <div className="chart-grid">
          {R.length < 2 && (
            <p className="cap" style={{ gridColumn: "1/-1", margin: "0 0 4px" }}>
              add another recording to watch the lines grow 🌱
            </p>
          )}
          <ChartCard
            h="Pitch (avg)"
            unit="Hz"
            activeId={activeId}
            cap={
              pitchGoal
                ? `shaded by zone · ${pitchGoal.name} band ${pitchGoal.from} Hz+`
                : "shaded by zone"
            }
            color="#e07ab0"
            data={mk((r) => r.pitch.mean_hz)}
            bands={PITCH_ZONES}
          />
          <ChartCard
            h="In-register melody"
            unit="st"
            activeId={activeId}
            cap="pitch movement in semitones, over steady in-register speech"
            color="#9b7ad0"
            data={mk((r) => r.register?.in_register_semitones_sd ?? null)}
            bands={MELODY_ZONES}
          />
          <ChartCard
            h="Endings that held"
            unit="%" percent
            activeId={activeId}
            cap="% of phrase endings that stayed up in register"
            color="#5fb89a"
            data={mk((r) => r.register?.phrases_landed_pct ?? null)}
            band={[80, 100]}
            bandColor="#b8ecd8"
          />
          <ChartCard
            h="Resonance (F2)"
            unit="Hz"
            activeId={activeId}
            cap="brightness / vocal-tract size cue"
            color="#d99a4e"
            data={mk((r) => r.formants.f2_hz)}
            bands={F2_ZONES}
          />
          <ChartCard
            h="Weight"
            unit="dB"
            activeId={activeId}
            cap={`spectral tilt · ${WEIGHT_DIRECTION}`}
            color="#8d7ba3" /* neutral lavender-grey: weight carries no pink/blue meaning */
            data={mk((r) => r.weight?.h1a3c_db ?? null)}
            bands={WEIGHT_ZONES}
          />
        </div>
      </section>
      )}

      {/* 📖 All recordings */}
      {R.length > 0 && (
        <section>
          <h2 className="section-title">
            <CardsIcon /> All recordings
          </h2>
          <div className="rec-grid">
            {[...R].reverse().map((r) => (
              <RecordingCard
                key={r.id}
                r={r}
                current={r.id === active?.id}
                onSelect={() => select(r.id, true)}
              />
            ))}
          </div>
        </section>
      )}

      {/* 💡 Cheat sheet */}
      <section>
        <h2 className="section-title">
          <BulbIcon /> What do these mean?
        </h2>
        <CheatSheet references={references} />
      </section>

      <VoiceCareNote />

      <footer>
        made with 🩷 &middot; numbers are a compass, not a judge &middot; your ears
        matter most
      </footer>

      {modal && (
        <MetricModal
          metric={METRICS[modal.key]}
          recordings={R}
          references={references}
          activeId={active?.id ?? null}
          origin={modal.rect}
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}

// The empty garden: one welcoming card with the exact command that plants the
// first take. Shown only when recordings.json loaded fine and holds nothing.
function WelcomeCard() {
  return (
    <section className="welcome">
      <h2>🌸 Welcome to your Voice Garden</h2>
      <p>
        Nothing planted yet — let's grow your first take. Record yourself
        reading something (the Rainbow Passage is a lovely start), then from the
        project folder run:
      </p>
      <pre>
        <code>uv run analyze.py "/path/to/recording.mp3" --label "what you were practicing"</code>
      </pre>
      <p>
        Refresh this page and your take blooms right here, with trends and a
        place for Claude to write you a personal insight. 💕
      </p>
    </section>
  );
}

interface ChartCardProps {
  h: string;
  cap: string;
  color: string;
  data: Point[];
  band?: [number, number];
  bandColor?: string;
  bands?: ChartBand[];
  unit?: string;
  percent?: boolean;
  activeId?: number | null;
}

function ChartCard({
  h,
  cap,
  color,
  data,
  band,
  bandColor,
  bands,
  unit,
  percent,
  activeId,
}: ChartCardProps) {
  return (
    <div className="chart-card">
      <h3>{h}</h3>
      <p className="cap">{cap}</p>
      <LineChart
        points={data}
        color={color}
        band={band}
        bandColor={bandColor}
        bands={bands}
        unit={unit}
        percent={percent}
        activeId={activeId}
      />
    </div>
  );
}
