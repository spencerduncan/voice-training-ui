"""Voice training analyzer 🌸

Analyzes a recording with Praat (via parselmouth) and adds it to the dashboard.

Two layers of analysis run on every recording:
  1. Standard summary metrics (pitch, formants, voice quality, intensity) →
     appended to recordings.json for the permanent dashboard cards.
  2. Detailed register & phrasing analysis (frame-level F0 contour, register
     stability, silence-based phrase segmentation, per-phrase boundary
     behavior) → written to dashboard-react/public/analysis/<id>.json for the
     permanent "Register & phrasing" visualizer, plus a few headline numbers
     folded into the recording entry.

Usage:
    uv run analyze.py "/path/to/recording.mp3" --label "trying to be louder"
    uv run analyze.py "clip.wav" --label "pitch + bright resonance" --note "felt strained"
    uv run analyze.py "clip.wav" --label "..." --register-floor 130   # absolute floor

recordings.json (repo root) is the source of truth. The React app in
dashboard-react/ reads everything from its public/ dir at runtime.
"""

from __future__ import annotations

import argparse
import atexit
import json
import math
import shutil
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import parselmouth
from parselmouth.praat import call


def _use_utf8_console() -> None:
    """Let this script's emoji survive a non-UTF-8 console.

    Every status line here is deliberately cozy (🎧 ✅ 🎚️ 🌸), but a default
    Windows console is cp1252, and Python encodes stdout with the console's
    codepage — so the very first `print` died with UnicodeEncodeError before
    any analysis output reached the user:

        UnicodeEncodeError: 'charmap' codec can't encode character '\\U0001f3a7'

    Re-encoding the streams as UTF-8 fixes it without stripping the emoji.
    `errors="replace"` means a console that genuinely can't render a glyph
    shows a placeholder instead of taking the whole run down with it.

    Called at import, not from main(), because scripts/build_reference_set.py
    imports this module and prints emoji of its own.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # not a TextIOWrapper (redirected/wrapped)
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # already detached, or read-only stream
            pass


_use_utf8_console()

# Stamped on every take (`analyzer_version`) and every analysis/<id>.json, so the
# dashboard and the analyze-voice skill can tell which pipeline measured a take.
# Semver: MAJOR when a stored number changes meaning (takes from different majors
# aren't comparable without --backfill), MINOR for new fields, PATCH for fixes
# that leave every number alone. 2.0.0 is the October 2026 pipeline (take-relative
# register floor, creak, fixed formant ceiling, p5/p95 pitch, cleaned pitch SD).
# Takes without the field predate it.
ANALYZER_VERSION = "2.0.0"

ROOT = Path(__file__).resolve().parent
RECORDINGS_JSON = ROOT / "recordings.json"  # source of truth

# The React dashboard (dashboard-react) reads these at runtime from public/.
APP_PUBLIC = ROOT / "dashboard-react" / "public"
PUBLIC_RECORDINGS_JSON = APP_PUBLIC / "recordings.json"
AUDIO_DIR = APP_PUBLIC / "audio"
ANALYSIS_DIR = APP_PUBLIC / "analysis"

# Pitch search range (Hz). Wide enough for a feminizing voice without octave errors.
PITCH_FLOOR = 75.0
PITCH_CEILING = 500.0
# Formant ceiling: one fixed maximum-formant ceiling for every take, 5500 Hz
# (Praat's standard for an adult female / shorter vocal tract). Override with
# --formant-ceiling; the value used is stored as formants.formant_ceiling_hz.
FORMANT_CEILING = 5500.0
# Vowel-nucleus gate for formants (see vowel_nuclei()).
VOWEL_LOUD_DB = 10.0  # within this of the phrase's 95th-percentile intensity
VOWEL_F1_RANGE = (250.0, 1000.0)
VOWEL_F2_STEP_HZ = 150.0  # max |F2 change| vs the previous 10 ms frame
MIN_VOWEL_FRAMES = 20  # fewer survivors -> formants reported as null
# Semitone reference (Hz). Only matters for absolute st values; variation (SD)
# is reference-independent. 100 Hz matches Praat's "semitones re 100 Hz".
SEMITONE_REF = 100.0

# ---- Register & phrasing model (see analyze_register) ----------------------
# Register floor: by default it sits REGISTER_FLOOR_ST semitones below the take's
# own median F0, so it describes the speaker's own register rather than grading
# everyone against one absolute number. --register-floor still forces an absolute
# floor in Hz.
# 6 st (a tritone): none of the 21 VCTK reference voices (17 F / 4 M) has a
# sustained (>= CRASH_MIN_S) non-creak stretch deeper than 3.5 st below its own
# median, so 6 st leaves a 2.5 st margin for livelier readers, while a drop from
# a trained ~180 Hz into a ~120 Hz chest voice (-7 st) still lands below it.
REGISTER_FLOOR_ST = 6.0
# Absolute fallback when a take has too little voicing to have a median.
DEFAULT_REGISTER_FLOOR = 130.0
# Creak: a run of frames below CREAK_F0_RATIO x the take median whose pitch is
# either irregular (median Praat pitch strength < CREAK_MAX_STRENGTH) or period-
# doubled (a candidate near 2x the tracked F0 with strength >= CREAK_MIN_SUBHARM).
# A modal drop into chest voice is periodic with no 2x candidate, so it is NOT
# creak and still counts as a crash. 0.6 x median = 8.8 st down; on the VCTK
# women creak/period-doubling sits ~10-14 st down, modal speech above -5 st.
CREAK_F0_RATIO = 0.6
CREAK_MAX_STRENGTH = 0.8
CREAK_MIN_SUBHARM = 0.3
# Crash: a sustained stretch below the floor. Sub-floor (non-creak) voiced frames
# separated only by unvoiced/creak gaps <= CRASH_BRIDGE_S (consonants) or a blip
# of in-register frames < CRASH_BREAK_FRAMES form one episode; the episode is a
# crash if its sub-floor voicing totals >= CRASH_MIN_S. A fry tail is not.
CRASH_MIN_S = 0.2
CRASH_BRIDGE_S = 0.15
CRASH_BREAK_FRAMES = 3
# A phrase "started"/"ended in register" unless most of its first ONSET_WINDOW_S /
# last ENDING_WINDOW_S of non-creak voicing is part of a crash.
ONSET_WINDOW_S = 0.08
ENDING_WINDOW_S = 0.12
# Phrase segmentation: "silent" = intensity more than PHRASE_SILENCE_DB below the
# take's 95th-percentile frame (a robust speech level; the old code used the
# single loudest frame). Sounding stretches split by < PHRASE_MIN_GAP_S are one
# phrase; phrases shorter than PHRASE_MIN_S or with < PHRASE_MIN_VOICED voiced
# 10 ms frames are dropped before any boundary statistic is taken.
PHRASE_SILENCE_DB = 25.0
PHRASE_MIN_GAP_S = 0.25
PHRASE_MIN_S = 0.5
PHRASE_MIN_VOICED = 20
# In-register melody SD is only reported when enough clean frames survive.
MELODY_MIN_FRAMES = 50
MELODY_MIN_SHARE = 0.3
# Octave-error cleanup for pitch extremes / melody: frames above OCTAVE_UP_RATIO
# or below OCTAVE_DOWN_RATIO x the take median are tracker jumps (or halved,
# creaky periods), not the speaker's pitch.
OCTAVE_UP_RATIO = 1.8
OCTAVE_DOWN_RATIO = 0.55
# Vocal weight (spectral_weight): frames under WEIGHT_MIN_F0 are skipped (harmonics
# too close to pick H1 cleanly); null below MIN_WEIGHT_FRAMES usable frames; very
# long takes use an evenly spaced WEIGHT_MAX_FRAMES of the gated frames.
WEIGHT_MIN_F0 = 100.0
MIN_WEIGHT_FRAMES = 20
WEIGHT_MAX_FRAMES = 600


def clean(value: float | None) -> float | None:
    """Round and convert NaN/inf — and Praat's <= -300 dB "no signal" sentinel —
    to None so it serializes cleanly to JSON."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value <= -300:
        return None
    return round(float(value), 2)


