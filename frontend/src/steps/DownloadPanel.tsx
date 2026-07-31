// Step 3: what the bundle is, where it goes, and whether the cluster will take
// it. Lifted out of App with its markup unchanged -- App keeps the state and
// the effects, because half of what is read here is also driven from there (the
// preview's token report, the preflight re-run, the status poll), and state
// that two owners write is the bug this split must not introduce.
//
// The props are a wide list rather than a bag: every one of them is something
// this panel actually reads, and a `Record<string, unknown>` would hide the day
// one of them stops being passed.
import { useState } from "react";
import {
  api, AgentStatus, Facts, GeneratedFile, Options, SavedBundle, Ship,
  SvCheckOut, SvMocksOut, TokenReport, Feature, Suggestion, downloadZip,
  saveBundle,
} from "../api";
import {
  Button, Check, ErrorMsg, inputCls, SegmentedControl, Switch,
} from "../components";
import { OptionGroup, svConfigured } from "../optionGroups";
import {
  EVIDENCE_SCRIPT, EvidenceHeader, PreflightState, STATUS_STYLE, verdictLine,
  worstStatus,
} from "../preflight";
import { Applied } from "../suggestions";
import { SuggestionList } from "../SuggestionList";
import { DownloadPlan } from "../token";

export interface DownloadPanelProps {
  // -- what is being generated for whom
  facts: Facts | null;
  shipId: string | null;
  ships: Ship[];
  sourceMode: "connect" | "manual";
  who: { email: string; keyId: string } | null;
  options: Options;
  set: (k: string, v: unknown) => void;
  raw: (k: string) => string;
  txt: (k: string) => string;
  // -- the download guard, and why it is closed when the reason is elsewhere
  saOk: boolean;
  svOk: boolean;
  genErr: string | null;
  /** Groups in use but unfinished. They are on the configure step, which is by
   *  definition not this one, so the block names them and offers the way back
   *  rather than pointing at a form nobody can see. */
  unfinished: OptionGroup[];
  goToConfigure: () => void;
  format: string;
  helmBlocked?: string;
  // -- the credential this bundle will carry
  previewToken: TokenReport | null;
  rotate: boolean;
  setRotate: (v: boolean) => void;
  tokenPlan: DownloadPlan;
  lastTokenReport: TokenReport | null;
  setLastTokenReport: (r: TokenReport | null) => void;
  dlErr: string | null;
  setDlErr: (v: string | null) => void;
  // -- saving to a folder
  saveDir: string;
  setSaveDir: (v: string) => void;
  saved: SavedBundle | null;
  setSaved: (v: SavedBundle | null) => void;
  saveErr: string | null;
  setSaveErr: (v: string | null) => void;
  // -- preflight, from a file somebody else collected
  preflight: PreflightState;
  preflightBusy: boolean;
  importEvidence: (f: File) => void;
  evidence: EvidenceHeader | null;
  applied: Applied;
  applySuggestion: (s: Suggestion, value: unknown) => void;
  undoSuggestion: (option: string) => void;
  // -- watching the agent that gets deployed
  polling: boolean;
  setPolling: (v: boolean) => void;
  status: AgentStatus | null;
  svMocks: { ns: string; read: SvMocksOut } | null;
  svChecks: Record<string, { busy: boolean; res?: SvCheckOut; err?: string }>;
  svScheme: "https" | "http";
  svCheckTone: (r: SvCheckOut) => string;
  checkEndpoint: (host: string) => void;
}

