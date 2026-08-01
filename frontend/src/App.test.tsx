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
import { Capacity, Options } from "./api";
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
// The two requests this step exists to make are the ones nothing else covers:
// they do not go through the injected client at all (a zip cannot carry a JSON
// envelope, so downloadZip reads the branch off response headers and hands the
// bytes to the browser), and what they carry decides whether a running agent's
// credential survives. So `fetch` is what is stubbed here, and what is asserted
// is the body that left -- the facts, the options with the agent's id, and
// `rotate_token`, which is the field the whole of #64 was about.

/** The zip and save routes, with what left recorded. Only these two: every
 *  other call on the page goes through the injected client, so anything else
 *  reaching fetch is a route that has escaped the seam and should be seen. */
function stubTransfers(answer: (url: string, body: Record<string, unknown>) => Response) {
  const calls: { url: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    calls.push({ url: String(url), body });
    return answer(String(url), body);
  });
  // jsdom implements neither, and the anchor dance is api.saveBlob's business
  // rather than this step's -- what is under test is the request that produced
  // the blob.
  const url = URL as unknown as Record<string, unknown>;
  url.createObjectURL = () => "blob:bundle";
  url.revokeObjectURL = () => {};
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  return calls;
}

/** A zip, with the credential branch beside it in the headers the server sets. */
const zipAnswer = (message: string) =>
  new Response(new Blob(["PK"]), {
    headers: {
      "X-Bzm-Token-Branch": "given",
      "X-Bzm-Token-Message": message,
    },
  });

/** An account with one performance location and one idle agent in it: enough
 *  to reach step 3 with the buttons enabled. */
function perfAccount() {
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
    const calls = stubTransfers(() => zipAnswer("the AUTH_TOKEN you supplied"));
    render(<App api={perfAccount()} />);

    fireEvent.click(await atDownloadStep());

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].url).toBe("/api/generate/zip");
    expect(calls[0].body.facts).toMatchObject({ harbor_id: "h-perf" });
    expect(calls[0].body.options).toMatchObject({
      namespace: "blazemeter", ship_id: "s-1" });
    // The default, and the whole of #64: reading a bundle must not revoke the
    // credential the deployed agent is running on.
    expect(calls[0].body.rotate_token).toBe(false);

    // ...and what it did is reported in core's own words, off the headers.
    expect(await screen.findByText(/the AUTH_TOKEN you supplied/)).toBeTruthy();
  });

test("saving reports where it landed, and a refusal replaces that with why",
  async () => {
    let refuse = false;
    const calls = stubTransfers((url) => {
      if (url === "/api/generate/zip") return zipAnswer("carried");
      if (refuse) {
        return new Response(JSON.stringify({ detail: "no such folder" }),
                            { status: 400 });
      }
      return new Response(JSON.stringify({
        out_dir: "/home/me/bzm-opl/blazemeter",
        files: [{ name: "crane.yaml", bytes: 12 }],
        token: { branch: "given", ship_id: "s-1", message: "kept the token" },
      }));
    });
    render(<App api={perfAccount()} />);
    await atDownloadStep();

    fireEvent.change(screen.getByLabelText("Folder"),
                     { target: { value: "~/bzm-opl/blazemeter" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to folder" }));

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].url).toBe("/api/generate/save");
    expect(calls[0].body).toMatchObject({
      out_dir: "~/bzm-opl/blazemeter", rotate_token: false });
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
    const calls = stubTransfers(() => zipAnswer("a NEW AUTH_TOKEN was issued"));
    render(<App api={perfAccount()} />);
    const download = await atDownloadStep();

    // Offered because this agent has no token in the field, the page is
    // connected, and an agent is selected -- minting is an API call.
    fireEvent.click(screen.getByLabelText(/Issue a NEW AUTH_TOKEN/));
    // What it kills, said while the box is being ticked, which is the only
    // moment anyone can still decide not to.
    expect(await screen.findByText(/kills the current one at once/)).toBeTruthy();

    fireEvent.click(download);
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].body.rotate_token).toBe(true);
  });
