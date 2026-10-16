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
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import zipfile
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
# Per-take annotation entries (entries/<NNN>.tsx), matched to takes by id alone.
ENTRIES_DIR = ROOT / "dashboard-react" / "src" / "annotations" / "entries"
# Sidecar for what only this CLI needs (next_id); recordings.json stays a list.
RECORDINGS_META_JSON = ROOT / "recordings.meta.json"
# --delete / --replace move files here instead of deleting them (gitignored).
TRASH_DIR = ROOT / "trash"
# Each take's source file, byte for byte, as originals/<NNN>.<ext> (gitignored).
# Outside public/, so it is never served or bundled into a build; public/audio
# gets a small mono mp3 instead. --backfill re-measures this file, so a re-run
# reproduces the first run exactly (the mp3 would move pitch, F2 and loudness).
ORIGINALS_DIR = ROOT / "originals"

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
# A take must be at least MIN_TAKE_S long and have MIN_VOICED_FRAMES voiced frames
# on the register model's 10 ms grid (half a second of voice) before anything is
# saved. Silence, white/pink/brown noise and a 50 Hz hum give 0-5 voiced frames;
# a single read sentence gives ~200, the 20-25 s reference previews ~850-1150.
# Fewer than 50 is also below what melody (MELODY_MIN_FRAMES) needs.
MIN_TAKE_S = 1.0
MIN_VOICED_FRAMES = 50
# --register-floor must be a plausible speaking pitch (Hz).
REGISTER_FLOOR_RANGE = (50.0, 400.0)


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


class TakeError(Exception):
    """Something about the request or the audio, said kindly. main() prints it
    and exits non-zero. Raised before the write phase, nothing has been touched."""


FFMPEG_MISSING = ("ffmpeg not found; install it (Windows: winget install Gyan.FFmpeg · "
                  "macOS: brew install ffmpeg · Linux: sudo apt install ffmpeg), then open "
                  "a new terminal so it's on PATH.")


