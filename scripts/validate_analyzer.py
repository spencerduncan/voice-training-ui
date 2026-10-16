"""Validate analyze.py against the reference voices 🌸

Runs the project's own analyzer (analyze(), analyze_register(), to_wav_mono())
over every dashboard-react/public/reference-audio/*.mp3, plus the same audio
tiled 4x and 8x for one woman and one man, and prints a per-speaker table of the
numbers the dashboard leans on. A measurement that holds up should not move when
the same audio is simply repeated, and should not grade an ordinary cis woman's
read speech as "out of register".

It also runs a sensitivity check: a few clean voices are re-synthesised (Praat
PSOLA) with the last 0.6 s of every phrase pulled down by a fixed factor — a
periodic, modal drop, standing in for falling into chest voice at phrase ends —
plus an unmodified resynthesis as a control. A register model that forgives
creak must still catch these.

    uv run scripts/validate_analyzer.py --out before.json
    uv run scripts/validate_analyzer.py --out after.json --compare before.json
    uv run scripts/validate_analyzer.py --analyzer old/analyze.py   # any copy

The script works against any version of analyze.py: fields the analyzer does not
emit yet are filled in from a small amount of instrumentation (which formant
ceiling the old two-ceiling selector picked, how many vowel frames it kept) or
from the raw pitch track, and marked as such in the JSON.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import statistics
import sys
import time
import types
from pathlib import Path
from typing import Self

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for _stream in (sys.stdout, sys.stderr):  # older analyze.py doesn't do this itself
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import parselmouth
from parselmouth.praat import call

import analyze as A

REF_AUDIO = ROOT / "dashboard-react" / "public" / "reference-audio"
DEFAULT_TILE = ("vctk_f294", "vctk_m311")
TILE_FACTORS = (4, 8)
DEFAULT_TRANSIENT = ("vctk_f297", "vctk_m311", "vctk_f301")
DEFAULT_SIM = ("vctk_f301", "vctk_f318")  # no sub-0.6x-median frames: clean modal voices
SIM_FACTORS = (1.0, 0.75, 0.65, 0.55)  # 1.0 = resynthesis control; 0.75 = -5 st ... 0.55 = -10.3 st
SIM_TAIL_S = 0.6


# ---- instrumentation for analyzers that don't report their own ceiling ------
class _CeilingProbe:
    """Records which formant ceiling the legacy two-ceiling selector chose.

    The old vowel_formants() measured at 5500 and 5000 Hz and kept the one whose
    gated F2 list had the smaller statistics.pstdev(). We wrap `call` to note the
    ceiling of each "To Formant (burg)" build and `statistics.pstdev` to note the
    spread and frame count it was handed; the winner is the smallest spread.
    Purely observational: both wrappers return exactly what they wrap.
    """

    def __init__(self) -> None:
        self.last_ceiling: float | None = None
        self.contests: list[tuple[float | None, float, int]] = []

    def __enter__(self) -> Self:
        self._call, self._stats = A.call, A.statistics
        real_call, real_pstdev = A.call, statistics.pstdev

        def call(*args, **kw):
            if len(args) > 4 and args[1] == "To Formant (burg)":
                self.last_ceiling = float(args[4])
            return real_call(*args, **kw)

        def pstdev(data, *a, **kw):
            data = list(data)
            out = real_pstdev(data, *a, **kw)
            self.contests.append((self.last_ceiling, out, len(data)))
            return out

        proxy = types.SimpleNamespace(**{k: getattr(statistics, k) for k in dir(statistics)
                                         if not k.startswith("__")})
        proxy.pstdev = pstdev
        A.call, A.statistics = call, proxy
        return self

    def __exit__(self, *exc) -> None:
        A.call, A.statistics = self._call, self._stats

    def winner(self) -> tuple[float | None, int | None]:
        if not self.contests:
            return None, None
        best = None
        for c in self.contests:  # strict <, first wins ties — as the selector did
            if best is None or c[1] < best[1]:
                best = c
        return best[0], best[2]


# ---- helpers ----------------------------------------------------------------
def tile(sound: parselmouth.Sound, n: int) -> parselmouth.Sound:
    return parselmouth.Sound(np.tile(sound.values, (1, n)),
                             sampling_frequency=sound.sampling_frequency)


def simulate_crash(sound: parselmouth.Sound, factor: float) -> parselmouth.Sound:
    """PSOLA-resynthesise `sound` with F0 x `factor` over the last SIM_TAIL_S of
    each sounding stretch longer than 0.8 s. Self-contained segmentation (95th-
    percentile intensity - 25 dB, gaps < 0.3 s bridged) so it doesn't depend on
    the analyzer version under test."""
    inten = sound.to_intensity(minimum_pitch=75.0)
    iv, its = inten.values[0], inten.xs()
    loud = (iv > -200) & (iv >= np.percentile(iv[iv > -200], 95) - 25)
    spans: list[list[float]] = []
    for i in np.flatnonzero(loud):
        t = float(its[i])
        if spans and t - spans[-1][1] < 0.3:
            spans[-1][1] = t
        else:
            spans.append([t, t])
    manip = call(sound, "To Manipulation", 0.01, 75, 600)
    tier = call(manip, "Extract pitch tier")
    if factor != 1.0:
        for t0, t1 in spans:
            if t1 - t0 > 0.8:
                call(tier, "Multiply frequencies", t1 - SIM_TAIL_S, t1, factor)
    call([tier, manip], "Replace pitch tier")
    return call(manip, "Get resynthesis (overlap-add)")


def raw_pitch_stats(sound: parselmouth.Sound) -> dict:
    """Script-side context from the raw (uncleaned) F0 track, independent of the
    analyzer version: percentiles and the share of voiced frames below 0.6x the
    median (period-doubled / creaky frames)."""
    f0 = sound.to_pitch(0.01, A.PITCH_FLOOR, A.PITCH_CEILING).selected_array["frequency"]
    v = f0[f0 > 0]
    if not len(v):
        return {}
    med = float(np.median(v))
    return {
        "raw_p5_hz": round(float(np.percentile(v, 5)), 1),
        "raw_p95_hz": round(float(np.percentile(v, 95)), 1),
        "raw_low_share_pct": round(100 * float(np.mean(v < 0.6 * med)), 1),
        "raw_high_share_pct": round(100 * float(np.mean(v > 1.8 * med)), 1),
        "n_voiced_frames": len(v),
    }


def register_call(sound: parselmouth.Sound):
    """Call analyze_register with the analyzer's own default floor when it has
    one (take-relative), else the legacy absolute DEFAULT_REGISTER_FLOOR."""
    params = inspect.signature(A.analyze_register).parameters
    floor_p = params.get("floor")
    if floor_p is not None and floor_p.default is not inspect.Parameter.empty:
        return A.analyze_register(sound)
    return A.analyze_register(sound, A.DEFAULT_REGISTER_FLOOR)


def measure(sound: parselmouth.Sound) -> dict:
    t0 = time.perf_counter()
    with _CeilingProbe() as probe:
        m = A.analyze(sound)
    t1 = time.perf_counter()
    _detail, reg = register_call(sound)
    t2 = time.perf_counter()

    p, f, w, i = m["pitch"], m["formants"], m.get("weight", {}), m["intensity"]
    probed_ceiling, probed_n = probe.winner()
    raw = raw_pitch_stats(sound)
    row = {
        "duration_s": m.get("duration_s"),
        "pitch_mean_hz": p.get("mean_hz"),
        "pitch_median_hz": p.get("median_hz"),
        "pitch_p5_hz": p.get("p5_hz", raw.get("raw_p5_hz")),
        "pitch_p95_hz": p.get("p95_hz", raw.get("raw_p95_hz")),
        "pitch_p5_p95_source": "analyzer" if "p5_hz" in p else "raw-track",
        "pitch_min_hz": p.get("min_hz"),
        "pitch_max_hz": p.get("max_hz"),
        "pitch_range_hz": p.get("range_hz"),
        "pitch_sd_hz": p.get("sd_hz"),
        "pitch_sd_raw_hz": p.get("sd_raw_hz"),
        "f1_hz": f.get("f1_hz"),
        "f2_hz": f.get("f2_hz"),
        "f3_hz": f.get("f3_hz"),
        "formant_ceiling_hz": f.get("formant_ceiling_hz", probed_ceiling),
        "n_vowel_frames": f.get("n_vowel_frames", probed_n),
        "formant_fields_source": "analyzer" if "formant_ceiling_hz" in f else "probe",
        "h1a3c_db": w.get("h1a3c_db"),
        "h1a3c_sd_db": w.get("h1a3c_sd_db"),
        "n_weight_frames": w.get("n_frames"),
        "hnr_db": m["voice_quality"].get("hnr_db"),
        "intensity_mean_db": i.get("mean_db"),
        "intensity_min_db": i.get("min_db"),
        "floor_hz": reg.get("floor_hz"),
        "floor_mode": reg.get("floor_mode"),
        "in_register_pct": reg.get("in_register_pct"),
        "creak_pct": reg.get("creak_pct"),
        "phrases_landed_pct": reg.get("phrases_landed_pct"),
        "n_phrases": reg.get("n_phrases"),
        "offset_sub_pct": reg.get("offset_sub_pct"),
        "semitones_sd": reg.get("semitones_sd"),
        "in_register_semitones_sd": reg.get("in_register_semitones_sd"),
        "melody_null_reason": reg.get("melody_null_reason"),
        "analyzer_version": getattr(A, "ANALYZER_VERSION", None),
        "elapsed_analyze_s": round(t1 - t0, 2),
        "elapsed_register_s": round(t2 - t1, 2),
        **raw,
    }
    return row


# ---- table ------------------------------------------------------------------
COLS = [
    ("key", "speaker", 16, None),
    ("pitch_mean_hz", "mean", 6, 0), ("pitch_median_hz", "med", 6, 0),
    ("pitch_p5_hz", "p5", 5, 0), ("pitch_p95_hz", "p95", 5, 0),
    ("pitch_range_hz", "range", 5, 0),
    ("f2_hz", "F2", 5, 0), ("f3_hz", "F3", 5, 0),
    ("formant_ceiling_hz", "ceil", 5, 0), ("n_vowel_frames", "nV", 4, 0),
    ("h1a3c_db", "H1A3c", 6, 1), ("intensity_mean_db", "dB", 5, 1),
    ("floor_hz", "floor", 5, 0), ("in_register_pct", "inReg", 5, 0),
    ("creak_pct", "creak", 5, 0), ("phrases_landed_pct", "land", 4, 0),
    ("n_phrases", "nPh", 3, 0), ("in_register_semitones_sd", "melSD", 5, 1),
    ("elapsed_analyze_s", "t_an", 5, 1), ("elapsed_register_s", "t_rg", 4, 1),
]


def fmt(v, nd) -> str:
    if v is None:
        return "-"
    if nd is None:
        return str(v)
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}"
    return str(v)


def print_table(rows: list[dict]) -> None:
    print("  ".join(f"{h:>{w}}" if i else f"{h:<{w}}" for i, (_, h, w, _) in enumerate(COLS)))
    for r in rows:
        print("  ".join(
            f"{fmt(r.get(k), nd):>{w}}" if i else f"{fmt(r.get(k), nd):<{w}}"
            for i, (k, _, w, nd) in enumerate(COLS)))


DELTA_KEYS = [
    "pitch_p5_hz", "pitch_p95_hz", "pitch_range_hz", "pitch_sd_hz", "f2_hz", "f3_hz",
    "formant_ceiling_hz", "n_vowel_frames", "h1a3c_db", "intensity_mean_db", "floor_hz",
    "in_register_pct", "phrases_landed_pct", "n_phrases", "in_register_semitones_sd",
]


def print_deltas(rows: list[dict], base: list[dict]) -> None:
    by_key = {r["key"]: r for r in base}
    print("\n=== deltas vs baseline (new - old) ===")
    print(f"{'speaker':<16}  " + "  ".join(f"{k[:12]:>12}" for k in DELTA_KEYS))
    for r in rows:
        b = by_key.get(r["key"])
        if not b:
            continue
        cells = []
        for k in DELTA_KEYS:
            n, o = r.get(k), b.get(k)
            if isinstance(n, (int, float)) and isinstance(o, (int, float)):
                cells.append(f"{n - o:>+12.1f}")
            else:
                cells.append(f"{fmt(o, 0)}->{fmt(n, 0)}".rjust(12))
        print(f"{r['key']:<16}  " + "  ".join(cells))


NOT_COMPARED = {"elapsed_analyze_s", "elapsed_register_s", "analyzer_version"}


def print_field_diff(current: dict[str, list[dict]], base: dict[str, list[dict]]) -> None:
    """Every field of every speaker/sim/transient row against the baseline:
    which fields differ (and in how many rows), which are new, which are gone.
    Timings and the version stamp are left out — only measurements count."""
    print("\n=== field-level comparison vs baseline (timings ignored) ===")
    for part in ("rows", "sims", "transients"):
        old = {r["key"]: r for r in base.get(part) or []}
        differing: dict[str, int] = {}
        added: set[str] = set()
        removed: set[str] = set()
        n = 0
        for r in current.get(part) or []:
            b = old.get(r["key"])
            if b is None:
                continue
            n += 1
            for k in r.keys() - NOT_COMPARED:
                if k not in b:
                    added.add(k)
                elif r[k] != b[k]:
                    differing[k] = differing.get(k, 0) + 1
            removed |= b.keys() - r.keys() - NOT_COMPARED
        print(f"  {part}: {n} compared · differing: "
              + (", ".join(f"{k} ({c})" for k, c in sorted(differing.items())) or "none")
              + f" · new: {', '.join(sorted(added)) or 'none'}"
              + f" · gone: {', '.join(sorted(removed)) or 'none'}")


def tiled_summary(rows: list[dict]) -> None:
    print("\n=== take-length stability (same audio tiled) ===")
    bases = sorted({r["key"].split("@")[0] for r in rows if "@" in r["key"]})
    for b in bases:
        group = [r for r in rows if r["key"] == b or r["key"].startswith(b + "@")]
        for k in ("f2_hz", "f3_hz", "h1a3c_db", "in_register_pct", "phrases_landed_pct",
                  "in_register_semitones_sd", "pitch_p5_hz", "pitch_p95_hz"):
            vals = [r.get(k) for r in group]
            nums = [v for v in vals if isinstance(v, (int, float))]
            spread = (max(nums) - min(nums)) if len(nums) == len(vals) and nums else None
            print(f"  {b:<12} {k:<26} " + "  ".join(fmt(v, 1).rjust(8) for v in vals)
                  + (f"   spread {spread:.1f}" if spread is not None else ""))


SIM_KEYS = ("floor_hz", "in_register_pct", "creak_pct", "n_crashes", "phrases_landed_pct",
            "n_phrases", "offset_sub_pct")


def run_sims(keys: list[str]) -> list[dict]:
    rows = []
    for key in keys:
        sound = parselmouth.Sound(str(A.to_wav_mono(REF_AUDIO / f"{key}.mp3")))
        for factor in SIM_FACTORS:
            _detail, reg = register_call(simulate_crash(sound, factor))
            rows.append({"key": f"{key}~x{factor:g}", **{k: reg.get(k) for k in SIM_KEYS}})
    return rows


def print_sims(rows: list[dict], base: list[dict] | None) -> None:
    print(f"\n=== sensitivity: phrase-final modal drops (PSOLA, last {SIM_TAIL_S:g} s) ===")
    old = {r["key"]: r for r in base or []}
    print(f"{'sim':<18}" + "".join(f"{k[:14]:>16}" for k in SIM_KEYS))
    for r in rows:
        b = old.get(r["key"], {})
        print(f"{r['key']:<18}" + "".join(
            (f"{fmt(b.get(k), 0)}->{fmt(r.get(k), 0)}" if b else fmt(r.get(k), 1)).rjust(16)
            for k in SIM_KEYS))


def run_transients(keys: list[str]) -> list[dict]:
    """Formants with and without one 15 ms full-scale noise burst in the leading
    silence: a single transient should not move (or null) a take's formants."""
    rng = np.random.default_rng(0)
    rows = []
    for key in keys:
        sound = parselmouth.Sound(str(A.to_wav_mono(REF_AUDIO / f"{key}.mp3")))
        fs = int(sound.sampling_frequency)
        v = sound.values.copy()
        i0, n = int(0.05 * fs), int(0.015 * fs)
        v[0, i0:i0 + n] = rng.uniform(-0.99, 0.99, n)
        clean, hit = (A.analyze(s)["formants"]
                      for s in (sound, parselmouth.Sound(v, sampling_frequency=fs)))
        rows.append({"key": key, "f2_hz": clean["f2_hz"], "f3_hz": clean["f3_hz"],
                     "f2_hit_hz": hit["f2_hz"], "f3_hit_hz": hit["f3_hz"]})
    return rows


