import { useCallback, useEffect, useRef, useState } from "react";
import { WaveformPlayer } from "./WaveformPlayer";
import { MicIcon } from "./icons";
import { blobToWav, takeFilename } from "../wav";

// Record a take, then analyse it, without leaving the page.
//
// Recording never leaves the browser until you act: audio goes getUserMedia ->
// Blob -> AudioBuffer -> WAV entirely in memory. "Analyse" posts it to the
// dev-server endpoint (localhost, same machine); "Save" writes it to disk. It
// is never uploaded anywhere else.
//
// The analyse step runs analyze.py — a plain deterministic command, no model
// involved. The LLM layer in this project is only the per-take interpretation
// (the analyze-voice skill), which still happens in your coding agent.
//
// If the endpoint isn't there (a static build, or `vite preview`), the panel
// degrades to save-the-file plus the exact command to run.

type Phase = "idle" | "asking" | "recording" | "encoding" | "ready" | "error";

const MAX_SECONDS = 180; // a read passage is well under this; stops a runaway tab

function fmtClock(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

/** Shown after "Analyse": ok, the analyzer failed (with what it said), or the
 *  endpoint isn't there at all (static build / preview). */
type Result = { kind: "ok" | "failed" | "unreachable"; text: string };

export function Recorder({ onAnalyzed }: { onAnalyzed?: (id?: number) => void }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [take, setTake] = useState<{ url: string; name: string; seconds: number } | null>(
    null,
  );
  const [label, setLabel] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const blobRef = useRef<Blob | null>(null);
  // Held in a ref as well as state so unmount cleanup can revoke the last
  // object URL without re-running on every render.
  const urlRef = useRef<string | null>(null);

  const teardown = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    void audioCtxRef.current?.close();
    audioCtxRef.current = null;
    recorderRef.current = null;
  }, []);

  useEffect(
    () => () => {
      teardown();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [teardown],
  );

  const stop = useCallback(() => {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }, []);

  const start = useCallback(async () => {
    setError(null);
    setResult(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setPhase("error");
      setError("This browser can't record audio. Chrome, Edge or Firefox will work.");
      return;
    }

    setPhase("asking");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // Leave the voice alone — these are tuned for phone calls and would
        // reshape exactly the things we're about to measure.
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
    } catch {
      setPhase("error");
      setError(
        "No microphone came through. Check the browser let this page use it, then try again.",
      );
      return;
    }
    streamRef.current = stream;

    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AudioCtx();
    audioCtxRef.current = ctx;
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);

    const tick = () => {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3.2));
      const secs = (performance.now() - startedAtRef.current) / 1000;
      setElapsed(secs);
      if (secs >= MAX_SECONDS) stop();
      else rafRef.current = requestAnimationFrame(tick);
    };

    const chunks: Blob[] = [];
    const rec = new MediaRecorder(stream);
    recorderRef.current = rec;
    rec.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
    rec.onstop = async () => {
      teardown();
      setLevel(0);
      setPhase("encoding");
      try {
        const { wav, seconds } = await blobToWav(new Blob(chunks, { type: rec.mimeType }));
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        const url = URL.createObjectURL(wav);
        urlRef.current = url;
        blobRef.current = wav;
        setTake({ url, name: takeFilename(), seconds });
        setPhase("ready");
      } catch {
        setPhase("error");
        setError("Something went wrong turning that into a file. Worth another go.");
      }
    };

    startedAtRef.current = performance.now();
    setElapsed(0);
    rec.start();
    setPhase("recording");
    rafRef.current = requestAnimationFrame(tick);
  }, [stop, teardown]);

  // once a take is in, analysing it again would only add a duplicate entry
  const analyzed = result?.kind === "ok";

  const analyze = async () => {
    if (!blobRef.current || !label.trim() || analyzed) return;
    setAnalyzing(true);
    setResult(null);
    try {
      const res = await fetch("api/analyze", {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Take-Label": encodeURIComponent(label.trim()),
          "X-Take-Ext": "wav",
        },
        body: blobRef.current,
      });
      // A static build has no endpoint and hands back index.html, not JSON.
      const body = await res.json().catch(() => null);
      if (body?.ok) {
        setResult({ kind: "ok", text: String(body.stdout || "").trim() });
        onAnalyzed?.(typeof body.id === "number" ? body.id : undefined);
      } else if (body) {
        // The analyzer ran and stopped — show its own words, they say why.
        setResult({
          kind: "failed",
          text:
            String(body.stderr || "").trim() ||
            String(body.stdout || "").trim() ||
            `The analyzer stopped (HTTP ${res.status}) without saying why.`,
        });
      } else {
        setResult({
          kind: "unreachable",
          text: "The analyzer endpoint isn't running here (it only exists under `npm run dev`).",
        });
      }
    } catch {
      setResult({
        kind: "unreachable",
        text: "Couldn't reach the analyzer — is `npm run dev` still running?",
      });
    } finally {
      setAnalyzing(false);
    }
  };

  const command = take
    ? `uv run analyze.py "${take.name}" --label "${label.trim() || "what you were going for"}"`
    : "";
  const busy = phase === "asking" || phase === "encoding";

  return (
    <section className="rec-panel">
      <h2 className="section-title">
        <MicIcon /> Record a take
      </h2>

      <div className="rec-row">
        <button
          className={`rec-btn${phase === "recording" ? " is-live" : ""}`}
          onClick={phase === "recording" ? stop : start}
          disabled={busy}
          aria-label={phase === "recording" ? "Stop recording" : "Start recording"}
        >
          {phase === "recording" ? (
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <rect x="4" y="4" width="12" height="12" rx="2.5" fill="currentColor" />
            </svg>
          ) : (
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <circle cx="10" cy="10" r="6.5" fill="currentColor" />
            </svg>
          )}
        </button>

        <div className="rec-meta">
          {phase === "recording" ? (
            <>
              <div className="rec-clock" role="timer">
                {fmtClock(elapsed)}
              </div>
              <div
                className="rec-level"
                role="meter"
                aria-label="Input level"
                aria-valuenow={Math.round(level * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <span style={{ width: `${Math.max(2, level * 100)}%` }} />
              </div>
            </>
          ) : (
            <p className="rec-hint">
              {phase === "asking" && "waiting for the microphone…"}
              {phase === "encoding" && "tidying that into a file…"}
              {(phase === "idle" || phase === "error") &&
                "read your usual passage — same mic, same distance each time is what makes takes comparable 🌷"}
              {phase === "ready" && "nice. say what you were going for, then analyse it 💕"}
            </p>
          )}
        </div>
      </div>

      {error && <p className="rec-error">{error}</p>}

      {phase === "ready" && take && (
        <div className="rec-done">
          <WaveformPlayer
            src={take.url}
            duration={take.seconds}
            downloadName={take.name.replace(/\.wav$/, "")}
            label="your new take"
          />

          <label className="rec-label">
            <span>What were you going for?</span>
            <input
              type="text"
              value={label}
              placeholder="e.g. keeping phrase endings up"
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !analyzing && label.trim() && analyze()}
              readOnly={analyzed}
              maxLength={200}
            />
          </label>

          <div className="rec-actions">
            <button
              className="rec-analyze"
              onClick={analyze}
              disabled={analyzing || analyzed || !label.trim()}
            >
              {analyzing ? "analysing…" : analyzed ? "✓ analysed" : "✨ Analyse this take"}
            </button>
            <a className="rec-save" href={take.url} download={take.name}>
              ↓ save file
            </a>
            <button className="rec-again" onClick={start} disabled={analyzing}>
              record another
            </button>
          </div>

          {result && (
            <div
              className={`rec-result${result.kind === "ok" ? " is-ok" : " is-warn"}`}
              role={result.kind === "ok" ? "status" : "alert"}
            >
              {result.kind === "failed" && (
                <p className="rec-result-head">The analyzer couldn't finish this one — here's what it said:</p>
              )}
              <pre>{result.text}</pre>
              {result.kind === "ok" ? (
                <p>Added below — your take is on the dashboard now. 🌸</p>
              ) : (
                <p>
                  You can still do it by hand: save the file, then run{" "}
                  <code>{command}</code> from the project folder.
                </p>
              )}
            </div>
          )}

          <p className="rec-note">
            Nothing was uploaded anywhere — analysing runs on this machine, and the
            recording only exists in this tab until you save or analyse it.
          </p>
        </div>
      )}
    </section>
  );
}
