// A route caller for tests. Every route not stubbed rejects naming itself, so
// a test never passes on an invented answer; the names come from the real client.
import { api, Api, ImageRow, ImagesAnswer } from "./api";

/** The real client's shape, answering only what `stubs` answers. */
export function fakeApi(stubs: Partial<Api> = {}): Api {
  const unstubbed = Object.fromEntries(Object.keys(api).map((name) => [
    name,
    () => Promise.reject(new Error(`fakeApi: ${name} was not stubbed`)),
  ]));
  // Built from the real client's keys, so it covers every route.
  return { ...unstubbed, ...stubs } as Api;
}

/** A promise a test settles when it chooses, for holding a request in flight. */
export function deferred<T>() {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((res) => { settle = res; });
  return { promise, settle };
}

/** One /api/images row with every registry field read; `over` changes any. */
export function imageRow(over: Partial<ImageRow> = {}): ImageRow {
  return {
    key: "taurus-cloud", repo: "blazemeter/v4", tag: "1.16.30",
    ref: "blazemeter/v4:1.16.30", category: "Engines",
    functionalities: ["performance"],
    purpose: "Runs the load test.", pulled_when: "when a test starts",
    verified: true, required: true, tag_mutable: false,
    source: "location-versions",
    registry_state: "read", registry_detail: null,
    digest: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    size_mb: 812.4, newest_tag: "1.16.30", update_available: false,
    ...over,
  };
}

/** An /api/images answer for a location read from the account. */
export function locationImages(over: Partial<ImagesAnswer> = {}): ImagesAnswer {
  return {
    source: "location",
    location: { harbor_id: "h-perf", name: "Dublin", func_ids: ["performance"] },
    image_list_state: "read",
    registry_lookup: { state: "read", detail: null },
    images: [imageRow()],
    ...over,
  };
}

/** An /api/images answer from the built-in catalogue: no location, and
 *  `required` null on every row. */
export function catalogueImages(over: Partial<ImagesAnswer> = {}): ImagesAnswer {
  return {
    source: "catalogue", location: null, image_list_state: "not-asked",
    registry_lookup: { state: "read", detail: null },
    images: [imageRow({ tag: "latest", ref: "blazemeter/v4:latest",
                        required: null, tag_mutable: true, source: "catalogue" })],
    ...over,
  };
}
