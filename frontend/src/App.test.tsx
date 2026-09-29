// @vitest-environment jsdom
//
// The page's effects, driven through the real controls and the fake API.
// `deferred` holds an answer open and vi's clock holds a debounce or poll open.
// Under fake timers, `waitFor` and `findBy*` must not be used (they poll a real
// interval), so tests set up on the real clock and switch to the fake one where
// the behaviour under test starts.
import {
  act, cleanup, fireEvent, render, screen, waitFor, within,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import {
  AgentStatus, Api, Capacity, CapacityPlan, Facts, FuncIdChoice,
  FuncIdVocabulary, Functionality, Location, Options, Ship, TokenRequest,
} from "./api";
import {
  catalogueImages, deferred, fakeApi, locationImages,
} from "./fakeApi";
// The served ignored-options table, from the one copy of it.
import {
  AGENT_ENV, IGNORED_BY_FORMAT, RESERVED_ENV, SIZING_MODELS, SLOT_MINIMUMS,
} from "./fixtures";
// The page's own snapshot writer, so forged snapshots track session.VERSION.
import * as session from "./session";
// The marker rule, from the page's own copy of it: a fake that spelled
// "<SHIP_ID>" out by hand would stop agreeing with the generator silently.
import { marker } from "./placeholder";
import { EMPTY_PLAN_INPUTS } from "./usePlan";
// The sizings a fresh page offers: one per served model, so they are built from
// the same fixture the page's own /api/sizing-models stub answers with.
import { defaultSizings } from "./sizings";
const DEFAULT_SIZINGS = defaultSizings(SIZING_MODELS);

/** The funcId vocabulary as /api/func-ids answers it. `source` matters: against
 *  the account a missing funcId is retired, against the baseline it is nothing. */
const vocabulary = (choices: FuncIdChoice[],
                    source: FuncIdVocabulary["source"]): FuncIdVocabulary =>
  ({ source, choices });
const NO_VOCABULARY = vocabulary([], "baseline");

// Registered before `cleanup`, so it runs after it: unmounting saves the
// session, and clearing first would leave that save for the next test.
afterEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(cleanup);
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
// Before cleanup, which unmounts and clears the faked interval.
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

    // Connected, on account 1, looking at the rollup: the first (slow) read is
    // in flight. The tab is disabled until the key answers.
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

    // ...and only then the first account's. Settled inside `act` and awaited,
    // so the unguarded version would have landed before the assertion.
    await act(async () => {
      alpha.settle(capacityOf(1, "Alpha workspace"));
      await alpha.promise;
    });

    expect(screen.queryByText("Alpha workspace")).toBeNull();
    expect(screen.queryByText("Bravo workspace")).not.toBeNull();
  });

/** An account whose rollup answers from `capacity`; nothing else is needed. */
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
    // A refresh outlives any effect run, so its guard is a ref on the account.
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

    // ...and only then the refresh, awaited to the end of its handlers.
    await act(async () => { pending[1].settle(capacityOf(1, "Alpha workspace")); });
    expect(screen.queryByText("Alpha workspace")).toBeNull();
    expect(screen.queryByText("Bravo workspace")).not.toBeNull();
  });

test("the account menu stays up while both of its pickers are used", async () => {
  // Choosing an account is the middle of the job, not the end of it.
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
// That the seeded ingress reaches the bundle, once.

/** An account with one location that runs virtual services. `record` collects
 *  the options every preview asks for; `extra` adds the watch routes. */
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
    // The funcId is the functionality id the sv group is tagged with, so it is
    // the real one; the page reads it off /api/sv-constants.
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

test("an SV location seeds a backend into the bundle, once, on whichever format",
  async () => {
    const asked: Options[] = [];
    render(<App api={svAccount(asked)} />);

    // Pick the location. Its funcIds are what make SV required -- nothing was
    // configured, and nothing was pressed in the group.
    fireEvent.click(await screen.findByText("Mocks"));

    // The seed reaches the bundle, not only the select.
    const latest = () => asked[asked.length - 1] ?? {};
    await waitFor(() => expect(latest().sv_ingress).toBe("nginx"));
    // ...and the format the session arrived with is kept.
    expect(latest().output_format).toBe("helm");
    // Settled: no further previews once the correction is applied.
    const settled = asked.length;
    await new Promise((r) => setTimeout(r, 400));
    expect(asked.length).toBe(settled);

    // The row says the location is what demands it...
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    expect(await screen.findByText(/this location runs mockServices/)).toBeTruthy();

    // ...and every format is offered.
    for (const name of [/Kubernetes manifests/, /Helm chart/, /Docker/]) {
      expect((await screen.findByRole<HTMLButtonElement>("radio", { name }))
        .disabled).toBe(false);
    }
    // Nothing was replaced, so nothing says it was. The notice existed for the
    // one write on this page that overrode a choice made on it.
    expect(screen.queryByText(/Switched to/)).toBeNull();
  });

// -- a functionality the location does not run --------------------------------
// Not on the configure step (manual entry excepted, where the card is the
// declaration), and its options are cleared rather than merely hidden, so no
// download is blocked by something off screen.

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
    // Needed: the two SV groups hide each other by format.
    ignoredOptions: async () => IGNORED_BY_FORMAT,
    generate: async (_facts: unknown, options: Options) => {
      record.push(options);
      return { files: [], token: { branch: "placeholder" as const,
                                   ship_id: "s-1", message: "" } };
    },
    ...extra,
  });
}

/** One functionality's card, by the anchor the rail links to (its label is on
 *  screen twice). */
