import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Recording, RecordingDetail } from "../types";
import type {
  AnnotationCtx,
  NoteId,
  RecordingAnnotations,
  RegionId,
  SlotFn,
  SlotId,
} from "./types";
import { RichText } from "../components/RichText";
import { ErrorBoundary } from "../components/ErrorBoundary";

// Auto-discovery: one file per recording at entries/<NNN>.tsx (zero-padded id),
// default-exporting a RecordingAnnotations. Vite resolves this glob and HMR
// hot-loads new files during `npm run dev` — no registry edits. The glob only
// matches names that start with a digit, so the `_example.tsx` template is
// never bundled.
// `import.meta.glob` is Vite-only (statically transformed at build time); the
// try/catch keeps the literal call site for Vite while letting non-Vite
// bundlers (e.g. the design-system export bundle) fall back to no entries
// instead of crashing at module load.
let entries: Record<string, () => Promise<unknown>> = {};
try {
  entries = import.meta.glob("./entries/[0-9]*.tsx");
} catch {
  entries = {};
}

const entryFile = (id: number) => `${String(id).padStart(3, "0")}.tsx`;

// "loading" → in flight · "ready" → have it · "none" → this take has none ·
// "error" → it exists but couldn't be loaded or read
export type LoadStatus = "loading" | "ready" | "none" | "error";

interface Ctx extends AnnotationCtx {
  detailStatus: LoadStatus;
  ann: RecordingAnnotations | null;
  annStatus: LoadStatus;
  entry: string; // entries/<NNN>.tsx filename for this take, for log messages
}
const AnnCtx = createContext<Ctx | null>(null);

export function useAnnotations(): Ctx {
  const c = useContext(AnnCtx);
  if (!c)
    throw new Error("useAnnotations must be used within <AnnotationsProvider>");
  return c;
}

// Detail files are fetched once per session and shared, so flipping back and
// forth between takes doesn't refetch (a page refresh still picks up a re-run
// analysis). A failed fetch is forgotten so the next visit retries.
const detailCache = new Map<string, Promise<unknown>>();
function loadDetail(path: string): Promise<unknown> {
  let p = detailCache.get(path);
  if (!p) {
    p = fetch(`${import.meta.env.BASE_URL}${path}?t=${Date.now()}`).then((r) =>
      r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)),
    );
    p.catch(() => detailCache.delete(path));
    detailCache.set(path, p);
  }
  return p;
}

// Just enough shape-checking that the contour and phrase viz can't crash on a
// truncated or hand-edited file.
function isDetail(d: unknown): d is RecordingDetail {
  if (!d || typeof d !== "object") return false;
  const o = d as Partial<RecordingDetail>;
  return (
    typeof o.register_floor_hz === "number" &&
    !!o.frames &&
    Array.isArray(o.frames.t) &&
    Array.isArray(o.frames.hz) &&
    o.frames.t.length === o.frames.hz.length &&
    (o.frames.state === undefined ||
      (Array.isArray(o.frames.state) && o.frames.state.length === o.frames.t.length)) &&
    Array.isArray(o.phrases)
  );
}

interface Loaded<T> {
  forId: number; // the take this was loaded for — never shown for another
  status: LoadStatus;
  value: T | null;
}

