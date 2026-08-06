// @vitest-environment jsdom
//
// The page's effects, driven through the seam.
//
// Every effect below has gone wrong once, and none of the failures is visible
// in a type or in a review: each needs two things outstanding at the same time,
// or a timer that has not fired yet. `deferred` is what holds an answer open;
// vi's clock is what holds a debounce or a poll interval open. The four the
// page documents in prose are all pinned here -- the account-capacity guard
// (the slower answer landing under the newer account's name), the session
// restore ordering, the preview debounce and its dependency on the save
// folder, and the status poll's target.
//
// Driven through the real controls rather than by poking state: switching
// account is the menu at the foot of the drawer, and a test that reached
// setAccountId directly would keep passing if that control stopped calling it.
//
// Under fake timers, nothing from testing-library that waits may be used:
// `waitFor` and every `findBy*` poll on a real interval, and this jsdom's
// testing-library only recognises jest's clock, so it would spin against a
// clock nothing is advancing. The tests below therefore set up under the real
// clock and install the fake one at the point the behaviour under test starts
// -- which for the poll has to be before the interval is created, or it is a
// real interval vi will never advance.
import {
  act, cleanup, fireEvent, render, screen, waitFor, within,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import {
  AgentStatus, Api, Capacity, CapacityPlan, Facts, FuncIdChoice,
  FuncIdVocabulary, Functionality, Location, Options, Ship, TokenRequest,
} from "./api";
import { deferred, fakeApi } from "./fakeApi";
// The served docker-ignored table, from the one copy of it.
import {
  AGENT_ENV, DOCKER_IGNORED, RESERVED_ENV, SIZING_MODELS, SLOT_MINIMUMS,
} from "./fixtures";
// The snapshot writer the page itself uses. A literal forged here would be a
// second declaration of the shape, and one that starts passing for the wrong
// reason the first time the version is bumped -- see session.VERSION.
import * as session from "./session";
import { EMPTY_PLAN_INPUTS } from "./usePlan";
// The sizings a fresh page offers: one per served model, so they are built from
// the same fixture the page's own /api/sizing-models stub answers with.
import { defaultSizings } from "./sizings";
const DEFAULT_SIZINGS = defaultSizings(SIZING_MODELS);

/** The funcId vocabulary as /api/func-ids answers it: the rows, and which of
 *  the two lists they are. Written out rather than defaulted, because `source`
 *  is load-bearing -- the account's list makes a funcId missing from it
 *  *retired*, and the baseline's makes it nothing at all (#160). A test whose
 *  subject is elsewhere passes the baseline with no rows, which is honestly
 *  what a fake that answers the same thing to both calls has read. */
const vocabulary = (choices: FuncIdChoice[],
                    source: FuncIdVocabulary["source"]): FuncIdVocabulary =>
  ({ source, choices });
const NO_VOCABULARY = vocabulary([], "baseline");

// Registered before `cleanup` so that it runs *after* it: hooks run in
// reverse, and unmounting writes. The page persists its selections on the way
// out, so clearing first left the storage repopulated by the very unmount that
// followed -- and the next test restored a session it never saved. React 18
// never showed it, because the write did not land before the clear.
afterEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(cleanup);
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
// Before cleanup, which unmounts: hooks run in reverse, and an effect cleanup
// clearing a faked interval on the way out is one more thing to get right for
// nothing.
afterEach(() => { vi.useRealTimers(); });

/** Let the fake clock run, with React's own work flushed around it. */
const tick = (ms: number) =>
  act(async () => { await vi.advanceTimersByTimeAsync(ms); });

/** One account's rollup, identifiable on screen by its workspace name. */
function capacityOf(accountId: number, workspace: string): Capacity {
  return {
    account_id: accountId,
    workspaces: [{ id: accountId * 10, name: workspace }],
    locations: [{
      id: `loc-${accountId}`, name: `location ${accountId}`,
      func_ids: ["performance"], agents: 1, agents_reporting: 1,
      agents_unknown: 0, slots: 1, threads_per_engine: 500, engines: 1,
      rated_vus: 500, workspace_ids: [accountId * 10],
      workspace_names: [workspace], shared: false,
    }],
    rated_vus: 500,
    unrated: 0,
  };
}

test("a slow capacity answer for the previous account never lands under the new one",
  async () => {
    const alpha = deferred<Capacity>();
    const bravo = deferred<Capacity>();
    const api = fakeApi({
      keyDetect: async () => ({ candidates: [], active_key_id: null }),
      keyStatus: async () => ({
        connected: true, user: { email: "someone@example.com" },
        default_account_id: 1, key_id: "key-1",
      }),
      accounts: async () => [{ id: 1, name: "Alpha" }, { id: 2, name: "Bravo" }],
      // Empty, so nothing downstream of the account tree is fetched: this test
      // is about the capacity read and a location list would only add noise.
      workspaces: async () => [],
      optionDefaults: async () => ({}),
      funcIdVocabulary: async () => NO_VOCABULARY,
      functionalities: async () => [],
      svConstants: async () => ({ func_ids: [], ingress_types: [], backends: {} }),
      capacity: (accountId: number) =>
        (accountId === 1 ? alpha : bravo).promise,
    });

    render(<App api={api} />);

    // Connected, on account 1, looking at the rollup -- which is what puts the
    // first (slow) capacity read in flight.
    // Disabled until the key answers -- there is no account to roll up before
    // that, so the click has to wait for the same thing the user does.
    const capacityTab = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Account capacity/ });
    await waitFor(() => expect(capacityTab.disabled).toBe(false));
    fireEvent.click(capacityTab);
    expect(await screen.findByText(/reading the account/)).toBeTruthy();

    // Change account while that read is still outstanding.
    fireEvent.click(screen.getByTitle(/the key everything is read with/));
    const accountBox = screen.getByLabelText("Account");
    fireEvent.focus(accountBox);
    // mouseDown, not click: the option commits there, because a click would
    // blur the box and close the list under the pointer.
    fireEvent.mouseDown(screen.getByText("Bravo (2)"));

    // The second account answers first...
    bravo.settle(capacityOf(2, "Bravo workspace"));
    expect(await screen.findByText("Bravo workspace")).toBeTruthy();

    // ...and only then does the first account's, which is the ordering the
    // guard exists for. Settled inside `act` and awaited to the end of its own
    // handlers: the unguarded version lands one microtask later, so an
    // assertion made before that flush passes over a page about to be wrong --
    // and a `waitFor` would be worse, since it returns on its first successful
    // check, which is that same too-early moment.
    await act(async () => {
      alpha.settle(capacityOf(1, "Alpha workspace"));
      await alpha.promise;
    });

    expect(screen.queryByText("Alpha workspace")).toBeNull();
    expect(screen.queryByText("Bravo workspace")).not.toBeNull();
  });

/** The account tree, with the rollup answering from `capacity`. Shared by the
 *  two Refresh tests below: the rollup needs no workspace, no location and no
 *  location list -- it is one read of the whole account. */
function rollupApi(extra: Partial<Api>) {
  return fakeApi({
    keyDetect: async () => ({ candidates: [], active_key_id: null }),
    keyStatus: async () => ({
      connected: true, user: { email: "someone@example.com" },
      default_account_id: 1, key_id: "key-1",
    }),
    accounts: async () => [{ id: 1, name: "Alpha" }, { id: 2, name: "Bravo" }],
    workspaces: async () => [],
    optionDefaults: async () => ({}),
    funcIdVocabulary: async () => NO_VOCABULARY,
    functionalities: async () => [],
    svConstants: async () => ({ func_ids: [], ingress_types: [], backends: {} }),
    ...extra,
  });
}

/** Open the rollup. Disabled until the key answers, so the click waits for the
 *  same thing the user does. */
async function openRollup() {
  const tab = await screen.findByRole<HTMLButtonElement>(
    "button", { name: /Account capacity/ });
  await waitFor(() => expect(tab.disabled).toBe(false));
  fireEvent.click(tab);
}

test("Refresh on the rollup drops the server's cache before re-reading", async () => {
  const calls: string[] = [];
  let vus = 500;
  render(<App api={rollupApi({
    refresh: async () => { calls.push("refresh"); return null; },
    capacity: async (accountId: number) => {
      calls.push("capacity");
      return { ...capacityOf(accountId, "Alpha workspace"), rated_vus: vus };
    },
  })} />);

  // The headline specifically: a location's own rating is on screen too, and
  // the two figures are only distinguishable by where they are.
  const headline = () => within(
    screen.getByText("account rated VUs").parentElement!);
  await openRollup();
  await waitFor(() => expect(headline().getByText("500")).toBeTruthy());
  expect(calls).toEqual(["capacity"]);

  // Somebody raised a location's engines-per-agent while this view sat open.
  vus = 900;
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

  await waitFor(() => expect(headline().getByText("900")).toBeTruthy());
  // The order is the whole point: served from the cache, this button would do
  // nothing for a minute and say so in no way at all.
  expect(calls).toEqual(["capacity", "refresh", "capacity"]);
});

test("a refreshed rollup for the previous account never lands under the new one",
  async () => {
    // The same guard the view's own effect has, in the shape a callback can
    // have it: `live` is a closure over one run of an effect and a button
    // outlives all of them. 1.3s on a 171-location account is plenty of time to
    // change account in the drawer.
    const pending: { id: number; settle: (c: Capacity) => void }[] = [];
    render(<App api={rollupApi({
      refresh: async () => null,
      capacity: (id: number) => {
        const d = deferred<Capacity>();
        pending.push({ id, settle: d.settle });
        return d.promise;
      },
    })} />);

    await openRollup();
    await waitFor(() => expect(pending.length).toBe(1));
    await act(async () => { pending[0].settle(capacityOf(1, "Alpha workspace")); });
    expect(await screen.findByText("Alpha workspace")).toBeTruthy();

    // Refresh account 1, and leave its answer outstanding.
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(pending.length).toBe(2));

    // Change account while it is in flight. That read answers first...
    fireEvent.click(screen.getByTitle(/the key everything is read with/));
    fireEvent.focus(screen.getByLabelText("Account"));
    fireEvent.mouseDown(screen.getByText("Bravo (2)"));
    await waitFor(() => expect(pending.length).toBe(3));
    await act(async () => { pending[2].settle(capacityOf(2, "Bravo workspace")); });
    expect(await screen.findByText("Bravo workspace")).toBeTruthy();

    // ...and only then does the refresh, which is the ordering the guard is
    // for. Awaited to the end of its own handlers: the unguarded version lands
    // one microtask later, so an assertion made before that flush passes over a
    // page about to be wrong.
    await act(async () => { pending[1].settle(capacityOf(1, "Alpha workspace")); });
    expect(screen.queryByText("Alpha workspace")).toBeNull();
    expect(screen.queryByText("Bravo workspace")).not.toBeNull();
  });

test("the account menu stays up while both of its pickers are used", async () => {
  // It is one control narrowed twice -- an account, then a workspace inside it
  // -- so choosing the first is the middle of the job, not the end of it.
  // Closing there meant reopening the menu to answer the question the first
  // answer had just revealed.
  render(<App api={accountOf([], {
    accounts: async () => [{ id: 1, name: "Alpha" }, { id: 2, name: "Bravo" }],
    workspaces: async () => [{ id: 10, name: "WS one" }, { id: 11, name: "WS two" }],
  })} />);

  fireEvent.click(await screen.findByTitle(/the key everything is read with/));
  fireEvent.focus(screen.getByLabelText("Account"));
  fireEvent.mouseDown(await screen.findByText("Bravo (2)"));

  // The hint is part of the label element, so this matches its start rather
  // than the whole of it.
  const workspace = await screen.findByLabelText(/^Workspace/);
  fireEvent.focus(workspace);
  fireEvent.mouseDown(await screen.findByText("WS two"));
  expect(screen.getByLabelText("Account")).toBeTruthy();
  expect(screen.getByLabelText(/^Workspace/)).toBeTruthy();

  // ...and it has a way out of its own, which is what earns the right to stay
  // open. Clicking away still closes it -- that is what a menu does.
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByLabelText("Account")).toBeNull());
});


// -- service virtualization, through the page --------------------------------
// sv.ts is tested as plain data (sv.test.ts) -- what needs a page is the wiring
// it replaced: an effect that WROTE the ingress option and another that READ it
// back. The bundle really carrying the seeded value, and doing so once, is the
// thing neither a type nor the module's own tests can show.

/** An account holding one location that runs virtual services, ready to
 *  generate for. `record` collects the options every preview is asked for --
 *  the bundle as the server would see it, rather than what the form shows.
 *  `extra` is what the test under way adds: the watch routes, which only the
 *  poll calls. */
function svAccount(record: Options[], extra: Partial<Api> = {}) {
  return fakeApi({
    keyDetect: async () => ({ candidates: [], active_key_id: null }),
    keyStatus: async () => ({
      connected: true, user: { email: "someone@example.com" },
      default_account_id: 1, key_id: "key-1",
    }),
    accounts: async () => [{ id: 1, name: "Alpha" }],
    workspaces: async () => [{ id: 10, name: "Alpha workspace" }],
    locations: async () => [{
      id: "h-mocks", name: "Mocks", funcIds: ["mockServices"], slots: 1,
      // Offline, so the page's own rule auto-picks it -- a running agent is
      // never cloned into a new deployment.
      ships: [{ id: "s-1", name: "agent-1", state: "IDLE" }],
    }],
    facts: async () => ({
      harbor_id: "h-mocks", func_ids: ["mockServices"],
      ships: [{ id: "s-1", name: "agent-1" }], images: [],
    }),
    optionDefaults: async () => ({
      namespace: "blazemeter", service_account_name: "crane",
      platform: "openshift", output_format: "helm",
    }),
    funcIdVocabulary: async () => NO_VOCABULARY,
    functionalities: async () => [{
      id: "mockServices", label: "Service Virtualization",
      namespace: "blazemeter-sv", runs_engine: false,
    }],
    // Two joins, and only one of them is free to be spelled differently here.
    // A functionality id *is* the funcId (#149), and the sv option group tags
    // itself with that string, so this fixture has to carry the real one. What
    // the served constants call it is still the server's own word, which is why
    // the page reads it off /api/sv-constants rather than testing for it.
    svConstants: async () => ({
      func_ids: ["mockServices"],
      ingress_types: ["nginx", "istio"],
      backends: {
        nginx: { group: "networking.k8s.io", resources: ["ingresses"],
                 creates: "Ingress", nodeport_ok: true },
      },
    }),
    generate: async (_facts: unknown, options: Options) => {
      record.push(options);
      return { files: [], token: { branch: "placeholder" as const,
                                   ship_id: "s-1", message: "" } };
    },
    ...extra,
  });
}

test("an SV location seeds a backend into the bundle, once, and is held to manifests",
  async () => {
    const asked: Options[] = [];
    render(<App api={svAccount(asked)} />);

    // Pick the location. Its funcIds are what make SV required -- nothing was
    // configured, and nothing was pressed in the group.
    fireEvent.click(await screen.findByText("Mocks"));

    // The seed reaches the bundle, not just the select: with sv_ingress unset
    // generate() refuses such a location outright, and the select showing its
    // own nginx fallback would hide that.
    const latest = () => asked[asked.length - 1] ?? {};
    await waitFor(() => expect(latest().sv_ingress).toBe("nginx"));
    // ...and the imported default of `helm`, which this location cannot have,
    // is corrected in the same pass.
    expect(latest().output_format).toBe("manifests");
    // Settled: the correction is applied and then there is nothing left to
    // correct. A loop would keep minting options identities and re-POSTing the
    // preview for a configuration that stopped changing.
    const settled = asked.length;
    await new Promise((r) => setTimeout(r, 400));
    expect(asked.length).toBe(settled);

    // The row says the location is what demands it...
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    expect(await screen.findByText(/this location runs mockServices/)).toBeTruthy();

    // ...and both formats that cannot publish a virtual service are refused
    // with their own sentence, rather than disappearing. On this step, because
    // the format decides which of the questions above it are asked.
    const chart = await screen.findByRole<HTMLButtonElement>(
      "radio", { name: /Helm chart/ });
    expect(chart.disabled).toBe(true);
    const docker = screen.getByRole<HTMLButtonElement>(
      "radio", { name: /Docker/ });
    expect(docker.disabled).toBe(true);
    expect(screen.getByText(/HOSTNAME_OVERRIDE/)).toBeTruthy();

    // #115: the profile arrived asking for a chart and is being generated as
    // manifests instead, so the page says so. It used to happen in silence,
    // which is the one correction here that overrides a choice rather than
    // completing one. The chart's own sentence is therefore on screen twice --
    // under the disabled segment, and in the notice naming what was replaced.
    expect(screen.getAllByText(/which this chart does not carry/).length)
      .toBe(2);
    expect(screen.getByText(/Switched to/)).toBeTruthy();
  });