const card = (functionalityId: string) =>
  within(document.getElementById("cfg-f-" + functionalityId)!);

/** Whether a functionality has a card at all; `card()` throws on a missing one. */
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

    // The undeclared card offers no switches.
    expect(card("mockServices").queryByRole("switch")).toBeNull();
    expect(card("mockServices").getByText(/tick/)).toBeTruthy();
    // ...while the declared one states the default engine size.
    expect(card("performance").getByText(/2 CPU \/ 8Gi/)).toBeTruthy();

    // Nothing was seeded, so the rail has nothing to flag.
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

    // The download is not blocked by the stranded SV option.
    const next = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Next/ });
    // All three in one wait: they settle across more than one render.
    await waitFor(() => {
      expect(next.disabled).toBe(false);
      expect(screen.queryByText(/needs attention/)).toBeNull();
      expect(screen.queryByText(/Service virtualization first/)).toBeNull();
    });
    // ...and the option is gone from the bundle, not merely off screen.
    await waitFor(() => expect(
      asked[asked.length - 1]?.sv_ingress).toBeFalsy());

    // ...and the functionality has no card and no rail entry.
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
    // The same, reached through a location and agent alone.
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

// -- a location whose funcIds name no served functionality -----------------------
// Nobody has said what it runs, so nothing is cleared and every switch is offered.

/** An account with such a location (`funcIds`, `["tdm"]` by default). Its
 *  vocabulary answers the baseline without an account and the account's own
 *  list, names and pins included, with one. */
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
      // Pins arrive under their parent, never as rows of their own.
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
    // An unconfigured funcId is named in the account's words, which also shows
    // the account's vocabulary replaced the baseline on connect.
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
  // A GUI location with its browser pins and a retired funcId: the pins are
  // parameters and are never named.
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
  // No pin anywhere, even with the parent itself uncovered.
  for (const pin of ["chrome:default", "firefox:139", "safari:15"]) {
    expect(screen.queryByText(new RegExp(pin))).toBeNull();
  }
  expect(screen.getByText(/GUI Functional/)).toBeTruthy();
});

test("an SV configuration no location demanded is generated on the format it arrived with",
  async () => {
    // A restored chart bundle with a full SV configuration nobody demanded
    // reaches the server as it is, and is generated.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-tdm", shipId: "s-1",
      confirmed: { loc: "h-tdm", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: {
        namespace: "blazemeter", output_format: "helm",
        sv_ingress: "nginx", sv_subdomain: "apps.example.com",
        sv_tls_secret: "wildcard",
      },
      step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    const asked: Options[] = [];
    render(<App api={unclaimedAccount(asked)} />);

    // Both halves survive, and neither is rewritten: the configuration is what
    // somebody wrote, and the format is what they chose.
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 400));
    expect(asked[asked.length - 1]?.sv_ingress).toBe("nginx");
    expect(asked[asked.length - 1]?.output_format).toBe("helm");

    // Nothing was swapped, so there is no notice, and no segment is disabled.
    expect(screen.queryByText(/Switched to/)).toBeNull();
    for (const name of [/Helm chart/, /Docker/]) {
      expect(screen.getByRole<HTMLButtonElement>("radio", { name }).disabled)
        .toBe(false);
    }
  });

test("every format offers the service-virtualization card its own switches",
  async () => {
    // Each format offers its own way of publishing a virtual service, for a
    // location whose funcIds say nothing.
    session.save({
      sourceMode: "connect", accountId: 1, workspaceId: 10,
      harborId: "h-tdm", shipId: "s-1",
      confirmed: { loc: "h-tdm", ship: "s-1" },
      manual: { harbor_id: "", ship_id: "" }, declaredFunctionalities: [],
      options: { namespace: "blazemeter", output_format: "helm" },
      step: 1, view: "flow", plan: EMPTY_PLAN_INPUTS, sizings: DEFAULT_SIZINGS,
    });
    const asked: Options[] = [];
    render(<App api={unclaimedAccount(asked)} />);
    fireEvent.click(await screen.findByRole("button", { name: /Configure/ }));

    // The chart, which is the format the session arrived on: the ingress
    // group's switch, and no sentence in place of it.
    await waitFor(() =>
      expect(card("mockServices").queryByRole("switch")).not.toBeNull());
    expect(card("mockServices").queryByText(/Not possible in this bundle/))
      .toBeNull();
    expect(card("mockServices").queryByText(/was declared to run/)).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: /Kubernetes manifests/ }));
    await waitFor(() =>
      expect(card("mockServices").queryByRole("switch")).not.toBeNull());
    // Docker's group, not the ingress one.
    fireEvent.click(screen.getByRole("radio", { name: /Docker/ }));
    await waitFor(() => expect(
      card("mockServices").getByRole("switch").getAttribute("aria-label"))
      .toBe("Virtual service endpoints"));
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

    // ...and the fields docker has no use for are off the step.
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
    // The OpenShift-only CA mode is offered by the cluster answer, not by the
    // SCC-friendly posture, which vanilla Kubernetes uses too.
    const asked: Options[] = [];
    render(<App api={accountOf([loc("h-0", "Dublin",
      [{ id: "s-1", name: "agent-1", state: "IDLE" }])], {
      // A profile that answered OpenShift; the generator's default is plain
      // Kubernetes (see the test below).
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
    fireEvent.click(await screen.findByLabelText(/OpenShift cluster trust bundle/));
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
    // ...and the mode is cleared with it, not only hidden.
    expect(asked[asked.length - 1]?.ca_openshift_inject).toBe(false);
    expect(screen.queryByLabelText(/OpenShift cluster trust bundle/)).toBeNull();
    // The posture is untouched: it is the other question, and the one this
    // customer still wants answered the recommended way.
    expect(asked[asked.length - 1]?.platform).toBe("openshift");
  });

test("an unanswered cluster is shown as plain Kubernetes, as the generator reads it",
  async () => {
    // openshift_cluster absent: the select must not claim OpenShift while the
    // bundle is generated with kubectl and the OpenShift-only CA mode is hidden.
    render(<App api={accountOf([loc("h-0", "Dublin",
      [{ id: "s-1", name: "agent-1", state: "IDLE" }])], {
      optionDefaults: async () => ({
        namespace: "blazemeter", service_account_name: "crane",
        output_format: "manifests", platform: "openshift",
      }),
      generate: async () => ({ files: [], token: {
        branch: "placeholder" as const, ship_id: "s-1", message: "" } }),
    })} />);

    fireEvent.click(await screen.findByText("Dublin"));
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Advanced/ }));
    const cluster = screen.getByLabelText<HTMLSelectElement>(/^Cluster/);
    expect(cluster.value).toBe("k8s");
    fireEvent.click(screen.getByRole("switch", { name: "Custom CA trust" }));
    expect(screen.queryByLabelText(/OpenShift cluster trust bundle/)).toBeNull();
  });

