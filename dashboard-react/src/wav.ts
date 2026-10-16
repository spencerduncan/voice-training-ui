// Mono 16-bit WAV encoding, done in the browser.
//
// Why encode here instead of just saving what MediaRecorder gives us: the
// recorder hands back webm/opus (or mp4 on Safari), and analyze.py only skips
// its ffmpeg conversion step for `.wav` — everything else needs ffmpeg on the
// PATH. Producing a WAV directly means a take can go straight into the
// analyzer on a machine with no ffmpeg at all.
//
// Mono matters too, not just the container: analyze.py passes `.wav` input
// straight to parselmouth without the `-ac 1` downmix it applies to other
// formats, so a stereo file would reach Praat as multichannel. We downmix here.

/** Average all channels of an AudioBuffer down to one Float32Array. */
export function toMono(buffer: AudioBuffer): Float32Array {
  const { numberOfChannels, length } = buffer;
  if (numberOfChannels === 1) return buffer.getChannelData(0).slice();

  const out = new Float32Array(length);
  for (let ch = 0; ch < numberOfChannels; ch++) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < length; i++) out[i] += data[i];
  }
  for (let i = 0; i < length; i++) out[i] /= numberOfChannels;
  return out;
}

/** Wrap mono float samples in a 16-bit PCM WAV container. */
export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const bytesPerSample = 2;
  const dataBytes = samples.length * bytesPerSample;
  const buf = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buf);

  const ascii = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };

  ascii(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true); // file size - 8
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // format = PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true); // byte rate
  view.setUint16(32, bytesPerSample, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, "data");
  view.setUint32(40, dataBytes, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    // Clamp before scaling: values outside [-1, 1] would wrap and click.
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += bytesPerSample;
  }
  return new Blob([buf], { type: "audio/wav" });
}

/** Decode whatever the recorder produced, then re-encode it as a mono WAV. */
export async function blobToWav(blob: Blob): Promise<{ wav: Blob; seconds: number }> {
  const AudioCtx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AudioCtx();
  try {
    const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
    return {
      wav: encodeWav(toMono(decoded), decoded.sampleRate),
      seconds: decoded.duration,
    };
  } finally {
    void ctx.close();
  }
}

/** `voice-garden-2026-08-04-1432.wav` — sorts chronologically, no collisions. */
export function takeFilename(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `voice-garden-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(
    d.getDate(),
  )}-${p(d.getHours())}${p(d.getMinutes())}.wav`;
}
