import { useCallback, useEffect, useRef, useState } from "react";
import { AgentStatus, Api, SvCheckOut, SvMocksOut, SvScheme } from "./api";
import { goneNotice } from "./stale";

export type SvChecks =
  Record<string, { busy: boolean; res?: SvCheckOut; err?: string }>;

/** What the agent-watch poll reads virtual services with. */
export interface SvWatch { on: boolean; namespace: string; subdomain: string; scheme: SvScheme }

/** Poll an agent's heartbeat every 10s while `polling` is on, and read the
 *  namespace's virtual services on the same tick when SV is configured.
 *
 *  The SV parameters and `onGone` travel by ref, so editing the namespace does
 *  not restart the interval. A 404 on the heartbeat stops the watch and reports
 *  through `onGone`; any other failure keeps the last status. */
export function useAgentWatch(api: Api, harborId: string | null, shipId: string | null,
                              sv: SvWatch, onGone: (msg: string) => void) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [polling, setPolling] = useState(false);
  // Labelled with the namespace it was read from, which may since have been edited.
  const [svMocks, setSvMocks] =
    useState<{ ns: string; read: SvMocksOut } | null>(null);
  // Keyed by host rather than row, so a result never lands beside a different
  // virtual service after the poll replaces the list.
  const [svChecks, setSvChecks] = useState<SvChecks>({});

  const svRef = useRef(sv);
  svRef.current = sv;
  const goneRef = useRef(onGone);
  goneRef.current = onGone;

  useEffect(() => {
    if (!polling || !harborId || !shipId) return;
    let live = true;
    const tick = () => {
      const { on, namespace: ns, subdomain } = svRef.current;
      // Applied as each lands: the SV read can take 15s against a dead cluster
      // and must not hold the heartbeat behind it.
      api.status(harborId, shipId)
        .then((s) => { if (live) setStatus(s); })
        .catch((e) => {
          if (!live) return;
          const gone = goneNotice(e, "agent");
          if (gone) { goneRef.current(gone); setPolling(false); }
        });
      if (on && ns) {
        api.svMocks(ns, subdomain)
          .then((m) => { if (live) setSvMocks({ ns, read: m }); }).catch(() => {});
      }
    };
    tick();
    const t = window.setInterval(tick, 10000);
    return () => { live = false; window.clearInterval(t); };
  }, [api, polling, harborId, shipId]);

  /** Probe a published endpoint. Only its own row goes busy; the server bounds
   *  the wait well inside one poll interval. */
  const checkEndpoint = useCallback(async (host: string) => {
    setSvChecks((c) => ({ ...c, [host]: { busy: true } }));
    try {
      const res = await api.svCheck(host, svRef.current.scheme);
      setSvChecks((c) => ({ ...c, [host]: { busy: false, res } }));
    } catch (e) {
      setSvChecks((c) => ({ ...c, [host]: { busy: false, err: String((e as Error).message) } }));
    }
  }, [api]);

  const clearStatus = useCallback(() => setStatus(null), []);

  return { status, clearStatus, polling, setPolling, svMocks, svChecks, checkEndpoint };
}
