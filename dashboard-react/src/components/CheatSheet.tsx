import { useState } from "react";
import { RichText } from "./RichText";
import type { ReferenceVoice } from "../types";
import {
  PITCH_ZONES,
  SD_ZONES,
  F2_ZONES,
  LOUD_ZONES,
  HNR_ZONES,
  JITTER_ZONES,
  WEIGHT_ZONES,
  goalOf,
  type Zone,
} from "../zones";

// The little "guide" lines under each glossary entry are RENDERED FROM
// `zones.ts` — the single source of truth the stat cards, gauges and trend
// charts also read. Never restate a band as a prose number here: the cheat
// sheet used to quietly disagree with the cards (e.g. it promised "bright
// 1500 Hz+" while F2_ZONES had already moved to 1420+), which meant a take
// could get a pink "bright" pill and then be told by the glossary that it
// wasn't there yet. Change a band in zones.ts and this line follows.
function ZoneGuide({
  zones,
  unit = "",
  highlight,
}: {
  zones: Zone[];
  unit?: string; // literal suffix, incl. its own spacing: " Hz", " dB", "%"
  highlight?: string; // zone name to bold; defaults to the table's declared goal band
}) {
  const goal = highlight ?? goalOf(zones)?.name;
  return (
    <>
      {zones.map((z, i) => {
        const last = i === zones.length - 1;
        // Unit on the outer bands only — repeating it on every band is noise.
        const text = last
          ? `${z.name} ${z.from}${unit}+`
          : i === 0
            ? `${z.name} <${z.to}${unit}`
            : `${z.name} ${z.from}–${z.to}`;
        return (
          <span key={z.name}>
            {i > 0 && " · "}
            {z.name === goal ? <b>{text}</b> : text}
          </span>
        );
      })}
    </>
  );
}

// Means of the shipped reference voices (public/reference.json), so the
// "real voices average ~X" asides can't drift from the corpus either. Returns
// null when reference.json didn't load — then we simply say nothing.
function refMean(
  refs: ReferenceVoice[],
  get: (r: ReferenceVoice) => number | null | undefined,
  gender?: "f" | "m",
  digits = 0,
): number | null {
  const v = refs
    .filter((r) => !gender || r.gender === gender)
    .map(get)
    .filter((n): n is number => n !== null && n !== undefined);
  if (!v.length) return null;
  const k = 10 ** digits;
  return Math.round((v.reduce((a, b) => a + b, 0) / v.length) * k) / k;
}

