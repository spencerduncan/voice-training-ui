/* ============================================================================
 * EXAMPLE / TEMPLATE — how to author a recording's annotations.
 *
 * This file is NOT rendered or bundled: the loader (../AnnotationsProvider)
 * only globs `entries/[0-9]*.tsx`, i.e. files named after a recording's
 * zero-padded id (001, 002…). It exists purely as a copy-me reference, and it
 * still type-checks (tsc covers it), so it can't rot.
 *
 * TO AUTHOR A REAL ONE: copy this to `entries/00N.tsx` (matching the recording's
 * id), then fill in only the slots you want. Everything you DON'T provide falls
 * back to the dashboard's built-in defaults — that's what keeps this DRY (the
 * page layout lives once; you only write the bespoke bits per take).
 *
 * The full workflow (run analyzer → interpret → author this file) is documented
 * in the `analyze-voice` skill (.claude/skills/analyze-voice/SKILL.md). Read it.
 * ========================================================================== */

import type { RecordingAnnotations } from "../types";
// Reusable building blocks. Grow this library (../lib) when an insight needs a
// new viz that could be reused — don't bury reusable charts in one entry.
import { InsightCard, Drill, PhraseEndingStrip, StatCompare } from "../lib";
import { fmt, goalOf, PITCH_ZONES } from "../../zones";

/* Each slot is `(ctx) => ReactNode`, where ctx = { recording, detail,
 * recordings, previous }.
 * - recording: the full Recording (pitch/formants/voice_quality/intensity/
 *   weight/register summary). See ../../types.ts.
 * - detail: the heavy per-frame/per-phrase analysis (frames, phrases, summary),
 *   loaded from public/analysis/<id>.json. May be null while loading — guard it.
 * - recordings: every take, sorted by id. previous: the take before this one,
 *   or null for the first.
 *
 * READ EVERY NUMBER FROM ctx (or a goal from ../../zones) — never type a
 * measured value or a target into the JSX. Returning null means "not
 * provided": the default note / empty state shows instead. A slot that throws
 * is caught, logged with this file's name, and replaced by the default.
 *
 * AVAILABLE SLOTS (typed — see SlotId in ../types.ts; a typo won't build):
 *   Override notes (replace a built-in woven note; default shows if you omit it):
 *     note.pitch · note.loudness · note.weight · note.resonance · note.register
 *   Freeform insertion regions (render nothing unless you fill them):
 *     region.top · region.afterLatest · region.afterResonance ·
 *     region.afterRegister · region.insights (the main one) · region.bottom
 *   Need a slot somewhere new? Add its id to SlotId, place a <Note id> /
 *   <Region id> in the layout (App.tsx or a component) and document it here.
 *
 * CONVENTIONS (non-negotiable — see CLAUDE.md):
 *   Color rule: blue (MASC) = masculine / sustained drop out of register ONLY,
 *   never a generic "bad". pink (FEM) = good/feminine, butter = neutral, GROW =
 *   neutral "room to grow" for non-gendered skill gaps (and creak). Components
 *   use the automatic JSX runtime — do NOT import React. Tone: warm, specific,
 *   "a compass not a judge". Emoji you type here render through RichText.
 */
const annotations: RecordingAnnotations = {
  slots: {
    // 1) Override a woven note, personalized for this take:
    "note.pitch": ({ recording, previous }) => (
      <>
        {fmt(recording.pitch.mean_hz)} Hz this take
        {previous && <> (take #{previous.id} was {fmt(previous.pitch.mean_hz)} Hz)</>} — say
        something specific and encouraging about where their pitch landed 💕
      </>
    ),

    // 2) The main insight, dropped into the dedicated insights section:
    "region.insights": ({ recording, detail, previous }) => {
      const reg = recording.register;
      if (!reg || !detail) return null; // guard: detail loads async
      const pitchGoal = goalOf(PITCH_ZONES);
      return (
        <InsightCard
          title="One clear, clockable headline for this take 🎯"
          subtitle="More specific than generic advice — name the moment + the fix."
          badges={[
            `${fmt(reg.phrases_landed_pct)}% of phrase endings held`,
            reg.in_register_semitones_sd == null
              ? "melody: not enough steady speech yet"
              : `melody ${fmt(reg.in_register_semitones_sd)} st`,
          ]}
        >
          <p>
            A short, honest paragraph interpreting the numbers like a phonetician
            — what's working, what the lead issue is, and why.
          </p>

          {/* Reusable viz from ../lib. PhraseEndingStrip plots each phrase
              ending vs the register floor; StatCompare contrasts this take
              with a goal or another take. */}
          <PhraseEndingStrip detail={detail} />
          {pitchGoal && (
            <StatCompare
              bTitle="goal"
              rows={[
                {
                  label: "pitch",
                  a: recording.pitch.mean_hz,
                  b: pitchGoal.from,
                  unit: "Hz",
                  better: "up",
                },
              ]}
            />
          )}
          {previous && (
            <StatCompare
              bTitle={`take #${previous.id}`}
              rows={[
                {
                  label: "endings that held",
                  a: reg.phrases_landed_pct,
                  b: previous.register?.phrases_landed_pct ?? null,
                  unit: "%",
                  better: "up",
                },
              ]}
            />
          )}

          <Drill title="A concrete thing to practice">
            One specific, doable exercise tied to the finding.
          </Drill>

          <p style={{ marginTop: 12, fontSize: 12.5, color: "var(--ink-soft)" }}>
            End warm and forward-looking — a finishing move, not a rebuild. 🌸
          </p>
        </InsightCard>
      );
    },
  },
};

export default annotations;
