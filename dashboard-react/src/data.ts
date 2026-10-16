// The fetch boundary: everything that comes off disk (recordings.json,
// reference.json) passes through here before any component sees it. A take
// missing a whole object (say, an old entry with no `formants`) gets null-valued
// defaults instead of crashing a card; an entry that can't be a take at all is
// dropped and reported, so the page can say "take #N couldn't be read" rather
// than going blank.
import type {
  Formants,
  Intensity,
  Pitch,
  Recording,
  ReferenceVoice,
  Register,
  VoiceQuality,
  Weight,
} from "./types";

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const obj = (v: unknown): Obj => (isObj(v) ? v : {});
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
// optional fields stay `undefined` when absent, so "this entry predates the
// field" remains distinguishable from "the analyzer measured nothing"
const optNum = (v: unknown): number | null | undefined =>
  v === undefined ? undefined : num(v);
const str = (v: unknown, fallback = ""): string =>
  typeof v === "string" ? v : fallback;

function pitchOf(v: unknown): Pitch {
  const o = obj(v);
  return {
    mean_hz: num(o.mean_hz),
    median_hz: num(o.median_hz),
    min_hz: num(o.min_hz),
    max_hz: num(o.max_hz),
    range_hz: num(o.range_hz),
    sd_hz: num(o.sd_hz),
    p5_hz: optNum(o.p5_hz),
    p95_hz: optNum(o.p95_hz),
  };
}

function formantsOf(v: unknown): Formants {
  const o = obj(v);
  return {
    f1_hz: num(o.f1_hz),
    f2_hz: num(o.f2_hz),
    f3_hz: num(o.f3_hz),
    formant_ceiling_hz: optNum(o.formant_ceiling_hz),
    n_vowel_frames: optNum(o.n_vowel_frames),
  };
}

function voiceQualityOf(v: unknown): VoiceQuality {
  const o = obj(v);
  return {
    hnr_db: num(o.hnr_db),
    jitter_pct: num(o.jitter_pct),
    shimmer_pct: num(o.shimmer_pct),
  };
}

function intensityOf(v: unknown): Intensity {
  const o = obj(v);
  return { mean_db: num(o.mean_db), min_db: num(o.min_db), max_db: num(o.max_db) };
}

function weightOf(v: unknown): Weight | undefined {
  if (!isObj(v)) return undefined;
  return {
    h1a3c_db: num(v.h1a3c_db),
    h1a3_db: optNum(v.h1a3_db),
    tilt_db_khz: optNum(v.tilt_db_khz),
    h1a3c_sd_db: optNum(v.h1a3c_sd_db),
    n_frames: optNum(v.n_frames),
  };
}

// A register block without a numeric floor can't be drawn or described, so
// it's treated as missing (the section then says to re-run the analyzer).
function registerOf(v: unknown): Register | undefined {
  if (!isObj(v)) return undefined;
  const floor = num(v.floor_hz);
  if (floor === null) return undefined;
  const mode = v.floor_mode === "relative" || v.floor_mode === "absolute" ? v.floor_mode : undefined;
  return {
    floor_hz: floor,
    floor_mode: mode,
    floor_st_below_median: optNum(v.floor_st_below_median),
    median_hz: optNum(v.median_hz),
    in_register_pct: num(v.in_register_pct),
    creak_pct: optNum(v.creak_pct),
    below_floor_pct: optNum(v.below_floor_pct),
    n_crashes: optNum(v.n_crashes),
    semitones_sd: num(v.semitones_sd),
    in_register_semitones_sd: num(v.in_register_semitones_sd),
    melody_frames: optNum(v.melody_frames),
    onset_sub_pct: num(v.onset_sub_pct),
    mid_sub_pct: num(v.mid_sub_pct),
    offset_sub_pct: num(v.offset_sub_pct),
    phrases_landed_pct: num(v.phrases_landed_pct),
    n_phrases: num(v.n_phrases) ?? 0,
  };
}

export type Normalized =
  | { ok: true; recording: Recording }
  | { ok: false; label: string; reason: string };

