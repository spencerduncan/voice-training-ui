import type { ReactNode } from "react";
import type { Recording, RecordingDetail } from "../types";

// Every slot the layout offers. Typed as a union so an entry that names a slot
// that doesn't exist (a typo, or one that was renamed) is a build error instead
// of content that silently never renders. Adding a slot = add its id here and
// place a <Note id> / <Region id> in the layout.
export type NoteId =
  | "note.pitch"
  | "note.loudness"
  | "note.weight"
  | "note.resonance"
  | "note.register";

export type RegionId =
  | "region.top"
  | "region.afterLatest"
  | "region.afterResonance"
  | "region.afterRegister"
  | "region.insights"
  | "region.bottom";

export type SlotId = NoteId | RegionId;

// Context every slot render-fn receives.
export interface AnnotationCtx {
  recording: Recording;
  detail: RecordingDetail | null; // null while the heavy analysis loads
  // every take, sorted by id ascending (so trends and comparisons read from
  // data rather than typed-in numbers)
  recordings: Recording[];
  // the take just before this one in id order, or null for the first take
  previous: Recording | null;
}

// Return null (or nothing) to mean "not provided": a Note then shows its
// default and a Region its empty state.
export type SlotFn = (ctx: AnnotationCtx) => ReactNode;

// One file per recording (src/annotations/entries/<NNN>.tsx) default-exports
// this. The agent fills in ONLY the slots it wants personalized for that take;
// every other spot falls back to the shared, data-driven default. This keeps
// per-recording content DRY: the page layout lives once, and only the bespoke
// bits are authored and persisted — so switching to an old recording brings its
// notes back without ever copy-pasting the whole page.
export interface RecordingAnnotations {
  slots?: Partial<Record<SlotId, SlotFn>>;
}
