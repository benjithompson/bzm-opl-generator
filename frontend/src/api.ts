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
  /** The three concurrency settings beyond `slots`, as BlazeMeter names them.
   *  `overrideCPU` / `overrideMemory` are the engine pod's *requests* (memory
   *  in MB) and are null on the great majority of locations, which is what
   *  makes the scheduler place engines at 250m/256Mi. */
  threadsPerEngine?: number | null;
  overrideCPU?: number | null;
  overrideMemory?: number | null;
}

/** The four settings this tool will change, as it names them. */
export interface LocationSettings {
  slots: number | null;
  threads_per_engine: number | null;
  override_cpu: number | null;
  override_memory: number | null;
}

/** The answer to a settings change: what the account holds *now*.
 *
 *  `changed` is what moved, `ignored` is what was sent and came back unchanged
 *  — a real case, since BlazeMeter's own POST accepts `threadsPerEngine` and
 *  drops it. Reporting the request as the outcome is the failure this shape
 *  exists to prevent; see core.update_location. */
export interface LocationUpdate {
  location: Location;
  changed: Partial<LocationSettings>;
  ignored: string[];
  before: LocationSettings;
  after: LocationSettings;
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

/** What a load target costs, from core.capacity_plan.
 *
 *  The arithmetic is all on the server, not because it is hard -- it is a
 *  division and two multiplications -- but because doctor judges a cluster
 *  against the same constants and the planner and doctor disagreeing is
 *  the one failure this feature can have. A second copy in TypeScript would be
 *  a second engine footprint to keep in step.
 *
 *  `vus_per_engine_assumed` is the field the panel must never drop: the whole
 *  plan is that number multiplied out, and nothing here can measure it. */
export interface CapacityPlan {
  /** Virtual users: the load target. A location holds agents, an agent runs
   *  engines, and each engine drives virtual users -- that hierarchy is the
   *  vocabulary this whole panel speaks. */
  users: number;
  vus_per_engine: number;
  vus_per_engine_assumed: boolean;
  engines: number;
  /** `slots` is engines per *agent*, so a location's concurrency is
   *  agents x slots. These carry the division. */
  agents: number;
  engines_per_agent: number;
  engines_per_node: number;
  nodes_per_agent: number;
  nodes: number;
  engine: {
    cpu: string; memory: string; disk_gb: number; tmp_gb: number;
    supported_vus: number;
  };
  node: { cpu: string; memory: string; disk_gb: number };
  peak: { cpu: string; memory: string; disk_gb: number };
  crane: { cpu_limit: string; memory_limit: string };
  /** The four location settings, in LOCATION_SETTINGS' own names and the
   *  units its PATCH takes -- so a plan can be applied to the settings form
   *  without renaming or re-parsing anything on the way. `override_cpu` is
   *  null when the engine is not a whole number of cores, which is the one
   *  thing the field cannot express. */
  location: LocationSettings & {
    // A plan always has these three; only override_cpu can be missing, and
    // only because the field takes whole cores and the engine may not be one.
    slots: number; threads_per_engine: number; override_memory: number;
  };
  egress: string[];
  warnings: string[];
  document: string;
  document_file: string;
}

/** One private location's share of an account's rated capacity. */
export interface CapLocation {
  id: string;
  name: string;
  func_ids: string[];
  agents: number;
  /** Agents the payload vouches for, and agents it says nothing about. Two
   *  counts because one cannot carry both: a listing need not include a
   *  heartbeat, and "we did not look" is not "it is down". */
  agents_reporting: number;
  agents_unknown: number;
  /** BlazeMeter's `slots`: engines per *agent*, so a location's concurrency is
   *  agents x slots. Null when the location has never been given one. */
  slots: number | null;
  threads_per_engine: number | null;
  engines: number;
  /** null, not 0: a location missing slots or threadsPerEngine has no rating
   *  to state, and 0 would read as "no capacity" when the truth is "nobody has
   *  said". */
  rated_vus: number | null;
  workspace_ids: number[];
  workspace_names: string[];
  /** In more than one workspace, so its capacity is claimable from either. */
  shared: boolean;
}

export interface Capacity {
  account_id: number;
  workspaces: { id: number; name: string }[];
  locations: CapLocation[];
  /** Shared locations counted once, which is why this is not the sum of the
   *  workspace totals. */
  rated_vus: number;
  unrated: number;
}

/** Which of four ways a bundle's AUTH_TOKEN arrived, from core.resolve_auth_token.
 *  GIVEN: the token in the form. ROTATED: a new one was issued and the previous
 *  one is dead. REUSED: the one already in the folder being saved to. PLACEHOLDER:
 *  none, so the bundle cannot be applied as it stands.
 *
 *  All four, though no request this page currently sends can produce `reused`:
 *  `out_dir` is a constant null since Save to folder went to the CLI
 *  (`generate -o`) and the MCP server. The union tracks what the *server* can
 *  answer, not what this page happens to ask -- `tokenFromHeaders` casts a raw
 *  header into it without validating, so a branch dropped here does not stop
 *  arriving, it only stops having a sentence, and `CARRIES[branch]` renders
 *  blank beside the button. tests/test_server.py holds the set equal to core's
 *  for that reason, across two languages neither compiler can see the other of.
 *
 *  Declared rather than served, because this set is closed. A fifth branch is
 *  not a list entry, it is a case the page has to grow a sentence for, and a
 *  union is what makes the compiler point at it. */
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

/** What a bundle request carries about the credential — spread into the body
 *  as it stands, under the server's own name for it.
 *
 *  A record rather than a boolean argument, and that is the whole of #104: the
 *  decision has one producer (token.downloadPlan) and travels as the thing that
 *  will be sent, so no call site converts it and no second call site converts
 *  it differently. `rotate_token` revokes the credential a deployed agent is
 *  running on, and there is no default here — a caller that has not been handed
 *  a plan cannot ask for a bundle at all. */
export interface TokenRequest { rotate_token: boolean }

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail: string | null = null;
    try { detail = (await r.json()).detail ?? null; } catch { /* not our JSON */ }
    // 404/405 with no `detail` is not this API answering -- it is the SPA's
    // static mount, which is what serves a path FastAPI has no route for and
    // answers 405 to any POST. In practice that means one thing: the page is
    // newer than the process serving it. The UI bundle is read from disk on
    // every request, so a long-running server hands out a build whose calls it
    // has never heard of, and the feature looks broken rather than stale.
    // Twice now that has cost a debugging session, so it says so itself.
    if (detail === null && (r.status === 404 || r.status === 405)) {
      throw new Error(
        `this page is newer than the server it is talking to — ${method} ${url} `
        + `is not a route it knows (HTTP ${r.status}). Restart it: `
        + `launchctl kickstart -k gui/$UID/com.blazemeter.bzm-opl-gen.ui, `
        + `or stop and re-run \`bzm-opl-gen ui\``);
    }
    throw new Error(detail ?? r.statusText);
  }
  return r.json();
}