def hz_to_st(hz: float, ref: float = SEMITONE_REF) -> float:
    """Convert a frequency to semitones — the perceptual scale for pitch."""
    return 12.0 * math.log2(hz / ref)


def sd(values: list[float]) -> float | None:
    """Sample standard deviation, or None if too few values."""
    return statistics.stdev(values) if len(values) >= 2 else None


# Temp dirs handed out by to_wav_mono(). The converted WAV has to stay readable
# after the call returns — every caller builds a parselmouth.Sound from the
# returned path, and scripts/build_reference_set.py does it inline
# (`analyze(parselmouth.Sound(str(to_wav_mono(c))))`) — so we can't delete
# eagerly or return a context manager without breaking those call sites.
# Instead we remember each dir and sweep them all when the process exits.
_TEMP_WAV_DIRS: list[str] = []


def _cleanup_temp_wavs() -> None:
    while _TEMP_WAV_DIRS:
        shutil.rmtree(_TEMP_WAV_DIRS.pop(), ignore_errors=True)


atexit.register(_cleanup_temp_wavs)


def to_wav_mono(src: Path) -> Path:
    """Convert any audio file to a temp mono WAV for analysis via ffmpeg.

    The returned path stays valid for the rest of the process; its temp dir is
    removed at exit, so a long run (build_reference_set.py converts ~60 clips)
    no longer leaves a pile of orphaned directories behind.
    """
    tmp_dir = tempfile.mkdtemp(prefix="voice-garden-")
    _TEMP_WAV_DIRS.append(tmp_dir)
    tmp = Path(tmp_dir) / "analysis.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "44100", str(tmp)],
        check=True,
        capture_output=True,
    )
    return tmp


def _intensity(sound: parselmouth.Sound) -> parselmouth.Intensity:
    """The one Intensity parameterisation used everywhere: minimum pitch
    PITCH_FLOOR, automatic time step, mean subtracted.

    Kept in a single place so the object can be built once per run and threaded
    through every consumer (loudness stats, the vowel-nucleus loudness gate,
    phrase segmentation) with no risk of the call sites drifting apart.
    """
    return call(sound, "To Intensity", PITCH_FLOOR, 0, "yes")


def _iseli_correction(f_harmonic: float, f_formant: float, bw_formant: float,
                      fs: float) -> float:
    """Iseli–Alwan (2007) vocal-tract correction for one formant, in dB.

    Models a formant as a single pole pair at frequency `f_formant` with
    bandwidth `bw_formant`. Returns the dB boost that pole adds to the spectrum
    at frequency `f_harmonic` (sampling rate `fs`). Subtracting this from a
    measured harmonic amplitude recovers the *source* amplitude — removing the
    resonance (filter) contribution. Eq. (5)–(6) of Iseli, Shue & Alwan,
    "Age, sex, and vowel dependencies of the relationship between source and
    vocal-tract characteristics" (JASA 2007), as implemented in VoiceSauce.
    """
    # Pole location on the unit circle (z = r·e^{jθ}).
    r = math.exp(-math.pi * bw_formant / fs)
    omega = 2 * math.pi * f_formant / fs
    omega_h = 2 * math.pi * f_harmonic / fs
    # |H(e^{jw})|^2 contribution of this conjugate pole pair, normalised at DC.
    # Correction = 10·log10( numerator / denominator ).
    num = (r**2 - 2 * r * math.cos(omega) + 1) ** 2
    den = (
        (r**2 - 2 * r * math.cos(omega + omega_h) + 1)
        * (r**2 - 2 * r * math.cos(omega - omega_h) + 1)
    )
    if den <= 0 or num <= 0:
        return 0.0
    return 10.0 * math.log10(num / den)


def _harmonic_db(spec_freqs: np.ndarray, spec_db: np.ndarray, target_hz: float,
                 f0: float) -> float | None:
    """Peak-pick the harmonic amplitude (dB) nearest `target_hz` by searching a
    ±F0/2 window of a narrowband magnitude spectrum and taking the local max."""
    if target_hz <= 0 or f0 <= 0:
        return None
    half = max(f0 * 0.5, 20.0)
    mask = (spec_freqs >= target_hz - half) & (spec_freqs <= target_hz + half)
    if not mask.any():
        return None
    vals = spec_db[mask]
    # spec_db is NaN in zero-power bins; np.max would let one such bin void the
    # whole window, so take the max over the finite bins.
    if np.all(np.isnan(vals)):
        return None
    peak = float(np.nanmax(vals))
    if math.isnan(peak) or math.isinf(peak):
        return None
    return peak


