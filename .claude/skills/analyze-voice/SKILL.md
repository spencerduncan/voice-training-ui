---
name: analyze-voice
description: Analyze one of Rachel's voice-training recordings end to end — run the parselmouth analyzer, intelligently interpret the results, and author per-recording "annotations" (a custom insight + woven-in personalized notes) that surface the most important specific thing to work on. Use whenever Rachel shares a new recording, points at an audio file to analyze, or asks how a take went / what to work on next.
---

# Analyze Rachel's voice (Voice Garden)

**Voice Garden** is a voice-feminization training tracker. Rachel records herself (usually
the Rainbow Passage), tells you what she was practicing, and you analyze it. There are
**two layers** and your job spans both:

1. **Standard analysis** → permanent, data-driven dashboard cards + the permanent
   "🎚️ Register & phrasing" visualizer. Fully automated by `analyze.py` — you just run it.
2. **Intelligent, per-recording annotations** → you read the detailed data, find the single
   most important, specific, *clockable* thing to work on, and author it as custom UI: a big
   insight in the "🔍 Insights for this take" section, plus optional personalized woven notes.

Read `CLAUDE.md` for the full vibe/design. Keep everything **warm, specific, and comforting**
("Animal Crossing girliepop"). Numbers are *a compass, not a judge.* Be honest about
weaknesses, always actionable and kind.

---

## Voice care comes first

If the `--note`, the user's message, or anything they say mentions **strain, pain, a tight
or tickly throat, hoarseness, losing the top of the range, or vocal tiredness**, lead with
rest: tell them kindly to stop for the day, rest and hydrate, and keep the next session
short and easy. **Do not prescribe more effort** for that take (no "push higher", "louder",
or extra drills); the insight can still say what the numbers show, framed for next time.
If hoarseness or a missing top end has lasted **more than 4 weeks**, suggest a
speech-language pathologist (ideally one who works with trans clients) or a laryngologist,
as a check-in, not a verdict. This mirrors the always-visible voice-care note in the
dashboard (`src/components/VoiceCareNote.tsx`).

## Step 1 — Run the standard analyzer

From the repo root (wherever this checkout lives):

```fish
uv run analyze.py "<path to audio>" --label "<what she was practicing>" [--note "..."]
```

- Always pass `--label` (her stated focus). Ask if she didn't say.
- The register floor is **take-relative** by default (6 semitones under her own median). Only
  pass `--register-floor <Hz>` if she asks for a fixed floor; it's then stored as
  `floor_mode: "absolute"`.
- Python is managed with **uv**, never pip. If you edit `analyze.py`, run `uvx ruff check .`
  and keep it clean.
- Writes (additive, idempotent per id): `recordings.json` (+ mirror in
  `dashboard-react/public/`), `public/analysis/<id>.json` (heavy detail), `public/audio/<id>`.
- Note the new **id** (printed as `entry #N`) — it's the annotation filename.

## Step 2 — Read the detailed data

Read `dashboard-react/public/analysis/<id>.json` and the new entry in `recordings.json`.
Shapes are in `dashboard-react/src/types.ts`:
- `Recording.register`: `floor_hz` + `floor_mode` (`"relative"` = `floor_st_below_median`
  under `median_hz`; `"absolute"` = set by hand), `in_register_pct` (time **without a
  sustained drop** — ~100% for fluent speech), `n_crashes` (sustained drops), `creak_pct`,
  `below_floor_pct` (brief dips included), `semitones_sd` (all voiced frames),
  `in_register_semitones_sd` (steady in-register speech only; **can be null**) +
  `melody_frames`, `onset_sub_pct`/`mid_sub_pct`/`offset_sub_pct` (sustained-drop share in a
  phrase's **first third / middle / last third**), `phrases_landed_pct` (the **phrase
  endings** that held), `n_phrases`.
- `RecordingDetail`: `frames {t[], hz[], state[]}` (hz null when unvoiced; state 0 unvoiced,
  1 in register, 2 brief dip, 3 sustained drop, 4 creak — legend in `frame_states`),
  `phrases[]` (`start/end/onset_hz/offset_hz/min_hz/started_in_register/ended_in_register/
  sub_register_pct/creak_pct`; onset/offset skip creak).
