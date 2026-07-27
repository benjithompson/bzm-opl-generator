import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, downloadZip, saveBlob, Account, AgentStatus, Facts, GeneratedFile,
  FuncIdChoice, KeyCandidate, Location, Options, Ship, SvConstants, SvExposeIn,
  SvExposeOut, SvMocksOut, Workspace,
} from "./api";
import {
  Button, Check, ErrorMsg, Field, inputCls, JsonArea, SearchSelect, Section, Switch, TextInput,
} from "./components";
import { Preview } from "./Preview";
import { SvCtx } from "./SvPrereqs";
// The option groups of step 4: one declaration each (title, hint, the option
// keys it owns, and its detect/enable/disable), plus a body per group. This
// file only wires them -- what a group *is* lives in optionGroups.ts.
import {
  allGroupsOff, caModeOf, caModePatch, CaMode, detectGroups, enginePreset,
  GROUP_BY_ID, GroupFlags, GroupId, OPTION_GROUPS,
} from "./optionGroups";
import { CaGroup } from "./groups/CaGroup";
import { GroupRow } from "./groups/GroupRow";
import { ProxyGroup } from "./groups/ProxyGroup";
import { RegistryGroup } from "./groups/RegistryGroup";
import { SchedGroup } from "./groups/SchedGroup";
import { SecurityGroup } from "./groups/SecurityGroup";
import { SizingGroup } from "./groups/SizingGroup";
import { SvGroup } from "./groups/SvGroup";

// What the bundle deploys. Performance and service virtualization want separate
// agents: one agent serving both puts mocks and load engines in a single
// namespace, on a single slot budget, with a single restart lifecycle, so
// redeploying the performance agent takes the virtual services down with it.
// The kind only seeds defaults -- it gates nothing, because accounts already
// running a combined location have to keep working.
type DeployKind = "performance" | "sv";
const KIND_NAMESPACE: Record<DeployKind, string> = {
  performance: "blazemeter", sv: "blazemeter-sv",
};
const KIND_CHOICES: [DeployKind, string, string][] = [
  ["performance", "Performance agent", "load & functional tests — engines on demand"],
  ["sv", "Service-virtualization agent", "virtual services / mocks — needs an ingress"],
];

// Said in the location list, in the callout under it, and in the kind picker.
// One string because the coupling is one fact -- three near-copies is how the
// list ends up claiming something the callout no longer does.
const KIND_COUPLING =
  "mocks and load engines share a namespace, a slot budget and a restart "
  + "lifecycle, so redeploying the performance agent takes the virtual "
  + "services down with it";

// How a location's own funcIds are labelled in the list. "both" is the case
// worth naming: it deploys as one agent whichever kind you picked.
const LOC_KIND_BADGE: Record<"performance" | "sv" | "both", [string, string]> = {
  performance: ["performance", "bg-slate-100 text-slate-600"],
  sv: ["service virtualization", "bg-violet-100 text-violet-700"],
  both: ["performance + SV", "bg-amber-100 text-amber-700"],
};

