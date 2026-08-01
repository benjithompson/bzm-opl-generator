import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Api, Account, AgentStatus, Capacity, Facts, Feature,
  GeneratedFile, ManualFactsOut, SavedBundle, TokenReport,
  FuncIdChoice, Location, Options, Ship, Suggestion, SvCheckOut,
  SvConstants, SvMocksOut, Workspace,
} from "./api";
import {
  Button, Check, Field, inputCls, Section, TextInput,
} from "./components";
// What a download is about to do to the agent's credential. The branch a bundle's
// token arrived by is core's and comes back on the answer; this decides what to
// say about the click that has not happened yet, which is the only moment a
// rotation can still be reconsidered (#64).
import { downloadPlan } from "./token";
import { SvCtx } from "./SvPrereqs";
// The option groups of the Configure step: one declaration each (title, hint, the option
// keys it owns, the features it belongs to, and its detect/enable/disable),
// plus a body per group. This file only wires them -- what a group *is*, and
// which of them a feature puts on screen, lives in optionGroups.ts.
import {
  allGroupsOff, caModeOf, caModePatch, CaMode, detectGroups, enginePreset,
  featuresOf, GROUP_BY_ID, GroupFlags, GroupId, incompleteGroups,
  serviceAccountOk, startFeature, suggestNamespace, SV_NONE, svConfigured,
  unclaimedFuncIds,
} from "./optionGroups";
// The preflight panel's own decisions -- how a verdict list reads, what a
// picked file has to be, what a refused import leaves behind. No verdict is
// reached there either: they are doctor's, and arrive in doctor's order.
import {
  evidenceHeader, imported, NO_PREFLIGHT, PreflightState,
  readEvidence, rechecked, refused,
} from "./preflight";
// Acting on the same file: what each suggestion offers, what applying writes,
// and how to take it back. What the evidence means is suggest.py's and how it
// stands against the options is suggest.merge()'s -- both arrive on the row.
import { Applied, apply, NOTHING_APPLIED, undo } from "./suggestions";
// What survives a refresh, and the one thing that must not.
import * as session from "./session";
// The shape a hand-typed id and token come in, and what is wrong with one that
// does not. Nothing is built from a value that fails it.
import { manualComplete } from "./manualIds";
// What the account can generate, by workspace.
import { CapacityView } from "./CapacityView";
// The capacity planner: a view of its own, not a step. See PlanPanel.
import { PlanHandover, PlanPanel } from "./PlanPanel";
// The planner's form shape and its empty value: plain data, so the session
// snapshot and this page share one declaration of it.
import { EMPTY_PLAN_INPUTS, PlanInputs } from "./usePlan";
import { AgentPanel } from "./steps/AgentPanel";
import { ConfigurePanel } from "./steps/ConfigurePanel";
import { DownloadPanel } from "./steps/DownloadPanel";
import { CaGroup } from "./groups/CaGroup";
import { ProxyGroup } from "./groups/ProxyGroup";
import { RegistryGroup } from "./groups/RegistryGroup";
import { SchedGroup } from "./groups/SchedGroup";
import { SecurityGroup } from "./groups/SecurityGroup";
import { SizingGroup } from "./groups/SizingGroup";
import { SvGroup } from "./groups/SvGroup";
import { PreviewDrawer } from "./layout/PreviewDrawer";
import { NavDrawer, ViewId } from "./layout/NavDrawer";
// The key, the account and the workspace: session-wide, so all three live in
// the drawer rather than inside step 1. See AccountMenu.
import { AccountMenu } from "./layout/AccountMenu";
import { StepFlow } from "./layout/StepFlow";


