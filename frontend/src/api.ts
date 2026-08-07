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
 *  the one failure this pairing can have. A second copy in TypeScript would be
 *  a second engine footprint to keep in step.
 *
 *  `vus_per_engine_assumed` is the field the panel must never drop: the whole
 *  plan is that number multiplied out, and nothing here can measure it. */
/** One functionality's sizing model, from /api/sizing-models.
 *
 *  `measured` false is the field this list exists for: no figure for that unit
 *  has ever been measured, so there is no per-pod box to offer and nothing to
 *  default. That is a different answer from a figure nobody has supplied yet,
 *  and the two must not share a control any more than they share a value. */
export interface SizingModel {
  /** The funcId, so it joins to `Functionality.id` and to a location's
   *  `func_ids` by equality. */
  functionality: string;
  /** BlazeMeter's own display name, joined on in core. */
  label: string;
  /** What the target counts: "virtual users", "browser instances". */
  unit: string;
  /** What one pod carries, in that unit: "virtual users per engine". */
  figure_unit: string;
  /** What the plan calls the pods this model needs — "engines", "mock pods".
   *  Never assume they are engines: a service-virtualization location carries
   *  no taurus engine at all. */
  pods: string;
  measured: boolean;
  /** A target to offer before anybody has typed one — the sizing this model's
   *  default saved sizing carries. A starting point and never a
   *  recommendation; served, because a number invented here for a model the
   *  page has only just been told about is exactly the figure this tool never
   *  measured. */
  example_target: number;
}

/** One functionality's slot minimum, from /api/slot-minimums — what
 *  BlazeMeter requires before it will create a location carrying that funcId
 *  at all (#159).
 *
 *  Served rather than written here: the number was found on a live POST and
 *  `message` is BlazeMeter's own sentence, so a second copy on the page is how
 *  the rule and what the form says about it stop being the same rule. */
export interface SlotMinimum {
  /** BlazeMeter's display name for the functionality, as its own error uses
   *  it — so the form's sentence and the account's read alike. */
  label: string;
  /** The smallest `slots` the account will accept. Never applied for anybody:
   *  slots is engines per agent and a real cost. */
  minimum: number;
  /** BlazeMeter's refusal, verbatim. */
  message: string;
}

/** What one pod of a given engine size is rated for, from /api/engine-vus.
 *
 *  Asked as soon as the size changes rather than waiting for a plan: the figure
 *  is most use *before* a target is typed, because that is when the size is
 *  being chosen.
 *
 *  `rated` is per model and **null** where that model has no measured per-pod
 *  figure -- the same absence `SizingModel.measured` reports, arriving as the
 *  value rather than as a rule the page has to know. It is why no field here
 *  has to ask which model is the performance one. */
export interface EngineRating {
  cpu: string;
  memory: string;
  /** The performance model under the name doctor and `threadsPerEngine` call
   *  it by. `rated.performance` is the same number; this is the one the
   *  location settings speak in. */
  supported_vus: number;
  rated: Record<string, number | null>;
}

/** One model's answer inside a plan: its target, what a pod carries, and how
 *  many pods that is. */
export interface PlanSizing {
  functionality: string;
  unit: string;
  target: number;
  /** Null where no figure has been measured, which is also `pods` null: the
   *  model was asked for and could not be answered, which is not the same as
   *  a model that needs nothing. */
  per_pod: number | null;
  per_pod_unit: string;
  /** Three answers, and they stay three. "assumed" is a figure this tool chose
   *  from the pod size; "unmeasured" is one nobody has ever measured. */
  per_pod_source: "supplied" | "assumed" | "unmeasured";
  pods: number | null;
  pods_label: string;
}

