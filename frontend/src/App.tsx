import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, downloadZip, Account, AgentStatus, Facts, Feature, GeneratedFile,
  ManualFactsOut,
  FuncIdChoice, KeyCandidate, Location, Options, Ship, SvCheckOut, SvConstants,
  SvMocksOut, Workspace,
} from "./api";
import {
  Button, Check, ErrorMsg, Field, inputCls, JsonArea, SearchSelect, Section,
  SegmentedControl, SubSection, Switch, TextInput,
} from "./components";
import { Preview } from "./Preview";
import { SvCtx } from "./SvPrereqs";
// The option groups of step 4: one declaration each (title, hint, the option
// keys it owns, the features it belongs to, and its detect/enable/disable),
// plus a body per group. This file only wires them -- what a group *is*, and
// which of them a feature puts on screen, lives in optionGroups.ts.
import {
  allGroupsOff, appliesTo, caModeOf, caModePatch, CaMode, detectGroups,
  enginePreset, featuresOf, GROUP_BY_ID, GroupFlags, GroupId, hiddenBlockers,
  incompleteGroups,
  setButHidden, startFeature, suggestNamespace, unavailableFeatures,
  unclaimedFuncIds, visibleGroups,
} from "./optionGroups";
import { CaGroup } from "./groups/CaGroup";
import { ManualSource } from "./groups/ManualSource";
import { GroupRow } from "./groups/GroupRow";
import { ProxyGroup } from "./groups/ProxyGroup";
import { RegistryGroup } from "./groups/RegistryGroup";
import { SchedGroup } from "./groups/SchedGroup";
import { SecurityGroup } from "./groups/SecurityGroup";
import { SizingGroup } from "./groups/SizingGroup";
import { SvGroup } from "./groups/SvGroup";

// Why performance and service virtualization want separate agents, and so
// separate namespaces: one agent serving both puts mocks and load engines in a
// single namespace, on a single slot budget, with a single restart lifecycle.
// Said in the location list, in the callout under it, and beside the suggested
// namespace in step 4. One string because the coupling is one fact -- three
// near-copies is how the list ends up claiming something the callout no longer
// does. It gates nothing: accounts already running a combined location have to
// keep working, and the feature selector is a view over one such location's
// options rather than a choice of what to deploy.
const KIND_COUPLING =
  "mocks and load engines share a namespace, a slot budget and a restart "
  + "lifecycle, so redeploying the performance agent takes the virtual "
  + "services down with it";


