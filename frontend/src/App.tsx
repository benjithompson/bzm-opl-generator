import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Api, Account, AgentEnvVar, AgentStatus, Capacity, Facts, Functionality,
  GeneratedFile, ManualFactsOut, TokenReport,
  FuncIdChoice, Location, Options, Ship, SvCheckOut,
  SvConstants, SvMocksOut, Workspace,
} from "./api";
// What the last download or save did, as one record with one owner -- see
// attempt.ts for why the four it replaced could not stay four.
import { Attempt, NO_ATTEMPT } from "./attempt";
// The only piece of furniture this file still renders itself: every form it
// used to hold is inside the step that owns it.
import { Section } from "./components";
// What a download is about to do to the agent's credential. The branch a bundle's
// token arrived by is core's and comes back on the answer; this decides what to
// say about the click that has not happened yet, which is the only moment a
// rotation can still be reconsidered (#64).
import { downloadPlan, Recall, recalled, recallNote } from "./token";
// The option groups of the Configure step: one declaration each (title, hint, the option
// keys it owns, the functionalities it belongs to, and its detect/enable/disable),
// plus a body per group. This file only wires them -- what a group *is*, and
// which of them a functionality puts on screen, lives in optionGroups.ts.
import {
  allGroupsOff, blockingGroups, caModeOf, caModePatch, CaMode,
  configureBlockedBy, detectGroups, enabledFunctionalities,
  functionalitiesOf, GROUP_BY_ID, GroupFlags, GroupId, incompleteGroups, isOpenshift,
  notRunPatch,
  runsFunctionality, serviceAccountOk, startFunctionality, suggestNamespace,
  unclaimedFuncIds,
} from "./optionGroups";
// Required fields nobody filled in: what they resolve to on the way out, and
// the one list the two panels warn from.
import { blankRequired, withPlaceholders } from "./placeholder";
// What the bundle is, and which options that leaves reaching something. The
// table of what a docker bundle drops is the generator's and is fetched, never
// restated here.
import { isDocker, optionApplies, whyIgnored as why } from "./formats";
// The engine size the bundle will carry, and where the figure came from
// (#132): generate derives it from the location's engine requests, so the
// configure step states it rather than editing it.
import { sizeStatement } from "./engineSize";
// Service virtualization, as one record rather than a dozen values derived in
// four places here. Whether the location demands it, whether that demand was
// declined, whether what is configured is finished, the prerequisite context,
// the RBAC prose, the scheme, the chart's refusal -- and the one patch the
// options need, which used to be two effects writing what a third read back.
import { svState } from "./sv";
// What survives a refresh, and the one thing that must not.
import * as session from "./session";
// Whether an agent is reporting. One statement of the rule, with its own tests
// -- it used to be a closure here, handed to step 1 as a predicate.
import { shipOnline } from "./heartbeat";
// The shape a hand-typed id and token come in, and what is wrong with one that
// does not. Nothing is built from a value that fails it.
import { manualComplete } from "./manualIds";
// What the account can generate, by workspace.
import { CapacityView } from "./CapacityView";
// The planner's form shape and its empty value: plain data, so the session
// snapshot and this page share one declaration of it. `PlanAsk` is what a
// profile asks for, assembled once here and read by two panels.
import { EMPTY_PLAN_INPUTS, PlanAsk, PlanInputs } from "./usePlan";
import { AgentPanel } from "./steps/AgentPanel";
// The sizing: step 1's first card, and the planner that used to be a view of
// its own. See Sizing for why it moved.
import { Sizing } from "./steps/Sizing";
import { ConfigurePanel } from "./steps/ConfigurePanel";
import { DownloadPanel } from "./steps/DownloadPanel";
import { CaGroup } from "./groups/CaGroup";
import { ProxyGroup } from "./groups/ProxyGroup";
import { RegistryGroup } from "./groups/RegistryGroup";
import { EnvVars } from "./groups/EnvVars";
import { SchedGroup } from "./groups/SchedGroup";
import { SecurityGroup } from "./groups/SecurityGroup";
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

  // Which location and which agent have been confirmed, **by id** rather than
  // as two booleans.
  //
  // A confirmation is about a selection, so changing the selection has to
  // withdraw it -- otherwise step 1 stays finished for a pairing nobody
  // checked, which is the whole thing the gate exists to stop. Stored as what
  // was confirmed, that follows from the comparison and no effect has to
  // remember to clear anything; two booleans would need one per list, and the
  // one that had to remember is the one that forgets.
  const [confirmed, setConfirmed] =
    useState<{ loc: string | null; ship: string | null }>(
      { loc: null, ship: null });

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
  // Identity only. What the location runs is derived from the selected functionality
  // (manualFuncIds below) rather than stored: it was state with two writers that
  // disagreed on the miss case, and it is a pure function of `functionality`.
  const [manual, setManual] = useState({ harbor_id: "", ship_id: "" });

  // -- options / preview -----------------------------------------------------
  const [defaults, setDefaults] = useState<Options>({});
  const [svConst, setSvConst] = useState<SvConstants>(
    { func_ids: [], ingress_types: [], backends: {} });
  // What a docker bundle drops, from the generator (see formats.ts). Empty
  // until it lands, and empty means every option applies -- the configure step
  // shows a field too many rather than hiding a required one on a guess.
  const [dockerIgnored, setDockerIgnored] = useState<Record<string, string>>({});
  // ...and which environment variables the bundle writes for itself, which the
  // env area refuses. Empty the same way, and meaning the same thing: nothing
  // is refused until the table lands, because generate() refuses
  // authoritatively either way and a name rejected on a guess is the worse
  // half of being wrong.
  const [reservedEnv, setReservedEnv] = useState<Record<string, string | null>>({});
  // ...and the other half of it: the documented variables that are left, which
  // the env area offers as a list. Empty again means "not read yet" -- the area
  // falls back to naming a variable by hand, which is a field too many rather
  // than an option nobody can reach.
  const [agentEnv, setAgentEnv] = useState<AgentEnvVar[]>([]);
  const [options, setOptions] = useState<Options>({ namespace: "blazemeter" });
  // The functionality being configured, and the vocabulary it is chosen from. A view
  // over the options, never a scope: one crane is deployed for the selected
  // location and that location's funcIds decide what the manifests contain, so
  // this only decides what is on screen. The list is served (/api/functionalities) so
  // that adding a functionality is a backend entry plus a tag on the groups it owns;
  // null until it lands, which hides nothing.
  const [functionalities, setFunctionalities] = useState<Functionality[]>([]);
  const [functionality, setFunctionality] = useState<string | null>(null);
  // One way to read a text option. Written out per-site, the `.trim()` was
  // getting forgotten -- an ingress name pasted with a trailing space missed
  // the SV_PREREQS lookup and the panel silently lost its prose.
  const txt = useCallback(
    (k: string) => String(options[k] ?? "").trim(), [options]);
  // The same read for a controlled input, where trimming would stop the user
  // typing a space -- so the two are separate rather than one with a flag.
  const raw = useCallback(
    (k: string) => String(options[k] ?? ""), [options]);
  // What this location runs, and the third state kept: manual entry declares,
  // a location read off the account carries funcIds, and null is nobody having
  // said yet. Up here because `sv` reads it -- which of the SV options are on
  // their way out is what decides whether they may block an output format, and
  // deriving that below the record that needs it was how the two got out of
  // step. `locUnclaimed` and `notRun` stay where they are used.
  const locFunctionalities = functionalitiesOf(facts?.func_ids, functionalities);
  const enabled = enabledFunctionalities(sourceMode, functionality, locFunctionalities);
  // Everything about service virtualization, answered once. Four blocks of this
  // file used to derive it -- what the location demands, whether that demand
  // was declined, whether what is set is finished, what the panels render
  // against -- and each read the options for itself, so one question had four
  // answers free to disagree. Declared up here because the status poll below
  // asks it too. See sv.ts; it is tested as plain data, with no page at all.
  //
  // The fourth input is whether this bundle still carries the functionality at all:
  // notRunPatch clears the SV options of a location known to run something
  // else, and options on their way out must not take an output format with
  // them. Unanswered reads as yes, which is the direction that over-blocks
  // rather than letting a bundle the server refuses through.
  const svRuns = runsFunctionality(enabled, "sv");
  const sv = useMemo(
    () => svState(facts?.func_ids, options, svConst, svRuns),
    [facts?.func_ids, options, svConst, svRuns]);
  // What the bundle is. Declared here rather than beside the two predicates it
  // feeds, because the SV correction below reads it to say which format it is
  // replacing -- see the effect that applies sv.patch.
  const format = String(options.output_format ?? "manifests");
  // ...and the last format the correction took away, or null. Not derived: once
  // the patch is applied the options no longer hold what was replaced, so a
  // derivation would state it for exactly the render it is already too late to
  // read. Cleared by picking a format, which is the answer to it.
  const [formatNotice, setFormatNotice] =
    useState<{ was: string; why: string } | null>(null);
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
    // The whole attempt, not only its token report: what the last download or
    // save did was done for the agent being left behind, and a folder named
    // under a different agent's bundle is the same claim about the wrong thing.
    setAttempt(NO_ATTEMPT);
  }, []);
  /** What this app still holds of a credential it minted for the chosen agent.
   *
   *  Four states rather than a token-or-not, and the pair that matters is
   *  `none` against `unread`: an agent nobody minted for and an agent nobody
   *  could be asked about are different answers, and only the first is entitled
   *  to the sentence saying a token cannot be read back. See token.Recall. */
  const [recall, setRecall] = useState<Recall>("asking");

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
  // Which of the two things this page is. The step flow deploys an agent; the
  // rollup is one read of a whole account and belongs to nothing in the flow.
  // The planner is no longer among them -- it is step 1's first card, because
  // reaching nothing is what makes it the first question rather than a separate
  // page (see Sizing).
  const [view, setView] = useState<ViewId>("flow");
  // The two drawers. The nav starts open because the views are the first thing
  // to understand; the preview starts shut because there is nothing in it until
  // an agent is chosen.
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
  // What the preview's bundle currently does for a credential, straight from
  // core: the preview never rotates, so its answer is a free look at what a
  // download would carry. Read rather than re-derived here -- the rule has four
  // branches and one of them revokes a running agent's token.
  const [previewToken, setPreviewToken] = useState<TokenReport | null>(null);
  // Whether the next download/save should issue a new credential. Off, always,
  // until asked: it is the one action here that breaks a deployment that is
  // currently working, and it used to be what the download button did by itself.
  // What the last download or save actually did -- the credential report in
  // core's own words, where a save landed, and why either was refused. One
  // piece of state because it is one fact: the four it replaced were reset in
  // pairs before every call, and whichever field was missed described the click
  // before last. The download step reports the next one; nothing else writes it
  // but forgetToken, which drops the lot when the agent changes.
  const [attempt, setAttempt] = useState<Attempt>(NO_ATTEMPT);
  // Where a save writes. Not part of the attempt: it is what was typed rather
  // than what happened, and the preview reads it too -- a folder already
  // holding this ship's bundle supplies the token the save would reuse.

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
    api.dockerIgnored().then(setDockerIgnored).catch(() => {});
    api.reservedEnv().then(setReservedEnv).catch(() => {});
    api.agentEnv().then(setAgentEnv).catch(() => {});
    api.funcIdChoices().then(setFuncIdChoices).catch(() => {});
    api.functionalities().then(setFunctionalities).catch(() => {});

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
      setConfirmed(saved.confirmed);
      pendingShip.current = saved.shipId;
      // Manual entry's declaration, and only manual entry's: connected, the
      // functionality is derived from the location's funcIds, and putting one back
      // would pin the page to a stale one (#118).
      //
      // Selected now rather than held until the vocabulary confirms it, unlike
      // the ids above -- because `functionality` cannot carry the wait. Null here does
      // not mean "not answered yet", it means "declared nothing", and
      // notRunPatch reads it on the very first render and clears every SV option
      // the snapshot has just restored. So it is applied, and the effect that
      // watches the vocabulary land is what drops it if it turns out not to be
      // offered any more.
      if (saved.sourceMode === "manual" && saved.declaredFunctionality) {
        setFunctionality(saved.declaredFunctionality);
        restoredFunctionality.current = saved.declaredFunctionality;
      }
      // The four account-side ids are not page state yet, and may never become
      // it -- see `held`, which is what carries them until something answers.
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
      // The account list has arrived, so it is what answers for the account id.
      release("accountId");
    }).catch(() => {})
      // Only now may the page write its own state back. Saving before this
      // resolves overwrites the snapshot with the empty state it is about to
      // restore *from* -- which is what happened the first time: one refresh
      // against a server that did not answer, and the selections were gone for
      // good.
      //
      // Resolving is not answering, though, and this fires on the rejection
      // too. What the page may write from here does not include the four ids
      // nothing has answered for: they are `held` below, which is where the
      // decision about a failed key check is argued.
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
  // ...and the restored declaration, for as long as nothing could have refuted
  // it. Not one of the three above: those are ids waiting to be *selected*,
  // where this is already selected (it has to be -- see the restore) and waiting
  // to be *checked*. What could refute it is the served vocabulary, and the
  // effect below is where that arrives.
  const restoredFunctionality = useRef<string | null>(null);

  // The four ids the restored snapshot named, for as long as nothing has
  // answered for them -- and what the page writes back in their place while
  // that is true (#106).
  //
  // `restored` above only defers the loss it was written to stop: a key check
  // that *rejects* flips it too, and the page then saves harborId and shipId as
  // null over the ids it had just read back. One unanswered request and the
  // selections are gone, which is the original bug with an extra tick in front
  // of it.
  //
  // The decision, and the reason: a failed key check means the account could
  // not be *asked*, and "we could not read" is not "there is nothing there" --
  // the rule this codebase keeps everywhere else. Nothing has said the location
  // was deleted; a server that did not answer says nothing about the account at
  // all. So the snapshot keeps what it held, and the next attempt -- another
  // refresh, or the Connect form below -- gets to use it.
  //
  // The case for clearing them is that with no account there is nothing to
  // validate an id against, so writing them away is honest. It answers a
  // different question. The page state IS empty and stays empty: a held id is
  // never selected, never rendered and never generated for -- it is handed to
  // the pending refs above and applied only where the account confirms the
  // thing still exists. What is at stake is only what a later attempt may try,
  // and an id that turns out to be gone costs one list lookup to find out.
  //
  // Each id is released by the answer that could refute it and by nothing else:
  // the account list for accountId, the workspace list for workspaceId, the
  // location list for harborId (and for shipId, when the location it belonged
  // to is gone), the location's own agents for shipId. A list that could not be
  // read releases nothing, for the same reason a failed key check does not.
  type HeldIds = Pick<session.Session,
                      "accountId" | "workspaceId" | "harborId" | "shipId">;
  const [held, setHeld] = useState<HeldIds | null>(null);
  /** Stop writing these ids back: what could refute them has arrived. */
  const release = (...keys: (keyof HeldIds)[]) => setHeld((h) => {
    if (!h || keys.every((k) => h[k] == null)) return h;
    const next = { ...h };
    for (const k of keys) next[k] = null;
    return next;
  });

  // Remember what a refresh would otherwise lose. Never the AUTH_TOKEN -- see
  // session.strip, which is where that decision lives and is tested.
  useEffect(() => {
    if (!restored) return;
    session.save({ sourceMode,
                   accountId: accountId ?? held?.accountId ?? null,
                   workspaceId: workspaceId ?? held?.workspaceId ?? null,
                   harborId: harborId ?? held?.harborId ?? null,
                   shipId: shipId ?? held?.shipId ?? null,
                   // Only manual entry has declared anything. Connected, this
                   // is a view over the location's funcIds and re-derives
                   // itself from them, so writing it down could only pin the
                   // next page load to a functionality the account never said.
                   declaredFunctionality: sourceMode === "manual" ? functionality : null,
                   manual, options, step, view, plan: planInputs,
                   confirmed });
  }, [restored, sourceMode, accountId, workspaceId, harborId, shipId, held,
      functionality, manual, options, step, view, planInputs, confirmed]);

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
    // The server drops every token it minted with this key (see key_clear), so
    // the record of which of them were typed over goes too -- kept, it would
    // stop a token minted after a reconnect from ever being evicted.
    evicted.current.clear();
    session.clear();
    // The held ids too, or the next save writes them straight back into a fresh
    // snapshot and this clear undoes itself. Nothing is being refuted here --
    // the key is simply being handed back, and what it was pointed at goes with
    // it, which is the same reason the account tree above is dropped.
    setHeld(null);
    // Land somewhere that still works. Account capacity has nothing to roll up
    // without a key, and Generate's "Connect to BlazeMeter" source has no
    // account to read a location from -- so the page goes to the flow's first
    // step in manual entry, where the sizing at the top of it needs
    // no account at all. That card is what "Plan capacity" used to be, and it
    // is still the one thing here that works with nothing connected.
    setView("flow");
    setStep(0);
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
      // A snapshot whose ids nothing has answered for gets its chance here:
      // after a failed key check this is the first account the page has
      // reached, and without this a successful connect would leave the ids kept
      // above with nothing to be applied by. They go through the same pending
      // refs a restore uses, so each is still applied only where the account
      // confirms it. The account itself is confirmed against the list rather
      // than set from the snapshot, because this key may be a different one.
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
      // The list is what answers for a held workspace id -- including when the
      // account has none, which is the empty answer rather than no answer.
      release("workspaceId");
    }).catch((e) => setLocErr(e.message))
      .finally(() => setWorkspacesBusy(false));
  }, [accountId, who]);

  // The funcId vocabulary again, now that there is an account to ask. The mount
  // fetch above got the covered baseline -- the three funcIds this tool
  // configures -- which is all there is with no key; this replaces it with what
  // the account actually offers, which is longer and carries BlazeMeter's own
  // display names for the funcIds nothing here has options for (#148).
  //
  // Failure keeps the baseline rather than clearing it: an account that refuses
  // the read has not said the vocabulary is empty, and a page with no funcIds at
  // all cannot even name what a location runs.
  useEffect(() => {
    if (!accountId || !who) return;
    api.funcIdChoices(accountId).then(setFuncIdChoices).catch(() => {});
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
      //
      // Either way this list is the answer for a held location id, so it stops
      // being written back here -- and it takes the agent id with it when the
      // location is gone, because an agent outlives its location nowhere. A
      // location that is still there answers for its own agents, below.
      if (want && ls.some((l) => l.id === want)) {
        setHarborId(want);
        release("harborId");
      } else {
        release("harborId", "shipId");
      }
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

  // The engine size this bundle will carry, and where the figure came from
  // (#132): the location's overrideCPU/overrideMemory unless an option
  // outranks them. Read off the page's own list rather than facts, because
  // locationUpdated keeps the list fresh after a settings save and facts are
  // fetched once; manual mode has no location and the statement carries that
  // structurally (noLocation), never as "the location sets nothing".
  const engineSize = useMemo(
    () => sizeStatement(raw("engine_cpu_limit"), raw("engine_mem_limit"),
                        location),
    [raw, location]);

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

  useEffect(() => {
    // A restored agent outranks the auto-pick: it is what the user chose, and
    // it is applied only once the location's own list has confirmed it exists.
    const want = pendingShip.current;
    if (want && ships.some((s) => s.id === want)) {
      pendingShip.current = null;
      setShipId(want);
    } else if (ships.length === 1 && !shipOnline(ships[0])) {
      // Auto-pick a lone agent only if it isn't running somewhere already --
      // a new deployment should get a NEW agent identity, not clone a live one.
      setShipId(ships[0].id);
    }
    // The location carries its own agents, so a location on screen is the
    // answer for a held agent id: re-selected just above if it is still there,
    // and written away if it is not.
    if (harborId) release("shipId");
  }, [harborId, ships.length]);

  // Which half of the agent section is on screen -- picking an identity or
  // minting one -- is derived from `showCreateShip` and the agents themselves,
  // in the panel that renders both: it is a view's decision, and the state it
  // is derived from is still here.

  // The credential of an agent this app minted for, back after a refresh (#123).
  //
  // A token is seen at exactly two moments -- creating an agent, and Regenerate
  // -- and both are this app's own writes, so the server keeps what it handed
  // over. Nothing else can: BlazeMeter shows a token once and no API reads one
  // back, which is why a reload used to lose it permanently and the next bundle
  // fell to a placeholder for an agent created a minute earlier.
  //
  // **Silently**, with no "restored" notice: a token claims nothing about the
  // world. It is the same value that was handed over, and it fails identically
  // whether it came from here or from a clipboard.
  //
  // Connect mode only. In manual entry the token is typed beside the two ids it
  // belongs to and switchMode clears it deliberately, so a lookup there would
  // put back exactly what that clear was for.
  useEffect(() => {
    setRecall("asking");
    if (sourceMode !== "connect" || !shipId) return;
    // Guarded like the capacity read: picking through a list of agents leaves
    // two answers in flight, and the slower one must not land under the agent
    // now selected.
    let live = true;
    api.mintedToken(shipId).then((r) => {
      if (!live) return;
      setRecall(recalled(r));
      // Only where there is one. A null is the server saying it holds nothing,
      // and writing that into the field would be this effect clearing a token
      // rather than restoring one -- forgetToken is what clears, on the move
      // that makes the old one wrong.
      if (r.auth_token) setOptions((o) => ({ ...o, auth_token: r.auth_token }));
    }).catch(() => {
      // Not `none`. The app may well be holding this agent's token and simply
      // be unable to say so, and the sentence for `none` would be a claim about
      // an account nothing here managed to ask.
      if (live) setRecall("unread");
    });
    return () => { live = false; };
  }, [sourceMode, shipId]);

  /** Which agents' remembered tokens have been typed over this session.
   *
   *  The token field is a controlled input, so `setToken` runs on every
   *  keystroke and the eviction must not. Once per agent is enough -- there is
   *  nothing left to forget after the first -- and a failed request re-arms it,
   *  because a store that still holds the old value is exactly the state this
   *  is for. */
  const evicted = useRef<Set<string>>(new Set());
  /** A hand-typed token wins, and goes on winning after a reload.
   *
   *  Without this the remembered copy comes back on the next page load and
   *  silently replaces what somebody typed over it. The page cannot keep the
   *  pasted one instead -- session.strip is where that is decided -- and this
   *  server only ever remembers what it minted, so dropping ours is the whole
   *  of it. */
  const forgetMintedToken = useCallback(() => {
    if (sourceMode !== "connect" || !shipId) return;
    if (evicted.current.has(shipId)) return;
    const ship = shipId;
    evicted.current.add(ship);
    api.forgetMintedToken(ship)
      // The store is empty for this agent now, and the field may yet be
      // cleared: `none` is what the sentence under an empty field should then
      // be reasoning from.
      .then(() => setRecall((r) => (r === "held" ? "none" : r)))
      .catch(() => { evicted.current.delete(ship); });
  }, [sourceMode, shipId]);

  /** Issue a NEW AUTH_TOKEN for the selected agent, and put it in the field.
   *
   *  This is the one way to mint from the page now: the download step had a
   *  rotate box beside its button, and it is gone. Minting belongs on the agent
   *  the credential is for, where what it kills is on screen. */
  const regenerateToken = async () => {
    if (!harborId || !shipId) return;
    const r = await api.issueToken(harborId, shipId);
    setOptions((o) => ({ ...o, auth_token: r.auth_token }));
    // The server remembered this one as it issued it, so the page is holding
    // what a reload would get back. The eviction re-arms with it: a token typed
    // over *this* one has a fresh copy to displace, and an agent left in the
    // evicted set would keep a dead credential alive across the next refresh.
    setRecall("held");
    evicted.current.delete(shipId);
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

  // The debounced live preview is further down, with the rest of what depends
  // on `sentOptions` -- it has to send what the download sends, and the blank
  // fields that go into that are not known until the group switches are.
  const previewTimer = useRef<number>();

  // agent status polling. An SV deployment also reads the namespace on the same
  // tick: the agent reports idle whether or not its virtual services ever
  // became reachable, so the heartbeat alone stays green through a deploy
  // stalled at WAITING_FOR_DOMAIN.
  //
  // The SV parameters travel by ref, not by dependency: they come from options,
  // and depending on them would tear down and restart the interval on every
  // keystroke in the namespace field.
  const svWatchRef = useRef({ on: false, ns: "", dom: "" });
  svWatchRef.current = { on: sv.configured, ns: txt("namespace"),
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

  /** What the sizing card states.
   *
   *  Assembled once, read twice: by the profile card at the top of step 1, and
   *  by whichever location is open below it -- which re-asks with its own agent
   *  count, since `slots` is engines per agent. Two of the five are the
   *  planner's own (they are what was typed at a target); three are bundle
   *  options, because the profile is sized for the engine the bundle asks for
   *  and a second copy of that size is how the two came to disagree.
   *
   *  There is nothing to "apply". The four location settings the plan implies
   *  are a write to the customer's account, and that write is made in one place
   *  -- the location's own panel, beside the sentence saying what it costs. */
  const profileAsk: PlanAsk = {
    users: planInputs.users,
    vusPerEngine: planInputs.vusPerEngine,
    engineCpu: raw("engine_cpu_limit"),
    engineMem: raw("engine_mem_limit"),
    enginesPerNode: raw("engines_per_node"),
  };

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

  // Manual mode declares the location's funcIds through the functionality buttons, so
  // it needs to know which funcIds a functionality stands for. Only the ones that
  // change the images are offered -- the rest generate the same bundle.
  const imageFuncs = useMemo(
    () => new Set(funcIdChoices.filter((c) => c.changes_images).map((c) => c.id)),
    [funcIdChoices]);
  /** The funcId a functionality declares when it is the manual-mode declaration: the
   *  first of the ones it claims that changes the images. */
  const primaryFuncOf = useCallback(
    (id: string | null) => (functionalities.find((f) => f.id === id)?.func_ids ?? [])
      .find((x) => imageFuncs.has(x)),
    [functionalities, imageFuncs]);
  // What manual mode declares the location runs: the selected functionality's primary
  // funcId. No literal funcId in TypeScript -- it comes from the served
  // vocabulary via primaryFuncOf.
  const manualFuncIds = useMemo(() => {
    const primary = primaryFuncOf(functionality);
    return primary ? [primary] : [];
  }, [primaryFuncOf, functionality]);

  // Manual facts are rebuilt from the typed values rather than held separately,
  // so there is one `facts` for the rest of the page whichever mode is on.
  // Debounced for the same reason the preview is: this runs on every keystroke.
  const manualTimer = useRef<number>();
  useEffect(() => {
    if (sourceMode !== "manual") return;
    // Nothing is built from a value that is not the shape an id comes in. The
    // fields say what is wrong; what this stops is the rest of the page --
    // preview, download -- describing a bundle assembled around a
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
  // Sticky: this only ever opens groups, so a group the user opened by hand
  // stays open with nothing set in it. `sv.required` is the dependency, not the
  // record it is carried in: a fresh object every render would re-run this on
  // every keystroke.
  useEffect(() => {
    setGrpOn((g) => detectGroups(options, g, { sv: sv.required }));
  }, [options, sv.required]);
  // The one place an SV option is written without anyone pressing anything, and
  // the whole of it: an imported profile can arrive stranded (openshift ingress
  // on a platform that is not OpenShift, a gateway no backend will read, a
  // chart format this location cannot have), and a location can turn out to be
  // an SV one after the form was filled in. What has to change is decided in
  // sv.ts as a value -- which is what makes it testable, and what stops this
  // being two effects writing what a third reads back. Applying the patch makes
  // the next one null, so this settles in one pass.
  //
  // Where the patch moves the output format, it is recorded rather than only
  // applied. That correction is the one thing here that overrides a choice the
  // user made on this page, and it used to happen in silence: pick Docker,
  // switch service virtualization back on, and the segment moved to Kubernetes
  // manifests with nothing said. The notice carries the generator's own
  // sentence for the refusal, and `setFormat` clears it -- a format chosen
  // after the fact is the answer, not something to keep explaining.
  useEffect(() => {
    const patch = sv.patch;
    if (!patch) return;
    // Read off the same render that produced the patch -- `sv` is derived from
    // `options`, so a new patch and the format it is correcting are always the
    // one pair. The sentence is the generator's, taken from the table that
    // disabled the segment, so the notice and the tooltip cannot drift.
    const was = format;
    if (patch.output_format && was && was !== patch.output_format) {
      setFormatNotice({ was, why: sv.blockedFormats[was] ?? "" });
    }
    setOptions((o) => ({ ...o, ...patch }));
  }, [sv.patch]);
  const flipGroup = (id: GroupId, on: boolean) => {
    setGrpOn((g) => ({ ...g, [id]: on }));
    const group = GROUP_BY_ID[id];
    setOptions((o) => {
      // `required` reaches disable so a group the location demands can record
      // being switched off rather than merely emptied -- see the SV group.
      const patch = on ? group.enable(o)
        : group.disable(o, !!sv.groupRequired[id]);
      // A group that seeds nothing must hand back the same object: a fresh
      // identity would re-run the preview effect and re-POST /api/generate for
      // options that did not change.
      return Object.keys(patch).length ? { ...o, ...patch } : o;
    });
  };
  // Moving the view. The only option it may write is the namespace, and only
  // while that still holds one a functionality suggested -- everything else stays
  // exactly as it is, because narrowing a view must not change what the bundle
  // generates. No group is flipped on or off here for the same reason.
  //
  // A function rather than an effect on `functionality`: an effect would also fire
  // when the vocabulary lands mid-session and rewrite a namespace already typed.
  // `suggestNs` is opt-in and only the location effect passes it. Switching the
  // view by hand must not touch the namespace: the namespace is generated into
  // every manifest, so suggesting on a manual switch would make looking at a
  // functionality change the bundle -- the one thing a view is not allowed to do. It
  // also flip-flopped blazemeter <-> blazemeter-sv on a location that has both.

  const pickFunctionality = useCallback((id: string, suggestNs = false) => {
    setFunctionality(id);
    // In manual mode the functionality buttons are the declaration rather than a
    // view -- but nothing is written here: manualFuncIds derives it from
    // `functionality`, so selecting one is the whole action.
    const f = functionalities.find((x) => x.id === id);
    if (!f || !suggestNs) return;
    setOptions((o) => {
      const ns = suggestNamespace(String(o.namespace ?? ""), f, functionalities);
      // Same object when there is nothing to suggest: a fresh identity re-POSTs
      // /api/generate for options that did not change.
      return ns == null ? o : { ...o, namespace: ns };
    });
  }, [functionalities]);

  // Which functionality a location opens on, from its funcIds. Keyed on the harbor
  // rather than on `facts`, which is refetched after creating an agent: that
  // must not yank the view back from wherever the user moved it. `functionality` is
  // read but deliberately not a dependency -- depending on it would re-force
  // the starting functionality every time the user chose a different one.
  useEffect(() => {
    if (!functionalities.length) return;
    // ...and where a restored declaration is checked, because this is where the
    // thing that could refute it arrives: it names a functionality from the served
    // vocabulary, and until that has landed there is nothing to check it
    // against. Still offered means it stands, and nothing below may touch it --
    // hence the return rather than a fall-through.
    const declared = restoredFunctionality.current;
    if (declared) {
      restoredFunctionality.current = null;
      if (functionalities.some((f) => f.id === declared)) return;
      // Not offered any more: dropped rather than kept. A functionality this build
      // does not serve names no funcId, so the identity's facts would be
      // gathered as though nothing had been declared, and no radio would be
      // selected to say so or to change it with -- which is what being stuck on
      // it looks like. So the page lands where a fresh manual session lands.
      // Without the namespace suggestion, though: a restore is not a hand
      // switch and not a location being picked, and what it read back is
      // generated into every manifest.
      pickFunctionality(functionalities[0].id);
      return;
    }
    // Manual entry declares rather than reads, so the facts have nothing to say
    // here: their funcIds *are* the declaration (manualFuncIds), and reading
    // them back can only restate it -- or lose it, where the declared functionality
    // has no image-changing funcId and startFunctionality falls back to the first
    // served one. What went with it is a namespace suggestion that fired when
    // the ship id was finished being typed, which is not a location being picked
    // either.
    if (sourceMode === "manual" || !facts) {
      // facts is cleared while the next location's are fetched. Falling back to
      // the default in that gap would flip the view (and the suggested
      // namespace) to performance and back for every SV location picked.
      if (!functionality) pickFunctionality(functionalities[0].id, true);
      return;
    }
    const start = startFunctionality(facts.func_ids, functionalities);
    if (start) pickFunctionality(start, true);
  }, [facts?.harbor_id, functionalities, pickFunctionality, sourceMode]);

  // -- has the choice been made, or only landed on? ---------------------------
  // Both lists auto-pick: a lone agent is chosen for you, and a session restore
  // brings back a location and an agent nobody has looked at this time round.
  // So "something is selected" was never the same question as "somebody has
  // said this is the one", and step 1 asked the first while claiming the
  // second. Confirm on each list answers it.
  //
  // Manual entry has neither list. Typing a harbor id and a ship id by hand IS
  // the deliberate act, and there is no panel to press -- so it is finished on
  // the ids alone, exactly as before.
  const locConfirmed = !!harborId && confirmed.loc === harborId;
  const shipConfirmed = !!shipId && confirmed.ship === shipId;
  const chosen = sourceMode === "manual" || (locConfirmed && shipConfirmed);
  /** What step 1 is still waiting for, or "" when nothing. Same shape as the
   *  configure step's: one derivation behind the tick and the sentence. */
  const agentBlocked = !facts || !shipId
    ? "fill in the agent details to continue"
    : chosen ? ""
    : "confirm " + [!locConfirmed ? "the location" : "",
                    !shipConfirmed ? "the agent" : ""]
      .filter(Boolean).join(" and ");

  // -- what the bundle is ----------------------------------------------------
  // Flat YAML to kubectl apply, the chart with a values overlay, or one agent
  // as one container. The first two render the same objects and differ only in
  // how you install and upgrade; the third is a different platform, and around
  // two dozen options reach nothing in it. So the choice is made at the top of
  // the configure step and the form follows it -- asking for a namespace and a
  // ServiceAccount and then handing over a bundle with neither is the silent
  // failure this arrangement exists to stop. Which formats this *configuration*
  // refuses is sv.blockedFormats, with the sentence each is refused in, and the
  // mirror of it -- which functionalities this format refuses -- is
  // sv.functionalityBlocked.
  // `format` itself is declared with `sv`, which reads it.
  /** Does this option reach anything in the bundle being generated? What the
   *  configure step hides by, and what the two blockers below are judged
   *  against. The table is the generator's; see formats.ts. */
  const applies = useCallback(
    (k: string) => optionApplies(k, format, dockerIgnored),
    [format, dockerIgnored]);
  /** ...and, where a field's absence needs explaining, the generator's own
   *  sentence for it. Served with the keys for exactly this: the bundle's
   *  README prints these, and the form hiding the field should not have to
   *  write its own version. */
  const whyIgnored = useCallback(
    (k: string) => why(k, format, dockerIgnored), [format, dockerIgnored]);

  // Both are answered against the format, not against the options alone: they
  // are what blocks the download, and an option this bundle cannot carry must
  // not block it -- the field for it is not on screen, so there would be
  // nothing to fix. generate() agrees on both counts for a docker bundle.
  const namespaceOk = !applies("namespace") || !!txt("namespace");
  // Empty is refused by generate(), so this blocks the download rather than
  // only colouring the field -- an unnamed account is the one state of these
  // two that produces no bundle at all.
  const saOk = !applies("service_account_name") || serviceAccountOk(options);
  const saCreate = options.service_account_create !== false;
  // What the download button will do about the credential: the hint beside it,
  // whether the bundle can be applied at all, and the request it sends. One
  // derivation because those answer one question and could otherwise disagree
  // -- see token.ts. It no longer takes a rotate choice: the box that made one
  // is gone, and minting is step 1's, on the agent the credential belongs to.
  const tokenPlan = downloadPlan(previewToken);
  // -- what this location runs -----------------------------------------------
  // `locFunctionalities` and `enabled` are derived above, beside the record that reads
  // them. This is the funcIds the location carries that the tool has no options
  // for: locations already run tdm/dataPublisher/delphix, and naming them is
  // the honest version of a page that quietly models three funcIds. Named with
  // the served vocabulary, so where an account has been read they carry
  // BlazeMeter's own words rather than a camelCase id.
  const locUnclaimed = unclaimedFuncIds(facts?.func_ids, functionalities,
                                        funcIdChoices);
  // The second and last place an option is written without anyone pressing
  // anything, and the same shape as the SV correction above: what has to change
  // is a value optionGroups decides, this only applies it. A profile, a
  // restored session or a location picked after the form was filled in can all
  // leave options set for a functionality the location does not run -- and the switch
  // that would clear them is deliberately not on screen, so nothing else can.
  // Below `enabled` rather than beside the other effects because it reads it.
  const notRun = notRunPatch(options, enabled);
  useEffect(() => {
    if (!notRun) return;
    setOptions((o) => ({ ...o, ...notRun }));
  }, [notRun]);
  // Which groups are in use but not finished. Each group declares its own rule,
  // so a functionality gaining required options later needs nothing here.
  const incomplete = incompleteGroups(options, sv.groupRequired, svConst.backends);
  // ...and what that leaves the configure step still needing, named. Empty is
  // "nothing", which is what ticks the step off -- see configureBlockedBy. The
  // blocking subset, not `incomplete`: a group with an empty required field is
  // unfinished on its row and no longer in the way of the step.
  const configureBlocked = configureBlockedBy(
    options, blockingGroups(options, sv.groupRequired, svConst.backends));
  // The required fields left empty. One list, feeding three things that must
  // not be allowed to disagree: what is sent, what the configure step warns
  // about, and what the download step repeats. Memoised on the options identity
  // because `withPlaceholders` below is in the preview effect's dependencies.
  const blanks = useMemo(
    () => blankRequired(options, applies, grpOn),
    [options, applies, grpOn]);
  // What every caller that generates actually sends. Derived, never stored:
  // the marker must not reach `options`, or it lands in the session snapshot
  // and comes back on the next load as a value somebody appears to have typed
  // -- and the input on screen would show it, which is a form answering its own
  // question. Same object when there is nothing to fill, so the effect below
  // does not re-POST for a bundle that did not change.
  const sentOptions = useMemo(
    () => withPlaceholders(options, blanks), [options, blanks]);

  // debounced live preview
  useEffect(() => {
    // The token report goes with the files: it describes the bundle those came
    // from, and left behind it announces a placeholder in a bundle that no longer
    // exists -- which is exactly what switching source mode used to leave on
    // screen.
    if (!facts) { setFiles([]); setPreviewToken(null); return; }
    // A bundle is generated *for an agent*, so without one there is nothing to
    // preview and generate() refuses -- correctly, and with a sentence about a
    // ship_id nobody has been asked for yet. Picking a location that has no
    // agents is a normal state this page has a whole amber panel for, and it
    // used to spend a 400 on saying so. The preview waits for the agent
    // instead; the empty preview reads as "not yet", which is what it is.
    if (!shipId) { setFiles([]); setPreviewToken(null); setGenErr(null); return; }
    window.clearTimeout(previewTimer.current);
    previewTimer.current = window.setTimeout(async () => {
      try {
        const opts = { ...sentOptions, ship_id: shipId ?? undefined };
        const r = await api.generate(facts, opts);
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
    // No save folder in the dependencies any more: the page used to send one, so
    // that a directory already holding this ship's bundle made the token branch
    // `reused`, and the preview said so. Saving to a folder is the CLI's and the
    // MCP server's now (`bzm-opl-gen generate -o`, `opl_bundle`), so from here
    // the branch is unreachable and there is nothing to debounce it against.
  }, [facts, sentOptions, shipId]);

  // -- is the published endpoint answering? ----------------------------------
  // A Running mock pod says nothing about whether anything routes to it: where
  // the controller rejects crane's Ingress the endpoint 503s while the pod is
  // healthy. The scheme is the record's -- it follows the TLS secret, because
  // that is what decides whether the published endpoint terminates TLS.
  //
  // Nothing renders off the promise: the row goes busy, the status poll behind
  // it keeps running, and the server bounds its own wait well inside one poll
  // interval, so a hanging endpoint holds up nothing but its own row.
  const checkEndpoint = async (host: string) => {
    setSvChecks((c) => ({ ...c, [host]: { busy: true } }));
    try {
      const res = await api.svCheck(host, sv.scheme);
      setSvChecks((c) => ({ ...c, [host]: { busy: false, res } }));
    } catch (e) {
      setSvChecks((c) => ({ ...c, [host]: { busy: false, err: String((e as Error).message) } }));
    }
  };

  // The environment variables, which are no longer a group.
  //
  // #131 made them one, and a switch was the wrong control for them: what the
  // switch turned on was an empty name box, so the area asked somebody to
  // supply the vocabulary as well as the value. It is a list now -- everything
  // BlazeMeter documents that no group here already writes -- and a list has
  // nothing to be off. It sits beside Advanced for that reason: closed, not
  // outside the form.
  const envArea = (
    <EnvVars env={options.extra_env} vars={agentEnv} reserved={reservedEnv}
      cluster={!isDocker(format)}
      // Written whole and already normalised: env.ts emits `null` for "nothing
      // set", so what comes out of the area is exactly what comes back as
      // `env`, which is how its editors tell their own writes from an imported
      // profile's.
      onChange={(v) => set("extra_env", v)} />
  );

  // Each group's body, wired with the props that group actually needs -- no
  // shared bag of options handed round, so a group reads on its own and what it
  // may write is what its declaration says it owns.
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
    proxy: <ProxyGroup proxy={proxyOpt} onField={setProxy} />,
    ca: (
      <CaGroup applies={applies} openshift={isOpenshift(options)}
        mode={caMode} onMode={setCaMode}
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
        engineTolerations={options.engine_tolerations}
        engineNodeSelector={options.engine_node_selector}
        onPatch={(p) => setOptions((o) => ({ ...o, ...p }))} />
    ),
    security: (
      <SecurityGroup applies={applies} cluster={!isDocker(format)}
        useSecret={Boolean(options.use_secret)}
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
    // The one record, and the four writes. Which backend is chosen, whether it
    // is finished, what the prose is rendered against and what may be offered
    // are all one answer -- assembled here from eleven props, they were eleven
    // chances to assemble it wrongly.
    sv: (
      <SvGroup sv={sv}
        onIngress={(v) => set("sv_ingress", v)}
        onSubdomain={(v) => set("sv_subdomain", v)}
        onTlsSecret={(v) => set("sv_tls_secret", v)}
        onGateway={(v) => set("sv_istio_gateway", v)} />
    ),
  };

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

  /** Create the private location the form describes, and work on it.
   *
   *  A named function here, like createShipNow, rather than a handler inside the
   *  form: this is a real write to the customer's account, and the panel renders
   *  the button beside the sentence saying so without being able to make the
   *  call itself. The list is re-read afterwards because a location arrives with
   *  no agents and the row has to say so. */
  const createLocationNow = async () => {
    try {
      const l = await api.createLocation({ ...newLoc, account_id: accountId! });
      const ls = await api.locations(workspaceId!);
      setLocations(ls); setHarborId(l.id); setShowCreateLoc(false);
    } catch (e) { setLocErr(String((e as Error).message)); }
  };
  /** Where in BlazeMeter the page is pointed. Named once because four things
   *  ask -- the summary line under the flow, the location list's heading, and
   *  the new-location form -- and three of them were each doing the find. */
  const accountName = useMemo(
    () => accounts.find((a) => a.id === accountId)?.name ?? null,
    [accounts, accountId]);
  const workspaceName = useMemo(
    () => workspaces.find((w) => w.id === workspaceId)?.name ?? null,
    [workspaces, workspaceId]);
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
              {/* The whole path, account first: the account and workspace are
                  chosen in the drawer, which is collapsed for most of a
                  session, so a bar that started at the location said which
                  location without saying which account's. Two bundles for two
                  customers differ in exactly that. */}
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
                <span className="text-[11px] text-amber-700 ml-1">
                  — this location is empty; the first agent has to be created
                </span>
              )}
            </div>
          ) : undefined}
          /* Step 2's tick and step 2's reason are one derivation, so the dot
             and the sentence under it cannot disagree -- and the sentence
             names what this bundle actually still needs rather than the three
             things a Kubernetes one would. */
          done={[!agentBlocked, !configureBlocked, false]}
          blockedBy={[agentBlocked, configureBlocked, ""]}>
          {/* 1 · How big the run is, and which agent it is generated for.
              The profile comes first because it decides everything after it and
              needs none of it -- no key, no account, no cluster -- and because
              its answer is four settings on the location picked below, which is
              the next thing on this screen rather than a number to carry to
              another one. Under it, where the harbor id, ship id and token come
              from: connected they are picked from the account, manually they
              are typed, and both end at the same three values. */}
          <Section n={1} title="Capacity & agent" done={!agentBlocked}
            hint="Size the run, then the location and agent it is generated for.">
            <div className="space-y-3">
            <Sizing
              api={api} ask={profileAsk} setInputs={setPlanInputs}
              /* The engine size and the engines per node are the bundle's own
                 options, edited here as well as in the Configure step's Sizing
                 group: the profile is sized for the engine the manifests ask
                 for, so there is one value rather than two that agree until
                 somebody changes one. */
              setEngine={(cpu, mem) => setOptions((o) => ({
                ...o, engine_cpu_limit: cpu, engine_mem_limit: mem }))}
              /* An integer option, so it is stored as one: the field is a
                 string because every form field is, and generate() refuses a
                 string here. */
              setPerNode={(v) => set("engines_per_node",
                                     v.trim() ? Number(v) : null)} />
            {/* Five records, assembled here. Every value in them is state this
                file still owns -- what the panel gained is the three answers it
                used to be handed already worked out: the filtered list, whether
                an agent is reporting, and the new-location form as a finished
                element. */}
            <AgentPanel
              api={api} profile={profileAsk}
              source={{
                mode: sourceMode, switchTo: switchMode,
                manual, setManual, who,
              }}
              locations={{
                accountName, workspaceName,
                list: locations, filter: locFilter, setFilter: setLocFilter,
                selectedId: harborId, pick: setHarborId,
                busy: locBusy, error: locErr, updated: locationUpdated,
                create: {
                  open: showCreateLoc,
                  // Opening or closing the form drops the last refusal with it:
                  // an error about a form nobody is looking at describes
                  // nothing.
                  setOpen: (v) => { setLocErr(null); setShowCreateLoc(v); },
                  workspace: workspaceName,
                  draft: newLoc,
                  // The draft the panel edits is four of the five fields; the
                  // workspace id is the drawer's and is merged back here.
                  setDraft: (f) => setNewLoc((n) => ({ ...n, ...f(n) })),
                  choices: funcIdChoices, blockedBy: createLocBlockedBy,
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
                newName: newShipName, setNewName: setNewShipName,
                create: createShipNow,
                error: shipErr, tokenNotice: shipTokenNotice,
                confirmed: shipConfirmed,
                confirm: () => setConfirmed((c) => ({ ...c, ship: shipId })),
              }}
              credential={{
                token: raw("auth_token"),
                // Typing is the one write to this field the app did not make,
                // so it is where the remembered copy is dropped -- see
                // forgetMintedToken.
                setToken: (v) => {
                  set("auth_token", v || null);
                  forgetMintedToken();
                },
                regenerate: regenerateToken,
                note: recallNote(recall),
              }} />
            </div>
          </Section>

          {/* 2 · Configure */}
          <Section n={2} title="Configure"
            hint="Everything re-renders the preview live.">
            <ConfigurePanel
              functionalities={functionalities} pickFunctionality={pickFunctionality}
              sourceMode={sourceMode} enabled={enabled}
              locUnclaimed={locUnclaimed}
              options={options} set={set}
              format={format}
              setFormat={(v) => { setFormatNotice(null); set("output_format", v); }}
              blockedFormats={sv.blockedFormats}
              functionalityBlocked={sv.functionalityBlocked} formatNotice={formatNotice}
              applies={applies}
              grpOn={grpOn} grpRequired={sv.groupRequired}
              grpDeclined={sv.groupDeclined}
              // Null where the format has no limits env (docker names the two
              // keys in its ignored table), so the card does not state a size
              // nothing reads.
              engineNote={applies("engine_cpu_limit") ? engineSize.text : null}
              flipGroup={flipGroup} groupBody={groupBody} envArea={envArea}
              incomplete={incomplete} blanks={blanks}
              namespaceOk={namespaceOk} saOk={saOk} saCreate={saCreate}
              exportProfile={exportProfile} importProfile={importProfile} />
          </Section>

          {/* 3 · Download & verify */}
          <Section n={3} title="Download & verify">
            {/* Four records and one report, assembled here. Every value in
                them is state this file still owns -- what changed is that the
                panel is handed the five questions it answers rather than forty
                fields it has to reassemble them from. */}
            <DownloadPanel
              /* The two requests that produce a bundle are made in the panel,
                 beside the warning saying what they cost -- but through the
                 same client every other route uses, so what one carries about
                 the credential is drivable rather than only reviewable. */
              api={api}
              bundle={{
                // `sentOptions`, not `options`: the zip this panel downloads has
                // to be the bundle the preview showed, markers included.
                facts, shipId, options: sentOptions, format,
                sv, saOk, genErr, blanks,
                unfinished: incomplete, goToConfigure: () => setStep(1),
              }}
              credential={{ plan: tokenPlan }}
              attempt={attempt} report={setAttempt}
              watch={{
                available: sourceMode === "connect",
                on: polling, setOn: setPolling,
                agent: ships.find((s) => s.id === shipId)?.name || shipId,
                status, mocks: svMocks, checks: svChecks,
                check: checkEndpoint,
              }} />
          </Section>
        </StepFlow>
      </main>
      )}
    </>
  );

  return (
    // The window's height, not a minimum of it, and the overflow is the inner
    // pane's. `min-h-screen` let the *document* grow to whatever the view
    // rendered, so the `overflow-y-auto` below never had a bounded parent to
    // scroll inside: on a real account Account capacity is 11,000px tall, the
    // drawer stretched to match, and the account menu at its foot sat that far
    // below the fold -- the one control that switches account, unreachable on
    // the view whose whole subject is the account. Generate never showed it
    // because StepFlow pins itself to `100vh - 6.75rem` and scrolls its own
    // step; this is that assumption made true for the shell rather than
    // restated per view.
    //
    // The height alone, with no `overflow-hidden` beside it: bounding the row
    // is what makes the pane scroll, and a clip here would also clip the one
    // thing that has to leave the drawer -- the account menu, which is
    // absolutely positioned inside it. It is what the drawer's own workspace
    // picker was already cut by once.
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
