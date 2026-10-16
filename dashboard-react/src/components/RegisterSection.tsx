import type { ReactNode } from "react";
import type { Register } from "../types";
import { MASC, FEM, GROW, CONTOUR, fmt } from "../zones";
import { ContourChart } from "./ContourChart";
import { useAnnotations, Note } from "../annotations/AnnotationsProvider";

// Permanent "Register & phrasing" visualizer. Reads the active recording and
// its heavy detail JSON from the annotations context (loaded once there), and
// shows the pitch contour, register-stability stats, and where in a phrase any
// sustained drops out of register happened.
//
// Vocabulary (matches types.ts — keep it in step):
//   · the floor is take-relative unless floor_mode says "absolute"
//   · a SUSTAINED DROP is the only thing painted masc blue; a brief dip under
//     the floor is butter, and creak is GROW grey — creak is not a register fall
//   · the position bins are FIRST THIRD / MIDDLE / LAST THIRD of a phrase;
//     "phrase endings" is reserved for phrases_landed_pct
export function RegisterSection() {
  const { recording, detail } = useAnnotations();
  const reg = recording.register;

  if (!reg)
    return (
      <div className="empty">
        no register data for this take yet 🌱
        <br />
        re-run the analyzer to generate it
      </div>
    );

  const positions = [
    { key: "onset", label: "first third", v: reg.onset_sub_pct },
    { key: "mid", label: "middle", v: reg.mid_sub_pct },
    { key: "offset", label: "last third", v: reg.offset_sub_pct },
  ] as const;

  // These three bars exist to be read against *each other*, so the tallest one
  // sets the scale. The floor stops a nearly-clean take from rendering a
  // full-height bar over a percent or two; a true zero draws no bar at all.
  const peak = Math.max(0, ...positions.map((p) => p.v ?? 0));
  const scaleMax = Math.max(peak, 10);
  const hasStates = !!detail?.frames.state;

  return (
    <div className="register">
      <p className="res-desc">
        Pitch isn't just an average — it's a <b>contour</b> that moves through every phrase.{" "}
        <FloorSentence reg={reg} /> Only a <b>sustained</b> drop under it counts as falling out of
        register — those are the{" "}
        <span style={{ color: CONTOUR.crash, fontWeight: 700 }}>blue stretches</span>. A quick dip
        or a bit of creak doesn't.
      </p>

      <div className="contour-card">
        {detail ? (
          <ContourChart detail={detail} />
        ) : (
          <div className="contour-loading">drawing your contour… 🌱</div>
        )}
        {hasStates && <ContourLegend />}
      </div>

      <div className="stat-grid" style={{ marginTop: 16 }}>
        <div className="stat">
          <div className="label">
            <span>Time without a sustained drop</span>
          </div>
          <div className="value">
            {fmt(reg.in_register_pct)}
            <small> %</small>
          </div>
          <div className="sub">
            of your steady voiced time stayed up in register (creak left out, brief dips
            forgiven) — fluent speech usually sits near the top
            {reg.n_crashes != null && (
              <>
                {" "}
                · {reg.n_crashes} sustained {reg.n_crashes === 1 ? "drop" : "drops"} this take
              </>
            )}
          </div>
        </div>

        <div className="stat">
          <div className="label">
            <span>Endings that held</span>
          </div>
          <div className="value">
            {fmt(reg.phrases_landed_pct)}
            <small> %</small>
          </div>
          <div className="sub">
            of your {reg.n_phrases} phrase endings stayed up in register instead of trailing
            off down low
            {reg.n_phrases < 3 && (
              <> · with only {reg.n_phrases} {reg.n_phrases === 1 ? "phrase" : "phrases"}, this one swings a lot</>
            )}
          </div>
        </div>

        {/* Melody is only judged on steady in-register speech: creak, sustained
            drops and pitch-tracking jumps are taken out first. When too little
            survives, the analyzer returns null and we say so in words — never a
            dash with a zone pill next to it. */}
        <div className="stat">
          <div className="label">
            <span>Melody in register</span>
          </div>
          {reg.in_register_semitones_sd == null ? (
            <div className="value value-note">
              not enough steady voiced speech to judge melody yet
            </div>
          ) : (
            <div className="value">
              {fmt(reg.in_register_semitones_sd)}
              <small> st</small>
            </div>
          )}
          <div className="sub">
            pitch movement across your steady, in-register speech
            {reg.semitones_sd != null && (
              <>
                . Over <i>all</i> voiced time it's {fmt(reg.semitones_sd)} st — that one also
                counts creak, drops and any pitch-tracking jumps, so it usually reads wider.
              </>
            )}
          </div>
        </div>
      </div>

      {/* Creak gets its own neutral line: it's a normal way for phrases to
          end, it isn't masculine register, and it must never read as a fall. */}
      {reg.creak_pct != null && (
        <div className="creak-line" style={{ background: GROW + "40" }}>
          <span className="creak-swatch" style={{ background: GROW }} aria-hidden="true" />
          <span>
            <b>{fmt(reg.creak_pct)}%</b> of your voiced time was creak (a little vocal fry) —
            some creak at phrase ends is normal and isn't a register fall.
          </span>
        </div>
      )}

      {/* phrase-position breakdown */}
      <div className="stat" style={{ marginTop: 16 }}>
        <div className="label">
          <span>Where the sustained drops happen</span>
        </div>
        <p className="sub" style={{ margin: "4px 0 14px" }}>
          % of steady voiced time inside a sustained drop, by where it falls in a phrase. Taller
          bar = more dropping there.
        </p>
        <div className="posbars">
          {positions.map((p) => {
            const v = p.v ?? 0;
            const h = v > 0 ? Math.max(4, Math.min(100, (v / scaleMax) * 100)) : 0;
            return (
              <div className="posbar" key={p.key}>
                <div className="posbar-track">
                  <div className="posbar-fill" style={{ height: `${h}%`, background: MASC }} />
                </div>
                <div className="posbar-val">{fmt(p.v)}%</div>
                <div className="posbar-lbl">{p.label}</div>
              </div>
            );
          })}
        </div>
        <div className="res-summary" style={{ marginTop: 14, background: FEM + "22" }}>
          <Note id="note.register">
            <RegisterTip reg={reg} positions={positions} />
          </Note>
        </div>
      </div>
    </div>
  );
}