// The one thing this page does not own: the caller of the local routes. It
// arrives as an adapter from main.tsx -- the real one in the browser, a fake in
// vitest -- because every effect below reaches it, and a module-level import
// leaves nowhere to alter that behaviour without editing in place. Every bug
// this page has had lived in one of those effects.
//
// Fixed for the page's lifetime, which is why it is not in any dependency array.
export default function App({ api }: { api: Api }) {
  // -- connection ------------------------------------------------------------
  const [keyPath, setKeyPath] = useState("");
  const [pasteId, setPasteId] = useState("");
  const [pasteSecret, setPasteSecret] = useState("");
  const [saveKey, setSaveKey] = useState(false);
  const [who, setWho] = useState<{ email: string; keyId: string } | null>(null);
  const [connErr, setConnErr] = useState<string | null>(null);
  // A round trip to BlazeMeter over someone's corporate network, so the wait is
  // long enough to look like nothing happened. Also guards re-entry: all three
  // entry points (Browse, Connect, paste) share this one flag.
  const [connecting, setConnecting] = useState(false);

  // -- account tree ----------------------------------------------------------
  const [accounts, setAccounts] = useState<Account[]>([]);
  // Both lists are a round trip to BlazeMeter, and both were silent while they
  // arrived: an empty dropdown and a slow one look the same, so the answer to
  // "why is my account not in here" was to wait and try again. See locBusy,
  // which is the same flag for the location list below them.
  const [accountsBusy, setAccountsBusy] = useState(false);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspacesBusy, setWorkspacesBusy] = useState(false);
  const [workspaceId, setWorkspaceId] = useState<number | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locFilter, setLocFilter] = useState("");
  const [harborId, setHarborId] = useState<string | null>(null);
  const [showCreateLoc, setShowCreateLoc] = useState(false);
  // Served from facts.CATEGORY_BY_FUNC over /api/func-ids, not listed here: the
  // copy that used to live in this file omitted sv-bridge, so an SV-bridge
  // location could not be created from the UI at all.
  const [funcIdChoices, setFuncIdChoices] = useState<FuncIdChoice[]>([]);
  const [newLoc, setNewLoc] = useState({
    name: "", workspace_id: 0, func_ids: ["performance"], slots: 1, threads_per_engine: 500 });
  const [locErr, setLocErr] = useState<string | null>(null);

  // -- agent -----------------------------------------------------------------
  const [shipId, setShipId] = useState<string | null>(null);
  const [showCreateShip, setShowCreateShip] = useState(false);
  const [newShipName, setNewShipName] = useState("");
  const [shipErr, setShipErr] = useState<string | null>(null);
  // Separate from shipErr: the agent WAS created, and only its credential
  // was refused. In the red error slot that reads as a failed creation, and
  // the next click makes a second agent in the same location.
  const [shipTokenNotice, setShipTokenNotice] = useState<string | null>(null);
  const [facts, setFacts] = useState<Facts | null>(null);

  // -- where the three account values come from -------------------------------
  // "connect" reads them from the account; "manual" takes them typed in, for a
  // customer whose account (and cluster) nobody here can reach. Everything
  // downstream consumes `facts` + shipId + options.auth_token and never learns
  // which way they arrived -- that is the whole point of manual facts being the
  // same shape gather() returns.
  const [sourceMode, setSourceMode] = useState<"connect" | "manual">("connect");
  // Identity only. What the location runs is derived from the selected feature
  // (manualFuncIds below) rather than stored: it was state with two writers that
  // disagreed on the miss case, and it is a pure function of `feature`.
  const [manual, setManual] = useState({ harbor_id: "", ship_id: "" });

  // -- options / preview -----------------------------------------------------
  const [defaults, setDefaults] = useState<Options>({});
  const [svConst, setSvConst] = useState<SvConstants>(
    { func_ids: [], ingress_types: [], backends: {} });
  const [options, setOptions] = useState<Options>({ namespace: "blazemeter" });
  // The feature being configured, and the vocabulary it is chosen from. A view
  // over the options, never a scope: one crane is deployed for the selected
  // location and that location's funcIds decide what the manifests contain, so
  // this only decides what is on screen. The list is served (/api/features) so
  // that adding a feature is a backend entry plus a tag on the groups it owns;
  // null until it lands, which hides nothing.
  const [features, setFeatures] = useState<Feature[]>([]);
  const [feature, setFeature] = useState<string | null>(null);
  // One way to read a text option. Written out per-site, the `.trim()` was
  // getting forgotten -- an ingress name pasted with a trailing space missed
  // the SV_PREREQS lookup and the panel silently lost its prose.
  const txt = useCallback(
    (k: string) => String(options[k] ?? "").trim(), [options]);
  // The same read for a controlled input, where trimming would stop the user
  // typing a space -- so the two are separate rather than one with a flag.
  const raw = useCallback(
    (k: string) => String(options[k] ?? ""), [options]);
  /** Drop the credential and everything said about it.
   *
   *  One function because it is one fact -- the token, the rotate choice and the
   *  report of the last download all belong to one agent, and leaving any of them
   *  behind when the target changes is how a bundle ends up carrying, or claiming
   *  to carry, another agent's credential. Called from every place the target
   *  moves: a different location, a different agent, the switch to manual entry.
   *  Declared with the readers above rather than beside `set` so that the effects
   *  further down can reach it. */
  const forgetToken = useCallback(() => {
    setOptions((o) => ({ ...o, auth_token: null }));
    setRotate(false);
    setLastTokenReport(null);
  }, []);

  const [files, setFiles] = useState<GeneratedFile[]>([]);
  const [genErr, setGenErr] = useState<string | null>(null);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  // Carries the namespace it was read from: the field can be edited between
  // polls, and labelling these rows with a namespace they did not come from
  // would vouch for virtual services nobody looked for.
  const [svMocks, setSvMocks] =
    useState<{ ns: string; read: SvMocksOut } | null>(null);
  // Endpoint checks, keyed by the host that was probed rather than by row
  // index: the poll above replaces the list every 10s, and a result must never
  // end up beside a different virtual service than the one it was asked about.
  // Keying by host also retires a result when its host does -- editing the
  // namespace or the domain changes every key, so nothing stale survives.
  const [svChecks, setSvChecks] =
    useState<Record<string, { busy: boolean; res?: SvCheckOut; err?: string }>>({});
  const [polling, setPolling] = useState(false);
  // Which step is open. Here rather than in StepFlow because the download step
  // sends you back to the configure step when a group it depends on is
  // unfinished, and it cannot do that with a position it cannot see.
  const [step, setStep] = useState(0);
  // Which of the two things this page is. The step flow deploys an agent and
  // needs an account or the values off one; the planner works out how much
  // cluster a load target needs and reaches nothing at all -- which is the
  // state a customer with no cluster is actually in, and why it cannot be a
  // step. `plan` holds what was typed into it, so switching views and coming
  // back does not empty the form.
  const [view, setView] = useState<ViewId>("flow");
  // The two drawers. The nav starts open because the three views are the first
  // thing to understand; the preview starts shut because there is nothing in it
  // until an agent is chosen.
  const [navOpen, setNavOpen] = useState(true);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [cap, setCap] = useState<Capacity | null>(null);
  const [capErr, setCapErr] = useState<string | null>(null);
  useEffect(() => {
    if (view !== "capacity" || !accountId) return;
    // Not cleared first. The server holds this for a minute, so a re-entry is a
    // few milliseconds -- but blanking it here showed "reading the account…"
    // on every visit anyway, which is the thing a cache is supposed to stop.
    // What is on screen stays until its replacement arrives, and only a change
    // of account throws it away, because then it is another account's numbers.
    setCapErr(null);
    // Guarded, because this is the slowest read on the page (171 locations)
    // and the account can be changed while it is in flight: without it the
    // slower answer wins and the numbers on screen belong to whichever account
    // was asked for first, under the name of the one now selected.
    let live = true;
    api.capacity(accountId)
      .then((c) => { if (live) setCap(c); })
      .catch((e: Error) => { if (live) setCapErr(e.message); });
    return () => { live = false; };
  }, [view, accountId]);
  useEffect(() => { setCap(null); }, [accountId]);
  const [planInputs, setPlanInputs] = useState<PlanInputs>(EMPTY_PLAN_INPUTS);
  const [dlErr, setDlErr] = useState<string | null>(null);
  // What the preview's bundle currently does for a credential, straight from
  // core: the preview never rotates, so its answer is a free look at what a
  // download would carry. Read rather than re-derived here -- the rule has four
  // branches and one of them revokes a running agent's token.
  const [previewToken, setPreviewToken] = useState<TokenReport | null>(null);
  // Whether the next download/save should issue a new credential. Off, always,
  // until asked: it is the one action here that breaks a deployment that is
  // currently working, and it used to be what the download button did by itself.
  const [rotate, setRotate] = useState(false);
  // What the last download or save actually did, in core's own words. Said
  // afterwards as well as before, because a rotation is worth confirming: the
  // bundle in the browser's downloads folder is now the only copy of that token.
  const [lastTokenReport, setLastTokenReport] = useState<TokenReport | null>(null);
  // Saving to a folder, beside downloading: the typed directory, and where the
  // last save actually landed (the server echoes the expanded path, which is
  // what a kubectl command can be copied against -- `~` is not).
  const [saveDir, setSaveDir] = useState("");
  const [saved, setSaved] = useState<SavedBundle | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  // The imported cluster evidence, its verdicts, and whatever the last import
  // was refused for. The document itself is kept, not just its verdicts: the
  // preflight re-runs against it on every option change, because verdicts that
  // described an older configuration would be worse than none.
  const [preflight, setPreflight] = useState<PreflightState>(NO_PREFLIGHT);
  const [preflightBusy, setPreflightBusy] = useState(false);
  // What the panel has written into the options this session, and what each of
  // those options held first. Kept beside the options rather than in them: an
  // applied value has to be indistinguishable from a typed one downstream, so
  // the only place the history can live is here -- which is also what makes
  // undo possible without the previous value being re-entered.
  const [applied, setApplied] = useState<Applied>(NOTHING_APPLIED);

  useEffect(() => {
    api.keyDetect().then((r) => {
      // Only the path: the list itself had no reader once the connect form
      // became a modal that takes one file.
      if (r.candidates[0]) setKeyPath(r.candidates[0].path);
    }).catch(() => {});
    api.optionDefaults().then((d) => {
      setDefaults(d);
      setOptions((o) => ({ ...d, ...o }));
    }).catch(() => {});
    api.svConstants().then(setSvConst).catch(() => {});
    api.funcIdChoices().then(setFuncIdChoices).catch(() => {});
    api.features().then(setFeatures).catch(() => {});

    // The key lives in the server process, so a refresh never disconnected
    // anything -- the page just forgot. Ask, and put back what it was pointed
    // at. Selections are restored before the connection resolves so the
    // location list is filtered to the right workspace as it arrives; the
    // agent is the exception (see pendingShip).
    const saved = session.load();
    if (saved) {
      setSourceMode(saved.sourceMode);
      setManual(saved.manual);
      setOptions((o) => ({ ...o, ...saved.options }));
      setStep(saved.step);
      setView(saved.view);
      setPlanInputs(saved.plan);
      pendingShip.current = saved.shipId;
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
    }).catch(() => {})
      // Only now may the page write its own state back. Saving before this
      // resolves overwrites the snapshot with the empty state it is about to
      // restore *from* -- which is what happened the first time: one refresh
      // against a server that did not answer, and the selections were gone for
      // good.
      .finally(() => setRestored(true));
  }, []);
  const [restored, setRestored] = useState(false);

  // What a restored session is still waiting to re-select. Each is consumed by
  // the effect that loads the list it belongs to, because a selection is only
  // legitimate once the account has confirmed the thing still exists -- a
  // location deleted since the last page load must not come back as an id the
  // rest of the page believes.
  const pendingWorkspace = useRef<number | null>(null);
  const pendingHarbor = useRef<string | null>(null);
  const pendingShip = useRef<string | null>(null);

  // Remember what a refresh would otherwise lose. Never the AUTH_TOKEN -- see
  // session.strip, which is where that decision lives and is tested.
  useEffect(() => {
    if (!restored) return;
    session.save({ sourceMode, accountId, workspaceId, harborId, shipId,
                   manual, options, step, view, plan: planInputs });
  }, [restored, sourceMode, accountId, workspaceId, harborId, shipId, manual,
      options, step, view, planInputs]);

  /** Hand the key back. The server forgets the client; the page forgets
   *  everything that was read with it, because a stale account tree is worse
   *  than an empty one -- it offers locations this page can no longer reach. */
  const disconnect = async () => {
    try { await api.keyClear(); } catch { /* forgetting locally still helps */ }
    setWho(null); setAccounts([]); setAccountId(null);
    setWorkspaces([]); setWorkspaceId(null);
    setLocations([]); setHarborId(null); setShipId(null); setFacts(null);
    setStatus(null); setPolling(false); setConnErr(null);
    forgetToken();
    session.clear();
    // Land somewhere that still works. Two of the three views need a key --
    // Account capacity has nothing to roll up, and Generate's "Connect to
    // BlazeMeter" source has no account to read a location from -- so leaving
    // the page where it was would leave it on a disabled control. Plan capacity
    // needs nothing at all, which is what makes it the place to land.
    setView("plan");
    setSourceMode("manual");
    setCap(null);
  };

  const connect = async (body: Parameters<typeof api.keySet>[0]) => {
    if (connecting) return;
    setConnErr(null);
    setConnecting(true);
    try {
      const r = await api.keySet(body);
      setWho({ email: r.user.email, keyId: r.key_id });
      // Connecting is the answer to "where do the three values come from", so
      // it settles that question too: picking the account is now the way on,
      // and leaving the page in manual entry would ask for ids by hand from
      // someone who has just handed over the account they are in.
      //
      // Here rather than in an effect on `who`, deliberately: this is the
      // deliberate act. A session restored with manual entry saved keeps it,
      // because reloading a page is not choosing anything.
      switchMode("connect");
      // Still connecting as far as the user is concerned: the key is accepted
      // but the account list is what the next step needs, and releasing the
      // button between the two would show a ready form with nothing in it.
      setAccountsBusy(true);
      const accts = await api.accounts().finally(() => setAccountsBusy(false));
      setAccounts(accts);
      setAccountId(r.default_account_id ?? accts[0]?.id ?? null);
    } catch (e) { setConnErr(String((e as Error).message)); }
    finally { setConnecting(false); }
  };

  useEffect(() => {
    // Cleared first, then the guard -- the shape the workspace effect below
    // already has. The other way round, clearing the account returned early and
    // left its workspaces on screen, so the page offered a workspace list
    // belonging to an account nothing was pointing at any more.
    setWorkspaces([]); setWorkspaceId(null);
    if (!accountId || !who) return;
    setWorkspacesBusy(true);
    api.workspaces(accountId).then((ws) => {
      setWorkspaces(ws);
      const want = pendingWorkspace.current;
      pendingWorkspace.current = null;
      setWorkspaceId(ws.find((w) => w.id === want)?.id ?? ws[0]?.id ?? null);
    }).catch((e) => setLocErr(e.message))
      .finally(() => setWorkspacesBusy(false));
  }, [accountId, who]);

  useEffect(() => {
    setNewLoc((n) => ({ ...n, workspace_id: workspaceId ?? 0 }));
    setLocations([]); setHarborId(null); setLocErr(null);
    if (workspaceId == null) return;
    // An empty workspace and an unfetched one look identical, so the list says
    // which it is rather than showing nothing and meaning two things.
    setLocBusy(true);
    api.locations(workspaceId).then((ls) => {
      setLocations(ls);
      const want = pendingHarbor.current;
      pendingHarbor.current = null;
      // Only if it is still there. An id restored blind would leave the page
      // configured for a location the account no longer has.
      if (want && ls.some((l) => l.id === want)) setHarborId(want);
    }).catch((e) => setLocErr(e.message))
      .finally(() => setLocBusy(false));
  }, [workspaceId]);

  // What is currently being fetched. Two flags rather than one: they are two
  // requests, and a location list that has arrived while its facts are still
  // coming is a real state to show.
  const [locBusy, setLocBusy] = useState(false);
  const [factsBusy, setFactsBusy] = useState(false);

  const location = useMemo(
    () => locations.find((l) => l.id === harborId) ?? null, [locations, harborId]);
  const ships: Ship[] = location?.ships ?? [];

  useEffect(() => {
    setShipId(null); setFacts(null); setStatus(null); setShowCreateShip(false);
    // A token belongs to one agent. Carried into another location's bundle it
    // applies cleanly and leaves that agent at 0/1 with a credential that was
    // never its own -- so changing location empties the field, and so does
    // picking a different agent below.
    forgetToken();
    if (!harborId) return;
    setFactsBusy(true);
    api.facts(harborId).then(setFacts).catch((e) => setShipErr(e.message))
      .finally(() => setFactsBusy(false));
  }, [harborId]);

  const shipOnline = (s: Ship) =>
    !!s.lastHeartBeat && Date.now() / 1000 - s.lastHeartBeat < 300;

  useEffect(() => {
    // A restored agent outranks the auto-pick: it is what the user chose, and
    // it is applied only once the location's own list has confirmed it exists.
    const want = pendingShip.current;
    if (want && ships.some((s) => s.id === want)) {
      pendingShip.current = null;
      setShipId(want);
      return;
    }
    // Auto-pick a lone agent only if it isn't running somewhere already --
    // a new deployment should get a NEW agent identity, not clone a live one.
    if (ships.length === 1 && !shipOnline(ships[0])) setShipId(ships[0].id);
  }, [harborId, ships.length]);

  // Which half of the agent section is on screen -- picking an identity and
  // minting one
  // are one-of, because reusing an identity that is already running conflicts
  // with that install while creating one is free. Derived, not a second piece
  // of state: a location with no agents has nothing to pick, so it opens on the
  // create form, and creating the first agent drops back to the list showing
  // it. The same derivation is why Cancel appears only when there is a list to
  // go back to.
  const creatingShip = showCreateShip || ships.length === 0;

  /** Issue a NEW AUTH_TOKEN for the selected agent, and put it in the field.
   *
   *  The rotate flag goes off as it lands: core's rule is that a token in the
   *  form wins over a rotation, so leaving it on would have the download step
   *  promise an issue that will not happen. */
  const regenerateToken = async () => {
    if (!harborId || !shipId) return;
    const r = await api.issueToken(harborId, shipId);
    setOptions((o) => ({ ...o, auth_token: r.auth_token }));
    setRotate(false);
  };

  // Creating the agent identity. A named function rather than the button's own
  // handler because the panel renders its own button and this is a real write
  // to the account -- one copy of it, or the two drift.
  const createShipNow = async () => {
    try {
      const r = await api.createShip(harborId!, newShipName);
      // Together: the write just dropped the server's cache, so both are cold
      // and neither depends on the other. In series this was the slower of the
      // two added to the other one, on the click that already waited for a
      // create.
      const [ls] = await Promise.all([
        api.locations(workspaceId!),
        api.facts(harborId!).then(setFacts).catch(() => {}),
      ]);
      setLocations(ls); setShipId(r.ship.id); setNewShipName("");
      setShowCreateShip(false);
      // The whole point of #64: the credential is captured at the one moment
      // it is free -- a ship created a second ago has no previous token for
      // the issue to invalidate -- so every download from here on carries it
      // without asking BlazeMeter for another. Nothing stores it, so the field
      // below is the copy to keep.
      setOptions((o) => ({ ...o, auth_token: r.auth_token }));
      setShipTokenNotice(r.token_error);
    } catch (e) { setShipErr(String((e as Error).message)); }
  };

  // debounced live preview
  const previewTimer = useRef<number>();
  useEffect(() => {
    // The token report goes with the files: it describes the bundle those came
    // from, and left behind it announces a placeholder in a bundle that no longer
    // exists -- which is exactly what switching source mode used to leave on
    // screen.
    if (!facts) { setFiles([]); setPreviewToken(null); return; }
    window.clearTimeout(previewTimer.current);
    previewTimer.current = window.setTimeout(async () => {
      try {
        const opts = { ...options, ship_id: shipId ?? undefined };
        // The typed save folder goes with it: if it already holds this ship's
        // bundle, the save will keep that token, and the preview should say so
        // rather than announcing a placeholder over a bundle that has one.
        const r = await api.generate(facts, opts, saveDir.trim() || undefined);
        setFiles(r.files);
        setPreviewToken(r.token);
        setGenErr(null);
        // Keep the open file open across the re-render every option edit
        // causes; fall back to the first manifest only once the one being read
        // stops being generated at all.
        setActiveFile((a) => (a && r.files.some((f) => f.name === a)
          ? a : r.files[0]?.name ?? null));
      } catch (e) { setGenErr(String((e as Error).message)); }
    }, 250);
    // saveDir is a dependency because it changes the answer: point the folder at
    // a bundle this ship already has and the token branch becomes `reused`. The
    // 250ms debounce above is what keeps that from being a request per keystroke.
  }, [facts, options, shipId, saveDir]);

  // agent status polling. An SV deployment also reads the namespace on the same
  // tick: the agent reports idle whether or not its virtual services ever
  // became reachable, so the heartbeat alone stays green through a deploy
  // stalled at WAITING_FOR_DOMAIN.
  //
  // The SV parameters travel by ref, not by dependency: they come from options,
  // and depending on them would tear down and restart the interval on every
  // keystroke in the namespace field.
  const svWatchRef = useRef({ on: false, ns: "", dom: "" });
  svWatchRef.current = { on: svConfigured(txt("sv_ingress")), ns: txt("namespace"),
                         dom: txt("sv_subdomain") };
  useEffect(() => {
    if (!polling || !harborId || !shipId) return;
    let live = true;
    const tick = () => {
      const { on, ns, dom } = svWatchRef.current;
      // Each request applies as it lands, rather than the pair being awaited
      // together: sv_read waits up to 15s on a cluster that never answers (it
      // has to -- kubectl retries an unreachable API server rather than
      // failing), which on a 10s poll would otherwise hold the heartbeat behind
      // a hung cluster. Failures keep the last good value, as before.
      api.status(harborId, shipId)
        .then((s) => { if (live) setStatus(s); }).catch(() => {});
      if (on && ns) {
        api.svMocks(ns, dom)
          .then((m) => { if (live) setSvMocks({ ns, read: m }); }).catch(() => {});
      }
    };
    tick();
    const t = window.setInterval(tick, 10000);
    return () => { live = false; window.clearInterval(t); };
  }, [polling, harborId, shipId]);

  const set = useCallback((k: string, v: unknown) =>
    setOptions((o) => ({ ...o, [k]: v })), []);

  /** Carry a plan into the generator, and move to step 1.
   *
   *  Writes four fields and creates nothing. Two are the new-location form's
   *  (slots, threadsPerEngine) -- which only matter if a location is made here,
   *  and are harmless if one is picked instead -- and two are bundle options
   *  the plan's node arithmetic assumed, so a bundle generated afterwards asks
   *  for the engines the request was sized for.
   *
   *  overrideCPU / overrideMemory are deliberately *not* written: they are
   *  fields of the BlazeMeter location and nothing here writes to an account
   *  without saying what it costs first. The panel names them and the request
   *  document explains them; setting them is the operator's, in BlazeMeter. */
  const applyPlan = useCallback((h: PlanHandover) => {
    setNewLoc((l) => ({ ...l, slots: h.slots,
                        threads_per_engine: h.threadsPerEngine }));
    setOptions((o) => ({ ...o, engine_cpu_limit: h.engineCpuLimit,
                         engine_mem_limit: h.engineMemLimit,
                         engines_per_node: h.enginesPerNode }));
    setView("flow");
    setStep(0);
  }, []);

  // Settled means: an agent is chosen and its facts are in. Collapsing then
  // keeps three steps of pickers from sitting above the configuration for the
  // rest of the session; "Change" reopens it.
  //
  // ...and it no longer collapses. The pickers are a step of their own with
  // nothing stacked below them, so folding them away buys nothing and costs the
  // thing you came back for: picking a location auto-selects its lone offline
  // agent, which used to swap the panel for a summary and take the agent list
  // with it. `sourceOpen` stays because the summary is still the right thing to
  // show in the one case that sets it -- see switchMode.

  // Manual mode declares the location's funcIds through the feature buttons, so
  // it needs to know which funcIds a feature stands for. Only the ones that
  // change the images are offered -- the rest generate the same bundle.
  const imageFuncs = useMemo(
    () => new Set(funcIdChoices.filter((c) => c.changes_images).map((c) => c.id)),
    [funcIdChoices]);
  /** The funcId a feature declares when it is the manual-mode declaration: the
   *  first of the ones it claims that changes the images. */
  const primaryFuncOf = useCallback(
    (id: string | null) => (features.find((f) => f.id === id)?.func_ids ?? [])
      .find((x) => imageFuncs.has(x)),
    [features, imageFuncs]);
  // What manual mode declares the location runs: the selected feature's primary
  // funcId. No literal funcId in TypeScript -- it comes from the served
  // vocabulary via primaryFuncOf.
  const manualFuncIds = useMemo(() => {
    const primary = primaryFuncOf(feature);
    return primary ? [primary] : [];
  }, [primaryFuncOf, feature]);

  // Manual facts are rebuilt from the typed values rather than held separately,
  // so there is one `facts` for the rest of the page whichever mode is on.
  // Debounced for the same reason the preview is: this runs on every keystroke.
  const manualTimer = useRef<number>();
  useEffect(() => {
    if (sourceMode !== "manual") return;
    // Nothing is built from a value that is not the shape an id comes in. The
    // fields say what is wrong; what this stops is the rest of the page --
    // preview, preflight, download -- describing a bundle assembled around a
    // truncated paste, which is a bundle that applies cleanly and then joins
    // nothing. `done` below follows from `facts`, so this is also what keeps
    // step 1 from being leavable.
    if (!manualComplete(manual.harbor_id, manual.ship_id,
                        String(options.auth_token ?? ""))) {
      setFacts(null); setShipId(null); return;
    }
    window.clearTimeout(manualTimer.current);
    manualTimer.current = window.setTimeout(() => {
      api.manualFacts({
        harbor_id: manual.harbor_id.trim(),
        ship_id: manual.ship_id.trim(),
        func_ids: manualFuncIds,
      }).then((r: ManualFactsOut) => {
        setFacts(r.facts);
        setShipId(r.facts.ships[0].id);
      }).catch((e) => setGenErr(String(e.message)));
    }, 250);
  }, [sourceMode, manual, manualFuncIds, options.auth_token]);

  // Switching modes drops what the other one established. Leaving a connected
  // location's facts in place while manual fields are on screen is how the
  // preview ends up describing an agent nobody is looking at.
  const switchMode = (m: string) => {
    const mode = m as "connect" | "manual";
    if (mode === sourceMode) return;
    setSourceMode(mode);
    setFacts(null); setShipId(null); setStatus(null); setGenErr(null);
    // Both modes now hold a typed-or-captured token, and it belongs to the agent
    // the other mode was about -- so it must not survive the switch either way.
    forgetToken();
  };

  const exportProfile = () => {
    const drop = new Set(["auth_token", "ship_id"]);
    const clean = Object.fromEntries(Object.entries(options)
      .filter(([k, v]) => !drop.has(k) && v !== (defaults as Record<string, unknown>)[k]));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(clean, null, 2)],
      { type: "application/json" }));
    a.download = "bzm-opl-profile.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importProfile = (file: File) => {
    file.text().then((t) => setOptions({ ...defaults, ...JSON.parse(t) }))
      .catch(() => setGenErr("could not parse profile JSON"));
  };

  const openshift = options.platform === "openshift";

  const proxyOpt = (options.proxy ?? {}) as Record<string, string | undefined>;
  const setProxy = (k: string, v: string) => {
    const p = { ...proxyOpt, [k]: v || undefined };
    set("proxy", Object.values(p).some(Boolean) ? p : null);
  };

  // CA trust is one-of: existing ConfigMap | inline PEM | OpenShift injection.
  // Derived from the options rather than stored, so the radios and the group's
  // own switch (which is caModePatch at "existing"/"none") cannot disagree.
  const caMode: CaMode = caModeOf(options);
  const setCaMode = (m: CaMode) =>
    setOptions((o) => ({ ...o, ...caModePatch(o, m) }));

  // Toggle-to-enable option groups: OFF hides the fields AND wipes their
  // options, so nothing hidden ever reaches the manifests. Auto-flips on when
  // a preset/import brings values in.
  const [grpOn, setGrpOn] = useState<GroupFlags>(allGroupsOff);
  // An SV location cannot be generated without this group: the manifests would
  // apply cleanly and then stall at WAITING_FOR_DOMAIN, so the backend refuses.
  // Surface it as required rather than letting the user find out later. The
  // funcIds come from generate.SV_FUNC_IDS over /api/sv-constants rather than a
  // copy here, so adding one cannot leave the UI silently disagreeing.
  const svLocation = !!facts?.func_ids?.some(
    (f) => svConst.func_ids.includes(f));
  // ...and answered: a location can carry mockServices and be wanted for
  // performance alone, which is a decision the options can hold (SV_NONE) and
  // generate() accepts. Required is therefore the demand *not yet answered* --
  // the state that blocks -- and declined is the same demand answered no.
  const svDeclined = options.sv_ingress === SV_NONE;
  const svRequired = svLocation && !svDeclined;
  // What a group cannot read off the options: SV is required by the location,
  // not by anything configured. Keyed by group id so the walk below never has
  // to test for one by name.
  const grpRequired: Partial<GroupFlags> = { sv: svRequired };
  const grpDeclined: Partial<GroupFlags> = { sv: svLocation && svDeclined };
  // Sticky: this only ever opens groups, so a group the user opened by hand
  // stays open with nothing set in it. svRequired is the dependency; the record
  // above is derived from it, which is why it is not one.
  useEffect(() => {
    setGrpOn((g) => detectGroups(options, g, grpRequired));
  }, [options, svRequired]);
  // Keep the SV options self-consistent however they arrived -- an imported
  // profile sets them without ever calling flipGroup, and opening the panel via
  // svRequired goes through setGrpOn, so neither path would otherwise seed
  // sv_ingress (leaving the select showing "NGINX" off the ?? fallback while
  // state stayed null).
  //
  // service_type is deliberately not touched here. This effect used to rewrite
  // a NODEPORT to CLUSTERIP whenever an ingress was configured; #60 showed the
  // pairing works, so an imported profile keeps the value it arrived with.
  //
  // Same reason a stale sv_istio_gateway is dropped here rather than only in the
  // select's onChange: only crane's istio backend reads it, so generate() now
  // refuses outright, and an imported profile pairing it with another ingress
  // would hit that error with nothing in the UI to explain it.
  // The openshift backend publishes a route.openshift.io Route, so switching the
  // platform away from OpenShift strands sv_ingress on a value generate() now
  // refuses -- and the option itself disappears from the select, leaving nothing
  // on screen to explain the error. Fall back to nginx, which works anywhere.
  useEffect(() => {
    setOptions((o) => {
      const stranded = o.sv_ingress === "openshift" && o.platform !== "openshift";
      const toNginx = stranded || (svRequired && !o.sv_ingress);
      const ingress = toNginx ? "nginx" : o.sv_ingress;
      const clearGateway = !!ingress && ingress !== "istio" && !!o.sv_istio_gateway;
      if (!toNginx && !clearGateway) return o;
      return {
        ...o,
        ...(toNginx ? { sv_ingress: "nginx" } : {}),
        ...(clearGateway ? { sv_istio_gateway: null } : {}),
      };
    });
  }, [svRequired, options.sv_ingress, options.sv_istio_gateway, options.platform]);
  const flipGroup = (id: GroupId, on: boolean) => {
    setGrpOn((g) => ({ ...g, [id]: on }));
    const group = GROUP_BY_ID[id];
    setOptions((o) => {
      // `required` reaches disable so a group the location demands can record
      // being switched off rather than merely emptied -- see the SV group.
      const patch = on ? group.enable(o) : group.disable(o, !!grpRequired[id]);
      // A group that seeds nothing must hand back the same object: a fresh
      // identity would re-run the preview effect and re-POST /api/generate for
      // options that did not change.
      return Object.keys(patch).length ? { ...o, ...patch } : o;
    });
  };
  // Moving the view. The only option it may write is the namespace, and only
  // while that still holds one a feature suggested -- everything else stays
  // exactly as it is, because narrowing a view must not change what the bundle
  // generates. No group is flipped on or off here for the same reason.
  //
  // A function rather than an effect on `feature`: an effect would also fire
  // when the vocabulary lands mid-session and rewrite a namespace already typed.
  // `suggestNs` is opt-in and only the location effect passes it. Switching the
  // view by hand must not touch the namespace: the namespace is generated into
  // every manifest, so suggesting on a manual switch would make looking at a
  // feature change the bundle -- the one thing a view is not allowed to do. It
  // also flip-flopped blazemeter <-> blazemeter-sv on a location that has both.

  const pickFeature = useCallback((id: string, suggestNs = false) => {
    setFeature(id);
    // In manual mode the feature buttons are the declaration rather than a
    // view -- but nothing is written here: manualFuncIds derives it from
    // `feature`, so selecting one is the whole action.
    const f = features.find((x) => x.id === id);
    if (!f || !suggestNs) return;
    setOptions((o) => {
      const ns = suggestNamespace(String(o.namespace ?? ""), f, features);
      // Same object when there is nothing to suggest: a fresh identity re-POSTs
      // /api/generate for options that did not change.
      return ns == null ? o : { ...o, namespace: ns };
    });
  }, [features]);

  // Which feature a location opens on, from its funcIds. Keyed on the harbor
  // rather than on `facts`, which is refetched after creating an agent: that
  // must not yank the view back from wherever the user moved it. `feature` is
  // read but deliberately not a dependency -- depending on it would re-force
  // the starting feature every time the user chose a different one.
  useEffect(() => {
    if (!features.length) return;
    // facts is cleared while the next location's are fetched. Falling back to
    // the default in that gap would flip the view (and the suggested namespace)
    // to performance and back for every SV location picked.
    if (!facts) { if (!feature) pickFeature(features[0].id, true); return; }
    const start = startFeature(facts.func_ids, features);
    if (start) pickFeature(start, true);
  }, [facts?.harbor_id, features, pickFeature]);

  const namespaceOk = !!txt("namespace");
  // Empty is refused by generate(), so this blocks the download rather than
  // only colouring the field -- an unnamed account is the one state of these
  // two that produces no bundle at all.
  const saOk = serviceAccountOk(options);
  const saCreate = options.service_account_create !== false;
  // What the bundle is: flat YAML to kubectl apply, or the chart with a values
  // overlay. Both render the same objects -- the choice is how you install and
  // upgrade -- except that the chart is performance-only, so an SV location is
  // held to manifests and the segment says why instead of disappearing.
  // What the download and save buttons will do about the credential: the hint
  // beside them, the banner over them, and whether the request rotates at all.
  // One derivation because those three answer one question, and three of them
  // could disagree -- see token.ts.
  const tokenPlan = downloadPlan(previewToken, rotate, shipId);
  const format = String(options.output_format ?? "manifests");
  const helmBlocked = svRequired
    ? "Not for this location — service virtualization needs an ingress, its RBAC "
      + "and a TLS secret, which this chart does not carry."
    : undefined;
  // A location can turn out to be an SV one after the format was picked, and an
  // imported profile can arrive already set to helm. Fall back rather than
  // leaving a disabled segment selected and every generate call failing.
  useEffect(() => {
    if (svRequired && options.output_format === "helm") set("output_format", "manifests");
  }, [svRequired, options.output_format, set]);

  // Read off the group's own rule rather than restated here. The two were
  // separate copies of _sv_cfg's requirements and had to be edited in lockstep
  // -- #60 relaxed the rule and had to touch both, which is the argument.
  const svOk = !GROUP_BY_ID.sv.incomplete!(options, svRequired, svConst.backends);
  // What the prerequisite list and the endpoint host are rendered against. The
  // list shows from the moment the group is on, so a field still empty renders
  // as its own placeholder rather than a gap; anything filled in is substituted
  // for real, which is the point -- the host below is meant to be pasted into a
  // browser after the first virtual service deploys.
  const svCtx: SvCtx = {
    ns: txt("namespace") || "<namespace>",
    dom: txt("sv_subdomain") || "<domain>",
    secret: txt("sv_tls_secret") || "<tls-secret>",
    gateway: txt("sv_istio_gateway"),
  };
  // Served, not restated here: what the Role grants is generate.py's to state,
  // and the two can disagree only if one of them is a copy.
  const svRbac = svConst.backends[txt("sv_ingress")];

  // -- what this location runs -----------------------------------------------
  // The features it carries, and the funcIds it carries that the tool has no
  // options for. Locations already run tdm/dataPublisher/delphix; naming them is
  // the honest version of a page that quietly models five funcIds.
  const locFeatures = featuresOf(facts?.func_ids, features);
  const locUnclaimed = unclaimedFuncIds(facts?.func_ids, features);
  // Features this location does not carry. Not "unavailable" any more: the card
  // offers to turn one on, which is a real PATCH of the location's funcIds. The
  // list is empty whenever the question has not been answered -- manual entry
  // declares rather than reads, and before a location is chosen an empty
  // locFeatures means "not asked yet", not "none".
  const notEnabled = sourceMode === "connect" && !!facts && locFeatures.length
    ? features.map((f) => f.id).filter((id) => !locFeatures.includes(id))
    : [];
  /** Turn a feature on for the selected location, then re-read the facts so the
   *  card's state comes from the account rather than from local memory. */
  const enableFeature = useCallback(async (id: string) => {
    const funcId = features.find((f) => f.id === id)?.func_ids[0];
    if (!harborId || !funcId) return;
    await api.addFuncId(harborId, funcId);
    const [ls, fresh] = await Promise.all([
      workspaceId != null ? api.locations(workspaceId) : Promise.resolve(null),
      api.facts(harborId),
    ]);
    if (ls) setLocations(ls);
    setFacts(fresh);
  }, [features, harborId, workspaceId]);
  // Which groups are in use but not finished. Each group declares its own rule,
  // so a feature gaining required options later needs nothing here.
  const incomplete = incompleteGroups(options, { sv: svRequired }, svConst.backends);

  // -- is the published endpoint answering? ----------------------------------
  // A Running mock pod says nothing about whether anything routes to it: where
  // the controller rejects crane's Ingress the endpoint 503s while the pod is
  // healthy. The scheme follows the TLS secret, because that is what decides
  // whether the published endpoint terminates TLS -- probing the other one
  // answers a question nobody asked.
  const svScheme = txt("sv_tls_secret") ? "https" as const : "http" as const;
  // Nothing renders off the promise: the row goes busy, the status poll behind
  // it keeps running, and the server bounds its own wait well inside one poll
  // interval, so a hanging endpoint holds up nothing but its own row.
  const checkEndpoint = async (host: string) => {
    setSvChecks((c) => ({ ...c, [host]: { busy: true } }));
    try {
      const res = await api.svCheck(host, svScheme);
      setSvChecks((c) => ({ ...c, [host]: { busy: false, res } }));
    } catch (e) {
      setSvChecks((c) => ({ ...c, [host]: { busy: false, err: String((e as Error).message) } }));
    }
  };
  // How the result reads. A 503 is amber, not red: the check worked and this is
  // its answer -- the one `bzm-opl-gen sv-expose` exists to fix, which the
  // message names. Anything else that answered routed, so only a probe that got
  // no status line is red.
  const svCheckTone = (r: SvCheckOut) =>
    r.status !== "ok" ? "text-red-600"
      : r.code != null && r.code < 400 ? "text-emerald-700" : "text-amber-700";

  // -- preflight against an imported cluster read ----------------------------
  // The cluster-side twin of manual facts entry: someone with access to the
  // customer's cluster runs the collector script, and this judges the file it
  // produced. No API key, no kubecontext, nothing reachable from here.

  /** Import: parse, then let the server say whether it is evidence at all --
   *  and only commit it once it has. A file that is not evidence must not
   *  displace the one whose verdicts are on screen, which it would if the doc
   *  were stored first and left for the re-run effect to fail on. */
  const importEvidence = async (f: File) => {
    const read = readEvidence(f.name, await f.text());
    if ("error" in read) { setPreflight((p) => refused(p, read.error)); return; }
    setPreflightBusy(true);
    try {
      const out = await api.preflight(facts, options, read.doc);
      setPreflight(imported(f.name, read.doc, out));
    } catch (e) {
      setPreflight((p) => refused(p, String((e as Error).message)));
    } finally { setPreflightBusy(false); }
  };

  // Re-judged whenever the configuration moves, so the verdicts on screen are
  // always about what is on screen -- engine sizing against the nodes, the
  // ingress class against the ones that exist, the namespace against the one
  // the file describes. Debounced like the preview: this runs on every
  // keystroke in the namespace field. Held to `facts` for the same reason the
  // picker is -- the checks measure the cluster against a location's slots and
  // engine size, and none of it means anything without them.
  const preflightTimer = useRef<number>();
  useEffect(() => {
    if (preflight.doc == null || !facts) return;
    window.clearTimeout(preflightTimer.current);
    preflightTimer.current = window.setTimeout(() => {
      api.preflight(facts, options, preflight.doc)
        .then((out) => setPreflight((p) => rechecked(p, out)))
        .catch((e) => setPreflight((p) => refused(p, String((e as Error).message))));
    }, 250);
  }, [preflight.doc, facts, options]);

  // Applying one of the suggestions the same file carries. Always a click, and
  // always one the row has already shown both values for: what would be written
  // and what is there now. Nothing is applied on import, and nothing suggestive
  // can be applied without a candidate being picked -- `offer` is what enforces
  // that, and it is tested as data in suggestions.test.ts.
  //
  // The write is the plain option, so the preview, the bundle and profile.json
  // cannot tell this from a value someone typed. The re-check effect above then
  // re-judges the evidence against the configuration it just changed, and the
  // group detection effect opens whichever group now holds something.
  //
  // What it writes and what it remembers for the undo are decided together, in
  // `apply` -- the value being replaced is the one the row displayed, and this
  // component is not a second place that gets to work it out.
  const applySuggestion = (s: Suggestion, value: unknown) => {
    const next = apply(applied, s, value);
    setApplied(next.applied);
    setOptions((o) => ({ ...o, ...next.patch }));
  };

  const undoSuggestion = (option: string) => {
    const back = undo(applied, option);
    if (!back) return;                  // never written from here; not ours
    setApplied(back.applied);
    setOptions((o) => ({ ...o, ...back.patch }));
  };

  // What the imported file says about itself -- collected when, for which
  // namespace, and what its collector could not read. doctor's, off the same
  // document; the header states all three rather than leaving them in the
  // leading verdict's prose.
  const evidence = preflight.out ? evidenceHeader(preflight.out) : null;

  // Each group's body, wired with the props that group actually needs -- no
  // shared bag of options handed round, so a group reads on its own and what it
  // may write is what its declaration says it owns.
  const groupBody: Record<GroupId, ReactNode> = {
    registry: (
      <RegistryGroup
        registry={raw("private_registry")}
        pullSecret={raw("pull_secret")}
        registryAuth={Boolean(options.registry_auth)}
        onRegistry={(v) => set("private_registry", v)}
        onPullSecret={(v) => set("pull_secret", v)}
        onRegistryAuth={(v) => set("registry_auth", v)} />
    ),
    proxy: <ProxyGroup proxy={proxyOpt} onField={setProxy} />,
    ca: (
      <CaGroup mode={caMode} onMode={setCaMode}
        configmap={raw("ca_existing_configmap")}
        configmapKey={raw("ca_configmap_key")}
        bundle={raw("ca_bundle")}
        onConfigmap={(v) => set("ca_existing_configmap", v)}
        onConfigmapKey={(v) => set("ca_configmap_key", v)}
        onBundle={(v) => set("ca_bundle", v)} />
    ),
    sched: (
      <SchedGroup
        tolerations={options.tolerations} nodeSelector={options.node_selector}
        onTolerations={(v) => set("tolerations", v)}
        onNodeSelector={(v) => set("node_selector", v)} />
    ),
    sizing: (
      <SizingGroup preset={enginePreset(options)}
        cpuLimit={raw("engine_cpu_limit")} memLimit={raw("engine_mem_limit")}
        onLimits={(cpu, mem) => setOptions((o) => ({
          ...o, engine_cpu_limit: cpu, engine_mem_limit: mem }))}
        onCpuLimit={(v) => set("engine_cpu_limit", v)}
        onMemLimit={(v) => set("engine_mem_limit", v)}
/>
    ),
    security: (
      <SecurityGroup useSecret={Boolean(options.use_secret)}
        clusterRbac={Boolean(options.cluster_rbac)}
        // Absent means the backend default, which is on -- so `!== false`
        // rather than Boolean(), which would show an untouched bundle as
        // unrestricted and invite someone to "fix" it by ticking a box that
        // then writes a key that was never there.
        restrictEngines={options.restrict_engines !== false}
        serviceType={String(options.service_type ?? "CLUSTERIP")}
        // Tri-state, so absent stays absent: `== null` rather than Boolean(),
        // which would resolve the default here and write it back as a choice.
        autoUpdate={options.auto_update == null ? null : Boolean(options.auto_update)}
        onUseSecret={(v) => set("use_secret", v)}
        onClusterRbac={(v) => set("cluster_rbac", v)}
        onRestrictEngines={(v) => set("restrict_engines", v)}
        onAutoUpdate={(v) => set("auto_update", v)}
        onServiceType={(v) => set("service_type", v)} />
    ),
    sv: (
      <SvGroup
        // Null, not "", while nothing is chosen: the select still shows its
        // nginx default, but no backend's prose is claimed until one is picked.
        ingress={options.sv_ingress == null ? null : String(options.sv_ingress)}
        ingressTypes={svConst.ingress_types} openshift={openshift}
        subdomain={raw("sv_subdomain")} tlsSecret={raw("sv_tls_secret")}
        gateway={raw("sv_istio_gateway")}
        onIngress={(v) => set("sv_ingress", v)}
        onSubdomain={(v) => set("sv_subdomain", v)}
        onTlsSecret={(v) => set("sv_tls_secret", v)}
        onGateway={(v) => set("sv_istio_gateway", v)}
        ok={svOk}
        // Computed, not deduced from the absence of other reasons: a later
        // completeness rule would otherwise inherit the nodePort sentence, and
        // the panel would show it before the backend table has even loaded.
        nodePortConflict={options.service_type != null
          && options.service_type !== "CLUSTERIP"
          && svConst.backends[txt("sv_ingress")]?.nodeport_ok === false}
        ctx={svCtx} rbac={svRbac} />
    ),
  };

  const filteredLocs = locations.filter((l) =>
    l.name.toLowerCase().includes(locFilter.toLowerCase()));

  /** Put a location that has just been changed back into the list.
   *
   *  In place rather than by re-fetching the workspace: the answer came from a
   *  re-read of that location, so it is newer than anything a list call would
   *  bring back, and re-fetching would also drop the ships the list is showing
   *  for every other row. The selection does not move -- changing a location's
   *  settings is not a reason to stop working on it. */
  const locationUpdated = useCallback((loc: Location) => {
    setLocations((ls) => ls.map((l) => (l.id === loc.id ? { ...l, ...loc } : l)));
  }, []);

  // What Create is waiting for, as the sentence it shows rather than as a
  // silently greyed button.
  const createLocBlockedBy = !newLoc.name.trim() ? "name the location first"
    : !newLoc.workspace_id ? "pick a workspace above first" : "";
  /** One segment of the summary line under the flow: a label nobody has to
   *  read twice, and a value that says "none yet" in amber where the absence is
   *  the thing worth knowing. */
  const pathSeg = (label: string, value: string | null, warn = false) => (
    <span className="flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className={"text-xs font-medium "
        + (value ? "text-slate-800" : warn ? "text-amber-700" : "text-slate-400")}>
        {value ?? (warn ? "none yet" : "—")}
      </span>
    </span>
  );

  const createLocationFormNode = (
    <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
      <p className="text-xs font-semibold text-slate-700">
        New private location
      </p>
      <Field required
        label={`Name (created in workspace: ${workspaces.find((w) => w.id === workspaceId)?.name ?? "?"})`}>
        <TextInput value={newLoc.name}
          onChange={(v) => setNewLoc({ ...newLoc, name: v })} /></Field>
      <div className="flex gap-4 items-end">
        <div className="flex gap-3 flex-wrap">
          {funcIdChoices.map((c) => (
            <Check key={c.id} label={c.label}
              checked={newLoc.func_ids.includes(c.id)}
              onChange={(on) => setNewLoc({
                ...newLoc,
                func_ids: on ? [...newLoc.func_ids, c.id]
                  : newLoc.func_ids.filter((x) => x !== c.id),
              })} />
          ))}
        </div>
        <Field label="Slots" hint="concurrent engines">
          <input type="number" min={1} className={inputCls + " w-20"}
            value={newLoc.slots}
            onChange={(e) => setNewLoc({ ...newLoc, slots: Number(e.target.value) })} />
        </Field>
        <Field label="Threads per engine" hint="required — tests can't start without it">
          <input type="number" min={1} className={inputCls + " w-24"}
            value={newLoc.threads_per_engine}
            onChange={(e) => setNewLoc({ ...newLoc, threads_per_engine: Number(e.target.value) })} />
        </Field>
      </div>
      {/* Create stays put and greys out, and says which of the two things it is
          waiting for -- a button that disables itself without a reason is the
          same dead end as one that disappears. */}
      <div className="flex gap-2 items-center">
        <Button disabled={!!createLocBlockedBy}
          onClick={async () => {
            try {
              const l = await api.createLocation({ ...newLoc, account_id: accountId! });
              const ls = await api.locations(workspaceId!);
              setLocations(ls); setHarborId(l.id); setShowCreateLoc(false);
            } catch (e) { setLocErr(String((e as Error).message)); }
          }}>Create</Button>
        <Button kind="ghost"
          onClick={() => { setLocErr(null); setShowCreateLoc(false); }}>
          Cancel
        </Button>
        {createLocBlockedBy && (
          <span className="text-[11px] text-amber-700">{createLocBlockedBy}</span>
        )}
      </div>
    </div>
  );

  const body = (
    <>
      {view === "capacity" ? (
        <main className="max-w-screen-xl mx-auto p-6">
          {!accountId && <p className="text-sm text-slate-500">Connect first.</p>}
          {capErr && <p className="text-sm text-red-600">{capErr}</p>}
          {!cap && accountId && !capErr && (
            <p className="text-sm text-slate-500">reading the account…</p>
          )}
          {cap && <CapacityView cap={cap} />}
        </main>
      ) : view === "plan" ? (
        // No WorkArea: there are no manifests to sit beside a plan, and an
        // empty preview pane next to it would suggest this step produces some.
        <main className="max-w-screen-lg mx-auto p-6">
          <PlanPanel inputs={planInputs} setInputs={setPlanInputs}
                     onUse={applyPlan} />
        </main>
      ) : (
      <main className="max-w-screen-xl mx-auto p-6">
        {/* `done` is what a step cannot say about itself -- whether it is
            finished enough to leave. The last step never is: there is nothing
            after the download to go on to. */}
        <StepFlow
          at={step} onGo={setStep}
          /* What all of step 1 adds up to, under the panel rather than inside
             it. It was a line between two of the three sections, where it read
             as a divider between them rather than as their result -- and it
             answers "which location and agent am I generating for?", which is a
             question you also have in steps 2 and 3. So it stays put as the
             steps change. */
          footer={sourceMode === "connect" ? (
            <div className="mt-3 pt-2.5 border-t border-slate-200 flex items-center gap-2 flex-wrap">
              {pathSeg("location", location?.name ?? null)}
              <span className="text-slate-300">›</span>
              {pathSeg("agent",
                       ships.find((x) => x.id === shipId)?.name ?? null,
                       !!location)}
              {!!location && ships.length === 0 && (
                <span className="text-[11px] text-amber-700 ml-1">
                  — this location is empty; the first agent has to be created
                </span>
              )}
            </div>
          ) : undefined}
          done={[
            !!facts && !!shipId,
            namespaceOk && saOk && incomplete.length === 0,
            false,
          ]}
          blockedBy={[
            "fill in the agent details to continue",
            "namespace, service account and any unfinished group first",
            "",
          ]}>
          {/* 1 · Where the harbor id, ship id and token come from.
              Three steps folded into one: connected they are picked from the
              account, manually they are typed. Both end at the same three
              values, so they belong in one place rather than three that only
              one mode ever uses. */}
          <Section n={1} title="Agent details" done={!!facts && !!shipId}
            hint="harbor_id, ship_id and AUTH_TOKEN — from your account, or typed.">
            <AgentPanel
              sourceMode={sourceMode} switchMode={switchMode} manual={manual}
              setManual={setManual}
              who={who}
              accountName={accounts.find((a) => a.id === accountId)?.name ?? null}
              workspaceName={workspaces.find((w) => w.id === workspaceId)?.name ?? null}
              locations={locations} filteredLocs={filteredLocs}
              locFilter={locFilter} setLocFilter={setLocFilter}
              harborId={harborId} setHarborId={setHarborId} location={location}
              locBusy={locBusy} locErr={locErr} showCreateLoc={showCreateLoc}
              setShowCreateLoc={setShowCreateLoc}
              onLocationUpdated={locationUpdated}
              createLocationForm={createLocationFormNode}
              ships={ships} shipId={shipId}
              pickShip={(id) => { setShipId(id); forgetToken(); }}
              shipOnline={shipOnline} factsBusy={factsBusy} facts={facts}
              creatingShip={creatingShip} setShowCreateShip={setShowCreateShip}
              newShipName={newShipName} setNewShipName={setNewShipName}
              createShip={createShipNow} shipErr={shipErr}
              shipTokenNotice={shipTokenNotice}
              authToken={raw("auth_token")}
              setAuthToken={(v) => set("auth_token", v || null)}
              regenerateToken={regenerateToken} />
          </Section>

          {/* 2 · Configure */}
          <Section n={2} title="Configure"
            hint="Everything re-renders the preview live.">
            <ConfigurePanel
              features={features} feature={feature} pickFeature={pickFeature}
              sourceMode={sourceMode} locFeatures={locFeatures}
              locUnclaimed={locUnclaimed} notEnabled={notEnabled}
              enableFeature={enableFeature}
              options={options} set={set}
              grpOn={grpOn} grpRequired={grpRequired} grpDeclined={grpDeclined}
              flipGroup={flipGroup} groupBody={groupBody} incomplete={incomplete}
              namespaceOk={namespaceOk} saOk={saOk} saCreate={saCreate}
              exportProfile={exportProfile} importProfile={importProfile} />
          </Section>

          {/* 3 · Download & verify */}
          <Section n={3} title="Download & verify">
            <DownloadPanel
              facts={facts} shipId={shipId} ships={ships} sourceMode={sourceMode}
              who={who} options={options} set={set} raw={raw} txt={txt}
              saOk={saOk} svOk={svOk} genErr={genErr}
              unfinished={incomplete} goToConfigure={() => setStep(1)}
              format={format}
              helmBlocked={helmBlocked}
              previewToken={previewToken} rotate={rotate} setRotate={setRotate}
              tokenPlan={tokenPlan} lastTokenReport={lastTokenReport}
              setLastTokenReport={setLastTokenReport}
              dlErr={dlErr} setDlErr={setDlErr}
              saveDir={saveDir} setSaveDir={setSaveDir} saved={saved}
              setSaved={setSaved} saveErr={saveErr} setSaveErr={setSaveErr}
              preflight={preflight} preflightBusy={preflightBusy}
              importEvidence={importEvidence} evidence={evidence}
              applied={applied} applySuggestion={applySuggestion}
              undoSuggestion={undoSuggestion}
              polling={polling} setPolling={setPolling} status={status}
              svMocks={svMocks} svChecks={svChecks} svScheme={svScheme}
              svCheckTone={svCheckTone} checkEndpoint={checkEndpoint} />
          </Section>
        </StepFlow>
      </main>
      )}
    </>
  );

  return (
    <div className="min-h-screen flex flex-col">
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

      {/* The shell: the drawer picks the view, the view fills what is left, and
          the preview slides over the top of it from the right. */}
      <div className="flex grow min-h-0">
        <NavDrawer view={view} setView={setView} connected={!!who}
          open={navOpen} setOpen={setNavOpen}
          /* Which key, which account it can see, which workspace inside it:
             one control, because they are one answer narrowed three times, and
             all three are the session's rather than any step's. */
          footer={
            <AccountMenu
              who={who} disconnect={disconnect}
              accounts={accounts} accountId={accountId}
              setAccountId={setAccountId} accountsBusy={accountsBusy}
              workspaces={workspaces} workspaceId={workspaceId}
              setWorkspaceId={setWorkspaceId} workspacesBusy={workspacesBusy}
              keyPath={keyPath} setKeyPath={setKeyPath}
              pasteId={pasteId} setPasteId={setPasteId}
              pasteSecret={pasteSecret} setPasteSecret={setPasteSecret}
              saveKey={saveKey} setSaveKey={setSaveKey}
              connect={connect} connErr={connErr} setConnErr={setConnErr}
              connecting={connecting} collapsed={!navOpen} />
          } />
        <div className="grow min-w-0 overflow-y-auto">{body}</div>
        {/* Only beside the view that produces manifests. On the two planning
            views there is nothing for it to show, and a rail promising a
            preview of nothing is a door to an empty room. */}
        {view === "flow" && (
          <PreviewDrawer files={files} activeFile={activeFile}
            setActiveFile={setActiveFile} genErr={genErr}
            open={previewOpen} setOpen={setPreviewOpen} />
        )}
      </div>
    </div>
  );
}