def _frame_h1a3(sound: parselmouth.Sound, t: float, f0: float,
                formant: parselmouth.Formant) -> tuple[float, float] | None:
    """(corrected H1*-A3*, uncorrected H1-A3) in dB for one frame, or None."""
    fs = sound.sampling_frequency
    # Narrowband windowed spectrum around this frame (>=3 pitch periods for
    # frequency resolution to separate harmonics).
    win = max(0.025, 3.0 / f0)
    t0, t1 = t - win / 2, t + win / 2
    if t0 < 0 or t1 > sound.xmax:
        return None
    try:
        seg = sound.extract_part(t0, t1, parselmouth.WindowShape.HAMMING, 1.0, False)
        spec = seg.to_spectrum()  # parselmouth obj; read bins as numpy below
    except parselmouth.PraatError:
        return None
    vals = spec.values  # shape (2, n_bins): row 0 real, row 1 imag — one shot
    power = vals[0] ** 2 + vals[1] ** 2
    freqs = np.asarray(spec.xs())
    with np.errstate(divide="ignore"):
        spec_db = 10.0 * np.log10(np.where(power > 0, power, np.nan))

    # Formants + bandwidths at this frame (same Formant object as the formants).
    fb: dict[int, tuple[float, float]] = {}
    for n in (1, 2, 3):
        fn = formant.get_value_at_time(n, t)
        bn = formant.get_bandwidth_at_time(n, t)
        if math.isnan(fn) or math.isnan(bn) or fn <= 0 or bn <= 0:
            return None
        fb[n] = (fn, bn)

    h1 = _harmonic_db(freqs, spec_db, f0, f0)
    k3 = max(1, round(fb[3][0] / f0))  # A3 = harmonic nearest F3
    a3 = _harmonic_db(freqs, spec_db, k3 * f0, f0)
    if h1 is None or a3 is None:
        return None
    # Iseli–Alwan correction: subtract each formant's boost at the harmonic
    # frequency to recover the source amplitude, for H1 and A3 alike.
    h1_corr, a3_corr = h1, a3
    for fn, bn in fb.values():
        h1_corr -= _iseli_correction(f0, fn, bn, fs)
        a3_corr -= _iseli_correction(k3 * f0, fn, bn, fs)
    out = (h1_corr - a3_corr, h1 - a3)
    if not all(math.isfinite(v) for v in out):
        return None
    return out


def spectral_weight(sound: parselmouth.Sound,
                    pitch: parselmouth.Pitch | None = None,
                    formant: parselmouth.Formant | None = None,
                    nuclei: dict[str, np.ndarray] | None = None,
                    intensity: parselmouth.Intensity | None = None) -> dict:
    """Vocal *weight* via **corrected H1*–A3*** — the Iseli–Alwan (2007) source
    spectral-tilt measure (as used in VoiceSauce).

    Per frame, from a windowed narrowband FFT:
      - H1 = amplitude (dB) of the harmonic at F0,
      - A3 = amplitude (dB) of the harmonic nearest F3,
    each **formant-corrected** with the Iseli–Alwan vocal-tract model (F1–F3 as
    poles with their bandwidths; we subtract each formant's boost so we recover
    the *source* amplitude). That correction is the difference from a raw alpha
    ratio, which just re-measures resonance (the filter).

    What the number says: h1a3c_db = H1* minus A3*. **Larger = more of the
    source's energy at the fundamental relative to the F3 region, i.e. a
    steeper source spectral roll-off; smaller = a flatter roll-off** (relatively
    more high-frequency source energy). The sign is unchanged from earlier
    versions.

    What it does NOT say: which direction is "lighter" or more feminine. That is
    **not validated** here. On the 21 VCTK reference voices the 4 men come out
    *higher* (steeper) than the 17 women — the opposite of the usual
    expectation that women's voices roll off more steeply — and four men cannot
    settle a direction either way. Treat it as the speaker's own trend, not a
    gender scale.

    Frames: the vowel nuclei the formants are read from (vowel_nuclei(), same
    Formant object and ceiling), minus creaky / period-halved frames (F0 below
    CREAK_F0_RATIO x the take's median F0) and frames under WEIGHT_MIN_F0, where
    harmonics sit too close together to pick H1 cleanly. Those exclusions
    matter: reference woman p294's old voiced-frame mean was 6.2 dB with her
    creak frames and 12.1 dB without them.
    The headline is the **median** over frames, with their SD and count beside
    it; fewer than MIN_WEIGHT_FRAMES usable frames gives null.

    `pitch`, `formant`, `nuclei` and `intensity` may be handed in by analyze(),
    which already built them; anything missing is built here at the default
    settings.

    Returns:
      - h1a3c_db: median corrected H1*–A3* over the gated frames (the headline).
      - h1a3c_sd_db: SD of the per-frame values (how steady the measure is).
      - n_frames: how many frames went into it.
      - h1a3_db: median UNcorrected H1–A3 over the same frames (secondary).
      - tilt_db_khz: LTAS regression slope (dB/kHz), kept as a legacy secondary.
    """
    if pitch is None:
        pitch = call(sound, "To Pitch", 0.0, PITCH_FLOOR, PITCH_CEILING)
    if formant is None:
        formant = call(sound, "To Formant (burg)", 0.0, 5, FORMANT_CEILING, 0.025, 50)
    if nuclei is None:
        nuclei = vowel_nuclei(sound, pitch, intensity or _intensity(sound), formant)

    f0_all = pitch.selected_array["frequency"]
    median = float(np.median(f0_all[f0_all > 0])) if (f0_all > 0).any() else 0.0
    keep = (nuclei["f0"] >= CREAK_F0_RATIO * median) & (nuclei["f0"] >= WEIGHT_MIN_F0)
    frames = list(zip(nuclei["t"][keep].tolist(), nuclei["f0"][keep].tolist()))
    # Cost bound for very long takes: an evenly spaced subset of the gated
    # frames (after gating, so it can't change which kind of frame is used).
    if len(frames) > WEIGHT_MAX_FRAMES:
        step = len(frames) / WEIGHT_MAX_FRAMES
        frames = [frames[int(i * step)] for i in range(WEIGHT_MAX_FRAMES)]

    measured = [m for t, f0 in frames if (m := _frame_h1a3(sound, t, f0, formant)) is not None]
    h1a3c_vals = [m[0] for m in measured]
    h1a3_vals = [m[1] for m in measured]
    ok = len(measured) >= MIN_WEIGHT_FRAMES

    # Legacy LTAS tilt, kept as a cheap secondary.
    tilt = None
    try:
        ltas = call(sound, "To Ltas", 100.0)
        freqs_l: list[float] = []
        vals_l: list[float] = []
        for f in range(100, 5001, 100):
            v = call(ltas, "Get value at frequency", float(f), "Linear")
            if not (math.isnan(v) or math.isinf(v)):
                freqs_l.append(float(f))
                vals_l.append(v)
        if len(freqs_l) >= 4:
            tilt = float(np.polyfit(np.array(freqs_l), np.array(vals_l), 1)[0] * 1000.0)
    except parselmouth.PraatError:
        tilt = None

    return {
        "h1a3c_db": clean(float(np.median(h1a3c_vals))) if ok else None,
        "h1a3c_sd_db": clean(float(np.std(h1a3c_vals, ddof=1))) if ok else None,
        "n_frames": len(measured),
        "h1a3_db": clean(float(np.median(h1a3_vals))) if ok else None,
        "tilt_db_khz": clean(tilt),
    }


