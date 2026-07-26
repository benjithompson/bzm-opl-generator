import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, downloadZip, saveBlob, Account, AgentStatus, Facts, GeneratedFile,
  FuncIdChoice, KeyCandidate, Location, Options, Ship, SvConstants, SvExposeIn,
  SvExposeOut, Workspace,
} from "./api";
import {
  Button, Check, ErrorMsg, Field, inputCls, JsonArea, SearchSelect, Section, Switch, TextInput,
} from "./components";
import { Preview } from "./Preview";
import { SvCtx, SvPrereqs } from "./SvPrereqs";

// Display names only. The set of values is served from generate.SV_INGRESS_TYPES
// -- an unlabelled backend falls back to its raw name and still appears, which
// is the failure mode worth having.
const SV_INGRESS_LABELS: Record<string, string> = {
  nginx: "NGINX", istio: "Istio", contour: "Contour", openshift: "OpenShift Route",
};


// Engine pod limits. Standard is BlazeMeter's own sizing; Small is validated
// to run real tests and fits dev clusters (CRC/minikube) that can't spare 8Gi.
const ENGINE_SIZES = [
  { id: "small", cpu: "1", mem: "4Gi", label: "Small — 1 CPU / 4Gi (dev clusters, light tests)" },
  { id: "standard", cpu: "2", mem: "8Gi", label: "Standard — 2 CPU / 8Gi (BlazeMeter default)" },
  { id: "large", cpu: "4", mem: "16Gi", label: "Large — 4 CPU / 16Gi (heavy scripts)" },
];

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
  type CaMode = "none" | "existing" | "inline" | "inject";
  const [options, setOptions] = useState<Options>({ namespace: "blazemeter" });
  const [profiles, setProfiles] = useState<{ name: string; options: Options }[]>([]);
  const [files, setFiles] = useState<GeneratedFile[]>([]);
  const [genErr, setGenErr] = useState<string | null>(null);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [status, setStatus] = useState<AgentStatus | null>(null);
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

  // agent status polling
  useEffect(() => {
    if (!polling || !harborId || !shipId) return;
    let live = true;
    const tick = async () => {
      try { const s = await api.status(harborId, shipId); if (live) setStatus(s); }
      catch { /* keep last */ }
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
  const caMode: CaMode = options.ca_existing_configmap != null ? "existing"
    : options.ca_bundle != null ? "inline"
    : options.ca_openshift_inject ? "inject" : "none";
  const setCaMode = (m: CaMode) => setOptions((o) => ({
    ...o,
    ca_existing_configmap: m === "existing" ? (o.ca_existing_configmap ?? "") : null,
    ca_configmap_key: m === "existing" ? o.ca_configmap_key : null,
    ca_bundle: m === "inline" ? (o.ca_bundle ?? "") : null,
    ca_openshift_inject: m === "inject",
  }));

  // Engine size is a dropdown of known-good shapes; the preset is derived from
  // the two limits rather than stored, so an imported/preset config lands on
  // the right entry and anything unrecognised shows as Custom.
  const sizePreset = ENGINE_SIZES.find(
    (s) => s.cpu === options.engine_cpu_limit && s.mem === options.engine_mem_limit,
  )?.id ?? "custom";

  // Toggle-to-enable option groups: OFF hides the fields AND wipes their
  // options, so nothing hidden ever reaches the manifests. Auto-flips on when
  // a preset/import brings values in.
  type GroupId = "registry" | "proxy" | "ca" | "sched" | "sizing" | "security" | "sv";
  const [grpOn, setGrpOn] = useState<Record<GroupId, boolean>>({
    registry: false, proxy: false, ca: false, sched: false, sizing: false,
    security: false, sv: false,
  });
  // An SV location cannot be generated without this group: the manifests would
  // apply cleanly and then stall at WAITING_FOR_DOMAIN, so the backend refuses.
  // Surface it as required rather than letting the user find out later. The
  // funcIds come from generate.SV_FUNC_IDS over /api/sv-constants rather than a
  // copy here, so adding one cannot leave the UI silently disagreeing.
  const svRequired = !!facts?.func_ids?.some(
    (f) => svConst.func_ids.includes(f));
  useEffect(() => {
    setGrpOn((g) => ({
      registry: g.registry || !!(options.private_registry || options.pull_secret || options.registry_auth),
      proxy: g.proxy || !!options.proxy,
      ca: g.ca || caMode !== "none",
      sched: g.sched || !!(options.tolerations || options.node_selector),
      sizing: g.sizing || !!(options.engine_cpu_limit || options.engine_mem_limit
        || options.emit_limitrange),
      security: g.security || options.use_secret === false || !!options.cluster_rbac ||
        (options.service_type != null && options.service_type !== "CLUSTERIP"),
      sv: g.sv || !!options.sv_ingress || svRequired,
    }));
  }, [options, caMode, svRequired]);
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
    if (id === "ca") { setCaMode(on ? "existing" : "none"); return; }
    if (on) {
      if (id === "sizing" && sizePreset === "custom") {
        const d = ENGINE_SIZES.find((s) => s.id === "standard")!;
        setOptions((o) => ({ ...o, engine_cpu_limit: d.cpu, engine_mem_limit: d.mem }));
      }
      // The ingress path only works on CLUSTERIP; NODEPORT would send crane to
      // the cluster-scoped Node object instead.
      if (id === "sv") {
        setOptions((o) => ({ ...o, sv_ingress: o.sv_ingress || "nginx",
          service_type: "CLUSTERIP" }));
      }
      return;
    }
    setOptions((o) => {
      const w = { ...o };
      if (id === "registry") Object.assign(w, { private_registry: null, pull_secret: null, registry_auth: false });
      if (id === "proxy") w.proxy = null;
      if (id === "sched") Object.assign(w, { tolerations: null, node_selector: null });
      if (id === "sizing") Object.assign(w, { engine_cpu_limit: null, engine_mem_limit: null,
        emit_limitrange: false });
      if (id === "security") Object.assign(w, { use_secret: true, cluster_rbac: false, service_type: "CLUSTERIP" });
      if (id === "sv") Object.assign(w, { sv_ingress: null, sv_subdomain: null,
        sv_tls_secret: null, sv_istio_gateway: null });
      return w;
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

  // One way to read a text option. Written out per-site, the `.trim()` was
  // getting forgotten -- an ingress name pasted with a trailing space missed
  // the SV_PREREQS lookup and the panel silently lost its prose.
  const txt = useCallback(
    (k: string) => String(options[k] ?? "").trim(), [options]);

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

              <div className="px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <Switch on={grpOn.registry} onChange={(v) => flipGroup("registry", v)} />
                  <div className="min-w-0">
                    <p className={`text-sm font-medium ${grpOn.registry ? "text-slate-900" : "text-slate-500"}`}>Private registry</p>
                    <p className="text-[11px] text-slate-400 truncate">mirror images into your own registry (air-gapped)</p>
                  </div>
                </div>
                {grpOn.registry && (
                <div className="mt-3 pl-12 space-y-2">
                  <Field label="Registry" hint="sets DOCKER_REGISTRY + IMAGE_OVERRIDES, disables auto-update, emits bzm-opl-image-mirror.sh">
                    <TextInput mono value={String(options.private_registry ?? "")}
                      placeholder="registry.corp.com/bzm"
                      onChange={(v) => set("private_registry", v || null)} />
                  </Field>
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="imagePullSecret name"
                      hint="existing docker-registry Secret in the namespace; lets the kubelet pull the crane image from your registry">
                      <TextInput mono value={String(options.pull_secret ?? "")}
                        onChange={(v) => set("pull_secret", v || null)} />
                    </Field>
                    <Check label="Registry auth env stubs"
                      hint="commented DOCKER_REGISTRY_USERNAME/PASSWORD"
                      checked={Boolean(options.registry_auth)}
                      onChange={(v) => set("registry_auth", v)} />
                  </div>
                </div>
                )}
              </div>

              <div className="px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <Switch on={grpOn.proxy} onChange={(v) => flipGroup("proxy", v)} />
                  <div className="min-w-0">
                    <p className={`text-sm font-medium ${grpOn.proxy ? "text-slate-900" : "text-slate-500"}`}>HTTP(S) proxy</p>
                    <p className="text-[11px] text-slate-400 truncate">egress via a corporate proxy, optional authentication</p>
                  </div>
                </div>
                {grpOn.proxy && (
                <div className="mt-3 pl-12 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="HTTP proxy">
                      <TextInput mono placeholder="http://proxy:3128"
                        value={String(proxyOpt.http ?? "")}
                        onChange={(v) => setProxy("http", v)} />
                    </Field>
                    <Field label="HTTPS proxy">
                      <TextInput mono placeholder="http://proxy:3128"
                        value={String(proxyOpt.https ?? "")}
                        onChange={(v) => setProxy("https", v)} />
                    </Field>
                    <Field label="Username" hint="optional — proxy auth">
                      <TextInput mono value={String(proxyOpt.username ?? "")}
                        onChange={(v) => setProxy("username", v)} />
                    </Field>
                    <Field label="Password">
                      <TextInput mono value={String(proxyOpt.password ?? "")}
                        onChange={(v) => setProxy("password", v)} />
                    </Field>
                  </div>
                  <Field label="NO_PROXY">
                    <TextInput mono placeholder="kubernetes.default,127.0.0.1,localhost"
                      value={String(proxyOpt.no_proxy ?? "")}
                      onChange={(v) => setProxy("no_proxy", v)} />
                  </Field>
                  <p className="text-[11px] text-slate-400">
                    BlazeMeter has no separate proxy-auth env vars — credentials are
                    URL-encoded into the proxy URL (user:pass@host). With
                    "AUTH_TOKEN in a Secret" on, the credentialed proxy URLs move
                    into the Secret instead of the ConfigMap.
                  </p>
                </div>
                )}
              </div>

              <div className="px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <Switch on={grpOn.ca} onChange={(v) => flipGroup("ca", v)} />
                  <div className="min-w-0">
                    <p className={`text-sm font-medium ${grpOn.ca ? "text-slate-900" : "text-slate-500"}`}>Custom CA trust</p>
                    <p className="text-[11px] text-slate-400 truncate">TLS-intercepting proxy / private CAs — mounted into crane + engines</p>
                  </div>
                </div>
                {grpOn.ca && (
                <div className="mt-3 pl-12 space-y-2">
                  <div className="space-y-1.5 text-sm">
                    {([
                      ["existing", "Reference an existing ConfigMap (recommended)",
                        "your platform/security team owns and rotates the trust bundle (e.g. via trust-manager); manifests only reference it"],
                      ["inline", "Paste PEM — generator creates the ConfigMap",
                        "you own the bundle; rotation means regenerating and re-applying"],
                      ["inject", "OpenShift cluster trust injection",
                        "empty ConfigMap labeled inject-trusted-cabundle; the cluster injects and rotates ca-bundle.crt — OpenShift only"],
                    ] as [CaMode, string, string][]).map(([m, label, hint]) => (
                      <label key={m} className="flex items-start gap-2 cursor-pointer select-none">
                        <input type="radio" name="ca-mode" className="mt-1 accent-bzm"
                          checked={caMode === m} onChange={() => setCaMode(m)} />
                        <span>{label}
                          <span className="block text-[11px] text-slate-400">{hint}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                  {caMode === "existing" && (
                    <div className="grid grid-cols-2 gap-2">
                      <Field label="ConfigMap name">
                        <TextInput mono placeholder="corp-trust-bundle"
                          value={String(options.ca_existing_configmap ?? "")}
                          onChange={(v) => set("ca_existing_configmap", v)} />
                      </Field>
                      <Field label="Bundle key" hint="file key inside the ConfigMap">
                        <TextInput mono placeholder="ca-bundle.crt"
                          value={String(options.ca_configmap_key ?? "")}
                          onChange={(v) => set("ca_configmap_key", v || null)} />
                      </Field>
                    </div>
                  )}
                  {caMode === "inline" && (
                    <Field label="CA bundle (PEM)">
                      <textarea className={inputCls + " font-mono text-[10px]"} rows={3}
                        placeholder="-----BEGIN CERTIFICATE-----"
                        value={String(options.ca_bundle ?? "")}
                        onChange={(e) => set("ca_bundle", e.target.value)} />
                    </Field>
                  )}
                  <p className="text-[11px] text-slate-400">
                    Mounted read-only at /var/cm in crane; engines get the same
                    ConfigMap via KUBERNETES_CA_BUNDLE_MOUNT, and
                    REQUESTS_CA_BUNDLE / AWS_CA_BUNDLE point at it.
                  </p>
                </div>
                )}
              </div>

              <div className="px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <Switch on={grpOn.sched} onChange={(v) => flipGroup("sched", v)} />
                  <div className="min-w-0">
                    <p className={`text-sm font-medium ${grpOn.sched ? "text-slate-900" : "text-slate-500"}`}>Scheduling</p>
                    <p className="text-[11px] text-slate-400 truncate">tolerations + nodeSelector for crane & engines</p>
                  </div>
                </div>
                {grpOn.sched && (
                <div className="mt-3 pl-12 space-y-2">
                  <JsonArea label="Tolerations (JSON list — crane pod + engines)"
                    value={options.tolerations}
                    placeholder='[{"key":"lifecycle","operator":"Equal","value":"spot","effect":"NoSchedule"}]'
                    onValid={(v) => set("tolerations", v)} />
                  <JsonArea label="Node selector (JSON object)" rows={2}
                    value={options.node_selector}
                    placeholder='{"pool":"loadtest"}'
                    onValid={(v) => set("node_selector", v)} />
                </div>
                )}
              </div>

              <div className="px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <Switch on={grpOn.sizing} onChange={(v) => flipGroup("sizing", v)} />
                  <div className="min-w-0">
                    <p className={`text-sm font-medium ${grpOn.sizing ? "text-slate-900" : "text-slate-500"}`}>Engine sizing</p>
                    <p className="text-[11px] text-slate-400 truncate">CPU / memory limits for load engines (default 2 CPU / 8Gi)</p>
                  </div>
                </div>
                {grpOn.sizing && (
                <div className="mt-3 pl-12 space-y-2">
                  <Field label="Engine size"
                    hint="KUBERNETES_RESOURCES_LIMITS_CPU / _MEMORY — the pod limits the crane stamps on every engine it spawns">
                    <select className={inputCls} value={sizePreset}
                      onChange={(e) => {
                        // "Custom…" clears both, which is what makes sizePreset
                        // fall through to "custom" and reveal the two fields.
                        const p = ENGINE_SIZES.find((s) => s.id === e.target.value);
                        setOptions((o) => ({ ...o,
                          engine_cpu_limit: p?.cpu ?? null,
                          engine_mem_limit: p?.mem ?? null }));
                      }}>
                      {ENGINE_SIZES.map((s) => (
                        <option key={s.id} value={s.id}>{s.label}</option>
                      ))}
                      <option value="custom">Custom…</option>
                    </select>
                  </Field>
                  {sizePreset === "custom" && (
                    <div className="grid grid-cols-2 gap-2">
                      <Field label="CPU limit">
                        <TextInput mono placeholder="2"
                          value={String(options.engine_cpu_limit ?? "")}
                          onChange={(v) => set("engine_cpu_limit", v || null)} />
                      </Field>
                      <Field label="Memory limit">
                        <TextInput mono placeholder="8Gi"
                          value={String(options.engine_mem_limit ?? "")}
                          onChange={(v) => set("engine_mem_limit", v || null)} />
                      </Field>
                    </div>
                  )}
                  <Check label="Emit a namespace LimitRange (bzm_limitrange.yaml)"
                    hint="Caps the namespace at the engine size and gives pods that declare
                          no resources a sensible default. It cannot change the taurus engine
                          itself — crane sets that pod's requests to 250m/256Mi explicitly."
                    checked={!!options.emit_limitrange}
                    onChange={(v) => set("emit_limitrange", v)} />
                  <p className="text-[11px] text-slate-400">
                    Each concurrent engine also needs ~60GB disk (40GB of it on /tmp).
                    Size worker nodes for slots × engine size.
                  </p>
                </div>
                )}
              </div>

              <div className="px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <Switch on={grpOn.security} onChange={(v) => flipGroup("security", v)} />
                  <div className="min-w-0">
                    <p className={`text-sm font-medium ${grpOn.security ? "text-slate-900" : "text-slate-500"}`}>Security & RBAC</p>
                    <p className="text-[11px] text-slate-400 truncate">defaults: token in a Secret, CLUSTERIP, no cluster RBAC</p>
                  </div>
                </div>
                {grpOn.security && (
                <div className="mt-3 pl-12 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <Check label="AUTH_TOKEN in a Secret"
                      hint="uncheck = simplified ConfigMap variant"
                      checked={Boolean(options.use_secret)}
                      onChange={(v) => set("use_secret", v)} />
                    <Check label="Read-only nodes ClusterRole"
                      hint="optional; not needed for perf tests"
                      checked={Boolean(options.cluster_rbac)}
                      onChange={(v) => set("cluster_rbac", v)} />
                  </div>
                  <Field label="Service type"
                    hint={options.sv_ingress
                      ? "locked to CLUSTERIP: service virtualization reaches pods through the ingress, and NODEPORT would need cluster-scoped node access"
                      : "NODEPORT is BlazeMeter's default but often disallowed"}>
                    <select className={inputCls}
                      value={String(options.service_type ?? "CLUSTERIP")}
                      onChange={(e) => set("service_type", e.target.value)}>
                      <option value="CLUSTERIP">CLUSTERIP</option>
                      {/* Offering NODEPORT while SV is on would only lead to a
                          blocked download; make the bad state unreachable. */}
                      {!options.sv_ingress && <option value="NODEPORT">NODEPORT</option>}
                    </select>
                  </Field>
                </div>
                )}
              </div>

              <div className="px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <Switch on={grpOn.sv} onChange={(v) => flipGroup("sv", v)} />
                  <div className="min-w-0">
                    <p className={`text-sm font-medium ${grpOn.sv ? "text-slate-900" : "text-slate-500"}`}>
                      Service virtualization
                      {svRequired && (
                        <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-bzm">
                          required
                        </span>
                      )}
                    </p>
                    <p className="text-[11px] text-slate-400 truncate">
                      {svRequired
                        ? "this location runs mockServices — virtual services need an ingress"
                        : "only for locations with the mockServices feature"}
                    </p>
                  </div>
                </div>
                {grpOn.sv && (
                <div className="mt-3 pl-12 space-y-2">
                  <Field label="Ingress controller"
                    hint={options.sv_ingress === "openshift"
                      // The cluster router is already there; telling an
                      // OpenShift user to install a controller would contradict
                      // the prerequisite list below.
                      ? "the cluster router already serves the wildcard domain below"
                      : "must already be installed and serving the wildcard domain below"}>
                    <select className={inputCls} value={String(options.sv_ingress ?? "nginx")}
                      onChange={(e) => set("sv_ingress", e.target.value)}>
                      {svConst.ingress_types
                        // openshift publishes a route.openshift.io Route, which
                        // a plain API server does not serve -- generate()
                        // refuses the combination, so do not offer it.
                        .filter((t) => t !== "openshift"
                                       || options.platform === "openshift")
                        .map((t) => (
                          <option key={t} value={t}>
                            {SV_INGRESS_LABELS[t] ?? t}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <Field label="Wildcard domain"
                    hint="endpoints become <service>-<port>-<namespace>.<domain>">
                    <TextInput mono placeholder="apps.example.com"
                      value={String(options.sv_subdomain ?? "")}
                      onChange={(v) => set("sv_subdomain", v || null)} />
                  </Field>
                  <Field label="Wildcard TLS secret"
                    hint={options.sv_ingress === "istio"
                      // Why it is inert on istio is one line down, in the
                      // prerequisite list, rather than said twice here.
                      ? "required even for HTTP — though nothing on Istio ever reads it"
                      : "in the agent namespace; required even for HTTP virtual services"}>
                    <TextInput mono placeholder="wildcard-credential"
                      value={String(options.sv_tls_secret ?? "")}
                      onChange={(v) => set("sv_tls_secret", v || null)} />
                  </Field>
                  {options.sv_ingress === "istio" && (
                    <Field label="Istio Gateway name (optional)"
                      hint="leave empty and crane creates a Gateway per virtual service">
                      <TextInput mono placeholder="bzm-gateway"
                        value={String(options.sv_istio_gateway ?? "")}
                        onChange={(v) => set("sv_istio_gateway", v || null)} />
                    </Field>
                  )}
                  {!svOk && (
                    <p className="text-[11px] text-amber-700">
                      {svNodePortConflict
                        ? "Service type must be CLUSTERIP — NODEPORT sends crane to the cluster-scoped Node object, which namespaced RBAC cannot grant."
                        : "Domain and TLS secret are both required — without them crane crash-loops on “TLS secret name is empty”."}
                    </p>
                  )}
                  <SvPrereqs ingress={txt("sv_ingress")} ctx={svCtx}
                    rbac={svRbac} />
                </div>
                )}
              </div>

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
                      <li key={m.name}>{`${m.name}:${m.port} → ${m.name}-${m.port}-${options.namespace}.${options.sv_subdomain}`}</li>
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
