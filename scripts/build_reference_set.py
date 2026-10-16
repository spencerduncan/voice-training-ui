# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "praat-parselmouth==0.4.7",  # same Praat as uv.lock, so references and takes match
#   "numpy==2.5.0",
#   "huggingface_hub",
#   "pyarrow",
#   "fsspec",
# ]
# ///
"""Build the reference-voice corpus for Voice Garden 🌸

Measures real adult women and men from VCTK 0.92 the same way a user's take is
measured, and writes:

  dashboard-react/public/reference.json             {"meta": {...}, "voices": [...]}
  dashboard-react/public/reference-audio/<key>.mp3  the audio each voice was measured on
  docs/reference-stats.json                         per-sex stats, Cohen's d, zone bands

Measurement unit
  A take is one analyze() + analyze_register() pass over one continuous file.
  Each reference speaker is measured the same way: their clips are concatenated
  with 0.35 s gaps into ONE preview mp3 (mono, 44.1 kHz, 96 kbps), and that mp3
  is decoded and analysed in a single pass, exactly like a user's mp3. So the
  preview the modal plays is the very audio its tick was measured on. Previews
  are therefore 4 concatenated clips, not one sentence. (The old build averaged
  analyze() over separate 2-4 s clips: a different estimator from a ~40 s take.)
  Per-clip values are still computed, as a diagnostic in the stats file only.

Clips
  The same four consecutive Rainbow Passage sentences for every speaker (VCTK
  text ids 009-012, mic1): the passage users usually read, so speakers differ in
  voice rather than in vowels. A speaker missing one of them gets the next
  available Rainbow Passage sentence (FALLBACK_IDS); the ids used are recorded.

Download
  From the sanchit-gandhi/vctk parquet mirror, pinned to DATASET_REVISION. Its
  shards are speaker-ordered and every row group's footer carries min/max stats
  for speaker_id, so speakers are found from the footers alone (~1 MB for all 27
  shards). Then only the small text columns of each speaker's first row group
  are read to pick the clips, and only the audio column of the row groups that
  hold them is fetched (~15 MB per speaker). Clips are cached in .refcache/
  (gitignored); a re-run with a warm cache needs no network.

Failure policy
  Any registered speaker with no clips, a clip that fails to download, or an
  analysis that throws or leaves a core metric null fails the run: it exits 1
  and writes nothing. --allow-partial writes what did build.

Zone rule: see zone_bands().

    uv run scripts/build_reference_set.py                  # build
    uv run scripts/build_reference_set.py --plan           # locate clips, report download size
    uv run scripts/build_reference_set.py --accents American   # US speakers only
    uv run scripts/build_reference_set.py --allow-partial

VCTK is CC BY 4.0 (CSTR, University of Edinburgh); see Credits in README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np
import parselmouth

import analyze as A

REPO = "sanchit-gandhi/vctk"
DATASET_REVISION = "73ef4ee7d49a6fed4ea1efd65f82b4c95faeb9de"  # 2024-02-14

CACHE = ROOT / ".refcache"
CLIPS = CACHE / "clips"
MANIFEST = CLIPS / "manifest.json"
STAGE = CACHE / "stage"
PUBLIC = ROOT / "dashboard-react" / "public"
REF_JSON = PUBLIC / "reference.json"
REF_AUDIO = PUBLIC / "reference-audio"
STATS_JSON = ROOT / "docs" / "reference-stats.json"

CLIP_IDS = ("009", "010", "011", "012")
FALLBACK_IDS = ("013", "014", "015", "016", "017", "018", "019", "020",
                "021", "022", "023", "024", "006", "007", "008")
MIC = "mic1"
GAP_S = 0.35
ALT_CEILING = 5000.0
SMALL_BLOCK = 1 << 16  # fsspec block size: keeps footer/text reads to ~64 KB each

# ---- speaker registry -------------------------------------------------------
# VCTK 0.92 speaker-info.txt, by accent and sex. Every run cross-checks each
# speaker against the mirror's own gender/accent columns.
VCTK = {
    "American": {
        "f": [294, 297, 299, 300, 301, 305, 306, 308, 310, 318,
              329, 330, 333, 339, 341, 361, 362],
        # p315 (American, M) is listed in speaker-info.txt, but VCTK 0.92 lost
        # its transcripts and the mirror has no rows for it, so it is not here.
        "m": [311, 334, 345, 360],
    },
    # General North American like the above. Added (Oct 2026) because the mirror
    # has only 4 American men; the stats file also reports American-only numbers.
    "Canadian": {
        "f": [303, 307, 312, 317, 343],
        "m": [302, 316, 363],
    },
}
DEFAULT_ACCENTS = ("American", "Canadian")

# ---- metrics ---------------------------------------------------------------
# Flat name -> (block, field) in analyze() / analyze_register() output.
FIELDS = {
    "pitch_mean_hz": ("pitch", "mean_hz"),
    "pitch_median_hz": ("pitch", "median_hz"),
    "pitch_sd_hz": ("pitch", "sd_hz"),
    "pitch_p5_hz": ("pitch", "p5_hz"),
    "pitch_p95_hz": ("pitch", "p95_hz"),
    "f1_hz": ("formants", "f1_hz"),
    "f2_hz": ("formants", "f2_hz"),
    "f3_hz": ("formants", "f3_hz"),
    "n_vowel_frames": ("formants", "n_vowel_frames"),
    "loudness_mean_db": ("intensity", "mean_db"),
    "hnr_db": ("voice_quality", "hnr_db"),
    "jitter_pct": ("voice_quality", "jitter_pct"),
    "shimmer_pct": ("voice_quality", "shimmer_pct"),
    "weight_h1a3c_db": ("weight", "h1a3c_db"),
    "weight_h1a3_db": ("weight", "h1a3_db"),
    "weight_h1a3c_sd_db": ("weight", "h1a3c_sd_db"),
    "semitones_sd": ("register", "semitones_sd"),
    "in_register_semitones_sd": ("register", "in_register_semitones_sd"),
    "creak_pct": ("register", "creak_pct"),
    "in_register_pct": ("register", "in_register_pct"),
}
# Read through the formant object, so they change with the formant ceiling.
CEILING_DEPENDENT = ("f1_hz", "f2_hz", "f3_hz", "n_vowel_frames",
                     "weight_h1a3c_db", "weight_h1a3_db", "weight_h1a3c_sd_db")
# A speaker whose single pass leaves any of these null has failed.
CORE = ("pitch_mean_hz", "f2_hz", "f3_hz", "hnr_db", "jitter_pct",
        "weight_h1a3c_db", "in_register_semitones_sd")


@dataclass(frozen=True)
class ZoneSpec:
    metric: str
    ts_name: str
    step: float  # boundaries are rounded to this
    names: tuple[str, str, str]  # low, middle, high band
    colors: tuple[str, str, str]  # zones.ts colour constants
    goal: int | None  # index of the goal band, if any
    outer: tuple[float, float]  # zones.ts outer edges, widened to fit the data
    gendered: bool  # True: low/high bands are masc/fem and need women higher


# Names, colours, goals and outer edges as zones.ts has them today, so the
# printed snippet replaces each table as-is; only the two cut points move.
ZONE_SPECS = (
    ZoneSpec("pitch_mean_hz", "PITCH_ZONES", 5, ("masc", "neutral", "fem"),
             ("MASC", "BUTTER", "FEM"), 2, (100, 260), True),
    ZoneSpec("f2_hz", "F2_ZONES", 10, ("deeper", "neutral", "bright"),
             ("MASC", "BUTTER", "FEM"), 2, (1100, 2200), True),
    ZoneSpec("f3_hz", "F3_ZONES", 10, ("deeper", "neutral", "bright"),
             ("MASC", "BUTTER", "FEM"), 2, (2100, 3400), True),
    ZoneSpec("weight_h1a3c_db", "WEIGHT_ZONES", 0.5, ("lighter", "mid", "heavier"),
             ("GROW", "BUTTER", "GROW"), None, (0, 20), False),
    ZoneSpec("hnr_db", "HNR_ZONES", 0.5, ("breathy", "typical", "clear"),
             ("GROW", "BUTTER", "FEM"), 2, (0, 35), False),
    ZoneSpec("jitter_pct", "JITTER_ZONES", 0.1, ("steady", "typical", "unsteady"),
             ("FEM", "BUTTER", "GROW"), 0, (0, 6), False),
    ZoneSpec("in_register_semitones_sd", "MELODY_ZONES", 0.1, ("flat", "natural", "lively"),
             ("GROW", "BUTTER", "FEM"), 1, (0, 7), False),
)

ZONE_RULE = (
    "Cut points at c - s/2 and c + s/2, where c = (female mean + male mean) / 2 and "
    "s = pooled within-sex SD; one value per speaker from the single-pass measurement. "
    "The middle band is one pooled SD wide, centred where the two sex means are equally "
    "far away. Cohen's d = (female mean - male mean) / s; when |d| < 1 both sex means "
    "fall inside the middle band, i.e. the metric does not separate the sexes on this "
    "corpus. Non-gendered metrics use the same cut points, with c serving as a "
    "sex-balanced centre so the female majority does not pull it."
)


def registry(accents: list[str]) -> list[dict]:
    return [{"key": f"vctk_{g}{i}", "spk": f"p{i}", "gender": g, "accent": accent}
            for accent in accents for g in ("f", "m") for i in VCTK[accent][g]]


# ---- locating + downloading clips -------------------------------------------
def shard_url(rev: str, name: str) -> str:
    return f"datasets/{REPO}@{rev}/data/{name}"


def scan_footers(fs, rev: str) -> list[dict]:
    """One record per row group of every shard: its speaker_id range and the
    compressed size of its audio and text columns. Footers only; cached."""
    import pyarrow.parquet as pq

    cache = CACHE / f"rowgroups-{rev[:12]}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    names = sorted(p.rsplit("/", 1)[-1] for p in fs.glob(shard_url(rev, "*.parquet")))
    if not names:
        raise SystemExit(f"❌ no parquet shards found at {shard_url(rev, '')}")
    groups: list[dict] = []
    for n, name in enumerate(names, 1):
        print(f"  · reading footers {n}/{len(names)}", end="\r", flush=True)
        with fs.open(shard_url(rev, name), "rb", block_size=SMALL_BLOCK) as fh:
            pf = pq.ParquetFile(fh)
            md = pf.metadata
            for rg in range(md.num_row_groups):
                g = md.row_group(rg)
                cols = {g.column(c).path_in_schema: g.column(c) for c in range(g.num_columns)}
                st = cols["speaker_id"].statistics
                if st is not None and st.has_min_max:
                    lo, hi = str(st.min), str(st.max)
                else:  # no stats in this footer: read just the speaker_id column
                    ids = pf.read_row_group(rg, columns=["speaker_id"]).column(0).to_pylist()
                    lo, hi = min(ids), max(ids)
                groups.append({
                    "shard": name, "rg": rg, "spk_min": lo, "spk_max": hi,
                    "audio_bytes": sum(c.total_compressed_size for p, c in cols.items()
                                       if p.startswith("audio")),
                    "text_bytes": sum(cols[p].total_compressed_size for p in LIGHT_COLUMNS),
                })
    print()
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(groups))
    return groups


LIGHT_COLUMNS = ("speaker_id", "file", "text_id", "text", "gender", "accent")


def locate(fs, rev: str, speakers: list[dict], groups: list[dict]) -> dict[str, dict]:
    """Pick each speaker's clips by reading only the text columns of their first
    row groups. Returns key -> {"clips": [...], "gender", "accent"} or {"error"}."""
    import pyarrow.parquet as pq

    light: dict[int, list[dict]] = {}
    text_bytes = 0

    def rows(i: int) -> list[dict]:
        nonlocal text_bytes
        if i not in light:
            g = groups[i]
            with fs.open(shard_url(rev, g["shard"]), "rb", block_size=SMALL_BLOCK) as fh:
                light[i] = pq.ParquetFile(fh).read_row_group(
                    g["rg"], columns=list(LIGHT_COLUMNS)).to_pylist()
            text_bytes += g["text_bytes"]
        return light[i]

    wanted = set(CLIP_IDS) | set(FALLBACK_IDS)
    last_id = max(wanted)
    out: dict[str, dict] = {}
    for s in speakers:
        cand = [i for i, g in enumerate(groups) if g["spk_min"] <= s["spk"] <= g["spk_max"]]
        if not cand:
            out[s["key"]] = {"error": f"{s['spk']} is not in the mirror"}
            continue
        found: dict[str, dict] = {}
        seen = None
        for i in cand:
            past = False
            for r in rows(i):
                if r["speaker_id"] != s["spk"]:
                    continue
                seen = seen or r
                if r["text_id"] > last_id:  # rows are sorted by file name
                    past = True
                    break
                name = r["file"].replace("\\", "/").rsplit("/", 1)[-1]
                if r["text_id"] in wanted and name.endswith(f"_{MIC}.flac"):
                    found[r["text_id"]] = {"id": f"{s['spk']}_{r['text_id']}",
                                           "text_id": r["text_id"], "file": name,
                                           "text": " ".join(r["text"].split()), "group": i}
            if past or all(t in found for t in CLIP_IDS):
                break
        if seen is None:
            out[s["key"]] = {"error": f"{s['spk']} has no rows in its row groups"}
            continue
        if seen["gender"].lower() != s["gender"] or seen["accent"] != s["accent"]:
            out[s["key"]] = {"error": f"mirror says {s['spk']} is {seen['gender']}/"
                                      f"{seen['accent']}, registry says "
                                      f"{s['gender'].upper()}/{s['accent']}"}
            continue
        ids = [t for t in CLIP_IDS if t in found]
        ids += [t for t in FALLBACK_IDS if t in found][:len(CLIP_IDS) - len(ids)]
        if len(ids) < len(CLIP_IDS):
            out[s["key"]] = {"error": f"only {len(ids)} of {len(CLIP_IDS)} Rainbow Passage "
                                      f"clips ({MIC}) available"}
            continue
        out[s["key"]] = {"clips": [found[t] for t in sorted(ids)],
                         "gender": seen["gender"], "accent": seen["accent"]}
    print(f"  · read text columns of {len(light)} row groups ({text_bytes / 1e6:.1f} MB)")
    return out


def fetch_audio(fs, rev: str, groups: list[dict], need: dict[int, list[tuple[str, Path]]],
                failures: dict[str, str], owner: dict[Path, str]) -> int:
    """Read the audio column of each needed row group and write the chosen
    clips. Returns compressed bytes read."""
    import pyarrow.parquet as pq

    total = 0
    for n, (i, items) in enumerate(sorted(need.items()), 1):
        g = groups[i]
        print(f"  ↓ row group {n}/{len(need)}: {g['shard'][:22]} #{g['rg']} "
              f"({g['audio_bytes'] / 1e6:.1f} MB)", flush=True)
        with fs.open(shard_url(rev, g["shard"]), "rb", block_size=SMALL_BLOCK) as fh:
            t = pq.ParquetFile(fh).read_row_group(g["rg"], columns=["file", "audio"])
        total += g["audio_bytes"]
        audio = {f.replace("\\", "/").rsplit("/", 1)[-1]: a
                 for f, a in zip(t.column("file").to_pylist(), t.column("audio").to_pylist())}
        for name, dest in items:
            data = (audio.get(name) or {}).get("bytes")
            if not data:
                failures[owner[dest]] = f"no audio bytes for {name}"
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    return total


def clip_path(key: str, clip: dict) -> Path:
    return CLIPS / key / clip["file"]


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def cached(manifest: dict, key: str, rev: str) -> bool:
    m = manifest.get(key)
    return bool(m and m["revision"] == rev and m["selection"] == list(CLIP_IDS)
                and all(clip_path(key, c).exists() for c in m["clips"]))


# ---- measuring ---------------------------------------------------------------
def concat_mp3(clips: list[Path], out: Path) -> None:
    """Clips joined with GAP_S of silence -> mono 44.1 kHz 96 kbps mp3."""
    n = len(clips)
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    inputs += ["-f", "lavfi", "-t", str(GAP_S),
               "-i", "anullsrc=channel_layout=mono:sample_rate=44100"]
    parts = [f"[{i}:a]aresample=44100,aformat=channel_layouts=mono[a{i}]" for i in range(n)]
    if n > 1:
        parts.append(f"[{n}:a]asplit={n - 1}" + "".join(f"[s{j}]" for j in range(n - 1)))
    seq = "".join(f"[a{i}]" + (f"[s{i}]" if i < n - 1 else "") for i in range(n))
    parts.append(f"{seq}concat=n={2 * n - 1}:v=0:a=1[out]")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(parts), "-map", "[out]",
                    "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "96k",
                    "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                    str(out)], check=True, capture_output=True)


def measure(path: Path, ceilings: list[float]) -> tuple[dict[float, dict], dict]:
    """One pass, as analyze.py's main() does for a take: analyze() at each
    ceiling (sharing one Intensity) plus analyze_register() at its default
    take-relative floor."""
    sound = parselmouth.Sound(str(A.to_wav_mono(path)))
    intensity = A._intensity(sound)
    by_ceiling = {c: A.analyze(sound, intensity, c) for c in ceilings}
    _, register = A.analyze_register(sound, None, intensity)
    return by_ceiling, register


def flat(metrics: dict, register: dict) -> dict:
    src = {**metrics, "register": register}
    return {k: (src.get(block) or {}).get(field) for k, (block, field) in FIELDS.items()}


def build_one(s: dict, entry: dict, ceiling: float, alt: float) -> tuple[dict, dict]:
    clips = [clip_path(s["key"], c) for c in entry["clips"]]
    mp3 = STAGE / f"{s['key']}.mp3"
    concat_mp3(clips, mp3)
    by_ceiling, register = measure(mp3, [ceiling, alt])
    head = by_ceiling[ceiling]
    per_clip = []
    for c, path in zip(entry["clips"], clips):
        m, reg = measure(path, [ceiling])
        per_clip.append({"id": c["id"], **flat(m[ceiling], reg)})
    ids = [c["id"] for c in entry["clips"]]
    voice = {
        "key": s["key"],
        "label": f"VCTK {s['spk']} ({s['accent']})",
        "gender": s["gender"],
        "accent": s["accent"],
        "source": f"VCTK 0.92 speaker {s['spk']} ({s['accent']} accent), Rainbow Passage "
                  f"sentences {', '.join(c['text_id'] for c in entry['clips'])} ({MIC}). "
                  "CC BY 4.0, https://datashare.ed.ac.uk/handle/10283/3443",
        "audio": f"reference-audio/{s['key']}.mp3",
        "measurement": "concatenated-single-pass",
        "n_clips": len(ids),
        "clips": ids,
        "formant_ceiling_hz": ceiling,
        "duration_s": head["duration_s"],
        "pitch": head["pitch"],
        "formants": head["formants"],
        "intensity": head["intensity"],
        "voice_quality": head["voice_quality"],
        "weight": head["weight"],
        "register": register,
    }
    row = {"key": s["key"], "gender": s["gender"], "accent": s["accent"], "clips": ids,
           "texts": [c["text"] for c in entry["clips"]],
           "single_pass": {str(int(c)): flat(m, register) for c, m in by_ceiling.items()},
           "per_clip": per_clip}
    return voice, row


# ---- statistics + zone rule ----------------------------------------------------
def describe(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 3),
            "sd": round(statistics.stdev(xs), 3) if len(xs) > 1 else None,
            "median": round(statistics.median(xs), 3),
            "min": round(min(xs), 3), "max": round(max(xs), 3)}


def compare(fv: list, mv: list) -> dict:
    fv, mv = [x for x in fv if x is not None], [x for x in mv if x is not None]
    out = {"f": describe(fv), "m": describe(mv)}
    if len(fv) < 2 or len(mv) < 2:
        return out
    nf, nm = len(fv), len(mv)
    s = math.sqrt(((nf - 1) * statistics.variance(fv) + (nm - 1) * statistics.variance(mv))
                  / (nf + nm - 2))
    mf, mm = statistics.fmean(fv), statistics.fmean(mv)
    out.update({
        "pooled_sd": round(s, 3),
        "cohens_d": round((mf - mm) / s, 2) if s else None,
        "f_above_highest_m": sum(x > max(mv) for x in fv),
        "m_below_lowest_f": sum(x < min(fv) for x in mv),
        **zone_bands(mf, mm, s),
    })
    return out


def zone_bands(f_mean: float, m_mean: float, pooled_sd: float) -> dict:
    """The zone rule (ZONE_RULE): cut points at c ± s/2, c = midpoint of the two
    sex means, s = pooled within-sex SD.

    Why this rule and not "33%/66% of the way between the means": that one makes
    the middle band as wide as the gap between the sexes, so a metric that barely
    separates them (F2 at 5500 Hz) gets a sliver of a neutral band and two
    confident-looking gendered bands. Tying the band width to the spread within
    each sex instead means a weak cue shows up as both sex means sitting inside
    the middle band (exactly when |d| < 1), and a strong one as clear bands
    either side. On pitch (22 F / 7 M) it lands at 140/165 Hz, next to the
    long-standing hand-set 145/165.
    """
    c = (f_mean + m_mean) / 2
    return {"centre": round(c, 3), "cut_low": round(c - pooled_sd / 2, 3),
            "cut_high": round(c + pooled_sd / 2, 3)}


def summarise(rows: list[dict], ceilings: list[float]) -> dict:
    out: dict = {"n": {g: sum(r["gender"] == g for r in rows) for g in ("f", "m")},
                 "ceilings": {}}
    for c in ceilings:
        key = str(int(c))
        out["ceilings"][key] = {
            metric: compare([r["single_pass"][key][metric] for r in rows if r["gender"] == "f"],
                            [r["single_pass"][key][metric] for r in rows if r["gender"] == "m"])
            for metric in FIELDS}
    return out


def estimator_check(rows: list[dict], ceiling: float) -> dict:
    """Single pass vs the old estimator (mean of per-clip values)."""
    key = str(int(ceiling))
    out = {}
    for metric in FIELDS:
        diffs, pc = [], {"f": [], "m": []}
        for r in rows:
            vals = [c[metric] for c in r["per_clip"] if c[metric] is not None]
            sp = r["single_pass"][key][metric]
            if vals:
                pc[r["gender"]].append(statistics.fmean(vals))
                if sp is not None:
                    diffs.append(sp - statistics.fmean(vals))
        if not diffs:
            continue
        old = compare(pc["f"], pc["m"])
        out[metric] = {"mean_diff": round(statistics.fmean(diffs), 3),
                       "mean_abs_diff": round(statistics.fmean(abs(d) for d in diffs), 3),
                       "max_abs_diff": round(max(abs(d) for d in diffs), 3),
                       "cohens_d_per_clip_mean": old.get("cohens_d")}
    return out


def snap(v: float, step: float) -> float:
    return round(round(v / step) * step, 2)


def num(v: float) -> str:
    return f"{v:g}"


def zone_ts(summary: dict, ceiling: float, label: str) -> str:
    """zones.ts-ready tables under the zone rule, one per ZoneSpec."""
    stats = summary["ceilings"][str(int(ceiling))]
    n = summary["n"]
    lines = [(f"// Generated by scripts/build_reference_set.py ({label}, formant ceiling "
              f"{num(ceiling)} Hz, n = {n['f']} F / {n['m']} M)."),
             f"// Rule: {ZONE_RULE}"]
    for z in ZONE_SPECS:
        st = stats[z.metric]
        if "cut_low" not in st:
            lines.append(f"// {z.ts_name}: not enough speakers to cut bands")
            continue
        lo, hi = snap(st["cut_low"], z.step), snap(st["cut_high"], z.step)
        a = min(z.outer[0], math.floor(min(st["f"]["min"], st["m"]["min"]) / z.step) * z.step)
        b = max(z.outer[1], math.ceil(max(st["f"]["max"], st["m"]["max"]) / z.step) * z.step)
        edges = [(a, lo), (lo, hi), (hi, b)]
        lines.append(f"// {z.metric}: F {st['f']['mean']:.4g} ± {st['f']['sd']:.3g}, "
                     f"M {st['m']['mean']:.4g} ± {st['m']['sd']:.3g}, "
                     f"pooled SD {st['pooled_sd']:.3g}, d = {st['cohens_d']}")
        if z.gendered and st["cohens_d"] is not None and st["cohens_d"] <= 0:
            lines.append(f"// ⚠️ women are not higher on {z.metric}: masc/fem labels would invert")
        lines.append(f"export const {z.ts_name}: Zone[] = [")
        for i, (f, t) in enumerate(edges):
            goal = ", goal: true" if z.goal == i else ""
            lines.append(f"  {{ from: {num(snap(f, z.step))}, to: {num(snap(t, z.step))}, "
                         f"color: {z.colors[i]}, name: \"{z.names[i]}\"{goal} }},")
        lines.append("];")
    return "\n".join(lines)


def print_tables(summary: dict, ceiling: float, alt: float) -> None:
    head, low = summary["ceilings"][str(int(ceiling))], summary["ceilings"][str(int(alt))]
    n = summary["n"]
    print(f"\n=== per-sex single-pass values (n = {n['f']} F / {n['m']} M) ===")
    print(f"{'metric':28s} {'women mean±sd [min..max]':34s} {'men mean±sd [min..max]':34s}  d")

    def row(name: str, st: dict) -> None:
        def cell(g: dict) -> str:
            if not g.get("n"):
                return "—"
            sd = f"±{g['sd']:.3g}" if g.get("sd") is not None else ""
            return f"{g['mean']:.4g}{sd} [{g['min']:.4g}..{g['max']:.4g}]"
        print(f"{name:28s} {cell(st['f']):34s} {cell(st['m']):34s}  {st.get('cohens_d', '—')}")

    for metric in FIELDS:
        row(metric, head[metric])
    for metric in CEILING_DEPENDENT:
        row(f"{metric} @{int(alt)}", low[metric])
    print(f"\n=== zone bands — {ZONE_RULE}")
    for z in ZONE_SPECS:
        for c, st in ((ceiling, head[z.metric]), (alt, low[z.metric])):
            if c == alt and z.metric not in CEILING_DEPENDENT:
                continue
            if "cut_low" not in st:
                continue
            lo, hi = snap(st["cut_low"], z.step), snap(st["cut_high"], z.step)
            print(f"  {z.metric:26s} @{int(c)}: {z.names[0]} < {num(lo)} ≤ {z.names[1]} < "
                  f"{num(hi)} ≤ {z.names[2]}   (d = {st['cohens_d']}, "
                  f"women above highest man {st['f_above_highest_m']}/{st['f']['n']})")


# ---- provenance ---------------------------------------------------------------
def git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                              text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def provenance(rev: str, accents: list[str], ceiling: float, alt: float,
               n_registered: int, voices: list[dict]) -> dict:
    ffmpeg = subprocess.run(["ffmpeg", "-version"], check=False, capture_output=True,
                            text=True).stdout
    return {
        "generated": date.today().isoformat(),
        "generator": "scripts/build_reference_set.py",
        "analyzer_commit": git("log", "-1", "--format=%H", "--", "analyze.py"),
        "analyzer_modified": bool(git("status", "--porcelain", "--", "analyze.py")),
        "analyzer_sha256": hashlib.sha256((ROOT / "analyze.py").read_bytes()).hexdigest(),
        "repo_commit": git("rev-parse", "HEAD"),
        "parselmouth_version": parselmouth.__version__,
        "praat_version": getattr(parselmouth, "PRAAT_VERSION", None),
        "numpy_version": np.__version__,
        "ffmpeg_version": ffmpeg.splitlines()[0] if ffmpeg else None,
        "corpus": "VCTK 0.92 (CSTR, University of Edinburgh; Veaux, Yamagishi & "
                  "MacDonald), CC BY 4.0, https://datashare.ed.ac.uk/handle/10283/3443",
        "dataset": f"https://huggingface.co/datasets/{REPO}",
        "dataset_revision": rev,
        "accents": accents,
        "measurement": "concatenated-single-pass",
        "formant_ceiling_hz": ceiling,
        "alt_formant_ceiling_hz": alt,
        "register_floor": "relative (analyze.py default)",
        "clips": {"text_ids": list(CLIP_IDS), "fallback_text_ids": list(FALLBACK_IDS),
                  "mic": MIC, "gap_s": GAP_S, "audio": "mono 44.1 kHz 96 kbps mp3"},
        "n_registered": n_registered,
        "n_built": len(voices),
        "n_f": sum(v["gender"] == "f" for v in voices),
        "n_m": sum(v["gender"] == "m" for v in voices),
    }


# ---- main ---------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Voice Garden reference corpus 🌸")
    ap.add_argument("--accents", default=",".join(DEFAULT_ACCENTS),
                    help=f"comma-separated, from {', '.join(VCTK)} (default: %(default)s)")
    ap.add_argument("--allow-partial", action="store_true",
                    help="write whatever built even if some speakers failed")
    ap.add_argument("--plan", action="store_true",
                    help="locate clips and report the download size, then stop")
    ap.add_argument("--formant-ceiling", type=float, default=A.FORMANT_CEILING,
                    help="headline ceiling, as takes use (default %(default)g)")
    ap.add_argument("--alt-ceiling", type=float, default=ALT_CEILING,
                    help="second ceiling reported in the stats (default %(default)g)")
    ap.add_argument("--revision", default=DATASET_REVISION, help="mirror revision")
    args = ap.parse_args()

    accents = [a.strip() for a in args.accents.split(",") if a.strip()]
    if unknown := [a for a in accents if a not in VCTK]:
        raise SystemExit(f"❌ unknown accent(s) {unknown}; known: {list(VCTK)}")
    ceiling, alt, rev = args.formant_ceiling, args.alt_ceiling, args.revision
    speakers = registry(accents)
    n_f = sum(s["gender"] == "f" for s in speakers)
    print(f"🌸 {len(speakers)} registered speakers ({n_f} F / {len(speakers) - n_f} M; "
          f"{', '.join(accents)})")

    failures: dict[str, str] = {}
    manifest = load_manifest()
    todo = [s for s in speakers if not cached(manifest, s["key"], rev)]
    if todo:
        from huggingface_hub import HfFileSystem

        fs = HfFileSystem()
        print(f"  locating {len(todo)} uncached speakers in {REPO}@{rev[:12]}")
        groups = scan_footers(fs, rev)
        picks = locate(fs, rev, todo, groups)
        need: dict[int, list[tuple[str, Path]]] = defaultdict(list)
        owner: dict[Path, str] = {}
        for s in todo:
            p = picks[s["key"]]
            if "error" in p:
                failures[s["key"]] = p["error"]
                continue
            for c in p["clips"]:
                dest = clip_path(s["key"], c)
                if not dest.exists():
                    need[c["group"]].append((c["file"], dest))
                    owner[dest] = s["key"]
        mb = sum(groups[i]["audio_bytes"] for i in need) / 1e6
        print(f"  audio to fetch: {len(need)} row groups, {mb:.0f} MB")
        if args.plan:
            for s in todo:
                p = picks[s["key"]]
                print(f"    {s['key']:12s} " + (p.get("error") or ", ".join(
                    f"{c['text_id']}@{groups[c['group']]['shard'][6:11]}#"
                    f"{groups[c['group']]['rg']}" for c in p["clips"])))
            return
        got = fetch_audio(fs, rev, groups, need, failures, owner)
        print(f"  fetched {got / 1e6:.0f} MB of audio")
        for s in todo:
            p = picks[s["key"]]
            if s["key"] not in failures:
                manifest[s["key"]] = {
                    "revision": rev, "selection": list(CLIP_IDS),
                    "gender": p["gender"], "accent": p["accent"],
                    "clips": [{k: c[k] for k in ("id", "text_id", "file", "text")}
                              for c in p["clips"]]}
        CLIPS.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, indent=1))
    elif args.plan:
        print("  every speaker is cached: nothing to download")
        return

    # ---- measure: one pass over each speaker's concatenated preview ----------
    shutil.rmtree(STAGE, ignore_errors=True)
    voices, rows = [], []
    for s in speakers:
        if s["key"] in failures:
            continue
        try:
            voice, row = build_one(s, manifest[s["key"]], ceiling, alt)
        except Exception as e:  # noqa: BLE001 — any failure is reported, then fails the run
            failures[s["key"]] = f"analysis failed: {e!r}"
            continue
        nulls = [m for m in CORE if row["single_pass"][str(int(ceiling))][m] is None]
        if nulls:
            failures[s["key"]] = f"single pass left {', '.join(nulls)} null"
            continue
        sp = row["single_pass"][str(int(ceiling))]
        print(f"  ✓ {s['key']:12s} {voice['duration_s']:5.1f}s  F0 {sp['pitch_mean_hz']:6.1f}  "
              f"F2 {sp['f2_hz']:6.0f}  F3 {sp['f3_hz']:6.0f}  weight {sp['weight_h1a3c_db']:5.1f}  "
              f"melody {sp['in_register_semitones_sd']:4.2f} st  creak {sp['creak_pct']:4.1f}%")
        voices.append(voice)
        rows.append(row)

    built_f = sum(v["gender"] == "f" for v in voices)
    print(f"\nbuilt {len(voices)} of {len(speakers)} registered speakers "
          f"({built_f}/{n_f} F, {len(voices) - built_f}/{len(speakers) - n_f} M)")
    for key, why in failures.items():
        print(f"  ✗ {key}: {why}")
    if failures and not args.allow_partial:
        raise SystemExit("❌ some speakers failed; nothing written (use --allow-partial to "
                         "write the rest)")
    if not voices:
        raise SystemExit("❌ nothing built")

    # ---- write ---------------------------------------------------------------------
    voices.sort(key=lambda v: (v["gender"], v["key"]))
    rows.sort(key=lambda r: (r["gender"], r["key"]))
    meta = provenance(rev, accents, ceiling, alt, len(speakers), voices)
    REF_AUDIO.mkdir(parents=True, exist_ok=True)
    keep = {Path(v["audio"]).name for v in voices}
    for v in voices:
        shutil.copyfile(STAGE / f"{v['key']}.mp3", REF_AUDIO / f"{v['key']}.mp3")
    for old in REF_AUDIO.glob("*.mp3"):
        if old.name not in keep:
            old.unlink()
            print(f"  removed stale preview {old.name}")
    REF_JSON.write_text(json.dumps({"meta": meta, "voices": voices}, indent=2,
                                   ensure_ascii=False) + "\n", encoding="utf-8")

    ceilings = [ceiling, alt]
    subsets = {"all": rows}
    if len(accents) > 1:
        subsets.update({a: [r for r in rows if r["accent"] == a] for a in accents})
    summaries = {name: summarise(rs, ceilings) for name, rs in subsets.items()}
    ts = {f"{name}@{int(c)}": zone_ts(summaries[name], c, name)
          for name in summaries for c in ceilings}
    STATS_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATS_JSON.write_text(json.dumps({
        "meta": meta,
        "zone_rule": ZONE_RULE,
        "summary": summaries,
        "zones_ts": ts,
        "estimator_check": estimator_check(rows, ceiling),
        "speakers": rows,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n🌸 wrote {len(voices)} voices → {REF_JSON.relative_to(ROOT)}, "
          f"previews → {REF_AUDIO.relative_to(ROOT)}, stats → {STATS_JSON.relative_to(ROOT)}")
    for name, summary in summaries.items():
        print(f"\n##### subset: {name}")
        print_tables(summary, ceiling, alt)
    print(f"\n=== zones.ts snippet (all speakers, ceiling {num(ceiling)} Hz) ===")
    print(ts[f"all@{int(ceiling)}"])


if __name__ == "__main__":
    main()
