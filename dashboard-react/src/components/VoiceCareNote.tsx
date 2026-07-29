// A permanent, always-visible aside about looking after your voice.
// Deliberately NOT inside the collapsible cheat sheet — a stop rule that
// nobody unfolds isn't a stop rule. Styled as a soft lavender note (the
// .res-f1 / .gloss-note register), never a red warning: this is care, not
// a disclaimer, and it shouldn't scare anyone out of practising.
export function VoiceCareNote() {
  return (
    <aside className="care-note">
      <h3>🌿 Taking care of your voice</h3>
      <ul>
        <li>
          If something hurts, feels tight, feels like a lump in your throat, or
          your voice suddenly changes — stop the session there. It keeps until
          tomorrow.
        </li>
        <li>
          Warm up, take breaks, keep sessions short. Little and often is kinder
          than long and hard.
        </li>
        <li>
          Hoarseness, vocal tiredness, or a top end that's gone missing for{" "}
          <b>more than 4 weeks</b> is worth taking to a speech-language
          pathologist (SLP) or laryngologist — ideally one who works with trans
          clients. That's a check-in, not a verdict.
        </li>
        <li>
          Voice Garden only listens to a recording. It can't see inside your
          throat, and it can't tell you whether something's hurt.
        </li>
        <li>
          If your throat and a number here disagree, believe your throat. 💗
        </li>
      </ul>
    </aside>
  );
}