export default function App() {
  // -- connection ------------------------------------------------------------
  const [candidates, setCandidates] = useState<KeyCandidate[]>([]);
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
  const [accountId, setAccountId] = useState<number | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
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
  const [facts, setFacts] = useState<Facts | null>(null);

  // -- where the three account values come from -------------------------------
  // "connect" reads them from the account; "manual" takes them typed in, for a
  // customer whose account (and cluster) nobody here can reach. Everything
  // downstream consumes `facts` + shipId + options.auth_token and never learns
  // which way they arrived -- that is the whole point of manual facts being the
  // same shape gather() returns.
  const [sourceMode, setSourceMode] = useState<"connect" | "manual">("connect");
  const [manual, setManual] = useState({ harbor_id: "", ship_id: "", func_ids: ["performance"] });
  const [guiIncomplete, setGuiIncomplete] = useState(false);
  // Collapsed once the source is settled: three steps' worth of pickers is
  // noise while you are configuring, and the summary says what was chosen.
  const [sourceOpen, setSourceOpen] = useState(true);

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

  const [profiles, setProfiles] = useState<{ name: string; options: Options }[]>([]);
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
  const [dlErr, setDlErr] = useState<string | null>(null);

  useEffect(() => {
    api.keyDetect().then((r) => {
      setCandidates(r.candidates);
      if (r.candidates[0]) setKeyPath(r.candidates[0].path);
    }).catch(() => {});
    api.profiles().then(setProfiles).catch(() => {});
    api.optionDefaults().then((d) => {
      setDefaults(d);
      setOptions((o) => ({ ...d, ...o }));
    }).catch(() => {});
    api.svConstants().then(setSvConst).catch(() => {});
    api.funcIdChoices().then(setFuncIdChoices).catch(() => {});
    api.features().then(setFeatures).catch(() => {});
  }, []);

  const connect = async (body: Parameters<typeof api.keySet>[0]) => {
    if (connecting) return;
    setConnErr(null);
    setConnecting(true);
    try {
      const r = await api.keySet(body);
      setWho({ email: r.user.email, keyId: r.key_id });
      // Still connecting as far as the user is concerned: the key is accepted
      // but the account list is what the next step needs, and releasing the
      // button between the two would show a ready form with nothing in it.
      const accts = await api.accounts();
      setAccounts(accts);
      setAccountId(r.default_account_id ?? accts[0]?.id ?? null);
    } catch (e) { setConnErr(String((e as Error).message)); }
    finally { setConnecting(false); }
  };

  useEffect(() => {
    if (!accountId || !who) return;
    setWorkspaces([]); setWorkspaceId(null);
    api.workspaces(accountId).then((ws) => {
      setWorkspaces(ws);
      setWorkspaceId(ws[0]?.id ?? null);
    }).catch((e) => setLocErr(e.message));
  }, [accountId, who]);

  useEffect(() => {
    setNewLoc((n) => ({ ...n, workspace_id: workspaceId ?? 0 }));
    setLocations([]); setHarborId(null); setLocErr(null);
    if (workspaceId == null) return;
    api.locations(workspaceId).then(setLocations).catch((e) => setLocErr(e.message));
  }, [workspaceId]);

  const location = useMemo(
    () => locations.find((l) => l.id === harborId) ?? null, [locations, harborId]);
  const ships: Ship[] = location?.ships ?? [];

  // Which agent a location's funcIds imply. SV membership is generate.SV_FUNC_IDS
  // over /api/sv-constants; everything else -- performance, the functional
  // suites, the recorder -- runs on a performance agent, so "not SV" is the test
  // rather than a second list to keep in step. Nothing is claimed until that
  // fetch lands, or every SV location would flash up labelled performance.
  // Both the badge here and the view in step 4 come from featuresOf, so a
  // location cannot be called one thing in the list and another below it.
  const locLabels = useCallback((l: Location) =>
    featuresOf(l.funcIds, features).map(
      (id) => features.find((f) => f.id === id)?.label ?? id), [features]);

  useEffect(() => {
    setShipId(null); setFacts(null); setStatus(null); setShowCreateShip(false);
    if (!harborId) return;
    api.facts(harborId).then(setFacts).catch((e) => setShipErr(e.message));
  }, [harborId]);

  const shipOnline = (s: Ship) =>
    !!s.lastHeartBeat && Date.now() / 1000 - s.lastHeartBeat < 300;

  useEffect(() => {
    // Auto-pick a lone agent only if it isn't running somewhere already --
    // a new deployment should get a NEW agent identity, not clone a live one.
    if (ships.length === 1 && !shipOnline(ships[0])) setShipId(ships[0].id);
  }, [harborId, ships.length]);

  // Which half of step 3 is on screen -- picking an identity and minting one
  // are one-of, because reusing an identity that is already running conflicts
  // with that install while creating one is free. Derived, not a second piece
  // of state: a location with no agents has nothing to pick, so it opens on the
  // create form, and creating the first agent drops back to the list showing
  // it. The same derivation is why Cancel appears only when there is a list to
  // go back to.
  const creatingShip = showCreateShip || ships.length === 0;

  // debounced live preview
  const previewTimer = useRef<number>();
  useEffect(() => {
    if (!facts) { setFiles([]); return; }
    window.clearTimeout(previewTimer.current);
    previewTimer.current = window.setTimeout(async () => {
      try {
        const opts = { ...options, ship_id: shipId ?? undefined };
        const r = await api.generate(facts, opts);
        setFiles(r.files);
        setGenErr(null);
        // Keep the open file open across the re-render every option edit
        // causes; fall back to the first manifest only once the one being read
        // stops being generated at all.
        setActiveFile((a) => (a && r.files.some((f) => f.name === a)
          ? a : r.files[0]?.name ?? null));
      } catch (e) { setGenErr(String((e as Error).message)); }
    }, 250);
  }, [facts, options, shipId]);

  // agent status polling. An SV deployment also reads the namespace on the same
  // tick: the agent reports idle whether or not its virtual services ever
  // became reachable, so the heartbeat alone stays green through a deploy
  // stalled at WAITING_FOR_DOMAIN.
  //
  // The SV parameters travel by ref, not by dependency: they come from options,
  // and depending on them would tear down and restart the interval on every
  // keystroke in the namespace field.
  const svWatchRef = useRef({ on: false, ns: "", dom: "" });
  svWatchRef.current = { on: !!txt("sv_ingress"), ns: txt("namespace"),
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

  // Settled means: an agent is chosen and its facts are in. Collapsing then
  // keeps three steps of pickers from sitting above the configuration for the
  // rest of the session; "Change" reopens it.
  useEffect(() => {
    if (sourceMode === "connect" && facts && shipId) setSourceOpen(false);
  }, [sourceMode, facts, shipId]);

  // Manual facts are rebuilt from the typed values rather than held separately,
  // so there is one `facts` for the rest of the page whichever mode is on.
  // Debounced for the same reason the preview is: this runs on every keystroke.
  const manualTimer = useRef<number>();
  useEffect(() => {
    if (sourceMode !== "manual") return;
    if (!manual.harbor_id.trim() || !manual.ship_id.trim()) {
      setFacts(null); setShipId(null); return;
    }
    window.clearTimeout(manualTimer.current);
    manualTimer.current = window.setTimeout(() => {
      api.manualFacts({
        harbor_id: manual.harbor_id.trim(),
        ship_id: manual.ship_id.trim(),
        func_ids: manual.func_ids,
      }).then((r: ManualFactsOut) => {
        setFacts(r.facts);
        setShipId(r.facts.ships[0].id);
        setGuiIncomplete(r.gui_images_incomplete);
      }).catch((e) => setGenErr(String(e.message)));
    }, 250);
  }, [sourceMode, manual]);

  // Switching modes drops what the other one established. Leaving a connected
  // location's facts in place while manual fields are on screen is how the
  // preview ends up describing an agent nobody is looking at.
  const switchMode = (m: string) => {
    const mode = m as "connect" | "manual";
    if (mode === sourceMode) return;
    setSourceMode(mode);
    setFacts(null); setShipId(null); setStatus(null); setGenErr(null);
    setSourceOpen(true);
    // The token is fetched on download when connected, and typed when not --
    // so it must not survive the switch either way.
    set("auth_token", null);
  };

  const applyProfile = (name: string) => {
    const p = profiles.find((x) => x.name === name);
    if (p) setOptions({ ...defaults, namespace: options.namespace, ...p.options });
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
  const svRequired = !!facts?.func_ids?.some(
    (f) => svConst.func_ids.includes(f));
  // What a group cannot read off the options: SV is required by the location,
  // not by anything configured. Keyed by group id so the walk below never has
  // to test for one by name.
  const grpRequired: Partial<GroupFlags> = { sv: svRequired };
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
  // state stayed null) or clear a NODEPORT the SV path cannot use.
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
      const strandedIngress =
        o.sv_ingress === "openshift" && o.platform !== "openshift";
      const seedIngress = svRequired && !o.sv_ingress;
      const ingress = seedIngress || strandedIngress ? "nginx" : o.sv_ingress;
      const clearNodePort = !!ingress
        && o.service_type != null && o.service_type !== "CLUSTERIP";
      const clearGateway = !!ingress && ingress !== "istio" && !!o.sv_istio_gateway;
      if (!seedIngress && !strandedIngress && !clearNodePort && !clearGateway) return o;
      return {
        ...o,
        ...(seedIngress || strandedIngress ? { sv_ingress: "nginx" } : {}),
        ...(clearNodePort ? { service_type: "CLUSTERIP" } : {}),
        ...(clearGateway ? { sv_istio_gateway: null } : {}),
      };
    });
  }, [svRequired, options.sv_ingress, options.service_type,
      options.sv_istio_gateway, options.platform]);
  const flipGroup = (id: GroupId, on: boolean) => {
    setGrpOn((g) => ({ ...g, [id]: on }));
    const group = GROUP_BY_ID[id];
    setOptions((o) => {
      const patch = on ? group.enable(o) : group.disable(o);
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
  // Manual mode declares the location's funcIds through the feature buttons, so
  // it needs to know which funcIds a feature stands for. Only the ones that
  // change the images are offered -- the rest generate the same bundle.
  const imageFuncs = useMemo(
    () => new Set(funcIdChoices.filter((c) => c.changes_images).map((c) => c.id)),
    [funcIdChoices]);
  const featureFuncs = useCallback(
    (id: string | null) => (features.find((f) => f.id === id)?.func_ids ?? [])
      .filter((x) => imageFuncs.has(x)),
    [features, imageFuncs]);
  const labelOf = useCallback(
    (id: string) => funcIdChoices.find((c) => c.id === id)?.label ?? id,
    [funcIdChoices]);

  const pickFeature = useCallback((id: string, suggestNs = false) => {
    setFeature(id);
    // Manual mode has no account to read funcIds from, so the feature buttons
    // are the declaration: picking one sets what the location runs. Connected,
    // the account already said, and this stays a view.
    if (sourceMode === "manual") {
      const f = features.find((x) => x.id === id);
      const primary = (f?.func_ids ?? []).find((x) => imageFuncs.has(x));
      if (primary) setManual((m) => ({ ...m, func_ids: [primary] }));
    }
    const f = features.find((x) => x.id === id);
    if (!f || !suggestNs) return;
    setOptions((o) => {
      const ns = suggestNamespace(String(o.namespace ?? ""), f, features);
      // Same object when there is nothing to suggest: a fresh identity re-POSTs
      // /api/generate for options that did not change.
      return ns == null ? o : { ...o, namespace: ns };
    });
  }, [features, sourceMode, imageFuncs]);

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
  // Mirrors _sv_cfg in generate.py: domain and TLS secret are both mandatory
  // once SV is on (the secret even for plain HTTP, because crane validates it at
  // startup), the ingress itself is mandatory for an SV location, and NODEPORT
  // is incompatible because it sends crane to the cluster-scoped Node object.
  // Absent service_type means the backend default (CLUSTERIP), so only an
  // explicit NODEPORT is a conflict -- same `!= null` treatment the group
  // toggles above use.
  const svNodePortConflict = options.service_type != null
    && options.service_type !== "CLUSTERIP";
  // What the bundle is: flat YAML to kubectl apply, or the chart with a values
  // overlay. Both render the same objects -- the choice is how you install and
  // upgrade -- except that the chart is performance-only, so an SV location is
  // held to manifests and the segment says why instead of disappearing.
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

  const svOk = (options.sv_ingress
    ? !!txt("sv_subdomain") && !!txt("sv_tls_secret") && !svNodePortConflict
    : !svRequired);
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

  // -- what the current view is not showing ----------------------------------
  // The features this location carries, and the ones it carries that the tool
  // has no options for. Locations already run tdm/dataPublisher/delphix; naming
  // them is the honest version of a selector that quietly models five funcIds.
  const locFeatures = featuresOf(facts?.func_ids, features);
  // Which feature buttons read as unavailable. The rule -- including every case
  // where it must stay silent -- is in optionGroups, where it is tested as data.
  const unavailable = unavailableFeatures(
    sourceMode === "connect" && !!facts, locFeatures, features);
  // Groups a feature owns that already hold settings. Only consulted for a
  // feature we are about to make unreachable: those options are still generated,
  // so they have to be named rather than quietly disappearing behind a disabled
  // button.
  const setUnderFeature = useCallback((featureId: string) =>
    Object.values(GROUP_BY_ID)
      .filter((g) => g.features.includes(featureId) && g.detect(options))
      .map((g) => g.title), [options]);
  const locUnclaimed = unclaimedFuncIds(facts?.func_ids, features);
  // Configured, off screen, and still in the bundle -- reported beside the
  // preview, which is where "what is in this bundle" is read.
  const hiddenSet = setButHidden(options, feature);
  // Which groups are in use but not finished. Each group declares its own rule,
  // so a feature gaining required options later needs nothing here. Whether the
  // reason is on screen is what decides between the group showing its own error
  // and the download button having to explain itself.
  const incomplete = incompleteGroups(options, { sv: svRequired });
  const blockers = hiddenBlockers(incomplete, feature);

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
        serviceType={String(options.service_type ?? "CLUSTERIP")}
        svOn={!!options.sv_ingress}
        onUseSecret={(v) => set("use_secret", v)}
        onClusterRbac={(v) => set("cluster_rbac", v)}
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
        ok={svOk} nodePortConflict={svNodePortConflict}
        ctx={svCtx} rbac={svRbac} />
    ),
  };

  const filteredLocs = locations.filter((l) =>
    l.name.toLowerCase().includes(locFilter.toLowerCase()));

  return (
    <div className="min-h-screen">
      <header className="bg-white border-b border-slate-200 px-6 py-3 sticky top-0 z-10">
        <div className="max-w-screen-2xl mx-auto flex items-baseline gap-3">
          <h1 className="text-lg font-bold text-slate-900">
            <span className="text-bzm">BlazeMeter</span> OPL Generator
          </h1>
          <span className="text-xs text-slate-400">
            private-location Kubernetes / OpenShift manifests, from your real account
          </span>
          {who && <span className="ml-auto text-xs text-slate-500">
            {who.email} · key {who.keyId.slice(0, 8)}…</span>}
        </div>
      </header>

      <main className="max-w-screen-2xl mx-auto p-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-5">
          {/* 1 · Where the harbor id, ship id and token come from.
              Three steps folded into one: connected they are picked from the
              account, manually they are typed. Both end at the same three
              values, so they belong in one place rather than three that only
              one mode ever uses. */}
          <Section n={1} title="Agent details" done={!!facts && !!shipId}
            hint="harbor_id, ship_id and AUTH_TOKEN — read from your account, or entered by hand.">
            <div className="space-y-3">
              <SegmentedControl
                value={sourceMode}
                onChange={switchMode}
                options={[
                  { value: "connect", label: "Connect to BlazeMeter",
                    hint: "Pick a location and agent; the token is fetched on download." },
                  { value: "manual", label: "Enter values manually",
                    hint: "For an account you cannot reach — generation only, nothing is checked." },
                ]} />

              {sourceMode === "manual" ? (
                /* `choices` is filtered to the funcIds that change which images
                   the bundle names: here a funcId's only job is to pick images,
                   and functionalApi picks exactly what performance does. The
                   create-location form above still offers the full vocabulary,
                   because BlazeMeter does distinguish them when creating one. */
                <ManualSource
                  harborId={manual.harbor_id}
                  shipId={manual.ship_id}
                  authToken={raw("auth_token")}
                  guiIncomplete={guiIncomplete}
                  privateRegistry={!!options.private_registry}
                  onHarborId={(v) => setManual((m) => ({ ...m, harbor_id: v }))}
                  onShipId={(v) => setManual((m) => ({ ...m, ship_id: v }))}
                  onAuthToken={(v) => set("auth_token", v || null)} />
              ) : !sourceOpen ? (
                /* Settled: say what was chosen, and offer the way back. */
                <div className="flex items-start gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                  <p className="text-xs text-slate-600 grow">
                    <b>{locations.find((l) => l.id === harborId)?.name ?? harborId}</b>
                    {" · agent "}<code>{shipId}</code>
                    <span className="block text-slate-400">
                      {who?.email} · images: {facts?.images_source}
                    </span>
                  </p>
                  <Button kind="ghost" onClick={() => setSourceOpen(true)}>Change</Button>
                </div>
              ) : (
              <>
          <SubSection title="Connect" done={!!who}
            hint="API key stays on this machine; only used server-side.">
            {!who ? (
              <div className="space-y-3">
                {candidates.length > 0 && (
                  <Field label="Detected key files">
                    <select className={inputCls} value={keyPath}
                      onChange={(e) => setKeyPath(e.target.value)}>
                      {candidates.map((c) => (
                        <option key={c.path} value={c.path}>
                          {c.path} (id {c.key_id.slice(0, 8)}…)
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                <div className="flex gap-2 items-end">
                  <div className="grow">
                    <Field label="…or path to api-key.json">
                      <TextInput value={keyPath} onChange={setKeyPath} mono
                        placeholder="/path/to/api-key.json" />
                    </Field>
                  </div>
                  {/* A label, not a Button, so it cannot be `disabled` -- while a
                      connect is in flight it is taken out of reach instead, or a
                      second key could be picked mid-request. */}
                  <label className={"rounded-md px-3 py-1.5 text-sm font-medium border "
                    + "border-slate-300 text-slate-600 whitespace-nowrap "
                    + (connecting
                      ? "opacity-40 pointer-events-none"
                      : "hover:bg-slate-50 cursor-pointer")}>
                    Browse…
                    <input type="file" accept=".json,application/json" className="hidden"
                      onChange={async (e) => {
                        const f = e.target.files?.[0];
                        if (!f) return;
                        e.target.value = "";
                        setConnErr(null);
                        try {
                          const d = JSON.parse(await f.text());
                          if (!d.id || !d.secret) throw new Error();
                          connect({ id: d.id, secret: d.secret, save: saveKey });
                        } catch {
                          setConnErr(`${f.name} is not an api-key JSON ({"id": ..., "secret": ...})`);
                        }
                      }} />
                  </label>
                  <Button onClick={() => connect({ path: keyPath })}
                    disabled={!keyPath} busy={connecting}>
                    {connecting ? "Connecting…" : "Connect"}
                  </Button>
                </div>
                <Check label="Remember this key on this machine" checked={saveKey} onChange={setSaveKey}
                  hint="applies to Browse & paste — saved to ~/.config/bzm-opl-gen/api-key.json (chmod 600)" />
                <details className="text-sm">
                  <summary className="cursor-pointer text-slate-500">Paste a key instead</summary>
                  <div className="mt-2 space-y-2">
                    <Field label="Key ID">
                      <TextInput value={pasteId} onChange={setPasteId} mono /></Field>
                    <Field label="Secret">
                      <input type="password" className={inputCls + " font-mono text-xs"}
                        value={pasteSecret} onChange={(e) => setPasteSecret(e.target.value)} />
                    </Field>
                    <Button onClick={() => connect({ id: pasteId, secret: pasteSecret, save: saveKey })}
                      disabled={!pasteId || !pasteSecret} busy={connecting}>
                      {connecting ? "Connecting…" : "Connect"}
                    </Button>
                  </div>
                </details>
                <ErrorMsg msg={connErr} />
              </div>
            ) : (
              <p className="text-sm text-emerald-700">Connected as {who.email}</p>
            )}
          </SubSection>

          {/* 2 · Location */}
          {/* Picking and creating are one-of, and the hint says which you are
              in: with both on screen at once it was never obvious which half
              of the step you were meant to fill in. */}
          <SubSection title="Private location" done={!!harborId}
            hint={showCreateLoc
              ? "Creating a new location — the existing ones are hidden until you create or cancel. Cancel keeps whatever you had selected."
              : "The location = harbor (harbor_id). Its agents live in step 3 — create a new location only for a genuinely new place to run tests."}>
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <Field label="Account">
                  <SearchSelect
                    options={accounts.map((a) => ({ value: a.id, label: `${a.name} (${a.id})` }))}
                    value={accountId} disabled={!who}
                    onChange={(v) => setAccountId(Number(v))} />
                </Field>
                <Field label="Workspace">
                  <SearchSelect
                    options={workspaces.map((w) => ({ value: w.id, label: w.name }))}
                    value={workspaceId} disabled={!who || workspaces.length === 0}
                    onChange={(v) => setWorkspaceId(Number(v))} />
                </Field>
              </div>
              {/* The whole picking half, hidden while the create form is open.
                  Nothing here is unmounted state -- harborId lives above, so
                  cancelling comes back to the same selection. */}
              {!showCreateLoc && (
                <>
                  {locations.length > 8 && (
                    <TextInput value={locFilter} onChange={setLocFilter}
                      placeholder={`filter ${locations.length} locations…`} />
                  )}
                  <div className="max-h-56 overflow-y-auto border border-slate-200 rounded-md divide-y divide-slate-100">
                    {filteredLocs.map((l) => {
                      const labels = locLabels(l);
                      return (
                      <button key={l.id}
                        className={`w-full text-left px-3 py-2 text-sm hover:bg-slate-50 ${l.id === harborId ? "bg-bzm/10 border-l-4 border-bzm" : ""}`}
                        onClick={() => setHarborId(l.id)}>
                        <span className="font-medium">{l.name}</span>
                        {labels.map((label) => (
                          <span key={label}
                            className="ml-2 text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 bg-slate-100 text-slate-600">
                            {label}
                          </span>
                        ))}
                        <span className="text-xs text-slate-400 ml-2">
                          {l.funcIds?.slice(0, 4).join(", ")}{(l.funcIds?.length ?? 0) > 4 && "…"} ·
                          {" "}{l.slots} slot{l.slots === 1 ? "" : "s"} · {l.ships?.length ?? 0} agent(s)
                        </span>
                        {/* Said here rather than left to the badge's tooltip: this
                            is where the location is being chosen, and a tooltip is
                            invisible on touch and to the keyboard. */}
                        {labels.length > 1 && (
                          <span className="block text-[11px] text-amber-700 mt-0.5">
                            one agent for both — {KIND_COUPLING}
                          </span>
                        )}
                      </button>
                      );
                    })}
                    {who && filteredLocs.length === 0 && (
                      <p className="px-3 py-2 text-sm text-slate-400">no locations match</p>)}
                  </div>
                  {location && locLabels(location).length > 1 && (
                    <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                      <b>{location.name}</b> carries both performance and
                      service-virtualization features, so one agent serves both:{" "}
                      {KIND_COUPLING}. You can still generate for it — a location
                      per kind is what avoids the coupling.
                    </p>
                  )}
                  <Button kind="ghost" disabled={!who}
                    onClick={() => { setLocErr(null); setShowCreateLoc(true); }}>
                    + New location (new harbor_id)
                  </Button>
                </>
              )}
              <ErrorMsg msg={locErr} />
              {showCreateLoc && (
                <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
                  <p className="text-xs font-semibold text-slate-700">
                    New private location
                  </p>
                  <Field label={`Name (created in workspace: ${workspaces.find((w) => w.id === workspaceId)?.name ?? "?"})`}>
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
                  <div className="flex gap-2">
                    <Button disabled={!newLoc.name || !newLoc.workspace_id}
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
                  </div>
                </div>
              )}
            </div>
          </SubSection>

          {/* 3 · Agent */}
          {/* Same one-of as step 2, and it matters more here: the two paths have
              different consequences, so the hint names the one you are in. */}
          <SubSection title="Agent (ship)" done={!!shipId}
            hint={creatingShip
              ? "Creating a new agent identity (new ship_id + AUTH_TOKEN, same harbor) — what a new deployment needs. The token is fetched automatically on download."
              : "Reusing an identity means replacing the install it is already running. The location's existing agents are below; creating a new one instead is free."}>
            <div className="space-y-3">
              {creatingShip ? (
                <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
                  <p className="text-xs font-semibold text-slate-700">
                    New agent in this location
                  </p>
                  <Field label="Name">
                    <TextInput value={newShipName} onChange={setNewShipName}
                      placeholder="e.g. k8s-prod-cluster" />
                  </Field>
                  <div className="flex gap-2">
                    <Button disabled={!harborId || !newShipName}
                      onClick={async () => {
                        try {
                          const r = await api.createShip(harborId!, newShipName);
                          const ls = await api.locations(workspaceId!);
                          setLocations(ls); setShipId(r.ship.id); setNewShipName("");
                          setShowCreateShip(false);
                          api.facts(harborId!).then(setFacts).catch(() => {});
                        } catch (e) { setShipErr(String((e as Error).message)); }
                      }}>Create</Button>
                    {/* Only where there is a list to come back to -- see
                        creatingShip. The selection it comes back to is shipId,
                        which nothing in this form touches. */}
                    {ships.length > 0 && (
                      <Button kind="ghost"
                        onClick={() => { setShipErr(null); setShowCreateShip(false); }}>
                        Cancel
                      </Button>
                    )}
                  </div>
                </div>
              ) : (
                <>
                  <div>
                    <p className="text-xs font-medium text-slate-600 mb-1.5">
                      Reuse an existing agent identity (re-deploying / replacing it):
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {ships.map((s) => (
                        <button key={s.id}
                          className={`px-3 py-1.5 rounded-md border text-sm ${s.id === shipId ? "border-bzm bg-bzm/10 text-bzm-dark font-medium" : "border-slate-300 hover:bg-slate-50"}`}
                          onClick={() => setShipId(s.id)}>
                          {s.name || s.id}{" "}
                          <span className={`text-xs ${shipOnline(s) ? "text-emerald-600" : "text-slate-400"}`}>
                            ({shipOnline(s) ? "online" : s.state})
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                  {/* The hazard, stated against the identity you have selected --
                      which is why it belongs to this half of the step and not
                      the create form, where nothing is being reused. */}
                  {(() => {
                    const sel = ships.find((s) => s.id === shipId);
                    return sel && shipOnline(sel) ? (
                      <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                        <b>{sel.name}</b> is currently online — it's already running
                        somewhere. Deploying a second agent with the same identity will
                        conflict. Create a new agent unless you're replacing that install.
                      </p>
                    ) : null;
                  })()}
                  <Button kind="ghost" disabled={!harborId}
                    onClick={() => { setShipErr(null); setShowCreateShip(true); }}>
                    + New agent identity (recommended)
                  </Button>
                </>
              )}
              <ErrorMsg msg={shipErr} />
              {facts && (
                <p className="text-xs text-slate-500">
                  image inventory: {facts.images_source} · features: {facts.func_ids?.join(", ")}
                </p>
              )}
            </div>
          </SubSection>
              </>
              )}
            </div>
          </Section>

          {/* 2 · Configure */}
          <Section n={2} title="Configure"
            hint="Everything re-renders the preview live. Presets give a starting point.">
            <div className="space-y-4">
              <div className="flex gap-2 items-center flex-wrap">
                <span className="text-xs font-medium text-slate-500">Presets:</span>
                {profiles.map((p) => (
                  <button key={p.name}
                    className="px-2.5 py-1 rounded-full text-xs border border-slate-300 text-slate-600 hover:bg-slate-50"
                    onClick={() => applyProfile(p.name)}>
                    {p.name}
                  </button>
                ))}
                <span className="flex-1" />
                <Button kind="ghost" onClick={exportProfile}>Export</Button>
                <label className="rounded-md px-3 py-1.5 text-sm font-medium border border-slate-300 text-slate-600 hover:bg-slate-50 cursor-pointer">
                  Import
                  <input type="file" accept=".json" className="hidden"
                    onChange={(e) => e.target.files?.[0] && importProfile(e.target.files[0])} />
                </label>
              </div>

              {/* The feature in view. Served list, so a feature added to the
                  backend vocabulary appears here with nothing changed in this
                  file -- and one with no group tagged to it still shows the
                  any-deployment groups rather than an empty step. */}
              {features.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-slate-600 mb-1.5">
                    {sourceMode === "manual"
                      ? "What does this location run?"
                      : "Which feature are you configuring?"}
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    {features.map((f) => {
                      // Enabled is the unremarkable case and says nothing; only
                      // the absence is worth a word.
                      const off = unavailable.includes(f.id);
                      const stranded = off ? setUnderFeature(f.id) : [];
                      return (
                        <button key={f.id} disabled={off}
                          title={off ? "not enabled on this location" : undefined}
                          onClick={() => !off && pickFeature(f.id)}
                          className={"text-left px-3 py-2 rounded-md border text-sm "
                            + (f.id === feature
                              ? "border-bzm bg-bzm/10 text-bzm-dark font-medium"
                              : off
                                ? "border-slate-200 bg-slate-50 text-slate-400 cursor-not-allowed"
                                : "border-slate-300 hover:bg-slate-50")}>
                          {f.label}
                          {off && (
                            <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500 bg-slate-200 rounded px-1.5 py-0.5">
                              not enabled
                            </span>
                          )}
                          <span className="block text-[11px] font-normal text-slate-400">
                            {off ? "not enabled on this location" : f.hint}
                          </span>
                          {/* Disabling hides the view, not the effect: options
                              set under this feature are still generated. Left
                              unsaid they would ship from behind a button nobody
                              can open. */}
                          {stranded.length > 0 && (
                            <span className="block text-[11px] font-normal text-amber-700 mt-0.5">
                              {stranded.join(", ")} still set here — generated, but
                              no longer reachable.
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                  {/* Connected, this is a view over a fact the account already
                      settled. Manually there is no account, so the same buttons
                      are the declaration -- which is why the sentence under them
                      has to change with the mode rather than claim both. */}
                  {sourceMode === "manual" ? (
                    <>
                      <p className="text-[11px] text-slate-400 mt-1">
                        Declared here, not read from an account — it decides which
                        images the bundle names.
                      </p>
                      {/* Refinements, not a second copy of the question above: a
                          feature can stand for several funcIds that name
                          different images, and without these manual mode could
                          not ask for the grid proxy or the recorder at all. Only
                          shown when the chosen feature has more than one. */}
                      {featureFuncs(feature).length > 1 && (
                        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                          {featureFuncs(feature).slice(1).map((id) => (
                            <label key={id}
                              className="flex items-center gap-1.5 text-[11px] text-slate-600 cursor-pointer select-none">
                              <input type="checkbox" className="accent-bzm"
                                checked={manual.func_ids.includes(id)}
                                onChange={() => setManual((m) => ({
                                  ...m,
                                  func_ids: m.func_ids.includes(id)
                                    ? m.func_ids.filter((x) => x !== id)
                                    : [...m.func_ids, id],
                                }))} />
                              also runs {labelOf(id)}
                            </label>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="text-[11px] text-slate-400 mt-1">
                      The location's own features decide what the manifests
                      contain. Anything already set under a feature stays set and
                      stays in the bundle — including under one this location
                      does not run, which is why those say so rather than
                      silently dropping it.
                    </p>
                  )}
                  {locUnclaimed.length > 0 && (
                    <p className="text-[11px] text-slate-500 mt-1">
                      This location also runs{" "}
                      <span className="font-mono">{locUnclaimed.join(", ")}</span>{" "}
                      — there are no options here for those; nothing about them
                      is generated or removed.
                    </p>
                  )}
                </div>
              )}

              <label className="block">
                <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
                  Namespace
                  {namespaceOk
                    ? <span className="text-[10px] font-bold uppercase tracking-wide bg-emerald-100 text-emerald-700 rounded px-1.5 py-0.5">✓ set</span>
                    : <span className="text-[10px] font-bold uppercase tracking-wide bg-red-100 text-red-700 rounded px-1.5 py-0.5">required</span>}
                </span>
                <div className="relative">
                  <input className={inputCls + (namespaceOk ? " border-emerald-400" : " border-red-300")}
                    value={String(options.namespace ?? "")} placeholder="e.g. blazemeter"
                    onChange={(e) => set("namespace", e.target.value)} />
                  {namespaceOk && <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-emerald-500 text-sm">✓</span>}
                </div>
                <span className="text-[11px] text-slate-400">
                  the only required setting — every group below is optional. One
                  is suggested per feature, because {KIND_COUPLING}; anything
                  you type here outranks the suggestion.
                </span>
              </label>
              {facts && (
                <p className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-md px-3 py-2">
                  Images are selected automatically from the location's enabled
                  features ({facts.func_ids?.join(", ") || "performance"}) —
                  performance engines always; browser/grid, mock-service, SV and
                  recorder images only when that feature is on.
                </p>
              )}

              {/* Only the groups the feature in view owns, plus the ones that
                  apply to any deployment. A hidden group keeps its options --
                  nothing here calls disable -- so the manifests are the same
                  whichever feature is being looked at. */}
              <div className="border border-slate-200 rounded-xl divide-y divide-slate-100">
                {visibleGroups(feature).map((g) => (
                  <GroupRow key={g.id} group={g} on={grpOn[g.id]}
                    required={!!grpRequired[g.id]}
                    applies={appliesTo(g, features)}
                    onFlip={(v) => flipGroup(g.id, v)}>
                    {groupBody[g.id]}
                  </GroupRow>
                ))}
              </div>

              <details className="border border-dashed border-slate-300 rounded-xl bg-slate-50/60">
                <summary className="cursor-pointer px-4 py-2.5 text-xs font-medium text-slate-500">
                  Advanced — you should not need this
                </summary>
                <div className="px-4 pb-3 pt-1 grid grid-cols-2 gap-3">
                  <Field label="Security posture"
                    hint="the SCC-friendly posture works on both OpenShift and vanilla k8s; the pinned-UID variant exists only for clusters that reject it">
                    <select className={inputCls} value={String(options.platform)}
                      onChange={(e) => set("platform", e.target.value)}>
                      <option value="openshift">Unified SCC-friendly (recommended)</option>
                      <option value="k8s">Legacy pinned-UID k8s</option>
                    </select>
                  </Field>
                  {!openshift && (
                    <Field label="runAsUser / runAsGroup">
                      <input type="number" className={inputCls}
                        value={Number(options.run_as_user ?? 1337)}
                        onChange={(e) => set("run_as_user", Number(e.target.value))} />
                    </Field>
                  )}
                </div>
              </details>
            </div>
          </Section>

          {/* 3 · Download & verify */}
          <Section n={3} title="Download & verify">
            <div className="space-y-3">
              <SegmentedControl
                label="Output format"
                value={format}
                onChange={(v) => set("output_format", v)}
                options={[
                  {
                    value: "manifests",
                    label: "Kubernetes manifests",
                    hint: "Flat YAML you kubectl apply. Live-testable with bzm-opl-gen livetest.",
                  },
                  {
                    value: "helm",
                    label: "Helm chart",
                    hint: "The chart plus a values overlay from this account. helm install / upgrade.",
                    disabledReason: helmBlocked,
                  },
                ]} />
              <div className="flex gap-2 items-center">
                <Button disabled={!facts || !shipId || !!genErr || !svOk}
                  onClick={() => {
                    setDlErr(null);
                    downloadZip(facts!, { ...options, ship_id: shipId },
                                sourceMode === "connect")
                      .catch((e) => setDlErr(String(e.message)));
                  }}>
                  ⬇ Download bundle (.zip)
                </Button>
                <span className="text-xs text-slate-400">
                  {format === "helm"
                    ? "helm/ + bzm-opl-values.yaml + README"
                    : "manifests + README"}
                  {options.private_registry ? " + bzm-opl-image-mirror.sh" : ""};
                  {sourceMode === "connect"
                    ? " AUTH_TOKEN fetched on download"
                    : " AUTH_TOKEN as entered above"}
                </span>
              </div>
              {/* Why the button is disabled, when the reason is not on screen.
                  A disabled button whose cause is somewhere else on the page is
                  the failure the feature view is meant to remove, so the block
                  names the feature and offers the switch to it. A group in view
                  is absent from `blockers` -- it shows its own error. */}
              {blockers.map((g) => (
                <div key={g.id}
                  className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                  <p className="text-xs text-amber-800 grow">
                    <b>{appliesTo(g, features)}</b> is not finished, and its
                    settings are not in view: {g.requiredHint ?? g.hint}.
                  </p>
                  <Button kind="ghost" onClick={() => pickFeature(g.features[0])}>
                    Configure {appliesTo(g, features)}
                  </Button>
                </div>
              ))}
              <ErrorMsg msg={dlErr} />
              <div className="border-t border-slate-100 pt-3">
                {sourceMode === "manual" ? (
                  /* Watching needs the API this mode exists to do without. Said
                     plainly, with the way to get it, rather than a dead
                     checkbox. */
                  <p className="text-xs text-slate-500">
                    Agent status needs an API key — switch to
                    {" "}<b>Connect to BlazeMeter</b> above to watch this agent
                    come online, or check Settings → Private Locations in
                    BlazeMeter after applying.
                  </p>
                ) : (
                <>
                <div className="flex items-center gap-3">
                  <Check label="Watch agent status" checked={polling} onChange={setPolling}
                    hint="polls the BlazeMeter API every 10s — flips green once your applied deployment heartbeats" />
                  {status && (
                    <span className={`text-sm font-medium px-2.5 py-1 rounded-full ${status.online ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                      {status.online ? "● online" : "○ waiting"} — {status.state}
                      {status.heartbeat_age_s != null && `, heartbeat ${status.heartbeat_age_s}s ago`}
                    </span>
                  )}
                </div>
                {/* An idle agent says nothing about whether its virtual services
                    became reachable, which is the part of an SV deploy that
                    actually stalls. Only for an SV deployment -- the
                    performance panel is exactly as it was. */}
                {polling && !!txt("sv_ingress") && svMocks && (
                  <div className="mt-3">
                    <p className="text-xs font-medium text-slate-600 mb-1">
                      Virtual services in {svMocks.ns}
                    </p>
                    {svMocks.read.mocks.length > 0 ? (
                      <ul className="space-y-1.5">
                        {svMocks.read.mocks.map((m) => {
                          const chk = m.host ? svChecks[m.host] : undefined;
                          return (
                            <li key={`${m.name}-${m.port}`} className="text-[11px] text-slate-500">
                              <span className="font-medium text-slate-700">{m.name}</span>
                              <span className="text-slate-400">:{m.port}</span>
                              {m.host ? (
                                <>
                                  {" — "}
                                  <a className="text-bzm hover:underline font-mono break-all"
                                    href={`${svScheme}://${m.host}/`}
                                    target="_blank" rel="noreferrer">
                                    {svScheme}://{m.host}/
                                  </a>
                                  {/* The check is made from the machine serving
                                      this page, against the host shown above --
                                      never a second copy of that string. */}
                                  <button type="button" disabled={chk?.busy}
                                    onClick={() => checkEndpoint(m.host!)}
                                    className="ml-2 align-baseline rounded border border-slate-300 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40">
                                    {chk?.busy ? "checking…" : "check endpoint"}
                                  </button>
                                </>
                              ) : <> — set a wildcard domain to get the endpoint host</>}
                              {chk?.res && (
                                <p className={`mt-0.5 ${svCheckTone(chk.res)}`}>
                                  {chk.res.message}
                                  {chk.res.status !== "ok" && chk.res.detail && (
                                    <span className="block text-slate-400 font-mono break-all">
                                      {chk.res.detail}
                                    </span>
                                  )}
                                </p>
                              )}
                              {chk?.err && (
                                <p className="mt-0.5 text-red-600">{chk.err}</p>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      // "Nothing deployed" and "cannot look" are different
                      // answers, and the second must not read as the first.
                      <p className="text-[11px] text-slate-400">{svMocks.read.message}</p>
                    )}
                  </div>
                )}
                </>
                )}
              </div>
            </div>
          </Section>
        </div>

        <div className="space-y-2">
          {/* What is in the bundle from a feature that is not in view. Here
              rather than in step 4 because this is where "what does this bundle
              contain" is read, and the answer is more than the step above is
              currently showing. Each is a way back to it. */}
          {hiddenSet.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
              <p className="text-[11px] text-slate-600">
                <b>Also in this bundle</b>, set while configuring another
                feature:{" "}
                {hiddenSet.map((g, i) => (
                  <span key={g.id}>
                    {i > 0 && ", "}
                    <button className="text-bzm hover:underline font-medium"
                      onClick={() => pickFeature(g.features[0])}>
                      {g.title}
                    </button>
                    <span className="text-slate-400">
                      {" "}({appliesTo(g, features)})
                    </span>
                  </span>
                ))}
                . These still generate — the feature above is only what is on
                screen.
              </p>
            </div>
          )}
          <Preview files={files} activeFile={activeFile}
            setActiveFile={setActiveFile} genErr={genErr} />
        </div>
      </main>
    </div>
  );
}