// Where the floor came from, in words. Relative floors hang off the take's own
// median; absolute ones were set by hand. Older entries carry neither field.
function FloorSentence({ reg }: { reg: Register }) {
  if (reg.floor_mode === "relative" && reg.floor_st_below_median != null) {
    return (
      <>
        This take's register floor is <b>{fmt(reg.floor_hz)} Hz</b> — {fmt(reg.floor_st_below_median)}{" "}
        semitones under your own median
        {reg.median_hz != null && <> of {fmt(reg.median_hz)} Hz</>}, so it moves with you.
      </>
    );
  }
  if (reg.floor_mode === "absolute") {
    return (
      <>
        This take's register floor was set by hand at <b>{fmt(reg.floor_hz)} Hz</b>.
      </>
    );
  }
  return (
    <>
      This take's register floor is <b>{fmt(reg.floor_hz)} Hz</b>.
    </>
  );
}

function ContourLegend() {
  const items: [string, string, string][] = [
    ["in register", CONTOUR.inRegister, ""],
    ["brief dip", CONTOUR.dip, ""],
    ["sustained drop", CONTOUR.crash, ""],
    ["creak", CONTOUR.creak, "dashed"],
  ];
  return (
    <div className="contour-legend">
      {items.map(([label, color, kind]) => (
        <span key={label}>
          <i
            className={`contour-key${kind ? ` is-${kind}` : ""}`}
            style={{ background: kind ? undefined : color, borderColor: color }}
            aria-hidden="true"
          />
          {label}
        </span>
      ))}
    </div>
  );
}

// The default phrasing tip (note.register). With no sustained drops it's a
// short warm note; otherwise the prescription follows whichever third of the
// phrase dropped most — not always the endings.
function RegisterTip({
  reg,
  positions,
}: {
  reg: Register;
  positions: readonly { key: "onset" | "mid" | "offset"; label: string; v: number | null }[];
}): ReactNode {
  const worst = [...positions].sort((a, b) => (b.v ?? 0) - (a.v ?? 0))[0];
  // older entries have no n_crashes; fall back to "nothing in any third"
  const noDrops = reg.n_crashes != null ? reg.n_crashes === 0 : (worst.v ?? 0) === 0;

  if (noDrops)
    return (
      <>
        💕 Nothing dropped out of register this take — your voice stayed up the whole way
        through. Lovely, steady work.
      </>
    );

  const where = (
    <>
      Most of this take's drops came in the <b>{worst.label}</b> of your phrases ({fmt(worst.v)}%
      of that stretch).
    </>
  );
  if (worst.key === "onset")
    return (
      <>
        🎯 {where} The most clockable goal: <b>start each phrase already up in your voice</b> — take
        an easy breath and begin right on the note you want, instead of climbing into it.
      </>
    );
  if (worst.key === "mid")
    return (
      <>
        🎯 {where} The most clockable goal: <b>keep the middle of long phrases buoyant</b> — breathe
        a little more often so the line doesn't sag where you start running low on air.
      </>
    );
  return (
    <>
      🎯 {where} The most clockable goal: <b>land the last word of each phrase up in register</b> —
      let it lift or stay level rather than trailing down low.
    </>
  );
}
