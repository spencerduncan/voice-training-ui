import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { SpeakerIcon } from "./icons";
import type { Recording, ReferenceVoice } from "../types";
import { METRICS, type MetricDef } from "../metrics";
import { MASC, FEM, fmt } from "../zones";
import { WaveformPlayer } from "./WaveformPlayer";

interface Props {
  metric: MetricDef;
  recordings: Recording[];
  references: ReferenceVoice[];
  activeId: number | null;
  // bounding rect of the card that was clicked, for the open animation origin
  origin: DOMRect | null;
  onClose: () => void;
}

interface Tick {
  v: number;
  pct: number; // true position on the scale (%) — never moves
  label: string;
  kind: "take" | "fem" | "masc";
  active: boolean;
  lane: number; // vertical row for stacked take/ref markers
  showLabel: boolean; // takes only: draw the "#N" next to the dot
  key: string; // unique id for playback selection
  audio?: string; // playable clip path, if any
  playTitle: string; // label shown above the player when this clip plays
  downloadName: string;
}

// what's currently selected for playback in the modal
interface Selected {
  key: string;
  audio: string;
  title: string;
  downloadName: string;
}

const clampPct = (p: number) => Math.min(100, Math.max(0, p));

// Which metrics actually carry male/female reference ticks. Derived from the
// registry (not hand-listed) so the legend can never drift when a metric's
// `showRefs` flips in metrics.ts.
const REF_METRIC_TITLES = Object.values(METRICS)
  .filter((m) => m.showRefs)
  .map((m) => m.title);

// vertical spacing for stacked take markers (px)
const TAKE_ROW = 22; // height of one stack lane
// gap between the LOWEST dot (lane 0) and the bar, so EVERY dot floats above the
// bar with a visible guide line — never sitting flush on it.
const TAKE_GAP = 6;
const DOT_R = 8; // ~dot radius, so the guide reaches the bar top
// At most this many take lanes: past it, dots share lanes (and overlap a
// little) rather than pushing the bar off a phone screen.
const MAX_TAKE_LANES = 4;
// horizontal room (% of the scale) a dot needs with / without its "#N" label
const LABELLED_W = 16;
const BARE_W = 3.5;
// When every label can't fit in MAX_TAKE_LANES, only the active take and the
// most recent few keep their "#N"; the rest are bare dots (still clickable,
// still titled and labelled for screen readers).
const LABEL_RECENT = 5;

// reference ticks: every tick gets at least this wide a hit area, and ticks
// closer than that stack into rows instead of covering each other
const REF_HIT_PX = 14;
const REF_ROW = 20;
const MAX_REF_LANES = 4;

// Greedy lane assignment: sort by position and drop each tick into the lowest
// lane whose previous tick has ended (its dot + label width) by the time this
// one starts. With `cap` set, a tick that fits nowhere goes into the lane that
// frees up earliest, accepting a small overlap. Returns the number of lanes.
function assignLanes(
  ticks: Tick[],
  width: (t: Tick) => number,
  cap = Infinity,
): number {
  const ordered = [...ticks].sort((a, b) => a.pct - b.pct);
  const laneEnd: number[] = []; // where the last tick in each lane ends (%)
  for (const t of ordered) {
    let lane = laneEnd.findIndex((end) => t.pct >= end);
    if (lane === -1) {
      if (laneEnd.length < cap) {
        lane = laneEnd.length;
        laneEnd.push(0);
      } else {
        lane = laneEnd.indexOf(Math.min(...laneEnd));
      }
    }
    laneEnd[lane] = Math.max(laneEnd[lane], t.pct + width(t));
    t.lane = lane;
  }
  return Math.max(1, laneEnd.length);
}

const playKeys = (play: () => void) => (e: React.KeyboardEvent) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    play();
  }
};