// Real React collapsible (controlled <details>-style) for the cheat sheet.
export function CheatSheet({ references = [] }: { references?: ReferenceVoice[] }) {
  const [open, setOpen] = useState(false);
  const f2f = refMean(references, (r) => r.formants?.f2_hz, "f");
  const f2m = refMean(references, (r) => r.formants?.f2_hz, "m");
  const hnrAvg = refMean(references, (r) => r.voice_quality?.hnr_db);
  const jitAvg = refMean(references, (r) => r.voice_quality?.jitter_pct, undefined, 1);
  return (
    <details className="cheat" open={open}>
      <summary
        onClick={(e) => {
          e.preventDefault();
          setOpen((o) => !o);
        }}
      >
        tap to open your gentle cheat sheet 🌸
      </summary>
      <div className="gloss">
        <div className="gloss-item">
          <h4>🎵 Pitch (avg F0)</h4>
          <p>
            The average highness of your voice — the single biggest cue people
            hear.
          </p>
          <div className="guide">
            <ZoneGuide zones={PITCH_ZONES} unit=" Hz" />
          </div>
          <div className="tip">
            💗 To raise it: think "light &amp; forward," let your voice float up
            a touch. Don't force it from the throat.
          </div>
        </div>

        <div className="gloss-item">
          <h4>🌊 Pitch range &amp; variability</h4>
          <p>
            How far your pitch travels (range) and how much it dances around
            (variability/SD). Flat = monotone; lively = expressive.
          </p>
          <div className="guide">
            variability: <ZoneGuide zones={SD_ZONES} unit=" Hz" />
          </div>
          <div className="tip">
            💗 Feminine speech tends to be more melodic. Read with feeling — let
            questions rise, add gentle ups &amp; downs.
          </div>
        </div>

        <div className="gloss-item">
          <h4>✨ Resonance (F2 &amp; formants)</h4>
          <p>
            Formants are the brightness of your voice — they signal vocal-tract{" "}
            <i>size</i>. This is what makes a voice read "small &amp; light" vs
            "big," independent of pitch. Now measured as the <b>median over your
            vowel nuclei</b> (loud, steady syllable cores), not every frame — so
            it separates voices more cleanly.
          </p>
          <div className="guide">
            F2: <ZoneGuide zones={F2_ZONES} unit=" Hz" />
            {f2f !== null && f2m !== null
              ? ` (real women avg ~${f2f}, men ~${f2m}; still varies by vowel)`
              : " (still varies a lot by vowel)"}
          </div>
          <div className="tip">
            💗 To brighten: smile slightly, lift the voice toward the front of
            your mouth / "mask," shrink the throat space.
          </div>
        </div>

        <div className="gloss-item">
          <h4>📣 Loudness (intensity)</h4>
          <p>
            How present and projected your voice is. Soft voices can read timid;
            a confident volume carries.
          </p>
          <div className="guide">
            <ZoneGuide zones={LOUD_ZONES} unit=" dB" highlight="strong" />
          </div>
          <div className="tip">
            💗 Support from the breath/belly, not the throat — push more air, not
            more strain. (Note: depends on mic distance too.)
          </div>
        </div>

        <div className="gloss-item">
          <h4>🫧 Clarity (HNR)</h4>
          <p>
            Harmonics-to-noise ratio — how clear vs. breathy/noisy your voice is.
            Higher = clearer.
          </p>
          <div className="guide">
            <ZoneGuide zones={HNR_ZONES} unit=" dB" /> (on a full passage
            {hnrAvg !== null && `; real studio voices avg ~${hnrAvg}`})
          </div>
          <div className="tip">
            💗 A <i>little</i> breathiness can read feminine, but too much loses
            clarity &amp; tires you out. Aim for easy, clear tone.
          </div>
        </div>

        <div className="gloss-item">
          <h4>
            <RichText>🎯 Steadiness (jitter &amp; shimmer)</RichText>
          </h4>
          <p>
            Tiny wobbles in pitch (jitter) and loudness (shimmer)
            cycle-to-cycle. Lower = steadier, more controlled.
          </p>
          <div className="guide">
            jitter: <ZoneGuide zones={JITTER_ZONES} unit="%" />
            {jitAvg !== null && ` (real studio voices avg ~${jitAvg})`}{" "}
            &nbsp;|&nbsp; shimmer: no trustworthy number for read speech, so we
            just show it
          </div>
          <div className="tip">
            💗 Steadiness improves with warm-ups &amp; not straining. If these
            spike, your voice is probably working too hard.
          </div>
        </div>

        <div className="gloss-item">
          <h4>🪶 Weight (source spectral tilt, corrected H1*–A3*)</h4>
          <p>
            The <i>thickness</i> of the voice itself — how heavy &amp; pressed vs.
            light &amp; airy it sounds. Measured as <b>corrected H1*–A3*</b> (the
            Iseli–Alwan source-tilt measure): on voiced frames only, the harmonic
            near your pitch (H1) minus the harmonic near F3 (A3), each{" "}
            <b>formant-corrected</b> so it isolates the <b>source</b> (your vocal
            folds, how they vibrate) from the <b>filter</b> (the shape of your
            throat &amp; mouth). A steeper roll-off — a <i>smaller</i> H1*–A3* —
            reads lighter &amp; more feminine; a flatter, larger value reads
            heavier. Gain-independent, so it's truly <i>comparable take-to-take</i>{" "}
            (unlike raw loudness dB).
          </p>
          <div className="guide">
            <ZoneGuide zones={WEIGHT_ZONES} unit=" dB" /> (smaller = lighter)
          </div>
          <div className="tip">
            💗 To lighten: let the folds come together gently — easy, breath-flowy
            tone, never pressed or squeezed. Lighter is the reliable direction;
            there's no magic number.
          </div>
        </div>

        <p className="gloss-note">
          🌸 Honest note: the jitter/shimmer/HNR numbers you'll find quoted
          almost everywhere come from a <i>held vowel</i> in a clinic, and
          they're screening lines for voice disorders — not targets for anyone's
          speech. The bands above are calibrated instead on{" "}
          <b>a full read passage</b>, from the same{" "}
          {references.length > 0 ? `${references.length} ` : ""}real
          studio-recorded voices behind the resonance zones, so they describe the thing you
          actually do here. Shimmer has no trustworthy passage-level number at
          all, so it's shown without a target. Use all three to spot{" "}
          <b>your own trend</b> over time, not as pass/fail. And remember: every
          metric is a hint, not a verdict. How your voice <i>feels</i> and how
          listeners <i>read</i> it are the real goals. 💗
        </p>
      </div>
    </details>
  );
}
