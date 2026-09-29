import { useCallback, useEffect, useRef, useState } from "react";
import { Api, Capacity } from "./api";

/** The whole-account rollup: read on arriving at the view, and again on demand.
 *
 *  What is on screen stays until its replacement arrives; only a change of
 *  account drops it. Both paths discard an answer for an account no longer
 *  selected, which on a large account is a real race (the read takes seconds). */
export function useCapacity(api: Api, active: boolean, accountId: number | null) {
  const [cap, setCap] = useState<Capacity | null>(null);
  const [capErr, setCapErr] = useState<string | null>(null);
  const [capRefreshing, setCapRefreshing] = useState(false);

  useEffect(() => { setCap(null); }, [accountId]);

  useEffect(() => {
    if (!active || !accountId) return;
    setCapErr(null);
    let live = true;
    api.capacity(accountId)
      .then((c) => { if (live) setCap(c); })
      .catch((e: Error) => { if (live) setCapErr(e.message); });
    return () => { live = false; };
  }, [api, active, accountId]);

  // A callback outlives any one effect run, so the guard is a ref.
  const accountRef = useRef(accountId);
  accountRef.current = accountId;
  const refreshCapacity = useCallback(async () => {
    const id = accountRef.current;
    if (!id) return;
    setCapRefreshing(true);
    setCapErr(null);
    try {
      // The server caches the rollup; drop it first or the re-read is a no-op.
      await api.refresh();
      const c = await api.capacity(id);
      if (accountRef.current === id) setCap(c);
    } catch (e) {
      if (accountRef.current === id) setCapErr((e as Error).message);
    } finally {
      setCapRefreshing(false);
    }
  }, [api]);

  return { cap, capErr, capRefreshing, refreshCapacity };
}
