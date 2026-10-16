export interface Point {
  id: number; // take number
  date: string; // the take's date as the analyzer wrote it (YYYY-MM-DD)
  y: number | null | undefined;
}

// A shaded horizontal zone band (e.g. the pitch zones behind the pitch trend).
export interface ChartBand {
  from: number;
  to: number;
  color: string;
}

interface Props {
  points: Point[];
  color?: string;
  band?: [number, number];
  bandColor?: string;
  bands?: ChartBand[]; // multi-zone background (matches the stat-card zone colors)
  unit?: string; // for the tooltip ("Hz", "st", "%")
  percent?: boolean; // a 0–100 measure: the axis never runs past 100 or below 0
  activeId?: number | null; // the take on show above, drawn filled
}

const DAY_MS = 86_400_000;

// "2026-10-16" → whole days since the epoch (UTC, so no timezone drift)
function dayOf(date: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!m) return null;
  const t = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isFinite(t) ? t / DAY_MS : null;
}

function dayLabel(day: number): string {
  return new Date(day * DAY_MS).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

// A "nice" tick step (1, 2, 2.5 or 5 × 10ⁿ) giving about `target` ticks.
function niceStep(span: number, target = 3): number {
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  return (n < 1.5 ? 1 : n < 2.25 ? 2 : n < 3.5 ? 2.5 : n < 7.5 ? 5 : 10) * mag;
}

// Tiny SVG trend chart. X is the take's DATE (takes recorded the same day are
// spread evenly within that day, so they don't stack), with a handful of date
// ticks instead of a label per take. Y gets "nice" round ticks. If any date
// can't be read, it falls back to plain take order with thinned "#id" labels.
export function LineChart({
  points,
  color = "#b06a96",
  band,
  bandColor,
  bands,
  unit = "",
  percent = false,
  activeId = null,
}: Props) {
  const W = 300;
  const H = 130;
  const pad = { l: 34, r: 12, t: 12, b: 24 };
  const vals = points
    .map((p) => p.y)
    .filter((v): v is number => v !== null && v !== undefined);
  if (!vals.length) return <div className="cap">no data yet</div>;

  // ---- y domain ----
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (band) {
    min = Math.min(min, band[0]);
    max = Math.max(max, band[1]);
  }
  if (bands && bands.length) {
    for (const b of bands) {
      min = Math.min(min, b.from);
      max = Math.max(max, b.to);
    }
  }
  const range = max - min || Math.abs(max) * 0.1 || 1;
  // Pad the axis only when there are no explicit zone bands; with bands we want
  // the plot to fill the zone extent so the colored regions read cleanly.
  if (!bands || !bands.length) {
    min -= range * 0.15;
    max += range * 0.15;
  }
  if (min === max) {
    min -= 1;
    max += 1;
  }
  if (percent) {
    min = Math.max(0, min);
    max = Math.min(100, max);
  }
  const step = niceStep(max - min);
  const yTicks: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step)
    yTicks.push(Number(v.toFixed(6)));
  const tickText = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

  // ---- x positions ----
  const days = points.map((p) => dayOf(p.date));
  const byDate = days.every((d) => d !== null);
  let xs: number[];
  let xMin: number;
  let xMax: number;
  if (byDate) {
    // same-day takes share the day evenly, in take order
    const groups = new Map<number, number[]>();
    points.forEach((_, i) => {
      const d = days[i] as number;
      groups.set(d, [...(groups.get(d) ?? []), i]);
    });
    xs = new Array(points.length);
    for (const [d, idxs] of groups) {
      idxs
        .sort((a, b) => points[a].id - points[b].id)
        .forEach((i, k) => {
          xs[i] = d + (k + 1) / (idxs.length + 1) - 0.5;
        });
    }
    const dayList = [...groups.keys()];
    xMin = Math.min(...dayList) - 0.5;
    xMax = Math.max(...dayList) + 0.5;
  } else {
    xs = points.map((_, i) => i);
    xMin = -0.5;
    xMax = points.length - 0.5;
  }
  const iw = W - pad.l - pad.r;
  const x = (v: number) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * iw;
  const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (H - pad.t - pad.b);

  // ---- x ticks: a few dates (or thinned take numbers) ----
  let xTicks: { at: number; text: string }[];
  if (byDate) {
    const uniq = [...new Set(days as number[])].sort((a, b) => a - b);
    const want = 4;
    const picked =
      uniq.length <= want
        ? uniq
        : [
            ...new Set(
              Array.from({ length: want }, (_, i) => {
                const target = uniq[0] + ((uniq[uniq.length - 1] - uniq[0]) * i) / (want - 1);
                return uniq.reduce((best, d) =>
                  Math.abs(d - target) < Math.abs(best - target) ? d : best,
                );
              }),
            ),
          ];
    xTicks = picked.map((d) => ({ at: d, text: dayLabel(d) }));
  } else {
    const every = points.length > 12 ? Math.ceil(points.length / 6) : 1;
    xTicks = points
      .map((p, i) => ({ at: i, text: `#${p.id}` }))
      .filter((_, i) => i % every === 0 || i === points.length - 1);
  }
  const tickX = (v: number) => Math.min(W - pad.r - 14, Math.max(pad.l + 14, x(v)));

  const valid = points
    .map((p, i) => ({ ...p, xv: xs[i] }))
    .filter((p): p is Point & { xv: number; y: number } => p.y !== null && p.y !== undefined)
    .sort((a, b) => a.xv - b.xv);
  const path = valid
    .map((p, k) => `${k ? "L" : "M"}${x(p.xv).toFixed(1)},${y(p.y).toFixed(1)}`)
    .join(" ");
  const dotR = valid.length > 12 ? 2.6 : 4;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%">
      {bands?.map((b, k) => {
        const top = y(Math.min(b.to, max));
        const bottom = y(Math.max(b.from, min));
        return bottom > top ? (
          <rect
            key={`band${k}`}
            x={pad.l}
            y={top}
            width={iw}
            height={bottom - top}
            fill={b.color}
            opacity="0.16"
          />
        ) : null;
      })}
      {band && (
        <rect
          x={pad.l}
          y={y(Math.min(band[1], max))}
          width={iw}
          height={y(Math.max(band[0], min)) - y(Math.min(band[1], max))}
          fill={bandColor || "#ffb6d5"}
          opacity="0.22"
          rx="4"
        />
      )}
      {yTicks.map((v) => (
        <g key={`y${v}`}>
          <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="#efe6f0" strokeWidth="1" />
          <text
            x={pad.l - 6}
            y={(y(v) + 3).toFixed(1)}
            fontSize="9"
            fill="#756384"
            textAnchor="end"
          >
            {tickText(v)}
          </text>
        </g>
      ))}
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {valid.map((p) => (
        <circle
          key={p.id}
          cx={x(p.xv).toFixed(1)}
          cy={y(p.y).toFixed(1)}
          r={p.id === activeId ? dotR + 1.2 : dotR}
          fill={p.id === activeId ? color : "#fff"}
          stroke={color}
          strokeWidth="2.5"
        >
          <title>
            #{p.id}
            {p.date ? ` · ${p.date}` : ""}: {p.y}
            {unit ? ` ${unit}` : ""}
          </title>
        </circle>
      ))}
      {xTicks.map((t) => (
        <text
          key={`x${t.at}`}
          x={tickX(t.at).toFixed(1)}
          y={H - 7}
          fontSize="9"
          fill="#756384"
          textAnchor="middle"
        >
          {t.text}
        </text>
      ))}
    </svg>
  );
}
