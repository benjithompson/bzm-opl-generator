// Thin typed client for the local bzm-opl-gen API.

export interface KeyCandidate { path: string; key_id: string }
export interface Account { id: number; name: string }
export interface Workspace { id: number; name: string }
export interface Ship {
  id: string; name: string; state: string;
  lastHeartBeat?: number; installedVersion?: string;
}
export interface Location {
  id: string; name: string; funcIds?: string[]; slots?: number; ships?: Ship[];
  workspacesId?: number[];
}
export interface Facts {
  harbor_id: string; harbor_name?: string; func_ids?: string[];
  ships: { id: string; name?: string }[];
  images: object[]; images_source?: string; crane_image?: string;
}
export interface GeneratedFile { name: string; content: string }
/** Manual facts, plus the one thing no catalogue can supply: a GUI location
 *  needs a version-pinned browser image that only a live agent inventory names.
 *  `gui_images_incomplete` is served but currently unread here -- the page can
 *  no longer declare functionalGui, so it cannot be true. `bzm-opl-gen facts
 *  --manual --func-ids functionalGui` still warns. */
export interface ManualFactsOut { facts: Facts; gui_images_incomplete: boolean }
export interface AgentStatus {
  state: string; heartbeat_age_s: number | null;
  installed_version?: string; online: boolean;
}
export interface Options { [k: string]: unknown }

/** Which of four ways a bundle's AUTH_TOKEN arrived, from core.resolve_auth_token.
 *  GIVEN: the token in the form. ROTATED: a new one was issued and the previous
 *  one is dead. REUSED: the one already in the folder being saved to. PLACEHOLDER:
 *  none, so the bundle cannot be applied as it stands.
 *
 *  Declared rather than served, for the reason Strength gives below: this set is
 *  closed. A fifth branch is not a list entry, it is a case the page has to grow
 *  a sentence for, and a union is what makes the compiler point at it. */
export type TokenBranch = "given" | "rotated" | "reused" | "placeholder";

/** What happened to the credential, carried on every answer that generates a
 *  bundle. `message` is core's own sentence and is shown as-is -- composing one
 *  here from `branch` would be a second copy of the rule that decided it, in a
 *  language that cannot see the first. It never contains the token value. */
export interface TokenReport {
  branch: TokenBranch;
  /** The ship the token belongs to, where that is known. */
  ship_id: string | null;
  message: string;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.json();
}