def print_transients(rows: list[dict], base: list[dict] | None) -> None:
    print("\n=== one loud transient (15 ms burst in the leading silence) ===")
    old = {r["key"]: r for r in base or []}
    for r in rows:
        b = old.get(r["key"])
        was = (f"   (before: {fmt(b['f2_hz'], 0)}/{fmt(b['f3_hz'], 0)} -> "
               f"{fmt(b['f2_hit_hz'], 0)}/{fmt(b['f3_hit_hz'], 0)})") if b else ""
        print(f"  {r['key']:<12} F2/F3 clean {fmt(r['f2_hz'], 0)}/{fmt(r['f3_hz'], 0)} -> "
              f"with transient {fmt(r['f2_hit_hz'], 0)}/{fmt(r['f3_hit_hz'], 0)}{was}")


def summary(rows: list[dict]) -> None:
    singles = [r for r in rows if "@" not in r["key"]]
    print("\n=== group summary (1x previews) ===")
    for g in ("f", "m"):
        grp = [r for r in singles if r["key"].startswith(f"vctk_{g}")]
        if not grp:
            continue
        def med(k, grp=grp):
            xs = [r[k] for r in grp if isinstance(r.get(k), (int, float))]
            return statistics.median(xs) if xs else None

        def mn(k, grp=grp):
            xs = [r[k] for r in grp if isinstance(r.get(k), (int, float))]
            return min(xs) if xs else None

        print(f"  {g} (n={len(grp)}): median inReg {fmt(med('in_register_pct'), 1)} "
              f"(min {fmt(mn('in_register_pct'), 1)}), median landed "
              f"{fmt(med('phrases_landed_pct'), 0)} (min {fmt(mn('phrases_landed_pct'), 0)}), "
              f"median nPh {fmt(med('n_phrases'), 0)}, median F2 {fmt(med('f2_hz'), 0)}, "
              f"median H1A3c {fmt(med('h1a3c_db'), 2)}")
    ceilings = [r.get("formant_ceiling_hz") for r in singles]
    print("  ceilings used: " + ", ".join(
        f"{c:g}: {ceilings.count(c)}" for c in sorted({c for c in ceilings if c is not None})))
    nulls = [r["key"] for r in singles if r.get("f2_hz") is None or r.get("f3_hz") is None]
    print(f"  null formants: {nulls or 'none'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="write the rows to this JSON file")
    ap.add_argument("--compare", type=Path, help="baseline JSON to print deltas against")
    ap.add_argument("--only", help="comma-separated speaker keys (e.g. vctk_f294,vctk_m311)")
    ap.add_argument("--tile", default=",".join(DEFAULT_TILE),
                    help="speakers to also run tiled 4x/8x ('' to skip)")
    ap.add_argument("--sim", default=",".join(DEFAULT_SIM),
                    help="clean speakers for the simulated-crash check ('' to skip)")
    ap.add_argument("--transient", default=",".join(DEFAULT_TRANSIENT),
                    help="speakers for the single-transient formant check ('' to skip)")
    ap.add_argument("--analyzer", type=Path,
                    help="validate this copy of analyze.py instead of the repo's")
    args = ap.parse_args()

    global A
    if args.analyzer:
        spec = importlib.util.spec_from_file_location("analyze", args.analyzer)
        A = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(A)
        print(f"analyzer: {args.analyzer}")

    files = sorted(REF_AUDIO.glob("*.mp3"))
    if args.only:
        keep = set(args.only.split(","))
        files = [f for f in files if f.stem in keep]
    tile_keys = [k for k in args.tile.split(",") if k]

    rows: list[dict] = []
    for mp3 in files:
        sound = parselmouth.Sound(str(A.to_wav_mono(mp3)))
        variants = [(mp3.stem, sound)]
        if mp3.stem in tile_keys:
            variants += [(f"{mp3.stem}@x{n}", tile(sound, n)) for n in TILE_FACTORS]
        for key, snd in variants:
            row = {"key": key, **measure(snd)}
            rows.append(row)
            print(f"  ✓ {key:<16} F2={row['f2_hz']} inReg={row['in_register_pct']} "
                  f"landed={row['phrases_landed_pct']} nPh={row['n_phrases']} "
                  f"({row['elapsed_analyze_s'] + row['elapsed_register_s']:.1f}s)", flush=True)

    sims = run_sims([k for k in args.sim.split(",") if k])
    transients = run_transients([k for k in args.transient.split(",") if k])
    base = json.loads(args.compare.read_text(encoding="utf-8")) if args.compare else None
    if isinstance(base, list):  # older --out files held only the speaker rows
        base = {"rows": base}

    print()
    print_table(rows)
    summary(rows)
    tiled_summary(rows)
    if sims:
        print_sims(sims, base.get("sims") if base else None)
    if transients:
        print_transients(transients, base.get("transients") if base else None)
    if base:
        print_deltas(rows, base["rows"])
        print_field_diff({"rows": rows, "sims": sims, "transients": transients}, base)
    if args.out:
        args.out.write_text(json.dumps({"rows": rows, "sims": sims, "transients": transients},
                                       indent=1), encoding="utf-8")
        print(f"\n🌸 wrote {len(rows)} rows, {len(sims)} sims, {len(transients)} transient "
              f"checks → {args.out}")


if __name__ == "__main__":
    main()