// -- a functionality the location does not run --------------------------------
// It is not on the configure step at all now. #113 made it a card that stated
// it and named the funcId to add, which is a true sentence about the location
// and nothing this step's reader can act on -- and on a performance location,
// which is most of them, it was half the section. Only manual entry still
// renders one, because there the card is the declaration rather than a report
// of one (#118), and filtering by the answer would take away the control that
// gives it.
//
// Everything #113 established still holds and is still asserted below: the
// options are cleared rather than merely hidden, and nothing is left blocking a
// download that nothing on screen can unblock. That is the half a filter cannot
// do -- hiding a row does not empty it.
//
// It was half-configurable before #113, in both source modes and differently in each.
// Manual mode had no guard at all: flipping Service virtualization on for an
// identity declared as performance seeded `sv_ingress: nginx` behind empty
// subdomain and TLS fields, and the rail went red for something nothing on the
// page had asked for. Connect mode had the mirror -- the card body carried
// `pointer-events-none` AND the click handler meant to intercept it, on the
// same element, so a group opened by a restored profile had a switch that could
// not be pressed and a download blocked by a row nobody could reach.
//
// Both need a page: what a *card* offers, and what the options end up as after
// the page has settled, is exactly what optionGroups.test.ts cannot see.

/** An account whose vocabulary carries both functionalities and whose one location
 *  runs only the first -- which is the state the card has to state. */
function twoFunctionalityAccount(record: Options[], extra: Partial<Api> = {}) {
  return fakeApi({
    keyDetect: async () => ({ candidates: [], active_key_id: null }),
    keyStatus: async () => ({
      connected: true, user: { email: "someone@example.com" },
      default_account_id: 1, key_id: "key-1",
    }),
    accounts: async () => [{ id: 1, name: "Alpha" }],
    workspaces: async () => [{ id: 10, name: "Alpha workspace" }],
    locations: async () => [{
      id: "h-perf", name: "Perf", funcIds: ["performance"], slots: 1,
      ships: [{ id: "s-1", name: "agent-1", state: "IDLE" }],
    }],
    facts: async () => ({
      harbor_id: "h-perf", func_ids: ["performance"],
      ships: [{ id: "s-1", name: "agent-1" }], images: [],
    }),
    optionDefaults: async () => ({
      namespace: "blazemeter", service_account_name: "crane",
      output_format: "manifests",
    }),
    funcIdVocabulary: async () => NO_VOCABULARY,
    functionalities: async () => [
      { id: "performance", label: "Performance", namespace: "blazemeter",
        runs_engine: true },
      { id: "mockServices", label: "Service Virtualization",
        namespace: "blazemeter-sv", runs_engine: false },
    ],
    svConstants: async () => ({
      func_ids: ["mockServices"], ingress_types: ["nginx"],
      backends: { nginx: { group: "networking.k8s.io",
                           resources: ["ingresses"], creates: "Ingress",
                           nodeport_ok: true } },
    }),
    generate: async (_facts: unknown, options: Options) => {
      record.push(options);
      return { files: [], token: { branch: "placeholder" as const,
                                   ship_id: "s-1", message: "" } };
    },
    ...extra,
  });
}

/** One functionality's card, by the anchor the rail already links to. Found by id
 *  rather than by its label, which is on screen twice -- the card and the rail
 *  entry pointing at it. */
const card = (functionalityId: string) =>
  within(document.getElementById("cfg-f-" + functionalityId)!);

/** ...and whether there is one at all, which is now an answer in its own right:
 *  a functionality the location does not run has no card, no rail entry and no
 *  sentence. `card()` throws on a missing one, so the two reads are separate. */
const hasCard = (functionalityId: string) =>
  document.getElementById("cfg-f-" + functionalityId) != null;

test("a functionality a manually entered identity was not declared to run has "
     + "no switches",
  async () => {
    const asked: Options[] = [];
    render(<App api={twoFunctionalityAccount(asked)} />);

    // Manual entry, which is where there was no guard: nothing is read, so the
    // declaration below is the only thing that says what this location runs.
    fireEvent.click(await screen.findByRole(
      "radio", { name: /Enter values manually/ }));
    fireEvent.change(screen.getByLabelText(/^Harbor ID/),
                     { target: { value: "0a1b2c3d4e5f60718293a4b5" } });
    fireEvent.change(screen.getByLabelText(/^Ship ID/),
                     { target: { value: "6c5b4a39281706f5e4d3c2b1" } });
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    // Declared performance -- the first served functionality, which is what a manual
    // identity opens on.
    await waitFor(() => expect(
      card("performance").getByLabelText("Enabled")).toHaveProperty("checked", true));

    // The card for the other one states it and offers nothing. A switch here
    // was pressable, seeded an ingress with no domain behind it, and turned the
    // step red for a functionality nobody had asked for.
    expect(card("mockServices").queryByRole("switch")).toBeNull();
    expect(card("mockServices").getByText(/tick/)).toBeTruthy();
    // ...and this is not passing because no card rendered anything: the
    // declared functionality states the engine size its bundle will carry -- the
    // documented default, since manual mode has no location to read.
    expect(card("performance").getByText(/2 CPU \/ 8Gi/)).toBeTruthy();

    // ...and nothing was seeded, so the rail has nothing to complain about.
    // The switch used to write `sv_ingress: nginx` over empty subdomain and TLS
    // fields, which is what turned this step red.
    expect(screen.queryByText(/needs attention/)).toBeNull();
  });

test("a restored profile's SV options for a location without mockServices are cleared, not left blocking",
  async () => {
    // The state the page cannot be clicked out of: nothing here pressed the SV
    // switch, so nothing on screen could press it back.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-perf", shipId: "s-1",
      confirmed: { loc: "h-perf", ship: "s-1" },
      // Connect mode declared nothing, and cannot: what the location runs is
      // its funcIds, which is what makes these options a state nobody chose.
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "blazemeter", sv_ingress: "nginx" },
      step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    const asked: Options[] = [];
    render(<App api={twoFunctionalityAccount(asked)} />);

    // The download is not blocked. This is the failure: the option opens the SV
    // group through detectGroups, `svIncomplete` sees an ingress with no
    // subdomain, and the rail reds -- for a row that was inert (connect mode
    // put `pointer-events-none` on the body) and is now not there at all.
    const next = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Next/ });
    // All three in one wait. They are one settled state, but not one render:
    // the button re-enables as soon as the options are cleared, and the rail
    // re-reads a render later. React 18 batched the pair; 19 does not, so
    // asserting the second two after waiting only for the first caught the
    // page between them.
    await waitFor(() => {
      expect(next.disabled).toBe(false);
      expect(screen.queryByText(/needs attention/)).toBeNull();
      expect(screen.queryByText(/Service virtualization first/)).toBeNull();
    });
    // ...and the option itself is gone from the bundle, not merely off screen:
    // generate() refuses an ingress with no subdomain whatever the location
    // runs, so hiding the row alone moves the blocker to the server.
    await waitFor(() => expect(
      asked[asked.length - 1]?.sv_ingress).toBeFalsy());

    // ...and the functionality is not on the step at all. It used to be a card
    // stating it and naming the funcId to add, which is a true sentence about
    // the location and nothing this step's reader can act on. Not the card, not
    // the rail entry: both are the same list.
    await waitFor(() => {
      expect(hasCard("mockServices")).toBe(false);
      expect(screen.queryByText(/Service virtualization/)).toBeNull();
    });
    // The one that is run is still there, so this is not passing on an empty
    // section.
    expect(hasCard("performance")).toBe(true);
  });

test("a location that runs one functionality shows one card, with nothing configured",
  async () => {
    // The same rule with no configuration behind it: the test above reaches it
    // through options somebody's profile left set, and this one through a
    // location and an agent alone. The card used to be on screen stating
    // itself; the step now opens on the one functionality this bundle has anything to
    // say about.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-perf", shipId: "s-1",
      confirmed: { loc: "h-perf", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "blazemeter" },
      step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    const asked: Options[] = [];
    render(<App api={twoFunctionalityAccount(asked)} />);

    await waitFor(() => expect(hasCard("performance")).toBe(true));
    await waitFor(() => {
      expect(hasCard("mockServices")).toBe(false);
      expect(screen.queryByText(/Service virtualization/)).toBeNull();
    });
  });

// -- a format that cannot serve a functionality -------------------------------
// #115, and what #113 left reachable. The blocked formats were read off the
// location's *demand*, and generate() refuses on the *configuration*: _sv_cfg
// returns a config without ever looking at the funcIds. The gap between the two
// is a location whose funcIds carry no served functionality -- `enabled` is null,
// nobody has said, so notRunPatch clears nothing and every switch is offered.
// Real accounts have them: tdm, dataPublisher and delphix are all funcIds this
// tool models no functionality for.

/** ...that account, with such a location. Its funcId vocabulary answers the way
 *  the server does: the covered baseline with no account, and the account's own
 *  nine -- names, pins and all -- once one is named (#148, #160).
 *
 *  `funcIds` is what the location carries, `["tdm"]` unless a test says
 *  otherwise: a funcId no functionality claims, which is the state the
 *  format-refusal tests below need (nobody has said what this location runs).
 *  A test about what gets *named* passes its own. */
const unclaimedAccount = (record: Options[], funcIds = ["tdm"]) =>
  twoFunctionalityAccount(record, {
    locations: async () => [{
      id: "h-tdm", name: "Tdm", funcIds, slots: 1,
      ships: [{ id: "s-1", name: "agent-1", state: "IDLE" }],
    }],
    facts: async () => ({
      harbor_id: "h-tdm", func_ids: funcIds,
      ships: [{ id: "s-1", name: "agent-1" }], images: [],
    }),
    funcIdVocabulary: async (accountId?: number) => accountId ? vocabulary([
      { id: "performance", label: "Performance", changes_images: true,
        covered: true, sub_func_ids: [] },
      { id: "mockServices", label: "Service Virtualization",
        changes_images: true, covered: true, sub_func_ids: [] },
      // Three of the 117 pins the account serves under this one. They are not
      // rows of their own anywhere: not here, and not in a location's funcIds
      // where they arrive beside the parent.
      { id: "functionalGui", label: "GUI Functional", changes_images: true,
        covered: true,
        sub_func_ids: ["chrome:default", "firefox:139", "safari:15"] },
      { id: "tdm", label: "TDM Integration", changes_images: false,
        covered: false, sub_func_ids: [] },
      // `functionalApi` and `sv-bridge` are deliberately absent, as they are
      // from the real account: 43 and 62 of its 171 locations still carry one.
    ], "account") : vocabulary([
      { id: "performance", label: "Performance", changes_images: true,
        covered: true, sub_func_ids: [] },
    ], "baseline"),
  });

test("a funcId this tool has no options for is named in the account's own words",
  async () => {
    // Silence would read as coverage: this location runs tdm, nothing here
    // configures it, and the honest sentence names it. The account is the only
    // thing that knows it is called "TDM Integration" -- the keyless vocabulary
    // is the three covered funcIds and holds no such row -- so this is also the
    // assertion that the account's list replaced the baseline on connect.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-tdm", shipId: "s-1",
      confirmed: { loc: "h-tdm", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "blazemeter" },
      step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    render(<App api={unclaimedAccount([])} />);
    fireEvent.click(await screen.findByRole("button", { name: /Configure/ }));

    expect(await screen.findByText(/TDM Integration/)).toBeTruthy();
    // ...and the raw id is gone with it. A page showing both would be the
    // vocabulary arriving and nothing reading it.
    expect(screen.queryByText(/\btdm\b/)).toBeNull();
  });

test("a browser pin is not a funcId this tool has no options for", async () => {
  // #160, end to end. This location is the shape 43% of one account's are: the
  // parent beside the browsers it is pinned to, plus a funcId the account
  // retired years ago. Every pin used to be named on the configure step as
  // something this tool has no options for -- 41 of them on the worst location
  // -- which is a true sentence about nothing and buried the one funcId the
  // sentence exists for.
  session.save({
    sourceMode: "connect", accountId: 1, workspaceId: 10,
    harborId: "h-tdm", shipId: "s-1",
    confirmed: { loc: "h-tdm", ship: "s-1" },
    manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
    options: { namespace: "blazemeter" },
    step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
  });
  render(<App api={unclaimedAccount([], [
    "functionalGui", "chrome:default", "firefox:139", "safari:15", "sv-bridge",
  ])} />);
  fireEvent.click(await screen.findByRole("button", { name: /Configure/ }));

  // The retired one, and it is the whole of that sentence: the account does not
  // serve `sv-bridge`, so this location was created before the removal.
  expect(await screen.findByText(/no longer offers/)).toBeTruthy();
  expect(screen.getByText("sv-bridge")).toBeTruthy();
  // Not one pin anywhere on the page. `functionalGui` is uncovered here -- this
  // account's served functionalities are performance and SV -- so this is also
  // the case where the parent is named and its pins still are not: what a pin
  // is a parameter *of* does not depend on whether that is configurable.
  for (const pin of ["chrome:default", "firefox:139", "safari:15"]) {
    expect(screen.queryByText(new RegExp(pin))).toBeNull();
  }
  expect(screen.getByText(/GUI Functional/)).toBeTruthy();
});

test("an SV configuration no location demanded still takes away the formats that refuse it",
  async () => {
    // A restored session is one of the three ways these options arrive without
    // anyone pressing anything, and the only one that needs no account to
    // reproduce. Docker plus a complete SV configuration: generate() refuses
    // the pair outright, and nothing on the page used to say so -- the segment
    // was enabled, the rail was green and the download was not blocked.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-tdm", shipId: "s-1",
      confirmed: { loc: "h-tdm", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: {
        namespace: "blazemeter", output_format: "docker",
        sv_ingress: "nginx", sv_subdomain: "apps.example.com",
        sv_tls_secret: "wildcard",
      },
      step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    const asked: Options[] = [];
    render(<App api={unclaimedAccount(asked)} />);

    // The acceptance criterion, as the assertion: no request carries the pair
    // the server refuses. Not "the error is nicer" -- the combination never
    // reaches generate() at all.
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 400));
    expect(asked.filter((o) => o.output_format === "docker" && o.sv_ingress
      && o.sv_ingress !== "none")).toEqual([]);
    // The SV options are what survive; the format is what gives way. Nothing
    // here wipes a configuration somebody wrote in order to keep a format.
    expect(asked[asked.length - 1]?.sv_ingress).toBe("nginx");
    expect(asked[asked.length - 1]?.output_format).toBe("manifests");

    // ...and the page said so rather than swapping the segment in silence.
    expect(await screen.findByText(/Switched to/)).toBeTruthy();
    const docker = screen.getByRole<HTMLButtonElement>(
      "radio", { name: /Docker/ });
    expect(docker.disabled).toBe(true);
  });

