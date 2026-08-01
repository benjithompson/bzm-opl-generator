// A route caller for tests: the page's seam, with something else on the other
// side of it.
//
// Every route defaults to a rejection naming itself, rather than to a plausible
// empty answer. A test that reaches a route it never described is a test whose
// subject was decided by this file, and an invented CapacityPlan or Facts would
// let it pass while proving nothing -- so an unstubbed route says so, and the
// page's own `.catch` handlers turn that into the "could not read" state it
// already has a rendering for. Stub what the flow under test actually calls.
//
// The route *names* come from the real client rather than from a list here:
// listing them would be a second declaration to keep in step, and one that has
// fallen behind is a fake quietly missing the route a page has started calling.
// Nothing is fetched by reading its keys.
import { api, Api } from "./api";

/** The real client's shape, answering only what `stubs` answers. */
export function fakeApi(stubs: Partial<Api> = {}): Api {
  const unstubbed = Object.fromEntries(Object.keys(api).map((name) => [
    name,
    () => Promise.reject(new Error(`fakeApi: ${name} was not stubbed`)),
  ]));
  // The one cast, and it is why the keys above are the real object's: a literal
  // built by walking a record cannot be seen as Api by the compiler, so the
  // check that this covers every route is that it is *made* from every route.
  return { ...unstubbed, ...stubs } as Api;
}

/** A promise a test resolves when it chooses, and the resolver beside it.
 *
 *  What a request in flight looks like: two answers can be outstanding at once
 *  and the slow one can land last, which is the shape of every guard on this
 *  page and cannot be provoked by a fake that answers immediately. */
export function deferred<T>() {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((res) => { settle = res; });
  return { promise, settle };
}
