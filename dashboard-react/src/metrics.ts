// Central registry for the bar-backed stat cards. Each entry knows everything
// the click-to-expand reference modal needs: how to pull this metric's value
// from one of Rachel's takes and from a web reference voice, plus the zone
// colors / scale it shares with the on-card ZoneBar.
import type { Recording, ReferenceVoice } from "./types";
import {
  type Zone,
  PITCH_ZONES,
  LOUD_ZONES,
  HNR_ZONES,
  JITTER_ZONES,
  WEIGHT_ZONES,
  WEIGHT_DIRECTION,
  F2_ZONES,
  F3_ZONES,
  goalOf,
} from "./zones";

// Shown in the modal legend while a metric's reference voices sit out: the
// shipped reference.json was measured before the current analyzer's formant
// and weight changes, so its F2/F3/weight values aren't comparable to new
// takes until it's rebuilt. Flip `showRefs` back once it is.
const REMEASURING = "reference voices are being re-measured with the current analyzer";

const pitchGoal = goalOf(PITCH_ZONES);

export type MetricKey =
  | "pitch"
  | "loudness"
  | "hnr"
  | "jitter"
  | "weight"
  | "f2"
  | "f3";

export interface MetricDef {
  key: MetricKey;
  title: string;
  unit: string;
  zones: Zone[];
  lo: number;
  hi: number;
  // value for one of Rachel's takes
  take: (r: Recording) => number | null | undefined;
  // value for a web reference voice (null if that metric isn't measured)
  ref: (v: ReferenceVoice) => number | null | undefined;
  // whether reference (real men's/women's) ticks are meaningful here.
  // gendered metrics (pitch, F2, F3) → show refs; skill/non-gendered → her
  // only. Weight is her own trend only until the measure is validated, and
  // F2/F3 sit out until reference.json is re-measured (see refNote).
  showRefs: boolean;
  // why the reference ticks are missing, when it's not simply "not gendered"
  refNote?: string;
  // one-line framing shown in the modal header
  blurb: string;
}

export const METRICS: Record<MetricKey, MetricDef> = {
  pitch: {
    key: "pitch",
    title: "Pitch (avg)",
    unit: "Hz",
    zones: PITCH_ZONES,
    lo: 100,
    hi: 260,
    take: (r) => r.pitch.mean_hz,
    ref: (v) => v.pitch.mean_hz,
    showRefs: true,
    blurb: pitchGoal
      ? `where you sit vs. real men's & women's voices · ${pitchGoal.from} Hz is the top of the in-between range, not a finish line`
      : "where you sit vs. real men's & women's voices",
  },
  loudness: {
    key: "loudness",
    title: "Loudness",
    unit: "dB",
    zones: LOUD_ZONES,
    lo: 45,
    hi: 78,
    take: (r) => r.intensity.mean_db,
    ref: (v) => v.intensity.mean_db,
    showRefs: false,
    blurb: "louder = more present · this one isn't gendered, just your takes here",
  },
  hnr: {
    key: "hnr",
    title: "Clarity (HNR)",
    unit: "dB",
    zones: HNR_ZONES,
    lo: 0,
    hi: 30,
    take: (r) => r.voice_quality.hnr_db,
    ref: (v) => v.voice_quality.hnr_db,
    showRefs: false,
    blurb: "higher = clearer, lower = breathier · a skill cue, not a gender cue",
  },
  jitter: {
    key: "jitter",
    title: "Steadiness (jitter)",
    unit: "%",
    zones: JITTER_ZONES,
    lo: 0,
    hi: 3,
    take: (r) => r.voice_quality.jitter_pct,
    ref: (v) => v.voice_quality.jitter_pct,
    showRefs: false,
    blurb: "lower = steadier · a skill cue, not a gender cue",
  },
  weight: {
    key: "weight",
    title: "Weight",
    unit: "dB",
    zones: WEIGHT_ZONES,
    lo: 0,
    hi: 20,
    take: (r) => r.weight?.h1a3c_db ?? null,
    ref: (v) => v.weight.h1a3c_db,
    showRefs: false,
    blurb: `source spectral tilt (corrected H1*–A3*) · ${WEIGHT_DIRECTION}`,
    refNote: "no reference voices here while the measure is being validated",
  },
  f2: {
    key: "f2",
    title: "Resonance (F2)",
    unit: "Hz",
    zones: F2_ZONES,
    lo: 1100,
    hi: 2000,
    take: (r) => r.formants.f2_hz,
    ref: (v) => v.formants?.f2_hz,
    showRefs: false,
    refNote: REMEASURING,
    blurb: "main brightness cue · median over vowel nuclei · higher = a brighter, smaller-sounding space · real voices land on both sides of these bands",
  },
  f3: {
    key: "f3",
    title: "Resonance (F3)",
    unit: "Hz",
    zones: F3_ZONES,
    lo: 2100,
    hi: 3400,
    take: (r) => r.formants.f3_hz,
    ref: (v) => v.formants?.f3_hz,
    showRefs: false,
    refNote: REMEASURING,
    blurb: "supports brightness · higher = brighter / lighter resonance",
  },
};