export default function App() {
  // -- connection ------------------------------------------------------------
  const [candidates, setCandidates] = useState<KeyCandidate[]>([]);
  const [keyPath, setKeyPath] = useState("");
  const [pasteId, setPasteId] = useState("");
  const [pasteSecret, setPasteSecret] = useState("");
  const [saveKey, setSaveKey] = useState(false);
  const [who, setWho] = useState<{ email: string; keyId: string } | null>(null);
  const [connErr, setConnErr] = useState<string | null>(null);

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
  // Picked before a location, and only ever a source of defaults -- nothing
  // downstream reads it as a constraint. In particular svRequired stays derived
  // from the selected location's funcIds, so an SV location chosen under the
  // performance kind still demands an ingress.
  const [kind, setKind] = useState<DeployKind>("performance");

  // -- agent -----------------------------------------------------------------
  const [shipId, setShipId] = useState<string | null>(null);
  const [newShipName, setNewShipName] = useState("");
  const [shipErr, setShipErr] = useState<string | null>(null);
  const [facts, setFacts] = useState<Facts | null>(null);

  // -- options / preview -----------------------------------------------------
  const [defaults, setDefaults] = useState<Options>({});
  const [svConst, setSvConst] = useState<SvConstants>(
    { func_ids: [], ingress_types: [], backends: {} });
  const [options, setOptions] = useState<Options>({ namespace: "blazemeter" });
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
  // is the same staleness the sv-expose block below refuses to show.
  const [svMocks, setSvMocks] =
    useState<{ ns: string; read: SvMocksOut } | null>(null);
  const [polling, setPolling] = useState(false);
  const [dlErr, setDlErr] = useState<string | null>(null);

  // -- sv-expose (the one thing here that reads a cluster) --------------------
  // Deliberately only ever set by the button in step 6: no other part of this
  // app may start depending on kubectl existing.
  const [svExpose, setSvExpose] = useState<SvExposeOut | null>(null);
  const [svExposeBusy, setSvExposeBusy] = useState(false);
  const [svExposeErr, setSvExposeErr] = useState<string | null>(null);
  // The preview effect below must not take svExpose as a dependency -- that
  // would re-POST /api/generate every time the namespace is read. Synced here
  // on every render rather than beside each setSvExpose, so adding a fifth
  // place that sets it cannot leave the two out of step.
  const svExposeRef = useRef(svExpose);
  svExposeRef.current = svExpose;

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
  }, []);

  const connect = async (body: Parameters<typeof api.keySet>[0]) => {
    setConnErr(null);
    try {
      const r = await api.keySet(body);
      setWho({ email: r.user.email, keyId: r.key_id });
      const accts = await api.accounts();
      setAccounts(accts);
      setAccountId(r.default_account_id ?? accts[0]?.id ?? null);
    } catch (e) { setConnErr(String((e as Error).message)); }
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
  const locKind = useCallback((l: Location) => {
    const ids = l.funcIds ?? [];
    if (!svConst.func_ids.length || !ids.length) return null;
    const sv = ids.some((f) => svConst.func_ids.includes(f));
    const perf = ids.some((f) => !svConst.func_ids.includes(f));
    return sv && perf ? "both" as const : sv ? "sv" as const : "performance" as const;
  }, [svConst]);

  useEffect(() => {
    setShipId(null); setFacts(null); setStatus(null);
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
        // bzm_sv_expose.yaml is previewed alongside these but is not one of
        // them, so it has to survive the re-render that follows any option
        // edit -- otherwise reading the namespace, then typing, yanks the pane
        // back to the first manifest.
        setActiveFile((a) => (a && (r.files.some((f) => f.name === a)
          || (svExposeRef.current?.files ?? []).some((f) => f.name === a))
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
  // Clearing a field whose key is absent from DEFAULT_OPTIONS has to *remove*
  // it, not null it: generate() spreads the options over the defaults and
  // profile.json dumps whatever survives, so an explicit null adds a key that
  // was never there and the bundle stops being byte-identical to one generated
  // without the field. Every other `v || null` field in this form is safe only
  // because its key already has a default to overwrite.
  const setOptional = useCallback((k: string, v: string) =>
    setOptions((o) => {
      if (v) return { ...o, [k]: v };
      const { [k]: _dropped, ...rest } = o;
      return rest;
    }), []);

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
  // Seeding happens here rather than in an effect on `kind`: an effect would
  // also fire when svConst arrives mid-session and undo a namespace the user had
  // already typed, and would have to run once on mount -- which is exactly where
  // the performance flow has to stay untouched.
  const pickKind = (k: DeployKind) => {
    setKind(k);
    // The SV funcIds are whatever generate.SV_FUNC_IDS says they are; a second
    // list here is what /api/sv-constants exists to prevent. Empty only in the
    // window before that fetch lands, where the old seed is the safer answer.
    setNewLoc((n) => ({ ...n, func_ids: k === "sv" && svConst.func_ids.length
      ? [...svConst.func_ids] : ["performance"] }));
    // Distinct namespaces are the point of the split, but only a namespace still
    // holding a kind default gets rewritten -- anything typed outranks the seed.
    setOptions((o) => {
      const ns = String(o.namespace ?? "").trim();
      const seeded = !ns || Object.values(KIND_NAMESPACE).includes(ns);
      return seeded ? { ...o, namespace: KIND_NAMESPACE[k] } : o;
    });
    // Not flipped back off for the performance kind: that would wipe a domain
    // and TLS secret already typed, and svRequired flips it straight back on for
    // an SV location anyway.
    if (k === "sv") flipGroup("sv", true);
  };

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

  // -- sv-expose -------------------------------------------------------------
  // Renders the pair that actually routes, from the mocks running in the
  // namespace. The server answers 200 with a reason whenever it cannot read the
  // cluster, so the only thing caught here is a genuinely broken request.
  const svExposeReady = !!txt("sv_subdomain") && namespaceOk;
  // The exact request, derived rather than assembled at the call site: the
  // staleness effect below depends on it, so a field added here cannot be
  // forgotten there.
  const svExposeReq: SvExposeIn = useMemo(() => ({
    namespace: txt("namespace"),
    sv_subdomain: txt("sv_subdomain") || null,
    sv_tls_secret: txt("sv_tls_secret") || null,
    sv_ingress_class: txt("sv_ingress_class") || null,
  }), [txt]);
  // What was rendered is a snapshot of one namespace, taken with the values
  // that were on screen. Editing any of them makes it stale, and stale YAML in
  // the preview is worse than none -- it would still carry the old ingress
  // class or host while the fields say otherwise. Drop it and make them read
  // again.
  useEffect(() => {
    setSvExpose(null); setSvExposeErr(null);
  }, [svExposeReq]);
  const readSvExpose = async () => {
    setSvExposeBusy(true); setSvExposeErr(null);
    try {
      const r = await api.svExpose(svExposeReq);
      setSvExpose(r);
      if (r.files[0]) setActiveFile(r.files[0].name);
    } catch (e) {
      setSvExpose(null);
      setSvExposeErr(String((e as Error).message));
    } finally { setSvExposeBusy(false); }
  };
  const downloadSvExpose = () => {
    const f = svExpose?.files[0];
    if (!f) return;
    saveBlob(new Blob([f.content], { type: "text/yaml" }), f.name);
  };
  // Same pane as the manifests, per the preview being where generated YAML is
  // read in this app.
  const previewFiles = useMemo(
    () => (svExpose?.files.length ? [...files, ...svExpose.files] : files),
    [files, svExpose]);

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
        emitLimitRange={!!options.emit_limitrange}
        onLimits={(cpu, mem) => setOptions((o) => ({
          ...o, engine_cpu_limit: cpu, engine_mem_limit: mem }))}
        onCpuLimit={(v) => set("engine_cpu_limit", v)}
        onMemLimit={(v) => set("engine_mem_limit", v)}
        onEmitLimitRange={(v) => set("emit_limitrange", v)} />
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
          {/* 1 · Connect */}
          <Section n={1} title="Connect" done={!!who}
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
                  <label className="rounded-md px-3 py-1.5 text-sm font-medium border border-slate-300 text-slate-600 hover:bg-slate-50 cursor-pointer whitespace-nowrap">
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
                  <Button onClick={() => connect({ path: keyPath })} disabled={!keyPath}>
                    Connect
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
                      disabled={!pasteId || !pasteSecret}>Connect</Button>
                  </div>
                </details>
                <ErrorMsg msg={connErr} />
              </div>
            ) : (
              <p className="text-sm text-emerald-700">Connected as {who.email}</p>
            )}
          </Section>

          {/* 2 · Location */}
          <Section n={2} title="Private location" done={!!harborId}
            hint="The location = harbor (harbor_id). Its agents live in step 3 — create a new location only for a genuinely new place to run tests.">
            <div className="space-y-3">
              <div>
                <p className="text-xs font-medium text-slate-600 mb-1.5">
                  What does this bundle deploy?
                </p>
                <div className="grid grid-cols-2 gap-2">
                  {KIND_CHOICES.map(([k, label, hint]) => (
                    <button key={k} onClick={() => pickKind(k)}
                      className={`text-left px-3 py-2 rounded-md border text-sm ${k === kind ? "border-bzm bg-bzm/10 text-bzm-dark font-medium" : "border-slate-300 hover:bg-slate-50"}`}>
                      {label}
                      <span className="block text-[11px] font-normal text-slate-400">
                        {hint}
                      </span>
                    </button>
                  ))}
                </div>
                <p className="text-[11px] text-slate-400 mt-1">
                  One agent per kind — with one agent serving both,{" "}
                  {KIND_COUPLING}. This only picks defaults; any location below
                  still works for either.
                </p>
              </div>
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
              {locations.length > 8 && (
                <TextInput value={locFilter} onChange={setLocFilter}
                  placeholder={`filter ${locations.length} locations…`} />
              )}
              <div className="max-h-56 overflow-y-auto border border-slate-200 rounded-md divide-y divide-slate-100">
                {filteredLocs.map((l) => {
                  const k = locKind(l);
                  return (
                  <button key={l.id}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-slate-50 ${l.id === harborId ? "bg-bzm/10 border-l-4 border-bzm" : ""}`}
                    onClick={() => setHarborId(l.id)}>
                    <span className="font-medium">{l.name}</span>
                    {k && (
                      <span className={`ml-2 text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 ${LOC_KIND_BADGE[k][1]}`}>
                        {LOC_KIND_BADGE[k][0]}
                      </span>
                    )}
                    <span className="text-xs text-slate-400 ml-2">
                      {l.funcIds?.slice(0, 4).join(", ")}{(l.funcIds?.length ?? 0) > 4 && "…"} ·
                      {" "}{l.slots} slot{l.slots === 1 ? "" : "s"} · {l.ships?.length ?? 0} agent(s)
                    </span>
                    {/* Said here rather than left to the badge's tooltip: this
                        is where the location is being chosen, and a tooltip is
                        invisible on touch and to the keyboard. */}
                    {k === "both" && (
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
              {location && locKind(location) === "both" && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                  <b>{location.name}</b> carries both performance and
                  service-virtualization features, so one agent serves both:{" "}
                  {KIND_COUPLING}. You can still generate for it — a location
                  per kind is what avoids the coupling.
                </p>
              )}
              <ErrorMsg msg={locErr} />
              {!showCreateLoc ? (
                <Button kind="ghost" onClick={() => setShowCreateLoc(true)} disabled={!who}>
                  + New location (new harbor_id)
                </Button>
              ) : (
                <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
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
                    <Button kind="ghost" onClick={() => setShowCreateLoc(false)}>Cancel</Button>
                  </div>
                </div>
              )}
            </div>
          </Section>

          {/* 3 · Agent */}
          <Section n={3} title="Agent (ship)" done={!!shipId}
            hint="A new deployment needs a NEW agent identity (new ship_id + AUTH_TOKEN, same harbor). The token is fetched automatically on download.">
            <div className="space-y-3">
              <div className="flex gap-2 items-end">
                <div className="grow">
                  <Field label="Create a new agent in this location (recommended)">
                    <TextInput value={newShipName} onChange={setNewShipName}
                      placeholder="e.g. k8s-prod-cluster" />
                  </Field>
                </div>
                <Button disabled={!harborId || !newShipName}
                  onClick={async () => {
                    try {
                      const r = await api.createShip(harborId!, newShipName);
                      const ls = await api.locations(workspaceId!);
                      setLocations(ls); setShipId(r.ship.id); setNewShipName("");
                      api.facts(harborId!).then(setFacts).catch(() => {});
                    } catch (e) { setShipErr(String((e as Error).message)); }
                  }}>Create</Button>
              </div>
              {ships.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-slate-600 mb-1.5">
                    …or reuse an existing agent identity (re-deploying / replacing it):
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
              )}
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
              <ErrorMsg msg={shipErr} />
              {facts && (
                <p className="text-xs text-slate-500">
                  image inventory: {facts.images_source} · features: {facts.func_ids?.join(", ")}
                </p>
              )}
            </div>
          </Section>

          {/* 4 · Configure */}
          <Section n={4} title="Configure"
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
                  the only required setting — every group below is optional
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

              <div className="border border-slate-200 rounded-xl divide-y divide-slate-100">
                {OPTION_GROUPS.map((g) => (
                  <GroupRow key={g.id} group={g} on={grpOn[g.id]}
                    required={!!grpRequired[g.id]}
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

          {/* 5 · Download & verify */}
          <Section n={5} title="Download & verify">
            <div className="space-y-3">
              <div className="flex gap-2 items-center">
                <Button disabled={!facts || !shipId || !!genErr || !svOk}
                  onClick={() => {
                    setDlErr(null);
                    downloadZip(facts!, { ...options, ship_id: shipId })
                      .catch((e) => setDlErr(String(e.message)));
                  }}>
                  ⬇ Download bundle (.zip)
                </Button>
                <span className="text-xs text-slate-400">
                  manifests + README{options.private_registry ? " + bzm-opl-image-mirror.sh" : ""};
                  AUTH_TOKEN fetched on download
                </span>
              </div>
              <ErrorMsg msg={dlErr} />
              <div className="border-t border-slate-100 pt-3">
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
                      <ul className="space-y-0.5">
                        {svMocks.read.mocks.map((m) => (
                          <li key={`${m.name}-${m.port}`} className="text-[11px] text-slate-500">
                            <span className="font-medium text-slate-700">{m.name}</span>
                            <span className="text-slate-400">:{m.port}</span>
                            {m.host
                              ? <> — <code className="text-slate-700">{m.host}</code></>
                              : <> — set a wildcard domain to get the endpoint host</>}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      // "Nothing deployed" and "cannot look" are different
                      // answers, and the second must not read as the first.
                      <p className="text-[11px] text-slate-400">{svMocks.read.message}</p>
                    )}
                  </div>
                )}
              </div>
            </div>
          </Section>

          {/* 6 · Expose virtual services */}
          <Section n={6} title="Expose virtual services"
            hint="Once the virtual services are deployed: reads them from the namespace with the kubectl/oc on THIS machine and renders the Service+Ingress pair that routes. Optional — nothing above needs a cluster.">
            <div className="space-y-3">
              <Field label="Ingress class"
                hint="the class put on the Ingress we own — defaults to nginx; on OpenShift use openshift-default and no nginx alias is needed. Saved into the profile, so the CLI picks it up too.">
                <TextInput mono placeholder="nginx"
                  value={String(options.sv_ingress_class ?? "")}
                  onChange={(v) => setOptional("sv_ingress_class", v)} />
              </Field>
              <div className="flex gap-2 items-center">
                <Button disabled={!svExposeReady || svExposeBusy}
                  onClick={readSvExpose}>
                  {svExposeBusy ? "Reading namespace…" : "Read namespace & render"}
                </Button>
                <span className="text-xs text-slate-400">
                  namespace <code>{String(options.namespace ?? "")}</code> via your
                  current kube context
                </span>
              </div>
              {!svExposeReady && (
                <p className="text-[11px] text-amber-700">
                  Set the wildcard domain in step 4 first — the endpoint host is
                  &lt;service&gt;-&lt;port&gt;-&lt;namespace&gt;.&lt;domain&gt;, so
                  there is nothing to route without it.
                </p>
              )}
              <ErrorMsg msg={svExposeErr} />
              {svExpose && (svExpose.status === "ok" ? (
                <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 space-y-2">
                  <p className="text-xs text-emerald-800">{svExpose.message}</p>
                  <ul className="text-[11px] text-slate-600 font-mono space-y-0.5">
                    {svExpose.mocks.map((m) => (
                      <li key={m.name}>{`${m.name}:${m.port} → ${m.host ?? "(set a wildcard domain)"}`}</li>
                    ))}
                  </ul>
                  <div className="flex gap-2 items-center">
                    <Button onClick={downloadSvExpose}>
                      ⬇ Download {svExpose.files[0]?.name}
                    </Button>
                    <span className="text-xs text-slate-400">
                      previewed on the right, alongside the manifests
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-500 font-mono break-all">
                    {svExpose.detail}
                  </p>
                </div>
              ) : (
                // Never a dead panel: each reason says what happened, and all of
                // them hand over the same command to run where the cluster IS
                // reachable, prefilled with what is on screen.
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 space-y-2">
                  <p className="text-xs text-amber-800">{svExpose.message}</p>
                  {svExpose.detail && (
                    <p className="text-[11px] text-amber-700 font-mono break-all">
                      {svExpose.detail}
                    </p>
                  )}
                  <p className="text-[11px] text-slate-600">
                    Run this wherever you do have access to the cluster:
                  </p>
                  <div className="flex gap-2 items-start">
                    <code className="grow min-w-0 text-[11px] font-mono bg-slate-900 text-slate-100 rounded px-2 py-1.5 break-all">
                      {svExpose.command}
                    </code>
                    <Button kind="ghost"
                      onClick={() => navigator.clipboard.writeText(svExpose.command)}>
                      copy
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </Section>
        </div>

        <Preview files={previewFiles} activeFile={activeFile}
          setActiveFile={setActiveFile} genErr={genErr} />
      </main>
    </div>
  );
}