export function MetricModal({
  metric,
  recordings,
  references,
  activeId,
  origin,
  onClose,
}: Props) {
  const cardRef = useRef<HTMLDivElement>(null);
  const scaleRef = useRef<HTMLDivElement>(null);
  // which clip (take or reference) is loaded into the in-modal player
  const [selected, setSelected] = useState<Selected | null>(null);
  // scale width in px — turns the 14 px reference hit area into a % gap
  const [scaleW, setScaleW] = useState(560);

  const play = (t: Tick) => {
    if (!t.audio) return;
    setSelected((cur) =>
      cur?.key === t.key
        ? null // click the same one again → close the player
        : {
            key: t.key,
            audio: t.audio!,
            title: t.playTitle,
            downloadName: t.downloadName,
          },
    );
  };

  // onClose can be a fresh closure each render; keep it in a ref so the
  // focus-management effect below can run exactly once (on mount/unmount)
  // without re-stealing focus or re-locking scroll mid-session.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // Grow out of the clicked card. transform-origin is relative to the modal
  // card's own box, so the clicked card's centre (viewport coordinates) has to
  // be shifted by where the modal card sits. offsetLeft/Top ignore the running
  // scale animation (getBoundingClientRect wouldn't); the backdrop is fixed at
  // the viewport origin, so they're viewport coordinates too. Runs before the
  // first paint, so the animation starts from the right point.
  useLayoutEffect(() => {
    const card = cardRef.current;
    if (!card || !origin) return;
    card.style.setProperty("--ox", `${origin.left + origin.width / 2 - card.offsetLeft}px`);
    card.style.setProperty("--oy", `${origin.top + origin.height / 2 - card.offsetTop}px`);
  }, [origin]);

  useLayoutEffect(() => {
    const el = scaleRef.current;
    if (!el) return;
    const measure = () => setScaleW(el.clientWidth || 560);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Focus management + scroll lock, so a keyboard / screen-reader user actually
  // ends up *inside* the dialog instead of stranded behind it:
  //   · move focus into the card on open (its aria-label gets announced)
  //   · trap Tab / Shift+Tab within the card
  //   · restore focus to whatever opened it (the stat card) on close
  //   · lock body scroll while it's up
  // Esc-to-close lives here too, alongside the Tab handling.
  useEffect(() => {
    const card = cardRef.current;
    const prevFocused = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    const prevPadRight = document.body.style.paddingRight;

    // Hiding the page scrollbar widens the layout by its width, which nudged
    // the centred page sideways under the backdrop. Pad the body by the same
    // amount so nothing moves (overlay scrollbars measure 0 and skip this).
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    if (scrollbar > 0) {
      const pad = parseFloat(getComputedStyle(document.body).paddingRight) || 0;
      document.body.style.paddingRight = `${pad + scrollbar}px`;
    }
    document.body.style.overflow = "hidden";
    card?.focus();

    const FOCUSABLE =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab" || !card) return;

      const items = Array.from(
        card.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      const here = document.activeElement as HTMLElement | null;

      if (items.length === 0) {
        // nothing tabbable inside — keep focus on the card itself
        e.preventDefault();
        card.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const inside = here != null && card.contains(here);

      if (e.shiftKey) {
        if (!inside || here === first || here === card) {
          e.preventDefault();
          last.focus();
        }
      } else if (!inside || here === last) {
        e.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      document.body.style.paddingRight = prevPadRight;
      // the trigger card may have been unmounted (e.g. take switched) — only
      // hand focus back if it's still on the page
      if (prevFocused && document.contains(prevFocused)) prevFocused.focus();
    };
  }, []);

  // Scale: stretch the metric's lo/hi so every real value (takes + refs) fits.
  const { lo, hi } = useMemo(() => {
    let lo = metric.lo;
    let hi = metric.hi;
    const vals: number[] = [];
    for (const r of recordings) {
      const v = metric.take(r);
      if (v != null && isFinite(v)) vals.push(v);
    }
    if (metric.showRefs) {
      for (const ref of references) {
        const v = metric.ref(ref);
        if (v != null && isFinite(v)) vals.push(v);
      }
    }
    for (const v of vals) {
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
    const pad = (hi - lo) * 0.06 || 1;
    return { lo: lo - pad, hi: hi + pad };
  }, [metric, recordings, references]);

  const span = hi - lo || 1;
  const toPct = (v: number) => clampPct(((v - lo) / span) * 100);
  const valueText = (v: number) => `${fmt(Math.round(v * 10) / 10)} ${metric.unit}`;

  const takeTicks: Tick[] = recordings
    .map((r) => {
      const v = metric.take(r);
      if (v == null || !isFinite(v)) return null;
      return {
        v,
        pct: toPct(v),
        label: `#${r.id}`,
        kind: "take" as const,
        active: r.id === activeId,
        lane: 0,
        showLabel: true,
        key: `take-${r.id}`,
        audio: r.audio ?? undefined,
        playTitle: `take #${r.id} — ${r.label}`,
        downloadName: `voice-take-${r.id}`,
      };
    })
    .filter(Boolean) as Tick[];

  // Each take = a dot + label pair sitting at the take's TRUE horizontal value.
  // When two are too close, the later one is bumped UP a lane (vertical offset)
  // so dot+label never overlap. A thin dashed guide drops from each dot down to
  // the bar so you can always read off its true position. If that would need
  // more than MAX_TAKE_LANES, only the active take + the latest few keep their
  // labels and the lanes are capped.
  let takeLanes = assignLanes(takeTicks, () => LABELLED_W);
  if (takeLanes > MAX_TAKE_LANES) {
    const recent = new Set(
      [...takeTicks]
        .sort((a, b) => Number(b.label.slice(1)) - Number(a.label.slice(1)))
        .slice(0, LABEL_RECENT)
        .map((t) => t.key),
    );
    for (const t of takeTicks) t.showLabel = t.active || recent.has(t.key);
    takeLanes = assignLanes(
      takeTicks,
      (t) => (t.showLabel ? LABELLED_W : BARE_W),
      MAX_TAKE_LANES,
    );
  }

  const refTicks: Tick[] = metric.showRefs
    ? (references
        .map((ref, idx) => {
          const v = metric.ref(ref);
          if (v == null || !isFinite(v)) return null;
          return {
            v,
            pct: toPct(v),
            label: ref.label,
            kind: ref.gender === "f" ? ("fem" as const) : ("masc" as const),
            active: false,
            lane: 0,
            showLabel: false,
            key: `ref-${idx}`,
            audio: ref.audio,
            playTitle: ref.label,
            downloadName: ref.label.replace(/[^a-z0-9]+/gi, "-").toLowerCase(),
          };
        })
        .filter(Boolean) as Tick[])
    : [];
  // ticks closer than one hit area stack into rows below the bar
  const refGap = ((REF_HIT_PX + 1) / Math.max(scaleW, 1)) * 100;
  const refLanes = refTicks.length
    ? assignLanes(refTicks, () => refGap, MAX_REF_LANES)
    : 0;

  return (
    <div className="mm-backdrop" onClick={onClose}>
      <div
        className="mm-card"
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${metric.title} reference scale`}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <button className="mm-close" onClick={onClose} aria-label="close">
          ✕
        </button>

        <div className="mm-head">
          <h3>{metric.title} reference scale</h3>
          <p>{metric.blurb}</p>
        </div>

        <div className="mm-scale-wrap" ref={scaleRef}>
          {/* her takes ABOVE the band: each is a dot+label pair at its true
              horizontal value, bumped up into lanes when too close, with a thin
              dashed guide dropping straight down to the bar. */}
          <div
            className="mm-takes"
            style={{ height: TAKE_GAP + DOT_R + takeLanes * TAKE_ROW }}
          >
            {takeTicks.map((t, i) => (
              <div
                key={`t${i}`}
                className={`mm-take${t.active ? " is-active" : ""}${
                  t.audio ? " is-clickable" : ""
                }${selected?.key === t.key ? " is-playing" : ""}`}
                style={{
                  left: `${t.pct}%`,
                  bottom: TAKE_GAP + t.lane * TAKE_ROW,
                }}
                onClick={t.audio ? () => play(t) : undefined}
                role={t.audio ? "button" : "img"}
                tabIndex={t.audio ? 0 : undefined}
                onKeyDown={t.audio ? playKeys(() => play(t)) : undefined}
                aria-label={`${t.audio ? "Play " : ""}take ${t.label}, ${valueText(t.v)}${
                  t.active ? " (the take on show)" : ""
                }${selected?.key === t.key ? " (now playing)" : ""}`}
                title={`take ${t.label} · ${valueText(t.v)}${
                  t.audio ? " · 🔊 click to hear it" : ""
                }`}
              >
                {/* dashed guide from this dot straight down to the bar */}
                <span
                  className="mm-guide"
                  style={{ height: TAKE_GAP + t.lane * TAKE_ROW + DOT_R }}
                />
                <span className="mm-take-dot">
                  {t.audio && (
                    <SpeakerIcon className="mm-spk" />
                  )}
                </span>
                {t.showLabel && (
                  <span className="mm-take-label" aria-hidden="true">
                    {t.label}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* colored zone band reusing the metric's zone colors */}
          <div className="mm-band">
            {metric.zones.map((z, i) => {
              const left = clampPct(((z.from - lo) / span) * 100);
              const right = clampPct(((z.to - lo) / span) * 100);
              return (
                <div
                  key={i}
                  className="mm-seg"
                  style={{
                    left: `${left}%`,
                    width: `${right - left}%`,
                    background: z.color,
                  }}
                >
                  <span className="mm-seg-name">{z.name}</span>
                </div>
              );
            })}
          </div>

          {/* reference voices below the band. Colour AND shape say who's who
              (pink round head = a woman, blue square head = a man), each tick
              has a ≥14 px hit area, and ticks that would collide stack into
              rows. Colours follow the strict rule: FEM pink / MASC blue. */}
          {refTicks.length > 0 && (
            <div className="mm-row mm-row-ref" style={{ height: refLanes * REF_ROW + 2 }}>
              {refTicks.map((t, i) => {
                const who = t.kind === "fem" ? "woman" : "man";
                return (
                  <div
                    key={`r${i}`}
                    className={`mm-ref mm-ref-${t.kind}${
                      t.audio ? " is-clickable" : ""
                    }${selected?.key === t.key ? " is-playing" : ""}`}
                    style={{ left: `${t.pct}%`, top: t.lane * REF_ROW }}
                    onClick={t.audio ? () => play(t) : undefined}
                    role={t.audio ? "button" : "img"}
                    tabIndex={t.audio ? 0 : undefined}
                    onKeyDown={t.audio ? playKeys(() => play(t)) : undefined}
                    aria-label={`${t.audio ? "Play " : ""}reference voice ${t.label} (a ${who}), ${valueText(t.v)}${
                      selected?.key === t.key ? " (now playing)" : ""
                    }`}
                    title={`${t.label} · ${who} · ${valueText(t.v)}${
                      t.audio ? " · 🔊 click to hear it" : ""
                    }`}
                  >
                    <span className="mm-ref-stem" />
                    <span className="mm-ref-head" />
                    {t.audio && (
                      <SpeakerIcon className="mm-ref-spk" />
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div className="mm-axis">
            <span>
              {Math.round(lo)} {metric.unit}
            </span>
            <span>
              {Math.round(hi)} {metric.unit}
            </span>
          </div>
        </div>

        {/* in-modal player: appears below the scale when a dot / ref tick with a
            clip is clicked */}
        {selected && (
          <div className="mm-player">
            <div className="mm-player-head">
              <span className="mm-player-title">🎧 {selected.title}</span>
              <button
                className="mm-player-close"
                onClick={() => setSelected(null)}
                aria-label="close player"
              >
                ✕
              </button>
            </div>
            <WaveformPlayer
              key={selected.key}
              src={selected.audio}
              downloadName={selected.downloadName}
              label={selected.title}
            />
          </div>
        )}

        <div className="mm-hear">
          <SpeakerIcon className="mm-hear-icon" />
          <span>
            click any <b>dot</b> or <b>tick</b> to hear that voice
          </span>
        </div>

        <div className="mm-legend">
          <span>
            <i className="mm-key mm-key-take" /> your takes
          </span>
          {metric.showRefs ? (
            <>
              <span>
                <i className="mm-key mm-key-fem" style={{ background: FEM }} />{" "}
                women's voices
              </span>
              <span>
                <i className="mm-key mm-key-masc" style={{ background: MASC }} />{" "}
                men's voices
              </span>
            </>
          ) : (
            <span className="mm-legend-note">
              {metric.refNote
                ? metric.refNote
                : REF_METRIC_TITLES.length > 0
                  ? `reference voices show on the gendered metrics — ${REF_METRIC_TITLES.join(" · ")}`
                  : "just your own takes here"}
            </span>
          )}
        </div>

        {/* The ticks are a sample, not a finish line. Without this the pink/blue
            rows read as "get over there", which is the wrong objective — the
            bands are rough guides drawn between the groups (not at either
            group's average), and real voices land on both sides of them. Count
            comes from refTicks (what actually rendered for THIS metric), not
            references.length, so it can never overstate. */}
        {metric.showRefs && refTicks.length > 0 && (
          <p className="mm-context">
            where {refTicks.length} recorded voices actually landed — a spread,
            not a line to reach. the bands are rough guides drawn between the
            groups, and real voices land on both sides of them.
          </p>
        )}
      </div>
    </div>
  );
}
