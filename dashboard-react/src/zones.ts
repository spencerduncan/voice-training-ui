// ---- reference zones for voice feminization (gentle guidance, not law) ----
//
// CRITICAL COLOR CONVENTION:
//   blue  #bcd3f0 = MASCULINE / deeper gendered end ONLY (never "needs work")
//   pink  #ffb6d5 = good / feminine end
//   butter #ffe9a8 = neutral / mid
//   GROW  #cdc6da = neutral "room to grow" for skill metrics (breathy/rough/flat)
export interface Zone {
  from: number;
  to: number;
  color: string;
  name: string;
  // The band the cheat sheet bolds and the trend captions name as the goal.
  // Declared here on purpose rather than inferred from the pink colour: not
  // every table has a goal (weight is own-trend only), and a goal band need
  // not be the pink one.
  goal?: boolean;
}

export const MASC = "#bcd3f0"; // masculine-gendered end ONLY
export const FEM = "#ffb6d5"; // good / feminine end
export const BUTTER = "#ffe9a8"; // neutral / mid
// "Room to grow" tone for skill metrics — NOT blue (blue is reserved for
// masculine-gendered ends only, never for generic "needs work").
export const GROW = "#cdc6da";

export const PITCH_ZONES: Zone[] = [
  { from: 100, to: 145, color: MASC, name: "masc" },
  { from: 145, to: 165, color: BUTTER, name: "neutral" },
  { from: 165, to: 260, color: FEM, name: "fem", goal: true },
];

// Resonance (formants F2 & F3) — the brightness / vocal-tract-size cue.
//
// MEASURE: vowel-targeted **median** F2/F3 over loud, stable vowel nuclei (not
// the old all-frames mean) — see `vowel_formants()` in analyze.py.
//
// REFERENCE-GROUNDED on **VCTK American speakers** via
// `scripts/build_reference_set.py` (single consistent corpus, measured with the
// SAME method): n = 17 female, 4 male.
//   F2: FEMALE mean 1450 Hz (1280…1646) · MALE mean 1281 Hz (1234…1325)
//   F3: FEMALE mean 2742 Hz (2403…3049) · MALE mean 2523 Hz (2365…2695)
//
// F2 separates the sexes (~169 Hz, female higher); F3 less so (~219 Hz, heavy
// overlap) — real-but-imperfect cues, not hard lines. Bands are set so each
// sex's MEAN lands in its zone (female → FEM, male → MASC) with the overlap as
// the neutral band. Caveats: small male n (4); the user reads the Rainbow
// Passage while refs read other sentences, so median formants stay somewhat
// content-dependent — guidance, not law. Re-ground after adding speakers via
// `build_reference_set.py`.
//
// COLOR RULE (strict): blue MASC = deeper/masculine (LOWER) ONLY; pink FEM =
// brighter/feminine (HIGHER); butter = the overlap between.
export const F2_ZONES: Zone[] = [
  { from: 1100, to: 1340, color: MASC, name: "deeper" },
  { from: 1340, to: 1420, color: BUTTER, name: "neutral" },
  { from: 1420, to: 2200, color: FEM, name: "bright", goal: true },
];

export const F3_ZONES: Zone[] = [
  { from: 2100, to: 2560, color: MASC, name: "deeper" },
  { from: 2560, to: 2700, color: BUTTER, name: "neutral" },
  { from: 2700, to: 3400, color: FEM, name: "bright", goal: true },
];

export const LOUD_ZONES: Zone[] = [
  { from: 45, to: 55, color: "#d7d0e8", name: "soft" },
  { from: 55, to: 65, color: "#cdeadd", name: "comfy" },
  { from: 65, to: 78, color: "#ffd9ea", name: "strong" },
];

// Pitch variability in **Hz** deliberately has NO zone bands. (There used to be
// a SD_ZONES here: flat <20 / natural 20–40 / expressive 40+, top band pink.)
//
// Hz SD is confounded with mean pitch — SD_Hz ≈ mean_F0 · 0.0578 · SD_semitones —
// so the SAME melodic movement (2.8 st, say) measures 17.8 Hz at a 110 Hz mean
// and 40.5 Hz at a 250 Hz mean. It would have graded one voice "flat" and the
// other "expressive" for doing the identical thing. Traunmüller & Eriksson
// (1995) report no significant sex difference in SD once it's expressed in
// semitones, while in Hz the women's value comes out higher in every case they
// list. Our own `public/reference.json` agrees: female mean 43.5 Hz vs male
// 40.9 Hz, and 2 of the 4 male speakers landed in the old pink "expressive"
// band. A number that mostly restates how high a voice sits can't carry an
// expressiveness verdict, so the Hz card is informational only — the verdict
// lives on MELODY_ZONES below, in semitones.

