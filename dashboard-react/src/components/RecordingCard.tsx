import type { Recording } from "../types";
import { fmt } from "../zones";
import { WaveformPlayer } from "./WaveformPlayer";

interface Props {
  r: Recording;
  // When given, the take's number + label become a real <button> that selects
  // this take. It sits beside the player rather than wrapping it, so no
  // interactive control is nested inside another.
  onSelect?: () => void;
  current?: boolean;
}

export function RecordingCard({ r, onSelect, current }: Props) {
  const heading = (
    <>
      <span className="num">{r.id}</span>
      <span className="label-txt">{r.label}</span>
    </>
  );
  return (
    <div className="rec">
      <div className="top">
        {onSelect ? (
          <button
            type="button"
            className="rec-pick"
            onClick={onSelect}
            aria-current={current ? "true" : undefined}
            aria-label={`Show take ${r.id} (${r.label}) in the sections above`}
          >
            {heading}
          </button>
        ) : (
          <div className="rec-pick">{heading}</div>
        )}
        <span className="date">
          {r.date} · {fmt(r.duration_s, "s")}
        </span>
      </div>
      <div className="metrics">
        <div className="chip">
          <b>{fmt(r.pitch.mean_hz)}</b>
          <span>pitch Hz</span>
        </div>
        <div
          className="chip"
          title={
            r.pitch.p5_hz !== undefined
              ? "typical span: 5th to 95th percentile of your pitch"
              : "lowest to highest pitch (older analysis)"
          }
        >
          <b>
            {fmt(r.pitch.p5_hz ?? r.pitch.min_hz)}–{fmt(r.pitch.p95_hz ?? r.pitch.max_hz)}
          </b>
          <span>{r.pitch.p5_hz !== undefined ? "typical span Hz" : "range Hz"}</span>
        </div>
        <div className="chip">
          <b>{fmt(r.formants.f2_hz)}</b>
          <span>F2 Hz</span>
        </div>
        <div className="chip">
          <b>{fmt(r.intensity.mean_db)}</b>
          <span>loud dB</span>
        </div>
        <div className="chip">
          <b>{fmt(r.pitch.sd_hz)}</b>
          <span>variab Hz</span>
        </div>
        <div className="chip">
          <b>{fmt(r.voice_quality.hnr_db)}</b>
          <span>HNR dB</span>
        </div>
      </div>
      {r.note && <div className="note">📝 {r.note}</div>}
      {r.audio && (
        <WaveformPlayer
          src={r.audio}
          duration={r.duration_s}
          downloadName={`voice-take-${r.id}`}
        />
      )}
    </div>
  );
}