test("a functionality this bundle's format cannot serve is stated, not offered",
  async () => {
    // The same card as #113's, for the other reason it can carry no switches --
    // and the two must not be confused. This location's funcIds say nothing, so
    // "not enabled here" is false; what is true is that no docker bundle can
    // publish a virtual service whatever the location runs.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-tdm", shipId: "s-1",
      confirmed: { loc: "h-tdm", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "blazemeter", output_format: "docker" },
      step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    const asked: Options[] = [];
    render(<App api={unclaimedAccount(asked)} />);
    fireEvent.click(await screen.findByRole("button", { name: /Configure/ }));

    // No switch: pressing one would configure a bundle the generator refuses,
    // and the format would then be yanked out from under the choice just made.
    await waitFor(() => expect(card("mockServices").queryByRole("switch")).toBeNull());
    // It says which of the two answers this is, and names the format that can.
    // The card is on screen at all because nobody has said what this location
    // runs (`tdm` is a funcId no functionality claims), which is the state the two
    // answers are easiest to confuse in.
    expect(card("mockServices").getByText(/Not possible in this bundle/)).toBeTruthy();
    expect(card("mockServices").queryByText(/was declared to run/)).toBeNull();

    // ...and it comes back on a format that can serve it, rather than being
    // gone for good: the card is a view over the bundle, not a decision.
    fireEvent.click(screen.getByRole("radio", { name: /Kubernetes manifests/ }));
    await waitFor(() =>
      expect(card("mockServices").queryByRole("switch")).not.toBeNull());
  });

test("the docker format is a third bundle, and it is what gets generated",
  async () => {
    const asked: Options[] = [];
    render(<App api={accountOf([loc("h-0", "Dublin",
      [{ id: "s-1", name: "agent-1", state: "IDLE" }])], {
      generate: async (_facts: unknown, options: Options) => {
        asked.push(options);
        return { files: [], token: { branch: "placeholder" as const,
                                     ship_id: "s-1", message: "" } };
      },
    })} />);

    fireEvent.click(await screen.findByText("Dublin"));
    // The row, not the path line under the flow: an offline lone agent is
    // auto-picked, so its name is already on screen twice.
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    // Offered for an ordinary performance location -- the two Kubernetes
    // formats are not the only platform BlazeMeter runs a private location on.
    const docker = await screen.findByRole<HTMLButtonElement>(
      "radio", { name: /Docker/ });
    expect(docker.disabled).toBe(false);
    // Before: this is a Kubernetes bundle, so it has a namespace and an
    // account to run as, and the form asks for both.
    expect(screen.getByDisplayValue("blazemeter")).toBeTruthy();
    expect(screen.getByDisplayValue("crane")).toBeTruthy();
    fireEvent.click(docker);

    // The choice reaches the request rather than only the control: the whole
    // bundle is decided server-side from this one option.
    await waitFor(() =>
      expect(asked[asked.length - 1]?.output_format).toBe("docker"));

    // ...and the questions it makes no sense of are off this step. This is why
    // the control moved here: choosing docker on the download step left the
    // form above it asking for a namespace, a ServiceAccount and a node
    // selector for a bundle that carries none of them, and the only place that
    // said so was the generated README.
    await waitFor(() => expect(screen.queryByDisplayValue("crane")).toBeNull());
    expect(screen.queryByDisplayValue("blazemeter")).toBeNull();
    expect(screen.queryByText(/Deployment placement/)).toBeNull();
    expect(screen.queryByText(/^Scheduling$/)).toBeNull();
    expect(screen.queryByText(/Engine sizing/)).toBeNull();
    expect(screen.queryByText(/crane-hook/)).toBeNull();
    // The two that a container genuinely has are still on screen, in the
    // vocabulary that reaches it rather than the cluster's.
    expect(screen.getByText(/Security & RBAC/)).toBeTruthy();
    expect(screen.getByText(/HTTP\(S\) proxy/)).toBeTruthy();

    // The download step names the format it did not choose, and says what the
    // bundle holds -- which is not a manifest.
    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    expect(await screen.findByText(/bzm-opl-agent\.sh/)).toBeTruthy();
  });

test("the cluster is asked under the posture, and takes the OpenShift-only mode with it",
  async () => {
    // `platform: openshift` is a *posture* -- the cluster assigns the UID --
    // and it is recommended on vanilla Kubernetes too, so it was answering a
    // second question nobody had asked: which cluster this is. Everything the
    // bundle tells somebody to run came out in `oc`, and OpenShift's own trust
    // injection was offered to customers whose cluster has nothing to inject
    // with.
    const asked: Options[] = [];
    render(<App api={accountOf([loc("h-0", "Dublin",
      [{ id: "s-1", name: "agent-1", state: "IDLE" }])], {
      // The generator's own defaults, which is where the posture arrives from:
      // both questions start where DEFAULT_OPTIONS leaves them.
      optionDefaults: async () => ({
        namespace: "blazemeter", service_account_name: "crane",
        output_format: "manifests", platform: "openshift",
        openshift_cluster: true,
      }),
      generate: async (_facts: unknown, options: Options) => {
        asked.push(options);
        return { files: [], token: { branch: "placeholder" as const,
                                     ship_id: "s-1", message: "" } };
      },
    })} />);

    fireEvent.click(await screen.findByText("Dublin"));
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    // The OpenShift-only CA mode, picked while the bundle is an OpenShift one.
    fireEvent.click(await screen.findByRole("switch", { name: "Custom CA trust" }));
    fireEvent.click(await screen.findByLabelText(/OpenShift cluster trust injection/));
    await waitFor(() =>
      expect(asked[asked.length - 1]?.ca_openshift_inject).toBe(true));

    // Advanced is closed, and the cluster is asked inside it -- one fold below
    // the posture it belongs to.
    fireEvent.click(screen.getByRole("button", { name: /Advanced/ }));
    fireEvent.change(screen.getByLabelText(/^Cluster/),
                     { target: { value: "k8s" } });

    // It reaches the bundle, which is where `oc` against `kubectl` is decided.
    await waitFor(() =>
      expect(asked[asked.length - 1]?.openshift_cluster).toBe(false));
    // ...and the mode goes with it. Hiding the radio alone would leave the
    // option set: an inject ConfigMap off OpenShift is emitted empty, nothing
    // ever fills it, and the agent trusts nothing extra while the bundle reads
    // as configured.
    expect(asked[asked.length - 1]?.ca_openshift_inject).toBe(false);
    expect(screen.queryByLabelText(/OpenShift cluster trust injection/)).toBeNull();
    // The posture is untouched: it is the other question, and the one this
    // customer still wants answered the recommended way.
    expect(asked[asked.length - 1]?.platform).toBe("openshift");
  });

// -- the download step, through the page -------------------------------------
// The two requests this step exists to make now go through the same seam as
// every other route (#104), so what is asserted here is what the page handed
// the client: the facts, the options with the agent's id, and the credential
// request -- the record the whole of #64 was about. Stubbing `fetch` proved the
// same thing one transport layer lower and could not reach the decision that
// produces it; the wire shape those two routes put on the request is pinned in
// api.test.ts, which is where a transport belongs.

/** What the page handed the client for a bundle, whichever route it used. */
interface Sent {
  route: "zip" | "save";
  facts: Facts;
  options: Options;
  credential: TokenRequest;
}

/** The bundle route, recording what left and answering as the server would --
 *  the credential sentence included, because it is core's wording and arrives
 *  on the answer rather than being composed on this side. */
function transfers(sent: Sent[]): Partial<Api> {
  return {
    downloadZip: async (facts, options, credential) => {
      sent.push({ route: "zip", facts, options, credential });
      return {
        branch: credential.rotate_token ? "rotated" : "given",
        ship_id: "s-1",
        message: credential.rotate_token
          ? "a NEW AUTH_TOKEN was issued" : "the AUTH_TOKEN you supplied",
      };
    },
  };
}

/** An account with one performance location and one idle agent in it: enough
 *  to reach step 3 with the buttons enabled. `extra` is what the test under way
 *  adds -- the two bundle routes, which nothing else on this page calls. */
function perfAccount(extra: Partial<Api> = {}) {
  return fakeApi({
    keyDetect: async () => ({ candidates: [], active_key_id: null }),
    keyStatus: async () => ({
      connected: true, user: { email: "someone@example.com" },
      default_account_id: 1, key_id: "key-1",
    }),
    accounts: async () => [{ id: 1, name: "Alpha" }],
    workspaces: async () => [{ id: 10, name: "Alpha workspace" }],
    locations: async () => [{
      id: "h-perf", name: "Perf", funcIds: ["performance"], slots: 1,
      ships: [{ id: "s-1", name: "agent-1", state: "IDLE" }],
    }],
    facts: async () => ({
      harbor_id: "h-perf", func_ids: ["performance"],
      ships: [{ id: "s-1", name: "agent-1" }], images: [],
    }),
    optionDefaults: async () => ({
      namespace: "blazemeter", service_account_name: "crane",
      output_format: "manifests",
    }),
    funcIdVocabulary: async () => NO_VOCABULARY,
    functionalities: async () => [{
      id: "performance", label: "Performance", namespace: "blazemeter",
      runs_engine: true,
    }],
    svConstants: async () => ({ func_ids: [], ingress_types: [], backends: {} }),
    generate: async () => ({
      files: [{ name: "crane.yaml", content: "kind: Deployment" }],
      token: { branch: "placeholder" as const, ship_id: "s-1",
               message: "no AUTH_TOKEN — the bundle carries a placeholder" },
    }),
    ...extra,
  });
}

/** Pick the location, then open step 3 with the buttons live. */
async function atDownloadStep() {
  fireEvent.click(await screen.findByText("Perf"));
  fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
  const button = await screen.findByRole<HTMLButtonElement>(
    "button", { name: /Download bundle/ });
  await waitFor(() => expect(button.disabled).toBe(false));
  return button;
}

test("downloading sends the configured bundle for the selected agent, and rotates nothing",
  async () => {
    const sent: Sent[] = [];
    render(<App api={perfAccount(transfers(sent))} />);

    fireEvent.click(await atDownloadStep());

    await waitFor(() => expect(sent.length).toBe(1));
    expect(sent[0].route).toBe("zip");
    expect(sent[0].facts).toMatchObject({ harbor_id: "h-perf" });
    expect(sent[0].options).toMatchObject({
      namespace: "blazemeter", ship_id: "s-1" });
    // The default, and the whole of #64: reading a bundle must not revoke the
    // credential the deployed agent is running on. Asserted on the record the
    // request is made of, so there is no boolean left for the button to
    // re-apply and no second place it could be re-applied differently.
    expect(sent[0].credential).toEqual({ rotate_token: false });

    // Nothing is said about the credential, because nothing happened to it.
    // Core still answers with its own sentence -- "nothing was issued" -- and
    // the page keeps it for the branch where something was: a line under every
    // download reporting that the download was uneventful is what teaches
    // people not to read the line. The rotated branch is asserted below.
    expect(screen.queryByText(/the AUTH_TOKEN you supplied/)).toBeNull();
  });

test("the scheduling radio prescribes a dedicated engine pool, and the choice reaches the bundle",
  async () => {
    const sent: Sent[] = [];
    render(<App api={perfAccount(transfers(sent))} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    // The group row's switch, reached from its title the way a reader reaches
    // it: the radio is behind the row, not a page of its own.
    const title = await screen.findByText(/^Scheduling$/);
    fireEvent.click(within(title.closest("div.flex") as HTMLElement)
      .getByRole("switch"));

    fireEvent.click(await screen.findByRole("radio", { name: /Separate nodes/ }));
    // The choice states its cost beside it: a dedicated pool without the
    // location's engine override packs every engine onto the first node. Found
    // by its own words rather than by "Location settings", which the engine-size
    // statement on the performance card also names -- two sentences about the
    // same field, each where its own decision is made.
    expect(await screen.findByText(/autoscalers grow pools by what pods request/))
      .toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const button = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Download bundle/ });
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);

    // The prescription is real options on the request -- the matched
    // label/taint pair on one vocabulary -- not a UI state that dies here.
    await waitFor(() => expect(sent.length).toBe(1));
    expect(sent[0].options).toMatchObject({
      engine_node_selector: { pool: "bzm-engines" },
      engine_tolerations: [{ key: "pool", operator: "Equal",
                             value: "bzm-engines", effect: "NoSchedule" }],
    });
  });

