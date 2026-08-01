// @vitest-environment jsdom
//
// The page's effects, driven through the seam.
//
// The account-capacity read is the one under test here because it is the
// cheapest of the four documented traps to provoke: it is the slowest read on
// the page (171 locations on a real account), the account can be changed while
// it is in flight, and without the `live` guard the slower answer wins -- so
// the numbers on screen are the previous account's, under the name of the one
// now selected. Nothing about that is visible in a type or in a review; it
// needs two answers outstanding at once, which is what `deferred` is for.
//
// Driven through the real controls rather than by poking state: switching
// account is the menu at the foot of the drawer, and a test that reached
// setAccountId directly would keep passing if that control stopped calling it.
import {
  act, cleanup, fireEvent, render, screen, waitFor,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import {
  Api, Capacity, Facts, Location, Options, SavedBundle, Ship, TokenRequest,
} from "./api";
import { deferred, fakeApi } from "./fakeApi";

afterEach(cleanup);
// The page writes its selections to sessionStorage, and one test's would
// otherwise be restored into the next one's page.
afterEach(() => { sessionStorage.clear(); localStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

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
      funcIdChoices: async () => [],
      features: async () => [],
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

// -- service virtualization, through the page --------------------------------
// sv.ts is tested as plain data (sv.test.ts) -- what needs a page is the wiring
// it replaced: an effect that WROTE the ingress option and another that READ it
// back. The bundle really carrying the seeded value, and doing so once, is the
// thing neither a type nor the module's own tests can show.

/** An account holding one location that runs virtual services, ready to
 *  generate for. `record` collects the options every preview is asked for --
 *  the bundle as the server would see it, rather than what the form shows. */
function svAccount(record: Options[]) {
  return fakeApi({
    keyDetect: async () => ({ candidates: [], active_key_id: null }),
    keyStatus: async () => ({
      connected: true, user: { email: "someone@example.com" },
      default_account_id: 1, key_id: "key-1",
    }),
    accounts: async () => [{ id: 1, name: "Alpha" }],
    workspaces: async () => [{ id: 10, name: "Alpha workspace" }],
    locations: async () => [{
      id: "h-mocks", name: "Mocks", funcIds: ["mock-services"], slots: 1,
      // Offline, so the page's own rule auto-picks it -- a running agent is
      // never cloned into a new deployment.
      ships: [{ id: "s-1", name: "agent-1", state: "IDLE" }],
    }],
    facts: async () => ({
      harbor_id: "h-mocks", func_ids: ["mock-services"],
      ships: [{ id: "s-1", name: "agent-1" }], images: [],
    }),
    optionDefaults: async () => ({
      namespace: "blazemeter", service_account_name: "crane",
      platform: "openshift", output_format: "helm",
    }),
    funcIdChoices: async () => [],
    features: async () => [{
      id: "sv", label: "Service virtualization", namespace: "blazemeter-sv",
      func_ids: ["mock-services"],
    }],
    // The funcId that means "runs virtual services" is served, never spelled
    // in the frontend -- this is the fixture standing in for that vocabulary.
    svConstants: async () => ({
      func_ids: ["mock-services"],
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

    // ...and the chart is refused with the sentence, rather than disappearing.
    fireEvent.click(screen.getByRole("button", { name: /Download & verify/ }));
    const chart = await screen.findByRole<HTMLButtonElement>(
      "radio", { name: /Helm chart/ });
    expect(chart.disabled).toBe(true);
    expect(screen.getByText(/which this chart does not carry/)).toBeTruthy();
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
  outDir?: string;
}

/** The two bundle routes, recording what left and answering as the server
 *  would -- the credential sentence included, because it is core's wording and
 *  arrives on the answer rather than being composed on this side. */
function transfers(sent: Sent[], save: () => Promise<SavedBundle>): Partial<Api> {
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
    saveBundle: async (facts, options, outDir, credential) => {
      sent.push({ route: "save", facts, options, credential, outDir });
      return save();
    },
  };
}

/** One folder written, as the server reports it: the expanded path rather than
 *  the `~` that was typed. */
const savedTo = async (): Promise<SavedBundle> => ({
  out_dir: "/home/me/bzm-opl/blazemeter",
  files: [{ name: "crane.yaml", bytes: 12 }],
  token: { branch: "given", ship_id: "s-1", message: "kept the token" },
});

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
    funcIdChoices: async () => [],
    features: async () => [{
      id: "perf", label: "Performance", namespace: "blazemeter",
      func_ids: ["performance"],
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
    render(<App api={perfAccount(transfers(sent, savedTo))} />);

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

    // ...and what it did is reported in core's own words, off the answer.
    expect(await screen.findByText(/the AUTH_TOKEN you supplied/)).toBeTruthy();
  });

test("saving reports where it landed, and a refusal replaces that with why",
  async () => {
    const sent: Sent[] = [];
    let refuse = false;
    const save = () => (refuse
      ? Promise.reject(new Error("no such folder")) : savedTo());
    render(<App api={perfAccount(transfers(sent, save))} />);
    await atDownloadStep();

    fireEvent.change(screen.getByLabelText("Folder"),
                     { target: { value: "~/bzm-opl/blazemeter" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to folder" }));

    await waitFor(() => expect(sent.length).toBe(1));
    expect(sent[0].route).toBe("save");
    expect(sent[0].outDir).toBe("~/bzm-opl/blazemeter");
    // The save is the other half of #64: writing into a folder a second time
    // is the ordinary way to use it, and it must not cost a rotation either.
    expect(sent[0].credential).toEqual({ rotate_token: false });
    // The expanded path the server echoed, not the `~` that was typed: it is
    // what a kubectl command can be copied against.
    expect(await screen.findByText(/Wrote 1 files to/)).toBeTruthy();
    expect(screen.getByText("/home/me/bzm-opl/blazemeter")).toBeTruthy();

    // A refused save says why, and takes the previous save's claim with it --
    // that folder is not where this bundle went.
    refuse = true;
    fireEvent.click(screen.getByRole("button", { name: "Save to folder" }));
    expect(await screen.findByText("no such folder")).toBeTruthy();
    expect(screen.queryByText(/Wrote 1 files to/)).toBeNull();
  });

test("ticking the rotate box is what makes the request issue a credential",
  async () => {
    const sent: Sent[] = [];
    render(<App api={perfAccount(transfers(sent, savedTo))} />);
    const download = await atDownloadStep();

    // Offered because this agent has no token in the field, the page is
    // connected, and an agent is selected -- minting is an API call.
    fireEvent.click(screen.getByLabelText(/Issue a NEW AUTH_TOKEN/));
    // What it kills, said while the box is being ticked, which is the only
    // moment anyone can still decide not to.
    expect(await screen.findByText(/kills the current one at once/)).toBeTruthy();

    fireEvent.click(download);
    await waitFor(() => expect(sent.length).toBe(1));
    expect(sent[0].credential).toEqual({ rotate_token: true });
    expect(await screen.findByText(/a NEW AUTH_TOKEN was issued/)).toBeTruthy();
  });

test("the box is the only thing that rotates: a save after one is asked for does too",
  async () => {
    const sent: Sent[] = [];
    render(<App api={perfAccount(transfers(sent, savedTo))} />);
    await atDownloadStep();

    // Both routes read one plan, so the pair cannot disagree about what the
    // click costs -- which is what a flag re-applied at two call sites could.
    fireEvent.click(screen.getByRole("button", { name: "Save to folder" }));
    await waitFor(() => expect(sent.length).toBe(1));
    expect(sent[0].credential).toEqual({ rotate_token: false });

    fireEvent.click(screen.getByLabelText(/Issue a NEW AUTH_TOKEN/));
    fireEvent.click(screen.getByRole("button", { name: "Save to folder" }));
    await waitFor(() => expect(sent.length).toBe(2));
    expect(sent[1].credential).toEqual({ rotate_token: true });
  });

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
    funcIdChoices: async () => [
      { id: "performance", label: "Performance", changes_images: true },
    ],
    features: async () => [{
      id: "perf", label: "Performance", namespace: "blazemeter",
      func_ids: ["performance"],
    }],
    svConstants: async () => ({ func_ids: [], ingress_types: [], backends: {} }),
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

test("a lone agent that is reporting is not auto-picked, and says why when it is",
  async () => {
    // Fresh by the rule in heartbeat.ts, which is the whole difference between
    // this location and the ones above.
    const live = { id: "s-live", name: "agent-live", state: "IDLE",
                   lastHeartBeat: Date.now() / 1000 - 10 };
    render(<App api={accountOf([loc("h-0", "Busy", [live])])} />);

    fireEvent.click(await screen.findByText("Busy"));
    // Counted as online in the row for its location...
    expect(await screen.findByText(/1 agent · 1 online/)).toBeTruthy();
    // ...and left unpicked: a new deployment on an identity that is already
    // running conflicts with the install that is working.
    expect(screen.queryByText(/already running somewhere/)).toBeNull();

    fireEvent.click(screen.getByText("agent-live"));
    expect(await screen.findByText(/already running somewhere/)).toBeTruthy();
  });
