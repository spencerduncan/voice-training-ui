// Typed data model matching recordings.json (source of truth).
//
// Fields marked optional were added by later analyzer versions; entries
// analysed before then simply lack them, so every reader has to cope with
// `undefined` as well as `null`.
export interface Pitch {
  mean_hz: number | null;
  median_hz: number | null;
  // min/max/range are the TYPICAL span: the 5th and 95th percentile of voiced
  // F0 (and their difference), after octave errors and creak are dropped.
  // Entries analysed before p5_hz/p95_hz existed hold Praat's raw extremes.
  min_hz: number | null;
  max_hz: number | null;
  range_hz: number | null;
  sd_hz: number | null; // raw Praat SD in Hz (still includes octave jumps)
  p5_hz?: number | null; // same value as min_hz on current entries
  p95_hz?: number | null; // same value as max_hz on current entries
}

export interface Formants {
  f1_hz: number | null;
  f2_hz: number | null;
  f3_hz: number | null; // all three are null when < 20 vowel frames survive
  formant_ceiling_hz?: number | null; // Praat formant ceiling the take was measured at
  n_vowel_frames?: number | null; // vowel-nucleus frames behind the medians
}

export interface VoiceQuality {
  hnr_db: number | null;
  jitter_pct: number | null;
  shimmer_pct: number | null;
}

export interface Intensity {
  // energy mean / min / max over voiced frames only
  mean_db: number | null;
  min_db: number | null;
  max_db: number | null;
}

// Vocal weight via corrected H1*–A3* (Iseli–Alwan source spectral tilt, the
// voice *source* spectrum measured independent of the vocal-tract filter).
// Median over vowel-nucleus frames, creak and < 100 Hz frames excluded. Larger
// = a steeper source roll-off; which direction reads lighter is NOT validated,
// so the dashboard shows it as the user's own trend only (WEIGHT_DIRECTION).
export interface Weight {
  h1a3c_db: number | null; // corrected H1*–A3* (dB)
  h1a3_db?: number | null; // uncorrected H1–A3 (dB), same frames, cheap secondary
  tilt_db_khz?: number | null; // legacy LTAS slope (dB/kHz)
  h1a3c_sd_db?: number | null; // per-frame SD of h1a3c (dB) — usually large
  n_frames?: number | null; // frames behind the median
}

// Headline register/phrasing numbers folded into each recording entry.
//
// Vocabulary (keep it consistent everywhere):
//   · the FLOOR is take-relative by default: `floor_st_below_median` semitones
//     under this take's own median (floor_mode "relative"), or a fixed Hz value
//     set with --register-floor (floor_mode "absolute").
//   · a SUSTAINED DROP (the analyzer's "crash") is a stretch of ordinary,
//     non-creak voicing that stays under the floor long enough to count; a
//     brief DIP under the floor does not.
//   · CREAK (vocal fry) is reported on its own and is never a register fall.
//   · the three position bins are a phrase's FIRST THIRD / MIDDLE / LAST THIRD.
//     "Phrase endings" means phrases_landed_pct and nothing else.
export interface Register {
  floor_hz: number;
  floor_mode?: "relative" | "absolute"; // missing on entries from before the relative floor
  floor_st_below_median?: number | null; // relative mode: how far under the median the floor sits
  median_hz?: number | null; // the take median the relative floor hangs from
  // share of non-creak voiced time that is NOT inside a sustained drop. Reads
  // ~100% for fluent speech — it's "time without a sustained drop", not a grade.
  in_register_pct: number | null;
  creak_pct?: number | null; // % of voiced time that was creak (normal at phrase ends)
  below_floor_pct?: number | null; // % under the floor at all, brief dips included
  n_crashes?: number | null; // number of sustained drops this take
  semitones_sd: number | null; // pitch movement over ALL voiced frames (st), incl. creak & drops
  // pitch movement over steady in-register speech only (creak, drops and
  // octave jumps removed). NULL when too little steady speech survived to judge.
  in_register_semitones_sd: number | null;
  melody_frames?: number | null; // frames behind in_register_semitones_sd
  onset_sub_pct: number | null; // % of a phrase's FIRST THIRD inside a sustained drop
  mid_sub_pct: number | null; // ...its MIDDLE third
  offset_sub_pct: number | null; // ...its LAST third
  phrases_landed_pct: number | null; // % of PHRASE ENDINGS that held (weren't in a sustained drop)
  n_phrases: number;
}

export interface Recording {
  id: number;
  label: string;
  note: string;
  date: string;
  source_file: string;
  audio: string | null;
  detail?: string; // path (relative to public/) to the heavy analysis JSON
  duration_s: number | null;
  pitch: Pitch;
  formants: Formants;
  voice_quality: VoiceQuality;
  intensity: Intensity;
  weight?: Weight;
  register?: Register;
}

// A web reference voice (real man's / woman's clip) measured with the SAME
// analyze() pipeline as Rachel's takes, loaded at runtime from reference.json.
export interface ReferenceVoice {
  label: string;
  gender: "f" | "m";
  source: string;
  audio?: string; // path (relative to public/) to a short playable preview clip
  pitch: { mean_hz: number | null; sd_hz: number | null };
  formants: { f2_hz: number | null; f3_hz: number | null };
  intensity: { mean_db: number | null };
  voice_quality: { hnr_db: number | null; jitter_pct: number | null };
  weight: { h1a3c_db: number | null };
}

// One phrase (a "sounding" stretch between silences) from the detail file.
// onset/offset/min ignore creak and anchor on the first/last non-creak frame.
export interface Phrase {
  start: number;
  end: number;
  onset_hz: number;
  offset_hz: number;
  min_hz: number;
  started_in_register: boolean; // not in a sustained drop at the start
  ended_in_register: boolean; // this phrase's ENDING held (feeds phrases_landed_pct)
  sub_register_pct: number; // % of the phrase inside a sustained drop
  creak_pct?: number; // % of the phrase that was creak
}

// Per-frame register state in frames.state (see frame_states for the legend).
export const FRAME_STATE = {
  unvoiced: 0,
  inRegister: 1,
  dip: 2, // briefly under the floor
  crash: 3, // a sustained drop — the only state drawn in masc blue
  creak: 4,
} as const;

// The heavy per-recording analysis at public/analysis/<id>.json.
export interface RecordingDetail {
  register_floor_hz: number;
  semitone_ref_hz: number;
  duration_s: number;
  time_step: number;
  frames: { t: number[]; hz: (number | null)[]; state?: number[] };
  frame_states?: Record<string, string>; // e.g. {"0": "unvoiced", "1": "in_register", …}
  phrases: Phrase[];
  summary: Register;
}