def run_ffmpeg(src: Path, args: list[str]) -> None:
    """Run ffmpeg on `src`, turning its failures into a TakeError a person can
    act on: a missing ffmpeg says how to install it; a file ffmpeg can't read
    shows the last lines of what ffmpeg said, not a Python traceback."""
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(src), *args], check=True, capture_output=True)
    except FileNotFoundError:
        raise TakeError(FFMPEG_MISSING) from None
    except subprocess.CalledProcessError as e:
        said = (e.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        tail = "\n   ".join(said[-4:]) or f"(no message; exit code {e.returncode})"
        raise TakeError(f"ffmpeg couldn't read {src.name}. It said:\n   {tail}") from None


def _temp_dir() -> Path:
    tmp_dir = tempfile.mkdtemp(prefix="voice-garden-")
    _TEMP_WAV_DIRS.append(tmp_dir)
    return Path(tmp_dir)


def to_wav_mono(src: Path) -> Path:
    """Convert any audio file to a temp mono WAV for analysis via ffmpeg.

    The returned path stays valid for the rest of the process; its temp dir is
    removed at exit, so a long run (build_reference_set.py converts ~60 clips)
    no longer leaves a pile of orphaned directories behind. ffmpeg problems
    raise TakeError (see run_ffmpeg()).
    """
    tmp = _temp_dir() / "analysis.wav"
    run_ffmpeg(src, ["-ac", "1", "-ar", "44100", str(tmp)])
    return tmp


# The dashboard's playback copy of every take: mono mp3 at this bitrate, cover
# art and metadata (recorder app, location, ...) dropped. A 3-minute stereo 48 kHz
# WAV is ~35 MB; this is ~2 MB, and voice loses nothing audible.
PLAYBACK_BITRATE = "96k"


def encode_playback(src: Path) -> Path:
    """A temp mono mp3 of `src` for public/audio/ (see PLAYBACK_BITRATE). Made
    before anything is written, so an ffmpeg failure leaves no files behind."""
    out = _temp_dir() / "playback.mp3"
    run_ffmpeg(src, ["-vn", "-map_metadata", "-1", "-ac", "1",
                     "-c:a", "libmp3lame", "-b:a", PLAYBACK_BITRATE, str(out)])
    return out


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
    # sd_hz is the SD over those same cleaned frames (Praat's formula: sample SD,
    # n - 1, over frames); Praat's own SD over every voiced frame, octave jumps
    # and creak included, is kept as sd_raw_hz (it was sd_hz before 2.0.0).
    p5 = p95 = sd_clean = None
    if len(voiced_f0):
        med = float(np.median(voiced_f0))
        kept = (voiced & ~creak_frames(pitch, med)
                & (f0_track <= OCTAVE_UP_RATIO * med) & (f0_track >= OCTAVE_DOWN_RATIO * med))
        if kept.any():
            p5, p95 = (float(v) for v in np.percentile(f0_track[kept], [5, 95]))
        if kept.sum() >= 2:
            sd_clean = float(np.std(f0_track[kept], ddof=1))

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
            "sd_hz": clean(sd_clean),
            "sd_raw_hz": clean(sd_f0),
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


# ---- the take store -----------------------------------------------------------
# recordings.json stays a plain list, because that is what the dashboard reads.
# What only this CLI needs lives beside it in recordings.meta.json: `next_id`,
# so a number is never handed out twice. A reused number would pick up the old
# take's annotation entry (entries/<NNN>.tsx is matched by id alone).

def rel(path: Path) -> str:
    """`path` relative to the repo root, with forward slashes, for messages."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def write_text_atomic(path: Path, text: str) -> None:
    """Write through a temp file in the same folder and rename it over `path`, so
    the dashboard (or a crash mid-write) never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_recordings(create: bool = True) -> list[dict]:
    """The takes in recordings.json. On a first run, when it doesn't exist yet,
    both copies (root + dashboard mirror) are created as [] so the dashboard and
    git see the same empty garden; a missing mirror is restored from the root.
    `create=False` only reads: the commands that analyse audio use it, so a
    refused take leaves no file behind."""
    if not RECORDINGS_JSON.exists():
        if create:
            save_recordings([])
        return []
    if create and not PUBLIC_RECORDINGS_JSON.exists():
        write_text_atomic(PUBLIC_RECORDINGS_JSON, RECORDINGS_JSON.read_text(encoding="utf-8"))
    try:
        data = json.loads(RECORDINGS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise TakeError(f"{rel(RECORDINGS_JSON)} isn't valid JSON ({e}). Fix it, or restore "
                        "it from an --export backup, before changing any takes.") from None
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise TakeError(f"{rel(RECORDINGS_JSON)} should be a list of takes. Fix it, or restore "
                        "it from an --export backup, before changing any takes.")
    return data


def save_recordings(recordings: list[dict]) -> None:
    payload = json.dumps(recordings, indent=2)
    write_text_atomic(RECORDINGS_JSON, payload)
    write_text_atomic(PUBLIC_RECORDINGS_JSON, payload)


def load_meta() -> dict:
    """recordings.meta.json, or {} when it's missing or unreadable (the id
    scan in next_free_id() still keeps numbers unique without it)."""
    try:
        meta = json.loads(RECORDINGS_META_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"⚠️  {rel(RECORDINGS_META_JSON)} is unreadable; working the next take number "
              "out from the files instead.")
        return {}
    return meta if isinstance(meta, dict) else {}


def reserve_id(rec_id: int) -> None:
    """Record that `rec_id` has been used, so next_free_id() stays past it even
    after the take is deleted and the trash emptied."""
    meta = load_meta()
    stored = meta.get("next_id")
    if not isinstance(stored, int) or stored <= rec_id:
        meta["next_id"] = rec_id + 1
        meta["about"] = ("analyze.py's take-number allocator: numbers are never reused, "
                         "so a deleted take's annotation entry can't attach to a new take.")
        write_text_atomic(RECORDINGS_META_JSON, json.dumps(meta, indent=2))


_ID_FILE = re.compile(r"(\d+)\.[A-Za-z0-9]+", re.ASCII)  # 003.mp3, 3.json, 003.tsx


def _id_of(name: str) -> int | None:
    m = _ID_FILE.fullmatch(name)
    return int(m.group(1)) if m else None


TAKE_DIRS = (AUDIO_DIR, ANALYSIS_DIR, ENTRIES_DIR, ORIGINALS_DIR)


def take_files(rec_id: int) -> list[Path]:
    """Every file on disk that belongs to take `rec_id`: its playback audio,
    analysis detail, annotation entry and original."""
    return [p for d in TAKE_DIRS if d.is_dir()
            for p in sorted(d.iterdir()) if p.is_file() and _id_of(p.name) == rec_id]


def ids_in_use(recordings: list[dict]) -> set[int]:
    """Every take number with a trace anywhere: recordings.json, the audio,
    analysis and annotation-entry folders, and the trash."""
    ids = {r["id"] for r in recordings if isinstance(r.get("id"), int)}
    for d in TAKE_DIRS:
        if d.is_dir():
            ids |= {i for p in d.iterdir() if (i := _id_of(p.name)) is not None}
    if TRASH_DIR.is_dir():
        for p in TRASH_DIR.rglob("*"):
            if (i := _id_of(p.name)) is not None:
                ids.add(i)
            elif p.name == "entry.json":
                try:
                    i = json.loads(p.read_text(encoding="utf-8")).get("id")
                except (OSError, ValueError, AttributeError):
                    continue
                if isinstance(i, int):
                    ids.add(i)
    return ids


def next_free_id(recordings: list[dict]) -> int:
    """One past the highest take number ever used (see ids_in_use() and
    reserve_id()). Deleted numbers stay retired."""
    stored = load_meta().get("next_id")
    stored = stored if isinstance(stored, int) else 1
    return max(stored, max(ids_in_use(recordings), default=0) + 1)


def _by_id(entry: dict) -> float:
    """Sort key: by take number; a hand-broken entry without one sorts last
    instead of crashing the save."""
    rec_id = entry.get("id")
    return rec_id if isinstance(rec_id, int) else math.inf


def describe(entry: dict) -> str:
    return f"#{entry.get('id')} “{entry.get('label', '')}” ({entry.get('date') or 'no date'})"


def find_take(recordings: list[dict], rec_id: int) -> dict:
    for r in recordings:
        if r.get("id") == rec_id:
            return r
    ids = ", ".join(f"#{r.get('id')}" for r in recordings) or "none yet"
    raise TakeError(f"There's no take #{rec_id}. Takes here: {ids}.")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def take_sha256(entry: dict) -> str | None:
    """The hash of the file a take was made from. Takes from before
    `source_sha256` existed kept a byte-for-byte copy of their source as the
    playback file, so for those the playback file's hash is the same thing."""
    if isinstance(entry.get("source_sha256"), str):
        return entry["source_sha256"]
    audio = entry.get("audio")
    if isinstance(audio, str) and (p := AUDIO_DIR / Path(audio).name).is_file():
        return file_sha256(p)
    return None


def find_duplicate(recordings: list[dict], sha: str, skip: int | None = None) -> dict | None:
    return next((r for r in recordings if r.get("id") != skip and take_sha256(r) == sha), None)


def new_trash_dir(why: str) -> Path:
    """A fresh trash/<timestamp>-<why>/ folder. trash/ is gitignored and never
    emptied by this script."""
    base = TRASH_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{why}"
    path, n = base, 2
    while path.exists():
        path, n = base.with_name(f"{base.name}-{n}"), n + 1
    path.mkdir(parents=True)
    return path


def move_to_trash(files: list[Path], trash: Path) -> list[str]:
    """Move each file to trash/<...>/<its folder name>/<its name>."""
    moved = []
    for p in files:
        dest = trash / p.parent.name / p.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest))
        moved.append(f"{p.parent.name}/{p.name}")
    return moved


