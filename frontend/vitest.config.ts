import { defineConfig } from "vitest/config";

// Its own config rather than a `test` block on vite.config.ts: most of what is
// tested here is plain data in, plain data out -- the option-group
// declarations, the session snapshot, the token plan -- and none of it needs
// tailwind or a browser.
//
// So `node` stays the default and a DOM is opted into per file, by the
// `@vitest-environment jsdom` docblock at the top of App.test.tsx: a page test
// costs a jsdom to start, and making every plain module pay it would be a
// slower suite for nothing. `.tsx` is included because a test that drives the
// page has to render it, which is JSX.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