def vowel_nuclei(sound: parselmouth.Sound, pitch: parselmouth.Pitch,
                 intensity: parselmouth.Intensity,
                 formant: parselmouth.Formant) -> dict[str, np.ndarray]:
    """The vowel-nucleus frames of a take: the frames formants (and the vocal-
    weight measure) are read from. Returns parallel arrays t / f0 / f1 / f2 / f3.

    Every frame of the pitch track's 10 ms grid is tested — no subsampling, so
    the result can't depend on how long the take is. A frame is kept when it is:
      1. **Voiced** — F0 present.
      2. **Loud for its phrase** — within VOWEL_LOUD_DB of the 95th-percentile
         intensity of the voiced frames in the same phrase (segment_phrases()).
         Vowel nuclei are the loud cores of syllables. Judging each phrase
         against its own level (not one take-wide peak) keeps a quieter last
         sentence in play, and means one transient can't empty the take — the
         same footing as the reference voices, which are measured clip by clip.
      3. **Plausible vowel F1** — VOWEL_F1_RANGE; rejects nasals/approximants
         and obvious mistracks.
      4. **Stable** — |F2 - F2 of the previous 10 ms frame| <= VOWEL_F2_STEP_HZ,
         dropping transitions/glides where the tracker is sliding. The
         reference is always the immediately preceding frame, whether or not
         that frame was itself kept, so one wild excursion can't cascade.
    """
    f0 = pitch.selected_array["frequency"]
    ts = np.asarray(pitch.xs())
    voiced = f0 > 0
    fmt = np.array([[formant.get_value_at_time(n, t) for t in ts] for n in (1, 2, 3)])
    with np.errstate(invalid="ignore"):
        plausible = (np.isfinite(fmt).all(axis=0) & (fmt > 0).all(axis=0)
                     & (fmt[0] >= VOWEL_F1_RANGE[0]) & (fmt[0] <= VOWEL_F1_RANGE[1]))
        stable = np.abs(np.diff(fmt[1], prepend=np.nan)) <= VOWEL_F2_STEP_HZ
    level = np.interp(ts, intensity.xs(), intensity.values[0])
    spans = segment_phrases(sound, intensity, ts[voiced]) or [(0.0, sound.xmax)]
    loud = np.zeros_like(voiced)
    for t0, t1 in spans:
        inside = voiced & (ts >= t0) & (ts <= t1)
        if inside.any():
            ref = float(np.percentile(level[inside], 95))
            loud |= inside & (level >= ref - VOWEL_LOUD_DB)
    keep = voiced & loud & plausible & stable
    return {"t": ts[keep], "f0": f0[keep],
            "f1": fmt[0][keep], "f2": fmt[1][keep], "f3": fmt[2][keep]}


def vowel_formants(sound: parselmouth.Sound, pitch: parselmouth.Pitch,
                   intensity: parselmouth.Intensity | None = None,
                   formant: parselmouth.Formant | None = None,
                   ceiling: float = FORMANT_CEILING,
                   nuclei: dict[str, np.ndarray] | None = None) -> dict:
    """Vowel-nucleus-targeted F1/F2/F3 (median over vowel cores), in Hz.

    The old method averaged each formant over EVERY voiced frame, which folds in
    consonant transitions, glides, schwas and tracker mistracks — that compresses
    the signal so it barely separates the sexes (on VCTK: women's F2 mean 1639 vs
    men's 1566, only ~73 Hz apart). Instead we take the MEDIAN over the frames
    vowel_nuclei() keeps, and report null rather than a number when fewer than
    MIN_VOWEL_FRAMES survive. `n_vowel_frames` says how many it was.

    Ceiling: one fixed maximum-formant ceiling for every take (`ceiling`,
    default FORMANT_CEILING = 5500 Hz, --formant-ceiling), stored as
    `formant_ceiling_hz`. This replaces a two-ceiling (5500/5000) "tighter F2
    spread wins" selector: a lower ceiling mechanically compresses the F2
    estimates, so their spread shrinks with them and 5000 won structurally
    (19 of 21 reference voices), and which ceiling won could flip F2 by
    ~230 Hz on identical audio. A fixed ceiling makes every take measured the
    same way, which is what a trend needs.

    `intensity` and `formant` may be handed in by a caller that already built
    them (analyze() does); `formant` must have been built at `ceiling`.
    `nuclei` likewise, if the caller already ran vowel_nuclei().
    """
    if nuclei is None:
        if intensity is None:
            intensity = _intensity(sound)
        if formant is None:
            formant = call(sound, "To Formant (burg)", 0.0, 5, ceiling, 0.025, 50)
        nuclei = vowel_nuclei(sound, pitch, intensity, formant)
    n = len(nuclei["t"])
    ok = n >= MIN_VOWEL_FRAMES
    return {
        "f1_hz": clean(float(np.median(nuclei["f1"]))) if ok else None,
        "f2_hz": clean(float(np.median(nuclei["f2"]))) if ok else None,
        "f3_hz": clean(float(np.median(nuclei["f3"]))) if ok else None,
        "formant_ceiling_hz": ceiling,
        "n_vowel_frames": n,
    }