export const api = {
  keyDetect: () =>
    req<{ candidates: KeyCandidate[]; active_key_id: string | null }>("GET", "/api/key/detect"),
  keySet: (body: { path?: string; id?: string; secret?: string; save?: boolean }) =>
    req<{ user: { email: string }; default_account_id: number | null; key_id: string }>(
      "POST", "/api/key", body),
  accounts: () => req<Account[]>("GET", "/api/accounts"),
  workspaces: (accountId: number) =>
    req<Workspace[]>("GET", `/api/workspaces?account_id=${accountId}`),
  locations: (workspaceId: number) =>
    req<Location[]>("GET", `/api/locations?workspace_id=${workspaceId}`),
  createLocation: (body: {
    name: string; account_id: number; workspace_id: number;
    func_ids: string[]; slots: number; threads_per_engine: number;
  }) => req<Location>("POST", "/api/locations", body),
  /** Create an agent, and take the credential it comes with.
   *
   *  The one call here that issues a token, and the reason nothing else has to:
   *  a ship created a moment ago has no previous credential for the issue to
   *  invalidate, so this is the free moment to capture it. `auth_token` is null
   *  with `token_error` set on an account whose token endpoint is closed -- the
   *  agent exists either way, and losing its id is what a thrown error would
   *  cost. */
  createShip: (harborId: string, name: string) =>
    req<{ ship: Ship; auth_token: string | null; token_error: string | null }>(
      "POST", "/api/ships", { harbor_id: harborId, name }),
  /** Issue a NEW AUTH_TOKEN for an agent that already exists.
   *
   *  Its own call, not a flag on a download: what it does to an agent running
   *  on the previous credential is revoke it, and the caller has to have said
   *  so before reaching this. The token comes back because BlazeMeter will not
   *  show it again. */
  issueToken: (harborId: string, shipId: string) =>
    req<{ auth_token: string }>(
      "POST", "/api/ships/token", { harbor_id: harborId, ship_id: shipId }),
  /** Turn a feature on for a location. Additive and idempotent server-side --
   *  see core.add_func_id, which reads the location's own list first. */
  addFuncId: (harborId: string, funcId: string) =>
    req<Location>("POST", "/api/locations/func-id",
                  { harbor_id: harborId, func_id: funcId }),
  facts: (harborId: string) => req<Facts>("GET", `/api/facts?harbor_id=${harborId}`),
  /** Facts from the three values BlazeMeter shows on the agent, with no API key.
   *  Nothing is validated and nothing is looked up -- see /api/facts/manual. */
  manualFacts: (body: { harbor_id: string; ship_id: string; func_ids: string[] }) =>
    req<ManualFactsOut>("POST", "/api/facts/manual", body),
  status: (harborId: string, shipId: string) =>
    req<AgentStatus>("GET", `/api/status?harbor_id=${harborId}&ship_id=${shipId}`),
  /** The live preview, which runs on every keystroke -- so `rotate_token` is sent
   *  explicitly false rather than left to the server's default: nothing about
   *  looking at manifests may touch the account. `token` says what the bundle
   *  currently carries, which is how the page knows a download would be a
   *  placeholder before anyone clicks it. */
  /** `outDir` is the folder a save would land in, when one has been typed. It is
   *  read, never written: a folder already holding this ship's bundle supplies
   *  its own token, so sending it is what lets the preview say `reused` instead
   *  of `placeholder`. Without it the page warned "fill it in before applying"
   *  over a folder whose token the save was about to keep -- which invites a
   *  rotation nothing needed. */
  generate: (facts: Facts, options: Options, outDir?: string) =>
    req<{ files: GeneratedFile[]; token: TokenReport }>("POST", "/api/generate",
      { facts, options, rotate_token: false, out_dir: outDir ?? null }),
  optionDefaults: () => req<Options>("GET", "/api/option-defaults"),
  funcIdChoices: () => req<FuncIdChoice[]>("GET", "/api/func-ids"),
  features: () => req<Feature[]>("GET", "/api/features"),
  svConstants: () => req<SvConstants>("GET", "/api/sv-constants"),
  svMocks: (namespace: string, subdomain: string) =>
    req<SvMocksOut>("GET", "/api/sv-mocks?" + new URLSearchParams(
      subdomain ? { namespace, sv_subdomain: subdomain } : { namespace })),
  svCheck: (host: string, scheme: SvScheme) =>
    req<SvCheckOut>("GET", "/api/sv-check?" + new URLSearchParams({ host, scheme })),
  /** The preflight verdicts for a cluster nobody here can reach, from the file
   *  its collector script produced. Needs no API key and no cluster, like
   *  manualFacts -- `evidence` is the parsed file, sent whole and judged
   *  server-side, because what counts as evidence is doctor's to say. */
  preflight: (facts: Facts | null, options: Options, evidence: unknown) =>
    req<PreflightOut>("POST", "/api/preflight",
      { facts: facts ?? {}, options, evidence }),
};

/** One verdict, exactly as `doctor` reaches it. FAIL = a test would not start;
 *  WARN = the numbers are wrong or it will bite later, but a test still starts.
 *  Nothing in the browser re-decides one. */
export type CheckStatus = "PASS" | "WARN" | "FAIL";
export interface PreflightCheck {
  name: string;
  status: CheckStatus;
  detail: string;
}
/** The verdicts, in doctor's order — which puts where the answers came from
 *  first, because it qualifies every one after it. `namespace` is the one they
 *  were judged against: the configured one, which the evidence file may not be
 *  the one collected for (the leading check says so when it is not). */
export interface PreflightOut {
  namespace: string;
  /** What the file says about itself. Distinct from `namespace` above on
   *  purpose: that is the namespace being preflighted, this is the one the
   *  file describes, and a file collected for another namespace says little
   *  about this one. */
  evidence: EvidenceSummary;
  checks: PreflightCheck[];
  /** What the same file implies about the options, in suggest.py's reporting
   *  order. Carried here rather than fetched separately because both halves are
   *  judged against the configuration that was sent, and two round trips is two
   *  answers that can end up describing different configurations in one panel. */
  suggestions: Suggestion[];
  /** Why there are none, when there are none -- a file that never reached a
   *  cluster and a cluster that constrains nothing produce the same empty list,
   *  and only the first is worth re-collecting for. Null once there is anything
   *  to show. */
  why_nothing: string | null;
}

/** What an imported evidence file says about itself, read off the document by
 *  doctor.evidence_summary. The verdicts are only ever as good as these three:
 *  how stale the read is, which namespace it describes, and which sections the
 *  collector was refused — a null section is "we did not look", never "there
 *  are none". Nulls where the file recorded neither. */
