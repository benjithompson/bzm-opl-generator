// Step 3: what the bundle is, where it goes, and whether the cluster will take
// it. Lifted out of App with its markup unchanged -- App keeps the state and
// the effects, because half of what is read here is also driven from there (the
// preview's token report, the preflight re-run, the status poll), and state
// that two owners write is the bug this split must not introduce.
//
// The interface is four records and one report, not forty props. The wide list
// was the honest first move -- every prop was something this panel read -- but
// it had eight of them as value-and-setter pairs the page only ever reset before
// a call, and a panel whose signature is that long is a panel you cannot see the
// shape of. What each record is, is a question this step asks: what is being
// generated, what that does to the credential, what the last attempt did, what
// the imported evidence says, and whether the agent came up. Nothing here holds
// state; the records are assembled in App from state App still owns, so the
// distribution of ownership is exactly what it was.
//
// The call that produces a bundle stays in this file deliberately. It can mint
// a credential, and CLAUDE.md's rule is that a request which touches the
// account is made where its cost is on screen -- which is beside that button,
// under the warning that says what a rotation kills. It is made through the
// injected client like every other route, and what it carries about the
// credential is credential.plan.request, taken whole: this file has no say in
// it, which is what stopped the two buttons disagreeing (#104) back when there
// were two.
import { useState } from "react";
import {
  Api, AgentStatus, Facts, GeneratedFile, Options, SvCheckOut, SvMocksOut,
  Suggestion,
} from "../api";
import { Attempt, NO_ATTEMPT, downloadFailed, downloaded } from "../attempt";
import { Button, ErrorMsg, Switch } from "../components";
import { isDocker } from "../formats";
import { OptionGroup } from "../optionGroups";
import {
  EVIDENCE_SCRIPT, EvidenceHeader, PreflightState, STATUS_STYLE, worstStatus,
} from "../preflight";
import { Applied } from "../suggestions";
import { SuggestionList } from "../SuggestionList";
import { DownloadPlan } from "../token";
// Service virtualization as one record: whether this bundle can be a chart,
// whether its settings are finished, how a published endpoint is probed. It
// used to arrive as four props derived four places in App.
import { Sv } from "../sv";

/** What is being generated, for whom, and whether it can be. */
export interface BundleHandover {
  facts: Facts | null;
  shipId: string | null;
  /** The options as they stand. Spread into the request, and read for what
   *  this step says the bundle holds -- whether it carries the mirror script.
   *  The panel is handed the record rather than a reader per key: it sends the
   *  whole thing. */
  options: Options;
  /** What is being generated. Read, not written: the choice is at the top of
   *  the configure step, because it decides which questions that step asks --
   *  a docker bundle has no namespace, no ServiceAccount and no scheduling, and
   *  a form that asked for all three and then dropped them was the silent
   *  failure. Read here for what the bundle holds and whether a cluster
   *  preflight means anything. */
  format: string;
  /** Back to the configure step, for the blocks that name an unfinished
   *  group -- the reason the button is disabled is a step away, so the block
   *  offers the way to it. */
  goToConfigure: () => void;
  /** Everything about service virtualization, from sv.ts. Four things are read
   *  off it here -- whether the settings are finished, whether the chart is
   *  refused and why, whether a mock watch is meaningful at all, and the scheme
   *  an endpoint is probed over -- and they are one answer, so they arrive as
   *  one value. */
  sv: Sv;
  /** The two blocks that are not a group's: an unusable service account name,
   *  and a preview that did not render. Neither is shown here -- the field and
   *  the preview pane say so where they are -- but both stop the buttons. */
  saOk: boolean;
  genErr: string | null;
  /** Groups in use but unfinished. They are on the configure step, which is by
   *  definition not this one, so the block names them and offers the way back
   *  rather than pointing at a form nobody can see. */
  unfinished: OptionGroup[];
}

/** What the next download will do about the agent's credential. */
export interface CredentialHandover {
  /** The answers that must agree -- the hint beside the button, whether the
   *  bundle can be applied at all, and the credential request the download
   *  sends. From token.ts, and `request` is sent rather than read: this panel
   *  is handed what to send, so it has nothing to get wrong about it.
   *
   *  Nothing here rotates any more. The box that did lived beside this button
   *  and was the *second* way to mint one; step 1 has the first, on the agent
   *  the credential belongs to, which is where the question is asked and where
   *  what it kills is on screen. */
  plan: DownloadPlan;
}

