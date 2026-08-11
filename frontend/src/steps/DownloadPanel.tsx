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
import { useState } from "react";
import {
  Api, AgentStatus, Facts, Options, SvCheckOut, SvMocksOut,
} from "../api";
import { Attempt, NO_ATTEMPT, downloadFailed, downloaded } from "../attempt";
import { Button, ErrorMsg, SubSection, Switch } from "../components";
import { Gap, gapSummary } from "../placeholder";
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
  /** A preview that did not render, which is the one block that is neither a
   *  group's nor a blank field's: there is no bundle to download. Not shown here
   *  -- the preview pane says so where it is.
   *
   *  It had `saOk` beside it, an empty service account name, and that is gone
   *  with the gate that read it (see `ready`): a blank required field is a
   *  marker now, so the bundle exists and this step's job is to say what it
   *  carries rather than to withhold it. */
  genErr: string | null;
  /** Every field this bundle carries a marker for, assembled in App by
   *  `placeholder.gaps` -- the identity, the credential and the blank options
   *  in one list, in the order somebody would fill them in.
   *
   *  One list, and it was four blocks: the token had a line, the identity had a
   *  card, the option blanks had a card and each unfinished group had another,
   *  all four in amber, stacked over a button that worked. Never a reason the
   *  download is disabled -- the bundle is real and says of itself that it is
   *  unfinished -- which is why this is beside the button rather than in front
   *  of it, and why the panel is folded shut. */
  gaps: Gap[];
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

/** One field left blank, with the way back to the form that fills it in.
 *
 *  The marker leads, because it is the half that is useful away from this page:
 *  it is the string somebody greps a handed-on bundle for. The option key sits
 *  beside it in the page's own vocabulary, and the sentence under both is the
 *  generator's -- served, never restated here, and simply absent where it has
 *  not been read (see `Gap.source`). A row with no sentence still says the two
 *  true things. */
function GapRow({ gap, goToAgent, goToConfigure }: {
  gap: Gap; goToAgent: () => void; goToConfigure: () => void;
}) {
  return (
    <li className="flex items-start gap-2">
      <div className="grow min-w-0">
        <p className="text-xs">
          <span className="font-mono font-semibold text-slate-700">
            {gap.marker}
          </span>
          <span className="text-slate-400"> · {gap.key}</span>
        </p>
        {gap.source && (
          <p className="text-[11px] text-slate-500 mt-0.5">{gap.source}</p>
        )}
      </div>
      <Button kind="ghost"
        onClick={gap.step === 1 ? goToAgent : goToConfigure}>
        {gap.step === 1 ? "Agent" : "Configure"}
      </Button>
    </li>
  );
}

export function DownloadPanel(p: DownloadPanelProps) {
  const { api, bundle, credential, attempt, report, watch } = p;
  // Whether the list is open. Local, like every other fold on this page: it is
  // a fact about this view rather than about the bundle.
  const [gapsOpen, setGapsOpen] = useState(false);
  // The names the markup below already used, for the values it reads most: the
  // markup is the markup that was in App, and rewriting every reference to
  // prove it moved is how a move turns into a rewrite nobody diffed.
  const { facts, shipId, options, format, sv } = bundle;
  const { plan } = credential;
  // One expression for both buttons rather than the same terms twice.
  //
  // **A blank field is not among them, and an empty service account name was
  // until #245.** It blocked here on the reading that `generate()` refuses one
  // -- true when that gate was written, and false since a blank required field
  // became its own marker: `fill_placeholders` runs before every validator, so
  // `service_account()` sees `<SERVICE_ACCOUNT_NAME>` and the bundle renders.
  // What was left was a page that printed "the bundle will carry those markers
  // instead" and then would not produce it, with the button disabled and
  // nothing on this step saying why -- the off-screen blocker, in the one place
  // that had kept it.
  //
  // **`sv.ok` was the last of them, and it went the same way.** It read
  // `svIncomplete`, which is true on a blank `sv_subdomain` or `sv_tls_secret`
  // -- and those are the sv group's own `requires`, so they are in `gaps` too:
  // the panel listed them as markers the bundle carries while the button beside
  // it refused to produce that bundle. Measured rather than reasoned, because
  // the comment above `REQUIRED_TEXT` said the opposite: `generate()` renders
  // both, emitting `<SV_SUBDOMAIN>` and `<SV_TLS_SECRET>` into the ConfigMap,
  // and `_sv_cfg`'s refusal cannot fire after `fill_placeholders` has run.
  //
  // The two arms of `svIncomplete` that *are* real failures -- no ingress
  // chosen on a mockServices location, and a NodePort the backend cannot
  // publish over -- are refused by `generate()` itself, so they arrive here as
  // `genErr` from the preview, in red, carrying BlazeMeter-grade sentences this
  // page could not improve on. So every term left is a bundle that does not
  // exist: no facts, no agent, or a preview that did not render.
  const ready = !!facts && !!shipId && !bundle.genErr;
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
              {/* What the bundle carries a marker for, folded shut.

                  This was four amber cards -- the token's line, the identity's,
                  the option blanks' and one per unfinished group -- stacked
                  over a button that worked perfectly well, which is how a
                  bundle that generates came to read as four errors. One
                  section, one row per field, and the same fold the configure
                  step's own sections use.

                  **Not amber, and that is deliberate.** A marker is the bundle
                  saying which box was left empty; it is expected, it is
                  actionable at leisure, and every one of them is the same kind
                  of thing. Amber over the whole panel said "something is wrong
                  here" about the ordinary case of generating a bundle before
                  the location exists.

                  **Collapsible only when there is something behind it.** The
                  finished state is the header alone with its tick: a chevron
                  over an empty body is a control promising something that is
                  not there.

                  The unfinished-group card is gone from this step with the
                  rest. Two of its three arms are already rows here -- a group's
                  `requires` is what `blankRequired` walks -- and the third is a
                  configuration `generate()` refuses, which arrives as the
                  `ErrorMsg` below. `incompleteGroups` still says so on the
                  configure step, where the row it names is on screen. */}
              {bundle.gaps.length === 0 ? (
                /* The finished state is the bar and nothing else. Deliberately
                   not a `SubSection` with an empty body: given no `open`/
                   `onToggle` that component is permanently *open*, so it drew
                   its padded body under the header as a blank strip -- a panel
                   claiming to hold something. Given them instead it would be a
                   chevron over nothing, which is the same claim with a control
                   on it. Built to the header's own measurements so the two
                   states sit in the same place as the list appears and goes. */
                <p className="flex items-center gap-2 rounded-lg border
                              border-slate-200 bg-slate-50 px-3 py-2.5 text-sm
                              font-semibold text-slate-800">
                  <span className="text-xs text-emerald-600">✓</span>
                  Nothing left to fill in
                </p>
              ) : (
                <SubSection title="Placeholders"
                  summary={`to update — ${gapSummary(bundle.gaps)}`}
                  open={gapsOpen} onToggle={() => setGapsOpen((v) => !v)}>
                  <ul className="space-y-2.5">
                    {bundle.gaps.map((g) => (
                      <GapRow key={g.key} gap={g}
                        goToAgent={bundle.goToAgent}
                        goToConfigure={bundle.goToConfigure} />
                    ))}
                  </ul>
                </SubSection>
              )}
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