def read_sound(src: Path) -> parselmouth.Sound:
    """A WAV is read as-is (as it always was); anything else, or a WAV Praat
    can't read, goes through ffmpeg to a mono 44.1 kHz WAV first."""
    if src.suffix.lower() == ".wav":
        try:
            return parselmouth.Sound(str(src))
        except parselmouth.PraatError:
            pass
    wav = to_wav_mono(src)
    try:
        return parselmouth.Sound(str(wav))
    except parselmouth.PraatError:
        raise TakeError(f"Couldn't read any audio from {src.name}.") from None


def check_usable(src: Path, metrics: dict, detail: dict) -> None:
    """Refuse a take too short or too silent to measure (see MIN_TAKE_S and
    MIN_VOICED_FRAMES), before anything is written."""
    duration = metrics.get("duration_s") or 0.0
    if duration < MIN_TAKE_S:
        raise TakeError(f"{src.name} is only {duration:.2f} s long; a take needs at least "
                        f"{MIN_TAKE_S:g} s of audio. Nothing was saved.")
    voiced = sum(hz is not None for hz in detail["frames"]["hz"])
    if voiced < MIN_VOICED_FRAMES:
        raise TakeError(
            f"I could hardly hear a voice in {src.name}: {voiced * detail['time_step']:.2f} s of "
            f"voicing, and a take needs at least {MIN_VOICED_FRAMES * detail['time_step']:g} s. "
            "Was the mic muted, or the wrong input picked? Nothing was saved.")


def measure_file(src: Path, floor: float | None,
                 formant_ceiling: float) -> tuple[dict, dict, dict]:
    """Read one audio file, run both analyses and check the take is usable:
    (metrics, detail, register). Raises TakeError; writes nothing."""
    sound = read_sound(src)
    intensity = _intensity(sound)  # shared by both analyses below
    metrics = analyze(sound, intensity, formant_ceiling)
    detail, register = analyze_register(sound, floor, intensity)
    check_usable(src, metrics, detail)
    return metrics, detail, register


def install_playback(playback: Path, rec_id: int) -> str:
    """Move an encode_playback() file into public/audio/ as <NNN>.mp3; returns
    the entry's `audio` path."""
    name = f"{rec_id:03d}{playback.suffix.lower()}"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(playback), str(AUDIO_DIR / name))
    return f"audio/{name}"