export interface CapacityPlan {
  /** Virtual users: the performance model's target, and **null** when no load
   *  test was sized. A location holds agents, an agent runs engines, and each
   *  engine drives virtual users -- that hierarchy is the vocabulary this
   *  whole panel speaks, but it is only one of the three sizings. */
  users: number | null;
  /** Every model asked for, in the server's own order. */
  sizings: PlanSizing[];
  /** The funcId of the model the pod count came from: where several were
   *  sized, the largest decides and this says which it was. */
  driven_by: string;
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

/** A refusal this API wrote, with the status it wrote it as.
 *
 *  The status is carried because one of them means something no sentence can be
 *  relied on to say: 404 is the location or agent being *gone*, which is the one
 *  failure a retry cannot fix and the one with a remedy the page can offer
 *  (Refresh). Everything else -- an expired key, an account that restricts an
 *  endpoint, BlazeMeter being down -- is a failure that may come right on its
 *  own, and telling somebody their location has been deleted because their VPN
 *  dropped is the pair this codebase keeps apart everywhere else. Parsing the
 *  code back out of the message would be that same fact stated twice, in the
 *  place least able to be right about it (see api.BzmApiError, which makes the
 *  argument on the Python side).
 *
 *  Callers that do not care read it as an ordinary Error, which is all it was
 *  before. The rule about which status means what lives in `stale.ts`, not
 *  here: this is transport. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

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
    // has never heard of, and the page looks broken rather than stale.
    // Twice now that has cost a debugging session, so it says so itself.
    if (detail === null && (r.status === 404 || r.status === 405)) {
      throw new Error(
        `this page is newer than the server it is talking to — ${method} ${url} `
        + `is not a route it knows (HTTP ${r.status}). Restart it: `
        + `launchctl kickstart -k gui/$UID/com.blazemeter.bzm-opl-gen.ui, `
        + `or stop and re-run \`bzm-opl-gen ui\``);
    }
    // ...and a 404 that *does* carry a detail is a route that answered: the
    // branch above is the only other thing a 404 can be here, so having taken
    // it first, the status is safe to hand on as a refusal about what was
    // asked for rather than about which routes exist.
    throw new ApiError(detail ?? r.statusText, r.status);
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
  /** Drop what the server remembers of BlazeMeter, so the next read is a real
   *  one. Answers nothing: what to re-read afterwards is the caller's, and the
   *  one caller re-reads the location list. Without it the Refresh button would
   *  be served from the same cache for up to a minute — a click that changes
   *  nothing and looks exactly like one that worked. */
  refresh: () => req<null>("POST", "/api/refresh"),
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
  // There is no call here that turns a functionality on for a location. Which funcIds
  // a location carries is what the location *is*, and changing it is
  // BlazeMeter's own UI's -- unlike the two writes above, which change an
  // agent's credential and a location's concurrency. The page states the
  // functionalities a location does not run and points at where they are enabled.
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
    users?: string; vus_per_engine?: string; engine_cpu?: string;
    engine_mem?: string; engines_per_node?: string; agents?: string;
    /** One row per functionality being sized, each in that model's own unit.
     *  `users` is the performance model's shorthand and still taken; a caller
     *  sizing more than one thing sends rows and nothing else. */
    sizings?: { functionality: string; target: string; figure?: string }[];
  }) => req<CapacityPlan>("POST", "/api/plan", body),
  /** What each functionality is sized in — plan.SIZING_MODELS with the
   *  account's label joined on. Served for the reason `functionalities` is: the
   *  card renders a field group per model, and a fourth model has to reach it
   *  by being added to the table rather than by an edit here. */
  sizingModels: () => req<SizingModel[]>("GET", "/api/sizing-models"),
  /** What a pod of this size is rated for, per model, so a field can suggest
   *  it. The ratios stay on the server -- doctor judges locations against the
   *  same one, and a second copy here is how the two come to disagree. */
  engineVus: (cpu: string, mem: string) =>
    req<EngineRating>(
      "GET", `/api/engine-vus?cpu=${encodeURIComponent(cpu)}&mem=${encodeURIComponent(mem)}`),
  /** What the account can generate, by workspace. See core.account_capacity
   *  for what "rated" means and why a shared location is counted once. */
  capacity: (accountId: number) =>
    req<Capacity>("GET", `/api/capacity?account_id=${accountId}`),
  optionDefaults: () => req<Options>("GET", "/api/option-defaults"),
  /** The funcId vocabulary. `accountId` is optional and the page uses both
   *  answers: the covered baseline on mount, when there is no key to ask with,
   *  and the account's own list the moment one is chosen — which is why the
   *  answer says which of the two it is rather than leaving the caller to
   *  remember (FuncIdVocabulary). */
  funcIdVocabulary: (accountId?: number) => req<FuncIdVocabulary>(
    "GET", accountId ? `/api/func-ids?account_id=${accountId}` : "/api/func-ids"),
  functionalities: () => req<Functionality[]>("GET", "/api/functionalities"),
  svConstants: () => req<SvConstants>("GET", "/api/sv-constants"),
  /** {format: {option: why}} for what each output format drops — generate's own
   *  IGNORED_BY_FORMAT. Served rather than restated in TypeScript: the configure
   *  step hides what it names, and a key added to the generator would
   *  otherwise go on being offered for a format that ignores it.
   *
   *  Every format has an entry, and an entry that is `{}` is a format that
   *  drops nothing — which is not the same answer as this request never having
   *  landed. `formats.ignoredFor` is where the two are told apart. */
  ignoredOptions: () => req<Record<string, Record<string, string>>>(
    "GET", "/api/ignored-options"),
  /** What this server is actually serving. `stale` true means the built page is
   *  older than the code behind it, which is invisible otherwise: a route the
   *  page needs answers 404, `formats.ignoredFor` honestly reads that as "not
   *  read yet", and the form then shows fields the format hides. `null` is a
   *  wheel, where there is no source to compare against — never false, which
   *  would claim a check nobody could make. */
  build: () => req<BuildState>("GET", "/api/build"),
  /** {funcId: the slots BlazeMeter needs before it will make the location} —
   *  core's SLOT_MINIMUMS. Served for the same reason as the table above: the
   *  new-location form states the rule before the account does, and the number
   *  and the sentence are both BlazeMeter's. An empty table is "not read yet"
   *  and refuses nothing — a create the account then rejects beats a form
   *  refusing on a guess. */
  slotMinimums: () => req<Record<string, SlotMinimum>>(
    "GET", "/api/slot-minimums"),
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
   *  away, without a second table here agreeing to it.
   *
   *  `funcIds` scopes it to what the location runs, and the scoping is done
   *  there rather than here so that the CLI and the MCP server get the same
   *  answer (#150). Absent — which is `null`/`undefined`, and reaches the route
   *  as no parameter at all — is nobody having said yet, and offers the
   *  reference whole; `[]` is a location running nothing this tool covers, and
   *  offers only what every agent reads. Two reads, never one. */
  agentEnv: (funcIds?: string[] | null) => req<AgentEnvVar[]>(
    "GET", funcIds == null
      ? "/api/agent-env"
      : "/api/agent-env?" + new URLSearchParams({ func_ids: funcIds.join(",") })),
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

/** Likewise served, and the account's rather than anybody's table: `label` is
 *  BlazeMeter's own display name, so the words on this page are the words in the
 *  customer's own UI. It falls back to the raw funcId server-side, so one the
 *  account serves without a name is offered rather than dropped.
 *
 *  With no account the answer is the covered baseline -- the three funcIds this
 *  tool configures -- because this is asked for on mount, before a key exists,
 *  and manual entry never has an account at all. Pass `accountId` once there is
 *  one and the account's own list replaces it (see core.func_ids).
 *
 *  `covered` is the difference between a funcId this tool has option groups and
 *  images for and one it can only name. Served rather than derived here: a page
 *  that showed `delphix` beside `performance` with nothing to tell them apart
 *  would be offering to configure something no bundle can.
 *
 *  `changes_images` is false for a funcId that needs the same images as one
 *  already offered. Creating a location keeps the full list, because BlazeMeter
 *  distinguishes them there; the manual form, where the only thing a funcId
 *  does is pick images, offers only the ones that change the answer.
 *
 *  `sub_func_ids` are the funcIds that are *parameters* of this one rather than
 *  functionalities of their own -- `functionalGui` carries 117 of them, the
 *  browser pins `chrome:default`, `firefox:139`, `safari:15`. They arrive in a
 *  location's `funcIds` beside the parent, and under the parent here because
 *  the row that knows which functionality a pin belongs to is the only one that
 *  can say. Always present, `[]` where a functionality has none: absence would
 *  be a third answer nobody defined (#160). */
export type FuncIdChoice = {
  id: string; label: string; changes_images: boolean; covered: boolean;
  sub_func_ids: string[];
};

/** ...and the envelope the vocabulary arrives in, which says which of the two
 *  answers above this is.
 *
 *  `source` exists because a funcId *missing* from `choices` means opposite
 *  things in them. Against the account, missing is retired -- BlazeMeter
 *  stopped serving it and the locations predating the removal still carry it.
 *  Against the baseline it means nothing at all: the baseline is the three
 *  covered funcIds, and every other one an account has is missing from it too.
 *  A caller that had to remember which call it made would be one refactor from
 *  saying "retired" about a vocabulary nobody read. */
export type FuncIdVocabulary = {
  source: "account" | "baseline";
  choices: FuncIdChoice[];
};

/** How reading the namespace ended. The watch panel is the only thing in this
 *  client that needs a cluster, and the only one allowed to: cluster access is
 *  optional, so an unreadable one is an "ok" HTTP response carrying which of
 *  the four reasons it was, and the caller never has to guess from an error
 *  string. */
export type SvReadStatus = "ok" | "no_cli" | "no_context" | "denied" | "no_mocks";

/** One functionality the configure step can be pointed at, from
 *  /api/functionalities. The list is served for the same reason as the two
 *  above -- secrets, TDM and data orchestration are expected to follow, and a
 *  functionality has to become selectable by being added to the vocabulary, not
 *  by an edit here. Option groups tag themselves with `id` (see
 *  optionGroups.ts); nothing in the frontend enumerates the functionalities
 *  themselves. */
export interface Functionality {
  /** **The funcId** (#149), so a location's `func_ids` join to these by
   *  equality. There is no list of claimed funcIds here any more: one entry
   *  claiming four was why a card over a `performance`-only location was
   *  labelled "Performance & functional testing", and a per-functionality list
   *  is a translation table between this tool's ids and BlazeMeter's. A funcId
   *  no entry has is simply not covered -- named on the page, configured
   *  nowhere. */
  id: string;
  /** BlazeMeter's own display name, so it reads as the customer's location
   *  settings read. */
  label: string;
  hint?: string;
  /** Suggested, never forced: applied only while the namespace field still
   *  holds a namespace some functionality suggested. Served with the label so the
   *  suggestion extends with the vocabulary. */
  namespace: string;
  /** Does this functionality's agent carry a taurus engine? Which is what makes
   *  "engine size" a true statement about its pod limits, and what service
   *  virtualization is declared apart from.
   *
   *  Served (facts.CATEGORY_BY_FUNC, read off real single-functionality
   *  locations' /versions) rather than kept here: it was
   *  `ENGINE_FUNCTIONALITIES`, two funcIds written out in optionGroups.ts, and
   *  a copy in TypeScript of a table Python owns is what `IGNORED_BY_FORMAT` and
   *  the funcId vocabulary are served to avoid. */
  runs_engine: boolean;
}

/** One agent variable the environment area offers, from /api/agent-env --
 *  BlazeMeter's own reference minus the names this generator writes, which is
 *  why the area lists what it lists and nothing has to be typed from memory.
 *
 *  `type` is what a control is chosen from, and a type this page does not know
 *  falls back to a text box rather than taking the row off screen: the same
 *  direction an unread ignored-options table goes, for the same reason. `default`
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
  /** The funcIds whose agent reads it, empty meaning every location — the same
   *  rule `OptionGroup.functionalities` follows. The *filtering* is the
   *  server's (see `agentEnv` below), so this is here to be read rather than
   *  applied: what a row is here for, not whether it is here. */
  functionalities: string[];
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


/** What the server is serving, from /api/build. `stale` is three-valued on
 *  purpose: true is a page older than its code, false is a checkout that was
 *  compared and is current, and null is a wheel with no source to compare. */
export interface BuildState {
  version: string | null;
  built: number | null;
  stale: boolean | null;
  commit: string | null;
}
