import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Your takes live in public/ and are served by this dev server, so only this
// origin may read them: no CORS for other sites (even other localhost apps),
// and Cross-Origin-Resource-Policy stops another page embedding the audio or
// JSON. Never start it with --host while personal takes exist — that serves
// them to your whole network.
const sameOriginOnly = { "Cross-Origin-Resource-Policy": "same-origin" };

// base: './' makes asset URLs relative, so dist/ works from any sub-path when
// served over http (`npm run preview`, or any static host). It does NOT make
// dist/index.html work from file:// — browsers block the fetch() of the JSON.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    cors: false,
    headers: sameOriginOnly,
  },
  preview: {
    cors: false,
    headers: sameOriginOnly,
  },
});