def keep_original(src: Path, rec_id: int) -> Path:
    """Copy the take's source file, unchanged, to originals/<NNN>.<ext>."""
    ext = src.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext, re.ASCII):
        ext = ".audio"
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    dest = ORIGINALS_DIR / f"{rec_id:03d}{ext}"
    shutil.copy2(src, dest)
    return dest


def measured_file(entry: dict) -> Path | None:
    """The file a take's numbers come from: its original when kept, else (takes
    from before originals/ existed) its playback copy, which for those takes
    is the source file byte for byte."""
    rec_id, audio = entry.get("id"), entry.get("audio")
    if isinstance(rec_id, int) and ORIGINALS_DIR.is_dir():
        for p in sorted(ORIGINALS_DIR.iterdir()):
            if p.is_file() and _id_of(p.name) == rec_id:
                return p
    if isinstance(audio, str):
        return AUDIO_DIR / Path(audio).name
    return None


def write_detail(rec_id: int, detail: dict) -> str:
    write_text_atomic(ANALYSIS_DIR / f"{rec_id}.json", json.dumps(detail))
    return f"analysis/{rec_id}.json"


def print_summary(metrics: dict, register: dict) -> None:
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


def duplicate_message(dup: dict, src: Path) -> str:
    return (f"This file is already take {describe(dup)}. To re-analyse that take in place, "
            f"run with --replace {dup['id']} \"{src}\"; to add it as a new take anyway, "
            "add --allow-duplicate.")


# ---- commands -----------------------------------------------------------------
def add_take(args: argparse.Namespace) -> None:
    """The plain run: analyse a new recording and add it as the next take."""
    if not args.audio:
        raise TakeError("Give an audio file to analyse (see --help for --backfill, "
                        "--replace, --delete, --relabel, --export and --import).")
    if not args.label:
        raise TakeError("--label is required for a new take: say what you were practising.")
    src = Path(args.audio).expanduser()
    if not src.is_file():
        raise TakeError(f"File not found: {src}")

    recordings = load_recordings(create=False)
    first_take = not recordings
    sha = file_sha256(src)
    if not args.allow_duplicate and (dup := find_duplicate(recordings, sha)):
        raise TakeError(duplicate_message(dup, src))
    if args.id is None:
        rec_id = next_free_id(recordings)
    else:
        rec_id = args.id
        if any(r.get("id") == rec_id for r in recordings):
            taken = find_take(recordings, rec_id)
            raise TakeError(
                f"Take {describe(taken)} already exists. To swap in new audio for it, run "
                f"--replace {rec_id} <audio>; to remove it, --delete {rec_id}; or leave out "
                f"--id and this take gets the next free number (#{next_free_id(recordings)}).")
        if leftovers := take_files(rec_id):
            raise TakeError(
                f"Files from an earlier take #{rec_id} are still here "
                f"({', '.join(rel(p) for p in leftovers)}); a new take #{rec_id} would pick "
                "them up. Move them away first, or choose another --id.")

    print(f"🎧 Analyzing entry #{rec_id}: {src.name} ...")
    metrics, detail, register = measure_file(src, args.register_floor, args.formant_ceiling)
    playback = encode_playback(src)

    # ---- write phase: nothing above has touched a file -----------------------
    reserve_id(rec_id)
    keep_original(src, rec_id)
    audio = install_playback(playback, rec_id)
    entry = {
        "id": rec_id,
        "label": args.label,
        "note": args.note or "",
        "date": args.date or datetime.now().strftime("%Y-%m-%d"),
        "source_file": src.name,
        "source_sha256": sha,
        "audio": audio,
        "detail": write_detail(rec_id, detail),
        "analyzer_version": ANALYZER_VERSION,
        "analyzed_at": now_iso(),
        **metrics,
        "register": register,
    }
    recordings.append(entry)
    recordings.sort(key=_by_id)
    save_recordings(recordings)

    print_summary(metrics, register)
    print("🌸 Saved. Run the React dashboard (npm run dev) and refresh.")
    if first_take:
        print("🔒 Your takes stay on this computer: audio, originals, analysis and "
              "recordings.json are gitignored (README: “Your recordings stay on your machine”).")


