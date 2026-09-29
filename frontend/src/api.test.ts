// @vitest-environment jsdom
//
// The bundle download at the wire: the credential field in the body, and the
// report coming back in response headers whose names the server pins too.
import { afterEach, expect, test, vi } from "vitest";
import { api, ApiError, Facts, TokenRequest } from "./api";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const facts: Facts = {
  harbor_id: "h-perf", ships: [{ id: "s-1" }], images: [],
};

/** fetch, recording what left, answering with `res`. */
function stubFetch(res: () => Response) {
  const calls: { url: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url: String(url), body: JSON.parse(String(init.body)) });
    return res();
  });
  // jsdom has neither; saving the blob is not under test.
  const u = URL as unknown as Record<string, unknown>;
  u.createObjectURL = () => "blob:bundle";
  u.revokeObjectURL = () => {};
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  return calls;
}

const zip = () => new Response("PK", {
  headers: {
    "X-Bzm-Token-Branch": "rotated",
    "X-Bzm-Token-Message": "a NEW AUTH_TOKEN was issued",
  },
});

test("downloadZip sends the credential request as the server names it", async () => {
  const calls = stubFetch(zip);
  const credential: TokenRequest = { rotate_token: true };

  const token = await api.downloadZip(facts, { namespace: "bzm" }, credential);

  expect(calls[0].url).toBe("/api/generate/zip");
  expect(calls[0].body).toMatchObject({
    facts: { harbor_id: "h-perf" },
    options: { namespace: "bzm" },
    // The plan's record, spread as it is.
    rotate_token: true,
  });
  // ...and what it did comes back off the headers, in core's own words.
  expect(token).toEqual({
    branch: "rotated", ship_id: null,
    message: "a NEW AUTH_TOKEN was issued",
  });
});

test("a zip with no credential headers reads as the placeholder, not as nothing",
  async () => {
    stubFetch(() => new Response("PK"));
    const token = await api.downloadZip(facts, {}, { rotate_token: false });
    // No header: the cautious branch.
    expect(token.branch).toBe("placeholder");
  });

test("the file is saved under the server's name, which is the folder it extracts to",
  async () => {
    // The server's archive name, which is also the folder it extracts to.
    const saved: string[] = [];
    stubFetch(() => new Response("PK", {
      headers: {
        "Content-Disposition": 'attachment; filename="bzm-opl-ns1.zip"',
      },
    }));
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) { saved.push(this.download); });

    await api.downloadZip(facts, { namespace: "<NAMESPACE>" },
                          { rotate_token: false });

    expect(saved).toEqual(["bzm-opl-ns1.zip"]);
  });

test("a download with no name header still saves under one", async () => {
  const saved: string[] = [];
  stubFetch(() => new Response("PK"));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function (this: HTMLAnchorElement) { saved.push(this.download); });

  await api.downloadZip(facts, { namespace: "bzm" }, { rotate_token: false });

  expect(saved).toEqual(["bzm-opl-bzm.zip"]);
});

test("a refused download keeps its status, as every other route does", async () => {
  // A 404 keeps its status, so stale.ts can say the thing is gone.
  stubFetch(() => new Response(JSON.stringify({ detail: "no such ship" }),
                               { status: 404 }));
  const err = await api.downloadZip(facts, {}, { rotate_token: false })
    .catch((e) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err.status).toBe(404);
  expect(err.message).toBe("no such ship");
});

test("a download refused by the static mount says the page is newer than the server",
  async () => {
    // A 405 with no JSON body gets the stale-server sentence.
    stubFetch(() => new Response("Method Not Allowed", { status: 405 }));
    const err = await api.downloadZip(facts, {}, { rotate_token: false })
      .catch((e) => e);
    expect(String(err.message)).toMatch(/newer than the server/);
  });
