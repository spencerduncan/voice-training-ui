import type { Recording } from "../types";
import { fmt } from "../zones";
import { LazyWaveformPlayer } from "./WaveformPlayer";

interface Props {
  r: Recording;
  // When given, the take's number + label become a real <button> that selects
  // this take (aria-current marks the one on show). It sits beside the player
  // rather than wrapping it, so no interactive control is nested inside
  // another, and nothing else on the card selects — scrubbing or playing a
  // take never switches the dashboard onto it.
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
    <div className={`rec${current ? " is-current" : ""}`}>
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
            <span className="rec-view" aria-hidden="true">
              {current ? "viewing" : "view ↑"}
            </span>
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
        // Presses inside the player stay inside it, so nothing an ancestor
        // listens for (now or later) can react to play / seek / volume.
        <div
          className="rec-player"
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <LazyWaveformPlayer
            src={r.audio}
            duration={r.duration_s}
            downloadName={`voice-take-${r.id}`}
            label={`take ${r.id}`}
          />
        </div>
      )}
    </div>
  );
}