test("the offered variables reach the bundle, each through the control its type has",
  async () => {
    // #131's escape hatch, as a list rather than a name box. The point of
    // driving it from here rather than from env.test.ts is the last mile: a
    // boolean's third position writes nothing, a key/value table writes JSON,
    // and "the control was pressed" and "the bundle carries it" are two claims.
    const sent: Sent[] = [];
    render(<App api={perfAccount({
      ...transfers(sent),
      // Both served tables. Unstubbed they reject, which is the honest "not
      // read yet" -- and that state offers nothing and refuses nothing, so
      // neither half below would be under test at all.
      reservedEnv: async () => RESERVED_ENV,
      agentEnv: async () => AGENT_ENV,
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    fireEvent.click(await screen.findByText("Environment variables"));
    // A string, off the served record -- the name is the row's, so this is the
    // one thing here nobody can mistype.
    fireEvent.change(await screen.findByLabelText("PREFERRED_INTERFACE"),
                     { target: { value: "eth1" } });
    // ...and a boolean, whose three positions are the whole reason it is not a
    // switch: this one defaults on, so Off is a departure worth writing.
    fireEvent.click(within(screen.getByRole("radiogroup", { name: "VERIFY_SSL" }))
      .getByRole("radio", { name: "Off" }));

    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const button = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Download bundle/ });
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(sent.length).toBe(1));
    expect((sent[0].options as { extra_env?: Record<string, string> }).extra_env)
      .toEqual({ PREFERRED_INTERFACE: "eth1", VERIFY_SSL: "false" });
  });

test("the catalogue is asked for over the funcIds the chosen location runs",
  async () => {
    // #150. The scoping is the server's -- one answer for the CLI, the MCP
    // server and this page -- so what has to hold here is that the page asks
    // the right question, and that "nobody has said" reaches the route as an
    // absent parameter rather than as an empty list. They are different reads:
    // absent offers the reference whole, empty offers only what every location
    // has a reader for.
    const asked: (string[] | null | undefined)[] = [];
    render(<App api={perfAccount({
      reservedEnv: async () => RESERVED_ENV,
      agentEnv: async (funcIds) => { asked.push(funcIds); return AGENT_ENV; },
    })} />);

    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(asked[0] ?? null).toBe(null);

    fireEvent.click(await screen.findByText("Perf"));
    await waitFor(() => expect(asked).toContainEqual(["performance"]));
  });

test("a variable the location's catalogue leaves out is still on screen and still editable",
  async () => {
    // The way #150 is most easily got wrong. Scoping is a filter on what is
    // *offered*, never on what is carried: a profile written for a GUI
    // location, or a location changed after the form was filled in, still holds
    // the value and the bundle still writes it. So the name/value editor
    // underneath keeps it -- a form denying a variable the ConfigMap has is the
    // failure this whole area's rules are about.
    const sent: Sent[] = [];
    render(<App api={perfAccount({
      ...transfers(sent),
      reservedEnv: async () => RESERVED_ENV,
      // What the server serves a performance location: the tagged rows gone.
      agentEnv: async () => AGENT_ENV.filter((v) => !v.functionalities.length),
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    fireEvent.click(await screen.findByText("Environment variables"));
    // Not offered as a row of its own...
    expect(screen.queryByLabelText("DODUO_PORT")).toBe(null);

    const body = JSON.stringify({ namespace: "blazemeter",
                                  extra_env: { DODUO_PORT: "8080" } });
    const file = Object.assign(
      new File([body], "profile.json", { type: "application/json" }),
      { text: async () => body });
    await act(async () => {
      fireEvent.change(document.querySelector(
        'input[type="file"][accept=".json"]') as HTMLInputElement,
        { target: { files: [file] } });
    });

    // ...and still on screen, by name, with the value the profile carried. The
    // editor's own row states the count while it is closed, so nothing is
    // silent about it either.
    expect(await screen.findByText(/Another variable by name/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("(1 set)")).toBeTruthy());
    fireEvent.click(screen.getByText(/Another variable by name/));
    await waitFor(() => expect(
      (screen.getByLabelText("Variable name 1") as HTMLInputElement).value)
      .toBe("DODUO_PORT"));
    fireEvent.change(screen.getByLabelText("Variable value 1"),
                     { target: { value: "9090" } });

    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const button = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Download bundle/ });
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(sent.length).toBe(1));
    expect((sent[0].options as { extra_env?: Record<string, string> }).extra_env)
      .toEqual({ DODUO_PORT: "9090" });
  });

test("the environment area says where a variable it will not take is set instead",
  async () => {
    // The other half of #150, and the reported complaint: "the list is missing
    // kubernetes auto update". It is not missing -- the bundle writes it, off
    // the `auto_update` option -- but that option is a tri-state inside a group
    // about RBAC, so nothing on the page led from the name to the control. The
    // area now states the whole reserved table with the owning option and the
    // section holding it, which is what a browser's find can land in.
    render(<App api={perfAccount({
      reservedEnv: async () => RESERVED_ENV,
      agentEnv: async () => AGENT_ENV,
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    fireEvent.click(await screen.findByText("Environment variables"));
    fireEvent.click(await screen.findByText(/Set by this bundle/));

    const row = (await screen.findByText("AUTO_KUBERNETES_UPDATE"))
      .closest("li") as HTMLElement;
    expect(within(row).getByText(/auto_update/)).toBeTruthy();
    expect(within(row).getByText(/Security & RBAC/)).toBeTruthy();
  });

test("a name typed by hand is still refused with the option that owns it",
  async () => {
    // The editor underneath the list, which is what stops a variable the
    // catalogue does not carry being unreachable. Its one judgement is the same
    // as before: a name the bundle already writes is refused on the row, in the
    // sentence naming the option that owns it -- "set it there" is the whole
    // answer, and a bare "that one is taken" is not.
    render(<App api={perfAccount({
      reservedEnv: async () => RESERVED_ENV,
      agentEnv: async () => AGENT_ENV,
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    fireEvent.click(await screen.findByText("Environment variables"));
    fireEvent.click(await screen.findByText(/Another variable by name/));
    fireEvent.click(await screen.findByText(/\+ Add variable/));
    fireEvent.change(await screen.findByLabelText("Variable name 1"),
                     { target: { value: "KUBERNETES_SERVICE_USE_TYPE" } });
    expect(await screen.findByText(/set it with service_type instead/)).toBeTruthy();
  });

test("an imported profile rewrites the environment rows rather than sitting under them",
  async () => {
    // The rows are local state and the option is what they add up to, which is
    // what stops a half-typed name flickering out of existence. Import is on
    // the same step and writes the option from outside, so without a resync the
    // rows go on showing variables the bundle no longer carries -- a form
    // showing a variable no bundle has, which is the failure this area's own
    // rules are otherwise about. See the `emitted` ref in EnvVars.
    render(<App api={perfAccount()} />);
    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    fireEvent.click(await screen.findByText("Environment variables"));
    fireEvent.click(await screen.findByText(/Another variable by name/));
    fireEvent.click(await screen.findByText(/\+ Add variable/));
    fireEvent.change(await screen.findByLabelText("Variable name 1"),
                     { target: { value: "TYPED_BY_HAND" } });

    // `text()` is supplied rather than inherited: this jsdom's Blob has no
    // Blob.prototype.text, and the page reads the picked file with it.
    const body = JSON.stringify({ namespace: "blazemeter",
                                  extra_env: { FROM_THE_PROFILE: "eth9" } });
    const file = Object.assign(
      new File([body], "profile.json", { type: "application/json" }),
      { text: async () => body });
    await act(async () => {
      fireEvent.change(document.querySelector(
        'input[type="file"][accept=".json"]') as HTMLInputElement,
        { target: { files: [file] } });
    });

    await waitFor(() => expect(
      (screen.getByLabelText("Variable name 1") as HTMLInputElement).value)
      .toBe("FROM_THE_PROFILE"));
    expect((screen.getByLabelText("Variable value 1") as HTMLInputElement).value)
      .toBe("eth9");
  });

test("the configure step states the engine size the location implies, and edits nothing",
  async () => {
    // The engine size is one figure and the location is where it is set
    // (#132): generate derives the bundle's limits from the location's
    // overrideCPU/overrideMemory, so the configure step carries a read-only
    // statement -- no group, no switch, no fields -- naming the size, its
    // source, and where to change it.
    const held = { ...loc("h-perf", "Perf",
      [{ id: "s-1", name: "agent-1", state: "IDLE" }]),
      overrideCPU: 1, overrideMemory: 4096 };
    render(<App api={accountOf([held], {
      locations: async () => [held],
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    // The statement, from the location's own requests -- 4096 MB read as Mi
    // lands on the Gi form -- and it names the place to change it.
    const note = await screen.findByText(/1 CPU \/ 4Gi/);
    expect(note.textContent).toContain("Location settings");

    // No editor: the size is not optional and not configurable here, so
    // there is no sizing switch and no Apply.
    expect(screen.queryByText(/^Engine sizing$/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  });

test("a location holding no engine requests is stated as the default, never blank",
  async () => {
    render(<App api={accountOf([loc("h-perf", "Perf",
      [{ id: "s-1", name: "agent-1", state: "IDLE" }])], {
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    // Always an effective size on screen -- the documented default here --
    // with the location named as the place that changes it.
    const note = await screen.findByText(/2 CPU \/ 8Gi/);
    expect(note.textContent).toContain("default");
    expect(note.textContent).toContain("Location settings");
  });

test("a location running two engine functionalities states the engine size once",
  async () => {
    // One agent, one KUBERNETES_RESOURCES_LIMITS pair, one statement (#149).
    // Both cards run engines -- a GUI Functional agent carries the same
    // apm/crane/v4 the performance one does, plus the grid -- so a per-card
    // `is this the sizing functionality` test would print the size twice and
    // read as two settings.
    const both = { ...loc("h-perf", "Perf",
      [{ id: "s-1", name: "agent-1", state: "IDLE" }]),
      funcIds: ["performance", "functionalGui"] };
    render(<App api={accountOf([both], {
      facts: async (harborId: string) => ({
        harbor_id: harborId, func_ids: ["performance", "functionalGui"],
        ships: [], images: [],
      }),
      functionalities: async () => [
        { id: "performance", label: "Performance", namespace: "blazemeter",
        runs_engine: true },
        { id: "functionalGui", label: "GUI Functional",
          namespace: "blazemeter-gui", runs_engine: true },
      ],
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    // Both cards are on screen, and the statement is on the first of them.
    await waitFor(() => expect(hasCard("functionalGui")).toBe(true));
    expect(screen.getAllByText(/^Engine size\.$/)).toHaveLength(1);
    expect(card("performance").getByText(/2 CPU \/ 8Gi/)).toBeTruthy();
  });

test("a GUI Functional location is told its engine size on its own card",
  async () => {
    // The statement used to be pinned to the performance card, and a location
    // that runs GUI Functional and not Performance has none -- so a bundle
    // carrying engine limits said nothing about them anywhere. A GUI agent
    // carries the taurus engine too (apm, crane, v4, plus doduo and a browser),
    // which is what makes "engines run at" true of it.
    const gui = { ...loc("h-gui", "Gui",
      [{ id: "s-1", name: "agent-1", state: "IDLE" }]),
      funcIds: ["functionalGui"] };
    render(<App api={accountOf([gui], {
      facts: async (harborId: string) => ({
        harbor_id: harborId, func_ids: ["functionalGui"], ships: [], images: [],
      }),
      functionalities: async () => [
        { id: "performance", label: "Performance", namespace: "blazemeter",
        runs_engine: true },
        { id: "functionalGui", label: "GUI Functional",
          namespace: "blazemeter-gui", runs_engine: true },
      ],
    })} />);

    fireEvent.click(await screen.findByText("Gui"));
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    // The performance card is not on this page at all -- the location does not
    // run it -- and the statement is on the card that is.
    await waitFor(() => expect(hasCard("functionalGui")).toBe(true));
    expect(hasCard("performance")).toBe(false);
    expect(card("functionalGui").getByText(/2 CPU \/ 8Gi/)).toBeTruthy();
  });

test("a location that runs no engine is not told what its engines run at",
  async () => {
    // An SV-only agent carries crane, group-gateway and service-mock and no
    // taurus engine at all, read off /versions. Its pod limits are still
    // carried and still sent -- crane applies them to every pod it creates --
    // but "engines run at 2 CPU / 8Gi" is not a true sentence about it, and
    // what those limits should be for a mock is #154's to say. So nothing is
    // stated rather than the wrong thing.
    render(<App api={svAccount([])} />);

    fireEvent.click(await screen.findByText("Mocks"));
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    await waitFor(() => expect(hasCard("mockServices")).toBe(true));
    expect(screen.queryByText(/^Engine size\.$/)).toBeNull();
    expect(screen.queryByText(/per engine/)).toBeNull();
  });

// Rotating from this step is gone with its box. It was the second way to mint
// a credential -- step 1 has the first, on the agent it belongs to and beside
// the sentence saying what it kills -- and two ways to do one irreversible
// thing is one more than the page can keep honest. What is left to assert is
// that the download carries the plan, which the test above does, and that the
// plan never rotates, which token.test.ts does over every branch.

// Saving to a folder was the other route, and the pair had a test each for
// reading one credential plan -- which is what stopped them disagreeing about
// what a click cost (#64). The button is gone from this step (the CLI's -o and
// the MCP server's opl_bundle write folders now), so there is one route, and
// what is left to assert is that it carries the plan: the two tests above.

// -- step 1: the two lists, and the two forms that write to the account -------
// A location holds agents and both are picked from a list, so both are driven
// here rather than only typechecked. The filter is part of it: a real account
// has 171 locations and the box only appears above eight, so a list that stops
// filtering is a list nobody can get to the bottom of.

/** One location in a workspace, as the listing carries it. */
const loc = (id: string, name: string, ships: Ship[] = []): Location =>
  ({ id, name, funcIds: ["performance"], slots: 1, ships });

/** An account holding exactly `locations`, and nothing else of interest. The
 *  list is the fixture's own array, so a test can have a create call add to it
 *  the way the account would. */
function accountOf(locations: Location[], extra: Partial<Api> = {}) {
  return fakeApi({
    keyDetect: async () => ({ candidates: [], active_key_id: null }),
    keyStatus: async () => ({
      connected: true, user: { email: "someone@example.com" },
      default_account_id: 1, key_id: "key-1",
    }),
    accounts: async () => [{ id: 1, name: "Alpha" }],
    workspaces: async () => [{ id: 10, name: "Alpha workspace" }],
    // A copy each time, as a fetch would be: the page stores what it is handed,
    // and one array mutated in place is a re-read React sees no change in.
    locations: async () => [...locations],
    facts: async (harborId: string) => ({
      harbor_id: harborId, func_ids: ["performance"],
      ships: [], images: [],
    }),
    optionDefaults: async () => ({
      namespace: "blazemeter", service_account_name: "crane",
      output_format: "manifests",
    }),
    funcIdVocabulary: async () => vocabulary([
      { id: "performance", label: "Performance", changes_images: true,
        covered: true, sub_func_ids: [] },
    ], "baseline"),
    functionalities: async () => [{
      id: "performance", label: "Performance", namespace: "blazemeter",
      runs_engine: true,
    }],
    svConstants: async () => ({ func_ids: [], ingress_types: [], backends: {} }),
    // An account whose agents were all made somewhere else, which is the
    // ordinary one: the server has minted nothing this session. Stubbed rather
    // than left to fakeApi's rejection because an empty store and an
    // unreachable one are different answers and the page says different things
    // about them -- the tests that mean the second say so themselves.
    mintedToken: async () => ({ auth_token: null }),
    // generate.DOCKER_IGNORED as the page receives it, from the one copy of
    // that table (see fixtures.ts -- this used to be a second, shorter slice,
    // which is how a page test comes to assert against a table the unit test
    // would call incomplete).
    dockerIgnored: async () => DOCKER_IGNORED,
    // plan.SIZING_MODELS, from the same one copy: the sizing card renders a
    // field group per model, so a page with no table has no fields.
    sizingModels: async () => SIZING_MODELS,
    generate: async () => ({
      files: [], token: { branch: "placeholder" as const, ship_id: null,
                          message: "" },
    }),
    ...extra,
  });
}

test("a short list has no filter over it", async () => {
  const eight = Array.from({ length: 8 }, (_, i) => loc(`h-${i}`, `Region ${i}`));
  render(<App api={accountOf(eight)} />);

  expect(await screen.findByText("Region 0")).toBeTruthy();
  // Eight rows fit on the screen they are on; a box over them would be furniture
  // asking to be filled in.
  expect(screen.queryByPlaceholderText(/^filter /)).toBeNull();
});

test("a long list is filtered, and the row picked is the one whose facts are read",
  async () => {
    const asked: string[] = [];
    const many = [...Array.from({ length: 8 }, (_, i) => loc(`h-${i}`, `Region ${i}`)),
                  loc("h-dublin", "Dublin")];
    render(<App api={accountOf(many, {
      facts: async (harborId: string) => {
        asked.push(harborId);
        return { harbor_id: harborId, func_ids: ["performance"], ships: [],
                 images: [] };
      },
    })} />);

    // The count is the whole list's, not the filtered one's -- it is what the
    // box is offering to narrow.
    const box = await screen.findByPlaceholderText("filter 9 locations…");
    fireEvent.change(box, { target: { value: "dub" } });

    expect(screen.queryByText("Region 0")).toBeNull();
    // Picked from what the filter left, which is the only way to reach a row on
    // a real account's list.
    fireEvent.click(screen.getByText("Dublin"));
    await waitFor(() => expect(asked).toEqual(["h-dublin"]));

    // ...and a query nothing matches says so, rather than showing an empty box
    // that reads like an empty workspace.
    fireEvent.change(box, { target: { value: "zzz" } });
    expect(screen.getByText("no locations match")).toBeTruthy();
  });

// -- Refresh -----------------------------------------------------------------
// stale.ts is tested as plain data. What needs a page is the ordering and the
// ownership: that the cache is dropped *before* the re-read (or the button is
// served the same list it was pressed about), and that the re-read writes the
// list and leaves everything else where it was.

test("Refresh drops the server's cache before re-reading, or it re-reads nothing",
  async () => {
    const calls: string[] = [];
    const listing = [loc("h-0", "Region 0")];
    render(<App api={accountOf(listing, {
      refresh: async () => { calls.push("refresh"); return null; },
      locations: async () => { calls.push("locations"); return [...listing]; },
    })} />);

    expect(await screen.findByText("Region 0")).toBeTruthy();
    expect(calls).toEqual(["locations"]);

    // What a colleague did while this page sat open.
    listing.push(loc("h-1", "Region 1"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("Region 1")).toBeTruthy();
    // The order is the whole point. The server holds a location list for
    // CACHE_TTL_S, so a re-read on its own comes back byte-identical and the
    // click looks exactly like one that worked.
    expect(calls).toEqual(["locations", "refresh", "locations"]);
  });

test("a location that has gone is said, and nothing else on the page moves",
  async () => {
    const listing = [loc("h-0", "Region 0"), loc("h-1", "Region 1")];
    const asked: string[] = [];
    render(<App api={accountOf(listing, {
      refresh: async () => null,
      facts: async (harborId: string) => {
        asked.push(harborId);
        return { harbor_id: harborId, func_ids: ["performance"], ships: [],
                 images: [] };
      },
    })} />);

    fireEvent.click(await screen.findByText("Region 0"));
    await waitFor(() => expect(asked).toEqual(["h-0"]));

    // Deleted in BlazeMeter's own UI, and a Refresh finds out.
    listing.splice(0, 1);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText(/no longer in the account/)).toBeTruthy();
    // It does not send the reader back to the button that just answered, and it
    // does not say "reload" -- a reload loses a pasted AUTH_TOKEN.
    expect(screen.queryByText(/reload/i)).toBeNull();
    // The refresh wrote the list and nothing else: no second facts read for a
    // location that is gone, and none for the one that happens to be left.
    expect(asked).toEqual(["h-0"]);
    // ...and the list it wrote is the account's.
    expect(screen.getByText("Region 1")).toBeTruthy();
    expect(screen.queryByText("Region 0")).toBeNull();
  });

test("a refresh that fails leaves the list it could not replace on screen",
  async () => {
    // The rule the whole page keeps: a read that failed has said nothing about
    // what the account holds, and blanking here would answer "could not read"
    // with "there is nothing there".
    render(<App api={accountOf([loc("h-0", "Region 0")], {
      refresh: async () => { throw new Error("BlazeMeter is unreachable"); },
    })} />);

    expect(await screen.findByText("Region 0")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText(/unreachable/)).toBeTruthy();
    expect(screen.getByText("Region 0")).toBeTruthy();
  });

test("creating a location sends what the form holds, and selects what comes back",
  async () => {
    const created: unknown[] = [];
    const listing = [loc("h-0", "Region 0")];
    const asked: string[] = [];
    render(<App api={accountOf(listing, {
      createLocation: async (body) => {
        created.push(body);
        const made = loc("h-new", body.name);
        listing.push(made);
        return made;
      },
      facts: async (harborId: string) => {
        asked.push(harborId);
        return { harbor_id: harborId, func_ids: ["performance"], ships: [],
                 images: [] };
      },
    })} />);

    fireEvent.click(await screen.findByRole("button", { name: /New location/ }));
    // Named after the workspace it would be created in, because the workspace is
    // chosen at the foot of the drawer and this is a write into it.
    const name = await screen.findByLabelText(/^Name \(created in workspace/);
    fireEvent.change(name, { target: { value: "Frankfurt" } });
    fireEvent.change(screen.getByLabelText(/^Slots/), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText(/^Threads per engine/),
                     { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    // The account this writes into, and the four fields, exactly as typed.
    await waitFor(() => expect(created.length).toBe(1));
    expect(created[0]).toEqual({
      name: "Frankfurt", account_id: 1, workspace_id: 10,
      func_ids: ["performance"], slots: 4, threads_per_engine: 250,
    });
    // Selected, so the agent section below is about the location just made --
    // which is the only reason anyone makes one here. It is on screen twice by
    // then (its row, and the path line under the step), so the read of the
    // account is what this asserts on.
    await waitFor(() => expect(asked).toEqual(["h-new"]));
    expect(screen.getAllByText("Frankfurt").length).toBeGreaterThan(0);
  });

test("a GUI Functional location says its slot minimum before Create is pressed",
  async () => {
    // #159. BlazeMeter refuses the create outright below two, and `slots`
    // defaults to one, so this form's own default was a 400 on every GUI
    // Functional location it made. The rule is stated on the form, not only
    // after the account has thrown the write away.
    const created: unknown[] = [];
    render(<App api={accountOf([loc("h-0", "Region 0")], {
      funcIdVocabulary: async () => vocabulary([
        { id: "performance", label: "Performance", changes_images: true,
          covered: true, sub_func_ids: [] },
        { id: "functionalGui", label: "GUI Functional", changes_images: true,
          covered: true, sub_func_ids: [] },
      ], "account"),
      slotMinimums: async () => SLOT_MINIMUMS,
      createLocation: async (body) => { created.push(body); return loc("h-new", "x"); },
    })} />);

    fireEvent.click(await screen.findByRole("button", { name: /New location/ }));
    const name = await screen.findByLabelText(/^Name \(created in workspace/);
    fireEvent.change(name, { target: { value: "Frankfurt" } });
    // Nothing is said about slots while no rule reaches the declaration: the
    // number is a real cost and most locations run one.
    expect(screen.queryByText(/needs at least/)).toBeNull();

    fireEvent.click(screen.getByLabelText("GUI Functional"));
    // Said as soon as the box is ticked, whether or not the number is wrong
    // yet -- the constraint is what the form is for, and a rule that only
    // speaks up after it has taken Create away reads as the form breaking.
    expect(await screen.findByText(/GUI Functional needs at least 2/)).toBeTruthy();
    // ...and while the default stands, Create is held with BlazeMeter's own
    // sentence rather than a paraphrase of it.
    expect(screen.getByText(/Parallel engine runs must be greater than 1/))
      .toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(created).toEqual([]);

    // Typing the number the form asked for clears it, and nothing raised it
    // on anybody's behalf: what is sent is what is on screen.
    fireEvent.change(screen.getByLabelText(/^Slots/), { target: { value: "2" } });
    expect(screen.queryByText(/Parallel engine runs/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(created.length).toBe(1));
    expect(created[0]).toMatchObject({
      func_ids: ["performance", "functionalGui"], slots: 2,
    });
  });

test("creating an agent in an empty location keeps the credential it is issued with",
  async () => {
    const listing = [loc("h-0", "Empty")];
    render(<App api={accountOf(listing, {
      createShip: async (_harborId: string, name: string) => {
        const ship = { id: "s-new", name, state: "IDLE" };
        listing[0] = { ...listing[0], ships: [ship] };
        return { ship, auth_token: "tok-from-the-account", token_error: null };
      },
    })} />);

    fireEvent.click(await screen.findByText("Empty"));
    // A location with no agents opens on the create form: there is nothing to
    // pick, so there is no list to offer first.
    expect(await screen.findByText(/has no agents yet/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name"),
                     { target: { value: "k8s-prod" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    // The agent now exists (its row, the section summary and the path line
    // under the step all name it) and is the one selected.
    await waitFor(() =>
      expect(screen.getAllByText("k8s-prod").length).toBeGreaterThan(1));
    expect(screen.getByText("token in hand")).toBeTruthy();
    // ...and the token it was created with, in the field. This is the one moment
    // it is free -- nothing reads a credential back afterwards.
    await waitFor(() => expect(
      screen.getByPlaceholderText(/paste the token this agent was created with/),
    ).toHaveProperty("value", "tok-from-the-account"));
  });

// -- ...and keeping it, which is the other half of the same moment -----------
// The credential is captured where it is free and the browser was the only copy
// of it, so a refresh threw it away for good: no API reads an AUTH_TOKEN back,
// and the next bundle silently carried a placeholder for an agent this app had
// created a minute earlier (#123). What the server remembers is what these
// drive, and every one of them asserts on the *bundle request* rather than on
// the field -- a field showing a value the request does not send is the failure
// being replaced, not evidence against it.

/** The bundle request most recently sent, or an empty one before the first. */
const last = (sent: Options[]): Options => sent[sent.length - 1] ?? {};

/** The server's memory, standing in. Filled by the one call that mints and read
 *  by the one that looks up, which is the pairing under test. */
function mintingAccount(listing: Location[], minted: Record<string, string>,
                        asked: Options[], extra: Partial<Api> = {}) {
  return accountOf(listing, {
    mintedToken: async (shipId: string) =>
      ({ auth_token: minted[shipId] ?? null }),
    generate: async (_facts: Facts, options: Options) => {
      asked.push(options);
      return { files: [], token: { branch: "given" as const,
                                   ship_id: null, message: "" } };
    },
    ...extra,
  });
}

test("an agent this app created keeps its credential across a refresh",
  async () => {
    const listing = [loc("h-0", "Empty")];
    const minted: Record<string, string> = {};
    const asked: Options[] = [];
    const api = mintingAccount(listing, minted, asked, {
      createShip: async (_harborId: string, name: string) => {
        const ship = { id: "s-new", name, state: "IDLE" };
        listing[0] = { ...listing[0], ships: [ship] };
        // The server remembers as it hands it over; there is no second moment
        // at which it could, which is the whole reason it does this one.
        minted[ship.id] = "tok-at-creation";
        return { ship, auth_token: "tok-at-creation", token_error: null };
      },
    });

    render(<App api={api} />);
    fireEvent.click(await screen.findByText("Empty"));
    fireEvent.change(await screen.findByLabelText("Name"),
                     { target: { value: "k8s-prod" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(
      asked.some((o) => o.auth_token === "tok-at-creation")).toBe(true));

    // The refresh, and what it may not carry the token in: session.strip is the
    // whole safety argument and this change does not touch it, so the value has
    // to come back from the server or not at all.
    cleanup();
    expect(JSON.stringify(sessionStorage)).not.toContain("tok-at-creation");
    asked.length = 0;
    render(<App api={api} />);

    // Nothing typed, and the bundle carries the real credential rather than the
    // placeholder it used to fall to.
    await waitFor(() => expect(last(asked)).toMatchObject({
      ship_id: "s-new", auth_token: "tok-at-creation" }));
  });

test("a credential is only ever found under the agent it was minted for",
  async () => {
    // Two agents in one location, which is what a store holding "the token" and
    // one holding a token per ship cannot both survive. The page used to keep
    // exactly one and clear it whenever the target moved -- the same guarantee,
    // held together by every caller remembering to let go.
    const listing = [loc("h-0", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" },
      { id: "s-2", name: "agent-2", state: "IDLE" },
    ])];
    const asked: Options[] = [];
    render(<App api={mintingAccount(
      listing, { "s-1": "tok-one", "s-2": "tok-two" }, asked)} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(await screen.findByText("agent-2"));
    await waitFor(() => expect(last(asked)).toMatchObject({
      ship_id: "s-2", auth_token: "tok-two" }));
    // Back to the first, which is the move the old page had no answer for.
    fireEvent.click(screen.getByText("agent-1"));
    await waitFor(() => expect(last(asked)).toMatchObject({
      ship_id: "s-1", auth_token: "tok-one" }));
    // ...and neither agent's credential was ever attached to the other, in any
    // request, including the ones in between.
    expect(asked.filter(
      (o) => o.auth_token === "tok-one" && o.ship_id !== "s-1")).toEqual([]);
    expect(asked.filter(
      (o) => o.auth_token === "tok-two" && o.ship_id !== "s-2")).toEqual([]);
  });

test("a token typed by hand beats the remembered one, and it does not come back",
  async () => {
    const listing = [loc("h-0", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" }])];
    const minted: Record<string, string> = { "s-1": "tok-remembered" };
    const forgotten: string[] = [];
    const asked: Options[] = [];
    const api = mintingAccount(listing, minted, asked, {
      forgetMintedToken: async (shipId: string) => {
        forgotten.push(shipId);
        return { forgotten: delete minted[shipId] };
      },
    });

    render(<App api={api} />);
    fireEvent.click(await screen.findByText("Perf"));
    const field = await screen.findByPlaceholderText(/paste the token/);
    await waitFor(() => expect(field).toHaveProperty("value", "tok-remembered"));

    fireEvent.change(field, { target: { value: "tok-typed" } });
    await waitFor(() => expect(last(asked).auth_token).toBe("tok-typed"));
    // Evicted at the server, not merely out-ranked in the page: the page cannot
    // keep the pasted one (session.strip), so a remembered copy left in place
    // is one that silently replaces it on the next load.
    await waitFor(() => expect(forgotten).toEqual(["s-1"]));

    cleanup();
    asked.length = 0;
    render(<App api={api} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    // A bundle with no token, which is the honest state -- what was typed is
    // gone with the page that held it, and what it replaced does not return.
    expect(asked.every((o) => !o.auth_token)).toBe(true);
  });

test("an agent this app could not be asked about claims nothing about its token",
  async () => {
    // The distinction this codebase keeps everywhere: an agent nobody minted a
    // token for and an agent nobody could be asked about are different answers,
    // and only the first may say a credential cannot be read back. Read as the
    // same thing, the page tells somebody their own agent's token is
    // unrecoverable while the server is holding it.
    const listing = [loc("h-0", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" }])];
    render(<App api={accountOf(listing, {
      mintedToken: async () => { throw new Error("no route there"); },
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    expect(await screen.findByText(/could not ask this app/)).toBeTruthy();
    expect(screen.queryByText(/cannot be read back/)).toBeNull();

    // ...where an account that answered gets the sentence that was always here.
    // A fresh page, not a reload: the snapshot would restore the selection and
    // this half is about making the same one.
    cleanup();
    sessionStorage.clear();
    render(<App api={accountOf(listing)} />);
    fireEvent.click(await screen.findByText("Perf"));
    expect(await screen.findByText(/cannot be read back/)).toBeTruthy();
  });

test("a lone agent that is reporting is not auto-picked, and says why when it is",
  async () => {
    // Fresh by the rule in heartbeat.ts, which is the whole difference between
    // this location and the ones above.
    const live = { id: "s-live", name: "agent-live", state: "IDLE",
                   lastHeartBeat: Date.now() / 1000 - 10 };
    // Named for the case rather than "Busy": the page prints a location's name
    // in the list and again in the header once one is chosen, so a name that is
    // also a word the page uses made `findByText` ambiguous the moment React 19
    // had both on screen at once.
    render(<App api={accountOf([loc("h-0", "Reporting", [live])])} />);

    fireEvent.click(await screen.findByText("Reporting"));
    // Counted as online in the row for its location...
    expect(await screen.findByText(/1 agent · 1 online/)).toBeTruthy();
    // ...and left unpicked: a new deployment on an identity that is already
    // running conflicts with the install that is working.
    expect(screen.queryByText(/already running somewhere/)).toBeNull();

    fireEvent.click(screen.getByText("agent-live"));
    expect(await screen.findByText(/already running somewhere/)).toBeTruthy();
  });

test("a second click on a location's header folds it, and chooses nothing else",
  async () => {
    const asked: string[] = [];
    const live = { id: "s-live", name: "agent-live", state: "IDLE",
                   lastHeartBeat: Date.now() / 1000 - 10 };
    render(<App api={accountOf([loc("h-0", "Dublin", [live])], {
      facts: async (harborId: string) => {
        asked.push(harborId);
        return { harbor_id: harborId, func_ids: ["performance"], ships: [],
                 images: [] };
      },
    })} />);

    // The row itself, not the path line under the flow -- both say "Dublin"
    // once the location is chosen, and only one of them is a control.
    const header = await screen.findByRole("button", { name: /Dublin/ });

    // Choosing opens it onto the settings, which is a lot of panel.
    fireEvent.click(header);
    expect(await screen.findByLabelText("Dublin settings")).toBeTruthy();
    expect(header.getAttribute("aria-expanded")).toBe("true");

    // The same header folds it back up...
    fireEvent.click(header);
    await waitFor(() => expect(
      screen.queryByLabelText("Dublin settings")).toBeNull());
    expect(header.getAttribute("aria-expanded")).toBe("false");

    // ...and that is all it does. The location is still the one being generated
    // for -- its agents are still listed, the path line still names it, and no
    // second read of the account was provoked. Folding a panel that changes
    // what the bundle is for would be a strange way to hide some text.
    expect(screen.getByText("agent-live")).toBeTruthy();
    const bar = screen.getByText("account").parentElement!.parentElement!;
    expect(bar.textContent).toMatch(/location.*Dublin/);
    expect(asked).toEqual(["h-0"]);
  });

test("the path under the flow starts at the account, not at the location",
  async () => {
    const live = { id: "s-live", name: "agent-live", state: "IDLE",
                   lastHeartBeat: Date.now() / 1000 - 10 };
    render(<App api={accountOf([loc("h-0", "Dublin", [live])])} />);

    fireEvent.click(await screen.findByText("Dublin"));
    fireEvent.click(await screen.findByText("agent-live"));

    // All four, in order. The account and the workspace are chosen at the foot
    // of the drawer, which is shut for most of a session, so this line is the
    // only place on screen that says whose account a bundle is being built for
    // -- and two customers' bundles differ in exactly that.
    const bar = screen.getByText("account").parentElement!.parentElement!;
    await waitFor(() => expect(bar.textContent).toMatch(
      /account.*Alpha.*workspace.*Alpha workspace.*location.*Dublin.*agent.*agent-live/));
  });

test("the account menu is reachable on the view whose subject is the account",
  async () => {
    // Layout, so the assertion is on the classes that do it -- jsdom lays
    // nothing out, exactly as the clipping test below.
    //
    // The shell was `min-h-screen`, so the *document* grew to whatever the view
    // rendered and the `overflow-y-auto` pane never had a bounded parent to
    // scroll inside. On a real account (166 workspaces) Account capacity is
    // 11,000px tall; the drawer stretched to match and the account menu at its
    // foot went that far below the fold -- the control that switches account,
    // out of reach on the account rollup. Generate hid it because StepFlow pins
    // itself to `100vh - 6.75rem` and scrolls its own step.
    render(<App api={accountOf([loc("h-0", "Dublin")])} />);

    const capacityTab = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Account capacity/ });
    await waitFor(() => expect(capacityTab.disabled).toBe(false));
    fireEvent.click(capacityTab);

    // The control is in the drawer on this view -- it always was -- and the
    // shell is the window's height, which is what keeps it on screen.
    const acct = screen.getByTitle(/the key everything is read with/);
    const shell = document.querySelector("div.h-screen")!;
    expect(shell).not.toBeNull();
    expect(shell.contains(acct)).toBe(true);
    // ...and the scrolling belongs to the pane beside the drawer, not to the
    // page. A second `h-screen` would not save it if this were static.
    const pane = document.querySelector("div.overflow-y-auto")!;
    expect(pane.contains(acct)).toBe(false);
    expect(shell.contains(pane)).toBe(true);
  });

test("the workspace picker is not clipped by the row it grows into", async () => {
  // A CSS clip, which is the one kind of breakage nothing else here can see:
  // the row animates by growing inside `overflow-hidden`, and the picker's list
  // hangs out of that box, so it was cut to the height of the field -- one row
  // of a 166-workspace account. jsdom does no layout, so the assertion is on
  // the class that does the clipping rather than on anything measured.
  vi.useFakeTimers();
  render(<App api={accountOf([loc("h-0", "Dublin")])} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });

  fireEvent.click(screen.getByTitle(/the key everything is read with/));
  // Anchored: the Field's hint is inside its label, so the accessible name is
  // the label and the sentence under it.
  const field = screen.getByLabelText(/^Workspace/);
  // The animation's own wrapper: the grid row, and the box inside it that the
  // height is clipped to.
  const clipped = () => field.closest("div.overflow-hidden");

  // Hidden while the height is moving, so the row still grows in...
  expect(clipped()).not.toBeNull();
  // ...and released once it has stopped, which is when a list has to be able to
  // leave it.
  await tick(400);
  expect(clipped()).toBeNull();
});

// -- what survives a refresh, and when it may be written back ----------------

test("a refresh keeps the confirmations, and keeps them attached to what was confirmed",
  async () => {
    // A refresh is not a decision. Asking somebody to confirm again what they
    // already confirmed is asking them to repeat themselves to prove the
    // browser was listening.
    const listing = [loc("h-perf", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" },
      { id: "s-2", name: "agent-2", state: "IDLE" },
    ])];
    const snapshot = (ship: string) => ({
      sourceMode: "connect" as const, accountId: 1, workspaceId: 10,
      harborId: "h-perf", shipId: "s-1",
      confirmed: { loc: "h-perf", ship },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "ns" }, step: 0, view: "flow" as const,
      plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });

    session.save(snapshot("s-1"));
    render(<App api={accountOf(listing)} />);
    // Finished on arrival, with nothing pressed this time round.
    await waitFor(() => expect(screen.getByRole<HTMLButtonElement>(
      "button", { name: /Next/ }).disabled).toBe(false));

    // ...and it is still a confirmation *of an agent*. Restored beside a
    // different one -- the location's list changed under it, or the snapshot
    // is older than the choice -- it does not answer for this pairing. Stored
    // as a flag it would have, which is the whole reason it is not one.
    cleanup();
    sessionStorage.clear();
    session.save(snapshot("s-2"));
    render(<App api={accountOf(listing)} />);
    await waitFor(() => expect(screen.getByText(/confirm the agent/)).toBeTruthy());
    expect(screen.getByRole<HTMLButtonElement>(
      "button", { name: /Next/ }).disabled).toBe(true);
  });

test("nothing is written back over a saved session until the restore has resolved",
  async () => {
    // Written with the page's own writer, at the page's own version.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-dublin", shipId: "s-1",
      confirmed: { loc: "h-dublin", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "restored-ns" }, step: 1, view: "flow",
      plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });

    // The key check is the last thing the mount effect waits on. Held open,
    // it is the refresh this guard was written for: the server had not
    // answered, the page saved the empty state it was about to restore *from*,
    // and every selection was gone for good.
    const key = deferred<Awaited<ReturnType<Api["keyStatus"]>>>();
    const listing = [
      loc("h-0", "Region 0"),
      loc("h-dublin", "Dublin", [{ id: "s-1", name: "agent-1", state: "IDLE" }]),
    ];
    render(<App api={accountOf(listing, {
      keyStatus: () => key.promise,
    })} />);

    // The options are back on screen before the connection resolves -- which is
    // the ordering, not an accident of it: the location list has to arrive
    // already filtered to the restored workspace.
    const ns = await screen.findByPlaceholderText("e.g. blazemeter");
    expect(ns).toHaveProperty("value", "restored-ns");
    // ...and the snapshot they came from is untouched. None of these four is
    // page state yet -- they are still waiting for the account to confirm the
    // things they name still exist -- so anything written now writes nulls
    // over them.
    expect(session.load()).toMatchObject({
      accountId: 1, workspaceId: 10, harborId: "h-dublin", shipId: "s-1",
    });

    key.settle({
      connected: true, user: { email: "someone@example.com" },
      default_account_id: 1, key_id: "key-1",
    });

    // Only now does the page write. Asserted with an edit on top of the
    // restored ids, so this cannot pass by nothing having been written at all:
    // the snapshot has to hold both the typed value and the four ids.
    fireEvent.change(ns, { target: { value: "typed-ns" } });
    await waitFor(() => expect(session.load()).toMatchObject({
      accountId: 1, workspaceId: 10, harborId: "h-dublin", shipId: "s-1",
      step: 1, options: { namespace: "typed-ns" },
      // Connected, nothing is declared -- and nothing is written down that
      // could pin the next load to a functionality the account never said. The
      // functionality here is derived from the location's funcIds every time (#118).
      declaredFunctionalities: [],
    }));
  });

test("a key check that could not be made keeps the ids, and a later connect re-selects them",
  async () => {
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-dublin", shipId: "s-1",
      confirmed: { loc: "h-dublin", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "restored-ns" }, step: 1, view: "flow",
      plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });

    const listing = [
      loc("h-0", "Region 0"),
      loc("h-dublin", "Dublin", [{ id: "s-1", name: "agent-1", state: "IDLE" }]),
    ];
    render(<App api={accountOf(listing, {
      // The refusal this test is about. Nothing has said anything about the
      // four ids the snapshot holds -- the account could not be asked.
      keyStatus: async () => { throw new Error("the server did not answer"); },
      keySet: async () => ({ user: { email: "someone@example.com" },
                             default_account_id: 1, key_id: "key-1" }),
    })} />);

    // The restore has resolved -- on the rejection, which is the only way it
    // can resolve here -- so the page is writing again...
    const ns = await screen.findByPlaceholderText("e.g. blazemeter");
    await waitFor(() => expect(ns).toHaveProperty("value", "restored-ns"));
    fireEvent.change(ns, { target: { value: "typed-ns" } });
    // ...and what it writes still carries the four ids. Asserted with the edit
    // in it, so it cannot pass by nothing having been written at all.
    await waitFor(() => expect(session.load()).toMatchObject({
      options: { namespace: "typed-ns" },
      accountId: 1, workspaceId: 10, harborId: "h-dublin", shipId: "s-1",
    }));
    // Kept is not selected: no account has confirmed the location, so nothing
    // on the page is pointed at one.
    expect(screen.queryByText("Dublin")).toBeNull();

    // Connect for real. This is the next attempt the ids were kept for.
    fireEvent.click(screen.getByTitle(/not connected/));
    fireEvent.click(screen.getByRole("button", { name: "Connect…" }));
    const form = within(
      screen.getByRole("dialog", { name: "Connect to BlazeMeter" }));
    fireEvent.change(form.getByLabelText("Key ID"), { target: { value: "id-1" } });
    fireEvent.change(form.getByLabelText("Secret"), { target: { value: "sec" } });
    fireEvent.click(form.getByRole("button", { name: "Connect" }));

    // The location comes back, and the agent inside it. Read on step 1, which
    // the restored step is not: both are on screen twice there -- in their
    // list, and in the summary of what the step is for -- which is what a
    // selection looks like here, rather than an id in storage.
    fireEvent.click(
      await screen.findByRole("button", { name: /Capacity & agent/ }));
    await waitFor(() =>
      expect(screen.getAllByText("Dublin").length).toBeGreaterThan(1));
    await waitFor(() =>
      expect(screen.getAllByText("agent-1").length).toBeGreaterThan(1));
  });

test("an id the account no longer has is written away once the account has said so",
  async () => {
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-gone", shipId: "s-gone",
      confirmed: { loc: "h-gone", ship: "s-gone" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      // Step 1, where the location list is, so the answer arriving is visible.
      options: { namespace: "restored-ns" }, step: 0, view: "flow",
      plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    // The account answers, and the location the snapshot named is not in it.
    render(<App api={accountOf([loc("h-0", "Region 0")])} />);

    expect(await screen.findByText("Region 0")).toBeTruthy();
    // Both ids go: this is the answer that refutes them, and the agent belonged
    // to the location that is gone. The account and workspace are still there
    // and stay -- being kept is not being kept indiscriminately.
    await waitFor(() => expect(session.load()).toMatchObject({
      accountId: 1, workspaceId: 10, harborId: null, shipId: null,
    }));
  });

// -- and the one input that decides the bundle and did not survive it (#118) --
// In manual entry the functionality radio is not a view over a location: it is the
// declaration. It decides the funcId the typed identity is said to run, which
// decides the facts, which decides the images the bundle carries. Everything
// else the page needs to rebuild that identity already survived a refresh --
// the typed harbor id, the typed ship id, the options -- and this did not, so a
// reload fell back to the first served functionality and a Service virtualization
// identity came back a performance one.
//
// Driven through the page and asserted on the *request*, not on which radio
// looks selected: what went wrong is the facts that were gathered, and a radio
// agreeing with itself would have looked the same either way.

/** A well-formed harbor id and ship id: manualComplete checks the shape, and
 *  nothing is requested for values that are not one. */
const TYPED = { harbor: "0a1b2c3d4e5f60718293a4b5",
                ship: "6c5b4a39281706f5e4d3c2b1" };

/** The manual-entry page: no key at all -- manual entry is for an account
 *  nobody here can reach -- with both functionalities and the two funcIds that pick
 *  different images served. Records the funcIds of every facts request, which
 *  is where the declaration ends up. */
function manualPage(asked: string[][], generated: Options[] = [],
                    extra: Partial<Api> = {}) {
  return twoFunctionalityAccount(generated, {
    keyStatus: async () => ({ connected: false }),
    funcIdVocabulary: async () => vocabulary([
      { id: "performance", label: "Performance", changes_images: true,
        covered: true, sub_func_ids: [] },
      { id: "mockServices", label: "Service Virtualization", changes_images: true,
        covered: true, sub_func_ids: [] },
    ], "baseline"),
    manualFacts: async (b) => {
      asked.push(b.func_ids);
      return {
        facts: { harbor_id: b.harbor_id, func_ids: b.func_ids,
                 ships: [{ id: b.ship_id, name: "agent-1" }], images: [] },
        gui_images_incomplete: false,
      };
    },
    ...extra,
  });
}

/** Type the identity in, and open the step the declaration is made on. */
async function declareManually() {
  fireEvent.click(await screen.findByRole(
    "radio", { name: /Enter values manually/ }));
  fireEvent.change(screen.getByLabelText(/^Harbor ID/),
                   { target: { value: TYPED.harbor } });
  fireEvent.change(screen.getByLabelText(/^Ship ID/),
                   { target: { value: TYPED.ship } });
  fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
}

test("declaring a functionality in manual entry suggests its namespace",
  async () => {
    // Connected, picking a *location* that runs virtual services suggests
    // blazemeter-sv. Manually there is no location to read it off -- the radio
    // is how it is said -- and the suggestion used to arrive through a facts
    // read-back that fired when the ship id was finished being typed. Not
    // reading the declaration back out of the facts it produced is the whole of
    // #118, so the suggestion belongs on the control that declares, which is
    // also where the connected page puts it: on the act, not on a re-read.
    const generated: Options[] = [];
    render(<App api={manualPage([], generated)} />);
    await declareManually();
    fireEvent.click(card("mockServices").getByRole("checkbox"));

    await waitFor(() => expect(generated.length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(generated[generated.length - 1].namespace).toBe("blazemeter-sv"));
  });

test("a functionality declared in manual entry is what the facts are gathered "
     + "for after a refresh",
  async () => {
    const asked: string[][] = [];
    const generated: Options[] = [];
    render(<App api={manualPage(asked, generated)} />);
    await declareManually();

    // The declaration: this identity runs virtual services. In manual mode the
    // radio is the control rather than a chip, because there is no account to
    // read the answer off.
    fireEvent.click(await within(document.getElementById("cfg-f-mockServices")!)
      .findByLabelText("Enabled"));
    // ...and it is configured as one, so the reload has something of the
    // functionality's own to lose as well.
    fireEvent.click(within(document.getElementById("cfg-f-mockServices")!)
      .getByRole("switch"));

    // What the page asks the facts for, before the reload.
    await waitFor(() => expect(asked[asked.length - 1]).toEqual(["mockServices"]));
    await waitFor(() => expect(
      generated[generated.length - 1]?.sv_ingress).toBe("nginx"));
    const before = generated[generated.length - 1];

    // The refresh. sessionStorage survives it, which is the whole mechanism.
    const asBefore = asked.length;
    cleanup();
    render(<App api={manualPage(asked, generated)} />);

    // A request of its own, rather than the one still on the list from before
    // the reload -- and then long enough for a late one to land behind it. The
    // facts effect is debounced and the vocabulary it needs is served, so a
    // second request under a different declaration is exactly the shape of this
    // failure.
    await waitFor(() => expect(asked.length).toBeGreaterThan(asBefore));
    await new Promise((r) => setTimeout(r, 400));
    // The acceptance criterion, as the assertion. It was ["performance"]: the
    // declaration was not in the snapshot, so the page fell back to the first
    // served functionality and gathered another functionality's images for an identity
    // nobody had re-declared.
    expect(asked.slice(asBefore)).toEqual([["mockServices"]]);

    // The functionality's own options came back with it. Restored without the
    // declaration they were cleared, correctly, by the patch that empties a
    // functionality the location does not run -- the page had just been told it runs
    // something else.
    const after = generated[generated.length - 1];
    expect(after.sv_ingress).toBe("nginx");
    // ...and the namespace is the one that was saved. It is generated into
    // every manifest, so a restore that suggests over it is a refresh changing
    // the bundle by another route.
    expect(after.namespace).toBe(before.namespace);
  });

test("a restored declaration waits for the vocabulary rather than being lost to it",
  async () => {
    // The same failure by the other route, and the one a fast localhost hides.
    // A declaration stands for a funcId, and until the vocabulary lands there
    // is nothing to check the restored one against -- so the identity is
    // gathered for no funcId at all, which must not be mistaken for an answer
    // about what was declared. Read the wrong way round it falls to the first
    // served functionality and the declaration is gone again, with the
    // namespace suggestion following it.
    //
    // It was /api/func-ids that was deferred here, because a declaration used
    // to be turned into a funcId through `changes_images`: two vocabularies had
    // to have landed. Since #149 the declaration *is* the funcId and only the
    // one list decides -- fewer ways to be outstanding, and the same rule about
    // what an outstanding one may be read as.
    const served = deferred<Awaited<ReturnType<Api["functionalities"]>>>();
    const asked: string[][] = [];
    session.save({
      sourceMode: "manual", accountId: null, workspaceId: null,
      harborId: null, shipId: null, confirmed: { loc: null, ship: null },
      manual: { harbor_id: TYPED.harbor, ship_id: TYPED.ship },
      declaredFunctionalities: ["mockServices"],
      options: { namespace: "blazemeter-sv" }, step: 1, view: "flow",
      plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    render(<App api={manualPage(asked, [], {
      functionalities: () => served.promise,
    })} />);

    // The identity is gathered for nothing while that list is outstanding,
    // which is fine -- and is exactly what must not be mistaken for an answer
    // about what was declared.
    await waitFor(() => expect(asked[asked.length - 1]).toEqual([]));
    served.settle([
      { id: "performance", label: "Performance", namespace: "blazemeter",
        runs_engine: true },
      { id: "mockServices", label: "Service Virtualization",
        namespace: "blazemeter-sv", runs_engine: false },
    ]);

    await waitFor(() => expect(asked[asked.length - 1]).toEqual(["mockServices"]));
    await new Promise((r) => setTimeout(r, 400));
    expect(asked[asked.length - 1]).toEqual(["mockServices"]);
    expect(screen.getByPlaceholderText("e.g. blazemeter"))
      .toHaveProperty("value", "blazemeter-sv");
  });

test("a restored declaration the vocabulary no longer offers is dropped, not sat on",
  async () => {
    const asked: string[][] = [];
    session.save({
      sourceMode: "manual", accountId: null, workspaceId: null,
      harborId: null, shipId: null, confirmed: { loc: null, ship: null },
      manual: { harbor_id: TYPED.harbor, ship_id: TYPED.ship },
      declaredFunctionalities: ["mockServices"],
      options: { namespace: "blazemeter-sv" }, step: 1, view: "flow",
      plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    render(<App api={manualPage(asked, [], {
      // The vocabulary this page is served no longer carries what the snapshot
      // named -- a functionality withdrawn, or a tab reloaded against a newer server.
      functionalities: async () => [
        { id: "performance", label: "Performance", namespace: "blazemeter",
        runs_engine: true },
      ],
    })} />);

    // Kept, it would name no funcId at all: the identity's facts would be
    // gathered as though nothing had been declared, with no radio selected to
    // say so or to change it with. So it is dropped, and the page lands where a
    // fresh manual session lands.
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 400));
    expect(asked[asked.length - 1]).toEqual(["performance"]);
    expect(card("performance").getByLabelText("Enabled"))
      .toHaveProperty("checked", true);
    // ...and the namespace read back is still the one read back. Dropping the
    // declaration is a decision about the declaration; rewriting a namespace
    // that is generated into every manifest is not part of it.
    expect(screen.getByPlaceholderText("e.g. blazemeter"))
      .toHaveProperty("value", "blazemeter-sv");
  });

// -- ...and the declaration is a list, because a location is (#151) -----------
// One id was tenable while `performance` claimed four funcIds. Since #149 it is
// not: a bundle declared for GUI functional alone carries `func_ids:
// ['functionalGui']`, which is a location nobody would create, and 71 of the 168
// locations in one real account run performance and GUI functional together.
// The claim below that only a page can make is that the *list* survives a
// refresh -- #118 exists because "the declaration is restored" was false once.

/** The three covered functionalities, as the server serves them. */
const THREE: Functionality[] = [
  { id: "performance", label: "Performance", namespace: "blazemeter",
    runs_engine: true },
  { id: "functionalGui", label: "GUI Functional", namespace: "blazemeter-gui",
    runs_engine: true },
  { id: "mockServices", label: "Service Virtualization",
    namespace: "blazemeter-sv", runs_engine: false },
];

test("a declaration of two functionalities is what the facts are gathered for "
     + "after a refresh",
  async () => {
    const asked: string[][] = [];
    const api = () => manualPage(asked, [], { functionalities: async () => THREE });
    render(<App api={api()} />);
    await declareManually();

    // Performance is what a fresh manual session opens on; this adds the
    // browser half, which is the pairing most of a real account's locations
    // run. Both boxes are ticked at once, which a radio could not say.
    fireEvent.click(card("functionalGui").getByLabelText("Enabled"));
    await waitFor(() => expect(asked[asked.length - 1])
      .toEqual(["performance", "functionalGui"]));
    expect(card("performance").getByLabelText("Enabled"))
      .toHaveProperty("checked", true);

    // The refresh. sessionStorage survives it, which is the whole mechanism.
    const asBefore = asked.length;
    cleanup();
    render(<App api={api()} />);

    // A request of its own, then long enough for a late one to land behind it:
    // the facts effect is debounced and the vocabulary it needs is served, so a
    // second request under a shorter declaration is exactly the shape of this
    // failure.
    await waitFor(() => expect(asked.length).toBeGreaterThan(asBefore));
    await new Promise((r) => setTimeout(r, 400));
    // The acceptance criterion, as the assertion. Restored as one id it was
    // ["performance"], and the GUI half of the bundle -- its grid and its
    // browser images -- was gathered for nothing.
    expect(asked.slice(asBefore)).toEqual([["performance", "functionalGui"]]);
    expect(card("functionalGui").getByLabelText("Enabled"))
      .toHaveProperty("checked", true);
  });

test("a restored declaration keeps the members the vocabulary still offers",
  async () => {
    // The check #118 added, now that there is more than one thing to check.
    // Dropping the whole declaration over one withdrawn member would inflict
    // that loss on the members that are still offered -- and dropping nothing
    // would gather the identity's facts for a funcId nothing serves, with no
    // box on screen to say so or to change it with.
    const asked: string[][] = [];
    session.save({
      sourceMode: "manual", accountId: null, workspaceId: null,
      harborId: null, shipId: null, confirmed: { loc: null, ship: null },
      manual: { harbor_id: TYPED.harbor, ship_id: TYPED.ship },
      declaredFunctionalities: ["performance", "functionalGui"],
      options: { namespace: "blazemeter" }, step: 1, view: "flow",
      plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    render(<App api={manualPage(asked, [], {
      // GUI functional is not in this build's vocabulary -- withdrawn, or a tab
      // reloaded against a newer server.
      functionalities: async () => [THREE[0], THREE[2]],
    })} />);

    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 400));
    expect(asked[asked.length - 1]).toEqual(["performance"]);
    expect(card("performance").getByLabelText("Enabled"))
      .toHaveProperty("checked", true);
    // ...and the namespace that was read back is still the one read back:
    // dropping a member is a decision about the declaration, and rewriting a
    // name generated into every manifest is not part of it.
    expect(screen.getByPlaceholderText("e.g. blazemeter"))
      .toHaveProperty("value", "blazemeter");
  });

test("declaring service virtualization clears the functionalities that run engines",
  async () => {
    // The one opinion this page is entitled to, and only where a location is
    // being decided. Crane applies one CPU/memory limit pair to every pod it
    // creates, so engine sizing and mock throughput cannot be set apart.
    const asked: string[][] = [];
    const generated: Options[] = [];
    render(<App api={manualPage(asked, generated, {
      functionalities: async () => THREE,
    })} />);
    await declareManually();
    fireEvent.click(card("functionalGui").getByLabelText("Enabled"));
    await waitFor(() => expect(asked[asked.length - 1])
      .toEqual(["performance", "functionalGui"]));

    fireEvent.click(card("mockServices").getByLabelText("Enabled"));

    // The declaration, and therefore the images, are service virtualization's
    // alone -- an SV agent carries crane, group-gateway and service-mock and no
    // taurus engine at all.
    await waitFor(() => expect(asked[asked.length - 1]).toEqual(["mockServices"]));
    expect(card("performance").getByLabelText("Enabled"))
      .toHaveProperty("checked", false);
    expect(card("functionalGui").getByLabelText("Enabled"))
      .toHaveProperty("checked", false);
    // ...and the namespace follows the one thing now declared.
    await waitFor(() => expect(
      generated[generated.length - 1].namespace).toBe("blazemeter-sv"));

    // Ticking an engine functionality again is the same statement the other way
    // round: whichever was ticked second is the one just asked for.
    fireEvent.click(card("performance").getByLabelText("Enabled"));
    await waitFor(() => expect(asked[asked.length - 1]).toEqual(["performance"]));
    expect(card("mockServices").getByLabelText("Enabled"))
      .toHaveProperty("checked", false);
  });

test("the reason it is exclusive is on screen before anything is ticked",
  async () => {
    // A rule that only speaks up after it has taken a tick away reads as the
    // page losing one.
    render(<App api={manualPage([], [], { functionalities: async () => THREE })} />);
    await declareManually();
    expect(await screen.findByText(/one CPU and memory limit pair/)).toBeTruthy();
  });

test("a location that already mixes the two is warned about, never blocked",
  async () => {
    // The asymmetry (#147). Connected, the location exists and nothing here can
    // un-mix it -- POST /api/locations/func-id was removed in #113, because
    // changing what a location *is* belongs in BlazeMeter's own UI. So the
    // bundle generates and the page says what the mixture costs.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-both", shipId: "s-1",
      confirmed: { loc: "h-both", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "blazemeter" },
      step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    const asked: Options[] = [];
    render(<App api={twoFunctionalityAccount(asked, {
      locations: async () => [{
        id: "h-both", name: "Both", funcIds: ["performance", "mockServices"],
        slots: 1, ships: [{ id: "s-1", name: "agent-1", state: "IDLE" }],
      }],
      facts: async () => ({
        harbor_id: "h-both", func_ids: ["performance", "mockServices"],
        ships: [{ id: "s-1", name: "agent-1" }], images: [],
      }),
    })} />);

    expect(await screen.findByText(/alongside load or browser tests/)).toBeTruthy();
    // Both cards are live -- the location runs both, and the warning is a
    // sentence rather than a view that hides one of them.
    expect(hasCard("performance")).toBe(true);
    expect(card("mockServices").queryByRole("switch")).not.toBeNull();
    // ...and nothing is blocked: the step advances and the bundle is requested.
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
  });

// -- the live preview, and the two things that decide when it is asked -------

test("the preview waits for the typing to stop", async () => {
    // /api/generate renders the whole bundle, so a preview that ran per
    // keystroke rendered it per keystroke. The folder field used to drive this
    // test; it was removed with the Save button, and the debounce it was
    // testing belongs to every option on the page -- so this types into one.
    const asked: Options[] = [];
    render(<App api={perfAccount({
      generate: async (_facts: Facts, options: Options) => {
        asked.push(options);
        return {
          files: [{ name: "crane.yaml", content: "kind: Deployment" }],
          token: { branch: "placeholder" as const, ship_id: "s-1",
                   message: "no AUTH_TOKEN — the bundle carries a placeholder" },
        };
      },
    })} />);
    await atDownloadStep();
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    const ns = await screen.findByLabelText(/^Namespace/);
    // Settled: the location's facts, the functionality it opens on and the option
    // defaults all move the configuration, and each moves it once.
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 400));
    const before = asked.length;

    // From here the clock is ours, because the point is what does *not* happen
    // inside the 250ms.
    vi.useFakeTimers();
    for (const typed of ["bzm", "bzm-", "bzm-ns"]) {
      fireEvent.change(ns, { target: { value: typed } });
      await tick(100);
    }
    // Three keystrokes, 300ms, no request.
    expect(asked.length).toBe(before);

    await tick(250);
    // ...and then exactly one, carrying what was typed rather than any of the
    // values it was typed through.
    expect(asked.length).toBe(before + 1);
    expect(asked[asked.length - 1].namespace).toBe("bzm-ns");
  });

// -- the watch, and what it is watching --------------------------------------

/** An agent that is up, which is all these two ask of a status read. */
const IDLE: AgentStatus = { state: "IDLE", heartbeat_age_s: 3, online: true };

test("the status poll moves with the agent, and leaves no interval behind",
  async () => {
    const polled: string[] = [];
    const both = loc("h-perf", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" },
      { id: "s-2", name: "agent-2", state: "IDLE" },
    ]);
    render(<App api={accountOf([both], {
      status: async (harborId: string, shipId: string) => {
        polled.push(`${harborId}/${shipId}`);
        return IDLE;
      },
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    // Two agents, so neither is auto-picked: the one being watched is the one
    // that was chosen, which is what makes changing it a change of target.
    fireEvent.click(await screen.findByText("agent-1"));
    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const watch = await screen.findByRole("switch", { name: /Watch agent status/ });

    // Installed before the click, because the click is what creates the
    // interval -- afterwards it would be a real one, and vi would advance a
    // clock nothing is using.
    vi.useFakeTimers();
    fireEvent.click(watch);
    // Read at once: ten seconds of "polling every 10s…" over an agent that is
    // already up reads as a page that has not started.
    expect(polled).toEqual(["h-perf/s-1"]);
    await tick(20_000);
    expect(polled).toEqual(["h-perf/s-1", "h-perf/s-1", "h-perf/s-1"]);

    // Move the target. The switch is still on -- what changed is which agent
    // the answers would be about.
    fireEvent.click(screen.getByRole("button", { name: /Capacity & agent/ }));
    fireEvent.click(screen.getByText("agent-2"));
    expect(polled[polled.length - 1]).toBe("h-perf/s-2");

    // The agent left behind is not still being polled beside the new one: one
    // request per tick, for the agent on screen. An interval that outlives its
    // effect keeps asking about an agent nothing is looking at, and every
    // change of agent adds another.
    await tick(20_000);
    expect(polled.filter((p) => p === "h-perf/s-1").length).toBe(3);
    expect(polled.filter((p) => p === "h-perf/s-2").length).toBe(3);
  });

test("the SV read travels by ref: typing in the namespace does not restart the poll",
  async () => {
    const asked: Options[] = [];
    const read: string[] = [];
    render(<App api={svAccount(asked, {
      status: async () => IDLE,
      svMocks: async (namespace: string) => {
        read.push(namespace);
        return { status: "no_mocks" as const, mocks: [],
                 message: "nothing deployed" };
      },
    })} />);

    fireEvent.click(await screen.findByText("Mocks"));
    // The location's own funcIds are what make this an SV watch, and the seed
    // that follows from them is what makes it configured -- so the poll reads
    // the namespace as well as the heartbeat only once that has landed.
    await waitFor(() =>
      expect(asked[asked.length - 1]?.sv_ingress).toBe("nginx"));
    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const watch = await screen.findByRole("switch", { name: /Watch agent status/ });

    vi.useFakeTimers();
    fireEvent.click(watch);
    expect(read).toEqual(["blazemeter"]);

    // One second short of the next tick, the namespace is edited. Back rather
    // than the stepper: the unfinished-group block on this step offers a
    // "Configure" button of its own, and both go to the same place.
    await tick(9_000);
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    fireEvent.change(screen.getByPlaceholderText("e.g. blazemeter"),
                     { target: { value: "mocks-ns" } });

    // Half a second later: nothing. A namespace in the dependency array tears
    // the interval down and stands a new one up, which reads at once -- so the
    // cluster would be read on every keystroke in the field.
    await tick(500);
    expect(read).toEqual(["blazemeter"]);

    // The tick that was already due arrives on time, and reads what the field
    // says now: the ref is what carries the new value into an interval that
    // was never restarted.
    await tick(1_000);
    expect(read).toEqual(["blazemeter", "mocks-ns"]);
  });

// -- the sizing, and the location it lands on -------------------------------
// The planner reaches nothing -- no key, no account, no cluster -- and that is
// the requirement rather than a property: it is the question somebody asks
// *before* they have any of it, which is why it is the first card of step 1
// rather than a view beside the flow. The first test below is that claim, made
// on a page nobody has connected. The second is the other half: what the
// profile says is filled into a location's own fields, and the only thing that
// reaches the account is Save.

/** A plan as core would answer it. The arithmetic is plan.py's and is tested
 *  there; what these two need is an answer that divides by the agents it was
 *  asked with, because that division is the whole reason a location re-asks. */
function planFor(body: {
  users?: string; agents?: string; vus_per_engine?: string;
  sizings?: { functionality: string; target: string; figure?: string }[];
}): CapacityPlan {
  const agents = Math.max(Number(body.agents) || 1, 1);
  // The card sends rows and the location panel sends `users`, and the route
  // takes both -- so the fake does too, or one of the two callers would be
  // testing a shape the server never sees.
  const perf = body.sizings?.find((s) => s.functionality === "performance");
  const users = Number(perf?.target ?? body.users);
  const vus = Number(perf?.figure || body.vus_per_engine) || 500;
  const engines = Math.ceil(users / vus);
  const perAgent = Math.ceil(engines / agents);
  return {
    users, vus_per_engine: vus,
    vus_per_engine_assumed: !(perf?.figure || body.vus_per_engine),
    sizings: [{ functionality: "performance", unit: "virtual users",
                target: users, per_pod: vus,
                per_pod_unit: "virtual users per engine",
                per_pod_source: perf?.figure || body.vus_per_engine
                  ? "supplied" : "assumed",
                pods: engines, pods_label: "engines" }],
    driven_by: "performance",
    engines, agents, engines_per_agent: perAgent, engines_per_node: 1,
    nodes_per_agent: perAgent, nodes: perAgent * agents,
    engine: { cpu: "2", memory: "8Gi", disk_gb: 60, tmp_gb: 40,
              supported_vus: 500 },
    // A node is one engine plus what the node spends on itself (1 CPU / 2Gi,
    // in generate.py), and the peak is that times the nodes. Coherent rather
    // than arbitrary because the summary line states all three, and a fixture
    // whose figures do not multiply cannot show whether the page's do.
    node: { cpu: "3", memory: "10Gi", disk_gb: 100 },
    peak: { cpu: String(perAgent * 3), memory: `${perAgent * 10}Gi`,
            disk_gb: perAgent * 60 },
    crane: { cpu_limit: "1", memory_limit: "2Gi" },
    location: { slots: perAgent, threads_per_engine: vus, override_cpu: 2,
                override_memory: 8192 },
    egress: ["a.blazemeter.com"], warnings: [],
    document: "# infrastructure request", document_file: "capacity-request.md",
  };
}

/** The four routes the unconnected page reads at mount, plus the two the
 *  sizing card needs. Everything else rejects by naming itself, so a card that
 *  had come to need an account could not pass any of these. */
const unconnected = (extra: Partial<Api>) => fakeApi({
  keyDetect: async () => ({ candidates: [], active_key_id: null }),
  keyStatus: async () => ({ connected: false }),
  optionDefaults: async () => ({ namespace: "blazemeter" }),
  funcIdVocabulary: async () => NO_VOCABULARY,
  functionalities: async () => [],
  svConstants: async () => ({ func_ids: [], ingress_types: [], backends: {} }),
  // Per model, as the route answers: the card reads each row's own rating,
  // and the one with no measured figure is null rather than absent.
  engineVus: async () => ({
    cpu: "2", memory: "8Gi", supported_vus: 500,
    rated: { performance: 500, functionalGui: 4, mockServices: null },
  }),
  sizingModels: async () => SIZING_MODELS,
  ...extra,
});

test("with no key connected, step 1 still makes a sizing", async () => {
  const asked: Parameters<Api["plan"]>[0][] = [];
  const api = unconnected({
    plan: async (body) => { asked.push(body); return planFor(body); },
  });
  render(<App api={api} />);

  // The card is on screen before anything is connected, and says it has no
  // answer yet rather than hiding until it does.
  expect(await screen.findByText("not sized yet")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(await screen.findByLabelText(/^Virtual users\*/),
                   { target: { value: "5000" } });

  // The summary is the answer, on the row that is visible with the editor shut.
  const summary = await screen.findByText(
    /5,000 virtual users · 10 engines × 2 CPU/);
  // Every step, because the total is node capacity: 10 engines at 2 CPU is 20
  // and the answer is 30, and the node line is where the difference enters. A
  // summary that skipped it read as arithmetic that does not work. Read off
  // textContent because the total is emphasised in a span of its own.
  expect(summary.textContent).toMatch(/10 engines × 2 CPU \/ 8Gi/);
  expect(summary.textContent).toMatch(/10 nodes × 3 vCPU \/ 10Gi/);
  expect(summary.textContent).toMatch(/30 vCPU \/ 100Gi total/);
  // Asked for the run, not for a location: how many agents will serve it is a
  // fact about a location, and there is no location here to have one.
  expect(asked[asked.length - 1].agents).toBeUndefined();
  // ...and the document that is the point of sizing without a cluster is
  // reachable from inside the editor.
  const download = screen.getByRole<HTMLButtonElement>(
    "button", { name: "Download" });
  await waitFor(() => expect(download.disabled).toBe(false));
});

test("each functionality is asked for in its own unit, and one has no figure",
  async () => {
    // The three models are served, so this is the page rendering a table
    // rather than a form somebody wrote three times. What it must get right is
    // the third: service virtualization has no measured figure, so there is no
    // box to type one into and the gap is stated instead of being an empty
    // field that reads as "not filled in yet".
    render(<App api={unconnected({ plan: async (b) => planFor(b) })} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));

    // Performance is ticked on a fresh page; the other two are offered.
    fireEvent.click(await screen.findByLabelText(/GUI Functional/));
    fireEvent.click(screen.getByLabelText(/Service Virtualization/));

    // A target each, in three units. (The asterisk is the required marker,
    // which is what tells a target apart from the per-pod figure beside it.)
    expect(screen.getByLabelText(/^Virtual users\*/)).toBeTruthy();
    expect(screen.getByLabelText(/^Browser instances\*/)).toBeTruthy();
    expect(screen.getByLabelText(/^Requests per second\*/)).toBeTruthy();

    // A figure box for the two that have a figure, and none for the one that
    // does not.
    expect(screen.getByLabelText(/^Virtual users per engine/)).toBeTruthy();
    expect(screen.getByLabelText(/^Browser instances per engine/)).toBeTruthy();
    expect(screen.queryByLabelText(/^Requests per second per core/)).toBeNull();
    expect(screen.getByText(
      /No measured figure for requests per second per core/)).toBeTruthy();
  });

test("a sizing nothing can size is the server's reason, never a node count",
  async () => {
    // The refusal is the explanation, and it is the server's sentence: this
    // page must not carry a second copy of why there is no figure. What it
    // owns is showing it where a plan would have gone.
    render(<App api={unconnected({
      plan: async () => { throw new Error("nothing measured here"); },
    })} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByLabelText(/Service Virtualization/));
    fireEvent.change(screen.getByLabelText(/^Requests per second\*/),
                     { target: { value: "2000" } });

    expect(await screen.findByText("nothing measured here")).toBeTruthy();
    // ...and the header still says there is no answer, rather than an old one.
    expect(screen.getByText("not sized yet")).toBeTruthy();
  });

test("a sizing saved under a name survives a refresh, and picking it fills the fields",
  async () => {
    const api = unconnected({ plan: async (b) => planFor(b) });
    const { unmount } = render(<App api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(await screen.findByLabelText(/^Virtual users\*/),
                     { target: { value: "7000" } });
    fireEvent.change(screen.getByLabelText(/^Save as/),
                     { target: { value: "Black Friday" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    // Written to the session snapshot by the page's own writer, which is what
    // the next render reads.
    await waitFor(() => expect(
      session.load()?.sizings?.some((s) => s.name === "Black Friday")).toBe(true));
    unmount();

    // The refresh.
    render(<App api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const target = await screen.findByLabelText<HTMLInputElement>(
      /^Virtual users\*/);
    fireEvent.change(target, { target: { value: "100" } });

    const picker = screen.getByLabelText<HTMLSelectElement>(/^Saved sizings/);
    expect([...picker.options].map((o) => o.value)).toContain("Black Friday");
    fireEvent.change(picker, { target: { value: "Black Friday" } });
    // Picking is the only thing here that could be called "apply", and all it
    // does is fill the fields -- which *are* the sizing.
    await waitFor(() => expect(target.value).toBe("7000"));
  });

test("the profile fills a location's settings, and Save is the only write",
  async () => {
    const asked: Parameters<Api["plan"]>[0][] = [];
    const sent: Record<string, string>[] = [];
    // What the account holds, moved only by a request that reaches it -- so
    // "before" on the second save is what the first one actually did.
    let held = loc("h-perf", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" },
      { id: "s-2", name: "agent-2", state: "IDLE" },
    ]);
    const state = () => ({
      slots: held.slots ?? null,
      threads_per_engine: held.threadsPerEngine ?? null,
      override_cpu: held.overrideCPU ?? null,
      override_memory: held.overrideMemory ?? null,
    });
    render(<App api={accountOf([held], {
      // The list is re-read on nothing here, so the fixture hands back what it
      // holds now rather than the array it was built from.
      locations: async () => [held],
      // Per model, as the route answers.
      engineVus: async () => ({
        cpu: "2", memory: "8Gi", supported_vus: 500,
        rated: { performance: 500, functionalGui: 4, mockServices: null },
      }),
      plan: async (body) => { asked.push(body); return planFor(body); },
      updateLocation: async (body) => {
        const { harbor_id: _h, ...fields } = body;
        sent.push(fields as Record<string, string>);
        const before = state();
        held = { ...held,
          slots: fields.slots ? Number(fields.slots) : held.slots,
          threadsPerEngine: fields.threads_per_engine
            ? Number(fields.threads_per_engine) : held.threadsPerEngine ?? null,
          // override_memory is deliberately not applied: BlazeMeter's own POST
          // accepts `threadsPerEngine` and drops it on some accounts, and a
          // field that comes back unstored is the case the answer exists for.
          overrideCPU: fields.override_cpu
            ? Number(fields.override_cpu) : held.overrideCPU ?? null };
        const after = state();
        const changed = Object.fromEntries(
          (Object.keys(after) as (keyof typeof after)[])
            .filter((k) => after[k] !== before[k]).map((k) => [k, after[k]]));
        return { location: held, changed, before, after,
                 ignored: fields.override_memory ? ["override_memory"] : [] };
      },
    })} />);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    fireEvent.change(await screen.findByLabelText(/^Virtual users\*/),
                     { target: { value: "5000" } });
    fireEvent.click(await screen.findByText("Perf"));

    // The row opens on this location's own arithmetic: 10 engines over its two
    // agents is 5 each, and writing the run's own figure into `slots` would
    // size the location for twenty.
    const panel = await screen.findByRole("region", { name: "Perf settings" });
    await waitFor(() => expect(asked.some((a) => a.agents === "2")).toBe(true));
    const field = (label: RegExp) =>
      within(panel).getByLabelText<HTMLInputElement>(label);
    await waitFor(() => expect(field(/^Engines per agent/).value).toBe("5"));
    expect(field(/^Virtual users per engine/).value).toBe("500");
    expect(field(/^Engine CPU request/).value).toBe("2");
    expect(field(/^Engine memory request/).value).toBe("8192");
    // Filled, and nothing has been written: filling a field applies nothing,
    // and Save is the only thing here that reaches the account.
    expect(sent).toEqual([]);

    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent.length).toBe(1));
    expect(sent[0]).toEqual({ slots: "5", threads_per_engine: "500",
                              override_cpu: "2", override_memory: "8192" });
    // What the account holds now, not what was sent: three landed and one came
    // back unstored, and the two are reported apart. It survives the re-read
    // the save itself caused -- the location arrives changed, which is what
    // used to clear this the moment it appeared.
    expect(await within(panel).findByText(/engines per agent 1 → 5/)).toBeTruthy();
    expect(within(panel).getByText(/BlazeMeter did not store engine memory request/))
      .toBeTruthy();

    // The fields are still fields. A hand edit outranks the profile, and only
    // what differs from the account is sent -- the two the first save landed
    // are not written back.
    fireEvent.change(field(/^Engines per agent/), { target: { value: "6" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent.length).toBe(2));
    expect(sent[1]).toEqual({ slots: "6", override_memory: "8192" });
  });


test("a location nobody needs to change still has a way on", async () => {
  // The panel's only control used to be Save, greyed whenever nothing had been
  // typed -- which is most locations, since most are already configured. So
  // choosing one opened a form whose one button was dead and left the next
  // thing to do somewhere else on the page with nothing pointing at it.
  const sent: unknown[] = [];
  render(<App api={accountOf([loc("h-perf", "Perf",
    [{ id: "s-1", name: "agent-1", state: "IDLE" }])], {
    updateLocation: async (body) => { sent.push(body); throw new Error("no"); },
  })} />);

  fireEvent.click(await screen.findByText("Perf"));
  const panel = await screen.findByRole("region", { name: "Perf settings" });

  // Live, and it says what it does: nothing has been typed, so it is the way
  // on rather than a write.
  const confirm = within(panel).getByRole<HTMLButtonElement>(
    "button", { name: "Confirm" });
  expect(confirm.disabled).toBe(false);
  expect(within(panel).getByText(/nothing to save/)).toBeTruthy();
  expect(within(panel).queryByRole("button", { name: "Save" })).toBeNull();

  fireEvent.click(confirm);

  // The location folds away -- both its settings row and the section over it --
  // and the agent list under it opens. Asserted through the agent becoming
  // reachable, which is the point of the move.
  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "Perf settings" })).toBeNull());
  expect(screen.getByRole("button", { name: /agent-1/ })).toBeTruthy();
  // ...and it reached the account for none of it. Confirm is not a write.
  expect(sent).toEqual([]);
});


test("Next waits for both confirmations, and a changed agent withdraws one",
  async () => {
    // Both lists auto-pick -- a lone agent is chosen for you, and a session
    // restore brings back a pairing nobody has looked at this time round -- so
    // "something is selected" was never "somebody said this is the one". Step 1
    // asked the first while claiming the second.
    render(<App api={accountOf([loc("h-perf", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" },
      { id: "s-2", name: "agent-2", state: "IDLE" },
    ])])} />);

    const next = () =>
      screen.getByRole<HTMLButtonElement>("button", { name: /Next/ });
    fireEvent.click(await screen.findByText("Perf"));
    const settings = await screen.findByRole("region", { name: "Perf settings" });
    expect(next().disabled).toBe(true);

    // Confirming the location folds it away and opens the agents. Two of them,
    // so nothing was auto-picked and the step is still waiting for the choice
    // itself rather than for a confirmation of one.
    fireEvent.click(within(settings).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Perf settings" })).toBeNull());
    expect(screen.getByText(/fill in the agent details/)).toBeTruthy();
    expect(next().disabled).toBe(true);

    // Chosen, and now it is the confirmation that is outstanding -- the block
    // names that half rather than repeating the whole step.
    // eslint-disable-next-line no-console
    fireEvent.click(await screen.findByText("agent-1"));
    await waitFor(() =>
      expect(screen.getByText(/confirm the agent/)).toBeTruthy());
    expect(next().disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(next().disabled).toBe(false));

    // ...and it is a confirmation *of that agent*. Picking the other one is a
    // different bundle, so the step is unfinished again -- which is why what
    // was confirmed is stored rather than a flag saying that something was.
    fireEvent.click(screen.getByText("agent-2"));
    await waitFor(() => expect(next().disabled).toBe(true));
    expect(screen.getByText(/confirm the agent/)).toBeTruthy();
  });


test("a location with no agents is not a bundle request", async () => {
  // Picking an empty location used to spend a 400 on saying so: the preview
  // asked for a bundle, and generate() refused -- correctly -- with a sentence
  // about a ship_id nobody had been asked for yet. An empty location is a
  // normal state this page has a whole amber panel for, so the preview waits
  // for the agent instead of asking a question it already knows the answer to.
  const generated: unknown[] = [];
  const api = accountOf([loc("h-empty", "no agents here")], {
    generate: async (...args: unknown[]) => {
      generated.push(args);
      throw new Error("ship_id required: location has 0 ships ([])");
    },
  });
  render(<App api={api} />);

  fireEvent.click(await screen.findByText("no agents here"));
  // The page says the location is empty...
  expect(await screen.findByText(/has no agents yet/)).toBeTruthy();
  // ...and asked for nothing. Waited past the preview's own debounce, so this
  // is "never asked" rather than "has not asked yet".
  await new Promise((r) => setTimeout(r, 400));
  expect(generated).toEqual([]);
});
