import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import type { IncomingMessage } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import type { Plugin } from "vite";

// Dev-only POST /api/analyze — lets the Record panel run analyze.py without a
// trip to the terminal.
//
// Deliberately `apply: "serve"`: this never exists in `vite build` output, so
// the production bundle stays a pure static site with no way to execute
// anything.
//
// Running the analyzer needs no model — it's a deterministic command. The LLM
// layer in this project is only the per-take interpretation (the analyze-voice
// skill), which stays exactly where it was.
//
// Request:  raw audio bytes as the body
//           X-Take-Label / X-Take-Note  (URI-encoded)
//           X-Take-Ext                  (extension, whitelisted below)
// Response: { ok, id?, stdout, stderr }

const ALLOWED_EXT = new Set(["wav", "mp3", "m4a", "flac", "ogg", "webm", "aac"]);
const MAX_BYTES = 60 * 1024 * 1024;
const TIMEOUT_MS = 5 * 60 * 1000;
// analyze.py's output on failure is a traceback or a one-line ❌; the tail is
// the part that says what went wrong.
const MAX_STDERR = 4000;

// This middleware runs before Vite's own host check and CORS handling, so it
// does both itself: the Host must be loopback (stops DNS rebinding — a page on
// evil.example re-pointed at 127.0.0.1 — and LAN devices when started with
// --host), and any Origin must be this very page (stops other sites, including
// other apps on localhost, from posting here).
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

function hostnameOf(host: string): string {
  // "[::1]:5173" → "[::1]", "localhost:5173" → "localhost"
  const m = /^(\[[^\]]*\]|[^:]*)(?::\d+)?$/.exec(host);
  return m ? m[1] : "";
}

function isLoopbackRequest(req: IncomingMessage): boolean {
  const host = (req.headers.host ?? "").trim().toLowerCase();
  if (!LOOPBACK.has(hostnameOf(host))) return false;
  const origin = req.headers.origin;
  if (origin === undefined) return true; // curl and other non-browser clients
  try {
    return new URL(origin).host === host;
  } catch {
    return false; // includes Origin: null (sandboxed iframes, file://)
  }
}

/** Header text is attacker-adjacent (it reaches argv). Keep it boring. */
function cleanHeader(raw: string | string[] | undefined, max: number): string {
  if (typeof raw !== "string") return "";
  let s: string;
  try {
    s = decodeURIComponent(raw);
  } catch {
    s = raw;
  }
  // Strip control characters (newlines, NULs, escapes) so nothing can smuggle
  // terminal escape sequences or line breaks into argv or the console output.
  return s.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, max);
}

/** Read the whole body, or null once it passes MAX_BYTES. */
function readBody(req: IncomingMessage): Promise<Buffer | null> {
  return new Promise((done, fail) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (c: Buffer) => {
      size += c.length;
      if (size > MAX_BYTES) {
        req.removeAllListeners("data");
        req.resume(); // drain, so the 413 can still be written
        done(null);
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => done(Buffer.concat(chunks)));
    req.on("error", fail);
  });
}

/** `recorded-2026-10-16-143205.wav` — becomes the take's source_file. */
function handOffName(ext: string, d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `recorded-${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(
    d.getHours(),
  )}${p(d.getMinutes())}${p(d.getSeconds())}.${ext}`;
}

interface RunResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

function runAnalyzer(repoRoot: string, args: string[]): Promise<RunResult> {
  return new Promise((done) => {
    // No shell: argv is passed as an array, so the label and note can never
    // be interpreted as shell syntax however they're spelled.
    const child = spawn(process.env.VOICE_GARDEN_UV || "uv", ["run", "analyze.py", ...args], {
      cwd: repoRoot,
      shell: false,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    const timer = setTimeout(() => {
      stderr += `\nStopped after ${TIMEOUT_MS / 60000} minutes without finishing.`;
      child.kill();
    }, TIMEOUT_MS);
    child.on("error", (err: NodeJS.ErrnoException) => {
      clearTimeout(timer);
      done({
        code: null,
        stdout,
        stderr:
          err.code === "ENOENT"
            ? "Couldn't find `uv`. Install it, or set VOICE_GARDEN_UV to its full path and restart the dev server."
            : String(err),
      });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      done({ code, stdout, stderr });
    });
  });
}

export function analyzeEndpoint(): Plugin {
  // analyze.py picks the next id from recordings.json and writes audio/<id>,
  // analysis/<id>.json and both recordings.json copies — two runs at once would
  // pick the same id and one take would overwrite the other. So runs go one at
  // a time through this chain; a request that arrives mid-run waits its turn.
  let queue: Promise<unknown> = Promise.resolve();
  const enqueue = <T>(job: () => Promise<T>): Promise<T> => {
    const run = queue.then(job, job);
    queue = run.catch(() => undefined);
    return run;
  };

  return {
    name: "voice-garden-analyze-endpoint",
    apply: "serve",
    configureServer(server) {
      // analyze.py lives one level up from the Vite root (dashboard-react).
      // Taken from the resolved config rather than __dirname, which doesn't
      // exist in an ESM config file.
      const repoRoot = resolve(server.config.root, "..");
      server.middlewares.use("/api/analyze", async (req, res) => {
        const send = (code: number, body: unknown) => {
          if (res.headersSent) return;
          res.statusCode = code;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify(body));
        };

        if (!isLoopbackRequest(req)) {
          return send(403, { ok: false, stderr: "Only this machine can analyse takes." });
        }
        if (req.method !== "POST") return send(405, { ok: false, stderr: "POST only" });

        const label = cleanHeader(req.headers["x-take-label"], 200);
        const note = cleanHeader(req.headers["x-take-note"], 500);
        const ext = cleanHeader(req.headers["x-take-ext"], 8).toLowerCase() || "wav";
        if (!label) return send(400, { ok: false, stderr: "A label is required." });
        if (!ALLOWED_EXT.has(ext)) {
          return send(400, { ok: false, stderr: `Unsupported audio type: .${ext}` });
        }

        let body: Buffer | null;
        try {
          body = await readBody(req);
        } catch {
          return; // the client went away mid-upload
        }
        if (body === null) return send(413, { ok: false, stderr: "That recording is too large." });
        if (!body.length) return send(400, { ok: false, stderr: "Empty upload." });

        const result = await enqueue(async () => {
          // A fresh temp dir per take — analyze.py copies the audio into
          // public/audio/, so this file is only a hand-off.
          const dir = await mkdtemp(join(tmpdir(), "voice-garden-"));
          try {
            const file = join(dir, handOffName(ext));
            await writeFile(file, body);
            // --opt=value, so a label that starts with "-" isn't read as a flag
            const args = [file, `--label=${label}`];
            if (note) args.push(`--note=${note}`);
            return await runAnalyzer(repoRoot, args);
          } finally {
            await rm(dir, { recursive: true, force: true });
          }
        });

        const ok = result.code === 0;
        const id = /entry #(\d+)/.exec(result.stdout)?.[1];
        const stderr = result.stderr.trim();
        send(ok ? 200 : 500, {
          ok,
          id: ok && id ? Number(id) : undefined,
          stdout: result.stdout,
          stderr:
            stderr.length > MAX_STDERR ? `…${stderr.slice(-MAX_STDERR)}` : stderr,
        });
      });
    },
  };
}
