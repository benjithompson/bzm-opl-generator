import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, downloadZip, Account, AgentStatus, Facts, GeneratedFile, KeyCandidate,
  Location, Options, Ship, Workspace,
} from "./api";
import {
  Button, Check, ErrorMsg, Field, inputCls, JsonArea, Section, TextInput,
} from "./components";

const FUNC_ID_CHOICES = ["performance", "functionalApi", "functionalGui", "mockServices"];

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
  const [locations, setLocations] = useState<Location[]>([]);
  const [locFilter, setLocFilter] = useState("");
  const [harborId, setHarborId] = useState<string | null>(null);
  const [showCreateLoc, setShowCreateLoc] = useState(false);
  const [newLoc, setNewLoc] = useState({ name: "", workspace_id: 0, func_ids: ["performance"], slots: 1 });
  const [locErr, setLocErr] = useState<string | null>(null);

  // -- agent -----------------------------------------------------------------
  const [shipId, setShipId] = useState<string | null>(null);
  const [newShipName, setNewShipName] = useState("");
  const [shipErr, setShipErr] = useState<string | null>(null);
  const [facts, setFacts] = useState<Facts | null>(null);

  // -- options / preview -----------------------------------------------------
  const [defaults, setDefaults] = useState<Options>({});
  const [options, setOptions] = useState<Options>({ namespace: "blazemeter" });
  const [profiles, setProfiles] = useState<{ name: string; options: Options }[]>([]);
  const [files, setFiles] = useState<GeneratedFile[]>([]);
  const [genErr, setGenErr] = useState<string | null>(null);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [status, setStatus] = useState<AgentStatus | null>(null);
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
    setLocations([]); setHarborId(null);
    api.locations(accountId).then(setLocations).catch((e) => setLocErr(e.message));
    api.workspaces(accountId).then((ws) => {
      setWorkspaces(ws);
      setNewLoc((n) => ({ ...n, workspace_id: ws[0]?.id ?? 0 }));
    }).catch(() => {});
  }, [accountId, who]);

  const location = useMemo(
    () => locations.find((l) => l.id === harborId) ?? null, [locations, harborId]);
  const ships: Ship[] = location?.ships ?? [];

  useEffect(() => {
    setShipId(null); setFacts(null); setStatus(null);
    if (!harborId) return;
    api.facts(harborId).then(setFacts).catch((e) => setShipErr(e.message));
  }, [harborId]);

  useEffect(() => {
    if (ships.length === 1) setShipId(ships[0].id);
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
        setActiveFile((a) => (a && r.files.some((f) => f.name === a) ? a : r.files[0]?.name ?? null));
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

      <main className="max-w-screen-2xl mx-auto p-6 grid grid-cols-1 xl:grid-cols-2 gap-6">
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
            hint="The BlazeMeter location (harbor) these manifests deploy an agent for.">
            <div className="space-y-3">
              <Field label="Account">
                <select className={inputCls} value={accountId ?? ""}
                  disabled={!who}
                  onChange={(e) => setAccountId(Number(e.target.value))}>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>{a.name} ({a.id})</option>))}
                </select>
              </Field>
              {locations.length > 8 && (
                <TextInput value={locFilter} onChange={setLocFilter}
                  placeholder={`filter ${locations.length} locations…`} />
              )}
              <div className="max-h-56 overflow-y-auto border border-slate-200 rounded-md divide-y divide-slate-100">
                {filteredLocs.map((l) => (
                  <button key={l.id}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-slate-50 ${l.id === harborId ? "bg-bzm/10 border-l-4 border-bzm" : ""}`}
                    onClick={() => setHarborId(l.id)}>
                    <span className="font-medium">{l.name}</span>
                    <span className="text-xs text-slate-400 ml-2">
                      {l.funcIds?.slice(0, 4).join(", ")}{(l.funcIds?.length ?? 0) > 4 && "…"} ·
                      {" "}{l.slots} slot{l.slots === 1 ? "" : "s"} · {l.ships?.length ?? 0} agent(s)
                    </span>
                  </button>
                ))}
                {who && filteredLocs.length === 0 && (
                  <p className="px-3 py-2 text-sm text-slate-400">no locations match</p>)}
              </div>
              <ErrorMsg msg={locErr} />
              {!showCreateLoc ? (
                <Button kind="ghost" onClick={() => setShowCreateLoc(true)} disabled={!who}>
                  + New location
                </Button>
              ) : (
                <div className="border border-slate-200 rounded-md p-3 space-y-2 bg-slate-50">
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="Name">
                      <TextInput value={newLoc.name}
                        onChange={(v) => setNewLoc({ ...newLoc, name: v })} /></Field>
                    <Field label="Workspace">
                      <select className={inputCls} value={newLoc.workspace_id}
                        onChange={(e) => setNewLoc({ ...newLoc, workspace_id: Number(e.target.value) })}>
                        {workspaces.map((w) => (
                          <option key={w.id} value={w.id}>{w.name}</option>))}
                      </select>
                    </Field>
                  </div>
                  <div className="flex gap-4 items-end">
                    <div className="flex gap-3 flex-wrap">
                      {FUNC_ID_CHOICES.map((f) => (
                        <Check key={f} label={f}
                          checked={newLoc.func_ids.includes(f)}
                          onChange={(on) => setNewLoc({
                            ...newLoc,
                            func_ids: on ? [...newLoc.func_ids, f]
                              : newLoc.func_ids.filter((x) => x !== f),
                          })} />
                      ))}
                    </div>
                    <Field label="Slots">
                      <input type="number" min={1} className={inputCls + " w-20"}
                        value={newLoc.slots}
                        onChange={(e) => setNewLoc({ ...newLoc, slots: Number(e.target.value) })} />
                    </Field>
                  </div>
                  <div className="flex gap-2">
                    <Button disabled={!newLoc.name || !newLoc.workspace_id}
                      onClick={async () => {
                        try {
                          const l = await api.createLocation({ ...newLoc, account_id: accountId! });
                          const ls = await api.locations(accountId!);
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
            hint="The agent identity this deployment runs as; its AUTH_TOKEN is fetched automatically on download.">
            <div className="space-y-3">
              {ships.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {ships.map((s) => (
                    <button key={s.id}
                      className={`px-3 py-1.5 rounded-md border text-sm ${s.id === shipId ? "border-bzm bg-bzm/10 text-bzm-dark font-medium" : "border-slate-300 hover:bg-slate-50"}`}
                      onClick={() => setShipId(s.id)}>
                      {s.name || s.id} <span className="text-xs text-slate-400">({s.state})</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="flex gap-2 items-end">
                <div className="grow">
                  <Field label={ships.length ? "…or create a new agent" : "Create an agent"}>
                    <TextInput value={newShipName} onChange={setNewShipName}
                      placeholder="e.g. k8s-prod-cluster" />
                  </Field>
                </div>
                <Button disabled={!harborId || !newShipName}
                  onClick={async () => {
                    try {
                      const r = await api.createShip(harborId!, newShipName);
                      const ls = await api.locations(accountId!);
                      setLocations(ls); setShipId(r.ship.id); setNewShipName("");
                      api.facts(harborId!).then(setFacts).catch(() => {});
                    } catch (e) { setShipErr(String((e as Error).message)); }
                  }}>Create</Button>
              </div>
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
              <div className="flex gap-2 items-end flex-wrap">
                <Field label="Start from preset">
                  <select className={inputCls} defaultValue=""
                    onChange={(e) => e.target.value && applyProfile(e.target.value)}>
                    <option value="">—</option>
                    {profiles.map((p) => (
                      <option key={p.name} value={p.name}>{p.name}</option>))}
                  </select>
                </Field>
                <Button kind="ghost" onClick={exportProfile}>Export profile</Button>
                <label className="rounded-md px-3 py-1.5 text-sm font-medium border border-slate-300 text-slate-600 hover:bg-slate-50 cursor-pointer">
                  Import profile
                  <input type="file" accept=".json" className="hidden"
                    onChange={(e) => e.target.files?.[0] && importProfile(e.target.files[0])} />
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <Field label="Platform">
                  <select className={inputCls} value={String(options.platform)}
                    onChange={(e) => set("platform", e.target.value)}>
                    <option value="openshift">OpenShift (SCC-friendly)</option>
                    <option value="k8s">Kubernetes (pinned UID)</option>
                  </select>
                </Field>
                <Field label="Namespace">
                  <TextInput value={String(options.namespace ?? "")}
                    onChange={(v) => set("namespace", v)} />
                </Field>
                <Field label="Service type" hint="NODEPORT is BlazeMeter's default but often disallowed">
                  <select className={inputCls} value={String(options.service_type)}
                    onChange={(e) => set("service_type", e.target.value)}>
                    <option value="CLUSTERIP">CLUSTERIP</option>
                    <option value="NODEPORT">NODEPORT</option>
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

              <div className="grid grid-cols-2 gap-2">
                <Check label="AUTH_TOKEN in a Secret"
                  hint="uncheck = simplified ConfigMap variant"
                  checked={Boolean(options.use_secret)}
                  onChange={(v) => set("use_secret", v)} />
                <Check label="Read-only nodes ClusterRole"
                  hint="optional; not needed for perf tests"
                  checked={Boolean(options.cluster_rbac)}
                  onChange={(v) => set("cluster_rbac", v)} />
                <Check label="Include GUI/mock images"
                  hint="for functionalGui / mockServices locations"
                  checked={Boolean(options.gui)}
                  onChange={(v) => set("gui", v)} />
              </div>

              <details className="border border-slate-200 rounded-md" open={!!options.private_registry}>
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-700">
                  Private registry
                </summary>
                <div className="p-3 pt-1 space-y-2">
                  <Field label="Registry" hint="sets DOCKER_REGISTRY + IMAGE_OVERRIDES, disables auto-update, emits bzm-opl-image-mirror.sh">
                    <TextInput mono value={String(options.private_registry ?? "")}
                      placeholder="registry.corp.com/bzm"
                      onChange={(v) => set("private_registry", v || null)} />
                  </Field>
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="imagePullSecret name (crane image)">
                      <TextInput mono value={String(options.pull_secret ?? "")}
                        onChange={(v) => set("pull_secret", v || null)} />
                    </Field>
                    <Check label="Registry auth env stubs"
                      hint="commented DOCKER_REGISTRY_USERNAME/PASSWORD"
                      checked={Boolean(options.registry_auth)}
                      onChange={(v) => set("registry_auth", v)} />
                  </div>
                </div>
              </details>

              <details className="border border-slate-200 rounded-md"
                open={!!(options.proxy || options.ca_bundle)}>
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-700">
                  Egress: proxy & corporate CA
                </summary>
                <div className="p-3 pt-1 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="HTTP proxy">
                      <TextInput mono placeholder="http://proxy:3128"
                        value={String((options.proxy as Record<string, string>)?.http ?? "")}
                        onChange={(v) => set("proxy", v || (options.proxy as Record<string, string>)?.https
                          ? { ...(options.proxy as object), http: v || undefined } : null)} />
                    </Field>
                    <Field label="HTTPS proxy">
                      <TextInput mono placeholder="http://proxy:3128"
                        value={String((options.proxy as Record<string, string>)?.https ?? "")}
                        onChange={(v) => set("proxy", v || (options.proxy as Record<string, string>)?.http
                          ? { ...(options.proxy as object), https: v || undefined } : null)} />
                    </Field>
                  </div>
                  <Field label="NO_PROXY">
                    <TextInput mono placeholder="kubernetes.default,127.0.0.1,localhost"
                      value={String((options.proxy as Record<string, string>)?.no_proxy ?? "")}
                      onChange={(v) => options.proxy &&
                        set("proxy", { ...(options.proxy as object), no_proxy: v })} />
                  </Field>
                  <Field label="CA bundle (PEM)"
                    hint="mounted into crane + engines via KUBERNETES_CA_BUNDLE_MOUNT (TLS-intercepting proxies)">
                    <textarea className={inputCls + " font-mono text-[10px]"} rows={3}
                      placeholder="-----BEGIN CERTIFICATE-----"
                      value={String(options.ca_bundle ?? "")}
                      onChange={(e) => set("ca_bundle", e.target.value || null)} />
                  </Field>
                </div>
              </details>

              <details className="border border-slate-200 rounded-md"
                open={!!(options.tolerations || options.node_selector ||
                  options.engine_cpu_limit || options.engine_mem_limit)}>
                <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-700">
                  Scheduling & engine sizing
                </summary>
                <div className="p-3 pt-1 space-y-2">
                  <JsonArea label="Tolerations (JSON list — crane pod + engines)"
                    value={options.tolerations}
                    placeholder='[{"key":"lifecycle","operator":"Equal","value":"spot","effect":"NoSchedule"}]'
                    onValid={(v) => set("tolerations", v)} />
                  <JsonArea label="Node selector (JSON object)" rows={2}
                    value={options.node_selector}
                    placeholder='{"pool":"loadtest"}'
                    onValid={(v) => set("node_selector", v)} />
                  <div className="grid grid-cols-2 gap-2">
                    <Field label="Engine CPU limit" hint="KUBERNETES_RESOURCES_LIMITS_CPU">
                      <TextInput mono placeholder="2"
                        value={String(options.engine_cpu_limit ?? "")}
                        onChange={(v) => set("engine_cpu_limit", v || null)} />
                    </Field>
                    <Field label="Engine memory limit" hint="KUBERNETES_RESOURCES_LIMITS_MEMORY">
                      <TextInput mono placeholder="8Gi"
                        value={String(options.engine_mem_limit ?? "")}
                        onChange={(v) => set("engine_mem_limit", v || null)} />
                    </Field>
                  </div>
                </div>
              </details>
            </div>
          </Section>

          {/* 5 · Download & verify */}
          <Section n={5} title="Download & verify">
            <div className="space-y-3">
              <div className="flex gap-2 items-center">
                <Button disabled={!facts || !shipId || !!genErr}
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
        </div>

        {/* preview pane */}
        <div className="xl:sticky xl:top-16 self-start">
          <div className="bg-slate-900 rounded-xl shadow-lg overflow-hidden min-h-[400px]">
            <div className="flex items-center gap-1 px-3 pt-2 pb-0 overflow-x-auto">
              {files.map((f) => (
                <button key={f.name}
                  className={`px-2.5 py-1.5 text-xs rounded-t-md whitespace-nowrap font-mono ${f.name === activeFile ? "bg-slate-800 text-white" : "text-slate-400 hover:text-slate-200"}`}
                  onClick={() => setActiveFile(f.name)}>
                  {f.name}
                </button>
              ))}
              {files.length === 0 && (
                <span className="text-slate-500 text-sm px-2 py-3">
                  {genErr ? "" : "pick a location & agent to preview manifests"}
                </span>
              )}
            </div>
            {genErr && <p className="text-red-400 text-xs px-4 py-2">{genErr}</p>}
            {activeFile && (
              <div className="relative">
                <button
                  className="absolute right-3 top-2 text-xs text-slate-400 hover:text-white border border-slate-600 rounded px-2 py-0.5"
                  onClick={() => {
                    const f = files.find((x) => x.name === activeFile);
                    if (f) navigator.clipboard.writeText(f.content);
                  }}>copy</button>
                <pre className="text-[11.5px] leading-relaxed text-slate-100 p-4 pt-3 overflow-auto max-h-[80vh] font-mono">
                  {files.find((f) => f.name === activeFile)?.content}
                </pre>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