def replace_take(rec_id: int, args: argparse.Namespace) -> None:
    """--replace ID <audio>: re-analyse take `rec_id` from new audio, in place.
    Keeps its id, label, note and date (unless --label/--note/--date are given)
    and its annotation entry; the old audio and analysis go to the trash."""
    if not args.audio:
        raise TakeError(f"--replace {rec_id} needs the audio file to analyse: "
                        f"analyze.py <audio> --replace {rec_id}")
    src = Path(args.audio).expanduser()
    if not src.is_file():
        raise TakeError(f"File not found: {src}")
    recordings = load_recordings(create=False)
    entry = find_take(recordings, rec_id)
    sha = file_sha256(src)
    if not args.allow_duplicate and (dup := find_duplicate(recordings, sha, skip=rec_id)):
        raise TakeError(f"This file is already take {describe(dup)}; replacing #{rec_id} with "
                        "it would make two copies of one take. Add --allow-duplicate if "
                        "that's really what you want.")
    # Same floor rule as --backfill: an explicitly set (absolute) floor is kept.
    floor = args.register_floor
    old_register = entry.get("register") or {}
    if floor is None and old_register.get("floor_mode") == "absolute":
        floor = old_register.get("floor_hz")

    print(f"🎧 Re-analyzing entry #{rec_id} from {src.name} ...")
    metrics, detail, register = measure_file(src, floor, args.formant_ceiling)
    playback = encode_playback(src)
    # Staged too: `src` may be this take's own original (re-measuring with
    # another --register-floor), which is about to move to the trash.
    staged = _temp_dir() / src.name
    shutil.copy2(src, staged)

    # ---- write phase ---------------------------------------------------------
    trash = new_trash_dir(f"replaced-take{rec_id:03d}")
    write_text_atomic(trash / "entry.json", json.dumps(entry, indent=2))
    old_files = [p for p in take_files(rec_id) if p.parent != ENTRIES_DIR]
    moved = move_to_trash(old_files, trash)
    keep_original(staged, rec_id)
    entry.update({
        "label": args.label if args.label is not None else entry.get("label", ""),
        "note": args.note if args.note is not None else entry.get("note", ""),
        "date": args.date if args.date is not None else entry.get("date", ""),
        "source_file": src.name,
        "source_sha256": sha,
        "audio": install_playback(playback, rec_id),
        "detail": write_detail(rec_id, detail),
        "analyzer_version": ANALYZER_VERSION,
        "analyzed_at": now_iso(),
        **metrics,
        "register": register,
    })
    save_recordings(recordings)

    print_summary(metrics, register)
    print(f"🧺 The old {' and '.join(moved) or 'entry'} went to {rel(trash)}/.")
    entry_file = ENTRIES_DIR / f"{rec_id:03d}.tsx"
    if entry_file.exists():
        print(f"📝 {rel(entry_file)} was written for the old audio. Re-read it against the "
              "new numbers before trusting its notes.")
    print("🌸 Saved. Refresh the dashboard.")


def delete_take(rec_id: int) -> None:
    """--delete ID: take a take out of the garden. Its entry, audio, analysis
    and annotation entry move to trash/<timestamp>-deleted-takeNNN/ (nothing is
    deleted for good), and its number is retired."""
    recordings = load_recordings()
    entry = find_take(recordings, rec_id)
    trash = new_trash_dir(f"deleted-take{rec_id:03d}")
    write_text_atomic(trash / "entry.json", json.dumps(entry, indent=2))
    reserve_id(rec_id)
    recordings.remove(entry)
    save_recordings(recordings)
    moved = move_to_trash(take_files(rec_id), trash)
    print(f"🧺 Take {describe(entry)} moved to {rel(trash)}/ "
          f"({', '.join(['entry.json', *moved])}).")
    print(f"   Nothing is gone for good: empty trash/ yourself when you're sure. "
          f"Take numbers are never reused, so the next new take is "
          f"#{next_free_id(recordings)}.")


def relabel_take(rec_id: int, label: str | None, note: str | None, date: str | None) -> None:
    """--relabel ID: change a take's label, note and/or date. Nothing is
    re-measured."""
    changes = {k: v for k, v in (("label", label), ("note", note), ("date", date))
               if v is not None}
    if not changes:
        raise TakeError(f"--relabel {rec_id} needs at least one of --label, --note, --date.")
    recordings = load_recordings()
    entry = find_take(recordings, rec_id)
    said = [f"{k} “{entry.get(k, '')}” → “{v}”" for k, v in changes.items()
            if entry.get(k) != v]
    if not said:
        print(f"Take #{rec_id} already says that; nothing to change.")
        return
    entry.update(changes)
    save_recordings(recordings)
    print(f"✏️  Take #{rec_id}: " + "; ".join(said) + ".")


EXPORT_FORMAT = "voice-garden-export"
EXPORT_FORMAT_VERSION = 1
# What a zip may carry into each folder, by extension.
IMPORT_EXTS = {
    "audio": {"mp3", "m4a", "wav", "ogg", "oga", "opus", "flac", "webm", "aac", "mp4"},
    "analysis": {"json"},
    "entries": {"tsx"},
    "originals": None,  # any short alphanumeric extension: kept, never served or run
}


def _export_dirs() -> dict[str, Path]:
    return {"audio": AUDIO_DIR, "analysis": ANALYSIS_DIR, "entries": ENTRIES_DIR,
            "originals": ORIGINALS_DIR}


