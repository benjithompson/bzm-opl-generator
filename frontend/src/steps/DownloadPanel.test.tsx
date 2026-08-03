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
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { CheckStatus, Facts, Options, PreflightCheck, PreflightOut } from "../api";
import { NO_ATTEMPT } from "../attempt";
import { fakeApi } from "../fakeApi";
import { evidenceHeader, imported } from "../preflight";
import { NOTHING_APPLIED } from "../suggestions";
import { svState } from "../sv";
import { downloadPlan } from "../token";
import { DownloadPanel } from "./DownloadPanel";

afterEach(cleanup);

const FACTS: Facts = { harbor_id: "H1", ships: [{ id: "S1" }], images: [] };
const OPTIONS: Options = { namespace: "blazemeter" };

const check = (status: CheckStatus, name: string, detail = ""): PreflightCheck =>
  ({ name, status, detail });

const out = (over: Partial<PreflightOut> = {}): PreflightOut => ({
  namespace: "blazemeter",
  summary: "3 passed, 1 warning, no failures",
  evidence: { collected_at: "2026-07-28T02:51:50Z", namespace: "some-ns",
              elsewhere: false, unreadable: [] },
  checks: [check("PASS", "location slots", "2 concurrent engine(s)")],
  suggestions: [], why_nothing: null,
  ...over,
});

function panel(preflightOut: PreflightOut, format = "manifests") {
  const read = imported("cluster-evidence.json", { schema: "x" }, preflightOut);
  return render(
    <DownloadPanel api={fakeApi()}
      bundle={{
        facts: FACTS, shipId: "S1", options: OPTIONS,
        format,
        sv: svState([], OPTIONS,
                    { func_ids: ["mockServices"], ingress_types: [],
                      backends: {} }),
        saOk: true, genErr: null, unfinished: [], goToConfigure: () => {},
        saveDir: "", setSaveDir: () => {},
      }}
      credential={{ plan: downloadPlan(null, false, "S1"), preview: null,
                    rotate: false, setRotate: () => {}, mayRotate: false }}
      attempt={NO_ATTEMPT} report={() => {}}
      preflight={{ read, busy: false, header: evidenceHeader(preflightOut),
                   importFile: () => {}, applied: NOTHING_APPLIED,
                   applySuggestion: () => {}, undoSuggestion: () => {} }}
      watch={{ available: false, on: false, setOn: () => {}, agent: null,
               status: null, mocks: null, checks: {}, check: () => {} }} />);
}

test("names the format, and offers no cluster preflight for a docker bundle", () => {
  // The control is on the configure step now, because it decides what that
  // step asks. What is left here is the name of what will be generated and the
  // way back -- and, for docker, the absence of a check that is entirely about
  // a cluster. Absent *and said so*: a block that just vanishes reads as a step
  // somebody forgot rather than one that does not apply.
  panel(out(), "docker");
  expect(screen.getByText(/Docker/)).toBeTruthy();
  expect(screen.getByText(/change it in Configure/)).toBeTruthy();
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