// HNR — clarity vs. breathiness, on a full read passage.
//
// REFERENCE-GROUNDED on **VCTK American speakers** (same corpus + same
// `analyze()` as F2/F3/weight): n = 17 female, 4 male.
//   HNR: FEMALE mean 12.41 dB (9.42…15.68) · MALE mean 9.83 dB (8.96…11.26)
//
// The previous bands ("clear" = 18 dB+) came from the sustained-vowel figure in
// the Praat manual — "a healthy speaker can produce a SUSTAINED [a] or [i] with
// a harmonicity of around 20 dB". That is a *held vowel* in a clinic, not
// connected speech: **0 of the 21 studio-recorded reference voices reach 18 dB**
// (the highest is 15.68), so the old top band was unreachable here and the
// middle band swallowed everyone. Re-cut on the reference set: the low edge (9)
// sits just under the female range and at the bottom of the male range; the top
// edge (14) is where the clearest reference voices land.
//
// NOT a gendered cue (the sexes overlap heavily and it's a skill/technique
// metric), so the low end is GROW, never blue.
export const HNR_ZONES: Zone[] = [
  { from: 0, to: 9, color: GROW, name: "breathy" },
  { from: 9, to: 14, color: BUTTER, name: "typical" },
  { from: 14, to: 35, color: FEM, name: "clear", goal: true },
];

// Jitter — cycle-to-cycle steadiness. LOWER is steadier, so that end is pink.
//
// REFERENCE-GROUNDED on the same **VCTK American** set (n = 17 female, 4 male):
//   jitter: FEMALE mean 2.03 % (1.41…2.95) · MALE mean 2.92 % (2.62…3.33)
//
// The previous bands ("steady" = <1 %, "rough" = 2 %+) were the MDVP clinical
// line — the Praat manual cites "1.040% as a threshold for pathology" — which is
// a sustained-vowel *pathology screen*, not a speech target. Measured on a read
// passage it mislabels clean studio speech: **0 of the 21 reference voices are
// below 1 %, and 13 of 21 would have been called "rough"**. Re-cut so the
// reference range spans the bands rather than piling into the worst one, and
// renamed "rough" → "unsteady" (it's a wobble, not a diagnosis).
//
// NOT a gendered cue — the high end is GROW, never blue.
export const JITTER_ZONES: Zone[] = [
  { from: 0, to: 1.5, color: FEM, name: "steady", goal: true },
  { from: 1.5, to: 2.5, color: BUTTER, name: "typical" },
  { from: 2.5, to: 6, color: GROW, name: "unsteady" },
];

// Vocal weight via **corrected H1*–A3*** (Iseli–Alwan 2007 source spectral tilt,
// dB; voiced-gated) — the *source* (vocal folds) with the vocal-tract filter
// corrected out. Shown as the user's **OWN TREND ONLY**, not a gendered scale:
//
//   · The measure isn't validated yet. The Oct 2026 review found it unstable
//     (creak frames and formant mistracking swing a take by several dB) and the
//     direction unsettled: arithmetically a steeper source roll-off gives a
//     LARGER H1*–A3* (see the analyzer docstring), yet on the VCTK reference set
//     the women average LOWER (~9.1 vs ~12.5 dB, only n = 4 men).
//   · So: neutral GROW/BUTTER colours (never MASC blue or FEM pink), direction
//     names instead of "light"/"heavy" verdicts, no goal band, and no male/
//     female reference ticks in the modal (`showRefs: false` in metrics.ts).
//
// The cut points are only there to give the bar some texture; they are not
// targets. Every UI sentence about direction reads WEIGHT_DIRECTION, so if the
// validation work settles the sign the other way, it changes in one place.
export const WEIGHT_DIRECTION = "lower = lighter and airier, higher = heavier";

export const WEIGHT_ZONES: Zone[] = [
  { from: 0, to: 10, color: GROW, name: "lighter" },
  { from: 10, to: 12.5, color: BUTTER, name: "mid" },
  { from: 12.5, to: 20, color: GROW, name: "heavier" },
];

// In-register melody (semitones SD, register crashes removed). Expressiveness,
// NOT a gendered cue — so no blue. flat = room to grow, lively = good. Rachel's
// lovely take #1 hit ~4 st; her flat takes sit ~1.9–2 st.
export const MELODY_ZONES: Zone[] = [
  { from: 0, to: 2, color: GROW, name: "flat" },
  { from: 2, to: 3.5, color: BUTTER, name: "natural", goal: true },
  { from: 3.5, to: 7, color: FEM, name: "lively" },
];

// Out-of-range values are handled ASYMMETRICALLY on purpose — please don't
// "simplify" this back to a single fallback:
//   BELOW the first band's floor → the FIRST zone. The tables start at 100 Hz
//     (pitch) / 1100 Hz (F2) / 2100 Hz (F3), and anything under those floors is
//     either the deepest end of the scale or a formant mistrack. A plain
//     `.find() || last` sends those to the LAST zone, which is pink FEM in all
//     three tables — i.e. the reading would say "bright" at exactly the moment
//     it's most masc, or most broken. Landing on the first zone is honest.
//   ABOVE the last band's ceiling → the LAST zone. That end genuinely is past
//     the goal, so clamping upward is correct. Keep that behaviour.
export function zoneOf(zones: Zone[], v: number | null | undefined): Zone | null {
  if (v === null || v === undefined) return null;
  if (zones.length && v < zones[0].from) return zones[0];
  return zones.find((z) => v >= z.from && v < z.to) || zones[zones.length - 1];
}

// The declared goal band of a table, if it has one.
export function goalOf(zones: Zone[]): Zone | undefined {
  return zones.find((z) => z.goal);
}

export function fmt(v: number | null | undefined, unit = ""): string {
  return v === null || v === undefined ? "—" : v + unit;
}
