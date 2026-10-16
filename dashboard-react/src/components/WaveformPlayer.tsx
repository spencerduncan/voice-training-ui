import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WavesurferPlayer from "@wavesurfer/react";
import type WaveSurfer from "wavesurfer.js";
import Hover from "wavesurfer.js/dist/plugins/hover.esm.js";

// Themed waveform player (wavesurfer.js) — replaces the native <audio> bar so
// playback matches the pastel theme AND shows the take's actual voice shape.
// Colors come from the index.css palette; pink = played, lavender = ahead.

// ---------------------------------------------------------------------------
// Exclusive-playback coordinator — module singleton
// Only one WaveSurfer instance may play at a time. When a player starts, it
// registers itself here; any previously-registered instance is paused first.
// Calling .pause() on the old instance fires its own onPause handler, which
// flips its button back to "play" automatically — no extra wiring needed.
// ---------------------------------------------------------------------------
const audioBus = {
  current: null as WaveSurfer | null,

  /** Call when a player begins playing. Pauses any other active player. */
  activate(ws: WaveSurfer) {
    if (audioBus.current && audioBus.current !== ws) {
      audioBus.current.pause();
    }
    audioBus.current = ws;
  },

  /** Call when a player pauses or finishes. Clears the reference if it owns it. */
  deactivate(ws: WaveSurfer) {
    if (audioBus.current === ws) {
      audioBus.current = null;
    }
  },
};

function fmtTime(s: number): string {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

interface PlayerProps {
  /** path relative to public/ (e.g. "audio/001.mp3"), or null to render nothing */
  src: string | null | undefined;
  /** known duration in seconds (optional; wavesurfer reports the real one) */
  duration?: number | null;
  /** filename suggested when downloading */
  downloadName?: string;
  /** what's playing, for the play button's accessible name ("take 3") */
  label?: string;
  /** start playing as soon as the audio is decoded, and take focus — used
   *  when the lazy player below swaps itself for the real one on a tap */
  autoPlay?: boolean;
}

const PlayIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
    <path d="M4.5 2.8 L13 8 L4.5 13.2 Z" fill="currentColor" />
  </svg>
);

const DownloadLink = ({ url, name }: { url: string; name: string }) => (
  <a
    className="dl"
    href={url}
    download={name}
    aria-label="Download this voice sample"
    title="Download this voice sample"
  >
    <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 3 V14 M8 10 L12 14 L16 10"
        stroke="currentColor"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M5 17 V18.5 A1.5 1.5 0 0 0 6.5 20 H17.5 A1.5 1.5 0 0 0 19 18.5 V17"
        stroke="currentColor"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  </a>
);

// A player that costs nothing until it's wanted. Every recording card used to
// mount a full wavesurfer instance, which fetches and decodes the whole audio
// file up front — with a few dozen takes that's a lot of work for audio nobody
// pressed play on. This renders the same pill (play button, a quiet wave
// placeholder, the known duration, the download link) and only swaps in the
// real WaveformPlayer on the first tap, which then starts playing by itself.
export function LazyWaveformPlayer(props: PlayerProps) {
  const [armed, setArmed] = useState(false);
  if (!props.src) return null;
  if (armed) return <WaveformPlayer {...props} autoPlay />;
  const url = `${import.meta.env.BASE_URL}${props.src}`;
  const ext = props.src.split(".").pop() || "m4a";
  return (
    <div className="player is-idle">
      <button
        className="play"
        aria-label={props.label ? `Play ${props.label}` : "Play"}
        onClick={() => setArmed(true)}
      >
        <PlayIcon />
      </button>
      <div className="wave wave-idle" aria-hidden="true" />
      <div className="time">
        0:00 <span>/ {fmtTime(props.duration ?? 0)}</span>
      </div>
      <DownloadLink url={url} name={`${props.downloadName ?? "voice-sample"}.${ext}`} />
    </div>
  );
}

export function WaveformPlayer({
  src,
  duration,
  downloadName,
  label,
  autoPlay = false,
}: PlayerProps) {
  const [ws, setWs] = useState<WaveSurfer | null>(null);
  const [playing, setPlaying] = useState(false);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(duration ?? 0);
  const [vol, setVol] = useState(1);

  // hover-to-seek preview: a soft pink line + time tooltip following the cursor.
  // created once so it isn't re-instantiated on every render.
  const plugins = useMemo(
    () => [
      Hover.create({
        lineColor: "#ff89bb",
        lineWidth: 2,
        labelBackground: "#b06a96",
        labelColor: "#ffffff",
        labelSize: 11,
      }),
    ],
    [],
  );

  const playBtn = useRef<HTMLButtonElement>(null);
  // a lazily-mounted player replaces the button that was just tapped, so hand
  // focus to its own play button rather than dropping it on <body>
  useEffect(() => {
    if (autoPlay) playBtn.current?.focus();
  }, [autoPlay]);

  const onReady = useCallback(
    (w: WaveSurfer) => {
      setWs(w);
      setDur(w.getDuration() || duration || 0);
      w.setVolume(vol);
      setPlaying(false);
      // the tap that armed us is the user gesture; if the browser still says
      // no, the button simply stays on "play"
      if (autoPlay) w.play()?.catch(() => {});
    },
    // vol is only the starting volume — re-running this on a volume change
    // would restart playback
    [duration, autoPlay],
  );

  if (!src) return null;
  const url = `${import.meta.env.BASE_URL}${src}`;
  const ext = src.split(".").pop() || "m4a";

  return (
    <div className="player">
      <button
        ref={playBtn}
        className="play"
        aria-label={`${playing ? "Pause" : "Play"}${label ? ` ${label}` : ""}`}
        onClick={() => ws?.playPause()}
      >
        {playing ? (
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="3" y="2.5" width="3.6" height="11" rx="1.4" fill="currentColor" />
            <rect x="9.4" y="2.5" width="3.6" height="11" rx="1.4" fill="currentColor" />
          </svg>
        ) : (
          <PlayIcon />
        )}
      </button>

      <div className="wave">
        <WavesurferPlayer
          url={url}
          height={42}
          normalize
          waveColor="#bfa9e6"
          progressColor="#ff9ec5"
          cursorColor="#ff89bb"
          cursorWidth={2}
          barWidth={2.5}
          barGap={1.6}
          barRadius={3}
          plugins={plugins}
          onReady={onReady}
          onPlay={(w: WaveSurfer) => { audioBus.activate(w); setPlaying(true); }}
          onPause={(w: WaveSurfer) => { audioBus.deactivate(w); setPlaying(false); }}
          onFinish={(w: WaveSurfer) => { audioBus.deactivate(w); setPlaying(false); }}
          onTimeupdate={(_w: WaveSurfer, time: number) => setCur(time)}
        />
      </div>

      <div className="time">
        {fmtTime(cur)} <span>/ {fmtTime(dur)}</span>
      </div>

      <div className="vol">
        <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 9 H8 L13 5 V19 L8 15 H4 Z" fill="#c0a9d6" />
          <path
            d="M16 8.5 C18 10 18 14 16 15.5"
            stroke="#c0a9d6"
            strokeWidth="1.8"
            fill="none"
            strokeLinecap="round"
          />
        </svg>
        <input
          type="range"
          min={0}
          max={1}
          step={0.02}
          value={vol}
          aria-label="Volume"
          onChange={(e) => {
            const v = Number(e.target.value);
            setVol(v);
            ws?.setVolume(v);
          }}
        />
      </div>

      <DownloadLink url={url} name={`${downloadName ?? "voice-sample"}.${ext}`} />
    </div>
  );
}