def analyze(sound: parselmouth.Sound,
            intensity: parselmouth.Intensity | None = None,
            formant_ceiling: float = FORMANT_CEILING) -> dict:
    """Standard summary metrics for the permanent dashboard cards.

    `intensity` may be supplied by a caller that also runs analyze_register()
    on the same Sound (main() and backfill() do), so the two analyses share one
    Intensity object; otherwise it's built here. `formant_ceiling` is the one
    maximum-formant ceiling (Hz) used for formants and vocal weight.
    """
    duration = sound.get_total_duration()

    # ---- Shared Praat objects ---------------------------------------------
    # Built ONCE and threaded into vowel_formants()/spectral_weight(), which
    # used to each construct their own with exactly these parameters. Same
    # commands, same arguments, same values — just a third of the work.
    pitch = call(sound, "To Pitch", 0.0, PITCH_FLOOR, PITCH_CEILING)
    formant = call(sound, "To Formant (burg)", 0.0, 5, formant_ceiling, 0.025, 50)
    if intensity is None:
        intensity = _intensity(sound)
    f0_track = pitch.selected_array["frequency"]
    voiced = f0_track > 0
    voiced_f0 = f0_track[voiced]

    # ---- Pitch (F0) -------------------------------------------------------
    mean_f0 = call(pitch, "Get mean", 0, 0, "Hertz")
    sd_f0 = call(pitch, "Get standard deviation", 0, 0, "Hertz")
    median_f0 = call(pitch, "Get quantile", 0, 0, 0.5, "Hertz")
    # Extremes: the 5th/95th percentiles of the voiced track once octave errors
    # (frames above OCTAVE_UP_RATIO or below OCTAVE_DOWN_RATIO x the median)
    # and creak runs (creak_frames(), the register model's rule) are dropped —
    # not the raw min/max, which one tracker octave jump or one creaky frame
    # decides. min_hz/max_hz/range_hz carry these same values.
    p5 = p95 = None
    if len(voiced_f0):
        med = float(np.median(voiced_f0))
        kept = (voiced & ~creak_frames(pitch, med)
                & (f0_track <= OCTAVE_UP_RATIO * med) & (f0_track >= OCTAVE_DOWN_RATIO * med))
        if kept.any():
            p5, p95 = (float(v) for v in np.percentile(f0_track[kept], [5, 95]))

    # ---- Formants (resonance) + weight: both read the same vowel nuclei ----
    nuclei = vowel_nuclei(sound, pitch, intensity, formant)
    formants = vowel_formants(sound, pitch, ceiling=formant_ceiling, nuclei=nuclei)
    weight = spectral_weight(sound, pitch, formant, nuclei)

    # ---- Voice quality: HNR, jitter, shimmer -----------------------------
    harmonicity = call(sound, "To Harmonicity (cc)", 0.01, PITCH_FLOOR, 0.1, 1.0)
    hnr = call(harmonicity, "Get mean", 0, 0)

    point_process = call(sound, "To PointProcess (periodic, cc)", PITCH_FLOOR, PITCH_CEILING)
    jitter = call(point_process, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
    shimmer = call(
        [sound, point_process], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6
    )

    # ---- Intensity (loudness) — directly relevant to "be louder" ---------
    # Over voiced frames only: the level of the voice, not of the room. The
    # take-wide mean folded in every pause (so more silence = "quieter"), and
    # the take-wide minimum was Praat's -300 dB digital-silence sentinel.
    level = np.interp(np.asarray(pitch.xs())[voiced], intensity.xs(), intensity.values[0])
    level = level[level > -200.0]
    mean_int = min_int = max_int = None
    if len(level):
        mean_int = 10.0 * math.log10(float(np.mean(10.0 ** (level / 10.0))))  # energy mean
        min_int, max_int = float(level.min()), float(level.max())

    return {
        "duration_s": clean(duration),
        "pitch": {
            "mean_hz": clean(mean_f0),
            "median_hz": clean(median_f0),
            "min_hz": clean(p5),
            "max_hz": clean(p95),
            "range_hz": clean(p95 - p5) if p5 is not None and p95 is not None else None,
            "sd_hz": clean(sd_f0),
            "p5_hz": clean(p5),
            "p95_hz": clean(p95),
        },
        "formants": formants,
        "voice_quality": {
            "hnr_db": clean(hnr),
            "jitter_pct": clean(jitter * 100 if not math.isnan(jitter) else None),
            "shimmer_pct": clean(shimmer * 100 if not math.isnan(shimmer) else None),
        },
        "intensity": {
            "mean_db": clean(mean_int),
            "min_db": clean(min_int),
            "max_db": clean(max_int),
        },
        "weight": weight,
    }


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of the True runs in a boolean array."""
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(edges == 1).tolist(),
                    np.flatnonzero(edges == -1).tolist()))


def segment_phrases(sound: parselmouth.Sound,
                    intensity: parselmouth.Intensity | None = None,
                    voiced_t: np.ndarray | None = None) -> list[tuple[float, float]]:
    """Split the recording into phrases using silence detection.

    1. **Sounding** frames are within PHRASE_SILENCE_DB of the take's 95th-
       percentile intensity frame. A percentile, not the single loudest frame,
       so one plosive burst or bump on the mic can't raise the bar for the take.
    2. Sounding stretches separated by less than PHRASE_MIN_GAP_S are one phrase
       — stop closures and the micro-pauses between words aren't boundaries.
    3. Stretches shorter than PHRASE_MIN_S, or (when `voiced_t`, the times of
       voiced pitch frames, is given) with fewer than PHRASE_MIN_VOICED voiced
       frames, are dropped: a breath, click or lone syllable has no "ending" to
       grade, and the old 50-300 ms slivers skewed every boundary statistic.

    Returns a list of (start, end) times. `intensity` is the run's shared
    Intensity object when the caller has one.
    """
    if intensity is None:
        intensity = _intensity(sound)
    iv = intensity.values[0]
    its = intensity.xs()
    real = np.isfinite(iv) & (iv > -200.0)  # Praat writes -300 dB for digital silence
    if not real.any():
        return []
    threshold = float(np.percentile(iv[real], 95)) - PHRASE_SILENCE_DB
    half = intensity.dx / 2
    spans: list[tuple[float, float]] = []
    for a, b in _runs(real & (iv >= threshold)):
        t0, t1 = float(its[a]) - half, float(its[b - 1]) + half
        if spans and t0 - spans[-1][1] < PHRASE_MIN_GAP_S:
            spans[-1] = (spans[-1][0], t1)
        else:
            spans.append((t0, t1))
    out: list[tuple[float, float]] = []
    for t0, t1 in spans:
        t0, t1 = max(t0, 0.0), min(t1, sound.xmax)
        if t1 - t0 < PHRASE_MIN_S:
            continue
        if voiced_t is not None and np.count_nonzero(
                (voiced_t >= t0) & (voiced_t <= t1)) < PHRASE_MIN_VOICED:
            continue
        out.append((t0, t1))
    return out


def creak_frames(pitch: parselmouth.Pitch, median: float) -> np.ndarray:
    """Boolean mask of creaky / period-doubled frames (see CREAK_* constants).

    Candidates are runs of voiced frames below CREAK_F0_RATIO x the take median.
    A run is creak when, over the run, Praat's pitch strength is low (irregular,
    aperiodic phonation) OR there is a strong pitch candidate near twice the
    tracked F0 (period doubling: the tracker locked onto the subharmonic, and
    the voice is really still near its modal pitch). Judged per run, not per
    frame, so one noisy frame can't split a creak run into "crash" pieces.

    What this deliberately does NOT catch: a periodic, modal drop to a low
    pitch — i.e. falling into chest voice — which keeps a high strength and has
    no 2x candidate. That stays a register question for the floor.
    """
    arr = pitch.selected_array
    f0, strength = arr["frequency"], arr["strength"]
    low = (f0 > 0) & (f0 < CREAK_F0_RATIO * median)
    if not low.any():
        return low
    cands = pitch.to_array()  # (n_candidates, n_frames)
    freqs, strs = cands["frequency"], cands["strength"]
    tracked = np.where(f0 > 0, f0, np.nan)[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        near_double = (freqs > 0) & (np.abs(12.0 * np.log2(freqs / (2.0 * tracked))) < 1.5)
    double_strength = np.where(near_double, strs, 0.0).max(axis=0)
    creak = np.zeros_like(low)
    for a, b in _runs(low):
        if (float(np.median(strength[a:b])) < CREAK_MAX_STRENGTH
                or float(np.median(double_strength[a:b])) >= CREAK_MIN_SUBHARM):
            creak[a:b] = True
    return creak


def crash_frames(below: np.ndarray, in_register: np.ndarray,
                 time_step: float) -> tuple[np.ndarray, int]:
    """Which sub-floor frames belong to a sustained crash, and how many crashes.

    `below` marks voiced, non-creak frames under the floor; `in_register` marks
    voiced, non-creak frames at/over it (everything else is unvoiced or creak).
    Sub-floor frames are chained into one episode across unvoiced/creak gaps of
    up to CRASH_BRIDGE_S (consonants inside a word) and across in-register blips
    shorter than CRASH_BREAK_FRAMES. An episode is a crash when its sub-floor
    voicing adds up to CRASH_MIN_S; shorter ones are ordinary dips.
    """
    crash = np.zeros_like(below)
    bridge = round(CRASH_BRIDGE_S / time_step)
    need = round(CRASH_MIN_S / time_step)
    episode: list[int] = []
    n_crashes = 0
    since = blip = 0

    def close() -> None:
        nonlocal n_crashes
        if len(episode) >= need:
            crash[episode] = True
            n_crashes += 1
        episode.clear()

    for i in range(len(below)):
        if below[i]:
            episode.append(i)
            since = blip = 0
            continue
        if not episode:
            continue
        since += 1
        blip = blip + 1 if in_register[i] else 0
        if blip >= CRASH_BREAK_FRAMES or since > bridge:
            close()
    close()
    return crash, n_crashes


def analyze_register(sound: parselmouth.Sound, floor: float | None = None,
                     intensity: parselmouth.Intensity | None = None) -> tuple[dict, dict]:
    """Detailed register & phrasing analysis.

    Returns (detail, headline) where `detail` is the heavy per-frame/per-phrase
    payload written to public/analysis/<id>.json and `headline` is the compact
    summary folded into the recording entry for the dashboard cards.

    `floor` is an absolute register floor in Hz; None (the default) puts it
    REGISTER_FLOOR_ST semitones below this take's median F0. Every voiced frame
    then falls in one of four states:
      - **creak** — creaky / period-doubled (creak_frames()). Reported as its
        own share and kept out of every register statistic below: phrase-final
        creak is ordinary in fluent speech and is not a fall out of register.
      - **crash** — part of a sustained sub-floor episode (crash_frames()).
      - **dip** — under the floor, but too briefly to be a crash.
      - **in register** — at or over the floor.
    Phrase onsets/offsets are judged on non-creak frames (anchored on the first/
    last of them), so a fry tail doesn't decide whether a phrase "landed".

    `intensity` is the run's shared Intensity object (phrase segmentation is the
    only thing here that needs one). The Pitch object below is deliberately NOT
    shared with analyze() — see the comment on it.
    """
    # This Pitch is its own object on purpose: a fixed 0.01 s time step, because
    # the contour chart and the per-frame register stats need an even timeline.
    # analyze() uses Praat's automatic step, so the two are genuinely different
    # analyses and must not be merged.
    time_step = 0.01
    pitch = sound.to_pitch(time_step, PITCH_FLOOR, PITCH_CEILING)
    f0 = pitch.selected_array["frequency"]
    times = np.asarray(pitch.xs())
    duration = sound.get_total_duration()
    voiced = f0 > 0
    median = float(np.median(f0[voiced])) if voiced.any() else None

    floor_mode = "relative" if floor is None else "absolute"
    if floor is None:
        floor = median * 2 ** (-REGISTER_FLOOR_ST / 12) if median else DEFAULT_REGISTER_FLOOR

    creak = creak_frames(pitch, median) if median else np.zeros_like(voiced)
    modal = voiced & ~creak
    below = modal & (f0 < floor)
    crash, n_crashes = crash_frames(below, modal & ~below, time_step)

    # 0 unvoiced, 1 in register, 2 dip (brief, under the floor), 3 crash, 4 creak
    state = np.where(voiced, 1, 0)
    state[below] = 2
    state[crash] = 3
    state[creak] = 4

    # Full timeline for the contour chart (null where unvoiced).
    frames_t = [round(float(t), 3) for t in times]
    frames_hz = [round(float(h), 1) if h > 0 else None for h in f0]

    # ---- per-phrase boundary behavior + positional crash counts ------------
    spans = segment_phrases(sound, intensity, times[voiced])
    phrases: list[dict] = []
    # [crash_count, total] of non-creak voiced frames per third of a phrase
    bins = {"onset": [0, 0], "mid": [0, 0], "offset": [0, 0]}
    for t0, t1 in spans:
        idx = np.flatnonzero(voiced & (times >= t0) & (times <= t1))
        m_idx = idx[modal[idx]]
        if not len(m_idx):  # all creak: nothing to say about register here
            continue
        tm = times[m_idx]
        onset = m_idx[tm - tm[0] <= ONSET_WINDOW_S]
        offset = m_idx[tm[-1] - tm <= ENDING_WINDOW_S]
        onset_hz = float(np.mean(f0[onset]))
        offset_hz = float(np.mean(f0[offset]))
        phrases.append({
            "start": round(t0, 3),
            "end": round(t1, 3),
            "onset_hz": round(onset_hz, 1),
            "offset_hz": round(offset_hz, 1),
            "min_hz": round(float(np.min(f0[m_idx])), 1),
            "started_in_register": bool(np.mean(crash[onset]) < 0.5),
            "ended_in_register": bool(np.mean(crash[offset]) < 0.5),
            "sub_register_pct": round(100 * float(np.mean(crash[m_idx])), 1),
            "creak_pct": round(100 * float(np.mean(creak[idx])), 1),
        })
        dur = (t1 - t0) or 1e-9
        for i in m_idx:
            rel = (times[i] - t0) / dur
            b = "onset" if rel < 1 / 3 else "mid" if rel < 2 / 3 else "offset"
            bins[b][1] += 1
            bins[b][0] += int(crash[i])

    def pct(b: list[int]) -> float | None:
        return round(100 * b[0] / b[1], 1) if b[1] else None

    # ---- overall register stability ---------------------------------------
    n_voiced, n_modal = int(voiced.sum()), int(modal.sum())
    in_register_pct = round(100 * (n_modal - int(crash.sum())) / n_modal, 1) if n_modal else None
    creak_pct = round(100 * int(creak.sum()) / n_voiced, 1) if n_voiced else None
    below_floor_pct = round(100 * int(below.sum()) / n_modal, 1) if n_modal else None
    semitones_sd = clean(sd([hz_to_st(h) for h in f0[voiced]]))
    # Melody: in-register, non-creak frames minus octave-up tracking errors, and
    # only when enough of the take survives for an SD to mean anything.
    melody = modal & ~crash & (f0 <= OCTAVE_UP_RATIO * (median or 0.0))
    n_melody = int(melody.sum())
    enough = n_melody >= MELODY_MIN_FRAMES and n_melody >= MELODY_MIN_SHARE * n_voiced
    in_register_semitones_sd = clean(sd([hz_to_st(h) for h in f0[melody]])) if enough else None
    melody_null_reason = None
    if in_register_semitones_sd is None:
        if n_melody < MELODY_MIN_FRAMES:
            melody_null_reason = f"too few steady frames: {n_melody} < {MELODY_MIN_FRAMES}"
        elif n_melody < MELODY_MIN_SHARE * n_voiced:
            melody_null_reason = (f"too little of the voicing is steady: "
                                  f"{100 * n_melody / n_voiced:.0f}% < {MELODY_MIN_SHARE:.0%}")
        else:
            melody_null_reason = "no spread could be measured"
    landed = sum(p["ended_in_register"] for p in phrases)
    landed_pct = round(100 * landed / len(phrases)) if phrases else None

    headline = {
        "floor_hz": round(floor, 1),
        "floor_mode": floor_mode,
        "floor_st_below_median": clean(hz_to_st(median) - hz_to_st(floor)) if median else None,
        "median_hz": clean(median),
        "in_register_pct": in_register_pct,
        "creak_pct": creak_pct,
        "below_floor_pct": below_floor_pct,
        "n_crashes": n_crashes,
        "semitones_sd": semitones_sd,
        "in_register_semitones_sd": in_register_semitones_sd,
        "melody_frames": n_melody,
        "melody_null_reason": melody_null_reason,
        "onset_sub_pct": pct(bins["onset"]),
        "mid_sub_pct": pct(bins["mid"]),
        "offset_sub_pct": pct(bins["offset"]),
        "phrases_landed_pct": landed_pct,
        "n_phrases": len(phrases),
        # The thresholds behind the numbers above, so the UI can quote them.
        "crash_min_s": CRASH_MIN_S,
        "crash_bridge_s": CRASH_BRIDGE_S,
        "onset_window_s": ONSET_WINDOW_S,
        "ending_window_s": ENDING_WINDOW_S,
    }
    detail = {
        "analyzer_version": ANALYZER_VERSION,
        "register_floor_hz": round(floor, 1),
        "semitone_ref_hz": SEMITONE_REF,
        "duration_s": round(duration, 2),
        "time_step": time_step,
        "frames": {"t": frames_t, "hz": frames_hz, "state": state.tolist()},
        "frame_states": {"0": "unvoiced", "1": "in_register", "2": "dip",
                         "3": "crash", "4": "creak"},
        "phrases": phrases,
        "summary": headline,
    }
    return detail, headline


def now_iso() -> str:
    """Local time with its UTC offset, to the second — `analyzed_at`. (`date`
    stays the take's own day, which --date can set.)"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_recordings() -> list[dict]:
    if RECORDINGS_JSON.exists():
        return json.loads(RECORDINGS_JSON.read_text())
    return []


def save_recordings(recordings: list[dict]) -> None:
    payload = json.dumps(recordings, indent=2)
    RECORDINGS_JSON.write_text(payload)
    APP_PUBLIC.mkdir(parents=True, exist_ok=True)
    PUBLIC_RECORDINGS_JSON.write_text(payload)


def backfill(register_floor: float | None = None,
             formant_ceiling: float = FORMANT_CEILING) -> None:
    """Recompute metrics for every existing recording from its stored audio,
    preserving id/label/note/date. Use after adding a new metric (e.g. weight).

    The register floor is re-derived under the current default (relative to each
    take's own median) unless `register_floor` (--register-floor) forces one.
    Entries written before floor_mode existed all carry the old fixed 130 Hz
    default, which is exactly what this re-derives; a floor that was set
    explicitly with --register-floor on a newer entry (floor_mode "absolute")
    is kept.
    """
    recordings = load_recordings()
    if not recordings:
        raise SystemExit("No recordings to backfill.")
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    for entry in recordings:
        src = AUDIO_DIR / Path(entry["audio"]).name
        if not src.exists():
            print(f"⚠️  #{entry['id']}: audio not found ({src}) — skipped")
            continue
        wav = to_wav_mono(src) if src.suffix.lower() != ".wav" else src
        sound = parselmouth.Sound(str(wav))
        old = entry.get("register") or {}
        floor = register_floor
        if floor is None and old.get("floor_mode") == "absolute":
            floor = old.get("floor_hz")
        intensity = _intensity(sound)  # shared by both analyses below
        entry.update(analyze(sound, intensity, formant_ceiling))
        detail, register = analyze_register(sound, floor, intensity)
        entry["register"] = register
        entry["analyzer_version"] = ANALYZER_VERSION
        entry["analyzed_at"] = now_iso()
        (ANALYSIS_DIR / f"{entry['id']}.json").write_text(json.dumps(detail))
        print(f"✅ #{entry['id']} backfilled — weight {entry['weight']}")
    recordings.sort(key=lambda r: r["id"])
    save_recordings(recordings)
    print("🌸 Backfill complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a voice training recording 🌸")
    parser.add_argument("audio", nargs="?", help="Path to the audio file (mp3/m4a/wav/...)")
    parser.add_argument("--label", help="What you were trying this take")
    parser.add_argument("--note", default="", help="Optional extra note")
    parser.add_argument("--date", default="", help="Override date (YYYY-MM-DD); defaults to today")
    parser.add_argument(
        "--register-floor", type=float, default=None,
        help="Absolute register floor in Hz. Default: relative to the take — "
             f"{REGISTER_FLOOR_ST:g} semitones below its own median pitch. "
             "With --backfill, applies to every recording.",
    )
    parser.add_argument(
        "--formant-ceiling", type=float, default=FORMANT_CEILING,
        help=f"Maximum-formant ceiling in Hz (default {FORMANT_CEILING:g}). Keep it the "
             "same across takes so they stay comparable; --backfill uses it for all.",
    )
    parser.add_argument(
        "--id", type=int, default=None,
        help="Force this recording id (e.g. insert an older take as #1). Default: next available.",
    )
    parser.add_argument(
        "--backfill", action="store_true",
        help="Recompute metrics for ALL existing recordings from their stored audio "
             "(preserves id/label/note/date). Use after adding a new metric.",
    )
    args = parser.parse_args()

    if args.backfill:
        backfill(args.register_floor, args.formant_ceiling)
        return

    if not args.audio:
        raise SystemExit("❌ Provide an audio file, or use --backfill.")
    if not args.label:
        raise SystemExit("❌ --label is required when analyzing a new recording.")

    src = Path(args.audio).expanduser()
    if not src.exists():
        raise SystemExit(f"❌ File not found: {src}")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    recordings = load_recordings()
    if args.id is not None:
        if any(r["id"] == args.id for r in recordings):
            raise SystemExit(f"❌ id {args.id} already exists — pick another or renumber first.")
        rec_id = args.id
    else:
        rec_id = (max((r["id"] for r in recordings), default=0)) + 1

    needs_convert = src.suffix.lower() != ".wav"
    wav_path = to_wav_mono(src) if needs_convert else src
    print(f"🎧 Analyzing entry #{rec_id}: {src.name} ...")
    sound = parselmouth.Sound(str(wav_path))
    intensity = _intensity(sound)  # shared by both analyses below
    metrics = analyze(sound, intensity, args.formant_ceiling)
    detail, register = analyze_register(sound, args.register_floor, intensity)

    playback_name = f"{rec_id:03d}{src.suffix.lower()}"
    shutil.copy2(src, AUDIO_DIR / playback_name)
    (ANALYSIS_DIR / f"{rec_id}.json").write_text(json.dumps(detail))

    entry = {
        "id": rec_id,
        "label": args.label,
        "note": args.note,
        "date": args.date or datetime.now().strftime("%Y-%m-%d"),
        "source_file": src.name,
        "audio": f"audio/{playback_name}",
        "detail": f"analysis/{rec_id}.json",
        "analyzer_version": ANALYZER_VERSION,
        "analyzed_at": now_iso(),
        **metrics,
        "register": register,
    }
    recordings.append(entry)
    recordings.sort(key=lambda r: r["id"])
    save_recordings(recordings)

    p, f = metrics["pitch"], metrics["formants"]
    print(
        f"✅ Done!  pitch ~{p['mean_hz']} Hz, F1/F2/F3 = "
        f"{f['f1_hz']}/{f['f2_hz']}/{f['f3_hz']} Hz, loudness "
        f"{metrics['intensity']['mean_db']} dB"
    )
    print(
        f"🎚️  Register (floor {register['floor_hz']} Hz, {register['floor_mode']}): "
        f"{register['in_register_pct']}% in-register · "
        f"phrase endings landed {register['phrases_landed_pct']}% · "
        f"crash share by position {register['onset_sub_pct']}/"
        f"{register['mid_sub_pct']}/{register['offset_sub_pct']}% "
        f"(onset/mid/offset) · creak {register['creak_pct']}%"
    )
    print("🌸 Saved. Run the React dashboard (npm run dev) and refresh.")


if __name__ == "__main__":
    main()
