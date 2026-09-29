import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Api, Account, AgentEnvVar, Facts, FuncIdVocabulary, Location, ManualFactsOut,
  Ship, SizingModel, Workspace,
} from "./api";
import { Attempt, NO_ATTEMPT } from "./attempt";
import { ErrorMsg, Section } from "./components";
import { downloadPlan, Recall, recalled, recallNote } from "./token";
import {
  blockingGroups, configureBlockedBy, detectGroups, enabledFunctionalities,
  engineFunctionalities, functionalitiesOf, GroupId, incompleteGroups,
  isOpenshift, notRunPatch, runsFunctionality, serviceAccountOk,
  startFunctionality, suggestNamespace, toggleDeclared, unclaimedFuncIds,
} from "./optionGroups";
import { blankRequired, gaps, withPlaceholders } from "./placeholder";
import { isDocker, optionApplies, whyIgnored as why } from "./formats";
import { sizeStatement } from "./engineSize";
import { exclusiveWith, SV_FUNCTIONALITY, svState } from "./sv";
import { slotsBlockedBy } from "./slots";
import * as session from "./session";
import { goneNotice } from "./stale";
import { buildNotice } from "./build";
import { shipOnline } from "./heartbeat";
import { blankManualIds, manualComplete } from "./manualIds";
import { CapacityView } from "./CapacityView";
import { CatalogueReason, ImagesView } from "./ImagesView";
import { EMPTY_PLAN_INPUTS, PlanAsk, PlanInputs } from "./usePlan";
import { defaultSizings, SavedSizing } from "./sizings";
import { AgentPanel } from "./steps/AgentPanel";
import { Sizing } from "./steps/Sizing";
import { ConfigurePanel } from "./steps/ConfigurePanel";
import { DownloadPanel } from "./steps/DownloadPanel";
import { CaGroup } from "./groups/CaGroup";
import { ProxyGroup } from "./groups/ProxyGroup";
import { RegistryGroup } from "./groups/RegistryGroup";
import { EnvVars } from "./groups/EnvVars";
import { SchedGroup } from "./groups/SchedGroup";
import { SecurityGroup } from "./groups/SecurityGroup";
import { SvDockerGroup } from "./groups/SvDockerGroup";
import { SvGroup } from "./groups/SvGroup";
import { PreviewDrawer } from "./layout/PreviewDrawer";
import { NavDrawer, ViewId } from "./layout/NavDrawer";
import { AccountMenu, ConnectBody } from "./layout/AccountMenu";
import { StepFlow } from "./layout/StepFlow";
import { useServedTables } from "./useServedTables";
import { useResource } from "./useResource";
import { useCapacity } from "./useCapacity";
import { useImages } from "./useImages";
import { useAgentWatch } from "./useAgentWatch";
import { usePreview } from "./usePreview";
import { useBundleOptions } from "./useBundleOptions";

type HeldIds = Pick<session.Session, "accountId" | "workspaceId" | "harborId" | "shipId">;

/** The page. Owns every piece of domain state and every effect that reaches
 *  the server; the panels get typed props.
 *
 *  `api` is the route caller (a fake under test) and is fixed for the page's
 *  lifetime. */