test("the CA group asks for a file name, and a blank one is not a blocker",
  async () => {
    /** The group asks one thing, the certificate's file name. Blank is not a
     *  blocker: the bundle carries `<CA_CERT_FILE>`. */
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
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    fireEvent.click(await screen.findByRole("switch", { name: "Custom CA trust" }));

    // Switching the group on is already a complete configuration: the file mode
    // needs nothing typed, which is why it is what `enable` seeds.
    await waitFor(() =>
      expect(asked[asked.length - 1]?.ca_bundle_slot).toBe(true));
    expect(asked[asked.length - 1]?.ca_cert_file ?? null).toBeNull();

    // No paste box and no upload: the only file input on the step is the
    // profile importer.
    expect(document.querySelector('input[type="file"][accept^=".pem"]'))
      .toBeNull();
    fireEvent.change(await screen.findByLabelText(/Certificate file name/),
                     { target: { value: "corp-root.crt" } });
    await waitFor(() =>
      expect(asked[asked.length - 1]?.ca_cert_file).toBe("corp-root.crt"));

    // Clearing it is allowed and blocks nothing; the marker is the generator's,
    // never held in `options`.
    fireEvent.change(screen.getByLabelText(/Certificate file name/),
                     { target: { value: "" } });
    await waitFor(() =>
      expect(asked[asked.length - 1]?.ca_cert_file).toBe(""));
    expect(screen.queryByText(/CA_CERT_FILE/)).toBeNull();
  });

// -- the download step, through the page -------------------------------------
// What the page hands the client: facts, options with the agent id, and the
// credential request. The wire shape is api.test.ts's.

/** What the page handed the client for a bundle, whichever route it used. */
interface Sent {
  route: "zip" | "save";
  facts: Facts;
  options: Options;
  credential: TokenRequest;
}

/** The bundle route: records what left, answers with core's credential sentence. */
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

/** One performance location with one idle agent: enough to reach step 3. */
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

test("a slow facts answer for the previous location never configures the next one",
  async () => {
    // Pick one location, then another before the first one's facts arrive.
    const first = deferred<Facts>();
    const second = deferred<Facts>();
    const generatedFor: string[] = [];
    const agent = { id: "s-1", name: "agent-1", state: "IDLE" };
    render(<App api={perfAccount({
      locations: async () => [
        { id: "h-a", name: "Alpha loc", funcIds: ["performance"], slots: 1, ships: [agent] },
        { id: "h-b", name: "Bravo loc", funcIds: ["performance"], slots: 1, ships: [agent] },
      ],
      facts: (harborId: string) => (harborId === "h-a" ? first : second).promise,
      generate: async (facts: Facts) => {
        generatedFor.push(facts.harbor_id);
        return { files: [{ name: "crane.yaml", content: "kind: Deployment" }],
                 token: { branch: "placeholder" as const, ship_id: "s-1",
                          message: "placeholder" } };
      },
    })} />);

    fireEvent.click(await screen.findByText("Alpha loc"));
    fireEvent.click(await screen.findByText("Bravo loc"));
    const factsOf = (harbor_id: string) => ({
      harbor_id, func_ids: ["performance"], ships: [agent], images: [],
    }) as unknown as Facts;
    await act(async () => { second.settle(factsOf("h-b")); });
    await waitFor(() => expect(generatedFor).toContain("h-b"));
    await act(async () => { first.settle(factsOf("h-a")); });
    // Past the preview's debounce, so a generate for the stale facts would
    // have been made by now.
    await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
    expect(generatedFor).not.toContain("h-a");
  });

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
    // Reading a bundle must not revoke the deployed agent's credential.
    expect(sent[0].credential).toEqual({ rotate_token: false });

    // Nothing happened to the credential, so nothing is said about it.
    expect(screen.queryByText(/the AUTH_TOKEN you supplied/)).toBeNull();
  });