- Standard metrics: `pitch` (`min_hz`/`max_hz`/`range_hz` are the **p5/p95 typical span**,
  also as `p5_hz`/`p95_hz`), `formants` (+ `formant_ceiling_hz`, `n_vowel_frames`; null
  under 20 vowel frames), `voice_quality`, `intensity` (voiced frames only), `weight`
  (+ `h1a3c_sd_db`, `n_frames`).
- If there's history, read prior entries to compare trends. Only compare takes analysed with
  the same analyzer version: an entry without `register.floor_mode` predates the relative
  floor, and its register numbers mean something different.

## Step 2b — Sanity-check before you interpret

Run through these before picking a finding; each one rules something out:
- **`creak_pct` high** (roughly > 15%): don't read `pitch.range_hz`, `pitch.sd_hz` or
  `semitones_sd` as melody — creak drags the low end down and inflates them. Use
  `in_register_semitones_sd` (or skip melody), and if creak is worth a word at all, frame it
  as normal phrase-end fry, never as a register fall.
- **`n_phrases` < 3**: `phrases_landed_pct` and the first-third / middle / last-third bins
  swing on a single phrase. Mention them only as a light observation, never as the lead.
- **`in_register_semitones_sd` is null**: there wasn't enough steady speech to judge melody.
  Skip melody for this take — don't fall back to `semitones_sd` or Hz SD.
- **Weight**: her own trend only. Compare with her earlier takes, never with other voices,
  never as a gender cue, and never as the lead finding.
- **F2/F3 vs. reference voices**: `reference.json` is still on the old formant pipeline, so
  don't quote "real women average X" for F2/F3 until it's rebuilt (the dashboard hides those
  ticks via `showRefs` in `metrics.ts`). Pitch references are fine.

## Step 3 — Interpret intelligently (think, don't just report)

Find the **1–3 most important, most actionable** things — beat generic advice like
"add melody." Reason like a phonetician. Check, roughly by leverage:
- **Register & phrase boundaries.** The floor is her own (6 st under her median), so a
  sustained drop means she left *her* register, not that she missed a fixed number. If
  `n_crashes` is 0, nothing dropped out — say so warmly and move on; don't invent a weak
  spot. Otherwise: which third of the phrase held the most sustained drop (first third /
  middle / last third)? Prescribe for *that* third — a slide-in start, a sag mid-phrase, or a
  trailing ending need different fixes. Which phrase endings dropped
  (`ended_in_register === false`)? Brief dips and creak are not drops. A big gap between
  `semitones_sd` and `in_register_semitones_sd` is mostly creak, drops and tracker jumps,
  not extra melody — say so kindly rather than calling it expression.
- **Pitch.** Mean vs the ~165 Hz **inflection point** — the top of the gender-ambiguous range,
  not a threshold a voice crosses. Read it *next to F2*: f0 and formants are heard together
  (Hillenbrand & Clark 2009), so only call a take "reads feminine" when both are bright.
  In-register melody flat or healthy (`MELODY_ZONES` in `zones.ts`)? Skip it if it's null.
- **Resonance.** F2/F3 deeper/neutral/bright (`zones.ts`). Often the biggest lever after pitch.
- **Weight** (`weight.h1a3c_db`). Her own trend only: compare with her earlier takes, never
  with other voices, and don't call it feminine or masculine. The measure is still being
  validated (direction wording lives in `WEIGHT_DIRECTION` in `zones.ts`), so don't make it
  the lead finding.
- **Voice quality.** Excess breathiness (low HNR) or strain (high jitter/shimmer).
- **Her stated `--label` goal** — speak to it directly.

Pick the lead finding. Name the moment, the number, the fix.

## Step 4 — Author the recording's annotations

Create **`dashboard-react/src/annotations/entries/<NNN>.tsx`** (`<NNN>` = zero-padded id;
id 2 → `002.tsx`). Default-export a `RecordingAnnotations` ({ slots }). Auto-discovered via
Vite `import.meta.glob` — no registry edits. **Fill only the slots you want** for this take;
everything else falls back to shared defaults (this is what keeps it DRY).