export const api = {
  keyDetect: () =>
    req<{ candidates: KeyCandidate[]; active_key_id: string | null }>("GET", "/api/key/detect"),
  /** The connection this server still holds, if any. The key lives in the
   *  server process, so a refresh never disconnected anything -- this is how
   *  the page finds out it is still connected. */
  keyStatus: () =>
    req<{ connected: boolean; user?: { email: string };
          default_account_id?: number | null; key_id?: string }>(
      "GET", "/api/key"),
  /** Forget the key the server holds. A key saved to disk stays there. */
  keyClear: () => req<{ connected: boolean }>("DELETE", "/api/key"),
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
  /** The AUTH_TOKEN this app minted for an agent, if the server still holds it.
   *
   *  A read of the server's own memory: it asks BlazeMeter nothing and mints
   *  nothing, which is what separates it from the POST above despite the
   *  neighbouring name. There is no endpoint that reads a credential back --
   *  that is the reason the server remembers at all (#123).
   *
   *  `auth_token: null` is "this process holds none for that ship", which is
   *  also what a restarted server honestly says. A request that *fails* is the
   *  third answer and arrives as a rejection, never as a null -- see
   *  token.recallNote for what the page is allowed to say about each. */
  mintedToken: (shipId: string) =>
    req<{ auth_token: string | null }>(
      "GET", `/api/ships/minted-token?ship_id=${encodeURIComponent(shipId)}`),
  /** Forget it: something has been typed over it.
   *
   *  The pasted value wins for this bundle and has to go on winning across a
   *  reload, and the page cannot keep it (session.strip) -- so the remembered
   *  copy is dropped rather than out-ranked. The ship is the argument and the
   *  token never is: a secret in a query string is a secret in an access log. */
  forgetMintedToken: (shipId: string) =>
    req<{ forgotten: boolean }>(
      "DELETE", `/api/ships/minted-token?ship_id=${encodeURIComponent(shipId)}`),
  /** Change a location's concurrency settings. A partial update: send only the
   *  fields being changed. The answer says what the account holds afterwards,
   *  which is not necessarily what was sent -- see LocationUpdate. */
  /* Keyed on LocationSettings rather than Record<string, string>: the four
     names are a closed set on the server too (core.LOCATION_SETTINGS, so a
     caller that meant to change one cannot replace something else), and an
     open record here let a typo through to a 400 that named a field nobody
     had heard of. Values stay strings — the form's blanks are what "leave
     this one alone" means, and parsing them here would lose that. */
  updateLocation: (body: { harbor_id: string }
    & Partial<Record<keyof LocationSettings, string>>) =>
    req<LocationUpdate>("POST", "/api/locations/settings", body),
  // There is no call here that turns a feature on for a location. Which funcIds
  // a location carries is what the location *is*, and changing it is
  // BlazeMeter's own UI's -- unlike the two writes above, which change an
  // agent's credential and a location's concurrency. The page states the
  // features a location does not run and points at where they are enabled.
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
  /** `out_dir` is always null from here, and is sent rather than omitted because
   *  the server's request model still declares it. It is the folder a save would
   *  land in, read for the token its predecessor holds -- but this page no
   *  longer has one to name, the Save to folder button having gone to the CLI
   *  (`generate -o`) and the MCP server. Writing a bundle into a directory is
   *  theirs; this page hands over a zip. */
  generate: (facts: Facts, options: Options) =>
    req<{ files: GeneratedFile[]; token: TokenReport }>("POST", "/api/generate",
      { facts, options, rotate_token: false, out_dir: null }),
  /** Size a load target. Reaches no account and no cluster, which is why the
   *  planner panel works with nothing connected -- see core.capacity_plan.
   *  Blank fields are sent as typed; the server reads "" as "not given". */
  plan: (body: {
    users: string; vus_per_engine?: string; engine_cpu?: string;
    engine_mem?: string; engines_per_node?: string; agents?: string;
  }) => req<CapacityPlan>("POST", "/api/plan", body),
  /** What an engine of this size is rated for, so a field can suggest it. The
   *  ratio stays on the server -- doctor judges locations against the same one,
   *  and a second copy here is how the two come to disagree. */
  engineVus: (cpu: string, mem: string) =>
    req<{ cpu: string; memory: string; supported_vus: number }>(
      "GET", `/api/engine-vus?cpu=${encodeURIComponent(cpu)}&mem=${encodeURIComponent(mem)}`),
  /** What the account can generate, by workspace. See core.account_capacity
   *  for what "rated" means and why a shared location is counted once. */
  capacity: (accountId: number) =>
    req<Capacity>("GET", `/api/capacity?account_id=${accountId}`),
  optionDefaults: () => req<Options>("GET", "/api/option-defaults"),
  funcIdChoices: () => req<FuncIdChoice[]>("GET", "/api/func-ids"),
  features: () => req<Feature[]>("GET", "/api/features"),
  svConstants: () => req<SvConstants>("GET", "/api/sv-constants"),
  /** {option: why} for the options a docker bundle drops — generate's own
   *  DOCKER_IGNORED. Served rather than restated in TypeScript: the configure
   *  step hides what it names, and a key added to the generator would
   *  otherwise go on being offered for a format that ignores it. */
  dockerIgnored: () => req<Record<string, string>>("GET", "/api/docker-ignored"),
  /** {NAME: owning option | null} for the environment variables a bundle writes
   *  for itself — generate's RESERVED_ENV, which `extra_env` refuses. Served
   *  for the same reason as the table above: the env area names what is taken,
   *  and a variable added to a template would otherwise go on being offered
   *  until the collision surfaced as a duplicate ConfigMap key. */
  reservedEnv: () => req<Record<string, string | null>>("GET", "/api/reserved-env"),
  /** ...and the other half of the same question: the variables `extra_env` can
   *  usefully carry, which is BlazeMeter's documented reference minus every
   *  name a control on this page already writes (core.agent_env). Served, so
   *  the page offers exactly what is left over: an option removed from the
   *  generator hands its variable back to this list, and one added takes it
   *  away, without a second table here agreeing to it. */
  agentEnv: () => req<AgentEnvVar[]>("GET", "/api/agent-env"),
  svMocks: (namespace: string, subdomain: string) =>
    req<SvMocksOut>("GET", "/api/sv-mocks?" + new URLSearchParams(
      subdomain ? { namespace, sv_subdomain: subdomain } : { namespace })),
  svCheck: (host: string, scheme: SvScheme) =>
    req<SvCheckOut>("GET", "/api/sv-check?" + new URLSearchParams({ host, scheme })),
  /** Download the bundle, and report what that did to the credential.
   *
   *  Here rather than beside `saveBlob` below, which is where it used to be: it
   *  is a route, and a route outside this object is outside the seam -- the
   *  only way to drive it from a test was to stub `fetch`, so the one path that
   *  can revoke a running agent's credential was the one path not drivable the
   *  way every other route is (#104).
   *
   *  `credential` is token.downloadPlan's, spread into the body as it stands.
   *  It used to be a boolean defaulting to true -- downloading a bundle to read
   *  it revoked a working agent's credential, and the pod that broke looked
   *  like a slow boot (#64). */
  downloadZip: async (
    facts: Facts, options: Options, credential: TokenRequest,
  ): Promise<TokenReport> => {
    const r = await fetch("/api/generate/zip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ facts, options, ...credential }),
    });
    if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
    // Read before the bytes: a zip cannot carry a JSON envelope and still be a
    // zip, so what happened to the credential travels beside the
    // Content-Disposition.
    const token = tokenFromHeaders(r);
    // The server's name, not one built here: it is also the directory the zip
    // extracts to, and a second copy of the rule is a file whose name and whose
    // folder disagree. The local guess is the fallback only -- a namespace the
    // server sanitised out of the folder would otherwise stay in the filename.
    saveBlob(await r.blob(), zipNameFromHeaders(r)
      ?? `bzm-opl-${(options.namespace as string) || "blazemeter"}.zip`);
    return token;
  },
};

