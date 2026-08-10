// Step 3: what the bundle is, where it goes, and whether the agent came up.
// Lifted out of App with its markup unchanged -- App keeps the state and the
// effects, because half of what is read here is also driven from there (the
// preview's token report, the status poll), and state that two owners write is
// the bug this split must not introduce.
//
// The interface is three records and one report, not forty props. The wide list
// was the honest first move -- every prop was something this panel read -- but
// it had eight of them as value-and-setter pairs the page only ever reset before
// a call, and a panel whose signature is that long is a panel you cannot see the
// shape of. What each record is, is a question this step asks: what is being
// generated, what that does to the credential, what the last attempt did, and
// whether the agent came up. Nothing here holds state; the records are assembled
// in App from state App still owns, so the distribution of ownership is exactly
// what it was.
//
// It asked a fourth question until this commit -- will the cluster take it --
// answered from an evidence file somebody collected, with crane-hook offered
// two ways beside it. All of it is gone: crane-hook is BlazeMeter's image and
// not ours to render, and the imported preflight was machinery no customer of
// this reached for. `bzm-opl-gen doctor` and the collector script still answer
// the same question from a terminal, which is where the people who do run it
// are.
//
// The call that produces a bundle stays in this file deliberately. It can mint
// a credential, and CLAUDE.md's rule is that a request which touches the
// account is made where its cost is on screen -- which is beside that button,
// under the warning that says what a rotation kills. It is made through the
// injected client like every other route, and what it carries about the
// credential is credential.plan.request, taken whole: this file has no say in
// it, which is what stopped the two buttons disagreeing (#104) back when there
// were two.
import {
  Api, AgentStatus, Facts, Options, SvCheckOut, SvMocksOut,
} from "../api";
import { Attempt, NO_ATTEMPT, downloadFailed, downloaded } from "../attempt";
import { Button, ErrorMsg, Switch } from "../components";
import { OptionGroup } from "../optionGroups";
import { placeholderWarning } from "../placeholder";
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
   *  failure. Read here for what the bundle holds. */
  format: string;
  /** Back to the configure step, for the blocks that name an unfinished
   *  group -- the reason the button is disabled is a step away, so the block
   *  offers the way to it. */
  goToConfigure: () => void;
  /** ...and back to step 1, which is where the identity is typed. Two ways back
   *  because there are two steps behind this one, and a block that named a blank
   *  harbor id while offering the configure step would send somebody to the wrong
   *  form. */
  goToAgent: () => void;
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
  /** Required fields left empty, so the bundle carries `<KEY>` for each of
   *  them. Never a reason the download is disabled -- the bundle is real and
   *  says of itself that it is unfinished -- which is why this is beside the
   *  button rather than in front of it. */
  blanks: string[];
  /** The identity left empty: `harbor_id`, `ship_id`, or both. Its own list and
   *  its own block, because the way back is step 1 rather than step 2 -- and it
   *  is only ever non-empty in manual entry, where a bundle may deliberately be
   *  generated before the BlazeMeter location exists. Not a reason the download
   *  is disabled either, for the same reason `blanks` is not. */
  idBlanks: string[];
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
   *  it -- the two that produce a bundle -- rather than importing the real
   *  client at module level, so what they carry is drivable from a test. */
  api: Api;
  bundle: BundleHandover;
  credential: CredentialHandover;
  /** What the last download did, and where the next one is reported. The
   *  record is App's -- this panel makes attempts and hands them over, and
   *  holds nothing. */
  attempt: Attempt;
  report: (a: Attempt) => void;
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
  // Both routes to one container, and the credential file is named in full:
  // it is deliberately not called `.env`, which compose reads for its own
  // substitution rather than passing to the container, and a line here saying
  // ".env" is the shorthand somebody renames the file to match.
  docker: "bzm-opl-agent.sh + compose.yaml + bzm-opl-agent.env + README",
};

export function DownloadPanel(p: DownloadPanelProps) {
  const { api, bundle, credential, attempt, report, watch } = p;
  // The names the markup below already used, for the values it reads most: the
  // markup is the markup that was in App, and rewriting every reference to
  // prove it moved is how a move turns into a rewrite nobody diffed.
  const { facts, shipId, options, format, sv } = bundle;
  const { plan } = credential;
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
              {/* The identity left empty, which is the one blank a bundle can
                  carry and still be exactly what was asked for: a customer with
                  no private location yet has no ids to type, and the manifests
                  are what gets their platform team to approve one. Said here for
                  the same reason as the block below -- this is where the zip is
                  taken away -- and pointing at step 1, where the ids are. */}
              {bundle.idBlanks.length > 0 && (
                <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                  <p className="text-xs text-amber-800 grow">
                    {placeholderWarning(bundle.idBlanks)}
                  </p>
                  <Button kind="ghost" onClick={bundle.goToAgent}>
                    Agent
                  </Button>
                </div>
              )}
              {/* Required fields left empty. Repeated here rather than left on
                  step 2, because this is where the bundle is taken away: the
                  zip really does download, and what it carries has to be said
                  beside the button that produces it. The token above has its
                  own line and is not repeated here -- it is the one blank field
                  with a source this page can offer. */}
              {bundle.blanks.length > 0 && (
                <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
                  <p className="text-xs text-amber-800 grow">
                    {placeholderWarning(bundle.blanks)}
                  </p>
                  <Button kind="ghost" onClick={bundle.goToConfigure}>
                    Configure
                  </Button>
                </div>
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
