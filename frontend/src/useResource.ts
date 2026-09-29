import { DependencyList, useEffect, useState } from "react";

/** `unread` is nothing asked (the fetcher returned null); `error` is asked and
 *  refused. The two are kept apart so a failed read never looks like an empty one. */
export type ResourceState = "unread" | "loading" | "ok" | "error";

export interface Resource<T> {
  state: ResourceState;
  /** The last answer that arrived. Kept through a reload and through a failed
   *  one: a read that could not be made says nothing about what was there. */
  data: T | null;
  error: string | null;
}

/** Read `fetcher()` whenever `deps` change, dropping any answer that lands
 *  after a newer request was made.
 *
 *  `fetcher` returns null when there is nothing to ask yet. */
export function useResource<T>(
  fetcher: () => Promise<T> | null,
  deps: DependencyList,
): Resource<T> {
  const [res, setRes] = useState<Resource<T>>(
    { state: "unread", data: null, error: null });
  useEffect(() => {
    const pending = fetcher();
    if (!pending) {
      setRes((r) => (r.state === "unread" && r.error === null
        ? r : { ...r, state: "unread", error: null }));
      return;
    }
    let live = true;
    setRes((r) => ({ ...r, state: "loading", error: null }));
    pending.then(
      (data) => { if (live) setRes({ state: "ok", data, error: null }); },
      (e: unknown) => {
        if (!live) return;
        const error = e instanceof Error ? e.message : String(e);
        setRes((r) => ({ ...r, state: "error", error }));
      });
    return () => { live = false; };
    // The caller's deps are the fetcher's; the closure itself is new every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return res;
}