/** Every route the page calls, as one thing it is handed rather than one it
 *  imports. `typeof api` rather than a hand-written interface: the two would
 *  drift, and what the page is allowed to reach is exactly what this module
 *  serves -- a route added below is available to a caller without a second
 *  declaration, and a fake that has fallen behind fails to typecheck.
 *
 *  Its point is the seam. Imported at module level there is nowhere to put a
 *  different implementation, so every effect on the page -- the session restore
 *  ordering, the debounced preview, the guarded account-capacity read, the poll
 *  that travels by ref -- can only be exercised against a live server. Passed
 *  in, a test drives them. See App.tsx, which takes it as a prop, and main.tsx,
 *  which is the one place the real one is chosen. */
export type Api = typeof api;

/** Served rather than declared here: generate.py owns both lists, and a copy in
 *  TypeScript is how a new expose backend goes missing from the picker. The
 *  rule is about vocabularies that grow — a closed set like TokenBranch is
 *  deliberately the other way round. */
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

/** One agent variable the environment area offers, from /api/agent-env --
 *  BlazeMeter's own reference minus the names this generator writes, which is
 *  why the area lists what it lists and nothing has to be typed from memory.
 *
 *  `type` is what a control is chosen from, and a type this page does not know
 *  falls back to a text box rather than taking the row off screen: the same
 *  direction an empty docker-ignored table goes, for the same reason. `default`
 *  is the *agent's* default, stated on the row, because a variable left unset
 *  is not a variable with no value. */
export interface AgentEnvVar {
  name: string;
  type: string;
  /** Which of BlazeMeter's two tables document it: "kubernetes", "docker", or
   *  both. A manifests bundle is offered the first, a docker bundle the second
   *  -- a variable the agent under this bundle has no reader for is a setting
   *  that would go quietly nowhere. */
  platforms: string[];
  summary: string;
  default: string | null;
  example: string | null;
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

/** The name the server gave the archive, which is also the directory it
 *  extracts to. Null where the header is missing or carries no filename -- the
 *  caller has a fallback, and a name guessed here is better than none. */
export function zipNameFromHeaders(r: Response): string | null {
  const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") ?? "");
  return m ? m[1] : null;
}

function tokenFromHeaders(r: Response): TokenReport {
  return {
    branch: (r.headers.get(TOKEN_BRANCH_HEADER) ?? "placeholder") as TokenBranch,
    ship_id: null,
    message: r.headers.get(TOKEN_MESSAGE_HEADER) ?? "",
  };
}