export interface EvidenceSummary {
  collected_at: string | null;
  namespace: string | null;
  /** Section names, in the order the collector wrote them; empty when it read
   *  everything it asked for. */
  unreadable: string[];
}

/** How strongly a suggestion holds. DECISIVE: the evidence settles it and
 *  `value` is the answer. SUGGESTIVE: it narrows the choice without making it,
 *  `value` is always null, and `candidates` is the shortlist a person still has
 *  to pick from. The invariant is suggest.py's and is asserted over every
 *  fixture there — `strength` alone is enough to decide what may be offered.
 *
 *  Declared here rather than served, unlike the vocabularies further down, and
 *  the difference is that this set is closed. A backend list grows: one is
 *  added to generate.py and every consumer should get it for free, so a copy
 *  here is a picker quietly missing an entry. A strength is not added — it is a
 *  branch the UI has to grow, in `offer()` and in STRENGTH_STYLE, and a union is
 *  what makes the compiler point at both. Served, a third strength would arrive
 *  as an undefined style and a row offering nothing, at runtime, on a customer's
 *  screen. Same reasoning for MergeState below. */
export type Strength = "DECISIVE" | "SUGGESTIVE";

/** How the suggestion stands against the options that were sent, from
 *  suggest.merge(). SETTLED: already configured this way. FILL: the option
 *  still holds what the generator would have used anyway. CHOOSE: suggestive,
 *  nothing picked yet. CONFLICT: the configuration says something else, which
 *  is a disagreement to show rather than a write to make. Declared rather than
 *  served for the reason Strength gives: every state is a branch in `offer()`,
 *  and the four are what suggest.merge() can return, not a list it extends. */
export type MergeState = "SETTLED" | "FILL" | "CHOOSE" | "CONFLICT";

/** One implication of the evidence, and where it stands. `option` is a generate
 *  option (asserted against DEFAULT_OPTIONS in tests/test_suggest.py), `detail`
 *  is why in the reader's terms, and `evidence` are dotted paths into the file
 *  so a reader can go and disagree with it. */
export interface Suggestion {
  option: string;
  strength: Strength;
  /** The settled value — always null for a suggestive one. */
  value: unknown;
  candidates: unknown[];
  ruled_out: unknown[];
  evidence: string[];
  detail: string;
  state: MergeState;
  /** What the configuration holds for this option right now. Shown whatever the
   *  state: applying is always a value replacing a value. */
  current: unknown;
}

/** Served rather than declared here: generate.py owns both lists, and a copy in
 *  TypeScript is how a new expose backend goes missing from the picker. The
 *  rule is about vocabularies that grow — see Strength above for why the two
 *  closed sets are deliberately the other way round. */
export interface SvBackend {
  group: string;
  resources: string[];
  /** What crane publishes with it — "Ingress", "Gateway + VirtualService", … */
  creates: string;
  /** Whether this backend works with service_type NODEPORT. False for the two
   *  where crane writes the Service's nodePort into the published object, so
   *  the endpoint never serves — generate() refuses those (#60). */
  nodeport_ok: boolean;
}
export type SvConstants = {
  func_ids: string[];
  /** The backends only. What `sv_ingress` holds when such a location is
   *  generated for performance alone is optionGroups.SV_NONE, and it is
   *  deliberately not here: this is what the picker is built from. */
  ingress_types: string[];
  backends: Record<string, SvBackend>;
};

/** Likewise served: facts.CATEGORY_BY_FUNC owns the vocabulary, and the copy
 *  that used to live here is how sv-bridge went missing from the create form.
 *  `label` falls back to the raw id server-side, so an unlabelled funcId is
 *  offered rather than dropped.
 *
 *  `changes_images` is false for a funcId that needs the same images as one
 *  already offered -- functionalApi is "the taurus engine", exactly as
 *  performance is. Creating a location keeps the full list, because BlazeMeter
 *  distinguishes them there; the manual form, where the only thing a funcId
 *  does is pick images, offers only the ones that change the answer. */
export type FuncIdChoice = { id: string; label: string; changes_images: boolean };

/** How reading the namespace ended. The watch panel is the only thing in this
 *  client that needs a cluster, and the only one allowed to: cluster access is
 *  optional, so an unreadable one is an "ok" HTTP response carrying which of
 *  the four reasons it was, and the caller never has to guess from an error
 *  string. */
export type SvReadStatus = "ok" | "no_cli" | "no_context" | "denied" | "no_mocks";