Each slot is `(ctx) => ReactNode` where `ctx = { recording, detail, recordings, previous }`
(`detail` may be null while loading — guard it; `recordings` is every take sorted by id;
`previous` is the take before this one, or null). **Read every number from `ctx`** (or from
`zones.ts` for goals) — never type a measured value or a goal into the JSX. Returning `null`
means "not provided": the default note / empty state shows instead. A slot that throws is
caught, logged with the entry id, and replaced by the default, so one bad entry can't blank
the page. Slot ids are a typed union (`SlotId` in `src/annotations/types.ts`); a typo is a
build error. Slot kinds:
- **Override slots** (`note.*`) replace a woven-in default note. Available: `note.pitch`,
  `note.loudness`, `note.weight`, `note.resonance`, `note.register`.
- **Insertion slots** (`region.*`) drop in freeform content; empty by default. Available:
  `region.top`, `region.afterLatest`, `region.afterResonance`, `region.afterRegister`,
  `region.insights` (put the main insight here), `region.bottom`.

Need a slot somewhere new? Add a `<Note id>`/`<Region id>` in the layout
(`src/App.tsx`, or inside a component like `ResonanceCard`/`RegisterSection`) and document it.

Reuse the library in **`src/annotations/lib/`** (import from `../lib`): `InsightCard`
(shell: title/subtitle/badges), `Drill` (a "try this" box), `PhraseEndingStrip` (per-phrase
ending dots vs the floor), `StatCompare` (this take vs. `previous` or vs. a goal — pass
`bTitle` and `better: "up" | "down"`; it formats the numbers and spaces the unit for you),
`MelodyArc` (melody across `recordings`). Emoji typed into any slot, `InsightCard` or
`Drill` render through `RichText` automatically. **Grow the library:** if your finding needs a new viz that could
be reused, build it in `lib/`, export from `lib/index.ts`, then use it — don't bury reusable
viz in one entry. `entries/_example.tsx` is the reference example (copy it to start). Helpers (`fmt`, colors, zones)
are in `../../zones`.

### Conventions (non-negotiable)
- **Color rule:** blue `MASC` (`#bcd3f0` / `#5e7fb8`) = masculine / fell-out-of-register ONLY,
  never generic "bad." Pink `FEM` = good, butter = neutral, `GROW` `#cdc6da` = non-gendered
  "room to grow" (breathy/rough/flat). A register crash *is* masculine register → blue is right.
- **Tone:** warm, specific, encouraging; tasteful emoji (🌸🎯💕). End affirming with a clear next
  step. Frame as a finishing move, not a rebuild, when she's close.
- **Honesty:** don't inflate progress. If the melody is a mirage, say so — kindly.

## Step 5 — Verify

```fish
cd dashboard-react     # from the repo root
npm run build          # must pass (tsc -b && vite build)
```
`npm run dev` (http://localhost:5173/) hot-reloads new entries and re-fetches data on refresh.
The recording switcher (pills, or click a card under "All recordings") changes which take is
shown, loading that recording's annotations.

> Ignore false IDE errors like "Cannot find name 'React' / UMD global" or "Property
> 'glob'/'env' does not exist on ImportMeta" — the project uses the automatic JSX runtime and
> `vite/client` types. **Trust `npm run build`, not inline diagnostics.**

## File map
- `analyze.py` — analyzer (uv + ruff). `recordings.json` — source of truth.
- `dashboard-react/public/` — served data (`recordings.json`, `analysis/<id>.json`, `audio/`).
- `dashboard-react/src/types.ts` — data model. `src/zones.ts` — colors/zones/`fmt`.
- `src/components/RegisterSection.tsx`, `ContourChart.tsx` — permanent register viz.
- `src/annotations/AnnotationsProvider.tsx` — context + `Note`/`Region`. `src/annotations/lib/`
  — reusable viz. `src/annotations/entries/<NNN>.tsx` — per-recording annotations you author.