// Loads the active recording's heavy detail JSON + its annotations module and
// shares both via context so every section/slot reads from one place.
//
// Everything loaded is tagged with the take id it belongs to, and the context
// only ever exposes values whose tag matches the current recording. So during
// a take switch (or a burst of them) the page shows "loading" states — never
// the previous take's contour or notes beside the new take's numbers.
export function AnnotationsProvider({
  recording,
  recordings,
  children,
  initialDetail = null,
  initialAnn = null,
}: {
  recording: Recording;
  // every take (any order); used for ctx.recordings / ctx.previous. Defaults
  // to just this one, for previews that render a single take.
  recordings?: Recording[];
  children: ReactNode;
  // Optional pre-loaded data — lets callers (e.g. tests or static previews)
  // inject the detail/annotations synchronously instead of fetching. When
  // omitted, the provider fetches.
  initialDetail?: RecordingDetail | null;
  initialAnn?: RecordingAnnotations | null;
}) {
  const id = recording.id;
  const sorted = useMemo(
    () => [...(recordings ?? [recording])].sort((a, b) => a.id - b.id),
    [recordings, recording],
  );
  const previous = useMemo(() => {
    const i = sorted.findIndex((r) => r.id === id);
    return i > 0 ? sorted[i - 1] : null;
  }, [sorted, id]);

  const [det, setDet] = useState<Loaded<RecordingDetail>>({
    forId: id,
    status: "loading",
    value: null,
  });
  const [annState, setAnnState] = useState<Loaded<RecordingAnnotations>>({
    forId: id,
    status: "loading",
    value: null,
  });

  useEffect(() => {
    // No detail file configured: fall back to the injected value (null in the
    // app; non-null only when a caller pre-loaded it, e.g. a static preview).
    if (!recording.detail) {
      setDet({
        forId: id,
        status: initialDetail ? "ready" : "none",
        value: initialDetail,
      });
      return;
    }
    const path = recording.detail;
    let alive = true;
    loadDetail(path).then(
      (d) => {
        if (!alive) return;
        if (isDetail(d)) setDet({ forId: id, status: "ready", value: d });
        else {
          console.error(`[analysis] ${path} for take #${id} isn't in the expected shape`);
          setDet({ forId: id, status: "error", value: null });
        }
      },
      (err) => {
        console.error(`[analysis] couldn't load ${path} for take #${id}`, err);
        if (alive) setDet({ forId: id, status: "error", value: null });
      },
    );
    return () => {
      alive = false;
    };
  }, [id, recording.detail, initialDetail]);

  useEffect(() => {
    const file = entryFile(id);
    const loader = entries[`./entries/${file}`];
    // No entry module: fall back to the injected annotations (null in the app;
    // non-null only when pre-loaded).
    if (!loader) {
      setAnnState({
        forId: id,
        status: initialAnn ? "ready" : "none",
        value: initialAnn,
      });
      return;
    }
    let alive = true;
    loader()
      .then((m) => {
        const mod = m as { default?: RecordingAnnotations };
        if (!mod?.default || typeof mod.default !== "object")
          throw new Error("no default-exported RecordingAnnotations");
        if (alive) setAnnState({ forId: id, status: "ready", value: mod.default });
      })
      .catch((err) => {
        console.error(`[annotations] entries/${file} failed to load`, err);
        if (alive) setAnnState({ forId: id, status: "error", value: null });
      });
    return () => {
      alive = false;
    };
  }, [id, initialAnn]);

  // derive — never expose something loaded for a different take
  const detailCurrent = det.forId === id;
  const annCurrent = annState.forId === id;
  const value: Ctx = {
    recording,
    recordings: sorted,
    previous,
    detail: detailCurrent ? det.value : null,
    detailStatus: detailCurrent ? det.status : recording.detail ? "loading" : "none",
    ann: annCurrent ? annState.value : null,
    annStatus: annCurrent ? annState.status : "loading",
    entry: entryFile(id),
  };

  return <AnnCtx.Provider value={value}>{children}</AnnCtx.Provider>;
}

// Renders the authored slot for `id` if this take provides one, else
// `fallback`. The slot fn runs INSIDE an error boundary (in SlotRender), so a
// slot that throws — on call or while its JSX renders — logs the entry id and
// shows the fallback instead of blanking the page. A slot that returns null /
// undefined / false counts as "not provided". Everything goes through
// RichText, so registered emoji convert in defaults and overrides alike.
function Slot({ id, fallback }: { id: SlotId; fallback: ReactNode }) {
  const c = useAnnotations();
  const fn = c.ann?.slots?.[id];
  const plain = <RichText>{fallback}</RichText>;
  if (!fn) return plain;
  const ctx: AnnotationCtx = {
    recording: c.recording,
    detail: c.detail,
    recordings: c.recordings,
    previous: c.previous,
  };
  return (
    <ErrorBoundary
      resetKey={`${c.recording.id}:${id}`}
      fallback={plain}
      onError={(err) =>
        console.error(
          `[annotations] slot "${id}" in entries/${c.entry} threw — showing the default instead`,
          err,
        )
      }
    >
      <SlotRender fn={fn} ctx={ctx} fallback={fallback} />
    </ErrorBoundary>
  );
}

function SlotRender({
  fn,
  ctx,
  fallback,
}: {
  fn: SlotFn;
  ctx: AnnotationCtx;
  fallback: ReactNode;
}) {
  const out = fn(ctx);
  return <RichText>{out == null || out === false ? fallback : out}</RichText>;
}

// Override slot: the take's authored content for `id`, else the shared default
// (children). For woven-in notes that always have a sensible generic version.
export function Note({ id, children }: { id: NoteId; children?: ReactNode }) {
  return <Slot id={id} fallback={children ?? null} />;
}

// Insertion slot: the take's authored content for `id` if present, else
// `empty` (nothing by default). `loading` shows while the take's entry module
// is still on its way, and `failed` if it couldn't be loaded at all.
export function Region({
  id,
  empty = null,
  loading = null,
  failed,
}: {
  id: RegionId;
  empty?: ReactNode;
  loading?: ReactNode;
  failed?: ReactNode;
}) {
  const { annStatus } = useAnnotations();
  if (annStatus === "loading") return <RichText>{loading}</RichText>;
  if (annStatus === "error") return <RichText>{failed ?? empty}</RichText>;
  return <Slot id={id} fallback={empty} />;
}