def export_garden(out: Path) -> None:
    """--export out.zip: one zip holding everything personal: recordings.json,
    recordings.meta.json, audio/, analysis/, entries/ and a manifest.json.
    The dashboard's public/recordings.json is a copy of recordings.json, so it
    isn't stored twice; --import writes both."""
    out = out.expanduser()
    if out.suffix.lower() != ".zip":
        raise TakeError(f"--export needs a .zip path (got {out}).")
    if out.exists():
        raise TakeError(f"{out} already exists; pick a new name so no backup is overwritten.")
    recordings = load_recordings()
    files = [(f"{folder}/{p.name}", p) for folder, d in _export_dirs().items() if d.is_dir()
             for p in sorted(d.iterdir()) if p.is_file() and _id_of(p.name) is not None]
    manifest = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "exported_at": now_iso(),
        "next_id": next_free_id(recordings),
        "takes": [{"id": r.get("id"), "label": r.get("label"),
                   "analyzer_version": r.get("analyzer_version")} for r in recordings],
        "files": ["recordings.json", "recordings.meta.json", *(a for a, _ in files)],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("recordings.json", json.dumps(recordings, indent=2))
        z.writestr("recordings.meta.json", json.dumps(load_meta(), indent=2))
        for arcname, path in files:
            z.write(path, arcname)
    os.replace(tmp, out)
    counts = ", ".join(f"{sum(a.startswith(f + '/') for a, _ in files)} {f}"
                       for f in _export_dirs())
    print(f"📦 Exported {len(recordings)} take(s) ({counts}) to "
          f"{out} ({out.stat().st_size / 1e6:.1f} MB). It's your voice: keep it private.")


