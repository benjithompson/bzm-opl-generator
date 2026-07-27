import { defineConfig } from "vitest/config";

// Its own config rather than a `test` block on vite.config.ts: what is tested
// here is the option-group declarations -- plain data in, plain data out -- so
// the run needs neither the react/tailwind plugins nor a DOM. Nothing is
// rendered, and nothing here should ever need to be.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