test("a blank namespace and service account still download, carrying their markers",
  async () => {
    // Blank namespace and service account: the step warns, and the download
    // still works. Driven through the form, by clearing the two boxes.
    const sent: Sent[] = [];
    render(<App api={perfAccount(transfers(sent))} />);
    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));
    // The namespace suggestion lands first, or it would undo the clearing.
    const ns = await screen.findByPlaceholderText<HTMLInputElement>(/e\.g\. blazemeter/);
    await waitFor(() => expect(ns.value).toBe("blazemeter"));
    for (const box of [/e\.g\. blazemeter/, /e\.g\. crane/]) {
      fireEvent.change(screen.getByPlaceholderText(box),
                       { target: { value: "" } });
    }

    // The rail reports the state in amber, without the marker string.
    const rail = screen.getByRole("link", { name: /Placement/ });
    await waitFor(() => expect(rail.textContent).toMatch(/not filled in/));
    expect(rail.textContent).not.toMatch(/<NAMESPACE>/);
    expect(screen.queryByText(/needs attention/)).toBeNull();
    // ...and the hint under the box is where it does appear.
    expect(screen.getByText(/the bundle carries <NAMESPACE>/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const button = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Download bundle/ });
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);

    // ...and what is sent carries the markers, not empty strings.
    await waitFor(() => expect(sent.length).toBe(1));
    expect(sent[0].options).toMatchObject({
      namespace: marker("namespace"),
      service_account_name: marker("service_account_name"),
    });
    // The folded list stays beside the button, its bar naming the markers.
    const bar = screen.getByRole("button", { name: /Placeholders/ });
    expect(bar.textContent).toMatch(/<NAMESPACE>/);
    expect(bar.textContent).toMatch(/<SERVICE_ACCOUNT_NAME>/);
    // Opening it names the field beside its marker and offers the way back.
    fireEvent.click(bar);
    expect(await screen.findByText(/· namespace/)).toBeTruthy();
  });