/** One feature the configure step can be pointed at, from /api/features. The
 *  list is served for the same reason as the two above -- functional testing,
 *  secrets and API monitoring are expected to follow, and a feature has to
 *  become selectable by being added to the vocabulary, not by an edit here.
 *  Option groups tag themselves with `id` (see optionGroups.ts); nothing in the
 *  frontend enumerates the features themselves. */
export interface Feature {
  id: string;
  label: string;
  hint?: string;
  /** Suggested, never forced: applied only while the namespace field still
   *  holds a namespace some feature suggested. Served with the label so the
   *  suggestion extends with the vocabulary. */
  namespace: string;
  /** The location funcIds that mean a location has this feature. A location's
   *  funcIds may include ones no feature claims -- that is not an error. */
  func_ids: string[];
}

/** A deployed virtual service and the host it answers at. `host` is null until
 *  a wildcard domain is configured. Built by the generator and carried here, so
 *  no caller rebuilds the string the endpoint is published at. */
export interface SvEndpoint { name: string; port: number; host: string | null }

/** What is deployed right now, for the watch panel. */
export interface SvMocksOut {
  status: SvReadStatus;
  mocks: SvEndpoint[];
  message: string;
}
/** Whether the endpoint a deployed mock publishes actually answers, probed by
 *  the machine serving this page. `status` is how the attempt ended, not how
 *  the mock feels: "ok" means something replied with a status line — a 503
 *  included, and that one is the finding, not a failed check. Its `message`
 *  carries the sv-expose wording, so no caller has to recognise the code. */
export type SvCheckStatus = "ok" | "dns" | "refused" | "tls" | "timeout" | "error";
export type SvScheme = "http" | "https";
export interface SvCheckOut {
  status: SvCheckStatus;
  /** The HTTP status, or null when nothing answered with one. */
  code: number | null;
  url: string;
  message: string;
  /** The raw reason, for the cases where the sentence above is not enough. */
  detail: string;
}
/** Save a Blob to disk under `filename`. Named rather than inlined into
 *  downloadZip because the object-URL and anchor dance is where the browser
 *  quirks live, and it is worth having one place to fix them. */
export function saveBlob(blob: Blob, filename: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** Where the branch travels on a route that answers with bytes. A zip cannot
 *  carry a JSON envelope and still be a zip, so the server puts it beside the
 *  Content-Disposition; these two names are server.TOKEN_BRANCH_HEADER and
 *  TOKEN_MESSAGE_HEADER, and tests/test_server.py pins the literals because a
 *  rename on one side would quietly lose the sentence rather than fail. */
const TOKEN_BRANCH_HEADER = "X-Bzm-Token-Branch";
const TOKEN_MESSAGE_HEADER = "X-Bzm-Token-Message";

function tokenFromHeaders(r: Response): TokenReport {
  return {
    branch: (r.headers.get(TOKEN_BRANCH_HEADER) ?? "placeholder") as TokenBranch,
    ship_id: null,
    message: r.headers.get(TOKEN_MESSAGE_HEADER) ?? "",
  };
}

/** Download the bundle, and report what that did to the credential.
 *
 *  `rotateToken` issues a NEW AUTH_TOKEN and kills the one any deployed agent is
 *  running on, so it is off unless the caller asked for exactly that. It used to
 *  default to true — downloading a bundle to read it revoked a working agent's
 *  credential, and the pod that broke looked like a slow boot (#64). */
export async function downloadZip(
  facts: Facts, options: Options, rotateToken = false,
): Promise<TokenReport> {
  const r = await fetch("/api/generate/zip", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ facts, options, rotate_token: rotateToken }),
  });
  if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
  const token = tokenFromHeaders(r);
  saveBlob(await r.blob(),
    `bzm-opl-${(options.namespace as string) || "blazemeter"}.zip`);
  return token;
}

export interface SavedBundle {
  out_dir: string;
  files: { name: string; bytes: number }[];
  token: TokenReport;
}

/** Write the bundle to a directory on the machine running this server — the
 *  same shape `bzm-opl-gen livetest` consumes and an MCP session's opl_bundle
 *  reads, so the folder is the handoff between this page and those.
 *
 *  Saving twice into one folder is the ordinary way to use it, and it no longer
 *  costs a rotation: the server generates *into* the directory, so the token
 *  already there is reused and the agent deployed from the last save keeps
 *  working. `token.branch` says which happened. */
export function saveBundle(
  facts: Facts, options: Options, outDir: string, rotateToken = false,
): Promise<SavedBundle> {
  return req<SavedBundle>("POST", "/api/generate/save",
    { facts, options, rotate_token: rotateToken, out_dir: outDir });
}
