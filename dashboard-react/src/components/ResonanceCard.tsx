import type { Recording, ReferenceVoice } from "../types";
import { F2_ZONES, F3_ZONES, FEM, zoneOf, fmt, type Zone } from "../zones";
import { FormantGauge } from "./FormantGauge";
import { Note } from "../annotations/AnnotationsProvider";
import type { MetricKey } from "../metrics";

interface ResonanceCardProps {
  r: Recording;
  onExpand?: (key: MetricKey, rect: DOMRect) => void;
  references?: ReferenceVoice[];
}

// How many reference women (with a measured value) fall OUTSIDE the pink band,
// counted from the loaded reference.json so the sentence below can't drift
// from the data or from zones.ts.
function womenOutsideFem(
  refs: ReferenceVoice[],
  zones: Zone[],
  get: (v: ReferenceVoice) => number | null | undefined,
): { outside: number; total: number } {
  const vals = refs
    .filter((v) => v.gender === "f")
    .map(get)
    .filter((n): n is number => n !== null && n !== undefined);
  const outside = vals.filter((n) => zoneOf(zones, n)?.color !== FEM).length;
  return { outside, total: vals.length };
}

export function ResonanceCard({ r, onExpand, references = [] }: ResonanceCardProps) {
  const f = r.formants;
  const onF2 = womenOutsideFem(references, F2_ZONES, (v) => v.formants?.f2_hz);
  const onF3 = womenOutsideFem(references, F3_ZONES, (v) => v.formants?.f3_hz);
  const z2 = zoneOf(F2_ZONES, f.f2_hz);
  const z3 = zoneOf(F3_ZONES, f.f3_hz);
  // overall lean from the two reliable cues (F2, F3)
  const score = [z2, z3].reduce(
    (s, z) => s + (z?.name === "bright" ? 1 : z?.name === "deeper" ? -1 : 0),
    0,
  );

  let summary: JSX.Element;
  if (score >= 1)
    summary = (
      <>
        ✨ Your resonance <b>leans bright &amp; light</b> — this is the cue that
        makes a voice read feminine beyond pitch. Lovely!
      </>
    );
  else if (score <= -1)
    summary = (
      <>
        🌱 Your resonance <b>leans deeper</b> right now — a good place to put
        your attention, since it's usually the most responsive lever after pitch.
      </>
    );
  else
    summary = (
      <>
        💫 Your resonance sits in a <b>neutral</b> zone — nudging brightness up
        will help it read lighter.
      </>
    );

  return (
    <div className="stat resonance">
      <p className="res-desc">
        Formants are your voice's <b>brightness / vocal-tract size</b>. Think of
        pitch as <i>which note</i> you sing, and resonance as{" "}
        <i>the size of the room</i> it echoes in. Higher = a smaller, brighter,
        lighter space (reads feminine). Measured as the <b>median over your vowel
        nuclei</b> (the loud, steady cores of syllables) — not every frame — so
        consonants and glides don't muddy it.
      </p>
      <FormantGauge
        name="F2"
        desc="main brightness cue"
        value={f.f2_hz}
        zones={F2_ZONES}
        lo={1100}
        hi={2000}
        metricKey="f2"
        onExpand={onExpand}
      />
      <FormantGauge
        name="F3"
        desc="supports brightness"
        value={f.f3_hz}
        zones={F3_ZONES}
        lo={2100}
        hi={3400}
        metricKey="f3"
        onExpand={onExpand}
      />
      <div className="res-summary">
        <Note id="note.resonance">{summary}</Note>
      </div>
      {/* The bands are drawn between the group averages of a small cis reference
          set, so on their own they read as a target. They aren't one: formants are set partly by
          how you shape the vocal tract and partly by how long it is, and the
          second part isn't something practice moves. Stating that plainly — with
          this project's OWN reference spread as the evidence — turns a verdict
          back into context, and heads off the failure mode where a user reads a
          settled number as insufficient effort and pushes into strain. */}
      <div className="res-context">
        🌿 <b>Where the bands come from.</b> They're drawn between the average man
        and the average woman in{" "}
        {references.length > 0
          ? `the ${references.length} reference voices`
          : "a small reference set"}
        , so each group's <i>average</i> lands in its own band — but plenty of
        real voices don't
        {onF2.total > 0 && onF3.total > 0 ? (
          <>
            : {onF2.outside} of the {onF2.total} women here read <i>neutral</i>{" "}
            or <i>deeper</i> on F2, and {onF3.outside} of {onF3.total} on F3
          </>
        ) : null}
        . Resonance is partly something you shape and partly the size
        of what you're shaping, so if this number finds a level and stays there,
        that's information — not something to force. It's one cue of several:
        melody and how you land phrase endings are still doing real
        work. 💗
      </div>
      <div className="res-f1">
        ℹ️ <b>F1 = {fmt(f.f1_hz)} Hz.</b> This one mostly reflects which{" "}
        <i>vowel</i> you're on (mouth openness), so it's not a reliable gender
        cue by itself — shown for completeness.
      </div>
    </div>
  );
}
