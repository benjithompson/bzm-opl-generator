// A route caller for tests. Every route not stubbed rejects naming itself, so
// a test never passes on an invented answer; the names come from the real client.
import { api, Api } from "./api";

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
