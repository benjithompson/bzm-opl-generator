import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
// A build records what it was built from, beside the page it built (#237).
// The rule is in that file, and `bzm_opl_gen/ui_build.py` is the reader.
import { recordSourceFingerprint } from "./scripts/source-fingerprint.mjs";

export default defineConfig({
  plugins: [react(), tailwindcss(), recordSourceFingerprint()],
  build: {
    outDir: "../bzm_opl_gen/ui_dist",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
