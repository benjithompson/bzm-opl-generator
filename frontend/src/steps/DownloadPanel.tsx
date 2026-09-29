// Step 3: download the bundle and watch the agent come up. App owns the state;
// this panel holds only its own fold. The download request is made here, beside
// the text saying what it does to the credential, and sends
// credential.plan.request exactly as given.
import { useState } from "react";
import {
  Api, AgentStatus, Facts, Options, SvCheckOut, SvMocksOut,
} from "../api";
import { Attempt, NO_ATTEMPT, downloadFailed, downloaded } from "../attempt";
import { Button, ErrorMsg, SubSection, Switch } from "../components";
import { Gap, gapSummary } from "../placeholder";
import { DownloadPlan } from "../token";
import { Sv } from "../sv";

/** What is being generated, for whom, and whether it can be. */
interface BundleHandover {
  facts: Facts | null;
  /** Facts for newly typed values are on their way, so `facts` is for the
   *  previous ones and must not be downloaded. */
  reading: boolean;
  shipId: string | null;
  /** The options as sent, markers included. */
  options: Options;
  /** The output format, chosen on the configure step; read for what the
   *  bundle holds. */
  format: string;
  /** Back to the configure step, for a row naming one of its fields. */
  goToConfigure: () => void;
  /** Back to step 1, where the identity and token are typed. */
  goToAgent: () => void;
  /** Service virtualization, read for whether to list mocks and the probe scheme. */
  sv: Sv;
  /** A preview that did not render: then there is no bundle to download. The
   *  preview pane shows the message. */
  genErr: string | null;
  /** Every field the bundle carries a marker for (placeholder.gaps), in fill-in
   *  order. Never a reason the download is disabled. */
  gaps: Gap[];
}

/** What the next download will do about the agent's credential. */
interface CredentialHandover {
  /** The hint beside the button and the credential request the download sends,
   *  from token.downloadPlan. Sent as given. */
  plan: DownloadPlan;
}

/** Watching the agent this bundle deploys, and the virtual services under it. */
interface WatchHandover {
  /** Whether it can be watched: polling needs an API key, which manual entry
   *  lacks. */
  available: boolean;
  on: boolean;
  setOn: (v: boolean) => void;
  /** The agent's name, or its id where it has none. */
  agent: string | null;
  status: AgentStatus | null;
  mocks: { ns: string; read: SvMocksOut } | null;
  checks: Record<string, { busy: boolean; res?: SvCheckOut; err?: string }>;
  check: (host: string) => void;
}

interface DownloadPanelProps {
  /** The route caller, so what a download sends is testable. */
  api: Api;
  bundle: BundleHandover;
  credential: CredentialHandover;
  /** What the last download did, and where the next one is reported. */
  attempt: Attempt;
  report: (a: Attempt) => void;
  watch: WatchHandover;
}

/** How an endpoint check reads: a 503 is amber (the check worked, and the
 *  message names the fix); only no status line at all is red. */
const checkTone = (r: SvCheckOut) =>
  r.status !== "ok" ? "text-red-600"
    : r.code != null && r.code < 400 ? "text-emerald-700" : "text-amber-700";

/** What each format's bundle contains, for the line beside the download button. */
const BUNDLE_HOLDS: Record<string, string> = {
  manifests: "manifests + README",
  helm: "helm/ + bzm-opl-values.yaml + README",
  // The credential file is named in full; never `.env`, which compose reads
  // for its own substitution.
  docker: "bzm-opl-agent.sh + compose.yaml + bzm-opl-agent.env + README",
};

/** One field left blank: its marker, its option key, where the value comes
 *  from once that is read, and the way back to the step that fills it. */
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
          <p className="text-2xs text-slate-500 mt-0.5">{gap.source}</p>
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
  // Local: whether the list is open is a fact about this view.
  const [gapsOpen, setGapsOpen] = useState(false);
  const { facts, shipId, options, format, sv } = bundle;
  const { plan } = credential;
  // Only a missing bundle disables download: no facts, facts still being read,
  // no agent, or a preview that failed. Blank fields are markers, and the
  // configurations generate() refuses arrive as genErr.
  const ready = !!facts && !bundle.reading && !!shipId && !bundle.genErr;
  return (
            <div className="space-y-3">
              <div className="flex gap-2 items-center">
                <Button disabled={!ready} busy={bundle.reading}
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
              {/* The markers the bundle carries, one row each, folded. Not
                  amber: they are expected. Collapsible only when non-empty. */}
              {bundle.gaps.length === 0 ? (
                /* The finished state is a bar built to the header's size: an
                   empty SubSection would draw a blank body or a chevron over
                   nothing. */
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
                  /* Watching needs the API key this mode does without. */
                  <p className="text-xs text-slate-500">
                    Agent status needs an API key — switch to
                    {" "}<b>Connect to BlazeMeter</b> above to watch this agent
                    come online, or check Settings → Private Locations in
                    BlazeMeter after applying.
                  </p>
                ) : (
                <>
                {/* Built like an option-group row: a switch with one line of
                    consequence. */}
                <div className="rounded-xl border border-slate-200 px-3 py-2.5 flex items-center gap-3">
                  <Switch on={watch.on} onChange={watch.setOn}
                    label="Watch agent status" />
                  <div className="min-w-0 grow">
                    <p className="text-sm font-medium text-slate-700">
                      Watch agent status
                      <span className="ml-2 font-mono text-2xs text-slate-500">
                        {watch.agent}
                      </span>
                    </p>
                    <p className="text-2xs text-slate-400">
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
                    <span className={"flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 shrink-0 "
                      + (watch.status?.online
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-slate-100 text-slate-500")}>
                      <span className={"h-1.5 w-1.5 rounded-full "
                        + (watch.status?.online ? "bg-emerald-500" : "bg-slate-400 animate-pulse")} />
                      {watch.status?.online ? "Online" : "Waiting"}
                    </span>
                  )}
                </div>
                {/* An idle agent says nothing about whether its virtual
                    services became reachable, so an SV deploy lists them. */}
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
                            <li key={`${m.name}-${m.port}`} className="text-2xs text-slate-500">
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
                                  {/* Probed from the machine serving this page,
                                      against the host shown. */}
                                  <button type="button" disabled={chk?.busy}
                                    onClick={() => watch.check(m.host!)}
                                    className="ml-2 align-baseline rounded border border-slate-300 px-1.5 py-0.5 text-3xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40">
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
                              <ErrorMsg msg={chk?.err ?? null} className="mt-0.5" />
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      // "Nothing deployed" and "cannot look" read differently.
                      <p className="text-2xs text-slate-400">{watch.mocks.read.message}</p>
                    )}
                  </div>
                )}
                </>
                )}
              </div>
            </div>
  );
}
