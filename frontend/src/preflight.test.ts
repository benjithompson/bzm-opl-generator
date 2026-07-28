import { describe, expect, it } from "vitest";
import { CheckStatus, PreflightCheck, PreflightOut } from "./api";
import {
  countByStatus, imported, NO_PREFLIGHT, readEvidence, rechecked, refused,
  STATUS_STYLE, verdictLine, worstStatus,
} from "./preflight";

// What the panel decides on its own, as data in and data out: how a verdict
// list reads at a glance, what a file that is not JSON says, and what an import
// leaves behind when it is refused. The verdicts themselves are doctor's and are
// tested against the command in tests/test_cluster_evidence.py -- nothing here
// re-decides one.

const check = (status: CheckStatus, name: string, detail = ""): PreflightCheck =>
  ({ name, status, detail });

/** A thin file's verdict list: provenance first, then the sections it could not
 *  read. Shaped like the degraded fixture the python suite uses. */
const THIN: PreflightCheck[] = [
  check("WARN", "cluster evidence",
    "cluster read by scripts/bzm-cluster-evidence.sh at 2026-07-28T02:51:50Z "
    + "for namespace some-ns, not from a live cluster; could not read nodes"),
  check("PASS", "location slots", "2 concurrent engine(s)"),
  check("WARN", "capacity", "the nodes could not be read"),
  check("WARN", "egress", "egress was not probed from inside the cluster"),
];

const out = (checks: PreflightCheck[]): PreflightOut =>
  ({ namespace: "blazemeter", checks });

describe("reading the verdict list", () => {
  it("counts every status, including the ones nothing has", () => {
    expect(countByStatus(THIN)).toEqual({ PASS: 1, WARN: 3, FAIL: 0 });
    expect(countByStatus([])).toEqual({ PASS: 0, WARN: 0, FAIL: 0 });
  });

  it("summarises in the same terms the command does", () => {
    expect(verdictLine(THIN)).toContain("1 passed");
    expect(verdictLine(THIN)).toContain("3 warnings");
    expect(verdictLine(THIN)).toContain("no failures");
  });

  it("counts of one are not pluralised", () => {
    expect(verdictLine([check("FAIL", "capacity")])).toContain("1 failure");
  });

  it("says a test would not start only when something failed", () => {
    // The sentence doctor's own report ends on. Saying it over a list of
    // warnings would make an unreadable file look like a rejection.
    expect(verdictLine(THIN)).not.toContain("would not start");
    expect(verdictLine([...THIN, check("FAIL", "capacity")]))
      .toContain("would not start");
  });

  it("takes its tone from the worst verdict in the list", () => {
    expect(worstStatus(THIN)).toBe("WARN");
    expect(worstStatus([...THIN, check("FAIL", "capacity")])).toBe("FAIL");
    expect(worstStatus([check("PASS", "location slots")])).toBe("PASS");
    // Nothing imported yet is not a pass.
    expect(worstStatus([])).toBeNull();
  });
});

describe("telling the three apart", () => {
  const statuses: CheckStatus[] = ["PASS", "WARN", "FAIL"];

  it("styles every status doctor can return", () => {
    for (const s of statuses) expect(STATUS_STYLE[s]).toBeTruthy();
  });

  it("gives each one its own colour, on the badge and in the summary", () => {
    for (const key of ["badge", "text"] as const) {
      const used = statuses.map((s) => STATUS_STYLE[s][key]);
      expect(new Set(used).size).toBe(statuses.length);
    }
  });

  it("labels each one in words as well", () => {
    // Colour alone is not a distinction: this list is read on a projector as
    // often as on a laptop, and a WARN that is only amber is a PASS to anyone
    // who cannot see the difference.
    const labels = statuses.map((s) => STATUS_STYLE[s].label);
    expect(new Set(labels).size).toBe(statuses.length);
    for (const l of labels) expect(l.trim()).not.toBe("");
  });
});

describe("picking a file", () => {
  it("hands back what the file parsed to", () => {
    const r = readEvidence("cluster-evidence.json", '{"schema": "x", "raw": {}}');
    expect(r).toEqual({ doc: { schema: "x", raw: {} } });
  });

  it("says which file, and that it is not JSON at all", () => {
    const r = readEvidence("notes.txt", "collected on tuesday, ask dave");
    expect("error" in r && r.error).toContain("notes.txt");
    expect("error" in r && r.error).toContain("not valid JSON");
    // And what was wanted instead, since nothing else on the page says it.
    expect("error" in r && r.error).toContain("bzm-cluster-evidence.sh");
  });

  it("does not decide what counts as evidence", () => {
    // Only whether it is JSON. Which documents are evidence is one judgement,
    // it lives in doctor, and a second copy here is how the browser starts
    // refusing files the command accepts.
    expect(readEvidence("array.json", "[1, 2]")).toEqual({ doc: [1, 2] });
  });
});

describe("what an import leaves behind", () => {
  const good = imported("cluster-evidence.json", { schema: "x" }, out(THIN));

  it("replaces whatever was there, and clears the last complaint", () => {
    const after = imported("newer.json", { schema: "y" },
      out([check("PASS", "capacity")]));
    expect(after.file).toBe("newer.json");
    expect(after.doc).toEqual({ schema: "y" });
    expect(after.error).toBeNull();
  });

  it("keeps the imported file when a later one is refused", () => {
    // The verdicts on screen are true of the file that produced them, so a
    // refusal adds a message and takes nothing away -- otherwise picking the
    // wrong file loses the preflight you already had.
    const after = refused(good, "unrecognised cluster evidence: found schema 'x/2'");
    expect(after.file).toBe(good.file);
    expect(after.doc).toEqual(good.doc);
    expect(after.out).toBe(good.out);
    expect(after.error).toContain("unrecognised cluster evidence");
  });

  it("has nothing to keep before anything was imported", () => {
    const after = refused(NO_PREFLIGHT, "notes.txt is not valid JSON");
    expect(after.out).toBeNull();
    expect(after.doc).toBeNull();
    expect(after.error).toContain("notes.txt");
  });

  it("re-runs against the same file, and drops a stale message", () => {
    // Every option edit re-runs the preflight over the file already imported;
    // it must not look like a second import, and a message from the last
    // attempt must not outlive the verdicts that replace it.
    const stale = refused(good, "engine_cpu_limit: 'two' is not a quantity");
    const after = rechecked(stale, out([check("FAIL", "capacity")]));
    expect(after.file).toBe(good.file);
    expect(after.doc).toEqual(good.doc);
    expect(after.out?.checks).toHaveLength(1);
    expect(after.error).toBeNull();
  });
});