test("a blank SV subdomain is a marker, not a disabled download",
  async () => {
    // Blank SV subdomain and TLS secret: generate() renders the markers, so the
    // download stays enabled. An SV location with nothing typed.
    const asked: Options[] = [];
    render(<App api={svAccount(asked)} />);
    fireEvent.click(await screen.findByText("Mocks"));
    await waitFor(() => expect(
      asked[asked.length - 1]?.sv_ingress).toBe("nginx"));

    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const button = await screen.findByRole<HTMLButtonElement>(
      "button", { name: /Download bundle/ });
    await waitFor(() => expect(button.disabled).toBe(false));
    // ...and it says what it carries instead of refusing to carry it.
    const bar = screen.getByRole("button", { name: /Placeholders/ });
    expect(bar.textContent).toMatch(/<SV_SUBDOMAIN>/);
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
    // The separate-nodes choice states its cost beside it.
    expect(await screen.findByText(/grows this pool by one node per engine/))
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
    // What each control writes reaches the bundle: a boolean's third position
    // writes nothing, a key/value table writes JSON.
    const sent: Sent[] = [];
    render(<App api={perfAccount({
      ...transfers(sent),
      // Both served tables; unstubbed, nothing would be offered or refused.
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
    // The page asks for the location's funcIds, and "nobody has said" is an
    // absent parameter, not an empty list.
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
    // Scoping narrows what is offered, never what is carried: an out-of-scope
    // variable stays editable in the name/value editor.
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

    // ...on screen by name, with its value; the fold's summary counts it.
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
    // A variable the bundle writes itself is listed with the option and section
    // that set it.
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
    // A reserved name typed by hand is refused, naming the owning option.
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
    // Import writes the option from outside, and the rows resync to it.
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
    // The engine size is stated from the location's requests; there is nothing
    // to edit on this step.
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
    expect(note.textContent).toContain("Engines request the same");
  });

test("a location running two engine functionalities states the engine size once",
  async () => {
    // Two engine functionalities, one limit pair: stated once.
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
    // A GUI-only location states it on its own card.
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
    // An SV-only agent runs no engine, so no engine size is stated.
    render(<App api={svAccount([])} />);

    fireEvent.click(await screen.findByText("Mocks"));
    fireEvent.click(await screen.findByRole("button", { name: /agent-1/ }));
    fireEvent.click(screen.getByRole("button", { name: /Configure/ }));

    await waitFor(() => expect(hasCard("mockServices")).toBe(true));
    expect(screen.queryByText(/^Engine size\.$/)).toBeNull();
    expect(screen.queryByText(/per engine/)).toBeNull();
  });



// -- step 1: the two lists, and the forms that write to the account -----------

/** One location in a workspace, as the listing carries it. */
const loc = (id: string, name: string, ships: Ship[] = []): Location =>
  ({ id, name, funcIds: ["performance"], slots: 1, ships });

/** An account holding exactly `locations`. The list is the fixture's own array,
 *  so a create can add to it. */
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
    // The server has minted nothing this session. Stubbed rather than left to
    // reject, because "holds none" and "could not ask" read differently.
    mintedToken: async () => ({ auth_token: null }),
    // The one copy of IGNORED_BY_FORMAT (fixtures.ts), every format stated.
    ignoredOptions: async () => IGNORED_BY_FORMAT,
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
// The cache is dropped before the re-read, and the re-read writes the list and
// nothing else.

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
    // Order matters: without the drop the re-read is served from the cache.
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
    // A failed read leaves the list as it was.
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
    // Selected: the new location's facts are read.
    await waitFor(() => expect(asked).toEqual(["h-new"]));
    expect(screen.getAllByText("Frankfurt").length).toBeGreaterThan(0);
  });

test("a GUI Functional location says its slot minimum before Create is pressed",
  async () => {
    // GUI Functional needs at least two slots; the form says so before Create.
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
    // Said as soon as the box is ticked.
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

// -- ...and keeping it --------------------------------------------------------
// A token this app minted comes back from the server after a reload. Asserted
// on the bundle request, not on the field.

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

    // The session snapshot never holds the token; it comes back from the server.
    cleanup();
    expect(JSON.stringify(sessionStorage)).not.toContain("tok-at-creation");
    asked.length = 0;
    render(<App api={api} />);

    // Nothing typed, and the bundle carries the real credential, not a marker.
    await waitFor(() => expect(last(asked)).toMatchObject({
      ship_id: "s-new", auth_token: "tok-at-creation" }));
  });

test("a credential is only ever found under the agent it was minted for",
  async () => {
    // Two agents in one location: each token is found only under its own agent.
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
    // Evicted at the server too, or the remembered copy would come back on reload.
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
    // "Could not ask" must not say a token cannot be read back.
    const listing = [loc("h-0", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" }])];
    render(<App api={accountOf(listing, {
      mintedToken: async () => { throw new Error("no route there"); },
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    expect(await screen.findByText(/could not ask this app/)).toBeTruthy();
    expect(screen.queryByText(/cannot be read back/)).toBeNull();

    // ...while an answered "none" does. A fresh page, not a reload.
    cleanup();
    sessionStorage.clear();
    render(<App api={accountOf(listing)} />);
    fireEvent.click(await screen.findByText("Perf"));
    expect(await screen.findByText(/cannot be read back/)).toBeTruthy();
  });

test("a regenerated token answering after the agent changed lands on neither",
  async () => {
    const listing = [loc("h-0", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" },
      { id: "s-2", name: "agent-2", state: "IDLE" },
    ])];
    const issued = deferred<{ auth_token: string }>();
    const asked: Options[] = [];
    render(<App api={mintingAccount(listing, {}, asked, {
      issueToken: () => issued.promise,
    })} />);

    fireEvent.click(await screen.findByText("Perf"));
    fireEvent.click(await screen.findByText("agent-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Regenerate token" }));
    fireEvent.click(screen.getByRole("button", { name: "I'm sure" }));
    expect(await screen.findByText("Regenerating…")).toBeTruthy();

    fireEvent.click(screen.getByText("agent-2"));
    await waitFor(() => expect(last(asked).ship_id).toBe("s-2"));
    await act(async () => {
      issued.settle({ auth_token: "tok-for-agent-1" });
      await issued.promise;
    });

    expect(screen.getByPlaceholderText(/paste the token this agent was created with/))
      .toHaveProperty("value", "");
    expect(screen.queryByText("Regenerated")).toBeNull();
    expect(asked.some((o) => o.auth_token === "tok-for-agent-1")).toBe(false);
  });

test("a location created after the workspace changed is not selected in the new one",
  async () => {
    const created = deferred<Location>();
    const read: number[] = [];
    const api = accountOf([loc("h-0", "Perf")], {
      workspaces: async () => [{ id: 10, name: "Alpha workspace" },
                               { id: 11, name: "Beta workspace" }],
      locations: async (ws: number) => {
        read.push(ws);
        return ws === 10 ? [loc("h-0", "Perf")] : [loc("h-9", "Beta only")];
      },
      createLocation: () => created.promise,
    });
    render(<App api={api} />);
    await screen.findByText("Perf");

    fireEvent.click(screen.getByRole("button", { name: "+ New location" }));
    fireEvent.change(screen.getByLabelText(/Name \(created in workspace/),
                     { target: { value: "New one" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    // Change workspace while the create is out.
    fireEvent.click(screen.getByTitle(/the key everything is read with/));
    fireEvent.focus(await screen.findByLabelText(/^Workspace/));
    fireEvent.mouseDown(await screen.findByText("Beta workspace"));
    expect(await screen.findByText("Beta only")).toBeTruthy();

    await act(async () => {
      created.settle(loc("h-new", "New one"));
      await created.promise;
    });
    // The create re-reads the workspace it was made in; let that land.
    await waitFor(() => expect(read.filter((w) => w === 10).length).toBe(2));
    await act(async () => {});
    expect(screen.queryByText("New one")).toBeNull();
    expect(screen.getByText("Beta only")).toBeTruthy();
  });

test("a lone agent that is reporting is not auto-picked, and says why when it is",
  async () => {
    // Fresh by the rule in heartbeat.ts, which is the whole difference between
    // this location and the ones above.
    const live = { id: "s-live", name: "agent-live", state: "IDLE",
                   lastHeartBeat: Date.now() / 1000 - 10 };
    // Named so the text is not also a word the page uses.
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

    // ...and that is all: the location stays chosen and nothing is re-read.
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

    // All four, in order: the only place on screen saying whose account it is.
    const bar = screen.getByText("account").parentElement!.parentElement!;
    await waitFor(() => expect(bar.textContent).toMatch(
      /account.*Alpha.*workspace.*Alpha workspace.*location.*Dublin.*agent.*agent-live/));
  });

test("the account menu is reachable on the view whose subject is the account",
  async () => {
    // Layout, asserted on the classes (jsdom lays nothing out): the shell is
    // screen-height and the pane beside the drawer scrolls, so the account menu
    // at the drawer's foot stays reachable on a tall view.
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
  // A CSS clip, asserted on the class (jsdom lays nothing out): the growing row
  // must not cut off the picker's list once it has grown.
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
    // A refresh keeps the confirmations.
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

    // ...but only for the agent that was confirmed.
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

    // The key check is held open: nothing may be saved before it resolves.
    const key = deferred<Awaited<ReturnType<Api["keyStatus"]>>>();
    const listing = [
      loc("h-0", "Region 0"),
      loc("h-dublin", "Dublin", [{ id: "s-1", name: "agent-1", state: "IDLE" }]),
    ];
    render(<App api={accountOf(listing, {
      keyStatus: () => key.promise,
    })} />);

    // The options are restored before the connection resolves.
    const ns = await screen.findByPlaceholderText("e.g. blazemeter");
    expect(ns).toHaveProperty("value", "restored-ns");
    // ...and the snapshot's four ids are untouched while none is confirmed.
    expect(session.load()).toMatchObject({
      accountId: 1, workspaceId: 10, harborId: "h-dublin", shipId: "s-1",
    });

    key.settle({
      connected: true, user: { email: "someone@example.com" },
      default_account_id: 1, key_id: "key-1",
    });

    // Once it resolves the page writes: the typed value and the four ids.
    fireEvent.change(ns, { target: { value: "typed-ns" } });
    await waitFor(() => expect(session.load()).toMatchObject({
      accountId: 1, workspaceId: 10, harborId: "h-dublin", shipId: "s-1",
      step: 1, options: { namespace: "typed-ns" },
      // Connected, nothing is declared.
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

    // The location comes back, and the agent inside it, on step 1.
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
    // Both ids go, the agent with its location; the account and workspace stay.
    await waitFor(() => expect(session.load()).toMatchObject({
      accountId: 1, workspaceId: 10, harborId: null, shipId: null,
    }));
  });

// -- manual entry's declaration survives a refresh ---------------------------
// It decides the funcIds the facts are gathered for, so it is asserted on the
// facts request.

/** A well-formed harbor id and ship id: manualComplete checks the shape, and
 *  nothing is requested for values that are not one. */
const TYPED = { harbor: "0a1b2c3d4e5f60718293a4b5",
                ship: "6c5b4a39281706f5e4d3c2b1" };

/** The manual-entry page with no key, both functionalities served. Records the
 *  funcIds of every facts request. */
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
        // As the server does, a blank id becomes its marker in the facts; the
        // page reads its agent out of this answer.
        facts: { harbor_id: b.harbor_id || marker("harbor_id"),
                 func_ids: b.func_ids,
                 ships: [{ id: b.ship_id || marker("ship_id"),
                           name: "agent-1" }], images: [] },
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

// -- ...and the identity nobody has yet --------------------------------------
// A location not yet created has no ids; the bundle carries the markers.

test("manual entry generates for a location that does not exist yet",
  async () => {
    const generated: Options[] = [];
    render(<App api={manualPage([], generated)} />);
    fireEvent.click(await screen.findByRole(
      "radio", { name: /Enter values manually/ }));

    // Nothing typed, and a bundle is still generated.
    await waitFor(() => expect(generated.length).toBeGreaterThan(0));
    expect(generated[generated.length - 1].ship_id).toBe(marker("ship_id"));

    // ...and the form names the blank fields, all three.
    const warning = await screen.findByText(/are empty, so the bundle will carry/);
    expect(warning.textContent).toMatch(/harbor_id \(<HARBOR_ID>\)/);
    expect(warning.textContent).toMatch(/ship_id \(<SHIP_ID>\)/);
    expect(warning.textContent).toMatch(/auth_token \(<AUTH_TOKEN>\)/);
  });

test("a bundle with nothing left blank says so, and offers nothing to open",
  async () => {
    // A finished bundle says so, as a bar rather than an empty fold: there is
    // no button here.
    const generated: Options[] = [];
    render(<App api={manualPage([], generated, {
      // A bundle that carries the credential.
      generate: async (_facts: unknown, options: Options) => {
        generated.push(options);
        return { files: [], token: { branch: "given" as const,
                                     ship_id: TYPED.ship, message: "" } };
      },
    })} />);
    await declareManually();
    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));

    expect(await screen.findByText(/Nothing left to fill in/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Nothing left to fill in/ }))
      .toBeNull();
    expect(screen.queryByRole("button", { name: /Placeholders/ })).toBeNull();
  });

test("the empty identity boxes show the marker the bundle will carry",
  async () => {
    // An empty box shows the marker it becomes, not a sample id.
    render(<App api={manualPage([])} />);
    fireEvent.click(await screen.findByRole(
      "radio", { name: /Enter values manually/ }));
    expect(screen.getByLabelText(/^Harbor ID/)
      .getAttribute("placeholder")).toBe(marker("harbor_id"));
    expect(screen.getByLabelText(/^Ship ID/)
      .getAttribute("placeholder")).toBe(marker("ship_id"));
    expect(screen.getByLabelText(/^Auth token/)
      .getAttribute("placeholder")).toBe(marker("auth_token"));
  });

test("declaring a functionality in manual entry suggests its namespace",
  async () => {
    // Declaring a functionality suggests its namespace, on the act of declaring.
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

    // In manual mode the checkbox is the declaration.
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

    // A request of its own, and time for a late one to land behind it.
    await waitFor(() => expect(asked.length).toBeGreaterThan(asBefore));
    await new Promise((r) => setTimeout(r, 400));
    // Gathered for the restored declaration.
    expect(asked.slice(asBefore)).toEqual([["mockServices"]]);

    // Its options came back with it.
    const after = generated[generated.length - 1];
    expect(after.sv_ingress).toBe("nginx");
    // ...and the saved namespace.
    expect(after.namespace).toBe(before.namespace);
  });

test("a restored declaration waits for the vocabulary rather than being lost to it",
  async () => {
    // Until the vocabulary lands, a restored declaration cannot be checked; the
    // identity is gathered for nothing meanwhile, and the declaration survives.
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

    // Gathered for nothing while the list is outstanding.
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

    // A declaration nothing serves is dropped: the page lands where a fresh
    // manual session does.
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 400));
    expect(asked[asked.length - 1]).toEqual(["performance"]);
    expect(card("performance").getByLabelText("Enabled"))
      .toHaveProperty("checked", true);
    // ...without rewriting the restored namespace.
    expect(screen.getByPlaceholderText("e.g. blazemeter"))
      .toHaveProperty("value", "blazemeter-sv");
  });

// -- ...and the declaration is a list -----------------------------------------
// That the whole list survives a refresh.

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

    // Performance plus GUI functional, both ticked.
    fireEvent.click(card("functionalGui").getByLabelText("Enabled"));
    await waitFor(() => expect(asked[asked.length - 1])
      .toEqual(["performance", "functionalGui"]));
    expect(card("performance").getByLabelText("Enabled"))
      .toHaveProperty("checked", true);

    // The refresh. sessionStorage survives it, which is the whole mechanism.
    const asBefore = asked.length;
    cleanup();
    render(<App api={api()} />);

    // A request of its own, and time for a late one to land behind it.
    await waitFor(() => expect(asked.length).toBeGreaterThan(asBefore));
    await new Promise((r) => setTimeout(r, 400));
    // Both funcIds.
    expect(asked.slice(asBefore)).toEqual([["performance", "functionalGui"]]);
    expect(card("functionalGui").getByLabelText("Enabled"))
      .toHaveProperty("checked", true);
  });

test("a restored declaration keeps the members the vocabulary still offers",
  async () => {
    // A withdrawn member is dropped and the rest kept.
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
    // ...without rewriting the restored namespace.
    expect(screen.getByPlaceholderText("e.g. blazemeter"))
      .toHaveProperty("value", "blazemeter");
  });

test("declaring service virtualization clears the functionalities that run engines",
  async () => {
    // SV is declared alone where a location is being decided: crane applies one
    // limit pair to every pod.
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

    // The declaration is SV's alone.
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
    // Connected, the location exists: the bundle generates and the page warns.
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
    // One generate after typing stops, not one per keystroke.
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

    // Before the click, which creates the interval.
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

    // One request per tick, for the agent on screen only.
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
    // The poll reads the namespace once the location's SV seed has landed.
    await waitFor(() =>
      expect(asked[asked.length - 1]?.sv_ingress).toBe("nginx"));
    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const watch = await screen.findByRole("switch", { name: /Watch agent status/ });

    vi.useFakeTimers();
    fireEvent.click(watch);
    expect(read).toEqual(["blazemeter"]);

    // One second before the next tick, the namespace is edited.
    await tick(9_000);
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    fireEvent.change(screen.getByPlaceholderText("e.g. blazemeter"),
                     { target: { value: "mocks-ns" } });

    // Nothing yet: the interval was not restarted by the edit.
    await tick(500);
    expect(read).toEqual(["blazemeter"]);

    // The due tick reads the new namespace, carried by the ref.
    await tick(1_000);
    expect(read).toEqual(["blazemeter", "mocks-ns"]);
  });

// -- the sizing, and the location it lands on -------------------------------
// The sizing works with nothing connected; a location's fields are filled from
// it, and only Save reaches the account.

/** A plan as core would answer it, divided by the agents it was asked with. */
function planFor(body: {
  users?: string; agents?: string; vus_per_engine?: string;
  sizings?: { functionality: string; target: string; figure?: string }[];
}): CapacityPlan {
  const agents = Math.max(Number(body.agents) || 1, 1);
  // The route takes rows or `users`, so the fake does too.
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
    // One engine plus the node's own 1 CPU / 2Gi, so the figures multiply.
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

/** The routes the unconnected page reads, plus the sizing card's. Anything
 *  else rejects, so a card needing an account fails here. */
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
  // Every step of the chain, including the node's own overhead.
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
    // The served models, one of them with no measured figure and so no box.
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
    // The server's refusal is shown where the plan would be.
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
          // Deliberately not applied, to test a field that comes back unstored.
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

    // Divided by the location's two agents: 5 engines each.
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
    // What the account holds now, stored and unstored reported apart, and still
    // shown after the save's own re-read.
    expect(await within(panel).findByText(/engines per agent 1 → 5/)).toBeTruthy();
    expect(within(panel).getByText(/BlazeMeter did not store engine memory request/))
      .toBeTruthy();

    // A hand edit outranks the sizing, and only changed fields are sent.
    fireEvent.change(field(/^Engines per agent/), { target: { value: "6" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent.length).toBe(2));
    expect(sent[1]).toEqual({ slots: "6", override_memory: "8192" });
  });


test("a location nobody needs to change still has a way on", async () => {
  // With nothing typed, the button is Confirm and writes nothing.
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

  // The location folds away and the agents open.
  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "Perf settings" })).toBeNull());
  expect(screen.getByRole("button", { name: /agent-1/ })).toBeTruthy();
  // ...and it reached the account for none of it. Confirm is not a write.
  expect(sent).toEqual([]);
});


test("Next waits for both confirmations, and a changed agent withdraws one",
  async () => {
    // Both lists auto-pick, so Next waits for both confirmations.
    render(<App api={accountOf([loc("h-perf", "Perf", [
      { id: "s-1", name: "agent-1", state: "IDLE" },
      { id: "s-2", name: "agent-2", state: "IDLE" },
    ])])} />);

    const next = () =>
      screen.getByRole<HTMLButtonElement>("button", { name: /Next/ });
    fireEvent.click(await screen.findByText("Perf"));
    const settings = await screen.findByRole("region", { name: "Perf settings" });
    expect(next().disabled).toBe(true);

    // Two agents, so nothing is auto-picked.
    fireEvent.click(within(settings).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Perf settings" })).toBeNull());
    expect(screen.getByText(/fill in the agent details/)).toBeTruthy();
    expect(next().disabled).toBe(true);

    // Chosen, and now it is the confirmation that is outstanding -- the block
    // names that half rather than repeating the whole step.
    fireEvent.click(await screen.findByText("agent-1"));
    await waitFor(() =>
      expect(screen.getByText(/confirm the agent/)).toBeTruthy());
    expect(next().disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(next().disabled).toBe(false));

    // Picking the other agent withdraws the confirmation.
    fireEvent.click(screen.getByText("agent-2"));
    await waitFor(() => expect(next().disabled).toBe(true));
    expect(screen.getByText(/confirm the agent/)).toBeTruthy();
  });


test("a location with no agents is not a bundle request", async () => {
  // An empty location: the preview waits for an agent instead of asking the
  // server for a bundle it would refuse.
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

// -- the built page against the code serving it ------------------------------
// Only a stale page interrupts; build.ts owns the sentences.

const OFFLINE: Partial<Api> = {
  keyDetect: async () => ({ candidates: [], active_key_id: null }),
  keyStatus: async () => ({ connected: false }),
  optionDefaults: async () => ({}),
  funcIdVocabulary: async () => NO_VOCABULARY,
  functionalities: async () => [],
  svConstants: async () => ({ func_ids: [], ingress_types: [], backends: {} }),
  ignoredOptions: async () => IGNORED_BY_FORMAT,
  reservedEnv: async () => ({}),
  slotMinimums: async () => ({}),
  sizingModels: async () => [],
  agentEnv: async () => [],
};

test("warns when the built page was not built from the server's source", async () => {
  render(<App api={fakeApi({
    ...OFFLINE,
    build: async () => ({
      version: "0.3.2", built: 1000, stale: true, commit: "abc123def456",
    }),
  })} />);

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toMatch(/not built from the code serving it/i);
  expect(alert.textContent).toMatch(/npm run build/);
});

test("a page recording nothing says so, and is not an alert", async () => {
  // Built before the fingerprint existed: said, but not an alert.
  render(<App api={fakeApi({
    ...OFFLINE,
    build: async () => ({
      version: "0.3.2", built: 1000, stale: "unrecorded", commit: "abc123",
    }),
  })} />);

  const note = await screen.findByRole("status");
  expect(note.textContent).toMatch(/records nothing about what it was built/i);
  expect(note.textContent).toMatch(/has not been checked/i);
  expect(screen.queryByRole("alert")).toBeNull();
});

test("says nothing where there is no source to compare against", async () => {
  // A wheel (`stale: null`) says nothing, as a matching page does.
  render(<App api={fakeApi({
    ...OFFLINE,
    build: async () => ({
      version: "0.3.2", built: 1000, stale: null, commit: null,
    }),
  })} />);

  await screen.findByText(/Not connected/i);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("status")).toBeNull();
});

test("says nothing about a page that was compared and matches", async () => {
  render(<App api={fakeApi({
    ...OFFLINE,
    build: async () => ({
      version: "0.3.2", built: 1000, stale: false, commit: "abc123",
    }),
  })} />);

  await screen.findByText(/Not connected/i);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("status")).toBeNull();
});

test("with no key, the images view is open and reads the catalogue", async () => {
  const asked: [string | null, boolean][] = [];
  render(<App api={unconnected({
    images: async (harborId, all) => { asked.push([harborId, all]); return catalogueImages(); },
  })} />);

  const tab = await screen.findByRole<HTMLButtonElement>("button", { name: /Images/ });
  expect(tab.disabled).toBe(false);
  fireEvent.click(tab);

  expect(await screen.findByRole("heading", { name: "BlazeMeter's image catalogue" }))
    .toBeTruthy();
  expect(asked).toEqual([[null, false]]);
  // The open view is what a refresh comes back to.
  await waitFor(() => expect(session.load()?.view).toBe("images"));
});

test("connected, the images view reads the location selected for the bundle",
  async () => {
    const asked: (string | null)[] = [];
    render(<App api={accountOf(
      [loc("h-dublin", "Dublin"), loc("h-berlin", "Berlin")], {
        images: async (harborId) => {
          asked.push(harborId);
          const name = harborId === "h-berlin" ? "Berlin" : "Dublin";
          return harborId
            ? locationImages({ location: { harbor_id: harborId, name, func_ids: [] } })
            : catalogueImages();
        },
      })} />);

    fireEvent.click(await screen.findByText("Dublin"));
    fireEvent.click(screen.getByRole("button", { name: /Images/ }));
    expect(await screen.findByRole("heading",
      { name: "Images for location Dublin (read from your account)" })).toBeTruthy();
    expect(asked[asked.length - 1]).toBe("h-dublin");

    // Another location, chosen under Generate, is read on coming back.
    fireEvent.click(screen.getByRole("button", { name: /Generate/ }));
    fireEvent.click(await screen.findByText("Berlin"));
    fireEvent.click(screen.getByRole("button", { name: /Images/ }));
    expect(await screen.findByRole("heading",
      { name: "Images for location Berlin (read from your account)" })).toBeTruthy();
    expect(asked[asked.length - 1]).toBe("h-berlin");
  });
