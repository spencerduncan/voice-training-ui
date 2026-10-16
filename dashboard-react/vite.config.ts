import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { analyzeEndpoint } from "./vite-plugin-analyze";

// Your takes live in public/ and are served by this dev server, so only this
// origin may read them: no CORS for other sites (even other localhost apps),
// and Cross-Origin-Resource-Policy stops another page embedding the audio or
// JSON. Never start it with --host while personal takes exist — that serves
// them to your whole network.
const sameOriginOnly = { "Cross-Origin-Resource-Policy": "same-origin" };

// base: './' makes asset URLs relative, so dist/ works from any sub-path when
// served over http (`npm run preview`, or any static host). It does NOT make
// dist/index.html work from file:// — browsers block the fetch() of the JSON.
//
// analyzeEndpoint() is dev-only (apply: "serve") — it lets the Record panel run
// analyze.py without a terminal trip. `vite build` output is unaffected and
// stays a pure static site with no way to execute anything.
export default defineConfig({
  base: "./",
  plugins: [react(), analyzeEndpoint()],
  server: {
    cors: false,
    headers: sameOriginOnly,
  },
  preview: {
    cors: false,
    headers: sameOriginOnly,
  },
});