// One raw entry → a Recording with every object present, or a reason it
// couldn't be read. `index` is its position in the file (for entries so broken
// they have no id to name them by).
export function normalizeRecording(raw: unknown, index: number): Normalized {
  if (!isObj(raw))
    return { ok: false, label: `entry ${index + 1}`, reason: "it isn't a take record" };
  const id = num(raw.id);
  if (id === null || !Number.isInteger(id) || id < 0)
    return { ok: false, label: `entry ${index + 1}`, reason: "it has no take number" };
  return {
    ok: true,
    recording: {
      id,
      label: str(raw.label, `take #${id}`),
      note: str(raw.note),
      date: str(raw.date),
      source_file: str(raw.source_file),
      audio: typeof raw.audio === "string" && raw.audio ? raw.audio : null,
      detail: typeof raw.detail === "string" && raw.detail ? raw.detail : undefined,
      duration_s: num(raw.duration_s),
      pitch: pitchOf(raw.pitch),
      formants: formantsOf(raw.formants),
      voice_quality: voiceQualityOf(raw.voice_quality),
      intensity: intensityOf(raw.intensity),
      weight: weightOf(raw.weight),
      register: registerOf(raw.register),
    },
  };
}

export interface Unreadable {
  label: string; // "take #4" or "entry 3"
  reason: string;
}

export interface LoadedRecordings {
  recordings: Recording[]; // sorted by id, duplicates removed
  unreadable: Unreadable[];
}

// Fetch + validate recordings.json. Resolves with the good takes and a list
// of the ones left out; rejects only when the file as a whole is unusable.
// A missing file (404, or the dev server's HTML fallback) means "no takes
// yet", not an error — a fresh checkout hasn't run the analyzer.
export async function loadRecordings(): Promise<LoadedRecordings> {
  const res = await fetch(`${import.meta.env.BASE_URL}recordings.json?t=${Date.now()}`);
  if (res.status === 404) return { recordings: [], unreadable: [] };
  if (!res.ok) throw new Error(`the server answered HTTP ${res.status}`);
  if ((res.headers.get("content-type") ?? "").includes("text/html"))
    return { recordings: [], unreadable: [] };
  let data: unknown;
  try {
    data = JSON.parse(await res.text());
  } catch {
    throw new Error("recordings.json isn't valid JSON");
  }
  if (!Array.isArray(data)) throw new Error("recordings.json should be a list of takes");

  const seen = new Set<number>();
  const recordings: Recording[] = [];
  const unreadable: Unreadable[] = [];
  data.forEach((raw, i) => {
    const n = normalizeRecording(raw, i);
    if (!n.ok) {
      unreadable.push({ label: n.label, reason: n.reason });
    } else if (seen.has(n.recording.id)) {
      unreadable.push({
        label: `take #${n.recording.id}`,
        reason: "another entry already uses that number",
      });
    } else {
      seen.add(n.recording.id);
      recordings.push(n.recording);
    }
  });
  recordings.sort((a, b) => a.id - b.id);
  return { recordings, unreadable };
}

// reference.json is optional: anything wrong with it just means no reference
// ticks. Entries without a label or a gender are skipped.
export async function loadReferences(): Promise<ReferenceVoice[]> {
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}reference.json?t=${Date.now()}`);
    if (!res.ok) return [];
    const data: unknown = await res.json();
    // reference.json is `{ meta, voices }` since the Oct 2026 rebuild; older
    // copies were a bare array. Accept both.
    const list: unknown = Array.isArray(data) ? data : obj(data).voices;
    if (!Array.isArray(list)) return [];
    return list.flatMap((raw): ReferenceVoice[] => {
      if (!isObj(raw) || typeof raw.label !== "string") return [];
      if (raw.gender !== "f" && raw.gender !== "m") return [];
      const p = obj(raw.pitch);
      const f = obj(raw.formants);
      const vq = obj(raw.voice_quality);
      return [
        {
          label: raw.label,
          gender: raw.gender,
          source: str(raw.source),
          audio: typeof raw.audio === "string" ? raw.audio : undefined,
          pitch: { mean_hz: num(p.mean_hz), sd_hz: num(p.sd_hz) },
          formants: { f2_hz: num(f.f2_hz), f3_hz: num(f.f3_hz) },
          intensity: { mean_db: num(obj(raw.intensity).mean_db) },
          voice_quality: { hnr_db: num(vq.hnr_db), jitter_pct: num(vq.jitter_pct) },
          weight: { h1a3c_db: num(obj(raw.weight).h1a3c_db) },
        },
      ];
    });
  } catch {
    return [];
  }
}
