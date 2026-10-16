import { FRAME_STATE, type RecordingDetail } from "../types";
import { MASC, FEM, CONTOUR, PITCH_ZONES, goalOf } from "../zones";

interface Props {
  detail: RecordingDetail;
  femThreshold?: number; // Hz that reads feminine (for the soft pink band)
}

type Kind = "in" | "dip" | "crash" | "creak";

const STROKE: Record<Kind, { color: string; width: number; dash?: string }> = {
  in: { color: CONTOUR.inRegister, width: 2.4 },
  dip: { color: CONTOUR.dip, width: 3 },
  crash: { color: CONTOUR.crash, width: 3 },
  creak: { color: CONTOUR.creak, width: 3.6, dash: "3 3" },
};

// Per-frame colour. Current analyses carry frames.state (1 in register, 2 a
// brief dip under the floor, 3 a sustained drop, 4 creak); older ones don't,
// so those fall back to the plain floor test — and then anything under the
// floor is drawn as a drop, which is all an old analysis can say.
function kindOf(state: number | undefined, hz: number, floor: number): Kind {
  if (state === undefined) return hz < floor ? "crash" : "in";
  if (state === FRAME_STATE.crash) return "crash";
  if (state === FRAME_STATE.dip) return "dip";
  if (state === FRAME_STATE.creak) return "creak";
  return "in";
}

// The pitch contour over time, with the register floor drawn across it. Only
// SUSTAINED drops are painted blue (masculine register); brief dips are butter
// and creak is a dashed neutral grey, so a bit of vocal fry at a phrase end
// never reads as a fall. Phrase boundaries are marked with soft dividers and a
// dot under each phrase ending (pink = the ending held, blue = it dropped).
export function ContourChart({
  detail,
  femThreshold = goalOf(PITCH_ZONES)?.from ?? PITCH_ZONES[PITCH_ZONES.length - 1].from,
}: Props) {
  const W = 900;
  const H = 240;
  const pad = { l: 40, r: 14, t: 14, b: 40 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;

  const floor = detail.register_floor_hz;
  const { t, hz, state } = detail.frames;
  const dur = detail.duration_s || t[t.length - 1] || 1;

  const voiced = hz.filter((v): v is number => v != null);
  const maxHz = Math.max(220, ...voiced, femThreshold) * 1.05;
  const minHz = Math.min(80, floor - 20, ...voiced);

  const x = (time: number) => pad.l + (time / dur) * iw;
  const y = (f: number) => pad.t + (1 - (f - minHz) / (maxHz - minHz)) * ih;

  // Build contour segments, one per run of the same kind, so each can be
  // stroked its own colour. A run breaks on unvoiced gaps too. When the kind
  // changes mid-run the new segment starts at the previous point, so the line
  // stays continuous instead of leaving a one-frame gap at every change — but
  // only across a small step: creak often reads at half the pitch, and a
  // bridge over that jump would draw a solid in-register line plunging
  // through the floor.
  const BRIDGE_ST = 3;
  type Seg = { kind: Kind; pts: [number, number][] };
  const segs: Seg[] = [];
  let cur: Seg | null = null;
  let last: [number, number] | null = null; // previous voiced point in this run
  let lastHz = 0;
  for (let i = 0; i < hz.length; i++) {
    const f = hz[i];
    if (f == null || state?.[i] === FRAME_STATE.unvoiced) {
      cur = null;
      last = null;
      continue;
    }
    const kind = kindOf(state?.[i], f, floor);
    const pt: [number, number] = [x(t[i]), y(f)];
    if (!cur || cur.kind !== kind) {
      const bridge = last && Math.abs(12 * Math.log2(f / lastHz)) < BRIDGE_ST;
      cur = { kind, pts: bridge && last ? [last] : [] };
      segs.push(cur);
    }
    cur.pts.push(pt);
    last = pt;
    lastHz = f;
  }
  const toPath = (pts: [number, number][]) =>
    pts.map((p, k) => `${k ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  // draw drops on top so they never hide under a neighbouring segment
  const order: Kind[] = ["creak", "dip", "in", "crash"];
  const drawn = [...segs].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));

  const yTicks = [minHz, floor, femThreshold, maxHz].filter(
    (v, i, a) => a.indexOf(v) === i && v <= maxHz && v >= minHz,
  );

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      className="contour"
      role="img"
      aria-label={`Pitch contour over ${detail.phrases.length} phrases, with the register floor at ${floor} Hz`}
    >
      {/* feminine band (soft pink, above the fem threshold) */}
      <rect
        x={pad.l}
        y={y(maxHz)}
        width={iw}
        height={y(femThreshold) - y(maxHz)}
        fill={FEM}
        opacity={0.12}
      />
      {/* under-the-floor band (blue: out-of-register territory) */}
      <rect
        x={pad.l}
        y={y(floor)}
        width={iw}
        height={y(minHz) - y(floor)}
        fill={MASC}
        opacity={0.18}
      />

      {/* register floor line */}
      <line
        x1={pad.l}
        x2={W - pad.r}
        y1={y(floor)}
        y2={y(floor)}
        stroke="#7c9fd6"
        strokeWidth={1.5}
        strokeDasharray="5 4"
      />
      <text x={W - pad.r} y={y(floor) - 5} fontSize="10" fill="#5e7fb8" textAnchor="end">
        register floor {floor} Hz
      </text>

      {/* phrase dividers */}
      {detail.phrases.map((p, k) => (
        <line
          key={`d${k}`}
          x1={x(p.end)}
          x2={x(p.end)}
          y1={pad.t}
          y2={pad.t + ih}
          stroke="#e7ddef"
          strokeWidth={1}
        />
      ))}

      {/* y ticks */}
      {yTicks.map((v, k) => (
        <text key={`y${k}`} x={pad.l - 6} y={y(v) + 3} fontSize="9" fill="#756384" textAnchor="end">
          {Math.round(v)}
        </text>
      ))}

      {/* the contour */}
      {drawn.map((s, k) => {
        const st = STROKE[s.kind];
        return (
          <path
            key={`s${k}`}
            d={toPath(s.pts)}
            fill="none"
            stroke={st.color}
            strokeWidth={st.width}
            strokeDasharray={st.dash}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        );
      })}

      {/* per-phrase ending dots (under each phrase's end) */}
      {detail.phrases.map((p, k) => (
        <circle
          key={`l${k}`}
          cx={x(p.end)}
          cy={pad.t + ih + 14}
          r={4.5}
          fill={p.ended_in_register ? FEM : MASC}
          stroke="#fff"
          strokeWidth={1.5}
        >
          <title>
            phrase {k + 1}: ended at {p.offset_hz} Hz —{" "}
            {p.ended_in_register ? "the ending held 💕" : "the ending dropped out of register"}
          </title>
        </circle>
      ))}
      <text x={pad.l} y={H - 6} fontSize="10" fill="#756384">
        time →
      </text>
      <text x={W - pad.r} y={H - 6} fontSize="10" fill="#756384" textAnchor="end">
        ● dots = whether each phrase ending held
      </text>
    </svg>
  );
}
