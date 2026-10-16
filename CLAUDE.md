# Voice Garden 🌷 — project guide for Claude

A cozy, quantitative **voice-feminization training tracker** for Rachel. She records
herself (usually the Rainbow Passage), tells you what she was practicing, and you
analyze it and surface — kindly and specifically — what to work on next.

If she shares a recording or asks how a take went, use the **`analyze-voice` skill**
(`.claude/skills/analyze-voice/SKILL.md`). It's the operating manual for the whole
workflow; this file is the why and the shape.

---

## Initial setup (first run — read this first if the project is fresh)

This may be a **starter copy**: the app, the `analyze-voice` skill, the reusable
annotation lib, the shared reference voices (`reference.json`), and an `_example`
annotation template are all here — but there may be **no recordings yet** (empty
`recordings.json`). The user adds their own; everything else is structure for you
to drive.

**Prerequisites** (install whatever's missing):
- **ffmpeg** — `brew install ffmpeg` (macOS) / `sudo apt-get install ffmpeg` (Debian/Ubuntu) /
  `winget install --id Gyan.FFmpeg` (Windows; open a new terminal afterwards so it's on PATH).
  Required by `analyze.py` to read mp3/m4a; the Record panel's WAVs don't need it.
- **uv** — the Python/dependency manager (https://docs.astral.sh/uv). **Use uv, never pip.**
- **Node ≥ 20 + npm** — for the dashboard (`engines` in `dashboard-react/package.json`).

**Bootstrap** — one command per line, so it works the same in bash, zsh, fish and
Windows PowerShell 5.1 (which has no `&&`):
```sh
uv sync
cd dashboard-react
npm ci
npm run dev
```
`uv sync` installs the Python deps (parselmouth, numpy, plus ruff from the dev group) into
`.venv`; `npm ci` installs exactly what `package-lock.json` pins; `npm run dev` serves the
dashboard at http://localhost:5173.

- **`npm audit`:** advisories in dev-only packages (Vite, esbuild and friends) concern the
  local dev server and build tools. None of them reaches your recordings, which are plain
  files on disk that nothing uploads. **Never run `npm audit fix --force`** — it jumps to
  major versions the dashboard isn't built for. Bump the one package within its range instead.
- **Never start the dev server with `--host`** (or set `server.host`) while personal takes
  exist: that serves `public/audio/`, `public/analysis/` and `recordings.json` to everyone on
  your network. The default (localhost only) is the safe one.

**Add the first recording** (this drives the whole dashboard): use the **Record a take**
panel at the top of the dashboard, or analyse an existing file from the project folder:
```sh
uv run analyze.py "/path/to/recording.mp3" --label "what they were trying"
```
Refresh the dashboard (the Record panel does this for you). Then author that take's insight by following the
`analyze-voice` skill — copy `dashboard-react/src/annotations/entries/_example.tsx`
to `00N.tsx` (matching the new recording's id) and fill in the slots.

**Takes, audio, analysis JSON and annotation entries are local and never committed.**
`recordings.json` (both copies), `public/audio/`, `public/analysis/` and
`src/annotations/entries/<NNN>.tsx` are gitignored (only `_example.tsx` and the `.gitkeep`s
are tracked). The two `recordings.json` starters are still tracked as `[]`, so stage files by
name — never `git add -A` — when committing code changes.

**Ownership note:** this guide and the skill were written for the original user
("Rachel"). Treat "the user" as whoever you're working with now — keep the same
warm, *compass-not-judge* tone and all the conventions below.

If this is your first time running and it is an empty codebase, please use this info provided to give the user an overview of how to operate this codebase. Be supportive and give them a good experience, I trust you Claude <3
~ Rachel

---

## The vibe (this matters as much as the code)

The aesthetic is **"Animal Crossing but girliepop"**: pastel pink/purple, soft rounded
cards, gentle gradients, comforting — *cute but not over-the-top*. The emotional design
goal is just as important as the metrics:

- **Numbers are a compass, not a judge.** Every metric is framed as a hint toward a goal,
  never a verdict. The footer literally says so. Honor that everywhere.
- **Honest but kind.** Don't inflate progress (if "melody" is really register crashes,
  say so) — but always pair a weakness with a concrete, doable next step, and end warm.
- **Woven-in, personal encouragement.** The little notes ("you're in the *fem* zone with
  bright resonance to match — that pairing is what ears actually go on 💕") are a core
  feature, not decoration. Personalize them per recording where it helps (see the
  annotation slots below). Keep them **conditional** where the science is conditional —
  the pitch note only says "reads feminine" when F2 is bright too (see Pitch below).
- Emoji in moderation (🌸🎀🎯💕🌱). Rounded font (`ui-rounded`). Keep it soft.

### Color convention — STRICT
Defined in `dashboard-react/src/zones.ts`:
- `MASC` blue `#bcd3f0` (and `#5e7fb8` strokes) = **masculine / fell-out-of-register ONLY.**
  Never use blue for a generic "bad / needs work." A register crash *is* masculine
  register, so blue is correct there.
- `FEM` pink `#ffb6d5` = good / feminine end. `BUTTER` `#ffe9a8` = neutral / mid.
- `GROW` `#cdc6da` = neutral "room to grow" for **non-gendered** skill gaps (breathy,
  rough, monotone). Use this, not blue, for those.

---

## Iconography & visual motifs

The app has a **custom hand-drawn icon set** (no more raw emoji for structural UI) plus a
**tulip favicon**. When touching the UI or adding icons, stay inside this system.

### The tulip mark 🌷 (favicon + hero)
The mascot is a soft tulip: pink bloom (gradient `#ffc8df→#ff92be`, center petal `#ffb6d5`,
seams `#ff89bb`, a white highlight ellipse) on a green stem/leaves (gradient
`#a6e0bd→#79c797`). The **favicon** wraps it in an iOS-style squircle over the brand gradient
`#ffd6ea→#d3c4ff`. Files live in `dashboard-react/public/`: `favicon.svg` (scalable primary),
`favicon-16/32.png`, `favicon.ico` (multi-res), `apple-touch-icon.png` (180² **full-bleed** —
iOS masks its own corners). Wired in `index.html` with `<link>`s + `theme-color #ffd6ea`. The
same flower (no square, viewBox cropped tight to the bloom) is the hero `<TulipIcon>`.

### The icon set — `src/components/icons.tsx`
Self-contained **inline-SVG React components**, one per section heading, replacing the old
leading emoji. Exports + where they're used (in `App.tsx`):
`TulipIcon` (hero), `BowIcon` (Latest take), `SparkleIcon` (Resonance),
`ContourIcon` (Register & phrasing), `InsightIcon` (Insights), `TrendsIcon` (Trends),
`CardsIcon` (All recordings), `BulbIcon` (What do these mean?).

How to use: `<BowIcon />` — drop it as the first child of a `.section-title` (already
`display:flex; gap:8px; align-items:center`). Props: `{ size?, className?, title? }`. Size
defaults to `1.15em` so it scales with the heading text; pass `title` for an accessible
label (else it's `aria-hidden`). Vertical alignment is baked in (`vertical-align:-0.18em`).

**ContourIcon is semantic, not decorative:** a pink "in-register hill" above a dashed floor
that dips into masc-blue `#9fbce8` (= fell out of register). The blue there is *correct* per
the color convention — a register crash is masculine register. Keep that meaning if you redraw it.

### Motifs / rules for new icons
- **Palette** (pull from here, don't invent): pink `#ffb6d5`, deep-pink `#ff9ec5`/`#ff89bb`,
  lavender `#c9b6ff`/`#b6a2f0`, mint `#7fd0ab`, butter `#ffe08f`/`#ffd27a`. Masc-blue
  `#9fbce8` **only** for "fell out of register," never generic bad/needs-work.
- **Style:** soft, rounded, 2-tone fills; favor **filled shapes over thin lines** so they
  keep visual mass at ~20px; self-contained glyphs (no background square — the favicon is the
  only squircle). Center the visual weight in the box.
- **Per-component:** namespace any gradient/filter `id` (e.g. `tulipPetal`, not `grad`) so
  multiple instances don't collide; size in `em`; mark decorative ones `aria-hidden`.
- **All-or-nothing cohesion:** the section icons are a *set*. Don't mix one custom icon with
  emoji siblings — match the family or leave the whole row as emoji.
- **The woven-in expressive emoji** (💕 🌱 💗 📣 in sentences, the 🌸 banner, 🌟 picker star,
  footer 🩷) are the cozy *voice*, not structural iconography — keep them inline. They can be
  *upgraded* to custom art via the emoji-replacement system below, but only when worth it;
  unmapped emoji render as their normal glyph and that's fine.
- **Always render & eyeball a new icon at ~22px on the card bg `#fffafd` before shipping** —
  use the global **`visual-iteration`** skill (render → look → critique → refine). That loop
  is how the favicon and this whole set were built.

### Inline emoji → custom icons (the RichText system)
A second, separate mechanism from the section-icon set: authors keep typing **literal emoji
in prose**, and a scanner swaps any *registered* emoji for a custom inline SVG. Unregistered
emoji pass through untouched, so the set grows one emoji at a time.

- **`src/components/emojiMap.tsx`** — the registry: `EMOJI_MAP` keyed by the exact emoji char,
  plus one component per emoji. Mapped today: `🎯 → TargetEmoji` (a pastel dartboard). Emoji
  components are `em`-sized with baseline alignment baked in.
- **`src/components/RichText.tsx`** — `<RichText>` recurses through children and rewrites only
  *string* leaves, so it's safe to wrap arbitrary JSX (bold tags, nested elements survive).
- **Where it's active:** baked into the reusable prose components **`InsightCard`** and
  **`Drill`** (so any emoji authored inside an insight/drill auto-converts — authors touch
  nothing), applied by **`<Note>` and `<Region>` themselves** (so every woven note and every
  authored slot converts, default or override), and inline as `<RichText>…</RichText>` in
  `CheatSheet`. The section/hero headings do **not** use this — they use the dedicated
  `icons.tsx` components directly.
- **To use it (author):** just type the emoji inside RichText-covered prose. To cover a new
  prose spot, wrap it with `<RichText>`.
- **To add a new emoji:** design the art with the `visual-iteration` skill (render at ~1em and
  **down to ~13–18px on the real bg** — inline body text is small), add a component to
  `emojiMap.tsx`, and register it in `EMOJI_MAP`. Stay on-palette, `aria-label` the emoji.
- **Legibility lesson (from 🎯):** at text size, **few bold bands + a thick contrast ring beat
  many thin pastel rings** — pale concentric detail washes out. Bold first, then soften toward
  the palette as far as legibility allows.

---

## Recording player (themed waveform)

The recordings play through a **themed wavesurfer.js waveform**, not the native `<audio>`
bar (which clashed with the theme). It's both on-brand and meaningful — you see the take's
voice shape.

- **`src/components/WaveformPlayer.tsx`** — uses `@wavesurfer/react`'s `WavesurferPlayer`
  component. **Generic props** `{ src, duration?, downloadName? }` (not a `Recording`), so it
  plays *any* clip — Rachel's takes **and** the reference voices. Recording cards use
  **`LazyWaveformPlayer`**: the same pill with a placeholder wave, which mounts the real
  player (and starts playing, handing it focus) only on the first tap — so the page doesn't
  fetch and decode every take's audio up front. The **reference-comparison modal** (see its
  section below) mounts `WaveformPlayer` directly when a dot or tick is clicked. An `audioBus` module singleton keeps
  only one player playing at a time across the whole app.
- **Layout:** a soft `lav-bg` rounded pill (CSS under `.player` in `index.css`) holding a
  gradient pink→lavender play/pause button, the waveform, an `m:ss / m:ss` time readout, and
  a pastel volume slider (`accent-color`). Volume hides under 520px.
- **Theming (pink = played, lavender = ahead):** `waveColor #bfa9e6`, `progressColor #ff9ec5`,
  `cursorColor #ff89bb`, rounded bars (`barWidth/barGap/barRadius`), and **`normalize`** so the
  waveform fills the height and looks lively (without it, quiet takes render flat). The **Hover
  plugin** (`wavesurfer.js/dist/plugins/hover.esm.js`) gives a seek preview: a pink line + a
  rose (`#b06a96`) time tooltip. `.player:hover` adds a gentle lift. To re-theme, change those
  props + the `.player` CSS — nothing else.
- **Audio URL:** `${import.meta.env.BASE_URL}${src}` (`src` is like `audio/001.mp3` or
  `reference-audio/vctk_f294.mp3`); duration falls back to `getDuration()`; play state +
  currentTime from wavesurfer events. Deps: `wavesurfer.js` + `@wavesurfer/react` (~+15kb gzip).

---

## Click-to-expand reference modal (where you sit vs. real voices)

Every **bar-backed** stat card (Pitch, Loudness, Clarity/HNR, Steadiness/jitter, Weight)
**and** the two resonance gauges (F2, F3) are **clickable** (Pitch variability in Hz is
informational only — no zones, not clickable — since Hz SD grows with mean pitch). A
click animates a modal up out of the card showing that metric's full scale with **ticks for
many audio samples as reference points** — instantly answering "where do I sit?".

**What's on the scale** (`src/components/MetricModal.tsx`):
- The metric's **colored zone band** (reuses the same `zones.ts` colors as the on-card bar).
- **Rachel's takes** (#1–#N) as prominent rose dots, each at its true value, with a `#label`
  and a thin dashed **guide line** straight down to the bar. When dots cluster, labels are
  bumped up into **stacked lanes** (greedy `assignLanes`) so they never overlap; the guide
  keeps each anchored. Lanes are **capped at 4**: past that, only the active take and the
  latest 5 keep their `#label` (the rest are bare dots, still clickable and labelled for
  screen readers) and dots may share a lane. The currently-selected take is highlighted pink
  + enlarged.
- **Web reference voices** as smaller **pink (female `FEM`) / blue (male `MASC`)** ticks below
  the bar — STRICT color rule — with **shape as a second channel** (round head = woman,
  square head = man; the legend keys match) and the gender in each tick's `aria-label`. Each
  tick has a ≥ 14 px hit area; ticks closer than that stack into rows (up to 4). Shown only on **gendered** metrics whose reference numbers are
  comparable (`showRefs: true` → Pitch, F2, F3: the corpus is measured with the same analyzer
  pass as a take, so they are). If the formant pipeline changes again, rebuild `reference.json`
  before trusting the F2/F3 ticks, or set `showRefs: false` with a `refNote` legend line in the
  meantime; the ResonanceCard counts and the CheatSheet F2 averages follow the same
  switch. Non-gendered ones (Loudness, HNR, jitter) and Weight (own trend only, still being
  validated) show her takes only, with a legend note.
- A **legend** + the lo/hi axis. The scale auto-stretches lo/hi so every value fits.

**Click a dot or a reference tick → a `WaveformPlayer` opens below the scale** and plays that
clip (take audio or the reference's preview). Click the same one again (or its ✕) to close it.
Dismiss the modal via click-outside, Esc, or its ✕. The card is capped at
`calc(100dvh - 40px)` and scrolls inside itself on short screens, and it grows out of the
clicked card (its `transform-origin` is computed relative to its own box on open).

**Plumbing:**
- `src/metrics.ts` — the **metric registry** (`METRICS`, keyed by `MetricKey`). Each
  `MetricDef` carries `{ title, unit, zones, lo, hi, take(r), ref(v), showRefs, blurb }`:
  the value-accessor for a take **and** for a reference voice, plus the scale. Adding a new
  comparable metric = one entry here.
- Bar cards get `metricKey` + `onExpand` props (`StatCard`, `FormantGauge`); **non-bar cards
  like "Pitch range" stay un-clickable** (no `metricKey`). `App.tsx` lifts one shared modal
  keyed by which metric was clicked, and passes the full recordings list + loaded reference
  data down.
- CSS: all `.mm-*` classes in `index.css`. Card→modal open = `mm-pop` (scale/fade up from the
  clicked card's rect, via `--ox/--oy` custom props).

## Reference voices (`public/reference.json` + `public/reference-audio/`)

A corpus of **real adult male & female voices**, each measured with the **same `analyze()`
pipeline** as the user's takes so the numbers are directly comparable — the data backbone for
the reference ticks above.

- **Corpus: VCTK 0.92, American + Canadian speakers** (22 female + 7 male; rebuilt
  2026-10-16), pulled from the `sanchit-gandhi/vctk` HuggingFace **parquet mirror** (speakers
  located from the parquet footer statistics, ~0.5 GB of audio) and filtered on the dataset's
  own `accent` column. Studio-clean read speech in consistent recording conditions → a fair,
  honest male/female reference. p315 is in VCTK's speaker list but absent from the mirror and
  is skipped.
- **`public/reference.json`** — `{ meta, voices }`. `meta` records provenance (analyzer commit
  + sha256, parselmouth/Praat/ffmpeg versions, corpus, date); `voices` is the array of
  `ReferenceVoice` (`src/types.ts`): label, gender, accent, source, audio, plus the same
  `pitch / formants / intensity / voice_quality / weight / register` blocks a take has. Each
  speaker is **one analyzer pass over four concatenated Rainbow Passage sentences** at the
  5500 Hz formant ceiling — the same estimator a take gets, not a per-clip average. Fetched at
  runtime (cache-busted) like `recordings.json`; the loader in `src/data.ts` also accepts the
  old bare-array shape and **degrades gracefully** to `[]` if missing.
- **`public/reference-audio/vctk_<g><id>.mp3`** — the exact audio that was measured: the four
  clips concatenated (≈18–23 s), mono, 96 kbps, played in the modal.
- **`docs/reference-stats.json`** — per-sex mean/SD/min/max and Cohen's d for every metric at
  5500 Hz and 5000 Hz, the zone rule, and paste-ready `zones.ts` tables (`zones_ts["all@5500"]`).
- **What the data says (5500 Hz, n = 22 F / 7 M):** pitch separates the sexes cleanly
  (F 197 vs M 111 Hz, d 3.7). **F3 is the steadier resonance cue** (F 2882 vs M 2682 Hz,
  d 1.7; 15/22 women above the highest man); F2 overlaps more (F 1695 vs M 1578 Hz, d 1.1).
  Weight (corrected H1\*–A3\*, median over vowel nuclei) does **not** separate the sexes
  (F 6.1 vs M 5.7 dB, d 0.2), so the app keeps it as the user's own trend only (see Weight
  below). HNR and jitter separate the sexes strongly here (d 2.5 / −2.9), largely an F0
  effect, so their bands stay skill bands and were not re-cut on the sex midpoint.

**How to (re)generate:** `uv run scripts/build_reference_set.py` — the committed generator
and the one place the reference pipeline lives (edit it there, not ad hoc). `--plan` only
locates clips and prints the download size; `--accents American` builds a US-only corpus.
1. It locates the registered speakers from the parquet footers' `speaker_id` statistics (no
   full-shard scan), downloads only their row groups with `huggingface_hub` + `pyarrow`, and
   caches clips in `.refcache/` (gitignored). A missing speaker or a failed clip is a hard
   error unless `--allow-partial`.
2. For each speaker it concatenates the four clips, runs the project's own `analyze()` once
   over that audio (exactly like a take), writes `public/reference.json` with provenance, and
   encodes the same audio as the preview mp3.
3. It writes `docs/reference-stats.json` and prints the suggested `zones.ts` tables; paste the
   ones you adopt and refresh the comment blocks above them.
- To **add a metric to the comparison**: extend the per-speaker output + the `ReferenceVoice`
  type, regenerate, then add a `METRICS` entry with `ref:`.

---

## What gets measured & why (voice fem)

- **Pitch (F0)** — the biggest *single* cue, but not sufficient on its own. **Never present
  165 Hz as an unconditional verdict.** Hillenbrand & Clark (2009, *Atten Percept Psychophys*
  71(5):1150–1166) resynthesized voices and found that shifting f0 **or** formants alone was
  "usually ineffective" at changing perceived sex, while shifting both worked ~82% of the time
  — "though even together these cues are not entirely effective." Gelfer & Bennett (2013) found
  male identification declines "at 165 Hz and above" and that in the 145–165 Hz band "vowel
  formants appeared to be important to perception of gender." So 165 Hz is a real **inflection
  point** — the top of the gender-ambiguous range, where resonance starts doing the deciding —
  not a threshold a voice crosses. The `note.pitch` slot is therefore **conditioned on F2**: it
  only says "reads feminine" when F2 is in its bright band too. Keep it that way.
  Don't hard-code single reference speakers as anecdotes in the UI or docs: their means can be
  measurement artefacts (p294's 164 Hz, for one, is dragged down by phrase-final creak).
- **Resonance (formants F1/F2/F3)** — the "vocal-tract size / brightness" cue; what makes
  a voice read light independent of pitch. Often the biggest lever after pitch. F1 is
  vowel-driven, not a reliable gender cue alone. **Now vowel-targeted** (in
  `analyze.py`'s `vowel_formants()`): instead of averaging each formant over every voiced
  frame (which folds in consonants/glides/mistracks and barely separated the sexes — old F2
  women 1639 vs men 1566), we take the **median over vowel nuclei** — frames that are voiced
  AND loud (within 10 dB of peak) AND have a plausible vowel F1 (~250–1000 Hz) AND are
  formant-stable. On the rebuilt VCTK corpus (one fixed 5500 Hz ceiling, same pass as a
  take) F3 is the steadier sex cue (women ~2882 vs men ~2682 Hz, d 1.7) and F2 overlaps more
  (~1695 vs ~1578 Hz, d 1.1). See `F2_ZONES`/`F3_ZONES` and `docs/reference-stats.json`.
- **Weight (vocal heaviness via source spectral tilt)** — a candidate cue alongside pitch and
  resonance, **shown as her own trend only** until it's validated. It's the voice **source** (the vocal folds themselves) vs.
  resonance, which is the **filter** (the vocal tract). Measured as **corrected H1\*–A3\***
  (`h1a3c_db`, the Iseli–Alwan 2007 source-tilt measure as used in VoiceSauce, in
  `analyze.py`'s `spectral_weight()`): on **voiced frames only**, the amplitude of the harmonic
  near F0 (H1) minus the harmonic near F3 (A3), each **formant-corrected** (each of F1–F3
  modeled as a pole + bandwidth, its boost subtracted) so it isolates the *source* from the
  *filter*. The formant correction is the whole point — uncorrected H1–A3 just re-measures
  resonance, which was the old alpha ratio's bug. It's a difference of two harmonic amplitudes,
  so it's largely **gain-independent** → comparable across recordings, unlike absolute loudness.
  **Not validated yet:** the Oct 2026 review found creak frames and formant mistracking swing a
  take by several dB, and the direction is unsettled (arithmetically a steeper roll-off gives a
  *larger* value, yet the reference women average *lower*). So the dashboard treats it as
  **her own trend only**: `WEIGHT_ZONES` use neutral GROW/BUTTER with direction names
  (lower / mid / higher — not "lighter/heavier", which would assume the direction), no goal
  band, **no MASC blue or FEM pink**, and `showRefs: false`
  (no male/female ticks). Every UI sentence about direction reads `WEIGHT_DIRECTION` in
  `zones.ts` ("lower or higher is not yet validated as a feminine cue; watch your own
  trend"), so a settled sign changes in
  one place. Don't call weight feminine/masculine in annotations either. `h1a3_db`
  (uncorrected H1–A3) and `tilt_db_khz` (LTAS slope) are stored too as cheap secondaries.
- **Register & phrasing** — the deep layer. Pitch isn't just an average; it's a *contour*.
  - **The floor is take-relative.** By default it sits `REGISTER_FLOOR_ST` (6) semitones under
    the take's own median F0 (`register.floor_mode: "relative"`, plus `floor_st_below_median`
    and `median_hz`); `--register-floor <Hz>` forces a fixed one (`floor_mode: "absolute"`).
    It describes *her own* register instead of grading every voice against one number, so a
    take that sits low throughout can't be flagged by it — by design; absolute pitch is the
    Pitch card's job. Never write a fixed floor number in UI copy; read `floor_hz`.
  - **Every frame has a state** (`frames.state` in the detail JSON): 1 in register, 2 a brief
    **dip** under the floor, 3 a **sustained drop** (the analyzer's "crash": ≥ 200 ms of
    non-creak voicing under the floor), 4 **creak**. Only a sustained drop is falling out of
    register, and it's the only thing painted masc blue; dips are butter, creak is GROW grey.
  - **Creak is not a register fall.** It's reported on its own (`creak_pct`, per-phrase
    `creak_pct`) and some creak at phrase ends is normal. Never frame it as dropping out.
  - `in_register_pct` = share of non-creak voiced time *not* inside a sustained drop. Fluent
    speech reads ~100%, so present it as "time without a sustained drop", never as a grade.
  - **Position bins** are a phrase's **first third / middle / last third**
    (`onset/mid/offset_sub_pct`, the sustained-drop share in each). **"Phrase endings"**
    means `phrases_landed_pct` and nothing else: an ending holds unless most of its last
    120 ms of non-creak voicing is in a sustained drop. The default register tip names
    whichever third dropped most (not always the endings), and when `n_crashes` is 0 it
    just says warmly that nothing dropped out.
  - **Melody in semitones**, two ways: `in_register_semitones_sd` over steady in-register
    speech (creak, drops and octave jumps removed; shown in the Register section — never a
    headline card) and `semitones_sd` over all voiced frames. The gap is mostly creak, drops
    and tracker jumps, not melody. The in-register number is **null** unless ≥ 50 frames and
    ≥ 30% of voicing survive; the UI then says "not enough steady voiced speech to judge
    melody yet" instead of a dash.
- **Loudness, HNR (clarity/breathiness), jitter/shimmer (steadiness)** — supporting cues.

Caveat to remember: the textbook jitter/shimmer/HNR norms are sustained-vowel clinical
screens that no read passage reaches, so the app doesn't use them. `HNR_ZONES` and
`JITTER_ZONES` in `zones.ts` are **reference-grounded on the VCTK read passages** (HNR breathy
<9 · typical 9–14 · clear 14 dB+; jitter steady <1.5 · typical 1.5–2.5 · unsteady 2.5 %+), and
shimmer has no trustworthy passage-level threshold, so it's shown without a zone. They're skill
cues, not gender cues — still read them as *her own trend*, not pass/fail.

---

## Architecture

```
analyze.py  ──(uv run)──►  recordings.json (root, source of truth)
   │                       dashboard-react/public/recordings.json   (mirror)
   │                       dashboard-react/public/analysis/<id>.json (heavy detail)
   │                       dashboard-react/public/audio/<NNN>.mp3    (playback, mono 96k)
   │                       originals/<NNN>.<ext>     (source file, unchanged; not served)
   │                       recordings.meta.json      (next_id: numbers are never reused)
   │                       trash/<timestamp>-…/      (what --delete / --replace moved out)
   ▼
dashboard-react/  (Vite + React + TS)  ── fetches public/* at runtime ──► browser
   ▲
scripts/build_reference_set.py ──(uv run + ffmpeg)──►    public/reference.json
   (measured with the SAME analyze())                    public/reference-audio/vctk_*.mp3
                                                          (for the comparison modal)
```

**Two layers of analysis** (Rachel's design):
1. **Standard** → permanent, data-driven dashboard cards + the permanent
   "🎚️ Register & phrasing" visualizer. Fully automated by `analyze.py`.
2. **Intelligent / per-recording** → you read the detailed data, find the single most
   important, *clockable* thing to work on, and author a custom **annotation** for that
   take (custom viz + explanation + drills). This is the "🔍 Insights for this take"
   section and any personalized woven notes.

### Take lifecycle (`analyze.py` commands)
A take is its entry in `recordings.json` plus up to four files sharing its number:
`public/audio/<NNN>.mp3`, `public/analysis/<id>.json`, `originals/<NNN>.<ext>` and
`src/annotations/entries/<NNN>.tsx`. The analyzer manages all of them together:

| command | what it does |
|---|---|
| `analyze.py <audio> --label "…" [--note …] [--date YYYY-MM-DD]` | add a new take with the next never-used number |
| `analyze.py <audio> --replace N` | re-analyse take N from new (or the same) audio in place: keeps number, label, note, date and annotation entry; old audio/analysis/original go to `trash/` |
| `analyze.py --relabel N --label/--note/--date …` | change those fields only; nothing re-measured |
| `analyze.py --delete N` | entry + its files → `trash/<timestamp>-deleted-takeNNN/`; the number is retired |
| `analyze.py --backfill` | re-measure every take from its original with the current analyzer |
| `analyze.py --export out.zip` / `--import in.zip` | back up / restore everything personal |

- **Numbers are never reused.** A new take gets one past the highest number found in
  `recordings.json`, the audio/analysis/originals/entries folders, `trash/` and `next_id` in
  `recordings.meta.json` (a sidecar; `recordings.json` stays a plain list for the dashboard).
  Entries are matched to takes by number alone, so a reused number would hand a deleted
  take's insight to a new take. `--id N` picks a number explicitly; it refuses a number in
  use, or one whose leftover files would be inherited.
- **Duplicates are refused.** Each take stores `source_sha256`; adding a file that's already
  a take names that take and suggests `--replace N` (or `--allow-duplicate`).
- **Nothing half-done.** Audio is analysed and checked before any write: a take shorter than
  1 s, or with under 0.5 s of voicing (`MIN_TAKE_S`, `MIN_VOICED_FRAMES`), is refused and no
  file is touched; so is an ffmpeg failure (with ffmpeg's own last lines, or install hints
  when it's missing). `--date` must be a real `YYYY-MM-DD`, `--register-floor` 50–400 Hz.
  JSON is written via temp file + rename. `--backfill` stages every detail file, then saves
  `recordings.json`, then renames the details in; a take that fails keeps its old numbers and
  the run exits non-zero.
- **Provenance.** Every take (and its `analysis/<id>.json`) carries `analyzer_version` (semver;
  `ANALYZER_VERSION` in `analyze.py`, 2.0.0 = the Oct 2026 pipeline) and `analyzed_at`; `date`
  stays the take's own day. A take without `analyzer_version` predates it. After an analyzer
  update, `--backfill` brings every take to the same version — then re-read the annotation
  entries, whose prose was written against the old numbers.
- `trash/`, `originals/` and `recordings.meta.json` are gitignored like the rest of the takes.
  Nothing empties `trash/`; that's left to the user. `--import` only accepts zips made by
  `--export` (entries are code the dashboard runs), skips takes already present unchanged and
  refuses if a number here holds a different take.

### The annotation slot system (how per-recording content persists, DRY)
The page layout lives **once**. Personalization happens through named slots woven through
the UI, backed by **one file per recording** at
`dashboard-react/src/annotations/entries/<NNN>.tsx` (zero-padded id) that default-exports
a `RecordingAnnotations` ({ slots }). Auto-discovered via Vite `import.meta.glob`
(`./entries/[0-9]*.tsx`, so the `_example.tsx` template is never bundled) — no registry.
Switching recordings loads that file, so old takes keep their notes. Only the bespoke bits
are authored; nothing about the page is copy-pasted per recording.

Two slot kinds (`src/annotations/AnnotationsProvider.tsx`):
- **`<Note id>`** — override a woven-in note that has a sensible default (children). If the
  active recording supplies the slot, it replaces the default; else the default shows.
- **`<Region id>`** — freeform insertion point; renders nothing unless a recording fills it
  (the insights region passes an `empty` placeholder).

Available slots today: notes `note.pitch`, `note.loudness`, `note.weight`, `note.resonance`,
`note.register`; regions `region.top`, `region.afterLatest`, `region.afterResonance`,
`region.afterRegister`, `region.insights`, `region.bottom`. They're a typed union (`SlotId` =
`NoteId | RegionId` in `src/annotations/types.ts`), so a misspelt slot is a build error. To
add one: extend the union, then place the `<Note>`/`<Region>` in the layout.

How the provider keeps a bad entry from hurting the page:
- **No stale content on a take switch.** The detail JSON and the entry module are tagged
  with the take id they were loaded for, and the context only exposes values whose tag
  matches the active take — mid-switch you see loading states, never the previous take's
  contour or notes. Detail files are cached per session.
- **Every slot runs inside an error boundary.** A slot that throws (when called or while its
  JSX renders) logs `[annotations] slot "<id>" in entries/<NNN>.tsx threw` and shows the
  default note / empty state. A slot returning `null` counts as "not provided".
- **An entry that fails to import** (syntax/runtime error at module level) logs the file and
  the insights region says "this take's insight failed to load"; notes fall back to defaults.
- `Note` and `Region` apply `RichText` themselves.
- Slot ctx is `{ recording, detail, recordings, previous }` — `recordings` sorted by id,
  `previous` the take before. Entries read every number from ctx (or a goal from
  `zones.ts`), never a typed-in literal.

Reusable viz/components live in `src/annotations/lib/` (`InsightCard`, `Drill`,
`PhraseEndingStrip`, …). **Grow this library**: if an insight needs a chart that could be
reused, add it to `lib/` and export it, then import from the entry — don't bury reusable
viz inside one entry. `entries/_example.tsx` is the reference example (the template).

### Recording selector
`App.tsx` tracks an `active` recording (defaults to latest). A pill switcher (shown with
2+ recordings) and each card's **"view ↑" button** in "All recordings" (the number + label,
`aria-current` on the one showing) set it; nothing else on a card selects, and presses inside
a card's player never bubble out. Selecting from a card scrolls back up to the take heading
and focuses it. The choice lives in the URL hash (`#take=N`, via `replaceState`) and is read
on load, so a refresh or a shared link opens the same take; an unknown id falls back to the
latest. The active recording drives the top sections + which annotation file loads.

### Loading, errors and the empty garden
`src/data.ts` is the fetch boundary. `loadRecordings()` runs every entry through
`normalizeRecording()`: missing objects (`pitch`, `formants`, …) get null-valued defaults,
optional fields stay `undefined` (so "older analysis" is still detectable), and an entry
with no usable id or a duplicate id is dropped and listed in a visible "take #N couldn't be
read" note. The app distinguishes **loading / error / ready**, so "no recordings yet" never
shows beside an error; a 404 (or the dev server's HTML fallback) means no takes yet. With
zero takes the Trends and All-recordings sections hide and one welcome card shows the exact
analyzer command. `<App/>` sits inside a top-level `ErrorBoundary` (`components/ErrorBoundary.tsx`,
the same one the slots and the contour use), so a crash shows a gentle card, never a blank page.

---

## File map

- `analyze.py` — the analyzer (parselmouth). Standard metrics + register/phrasing detail,
  and the take lifecycle commands (see "Take lifecycle" above).
- `recordings.json` — source of truth. `recordings.meta.json` — its sidecar (`next_id`).
- `originals/` — each take's source file, unchanged (what `--backfill` re-measures).
  `trash/` — what `--delete`/`--replace` moved out. Both gitignored, like the takes.
- `dashboard-react/`
  - `public/` — served data: `recordings.json`, `analysis/<id>.json`, `audio/`; **plus
    `reference.json` + `reference-audio/*.mp3`** (the comparison-modal corpus); plus the
    favicons (`favicon.svg`, `favicon-16/32.png`, `favicon.ico`, `apple-touch-icon.png`).
  - `index.html` — title + favicon `<link>`s + `theme-color`.
  - `src/types.ts` — data model (`Recording`, `Register`, `RecordingDetail`, `Phrase`,
    `ReferenceVoice`).
  - `src/zones.ts` — colors, reference zones, `fmt`, `zoneOf`.
  - `src/metrics.ts` — **the metric registry** (`METRICS`/`MetricDef`) powering the
    click-to-expand reference modal: per-metric scale + take/reference value-accessors.
  - `src/components/` — cards, charts, `RegisterSection` + `ContourChart` (permanent viz),
    `icons.tsx` (the custom hand-drawn section/hero icon set — see Iconography above), the
    inline-emoji system: `emojiMap.tsx` (emoji→icon registry) + `RichText.tsx` (the scanner);
    `WaveformPlayer.tsx` (themed wavesurfer player — see Recording player above); and
    **`MetricModal.tsx`** (the reference-comparison modal — see its section above).
  - `src/annotations/` — `AnnotationsProvider` (context + `Note`/`Region`), `lib/`
    (reusable), `entries/` (per-recording, authored by you).
- `scripts/build_reference_set.py` — the reference-set generator (see "Reference voices"
  above). Its downloaded VCTK clips are cached in `.refcache/`, which is gitignored.
- `scripts/validate_analyzer.py` — runs `analyze.py` over every reference preview (plus tiled,
  PSOLA-crash and transient variants); `--out` saves the numbers, `--compare` diffs every
  field against a saved run, `--analyzer` validates another copy (e.g. `git show main:analyze.py`).
- `.claude/skills/analyze-voice/` — the workflow skill.

---

## Technical notes for future models

- **Python: use `uv`, never `pip`.** `uv run analyze.py …`, `uv add …`. Lint with
  `uv run ruff check .` and keep it clean (fix auto-fixable; don't over-refactor). ruff is
  pinned in pyproject's dev group and configured under `[tool.ruff]`; `uvx ruff check .`
  reads the same config.
- **Run the dashboard:** from `dashboard-react/`, `npm ci` once, then `npm run dev` →
  http://localhost:5173/ (Node ≥ 20). It fetches `public/*` at runtime (cache-busted), so
  new analyses show on refresh — no rebuild. `npm run build` runs `tsc -b`, then `vite build`.
  The dev server sends no CORS headers and `Cross-Origin-Resource-Policy: same-origin`
  (`vite.config.ts`), so only the dashboard page itself can read your takes; keep it on
  localhost (no `--host`).
- **Screenshotting the live UI** (for `visual-iteration`): **Playwright** is a devDependency.
  `chromium.launch()` uses Playwright's own Chromium (`npx playwright install chromium` once);
  `{ channel: "chrome" }` or `{ channel: "msedge" }` use an installed browser instead, if there
  is one. Run the script **from `dashboard-react/`** so ESM resolves `playwright` (or load it
  with `createRequire` pointed at `dashboard-react/package.json`). Needed for canvas/JS-rendered
  bits like the waveform (static SVG renderers can't capture those); also useful if the browser
  MCP drops mid-session. To capture hover states, `page.mouse.move(...)` then screenshot.
- **Record panel** (`src/components/Recorder.tsx`, `src/wav.ts`, `vite-plugin-analyze.ts`):
  records in the browser with echo cancellation / noise suppression / auto-gain **off**,
  re-encodes to mono 16-bit WAV, and POSTs it to `/api/analyze` — a **dev-server-only**
  endpoint (`apply: "serve"`; it doesn't exist in `vite build`/`preview`, where the panel
  falls back to "save the file, run this command"). The endpoint writes the take to a temp
  file and runs `uv run analyze.py <file> --label=… [--note=…]`, the same CLI as by hand.
  It answers only a loopback `Host` with no `Origin` or this page's own, runs one analysis at
  a time (analyze.py picks the next id from `recordings.json`, so overlapping runs would
  clash), and returns analyze.py's stderr to the panel on failure. `VOICE_GARDEN_UV`
  overrides the `uv` path. To test without a mic, launch Chromium with
  `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=<some.wav>`.
- **Ignore false IDE TypeScript errors** like *"Cannot find name 'React' / UMD global"* or
  *"Property 'glob'/'env' does not exist on ImportMeta"*. The project uses the automatic
  JSX runtime (`tsconfig` `jsx: react-jsx`) and `vite/client` types, so components
  correctly don't import React. **Trust `npm run build`, not inline diagnostics.** The
  IDE's TS server often lags behind file moves; a restart clears it.
- **Data flow:** a plain `analyze.py` run only ever *adds* a take, under a new number; it
  never overwrites one. Re-measuring is explicit: `--replace N <audio>` for one take (old
  files to `trash/`), `--backfill` for all of them from `originals/` (exactly reproducible:
  same analyzer + same original = same numbers). The React app never imports data
  statically — it fetches at runtime. Don't hardcode data.
- **Changing the analyzer:** bump `ANALYZER_VERSION` (MAJOR if a stored number changes
  meaning, MINOR for new fields, PATCH otherwise), then check it with
  `uv run scripts/validate_analyzer.py --out before.json --analyzer <copy of the old analyze.py>`
  followed by `--out after.json --compare before.json`: its field-level comparison should list
  only the fields you meant to change.
- **Register methodology** (in `analyze.py`; constants at the top of the file): F0 contour via
  `Sound.to_pitch`; semitones = `12*log2(hz/100)` (perceptual; SD is reference-independent).
  Octave errors (> 1.8× or < 0.55× the take median) are dropped before pitch extremes and
  melody. Floor = median − 6 st unless `--register-floor` is given. **Creak** = a run below
  0.6× median whose Praat pitch strength is < 0.8 or that has a ~2× candidate (≥ 0.3) —
  a periodic modal drop into a lower register is *not* creak. **Sustained drop** = ≥ 200 ms of
  sub-floor non-creak voicing, bridging consonant gaps up to 150 ms; shorter excursions are
  dips. **Phrases**: frames more than 25 dB under the take's 95th-percentile intensity are
  silence, gaps < 250 ms merge, phrases < 0.5 s or with < 20 voiced frames are dropped.
  Per-frame position is binned into phrase thirds. Pitch min/max/range are the p5/p95 of the
  cleaned track (`p5_hz`/`p95_hz` too) — the typical span, not the extremes — and `sd_hz` is
  the SD of that same cleaned track (Praat's raw SD, octave jumps included, is `sd_raw_hz`).
  The register block also stores its own thresholds (`crash_min_s`, `crash_bridge_s`,
  `onset_window_s`, `ending_window_s`, `floor_st_below_median`) and, when melody is null,
  `melody_null_reason`, so the UI can quote them instead of hardcoding.
- **`base: './'`** is set in `vite.config.ts`. Note: opening the built `dist/index.html`
  from `file://` won't work (browsers block `fetch` of the JSON) — use `npm run dev`/preview.
- When in doubt about a metric or model, prefer the dedicated skill and verify with a build.