def import_garden(src: Path) -> None:
    """--import in.zip: add the takes from an --export zip. Takes already here
    unchanged are skipped; if the zip has a different take under a number in use
    here, nothing is imported. Everything is checked before the first write."""
    src = src.expanduser()
    if not src.is_file():
        raise TakeError(f"File not found: {src}")
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        raise TakeError(f"{src} isn't a zip file.") from None
    with z:
        names = set(z.namelist())

        def read_json(name: str):
            if name not in names:
                raise TakeError(f"{src.name} has no {name}, so it isn't a Voice Garden "
                                "export (make one with --export).")
            try:
                return json.loads(z.read(name).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise TakeError(f"{name} in {src.name} isn't valid JSON.") from None

        manifest = read_json("manifest.json")
        if not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT:
            raise TakeError(f"{src.name} isn't a Voice Garden export (make one with --export).")
        if not isinstance(manifest.get("format_version"), int) \
                or manifest["format_version"] > EXPORT_FORMAT_VERSION:
            raise TakeError(f"{src.name} was written by a newer analyze.py "
                            f"(export format {manifest.get('format_version')}); update first.")
        incoming = read_json("recordings.json")
        if not isinstance(incoming, list) or not all(
                isinstance(r, dict) and isinstance(r.get("id"), int) and r["id"] >= 1
                for r in incoming):
            raise TakeError(f"recordings.json in {src.name} isn't a list of numbered takes.")
        if len({r["id"] for r in incoming}) != len(incoming):
            raise TakeError(f"recordings.json in {src.name} uses a take number twice.")
        meta_in = read_json("recordings.meta.json") if "recordings.meta.json" in names else {}

        recordings = load_recordings()
        here = {r.get("id"): r for r in recordings}
        clash = [r["id"] for r in incoming if r["id"] in here and here[r["id"]] != r]
        if clash:
            raise TakeError(
                f"Take number(s) {', '.join(f'#{i}' for i in clash)} are already used here by "
                f"different takes, so nothing was imported. Export this garden first if you "
                f"want a backup, then --delete those takes, or import into a fresh checkout.")
        new = [r for r in incoming if r["id"] not in here]
        new_ids = {r["id"] for r in new}
        busy = {i: files for i in sorted(new_ids) if (files := take_files(i))}
        if busy:
            raise TakeError("Files for take(s) the zip would add are already here: " + "; ".join(
                f"#{i}: {', '.join(rel(p) for p in files)}" for i, files in busy.items())
                + ". Move them away first; nothing was imported.")

        writes: list[tuple[Path, str]] = []
        # Files of takes already here are left alone quietly; `skipped` counts
        # only what isn't a take file at all.
        skipped = 0
        here_ids = {r["id"] for r in incoming} - new_ids
        for info in z.infolist():
            if info.is_dir() or info.filename in (
                    "manifest.json", "recordings.json", "recordings.meta.json"):
                continue
            folder, _, name = info.filename.partition("/")
            ext = name.rpartition(".")[2].lower()
            exts = IMPORT_EXTS.get(folder, set())
            if (folder not in IMPORT_EXTS or "/" in name
                    or (exts is not None and ext not in exts)
                    or _id_of(name) not in new_ids):
                skipped += _id_of(name) not in here_ids
                continue
            writes.append((_export_dirs()[folder] / name, info.filename))
        missing = [r["id"] for r in new if isinstance(r.get("audio"), str) and not any(
            arc == f"audio/{Path(r['audio']).name}" for _, arc in writes)]

        # ---- write phase -----------------------------------------------------
        for dest, arcname in writes:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f".{dest.name}.tmp")
            tmp.write_bytes(z.read(arcname))
            os.replace(tmp, dest)
    if new:
        theirs = meta_in.get("next_id") if isinstance(meta_in, dict) else None
        reserve_id(max(max(new_ids), theirs - 1 if isinstance(theirs, int) else 0))
        recordings.extend(new)
        recordings.sort(key=_by_id)
        save_recordings(recordings)
    same = len(incoming) - len(new)
    print(f"📥 Imported {len(new)} take(s)"
          + (f" ({', '.join(f'#{i}' for i in sorted(new_ids))})" if new else "")
          + f" and {len(writes)} file(s) from {src.name}"
          + (f"; {same} take(s) were already here and left as they are" if same else "")
          + (f"; ignored {skipped} file(s) that aren't take files" if skipped else "") + ".")
    if missing:
        print(f"⚠️  No audio in the zip for take(s) {', '.join(f'#{i}' for i in missing)}.")
    old = sorted({str(r.get("analyzer_version") or "pre-2.0") for r in new} - {ANALYZER_VERSION})
    if old:
        print(f"ℹ️  Some imported takes were measured by analyzer {', '.join(old)}; this is "
              f"{ANALYZER_VERSION}. Run --backfill to re-measure them all the same way.")
    if any(dest.parent == ENTRIES_DIR for dest, _ in writes):
        print("ℹ️  Annotation entries are code the dashboard runs: only import zips you made.")


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

    All or nothing per take, and nothing half-written overall: every take is
    measured first, its detail written under a temp name (.<id>.json.backfill);
    only then is recordings.json saved and the details renamed into place. An
    interruption before that point leaves the old files exactly as they were.
    A take that fails (missing audio, unreadable, too short) is reported and
    keeps its old numbers; the run carries on and exits non-zero at the end.
    """
    recordings = load_recordings()
    if not recordings:
        raise TakeError("No recordings to backfill.")
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []  # (temp detail, final detail)
    updated: dict[int, dict] = {}  # index in recordings -> re-measured entry
    failed: list[str] = []
    try:
        for i, entry in enumerate(recordings):
            rec_id = entry.get("id")
            try:
                src = measured_file(entry)
                if not isinstance(rec_id, int) or src is None:
                    raise TakeError("the entry has no take number or no audio path")
                if not src.is_file():
                    raise TakeError(f"its audio is missing ({rel(src)})")
                old = entry.get("register") or {}
                floor = register_floor
                if floor is None and old.get("floor_mode") == "absolute":
                    floor = old.get("floor_hz")
                metrics, detail, register = measure_file(src, floor, formant_ceiling)
                final = ANALYSIS_DIR / f"{rec_id}.json"
                tmp = final.with_name(f".{final.name}.backfill")
                tmp.write_text(json.dumps(detail), encoding="utf-8")
                staged.append((tmp, final))
            except Exception as e:  # noqa: BLE001 — one bad take mustn't stop the rest
                failed.append(f"#{rec_id}: {e}")
                print(f"⚠️  #{rec_id} not re-measured: {e}")
                continue
            updated[i] = {**entry, **metrics, "register": register,
                          "detail": f"analysis/{rec_id}.json",
                          "analyzer_version": ANALYZER_VERSION, "analyzed_at": now_iso()}
            print(f"✅ #{rec_id} re-measured — pitch ~{metrics['pitch']['mean_hz']} Hz, "
                  f"in-register {register['in_register_pct']}%")
        # ---- commit: recordings.json, then the details ------------------------
        for i, entry in updated.items():
            recordings[i] = entry
        if updated:
            recordings.sort(key=_by_id)
            save_recordings(recordings)
            for tmp, final in staged:
                os.replace(tmp, final)
            staged.clear()
    finally:
        for tmp, _ in staged:  # interrupted or failed before the commit
            tmp.unlink(missing_ok=True)
    print(f"🌸 Backfill: {len(updated)} of {len(recordings)} take(s) re-measured with analyzer "
          f"{ANALYZER_VERSION}.")
    if failed:
        raise TakeError(f"{len(failed)} take(s) couldn't be re-measured and kept their old "
                        "numbers:\n   " + "\n   ".join(failed))


# Which options each command reads; anything else given alongside it is an error
# rather than silently ignored ("--delete 3 --label x" deletes nothing by label).
_OPTIONS_FOR = {
    "add": {"audio", "label", "note", "date", "register_floor", "formant_ceiling", "id",
            "allow_duplicate"},
    "replace": {"audio", "label", "note", "date", "register_floor", "formant_ceiling",
                "allow_duplicate"},
    "relabel": {"label", "note", "date"},
    "backfill": {"register_floor", "formant_ceiling"},
    "delete": set(),
    "export": set(),
    "import_zip": set(),
}
_COMMANDS = ("backfill", "delete", "relabel", "replace", "export", "import_zip")


def _date_arg(text: str) -> str:
    """--date: a real calendar date written YYYY-MM-DD (what the trend charts sort by)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text, re.ASCII):
        raise argparse.ArgumentTypeError(
            f"write the date as YYYY-MM-DD, e.g. {datetime.now():%Y-%m-%d} (got {text!r})")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text} isn't a real date") from None
    return text