export default function App({ api }: { api: Api }) {
  // -- connection ------------------------------------------------------------
  const [keyPath, setKeyPath] = useState("");
  const [who, setWho] = useState<{ email: string; keyId: string } | null>(null);
  // Shared by every way of connecting, so it also guards re-entry.
  const [connecting, setConnecting] = useState(false);

  // -- account tree ----------------------------------------------------------
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountsBusy, setAccountsBusy] = useState(false);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspacesBusy, setWorkspacesBusy] = useState(false);
  const [workspaceId, setWorkspaceId] = useState<number | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locBusy, setLocBusy] = useState(false);
  const [harborId, setHarborId] = useState<string | null>(null);
  const [showCreateLoc, setShowCreateLoc] = useState(false);
  const [newLoc, setNewLoc] = useState({
    name: "", workspace_id: 0, func_ids: ["performance"], slots: 1, threads_per_engine: 500 });
  const [locErr, setLocErr] = useState<string | null>(null);
  // What was confirmed, by id: choosing something else withdraws the
  // confirmation by comparison, with nothing to remember to clear.
  const [confirmed, setConfirmed] =
    useState<{ loc: string | null; ship: string | null }>({ loc: null, ship: null });

  // -- agent -----------------------------------------------------------------
  const [shipId, setShipId] = useState<string | null>(null);
  const [showCreateShip, setShowCreateShip] = useState(false);
  const [shipErr, setShipErr] = useState<string | null>(null);
  // The agent was created and only its credential was refused: an amber notice,
  // not an error, or the next click makes a second agent.
  const [shipTokenNotice, setShipTokenNotice] = useState<string | null>(null);
  const [facts, setFacts] = useState<Facts | null>(null);
  const [factsBusy, setFactsBusy] = useState(false);

  // Where the harbor id, ship id and token come from: read off the account, or
  // typed. Everything downstream reads `facts`, `shipId` and the token either way.
  const [sourceMode, setSourceMode] = useState<"connect" | "manual">("connect");
  const [manual, setManual] = useState({ harbor_id: "", ship_id: "" });

  // Current selection, for async work that must not land on a newer one.
  const harborRef = useRef(harborId);
  harborRef.current = harborId;
  const shipRef = useRef(shipId);
  shipRef.current = shipId;
  const workspaceRef = useRef(workspaceId);
  workspaceRef.current = workspaceId;

  // -- options ---------------------------------------------------------------
  const {
    svConst, ignored, reservedEnv, slotMinimums, placeholderSources,
    functionalities, build,
  } = useServedTables(api);
  const {
    options, setOptions, patch, set, applyDefaults, grpOn, setGrpOn, flipGroup,
    caMode, setCaMode, proxy, setProxy, importProfile, exportProfile,
  } = useBundleOptions();
  const engineFuncIds = useMemo(
    () => engineFunctionalities(functionalities), [functionalities]);
  // Connected, a view over the location's funcIds; in manual entry, the
  // declaration of what the typed identity runs.
  const [declared, setDeclared] = useState<string[]>([]);
  const txt = useCallback((k: string) => String(options[k] ?? "").trim(), [options]);
  // Untrimmed, for controlled inputs.
  const raw = useCallback((k: string) => String(options[k] ?? ""), [options]);

  // What this location runs: declared (manual), read (connected), or null when
  // nobody has said yet.
  const locFunctionalities = functionalitiesOf(facts?.func_ids, functionalities);
  const enabled = enabledFunctionalities(sourceMode, declared, locFunctionalities);
  const svRuns = runsFunctionality(enabled, SV_FUNCTIONALITY);
  const format = String(options.output_format ?? "manifests");
  /** Does this option reach anything in the bundle being generated? */
  const applies = useCallback(
    (k: string) => optionApplies(k, format, ignored), [format, ignored]);
  const whyIgnored = useCallback(
    (k: string) => why(k, format, ignored), [format, ignored]);
  const sv = useMemo(
    () => svState(facts?.func_ids, options, svConst, svRuns, applies),
    [facts?.func_ids, options, svConst, svRuns, applies]);

  // The required fields left empty, and what is sent in their place. The
  // marker never enters `options`, or it would be saved as if typed.
  const blanks = useMemo(
    () => blankRequired(options, applies, grpOn), [options, applies, grpOn]);
  const sentOptions = useMemo(
    () => withPlaceholders(options, blanks), [options, blanks]);

  const [recall, setRecall] = useState<Recall>("asking");
  // What the last download or save did, for the agent it was done for.
  const [attempt, setAttempt] = useState<Attempt>(NO_ATTEMPT);
  /** Drop the credential and everything said about it. Called whenever the
   *  target agent changes, so a bundle never carries another agent's token. */
  const forgetToken = useCallback(() => {
    set("auth_token", null);
    setAttempt(NO_ATTEMPT);
  }, [set]);

  const watch = useAgentWatch(api, harborId, shipId, {
    on: sv.configured, namespace: txt("namespace"),
    subdomain: txt("sv_subdomain"), scheme: sv.scheme,
  }, setShipErr);
  const { clearStatus, setPolling } = watch;
  const preview = usePreview(api, facts, shipId, sentOptions);
  const { setGenErr } = preview;

  // -- views -----------------------------------------------------------------
  const [step, setStep] = useState(0);
  const [view, setView] = useState<ViewId>("flow");
  const [navOpen, setNavOpen] = useState(true);
  const [previewOpen, setPreviewOpen] = useState(false);
  const capacity = useCapacity(api, view === "capacity", accountId);
  // The images of the location selected for the bundle, read from the account;
  // the catalogue (null) when nothing connected is selected. Manual entry reads
  // nothing from an account, so it is the catalogue too.
  const imagesHarbor = sourceMode === "connect" && who ? harborId : null;
  const [imagesAll, setImagesAll] = useState(false);
  // The catalogue answers the same list either way, so it is not re-read.
  const images = useImages(api, view === "images", imagesHarbor,
                           imagesHarbor ? imagesAll : false);
  const [planInputs, setPlanInputs] = useState<PlanInputs>(EMPTY_PLAN_INPUTS);
  const [sizingModels, setSizingModels] = useState<SizingModel[]>([]);
  // Null until decided: the defaults are one per served model and are seeded
  // once, so an emptied list stays empty.
  const [savedSizings, setSavedSizings] = useState<SavedSizing[] | null>(null);

  // -- session restore -------------------------------------------------------
  // Ids a restored session is waiting to re-select, each consumed by the load
  // of the list that can confirm it still exists.
  const pendingWorkspace = useRef<number | null>(null);
  const pendingHarbor = useRef<string | null>(null);
  const pendingShip = useRef<string | null>(null);
  // A restored declaration, until the served vocabulary can check it.
  const restoredDeclaration = useRef<string[] | null>(null);
  const [restored, setRestored] = useState(false);
  // The snapshot's ids, written back in place of the page's own until the list
  // that could refute each one has answered. A failed read releases nothing:
  // "could not ask" is not "it is gone".
  const [held, setHeld] = useState<HeldIds | null>(null);
  /** Stop writing these ids back: what could refute them has arrived. */
  const release = useCallback((...keys: (keyof HeldIds)[]) => setHeld((h) => {
    if (!h || keys.every((k) => h[k] == null)) return h;
    const next = { ...h };
    for (const k of keys) next[k] = null;
    return next;
  }), []);

  useEffect(() => {
    api.keyDetect().then((r) => {
      if (r.candidates[0]) setKeyPath(r.candidates[0].path);
    }).catch(() => {});
    api.optionDefaults().then(applyDefaults).catch(() => {});
    api.sizingModels().then((ms) => {
      setSizingModels(ms);
      setSavedSizings((s) => s ?? defaultSizings(ms));
    }).catch(() => {});

    // The key lives in the server process, so a refresh only made the page forget.
    const saved = session.load();
    if (saved) {
      setSourceMode(saved.sourceMode);
      setManual(saved.manual);
      setOptions((o) => ({ ...o, ...saved.options }));
      setStep(saved.step);
      setView(saved.view);
      setPlanInputs(saved.plan);
      setSavedSizings(saved.sizings);
      setConfirmed(saved.confirmed);
      pendingShip.current = saved.shipId;
      // Applied now, because an empty declaration would clear the restored SV
      // options on the first render; the vocabulary effect checks it later.
      if (saved.sourceMode === "manual" && saved.declaredFunctionalities.length) {
        setDeclared(saved.declaredFunctionalities);
        restoredDeclaration.current = saved.declaredFunctionalities;
      }
      setHeld({ accountId: saved.accountId, workspaceId: saved.workspaceId,
                harborId: saved.harborId, shipId: saved.shipId });
    }
    api.keyStatus().then(async (r) => {
      if (!r.connected || !r.user) return;
      setWho({ email: r.user.email, keyId: r.key_id ?? "" });
      setAccountsBusy(true);
      const accts = await api.accounts().finally(() => setAccountsBusy(false));
      setAccounts(accts);
      pendingWorkspace.current = saved?.workspaceId ?? null;
      pendingHarbor.current = saved?.harborId ?? null;
      setAccountId(saved?.accountId ?? r.default_account_id ?? accts[0]?.id ?? null);
      release("accountId");
    }).catch(() => {})
      // Saving before this would overwrite the snapshot being restored from.
      .finally(() => setRestored(true));
    // Runs once, on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Never the AUTH_TOKEN: session.strip drops it.
  useEffect(() => {
    if (!restored) return;
    session.save({ sourceMode,
                   accountId: accountId ?? held?.accountId ?? null,
                   workspaceId: workspaceId ?? held?.workspaceId ?? null,
                   harborId: harborId ?? held?.harborId ?? null,
                   shipId: shipId ?? held?.shipId ?? null,
                   declaredFunctionalities: sourceMode === "manual" ? declared : [],
                   manual, options, step, view, plan: planInputs,
                   sizings: savedSizings, confirmed });
  }, [restored, sourceMode, accountId, workspaceId, harborId, shipId, held,
      declared, manual, options, step, view, planInputs, savedSizings,
      confirmed]);

  // -- connecting ------------------------------------------------------------
  const evicted = useRef<Set<string>>(new Set());

  /** Hand the key back, and forget everything read with it. Lands on manual
   *  entry, which is what still works with no key. */
  const disconnect = async () => {
    try { await api.keyClear(); } catch { /* forgetting locally still helps */ }
    setWho(null); setAccounts([]); setAccountId(null);
    setWorkspaces([]); setWorkspaceId(null);
    setLocations([]); setHarborId(null); setShipId(null); setFacts(null);
    clearStatus(); setPolling(false);
    forgetToken();
    // The server drops every token it minted with this key.
    evicted.current.clear();
    session.clear();
    setHeld(null);
    // The images view works with no key, as the catalogue.
    setView((v) => (v === "images" ? v : "flow"));
    setStep(0);
    setSourceMode("manual");
  };

  /** Connect with a key. Resolves with the refusal to show, or null. */
  const connect = async (body: ConnectBody): Promise<string | null> => {
    if (connecting) return null;
    setConnecting(true);
    try {
      const r = await api.keySet(body);
      setWho({ email: r.user.email, keyId: r.key_id });
      // Connecting is the deliberate act that chooses the account as the source;
      // a restored session is not, so this is here rather than an effect on `who`.
      switchMode("connect");
      setAccountsBusy(true);
      const accts = await api.accounts().finally(() => setAccountsBusy(false));
      setAccounts(accts);
      // Ids a snapshot is still holding get their chance now, through the same
      // pending refs a restore uses. The account is checked against the list,
      // since this may be a different key.
      const h = held;
      if (h) {
        pendingWorkspace.current = h.workspaceId;
        pendingHarbor.current = h.harborId;
        pendingShip.current = h.shipId;
      }
      const known = h?.accountId != null
        && accts.some((a) => a.id === h.accountId) ? h.accountId : null;
      setAccountId(known ?? r.default_account_id ?? accts[0]?.id ?? null);
      release("accountId");
      return null;
    } catch (e) {
      return String((e as Error).message);
    } finally {
      setConnecting(false);
    }
  };

  // -- the account tree --------------------------------------------------------
  useEffect(() => {
    // Cleared before the guard, so a cleared account leaves no workspaces behind.
    setWorkspaces([]); setWorkspaceId(null);
    if (!accountId || !who) return;
    setWorkspacesBusy(true);
    let live = true;
    api.workspaces(accountId).then((ws) => {
      if (!live) return;
      setWorkspaces(ws);
      const want = pendingWorkspace.current;
      pendingWorkspace.current = null;
      setWorkspaceId(ws.find((w) => w.id === want)?.id ?? ws[0]?.id ?? null);
      release("workspaceId");
    }).catch((e) => { if (live) setLocErr(e.message); })
      .finally(() => { if (live) setWorkspacesBusy(false); });
    return () => { live = false; };
  }, [api, accountId, who, release]);

  // The account's funcId vocabulary once connected, the keyless baseline before.
  // A failed read keeps the previous answer.
  const vocabAccount = accountId && who ? accountId : null;
  const funcIdsRead = useResource(
    () => api.funcIdVocabulary(vocabAccount ?? undefined), [api, vocabAccount]);
  const funcIds: FuncIdVocabulary =
    funcIdsRead.data ?? { source: "baseline", choices: [] };

  // The agent variables left for the bundle's options, scoped server-side to
  // what this location runs; null asks for the whole reference.
  const enabledKey = enabled === null ? null : enabled.join(",");
  const agentEnvRead = useResource(
    () => api.agentEnv(enabledKey === null ? null : enabledKey.split(",").filter(Boolean)),
    [api, enabledKey]);
  const agentEnv: AgentEnvVar[] = agentEnvRead.data ?? [];

  // The initial load of a workspace's locations. Refresh is a separate path
  // (refreshLocations) because this one also resolves a restored selection.
  useEffect(() => {
    setNewLoc((n) => ({ ...n, workspace_id: workspaceId ?? 0 }));
    setLocations([]); setHarborId(null); setLocErr(null);
    if (workspaceId == null) return;
    setLocBusy(true);
    let live = true;
    api.locations(workspaceId).then((ls) => {
      if (!live) return;
      setLocations(ls);
      const want = pendingHarbor.current;
      pendingHarbor.current = null;
      // Re-selected only if still there; a gone location takes its agent id with it.
      if (want && ls.some((l) => l.id === want)) {
        setHarborId(want);
        release("harborId");
      } else {
        release("harborId", "shipId");
      }
    }).catch((e) => { if (live) setLocErr(e.message); })
      .finally(() => { if (live) setLocBusy(false); });
    return () => { live = false; };
  }, [api, workspaceId, release]);

  const location = useMemo(
    () => locations.find((l) => l.id === harborId) ?? null, [locations, harborId]);
  const ships: Ship[] = location?.ships ?? [];

  // Read off the list rather than facts, so a settings save shows here at once.
  const engineSize = useMemo(
    () => sizeStatement(raw("engine_cpu_limit"), raw("engine_mem_limit"), location),
    [raw, location]);

  useEffect(() => {
    setShipId(null); setFacts(null); clearStatus(); setShowCreateShip(false);
    // A token belongs to one agent.
    forgetToken();
    if (!harborId) return;
    setFactsBusy(true);
    let live = true;
    api.facts(harborId).then((f) => { if (live) setFacts(f); })
      .catch((e) => { if (live) setShipErr(goneNotice(e, "location") ?? e.message); })
      .finally(() => { if (live) setFactsBusy(false); });
    return () => { live = false; };
  }, [api, harborId, forgetToken, clearStatus]);

  useEffect(() => {
    // A restored agent outranks the auto-pick, once the location confirms it.
    const want = pendingShip.current;
    if (want && ships.some((s) => s.id === want)) {
      pendingShip.current = null;
      setShipId(want);
    } else if (ships.length === 1 && !shipOnline(ships[0])) {
      // A lone agent is picked only if idle: a new deployment should not clone
      // a live identity.
      setShipId(ships[0].id);
    }
    if (harborId) release("shipId");
    // On a change of location or agent count, not on every re-read of the list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [harborId, ships.length]);

  // -- the credential ----------------------------------------------------------
  // A token this app minted for the agent comes back after a reload; the server
  // holds it. Connect mode only: in manual entry the token is typed.
  useEffect(() => {
    setRecall("asking");
    if (sourceMode !== "connect" || !shipId) return;
    let live = true;
    api.mintedToken(shipId).then((r) => {
      if (!live) return;
      setRecall(recalled(r));
      if (r.auth_token) set("auth_token", r.auth_token);
    }).catch(() => {
      // Could not ask, which is not the same as "holds none".
      if (live) setRecall("unread");
    });
    return () => { live = false; };
  }, [api, sourceMode, shipId, set]);

  /** A hand-typed token wins: drop the server's remembered copy so it does not
   *  come back on reload. Once per agent; a failure re-arms it. */
  const forgetMintedToken = useCallback(() => {
    if (sourceMode !== "connect" || !shipId) return;
    if (evicted.current.has(shipId)) return;
    const ship = shipId;
    evicted.current.add(ship);
    api.forgetMintedToken(ship)
      .then(() => setRecall((r) => (r === "held" ? "none" : r)))
      .catch(() => { evicted.current.delete(ship); });
  }, [api, sourceMode, shipId]);

  /** Issue a new AUTH_TOKEN for the selected agent and put it in the field.
   *  Resolves false when the agent changed while the request was out. */
  const regenerateToken = async (): Promise<boolean> => {
    const ship = shipId;
    if (!harborId || !ship) return false;
    const r = await api.issueToken(harborId, ship);
    if (shipRef.current !== ship) return false;
    set("auth_token", r.auth_token);
    // The server remembers what it issued, so a later hand edit can evict it again.
    setRecall("held");
    evicted.current.delete(ship);
    return true;
  };

  /** Create an agent in the selected location. The token is captured here, the
   *  one moment it is free. Resolves true once the agent exists. */
  const createShipNow = async (name: string): Promise<boolean> => {
    const harbor = harborRef.current;
    const ws = workspaceRef.current;
    if (!harbor || ws == null) return false;
    let created;
    try {
      created = await api.createShip(harbor, name);
    } catch (e) {
      if (harborRef.current === harbor) {
        setShipErr(goneNotice(e, "location") ?? String((e as Error).message));
      }
      return false;
    }
    try {
      // Both are cold after the write, and neither needs the other.
      const [ls, f] = await Promise.all([
        api.locations(ws), api.facts(harbor).catch(() => null)]);
      if (workspaceRef.current === ws) setLocations(ls);
      if (harborRef.current !== harbor) return true;
      if (f) setFacts(f);
      setShipId(created.ship.id);
      setShowCreateShip(false);
      set("auth_token", created.auth_token);
      setShipTokenNotice(created.token_error);
    } catch (e) {
      if (harborRef.current === harbor) {
        setShipErr(goneNotice(e, "location") ?? String((e as Error).message));
      }
    }
    return true;
  };

  // -- manual entry ------------------------------------------------------------
  // The declaration is the funcIds, filtered against what is served.
  const manualFuncIds = useMemo(
    () => functionalities.filter((f) => declared.includes(f.id)).map((f) => f.id),
    [functionalities, declared]);

  // Facts rebuilt from the typed values, debounced like the preview. Nothing is
  // built from an id that is not the shape one comes in. The token is not in
  // the request, so only whether it is well formed is a dependency: typing it
  // does not re-read the registry.
  const manualReady = manualComplete(manual.harbor_id, manual.ship_id,
                                     String(options.auth_token ?? ""));
  // The server reads the public registry for these facts, which takes seconds.
  // While it does, the facts on screen are for the previous values.
  const [manualReading, setManualReading] = useState(false);
  const [manualWarnings, setManualWarnings] = useState<string[]>([]);
  useEffect(() => {
    if (sourceMode !== "manual") return;
    if (!manualReady) {
      setFacts(null); setShipId(null); setManualWarnings([]); return;
    }
    let live = true;
    setManualReading(true);
    const timer = window.setTimeout(() => {
      api.manualFacts({
        harbor_id: manual.harbor_id.trim(),
        ship_id: manual.ship_id.trim(),
        func_ids: manualFuncIds,
      }).then((r: ManualFactsOut) => {
        if (!live) return;
        setFacts(r.facts);
        setShipId(r.facts.ships[0].id);
        setManualWarnings(r.warnings);
      }).catch((e) => { if (live) setGenErr(String(e.message)); })
        .finally(() => { if (live) setManualReading(false); });
    }, 250);
    // A superseded read is dropped; the next run marks itself as reading.
    return () => { live = false; window.clearTimeout(timer); setManualReading(false); };
  }, [api, sourceMode, manual, manualFuncIds, manualReady, setGenErr]);

  /** Switching modes drops what the other one established, the token included. */
  const switchMode = (m: string) => {
    const mode = m as "connect" | "manual";
    if (mode === sourceMode) return;
    setSourceMode(mode);
    setFacts(null); setShipId(null); clearStatus(); setGenErr(null);
    forgetToken();
  };

  // -- option groups -------------------------------------------------------------
  // Only ever opens groups, so one opened by hand stays open.
  useEffect(() => {
    setGrpOn((g) => detectGroups(options, g, { sv: sv.required }));
  }, [options, sv.required, setGrpOn]);
  // The SV options a location or import leaves stranded, corrected once.
  useEffect(() => { patch(sv.patch); }, [sv.patch, patch]);

  /** Suggest the namespace for a declaration, if the field still holds a
   *  suggested one. Several functionalities suggest the first in served order,
   *  as a connected location carrying them does. */
  const suggestNsFor = useCallback((ids: string[]) => {
    if (!ids.length) return;
    const lead = startFunctionality(ids, functionalities);
    const f = functionalities.find((x) => x.id === lead);
    if (!f) return;
    setOptions((o) => {
      const ns = suggestNamespace(String(o.namespace ?? ""), f, functionalities);
      return ns == null ? o : { ...o, namespace: ns };
    });
  }, [functionalities, setOptions]);

  /** Replace the declaration. Only a location being opened suggests a namespace:
   *  looking at a functionality must not change the bundle. */
  const pickFunctionality = useCallback((id: string, suggestNs = false) => {
    setDeclared([id]);
    if (suggestNs) suggestNsFor([id]);
  }, [suggestNsFor]);

  /** Manual entry's checkbox. Service virtualization is declared alone, since
   *  crane sizes every pod from one limit pair. */
  const declareFunctionality = useCallback((id: string, on: boolean) => {
    const next = toggleDeclared(declared, id, on,
                                functionalities.map((f) => f.id),
                                exclusiveWith(engineFuncIds));
    setDeclared(next);
    suggestNsFor(next);
  }, [declared, engineFuncIds, functionalities, suggestNsFor]);

  // Which functionality a location opens on. Keyed on the harbor rather than on
  // `facts`, which is refetched after creating an agent and must not move the view.
  useEffect(() => {
    if (!functionalities.length) return;
    // A restored declaration is checked member by member once the vocabulary
    // lands; the survivors stand, and none surviving is a fresh manual session.
    const restoredIds = restoredDeclaration.current;
    if (restoredIds) {
      restoredDeclaration.current = null;
      const kept = functionalities.filter((f) => restoredIds.includes(f.id))
        .map((f) => f.id);
      if (kept.length) {
        if (kept.length !== restoredIds.length) setDeclared(kept);
        return;
      }
      pickFunctionality(functionalities[0].id);
      return;
    }
    // Manual entry declares rather than reads. With no facts yet (a location's
    // are in flight), keep the current view rather than flip to the default.
    if (sourceMode === "manual" || !facts) {
      if (!declared.length) pickFunctionality(functionalities[0].id, true);
      return;
    }
    const start = startFunctionality(facts.func_ids, functionalities);
    if (start) pickFunctionality(start, true);
    // Reads `declared` and `facts` without following them: re-running on either
    // would re-force the starting functionality over the user's choice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facts?.harbor_id, functionalities, pickFunctionality, sourceMode]);

  // -- step 1: has the choice been made? -------------------------------------------
  // Both lists auto-pick, so being selected is not being confirmed. Manual entry
  // has no lists: typing the ids is the deliberate act.
  const locConfirmed = !!harborId && confirmed.loc === harborId;
  const shipConfirmed = !!shipId && confirmed.ship === shipId;
  const chosen = sourceMode === "manual" || (locConfirmed && shipConfirmed);
  const agentBlocked = !facts || !shipId
    ? "fill in the agent details to continue"
    : chosen ? ""
    : "confirm " + [!locConfirmed ? "the location" : "",
                    !shipConfirmed ? "the agent" : ""]
      .filter(Boolean).join(" and ");

  // -- step 2: what the bundle is --------------------------------------------------
  // Judged against the format: an option this bundle cannot carry has no field
  // on screen, so it must not block anything.
  const namespaceOk = !applies("namespace") || !!txt("namespace");
  const saOk = !applies("service_account_name") || serviceAccountOk(options);
  const saCreate = options.service_account_create !== false;
  const tokenPlan = downloadPlan(preview.previewToken);
  const locUnclaimed = unclaimedFuncIds(facts?.func_ids, functionalities, funcIds);
  // Options set for a functionality the location does not run are cleared; the
  // switch that would clear them is not on screen.
  const notRun = notRunPatch(options, enabled);
  useEffect(() => { patch(notRun); }, [notRun, patch]);
  const incomplete = incompleteGroups(
    options, sv.groupRequired, svConst.backends, applies);
  const configureBlocked = configureBlockedBy(
    options, blockingGroups(options, sv.groupRequired, svConst.backends, applies));
  // Manual entry is the only mode the identity can be blank in.
  const idBlanks = useMemo(
    () => (sourceMode === "manual"
      ? blankManualIds(manual.harbor_id, manual.ship_id) : []),
    [sourceMode, manual]);
  const downloadGaps = useMemo(
    () => gaps(idBlanks, blanks, tokenPlan.incomplete, placeholderSources),
    [idBlanks, blanks, tokenPlan.incomplete, placeholderSources]);

  const envArea = (
    <EnvVars env={options.extra_env} vars={agentEnv} reserved={reservedEnv}
      cluster={!isDocker(format)}
      onChange={(v) => set("extra_env", v)} />
  );

  const groupBody: Record<GroupId, ReactNode> = {
    registry: (
      <RegistryGroup applies={applies} whyIgnored={whyIgnored}
        registry={raw("private_registry")}
        pullSecret={raw("pull_secret")}
        registryAuth={Boolean(options.registry_auth)}
        onRegistry={(v) => set("private_registry", v)}
        onPullSecret={(v) => set("pull_secret", v)}
        onRegistryAuth={(v) => set("registry_auth", v)} />
    ),
    proxy: <ProxyGroup proxy={proxy} onField={setProxy} />,
    ca: (
      <CaGroup applies={applies} openshift={isOpenshift(options)}
        mode={caMode} onMode={setCaMode}
        configmap={raw("ca_existing_configmap")}
        configmapKey={raw("ca_configmap_key")}
        certFile={raw("ca_cert_file")}
        onCertFile={(v) => set("ca_cert_file", v)} />
    ),
    sched: (
      <SchedGroup
        tolerations={options.tolerations} nodeSelector={options.node_selector}
        engineTolerations={options.engine_tolerations}
        engineNodeSelector={options.engine_node_selector}
        onPatch={patch} />
    ),
    security: (
      <SecurityGroup applies={applies} cluster={!isDocker(format)}
        useSecret={Boolean(options.use_secret)}
        clusterRbac={Boolean(options.cluster_rbac)}
        // Absent means the default, which is on.
        restrictEngines={options.restrict_engines !== false}
        serviceType={String(options.service_type ?? "CLUSTERIP")}
        // Tri-state: absent stays absent rather than being written as a choice.
        autoUpdate={options.auto_update == null ? null : Boolean(options.auto_update)}
        onUseSecret={(v) => set("use_secret", v)}
        onClusterRbac={(v) => set("cluster_rbac", v)}
        onRestrictEngines={(v) => set("restrict_engines", v)}
        onAutoUpdate={(v) => set("auto_update", v)}
        onServiceType={(v) => set("service_type", v)} />
    ),
    sv: (
      <SvGroup sv={sv}
        onIngress={(v) => set("sv_ingress", v)}
        onSubdomain={(v) => set("sv_subdomain", v)}
        onTlsSecret={(v) => set("sv_tls_secret", v)}
        onGateway={(v) => set("sv_istio_gateway", v)} />
    ),
    svDocker: (
      <SvDockerGroup
        hostname={raw("sv_hostname")} cert={raw("sv_tls_cert")}
        key_={raw("sv_tls_key")}
        onHostname={(v) => set("sv_hostname", v)}
        onCert={(v) => set("sv_tls_cert", v)}
        onKey={(v) => set("sv_tls_key", v)} />
    ),
  };

  /** Put a location that was just changed back into the list, in place. */
  const locationUpdated = useCallback((loc: Location) => {
    setLocations((ls) => ls.map((l) => (l.id === loc.id ? { ...l, ...loc } : l)));
  }, []);

  // -- Refresh -----------------------------------------------------------------------
  // Writes `locations` and nothing else: not the selection, the options or facts.
  const [refreshing, setRefreshing] = useState(false);
  const refreshLocations = useCallback(async () => {
    const ws = workspaceRef.current;
    if (ws == null) return;
    setRefreshing(true);
    setLocErr(null);
    try {
      // The server caches the list; drop it first or the re-read is a no-op.
      await api.refresh();
      const ls = await api.locations(ws);
      if (workspaceRef.current === ws) setLocations(ls);
    } catch (e) {
      // The list on screen stays: a failed read says nothing about the account.
      if (workspaceRef.current === ws) setLocErr((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [api]);

  // BlazeMeter refuses a create below the slot minimum, so it is said first.
  const createLocBlockedBy = !newLoc.name.trim() ? "name the location first"
    : !newLoc.workspace_id ? "pick a workspace above first"
    : slotsBlockedBy(newLoc.func_ids, newLoc.slots, slotMinimums);

  /** Create the private location the form describes, and select it. */
  const createLocationNow = async () => {
    const ws = workspaceRef.current;
    if (ws == null || !accountId) return;
    try {
      const l = await api.createLocation({ ...newLoc, account_id: accountId });
      const ls = await api.locations(ws);
      if (workspaceRef.current !== ws) return;
      setLocations(ls); setHarborId(l.id); setShowCreateLoc(false);
    } catch (e) {
      if (workspaceRef.current === ws) setLocErr(String((e as Error).message));
    }
  };

  const accountName = useMemo(
    () => accounts.find((a) => a.id === accountId)?.name ?? null,
    [accounts, accountId]);
  const workspaceName = useMemo(
    () => workspaces.find((w) => w.id === workspaceId)?.name ?? null,
    [workspaces, workspaceId]);
  /** One segment of the path under the flow; `warn` says "none yet" in amber. */
  const pathSeg = (label: string, value: string | null, warn = false) => (
    <span className="flex items-center gap-1.5">
      <span className="text-3xs uppercase tracking-wide text-slate-400">{label}</span>
      <span className={"text-xs font-medium "
        + (value ? "text-slate-800" : warn ? "text-amber-700" : "text-slate-400")}>
        {value ?? (warn ? "none yet" : "—")}
      </span>
    </span>
  );

  /** What the sizing card states, and what the open location re-asks with its
   *  own agent count. One row per ticked functionality, in served order; a
   *  model with no measured figure carries none. */
  const profileAsk: PlanAsk = {
    sizings: sizingModels
      .filter((m) => planInputs.functionalities.includes(m.functionality))
      .map((m) => ({
        functionality: m.functionality,
        target: planInputs.targets[m.functionality] ?? "",
        ...(m.measured
          ? { figure: planInputs.figures[m.functionality] ?? "" } : {}),
      })),
    engineCpu: raw("engine_cpu_limit"),
    engineMem: raw("engine_mem_limit"),
    enginesPerNode: raw("engines_per_node"),
  };

  // BlazeMeter's name for a funcId: the account's vocabulary (every funcId it
  // has), then the served functionalities (the covered ones). Null where
  // neither names it: unconnected, an uncovered funcId has no served name.
  const funcLabel = useCallback((id: string) =>
    funcIds.choices.find((c) => c.id === id)?.label
      ?? functionalities.find((f) => f.id === id)?.label ?? null,
  [funcIds.choices, functionalities]);
  const catalogueReason: CatalogueReason | null = imagesHarbor ? null
    : sourceMode === "manual" ? "manual"
    : who ? "no-location" : "disconnected";

  const { cap, capErr, capRefreshing, refreshCapacity } = capacity;
  const body = view === "images" ? (
    <main className="max-w-screen-xl mx-auto p-4 sm:p-6">
      <ImagesView answer={images.answer} busy={images.busy} error={images.error}
        all={imagesAll} setAll={setImagesAll} labelOf={funcLabel}
        registry={txt("private_registry") || null}
        catalogueReason={catalogueReason} />
    </main>
  ) : view === "capacity" ? (
    <main className="max-w-screen-xl mx-auto p-6">
      {!accountId && <p className="text-sm text-slate-500">Connect first.</p>}
      <ErrorMsg msg={capErr} className="text-sm" />
      {!cap && accountId && !capErr && (
        <p className="text-sm text-slate-500">reading the account…</p>
      )}
      {cap && <CapacityView cap={cap} refresh={refreshCapacity}
        refreshing={capRefreshing} />}
    </main>
  ) : (
    <main className="max-w-screen-xl mx-auto p-6">
      <StepFlow
        at={step} onGo={setStep}
        // Which account, workspace, location and agent the bundle is for.
        footer={sourceMode === "connect" ? (
          <div className="mt-3 pt-2.5 border-t border-slate-200 flex items-center gap-2 flex-wrap">
            {pathSeg("account", accountName)}
            <span className="text-slate-300">›</span>
            {pathSeg("workspace", workspaceName)}
            <span className="text-slate-300">›</span>
            {pathSeg("location", location?.name ?? null)}
            <span className="text-slate-300">›</span>
            {pathSeg("agent",
                     ships.find((x) => x.id === shipId)?.name ?? null,
                     !!location)}
            {!!location && ships.length === 0 && (
              <span className="text-2xs text-amber-700 ml-1">
                — this location is empty; the first agent has to be created
              </span>
            )}
          </div>
        ) : undefined}
        done={[!agentBlocked, !configureBlocked, false]}
        blockedBy={[agentBlocked, configureBlocked, ""]}>
        <Section n={1} title="Capacity & agent" done={!agentBlocked}
          hint="Size the run, then the location and agent it is generated for.">
          <div className="space-y-3">
          <Sizing
            api={api} ask={profileAsk} models={sizingModels}
            inputs={planInputs} setInputs={setPlanInputs}
            saved={savedSizings ?? []} setSaved={setSavedSizings}
            // The engine size is the bundle's own option, edited from here too.
            setEngine={(cpu, mem) => patch({ engine_cpu_limit: cpu, engine_mem_limit: mem })}
            // An integer option: generate() refuses a string.
            setPerNode={(v) => set("engines_per_node", v.trim() ? Number(v) : null)} />
          <AgentPanel
            api={api} profile={profileAsk}
            source={{
              mode: sourceMode, switchTo: switchMode,
              manual, setManual, who,
              manualReading, manualWarnings,
            }}
            locations={{
              accountName, workspaceName,
              list: locations,
              selectedId: harborId, pick: setHarborId,
              busy: locBusy, error: locErr, updated: locationUpdated,
              refresh: refreshLocations, refreshing,
              create: {
                open: showCreateLoc,
                setOpen: (v) => { setLocErr(null); setShowCreateLoc(v); },
                workspace: workspaceName,
                draft: newLoc,
                setDraft: (f) => setNewLoc((n) => ({ ...n, ...f(n) })),
                choices: funcIds.choices, engines: engineFuncIds,
                minimums: slotMinimums,
                blockedBy: createLocBlockedBy,
                submit: createLocationNow,
              },
              confirmed: locConfirmed,
              confirm: () => setConfirmed((c) => ({ ...c, loc: harborId })),
            }}
            agents={{
              id: shipId,
              pick: (id) => { setShipId(id); forgetToken(); },
              busy: factsBusy, facts,
              showCreate: showCreateShip, setShowCreate: setShowCreateShip,
              create: createShipNow,
              error: shipErr, tokenNotice: shipTokenNotice,
              confirmed: shipConfirmed,
              confirm: () => setConfirmed((c) => ({ ...c, ship: shipId })),
            }}
            credential={{
              token: raw("auth_token"),
              // Typing is the one write the app did not make, so it evicts the
              // remembered copy.
              setToken: (v) => {
                set("auth_token", v || null);
                forgetMintedToken();
              },
              regenerate: regenerateToken,
              note: recallNote(recall),
            }} />
          </div>
        </Section>

        <Section n={2} title="Configure"
          hint="Everything re-renders the preview live.">
          <ConfigurePanel
            functionalities={functionalities} declare={declareFunctionality}
            sourceMode={sourceMode} enabled={enabled}
            locUnclaimed={locUnclaimed}
            options={options} set={set}
            format={format}
            setFormat={(v) => set("output_format", v)}
            applies={applies}
            grpOn={grpOn} grpRequired={sv.groupRequired}
            grpDeclined={sv.groupDeclined}
            // Docker carries no limits pair, so no size is stated for it.
            engineNote={applies("engine_cpu_limit") ? engineSize.text : null}
            flipGroup={(id, on) => flipGroup(id, on, !!sv.groupRequired[id])}
            groupBody={groupBody} envArea={envArea}
            incomplete={incomplete} blanks={blanks}
            namespaceOk={namespaceOk} saOk={saOk} saCreate={saCreate}
            exportProfile={exportProfile}
            importProfile={(f) => importProfile(f, setGenErr)} />
        </Section>

        <Section n={3} title="Download & verify">
          <DownloadPanel
            api={api}
            bundle={{
              // What the preview showed, markers included.
              facts, reading: sourceMode === "manual" && manualReading,
              shipId, options: sentOptions, format,
              sv, genErr: preview.genErr, gaps: downloadGaps,
              goToConfigure: () => setStep(1),
              goToAgent: () => setStep(0),
            }}
            credential={{ plan: tokenPlan }}
            attempt={attempt} report={setAttempt}
            watch={{
              available: sourceMode === "connect",
              on: watch.polling, setOn: setPolling,
              agent: ships.find((s) => s.id === shipId)?.name || shipId,
              status: watch.status, mocks: watch.svMocks, checks: watch.svChecks,
              check: watch.checkEndpoint,
            }} />
        </Section>
      </StepFlow>
    </main>
  );

  // Null for the answers that need no sentence, including "not read yet".
  const notice = buildNotice(build?.stale ?? null);

  return (
    // The window's height, so the pane beside the drawer is what scrolls and the
    // account menu at the drawer's foot stays reachable. No overflow clip here:
    // that menu has to be able to leave the drawer.
    <div className="h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200 px-4 py-2.5 shrink-0">
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-bold text-slate-900 whitespace-nowrap">
            <span className="text-bzm">BlazeMeter</span> OPL Generator
          </h1>
          <span className="text-xs text-slate-400 truncate">
            private-location Kubernetes / OpenShift manifests, from your real account
          </span>
        </div>
      </header>

      {/* An alert only for a stale page; an unrecorded one is nothing known wrong. */}
      {notice && (
        <div role={notice.tone === "warning" ? "alert" : "status"}
          className={"border-b px-4 py-2 text-sm "
            + (notice.tone === "warning"
              ? "bg-amber-100 text-amber-900 border-amber-300"
              : "bg-slate-100 text-slate-700 border-slate-300")}>
          <strong>{notice.heading}</strong>{" "}
          {notice.detail}{" "}
          <code className="font-mono">{notice.command}</code>
        </div>
      )}

      <div className="flex grow min-h-0">
        <NavDrawer view={view} setView={setView} connected={!!who}
          open={navOpen} setOpen={setNavOpen}
          footer={
            <AccountMenu
              who={who} disconnect={disconnect}
              accounts={accounts} accountId={accountId}
              setAccountId={setAccountId} accountsBusy={accountsBusy}
              workspaces={workspaces} workspaceId={workspaceId}
              setWorkspaceId={setWorkspaceId} workspacesBusy={workspacesBusy}
              keyPath={keyPath} setKeyPath={setKeyPath}
              connect={connect}
              connecting={connecting} collapsed={!navOpen} />
          } />
        <div className="grow min-w-0 overflow-y-auto">{body}</div>
        {/* Only beside the view that produces manifests. */}
        {view === "flow" && (
          <PreviewDrawer files={preview.files} activeFile={preview.activeFile}
            setActiveFile={preview.setActiveFile} genErr={preview.genErr}
            open={previewOpen} setOpen={setPreviewOpen} />
        )}
      </div>
    </div>
  );
}
