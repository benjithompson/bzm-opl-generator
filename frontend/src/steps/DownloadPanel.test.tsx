// @vitest-environment jsdom
//
// The preflight panel's header, which is where the imported file stops being a
// file and starts being an answer.
//
// Both facts on it are doctor's -- the sentence over the verdict list, and
// whether the file describes the namespace being preflighted -- and the point
// of these tests is that the panel renders them rather than reaching the same
// conclusions again from the same numbers. So the payloads below say things a
// browser recomposing them could not: a summary that does not follow from the
// verdicts beside it, and a mismatch flagged over two namespaces that agree.
//
// The panel is driven through its own props rather than through App: what is
// under test is the rendering, and App's own effects are pinned in App.test.tsx.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { Facts, Options, PreflightOut } from "../api";
import { NO_ATTEMPT } from "../attempt";
import { fakeApi } from "../fakeApi";
// The served answer, from the one builder for it: the panel that renders it,
// the snapshot that stores it and the page that restores it all want one, and
// three sets of defaults for one schema is the divergence fixtures.ts exists to
// stop.
import { preflightOut as out } from "../fixtures";
import { evidenceHeader, fromSnapshot, imported, PreflightState } from "../preflight";
import { NOTHING_APPLIED } from "../suggestions";
import { svState } from "../sv";
import { downloadPlan } from "../token";
import { DownloadPanel } from "./DownloadPanel";

afterEach(cleanup);

const FACTS: Facts = { harbor_id: "H1", ships: [{ id: "S1" }], images: [] };
const OPTIONS: Options = { namespace: "blazemeter" };

/** What the last crane-hook switch wrote, so a test can assert the panel writes
 *  the option rather than only rendering a row. */
let hookWrote: boolean | null = null;

function panel(preflightOut: PreflightOut, format = "manifests",
               read: PreflightState =
                 imported("cluster-evidence.json", { schema: "x" }, preflightOut)) {
  hookWrote = null;
  return render(
    <DownloadPanel api={fakeApi()}
      bundle={{
        facts: FACTS, shipId: "S1", options: OPTIONS,
        format,
        sv: svState([], OPTIONS,
                    { func_ids: ["mockServices"], ingress_types: [],
                      backends: {} }),
        saOk: true, genErr: null, blanks: [], unfinished: [],
        goToConfigure: () => {},
      }}
      credential={{ plan: downloadPlan(null) }}
      attempt={NO_ATTEMPT} report={() => {}}
      preflight={{ read, busy: false, header: evidenceHeader(preflightOut),
                   importFile: () => {}, applied: NOTHING_APPLIED,
                   applySuggestion: () => {}, undoSuggestion: () => {},
                   // Null for docker, as App works out from the served ignored
                   // table -- crane-hook is a Pod, and there is no cluster.
                   craneHook: format === "docker" ? null
                     : { on: false, set: (v) => { hookWrote = v; } } }}
      watch={{ available: false, on: false, setOn: () => {}, agent: null,
               status: null, mocks: null, checks: {}, check: () => {} }} />);
}

test("offers no cluster preflight for a docker bundle", () => {
  // Absent *and said so*: a block that just vanishes reads as a step somebody
  // forgot rather than one that does not apply.
  panel(out(), "docker");
  expect(screen.queryByText(/Preflight the target cluster/)).toBeNull();
  expect(screen.getByText(/No cluster preflight for a docker bundle/)).toBeTruthy();
  // ...and it is offered for the two formats that have one.
  cleanup();
  panel(out());
  expect(screen.getByText(/Preflight the target cluster/)).toBeTruthy();
});

test("summarises the verdict list in the words it was served", () => {
  // Nothing about "9 passed" follows from the one check below. That is the
  // test: the sentence is doctor's, and the panel is not counting.
  panel(out({ summary: "9 passed, 2 warnings, 1 failure — a test would not "
                       + "start on this location as configured" }));
  expect(screen.getByText(/9 passed, 2 warnings, 1 failure/)).toBeTruthy();
  expect(screen.getByText(/would not start on this location/)).toBeTruthy();
});

test("says the file describes another namespace when it was told so", () => {
  // The two namespaces on screen agree. Whether that is a mismatch is decided
  // where the verdict about it is written, and this renders that decision.
  panel(out({ evidence: { collected_at: null, namespace: "blazemeter",
                          elsewhere: true, unreadable: [] } }));
  expect(screen.getByText(/every namespaced verdict below describes/)).toBeTruthy();
});

test("says a restored answer is no longer being re-judged", () => {
  // A verdict is about a cluster at a moment, and the header already dates the
  // collection. This is the other staleness, and only a restore has it: the
  // page no longer holds the file, so the verdicts have stopped following the
  // configuration they are judged against. Unsaid, a restored list looks exactly
  // like one re-judged on the last keystroke.
  const o = out();
  panel(o, "manifests", fromSnapshot("cluster-evidence.json", o));
  // ...and the file is named in the sentence itself, not only in the header
  // above it, because picking it again is the fix the sentence offers.
  expect(screen.getByText(/not being re-judged/).textContent)
    .toContain("cluster-evidence.json");
});

test("says nothing of the sort about verdicts it can still re-judge", () => {
  // The same panel over an imported file, which is re-judged on every option
  // change. A page that says this either way is a page saying nothing.
  panel(out());
  expect(screen.queryByText(/not being re-judged/)).toBeNull();
});

test("says nothing about a mismatch it was not told about", () => {
  // ...and the other way round: two namespaces that differ, and no mismatch
  // reported, because reporting one is not this page's call.
  panel(out({ namespace: "blazemeter",
              evidence: { collected_at: null, namespace: "their-ns",
                          elsewhere: false, unreadable: [] } }));
  expect(screen.queryByText(/every namespaced verdict below describes/)).toBeNull();
  // The header still says which namespace the file describes -- that is a fact
  // about the file, and it is on screen whatever the mismatch says.
  expect(screen.getByText("their-ns")).toBeTruthy();
});

test("ships the cluster check with the bundle, from this step", () => {
  // #130: the switch was on the configure step, among the options that shape
  // the agent. It shapes none of it -- the same agent is deployed either way --
  // and what it is about is the cluster this bundle is going to, which is the
  // question this step asks. It has to write the option from here, because the
  // download that carries it is here.
  panel(out());
  fireEvent.click(screen.getByRole("switch", { name: /Ship the check/ }));
  expect(hookWrote).toBe(true);
});

test("offers no cluster check where the bundle cannot carry one", () => {
  // A docker bundle has no cluster to run a Pod in, and App answers that off
  // the served ignored table rather than this panel re-deriving it.
  panel(out(), "docker");
  expect(screen.queryByRole("switch", { name: /Ship the check/ })).toBeNull();
});