def _floor_arg(text: str) -> float:
    """--register-floor: Hz, within REGISTER_FLOOR_RANGE."""
    lo, hi = REGISTER_FLOOR_RANGE
    try:
        hz = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"give the floor in Hz, e.g. 140 (got {text!r})") from None
    if not lo <= hz <= hi:  # also rejects nan
        raise argparse.ArgumentTypeError(
            f"{text} Hz isn't a speaking pitch; use {lo:g}-{hi:g} Hz, or leave it out for the "
            f"take-relative floor ({REGISTER_FLOOR_ST:g} semitones under the take's median)")
    return hz


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze a voice training recording 🌸",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  analyze.py take.m4a --label "bright resonance"         add a new take
  analyze.py better.m4a --replace 3                      re-analyse take #3 from new audio
  analyze.py --relabel 3 --label "forward focus" --date 2026-10-01
  analyze.py --delete 3                                  move take #3 to trash/
  analyze.py --backfill                                  re-measure every take
  analyze.py --export backup.zip / --import backup.zip   back up / restore""",
    )
    parser.add_argument("audio", nargs="?", help="Path to the audio file (mp3/m4a/wav/...)")
    parser.add_argument("--label", help="What you were trying this take")
    parser.add_argument("--note", default=None, help="Optional extra note")
    parser.add_argument("--date", type=_date_arg, default=None,
                        help="The take's date (YYYY-MM-DD); defaults to today")
    parser.add_argument(
        "--register-floor", type=_floor_arg, default=None,
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
        help="Give a new take this number (e.g. add an older take as #1). Default: the next "
             "number never used before.",
    )
    parser.add_argument(
        "--allow-duplicate", action="store_true",
        help="Add (or --replace with) a file that is already a take.",
    )
    cmd = parser.add_mutually_exclusive_group()
    cmd.add_argument(
        "--backfill", action="store_true",
        help="Recompute metrics for ALL existing recordings from their stored audio "
             "(preserves id/label/note/date). Use after the analyzer changes.",
    )
    cmd.add_argument("--replace", type=int, metavar="ID",
                     help="Re-analyse take ID from the given audio, keeping its number, "
                          "label, note, date and annotation entry (old files go to trash/).")
    cmd.add_argument("--relabel", type=int, metavar="ID",
                     help="Change take ID's --label, --note and/or --date.")
    cmd.add_argument("--delete", type=int, metavar="ID",
                     help="Move take ID (entry, audio, analysis, annotation entry) to trash/.")
    cmd.add_argument("--export", type=Path, metavar="OUT.zip",
                     help="Back up every take, its audio, analysis and annotation entry.")
    cmd.add_argument("--import", dest="import_zip", type=Path, metavar="IN.zip",
                     help="Add the takes from an --export zip.")
    return parser


def run(args: argparse.Namespace) -> None:
    if args.backfill:
        backfill(args.register_floor, args.formant_ceiling)
    elif args.replace is not None:
        replace_take(args.replace, args)
    elif args.relabel is not None:
        relabel_take(args.relabel, args.label, args.note, args.date)
    elif args.delete is not None:
        delete_take(args.delete)
    elif args.export is not None:
        export_garden(args.export)
    elif args.import_zip is not None:
        import_garden(args.import_zip)
    else:
        add_take(args)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    command = next((c for c in _COMMANDS if getattr(args, c) not in (None, False)), "add")
    flag = {"import_zip": "--import", "add": "a new take"}.get(command, f"--{command}")
    for name in vars(args).keys() - _OPTIONS_FOR[command] - set(_COMMANDS):
        if getattr(args, name) != parser.get_default(name):
            shown = name if name == "audio" else "--" + name.replace("_", "-")
            parser.error(f"{shown} doesn't go with {flag}")
    for name in ("id", "replace", "relabel", "delete"):
        if (v := getattr(args, name)) is not None and v < 1:
            parser.error(f"--{name} takes a take number (1 or more), not {v}")
    try:
        run(args)
    except TakeError as e:
        raise SystemExit(f"❌ {e}") from None


if __name__ == "__main__":
    main()
