// @vitest-environment jsdom
//
// The two routes that produce a bundle, at the wire.
//
// Everything else this client does is one shape of `req`, and the pages that
// call it are tested against the seam rather than against fetch (see
// fakeApi.ts). These two are here because they are the ones that are not: a zip
// cannot carry a JSON envelope and still be a zip, so what happened to the
// credential travels in response *headers* whose literals are pinned on the
// server side too -- and the field that leaves in the body decides whether a
// deployed agent's credential survives. A rename on either side would otherwise
// lose the sentence, or the refusal to rotate, without failing anything.
import { afterEach, expect, test, vi } from "vitest";
import { api, Facts, TokenRequest } from "./api";

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
  // jsdom implements neither, and the anchor dance is saveBlob's business
  // rather than this test's -- what is under test is the request and the
  // headers that came back with it.
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
    // Spread, not converted: the record token.downloadPlan produced is the
    // record that left, under the one name both sides use.
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
    // The understating branch: a bundle claimed to carry a token it may not
    // have is the failure worth avoiding, and "" is a message, not a token.
    expect(token.branch).toBe("placeholder");
  });

test("the file is saved under the server's name, which is the folder it extracts to",
  async () => {
    // The name is the server's because it is also the archive's root directory
    // (core.zip_stem). Built here instead, a namespace the server sanitised out
    // of the folder stays in the filename and the two disagree again.
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