export function DownloadPanel(p: DownloadPanelProps) {
  // Destructured rather than read off `p` throughout: the markup below is the
  // markup that was in App, and rewriting every reference to prove it moved is
  // how a move turns into a rewrite nobody diffed.
  const {
    facts, shipId, ships, sourceMode, who, options, set, raw, txt,
    saOk, svOk, genErr, unfinished, goToConfigure, format, helmBlocked,
    previewToken, rotate, setRotate, tokenPlan, lastTokenReport,
    setLastTokenReport, dlErr, setDlErr,
    saveDir, setSaveDir, saved, setSaved, saveErr, setSaveErr,
    preflight, preflightBusy, importEvidence, evidence,
    applied, applySuggestion, undoSuggestion,
    polling, setPolling, status, svMocks, svChecks, svScheme, svCheckTone,
    checkEndpoint,
  } = p;
  return (
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
                <Button disabled={!facts || !shipId || !!genErr || !svOk || !saOk}
                  onClick={() => {
                    setDlErr(null); setLastTokenReport(null);
                    downloadZip(facts!, { ...options, ship_id: shipId },
                                tokenPlan.rotates)
                      .then(setLastTokenReport)
                      .catch((e) => setDlErr(String(e.message)));
                  }}>
                  ⬇ Download bundle (.zip)
                </Button>
                <span className="text-xs text-slate-400">
                  {format === "helm"
                    ? "helm/ + bzm-opl-values.yaml + README"
                    : "manifests + README"}
                  {options.private_registry ? " + bzm-opl-image-mirror.sh" : ""};
                  {" "}{tokenPlan.hint}
                </span>
              </div>
              {/* What the bundle does about the credential, before the click.
                  Three states, and the one that used to be silent is the one that
                  breaks a working install: a download that mints. `incomplete`
                  carries core's own sentence -- where a real token comes from,
                  kubectl included -- rather than a copy of it in TypeScript. */}
              {tokenPlan.incomplete && previewToken && (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                  <p className="text-xs font-semibold text-amber-800">
                    This bundle carries a placeholder AUTH_TOKEN — fill it in
                    before applying it.
                  </p>
                  <p className="text-[11px] text-amber-700 whitespace-pre-line mt-1">
                    {previewToken.message}
                  </p>
                </div>
              )}
              {/* Rotating is an action of its own, offered only where it is the
                  only way forward: an agent nobody kept a token for. Not a
                  by-product of downloading, which is what it used to be -- and
                  hidden once a token is in the field, because core answers that
                  contradiction by ignoring the rotation rather than performing
                  it. Needs the account: minting is an API call. */}
              {!!who && sourceMode === "connect" && !!shipId && !raw("auth_token") && (
                <div className="space-y-1.5">
                  <Check checked={rotate} onChange={setRotate}
                    label="Issue a NEW AUTH_TOKEN with this bundle (rotates)"
                    hint="For an agent whose token nobody kept — it replaces the credential; none can be read back." />
                  {tokenPlan.warning && (
                    <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                      {tokenPlan.warning}
                    </p>
                  )}
                </div>
              )}
              {/* Afterwards as well as before: once a rotation has happened the
                  bundle just handed over is the only copy of that credential. */}
              {lastTokenReport && (
                <p className="text-xs text-slate-600 whitespace-pre-line">
                  {lastTokenReport.message}
                </p>
              )}
              {/* The zip is for handing the bundle to somebody; saving writes
                  the same files (profile.json included) to a folder on this
                  machine -- the shape livetest re-renders from and an MCP
                  session's opl_bundle reads, so the folder is the shared
                  state between this page and those. */}
              <div className="flex gap-2 items-end">
                <label className="grow block">
                  <span className="text-xs font-medium text-slate-600">Folder</span>
                  <input className={inputCls + " font-mono"}
                    placeholder={`~/bzm-opl/${(options.namespace as string) || "blazemeter"}`}
                    value={saveDir}
                    onChange={(e) => setSaveDir(e.target.value)} />
                </label>
                {/* A plain button of the same size as every other one here. The
                    label is typed rather than browsed because a browser cannot
                    hand back an absolute directory path -- webkitdirectory
                    yields file names relative to the folder, which is not what
                    the server needs -- and `~` is expanded server-side. */}
                <Button disabled={!facts || !shipId || !!genErr || !svOk || !saOk}
                  onClick={() => {
                    setSaveErr(null); setSaved(null); setLastTokenReport(null);
                    const dir = saveDir.trim() ||
                      `~/bzm-opl/${(options.namespace as string) || "blazemeter"}`;
                    saveBundle(facts!, { ...options, ship_id: shipId }, dir,
                               tokenPlan.rotates)
                      .then((s) => { setSaved(s); setLastTokenReport(s.token); })
                      .catch((e) => setSaveErr(String(e.message)));
                  }}>
                  Save to folder
                </Button>
              </div>
              {saved && (
                <p className="text-xs text-emerald-700">
                  Wrote {saved.files.length} files to{" "}
                  <code className="font-mono">{saved.out_dir}</code>. Apply with{" "}
                  <code className="font-mono">
                    {format === "helm"
                      ? `helm install bzm-opl ${saved.out_dir}/helm -f ${saved.out_dir}/bzm-opl-values.yaml`
                      : `kubectl apply -f ${saved.out_dir}/ -n ${(options.namespace as string) || "blazemeter"}`}
                  </code>
                  {" "}— or point <code className="font-mono">livetest</code> or
                  an MCP session at the folder.
                </p>
              )}
              <ErrorMsg msg={saveErr} />
              {/* Why the button is disabled, when the reason is a step back.
                  A disabled button whose cause is elsewhere is the failure this
                  is here to remove, so it names the group and offers the way
                  to it. */}
              {unfinished.map((g) => (
                <div key={g.id}
                  className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                  <p className="text-xs text-amber-800 grow">
                    <b>{g.title}</b> is not finished:{" "}
                    {g.requiredHint ?? g.hint}.
                  </p>
                  <Button kind="ghost" onClick={goToConfigure}>
                    Configure
                  </Button>
                </div>
              ))}
              <ErrorMsg msg={dlErr} />

              {/* Will the cluster take it? Answered from a file rather than
                  from a cluster, because the person configuring this usually
                  has access to neither the account nor the cluster -- so it
                  sits here beside the download, needing no key and no
                  kubecontext of its own. */}
              <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                <div className="flex items-start gap-2 flex-wrap">
                  <div className="grow min-w-[16rem]">
                    <p className="text-sm font-semibold text-slate-800">
                      Preflight the target cluster
                    </p>
                    <p className="text-[11px] text-slate-400">
                      Nothing here reads a cluster. Have someone with access run{" "}
                      <code className="font-mono">{EVIDENCE_SCRIPT}</code>{" "}
                      (read-only, reads no secret value) and pick the file it
                      wrote — the same checks{" "}
                      <code className="font-mono">bzm-opl-gen doctor</code> runs.
                    </p>
                  </div>
                  {/* Left of the file picker, because it comes first in time:
                      this is what you run *on* the cluster, and the evidence
                      file is what comes back. */}
                  <TestDeploy facts={facts} options={options} />
                  {/* A label rather than a Button so the file dialog is the
                      click, as in Connect and Import above. */}
                  <label className={"rounded-md px-3 py-1.5 text-sm font-medium "
                    + "border border-slate-300 text-slate-600 whitespace-nowrap "
                    + (!facts || preflightBusy
                      ? "opacity-40 pointer-events-none"
                      : "hover:bg-slate-50 cursor-pointer")}>
                    {preflightBusy ? "Checking…"
                      : preflight.out ? "Choose another file…"
                      : "Choose evidence file…"}
                    <input type="file" accept=".json,application/json"
                      className="hidden" disabled={!facts || preflightBusy}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";      // so the same file re-imports
                        if (f) importEvidence(f);
                      }} />
                  </label>
                </div>
                {!facts && (
                  <p className="text-[11px] text-slate-400 mt-1">
                    Needs the agent details above: the checks measure the cluster
                    against this location's slots, engine size and namespace.
                  </p>
                )}
                <ErrorMsg msg={preflight.error} />
                {preflight.out && evidence && (
                  <div className="mt-2">
                    {/* What was imported, before what it implies. All of this
                        is in the leading verdict's prose as well, and that is
                        not enough: a file collected by somebody with almost no
                        access reads as a clean bill of health if the only place
                        that says so is the tenth line of a list (#53). */}
                    <p className="text-[11px] text-slate-500">
                      <b className="text-slate-700">{preflight.file}</b>
                      {" · collected "}
                      <code className="font-mono">{evidence.collected}</code>
                      {" · describes namespace "}
                      <code className="font-mono">{evidence.describes}</code>
                      {" · preflighting "}
                      <code className="font-mono">{preflight.out.namespace}</code>
                      {" · "}
                      <span className={worstStatus(preflight.out.checks)
                        ? STATUS_STYLE[worstStatus(preflight.out.checks)!].text
                        : ""}>
                        {verdictLine(preflight.out.checks)}
                      </span>
                    </p>
                    {/* The namespaced verdicts -- LimitRanges, quotas,
                        ServiceAccounts, the PSA labels -- are all about the
                        namespace the file describes, whichever one is being
                        configured here. */}
                    {evidence.elsewhere && (
                      <p className="text-[11px] text-amber-700">
                        This file was collected for{" "}
                        <code className="font-mono">{evidence.describes}</code>,
                        so every namespaced verdict below describes that
                        namespace and not{" "}
                        <code className="font-mono">{preflight.out.namespace}</code>.
                      </p>
                    )}
                    {evidence.unreadableLine && (
                      <p className="text-[11px] text-amber-700">
                        {evidence.unreadableLine}
                      </p>
                    )}
                    {/* doctor's order, kept: where the answers came from leads,
                        because every verdict under it is only as good as that
                        one -- a file collected by someone with little access
                        warns about each section it could not see, and a list
                        sorted by severity would bury the reason for all of
                        them. */}
                    <ul className="mt-1.5 space-y-1">
                      {preflight.out.checks.map((c, i) => (
                        <li key={`${c.name}-${i}`}
                          className="flex items-start gap-2 text-[11px] text-slate-500">
                          <span className={"shrink-0 rounded px-1.5 py-0.5 "
                            + "text-[10px] font-bold uppercase tracking-wide "
                            + STATUS_STYLE[c.status].badge}>
                            {STATUS_STYLE[c.status].label}
                          </span>
                          <span>
                            <span className="font-medium text-slate-700">
                              {c.name}
                            </span>
                            {" — "}{c.detail}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {/* The same file's other half: not "would this survive the
                        cluster" but "how should it have been configured".
                        Nothing here is applied on import -- every value written
                        from this list is a click on a row showing both the value
                        it writes and the one it replaces. */}
                    <SuggestionList
                      suggestions={preflight.out.suggestions}
                      whyNothing={preflight.out.why_nothing}
                      options={options} applied={applied}
                      onApply={applySuggestion} onUndo={undoSuggestion} />
                  </div>
                )}
              </div>

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
                {/* Built like an option-group row -- a Switch, a title, a
                    sub-title -- because that is what it is: an on/off with one
                    line of consequence. The status belongs to an agent, so the
                    row names it; a bare "online" beside a page that has four
                    other identities on it says less than it looks like it
                    does. */}
                <div className="rounded-xl border border-slate-200 px-3 py-2.5 flex items-center gap-3">
                  <Switch on={polling} onChange={setPolling} />
                  <div className="min-w-0 grow">
                    <p className="text-sm font-medium text-slate-700">
                      Watch agent status
                      <span className="ml-2 font-mono text-[11px] text-slate-500">
                        {ships.find((s) => s.id === shipId)?.name || shipId}
                      </span>
                    </p>
                    <p className="text-[11px] text-slate-400">
                      {polling
                        ? status
                          ? `${status.state}`
                            + (status.heartbeat_age_s != null
                              ? ` · heartbeat ${status.heartbeat_age_s}s ago` : "")
                          : "polling every 10s…"
                        : "polls every 10s — green once the applied deployment heartbeats"}
                    </p>
                  </div>
                  {polling && (
                    <span className={"flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 shrink-0 "
                      + (status?.online
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-slate-100 text-slate-500")}>
                      <span className={"h-1.5 w-1.5 rounded-full "
                        + (status?.online ? "bg-emerald-500" : "bg-slate-400 animate-pulse")} />
                      {status?.online ? "Online" : "Waiting"}
                    </span>
                  )}
                </div>
                {/* An idle agent says nothing about whether its virtual services
                    became reachable, which is the part of an SV deploy that
                    actually stalls. Only for an SV deployment -- the
                    performance panel is exactly as it was. */}
                {polling && svConfigured(txt("sv_ingress")) && svMocks && (
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
  );
}

/** crane-hook, as a manifest to apply to the cluster under test.
 *
 *  There is deliberately no chart to fetch. crane-hook is an image, published
 *  as a `helm test` hook inside the separate helm-crane chart -- its own
 *  repository ships no chart at all, and documents a Kubernetes manifest as the
 *  standalone way to run it. That manifest is one this generator already
 *  renders (`crane_hook`), so this hands over the one it would put in the
 *  bundle: the same Pod, Role and RoleBinding, for the namespace and registry
 *  currently configured, rather than a generic copy that ignores both.
 *
 *  It does not turn the option on. Applying the check and shipping it inside
 *  the agent's bundle are different decisions -- this is the one you make
 *  before deploying anything.
 */
function TestDeploy({ facts, options }: { facts: Facts | null; options: Options }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    if (!facts) return;
    setBusy(true); setErr(null);
    try {
      const out = await api.generate(facts, { ...options, crane_hook: true });
      const f = out.files.find((x: GeneratedFile) => x.name.includes("cranehook"));
      if (!f) throw new Error("this bundle renders no crane-hook manifest");
      const url = URL.createObjectURL(
        new Blob([f.content], { type: "text/yaml" }));
      const a = document.createElement("a");
      a.href = url; a.download = f.name; a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(String((e as Error).message));
    } finally { setBusy(false); }
  };

  return (
    <span className="flex items-center gap-1">
      <Button kind="ghost" onClick={run} disabled={!facts} busy={busy}>
        Test deploy
      </Button>
      <a href="https://github.com/Blazemeter/crane-hook" target="_blank"
        rel="noreferrer" title="crane-hook on GitHub — what this checks and how"
        aria-label="About crane-hook"
        className="w-5 h-5 rounded-full border border-slate-300 text-slate-500
                   hover:text-slate-900 hover:bg-slate-100 flex items-center
                   justify-center text-[10px] font-serif italic shrink-0">
        i
      </a>
      <ErrorMsg msg={err} />
    </span>
  );
}