/** An option this step both reads and writes, as one value. Named because the
 *  pair was spelled out three times over -- the field, the component's param
 *  and App's literal -- and a two-field type written out at each end is two
 *  ends free to disagree about which of them owns the write. */
export interface Toggle {
  on: boolean;
  set: (v: boolean) => void;
}

/** The cluster read somebody else collected, and what may be applied from it. */
export interface PreflightHandover {
  /** crane-hook: whether the bundle carries it, and the write that decides
   *  (#130). It is a generate option, so it shapes the bundle -- but what it
   *  is *about* is the cluster the bundle is going to, which is this step's
   *  question and not the configure step's, where it used to sit among options
   *  that shape the agent.
   *
   *  Null where the format cannot carry it: App asks the served ignored table
   *  rather than this file re-deriving it from `format`. It blocks nothing, so
   *  its absence takes no blocker off screen with it. */
  craneHook: Toggle | null;
  /** The imported file, its verdicts and whatever the last import was refused
   *  for -- preflight.ts's own state, unchanged. */
  read: PreflightState;
  busy: boolean;
  /** What the file says about itself: collected when, for which namespace, and
   *  what its collector could not read. */
  header: EvidenceHeader | null;
  importFile: (f: File) => void;
  applied: Applied;
  applySuggestion: (s: Suggestion, value: unknown) => void;
  undoSuggestion: (option: string) => void;
}

/** Watching the agent this bundle deploys, and the virtual services under it. */
export interface WatchHandover {
  /** Whether it can be watched at all: polling is an API call, and manual entry
   *  is the mode that exists to do without a key. */
  available: boolean;
  on: boolean;
  setOn: (v: boolean) => void;
  /** The agent's name, or its id where it has none. The status belongs to an
   *  agent, so the row names it: a bare "online" beside a page with four other
   *  identities on it says less than it looks like it does. */
  agent: string | null;
  status: AgentStatus | null;
  mocks: { ns: string; read: SvMocksOut } | null;
  checks: Record<string, { busy: boolean; res?: SvCheckOut; err?: string }>;
  check: (host: string) => void;
}

export interface DownloadPanelProps {
  /** The route caller, from App. Every request this panel makes goes through
   *  it -- the two that produce a bundle and the crane-hook render behind Test
   *  deploy, which was the last call site on the page still importing the real
   *  client at module level and so the last one outside the seam. */
  api: Api;
  bundle: BundleHandover;
  credential: CredentialHandover;
  /** What the last download did, and where the next one is reported. The
   *  record is App's -- this panel makes attempts and hands them over, and
   *  holds nothing. */
  attempt: Attempt;
  report: (a: Attempt) => void;
  preflight: PreflightHandover;
  watch: WatchHandover;
}

/** How an endpoint check reads. A 503 is amber, not red: the check worked and
 *  this is its answer -- the one `bzm-opl-gen sv-expose` exists to fix, which
 *  the message names. Anything else that answered routed, so only a probe that
 *  got no status line at all is red. Here rather than in App: it is a class
 *  name for a row this file renders, and nothing else asks. */
const checkTone = (r: SvCheckOut) =>
  r.status !== "ok" ? "text-red-600"
    : r.code != null && r.code < 400 ? "text-emerald-700" : "text-amber-700";

/** What each format's bundle contains, for the line beside the download button.
 *  A lookup rather than a chain of ternaries: there are three formats now, and
 *  the next one should be a row here rather than another branch. */
const BUNDLE_HOLDS: Record<string, string> = {
  manifests: "manifests + README",
  helm: "helm/ + bzm-opl-values.yaml + README",
  docker: "bzm-opl-agent.sh + .env + README",
};

