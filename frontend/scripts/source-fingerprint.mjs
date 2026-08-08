// What this build was built from (#237), written beside the built page.
//
// The server serves `ui_dist` out of a checkout whose sources move under it,
// and until this file the only way to ask whether the two matched was to
// compare timestamps -- which answers a different question. Any `git pull` or
// branch switch rewrites the files it touches, so a fast-forward through two
// merged pull requests reported a stale page with the built output
// byte-identical. A hash of the bytes cannot say that.
//
// The reader is `bzm_opl_gen/ui_build.py`, so the covered set and the hashing
// rule are stated twice, in two languages, by necessity. Nothing here keeps
// them equal: `tests/test_ui_build.py` recomputes the fingerprint in Python and
// asserts the committed one matches, so the two halves disagreeing is a failing
// test rather than a page that quietly reports the wrong thing.
//
// A vite plugin rather than a step after `vite build` in package.json: the out
// directory is then the one vite resolved rather than a second copy of the
// path, and every build records, including a `vite build` somebody runs by
// hand. `emptyOutDir` wipes the directory first, so a build that fails leaves
// no fingerprint at all -- which reads as "not recorded", the honest answer.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

/** Written into the file and checked when it is read. A page fingerprinted by
 *  an older rule cannot be compared with one computed by this one. */
export const ALGORITHM = "sha256-paths-v1";

/** The file, beside index.html. */
export const FINGERPRINT_FILE = "source-fingerprint.json";

// Named files outside src/ that the page is compiled from. Not package.json,
// its lockfile or tsconfig.json: a dependency bump changes the output, but it
// changes it under a command somebody just ran, and covering the toolchain
// would flip the fingerprint on every `npm install`.
const EXTRA_SOURCES = ["index.html", "vite.config.ts"];

// A test reaches no bundle, so a test edit must not ask for a rebuild.
const isTest = (name) => name.endsWith(".test.ts") || name.endsWith(".test.tsx");

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

function walk(dir, base, into) {
  for (const name of readdirSync(dir).sort()) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, base, into);
    else if (!isTest(name)) into.push(full.slice(base.length + 1).split("\\").join("/"));
  }
  return into;
}

/** The covered files, as sorted POSIX paths relative to `frontend`. Sorted and
 *  relative because the fingerprint is over the list as well as over the bytes:
 *  a rename with no edit changes the answer, a different checkout path does
 *  not. */
export function sourceFiles(frontend) {
  const found = walk(join(frontend, "src"), frontend, []);
  for (const name of EXTRA_SOURCES) {
    try {
      if (statSync(join(frontend, name)).isFile()) found.push(name);
    } catch {
      // Absent is not covered. The Python reader skips a missing one too, and
      // a frontend without index.html does not build in the first place.
    }
  }
  return found.sort();
}

/** The fingerprint of the sources on disk. */
export function sourceFingerprint(frontend) {
  const whole = createHash("sha256");
  for (const rel of sourceFiles(frontend)) {
    // Path and content both, separated by a byte no path may contain, so that
    // concatenation cannot make two different file lists agree.
    whole.update(`${rel}\0${sha256(readFileSync(join(frontend, rel)))}\n`);
  }
  return whole.digest("hex");
}

/** The plugin. `apply: "build"` because a dev server builds nothing: a
 *  fingerprint written there would describe a page that was never emitted. */
export function recordSourceFingerprint() {
  let outDir = null;
  let frontend = null;
  return {
    name: "bzm-opl-gen:source-fingerprint",
    apply: "build",
    configResolved(config) {
      frontend = config.root;
      outDir = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      // After the bundle is written, so `emptyOutDir` cannot take it away.
      writeFileSync(
        join(outDir, FINGERPRINT_FILE),
        `${JSON.stringify({
          algorithm: ALGORITHM,
          fingerprint: sourceFingerprint(frontend),
        }, null, 2)}\n`,
      );
    },
  };
}