export function DownloadPanel(p: DownloadPanelProps) {
  const { api, bundle, credential, attempt, report, preflight, watch } = p;
  // The names the markup below already used, for the values it reads most: the
  // markup is the markup that was in App, and rewriting every reference to
  // prove it moved is how a move turns into a rewrite nobody diffed.
  const { facts, shipId, options, format, sv } = bundle;
  const { plan } = credential;
  const { read } = preflight;
  // One expression for both buttons rather than the same five terms twice. It
  // is a judgement this panel makes and keeps: two of the five are elsewhere on
  // screen (the service account field, the preview pane), and the other three
  // are said right here.
  const ready = !!facts && !!shipId && !bundle.genErr && sv.ok && bundle.saOk;
  return (
            <div className="space-y-3">
              <div className="flex gap-2 items-center">
                <Button disabled={!ready}
                  onClick={() => {
                    report(NO_ATTEMPT);
                    api.downloadZip(facts!, { ...options, ship_id: shipId },
                                    plan.request)
                      .then((t) => report(downloaded(t)))
                      .catch((e) => report(downloadFailed(String(e.message))));
                  }}>
                  ⬇ Download bundle (.zip)
                </Button>
                <span className="text-xs text-slate-400">
                  {BUNDLE_HOLDS[format] ?? BUNDLE_HOLDS.manifests}
                  {options.private_registry ? " + bzm-opl-image-mirror.sh" : ""};
                  {" "}{plan.hint}
                </span>
              </div>
              {/* The bundle cannot be applied as it stands, said over the
                  button rather than in a README nobody opens afterwards.
                  One line, and it is the whole message. Core's own paragraph
                  used to sit under it -- four ways to come by a token and a
                  kubectl command to read one back out of a running cluster --
                  which is a page of recovery instructions answering a question
                  nobody has asked yet. Where the token comes from is step 1's;
                  this says only that this bundle has not got one. */}
              {plan.incomplete && (
                <p className="rounded-md border border-amber-200 bg-amber-50
                              px-3 py-2 text-xs font-semibold text-amber-800">
                  This bundle carries a placeholder AUTH_TOKEN — fill it in
                  before applying it.
                </p>
              )}
              {/* Why the button is disabled, when the reason is a step back.
                  A disabled button whose cause is elsewhere is the failure this
                  is here to remove, so it names the group and offers the way
                  to it. */}
              {bundle.unfinished.map((g) => (
                <div key={g.id}
                  className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                  <p className="text-xs text-amber-800 grow">
                    <b>{g.title}</b> is not finished:{" "}
                    {g.requiredHint ?? g.hint}.
                  </p>
                  <Button kind="ghost" onClick={bundle.goToConfigure}>
                    Configure
                  </Button>
                </div>
              ))}
              <ErrorMsg msg={attempt.downloadError} />

              {/* Will the cluster take it? Answered from a file rather than
                  from a cluster, because the person configuring this usually
                  has access to neither the account nor the cluster -- so it
                  sits here beside the download, needing no key and no
                  kubecontext of its own.

                  Every check in it is a cluster check -- node allocatable,
                  LimitRanges, PodSecurity labels, the ServiceAccount -- and a
                  docker bundle has no cluster to run them against. Named
                  rather than dropped: a block that vanishes reads as a step
                  somebody forgot, and the sentence is where "then what do I
                  check" gets answered. */}
              {isDocker(format) && (
                <p className="mt-5 text-[11px] text-slate-400">
                  No cluster preflight for a docker bundle — every check{" "}
                  <code className="font-mono">bzm-opl-gen doctor</code> runs is
                  about a cluster. What this bundle needs of its host is in the
                  README it ships with.
                </p>
              )}
              {!isDocker(format) && (
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
                  {/* Above the file picker, because it comes first in time:
                      this is what you run *on* the cluster, and the evidence
                      file is what comes back from it. Stacked rather than in a
                      row so the order reads as the sequence it is. */}
                  <div className="flex flex-col items-start gap-2">
                  <TestDeploy api={api} facts={facts} options={options} />
                  {/* A label rather than a Button so the file dialog is the
                      click, as in Connect and Import above. */}
                  <label className={"rounded-md px-3 py-1.5 text-sm font-medium "
                    + "border border-slate-300 text-slate-600 whitespace-nowrap "
                    + (!facts || preflight.busy
                      ? "opacity-40 pointer-events-none"
                      : "hover:bg-slate-50 cursor-pointer")}>
                    {preflight.busy ? "Checking…"
                      : read.out ? "Choose another file…"
                      : "Choose evidence file…"}
                    <input type="file" accept=".json,application/json"
                      className="hidden" disabled={!facts || preflight.busy}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";      // so the same file re-imports
                        if (f) preflight.importFile(f);
                      }} />
                  </label>
                  </div>
                </div>
                {!facts && (
                  <p className="text-[11px] text-slate-400 mt-1">
                    Needs the agent details above: the checks measure the cluster
                    against this location's slots, engine size and namespace.
                  </p>
                )}
                <ErrorMsg msg={read.error} />
                {read.out && preflight.header && (
                  <div className="mt-2">
                    {/* What was imported, before what it implies. All of this
                        is in the leading verdict's prose as well, and that is
                        not enough: a file collected by somebody with almost no
                        access reads as a clean bill of health if the only place
                        that says so is the tenth line of a list (#53). */}
                    <p className="text-[11px] text-slate-500">
                      <b className="text-slate-700">{read.file}</b>
                      {" · collected "}
                      <code className="font-mono">{preflight.header.collected}</code>
                      {" · describes namespace "}
                      <code className="font-mono">{preflight.header.describes}</code>
                      {" · preflighting "}
                      <code className="font-mono">{read.out.namespace}</code>
                      {" · "}
                      {/* doctor's own sentence about the list, in the colour
                          the worst verdict in it takes. The tone is a
                          rendering; the sentence is not, and is served. */}
                      <span className={worstStatus(read.out.checks)
                        ? STATUS_STYLE[worstStatus(read.out.checks)!].text
                        : ""}>
                        {read.out.summary}
                      </span>
                    </p>
                    {/* A restored answer is older than it looks, and says so.
                        The header above already dates the *collection*, which
                        is staleness about the cluster and is on screen either
                        way. This is the other one, and only a restore has it:
                        the verdicts were judged against a configuration, they
                        follow it on every option change while the page holds
                        the file, and a snapshot does not carry the file -- so
                        after a refresh they stop following and nothing else on
                        screen would say so. Left unsaid, the failure is a
                        verdict list that looks live and quietly describes the
                        namespace, engine size and ingress class of an earlier
                        page load.

                        Said rather than withheld: the alternative is to drop
                        the verdicts at the first option change, and that takes
                        the undo history's only rendering with it -- the button
                        is a row of this list -- which is the state #119 exists
                        to remove. */}
                    {read.restored && (
                      <p className="text-[11px] text-amber-700">
                        Read back from an earlier page load — this page no
                        longer holds{" "}
                        <code className="font-mono">{read.file}</code>, so these
                        verdicts are not being re-judged and any option changed
                        since is not in them. Pick the file again to re-judge.
                      </p>
                    )}
                    {/* The namespaced verdicts -- LimitRanges, quotas,
                        ServiceAccounts, the PSA labels -- are all about the
                        namespace the file describes, whichever one is being
                        configured here. */}
                    {preflight.header.elsewhere && (
                      <p className="text-[11px] text-amber-700">
                        This file was collected for{" "}
                        <code className="font-mono">{preflight.header.describes}</code>,
                        so every namespaced verdict below describes that
                        namespace and not{" "}
                        <code className="font-mono">{read.out.namespace}</code>.
                      </p>
                    )}
                    {preflight.header.unreadableLine && (
                      <p className="text-[11px] text-amber-700">
                        {preflight.header.unreadableLine}
                      </p>
                    )}
                    {/* doctor's order, kept: where the answers came from leads,
                        because every verdict under it is only as good as that
                        one -- a file collected by someone with little access
                        warns about each section it could not see, and a list
                        sorted by severity would bury the reason for all of
                        them. */}
                    <ul className="mt-1.5 space-y-1">
                      {read.out.checks.map((c, i) => (
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
                      suggestions={read.out.suggestions}
                      whyNothing={read.out.why_nothing}
                      options={options} applied={preflight.applied}
                      onApply={preflight.applySuggestion}
                      onUndo={preflight.undoSuggestion} />
                  </div>
                )}
                {/* The third way to run the same check, and the one that is
                    part of the bundle. Last of the three because it is last in
                    time: Test deploy runs it now, the evidence file judges a
                    read of the cluster, and this ships it for whoever applies
                    the bundle. Below the file's verdicts rather than between
                    the picker and them -- the import's own error and answer
                    belong next to the control that produced them. */}
                {preflight.craneHook && (
                  <CraneHookRow hook={preflight.craneHook} />
                )}
              </div>
              )}

              <div className="border-t border-slate-100 pt-3">
                {!watch.available ? (
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
                  <Switch on={watch.on} onChange={watch.setOn}
                    label="Watch agent status" />
                  <div className="min-w-0 grow">
                    <p className="text-sm font-medium text-slate-700">
                      Watch agent status
                      <span className="ml-2 font-mono text-[11px] text-slate-500">
                        {watch.agent}
                      </span>
                    </p>
                    <p className="text-[11px] text-slate-400">
                      {watch.on
                        ? watch.status
                          ? `${watch.status.state}`
                            + (watch.status.heartbeat_age_s != null
                              ? ` · heartbeat ${watch.status.heartbeat_age_s}s ago` : "")
                          : "polling every 10s…"
                        : "polls every 10s — green once the applied deployment heartbeats"}
                    </p>
                  </div>
                  {watch.on && (
                    <span className={"flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 shrink-0 "
                      + (watch.status?.online
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-slate-100 text-slate-500")}>
                      <span className={"h-1.5 w-1.5 rounded-full "
                        + (watch.status?.online ? "bg-emerald-500" : "bg-slate-400 animate-pulse")} />
                      {watch.status?.online ? "Online" : "Waiting"}
                    </span>
                  )}
                </div>
                {/* An idle agent says nothing about whether its virtual services
                    became reachable, which is the part of an SV deploy that
                    actually stalls. Only for an SV deployment -- the
                    performance panel is exactly as it was. */}
                {watch.on && sv.configured && watch.mocks && (
                  <div className="mt-3">
                    <p className="text-xs font-medium text-slate-600 mb-1">
                      Virtual services in {watch.mocks.ns}
                    </p>
                    {watch.mocks.read.mocks.length > 0 ? (
                      <ul className="space-y-1.5">
                        {watch.mocks.read.mocks.map((m) => {
                          const chk = m.host ? watch.checks[m.host] : undefined;
                          return (
                            <li key={`${m.name}-${m.port}`} className="text-[11px] text-slate-500">
                              <span className="font-medium text-slate-700">{m.name}</span>
                              <span className="text-slate-400">:{m.port}</span>
                              {m.host ? (
                                <>
                                  {" — "}
                                  <a className="text-bzm hover:underline font-mono break-all"
                                    href={`${sv.scheme}://${m.host}/`}
                                    target="_blank" rel="noreferrer">
                                    {sv.scheme}://{m.host}/
                                  </a>
                                  {/* The check is made from the machine serving
                                      this page, against the host shown above --
                                      never a second copy of that string. */}
                                  <button type="button" disabled={chk?.busy}
                                    onClick={() => watch.check(m.host!)}
                                    className="ml-2 align-baseline rounded border border-slate-300 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40">
                                    {chk?.busy ? "checking…" : "check endpoint"}
                                  </button>
                                </>
                              ) : <> — set a wildcard domain to get the endpoint host</>}
                              {chk?.res && (
                                <p className={`mt-0.5 ${checkTone(chk.res)}`}>
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
                      <p className="text-[11px] text-slate-400">{watch.mocks.read.message}</p>
                    )}
                  </div>
                )}
                </>
                )}
              </div>
            </div>
  );
}

/** crane-hook in the bundle, rather than as a manifest handed over now (#130).
 *
 *  github.com/Blazemeter/crane-hook: BlazeMeter's own cluster-readiness
 *  checker, a one-shot Pod plus its own Role and RoleBinding. Every variable it
 *  takes -- the namespace, the RBAC names, the expose type and its TLS secret,
 *  the registry, the proxy -- is something the configure step already decided,
 *  which is why it is a switch rather than a page in a doc.
 *
 *  It lived on that step until #130, among the options that shape the agent,
 *  and it shapes none of it: the same agent is deployed either way, and what
 *  the switch adds is a check that runs beside it. So it is here, with the
 *  other two ways of asking whether this cluster will take the bundle.
 *
 *  Writing the option from this step is what keeps the move honest -- the
 *  download has to carry it, so the control has to be somewhere the download
 *  can see. It is a boolean and blocks nothing, so nothing came with it. */
function CraneHookRow({ hook }: { hook: Toggle }) {
  const { on } = hook;
  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-white px-3 py-2.5">
      <div className="flex items-center gap-3">
        <Switch on={on} onChange={hook.set}
          label="Ship the check with the bundle" />
        <div className="min-w-0 grow">
          <p className={`text-sm font-medium ${on ? "text-slate-900" : "text-slate-500"}`}>
            Ship the check with the bundle
          </p>
          <p className="text-[11px] text-slate-400">
            crane-hook, for whoever applies it — the same checks Test deploy
            runs, run on their cluster rather than on a file
          </p>
        </div>
      </div>
      {on && (
        <p className="mt-2 pl-12 text-[11px] text-slate-500">
          Adds <span className="font-mono">bzm_cranehook.yaml</span> — a Pod, a
          Role and a RoleBinding, configured from this bundle&apos;s own
          settings. It runs once and exits 0 or 1; delete it when it has. Under
          Helm it is the chart&apos;s own{" "}
          <span className="font-mono">helm test</span> hook.
        </p>
      )}
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
 *
 *  `api` is handed down rather than imported, as everywhere else on the page:
 *  a module-level import here was the one call site left with nowhere to put a
 *  different implementation, so the one thing this component does could not be
 *  driven from a test at all.
 */
function TestDeploy(
  { api, facts, options }:
  { api: Api; facts: Facts | null; options: Options },
) {
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
